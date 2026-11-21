"""Route alerts to the users watching each symbol, according to their plan."""
from __future__ import annotations

import logging

import threading

import db
import phone
import push
import telegram_bot

log = logging.getLogger("notify")
PRO_ONLY_TYPES = {"preclose"}        # pre-close warnings are a Pro feature
# Nov 20: members get the trade and nothing else — the breakout heads-up, the "back at the edge" heads-up, and the
# retest itself (hub and double bottom/top). Hub formed, early read, unconfirmed, pre-close, premarket gap, the live
# trigger-cross note, other-pattern alerts and the every-A digest are admin-only now. Their own price alerts and the
# daily summaries go by other paths and are unaffected. MEMBER_ALERT_TYPES (comma list) overrides.
import os as _os
MEMBER_TYPES = set(filter(None, (_os.getenv("MEMBER_ALERT_TYPES") or
                                 "breakout,breakdown,retesting_now,retest,retest_double").split(",")))
MEMBER_BREAKOUT_STYLES = {"hub", "double", "gapfade"}    # breakouts of setups that have a trade plan (not triangles etc.)


def member_wants(u: dict, alert_type: str, style: str) -> bool:
    """Does this alert reach this member? Admins get everything."""
    if u["is_admin"]:
        return True
    if alert_type not in MEMBER_TYPES:
        return False
    if alert_type in ("breakout", "breakdown") and style not in MEMBER_BREAKOUT_STYLES:
        return False
    return True


def _pro(u: dict) -> bool:
    return u["plan"] == "pro" or bool(u["is_admin"])


def quiet_now(u: dict, now=None) -> bool:
    """Inside the member's quiet hours (New York time)? Then no phone calls or texts, and Telegram arrives without a sound."""
    q = (u.get("quiet") or "").strip()
    if not q or "-" not in q:
        return False
    try:
        import pandas as pd
        t = (now or pd.Timestamp.now(tz="America/New_York"))
        cur = t.hour * 60 + t.minute
        a, b = [int(x.split(":")[0]) * 60 + int(x.split(":")[1]) for x in q.split("-")]
    except Exception:
        return False
    return a <= cur < b if a < b else (cur >= a or cur < b)


def styles_of(u: dict) -> set[str]:
    """Setups a member wants alerts for. Hubs by default; extra setups (triangles, channels, S/R) are Pro."""
    picked = {x for x in (u.get("styles") or "hub").split(",") if x}
    return picked if _pro(u) else picked & {"hub"} or {"hub"}


def late_close(candle_start) -> bool:
    """Is this the 1:30-4:00 PM candle (confirmed after the market has closed)?"""
    try:
        import pandas as _pd
        t = _pd.Timestamp(candle_start).tz_convert("America/New_York")
        return t.hour * 60 + t.minute >= 13 * 60 + 30
    except Exception:
        return False


def symbol_alert(symbol: str, alert_type: str, text: str, speech: str | None = None, style: str = "hub",
                 pro_extra=None, candle_start=None, members: bool = True, flow_extra: str | None = None):
    """Telegram to every watcher (Pro-only types to Pro users); texts to Pro users with a number; phone calls to Pro users with a number (preclose + breakouts only).
    pro_extra: a line (or a function that makes one, called once) added for Pro members, e.g. the options swing idea."""
    extra_cache = {}
    for u in db.watchers(symbol):
        if not member_wants(u, alert_type, style):
            continue
        if not members and not u["is_admin"]:      # this one failed the Lab (shorts, B grades, double tops): admin only
            continue
        if alert_type in PRO_ONLY_TYPES and not _pro(u):
            continue
        if style not in styles_of(u):
            continue
        if candle_start is not None and (u.get("closes") or "both") == "midday" and late_close(candle_start):
            continue                     # this member only trades the 1:30 PM close; the recap covers the 4:00 PM one
        quiet = quiet_now(u)
        msg = text
        if pro_extra and _pro(u):
            if "v" not in extra_cache:
                try:
                    extra_cache["v"] = pro_extra() if callable(pro_extra) else pro_extra
                except Exception:
                    extra_cache["v"] = None
            if extra_cache["v"]:
                msg = f"{text}\n{extra_cache['v']}"
        rich = msg                       # Patch 182: the tape line, for recipients the flow gate allows
        if flow_extra:
            try:
                import swing_card
                if swing_card.flow_allowed(u):
                    rich = f"{msg}\n{flow_extra}"
            except Exception:
                pass                     # never delays or blocks the alert
        if u["telegram_chat"]:
            telegram_bot.send(rich, u["telegram_chat"], silent=quiet)
        _push(u["id"], rich, f"/t/{symbol}", f"{symbol}-{alert_type}", quiet)
        if _pro(u) and u["phone"] and not quiet:
            phone.safe_text(msg, to=u["phone"])     # texts and calls never carry the tape line
        if speech and _pro(u) and u["phone"] and phone.wants(alert_type) and not quiet:
            phone.safe_call(speech, to=u["phone"])


