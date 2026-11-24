"""Lab: which hub breakouts work, and which exit works best.

Replays every historical hub breakout across the board universe (same rules as the live scanner), tags each one
with what was known at the breakout close (volume, market direction, chase, close margin, reward:risk, hub shape,
time of day), then scores it under several exit rules. Results go to the "lab_exits" setting for the admin Lab page.

Nothing here changes live alerts. It only measures.
"""
from __future__ import annotations

import json
import os
import gc
import logging
import sys
import resource
import threading
import time
import traceback

import numpy as np
import pandas as pd

import data
import db
import engine
import universe

log = logging.getLogger("lab")
DAYS = 720                       # the test period; each run can pick its own (2, 3, 5 or 10 years)
YEARS_OK = (2, 3, 5, 10)
CHUNK = 20
def _mem_limit_mb() -> int:
    """The server's memory size: LAB_MEM_MB if set, else the container limit (cgroup v2 or v1), else 512 (Render Starter)."""
    try:
        if os.environ.get("LAB_MEM_MB"):
            return int(os.environ["LAB_MEM_MB"])
    except ValueError:
        pass
    for f in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            v = open(f).read().strip()
            if v.isdigit() and int(v) < (1 << 50):
                return int(v) // (1024 * 1024)
        except Exception:
            pass
    return 512


MEM_LIMIT_MB = _mem_limit_mb()
# stop the test before Render kills the whole instance: 400 of 512 MB (78%), scaled when the instance is bigger
LAB_RSS_STOP_MB = int(os.environ.get("LAB_RSS_STOP_MB") or round(MEM_LIMIT_MB * 0.78))
_LOCK = threading.Lock()
_CANCEL = threading.Event()   # set by stop(): the run quits at the next stock
_RESTART = {"on": False}      # stop(restart=True): start a fresh run as soon as the old one has quit


class _Stopped(Exception):
    pass

EXITS = [   # key, label
    ("edge", "Current: out on a close back inside the pattern"),
    ("mid", "Out on a close past the middle of the pattern"),
    ("far", "Out on a close past the far side of the pattern"),
    ("half", "Half off at 1R, rest stopped at entry"),
    ("edge20", "Current, but 20 candles before giving up"),
    ("atr05", "Stop 0.5 ATR from entry"),
    ("atr10", "Stop 1 ATR from entry"),
]

GROUPS = [  # key, label, pattern kinds
    ("hub", "Hubs", ("hub",)),
    ("flag", "Flags", ("flag_bull", "flag_bear")),
    ("double", "Double tops & bottoms", ("double_top", "double_bottom")),
    ("triangle", "Triangles", ("triangle",)),
    ("channel", "Channels", ("channel_up", "channel_down")),
    ("sr", "Support & resistance", ("sr",)),
    ("gapfade", "Gap fade & rip", ("gapfade",)),
    ("fakeout", "Failed breakdowns & breakouts", ("fail_down", "fail_up")),
    ("flush", "Flush & reclaim", ("flush",)),
]
GROUP_OF = {k: g for g, _, kinds in GROUPS for k in kinds}

_has = lambda f, k, test: f.get(k) is not None and test(f[k])
FILTERS = [   # key, label, test(features) -> bool
    ("all", "Every breakout", lambda f: True),
    ("vol", "Volume 1.2× normal or more", lambda f: _has(f, "rvol", lambda v: v >= 1.2)),
    ("mkt", "With the market (SPY vs 50-day)", lambda f: f["with_mkt"] is True),
    ("against", "Against the market", lambda f: f["with_mkt"] is False),
    ("trend", "With the stock's own trend (vs its 50-day)", lambda f: f["with_trend"] is True),
    ("nochase", "Not chasing (entry in the first half)", lambda f: not f["chase"]),
    ("gapopen", "Opened past the edge (a premarket break)", lambda f: f.get("gap_open") is True),
    ("nogapopen", "Did not open past the edge", lambda f: f.get("gap_open") is False),
    ("margin", "Close clears the edge by 0.2 ATR+", lambda f: f["margin"] >= 0.2),
    ("strong", "Strong breaks only (volume 1.2×+ and 0.2 ATR+ past the edge)", lambda f: f["margin"] >= 0.2 and (f.get("rvol") is None or f["rvol"] >= 1.2)),
    ("weak", "Weak breaks only (missing either)", lambda f: not (f["margin"] >= 0.2 and (f.get("rvol") is None or f["rvol"] >= 1.2))),
    ("rr", "Room to the target: reward:risk 1.5+", lambda f: _has(f, "rr", lambda v: v >= 1.5)),
    ("tight", "Tight pattern (1 ATR wide or less)", lambda f: f["width"] <= 1.0),
    ("long", "Long pattern (4+ sessions)", lambda f: f["sessions"] >= 4),
    ("pm", "4:00 PM closes only", lambda f: f["pm"]),
    ("board", "Passes today's board filters ($10M/day, $25B cap)", lambda f: f["board_ok"]),
    ("dv100", "Trades $100M+ a day", lambda f: _has(f, "dv", lambda v: v >= 100e6)),
    ("dv250", "Trades $250M+ a day", lambda f: _has(f, "dv", lambda v: v >= 250e6)),
    ("dv500", "Trades $500M+ a day", lambda f: _has(f, "dv", lambda v: v >= 500e6)),
    ("dvlow", "Thinner names: under $100M a day", lambda f: _has(f, "dv", lambda v: v < 100e6)),
    ("up", "Longs only", lambda f: f["d"] == 1),
    ("down", "Shorts only", lambda f: f["d"] == -1),
    ("combo", "Volume + with the market + not chasing",
     lambda f: _has(f, "rvol", lambda v: v >= 1.2) and f["with_mkt"] is True and not f["chase"]),
    ("combo2", "With the market + not chasing + room to the target",
     lambda f: f["with_mkt"] is True and not f["chase"] and _has(f, "rr", lambda v: v >= 1.5)),
    ("combo3", "With the market + with the stock's trend + volume",
     lambda f: f["with_mkt"] is True and f["with_trend"] is True and _has(f, "rvol", lambda v: v >= 1.2)),
]
EXTRA = {   # filters that only make sense for one pattern
    "flag": [
        ("polevol", "Pole on heavy volume (1.3× the candles before it)", lambda f: _has(f, "pole_vol", lambda v: v >= 1.3)),
        ("shallow", "Shallow flag (gives back a third of the pole or less)", lambda f: _has(f, "retrace", lambda v: v <= 0.34)),
        ("quiet", "Quiet flag (volume dries up during the pause)", lambda f: _has(f, "flag_quiet", lambda v: v <= 0.75)),
        ("flagbest", "Heavy pole + shallow flag + with the market",
         lambda f: _has(f, "pole_vol", lambda v: v >= 1.3) and _has(f, "retrace", lambda v: v <= 0.34) and f["with_mkt"] is True),
    ],
    "sr": [("touch4", "Level tested 4+ times", lambda f: f["sessions"] >= 4)],
}


def _spy_regime() -> pd.Series | None:
    """Daily True/False: SPY closed above its 50-day average that day."""
    try:
        c4 = engine.to_4h(data.get_bars("SPY", DAYS + 120))
        daily = c4["Close"].groupby(c4.index.normalize()).last()
        return (daily > daily.rolling(50).mean()).where(daily.rolling(50).count() >= 50)
    except Exception:
        return None


_REGX = None      # market conditions by day (Nov 20, the regime test): set once per run by _spy_regime_ext()


def _spy_regime_ext():
    """Daily market conditions from SPY, for the regime test: above the 200-day, 20-day return positive, and calm
    (20-day realized volatility below its own one-year median). Values for a day use that day's close; trades read
    the PRIOR day (no look-ahead)."""
    try:
        c4 = engine.to_4h(data.get_bars("SPY", DAYS + 400))
        dc = c4["Close"].groupby(c4.index.normalize()).last()
        rv = dc.pct_change().rolling(20).std()
        return pd.DataFrame({
            "mkt200": (dc > dc.rolling(200).mean()).where(dc.rolling(200).count() >= 200),
            "mom20": (dc > dc.shift(20)).where(dc.shift(20).notna()),
            "calm": (rv < rv.rolling(252, min_periods=120).median()).where(rv.rolling(252, min_periods=120).count() >= 120),
        })
    except Exception:
        traceback.print_exc()
        return None


def _regx_as_of(on_day) -> dict:
    if _REGX is None:
        return {}
    past = _REGX[_REGX.index < on_day]
    if not len(past):
        return {}
    row = past.iloc[-1]
    return {k: (None if pd.isna(v) else bool(v)) for k, v in row.items()}


def _exit(close: np.ndarray, i0: int, d: int, entry: float, target: float, stop: float, horizon: int):
    """Close-based exits from the candle after entry. Returns (ret %, how) or None when the outcome isn't known yet."""
    n = len(close)
    for j in range(i0 + 1, min(i0 + 1 + horizon, n)):
        c = close[j]
        if (c - target) * d >= 0:
            return d * (target / entry - 1) * 100, "target"
        if (c - stop) * d <= 0:
            return d * (c / entry - 1) * 100, "stop"
    if i0 + horizon < n:
        return d * (close[i0 + horizon] / entry - 1) * 100, "time"
    return None


# ---------------------------------------------------------------- hub floor bounce (test only, not alerted)
# Buy a 4H close in the bottom quarter of a hub that's still forming (holding the floor, not a new low), aim for the hub top. Hub bounds are the
# closes SO FAR (point in time), never the hub's final range, so the test can't see the future.
FLOOR_ZONE = 0.25        # bottom quarter of the hub
FLOOR_EXITS = {
    "fl_close": "Stop: a 4H close below the floor · target: the hub top (on a close)",
    "fl_cush": "Stop: the floor minus 0.25 ATR, hit intraday · target: the hub top, hit intraday",
    "fl_tight": "Stop: right at the floor, hit intraday · target: the hub top, hit intraday",
    "fl_ride": "Stop: a 4H close below the floor · target: one hub-width above the top (20 candles)",
    "fl_half": "Stop: right at the floor, intraday · half off at the hub middle, then the stop moves to breakeven · rest at the hub top",
}


def _exit_half_intrabar(open_, high, low, close, i0, entry, mid, target, stop, horizon):
    """Long only. Half off at `mid` (hit intraday), the stop then moves to the entry; the rest goes to the target.
    Same worst-case rule as _exit_intrabar: a candle that touches both the stop and a target counts the stop."""
    n, half = len(close), False
    for j in range(i0 + 1, min(i0 + 1 + horizon, n)):
        st = entry if half else stop
        if low[j] <= st:
            px = min(open_[j], st)
            r = (px / entry - 1) * 100
            return ((mid / entry - 1) * 50 + r / 2, "stop") if half else (r, "stop")
        if not half and high[j] >= mid:
            half = True
            if high[j] >= target:
                return ((mid / entry - 1) * 50 + (max(open_[j], target) / entry - 1) * 50, "target")
            continue
        if half and high[j] >= target:
            return ((mid / entry - 1) * 50 + (max(open_[j], target) / entry - 1) * 50, "target")
    if i0 + horizon < n:
        r = (close[i0 + horizon] / entry - 1) * 100
        return ((mid / entry - 1) * 50 + r / 2, "time") if half else (r, "time")
    return None


# no shelf above a hub: which target works best? (A retest longs, stop 1 ATR under the retest entry)
NOSHELF_EXITS = {
    "nt_atr": "Target: 1 ATR past the hub top (current rule) · stop 1 ATR under entry",
    "nt_trig": "Target: 1 ATR past the breakout close · stop 1 ATR under entry",
    "nt_high": "Target: the nearest recent high above the breakout close (else 1 ATR past the top) · stop 1 ATR under entry",
}


