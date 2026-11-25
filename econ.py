"""Patch 219: the This Week card's macro calendar, from a feed instead of a hand-edited file.

Mirrors Patch 190's VIX pattern: fetch on a schedule, store in db settings, reads never fetch.
Context only, never a gate: nothing in detection, grades, alerts, the paper trader or the Lab reads econ_week.
"""
from __future__ import annotations

import json
import logging

import pandas as pd

import db

log = logging.getLogger("econ")
FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
NY = "America/New_York"
CAP = 14


def _clean(v) -> str:
    return str(v).strip() if v is not None else ""


def normalize(feed: list) -> list[dict]:
    """USD, High/Medium impact, in the card's {date, title, sub} schema, earliest first, at most 14."""
    rows = []
    for e in feed or []:
        try:
            if _clean(e.get("country")).upper() != "USD" or _clean(e.get("impact")) not in ("High", "Medium"):
                continue
            title = _clean(e.get("title"))
            t = pd.Timestamp(e["date"])
            t = t.tz_localize(NY) if t.tzinfo is None else t.tz_convert(NY)
        except Exception:
            continue
        if not title:
            continue
        parts = ["All day" if (t.hour, t.minute) == (0, 0) else t.strftime("%-I:%M %p") + " ET"]
        if _clean(e.get("impact")) == "High":
            parts.append("High impact")
        if _clean(e.get("forecast")):
            parts.append("forecast " + _clean(e.get("forecast")))
        if _clean(e.get("previous")):
            parts.append("prev " + _clean(e.get("previous")))
        rows.append(dict(date=t.strftime("%Y-%m-%d"), title=title, sub=" · ".join(parts), _t=t.isoformat()))
    rows.sort(key=lambda r: r["_t"])
    return rows[:CAP]


def refresh() -> list[dict] | None:
    """Fetch, normalize, store. Any failure keeps the last-good copy untouched and never raises."""
    try:
        import requests
        r = requests.get(FEED, timeout=10, headers={"User-Agent": UA, "Accept": "application/json"})
        r.raise_for_status()
        feed = r.json()
        if not isinstance(feed, list):
            raise ValueError("feed is not a list")
        rows = normalize(feed)
        db.set_setting("econ_week", json.dumps(rows))
        db.set_setting("econ_week_fetched_at", pd.Timestamp.now(tz="UTC").isoformat())
        log.info("econ calendar stored: %d USD high/medium events", len(rows))
        return rows
    except Exception as e:
        log.info("econ refresh failed (keeping the stored copy): %s", e)
        return None


def stored() -> list[dict]:
    """The stored list, or []. Never fetches."""
    try:
        v = json.loads(db.get_setting("econ_week") or "[]")
        return v if isinstance(v, list) else []
    except Exception:
        return []


def stale(hours: float = 36) -> bool:
    try:
        t = db.get_setting("econ_week_fetched_at")
        return not t or (pd.Timestamp.now(tz="UTC") - pd.Timestamp(t)) > pd.Timedelta(hours=hours)
    except Exception:
        return True


def merged(hand: dict) -> dict:
    """The hand file plus the stored feed: sorted by date then time, deduplicated on (date, title) — the hand entry wins.
    With nothing stored, exactly the hand file."""
    feed = stored()
    if not feed:
        return hand
    out = dict(hand)
    events = [dict(e, _src=0) for e in hand.get("events") or [] if isinstance(e, dict)]
    have = {(e.get("date"), str(e.get("title") or "").strip().lower()) for e in events}
    hand_days = {e.get("date") for e in events}
    for e in feed:
        # the feed's generic "Bank Holiday" says less than the hand file's own holiday line for that day
        if str(e.get("title") or "").strip().lower() == "bank holiday" and e.get("date") in hand_days:
            continue
        if (e.get("date"), str(e.get("title") or "").strip().lower()) not in have:
            events.append(dict(e, _src=1))

    def key(e):
        t = e.get("_t")
        if not t:        # a hand event: its "8:30 AM ET" sub if it has one, else the top of the day
            try:
                hm = pd.Timestamp(f"{e.get('date')} {str(e.get('sub') or '').split(' ET')[0]}")
                t = hm.isoformat()
            except Exception:
                t = f"{e.get('date')}T00:00:00"
        return (str(e.get("date") or ""), str(t)[11:16] if len(str(t)) > 16 else "00:00", e["_src"])
    events.sort(key=key)
    out["events"] = [{k: v for k, v in e.items() if not k.startswith("_")} for e in events]
    return out
