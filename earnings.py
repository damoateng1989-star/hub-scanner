"""Upcoming earnings dates, so members know when a setup carries gap risk.

Sources (whichever works first):
  1. Finnhub earnings calendar, if FINNHUB_API_KEY is set (free key at finnhub.io; one call covers every stock).
  2. Nasdaq's public earnings calendar (no key; one request per day ahead).
Demo mode makes up a few dates so the app can be tried without data.

The calendar is refreshed at most every 6 hours and saved, so a restart doesn't lose it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from datetime import date, datetime, timedelta

import requests

import data
import db

log = logging.getLogger("earnings")
AHEAD = 21                 # days of calendar to keep
TTL = 6 * 3600
_LOCK = threading.Lock()
_CAL: dict[str, dict] = {}  # symbol -> {"date": "2026-11-06", "when": "amc"|"bmo"|""}
_AT = 0.0

WHEN = {"bmo": "before the open", "amc": "after the close"}


def _today() -> date:
    import pandas as pd
    return pd.Timestamp.now(tz="America/New_York").date()


def _finnhub(start: date, end: date, latest: bool = False) -> dict[str, dict]:
    r = requests.get("https://finnhub.io/api/v1/calendar/earnings", timeout=20,
                     params={"from": start.isoformat(), "to": end.isoformat(), "token": os.getenv("FINNHUB_API_KEY", "")})
    r.raise_for_status()
    out = {}
    for x in r.json().get("earningsCalendar") or []:
        sym, d = (x.get("symbol") or "").upper(), x.get("date")
        if sym and d and (sym not in out or (d > out[sym]["date"] if latest else d < out[sym]["date"])):
            out[sym] = {"date": d, "when": x.get("hour") if x.get("hour") in WHEN else ""}
    return out


def _nasdaq(start: date, end: date, latest: bool = False) -> dict[str, dict]:
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
               "Accept": "application/json, text/plain, */*", "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    days = [d for d in days if d.weekday() < 5]

    def one(d):
        try:
            r = requests.get("https://api.nasdaq.com/api/calendar/earnings", params={"date": d.isoformat()}, headers=headers, timeout=8)
            return d, ((r.json() or {}).get("data") or {}).get("rows") or []
        except Exception as e:  # one bad day shouldn't lose the rest
            log.warning("nasdaq earnings %s failed: %s", d, e)
            return d, []
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as ex:
        results = sorted(ex.map(one, days))
    out = {}
    for d, rows in (reversed(results) if latest else results):
        for x in rows:
            sym = (x.get("symbol") or "").upper().replace("/", ".")
            t = x.get("time") or ""
            if sym and sym not in out:
                out[sym] = {"date": d.isoformat(), "when": "bmo" if "pre" in t else "amc" if "after" in t else ""}
    return out


def _demo(start: date) -> dict[str, dict]:
    """Stable made-up dates for the demo: about a third of tickers report within three weeks."""
    out = {}
    import universe
    for sym in set(universe.board_universe()) | {"AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META", "GOOGL", "AMZN", "INTC", "AVGO", "QCOM", "PANW", "COST", "CRWD", "PEP"}:
        h = int(hashlib.md5(f"{sym}{start.isocalendar()[1]}".encode()).hexdigest(), 16)
        if h % 3 == 0:
            d = start + timedelta(days=h % AHEAD)
            while d.weekday() >= 5:
                d += timedelta(days=1)
            out[sym] = {"date": d.isoformat(), "when": ("bmo", "amc")[h % 2]}
    return out


# ---------------------------------------------------------------- the last report (for setups that trade the earnings move)
BACK = 95                  # days of past reports to keep: covers the last quarter for every stock
_PAST: dict[str, dict] = {}
_PAST_AT = 0.0
_PAST_BUSY = threading.Lock()


def _demo_past(today: date) -> dict[str, dict]:
    out = {}
    import universe
    for sym in set(universe.board_universe()) | {"AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META", "GOOGL", "AMZN", "AMAT"}:
        h = int(hashlib.md5(f"past{sym}".encode()).hexdigest(), 16)
        d = today - timedelta(days=1 + h % 80)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        out[sym] = {"date": d.isoformat(), "when": ("bmo", "amc")[h % 2]}
    return out


def refresh_past(force: bool = False) -> int:
    """The most recent report in the last BACK days, per stock. At most once a day; saved so a restart keeps it."""
    global _PAST, _PAST_AT
    if not _PAST:
        try:
            saved = json.loads(db.get_setting("earnings_past") or "{}")
            _PAST, _PAST_AT = saved.get("cal", {}), saved.get("at", 0)
        except Exception:
            pass
    if not force and _PAST and time.time() - _PAST_AT < 86400:
        return len(_PAST)
    today = _today()
    start, end = today - timedelta(days=BACK), today - timedelta(days=1)
    try:
        if data.provider() == "demo":
            cal = _demo_past(today)
        elif os.getenv("FINNHUB_API_KEY"):
            cal = _finnhub(start, end, latest=True)
        else:
            cal = _nasdaq(start, end, latest=True)
    except Exception as e:
        log.warning("past earnings refresh failed: %s", e)
        return len(_PAST)
    if cal:
        merged = dict(_PAST)
        for s, x in cal.items():           # keep whichever report is newer
            if s not in merged or x["date"] >= merged[s]["date"]:
                merged[s] = x
        _PAST, _PAST_AT = merged, time.time()
        try:
            db.set_setting("earnings_past", json.dumps({"at": _PAST_AT, "cal": _PAST}))
        except Exception:
            pass
    return len(_PAST)


def _past_async():
    if (not _PAST or time.time() - _PAST_AT > 86400) and _PAST_BUSY.acquire(blocking=False):
        def run():
            try:
                refresh_past()
            finally:
                _PAST_BUSY.release()
        threading.Thread(target=run, daemon=True).start()


def last(sym: str) -> dict | None:
    """{"date", "days_ago", "when", "label"} for the most recent report, else None. Never blocks on the network.
    A report from the upcoming calendar that has now passed counts too, so a fresh report shows up the same day."""
    if not _PAST:
        try:
            saved = json.loads(db.get_setting("earnings_past") or "{}")
            _PAST.update(saved.get("cal", {}))
        except Exception:
            pass
    _past_async()
    sym = (sym or "").upper()
    cands = [x for x in (_PAST.get(sym), _CAL.get(sym)) if x and x.get("date") and x["date"] < _today().isoformat()]
    if not cands:
        return None
    x = max(cands, key=lambda c: c["date"])
    d = date.fromisoformat(x["date"])
    when = WHEN.get(x.get("when") or "", "")
    return dict(date=x["date"], days_ago=(_today() - d).days, when=x.get("when") or "",
                label=d.strftime("%a %b ") + str(d.day) + (f", {when}" if when else ""))


def refresh(force: bool = False) -> int:
    """Reload the calendar if it's stale. Returns how many symbols have a date."""
    global _CAL, _AT
    with _LOCK:
        if not _CAL and not force:
            try:
                saved = json.loads(db.get_setting("earnings_cal") or "{}")
                if saved.get("at", 0) > time.time() - TTL:
                    _CAL, _AT = saved.get("cal", {}), saved["at"]
            except Exception:
                pass
        if not force and _CAL and time.time() - _AT < TTL:
            return len(_CAL)
        start = _today()
        end = start + timedelta(days=AHEAD)
        try:
            if data.provider() == "demo":
                cal = _demo(start)
            elif os.getenv("FINNHUB_API_KEY"):
                cal = _finnhub(start, end)
            else:
                cal = _nasdaq(start, end)
        except Exception as e:
            log.warning("earnings refresh failed: %s", e)
            _AT = time.time() - TTL + 1800          # try again in 30 min, keep what we had
            return len(_CAL)
        if not cal:
            _AT = time.time() - TTL + 1800              # nothing came back: try again in 30 min, keep what we had
            return len(_CAL)
        if cal:
            _CAL, _AT = cal, time.time()
            try:
                db.set_setting("earnings_cal", json.dumps({"at": _AT, "cal": _CAL}))
            except Exception:
                pass
        return len(_CAL)


