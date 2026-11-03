"""Scanner: pulls data, finds hubs, sends new alerts once."""
from __future__ import annotations

import json
import logging
import os
import re
import threading

import pandas as pd

import data
import earnings
import db
import engine
import options
import patterns
import notify
import phone

log = logging.getLogger("scanner")
SETTINGS = engine.Settings.from_env()
SCAN_DAYS = int(os.getenv("SCAN_DAYS", 260))      # same history everywhere, so every screen finds the same shelves
BACKTEST_DAYS = int(os.getenv("BACKTEST_DAYS", 720))
SYMBOL_RE = re.compile(r"^[A-Z][A-Z.\-]{0,9}$")
_scan_lock = threading.Lock()


def clean_symbol(s: str) -> str:
    s = (s or "").strip().upper().lstrip("$")
    if not SYMBOL_RE.match(s):
        raise ValueError(f"'{s}' doesn't look like a ticker")
    return s


def load_4h(symbol: str, days: int) -> pd.DataFrame:
    bars = data.get_bars(symbol, days)
    return engine.to_4h(bars, now=pd.Timestamp.now(tz="UTC"))


def _money(x: float) -> str:
    return f"${x:,.2f}"


def candle_close(ts: pd.Timestamp) -> str:
    """4H candles are labelled by their start; alerts show when they closed."""
    return engine.candle_end(ts).strftime("%a %b %d, %-I:%M %p ET")


def _pct(a: float, b: float) -> str:
    return f"{(b / a - 1) * 100:+.2f}%"


def history_line(sym: str) -> str:
    """This ticker's past breakout results (about 2 years), cached for a day."""
    key = f"bt:{sym}"
    today = pd.Timestamp.now(tz=engine.NY).date().isoformat()
    try:
        cached = json.loads(db.get_setting(key) or "{}")
        if cached.get("date") != today:
            res = engine.analyze(load_4h(sym, BACKTEST_DAYS), SETTINGS)
            cached = dict(date=today, stats=engine.stats(res["trades"]))
            db.set_setting(key, json.dumps(cached))
        st = cached["stats"]
    except Exception as e:
        log.warning("history for %s failed: %s", sym, e)
        return ""
    if not st.get("trades"):
        return "History: no completed breakouts yet"
    return (f"History ({st['trades']} trades, ~2y): {st['win_rate']}% winners · "
            f"avg win {st['avg_win_pct'] or 0:+.2f}% · avg loss {st['avg_loss_pct'] or 0:+.2f}% · "
            f"{st['expectancy_pct']:+.2f}% per trade")


def shelf_line(target: float, shelf: dict | None, d: int, entry: float) -> str:
    if shelf:
        return (f"• Next shelf {_money(shelf['low'])}–{_money(shelf['high'])} "
                f"→ target {_money(target)} ({_pct(entry, target)})")
    return (f"• No shelf {'above' if d == 1 else 'below'} in range, "
            f"target {SETTINGS.target_atr:g}×ATR {_money(target)} ({_pct(entry, target)})")


def plan_lines(entry: float, edge: float, target: float, d: int, reached: bool, shelf: dict | None = None) -> str:
    if reached:
        where = "the next shelf" if shelf else "the target"
        return (f"⚠️ Price is already at {where} ({_money(target)}). The follow-through may be done, "
                "so entering here is a chase.")
    return (f"Plan (not advice):\n"
            f"• Entry ≈ {_money(entry)}\n"
            f"• Exit on a 4H close back {'≤' if d == 1 else '≥'} {_money(edge)} (the loss is wherever that close lands)\n"
            + shelf_line(target, shelf, d, entry))


def format_preclose(sym: str, sig: dict, minutes_left: int) -> str:
    up = sig["direction"] == 1
    side = "ABOVE" if up else "BELOW"
    zone = f"{_money(sig['low'])}–{_money(sig['high'])}"
    hist = history_line(sym)
    return (f"⚠️ {sym}: trading {side} hub, ~{minutes_left} min before the 4H close\n"
            f"Now {_money(sig['price'])} vs hub {zone} ({sig['sessions']} sessions)\n"
            f"If the candle closes {'above' if up else 'below'} {_money(sig['edge'])}, that's the breakout.\n"
            + plan_lines(sig["price"], sig["edge"], sig["target"], sig["direction"], sig["reached"], sig["shelf"])
            + (f"\n{hist}" if hist else ""))


