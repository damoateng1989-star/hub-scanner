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
import gamma
import news
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


_BARS: dict[tuple, tuple[float, pd.DataFrame]] = {}
_BARS_LOCKS: dict[tuple, threading.Lock] = {}
BARS_TTL = 90      # seconds: the chart, the Shelf card and the options plan share one download per stock


def load_4h(symbol: str, days: int) -> pd.DataFrame:
    import time as _t
    key = (symbol, days)
    with _BARS_LOCKS.setdefault(key, threading.Lock()):     # chart + options asking at once: download once, share it
        hit = _BARS.get(key)
        if hit and _t.time() - hit[0] < BARS_TTL:
            bars = hit[1]
        else:
            bars = data.get_bars(symbol, days)
            _BARS[key] = (_t.time(), bars)
        if len(_BARS) > 300:                       # keep memory small
            for k in sorted(_BARS, key=lambda k: _BARS[k][0])[:100]:
                _BARS.pop(k, None)
    return engine.to_4h(bars, now=pd.Timestamp.now(tz="UTC"))



def forming_volume(sym: str) -> float | None:
    """The still-forming 4H candle's volume, projected to the full candle from the share of it traded so far
    (the 30-min bars run ~15 min behind). None when there's no volume data: fail open."""
    try:
        bars = data.get_bars(sym, 5)
        if bars is None or bars.empty or "Volume" not in bars.columns:
            return None
        now = pd.Timestamp.now(tz="UTC")
        start = engine.current_candle_start(now).tz_convert("UTC")
        part = bars[bars.index >= start]
        if part.empty:
            return None
        length = (engine.MID_MIN - engine.OPEN_MIN) if engine.current_candle_start(now).hour < 13 else (engine.CLOSE_MIN - engine.MID_MIN)
        covered = (part.index[-1] + pd.Timedelta(minutes=30) - start).total_seconds() / 60
        frac = min(max(covered / length, 0.1), 1.0)
        return float(part["Volume"].sum()) / frac
    except Exception:
        return None

def _money(x: float) -> str:
    return f"${x:,.2f}"


def candle_close(ts: pd.Timestamp) -> str:
    """4H candles are labelled by their start; alerts show when they closed."""
    return engine.candle_end(ts).strftime("%a %b %d, %-I:%M %p ET")


def _pct(a: float, b: float) -> str:
    return f"{(b / a - 1) * 100:+.2f}%"


def _bt_trades(sym: str) -> list:
    """This ticker's finished hub trades over ~2 years (direction and return only), cached for a day."""
    key = f"bt2:{sym}"
    today = pd.Timestamp.now(tz=engine.NY).date().isoformat()
    cached = json.loads(db.get_setting(key) or "{}")
    if cached.get("date") != today:
        res = engine.analyze(load_4h(sym, BACKTEST_DAYS), SETTINGS)
        cached = dict(date=today, trades=[dict(direction=t["direction"], outcome=t["outcome"], ret=t["ret"])
                                          for t in res["trades"] if t["outcome"] != "pending"])
        db.set_setting(key, json.dumps(cached))
    return cached["trades"]


def grade_line(sym: str, d: int) -> str:
    """'Setup grade D · Poor: long breakouts on AMZN lost 1.24% per trade on average (38.5% winners, 39 trades, ~2y)'"""
    try:
        g = engine.setup_grade(_bt_trades(sym), d)
    except Exception as e:
        log.warning("grade for %s failed: %s", sym, e)
        return ""
    what = "long breakouts" if d == 1 else "short breakdowns"
    if g["grade"] == "?":
        return f"Setup grade: not enough history ({g['n']} past {what} on {sym})"
    verb = f"made {g['avg']:+.2f}%" if g["avg"] > 0 else f"lost {abs(g['avg']):.2f}%"
    return (f"Setup grade {g['grade']} · {g['word']}: {what} on {sym} {verb} per trade on average "
            f"({g['win']}% winners, {g['n']} trades, ~2y)")


