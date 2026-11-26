"""Lab test (Nov 24): trigger-cross entry vs the standing retest rule, plus the owner's 1H break-and-retest.

Spec: "Lab test spec — Trigger-cross entry vs the standing retest rule" (Arms A, B, C). Hub breakouts (up) only.

Counting is PER SIGNAL. A signal is one qualified hub episode's first trigger cross: a finished hub of 4+ sessions
whose room to its target from the trigger is 1.5+ (the hub A room bar), and whose first touch of the trigger comes
before any 4H close below the hub. Every arm is scored on every signal; an arm that doesn't enter scores 0.

  A  cross entry: a buy-stop at the trigger, filled on the first 30-minute bar that reaches it (at the open if it
     gaps over). Lives on 4H closes: target on a close at/above it, dead on a 4H close back inside the hub, 1 ATR
     stop, 10 candles.
  B  the standing rule, exactly the hub A card's entry: the engine's confirmed 4H break, the first close back near
     the edge (0.5 ATR, 5 candles), room 1.5+ at the retest, 1 ATR stop, close-based, 10 candles ("atr10").
  C  the 1H break-and-retest: from the cross, 1H candles. A test is a 1H candle whose low comes within 0.5 ATR of the
     hub edge (or dips in) and closes at or above the edge. Entry = the close of the first 1H candle closing above the
     trigger at or after a completed test (the test candle may be it). A 1H close back inside the hub first kills it.
     Must complete within 2 sessions (the cross day and the next). Then the same 4H exits as A.

Trigger: the Lab's own (the hub high), as the spec requires, so every Arm B trade belongs to a signal. The live card's
trigger can sit a little higher (its lowest close + k·ATR); with the Lab's, a test and the entry close can be the same
1H candle more often. The 1H candles are built from the same 30-minute download as the 4H ones, so the
window is identical and nothing extra is stored on disk. Results go to the "lab_trigcross" setting.
"""
from __future__ import annotations

import gc
import json
import logging
import threading
import time
import traceback

import numpy as np
import pandas as pd

import db
import engine

log = logging.getLogger("lab_trigcross")
_LOCK = threading.Lock()
_CANCEL = threading.Event()
NY = "America/New_York"
ROOM_MIN, SESS_MIN = 1.5, 4
GATE = dict(n=100, recent_n=20)


def _set(**kw):
    cur = json.loads(db.get_setting("lab_trigcross") or "{}")
    cur.update(kw)
    db.set_setting("lab_trigcross", json.dumps(cur, default=lambda o: o.item() if hasattr(o, "item") else str(o)))


def status() -> dict:
    st = json.loads(db.get_setting("lab_trigcross") or "{}")
    if st.get("status") == "running" and not _LOCK.locked():
        st.update(status="stopped", error="Interrupted (the server restarted part-way). Tap Run again.")
        db.set_setting("lab_trigcross", json.dumps(st))
    return st


def one_hour(b: pd.DataFrame) -> pd.DataFrame:
    """Regular-hours 1H candles from 30-minute bars (9:30, 10:30 … 15:30; the last one is half an hour)."""
    df = b.tz_convert(NY)
    m = df.index.hour * 60 + df.index.minute
    df = df[(m >= 570) & (m < 960)]
    if df.empty:
        return df
    key = df.index.normalize() + pd.to_timedelta(((m[(m >= 570) & (m < 960)] - 570) // 60) * 60 + 570, unit="m")
    g = df.groupby(key)
    out = pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(), "Low": g["Low"].min(), "Close": g["Close"].last()})
    out.index = pd.DatetimeIndex(out.index)
    return out


def _walk4(close4, ends4, start_k, after, entry, edge, target, stop, horizon):
    """4H close-based exits for positions opened mid-candle: the first close counted is the 4H candle ending after
    `after`. Target on a close at/above it; dead on a close back inside the hub (below the edge) or at the stop."""
    k0 = start_k
    while k0 < len(close4) and ends4[k0] <= after:
        k0 += 1
    for k in range(k0, min(k0 + horizon, len(close4))):
        c = close4[k]
        if c >= target:
            return (target / entry - 1) * 100, "target", k
        if c < edge or c <= stop:
            return (c / entry - 1) * 100, "stop", k
    if k0 + horizon - 1 < len(close4):
        k = k0 + horizon - 1
        return (close4[k] / entry - 1) * 100, "time", k
    return None


