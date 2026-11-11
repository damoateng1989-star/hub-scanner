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
    ("edge", "Current: out on a close back inside the hub"),
    ("mid", "Out on a close past the hub middle"),
    ("far", "Out on a close past the far side of the hub"),
    ("half", "Half off at 1R, rest stopped at entry"),
    ("edge20", "Current, but 20 candles before giving up"),
]

FILTERS = [   # key, label, test(features) -> bool
    ("all", "Every breakout", lambda f: True),
    ("vol", "Volume 1.2× normal or more", lambda f: f["rvol"] is not None and f["rvol"] >= 1.2),
    ("mkt", "With the market (SPY vs 50-day)", lambda f: f["with_mkt"] is True),
    ("against", "Against the market", lambda f: f["with_mkt"] is False),
    ("nochase", "Not chasing (entry in the first half)", lambda f: not f["chase"]),
    ("margin", "Close clears the edge by 0.2 ATR+", lambda f: f["margin"] >= 0.2),
    ("rr", "Room to the shelf: reward:risk 1.5+", lambda f: f["rr"] is not None and f["rr"] >= 1.5),
    ("tight", "Tight hub (1 ATR wide or less)", lambda f: f["width"] <= 1.0),
    ("long", "Long hub (4+ sessions)", lambda f: f["sessions"] >= 4),
    ("pm", "4:00 PM closes only", lambda f: f["pm"]),
    ("board", "Passes today's board filters ($10M/day, $25B cap)", lambda f: f["board_ok"]),
    ("dv100", "Trades $100M+ a day", lambda f: f["dv"] is not None and f["dv"] >= 100e6),
    ("dv250", "Trades $250M+ a day", lambda f: f["dv"] is not None and f["dv"] >= 250e6),
    ("dv500", "Trades $500M+ a day", lambda f: f["dv"] is not None and f["dv"] >= 500e6),
    ("dvlow", "Thinner names: under $100M a day", lambda f: f["dv"] is not None and f["dv"] < 100e6),
    ("up", "Longs only", lambda f: f["d"] == 1),
    ("down", "Shorts only", lambda f: f["d"] == -1),
    ("combo", "Volume + with the market + not chasing",
     lambda f: f["rvol"] is not None and f["rvol"] >= 1.2 and f["with_mkt"] is True and not f["chase"]),
    ("combo2", "With the market + not chasing + room to the shelf",
     lambda f: f["with_mkt"] is True and not f["chase"] and f["rr"] is not None and f["rr"] >= 1.5),
]


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


def trades_for(sym: str, c4: pd.DataFrame, s: engine.Settings, regime: pd.Series | None) -> list[dict]:
    if c4 is None or len(c4) < 60:
        return []
    res = engine.analyze(c4, s)
    close = res["close"].to_numpy(float)
    atr = res["atr"].to_numpy(float)
    idx = c4.index
    dvol = None
    if "Volume" in c4.columns:      # daily dollar volume, to judge liquidity as it was before each breakout
        day = idx.tz_convert(engine.NY).normalize()
        dvol = (c4["Volume"] * c4["Close"]).groupby(day).sum()
    mc = universe.market_cap(sym)
    out = []
    for t in res["trades"]:
        if t["outcome"] == "pending" or t["chase"]:      # chase here = target already hit on the entry close: no trade
            continue
        h, i0, d = t["hub"], t["i0"], t["direction"]
        entry, edge, target = t["entry"], t["edge"], t["target"]
        A = atr[i0]
        if not A or A != A:
            continue
        mid = (h["low"] + h["high"]) / 2
        far = h["low"] if d == 1 else h["high"]
        risk_mid = d * (entry - mid)
        with_mkt = None
        if regime is not None:
            day = idx[i0].tz_convert(engine.NY).normalize()
            past = regime[regime.index <= day].dropna()
            if len(past):
                up = bool(past.iloc[-1])
                with_mkt = up if d == 1 else not up
        dv = None
        if dvol is not None:
            prior = dvol[dvol.index < idx[i0].tz_convert(engine.NY).normalize()]
            prior = prior[prior > 0].tail(20)
            dv = float(prior.mean()) if len(prior) >= 5 else None
        import board
        f = dict(sym=sym, t=str(idx[i0]), d=d, dv=dv,
                 board_ok=board.quality_skip(sym, dv, mc) is None,
                 rvol=engine.rvol(c4, i0),
                 with_mkt=with_mkt,
                 chase=d * (entry - edge) > 0.5 * d * (target - edge),
                 margin=d * (entry - edge) / A,
                 rr=(d * (target - entry) / risk_mid) if risk_mid > 0 else None,
                 width=(h["high"] - h["low"]) / A,
                 sessions=h["sessions"],
                 pm=engine.candle_end(idx[i0].tz_convert(engine.NY)).hour == 16)
        ex = {}
        for key, stop, hz in (("edge", edge, s.horizon), ("mid", mid, s.horizon), ("far", far, s.horizon),
                              ("edge20", edge, 20)):
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


def summarize(rows: list[dict]) -> dict:
    table = []
    for fk, flabel, test in FILTERS:
        sub = [r for r in rows if test(r)]
        table.append(dict(key=fk, label=flabel, exits={ek: _score(sub, ek) for ek, _ in EXITS}))
    return dict(filters=table, exits=[dict(key=k, label=l) for k, l in EXITS])


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
