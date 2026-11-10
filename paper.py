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
import earnings
import engine
import gamma
import notify
import scanner

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
            msg = r.text[:200]
            try:
                msg = r.json().get("message") or msg
            except Exception:
                pass
            if r.status_code in (401, 403) and "forbidden" in msg.lower():
                msg += " (check that ALPACA_API_KEY / ALPACA_SECRET_KEY are PAPER keys)"
            raise RuntimeError(f"Alpaca {r.status_code}: {msg}")
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
                raise RuntimeError(f"Alpaca {o['status']} the order")
            time.sleep(0.5)
        try:                       # don't leave an order sitting there to fill later behind our back
            self._req("DELETE", f"/v2/orders/{oid}")
        except RuntimeError:
            pass
        raise RuntimeError("order didn't fill within 30 seconds, so it was cancelled")

    def enter(self, sym: str, qty: int, side: str) -> tuple[float, str]:
        o = self._req("POST", "/v2/orders", json=dict(symbol=sym, qty=str(qty), side=side,
                                                       type="market", time_in_force="day"))
        return self._wait_fill(o["id"]), o["id"]

    def shortable(self, sym: str) -> bool:
        try:
            a = self._req("GET", f"/v2/assets/{sym}")
            return bool(a.get("shortable") and a.get("easy_to_borrow"))
        except RuntimeError:
            return False

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
    sig = engine.preclose_check(res, price, scanner.SETTINGS, scanner.forming_volume(sym))
    if not sig or sig["reached"]:
        return None
    if sig.get("vol_ok") is False:
        log.info("%s: paper entry skipped (no volume on the break)", sym)
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
    if not b.is_open():                 # the scan can take a while; never send an order after the bell
        return None
    if d == -1 and hasattr(b, "shortable") and not b.shortable(sym):
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


# ---------------------------------------------------------------- one trade a day, from the whole board, after the close
DAILY_MAX = int(os.getenv("PAPER_DAILY_MAX", 1))     # new paper trades per day
MAX_OPEN = int(os.getenv("PAPER_MAX_OPEN", 10))      # open paper trades at once
GOAL = int(os.getenv("PAPER_GOAL", 50))              # closed trades before judging the strategy


def source() -> str:
    """'board' (default): one confirmed breakout a day from the whole board. 'watchlist': the old pre-close entries."""
    return os.getenv("PAPER_SOURCE", "board").strip().lower()


def _today_ny() -> str:
    return pd.Timestamp.now(tz=engine.NY).strftime("%Y-%m-%d")


def entries_today() -> int:
    n = 0
    for t in db.all_trades(50):
        try:
            if pd.Timestamp(t["opened_at"]).tz_localize("UTC").tz_convert(engine.NY).strftime("%Y-%m-%d") == _today_ny():
                n += 1
        except Exception:
            pass
    return n


def _fresh(row: dict, window: str) -> bool:
    """Did this breakout confirm on the candle we're entering after?
    midday: today's 9:30-1:30 candle.  morning: the previous session's 1:30-4:00 candle."""
    import re as _re
    m = _re.search(r"(\d+)/\d+", row.get("label") or "")
    if not m or int(m.group(1)) != 0 or not row.get("last_candle"):
        return False
    t = pd.Timestamp(row["last_candle"]).tz_convert(engine.NY)
    today = pd.Timestamp.now(tz=engine.NY).normalize()
    if window == "midday":
        return t.normalize() == today and t.hour == 9
    return t.normalize() < today and t.hour == 13 and (today - t.normalize()).days <= 4