def signals_for(sym: str, b30: pd.DataFrame, s) -> list[dict]:
    c4 = engine.to_4h(b30)
    if c4 is None or len(c4) < 60:
        return []
    res = engine.analyze(c4, s)
    close4 = res["close"].to_numpy(float)
    high4, low4 = c4["High"].to_numpy(float), c4["Low"].to_numpy(float)
    atr4 = res["atr"].to_numpy(float)
    idx4 = c4.index
    ends4 = np.array([engine.candle_end(t.tz_convert(NY)).value for t in idx4])
    h1 = one_hour(b30)
    b = b30.tz_convert(NY)
    bm = b.index.hour * 60 + b.index.minute
    b = b[(bm >= 570) & (bm < 960)]
    trade_of = {id(t["hub"]): t for t in res["trades"]}
    out = []
    n = len(close4)
    for h in res["hubs"]:
        if h["active"] or h["sessions"] < SESS_MIN:
            continue
        e = h["end_i"]
        A = float(atr4[e]) if e < n else float("nan")
        if not A or A != A:
            continue
        edge = float(h["high"])
        trig = edge          # the spec: "the same trigger the Lab's hub test already uses" — the hub high
        cross = None
        for i in range(e + 1, min(e + 1 + s.horizon, n)):
            if close4[i] < h["low"]:
                break                                            # broke down first: not an up episode
            if high4[i] >= trig:
                cross = i
                break
        if cross is None:
            continue
        target, shelf = engine.target_for(res["hubs"], h, 1, float(atr4[cross]), s)
        w = edge - float(h["low"]) if edge > h["low"] else A
        mid = edge - w / 2
        room = (target - trig) / (trig - mid) if trig > mid else None
        if room is None or room < ROOM_MIN or target <= trig:
            continue
        # the 30-minute bar that first reaches the trigger (inside the cross candle)
        c_start, c_end = idx4[cross].tz_convert(NY), pd.Timestamp(ends4[cross], tz="UTC").tz_convert(NY)
        seg = b[(b.index >= c_start) & (b.index < c_end)]
        hit = seg[seg["High"] >= trig]
        if hit.empty:
            continue
        t_cross = hit.index[0]
        fill = max(trig, float(hit["Open"].iloc[0]))
        sig = dict(sym=sym, t=str(t_cross), edge=edge, trig=trig, target=float(target), room=round(room, 2),
                   late=bool(cross > e + 1),       # Nov 26: crossed AFTER the candle right after the hub (live can see it)
                   saved=bool(close4[cross] <= edge))           # the cross candle's own 4H close fell back inside
        # ---- Arm A: cross entry
        rA = _walk4(close4, ends4, cross, t_cross.value, fill, edge, target, fill - A, s.horizon)
        sig["a"] = round(rA[0], 3) if rA else None
        # ---- Arm B: the standing rule (the engine's confirmed break + the retest), the hub A card exactly
        tB = trade_of.get(id(h))
        sig["b"], sig["b_entered"], sig["b_bars"], sig["b_t"] = 0.0, False, None, None
        reached_first = False
        if tB is not None and tB["direction"] == 1 and not tB.get("chase") and tB["outcome"] != "pending":
            import lab_exits
            rt = lab_exits._retest_entry(close4, low4, high4, atr4, tB["i0"], 1, tB["edge"])
            if rt:
                j, e2 = rt
                A2 = float(atr4[j])
                risk2 = e2 - mid
                rr2 = (tB["target"] - e2) / risk2 if risk2 > 0 else None
                pre = close4[cross:j + 1]
                reached_first = bool((pre >= target).any())
                if rr2 is not None and rr2 >= ROOM_MIN and A2 == A2:
                    rB = lab_exits._exit(close4, j, 1, e2, float(tB["target"]), e2 - A2, s.horizon)
                    if rB:
                        sig.update(b=round(rB[0], 3), b_entered=True, b_bars=j - cross, b_t=str(idx4[j]))
        if not sig["b_entered"]:
            reached_first = bool((close4[cross:min(cross + s.horizon, n)] >= target).any())
        sig["missed"] = reached_first
        # ---- Arm C: the 1H break-and-retest, within 2 sessions of the cross
        sig["c"], sig["c_entered"], sig["c_t"] = 0.0, False, None
        days = sorted({d for d in h1.index.normalize() if d >= t_cross.normalize()})[:2]
        hh = h1[(h1.index.normalize().isin(days)) & (h1.index + pd.Timedelta(hours=1) > t_cross)]
        hh = hh[hh.index >= t_cross.floor("h") - pd.Timedelta(minutes=30)]
        tested = False
        for ts, r in hh.iterrows():
            if r["Close"] < edge:
                break                                            # a 1H close back inside the hub kills it
            if r["Low"] <= edge + 0.5 * A:
                tested = True
            if tested and r["Close"] > trig:
                t_end = min(ts + pd.Timedelta(hours=1), ts.normalize() + pd.Timedelta(hours=16))
                rC = _walk4(close4, ends4, cross, t_end.value, float(r["Close"]), edge, target, float(r["Close"]) - A, s.horizon)
                if rC:
                    sig.update(c=round(rC[0], 3), c_entered=True, c_t=str(t_end))
                break
        out.append(sig)
    return out


