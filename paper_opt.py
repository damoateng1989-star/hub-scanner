"""Paper OPTIONS experiment: the same confirmed hub breakouts as the share paper trader, expressed with
long calls (breakouts up) and long puts (breakdowns), at the money.

A separate, private journal (paper_trades.kind = 'option'). It never mixes into the share record, the public
track record or the board counts. Paper only: orders go to PAPER_URL, never real money. Off by default.
Long calls and puts only: no selling, no spreads, defined risk (the premium paid).

Paper option fills are simulated at the quote and never face real liquidity, so this journal flatters
real-world results. It stays a labelled experiment.
"""
from __future__ import annotations

import logging
import math
import os
import threading
from datetime import date

import pandas as pd

import data
import db
import engine
import notify
import options
import paper
import scanner

log = logging.getLogger("paper-opt")
_lock = threading.Lock()

KIND = "option"
DAILY_MAX = int(os.getenv("PAPER_OPT_DAILY_MAX", 5))        # up to five A setups a day, best reward:risk first
MAX_OPEN = int(os.getenv("PAPER_OPT_MAX_OPEN", 10))
MAX_PREMIUM = float(os.getenv("PAPER_OPT_MAX_PREMIUM", 2000))
MAX_SPREAD = float(os.getenv("PAPER_OPT_MAX_SPREAD_PCT", 0.15))
MIN_DTE_ENTRY, EXIT_DTE = 14, 7


def enabled() -> bool:
    v = db.get_setting("paper_opt_enabled")
    if v is None:
        v = os.getenv("PAPER_OPT_TRADING", "off")
    return str(v).lower() in ("1", "on", "true", "yes")


def set_enabled(on: bool):
    db.set_setting("paper_opt_enabled", "on" if on else "off")


def _ready() -> bool:
    """Needs real Alpaca paper keys: the demo chain is synthetic, so the experiment never simulates fills."""
    if data.provider() == "demo" or not os.getenv("ALPACA_API_KEY") or os.getenv("PAPER_BROKER") == "sim":
        log.info("paper-opt skipped (needs Alpaca paper keys)")
        return False
    return True


def _money(x: float) -> str:
    return f"${x:,.2f}"


def _entries_today() -> int:
    today = pd.Timestamp.now(tz=engine.NY).strftime("%Y-%m-%d")
    n = 0
    for t in db.all_trades(100, kind=KIND):
        try:
            if pd.Timestamp(t["opened_at"]).tz_localize("UTC").tz_convert(engine.NY).strftime("%Y-%m-%d") == today:
                n += 1
        except Exception:
            pass
    return n


def _contract(sym: str, d: int) -> tuple[dict, dict] | tuple[None, str]:
    """The at-the-money call (d=1) or put (d=-1) from the options plan, or (None, why it was skipped)."""
    p = options.plan(sym)
    if p.get("demo"):
        return None, "demo chain"
    pl = next((x for x in p.get("plans") or [] if x["direction"] == d), None)
    if not pl:
        return None, "no options plan for this side"
    c = next((x for x in pl["contracts"] if x["label"] == "At the money"), None)
    if not c or not c.get("symbol"):
        return None, "no at-the-money contract"
    if (pl.get("dte") or 0) < MIN_DTE_ENTRY:
        return None, f"only {pl.get('dte')} days to expiry"
    bid, ask = c.get("bid") or 0, c.get("ask") or 0
    if bid <= 0 or ask <= 0:
        return None, "no live quote"                                  # fail closed
    mid = (bid + ask) / 2
    spread = (ask - bid) / mid
    if c.get("wide_spread") or spread > MAX_SPREAD:
        return None, f"wide spread {spread:.0%} ({_money(bid)} / {_money(ask)})"
    c = dict(c, mid=mid, spread=spread, expiry=pl["expiry"], dte=pl["dte"])
    return c, pl


