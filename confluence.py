"""Confluence log: does a gamma level sitting on a setup's own trigger or target make it work more often?

Patch 173 shows confluence as a fact about levels. Nobody has measured whether it helps, so this tags every
confirmed setup (the live record) and every hub breakout with confluence yes/no at the moment it fires, using
that day's options chain, and the Lab and the next-day check split their results by the tag. Gamma chains are
not stored historically, so tagging starts now: only signals from the last 2 days are tagged. Information only.
"""
from __future__ import annotations

import logging

import pandas as pd

import db
import engine
import gamma
import swing_card

log = logging.getLogger("confluence")
MAX_PER_RUN = 25

with db._lock:
    db._conn.execute("""CREATE TABLE IF NOT EXISTS confluence_log (
        signal_id TEXT PRIMARY KEY, symbol TEXT, kind TEXT, confluence INTEGER, detail TEXT,
        trigger REAL, target REAL, logged_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    db._conn.commit()
    for _c, _d in (("n_levels", "INTEGER"), ("gamma_conf", "INTEGER"),
                   # Nov 25 (owner): three gamma questions, tagged from today, judged at 40+ resolved a side
                   ("wall_before", "INTEGER"), ("neg_gex", "INTEGER"), ("edge_support", "INTEGER"), ("net_gex", "REAL")):
        try:
            db._conn.execute(f"ALTER TABLE confluence_log ADD COLUMN {_c} {_d}")
            db._conn.commit()
        except Exception:
            pass


def _pending() -> list[dict]:
    rows = db._run("""SELECT s.* FROM signals s LEFT JOIN confluence_log c ON c.signal_id = s.id
                      WHERE COALESCE(s.pattern, 'hub') IN ('rules', 'hub') AND s.entry IS NOT NULL AND s.target IS NOT NULL
                        AND c.signal_id IS NULL ORDER BY COALESCE(s.entry_time, s.breakout_time) DESC""").fetchall()
    cut = pd.Timestamp.now(tz=engine.NY) - pd.Timedelta(days=2)
    out = []
    for r in rows:
        r = dict(r)
        try:
            t = pd.Timestamp(r.get("entry_time") or r["breakout_time"]).tz_convert(engine.NY)
        except Exception:
            continue
        if t >= cut:
            out.append(r)
    return out


def check(sig: dict) -> dict | None:
    """The confluence reading for one signal, or None when the chain can't be read (left untagged, retried)."""
    d = int(sig.get("direction") or 1)
    trigger = sig.get("hub_high") if d == 1 else sig.get("hub_low")
    target, spot = sig.get("target"), sig.get("entry")
    if not (trigger and target and spot):
        return None
    import scanner
    sh = gamma.shelves(sig["symbol"], spot, n=swing_card.GAMMA_LADDER_N)
    gx = swing_card._gamma_facts(sig["symbol"], spot, sh)
    if not gx:
        return None                       # no chain yet: leave untagged and retry, so both groups use the same inputs
    c4 = scanner.load_4h(sig["symbol"], scanner.SCAN_DAYS)
    tr = pd.concat([c4["High"] - c4["Low"], (c4["High"] - c4["Close"].shift()).abs(), (c4["Low"] - c4["Close"].shift()).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1]) if len(c4) > 15 else None
    conf = swing_card._gamma_confluence(gx, d, float(trigger), float(target), atr, swing_card.price_levels(c4))
    detail = "; ".join(f"{c['what']} {c['strike']:g} at the {c['at']}" for c in conf)
    q = gamma_questions(sig["symbol"], float(spot), d, float(trigger), float(target), atr)
    return dict(signal_id=sig["id"], symbol=sig["symbol"], kind="setup" if sig.get("pattern") == "rules" else "breakout",
                confluence=int(bool(conf)), detail=detail or None, trigger=float(trigger), target=float(target),
                n_levels=len(conf), gamma_conf=int(any(c["kind"] == "gamma" for c in conf)), **q)


NODE_SHARE = 0.25      # a strike counts as a node when its net GEX (all grid expiries added) is 25%+ of the band's largest


def gamma_questions(sym: str, spot: float, d: int, trigger: float, target: float, atr: float | None) -> dict:
    """Nov 25 (owner), pre-decided before any result:
      wall_before   a positive node strictly between the trigger and the target (it may stall the move)
      neg_gex       the band's total net GEX is below zero (moves can run)
      edge_support  a positive node within 0.5 ATR of the trigger (the retest may hold)
    From the same grid the heat map draws. Unknown (None) when the chain can't be read."""
    g = gamma.grid(sym, spot)
    if not g or not g.get("rows"):
        return dict(wall_before=None, neg_gex=None, edge_support=None, net_gex=None)
    sums = {r["strike"]: sum(r["gex"]) for r in g["rows"]}
    big = max((abs(v) for v in sums.values()), default=0) or 1
    nodes = [k for k, v in sums.items() if v > 0 and v >= NODE_SHARE * big]
    lo, hi = sorted((trigger, target))
    tol = 0.5 * atr if atr else trigger * 0.005
    return dict(wall_before=int(any(lo < k < hi for k in nodes)), neg_gex=int((g.get("net_total") or 0) < 0),
                edge_support=int(any(abs(k - trigger) <= tol for k in nodes)), net_gex=g.get("net_total"))


def tag_recent() -> int:
    n = 0
    for sig in _pending()[:MAX_PER_RUN]:
        try:
            r = check(sig)
        except Exception as e:
            log.warning("confluence %s failed: %s", sig.get("symbol"), e)
            continue
        if r:
            db._run(f"INSERT OR REPLACE INTO confluence_log ({', '.join(r)}) VALUES ({', '.join('?' * len(r))})", tuple(r.values()))
            n += 1
    if n:
        log.info("confluence: tagged %d signals", n)
    return n


def stats() -> list[dict]:
    """Results split by confluence, per kind: resolved record outcomes and next-day closes."""
    out = []
    groups = (("any", "c.confluence = 1"), ("none", "c.confluence = 0"), ("gamma", "c.gamma_conf = 1"), ("multi", "c.n_levels >= 2"),
              ("wall_y", "c.wall_before = 1"), ("wall_n", "c.wall_before = 0"), ("neg_y", "c.neg_gex = 1"), ("neg_n", "c.neg_gex = 0"),
              ("edge_y", "c.edge_support = 1"), ("edge_n", "c.edge_support = 0"))
    for kind in ("setup", "breakout"):
        for flag, where in groups:
            rows = [dict(r) for r in db._run(
                f"""SELECT s.outcome, s.ret_pct, s.direction, n.fav_pct, n.close_pct FROM confluence_log c
                   JOIN signals s ON s.id = c.signal_id LEFT JOIN next_day n ON n.signal_id = c.signal_id
                   WHERE c.kind = ? AND {where}""", (kind,)).fetchall()]
            done = [r for r in rows if r["outcome"] in ("follow-through", "snap-back", "stalled", "hit", "miss", "target", "stop")]
            hits = [r for r in done if r["outcome"] in ("follow-through", "hit", "target")]
            rets = [r["ret_pct"] for r in done if r["ret_pct"] is not None]
            nd = [r["fav_pct"] if r["fav_pct"] is not None else (r["close_pct"] * (r["direction"] or 1) if r["close_pct"] is not None else None) for r in rows]
            nd = [x for x in nd if x is not None]
            out.append(dict(kind=kind, group=flag, tagged=len(rows), resolved=len(done),
                            hit_pct=round(100 * len(hits) / len(done)) if done else None,
                            avg_ret=round(sum(rets) / len(rets), 2) if rets else None,
                            next_day_n=len(nd), next_day_green=round(100 * sum(x > 0 for x in nd) / len(nd)) if nd else None,
                            next_day_avg=round(sum(nd) / len(nd), 2) if nd else None))
    return out