def every_a_alert(items: list[dict]):
    """Opt-in: every A setup confirmed in a board sweep, across all stocks, in one message per member.
    Skips stocks the member watches (they already get that stock's own alert) and setups they haven't picked."""
    import patterns
    fresh = []
    for x in items:                                           # once per setup, even if a sweep reruns
        key = f"every_a|{x['symbol']}|{x['kind']}|{x['entry_time']}"
        try:
            if db.add_alert(key, x["symbol"], "every_a", x["entry_time"], x["setup"]):
                fresh.append(x)
        except Exception:
            fresh.append(x)
    if not fresh:
        return 0
    money = lambda v: f"${v:,.2f}"
    sent = 0
    for u in db.all_users():
        if not u.get("a_all"):
            continue
        if not u["is_admin"] and "every_a" not in MEMBER_TYPES:      # Nov 20: members get the trade only
            continue
        mine = set(db.user_symbols(u["id"]))
        late = all(late_close(x["entry_time"]) for x in fresh)
        if (u.get("closes") or "both") == "midday" and late:
            continue
        picks = [x for x in fresh if x["symbol"] not in mine and patterns.STYLE_OF.get(x["kind"], "hub") in styles_of(u)]
        if not picks:
            continue
        lines = [f"🅰️ {len(picks)} A setup{'s' if len(picks) != 1 else ''} just confirmed (all stocks)"]
        for x in picks[:12]:
            rr = (x["target"] - x["entry"]) / (x["entry"] - x["stop"]) if x["entry"] > x["stop"] else 0
            lines.append(f"• {x['symbol']} · {x['setup']}: entry {money(x['entry'])} · stop {money(x['stop'])} · target {money(x['target'])}"
                         + (f" · {rr:.1f}R" if rr else ""))
        if len(picks) > 12:
            lines.append(f"…and {len(picks) - 12} more on the Setups page.")
        msg = "\n".join(lines)
        quiet = quiet_now(u)
        if u["telegram_chat"]:
            telegram_bot.send(msg, u["telegram_chat"], silent=quiet)
        _push(u["id"], msg, "/app#setups", "every-a", quiet)
        if _pro(u) and u["phone"] and not quiet:
            phone.safe_text(msg, to=u["phone"])
        sent += 1
    return sent


def user_alert(u: dict, text: str, url: str, tag: str | None = None):
    """One message to one member (their own price alerts): Telegram, push, and a text for Pro members with a number."""
    quiet = quiet_now(u)
    if u.get("telegram_chat"):
        telegram_bot.send(text, u["telegram_chat"], silent=quiet)
    _push(u["id"], text, url, tag, quiet)
    if _pro(u) and u.get("phone") and not quiet:
        phone.safe_text(text, to=u["phone"])


def _push(user_id: int, text: str, url: str, tag: str | None = None, silent: bool = False):
    """iPhone / browser notification, off the alert loop's thread so a slow push service never delays Telegram."""
    title, body = push.split(text)
    threading.Thread(target=push.send, args=(user_id, title, body, url, tag, silent), daemon=True).start()


def admins(text: str):
    """Messages only the site owner should get (e.g. paper trading)."""
    for u in db.all_users():
        if u["is_admin"]:
            if u["telegram_chat"]:
                telegram_bot.send(text, u["telegram_chat"])
            _push(u["id"], text, "/app#paper", None)