def history_line(sym: str) -> str:
    """Kept for callers that want both directions together."""
    try:
        st = engine.stats([dict(t, fwd=None) for t in _bt_trades(sym)])
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
    side, word = ("above", "ABOVE") if up else ("below", "BELOW")
    zone = f"{_money(sig['low'])}–{_money(sig['high'])}"
    trig = sig.get("trigger", sig["edge"])
    g = grade_line(sym, sig["direction"])
    lines = [f"⚠️ {sym}: not a setup yet · ~{minutes_left} min to the 4H close",
             f"Trading {word} its hub: now {_money(sig['price'])}, hub {zone} ({sig['sessions']} sessions)"]
    if g:
        lines.append(g)
    if sig.get("widens"):
        lines.append(f"A close here would only widen the hub. It needs a 4H close {side} about {_money(trig)} to count.")
    else:
        lines.append(f"It only counts if the 4H candle closes {side} {_money(trig)}.")
    if sig.get("vol_ok") is not None:
        lines.append("Volume: ✓ confirming so far" if sig["vol_ok"] else "Volume: ✗ light so far. It only counts if volume picks up by the close")
    est = max(sig["price"], trig) if up else min(sig["price"], trig)     # the soonest a counting close could be
    if sig["reached"] or (sig["target"] - est) * sig["direction"] <= 0:
        what = "the next shelf" if sig["shelf"] else "the target"
        lines.append(f"⚠️ {what.capitalize()} ({_money(sig['target'])}) is {'already reached' if sig['reached'] else 'inside that move'}, so a close there would be a chase.")
    else:
        lines += [f"If it closes {side} {_money(trig)} (not advice):",
                  f"• Entry ≈ the close (about {_money(est)} at the earliest)" if sig.get("widens") else f"• Entry ≈ the close (about {_money(sig['price'])} now)",
                  f"• Exit on a 4H close back {'≤' if up else '≥'} {_money(sig['edge'])} (the loss is wherever that close lands)",
                  shelf_line(sig["target"], sig["shelf"], sig["direction"], est)]
    return "\n".join(lines)


def _say_money(x: float) -> str:
    return f"${x:,.2f}"


