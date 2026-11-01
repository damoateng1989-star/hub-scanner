"""Hub Board (market-wide scan), public track record, and chart data for ticker pages."""
from __future__ import annotations

import json
import logging
import os
import threading

import pandas as pd

import data
import db
import engine
import scanner
import universe

log = logging.getLogger("board")
_lock = threading.Lock()
BOARD_DAYS = int(os.getenv("BOARD_DAYS", 200))


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


def _record(sym: str, c4: pd.DataFrame, res: dict, start: pd.Timestamp):
    idx = res["close"].index
    for t in res["trades"]:
        bt = idx[t["i0"]]
        if bt <= start:
            continue
        h = t["hub"]
        db.upsert_signal(
            id=f"{sym}|{bt.isoformat()}", symbol=sym, direction=t["direction"],
            hub_low=round(h["low"], 2), hub_high=round(h["high"], 2), hub_sessions=h["sessions"],
            breakout_time=bt.isoformat(), entry=round(t["entry"], 2), target=round(t["target"], 2),
            target_kind="shelf" if t["shelf"] else "atr",
            outcome=t["outcome"], outcome_time=idx[t["oi"]].isoformat() if t["oi"] is not None else None,
            ret_pct=None if t["ret"] is None else round(t["ret"], 2), chase=int(bool(t["chase"])))


def scan_board() -> int:
    """Scan the whole universe (plus your watchlist). Runs after each 4H close."""
    with _lock:
        syms = sorted(set(universe.board_universe()) | {r["symbol"] for r in db.list_tickers()})
        start = pd.Timestamp(record_start())
        now = pd.Timestamp.now(tz="UTC")
        bars = data.get_bars_many(syms, BOARD_DAYS)
        done = 0
        for sym in syms:
            try:
                c4 = engine.to_4h(bars.get(sym, pd.DataFrame()), now=now)
                if len(c4) < scanner.SETTINGS.window + 15:
                    continue
                res = engine.analyze(c4, scanner.SETTINGS)
                db.set_board(sym, json.dumps(_board_status(c4, res)))
                _record(sym, c4, res, start)
                done += 1
            except Exception as e:
                log.warning("board scan failed for %s: %s", sym, e)
        log.info("board scan done: %d/%d symbols", done, len(syms))
        return done


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
    return dict(updated=updated, universe=len(rows), live=live, near=near, forming=forming, recent=recent)


def track_record() -> dict:
    sigs = db.all_signals(1000)
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
        signals=sigs[:200])


def chart(sym: str) -> dict:
    sym = scanner.clean_symbol(sym)
    c4 = scanner.load_4h(sym, 260)
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
    return dict(symbol=sym, candles=candles, hubs=hubs, trades=trades, status=st,
                stats=engine.stats(res["trades"]),
                watching=db.get_ticker(sym) is not None)
