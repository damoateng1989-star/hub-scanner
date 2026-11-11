"""Recent headlines for a stock, so a breakout comes with its possible reason.

Source: Alpaca's news feed (Benzinga), with the same API keys as the price data. Headlines are cached for 10 minutes
per stock. Nothing here decides a trade: a headline near a breakout is a possible catalyst, not a proven cause.
Demo mode makes up two clearly-labelled sample headlines so the app can be tried without data.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time

import pandas as pd
import requests

import data

log = logging.getLogger("news")
TTL = 600
DAYS = 10                   # how far back the Shelf card looks (a breakout stays live for up to 10 candles)
_CACHE: dict[str, tuple[float, list[dict]]] = {}
_LOCK = threading.Lock()

TAGS = [  # key, label, pattern (first match wins, so the order is the priority)
    ("earnings", "Earnings", r"\b(earnings|eps|q[1-4]\b|quarter(ly)? results|revenue (beat|miss)|beats?|misses|tops estimates|results)\b"),
    ("guidance", "Guidance", r"\b(guidance|outlook|forecast|raises (fy|full)|cuts (fy|full)|preannounce)"),
    ("deal", "Deal", r"\b(acquir\w+|acquisition|merger|merge with|buyout|takeover|to buy|to be bought|stake in|spin[- ]?off|divest)"),
    ("analyst", "Analyst", r"\b(upgrad\w+|downgrad\w+|price target|initiat\w+ coverage|reiterates|overweight|underweight|outperform|underperform|raises pt|lowers pt)"),
    ("fda", "FDA / trial", r"\b(fda|approval|approved|phase [123i]+|clinical|trial (data|results)|crl)\b"),
    ("offering", "Offering", r"\b(offering|convertible notes?|secondary|dilut\w+|prices \$|shelf registration)"),
    ("capital", "Buyback / dividend", r"\b(buyback|repurchase|dividend|stock split)"),
    ("legal", "Legal / regulatory", r"\b(lawsuit|sues|sued|investigation|probe|subpoena|recall|antitrust|settle\w*|fined|fines)\b"),
    ("contract", "Contract / product", r"\b(contract|partnership|partners with|launch\w*|order from|wins|awarded|agreement)\b"),
    ("exec", "Leadership", r"\b(ceo|cfo|chief executive|resign\w*|steps down|appoint\w*|names new)\b"),
]
_TAGS = [(k, l, re.compile(p, re.I)) for k, l, p in TAGS]
ROUNDUP = re.compile(r"\b(stocks? (moving|to watch)|movers|biggest|top \d+|\d+ stocks|market (wrap|update|clubhouse)|mid-?day|premarket|after-?hours|why .* (are|is) (moving|trading)|52-week)\b", re.I)


def _tag(headline: str) -> tuple[str | None, str | None]:
    for k, l, rx in _TAGS:
        if rx.search(headline or ""):
            return k, l
    return None, None


def _clean(x: dict, sym: str) -> dict | None:
    head = (x.get("headline") or "").strip()
    if not head:
        return None
    syms = [s.upper() for s in (x.get("symbols") or [])]
    key, label = _tag(head)
    return dict(headline=head[:220], source=(x.get("source") or "").title() or "News", url=x.get("url") or "",
                time=x.get("created_at") or x.get("updated_at"), tag=key, tag_label=label,
                broad=len(syms) > 4 or bool(ROUNDUP.search(head)))      # market round-ups mention many stocks: weak evidence


def _alpaca(sym: str, days: int, timeout: float) -> list[dict]:
    start = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = requests.get("https://data.alpaca.markets/v1beta1/news", timeout=timeout,
                     headers={"APCA-API-KEY-ID": os.getenv("ALPACA_API_KEY", ""), "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET_KEY", "")},
                     params={"symbols": sym, "start": start, "limit": 30, "sort": "desc", "include_content": "false"})
    r.raise_for_status()
    out = [_clean(x, sym) for x in (r.json().get("news") or [])]
    return [x for x in out if x and x["time"]]


def _demo(sym: str) -> list[dict]:
    now = pd.Timestamp.now(tz="UTC")
    rows = [dict(headline=f"Sample headline: {sym} raises full-year outlook (demo data)", source="Demo", url="",
                 created_at=(now - pd.Timedelta(hours=7)).isoformat(), symbols=[sym]),
            dict(headline=f"Sample headline: 12 stocks moving in today's session (demo data)", source="Demo", url="",
                 created_at=(now - pd.Timedelta(hours=30)).isoformat(), symbols=[sym, "A", "B", "C", "D", "E"])]
    return [_clean(x, sym) for x in rows]


def recent(sym: str, days: int = DAYS, timeout: float = 8) -> list[dict]:
    sym = sym.upper().strip()
    with _LOCK:
        hit = _CACHE.get(sym)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    if data.provider() == "demo":
        items = _demo(sym)
    elif data.provider() == "alpaca":
        items = _alpaca(sym, days, timeout)
    else:
        items = []
    with _LOCK:
        _CACHE[sym] = (time.time(), items)
    return items


def catalyst(items: list[dict], since: str | None) -> dict | None:
    """The headline most likely tied to a breakout: tagged, about this stock (not a round-up), and published in the
    two days before the breakout candle closed or since. Newest first. None when nothing fits."""
    if not since:
        return None
    try:
        t0 = pd.Timestamp(since)
        t0 = t0.tz_localize("UTC") if t0.tzinfo is None else t0.tz_convert("UTC")
    except Exception:
        return None
    lo = t0 - pd.Timedelta(hours=48)
    fit = [x for x in items if x["tag"] and not x["broad"] and pd.Timestamp(x["time"]) >= lo]
    return fit[0] if fit else None


def for_card(sym: str, candles: int | None = None) -> dict:
    """Headlines for the Shelf card. `candles` = 4-hour candles since a live breakout (None when nothing broke out)."""
    since = None
    if candles is not None:      # two candles a trading day, five trading days a week: about 17 hours of clock per candle
        since = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=17 * max(0, min(int(candles), 12)))).isoformat()
    try:
        items = recent(sym)
    except Exception as e:
        log.warning("news for %s failed: %s", sym, e)
        return dict(items=[], catalyst=None, ok=False)
    return dict(items=items[:8], catalyst=catalyst(items, since), ok=True, days=DAYS)


def _ago(ts: str) -> str:
    h = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(ts)).total_seconds() / 3600
    return "just now" if h < 1 else f"{int(h)}h ago" if h < 24 else f"{int(h // 24)}d ago"


def alert_line(sym: str, since) -> str:
    """One extra line for a breakout alert when a headline may explain it. Never raises, never slows an alert much."""
    try:
        c = catalyst(recent(sym, timeout=4), str(since))
        if not c:
            return ""
        return f"\n📰 Possible catalyst ({c['tag_label']}): {c['headline'][:140]} · {c['source']}, {_ago(c['time'])}"
    except Exception as e:
        log.info("news line for %s skipped: %s", sym, e)
        return ""