def speak_preclose(sym: str, sig: dict, minutes_left: int) -> str:
    up = sig["direction"] == 1
    tgt = (f"Next shelf {_say_money(sig['target'])}." if sig["shelf"]
           else f"No shelf in range. Target {_say_money(sig['target'])}.")
    if sig["reached"]:
        tgt = "Price is already at the target, so this would be a chase."
    return (f"Next Shelf. {phone.spell(sym)} is trading {'above' if up else 'below'} its hub, "
            f"about {minutes_left} minutes before the 4 hour close. Not a setup yet. "
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
        hist = grade_line(sym, d)
        return (f"{'🚀' if up else '🔻'} {sym} {'broke out' if up else 'broke down'} · heading to {'next shelf' if e.get('shelf') else 'target'}\n"
                f"4H close {'above' if up else 'below'} the hub\n"
                f"Close {_money(e['close'])} vs hub {zone} ({e['sessions']} sessions)\n"
                f"Hub grade: {e.get('grade', 'ledge').capitalize()} ({e.get('grade_score', '?')}/6)\n"
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


WAREHOUSE_ONLY = os.getenv("WAREHOUSE_ONLY", "0").strip().lower() in ("1", "true", "yes", "on")


def regime_line(direction: int) -> str:
    """One line for an alert when the breakout fights the market regime.
    Empty when the breakout runs with the trend or no regime is stored yet.
    The regime stays a label, never a gate (Patch 68)."""
    try:
        rg = json.loads(db.get_setting("regime") or "null")
    except Exception:
        return ""
    if not rg or rg.get("regime") not in ("up", "down"):
        return ""
    if (rg["regime"] == "up") == (direction == 1):
        return ""
    nums = f" ({_money(rg['close'])} vs {_money(rg['sma'])})" if rg.get("close") and rg.get("sma") else ""
    return (f"\n⚠️ Against the tape — {rg.get('index', 'SPY')} "
            f"{'above' if rg['regime'] == 'up' else 'below'} its {rg.get('days', 50)}-day{nums}")


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
                if e["type"] in ("breakout", "breakdown"):
                    msg += volume_line(c4, e["time"])
                if e["type"] in ("breakout", "breakdown", "hub_formed"):
                    msg += earnings.alert_line(sym)
                if e["type"] in ("breakout", "breakdown") and e["time"] == c4.index[-1]:
                    # gamma wall (newest candle only, so old events never trigger a chain download)
                    gd_ = 1 if e["type"] == "breakout" else -1
                    g = gamma.attach({"symbol": sym, "d": gd_, "edge": e["high"] if gd_ == 1 else e["low"],
                                      "target": e["target"], "price": e["close"]})
                    if (gl := gamma.line(g)):
                        msg += "\n" + gl
                if e["type"] in ("breakout", "breakdown"):
                    msg += regime_line(1 if e["type"] == "breakout" else -1)
                if e["type"] in ("breakout", "breakdown") and e["time"] == c4.index[-1]:
                    msg += news.alert_line(sym, e["time"])       # newest candle only: one news lookup, never a burst
                if WAREHOUSE_ONLY and e.get("grade") == "ledge":
                    continue       # thin hub: graded, not gated, until the split proves it
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
                    early = db.get_alert(f"{sym}|early|{e['time'].isoformat()}") if e["type"] in ("breakout", "breakdown") else None
                    if early and early["type"] == e["type"]:      # already told them at 1:31 PM: confirm, don't ring again
                        msg = "✅ Confirmed with the full market data\n" + msg
                        speech = None
                    notify.symbol_alert(sym, e["type"], msg, speech, pro_extra=extra, candle_start=e["time"])
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
                if e["type"] in ("breakout", "breakdown"):
                    msg += regime_line(1 if e["type"] == "breakout" else -1)
                    msg += news.alert_line(sym, e["time"])
                if db.add_alert(f"{sym}|{e['kind']}|{e['type']}|{e['time'].isoformat()}", sym, e["type"], e["time"].isoformat(), msg):
                    notify.symbol_alert(sym, e["type"], msg, None, style=patterns.STYLE_OF[e["kind"]], candle_start=e["time"])
                    sent.append(msg)
    except Exception as ex:
        log.warning("pattern scan failed for %s: %s", sym, ex)
    try:      # an early read went out for the newest candle but the full data shows no breakout: say so
        last = c4.index[-1]
        early = db.get_alert(f"{sym}|early|{last.isoformat()}")
        if early and not any(e["time"] == last and e["type"] == early["type"] for e in res["events"]):
            msg = (f"⚠️ {sym}: not confirmed after all\n" f"The full market data puts the {engine.candle_end(last).strftime('%-I:%M %p')} close back inside the hub"
                   f" ({_money(float(c4['Close'].iloc[-1]))}). No breakout, so no trade. If you entered on the early read, the plan says exit.")
            if db.add_alert(f"{sym}|unconfirmed|{last.isoformat()}", sym, "unconfirmed", last.isoformat(), msg):
                notify.symbol_alert(sym, "unconfirmed", msg, None, candle_start=last)
                sent.append(msg)
    except Exception as ex:
        log.warning("early-read check failed for %s: %s", sym, ex)
    db.update_ticker(sym, c4.index[-1].isoformat(), json.dumps(status))
    return status, sent


def volume_line(c4: pd.DataFrame, t) -> str:
    """Breakout-candle volume vs a normal candle at that time of day. A label, not a rule."""
    try:
        r = engine.rvol(c4, c4.index.get_loc(t))
    except Exception:
        r = None
    if r is None:
        return ""
    w = engine.rvol_word(r)
    slot = "1:30 PM" if pd.Timestamp(t).tz_convert(engine.NY).hour == 9 else "4:00 PM"
    return (f"\nVolume: {r:.2f}× a normal {slot} candle · {w}"
            + (" (more conviction behind the move)" if w == "strong" else " (thin support for the move, be careful)" if w == "light" else ""))


def format_pattern_event(sym: str, e: dict) -> str:
    if e["kind"] == "gapfade":
        risk = e["close"] - (e.get("stop") or e["close"])
        return (f"🔄 Post-earnings gap fade & rip: {sym} gapped up {e.get('gap_pct', 0) * 100:.1f}% on {e.get('gap_day', 'the gap day')}, "
                f"faded to {_money(e.get('stop'))}, and just closed back above the gap-day high {_money(e['edge'])}.\n"
                f"Entry {_money(e['close'])}, stop {_money(e.get('stop'))} (−1R), target {_money(e['target'])} (+2R).\n"
                f"Size off the risk ({_money(risk)} a share), not a fixed %. Beta: the shape is measured, the earnings link isn't.\n"
                f"Day closed: {candle_close(e['time'])}")
    up = e["type"] == "breakout"
    d = 1 if up else -1
    name = patterns.KINDS[e["kind"]].lower()
    if e["kind"] == "sr":
        what = f"{'resistance' if up else 'support'} at {_money(e['edge'])}" + (f" (tested {e['touches']}x)" if e.get("touches") else "")
    else:
        what = f"the {name} {'upper' if up else 'lower'} line at {_money(e['edge'])}"
    reached = (e["close"] - e["target"]) * d >= 0
    return (f"{'🚀' if up else '🔻'} {sym}: 4H close {'ABOVE' if up else 'BELOW'} {what}\n"
            f"Setup: {patterns.KINDS[e['kind']]} (beta: too few results to judge yet; hubs are the tested setup)\n"
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


def premarket_levels(c4: pd.DataFrame) -> tuple[list[dict], float]:
    """The levels a premarket gap can land on, from regular-session 4H bars only (to_4h drops premarket by design):
    the live hub's edges (resting or just ended) and every tested support/resistance level. Returns (levels, ATR)."""
    res = engine.analyze(c4, SETTINGS)
    status = engine.current_status(c4, res, SETTINGS)
    A = float(res["atr"].iloc[-1])
    levels = []
    h = status.get("hub")
    if h and status.get("state") in ("forming", "watching"):
        levels.append(dict(price=float(h["high"]), kind="hub", touches=None, sessions=h.get("sessions")))
        levels.append(dict(price=float(h["low"]), kind="hub", touches=None, sessions=h.get("sessions")))
    try:
        pa = patterns.analyze_all(c4, res, SETTINGS)
        for L in pa.get("levels_now", []):
            if all(abs(float(L["price"]) - x["price"]) > 0.25 * A for x in levels):   # a hub edge and an S&R level are often the same level: name the hub edge
                levels.append(dict(price=float(L["price"]), kind="sr", touches=L.get("touches"), sessions=None))
    except Exception as ex:
        log.warning("premarket levels: S&R failed: %s", ex)
    return levels, A


def premarket_check(sym: str, bars: pd.DataFrame, now: pd.Timestamp | None = None) -> list[dict]:
    """Levels a premarket gap moved this ticker onto. A level counts when the last premarket price is within
    0.25 ATR of it (the same 'testing' rule the S&R board uses) and yesterday's close was NOT (the gap did it).
    Above yesterday's close it's resistance (the trade is a close up through it); below, support (a close down through it)."""
    if bars is None or bars.empty:
        return []
    now = (now or pd.Timestamp.now(tz="UTC")).tz_convert("America/New_York")
    ny = bars.tz_convert("America/New_York")
    mins = ny.index.hour * 60 + ny.index.minute
    today = now.normalize()
    pm = ny[(ny.index.normalize() == today) & (mins >= 240) & (mins < 570)]
    if pm.empty:
        log.warning("premarket: no premarket bars for %s (feed check)", sym)
        return []
    reg = ny[(ny.index.normalize() < today) & (mins >= 570) & (mins < 960)]
    if reg.empty:
        return []
    pre_last, yest = float(pm["Close"].iloc[-1]), float(reg["Close"].iloc[-1])
    c4 = engine.to_4h(bars[bars.index < today.tz_convert("UTC")])        # yesterday's structure, no premarket
    if len(c4) < SETTINGS.window + 15:
        return []
    levels, A = premarket_levels(c4)
    if not A or A != A:
        return []
    out = []
    for L in levels:
        lv = L["price"]
        if abs(pre_last - lv) > 0.25 * A or abs(yest - lv) <= 0.25 * A:
            continue
        side = "resistance" if yest < lv else "support"
        out.append(dict(L, side=side, direction=1 if side == "resistance" else -1, pre_last=pre_last, yest=yest,
                        pre_high=float(pm["High"].max()), pre_low=float(pm["Low"].min()), bars=len(pm)))
    return out


def format_premarket(sym: str, h: dict) -> str:
    gap = (h["pre_last"] / h["yest"] - 1) * 100
    lv = _money(h["price"])
    if h["kind"] == "hub":
        what = f"Sitting on its hub's {'top' if h['side'] == 'resistance' else 'bottom'} edge {lv} ({h['sessions']} sessions)"
    else:
        what = f"Testing {h['side']} {lv}" + (f" ({h['touches']} touches)" if h.get("touches") else "")
    through = "above" if h["side"] == "resistance" else "below"
    return (f"🔔 {sym} gapped onto a shelf premarket\n"
            f"Last {_money(h['pre_last'])} premarket (gap {gap:+.1f}% vs yesterday's close {_money(h['yest'])}) · "
            f"premarket range {_money(h['pre_low'])}–{_money(h['pre_high'])}\n"
            f"{what}. It opens on the level. Nothing counts until a 4-hour close: a close {through} {lv} is the break, "
            f"and the 1:30 PM close is the first one that can decide it.")


def premarket_all(now: pd.Timestamp | None = None) -> list[str]:
    """Weekday mornings before the open: alert only when the overnight gap moved a watched ticker onto a shelf.
    The morning brief can't see this (it's built on yesterday's close). Information only: no entries, no calls."""
    with _scan_lock:
        sent, seen = [], 0
        day = (now or pd.Timestamp.now(tz="UTC")).tz_convert("America/New_York").date().isoformat()
        for row in db.list_tickers():
            sym = row["symbol"]
            try:
                hits = premarket_check(sym, data.get_bars(sym, SCAN_DAYS), now)
                seen += 1
                for h in hits:
                    msg = format_premarket(sym, h) + earnings.alert_line(sym)
                    msg += regime_line(h["direction"])
                    if db.add_alert(f"{sym}|premarket_gap|{day}|{h['price']:.2f}", sym, "premarket_gap", day, msg):
                        notify.symbol_alert(sym, "premarket_gap", msg, None)
                        sent.append(msg)
            except Exception as e:
                log.warning("premarket failed for %s: %s", sym, e)
        log.info("premarket done: %d tickers checked, %d alerts", seen, len(sent))
        return sent


def early_all(now: pd.Timestamp | None = None) -> list[str]:
    """Right after the 1:30 PM close: an early read from the real-time feed, so members can act ~15 minutes sooner.
    The regular scan confirms it with the full (delayed) data, or sends a correction."""
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    start = engine.current_candle_start(now - pd.Timedelta(minutes=5))     # the candle that just closed
    end = engine.candle_end(start)
    if now < end or now - end > pd.Timedelta(minutes=12):
        return []
    sent = []
    with _scan_lock:
        for row in db.list_tickers():
            sym = row["symbol"]
            try:
                c4 = engine.to_4h(data.get_bars(sym, SCAN_DAYS), now=start)    # candles before the one being judged
                if len(c4) < SETTINGS.window + 15:
                    continue
                res = engine.analyze(c4, SETTINGS)
                close = data.close_at(sym, end)
                sig = engine.preclose_check(res, close, SETTINGS)
                if not sig:
                    continue
                up = sig["direction"] == 1
                typ = "breakout" if up else "breakdown"
                msg = (f"{'🚀' if up else '🔻'} {sym} {'broke out' if up else 'broke down'} · heading to {'next shelf' if sig.get('shelf') else 'target'} (early read)\n"
                       f"1:30 PM close {'above' if up else 'below'} the hub\n"
                       f"Close ≈ {_money(close)} · hub {_money(sig['low'])}–{_money(sig['high'])}\n"
                       + plan_lines(close, sig["edge"], sig["target"], sig["direction"], sig["reached"], sig.get("shelf"))
                       + "\nFrom the real-time feed. The full market data confirms it, with the breakout volume, at about 1:47 PM; you'll get a note either way."
                       + earnings.alert_line(sym))
                msg += regime_line(sig["direction"])
                if db.add_alert(f"{sym}|early|{start.isoformat()}", sym, typ, start.isoformat(), msg):
                    speech = None if sig["reached"] else (
                        f"Next Shelf. {phone.spell(sym)} closed {'above' if up else 'below'} its hub at 1 30. Early read. "
                        f"Close {_say_money(close)}. Hub edge {_say_money(sig['edge'])}. Target {_say_money(sig['target'])}.")
                    notify.symbol_alert(sym, typ, msg, speech, candle_start=start)
                    sent.append(msg)
            except Exception as e:
                log.warning("early read failed for %s: %s", sym, e)
    log.info("early read done, %d alerts", len(sent))
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
                sig = engine.preclose_check(res, data.latest_price(sym), SETTINGS, forming_volume(sym))
                if not sig:
                    continue
                msg = format_preclose(sym, sig, minutes_left) + earnings.alert_line(sym)
                if db.add_alert(f"{sym}|preclose|{start.isoformat()}", sym, "preclose", start.isoformat(), msg):
                    notify.symbol_alert(sym, "preclose", msg,
                                        None if sig["reached"] else speak_preclose(sym, sig, minutes_left), candle_start=start)
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


def backtest(raw: str, pattern: str = "hub") -> dict:
    sym = clean_symbol(raw)
    c4 = load_4h(sym, BACKTEST_DAYS)
    if pattern == "gapfade":          # Patch 38: daily candles from the 4H frame, 2R / stop / 10-day walk
        daily = engine.complete_days(c4)
        res = engine.analyze_gapfade(daily, SETTINGS)
        idx = daily.index
        trades = [dict(time=idx[t["i0"]].isoformat(), direction="up", gap_pct=round(t["pattern"]["gap_pct"] * 100, 1),
                       gap_high=round(t["edge"], 2), close=round(t["entry"], 2), stop=round(t["stop"], 2),
                       target=round(t["target"], 2), target_kind="2R", outcome=t["outcome"],
                       ret_pct=None if t["ret"] is None else round(t["ret"], 2),
                       ret_r=None if t["ret_r"] is None else round(t["ret_r"], 2), chase=False)
                  for t in res["trades"]]
        rs = [t["ret_r"] for t in res["trades"] if t["ret_r"] is not None]
        st = engine.stats(res["trades"])
        st["avg_r"] = round(sum(rs) / len(rs), 2) if rs else None
        return dict(symbol=sym, pattern="gapfade", period=f"{idx[0].date()} to {idx[-1].date()}" if len(idx) else "",
                    stats=st, trades=trades[::-1])
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
    "/backtest NVDA: breakout stats (~2 years)\n"
    "/backtest NVDA gapfade: post-earnings gap fade & rip stats\n\n"
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
        if len(args) > 1 and args[1].lower() in ("gapfade", "gap"):
            r = backtest(args[0], "gapfade")
            s = r["stats"]
            if not s["n"]:
                return f"{r['symbol']}: no gap fade & rip setups in {r['period']}"
            return (f"{r['symbol']} post-earnings gap fade & rip, {r['period']}\nSetups entered: {s['n']}\n"
                    f"Hit 2R {s['follow-through']}% · stopped {s['snap-back']}% · ran out of time {s['stalled']}%\n"
                    f"Avg {s['avg_r'] if s['avg_r'] is not None else '–'}R per trade (close-based; real stops trigger on the low, so live is worse)")
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