def _say_money(x: float) -> str:
    return f"${x:,.2f}"


def speak_preclose(sym: str, sig: dict, minutes_left: int) -> str:
    up = sig["direction"] == 1
    tgt = (f"Next shelf {_say_money(sig['target'])}." if sig["shelf"]
           else f"No shelf in range. Target {_say_money(sig['target'])}.")
    if sig["reached"]:
        tgt = "Price is already at the target, so this would be a chase."
    return (f"Next Shelf. {phone.spell(sym)} is trading {'above' if up else 'below'} its hub, "
            f"about {minutes_left} minutes before the 4 hour close. "
            f"Price {_say_money(sig['price'])}. Hub edge {_say_money(sig['edge'])}. {tgt}")


def speak_event(sym: str, e: dict) -> str:
    if e["type"] in ("breakout", "breakdown"):
        up = e["type"] == "breakout"
        d = 1 if up else -1
        tgt = (f"Next shelf {_say_money(e['target'])}." if e.get("shelf")
               else f"No shelf in range. Target {_say_money(e['target'])}.")
        if (e["close"] - e["target"]) * d >= 0:
            tgt = "It already closed at the target, so this would be a chase."
        return (f"Next Shelf. {phone.spell(sym)} closed {'above' if up else 'below'} its hub. "
                f"4 hour close {_say_money(e['close'])}. {tgt}")
    if e["type"] == "follow-through":
        return f"Next Shelf. {phone.spell(sym)} reached the next shelf at {_say_money(e['target'])}. Follow-through."
    if e["type"] == "snap-back":
        return f"Next Shelf. {phone.spell(sym)} closed back inside its hub. Snap-back."
    return f"Next Shelf. {phone.spell(sym)}: {e['type'].replace('_', ' ')}."


def format_event(sym: str, e: dict) -> str:
    t = candle_close(e["time"])
    zone = f"{_money(e['low'])}–{_money(e['high'])}"
    if e["type"] == "hub_formed":
        return (f"🟦 {sym}: new hub\n4H closes held {zone} for {e['sessions']} sessions.\n"
                f"Watching for a 4H close outside the zone.\nCandle closed: {t}")
    if e["type"] in ("breakout", "breakdown"):
        up = e["type"] == "breakout"
        edge = e["high"] if up else e["low"]
        d = 1 if up else -1
        hist = history_line(sym)
        return (f"{'🚀' if up else '🔻'} {sym}: 4H close {'ABOVE' if up else 'BELOW'} hub\n"
                f"Close {_money(e['close'])} vs hub {zone} ({e['sessions']} sessions)\n"
                + plan_lines(e["close"], edge, e["target"], d, (e["close"] - e["target"]) * d >= 0, e.get("shelf"))
                + (f"\n{hist}" if hist else "") + f"\nCandle closed: {t}")
    if e["type"] == "follow-through":
        what = "the next shelf" if e.get("shelf") else "the target"
        return (f"✅ {sym}: follow-through\n4H close {_money(e['close'])} reached {what} {_money(e['target'])}, "
                + ("on the breakout candle itself" if e["candles"] == 0 else
                   f"{e['candles']} candle{'s' if e['candles'] > 1 else ''} after the breakout")
                + f"\nCandle closed: {t}")
    if e["type"] == "snap-back":
        return (f"↩️ {sym}: snap-back\n4H close {_money(e['close'])} is back inside the hub {zone}\nCandle closed: {t}")
    if e["type"] == "stalled":
        return (f"⏸ {sym}: stalled\nDidn't reach the {'next shelf' if e.get('shelf') else 'target'} or close back inside within {SETTINGS.horizon} candles "
                f"(last close {_money(e['close'])})\nCandle closed: {t}")
    return f"{sym}: {e['type']}"