def a_candidates(window: str) -> list[dict]:
    """A-grade retests (hubs and double bottoms, longs) whose retest closed on the candle we're entering after,
    from the live record of today's rules (board.RULES, logged by the sweep). Best reward:risk at today's price first."""
    import board
    import earnings
    today = pd.Timestamp.now(tz=engine.NY).normalize()
    rows = []
    for s_ in db.all_signals(500, board.RULES):
        if s_.get("grade") != "A" or s_.get("outcome") != "pending" or not s_.get("entry_time") or s_.get("stop") is None:
            continue
        t = pd.Timestamp(s_["entry_time"]).tz_convert(engine.NY)
        fresh = (t.normalize() == today and t.hour == 9) if window == "midday" else \
                (t.normalize() < today and t.hour == 13 and (today - t.normalize()).days <= 4)
        if fresh:
            rows.append(s_)
    if not rows:
        return []
    try:
        live = data.latest_prices([r["symbol"] for r in rows])
    except Exception:
        live = {}
    out = []
    for r in rows:
        px = (live.get(r["symbol"]) or {}).get("price") or r["entry"]
        if not (r["stop"] < px < r["target"]):
            continue                               # already stopped or already at the target: nothing left to enter
        try:
            e = earnings.lookup(r["symbol"])
            if e and e.get("days") is not None and e["days"] <= 7:
                continue                           # don't hold an option through an earnings report
        except Exception:
            pass
        out.append(dict(symbol=r["symbol"], d=1, edge=r["stop"], target=r["target"], price=px, low=r["hub_low"], high=r["hub_high"],
                        kind=r.get("target_kind") or "shelf", hub_start=r["breakout_time"], candle=r["entry_time"],
                        setup=r.get("setup") or "Retest", rr=(r["target"] - px) / max(px - r["stop"], 1e-9)))
    return sorted(out, key=lambda c: (-min(c["rr"], 4.0), -(c["target"] / c["price"] - 1)))


def enter_daily_opt(window: str = "midday") -> list[str] | None:
    """After the confirming close: one options trade per A setup (an A-grade retest), up to DAILY_MAX a day and MAX_OPEN open.
    The stop rides in the trade's `edge` field, so the pre-close check exits on it (1 ATR for hubs, the lows for doubles)."""
    if not enabled() or paper.source() != "board" or not _ready():
        return None
    if window == "midday":                 # wait for the 1:52 PM sweep that confirms the 1:30 close
        import time as _t
        from board import SCAN
        for _ in range(60):
            if not SCAN.get("running"):
                break
            _t.sleep(10)
    msgs = []
    with _lock:
        b = paper.AlpacaPaper()
        try:
            if not b.is_open():
                return None
        except Exception as e:
            log.warning("clock check failed: %s", e)
            return None
        for c in a_candidates(window):
            if _entries_today() >= DAILY_MAX or len(db.open_trades(kind=KIND)) >= MAX_OPEN:
                break
            hub_key = f"OPT:{c['symbol']}|{c['hub_start']}"
            if db.hub_traded(hub_key) or any(t["symbol"] == c["symbol"] for t in db.open_trades(kind=KIND)):
                continue
            try:
                k, pl = _contract(c["symbol"], c["d"])
            except Exception as e:
                log.warning("paper-opt plan failed for %s: %s", c["symbol"], e)
                continue
            if k is None:
                log.info("paper-opt skipped %s (%s)", c["symbol"], pl)
                continue
            each = k["mid"] * 100
            if each > MAX_PREMIUM:
                log.info("paper-opt skipped %s (one contract %s > max %s)", c["symbol"], _money(each), _money(MAX_PREMIUM))
                continue
            qty = max(1, math.floor(MAX_PREMIUM / each))
            try:
                fill, oid = b.enter(k["symbol"], qty, "buy")
            except Exception as e:
                log.warning("paper-opt order failed for %s: %s", k["symbol"], e)
                msgs.append(f"🧪📈 Paper OPTIONS experiment: order for {c['symbol']} failed ({e})")
                continue
            db.add_trade(kind=KIND, symbol=c["symbol"], direction=c["d"], qty=qty, hub_key=hub_key,
                         hub_low=c["low"], hub_high=c["high"], edge=c["edge"], target=round(c["target"], 2),
                         target_kind=c["kind"], entry_candle=c["candle"], entry_price=fill, entry_order=oid,
                         occ=k["symbol"], expiry=k["expiry"], strike=k["strike"], opt_iv=k.get("iv"),
                         opt_delta=k.get("delta"), opt_premium=round(fill * 100 * qty, 2))
            msgs.append(f"🧪📈 Paper OPTIONS experiment: BUY {qty} {c['symbol']} {k['expiry']} ${k['strike']:g} "
                        f"{'call' if c['d'] == 1 else 'put'} @ {_money(fill)} (premium {_money(fill * 100 * qty)})\n"
                        f"A setup: {c['setup']} at {_money(c['price'])}, aiming for {_money(c['target'])}. "
                        f"Exits on the stock: the target, a close below the stop {_money(c['edge'])}, "
                        f"{scanner.SETTINGS.horizon} candles, or under {EXIT_DTE} days to expiry. Max loss is the premium.")
    for m in msgs:
        notify.admins(m)
    return msgs


