"""Hub Board (market-wide scan), public track record, and chart data for ticker pages."""
from __future__ import annotations

import json
import logging
import os
import threading
import time

import pandas as pd

import data
import db
import engine
import patterns
import scanner
import universe

log = logging.getLogger("board")
_lock = threading.Lock()
BOARD_DAYS = int(os.getenv("BOARD_DAYS", os.getenv("SCAN_DAYS", 260)))


def record_start() -> str:
    """The track record only counts breakouts after the board first ran (no hindsight)."""
    v = db.get_setting("record_start")
    if not v:
        v = pd.Timestamp.now(tz=engine.NY).isoformat()
        db.set_setting("record_start", v)
    return v


def _board_status(c4: pd.DataFrame, res: dict) -> dict:
    st = engine.current_status(c4, res, scanner.SETTINGS)
    st.pop("spark", None)
    p = st["last_close"]
    if st.get("hub") and st["state"] in ("forming", "watching"):
        h = st["hub"]
        width = max(h["high"] - h["low"], 1e-9)
        st["pos"] = round((p - h["low"]) / width, 3)            # 0 = at hub low, 1 = at hub high
        st["to_high_pct"] = round((h["high"] / p - 1) * 100, 2)
        st["to_low_pct"] = round((h["low"] / p - 1) * 100, 2)
        st["near_edge"] = "up" if st["pos"] >= 0.75 else "down" if st["pos"] <= 0.25 else None
    if st.get("target") is not None:
        st["to_target_pct"] = round((st["target"] / p - 1) * 100, 2)
    return st


def _record(sym: str, c4: pd.DataFrame, res: dict, start: pd.Timestamp, pattern: str = "hub", trades: list | None = None):
    idx = res["close"].index
    for t in (res["trades"] if trades is None else trades):
        bt = idx[t["i0"]]
        if bt <= start:
            continue
        h = t["hub"]
        db.upsert_signal(
            id=(f"{sym}|{bt.isoformat()}" if pattern == "hub" else f"T|{sym}|{bt.isoformat()}" if pattern == "tightening"
                else f"{pattern}|{sym}|{bt.isoformat()}"), pattern=pattern,
            symbol=sym, direction=t["direction"],
            hub_low=round(h["low"], 2), hub_high=round(h["high"], 2), hub_sessions=h["sessions"],
            breakout_time=bt.isoformat(), entry=round(t["entry"], 2), target=round(t["target"], 2),
            target_kind="shelf" if t["shelf"] else "atr",
            outcome=t["outcome"], outcome_time=idx[t["oi"]].isoformat() if t["oi"] is not None else None,
            ret_pct=None if t["ret"] is None else round(t["ret"], 2), chase=int(bool(t["chase"])))


# live progress of the market sweep, read by the scanner agent on Discover
SCAN = dict(running=False, done=0, total=0, current=None, started=None, finished=None, found=[])
FIND_STATES = {"breakout_up": "breakout", "breakdown": "breakdown"}


def scan_board() -> int:
    """Scan the whole universe (plus your watchlist). Runs after each 4H close."""
    if not _lock.acquire(blocking=False):      # a sweep is already running
        return 0
    try:
        return _scan_board()
    finally:
        SCAN.update(running=False, current=None, finished=pd.Timestamp.now(tz="UTC").isoformat())
        _lock.release()


