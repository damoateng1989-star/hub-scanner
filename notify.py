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
                 pro_extra=None, candle_start=None):
    """Telegram to every watcher (Pro-only types to Pro users); texts to Pro users with a number; phone calls to Pro users with a number (preclose + breakouts only).
    pro_extra: a line (or a function that makes one, called once) added for Pro members, e.g. the options swing idea."""
    extra_cache = {}
    for u in db.watchers(symbol):
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
        if u["telegram_chat"]:
            telegram_bot.send(msg, u["telegram_chat"], silent=quiet)
        _push(u["id"], msg, f"/t/{symbol}", f"{symbol}-{alert_type}", quiet)
        if _pro(u) and u["phone"] and not quiet:
            phone.safe_text(msg, to=u["phone"])
        if speech and _pro(u) and u["phone"] and phone.wants(alert_type) and not quiet:
            phone.safe_call(speech, to=u["phone"])


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
