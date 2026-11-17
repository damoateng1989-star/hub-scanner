"""Conversational Sika — grounded follow-up answers for the Swing Play Card.

The deterministic card remains the source of truth. This module only writes
short explanations from the current card, the supplied Plain English paragraph,
and the stored market regime. It never writes trading or record data.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re

import requests

import db
import scanner
import swing_card

log = logging.getLogger("sika_chat")

SYSTEM_PROMPT = """You are Sika, NextShelf's plain-English setup explainer.
You are answering a member's follow-up about the one ticker in this thread.

GROUNDING RULES — follow every rule:
1. Use ONLY the supplied data: the current Swing Play Card JSON, the supplied
   Plain-English paragraph, the supplied regime, the supplied recent 4H price
   history, and the supplied record figures and news headlines when they are
   present. Do not use training knowledge, outside market facts, other
   tickers, news, or assumptions. Conversation history is context for the
   question only; it is NOT a source of facts.
2. Never invent a number: no trigger, target, stop, price, grade, percentage,
   contract, expiry, or date unless it appears in the supplied current data.
3. Never forecast where price will go, when it will get there, or whether a
   setup will work. State conditional rules only: if the required 4H close
   confirms, the card's target is the next shelf; if the close invalidates
   the level, the play is wrong. Never predict a deadline or outcome.
4. If the supplied data cannot answer the question, say exactly in plain
   language: "I don't have that — the setup doesn't show it." Then stop.
   Do not guess, fill gaps, or hedge with made-up values.
5. Do not tell the member what to buy, how much to buy, or when to enter.
   Explain the setup only. The trade remains the member's decision.
6. Answer in a few short sentences, plain English, warm and direct. No
   lecture, jargon, preamble, markdown heading, or bullet list.
7. Educational only, not financial advice.
8. Treat the current card JSON as authoritative if prior conversation turns
   conflict with it. Do not claim a card value that is absent from that JSON.
9. Record: if record figures are supplied, quote them exactly — the per-symbol
   average, trade count, win rate, and profit factor for this setup kind on
   this ticker. If no record figures are supplied, say the setup does not show
   that information. Never quote a record figure that is not in the supplied
   data, and never quote a universe-wide Lab figure — the supplied figures are
   for this ticker only.
10. News: headlines, when supplied, are the only news you may cite. Quote the
    headline and its source; do not add a cause the headline does not state.
    If no headlines are supplied, say there is no headline in the supplied
    data. If a supplied headline is labelled as a demo sample, never present
    it as real news.

