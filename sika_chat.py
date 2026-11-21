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

# Shared ETF proxies for the GICS sectors used by the market map.
_SECTOR_ETF = {
    "Information Technology": "XLK",
    "Financials": "XLF",
    "Energy": "XLE",
    "Health Care": "XLV",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Industrials": "XLI",
    "Materials": "XLB",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
}
_SEMICONDUCTORS = {
    "AAOI", "ADI", "ALAB", "AMAT", "AMD", "AMKR", "ASML", "AVGO",
    "COHR", "ENTG", "INTC", "KLAC", "LRCX", "MCHP", "MPWR", "MRVL",
    "MU", "NVDA", "NXPI", "ON", "QCOM", "QRVO", "SMTC", "SWKS",
    "TER", "TSM", "TXN",
}
_SECTOR_CACHE = {}
_SECTOR_CACHE_LOCK = __import__("threading").Lock()
_SECTOR_CACHE_TTL = 300


SYSTEM_PROMPT = """You are Sika, NextShelf's plain-English setup explainer.
You are answering a member's follow-up about the one ticker in this thread.

GROUNDING RULES — follow every rule:
1. Use ONLY the supplied data: the current Swing Play Card JSON, the supplied
   Plain-English paragraph, the supplied regime, the supplied recent 4H price
   history, supplied record figures, news headlines, sector/market
   performance, the supplied VIX close (market_volatility), the gamma figures when they are present, and the supplied flow figures (the breakout's aggressive-buy classification, its net premium, and the measured Flow Lab record) when they are present. Do not use training knowledge, outside market facts, other
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
10. News and catalysts: headlines, when supplied, are the only news
    you may cite. Quote the headline and its source; do not add a cause the
    headline does not state. A headline near the setup is a possible catalyst,
    not proof that it caused the move. Use supplied sector/market returns
    to compare the stock with its sector ETF and SPY over the 1-, 5-, and
    20-session windows. State the comparison plainly (stronger/weaker/in line)
    only when the relevant figures exist. For semiconductor tickers, use SMH
    and SOXX as industry-group context when supplied. These are price
    comparisons, not proof of causation. Do not invent earnings dates, analyst
    actions, upcoming events, sector moves, or market conditions. If no
    headlines are supplied, say there is no headline in the supplied data.
    If a supplied headline is labelled as a demo sample, never present it as
    real news.
11. Gamma: gamma figures, when supplied, are context only. They never
    change the card's trigger, target, stop, grade, or contract, and they are
    never a reason to take or skip a trade. State gamma facts plainly — where
    the call wall, put wall, and flip sit relative to the card's levels, and
    which regime the supplied data shows. When the supplied data shows a gamma
    wall or shelf at the same price as the card's trigger or target, you may
    call that confluence — the two level systems pointing at the same price —
    stated as a fact about the levels, never as evidence the setup is more
    likely to work. Do not call gamma bullish or bearish, do not say it
    confirms, weakens, or supports the setup, and do not present pinning or
    acceleration as a certainty — describe what the map shows. Never call a
    gamma level "next". If no gamma figures are supplied and the member asks
    about gamma, walls, or the flip, say exactly: "I don't have gamma data for
    this one right now." Then stop.
12. Flow: when flow figures are supplied, they are the classification of
    this play's breakout from the options tape — aggressive buying measured
    trade by trade against the paired quote — together with the measured
    record of the Flow Lab test (confirmed retests +0.45% per trade,
    n = 102, PF 1.31; against −0.49%, n = 95; none −0.10%, n = 376). You may
    say the flow confirmed the breakout, or ran against it, only in those
    words and only when the supplied state says so, and when you do, cite
    the record. Confirmation is a fact about similar past setups, not a
    prediction about this one: never say flow makes this trade likely to
    work, never call flow bullish or bearish, and never present it as a
    direction call. The volume-versus-open-interest line elsewhere in the
    app is a different, unmeasured proxy — never describe it with these
    numbers or this language. Flow never changes the card's trigger,
    target, stop, grade, or contract. If no flow figures are supplied and
    the member asks about flow, aggressive buying, or confirmation, say
    exactly: "I don't have flow data for this one right now." Then stop.
13. Market volatility: when the VIX is supplied (market_volatility), you may
    state its close, its band word (calm, normal, elevated, stressed — use
    the supplied word only), its change, and its date when the member asks
    about the market or conditions. It describes that close only: never a
    forecast for the next session, never a reason to take or skip a trade,
    and never connected to this setup's odds. Call it context. If it is not
    supplied and the member asks about the VIX or volatility, say you don't
    have it right now.

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
_WHY_WORDS = (
    "why", "news", "catalyst", "what happened", "moved", "reason",
    "what drove", "what is driving", "what's driving", "whats driving",
    "what drives", "what caused", "what is behind", "what's behind",
    "whats behind", "behind the setup", "driving the setup",
    "driving this setup", "what is moving", "what's moving",
    "whats moving", "why is", "why did",
)


def _wants(question: str, words) -> bool:
    q = (question or "").lower()
    return any(w in q for w in words)


def _vix() -> dict | None:
    """The stored VIX fact for Sika (Patch 190). Never fetches; None when nothing is stored."""
    try:
        v = scanner.vix_stored()
        if not v:
            return None
        return dict(vix_close=v["level"], band=v.get("band"), change_pct_vs_prior_close=v.get("change_pct"),
                    close_date=v.get("date"), sentence=scanner.vix_line(v))
    except Exception:
        return None


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


def _returns(frame) -> dict | None:
    """Daily close returns over 1, 5 and 20 sessions from intraday provider bars."""
    try:
        if frame is None or frame.empty or "Close" not in frame:
            return None
        daily = frame["Close"].resample("1D").last().dropna()
        if len(daily) < 2:
            return None
        last = float(daily.iloc[-1])
        if last <= 0:
            return None
        out = {"last_close": round(last, 2), "sessions": int(len(daily))}
        for label, offset in (("1d_pct", 1), ("5d_pct", 5), ("20d_pct", 20)):
            if len(daily) > offset:
                base = float(daily.iloc[-1 - offset])
                out[label] = round((last / base - 1) * 100, 2) if base > 0 else None
            else:
                out[label] = None
        out["as_of"] = str(daily.index[-1])
        return out
    except Exception:
        return None


def _sector_context(symbol: str) -> dict | None:
    """Compare the stock with its sector ETF and SPY; add semiconductor-group proxies where relevant.
    Uses the app's existing market-data provider and a short shared cache. Fail-open."""
    try:
        import data
        import universe

        if data.provider() == "demo":
            return dict(available=False, reason="Sector performance is unavailable in demo market-data mode.")
        sector = universe.sector(symbol)
        sector_etf = _SECTOR_ETF.get(sector)
        if not sector_etf:
            return dict(available=False, sector=sector,
                        reason="No sector ETF mapping is available for this ticker.")
        symbols = [symbol, sector_etf, "SPY"]
        industry = []
        if symbol in _SEMICONDUCTORS:
            industry = ["SMH", "SOXX"]
            symbols.extend(industry)

        key = tuple(sorted(set(symbols)))
        now = __import__("time").time()
        with _SECTOR_CACHE_LOCK:
            hit = _SECTOR_CACHE.get(key)
            if hit and now - hit[0] < _SECTOR_CACHE_TTL:
                frames = hit[1]
            else:
                frames = None
        if frames is None:
            frames = data.get_bars_many(list(key), 35)
            with _SECTOR_CACHE_LOCK:
                _SECTOR_CACHE[key] = (now, frames)

        perf = {s: _returns(frames.get(s)) for s in key}
        stock = perf.get(symbol)
        sector_data = perf.get(sector_etf)
        spy = perf.get("SPY")
        if not stock or not sector_data or not spy:
            return dict(available=False, sector=sector, sector_etf=sector_etf,
                        reason="The provider did not return enough recent bars for a sector comparison.")

        def relative(a, b):
            if a is None or b is None:
                return None
            return round(a - b, 2)

        result = {
            "available": True,
            "sector": sector,
            "sector_etf": sector_etf,
            "stock": stock,
            "sector_performance": sector_data,
            "broad_market": {"symbol": "SPY", **spy},
            "stock_minus_sector_pct_points": {
                k: relative(stock.get(k), sector_data.get(k))
                for k in ("1d_pct", "5d_pct", "20d_pct")
            },
            "sector_minus_spy_pct_points": {
                k: relative(sector_data.get(k), spy.get(k))
                for k in ("1d_pct", "5d_pct", "20d_pct")
            },
            "industry_proxies": {s: perf.get(s) for s in industry},
            "note": "Returns are price performance, not proof of causation; daily bars reflect the configured provider's freshness and delay.",
        }
        return result
    except Exception as e:
        log.warning("sika chat %s: sector context failed: %s", symbol, e)
        return dict(available=False, reason="Sector performance could not be retrieved.")


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