def candidates(window: str, why: dict | None = None) -> list[dict]:
    """Fresh hub breakouts, still in the first half of the move, ranked by reward : risk at today's price.
    Pass a dict as `why` to get the filter counts back (for the daily no-trade note)."""
    from board import board as _board
    why = why if why is not None else {}
    live_rows = _board()["live"]
    rows = [r for r in live_rows if _fresh(r, window) and r.get("hub") and r.get("target")]
    why.update(breakouts=len(live_rows), fresh=len(rows), shorts_off=0, past_half=0, back_inside=0, earnings=0, wall=0, illiquid=0, smallcap=0, regime=0, ledge=0, kept=0)
    if not rows:
        return []
    try:
        live = data.latest_prices([r["symbol"] for r in rows])
    except Exception:
        live = {}
    out = []
    for r in rows:
        d = 1 if r["state"] == "breakout_up" else -1
        if d == -1 and not allow_short():
            why["shorts_off"] += 1
            continue
        if not scanner.regime_allows(d):
            why["regime"] += 1
            continue       # fights the market regime: no paper entry
        if scanner.WAREHOUSE_ONLY and (r.get("hub") or {}).get("grade") == "ledge":
            why["ledge"] += 1
            continue       # thin hub: warehouse-only mode
        import board as _bmod
        q = _bmod.quality_skip(r["symbol"], r.get("dv20"), r.get("mc"))
        if q:                  # a watchlist name (exempt from the scan skip) is still never paper-traded
            why[q] += 1
            continue
        edge = r["hub"]["high"] if d == 1 else r["hub"]["low"]
        tgt = r["target"]
        px = (live.get(r["symbol"]) or {}).get("price") or r.get("last_close")
        if not px or (tgt - edge) * d <= 0:
            continue
        half = edge + (tgt - edge) / 2
        if (px - edge) * d <= 0 or (px - half) * d > 0:     # back inside, or past the first half: skip, don't chase
            why["back_inside" if (px - edge) * d <= 0 else "past_half"] += 1
            continue
        try:
            e = earnings.lookup(r["symbol"])
            if e and e.get("days") is not None and e["days"] <= 7:
                why["earnings"] += 1
                continue                                        # don't hold through an earnings report
        except Exception:
            pass
        rr = abs(tgt - px) / max(abs(px - edge), 1e-9)
        c = dict(symbol=r["symbol"], d=d, edge=edge, target=tgt, price=px, rr=rr, low=r["hub"]["low"], high=r["hub"]["high"],
                 kind=r.get("target_kind") or "shelf", hub_start=r["hub"].get("start"), candle=r["last_candle"])
        gamma.attach(c)                       # a gamma wall between the edge and the shelf caps the target at the wall
        if c["gamma_capped"] and (c["target"] - px) * d <= 0:
            why["wall"] += 1
            continue                          # price is already at/through the wall: the capped move is done, don't chase
        if c["gamma_capped"]:
            c["rr"] = abs(c["target"] - px) / max(abs(px - edge), 1e-9)
        out.append(c)
    why["kept"] = len(out)
    # best reward : risk first (capped, so a price sitting right on the edge doesn't win on a tiny risk alone), then the bigger move
    return sorted(out, key=lambda c: (-min(c["rr"], 4.0), -abs(c["target"] / c["price"] - 1)))


def _why_text(w: dict) -> str:
    if not w:
        return ""
    if not w.get("fresh"):
        return (f"none of the {w.get('breakouts', 0)} live breakouts confirmed on this close" if w.get("breakouts")
                else "no live breakouts on the board")
    parts = [f"{w['fresh']} fresh breakout{'s' if w['fresh'] != 1 else ''}"]
    for k, label in (("past_half", "already past halfway to the target"), ("back_inside", "back inside the hub"),
                     ("earnings", "earnings within 7 days"), ("wall", "already through the gamma wall"),
                     ("shorts_off", "shorts are off"), ("illiquid", "too thin to trade"),
                     ("smallcap", "too small to trade"), ("regime", "fought the market regime"), ("ledge", "thin hub (ledge)")):
        if w.get(k):
            parts.append(f"{w[k]} {label}")
    if w.get("kept"):
        parts.append(f"{w['kept']} qualified but couldn't be entered (already traded, already held, or not shortable)")
    return " · ".join(parts)


def _record(window: str, traded: bool, reason: str):
    """Keep the latest decision per window (Paper trades page), and tell the admins once a day when nothing fired."""
    import json as _json
    try:
        db.set_setting(f"paper_check_{window}", _json.dumps(dict(at=_today_ny(), traded=traded, reason=reason)))
    except Exception:
        pass
    if traded or window != "midday":
        return
    try:
        m = _json.loads(db.get_setting("paper_check_morning") or "{}")
        morning = m.get("reason") if m.get("at") == _today_ny() and not m.get("traded") else None
        notify.admins("🧪 No paper trade today.\n"
                      + (f"9:35 AM: {morning}\n" if morning else "")
                      + f"3:50 PM: {reason}")
    except Exception as e:
        log.warning("no-trade note failed: %s", e)


def last_checks() -> dict:
    import json as _json
    out = {}
    for w in ("morning", "midday"):
        try:
            v = _json.loads(db.get_setting(f"paper_check_{w}") or "null")
        except Exception:
            v = None
        if v:
            out[w] = v
    return out


