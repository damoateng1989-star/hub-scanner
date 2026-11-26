"""Nov 26 (owner): the cross entry, replayed the way it trades live — no hindsight.

The trigger-cross test (lab_trigcross) finds hubs on the FULL chart and counts the first touch of a hub's top after the
hub ended. Live can't do that: while a candle trades, nobody knows whether it ends the hub or just pokes above it and
closes back inside (the hub then keeps going). Its 'next candle' slice (+0.58%/signal) is built partly on that hindsight.

This test walks every stock candle by candle. Before each 4H candle it measures the hub exactly as the live scan would —
only the bars that had closed, over the same SCAN_DAYS lookback — asks the live rule (cross.plan_from_status) whether a
cross plan is open, and if the candle trades through the trigger it fills a buy-stop on the first 30-minute bar that
reaches it (at the trigger, or the open if it gapped through). Then the same exits as the live plan: target = the plan's
target, out at the stop (trigger − 1 ATR) or on a 4H close back inside the hub, 10-candle horizon. One live episode per
stock at a time, one per hub, as live.

Two groups, scored the same way:
  forming      — the hub was still forming when price crossed (what live alerted until Nov 26)
  just_ended   — the hub had ended at an earlier close (what live alerts now, as a heads-up)
'forming' is the honest number for the trigger-cross test's 'next candle' slice. A group earns 'entry' status only if it
passes the same per-signal gate as every other Lab card (n ≥ 100, both halves, last 6 months, without the top 5 > 0).
"""
from __future__ import annotations

import gc
import json
import threading
import time
import traceback

import numpy as np
import pandas as pd

import db
import engine

_LOCK = threading.Lock()
_CANCEL = threading.Event()
NY = "America/New_York"
KEY = "lab_crossreplay"


def _set(**kw):
    cur = json.loads(db.get_setting(KEY) or "{}")
    cur.update(kw)
    db.set_setting(KEY, json.dumps(cur, default=lambda o: o.item() if hasattr(o, "item") else str(o)))


def status() -> dict:
    st = json.loads(db.get_setting(KEY) or "{}")
    if st.get("status") == "running" and not _LOCK.locked():
        st.update(status="stopped", error="Interrupted (the server restarted part-way). Tap Run again.")
        db.set_setting(KEY, json.dumps(st))
    return st


def signals_for(sym: str, b30: pd.DataFrame, s, scan_days: int, start_t: pd.Timestamp) -> list[dict]:
    import cross
    import lab_trigcross as LT
    c4 = engine.to_4h(b30)
    if c4 is None or len(c4) < 80:
        return []
    close4 = c4["Close"].to_numpy(float)
    high4 = c4["High"].to_numpy(float)
    idx4 = c4.index
    ends4 = np.array([engine.candle_end(t.tz_convert(NY)).value for t in idx4])
    b = b30.tz_convert(NY)
    bm = b.index.hour * 60 + b.index.minute
    b = b[(bm >= 570) & (bm < 960)]
    look = pd.Timedelta(days=scan_days)
    out, seen, busy_until = [], set(), -1
    for j in range(60, len(c4)):
        if idx4[j] < start_t:
            continue                                      # warm-up: the first SCAN_DAYS only feed the lookback
        if j <= busy_until:
            continue                                      # one live episode per stock at a time
        prev = c4[(idx4 >= idx4[j] - look) & (idx4 < idx4[j])]
        if len(prev) < 60:
            continue
        try:
            res = engine.analyze(prev, s)
            st = engine.current_status(prev, res, s)
        except Exception:
            continue
        p = cross.plan_from_status(sym, st, allow_forming=True)
        if not p or high4[j] < p["trigger"]:
            continue
        if p["ep_key"] in seen:                           # one episode per hub, as live (same key)
            continue
        seen.add(p["ep_key"])
        trig = float(p["trigger"])
        c_end = pd.Timestamp(ends4[j], tz="UTC").tz_convert(NY)
        seg = b[(b.index >= idx4[j].tz_convert(NY)) & (b.index < c_end)]
        hit = seg[seg["High"] >= trig]
        if hit.empty:
            continue
        t_cross = hit.index[0]
        fill = max(trig, float(hit["Open"].iloc[0]))
        r = LT._walk4(close4, ends4, j, t_cross.value, fill, float(p["hub_high"]), float(p["target"]), float(p["stop"]),
                      s.horizon)
        if not r:
            continue
        ret, why, k_exit = r
        busy_until = k_exit
        out.append(dict(sym=sym, t=str(t_cross), kind=st.get("state"), ret=round(float(ret), 3), exit=why,
                        back_inside=bool(close4[j] <= float(p["hub_high"])), gap=bool(fill > trig * 1.002)))
    return out