def _scan_board() -> int:
    if True:
        syms = sorted(set(universe.board_universe()) | {r["symbol"] for r in db.list_tickers()})
        start = pd.Timestamp(record_start())
        now = pd.Timestamp.now(tz="UTC")
        SCAN.update(running=True, done=0, total=len(syms), current="loading prices", found=[],
                    started=pd.Timestamp.now(tz="UTC").isoformat())
        bars = data.get_bars_many(syms, BOARD_DAYS)
        done = 0
        lab_hub, lab_tri, lab_live, lab_pat = [], [], [], {}
        for k, sym in enumerate(syms):
            time.sleep(0.01)          # let web requests through between stocks so the site stays quick during a scan
            SCAN.update(current=sym, done=k)
            try:
                c4 = engine.to_4h(bars.get(sym, pd.DataFrame()), now=now)
                if len(c4) < scanner.SETTINGS.window + 15:
                    continue
                res = engine.analyze(c4, scanner.SETTINGS)
                bs = _board_status(c4, res)
                db.set_board(sym, json.dumps(bs))
                kind = FIND_STATES.get(bs.get("state"))
                if not kind and bs.get("state") == "forming" and bs.get("near_edge"):
                    kind = "at edge"
                if kind:
                    SCAN["found"].append(dict(symbol=sym, kind=kind))
                _record(sym, c4, res, start)
                lab_hub += res["trades"]
                try:      # more setups: triangles, channels, support & resistance
                    pa = patterns.analyze_all(c4, res, scanner.SETTINGS)
                    bs["setups"] = patterns.live_json(sym, c4, res, pa)
                    db.set_board(sym, json.dumps(bs))
                    for kind in patterns.KINDS:
                        if kind == "hub":
                            continue
                        tr = [x for x in pa["trades"] if x["kind"] == kind]
                        _record(sym, c4, res, start, kind, tr)
                        lab_pat.setdefault(kind, []).extend(tr)
                    for st in bs["setups"]:
                        if st["state"] in ("breakout_up", "breakdown"):
                            SCAN["found"].append(dict(symbol=sym, kind="breakout" if st["state"] == "breakout_up" else "breakdown", setup=st["name"]))
                except Exception as e:
                    log.warning("pattern scan failed for %s: %s", sym, e)
                try:      # test mode: tightening hubs (triangles), admin-only
                    tri = engine.analyze_tightening(c4, res, scanner.SETTINGS)
                    _record(sym, c4, res, start, "tightening", tri["trades"])
                    lab_tri += tri["trades"]
                    lab_live += _tri_live(sym, c4, res, tri)
                except Exception as e:
                    log.warning("tightening scan failed for %s: %s", sym, e)
                done += 1
            except Exception as e:
                log.warning("board scan failed for %s: %s", sym, e)
        db.set_setting("lab", json.dumps(dict(
            updated=pd.Timestamp.now(tz=engine.NY).isoformat(), symbols=done, days=BOARD_DAYS,
            hub=engine.stats(lab_hub), tightening=engine.stats(lab_tri), live=lab_live,
            patterns={k: engine.stats(v) for k, v in lab_pat.items()})))
        log.info("board scan done: %d/%d symbols", done, len(syms))
        return done


def _tri_live(sym: str, c4: pd.DataFrame, res: dict, tri: dict) -> list[dict]:
    """Tightening hubs that are active now, or broke out within the last few candles."""
    n, close, out = len(c4), res["close"], []
    for p in tri["patterns"]:
        if p["active"]:
            up, dn = engine.tri_lines(p, n - 1)
            out.append(dict(symbol=sym, kind=p["kind"], state="forming", sessions=p["sessions"],
                            upper=round(up, 2), lower=round(dn, 2), last=round(float(close.iloc[-1]), 2)))
    for t in tri["trades"]:
        if n - 1 - t["i0"] <= 4:
            out.append(dict(symbol=sym, kind=t["hub"]["kind"], state="breakout_up" if t["direction"] == 1 else "breakdown",
                            sessions=t["hub"]["sessions"], edge=round(t["edge"], 2), target=round(t["target"], 2),
                            outcome=t["outcome"], last=round(float(close.iloc[-1]), 2)))
    return out


def lab() -> dict:
    """Admin-only comparison: plain hubs vs tightening hubs."""
    snap = json.loads(db.get_setting("lab") or "{}")
    fwd = {}
    for pat in ("hub", "tightening"):
        sigs = db.all_signals(1000, pat)
        done = [x for x in sigs if x["outcome"] not in ("pending", None) and not x["chase"]]
        rets = [x["ret_pct"] for x in done if x["ret_pct"] is not None]
        fwd[pat] = dict(signals=len(sigs), closed=len(done),
                        reached=round(100 * sum(x["outcome"] == "follow-through" for x in done) / len(done), 1) if done else None,
                        win_rate=round(100 * sum(r > 0 for r in rets) / len(rets), 1) if rets else None,
                        avg_ret=round(sum(rets) / len(rets), 2) if rets else None)
    return dict(snapshot=snap, forward=fwd, since=record_start(), recent=db.all_signals(40, "tightening"))


