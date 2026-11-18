"""Ask Sika to find setups: a deterministic list from the board rows.

Reads board.board() (the latest stored rows — never triggers a fresh scan)
and returns the tickers with the requested setup kind. No model call, no
new DB table, no new scheduled job. Pro-gated by the endpoint (options_for).
"""
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import board
import patterns
import scanner
import swing_card

NY = ZoneInfo("America/New_York")

_KINDS = {
    "all": ["hub", "double_top", "double_bottom", "gapfade"],
    "hub": ["hub"],
    "double_top": ["double_top"],
    "double_bottom": ["double_bottom"],
    "double": ["double_top", "double_bottom"],
    "gapfade": ["gapfade"],
}

_GRADE_CACHE = {}
_GRADE_TTL_S = 3600
GRADE_BUDGET_S = 5.0
CAP = 8
_GRADE_RANK = {"A": 0, "B": 1, "C": 2, "D": 3, "?": 4}


def _label(kind: str) -> str:
    if kind == "all":
        return "All setups"
    if kind == "double":
        return scanner.UNIVERSE_RECORD["double"]["label"]
    return patterns.KINDS[kind]


def _board_times():
    return [t.strip() for t in os.getenv("BOARD_TIMES", "13:52,16:22").split(",") if t.strip()]


def _parse_utc(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _as_of_iso(updated):
    dt = _parse_utc(updated)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _last_scheduled_scan(now_ny):
    for back in range(0, 8):
        day = (now_ny - timedelta(days=back)).date()
        if day.weekday() >= 5:
            continue
        for hm in sorted(_board_times(), reverse=True):
            h, m = map(int, hm.split(":"))
            cand = datetime(day.year, day.month, day.day, h, m, tzinfo=NY)
            if cand <= now_ny:
                return cand.astimezone(timezone.utc)
    return None


def _is_stale(updated):
    upd = _parse_utc(updated)
    if upd is None:
        return True
    last = _last_scheduled_scan(datetime.now(NY))
    if last is None:
        return False
    return upd < last


def _grade_for(symbol, kind, direction, deadline):
    key = (symbol, kind, direction)
    ent = _GRADE_CACHE.get(key)
    if ent and ent[1] > time.monotonic():
        return ent[0]
    if time.monotonic() > deadline:
        return dict(grade="?", word="Not enough history")
    try:
        g = swing_card._grade(symbol, kind, direction)
    except Exception:
        g = dict(grade="?", word="Not enough history")
    _GRADE_CACHE[key] = (g, time.monotonic() + _GRADE_TTL_S)
    return g


def _money(v):
    return round(float(v), 2) if v is not None else None


def _hub_live(r, grade_fn):
    retest = r.get("retest") or {}
    edge = retest.get("edge")
    if edge is None:
        hub = r.get("hub") or {}
        edge = hub.get("high") if r.get("state") == "breakout_up" else hub.get("low")
    if edge is None or r.get("target") is None:
        return None
    d = 1 if r.get("state") == "breakout_up" else -1
    g = grade_fn(r["symbol"], "hub", d)
    return dict(symbol=r["symbol"], kind="hub", name=patterns.KINDS["hub"], state="live",
                direction=d, grade=g.get("grade"), grade_word=g.get("word"),
                trigger=_money(edge), target=_money(r["target"]), distance_pct=None,
                since=retest.get("candles_since"))


def _hub_near(r):
    side = r.get("near_edge")
    if side not in ("up", "down"):
        return None
    trigger = r.get("trigger_up") if side == "up" else r.get("trigger_down")
    dist = r.get("to_high_pct") if side == "up" else r.get("to_low_pct")
    if trigger is None or dist is None:
        return None
    return dict(symbol=r["symbol"], kind="hub", name=patterns.KINDS["hub"], state="near",
                direction=1 if side == "up" else -1, grade=None, grade_word=None,
                trigger=_money(trigger), target=None,
                distance_pct=round(abs(float(dist)), 1), since=None)


def _pattern_live(x, grade_fn):
    if x.get("edge") is None or x.get("target") is None:
        return None
    d = 1 if x.get("state") == "breakout_up" else -1
    g = grade_fn(x["symbol"], x["kind"], d)
    return dict(symbol=x["symbol"], kind=x["kind"], name=patterns.KINDS[x["kind"]], state="live",
                direction=d, grade=g.get("grade"), grade_word=g.get("word"),
                trigger=_money(x["edge"]), target=_money(x["target"]), distance_pct=None,
                since=x.get("since"))


def _pattern_near(x):
    if x.get("state") != "forming" or not x.get("near_edge"):
        return None
    if x["kind"] in ("double_top", "double_bottom"):
        trigger = x.get("neck")
    elif x["kind"] == "gapfade":
        trigger = x.get("upper")
    else:
        return None
    last = x.get("last_close")
    if trigger is None or not last:
        return None
    return dict(symbol=x["symbol"], kind=x["kind"], name=patterns.KINDS[x["kind"]], state="near",
                direction=1 if x.get("near_edge") == "up" else -1, grade=None, grade_word=None,
                trigger=_money(trigger), target=None,
                distance_pct=round(abs((float(trigger) / float(last) - 1) * 100), 1), since=None)


def _recency_key(r):
    s = r.get("since")
    if isinstance(s, (int, float)):
        return float(s)
    if isinstance(s, str):
        try:
            return -datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0.0
    return 0.0


def find(kind: str) -> dict:
    if kind not in _KINDS:
        raise ValueError(f"Unknown setup kind: {kind}")
    board_kinds = _KINDS[kind]
    b = board.board()
    as_of = _as_of_iso(b.get("updated"))
    stale = _is_stale(b.get("updated"))
    deadline = time.monotonic() + GRADE_BUDGET_S
    grade_fn = lambda sym, k, d: _grade_for(sym, k, d, deadline)
    live, near = [], []
    if "hub" in board_kinds:
        for r in b.get("live") or []:
            row = _hub_live(r, grade_fn)
            if row:
                live.append(row)
        for r in b.get("near") or []:
            row = _hub_near(r)
            if row:
                near.append(row)
    if any(k != "hub" for k in board_kinds):
        for x in b.get("setups") or []:
            if x.get("kind") not in board_kinds:
                continue
            if x.get("state") in ("breakout_up", "breakdown"):
                row = _pattern_live(x, grade_fn)
                if row:
                    live.append(row)
            elif x.get("state") == "forming" and x.get("near_edge"):
                row = _pattern_near(x)
                if row:
                    near.append(row)
    live.sort(key=lambda r: (_GRADE_RANK.get(r["grade"], 4), _recency_key(r)))
    near.sort(key=lambda r: (r["distance_pct"] if r["distance_pct"] is not None else 99))
    rows = (live + near)[:CAP]
    return dict(kind=kind, kinds=list(board_kinds), label=_label(kind), as_of=as_of,
                stale=stale, total=len(live) + len(near), shown=len(rows), rows=rows)
