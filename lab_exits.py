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

EXITS = [   # key, label
    ("edge", "Current: out on a close back inside the pattern"),
    ("mid", "Out on a close past the middle of the pattern"),
    ("far", "Out on a close past the far side of the pattern"),
    ("half", "Half off at 1R, rest stopped at entry"),
    ("edge20", "Current, but 20 candles before giving up"),
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
    ("margin", "Close clears the edge by 0.2 ATR+", lambda f: f["margin"] >= 0.2),
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


def trades_for(sym: str, c4: pd.DataFrame, s: engine.Settings, regime: pd.Series | None) -> list[dict]:
    if c4 is None or len(c4) < 60:
        return []
    import board
    import patterns
    res = engine.analyze(c4, s)
    try:
        other = patterns.analyze_all(c4, res, s)["trades"]
    except Exception:
        traceback.print_exc()
        other = []
    close = res["close"].to_numpy(float)
    atr = res["atr"].to_numpy(float)
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

        def _as_of(series):
            if series is None:
                return None
            past = series[series.index < today].dropna()
            return bool(past.iloc[-1]) if len(past) else None
        mkt_up, own_up = _as_of(regime), _as_of(own)
        dv = None
        if dvol is not None:
            prior = dvol[dvol.index < today]
            prior = prior[prior > 0].tail(20)
            dv = float(prior.mean()) if len(prior) >= 5 else None
        f = dict(sym=sym, grp=grp, t=str(idx[i0]), d=d, dv=dv,
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
        if grp == "flag":
            f.update(_flag_feats(c4, t["p"], d))
        if grp == "gapfade":                                  # daily clock with its own stop and 2R target: scored as recorded
            if t.get("ret") is None:
                continue
            f["ex"] = {"edge": (round(float(t["ret"]), 3), "target" if t["outcome"] == "follow-through" else "stop")}
            out.append(f)
            continue
        ex = {}
        cur = float(t["stop"]) if grp == "fakeout" else edge   # fakeouts: the live rule's stop is the fake-out extreme
        for key, stop, hz in (("edge", cur, s.horizon), ("mid", mid, s.horizon), ("far", far, s.horizon),
                              ("edge20", cur, 20)):
            ex[key] = _exit(close, i0, d, entry, target, stop, hz)
        ex["half"] = _exit_half(close, i0, d, entry, target, edge, risk_mid if risk_mid > 0 else d * (entry - edge), s.horizon)
        f["ex"] = {k: (round(v[0], 3), v[1]) for k, v in ex.items() if v is not None}
        if "edge" in f["ex"]:
            out.append(f)
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


def best_filters(mine: list[dict], filters: list) -> list[dict]:
    """Search every combination of up to 3 simple filters, under every exit rule, for ones that made money in BOTH
    halves of the test window with enough trades. Ranked by the weaker half, so one lucky stretch can't win."""
    from itertools import combinations
    if len(mine) < MIN_BEST:
        return []
    mine = sorted(mine, key=lambda r: r["t"])
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
                found.append(dict(filters=[c[1] for c in combo], keys=sorted(keys), exit=ek, exit_label=elabel, n=n,
                                  win=round(100 * len(wins) / n, 1), avg=round(float(x.mean()), 2),
                                  pf=round(float(wins.sum() / -losses.sum()), 2) if losses.sum() < 0 else None,
                                  early=round(early, 2), late=round(late, 2), size=size))
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
    return out


def summarize(rows: list[dict]) -> dict:
    groups = []
    for gk, glabel, _ in GROUPS:
        mine = [r for r in rows if r["grp"] == gk]
        filters = FILTERS + EXTRA.get(gk, [])
        table = []
        for fk, flabel, test in filters:
            sub = [r for r in mine if test(r)]
            table.append(dict(key=fk, label=flabel, exits={ek: _score(sub, ek) for ek, _ in EXITS}))
        groups.append(dict(key=gk, label=glabel, trades=len(mine), filters=table, best=best_filters(mine, filters)))
    return dict(groups=groups, exits=[dict(key=k, label=l) for k, l in EXITS])


def _set(**kw):
    cur = json.loads(db.get_setting("lab_exits") or "{}")
    cur.update(kw)
    db.set_setting("lab_exits", json.dumps(cur))


def run(symbols: list[str] | None = None):
    if not _LOCK.acquire(blocking=False):
        return
    try:
        import scanner
        s = scanner.SETTINGS
        syms = symbols or universe.board_universe()
        started = time.time()
        _set(status="running", done=0, total=len(syms), started=pd.Timestamp.now(tz="UTC").isoformat(), error=None,
             failed=0, failed_msg=None, trades_so_far=0)
        log.info("lab test: started, %d stocks", len(syms))
        regime = _spy_regime()
        rows: list[dict] = []
        failed, failed_msg = 0, None
        for i in range(0, len(syms), CHUNK):
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
                try:
                    b = bars.get(sym)
                    if b is not None and not b.empty:
                        rows.extend(trades_for(sym, engine.to_4h(b), s, regime))
                except Exception:
                    traceback.print_exc()
            bars = None
            _set(done=min(i + CHUNK, len(syms)), failed=failed, failed_msg=failed_msg, trades_so_far=len(rows),
                 heartbeat=pd.Timestamp.now(tz="UTC").isoformat())
            log.info("lab test: %d/%d stocks, %d breakouts", min(i + CHUNK, len(syms)), len(syms), len(rows))
            time.sleep(0.2)                   # leave room for the site and the live scan
        if not rows:
            raise RuntimeError("no breakouts found: the market data download failed" + (f" ({failed_msg})" if failed_msg else ""))
        out = summarize(rows)
        span = sorted(r["t"] for r in rows)
        _set(status="done", result=out, trades=len(rows), days=DAYS, symbols=len(syms),
             first=span[0] if span else None, last=span[-1] if span else None,
             finished=pd.Timestamp.now(tz="UTC").isoformat(), seconds=round(time.time() - started),
             regime_ok=regime is not None)
    except Exception as e:
        traceback.print_exc()
        _set(status="error", error=str(e)[:300])
    finally:
        _LOCK.release()


def start() -> bool:
    if _LOCK.locked():
        return False
    threading.Thread(target=run, daemon=True).start()
    return True


def status() -> dict:
    st = json.loads(db.get_setting("lab_exits") or "{}")
    if st.get("status") == "running" and not _LOCK.locked():
        # saved as running, but nothing is running in this server: it restarted (a deploy, or it ran out of memory)
        st.update(status="error", error="The test was interrupted (the server restarted part-way). Tap Run the test again.")
        db.set_setting("lab_exits", json.dumps(st))
    return st