def _exit_intrabar(open_, high, low, close, i0, entry, target, stop, horizon):
    """Long only. Stop/target hit by the candle's low/high; a gap past either fills at the open; when one candle
    touches both, count the stop (we can't know which came first, so assume the worse)."""
    n = len(close)
    for j in range(i0 + 1, min(i0 + 1 + horizon, n)):
        if low[j] <= stop:
            px = min(open_[j], stop)
            return (px / entry - 1) * 100, "stop"
        if high[j] >= target:
            px = max(open_[j], target)
            return (px / entry - 1) * 100, "target"
    if i0 + horizon < n:
        return (close[i0 + horizon] / entry - 1) * 100, "time"
    return None


def _floor_rows(sym, c4, res, s, atr, dvol, day, mc, regime=None, own=None):
    import board
    close = res["close"].to_numpy(float)

    def as_of(series, on_day):            # yesterday's value: no peeking at today's close
        if series is None:
            return None
        past = series[series.index < on_day].dropna()
        return bool(past.iloc[-1]) if len(past) else None
    open_, high, low = (c4[k].to_numpy(float) for k in ("Open", "High", "Low"))
    idx, out = c4.index, []
    for h in res["hubs"]:
        si, got_any, got_4, got_6 = h["start_i"], False, False, False
        older = [x for x in res["hubs"] if x["end_i"] < si]
        for j in range(h["formed_i"], h["end_i"] + 1):
            sess = (j - si + 1) // 2
            if got_any and (got_4 or sess < 4) and (got_6 or sess < 6):
                continue
            lo, hi = float(close[si:j].min()), float(close[si:j + 1].max())    # floor set BEFORE this candle
            if hi <= lo or close[j] < lo or close[j] > lo + FLOOR_ZONE * (hi - lo):
                continue                                      # not in the bottom quarter, or it just made a new low
            A = atr[j]
            if not A or A != A:
                continue
            entry, w = float(close[j]), hi - lo
            dv = None
            if dvol is not None:
                prior = dvol[dvol.index < day[j]]
                prior = prior[prior > 0].tail(20)
                dv = float(prior.mean()) if len(prior) >= 5 else None
            ex = {"fl_close": _exit(close, j, 1, entry, hi, lo - 1e-6, s.horizon),
                  "fl_cush": _exit_intrabar(open_, high, low, close, j, entry, hi, lo - 0.25 * A, s.horizon),
                  "fl_tight": _exit_intrabar(open_, high, low, close, j, entry, hi, lo, s.horizon),
                  "fl_ride": _exit(close, j, 1, entry, hi + w, lo - 1e-6, 20),
                  "fl_half": _exit_half_intrabar(open_, high, low, close, j, entry, (lo + hi) / 2, hi, lo, s.horizon)}
            ex = {k: (round(v[0], 3), v[1]) for k, v in ex.items() if v is not None}
            # one entry per hub for each rule: the first time it reaches the floor (any age), and the first time at 4+ sessions
            first_any, first_4, first_6 = not got_any, sess >= 4 and not got_4, sess >= 6 and not got_6
            got_any, got_4, got_6 = True, got_4 or sess >= 4, got_6 or sess >= 6
            # old support: an earlier hub's top or bottom within 0.5 ATR of this floor (two reasons for buyers to show up)
            support = any(abs(x["high"] - lo) <= 0.5 * A or abs(x["low"] - lo) <= 0.5 * A for x in older)
            if "fl_close" in ex:
                out.append(dict(sym=sym, grp="floor", entry="floor", t=str(idx[j]), d=1, dv=dv,
                                board_ok=board.quality_skip(sym, dv, mc) is None, first_any=first_any, first_4=first_4, first_6=first_6,
                                sessions=sess, width=w / A, rr=(hi - entry) / max(entry - lo, 1e-9), support=support,
                                with_mkt=as_of(regime, day[j]), with_trend=as_of(own, day[j]), ex=ex))
    return out


# ---------------------------------------------------------------- hub edge close (test only, not alerted)
# The AMZN question (Nov 17): go long on the first 4H close above a resting hub's top, even though the engine doesn't
# count it as a breakout yet (the close only stretches the hub: it's below max(top, recent closes + k x ATR)).
# Point in time: the hub top/bottom are the closes SO FAR, the trigger uses only closes before this candle.
EDGE_EXITS = {
    "ed_trig": "Target: the real breakout trigger · stop: a 4H close below the hub",
    "ed_shelf": "Target: the next shelf above (else 1 ATR past the trigger) · stop: a 4H close below the hub",
    "ed_tight": "Target: the next shelf above, hit intraday · stop: the hub bottom, hit intraday",
}


def _edge_rows(sym, c4, res, s, atr, dvol, day, mc):
    import board
    close = res["close"].to_numpy(float)
    open_, high, low = (c4[k].to_numpy(float) for k in ("Open", "High", "Low"))
    idx, out, W = c4.index, [], s.window
    for h in res["hubs"]:
        si = h["start_i"]
        for j in range(max(h["formed_i"], si + W - 1, 1), h["end_i"] + 1):
            lo, hi = float(close[si:j].min()), float(close[si:j].max())          # the hub so far, before this candle
            A = atr[j - 1]
            if not A or A != A or hi <= 0:
                continue
            prev = close[max(si, j - (W - 1)):j]
            trig = max(hi, float(prev.min()) + s.k * A)                           # the close that would end the hub
            c = float(close[j])
            if not (hi < c < trig):
                continue                                                          # not above the top, or already a real breakout
            shelves = [x["low"] for x in res["hubs"] if x["end_i"] < si and x["low"] > c]
            shelf = min(shelves) if shelves else trig + s.target_atr * A
            dv = None
            if dvol is not None:
                prior = dvol[dvol.index < day[j]]
                prior = prior[prior > 0].tail(20)
                dv = float(prior.mean()) if len(prior) >= 5 else None
            ex = {"ed_trig": _exit(close, j, 1, c, trig, lo - 1e-6, s.horizon),
                  "ed_shelf": _exit(close, j, 1, c, shelf, lo - 1e-6, s.horizon),
                  "ed_tight": _exit_intrabar(open_, high, low, close, j, c, shelf, lo, s.horizon)}
            ex = {k: (round(v[0], 3), v[1]) for k, v in ex.items() if v is not None}
            if "ed_shelf" in ex:
                out.append(dict(sym=sym, grp="edge", entry="edge", t=str(idx[j]), d=1, dv=dv,
                                board_ok=board.quality_skip(sym, dv, mc) is None, sessions=(j - si + 1) // 2,
                                width=(hi - lo) / A, tight=(hi - lo) < 0.5 * A, rr=(shelf - c) / max(c - lo, 1e-9), ex=ex))
            break                                                                 # one entry per hub: the first such close
    return out


# ---------------------------------------------------------------- gap fade: buy the open vs wait for the close (test only)
# The MU question (Nov 18): pre-market is already above the gap-day high, so why wait for the daily close?
# Proxy for "pre-market above the trigger": the session OPENS above the gap-day high (the Lab has no pre-market bars).
# Same gap events as the live rule. Open entry: stop = the lowest low known before the open, 2R target, the same
# close-based walk as the live rule, but the entry day's own close counts (you're in from 9:30).
GAPGO_EXITS = {
    "gg_open": "Buy the open (opened above the gap-day high) · 2R target · stop on a close below the fade low",
    "gg_close": "Wait for the daily close above the gap-day high (the live rule) · 2R target · same stop",
}


def _gg_walk(close, start, entry, stop, target, horizon):
    n = len(close)
    for j in range(start, min(start + horizon, n)):
        if close[j] >= target:
            return (close[j] / entry - 1) * 100, "target"
        if close[j] <= stop:
            return (close[j] / entry - 1) * 100, "stop"
    end = start + horizon - 1
    if end < n:
        return (close[end] / entry - 1) * 100, "time"
    return None


def _gapgo_rows(sym, c4, s):
    d = engine.complete_days(c4)
    if len(d) < engine.PEG_MIN_DAILY:
        return []
    close, open_, high, low = (d[c].to_numpy(float) for c in ("Close", "Open", "High", "Low"))
    n, out, i = len(d), [], 1
    G, W, RW = engine.PEG_GAP, engine.PEG_FADE_WICK, engine.PEG_RECLAIM_MAX
    isgap = lambda k: open_[k] >= close[k - 1] * (1 + G) and high[k] > low[k] and (high[k] - close[k]) >= W * (high[k] - low[k])
    while i < n:
        if not isgap(i):
            i += 1
            continue
        g, gh, fade_low = i, float(high[i]), float(low[i])
        k_open = k_close = None
        nxt = None
        for k in range(g + 1, min(g + RW, n - 1) + 1):
            if k_open is None and open_[k] > gh:
                k_open, stop_open = k, fade_low                     # stop known before the open; bought before we
            if isgap(k):                                            # know this day turns into a new gap-and-fade
                nxt = k
                break
            if close[k] < fade_low:
                break                                               # dead
            fade_low = min(fade_low, float(low[k]))
            if close[k] > gh:
                k_close = k
                break
        if k_open is not None:
            ex = {}
            e = float(open_[k_open])
            R = e - stop_open
            if R > 0:
                r = _gg_walk(close, k_open, e, stop_open, e + engine.PEG_TARGET_R * R, s.horizon + 1)
                if r:
                    ex["gg_open"] = (round(r[0], 3), r[1])
            if k_close is not None:
                e2 = float(close[k_close])
                st2 = float(low[g:k_close + 1].min())
                R2 = e2 - st2
                if R2 > 0:
                    r = _gg_walk(close, k_close + 1, e2, st2, e2 + engine.PEG_TARGET_R * R2, s.horizon)
                    if r:
                        ex["gg_close"] = (round(r[0], 3), r[1])
            held = bool(close[k_open] > gh)                         # that same day closed above too: the close rule fired
            if "gg_open" in ex:
                out.append(dict(sym=sym, grp="gapgo", entry="gapgo", t=str(d.index[k_open]), d=1, held=held,
                                margin=(e - gh) / gh * 100, ex=ex))
        i = (k_close + 1) if k_close is not None else (nxt if nxt is not None else g + 1)
    return out


# ---------------------------------------------------------------- the Stacked Shelf (test only, Nov 19)
# A+B hub retests (longs, the live record's trades), each tagged with the stacked rules from stacked.py using only
# levels that existed at the retest bar. Two exits: the usual shelf target, and the nearest stack beyond the entry.
STACKED_EXITS = {
    "stk_shelf": "Retest entry · shelf target · 1 ATR stop · close-based, 10 candles (the live rule)",
    "stk_stack": "Retest entry · target = the nearest stack (2+ kinds) beyond the entry, else the shelf · same stop",
}


def _close_exit(close, j, entry, stop, target, horizon):
    n = len(close)
    for k in range(j + 1, min(j + 1 + horizon, n)):
        if close[k] >= target:
            return (round((target / entry - 1) * 100, 3), "target")
        if close[k] <= stop:
            return (round((close[k] / entry - 1) * 100, 3), "stop")
    if j + horizon < n:
        return (round((close[j + horizon] / entry - 1) * 100, 3), "time")
    return None


def _stacked_rows(sym, c4, res, s):
    import stacked
    close = res["close"].to_numpy(float)
    low, high, atr = c4["Low"].to_numpy(float), c4["High"].to_numpy(float), res["atr"].to_numpy(float)
    hist = stacked.History(c4, res)
    out = []
    for t in res["trades"]:
        if t["direction"] != 1:
            continue
        rt = _retest_entry(close, low, high, atr, t["i0"], 1, t["edge"])
        if not rt:
            continue
        j, entry = rt
        g = engine.retest_grade(t, entry, float(atr[j]))
        if g.get("grade") not in ("A", "B"):
            continue
        tl = stacked.tol(entry, float(atr[j]))
        cands = hist.at(j, own_hub=t["hub"], price=entry)
        chk = stacked.check(cands, 1, float(t["edge"]), entry, float(t["target"]), tl)
        ex = {}
        r = _close_exit(close, j, entry, g["stop"], float(t["target"]), s.horizon)
        if r:
            ex["stk_shelf"] = r
        tgt2 = chk["stack_target"] if chk["stack_target"] and chk["stack_target"] > entry else float(t["target"])
        r = _close_exit(close, j, entry, g["stop"], tgt2, s.horizon)
        if r:
            ex["stk_stack"] = r
        if ex:
            out.append(dict(sym=sym, grp="stacked", entry="stacked", t=str(c4.index[j]), d=1, ex=ex,
                            st_trig=chk["trig_stacked"], st_tgt=chk["target_stacked"], st_path=chk["clear_path"],
                            st_has=chk["stack_target"] is not None, st_all=chk["stacked"], grade=g.get("grade")))
    return out