def _close(b, t: dict, reason: str) -> str:
    try:
        o = b._req("DELETE", f"/v2/positions/{t['occ']}")     # market order closing the option
        price = b._wait_fill(o["id"])
    except Exception as e:
        log.warning("paper-opt close failed for %s: %s", t["occ"], e)
        return f"🧪📈 Paper OPTIONS experiment: couldn't close {t['occ']} ({e}). It'll retry next check."
    pnl = (price - t["entry_price"]) * 100 * t["qty"]
    pct = (price / t["entry_price"] - 1) * 100 if t["entry_price"] else 0
    db.update_trade(t["id"], status="closed", closed_at=pd.Timestamp.now(tz="UTC").isoformat(), exit_price=price,
                    exit_reason=reason, pnl=round(pnl, 2), pnl_pct=round(pct, 2))
    return (f"🧪📈 Paper OPTIONS experiment closed: {t['symbol']} {t['occ']} · {reason}\n"
            f"{t['qty']} @ {_money(t['entry_price'])} → {_money(price)} · P&L {'+' if pnl >= 0 else '-'}{_money(abs(pnl))} ({pct:+.0f}%)")


def _dte(t: dict) -> int:
    try:
        return (date.fromisoformat(t["expiry"]) - pd.Timestamp.now(tz=engine.NY).date()).days
    except Exception:
        return 99


def run_check_opt() -> list[str]:
    """Pre-close (same slots as the share trader): price below the stop (stored as `edge`), time limit, or expiry week."""
    if not db.open_trades(kind=KIND) or not _ready():
        return []
    msgs = []
    with _lock:
        b = paper.AlpacaPaper()
        for t in db.open_trades(kind=KIND):
            try:
                px = data.latest_price(t["symbol"])
                if (px - t["edge"]) * t["direction"] <= 0:
                    msgs.append(_close(b, t, "snap-back"))
                elif paper._candles_held(t) >= scanner.SETTINGS.horizon:
                    msgs.append(_close(b, t, "time limit"))
                elif _dte(t) < EXIT_DTE:
                    msgs.append(_close(b, t, "expiry week"))
            except Exception as e:
                log.warning("paper-opt exit check failed for %s: %s", t["symbol"], e)
    for m in msgs:
        notify.admins(m)
    return msgs


def sync_opt() -> list[str]:
    """Every 5 minutes in market hours: the stock (never the option price) touching the shelf closes the option."""
    if not db.open_trades(kind=KIND) or not _ready():
        return []
    msgs = []
    with _lock:
        b = paper.AlpacaPaper()
        for t in db.open_trades(kind=KIND):
            try:
                px = data.latest_price(t["symbol"])
                if (px - t["target"]) * t["direction"] >= 0:
                    msgs.append(_close(b, t, "follow-through"))
                elif _dte(t) < EXIT_DTE:
                    msgs.append(_close(b, t, "expiry week"))
            except Exception as e:
                log.warning("paper-opt sync failed for %s: %s", t["symbol"], e)
    for m in msgs:
        notify.admins(m)
    return msgs


def summary_opt() -> dict:
    trades = db.all_trades(500, kind=KIND)
    closed = [t for t in trades if t["status"] == "closed"]
    wins = [t for t in closed if (t["pnl"] or 0) > 0]
    return dict(enabled=enabled(), ready=data.provider() != "demo" and bool(os.getenv("ALPACA_API_KEY")),
                max_premium=MAX_PREMIUM, daily_max=DAILY_MAX, max_open=MAX_OPEN, max_spread=MAX_SPREAD, goal=paper.GOAL,
                stats=dict(closed=len(closed), wins=len(wins),
                           win_rate=round(100 * len(wins) / len(closed), 1) if closed else None,
                           total_pnl=round(sum(t["pnl"] or 0 for t in closed), 2),
                           avg_pct=round(sum(t["pnl_pct"] or 0 for t in closed) / len(closed), 2) if closed else None,
                           premium_paid=round(sum(t["opt_premium"] or 0 for t in trades), 2)),
                open=[t for t in trades if t["status"] == "open"], closed=closed[:50])
