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
1. Use ONLY the supplied current Swing Play Card JSON, supplied Plain-English
   paragraph, and supplied regime. Do not use training knowledge, outside
   market facts, other tickers, news, or assumptions. Conversation history
   is context for the question only; it is NOT a source of facts.
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
9. If asked about historical record/performance and no record figures are
   explicitly supplied, say the setup does not show that information.

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

    supplied = {
        "symbol": symbol,
        "current_swing_play_card": card,
        "plain_english_explainer": _plain_text(plain_english),
        "market_regime": regime if isinstance(regime, (dict, list, str, int, float, bool)) else None,
    }
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