def _gamma(card: dict) -> dict | None:
    """The gamma facts the swing card already computed (Patch 173): the card's own gamma object, returned
    unchanged, so chat and the card can never disagree. None when the card carries none (rule 11 gives the
    exact fallback sentence for that case)."""
    try:
        gx = (card or {}).get("gamma")
        return gx if isinstance(gx, dict) and gx else None
    except Exception:
        return None


def _flow(card: dict) -> dict | None:
    """The flow facts the swing card already computed (Patch 174): the card's own flow object, returned
    unchanged, so chat and the card can never disagree. None when the card carries none — no stored
    classification for the play's breakout, or the visibility gate kept the key off this card (rule 12
    gives the exact fallback sentence for that case)."""
    try:
        fx = (card or {}).get("flow")
        return fx if isinstance(fx, dict) and fx else None
    except Exception:
        return None


def answer(sym: str, message: str, history=None, plain_english: str = "", include_flow: bool = False) -> dict:
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
    card = swing_card.card(symbol, include_flow=include_flow)
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
        # Patch 190: the stored VIX close, its fixed band word, day change and date (context only; None if never stored).
        "market_volatility": _vix(),
        # Always supplied: how price got here (last 4H closes + the hub that formed).
        "recent_price_history": _price_history(symbol),
    }
    # Always supplied when the card carries it: the gamma context (Patch 173).
    gx = _gamma(card)
    if gx is not None:
        supplied["gamma_context"] = gx
    # Always supplied when the card carries it: the flow context (Patch 174).
    fx = _flow(card)
    if fx is not None:
        supplied["flow_context"] = fx

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
        supplied["sector_and_market_performance"] = _sector_context(symbol)
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
