"""Web push: alerts as real notifications on iPhone (home-screen app, iOS 16.4+), Android and desktop browsers.

Keys: VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY env vars if set, otherwise generated once and kept in the database
(settings), so they survive restarts. Changing the keys breaks every existing subscription: don't rotate casually.
Fail-open: no library, no keys or a push-service error never blocks Telegram, email or phone alerts."""
from __future__ import annotations

import base64
import json
import logging
import os
import threading

import db

log = logging.getLogger("push")
_LOCK = threading.Lock()
_KEYS: dict = {}
SUB = os.getenv("VAPID_SUB", "https://nextshelfapp.com")      # contact claim the push services see (a URL, not an email)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def keys() -> dict | None:
    """{'public': applicationServerKey (base64url), 'private': PEM} or None if the library is missing."""
    with _LOCK:
        if _KEYS:
            return _KEYS
        pub, priv = os.getenv("VAPID_PUBLIC_KEY", "").strip(), os.getenv("VAPID_PRIVATE_KEY", "").strip()
        if not (pub and priv):
            try:
                stored = json.loads(db.get_setting("vapid") or "null")
            except Exception:
                stored = None
            if stored:
                pub, priv = stored["public"], stored["private"]
            else:
                try:
                    from cryptography.hazmat.primitives import serialization
                    from py_vapid import Vapid
                except Exception as e:
                    log.warning("web push unavailable (pywebpush not installed): %s", e)
                    return None
                v = Vapid()
                v.generate_keys()
                priv = v.private_pem().decode()
                pub = _b64(v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
                db.set_setting("vapid", json.dumps(dict(public=pub, private=priv)))
        _KEYS.update(public=pub, private=priv)
        return _KEYS


def send(user_id: int, title: str, body: str, url: str = "/app#today", tag: str | None = None, silent: bool = False) -> int:
    """Notify every device the member turned alerts on for. Returns how many got it. Never raises."""
    subs = db.push_subs(user_id)
    if not subs:
        return 0
    k = keys()
    if not k:
        return 0
    try:
        from pywebpush import WebPushException, webpush
    except Exception:
        return 0
    try:
        from py_vapid import Vapid
        if "vapid" not in _KEYS:
            pem = k["private"]
            _KEYS["vapid"] = Vapid.from_pem(pem.encode()) if "BEGIN" in pem else Vapid.from_string(pem)
        vapid = _KEYS["vapid"]
    except Exception as e:
        log.warning("bad VAPID private key: %s", e)
        return 0
    payload = json.dumps(dict(title=title[:120], body=body[:600], url=url, tag=tag, silent=silent))
    ok = 0
    for s in subs:
        try:
            webpush(subscription_info=dict(endpoint=s["endpoint"], keys=dict(p256dh=s["p256dh"], auth=s["auth"])),
                    data=payload, vapid_private_key=vapid, vapid_claims={"sub": SUB}, ttl=6 * 3600, timeout=10)
            ok += 1
        except WebPushException as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (404, 410):            # the device unsubscribed or the app was removed: forget it
                db.del_push_sub(s["endpoint"])
            else:
                log.warning("push failed (%s): %s", code, str(e)[:160])
        except Exception as e:
            log.warning("push failed: %s", str(e)[:160])
    return ok


def split(text: str) -> tuple[str, str]:
    """Telegram-style alert text -> (title, body): first line is the title."""
    lines = [l for l in (text or "").strip().splitlines() if l.strip()]
    return (lines[0], "\n".join(lines[1:])) if lines else ("NextShelf", "")