# ---------------------------------------------------------------- the Stacked Double Bottom (test only, Nov 19)
# Double bottom A retests (longs, 4 PM, not chasing: the live rule), each tagged with the stacked rules using only
# levels that existed at the retest bar. Two exits: the usual measured target, and the nearest stack beyond the entry.
DBSTK_EXITS = {
    "db_shelf": "Retest entry · measured target · stop at the pattern low · close-based, 10 candles (the live rule)",
    "db_stack": "Retest entry · target = the nearest stack (2+ kinds) beyond the entry, else the measured target · same stop",
}


def _dbstk_rows(sym, c4, res, s):
    import stacked
    import patterns
    close_s = res["close"]
    close = close_s.to_numpy(float)
    low, high, atr = c4["Low"].to_numpy(float), c4["High"].to_numpy(float), res["atr"].to_numpy(float)
    idx = c4.index
    hist = None
    out = []
    for p in patterns._find_doubles(c4, atr, close_s):
        if p["active"] or p["kind"] != "double_bottom" or p["brk"] is None:
            continue
        i0, edge = p["brk"], float(p["neck"])
        target, shelf = patterns._target(res, p["a"], 1, edge, float(atr[i0]), s)
        t = dict(kind="double_bottom", hub=dict(start_i=p["a"], end_i=i0 - 1, low=p["cl"], high=p["ch"], sessions=p["sessions"]),
                 i0=i0, direction=1, edge=edge, target=target)
        rt = _retest_entry(close, low, high, atr, i0, 1, edge)
        if not rt:
            continue
        j, entry = rt
        g = engine.double_retest_grade(t, entry, engine.candle_end(idx[j].tz_convert(engine.NY)).hour == 16)
        if g.get("grade") != "A":
            continue
        if hist is None:
            hist = stacked.History(c4, res)
        tl = stacked.tol(entry, float(atr[j]))
        cands = hist.at(j, own_hub=t["hub"], price=entry)
        chk = stacked.check(cands, 1, edge, entry, float(target), tl)
        ex = {}
        r = _close_exit(close, j, entry, g["stop"], float(target), s.horizon)
        if r:
            ex["db_shelf"] = r
        tgt2 = chk["stack_target"] if chk["stack_target"] and chk["stack_target"] > entry else float(target)
        r = _close_exit(close, j, entry, g["stop"], tgt2, s.horizon)
        if r:
            ex["db_stack"] = r
        if ex:
            out.append(dict(sym=sym, grp="dbstk", entry="dbstk", t=str(idx[j]), d=1, ex=ex,
                            st_trig=chk["trig_stacked"], st_tgt=chk["target_stacked"], st_path=chk["clear_path"],
                            st_has=chk["stack_target"] is not None, st_all=chk["stacked"], grade="A"))
    return out


# ---------------------------------------------------------------- the Stacked Gap Fade (test only, Nov 19)
# Post-earnings gap fade & rip trades (the live rule: daily close above the gap-day high), each tagged with the
# stacked rules using only price levels that existed at the entry close. Daily close-based walk, same horizon.
GAPSTK_EXITS = {
    "gs_2r": "Close above the gap-day high · 2R target · stop on a close below the fade low · daily closes (the live rule)",
    "gs_stack": "Same entry and stop · target = the nearest stack (2+ kinds) beyond the entry, else 2R",
}


def _gapstk_rows(sym, c4, res, s):
    import patterns
    import stacked
    out_p = {"trades": [], "live": []}
    patterns._gapfade(c4, res["close"], len(c4), s, out_p)
    if not out_p["trades"]:
        return []
    d = engine.complete_days(c4)
    dclose = d["Close"].to_numpy(float)
    dkey = d.index.tz_convert(engine.NY).normalize()
    day4 = c4.index.tz_convert(engine.NY).normalize()
    atr = res["atr"].to_numpy(float)
    hist = stacked.History(c4, res)
    rows = []
    for t in out_p["trades"]:
        j = t["i0"]
        di = int(np.searchsorted(dkey, day4[j]))
        if di >= len(dkey) or dkey[di] != day4[j]:
            continue
        entry, stop, tgt = float(t["entry"]), float(t["stop"]), float(t["target"])
        tl = stacked.tol(entry, float(atr[j]))
        cands = hist.at(j, own_hub=t["hub"], price=entry)          # pivots from the gap day/fade are skipped
        chk = stacked.check(cands, 1, float(t["edge"]), entry, tgt, tl)
        ex = {}
        r = _gg_walk(dclose, di + 1, entry, stop, tgt, s.horizon)
        if r:
            ex["gs_2r"] = (round(r[0], 3), r[1])
        t2 = chk["stack_target"] if chk["stack_target"] and chk["stack_target"] > entry else tgt
        r = _gg_walk(dclose, di + 1, entry, stop, t2, s.horizon)
        if r:
            ex["gs_stack"] = (round(r[0], 3), r[1])
        if ex:
            rows.append(dict(sym=sym, grp="gapstk", entry="gapstk", t=str(c4.index[j]), d=1, ex=ex,
                             st_trig=chk["trig_stacked"], st_tgt=chk["target_stacked"], st_path=chk["clear_path"],
                             st_has=chk["stack_target"] is not None, st_all=chk["stacked"]))
    return rows


def _exit_half(close: np.ndarray, i0: int, d: int, entry: float, target: float, stop: float, r: float, horizon: int):
    """Half off at entry + 1R (or the target if that comes first); the rest is stopped at entry once half is off."""
    n, lvl, half = len(close), entry + d * r, None
    for j in range(i0 + 1, min(i0 + 1 + horizon, n)):
        c = close[j]
        if (c - target) * d >= 0:
            rest = d * (target / entry - 1) * 100
            return ((half if half is not None else rest) + rest) / 2, "target"
        if half is None and (c - lvl) * d >= 0:
            half = d * (lvl / entry - 1) * 100
            continue
        cur_stop = entry if half is not None else stop
        if (c - cur_stop) * d <= 0:
            rest = d * (c / entry - 1) * 100
            return ((half if half is not None else rest) + rest) / 2, "stop"
    if i0 + horizon < n:
        rest = d * (close[i0 + horizon] / entry - 1) * 100
        return ((half if half is not None else rest) + rest) / 2, "time"
    return None


def _flag_feats(c4: pd.DataFrame, p: dict, d: int) -> dict:
    """Pole volume vs the candles before it, how much of the pole the flag gave back, and how quiet the flag was."""
    try:
        v = c4["Volume"].to_numpy(float)
        ps, a, b = p["pole_start"], p["a"], p["b"]
        pole, before, flag = v[ps + 1:a], v[max(0, ps - 19):ps + 1], v[a:b + 1]
        pole_m, before_m, flag_m = pole[pole > 0].mean(), before[before > 0].mean(), flag[flag > 0].mean()
        hi, lo = c4["High"].to_numpy(float), c4["Low"].to_numpy(float)
        top = hi[ps:a].max() if d == 1 else lo[ps:a].min()
        back = (top - lo[a:b + 1].min()) if d == 1 else (hi[a:b + 1].max() - top)
        return dict(pole_vol=float(pole_m / before_m) if before_m > 0 else None,
                    flag_quiet=float(flag_m / pole_m) if pole_m > 0 else None,
                    retrace=float(back / p["pole_height"]) if p["pole_height"] else None)
    except Exception:
        return {}


def _retest_entry(close, low, high, atr, i0, d, edge):
    """First close back near the edge after the breakout, within RETEST_MAX candles.
    Returns (j, entry) or None. Dead on any close back inside the edge first."""
    A0 = atr[i0]
    if not A0 or A0 != A0:
        return None
    n = len(close)
    for j in range(i0 + 1, min(i0 + 1 + engine.RETEST_MAX, n)):
        c = close[j]
        if (c - edge) * d < 0:                      # closed back inside: breakout failed, no retest
            return None
        touched = (low[j] <= edge + engine.RETEST_TOUCH * A0) if d == 1 else (high[j] >= edge - engine.RETEST_TOUCH * A0)
        near = (edge < c <= edge + engine.RETEST_NEAR * A0) if d == 1 else (edge - engine.RETEST_NEAR * A0 <= c < edge)
        if touched and near:
            return j, float(c)
    return None


