"""Ask Sika to find setups: a deterministic list from the board rows.

Reads board.board() (the latest stored rows — never triggers a fresh scan)
and returns live breakouts/breakdowns whose targets have Room >= 1.5.
No model call, no new DB table, no new scheduled job. Pro-gated by the endpoint.
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

# "all" is the conversational convenience for "find me a set up".
# Specific requests remain restricted to their exact setup kind.
_KINDS = {
    "all": ["hub", "double_top", "double_bottom", "gapfade"],
    "hub": ["hub"],
    "double_top": ["double_top"],
    "double_bottom": ["double_bottom"],
    "double": ["double_top", "double_bottom"],
    "gapfade": ["gapfade"],
}

_GRADE_CACHE = {}          # (symbol, kind, direction) -> (grade dict, expires_at monotonic)
_GRADE_TTL_S = 12 * 3600    # grades are 2-year records: they barely move between board sweeps (warmed after each sweep)
GRADE_BUDGET_S = 12.0      # total grade-compute time per request (the cache is normally warm, so this rarely matters)
CAP = 8
_GRADE_RANK = {"A": 0, "B": 1, "C": 2, "D": 3, "?": 4}
_ROOM_MIN = 1.5             # retest-grade A-bar room threshold


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


def _room(edge, target, direction, width):
    """Retest-grade risk/reward with entry at the trigger: 2*d*(target-edge)/width."""
    if edge is None or target is None or width is None:
        return None
    try:
        width = float(width)
        edge = float(edge)
        target = float(target)
    except (TypeError, ValueError):
        return None
    if width <= 0:
        return None
    mid = edge - direction * width / 2
    risk = direction * (edge - mid)
    if risk <= 0:
        return None
    room = direction * (target - edge) / risk
    if room != room:  # NaN guard
        return None
    return room


def _hub_live(r, grade_fn):
    """Build a hub row only after its target passes Room >= 1.5."""
    retest = r.get("retest") or {}
    hub = r.get("hub") or {}
    edge = retest.get("edge")
    if edge is None:
        edge = hub.get("high") if r.get("state") == "breakout_up" else hub.get("low")
    if edge is None or r.get("target") is None:
        return None
    direction = 1 if r.get("state") == "breakout_up" else -1
    width = (hub.get("high") - hub.get("low")) if hub.get("high") is not None and hub.get("low") is not None else None
    room = _room(edge, r["target"], direction, width)
    if room is None or room < _ROOM_MIN:
        return None
    grade = grade_fn(r["symbol"], "hub", direction)
    return dict(symbol=r["symbol"], kind="hub", name=patterns.KINDS["hub"], state="live",
                direction=direction, grade=grade.get("grade"), grade_word=grade.get("word"),
                trigger=_money(edge), target=_money(r["target"]), room=round(room, 1),
                since=retest.get("candles_since"))


def _pattern_live(x, grade_fn):
    """Build a live pattern row only after its target passes Room >= 1.5."""
    if x.get("edge") is None or x.get("target") is None:
        return None
    direction = 1 if x.get("state") == "breakout_up" else -1
    if x["kind"] in ("double_top", "double_bottom"):
        far = x.get("far")
        try:
            width = abs(float(x["edge"]) - float(far)) if far is not None else None
        except (TypeError, ValueError):
            width = None
    elif x["kind"] == "gapfade":
        stop = x.get("stop")
        try:
            width = float(x["edge"]) - float(stop) if stop is not None else None
        except (TypeError, ValueError):
            width = None
    else:
        return None
    room = _room(x["edge"], x["target"], direction, width)
    if room is None or room < _ROOM_MIN:
        return None
    grade = grade_fn(x["symbol"], x["kind"], direction)
    return dict(symbol=x["symbol"], kind=x["kind"], name=patterns.KINDS[x["kind"]], state="live",
                direction=direction, grade=grade.get("grade"), grade_word=grade.get("word"),
                trigger=_money(x["edge"]), target=_money(x["target"]), room=round(room, 1),
                since=x.get("since"))


def _recency_key(row, board_updated):
    """Normalize board recency to age in seconds, including mixed 'all' scans.

    Hub rows expose candles_since; pattern rows expose an ISO breakout time.
    Convert both to an age relative to the same board update timestamp so an
    all-kinds scan can rank same-grade rows by recency meaningfully.
    """
    since = row.get("since")
    if isinstance(since, (int, float)):
        return max(0.0, float(since) * 4 * 60 * 60)
    if isinstance(since, str):
        try:
            stamp = datetime.fromisoformat(since.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            reference = _parse_utc(board_updated) or datetime.now(timezone.utc)
            return max(0.0, (reference - stamp.astimezone(timezone.utc)).total_seconds())
        except Exception:
            return float("inf")
    return float("inf")


_WARMING = {"on": False}


def _warm_bg():
    try:
        warm()
    finally:
        _WARMING["on"] = False


def warm() -> int:
    """After a board sweep: compute every live row's grade into the cache, with no time budget, so the Sika list
    shows real grades (never 'grade pending') and answers instantly."""
    return find("all", limit=1000, budget_s=600.0)["total"]


def warm_board_grades() -> int:
    """After a board sweep: cache the setup grade of EVERY live hub row, including the ones find()
    skips for Room < 1.5 — Patch 165's board order grades the whole live group. Same cache, same
    _grade; the board endpoint only ever reads the cache."""
    deadline = time.monotonic() + 600.0
    n = 0
    for r in board.board().get("live") or []:
        _grade_for(r["symbol"], "hub", 1 if r.get("state") == "breakout_up" else -1, deadline)
        n += 1
    return n


def find(kind: str, limit: int = CAP, budget_s: float = GRADE_BUDGET_S) -> dict:
    """Return live breakouts/breakdowns only, each with computable Room >= 1.5.

    Reads the latest stored board. No near/forming rows, no scan trigger, no writes.
    Room is checked before the comparatively expensive grade lookup; uncomputable
    room fails closed and the row is omitted.
    """
    if kind not in _KINDS:
        raise ValueError(f"Unknown setup kind: {kind}")
    board_kinds = _KINDS[kind]
    data = board.board()
    updated = data.get("updated")
    as_of = _as_of_iso(updated)
    stale = _is_stale(updated)
    deadline = time.monotonic() + budget_s
    grade_fn = lambda symbol, setup_kind, direction: _grade_for(symbol, setup_kind, direction, deadline)

    live = []
    if "hub" in board_kinds:
        for row in data.get("live") or []:
            if row.get("state") not in ("breakout_up", "breakdown"):
                continue
            result = _hub_live(row, grade_fn)
            if result:
                live.append(result)

    pattern_kinds = [k for k in board_kinds if k != "hub"]
    if pattern_kinds:
        for row in data.get("setups") or []:
            if row.get("kind") not in pattern_kinds:
                continue
            if row.get("state") not in ("breakout_up", "breakdown"):
                continue
            result = _pattern_live(row, grade_fn)
            if result:
                live.append(result)

    if budget_s < 600 and time.monotonic() > deadline and not _WARMING["on"]:
        # ran out of time before every grade was cached (e.g. right after a deploy): finish them in the background
        import threading
        _WARMING["on"] = True
        threading.Thread(target=_warm_bg, daemon=True).start()
    live.sort(key=lambda row: (_GRADE_RANK.get(row["grade"], 4), _recency_key(row, updated)))
    rows = live[:max(1, int(limit))]
    return dict(kind=kind, kinds=list(board_kinds), label=_label(kind), as_of=as_of,
                stale=stale, total=len(live), shown=len(rows), rows=rows)