def describe(sym: str, st: dict) -> str:
    if st.get("error"):
        return f"{sym}: error: {st['error']}"
    line = f"{sym} {_money(st['last_close'])}: {st['label']}"
    if st.get("hub"):
        h = st["hub"]
        line += f"\n   {'last hub' if st['state'] == 'none' else 'hub'} {_money(h['low'])}–{_money(h['high'])} ({h['sessions']} sessions)"
    if st.get("target") and st["state"] in ("breakout_up", "breakdown"):
        line += (f", next shelf {_money(st['target'])}" if st.get("target_kind") == "shelf"
                 else f", target {_money(st['target'])} (no shelf)")
    elif st.get("state") in ("forming", "watching"):
        up, dn = st.get("shelf_up"), st.get("shelf_down")
        line += (f"\n   shelves: ▲ {_money(up['level']) if up else 'none'} · "
                 f"▼ {_money(dn['level']) if dn else 'none'}")
    return line


def scan_symbol(sym: str) -> tuple[dict, list[str]]:
    """Scan one ticker. The first scan only sets a baseline; later scans alert on new events."""
    c4 = load_4h(sym, SCAN_DAYS)
    if len(c4) < SETTINGS.window + 15:
        raise ValueError("not enough price history (check the ticker)")
    res = engine.analyze(c4, SETTINGS)
    status = engine.current_status(c4, res, SETTINGS)
    row = db.get_ticker(sym)
    baseline = row["last_candle"] if row else None
    sent = []
    if baseline:
        b = pd.Timestamp(baseline)
        for e in res["events"]:
            if e["time"] > b:
                msg = format_event(sym, e)
                if e["type"] in ("breakout", "breakdown", "hub_formed"):
                    msg += earnings.alert_line(sym)
                if db.add_alert(f"{sym}|{e['type']}|{e['time'].isoformat()}", sym, e["type"],
                                e["time"].isoformat(), msg):
                    # ring only for the newest candle (no burst of calls after downtime), and not for chases
                    chase = e["type"] in ("breakout", "breakdown") and \
                        (e["close"] - e["target"]) * (1 if e["type"] == "breakout" else -1) >= 0
                    newest = e["time"] == c4.index[-1]
                    speech = speak_event(sym, e) if newest and not chase else None
                    extra = None
                    if newest and not chase and e["type"] in ("breakout", "breakdown"):
                        d_ = 1 if e["type"] == "breakout" else -1
                        edge_ = e["high"] if d_ == 1 else e["low"]
                        extra = (lambda s_=sym, d_=d_, c_=e["close"], ed_=edge_, t_=e["target"]: options.alert_line(s_, d_, c_, ed_, t_))
                    notify.symbol_alert(sym, e["type"], msg, speech, pro_extra=extra)
                    sent.append(msg)
    try:      # other setups (triangles, channels, support & resistance): alert on new breaks only
        pa = patterns.analyze_all(c4, res, SETTINGS)
        status["setups"] = patterns.live_json(sym, c4, res, pa)
        if baseline:
            b = pd.Timestamp(baseline)
            for e in patterns.events(sym, res, pa):
                if e["time"] <= b or e["time"] != c4.index[-1]:
                    continue                 # only the newest candle: no burst of old breaks after downtime
                msg = format_pattern_event(sym, e)
                if e["type"] in ("breakout", "breakdown"):
                    msg += earnings.alert_line(sym)
                if db.add_alert(f"{sym}|{e['kind']}|{e['type']}|{e['time'].isoformat()}", sym, e["type"], e["time"].isoformat(), msg):
                    notify.symbol_alert(sym, e["type"], msg, None, style=patterns.STYLE_OF[e["kind"]])
                    sent.append(msg)
    except Exception as ex:
        log.warning("pattern scan failed for %s: %s", sym, ex)
    db.update_ticker(sym, c4.index[-1].isoformat(), json.dumps(status))
    return status, sent