def board() -> dict:
    rows = []
    for r in db.board_rows():
        st = json.loads(r["status"] or "{}")
        st["symbol"] = r["symbol"]
        rows.append(st)
    watch = {r["symbol"] for r in db.list_tickers()}
    for r in rows:
        r["watching"] = r["symbol"] in watch
    live = [r for r in rows if r.get("state") in ("breakout_up", "breakdown")]
    live.sort(key=lambda r: abs(r.get("to_target_pct") or 99))
    forming = [r for r in rows if r.get("state") in ("forming", "watching")]
    near = [r for r in forming if r.get("near_edge")]
    near.sort(key=lambda r: -abs(r["pos"] - 0.5))
    forming.sort(key=lambda r: -(r.get("hub") or {}).get("sessions", 0))
    recent = [r for r in rows if r.get("state") in ("follow-through", "snap-back", "stalled")]
    updated = max((r["updated_at"] for r in db.board_rows()), default=None)
    setups = []      # every non-hub setup on the board, one row each
    for r in rows:
        for st in r.get("setups") or []:
            if st["state"] == "between":
                continue
            setups.append(dict(st, symbol=r["symbol"], last_close=r.get("last_close"), watching=r["watching"]))
    rank = {"breakout_up": 0, "breakdown": 0, "testing": 1, "forming": 2}
    setups.sort(key=lambda x: (rank.get(x["state"], 9), x["symbol"]))
    for r in rows:
        r.pop("setups", None)
    return dict(updated=updated, universe=len(rows), live=live, near=near, forming=forming, recent=recent, setups=setups)


def _summary(sigs: list) -> dict:
    closed = [s for s in sigs if s["outcome"] not in ("pending", None) and not s["chase"]]
    rets = [s["ret_pct"] for s in closed if s["ret_pct"] is not None]
    return dict(signals=len(sigs), closed=len(closed),
                reached=round(100 * sum(s["outcome"] == "follow-through" for s in closed) / len(closed), 1) if closed else None,
                win_rate=round(100 * sum(r > 0 for r in rets) / len(rets), 1) if rets else None,
                avg_ret=round(sum(rets) / len(rets), 2) if rets else None)


def by_pattern() -> dict:
    return {k: dict(_summary(db.all_signals(1000, k)), name=patterns.KINDS[k]) for k in patterns.KINDS}


def track_record(pattern: str = "hub") -> dict:
    sigs = db.all_signals(1000, pattern)
    closed = [s for s in sigs if s["outcome"] not in ("pending", None) and not s["chase"]]
    rets = [s["ret_pct"] for s in closed if s["ret_pct"] is not None]
    wins = [r for r in rets if r > 0]
    by = {o: sum(1 for s in closed if s["outcome"] == o) for o in ("follow-through", "snap-back", "stalled")}
    return dict(
        since=record_start(),
        stats=dict(signals=len(sigs), closed=len(closed), open=sum(1 for s in sigs if s["outcome"] == "pending"),
                   chases=sum(1 for s in sigs if s["chase"]),
                   win_rate=round(100 * len(wins) / len(rets), 1) if rets else None,
                   avg_ret=round(sum(rets) / len(rets), 2) if rets else None,
                   avg_win=round(sum(wins) / len(wins), 2) if wins else None,
                   avg_loss=round(sum(r for r in rets if r <= 0) / max(1, len(rets) - len(wins)), 2)
                   if len(rets) > len(wins) else None,
                   by_outcome=by),
        signals=sigs[:200], pattern=pattern, patterns=by_pattern())