def enter_daily(window: str = "midday") -> str | None:
    """The paper trader's one trade a day: the best fresh, confirmed breakout from the whole board."""
    if not enabled() or source() != "board":
        return None      # paper trading off (or watchlist mode): nothing to record
    if window == "midday":                 # the 1:52 PM board sweep confirms the 1:30 close: wait for it
        import time as _t
        from board import SCAN
        for _ in range(60):
            if not SCAN.get("running"):
                break
            _t.sleep(10)
    with _lock:
        if entries_today() >= DAILY_MAX:
            return None      # already traded today
        if len(db.open_trades()) >= MAX_OPEN:
            _record(window, False, f"{MAX_OPEN} trades already open (the cap); no new entries until one closes")
            return None
        b = broker()
        try:
            if not b.is_open():
                _record(window, False, "market closed (holiday or early close)")
                return None
        except Exception as e:
            log.warning("clock check failed: %s", e)
            _record(window, False, f"couldn't reach the paper broker ({str(e)[:80]})")
            return None
        busy = {t["symbol"] for t in db.open_trades()}
        why: dict = {}
        for c in candidates(window, why):
            hub_key = f"{c['symbol']}|{c['hub_start']}"
            if c["symbol"] in busy or db.hub_traded(hub_key):
                continue
            qty = math.floor(trade_usd() / c["price"])
            if qty < 1 or (c["d"] == -1 and hasattr(b, "shortable") and not b.shortable(c["symbol"])):
                continue
            fill, oid = b.enter(c["symbol"], qty, "buy" if c["d"] == 1 else "sell")
            tp = b.place_target(c["symbol"], qty, "sell" if c["d"] == 1 else "buy", c["target"])
            db.add_trade(symbol=c["symbol"], direction=c["d"], qty=qty, hub_key=hub_key, hub_low=c["low"], hub_high=c["high"],
                         edge=c["edge"], target=round(c["target"], 2), target_kind=c["kind"],
                         entry_candle=c["candle"], entry_price=fill, entry_order=oid, tp_order=tp)
            msg = (f"🧪 Paper trade of the day: {'LONG' if c['d'] == 1 else 'SHORT'} {qty} {c['symbol']} @ {_money(fill)}\n"
                   f"Confirmed {'1:30 PM' if window == 'midday' else 'yesterday 4:00 PM'} close outside the hub {_money(c['low'])}–{_money(c['high'])} · "
                   f"{'gamma wall' if c.get('gamma_capped') else 'next shelf' if c['kind'] == 'shelf' else 'ATR target'} {_money(c['target'])} · reward:risk {c['rr']:.1f}\n"
                   + (f"{gamma.line(c)}\n" if gamma.line(c) else "") +
                   f"Exit on a close back {'≤' if c['d'] == 1 else '≥'} {_money(c['edge'])}, or after {scanner.SETTINGS.horizon} candles.")
            notify.admins(msg)
            _record(window, True, f"entered {c['symbol']}")
            return msg
        _record(window, False, _why_text(why))
    return None


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
    msgs, failed = [], []
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
        for row in (db.list_tickers() if source() == "watchlist" else []):   # board mode enters in enter_daily()
            if row["symbol"] in busy:
                continue
            try:
                m = _try_entry(b, row["symbol"], start)
                if m:
                    msgs.append(m)
            except Exception as e:
                log.warning("entry failed for %s: %s", row["symbol"], e)
                failed.append(f"{row['symbol']}: {e}")
    if failed:
        msgs.append("🧪 Paper entries that failed:\n" + "\n".join(failed[:8]) + (f"\n…and {len(failed) - 8} more" if len(failed) > 8 else ""))
    for m in msgs:
        notify.admins(m)
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
        notify.admins(m)
    return msgs


def summary(with_prices: bool = True) -> dict:
    trades = db.all_trades(500)
    closed = [t for t in trades if t["status"] == "closed"]
    opened = [t for t in trades if t["status"] == "open"]
    live = {}
    if with_prices and opened:
        try:
            live = data.latest_prices([t["symbol"] for t in opened])
        except Exception as e:
            log.warning("live prices failed: %s", e)
    if with_prices:
        for t in opened:
            try:
                p = (live.get(t["symbol"]) or {}).get("price") or data.latest_price(t["symbol"])
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
        source=source(), daily_max=DAILY_MAX, max_open=MAX_OPEN, goal=GOAL, last_checks=last_checks(),
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