def _card(x: np.ndarray, entered: np.ndarray, ts: list[str], syms: list[str]) -> dict:
    """Step 0 format, per signal: avg / halves / last 6 months / without top-5 on the per-signal series (zeroes in);
    win% and PF on the entered trades only."""
    n = len(x)
    if not n:
        return dict(n=0)
    order = np.argsort(np.array(ts))
    x, entered, ts, syms = x[order], entered[order], [ts[i] for i in order], [syms[i] for i in order]
    ent = x[entered]
    wins, losses = ent[ent > 0], ent[ent <= 0]
    pf = float(wins.sum() / -losses.sum()) if losses.sum() < 0 else None
    cut = (pd.Timestamp(ts[-1]) - pd.Timedelta(days=182)).isoformat()
    rec = np.array([x[i] for i in range(n) if ts[i] >= cut])
    by = {}
    for v, sy in zip(x, syms):
        by[sy] = by.get(sy, 0.0) + v
    top = set(sorted(by, key=by.get, reverse=True)[:5])
    ex = np.array([v for v, sy in zip(x, syms) if sy not in top])
    c = dict(n=n, entered=int(entered.sum()), entry_rate=round(100 * entered.mean(), 1),
             win=round(100 * len(wins) / len(ent), 1) if len(ent) else None, avg=round(float(x.mean()), 3),
             avg_entered=round(float(ent.mean()), 3) if len(ent) else None, pf=round(pf, 2) if pf else None,
             early=round(float(x[:n // 2].mean()), 3), late=round(float(x[n // 2:].mean()), 3),
             recent=round(float(rec.mean()), 3) if len(rec) else None, recent_n=len(rec),
             ex_top5=round(float(ex.mean()), 3) if len(ex) else None, stocks=len(by))
    checks = [("n", n >= GATE["n"]), ("avg", c["avg"] > 0), ("pf", (c["pf"] or 0) > 1), ("early", c["early"] > 0),
              ("late", c["late"] > 0), ("recent", (c["recent"] or 0) > 0 and c["recent_n"] >= GATE["recent_n"]),
              ("ex_top5", (c["ex_top5"] or 0) > 0)]
    c["checks"] = {k: bool(v) for k, v in checks}
    c["passed"] = all(v for _, v in checks)
    return c


def summarize(sigs: list[dict], b_all: list[float]) -> dict:
    ok = [g for g in sigs if g["a"] is not None]
    ts, syms = [g["t"] for g in ok], [g["sym"] for g in ok]
    A = _card(np.array([g["a"] for g in ok]), np.ones(len(ok), bool), ts, syms)
    B = _card(np.array([g["b"] for g in ok]), np.array([g["b_entered"] for g in ok]), ts, syms)
    C = _card(np.array([g["c"] for g in ok]), np.array([g["c_entered"] for g in ok]), ts, syms)
    med = lambda v: float(np.median(v)) if len(v) else None
    hrs = lambda a, b: (pd.Timestamp(b) - pd.Timestamp(a)).total_seconds() / 3600
    bz = [g for g in ok if not g["b_entered"]]
    diag = dict(signals=len(ok),
                missed=round(100 * np.mean([g["missed"] for g in ok]), 1) if ok else None,
                saved=round(100 * np.mean([g["saved"] for g in ok]), 1) if ok else None,
                b_rate=B.get("entry_rate"), b_bars_median=med([g["b_bars"] for g in ok if g["b_entered"]]),
                c_rate=C.get("entry_rate"),
                c_hours_median=med([hrs(g["t"], g["c_t"]) for g in ok if g["c_entered"]]),
                b_hours_median=med([hrs(g["t"], g["b_t"]) for g in ok if g["b_entered"]]),
                c_caught_b_zeroes=sum(1 for g in bz if g["c_entered"]), b_zeroes=len(bz),
                overlap=sum(1 for g in ok if g["b_entered"] and g["c_entered"]))
    x = np.array(b_all)
    w, l = x[x > 0], x[x <= 0]
    repro = dict(n=len(x), avg=round(float(x.mean()), 3) if len(x) else None,
                 win=round(100 * len(w) / len(x), 1) if len(x) else None,
                 pf=round(float(w.sum() / -l.sum()), 2) if len(l) and l.sum() < 0 else None)
    repro["ok"] = bool(len(x) and abs(repro["avg"] - 0.50) <= 0.15 and 0.85 * 319 <= len(x) <= 1.15 * 319 and abs(repro["win"] - 52) <= 5)
    best = None
    for k, card in (("A", A), ("C", C)):
        if card.get("passed") and card["avg"] > (B.get("avg") or 0) + 0.05:
            best = k if best is None or card["avg"] > (A if best == "A" else C)["avg"] else best
    verdict = ("no verdict: Arm B's entered trades don't reproduce the hub A card" if not repro["ok"] else
               f"Arm {best} earns the change" if best else "the standing rule stays (no challenger beat it by 0.05%+/signal and passed its gate)")
    # Nov 26 (owner): live now alerts only crosses of a hub that ended at an EARLIER close — the 'late' slice (the
    # candle right after the hub can't be told apart live from a poke that closes back inside). Arm A, split.
    split = {}
    for name, keep in (("just_ended", True), ("next_candle", False)):
        g = [x for x in ok if bool(x.get("late")) == keep]
        split[name] = _card(np.array([x["a"] for x in g]), np.ones(len(g), bool), [x["t"] for x in g], [x["sym"] for x in g]) if g else dict(n=0)
        split[name]["n"] = len(g)
    return dict(A=A, B=B, C=C, diag=diag, repro=repro, verdict=verdict, split=split)


def run(years: int = 2):
    if not _LOCK.acquire(blocking=False):
        return
    _CANCEL.clear()
    try:
        import data
        import lab_exits
        import scanner
        import universe
        s = scanner.SETTINGS
        days = 720 if years == 2 else 365 * years
        syms = universe.board_universe()
        started = time.time()
        _set(status="running", done=0, total=len(syms), started=pd.Timestamp.now(tz="UTC").isoformat(), error=None, days=days)
        sigs, b_all, failed = [], [], 0
        lab_exits.DAYS = days
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
                try:
                    bb = bars.get(sym)
                    if bb is None or bb.empty:
                        continue
                    sigs += signals_for(sym, bb, s)
                    # the reproduction gate: every hub A retest (the hub_long rule), signal or not
                    rule = next(t for k, _, t, _e in lab_exits.LIVE_RULES if k == "hub_long")
                    for r in lab_exits.trades_for(sym, engine.to_4h(bb), s, None, "regime"):
                        if rule(r) and "atr10" in r["ex"]:
                            b_all.append(float(r["ex"]["atr10"][0]))
                except Exception:
                    traceback.print_exc()
            bars = None
            gc.collect()
            _set(done=min(i + lab_exits.CHUNK, len(syms)), signals=len(sigs), failed=failed,
                 heartbeat=pd.Timestamp.now(tz="UTC").isoformat())
            time.sleep(1.0)
        out = summarize(sigs, b_all)
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
    if _LOCK.locked() or lab_exits._LOCK.locked():       # one Lab download at a time (memory, Alpaca's rate limit)
        return False
    threading.Thread(target=run, kwargs=dict(years=years), daemon=True).start()
    return True


def stop():
    _CANCEL.set()