def format_pattern_event(sym: str, e: dict) -> str:
    up = e["type"] == "breakout"
    d = 1 if up else -1
    name = patterns.KINDS[e["kind"]].lower()
    if e["kind"] == "sr":
        what = f"{'resistance' if up else 'support'} at {_money(e['edge'])}" + (f" (tested {e['touches']}x)" if e.get("touches") else "")
    else:
        what = f"the {name} {'upper' if up else 'lower'} line at {_money(e['edge'])}"
    reached = (e["close"] - e["target"]) * d >= 0
    return (f"{'🚀' if up else '🔻'} {sym}: 4H close {'ABOVE' if up else 'BELOW'} {what}\n"
            f"Setup: {patterns.KINDS[e['kind']]}\n"
            + plan_lines(e["close"], e["edge"], e["target"], d, reached, e.get("shelf"))
            + f"\nCandle closed: {candle_close(e['time'])}")


def scan_all() -> list[str]:
    with _scan_lock:
        sent = []
        for row in db.list_tickers():
            sym = row["symbol"]
            try:
                _, s = scan_symbol(sym)
                sent += s
            except Exception as e:
                log.warning("scan failed for %s: %s", sym, e)
                db.update_ticker(sym, row["last_candle"], json.dumps({"error": str(e)[:200]}))
        log.info("scan done, %d alerts", len(sent))
        return sent


