"""Telegram: send alerts and answer commands (long polling, no webhook needed)."""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

import requests

import db

log = logging.getLogger("telegram")
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
API = f"https://api.telegram.org/bot{TOKEN}"


def enabled() -> bool:
    return bool(TOKEN)


def owner_chat() -> str | None:
    return os.getenv("TELEGRAM_CHAT_ID", "").strip() or db.get_setting("owner_chat_id")


def send(text: str, chat_id: str | None = None):
    if not enabled():
        log.info("Telegram disabled, alert not sent: %s", text.splitlines()[0])
        return
    chat = chat_id or owner_chat()
    if not chat:
        log.warning("No Telegram chat linked yet. Send /start to your bot.")
        return
    try:
        r = requests.post(f"{API}/sendMessage", timeout=15,
                          json={"chat_id": chat, "text": text, "disable_web_page_preview": True})
        if r.status_code != 200:
            log.warning("Telegram send failed: %s", r.text[:200])
    except requests.RequestException as e:
        log.warning("Telegram send failed: %s", e)


def start_polling(handle: Callable[[str, list[str]], str]):
    """Run the command listener in a background thread."""
    if not enabled():
        log.info("TELEGRAM_BOT_TOKEN not set, bot is off")
        return
    threading.Thread(target=_poll, args=(handle,), daemon=True, name="telegram").start()


def _poll(handle):
    try:
        requests.post(f"{API}/deleteWebhook", timeout=15)
    except requests.RequestException:
        pass
    offset = None
    while True:
        try:
            r = requests.get(f"{API}/getUpdates", params={"timeout": 50, "offset": offset}, timeout=65)
            if r.status_code == 409:      # another copy of the app is polling (e.g. during a redeploy)
                time.sleep(10)
                continue
            for upd in r.json().get("result", []):
                offset = upd["update_id"] + 1
                _on_message(upd.get("message") or {}, handle)
        except Exception as e:  # keep the bot alive no matter what
            log.warning("Telegram polling error: %s", e)
            time.sleep(5)


def _on_message(msg: dict, handle):
    text = (msg.get("text") or "").strip()
    chat = str((msg.get("chat") or {}).get("id", ""))
    if not text.startswith("/") or not chat:
        return
    parts = text.split()
    cmd, args = parts[0].split("@")[0].lower(), parts[1:]

    owner = owner_chat()
    if owner is None and cmd == "/start":
        db.set_setting("owner_chat_id", chat)
        owner = chat
        send("Linked. Alerts from your hub scanner will come to this chat.", chat)
    if chat != owner:
        send("This is a private bot.", chat)
        return
    try:
        reply = handle(cmd, args)
    except Exception as e:
        log.exception("command failed")
        reply = f"Something went wrong: {e}"
    if reply:
        send(reply, chat)
