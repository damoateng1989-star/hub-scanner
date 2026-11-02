"""Route alerts to the users watching each symbol, according to their plan."""
from __future__ import annotations

import logging

import db
import phone
import telegram_bot

log = logging.getLogger("notify")
PRO_ONLY_TYPES = {"preclose"}        # pre-close warnings are a Pro feature


def _pro(u: dict) -> bool:
    return u["plan"] == "pro" or bool(u["is_admin"])


def styles_of(u: dict) -> set[str]:
    """Setups a member wants alerts for. Hubs by default; extra setups (triangles, channels, S/R) are Pro."""
    picked = {x for x in (u.get("styles") or "hub").split(",") if x}
    return picked if _pro(u) else picked & {"hub"} or {"hub"}


def symbol_alert(symbol: str, alert_type: str, text: str, speech: str | None = None, style: str = "hub"):
    """Telegram to every watcher (Pro-only types to Pro users); phone calls to Pro users with a number."""
    for u in db.watchers(symbol):
        if alert_type in PRO_ONLY_TYPES and not _pro(u):
            continue
        if style not in styles_of(u):
            continue
        if u["telegram_chat"]:
            telegram_bot.send(text, u["telegram_chat"])
        if speech and _pro(u) and u["phone"] and phone.wants(alert_type):
            phone.safe_call(speech, to=u["phone"])


def admins(text: str):
    """Messages only the site owner should get (e.g. paper trading)."""
    for u in db.all_users():
        if u["is_admin"] and u["telegram_chat"]:
            telegram_bot.send(text, u["telegram_chat"])
