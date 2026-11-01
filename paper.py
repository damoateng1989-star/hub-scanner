"""Automatic paper trading.

Uses your Alpaca PAPER account (fake money). The trading URL is hard-coded to Alpaca's paper
endpoint, so this app can never place a real-money order. In demo mode, or without Alpaca keys,
a built-in simulator fills orders at the latest price instead.

Rules (same as the backtest):
  entry      ~2 min before a 4H close, if price is outside a hub that hasn't broken out yet
             (one trade per hub, skipped if price is already at the target)
  target     the next shelf (or the ATR fallback): a limit order sits there
  snap-back  ~2 min before a 4H close, if price is back inside the hub: close at market
  time limit after HORIZON 4H candles: close at market
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time

import pandas as pd
import requests

import data
import db
import engine
import scanner
import telegram_bot

log = logging.getLogger("paper")
PAPER_URL = "https://paper-api.alpaca.markets"   # paper only: never real money
_lock = threading.Lock()


def enabled() -> bool:
    v = db.get_setting("paper_enabled")
    if v is None:
        v = os.getenv("PAPER_TRADING", "off")
    return str(v).lower() in ("1", "on", "true", "yes")


def set_enabled(on: bool):
    db.set_setting("paper_enabled", "on" if on else "off")


def trade_usd() -> float:
    return float(os.getenv("PAPER_TRADE_USD", 5000))


def allow_short() -> bool:
    return os.getenv("PAPER_ALLOW_SHORT", "on").lower() in ("1", "on", "true", "yes")


# ---------------------------------------------------------------- brokers
class AlpacaPaper:
    name = "Alpaca paper"

    def __init__(self):
        self.h = {"APCA-API-KEY-ID": os.getenv("ALPACA_API_KEY", ""),
                  "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET_KEY", "")}

    def _req(self, method: str, path: str, **kw):
        r = requests.request(method, PAPER_URL + path, headers=self.h, timeout=20, **kw)
        if r.status_code >= 300:
            raise RuntimeError(f"Alpaca {r.status_code}: {r.text[:200]}")
        return r.json() if r.text else {}

    def is_open(self) -> bool:
        return bool(self._req("GET", "/v2/clock").get("is_open"))

    def account(self) -> dict | None:
        a = self._req("GET", "/v2/account")
        return dict(equity=float(a["equity"]), cash=float(a["cash"]), buying_power=float(a["buying_power"]))

    def _wait_fill(self, oid: str, secs: int = 30) -> float:
        for _ in range(secs * 2):
            o = self._req("GET", f"/v2/orders/{oid}")
            if o["status"] == "filled":
                return float(o["filled_avg_price"])
            if o["status"] in ("canceled", "rejected", "expired"):
                raise RuntimeError(f"order {o['status']}")
            time.sleep(0.5)
        raise RuntimeError("order not filled within 30s")

    def enter(self, sym: str, qty: int, side: str) -> tuple[float, str]:
        o = self._req("POST", "/v2/orders", json=dict(symbol=sym, qty=str(qty), side=side,
                                                       type="market", time_in_force="day"))
        return self._wait_fill(o["id"]), o["id"]

    def place_target(self, sym: str, qty: int, side: str, price: float) -> str:
        o = self._req("POST", "/v2/orders", json=dict(symbol=sym, qty=str(qty), side=side, type="limit",
                                                       limit_price=f"{price:.2f}", time_in_force="gtc"))
        return o["id"]

    def target_fill(self, oid: str) -> float | None:
        o = self._req("GET", f"/v2/orders/{oid}")
        return float(o["filled_avg_price"]) if o["status"] == "filled" else None

    def exit(self, sym: str, qty: int, side: str, tp_order: str | None) -> float:
        if tp_order:
            try:
                self._req("DELETE", f"/v2/orders/{tp_order}")
            except RuntimeError as e:
                log.warning("cancel target order failed: %s", e)
            time.sleep(1)
        o = self._req("DELETE", f"/v2/positions/{sym}")   # closes whatever is left, long or short
        return self._wait_fill(o["id"])


class Simulator:
    """Fills at the latest price. Used in demo mode or when no Alpaca keys are set."""
    name = "Simulator"

    def is_open(self) -> bool:
        now = pd.Timestamp.now(tz=engine.NY)
        mins = now.hour * 60 + now.minute
        return now.weekday() < 5 and engine.OPEN_MIN <= mins < engine.CLOSE_MIN

    def account(self) -> dict | None:
        return None

    def enter(self, sym, qty, side):
        return data.latest_price(sym), "sim-entry"

    def place_target(self, sym, qty, side, price):
        return f"sim|{sym}|{side}|{price}"

    def target_fill(self, oid):
        _, sym, side, price = oid.split("|")
        price, last = float(price), data.latest_price(sym)
        hit = last >= price if side == "sell" else last <= price
        return price if hit else None

    def exit(self, sym, qty, side, tp_order):
        return data.latest_price(sym)


def broker():
    if data.provider() == "demo" or not os.getenv("ALPACA_API_KEY") or os.getenv("PAPER_BROKER") == "sim":
        return Simulator()
    return AlpacaPaper()


# ---------------------------------------------------------------- trading logic
def _money(x):
    return f"${x:,.2f}"


def _close(t: dict, price: float, reason: str, held: int | None = None) -> str:
    d = t["direction"]
    pnl = d * (price - t["entry_price"]) * t["qty"]
    pct = d * (price / t["entry_price"] - 1) * 100
    db.update_trade(t["id"], status="closed", closed_at=pd.Timestamp.now(tz="UTC").isoformat(),
                    exit_price=price, exit_reason=reason, pnl=round(pnl, 2), pnl_pct=round(pct, 2),
                    candles_held=held)
    icon = {"follow-through": "✅", "snap-back": "↩️", "time limit": "⏸"}.get(reason, "•")
    return (f"🧪 Paper trade closed {icon} {t['symbol']} {reason}\n"
            f"{'Long' if d == 1 else 'Short'} {t['qty']} @ {_money(t['entry_price'])} → {_money(price)}\n"
            f"P&L {'+' if pnl >= 0 else '-'}{_money(abs(pnl))} ({pct:+.2f}%)")


def _candles_held(t: dict) -> int:
    """4H candles since entry, counting the one about to close."""
    c4 = scanner.load_4h(t["symbol"], 30)
    return int((c4.index > pd.Timestamp(t["entry_candle"])).sum()) + 1


def _check_exit(b, t: dict) -> str | None:
    if t["tp_order"]:
        p = b.target_fill(t["tp_order"])
        if p is not None:
            return _close(t, p, "follow-through")
    price = data.latest_price(t["symbol"])
    side = "sell" if t["direction"] == 1 else "buy"
    if (price - t["edge"]) * t["direction"] <= 0:
        return _close(t, b.exit(t["symbol"], t["qty"], side, t["tp_order"]), "snap-back", _candles_held(t))
    held = _candles_held(t)
    if held >= scanner.SETTINGS.horizon:
        return _close(t, b.exit(t["symbol"], t["qty"], side, t["tp_order"]), "time limit", held)
    return None


def _try_entry(b, sym: str, candle_start: pd.Timestamp) -> str | None:
    c4 = scanner.load_4h(sym, scanner.SCAN_DAYS)
    res = engine.analyze(c4, scanner.SETTINGS)
    price = data.latest_price(sym)
    sig = engine.preclose_check(res, price, scanner.SETTINGS)
    if not sig or sig["reached"]:
        return None
    d = sig["direction"]
    if d == -1 and not allow_short():
        return None
    hub = res["hubs"][-1]
    hub_key = f"{sym}|{c4.index[hub['start_i']].isoformat()}"
    if db.hub_traded(hub_key):          # one trade per hub, like the backtest
        return None
    qty = math.floor(trade_usd() / price)
    if qty < 1:
        return None
    fill, oid = b.enter(sym, qty, "buy" if d == 1 else "sell")
    tp = b.place_target(sym, qty, "sell" if d == 1 else "buy", sig["target"])
    kind = "shelf" if sig["shelf"] else "atr"
    db.add_trade(symbol=sym, direction=d, qty=qty, hub_key=hub_key, hub_low=sig["low"], hub_high=sig["high"],
                 edge=sig["edge"], target=round(sig["target"], 2), target_kind=kind,
                 entry_candle=candle_start.isoformat(), entry_price=fill, entry_order=oid, tp_order=tp)
    return (f"🧪 Paper trade opened: {'LONG' if d == 1 else 'SHORT'} {qty} {sym} @ {_money(fill)}\n"
            f"Hub {_money(sig['low'])}–{_money(sig['high'])} · "
            f"{'next shelf' if kind == 'shelf' else 'ATR target'} {_money(sig['target'])} "
            f"(potential {d * (sig['target'] / fill - 1) * 100:+.2f}%), limit order placed\n"
            f"Closes if price is back {'≤' if d == 1 else '≥'} {_money(sig['edge'])} near a 4H close, "
            f"or after {scanner.SETTINGS.horizon} candles")


def run_check(force: bool = False) -> list[str]:
    """Runs ~2 minutes before each 4H close: exits first, then new entries."""
    if not enabled() and not force:
        return []
    b = broker()
    try:
        if not force and not b.is_open():
            log.info("market closed, paper check skipped")
            return []
    except Exception as e:
        log.warning("clock check failed: %s", e)
        return []
    msgs = []
    with _lock:
        start = engine.current_candle_start(pd.Timestamp.now(tz="UTC"))
        for t in db.open_trades():
            try:
                m = _check_exit(b, t)
                if m:
                    msgs.append(m)
            except Exception as e:
                log.warning("exit check failed for %s: %s", t["symbol"], e)
                msgs.append(f"🧪 {t['symbol']}: paper exit check failed ({e})")
        busy = {t["symbol"] for t in db.open_trades()}
        for row in db.list_tickers():
            if row["symbol"] in busy:
                continue
            try:
                m = _try_entry(b, row["symbol"], start)
                if m:
                    msgs.append(m)
            except Exception as e:
                log.warning("entry failed for %s: %s", row["symbol"], e)
                msgs.append(f"🧪 {row['symbol']}: paper entry failed ({e})")
    for m in msgs:
        telegram_bot.send(m)
    return msgs


def sync() -> list[str]:
    """Every few minutes in market hours: record target (limit) fills."""
    if not db.open_trades():
        return []
    b = broker()
    msgs = []
    with _lock:
        for t in db.open_trades():
            if not t["tp_order"]:
                continue
            try:
                p = b.target_fill(t["tp_order"])
                if p is not None:
                    msgs.append(_close(t, p, "follow-through", _candles_held(t)))
            except Exception as e:
                log.warning("sync failed for %s: %s", t["symbol"], e)
    for m in msgs:
        telegram_bot.send(m)
    return msgs


def summary(with_prices: bool = True) -> dict:
    trades = db.all_trades(500)
    closed = [t for t in trades if t["status"] == "closed"]
    opened = [t for t in trades if t["status"] == "open"]
    if with_prices:
        for t in opened:
            try:
                p = data.latest_price(t["symbol"])
                t["last_price"] = p
                t["unreal_pct"] = round(t["direction"] * (p / t["entry_price"] - 1) * 100, 2)
                t["unreal"] = round(t["direction"] * (p - t["entry_price"]) * t["qty"], 2)
            except Exception:
                pass
    wins = [t for t in closed if (t["pnl"] or 0) > 0]
    b = broker()
    acct = None
    try:
        acct = b.account()
    except Exception as e:
        log.warning("account fetch failed: %s", e)
    return dict(
        enabled=enabled(), broker=b.name, trade_usd=trade_usd(), allow_short=allow_short(), account=acct,
        stats=dict(closed=len(closed), wins=len(wins),
                   win_rate=round(100 * len(wins) / len(closed), 1) if closed else None,
                   total_pnl=round(sum(t["pnl"] or 0 for t in closed), 2),
                   avg_pct=round(sum(t["pnl_pct"] or 0 for t in closed) / len(closed), 2) if closed else None),
        open=opened, closed=closed[:50])


def summary_text() -> str:
    s = summary()
    st = s["stats"]
    lines = [f"🧪 Paper trading is {'ON' if s['enabled'] else 'OFF'} ({s['broker']}, {_money(s['trade_usd'])} per trade)"]
    if s["account"]:
        lines.append(f"Paper account equity {_money(s['account']['equity'])}")
    if st["closed"]:
        lines.append(f"Closed: {st['closed']} trades, {st['win_rate']}% winners, "
                     f"total {'+' if st['total_pnl'] >= 0 else '-'}{_money(abs(st['total_pnl']))}, "
                     f"avg {st['avg_pct']:+.2f}% per trade")
    else:
        lines.append("No closed trades yet.")
    for t in s["open"]:
        u = f", now {t['unreal_pct']:+.2f}%" if "unreal_pct" in t else ""
        lines.append(f"Open: {'LONG' if t['direction'] == 1 else 'SHORT'} {t['qty']} {t['symbol']} "
                     f"@ {_money(t['entry_price'])} → {_money(t['target'])}{u}")
    return "\n".join(lines)