def trades_for(sym: str, c4: pd.DataFrame, s: engine.Settings, regime: pd.Series | None, only_tests: bool = False) -> list[dict]:
    if c4 is None or len(c4) < 60:
        return []
    import board
    import patterns
    if only_tests == "gapgo":                                 # lightest run: only the gap-fade open test (daily candles, no hub scan)
        return _gapgo_rows(sym, c4, s)
    res = engine.analyze(c4, s)
    if only_tests == "stacked":                               # only the Stacked Shelf test
        return _stacked_rows(sym, c4, res, s)
    if only_tests == "dbstk":                                 # only the Stacked Double Bottom test
        return _dbstk_rows(sym, c4, res, s)
    if only_tests == "gapstk":                                # only the Stacked Gap Fade test
        return _gapstk_rows(sym, c4, res, s)
    if only_tests and only_tests not in ("regime", "pfprint", "flags"):     # a light run: just the test-only setups (floor bounce, hub edge)
        atr = res["atr"].to_numpy(float)
        day = c4.index.tz_convert(engine.NY).normalize()
        dvol = (c4["Volume"] * c4["Close"]).groupby(day).sum() if "Volume" in c4.columns else None
        mc = universe.market_cap(sym)
        dclose = res["close"].groupby(day).last()
        own = (dclose > dclose.rolling(50).mean()).where(dclose.rolling(50).count() >= 50)
        return (_floor_rows(sym, c4, res, s, atr, dvol, day, mc, regime, own) + _edge_rows(sym, c4, res, s, atr, dvol, day, mc)
                + _gapgo_rows(sym, c4, s))
    try:
        other = patterns.analyze_all(c4, res, s, live_only=False)["trades"]
    except Exception:
        traceback.print_exc()
        other = []
    close = res["close"].to_numpy(float)
    atr = res["atr"].to_numpy(float)
    low = c4["Low"].to_numpy(float)
    high = c4["High"].to_numpy(float)
    idx = c4.index
    day = idx.tz_convert(engine.NY).normalize()
    dvol = (c4["Volume"] * c4["Close"]).groupby(day).sum() if "Volume" in c4.columns else None
    dclose = res["close"].groupby(day).last()                 # the stock's own 50-day trend
    own = (dclose > dclose.rolling(50).mean()).where(dclose.rolling(50).count() >= 50)
    mc = universe.market_cap(sym)
    out = []
    if only_tests == "regime":      # the regime test: only hub and double retests, the live alerts' rows
        other = [t for t in other if t.get("kind") in ("double_top", "double_bottom")]
    elif only_tests == "pfprint":   # Nov 24 PF print: double bottoms (with clear path) + gap fade, as the full run scores them
        other = [t for t in other if t.get("kind") in ("double_bottom", "gapfade")]
    elif only_tests == "flags":     # Nov 24 flags retest test: bull and bear flags only
        other = [t for t in other if t.get("kind") in ("flag_bull", "flag_bear")]
    else:
        try:
            out += _floor_rows(sym, c4, res, s, atr, dvol, day, mc, regime, own)
        except Exception:
            traceback.print_exc()
        try:
            out += _edge_rows(sym, c4, res, s, atr, dvol, day, mc)
        except Exception:
            traceback.print_exc()
        try:
            out += _gapgo_rows(sym, c4, s)
        except Exception:
            traceback.print_exc()
    hubs_in = [] if only_tests in ("pfprint", "flags") else [dict(x, kind="hub") for x in res["trades"]]
    st_hist = None
    for t in hubs_in + other:
        kind = t.get("kind", "hub")
        grp = GROUP_OF.get(kind)
        if not grp or t["outcome"] == "pending" or t.get("chase") or t.get("i0") is None:
            continue                                          # chase = target already hit on the entry close: no trade
        h, i0, d = t["hub"], t["i0"], t["direction"]
        entry, edge, target = float(t["entry"]), float(t["edge"]), float(t["target"])
        A = atr[i0]
        if not A or A != A:
            continue
        w = (h["high"] - h["low"]) if h["high"] > h["low"] else A   # a single level (S&R) has no width: use 1 ATR
        mid, far = edge - d * w / 2, edge - d * w
        risk_mid = d * (entry - mid)
        today = day[i0]

        def _as_of(series, on_day):
            if series is None:
                return None
            past = series[series.index < on_day].dropna()
            return bool(past.iloc[-1]) if len(past) else None
        mkt_up, own_up = _as_of(regime, today), _as_of(own, today)
        dv = None
        if dvol is not None:
            prior = dvol[dvol.index < today]
            prior = prior[prior > 0].tail(20)
            dv = float(prior.mean()) if len(prior) >= 5 else None
        f = dict(sym=sym, grp=grp, entry="breakout", t=str(idx[i0]), d=d, dv=dv,
                 board_ok=board.quality_skip(sym, dv, mc) is None,
                 rvol=engine.rvol(c4, i0),
                 with_mkt=None if mkt_up is None else (mkt_up if d == 1 else not mkt_up),
                 with_trend=None if own_up is None else (own_up if d == 1 else not own_up),
                 chase=d * (entry - edge) > 0.5 * d * (target - edge),
                 margin=d * (entry - edge) / A,
                 rr=(d * (target - entry) / risk_mid) if risk_mid > 0 else None,
                 width=(h["high"] - h["low"]) / A,
                 sessions=h.get("sessions") or 0,
                 pm=engine.candle_end(idx[i0].tz_convert(engine.NY)).hour == 16)
        try:      # a premarket break, as the Lab can see it: the breakout is the day's first candle and it OPENED past the edge
            t0 = idx[i0].tz_convert(engine.NY)
            f["gap_open"] = bool(t0.hour == 9 and t0.minute == 30 and d * (float(c4["Open"].iloc[i0]) - edge) > 0)
        except Exception:
            f["gap_open"] = False
        if grp == "flag":
            f.update(_flag_feats(c4, t["p"], d))
        if grp in ("gapfade", "flush"):                       # daily clock with its own exit rule: scored as recorded
            if t.get("ret") is None:
                continue
            f["ex"] = {"edge": (round(float(t["ret"]), 3), "target" if t["outcome"] == "follow-through" else "stop")}
            out.append(f)
            continue
        ex = {}
        cur = float(t["stop"]) if grp == "fakeout" else edge   # fakeouts: the live rule's stop is the fake-out extreme
        for key, stop, hz in (("edge", cur, s.horizon), ("mid", mid, s.horizon), ("far", far, s.horizon),
                              ("edge20", cur, 20),
                              ("atr05", entry - d * 0.5 * A, s.horizon),     # fixed at entry, no trailing
                              ("atr10", entry - d * 1.0 * A, s.horizon)):
            ex[key] = _exit(close, i0, d, entry, target, stop, hz)
        ex["half"] = _exit_half(close, i0, d, entry, target, edge, risk_mid if risk_mid > 0 else d * (entry - edge), s.horizon)
        f["ex"] = {k: (round(v[0], 3), v[1]) for k, v in ex.items() if v is not None}
        if "edge" in f["ex"] and only_tests not in ("regime", "pfprint", "flags"):
            out.append(f)

        # --- Patch 79: the same breakout, entered on the retest instead ---
        rt = _retest_entry(close, low, high, atr, i0, d, edge)
        if rt is not None:
            j, entry2 = rt
            A2 = atr[j]
            if A2 and A2 == A2:
                today2 = day[j]
                dv2 = None
                if dvol is not None:
                    prior2 = dvol[dvol.index < today2]
                    prior2 = prior2[prior2 > 0].tail(20)
                    dv2 = float(prior2.mean()) if len(prior2) >= 5 else None
                mkt2 = _as_of(regime, today2)
                own2 = _as_of(own, today2)
                risk2 = d * (entry2 - mid)
                f2 = dict(f, entry="retest", t=str(idx[j]),
                          dv=dv2, board_ok=board.quality_skip(sym, dv2, mc) is None,
                          rvol=engine.rvol(c4, j),
                          with_mkt=None if mkt2 is None else (mkt2 if d == 1 else not mkt2),
                          with_trend=None if own2 is None else (own2 if d == 1 else not own2),
                          chase=d * (entry2 - edge) > 0.5 * d * (target - edge),
                          margin=d * (entry2 - edge) / A2,
                          rr=(d * (target - entry2) / risk2) if risk2 > 0 else None,
                          pm=engine.candle_end(idx[j].tz_convert(engine.NY)).hour == 16,
                          **_regx_as_of(today2))
                ex2 = {}
                for key, stop, hz in (("edge", cur, s.horizon), ("mid", mid, s.horizon), ("far", far, s.horizon),
                                      ("edge20", cur, 20),
                                      ("atr05", entry2 - d * 0.5 * A2, s.horizon),
                                      ("atr10", entry2 - d * 1.0 * A2, s.horizon)):
                    ex2[key] = _exit(close, j, d, entry2, target, stop, hz)
                ex2["half"] = _exit_half(close, j, d, entry2, target, edge, risk2 if risk2 > 0 else d * (entry2 - edge), s.horizon)
                if grp == "hub" and d == 1 and not t.get("shelf"):          # no shelf: compare targets
                    f2["no_shelf"] = True
                    stp = entry2 - A2
                    ex2["nt_atr"] = ex2.get("atr10")
                    t_trig = entry + A
                    try:
                        oh = engine.old_extremes(c4.iloc[:i0 + 2], entry)["old_high"]
                    except Exception:
                        oh = None
                    t_high = oh["level"] if oh and oh["level"] > entry2 else edge + s.target_atr * A
                    for k_, tg in (("nt_trig", t_trig), ("nt_high", t_high)):
                        ex2[k_] = _exit(close, j, 1, entry2, tg, stp, s.horizon) if tg > entry2 else None
                    # room (reward / risk to the hub middle) measured against each target, as the A grade does
                    f2["rr_nt"] = {"nt_atr": f2.get("rr"), "nt_trig": (t_trig - entry2) / risk2 if risk2 > 0 else None,
                                   "nt_high": (t_high - entry2) / risk2 if risk2 > 0 else None}
                f2["ex"] = {k: (round(v[0], 3), v[1]) for k, v in ex2.items() if v is not None}
                if only_tests == "pfprint" and grp == "double" and d == 1:     # clear path at the retest bar (the hub print's rule)
                    try:
                        import stacked
                        if st_hist is None:
                            st_hist = stacked.History(c4, res)
                        chk = stacked.check(st_hist.at(j, own_hub=h, price=entry2), 1, edge, entry2, target, stacked.tol(entry2, float(A2)))
                        f2["st_path"] = bool(chk["clear_path"])
                    except Exception:
                        traceback.print_exc()
                if "edge" in f2["ex"]:
                    out.append(f2)
    return out