def preclose_all() -> list[str]:
    """Run shortly before each 4H close: warn if price is outside a hub that hasn't broken out yet."""
    now = pd.Timestamp.now(tz="UTC")
    start = engine.current_candle_start(now)
    end = engine.candle_end(start)
    minutes_left = max(0, int((end - now).total_seconds() // 60))
    sent = []
    with _scan_lock:
        for row in db.list_tickers():
            sym = row["symbol"]
            try:
                c4 = load_4h(sym, SCAN_DAYS)
                res = engine.analyze(c4, SETTINGS)
                sig = engine.preclose_check(res, data.latest_price(sym), SETTINGS)
                if not sig:
                    continue
                msg = format_preclose(sym, sig, minutes_left) + earnings.alert_line(sym)
                if db.add_alert(f"{sym}|preclose|{start.isoformat()}", sym, "preclose", start.isoformat(), msg):
                    notify.symbol_alert(sym, "preclose", msg,
                                        None if sig["reached"] else speak_preclose(sym, sig, minutes_left))
                    sent.append(msg)
            except Exception as e:
                log.warning("pre-close check failed for %s: %s", sym, e)
    log.info("pre-close check done, %d alerts", len(sent))
    return sent


def _fresh_board_status(sym: str) -> dict | None:
    """The board's status for sym if it was scanned within the last 5 hours (one 4H candle)."""
    r = db._run("SELECT status, updated_at FROM board WHERE symbol=?", (sym,)).fetchone()
    if not r or not r["status"]:
        return None
    try:
        age = pd.Timestamp.now(tz="UTC") - pd.Timestamp(r["updated_at"]).tz_localize("UTC")
        st = json.loads(r["status"])
    except Exception:
        return None
    return st if age < pd.Timedelta(hours=5) and st.get("last_candle") and st.get("state") else None


def add_symbol(raw: str, uid: int | None = None) -> dict:
    """Add to the shared scan set (and to a user's watchlist when uid is given)."""
    sym = clean_symbol(raw)
    if uid is not None and sym in db.user_symbols(uid):
        row = db.get_ticker(sym)
        return dict(symbol=sym, status=json.loads(row["status"] or "{}") if row else {}, created=False)
    created = db.add_ticker(sym)
    fresh = _fresh_board_status(sym) if created else None
    if fresh:     # the market board scanned this stock after the latest 4H close: reuse it (instant add)
        status = fresh
        db.update_ticker(sym, fresh["last_candle"], json.dumps(fresh))
    elif created or not (db.get_ticker(sym) or {}).get("last_candle"):
        try:
            status, _ = scan_symbol(sym)
        except Exception:
            if created:
                db.remove_ticker(sym)
            raise
    else:
        status = json.loads(db.get_ticker(sym)["status"] or "{}")
    if uid is not None:
        db.watch(uid, sym)
    return dict(symbol=sym, status=status, created=True)


def backtest(raw: str) -> dict:
    sym = clean_symbol(raw)
    c4 = load_4h(sym, BACKTEST_DAYS)
    res = engine.analyze(c4, SETTINGS)
    trades = [dict(time=res["close"].index[t["i0"]].isoformat(),
                   direction="up" if t["direction"] == 1 else "down",
                   hub_low=round(t["hub"]["low"], 2), hub_high=round(t["hub"]["high"], 2),
                   close=round(float(res["close"].iloc[t["i0"]]), 2), target=round(t["target"], 2),
                   target_kind="shelf" if t["shelf"] else "atr",
                   outcome=t["outcome"], fwd_pct=None if t["fwd"] is None else round(t["fwd"], 2),
                   ret_pct=None if t["ret"] is None else round(t["ret"], 2), chase=t["chase"])
              for t in res["trades"]]
    return dict(symbol=sym, period=f"{c4.index[0].date()} to {c4.index[-1].date()}" if len(c4) else "",
                stats=engine.stats(res["trades"]), trades=trades[::-1])


HELP = (
    "NextShelf commands:\n"
    "/add NVDA TSLA: watch tickers\n"
    "/remove NVDA: stop watching\n"
    "/list: watchlist with current status\n"
    "/status NVDA: one ticker\n"
    "/scan: scan everything now\n"
    "/precheck: run the pre-close check now\n"
    "/backtest NVDA: breakout stats (~2 years)\n\n"
    "Pre-close warnings come ~15 min before each 4H close; scans run after it. Not financial advice."
)


def handle_command(cmd: str, args: list[str]) -> str:
    if cmd in ("/start", "/help"):
        return HELP
    if cmd == "/add":
        if not args:
            return "Usage: /add NVDA TSLA"
        out = []
        for a in args:
            try:
                r = add_symbol(a)
                out.append(("Watching " if r["created"] else "Already watching ") + describe(r["symbol"], r["status"]))
            except Exception as e:
                out.append(f"{a.upper()}: couldn't add ({e})")
        return "\n".join(out)
    if cmd == "/remove":
        return "\n".join(f"{a.upper()}: {'removed' if db.remove_ticker(a.upper()) else 'not on the list'}"
                         for a in args) or "Usage: /remove NVDA"
    if cmd == "/list":
        rows = db.list_tickers()
        if not rows:
            return "Watchlist is empty. Add one with /add NVDA"
        return "\n".join(describe(r["symbol"], json.loads(r["status"] or "{}") or {"error": "not scanned"})
                         for r in rows)
    if cmd == "/status":
        if not args:
            return "Usage: /status NVDA"
        row = db.get_ticker(args[0].upper())
        if not row:
            return f"{args[0].upper()} isn't on your watchlist"
        return describe(row["symbol"], json.loads(row["status"] or "{}"))
    if cmd == "/scan":
        sent = scan_all()
        return f"Scan done. {len(sent)} new alert(s)." if sent else "Scan done. Nothing new."
    if cmd == "/precheck":
        sent = preclose_all()
        return f"{len(sent)} pre-close warning(s)." if sent else "Nothing trading outside a hub right now."
    if cmd == "/backtest":
        if not args:
            return "Usage: /backtest NVDA"
        r = backtest(args[0])
        s = r["stats"]
        if not s["n"]:
            return f"{r['symbol']}: no completed breakouts in {r['period']}"
        trade = "No tradeable breakouts (all were already past target)" if not s["trades"] else (
            f"If entered at each breakout close: {s['trades']} trades, {s['win_rate']}% winners\n"
            f"Avg win {s['avg_win_pct'] or 0:+.2f}% · avg loss {s['avg_loss_pct'] or 0:+.2f}% · "
            f"expectancy {s['expectancy_pct']:+.2f}% per trade")
        return (f"{r['symbol']} backtest, {r['period']}\nBreakouts: {s['n']}\n"
                f"Follow-through {s['follow-through']}% · Snap-back {s['snap-back']}% · Stalled {s['stalled']}%\n"
                f"{trade}\n"
                f"Up: {s['up']['n']} ({s['up']['follow_through']}% FT) · "
                f"Down: {s['down']['n']} ({s['down']['follow_through']}% FT)")
    return "Unknown command. /help for the list."