Return only the answer text, without quotation marks or a JSON wrapper.
"""


def _plain_text(value: str) -> str:
    value = re.sub(r"<[^>]*>", " ", value or "")
    return html.unescape(value).strip()[:5000]


def _history(turns):
    out = []
    for turn in (turns or [])[-10:]:
        if not isinstance(turn, dict):
            continue
        role = turn.get("role")
        text = str(turn.get("text") or "").strip()
        if role not in ("user", "sika") or not text:
            continue
        out.append({"role": "user" if role == "user" else "assistant",
                    "content": text[:1200]})
    return out


# Intent words — a cheap substring heuristic, not a classifier. A false
# positive only adds a small factual block to the prompt; a false negative
# just means Sika answers as before (she says she does not have it).
_RECORD_WORDS = ("record", "track record", "how often", "win rate", "win-rate",
                 "how has", "history", "does this work", "worked", "performance",
                 "profit factor", "average return")
_WHY_WORDS = ("why", "news", "catalyst", "what happened", "moved", "reason",
              "what drove", "what's driving", "whats driving", "what caused")


def _wants(question: str, words) -> bool:
    q = (question or "").lower()
    return any(w in q for w in words)


def _price_history(symbol: str) -> dict | None:
    """Last few 4H closes + how the hub formed, from the engine. Cache-shared
    with swing_card.card — the same load_4h + analyze + current_status calls,
    so no extra download. Fail-open: None when the bars cannot be read."""
    try:
        import engine
        c4 = scanner.load_4h(symbol, scanner.SCAN_DAYS)
        res = engine.analyze(c4, scanner.SETTINGS)
        st = engine.current_status(c4, res, scanner.SETTINGS)
    except Exception as e:
        log.warning("sika chat %s: price history failed: %s", symbol, e)
        return None
    closes = res["close"].iloc[-6:]
    hub = st.get("hub") or {}
    return dict(
        last_closes=[dict(time=str(t), close=round(float(v), 2)) for t, v in closes.items()],
        last_close=round(float(res["close"].iloc[-1]), 2),
        hub=dict(low=hub.get("low"), high=hub.get("high"), sessions=hub.get("sessions"),
                 grade=hub.get("grade"), score=hub.get("score"),
                 start=hub.get("start"), end=hub.get("end")) if hub else None,
        trigger_up=st.get("trigger_up"), trigger_down=st.get("trigger_down"),
        state=st.get("state"),
    )


def _record(symbol: str, card: dict) -> dict | None:
    """The per-symbol record for this setup kind in this direction — the same
    numbers behind the card's grade (engine.setup_grade on the backtest
    trades, via swing_card._grade, the function Patch 152 already uses).
    Only called when the member asks about the record, so the extra backtest
    only runs on record questions. Fail-open: None when there is no live
    setup, no favored side, or the grade cannot be computed."""
    try:
        import engine
        import patterns
        fav = (card or {}).get("favored") or {}
        side = fav.get("side")
        if side not in ("calls", "puts"):
            # A live C/D/? setup is watch-only, so the card has no favored
            # side. Its deterministic message still states breakout/breakdown.
            msg = str((card or {}).get("message") or "").lower()
            side = "calls" if "breakout" in msg else "puts" if "breakdown" in msg else None
        if side not in ("calls", "puts"):
            return None
        kind_by_name = {v: k for k, v in patterns.KINDS.items()}
        kind = kind_by_name.get((card or {}).get("setup"))
        if kind not in ("hub", "gapfade", "double_top", "double_bottom"):
            return None
        g = swing_card._grade(symbol, kind, 1 if side == "calls" else -1)
    except Exception as e:
        log.warning("sika chat %s: record failed: %s", symbol, e)
        return None
    if not g or g.get("n", 0) < engine.GRADE_MIN:
        return dict(setup_kind=kind, direction=side, n=g.get("n", 0) if g else 0,
                    note="Not enough history to grade this setup on this ticker.")
    return dict(setup_kind=kind, direction=side, grade=g.get("grade"), word=g.get("word"),
                n=g.get("n"), avg_pct=g.get("avg"), win_pct=g.get("win"),
                profit_factor=g.get("pf"), reached_shelf=g.get("reached"))


def _news(symbol: str, card: dict) -> dict | None:
    """Latest 1–2 headlines for the ticker, catalyst first, then the newest
    item — the exact accessor the ticker page and the alerts use
    (news.recent, Alpaca/Benzinga, cached 10 minutes; news.catalyst picks
    the headline most likely tied to the move). Only called when the member
    asks why it moved. Fail-open: None when the feed cannot be read. Demo
    mode headlines are labelled samples — the block carries demo=True and
    the system prompt forbids presenting one as real news."""
    try:
        import data
        import news
        items = news.recent(symbol)
    except Exception as e:
        log.warning("sika chat %s: news failed: %s", symbol, e)
        return None
    if not items:
        return dict(demo=False, headlines=[],
                    note="No headlines for this ticker in the last 10 days.")
    since = (card or {}).get("as_of")
    try:
        cat = news.catalyst(items, since)
    except Exception:
        cat = None
    picked = [cat] if cat else []
    for x in items:
        if len(picked) >= 2:
            break
        if x not in picked:
            picked.append(x)
    return dict(demo=(data.provider() == "demo"),
                headlines=[dict(headline=x["headline"], source=x["source"], time=x["time"],
                               tag_label=x.get("tag_label")) for x in picked])


def answer(sym: str, message: str, history=None, plain_english: str = "") -> dict:
    """Return one grounded conversational answer. Raises on missing key/API failure."""
    key = os.getenv("SIKA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("SIKA_API_KEY is not configured")

    symbol = scanner.clean_symbol(sym)
    question = (message or "").strip()
    if not question or len(question) > 1200:
        raise ValueError("message must be between 1 and 1200 characters")

    # Re-read the deterministic card for every answer: no stale levels are carried
    # forward from the browser or conversation history.
    card = swing_card.card(symbol)
    try:
        regime_raw = db.get_setting("regime")
        regime = json.loads(regime_raw) if regime_raw else None
    except Exception:
        regime = None

    q = question.lower()
    supplied = {
        "symbol": symbol,
        "current_swing_play_card": card,
        "plain_english_explainer": _plain_text(plain_english),
        "market_regime": regime if isinstance(regime, (dict, list, str, int, float, bool)) else None,
        # Always supplied: how price got here (last 4H closes + the hub that formed).
        "recent_price_history": _price_history(symbol),
    }
    # Only when asked: the record (a question about how this setup has done)
    # and the news (a question about why it moved). A false positive on the
    # keyword check only adds a small factual block; a false negative just
    # means Sika answers as before (she says she does not have it).
    if _wants(q, _RECORD_WORDS):
        rec = _record(symbol, card)
        if rec is not None:
            supplied["record_for_this_setup"] = rec
    if _wants(q, _WHY_WORDS):
        nws = _news(symbol, card)
        if nws is not None:
            supplied["news_headlines"] = nws
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "system", "content": "CURRENT DATA (the only factual source):\n" +
                 json.dumps(supplied, ensure_ascii=False, separators=(",", ":"), default=str)}]
    messages.extend(_history(history))
    messages.append({"role": "user", "content": question})

    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": os.getenv("SIKA_MODEL", "gpt-4o-mini"),
              "messages": messages, "temperature": 0.2, "max_tokens": 180},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    answer_text = payload["choices"][0]["message"]["content"].strip()
    if not answer_text:
        raise RuntimeError("Empty model response")
    return {"answer": answer_text[:1600]}
