"""Lab: which hub breakouts work, and which exit works best.

Replays every historical hub breakout across the board universe (same rules as the live scanner), tags each one
with what was known at the breakout close (volume, market direction, chase, close margin, reward:risk, hub shape,
time of day), then scores it under several exit rules. Results go to the "lab_exits" setting for the admin Lab page.

Nothing here changes live alerts. It only measures.
"""
from __future__ import annotations

import json
import logging
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
DAYS = 720
CHUNK = 20
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


def trades_for(sym: str, c4: pd.DataFrame, s: engine.Settings, regime: pd.Series | None) -> list[dict]:
    if c4 is None or len(c4) < 60:
        return []
    import board
    import patterns
    res = engine.analyze(c4, s)
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
    for t in [dict(x, kind="hub") for x in res["trades"]] + other:
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
        if "edge" in f["ex"]:
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
                          pm=engine.candle_end(idx[j].tz_convert(engine.NY)).hour == 16)
                ex2 = {}
                for key, stop, hz in (("edge", cur, s.horizon), ("mid", mid, s.horizon), ("far", far, s.horizon),
                                      ("edge20", cur, 20),
                                      ("atr05", entry2 - d * 0.5 * A2, s.horizon),
                                      ("atr10", entry2 - d * 1.0 * A2, s.horizon)):
                    ex2[key] = _exit(close, j, d, entry2, target, stop, hz)
                ex2["half"] = _exit_half(close, j, d, entry2, target, edge, risk2 if risk2 > 0 else d * (entry2 - edge), s.horizon)
                f2["ex"] = {k: (round(v[0], 3), v[1]) for k, v in ex2.items() if v is not None}
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
        return dict(rows=rows, symbols=st.get("symbols"), first=st.get("first"), last=st.get("last"), finished=st.get("finished"))
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
        res = dict(key=key, label=label, exit=ek, exit_label=dict(EXITS).get(ek, ek), n=n,
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
                                   exits=[k for k, _ in EXITS], entries=["breakout", "retest"]),
                              default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    tmp.replace(CKPT)


def _load_ckpt():
    try:
        ck = json.loads(CKPT.read_text())
    except Exception:
        return None
    return ck if ck.get("exits") == [k for k, _ in EXITS] and ck.get("entries") == ["breakout", "retest"] else None   # saved by an older version: start over


def run(symbols: list[str] | None = None, resume: bool = False):
    if not _LOCK.acquire(blocking=False):
        return
    _CANCEL.clear()
    try:
        import scanner
        s = scanner.SETTINGS
        ck = _load_ckpt() if resume else None
        if ck:
            syms, start_i, rows = ck["syms"], ck["done"], ck["rows"]
            for r in rows:                    # JSON turned the (return, how) pairs into lists
                r["ex"] = {k: tuple(v) for k, v in r["ex"].items()}
            failed, failed_msg, started = ck.get("failed", 0), ck.get("failed_msg"), ck.get("started") or time.time()
            _set(status="running", resumed=True, error=None, heartbeat=pd.Timestamp.now(tz="UTC").isoformat())
            log.info("lab test: resuming at %d/%d stocks", start_i, len(syms))
        else:
            syms = symbols or universe.board_universe()
            start_i, rows, failed, failed_msg, started = 0, [], 0, None, time.time()
            _set(status="running", done=0, total=len(syms), started=pd.Timestamp.now(tz="UTC").isoformat(), error=None,
                 failed=0, failed_msg=None, trades_so_far=0, resumed=False, resumes=0)
            log.info("lab test: started, %d stocks", len(syms))
        regime = _spy_regime()
        for i in range(start_i, len(syms), CHUNK):
            if _CANCEL.is_set():
                raise _Stopped()
            part = syms[i:i + CHUNK]
            bars = {}
            for attempt in range(3):          # Alpaca rate limits come back as errors: wait and retry
                try:
                    bars = data.get_bars_many(part, DAYS)
                    break
                except Exception as e:
                    failed_msg = str(e)[:200]
                    log.warning("lab test: %s..%s failed (try %d): %s", part[0], part[-1], attempt + 1, e)
                    time.sleep(10 * (attempt + 1))
            else:
                failed += len(part)
            for sym in part:
                if _CANCEL.is_set():
                    raise _Stopped()
                try:
                    b = bars.get(sym)
                    if b is not None and not b.empty:
                        rows.extend(trades_for(sym, engine.to_4h(b), s, regime))
                except Exception:
                    traceback.print_exc()
            bars = None
            _save_ckpt(syms, min(i + CHUNK, len(syms)), rows, failed, failed_msg, started)
            _set(done=min(i + CHUNK, len(syms)), failed=failed, failed_msg=failed_msg, trades_so_far=len([r for r in rows if r.get("entry", "breakout") == "breakout"]),
                 heartbeat=pd.Timestamp.now(tz="UTC").isoformat())
            log.info("lab test: %d/%d stocks, %d breakouts", min(i + CHUNK, len(syms)), len(syms), len(rows))
            time.sleep(0.2)                   # leave room for the site and the live scan
        if not rows:
            raise RuntimeError("no breakouts found: the market data download failed" + (f" ({failed_msg})" if failed_msg else ""))
        out = summarize(rows)
        brows = [r for r in rows if r.get("entry", "breakout") == "breakout"]
        span = sorted(r["t"] for r in brows)
        _set(status="done", result=out, trades=len(brows), days=DAYS, symbols=len(syms),
             first=span[0] if span else None, last=span[-1] if span else None,
             finished=pd.Timestamp.now(tz="UTC").isoformat(), seconds=round(time.time() - started),
             regime_ok=regime is not None)
        CKPT.unlink(missing_ok=True)
    except _Stopped:
        CKPT.unlink(missing_ok=True)           # a stopped run isn't resumed; the last finished results stay on the page
        _set(status="stopped", error=None, stopped=pd.Timestamp.now(tz="UTC").isoformat())
        log.info("lab test: stopped")
    except Exception as e:
        traceback.print_exc()
        _set(status="error", error=str(e)[:300])
    finally:
        _LOCK.release()
        if _RESTART["on"]:
            _RESTART["on"] = False
            start()


def start(resume: bool = False) -> bool:
    if _LOCK.locked():
        return False
    threading.Thread(target=run, kwargs=dict(resume=resume), daemon=True).start()
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
    started = start() if restart else False
    return dict(stopping=False, restart=restart, started=started)


def resume_if_interrupted():
    """Called at server start: a test that was running when the server restarted (a deploy) carries on by itself."""
    st = json.loads(db.get_setting("lab_exits") or "{}")
    if st.get("status") == "running" and _load_ckpt() is not None:
        start(resume=True)


def status() -> dict:
    st = json.loads(db.get_setting("lab_exits") or "{}")
    if st.get("status") == "running" and not _LOCK.locked():
        # saved as running, but nothing is running in this server: it restarted (a deploy, or it ran out of memory)
        if _load_ckpt() is not None and st.get("resumes", 0) < 3:
            _set(resumes=st.get("resumes", 0) + 1)
            start(resume=True)
            return json.loads(db.get_setting("lab_exits") or "{}")
        st.update(status="error", error="The test was interrupted (the server restarted part-way). Tap Run the test again.")
        db.set_setting("lab_exits", json.dumps(st))
    return st