def summarize(sigs: list[dict]) -> dict:
    import lab_trigcross as LT
    groups = {}
    for name, keep in (("all", None), ("forming", "forming"), ("just_ended", "watching")):
        g = [x for x in sigs if keep is None or x["kind"] == keep]
        if not g:
            groups[name] = dict(n=0)
            continue
        card = LT._card(np.array([x["ret"] for x in g]), np.ones(len(g), bool), [x["t"] for x in g], [x["sym"] for x in g])
        card["n"] = len(g)
        card["back_inside"] = round(100 * np.mean([x["back_inside"] for x in g]), 1)
        card["exits"] = {k: sum(1 for x in g if x["exit"] == k) for k in ("target", "stop", "time")}
        groups[name] = card
    f = groups.get("forming") or {}
    verdict = ("the forming cross holds up live: it can come back as an entry" if f.get("passed") else
               "no live edge on the forming cross: crosses stay a heads-up")
    return dict(groups=groups, verdict=verdict)


def run(years: int = 2):
    if not _LOCK.acquire(blocking=False):
        return
    _CANCEL.clear()
    try:
        import lab_exits
        import scanner
        import universe
        s = scanner.SETTINGS
        days = 720 if years == 2 else 365 * years
        scan_days = scanner.SCAN_DAYS
        syms = universe.board_universe()
        started = time.time()
        start_t = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
        _set(status="running", done=0, total=len(syms), started=pd.Timestamp.now(tz="UTC").isoformat(), error=None,
             days=days, signals=0, result=None)
        sigs, failed = [], 0
        lab_exits.DAYS = days + scan_days                  # the lookback needs its own warm-up history
        for i in range(0, len(syms), lab_exits.CHUNK):
            if _CANCEL.is_set():
                _set(status="stopped")
                return
            part = syms[i:i + lab_exits.CHUNK]
            bars, _ = lab_exits._download(part)
            if bars is None:
                failed += len(part)
                bars = {}
            for sym in part:
                if _CANCEL.is_set():
                    break
                try:
                    bb = bars.get(sym)
                    if bb is None or bb.empty:
                        continue
                    sigs += signals_for(sym, bb, s, scan_days, start_t)
                except Exception:
                    traceback.print_exc()
                time.sleep(0.05)                           # leave the CPU to the live app between stocks
            bars = None
            gc.collect()
            _set(done=min(i + lab_exits.CHUNK, len(syms)), signals=len(sigs), failed=failed,
                 heartbeat=pd.Timestamp.now(tz="UTC").isoformat())
        out = summarize(sigs)
        span = sorted(g["t"] for g in sigs)
        _set(status="done", result=out, first=span[0] if span else None, last=span[-1] if span else None,
             symbols=len(syms), failed=failed, seconds=round(time.time() - started),
             finished=pd.Timestamp.now(tz="UTC").isoformat())
    except Exception as e:
        traceback.print_exc()
        _set(status="error", error=str(e)[:300])
    finally:
        _LOCK.release()


def start(years: int = 2) -> bool:
    import lab_exits
    import lab_trigcross
    if _LOCK.locked() or lab_exits._LOCK.locked() or lab_trigcross._LOCK.locked():
        return False
    threading.Thread(target=run, kwargs=dict(years=years), daemon=True).start()
    return True


def stop():
    _CANCEL.set()