def _refresh_async():
    if (not _CAL or time.time() - _AT > TTL) and _BUSY.acquire(blocking=False):
        def run():
            try:
                refresh()
            finally:
                _BUSY.release()
        threading.Thread(target=run, daemon=True).start()


_LOADED = False
_BUSY = threading.Lock()


def _load_saved():
    """Use the last saved calendar straight away (even if a bit old) so no page ever waits for a refresh."""
    global _CAL, _AT, _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        saved = json.loads(db.get_setting("earnings_cal") or "{}")
        if saved.get("cal") and not _CAL:
            _CAL, _AT = saved["cal"], saved.get("at", 0)
    except Exception:
        pass


def lookup(sym: str) -> dict | None:
    """{"date", "days", "when", "label"} if the stock reports within AHEAD days, else None. Never blocks on the network."""
    _load_saved()
    _refresh_async()
    x = _CAL.get((sym or "").upper())
    if not x:
        return None
    d = date.fromisoformat(x["date"])
    days = (d - _today()).days
    if days < 0:
        return None
    day = "today" if days == 0 else "tomorrow" if days == 1 else d.strftime("%a %b ") + str(d.day)
    when = WHEN.get(x.get("when") or "", "")
    return dict(date=x["date"], days=days, when=x.get("when") or "", label=day + (f", {when}" if when else ""))


def many(symbols: list[str]) -> dict[str, dict]:
    """Upcoming report (date/days/when/label) and the last report ("last"), per symbol that has either."""
    out = {}
    for s in symbols:
        e = dict(lookup(s) or {})
        try:
            lt = last(s)
        except Exception:
            lt = None
        if lt:
            e["last"] = lt
        if e:
            out[s.upper()] = e
    return out


def alert_line(sym: str, within: int = 7) -> str:
    """Extra line for alerts when earnings are close: a gap can jump past both the hub and the shelf."""
    try:
        e = lookup(sym)
    except Exception:
        return ""
    if not e or e["days"] > within:
        return ""
    return f"\n📅 Earnings {e['label']}: expect a gap. Holding through it is a separate bet."