def _seg(p, idx, t, a, b, kind, live):
    ua, da = patterns.lines_at(p, a)
    ub, db_ = patterns.lines_at(p, b)
    return dict(kind=kind, name=patterns.KINDS[kind], live=live, start=t(idx[a]), end=t(idx[b]),
                ua=round(ua, 2), ub=round(ub, 2), la=round(da, 2), lb=round(db_, 2))


def chart(sym: str, lab_mode: bool = False) -> dict:
    sym = scanner.clean_symbol(sym)
    c4 = scanner.load_4h(sym, scanner.SCAN_DAYS)
    if c4.empty:
        raise ValueError("no price data")
    res = engine.analyze(c4, scanner.SETTINGS)
    st = engine.current_status(c4, res, scanner.SETTINGS)
    idx = c4.index

    def t(ts):   # chart library uses UTC seconds; shift so labels read in New York time
        return int(ts.tz_localize(None).timestamp())

    candles = [dict(time=t(i), open=round(r.Open, 2), high=round(r.High, 2), low=round(r.Low, 2), close=round(r.Close, 2))
               for i, r in c4.iterrows()]
    hubs = [dict(start=t(idx[h["start_i"]]), end=t(idx[h["end_i"]]), low=round(h["low"], 2), high=round(h["high"], 2),
                 sessions=h["sessions"], active=h["active"]) for h in res["hubs"]]
    trades = [dict(time=t(idx[x["i0"]]), direction=x["direction"], target=round(x["target"], 2),
                   outcome=x["outcome"], chase=x["chase"],
                   outcome_time=t(idx[x["oi"]]) if x["oi"] is not None else None) for x in res["trades"]]
    out = dict(symbol=sym, candles=candles, hubs=hubs, trades=trades, status=st,
               stats=engine.stats(res["trades"]), watching=db.get_ticker(sym) is not None)
    try:      # other setups, drawn as lines on the chart
        pa = patterns.analyze_all(c4, res, scanner.SETTINGS)
        st["setups"] = patterns.live_json(sym, c4, res, pa)
        draw = []
        for L in pa["live"]:
            if L["kind"] != "sr" and L["state"] == "forming":
                p = L["p"]
                draw.append(_seg(p, idx, t, p["a"], len(idx) - 1, L["kind"], True))
        for x in pa["trades"][-12:]:
            if x["kind"] != "sr" and x["i0"] >= len(idx) - 120:
                p = x["p"]
                draw.append(_seg(p, idx, t, p["a"], x["i0"], x["kind"], False))
        levels = []
        for L in pa.get("levels_now", []):
            levels.append(dict(price=round(L["price"], 2), touches=L["touches"], since=t(idx[L["first"]])))
        out["setups"] = dict(lines=draw, levels=levels,
                             trades=[dict(time=t(idx[x["i0"]]), direction=x["direction"], kind=x["kind"], outcome=x["outcome"],
                                          chase=x["chase"]) for x in pa["trades"] if x["i0"] >= len(idx) - 160],
                             stats={k: engine.stats([x for x in pa["trades"] if x["kind"] == k]) for k in patterns.KINDS if k != "hub"})
    except Exception as e:
        log.warning("chart setups failed for %s: %s", sym, e)
    if lab_mode:   # admin test mode: tightening hubs as two lines each
        tri = engine.analyze_tightening(c4, res, scanner.SETTINGS)
        out["tightening"] = []
        for p in tri["patterns"]:
            b = min(len(idx) - 1, p["end_i"] + 3)
            ua, la = engine.tri_lines(p, p["start_i"])
            ub, lb = engine.tri_lines(p, b)
            out["tightening"].append(dict(kind=p["kind"], start=t(idx[p["start_i"]]), end=t(idx[b]),
                                          ua=round(ua, 2), la=round(la, 2), ub=round(ub, 2), lb=round(lb, 2)))
        out["tightening_trades"] = [dict(time=t(idx[x["i0"]]), direction=x["direction"], outcome=x["outcome"],
                                         chase=x["chase"]) for x in tri["trades"]]
        out["tightening_stats"] = engine.stats(tri["trades"])
    return out