def _score(rows: list[dict], key: str) -> dict | None:
    got = [(r["t"], r["ex"][key]) for r in rows if key in r["ex"]]
    if not got:
        return None
    got.sort()
    rets = np.array([g[1][0] for g in got])
    wins, losses = rets[rets > 0], rets[rets <= 0]
    half = len(rets) // 2
    pf = (wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else None
    return dict(n=len(rets),
                win=round(100 * len(wins) / len(rets), 1),
                reached=round(100 * sum(g[1][1] == "target" for g in got) / len(got), 1),
                avg=round(float(rets.mean()), 2),
                avg_win=round(float(wins.mean()), 2) if len(wins) else None,
                avg_loss=round(float(losses.mean()), 2) if len(losses) else None,
                pf=round(float(pf), 2) if pf is not None else None,
                early=round(float(rets[:half].mean()), 2) if half >= 10 else None,
                late=round(float(rets[half:].mean()), 2) if len(rets) - half >= 10 else None)


ATOMS = ("vol", "mkt", "trend", "nochase", "margin", "rr", "tight", "long", "pm", "dv100", "dv250", "dv500", "up", "down",
         "polevol", "shallow", "quiet", "touch4")
MIN_BEST = 100          # a winning filter must keep at least this many trades


TOP_N = 5          # concentration check: drop the 5 stocks that made the most and see if it still makes money
MIN_STOCKS = 15    # and it has to have traded at least this many different stocks


def _spread(x: np.ndarray, syms: np.ndarray) -> dict:
    """How broad the profit is: stocks traded, share of the profit from the top TOP_N stocks, and the result without them."""
    tot = {}
    for r, sy in zip(x, syms):
        tot[sy] = tot.get(sy, 0.0) + float(r)
    top = sorted(tot, key=lambda k: -tot[k])[:TOP_N]
    pos = sum(v for v in tot.values() if v > 0)
    keep = ~np.isin(syms, top)
    rest = x[keep]
    return dict(stocks=len(tot), top=top,
                top_share=round(100 * sum(max(tot[k], 0) for k in top) / pos, 1) if pos > 0 else None,
                n_ex_top=int(keep.sum()), avg_ex_top=round(float(rest.mean()), 2) if len(rest) else None)


def best_filters(mine: list[dict], filters: list, stats: dict | None = None) -> list[dict]:
    """Search every combination of up to 3 simple filters, under every exit rule, for ones that made money in BOTH
    halves of the test window with enough trades, across at least MIN_STOCKS stocks, and still made money with the
    TOP_N best stocks removed (so a few hot names can't carry it). Ranked by the weaker half."""
    from itertools import combinations
    if len(mine) < MIN_BEST:
        return []
    mine = sorted(mine, key=lambda r: r["t"])
    syms = np.array([r["sym"] for r in mine])
    narrow = []
    atoms = [(k, l, np.array([bool(t(r)) for r in mine])) for k, l, t in filters if k in ATOMS]
    atoms = [a for a in atoms if a[2].sum() >= MIN_BEST]
    found = []
    for ek, elabel in EXITS:
        rets = np.array([r["ex"][ek][0] if ek in r["ex"] else np.nan for r in mine])
        ok = ~np.isnan(rets)
        if ok.sum() < MIN_BEST:
            continue
        for size in (1, 2, 3):
            for combo in combinations(atoms, size):
                keys = {c[0] for c in combo}
                if {"up", "down"} <= keys or len(keys & {"dv100", "dv250", "dv500"}) > 1:
                    continue
                m = ok.copy()
                for c in combo:
                    m &= c[2]
                n = int(m.sum())
                if n < MIN_BEST:
                    continue
                x = rets[m]
                early, late = float(x[:n // 2].mean()), float(x[n // 2:].mean())
                if early <= 0 or late <= 0:
                    continue
                wins, losses = x[x > 0], x[x <= 0]
                row = dict(filters=[c[1] for c in combo], keys=sorted(keys), exit=ek, exit_label=elabel, n=n,
                           win=round(100 * len(wins) / n, 1), avg=round(float(x.mean()), 2),
                           pf=round(float(wins.sum() / -losses.sum()), 2) if losses.sum() < 0 else None,
                           early=round(early, 2), late=round(late, 2), size=size, **_spread(x, syms[m]))
                if row["stocks"] < MIN_STOCKS or not (row["avg_ex_top"] or 0) > 0:
                    narrow.append(row)            # positive, but only thanks to a few stocks
                    continue
                found.append(row)
    found.sort(key=lambda f: (-min(f["early"], f["late"]), f["size"], -f["n"]))
    out, seen = [], set()
    for f in found:                       # top 3, no near-duplicates (same filters under another exit)
        k = tuple(f["keys"])
        if k in seen:
            continue
        seen.add(k)
        out.append(f)
        if len(out) == 3:
            break
    if stats is not None:
        narrow.sort(key=lambda f: (-min(f["early"], f["late"]), -f["n"]))
        stats.update(narrow=len({tuple(f["keys"]) for f in narrow}), narrow_top=narrow[0] if narrow else None)
    return out


def summarize(rows: list[dict]) -> dict:
    groups = []
    for gk, glabel, _ in GROUPS:
        mine = [r for r in rows if r["grp"] == gk]
        filters = FILTERS + EXTRA.get(gk, [])

        def build(sub_rows):
            table = []
            for fk, flabel, test in filters:
                sub = [r for r in sub_rows if test(r)]
                table.append(dict(key=fk, label=flabel, exits={ek: _score(sub, ek) for ek, _ in EXITS}))
            st = {}
            return table, (best_filters(sub_rows, filters, st), st)
        mine_b = [r for r in mine if r.get("entry", "breakout") == "breakout"]
        # gap fade and flush have no retest variant: their retest tables ARE the breakout tables
        mine_r = [r for r in mine if r.get("entry") == "retest"] if gk not in ("gapfade", "flush") else mine_b
        table, (best, st) = build(mine_b)
        table_r, (best_r, st_r) = build(mine_r)
        groups.append(dict(key=gk, label=glabel, trades=len(mine_b), trades_retest=len(mine_r),
                           filters=table, best=best, filters_retest=table_r, best_retest=best_r,
                           narrow=st, narrow_retest=st_r))
    return dict(groups=groups, exits=[dict(key=k, label=l) for k, l in EXITS], graduation=graduation(rows))


# ---------------------------------------------------------------- leaving beta: the live rules, scored exactly as alerted
GRAD_MIN_N, GRAD_MIN_PF = 100, 1.2
LIVE_RULES = [
    # key, label, which rows, which exit, measured? (the A grades were picked by the best-filters search on longs)
    ("hub_long", "Hubs: A retest, longs", lambda r: r["grp"] == "hub" and r.get("entry") == "retest" and r["d"] == 1
     and (r.get("rr") or 0) >= 1.5 and (r.get("sessions") or 0) >= 4, "atr10"),
    ("hub_short", "Hubs: A retest, shorts", lambda r: r["grp"] == "hub" and r.get("entry") == "retest" and r["d"] == -1
     and (r.get("rr") or 0) >= 1.5 and (r.get("sessions") or 0) >= 4, "atr10"),
    ("gapopen_hub_close", "Premarket hub breaks, longs: entered at the breakout close", lambda r: r["grp"] == "hub" and r.get("entry") == "breakout"
     and r["d"] == 1 and r.get("gap_open"), "edge"),
    ("gapopen_hub_retest", "Premarket hub breaks, longs: entered on the retest", lambda r: r["grp"] == "hub" and r.get("entry") == "retest"
     and r["d"] == 1 and r.get("gap_open"), "atr10"),
    ("hub_long_b", "Hubs: B retest, longs (room 1.0+, 3+ sessions, not A)", lambda r: r["grp"] == "hub" and r.get("entry") == "retest" and r["d"] == 1
     and (r.get("rr") or 0) >= engine.B_ROOM and (r.get("sessions") or 0) >= engine.B_SESSIONS
     and not ((r.get("rr") or 0) >= 1.5 and (r.get("sessions") or 0) >= 4), "atr10"),
    ("double_long_b", "Double bottoms: B retest (1:30 PM close, not chasing)", lambda r: r["grp"] == "double" and r.get("entry") == "retest" and r["d"] == 1
     and not r.get("pm") and not r.get("chase"), "far"),
    ("double_long", "Double bottoms: A retest", lambda r: r["grp"] == "double" and r.get("entry") == "retest" and r["d"] == 1
     and r.get("pm") and not r.get("chase"), "far"),
    ("double_short", "Double tops: A retest", lambda r: r["grp"] == "double" and r.get("entry") == "retest" and r["d"] == -1
     and r.get("pm") and not r.get("chase"), "far"),
    ("gapfade", "Gap fade & rip", lambda r: r["grp"] == "gapfade", "edge"),
    # test only (not alerted): buying the bottom of a hub
    ("floor_close", "Test · Hub floor bounce: stop on a close below the floor (hub 4+ sessions)",
     lambda r: r["grp"] == "floor" and r.get("first_4"), "fl_close"),
    ("floor_cush", "Test · Hub floor bounce: stop 0.25 ATR under the floor (hub 4+ sessions)",
     lambda r: r["grp"] == "floor" and r.get("first_4"), "fl_cush"),
    ("floor_tight", "Test · Hub floor bounce: stop right at the floor (hub 4+ sessions)",
     lambda r: r["grp"] == "floor" and r.get("first_4"), "fl_tight"),
    ("floor_ride", "Test · Hub floor bounce: hold for one hub-width above the top (hub 4+ sessions)",
     lambda r: r["grp"] == "floor" and r.get("first_4"), "fl_ride"),
    ("floor_any", "Test · Hub floor bounce: stop on a close below the floor (any hub)",
     lambda r: r["grp"] == "floor" and r.get("first_any"), "fl_close"),
    # test only (Nov 18): floor bounce with a few filters picked BEFORE looking (uptrend, old support, mature hub, room),
    # all on the intraday stop at the floor (the best exit above), plus the half-off exit
    ("floor_trend", "Test · Floor bounce, uptrends only (stock and market above their 50-day)",
     lambda r: r["grp"] == "floor" and r.get("first_4") and r.get("with_trend") is True and r.get("with_mkt") is True, "fl_tight"),
    ("floor_support", "Test · Floor bounce, floor is also old support (an earlier hub level within 0.5 ATR)",
     lambda r: r["grp"] == "floor" and r.get("first_4") and r.get("support"), "fl_tight"),
    ("floor_mature", "Test · Floor bounce, mature hubs only (6+ sessions)",
     lambda r: r["grp"] == "floor" and r.get("first_6"), "fl_tight"),
    ("floor_room", "Test · Floor bounce, room to the top of 1 ATR or more",
     lambda r: r["grp"] == "floor" and r.get("first_4") and (r.get("width") or 0) >= 1, "fl_tight"),
    ("floor_all", "Test · Floor bounce, all four together (uptrend, support, 6+ sessions, room)",
     lambda r: r["grp"] == "floor" and r.get("first_6") and r.get("with_trend") is True and r.get("with_mkt") is True
     and r.get("support") and (r.get("width") or 0) >= 1, "fl_tight"),
    ("floor_half", "Test · Floor bounce: half off at the hub middle, then stop to breakeven",
     lambda r: r["grp"] == "floor" and r.get("first_4"), "fl_half"),
    ("floor_trend_half", "Test · Floor bounce, uptrends only, with the half-off exit",
     lambda r: r["grp"] == "floor" and r.get("first_4") and r.get("with_trend") is True and r.get("with_mkt") is True, "fl_half"),
    # test only (Nov 17, the AMZN question): long on the first close above a resting hub's top, before the real trigger
    ("edge_trig", "Test · Close above a resting hub's top (not a breakout yet): target the real trigger",
     lambda r: r["grp"] == "edge", "ed_trig"),
    ("edge_shelf", "Test · Close above a resting hub's top (not a breakout yet): target the next shelf",
     lambda r: r["grp"] == "edge", "ed_shelf"),
    ("edge_tight", "Test · Close above a resting hub's top: next shelf, intraday stop at the hub bottom",
     lambda r: r["grp"] == "edge", "ed_tight"),
    ("edge_shelf_tighthub", "Test · Same, tight hubs only (under 0.5 ATR wide, like AMZN): target the next shelf",
     lambda r: r["grp"] == "edge" and r.get("tight"), "ed_shelf"),
    ("edge_tight_tighthub", "Test · Same, tight hubs only: next shelf, intraday stop at the hub bottom",
     lambda r: r["grp"] == "edge" and r.get("tight"), "ed_tight"),
    # test only (Nov 20): the regime test — does the live A retest only work in some markets?
    ("rg_hub_all", "Regime · Hubs A retest longs: every market (the baseline)",
     lambda r: r["grp"] == "hub" and r.get("entry") == "retest" and r["d"] == 1 and (r.get("rr") or 0) >= 1.5 and (r.get("sessions") or 0) >= 4, "atr10"),
    ("rg_hub_mkt50", "Regime · Hubs A retest longs: SPY above its 50-day",
     lambda r: r["grp"] == "hub" and r.get("entry") == "retest" and r["d"] == 1 and (r.get("rr") or 0) >= 1.5 and (r.get("sessions") or 0) >= 4 and r.get("with_mkt") is True, "atr10"),
    ("rg_hub_mkt50_no", "Regime · Hubs A retest longs: SPY below its 50-day (for comparison)",
     lambda r: r["grp"] == "hub" and r.get("entry") == "retest" and r["d"] == 1 and (r.get("rr") or 0) >= 1.5 and (r.get("sessions") or 0) >= 4 and r.get("with_mkt") is False, "atr10"),
    ("rg_hub_mkt200", "Regime · Hubs A retest longs: SPY above its 200-day",
     lambda r: r["grp"] == "hub" and r.get("entry") == "retest" and r["d"] == 1 and (r.get("rr") or 0) >= 1.5 and (r.get("sessions") or 0) >= 4 and r.get("mkt200") is True, "atr10"),
    ("rg_hub_mom", "Regime · Hubs A retest longs: SPY up over the last 20 days",
     lambda r: r["grp"] == "hub" and r.get("entry") == "retest" and r["d"] == 1 and (r.get("rr") or 0) >= 1.5 and (r.get("sessions") or 0) >= 4 and r.get("mom20") is True, "atr10"),
    ("rg_hub_calm", "Regime · Hubs A retest longs: calm market (SPY volatility below its 1-year median)",
     lambda r: r["grp"] == "hub" and r.get("entry") == "retest" and r["d"] == 1 and (r.get("rr") or 0) >= 1.5 and (r.get("sessions") or 0) >= 4 and r.get("calm") is True, "atr10"),
    ("rg_hub_wild", "Regime · Hubs A retest longs: volatile market (for comparison)",
     lambda r: r["grp"] == "hub" and r.get("entry") == "retest" and r["d"] == 1 and (r.get("rr") or 0) >= 1.5 and (r.get("sessions") or 0) >= 4 and r.get("calm") is False, "atr10"),
    ("rg_hub_trend", "Regime · Hubs A retest longs: the stock above its own 50-day",
     lambda r: r["grp"] == "hub" and r.get("entry") == "retest" and r["d"] == 1 and (r.get("rr") or 0) >= 1.5 and (r.get("sessions") or 0) >= 4 and r.get("with_trend") is True, "atr10"),
    ("rg_hub_best", "Regime · Hubs A retest longs: SPY above 50-day + calm",
     lambda r: r["grp"] == "hub" and r.get("entry") == "retest" and r["d"] == 1 and (r.get("rr") or 0) >= 1.5 and (r.get("sessions") or 0) >= 4 and r.get("with_mkt") is True and r.get("calm") is True, "atr10"),
    ("rg_hub_both", "Regime · Hubs A retest longs: SPY and the stock both above their 50-day",
     lambda r: r["grp"] == "hub" and r.get("entry") == "retest" and r["d"] == 1 and (r.get("rr") or 0) >= 1.5 and (r.get("sessions") or 0) >= 4 and r.get("with_mkt") is True and r.get("with_trend") is True, "atr10"),
    ("rg_db_all", "Regime · Double bottoms A retest: every market (the baseline)",
     lambda r: r["grp"] == "double" and r.get("entry") == "retest" and r["d"] == 1 and r.get("pm") and not r.get("chase"), "far"),
    ("rg_db_mkt50", "Regime · Double bottoms A retest: SPY above its 50-day",
     lambda r: r["grp"] == "double" and r.get("entry") == "retest" and r["d"] == 1 and r.get("pm") and not r.get("chase") and r.get("with_mkt") is True, "far"),
    ("rg_db_calm", "Regime · Double bottoms A retest: calm market",
     lambda r: r["grp"] == "double" and r.get("entry") == "retest" and r["d"] == 1 and r.get("pm") and not r.get("chase") and r.get("calm") is True, "far"),
    # Nov 24 PF print (spec "PF print — doubles + gap fade"): the published rules, plus the doubles clear / blocked split
    ("pf_db", "PF print · Double bottom A retests (headline; must reproduce +0.28% / 236 / 75%)",
     lambda r: r["grp"] == "double" and r.get("entry") == "retest" and r["d"] == 1 and r.get("pm") and not r.get("chase"), "far"),
    ("pf_db_clear", "PF print · Double bottom A retests, clear path",
     lambda r: r["grp"] == "double" and r.get("entry") == "retest" and r["d"] == 1 and r.get("pm") and not r.get("chase") and r.get("st_path") is True, "far"),
    ("pf_db_blocked", "PF print · Double bottom A retests, blocked",
     lambda r: r["grp"] == "double" and r.get("entry") == "retest" and r["d"] == 1 and r.get("pm") and not r.get("chase") and r.get("st_path") is not True, "far"),
    ("pf_gap", "PF print · Gap fade & rip (headline; must reproduce +2.76% / 341 / 60%)",
     lambda r: r["grp"] == "gapfade", "edge"),
    # Nov 24 flags retest test (spec "Flags retest Lab test"): baseline, no filters, the hub A card's exit (1 ATR stop)
    ("flg_bull", "Flags · Bull flag retests (no filters, 1 ATR stop)",
     lambda r: r["grp"] == "flag" and r.get("entry") == "retest" and r["d"] == 1, "atr10"),
    ("flg_bear", "Flags · Bear flag retests (short, no filters, 1 ATR stop)",
     lambda r: r["grp"] == "flag" and r.get("entry") == "retest" and r["d"] == -1, "atr10"),
    ("flg_all", "Flags · Combined (the reconciliation card)",
     lambda r: r["grp"] == "flag" and r.get("entry") == "retest", "atr10"),
    # test only (Nov 19): the Stacked Double Bottom — DB A retests with stacked trigger / stacked target / clear path
    ("db_base", "Test · Double bottom: all A retests (the baseline, measured target)",
     lambda r: r["grp"] == "dbstk", "db_shelf"),
    ("db_trig", "Test · Neckline stacked (another kind of level on it)",
     lambda r: r["grp"] == "dbstk" and r.get("st_trig"), "db_shelf"),
    ("db_notrig", "Test · Neckline NOT stacked (for comparison)",
     lambda r: r["grp"] == "dbstk" and not r.get("st_trig"), "db_shelf"),
    ("db_tgt", "Test · Measured target has a level on it",
     lambda r: r["grp"] == "dbstk" and r.get("st_tgt"), "db_shelf"),
    ("db_path", "Test · Clear path (no 2+ stack between the neckline and the target)",
     lambda r: r["grp"] == "dbstk" and r.get("st_path"), "db_shelf"),
    ("db_all", "Test · Full Stacked Double Bottom, measured target",
     lambda r: r["grp"] == "dbstk" and r.get("st_all"), "db_shelf"),
    ("db_all_stack", "Test · Full Stacked Double Bottom, aiming at the nearest stack",
     lambda r: r["grp"] == "dbstk" and r.get("st_all"), "db_stack"),
    ("db_base_stack", "Test · All double bottom A retests, aiming at the nearest stack",
     lambda r: r["grp"] == "dbstk", "db_stack"),
    # Nov 19 (after the first Stacked Shelf run): clear path was the edge, a stacked trigger did worse
    ("db_path_clean", "Test · Stacked Double Bottom: clear path + neckline NOT stacked, measured target",
     lambda r: r["grp"] == "dbstk" and r.get("st_path") and not r.get("st_trig"), "db_shelf"),
    ("db_path_clean_stack", "Test · Stacked Double Bottom: clear path + neckline NOT stacked, aiming at the nearest stack",
     lambda r: r["grp"] == "dbstk" and r.get("st_path") and not r.get("st_trig"), "db_stack"),
    ("db_path_stack", "Test · Stacked Double Bottom: clear path only, aiming at the nearest stack",
     lambda r: r["grp"] == "dbstk" and r.get("st_path"), "db_stack"),
    ("db_v2", "Test · New Stacked Double Bottom (clear path + clean neckline + a stack at or beyond the target), measured target",
     lambda r: r["grp"] == "dbstk" and r.get("st_path") and not r.get("st_trig") and (r.get("st_tgt") or r.get("st_has")), "db_shelf"),
    ("db_v2_stack", "Test · New Stacked Double Bottom, aiming at the nearest stack",
     lambda r: r["grp"] == "dbstk" and r.get("st_path") and not r.get("st_trig") and (r.get("st_tgt") or r.get("st_has")), "db_stack"),
    # test only (Nov 19): the Stacked Gap Fade — gap fade & rip with stacked trigger / stacked target / clear path
    ("gs_base", "Test · Gap fade & rip: all trades (the baseline, 2R target)",
     lambda r: r["grp"] == "gapstk", "gs_2r"),
    ("gs_trig", "Test · Gap-day high stacked (another kind of level on it)",
     lambda r: r["grp"] == "gapstk" and r.get("st_trig"), "gs_2r"),
    ("gs_notrig", "Test · Gap-day high NOT stacked (for comparison)",
     lambda r: r["grp"] == "gapstk" and not r.get("st_trig"), "gs_2r"),
    ("gs_tgt", "Test · 2R target has a level on it",
     lambda r: r["grp"] == "gapstk" and r.get("st_tgt"), "gs_2r"),
    ("gs_path", "Test · Clear path (no 2+ stack between the gap-day high and the 2R target)",
     lambda r: r["grp"] == "gapstk" and r.get("st_path"), "gs_2r"),
    ("gs_all", "Test · Full Stacked Gap Fade, 2R target",
     lambda r: r["grp"] == "gapstk" and r.get("st_all"), "gs_2r"),
    ("gs_all_stack", "Test · Full Stacked Gap Fade, aiming at the nearest stack",
     lambda r: r["grp"] == "gapstk" and r.get("st_all"), "gs_stack"),
    ("gs_base_stack", "Test · All gap fades, aiming at the nearest stack instead of 2R",
     lambda r: r["grp"] == "gapstk", "gs_stack"),
    # Nov 19 (after the first Stacked Shelf run): clear path was the edge, a stacked trigger did worse
    ("gs_path_clean", "Test · Stacked Gap Fade: clear path + gap-day high NOT stacked, 2R target",
     lambda r: r["grp"] == "gapstk" and r.get("st_path") and not r.get("st_trig"), "gs_2r"),
    ("gs_path_clean_stack", "Test · Stacked Gap Fade: clear path + gap-day high NOT stacked, aiming at the nearest stack",
     lambda r: r["grp"] == "gapstk" and r.get("st_path") and not r.get("st_trig"), "gs_stack"),
    ("gs_path_stack", "Test · Stacked Gap Fade: clear path only, aiming at the nearest stack",
     lambda r: r["grp"] == "gapstk" and r.get("st_path"), "gs_stack"),
    ("gs_v2", "Test · New Stacked Gap Fade (clear path + clean gap-day high + a stack at or beyond the target), 2R target",
     lambda r: r["grp"] == "gapstk" and r.get("st_path") and not r.get("st_trig") and (r.get("st_tgt") or r.get("st_has")), "gs_2r"),
    ("gs_v2_stack", "Test · New Stacked Gap Fade, aiming at the nearest stack",
     lambda r: r["grp"] == "gapstk" and r.get("st_path") and not r.get("st_trig") and (r.get("st_tgt") or r.get("st_has")), "gs_stack"),
    # test only (Nov 19): the Stacked Shelf — A+B hub retests with stacked trigger / stacked target / clear path
    ("stk_base", "Test · Stacked Shelf: all A+B hub retests (the baseline, shelf target)",
     lambda r: r["grp"] == "stacked", "stk_shelf"),
    ("stk_trig", "Test · Stacked trigger (another kind of level on the hub edge)",
     lambda r: r["grp"] == "stacked" and r.get("st_trig"), "stk_shelf"),
    ("stk_notrig", "Test · Trigger NOT stacked (for comparison)",
     lambda r: r["grp"] == "stacked" and not r.get("st_trig"), "stk_shelf"),
    ("stk_tgt", "Test · Shelf target has a level on it",
     lambda r: r["grp"] == "stacked" and r.get("st_tgt"), "stk_shelf"),
    ("stk_path", "Test · Clear path (no 2+ stack between trigger and target)",
     lambda r: r["grp"] == "stacked" and r.get("st_path"), "stk_shelf"),
    ("stk_a_clear", "Test · Clear path, A retests only (Patch 198: the A population)",
     lambda r: r["grp"] == "stacked" and r.get("grade") == "A" and r.get("st_path"), "stk_shelf"),
    ("stk_a_blocked", "Test · Stack in the way, A retests only (Patch 198: the step-down population)",
     lambda r: r["grp"] == "stacked" and r.get("grade") == "A" and not r.get("st_path"), "stk_shelf"),
    ("stk_all", "Test · Full Stacked Shelf (stacked trigger + clear path + stacked target), shelf target",
     lambda r: r["grp"] == "stacked" and r.get("st_all"), "stk_shelf"),
    ("stk_all_stack", "Test · Full Stacked Shelf, aiming at the nearest stack",
     lambda r: r["grp"] == "stacked" and r.get("st_all"), "stk_stack"),
    ("stk_base_stack", "Test · All A+B retests, aiming at the nearest stack instead of the shelf",
     lambda r: r["grp"] == "stacked", "stk_stack"),
    # Nov 19 (after the first Stacked Shelf run): clear path was the edge, a stacked trigger did worse
    ("stk_path_clean", "Test · Stacked Shelf: clear path + trigger NOT stacked, shelf target",
     lambda r: r["grp"] == "stacked" and r.get("st_path") and not r.get("st_trig"), "stk_shelf"),
    ("stk_path_clean_stack", "Test · Stacked Shelf: clear path + trigger NOT stacked, aiming at the nearest stack",
     lambda r: r["grp"] == "stacked" and r.get("st_path") and not r.get("st_trig"), "stk_stack"),
    ("stk_path_stack", "Test · Stacked Shelf: clear path only, aiming at the nearest stack",
     lambda r: r["grp"] == "stacked" and r.get("st_path"), "stk_stack"),
    ("stk_v2", "Test · New Stacked Shelf (clear path + clean trigger + a stack at or beyond the target), shelf target",
     lambda r: r["grp"] == "stacked" and r.get("st_path") and not r.get("st_trig") and (r.get("st_tgt") or r.get("st_has")), "stk_shelf"),
    ("stk_v2_stack", "Test · New Stacked Shelf, aiming at the nearest stack",
     lambda r: r["grp"] == "stacked" and r.get("st_path") and not r.get("st_trig") and (r.get("st_tgt") or r.get("st_has")), "stk_stack"),
    # test only (Nov 18, the MU question): gap fades where the session OPENS above the gap-day high. Buy the open, or wait for the close?
    ("gapgo_open", "Test · Gap fade, opened above the gap-day high: buy the open",
     lambda r: r["grp"] == "gapgo", "gg_open"),
    ("gapgo_close", "Test · Same gaps: wait for the daily close (the live rule)",
     lambda r: r["grp"] == "gapgo", "gg_close"),
    ("gapgo_open_held", "Test · Opened above and also closed above that day: bought the open",
     lambda r: r["grp"] == "gapgo" and r.get("held"), "gg_open"),
    ("gapgo_close_held", "Test · Opened above and also closed above that day: waited for the close",
     lambda r: r["grp"] == "gapgo" and r.get("held"), "gg_close"),
    ("gapgo_open_faded", "Test · Opened above but closed back below that day: bought the open (the trades waiting skips)",
     lambda r: r["grp"] == "gapgo" and not r.get("held"), "gg_open"),
    # test only: hubs with no shelf above, A retest longs, three targets
    *[(f"noshelf_{k[3:]}", f"Test · No shelf above, retest after a 4+ session hub: {lbl.split(' · ')[0].replace('Target: ', 'target ')}",
       lambda r: r["grp"] == "hub" and r.get("entry") == "retest" and r["d"] == 1 and r.get("no_shelf")
       and (r.get("sessions") or 0) >= 4, k) for k, lbl in NOSHELF_EXITS.items()],
    *[(f"noshelf_{k[3:]}_a", f"Test · No shelf above, A retest (room 1.5+ to this target): {lbl.split(' · ')[0].replace('Target: ', 'target ')}",
       (lambda kk: lambda r: r["grp"] == "hub" and r.get("entry") == "retest" and r["d"] == 1 and r.get("no_shelf")
        and (r.get("sessions") or 0) >= 4 and ((r.get("rr_nt") or {}).get(kk) or 0) >= 1.5)(k), k) for k, lbl in NOSHELF_EXITS.items()],
]


PUBLIC_RULES = [("hub_long", "Hub A retests"), ("double_long", "Double bottom A retests"), ("gapfade", "Gap fade & rip")]


def tested_public() -> dict | None:
    """The two-year test of the rules NextShelf alerts on today, for the public record pages. Straight from the last
    finished Lab run, shown whatever the numbers are (a rule that tests negative shows negative). None before a run."""
    try:
        st = json.loads(db.get_setting("lab_exits") or "{}")
        grad = {g["key"]: g for g in (st.get("result") or {}).get("graduation") or []}
        rows = [dict(key=k, label=label, n=grad[k]["n"], win=grad[k]["win"], avg=grad[k]["avg"], pf=grad[k]["pf"],
                     recent=grad[k].get("recent"), recent_n=grad[k].get("recent_n"))
                for k, label in PUBLIC_RULES if k in grad and grad[k].get("n")]
        if not rows:
            return None
        days = int(st.get("days") or 720)
        try:      # Patch 185: name the period from the run's own first–last dates, so the word and the dates always agree
            span = (pd.Timestamp(st["last"]) - pd.Timestamp(st["first"])).days
            if span > 0:
                days = span
        except Exception:
            pass
        yrs = max(1, 2 if days <= 760 else round(days / 365))
        return dict(rows=rows, symbols=st.get("symbols"), first=st.get("first"), last=st.get("last"), finished=st.get("finished"),
                    days=days, period=f"{['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten'][yrs] if yrs <= 10 else yrs}-year")
    except Exception:
        return None


def graduation(rows: list[dict]) -> list[dict]:
    """Each live rule, scored exactly as the alert trades it, against fixed bars: enough trades, positive overall,
    in both halves, in the last 6 months, without its 5 best stocks, and a profit factor of 1.2+."""
    out = []
    for key, label, test, ek in LIVE_RULES:
        got = sorted([r for r in rows if test(r) and ek in r["ex"]], key=lambda r: r["t"])
        if not got:
            out.append(dict(key=key, label=label, exit=ek, n=0, checks=[], passed=False))
            continue
        x = np.array([r["ex"][ek][0] for r in got])
        syms = np.array([r["sym"] for r in got])
        n = len(x)
        wins, losses = x[x > 0], x[x <= 0]
        pf = float(wins.sum() / -losses.sum()) if losses.sum() < 0 else None
        early, late = float(x[:n // 2].mean()) if n >= 2 else None, float(x[n // 2:].mean()) if n >= 2 else None
        cut = (pd.Timestamp(got[-1]["t"]) - pd.Timedelta(days=182)).isoformat()
        recent = np.array([r["ex"][ek][0] for r in got if r["t"] >= cut])
        sp = _spread(x, syms)
        res = dict(key=key, label=label, exit=ek, exit_label=dict(EXITS).get(ek) or FLOOR_EXITS.get(ek) or EDGE_EXITS.get(ek) or GAPGO_EXITS.get(ek) or STACKED_EXITS.get(ek) or GAPSTK_EXITS.get(ek) or DBSTK_EXITS.get(ek) or NOSHELF_EXITS.get(ek, ek), n=n,
                   win=round(100 * len(wins) / n, 1), avg=round(float(x.mean()), 2),
                   pf=round(pf, 2) if pf is not None else None,
                   early=round(early, 2) if early is not None else None, late=round(late, 2) if late is not None else None,
                   recent_n=len(recent), recent=round(float(recent.mean()), 2) if len(recent) else None, **sp)
        checks = [
            ("trades", f"{GRAD_MIN_N}+ trades", n >= GRAD_MIN_N),
            ("avg", "Positive average per trade", res["avg"] > 0),
            ("pf", f"Profit factor {GRAD_MIN_PF}+", (pf or 0) >= GRAD_MIN_PF),
            ("halves", "Positive in both halves", (early or 0) > 0 and (late or 0) > 0),
            ("recent", "Positive in the last 6 months (20+ trades)", (res["recent"] or 0) > 0 and len(recent) >= 20),
            ("broad", "Positive without its 5 best stocks", (sp["avg_ex_top"] or 0) > 0 and sp["stocks"] >= 15),
        ]
        res["checks"] = [dict(key=k, label=l, ok=bool(ok)) for k, l, ok in checks]
        res["passed"] = all(c["ok"] for c in res["checks"])
        out.append(res)
    return out


def _set(**kw):
    cur = json.loads(db.get_setting("lab_exits") or "{}")
    cur.update(kw)
    db.set_setting("lab_exits", json.dumps(cur))


CKPT = db.DATA_DIR / "lab_exits_ckpt.json"   # progress saved after every chunk, so a restart picks up where it left off


def _save_ckpt(syms, done, rows, failed, failed_msg, started):
    tmp = CKPT.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(syms=syms, done=done, rows=rows, failed=failed, failed_msg=failed_msg, started=started,
                                   exits=[k for k, _ in EXITS], entries=["breakout", "retest"], days=DAYS),
                              default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    tmp.replace(CKPT)


def _load_ckpt():
    try:
        ck = json.loads(CKPT.read_text())
    except Exception:
        return None
    return ck if ck.get("exits") == [k for k, _ in EXITS] and ck.get("entries") == ["breakout", "retest"] else None   # saved by an older version: start over


# ---------------------------------------------------------------- compact rows (memory)
# A full run keeps ~45-60k rows. As plain dicts with an exits dict of (ret, how) tuples that was ~2.3 KB a row,
# enough to push the 512 MB server over. These slot-based rows act like the dicts every function here reads
# (r["k"], r.get, "k" in r, r["ex"][k], k in r["ex"], .items()) at roughly a third of the memory.
_HOW_CODE, _HOW_NAME, _EXKEYS = {}, [], {}


class _Ex:
    __slots__ = ("_k", "_r", "_h")

    def __init__(self, d: dict):
        ks = tuple(sorted(d))
        self._k = _EXKEYS.setdefault(ks, ks)                  # identical key sets share one tuple
        self._r = tuple(float(d[k][0]) for k in self._k)
        codes = []
        for k in self._k:
            h = str(d[k][1])
            if h not in _HOW_CODE:
                _HOW_CODE[h] = chr(65 + len(_HOW_NAME)); _HOW_NAME.append(h)
            codes.append(_HOW_CODE[h])
        self._h = sys.intern("".join(codes))

    def __contains__(self, k):
        return k in self._k

    def __getitem__(self, k):
        try:
            i = self._k.index(k)
        except ValueError:
            raise KeyError(k)
        return (self._r[i], _HOW_NAME[ord(self._h[i]) - 65])

    def get(self, k, default=None):
        return self[k] if k in self._k else default

    def keys(self):
        return self._k

    def items(self):
        return [(k, self[k]) for k in self._k]

    def __len__(self):
        return len(self._k)


_ROW_KEYS = ("sym", "grp", "entry", "t", "d", "dv", "board_ok", "rvol", "with_mkt", "with_trend", "chase", "margin", "rr",
             "width", "sessions", "pm", "gap_open", "ex", "no_shelf", "rr_nt", "first_any", "first_4", "far", "tight", "first_6", "support", "held", "st_trig", "st_tgt", "st_path", "st_has", "st_all", "grade",
             "mkt200", "mom20", "calm")


class _Row:
    __slots__ = _ROW_KEYS + ("_x",)

    def __init__(self, d: dict):
        x = None
        for k, v in d.items():
            if k == "ex":
                v = _Ex(v)
            elif isinstance(v, str):
                v = sys.intern(v)
            elif isinstance(v, np.generic):
                v = v.item()
            if k in _ROW_KEYS:
                object.__setattr__(self, k, v)
            else:
                x = x or {}
                x[k] = v
        self._x = x

    def __getitem__(self, k):
        try:
            return object.__getattribute__(self, k) if k in _ROW_KEYS else self._x[k]
        except (AttributeError, TypeError):
            raise KeyError(k)

    def get(self, k, default=None):
        try:
            return self[k]
        except KeyError:
            return default

    def __contains__(self, k):
        try:
            self[k]
            return True
        except KeyError:
            return False

    def __setitem__(self, k, v):
        if k in _ROW_KEYS:
            object.__setattr__(self, k, v)
        else:
            self._x = self._x or {}
            self._x[k] = v


def _compact(rows: list[dict]) -> list:
    return [_Row(r) for r in rows]


def _rss_mb() -> float:
    """This process's CURRENT resident memory in MB (/proc VmRSS; falls back to the peak from getrusage,
    which on Linux is in kilobytes). 0 when it can't be read."""
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except Exception:
        pass
    try:
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except Exception:
        return 0.0


TEST_KEYS = ("floor_", "edge_", "gapgo_", "stk_", "gs_", "db_", "rg_", "pf_", "flg_")      # graduation rows a tests-only run measures


# ---------------------------------------------------------------- named tests: progress that survives a stop (Nov 19)
# The Stacked / gap-open tests keep only a few thousand small rows, so their progress is saved after every chunk.
# If one stops (memory guard, a deploy, a restart), tapping the same test again carries on from that stock.
NAMED_TESTS = ("gapgo", "stacked", "gapstk", "dbstk", "regime", "pfprint", "flags")
TPROG = db.DATA_DIR / "lab_test_progress.json"


def _row_plain(r) -> dict:
    d = {}
    for k in _ROW_KEYS:
        try:
            v = object.__getattribute__(r, k)
        except AttributeError:
            continue
        d[k] = {kk: list(vv) for kk, vv in v.items()} if k == "ex" else v
    if r._x:
        d.update(r._x)
    return d


def _save_tprog(test, syms, done, rows, failed, failed_msg, started, failed_syms=None):
    try:
        tmp = TPROG.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(test=test, days=DAYS, syms=syms, done=done, rows=[_row_plain(r) for r in rows],
                                       failed=failed, failed_msg=failed_msg, started=started, saved=time.time(),
                                       failed_syms=list(failed_syms or [])),
                                  default=lambda o: o.item() if hasattr(o, "item") else str(o)))
        tmp.replace(TPROG)
    except Exception:
        traceback.print_exc()


def _load_tprog(test, days):
    try:
        p = json.loads(TPROG.read_text())
    except Exception:
        return None
    if p.get("test") != test or p.get("days") != days or time.time() - (p.get("saved") or 0) > 2 * 86400:
        return None
    for r in p["rows"]:
        r["ex"] = {k: tuple(v) for k, v in r["ex"].items()}
    return p


def _download(part):
    """Bars for one chunk, patient with Alpaca's rate limit (429): up to 6 tries, waiting 15 s, 30 s, 60 s, 90 s…
    Returns (bars, error message); bars is None when every try failed."""
    msg = None
    for attempt in range(6):
        if _CANCEL.is_set():
            raise _Stopped()
        try:
            return data.get_bars_many(part, DAYS), msg
        except Exception as e:
            msg = str(e)[:200]
            log.warning("lab test: %s..%s failed (try %d): %s", part[0], part[-1], attempt + 1, e)
            time.sleep(min(90, 15 * 2 ** attempt))
    return None, msg


def _free_mem():
    """Drop the site's own caches (4H bars, levels) and hand freed memory back before the memory guard decides."""
    try:
        import scanner
        scanner._BARS.clear()
    except Exception:
        pass
    try:
        import levels
        levels._CACHE.clear()
        levels._SL_CACHE.clear()
    except Exception:
        pass
    gc.collect()
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


def run(symbols: list[str] | None = None, resume: bool = False, years: int | None = None, only_tests: bool | str = False):
    global DAYS
    if not _LOCK.acquire(blocking=False):
        return
    _CANCEL.clear()
    try:
        import scanner
        s = scanner.SETTINGS
        ck = _load_ckpt() if resume else None
        if ck:
            DAYS = int(ck.get("days") or DAYS)          # a resumed run keeps the period it started with
            syms, start_i, rows = ck["syms"], ck["done"], ck["rows"]
            for r in rows:                    # JSON turned the (return, how) pairs into lists
                r["ex"] = {k: tuple(v) for k, v in r["ex"].items()}
            failed, failed_msg, started = ck.get("failed", 0), ck.get("failed_msg"), ck.get("started") or time.time()
            _set(status="running", resumed=True, error=None, heartbeat=pd.Timestamp.now(tz="UTC").isoformat())
            log.info("lab test: resuming at %d/%d stocks", start_i, len(syms))
        else:
            if years in YEARS_OK:
                DAYS = 360 * years if years == 2 else 365 * years   # 2 years stays 720 days, like every earlier run
            syms = symbols or universe.board_universe()
            start_i, rows, failed, failed_msg, started = 0, [], 0, None, time.time()
            tp = _load_tprog(only_tests, DAYS) if only_tests in NAMED_TESTS and not symbols else None
            if tp:            # the same test stopped part-way: carry on from where it got to
                syms, start_i, rows = tp["syms"], tp["done"], _compact(tp["rows"])
                failed, failed_msg, started = tp.get("failed", 0), tp.get("failed_msg"), tp.get("started") or time.time()
                failed_syms = tp.get("failed_syms")
                if failed_syms is None and failed:      # saved before the list existed: retry every stock with no rows
                    have = {r["sym"] for r in rows}
                    failed_syms = [x for x in syms[:start_i] if x not in have]
                log.info("lab test: %s continuing at %d/%d stocks", only_tests, start_i, len(syms))
            _set(status="running", done=start_i, total=len(syms), started=pd.Timestamp.now(tz="UTC").isoformat(), error=None, days=DAYS,
                 run_test=only_tests if isinstance(only_tests, str) else bool(only_tests), run_years=years,
                 failed=failed, failed_msg=failed_msg, trades_so_far=len(rows), resumed=bool(start_i), resumes=0)
            log.info("lab test: started, %d stocks", len(syms))
        try:
            failed_syms
        except NameError:
            failed_syms = None
        failed_syms = list(failed_syms or [])
        regime = _spy_regime()
        global _REGX
        _REGX = _spy_regime_ext()
        for i in range(start_i, len(syms), CHUNK):
            if _CANCEL.is_set():
                raise _Stopped()
            part = syms[i:i + CHUNK]
            bars, m = _download(part)
            if m:
                failed_msg = m
            if bars is None:
                bars = {}
                failed += len(part)
                failed_syms += part
            for sym in part:
                if _CANCEL.is_set():
                    raise _Stopped()
                try:
                    b = bars.get(sym)
                    if b is not None and not b.empty:
                        rows.extend(_compact(trades_for(sym, engine.to_4h(b), s, regime, only_tests)))
                except Exception:
                    traceback.print_exc()
            bars = None
            gc.collect()
            # no checkpoint any more: runs don't resume (Patch 94), and rewriting every row to JSON each chunk was the biggest memory spike
            _set(done=min(i + CHUNK, len(syms)), failed=failed, failed_msg=failed_msg, trades_so_far=len(rows) if only_tests else len([r for r in rows if r.get("entry", "breakout") == "breakout"]),
                 heartbeat=pd.Timestamp.now(tz="UTC").isoformat())
            log.info("lab test: %d/%d stocks, %d breakouts", min(i + CHUNK, len(syms)), len(syms), len(rows))
            if only_tests in NAMED_TESTS:
                _save_tprog(only_tests, syms, min(i + CHUNK, len(syms)), rows, failed, failed_msg, started, failed_syms)
            rss = _rss_mb()
            if rss > LAB_RSS_STOP_MB:
                _free_mem()                   # the site's caches first: often enough to carry on
                rss = _rss_mb()
            if rss > LAB_RSS_STOP_MB:
                CKPT.unlink(missing_ok=True)
                more = " Tap the same test again: it carries on from here." if only_tests in NAMED_TESTS else " Tap Run the test again."
                _set(status="stopped", error=f"Stopped at {min(i + CHUNK, len(syms))}/{len(syms)} stocks ({int(rss)} MB) to stay under the server's {MEM_LIMIT_MB} MB limit.{more}")
                log.info("lab test: stopped at %d MB to stay under the memory limit", int(rss))
                return
            time.sleep(1.0)                   # leave room for the site, the live scan and Alpaca's rate limit
        if failed_syms:                       # one more pass over the stocks that couldn't download, slower
            _set(failed_msg=f"Retrying the {len(failed_syms)} stocks that couldn't download…", heartbeat=pd.Timestamp.now(tz="UTC").isoformat())
            log.info("lab test: retrying %d stocks that failed to download", len(failed_syms))
            time.sleep(30)
            still = []
            for k in range(0, len(failed_syms), 10):
                part = failed_syms[k:k + 10]
                bars, m = _download(part)
                if bars is None:
                    still += part
                    continue
                failed = max(0, failed - len(part))   # the retry list can hold stocks that never failed (old progress files)
                for sym in part:
                    try:
                        b = bars.get(sym)
                        if b is not None and not b.empty:
                            rows.extend(_compact(trades_for(sym, engine.to_4h(b), s, regime, only_tests)))
                    except Exception:
                        traceback.print_exc()
                bars = None
                gc.collect()
                _set(failed=failed, trades_so_far=len(rows), heartbeat=pd.Timestamp.now(tz="UTC").isoformat(),
                     failed_msg=f"Retrying the stocks that couldn't download: {min(k + 10, len(failed_syms))}/{len(failed_syms)}…")
                time.sleep(3)
            failed_syms = still
            if not still:
                failed_msg = None
            _set(failed=failed, failed_msg=failed_msg)
            if only_tests in NAMED_TESTS:
                _save_tprog(only_tests, syms, len(syms), rows, failed, failed_msg, started, failed_syms)
        if not rows and only_tests == "regime" and failed < len(syms):
            raise RuntimeError("no hub or double retests in this period, so there is nothing to compare")
        if not rows and only_tests == "dbstk" and failed < len(syms):
            raise RuntimeError("no double bottom A retests in this period, so there is nothing to compare")
        if not rows and only_tests == "gapstk" and failed < len(syms):
            raise RuntimeError("no gap fade & rip trades in this period, so there is nothing to compare")
        if not rows and only_tests == "stacked" and failed < len(syms):
            raise RuntimeError("no A or B hub retests in this period, so there is nothing to compare")
        if not rows and only_tests == "gapgo" and failed < len(syms):
            raise RuntimeError("no gap fade opened above its gap-day high in this period, so there is nothing to compare")
        if not rows:
            raise RuntimeError("no breakouts found: the market data download failed" + (f" ({failed_msg})" if failed_msg else ""))
        out = summarize(rows)
        if only_tests:
            # keep the last full run's tables and live-rule checks; replace only the test rows
            prev = (json.loads(db.get_setting("lab_exits") or "{}").get("result") or {})
            tk = ("gapgo_",) if only_tests == "gapgo" else ("stk_",) if only_tests == "stacked" else ("gs_",) if only_tests == "gapstk" else ("db_",) if only_tests == "dbstk" else ("rg_",) if only_tests == "regime" else ("pf_",) if only_tests == "pfprint" else ("flg_",) if only_tests == "flags" else TEST_KEYS
            mine = [g for g in out["graduation"] if g["key"].startswith(tk)]
            keep = [g for g in (prev.get("graduation") or []) if not g["key"].startswith(tk)]
            out = dict(prev or out, graduation=keep + mine, tests_run=pd.Timestamp.now(tz="UTC").isoformat(),
                       tests_symbols=len(syms), tests_days=DAYS)
            _set(status="done", result=out, tests_only=True, finished=pd.Timestamp.now(tz="UTC").isoformat(),
                 seconds=round(time.time() - started))
            CKPT.unlink(missing_ok=True)
            TPROG.unlink(missing_ok=True)
            return
        brows = [r for r in rows if r.get("entry", "breakout") == "breakout"]
        span = sorted(r["t"] for r in brows)
        _set(status="done", result=out, trades=len(brows), days=DAYS, symbols=len(syms),
             first=span[0] if span else None, last=span[-1] if span else None,
             finished=pd.Timestamp.now(tz="UTC").isoformat(), seconds=round(time.time() - started),
             regime_ok=regime is not None)
        CKPT.unlink(missing_ok=True)
    except _Stopped:
        CKPT.unlink(missing_ok=True)           # a stopped run isn't resumed; the last finished results stay on the page
        try:      # you pressed Stop on this named test: its next tap starts fresh. Another test's progress is kept.
            if only_tests in NAMED_TESTS and json.loads(TPROG.read_text()).get("test") == only_tests:
                TPROG.unlink(missing_ok=True)
        except Exception:
            pass
        _set(status="stopped", error=None, stopped=pd.Timestamp.now(tz="UTC").isoformat())
        log.info("lab test: stopped")
    except Exception as e:
        traceback.print_exc()
        _set(status="error", error=str(e)[:300])
    finally:
        _LOCK.release()
        if _RESTART["on"]:
            _RESTART["on"] = False
            last = json.loads(db.get_setting("lab_exits") or "{}")      # Restart = the same test and period again
            start(years=last.get("run_years"), only_tests=last.get("run_test") or False)


def start(resume: bool = False, years: int | None = None, only_tests: bool | str = False) -> bool:
    if _LOCK.locked():
        return False
    threading.Thread(target=run, kwargs=dict(resume=resume, years=years, only_tests=only_tests), daemon=True).start()
    return True


def stop(restart: bool = False) -> dict:
    """Stop the running test (it quits at the next stock). restart=True starts a fresh run right after."""
    if _LOCK.locked():
        _RESTART["on"] = restart
        _CANCEL.set()
        return dict(stopping=True, restart=restart)
    CKPT.unlink(missing_ok=True)               # nothing running here (e.g. a run cut off by a restart): clear it
    st = json.loads(db.get_setting("lab_exits") or "{}")
    if st.get("status") == "running":
        _set(status="stopped", error=None)
    started = start(years=st.get("run_years"), only_tests=st.get("run_test") or False) if restart else False
    return dict(stopping=False, restart=restart, started=started)


def resume_if_interrupted():
    """Called at server start: a test that was running when the server restarted does NOT carry on.

    It used to resume itself here, with no cap and no memory check, so when Render killed the
    instance for running out of memory mid-test, the next start resumed the same test, grew the
    memory again, and got killed again, about every 5 minutes. An interrupted run now stops
    cleanly instead; the admin starts a fresh run from the Lab page when they want one.
    """
    st = json.loads(db.get_setting("lab_exits") or "{}")
    if st.get("status") == "running":
        CKPT.unlink(missing_ok=True)   # same as stop() when nothing is running: a cut-off run is cleared, not resumed
        _set(status="stopped", error="The test was interrupted (the server restarted part-way). Tap Run the test again.")
        log.info("lab test: interrupted run stopped at server start, not resumed")


def status() -> dict:
    st = json.loads(db.get_setting("lab_exits") or "{}")
    if st.get("status") == "running" and not _LOCK.locked():
        # saved as running, but nothing is running in this server: it restarted (a deploy, or it ran out of memory)
        # Do not resume it here: a page view must never silently restart a memory-heavy test.
        CKPT.unlink(missing_ok=True)
        st.update(status="stopped", error="The test was interrupted (the server restarted part-way). Tap Run the test again"
                  + (": the Stacked and gap-open tests carry on from where they got to." if TPROG.exists() else "."))
        db.set_setting("lab_exits", json.dumps(st))
    return st
