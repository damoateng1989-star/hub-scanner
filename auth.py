"""Accounts: email + password, Google sign-in, sessions, password reset, plans."""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from collections import defaultdict
from urllib.parse import urlencode

import pandas as pd
import requests
from fastapi import HTTPException, Request
from fastapi.responses import Response

import db

log = logging.getLogger("auth")
COOKIE = "hs_session"
SESSION_DAYS = 365        # stay signed in for a year (renewed each visit)
FREE_TICKER_LIMIT = int(os.getenv("FREE_TICKER_LIMIT", 3))


# ---------------------------------------------------------------- passwords
def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${h.hex()}"


def check_password(pw: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    _, salt, h = stored.split("$")
    test = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
    return hmac.compare_digest(test.hex(), h)


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


# ---------------------------------------------------------------- sessions
def _secure(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


def start_session(response: Response, request: Request, uid: int):
    token = secrets.token_urlsafe(32)
    db.add_session(_sha(token), uid, (_now() + pd.Timedelta(days=SESSION_DAYS)).isoformat())
    db.touch_login(uid)
    response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=_secure(request), path="/")


def end_session(response: Response, request: Request):
    token = request.cookies.get(COOKIE)
    if token:
        db.delete_session(_sha(token))
    response.delete_cookie(COOKIE, path="/")


def current_user(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    u = db.session_user(_sha(token), _now().isoformat())
    if u:   # sliding expiry: every visit pushes sign-out another year away
        db.touch_session(_sha(token), (_now() + pd.Timedelta(days=SESSION_DAYS)).isoformat())
    return u


def require_user(request: Request) -> dict:
    u = current_user(request)
    if not u:
        raise HTTPException(401, "Please sign in")
    return u


def require_pro(request: Request) -> dict:
    u = require_user(request)
    if not is_pro(u):
        raise HTTPException(402, "This is a Pro feature")
    return u


def require_admin(request: Request) -> dict:
    u = require_user(request)
    if not u["is_admin"]:
        raise HTTPException(403, "Admins only")
    return u


def is_pro(u: dict | None) -> bool:
    return bool(u) and (u["plan"] == "pro" or bool(u["is_admin"]))


# ---------------------------------------------------------------- sign-up / sign-in
_attempts: dict[str, list[float]] = defaultdict(list)


def client_ip(request: Request) -> str:
    """The visitor's real IP. Behind Render's proxy, request.client is the proxy (shared by everyone)."""
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "")


def throttle(key: str, limit: int = 8, window: int = 600, record: bool = True):
    """Simple brute-force guard: at most `limit` attempts per `window` seconds per key."""
    now = time.time()
    _attempts[key] = [t for t in _attempts[key] if now - t < window]
    if len(_attempts[key]) >= limit:
        wait = int(window - (now - _attempts[key][0])) // 60 + 1
        raise HTTPException(429, f"Too many tries. Please wait about {wait} minute{'s' if wait != 1 else ''} and try again, "
                                 "or use 'Forgot password?' to reset it.")
    if record:
        _attempts[key].append(now)


def record_failure(key: str):
    _attempts[key].append(time.time())


def clear_attempts(key: str):
    _attempts.pop(key, None)


def _new_user(email: str, name: str = "", password_hash: str | None = None, google_sub: str | None = None) -> int:
    first = db.user_count() == 0
    admin_email = os.getenv("ADMIN_EMAIL", "").strip().lower()
    is_admin = first or (admin_email and email == admin_email)
    uid = db.create_user(email=email, name=name, password_hash=password_hash, google_sub=google_sub,
                         plan="pro" if is_admin else "free", is_admin=int(bool(is_admin)),
                         link_code=secrets.token_urlsafe(9))
    if first:
        # the first account takes over the existing single-user setup: watchlist + Telegram chat
        for t in db.list_tickers():
            db.watch(uid, t["symbol"])
        owner = db.get_setting("owner_chat_id") or os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if owner:
            db.update_user(uid, telegram_chat=owner)
        if os.getenv("CALL_TO"):
            db.update_user(uid, phone=os.getenv("CALL_TO"))
    return uid


def clean_email(email: str) -> str:
    email = (email or "").strip().lower()
    if "@" not in email or "." not in email.split("@")[-1] or len(email) > 200:
        raise HTTPException(400, "Enter a valid email address")
    return email


def signups_open() -> bool:
    """SIGNUPS_OPEN=true on the server opens sign-up to everyone. Anything else = waitlist mode (invite code needed).
    The very first account (the owner) can always be created."""
    return os.getenv("SIGNUPS_OPEN", "false").strip().lower() in ("1", "true", "yes", "on") or db.user_count() == 0


CLOSED_MSG = "Sign-ups are invite-only while NextShelf is in early access. Join the waitlist and we'll email you when it opens."


def check_invite(code: str):
    """Raise unless sign-up is open or the invite code is valid."""
    if signups_open():
        return
    if not (code or "").strip():
        raise HTTPException(403, CLOSED_MSG)
    if not db.invite_ok(code):
        raise HTTPException(403, "That invite code isn't valid or has been used up. Join the waitlist instead.")


def signup(email: str, password: str, name: str = "", invite: str = "") -> int:
    email = clean_email(email)
    if len(password or "") < 8:
        raise HTTPException(400, "Use at least 8 characters for your password")
    existing = db.user_by("email", email)
    if existing:
        if existing["password_hash"] is None and existing["google_sub"]:
            raise HTTPException(400, "This email signs in with Google. Use the Google button.")
        raise HTTPException(400, "An account with this email already exists. Sign in instead.")
    gated = not signups_open()
    check_invite(invite)
    uid = _new_user(email, name.strip()[:80], password_hash=hash_password(password))
    if gated:
        db.use_invite(invite)
    return uid


def login(email: str, password: str) -> int:
    email = clean_email(email)
    u = db.user_by("email", email)
    if not u:
        raise HTTPException(400, "There's no account with that email yet. Tap 'Create an account' to sign up.")
    if not check_password(password, u["password_hash"]):
        if not u["password_hash"] and u["google_sub"]:
            raise HTTPException(400, "This email signs in with Google. Use the Google button.")
        raise HTTPException(400, "Wrong password. Try again or use 'Forgot password?'.")
    return u["id"]


# ---------------------------------------------------------------- password reset (email via Resend)
def send_email(to: str, subject: str, html: str, text: str | None = None) -> bool:
    key = os.getenv("RESEND_API_KEY", "").strip()
    sender = os.getenv("EMAIL_FROM", "NextShelf <onboarding@resend.dev>")
    if not key:
        log.warning("RESEND_API_KEY not set; email to %s not sent: %s", to, subject)
        return False
    payload = {"from": sender, "to": [to], "subject": subject, "html": html}
    if text:
        payload["text"] = text
    r = requests.post("https://api.resend.com/emails", headers={"Authorization": f"Bearer {key}"},
                      json=payload, timeout=20)
    if r.status_code >= 300:
        log.warning("email failed: %s", r.text[:200])
        return False
    return True


def _reset_email_html(link: str) -> str:
    """Branded password-reset email. Table layout + inline styles only
    (email-client safe). Same copy as before, dressed in the brand:
    dark background, white pill button, black button text."""
    return f"""<html><head><meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark"></head>
<body style="margin:0;padding:0;background:#000000;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#000000;">
<tr><td align="center" style="padding:32px 16px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:600px;background:#000000;border:1px solid #33302a;border-radius:12px;">
<tr><td style="padding:32px 40px 0 40px;text-align:center;">
<div style="font-family:Georgia,'Times New Roman',serif;font-size:28px;line-height:1.2;color:#f5efe3;">NextShelf</div>
<div style="font-family:Georgia,'Times New Roman',serif;font-size:13px;line-height:1.5;color:#a89e8d;margin-top:6px;font-style:italic;">We watch your setup while you go do other things.</div>
</td></tr>
<tr><td style="padding:24px 40px 0 40px;"><div style="border-top:1px solid #33302a;font-size:0;line-height:0;">&nbsp;</div></td></tr>
<tr><td style="padding:28px 40px 8px 40px;">
<div style="font-family:Georgia,'Times New Roman',serif;font-size:22px;line-height:1.3;color:#f5efe3;">Reset your password</div>
<p style="font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:1.6;color:#e9e2d3;margin:16px 0 0 0;">Someone asked to reset the password for your NextShelf account.</p>
</td></tr>
<tr><td align="center" style="padding:24px 40px 8px 40px;">
<!--[if mso]>
<v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" href="{link}" style="width:240px;height:48px;" arcsize="50%" fillcolor="#ffffff" stroke="f">
<v:textbox inset="0,0,0,0"><center style="color:#1c1917;font-family:Arial,sans-serif;font-size:16px;">Choose a new password</center></v:textbox>
</v:roundrect>
<![endif]-->
<!--[if !mso]><!-- -->
<a href="{link}" style="display:inline-block;background:#ffffff;color:#1c1917;font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:1;text-decoration:none;padding:15px 32px;border-radius:999px;">Choose a new password</a>
<!--<![endif]-->
<p style="font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:1.5;color:#a89e8d;margin:14px 0 0 0;">Link works for 1 hour.</p>
</td></tr>
<tr><td style="padding:16px 40px 8px 40px;">
<p style="font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:1.6;color:#e9e2d3;margin:0;">If this wasn&rsquo;t you, ignore this email &mdash; your password stays as it is.</p>
<p style="font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:1.6;color:#a89e8d;margin:20px 0 0 0;">Button not working? Paste this link into your browser:<br>
<span style="word-break:break-all;color:#d8cdb8;">{link}</span></p>
</td></tr>
<tr><td style="padding:24px 40px 0 40px;"><div style="border-top:1px solid #33302a;font-size:0;line-height:0;">&nbsp;</div></td></tr>
<tr><td style="padding:20px 40px 32px 40px;text-align:center;">
<p style="font-family:Arial,Helvetica,sans-serif;font-size:12px;line-height:1.6;color:#a89e8d;margin:0;">NextShelf &mdash; hub-breakout scanner for swing traders with day jobs.<br>
You&rsquo;re getting this because a password reset was requested for your NextShelf account.<br>
<a href="https://nextshelfapp.com" style="color:#a89e8d;">nextshelfapp.com</a></p>
</td></tr>
</table></td></tr></table></body></html>"""


def _reset_email_text(link: str) -> str:
    return ("Someone asked to reset the password for your NextShelf account.\n\n"
            f"Choose a new password (link works for 1 hour): {link}\n\n"
            "If this wasn't you, ignore this email — your password stays as it is.\n\n"
            "NextShelf — hub-breakout scanner for swing traders with day jobs.\n"
            "https://nextshelfapp.com")


def start_reset(email: str, base_url: str):
    """Always behaves the same whether or not the email exists (no account probing)."""
    u = db.user_by("email", (email or "").strip().lower())
    if not u:
        return
    token = secrets.token_urlsafe(32)
    db.add_reset(_sha(token), u["id"], (_now() + pd.Timedelta(hours=1)).isoformat())
    link = f"{base_url}/login#reset={token}"
    sent = send_email(u["email"], "Reset your NextShelf password",
                      _reset_email_html(link), _reset_email_text(link))
    if not sent:
        log.warning("Password reset link for %s (email not configured): %s", u["email"], link)


def finish_reset(token: str, password: str) -> int:
    if len(password or "") < 8:
        raise HTTPException(400, "Use at least 8 characters for your password")
    uid = db.use_reset(_sha(token or ""), _now().isoformat())
    if not uid:
        raise HTTPException(400, "This reset link is invalid or has expired. Request a new one.")
    db.update_user(uid, password_hash=hash_password(password))
    db.delete_user_sessions(uid)
    return uid


# ---------------------------------------------------------------- Google sign-in
def google_enabled() -> bool:
    return bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))


def base_url(request: Request) -> str:
    env = os.getenv("APP_URL", "").strip().rstrip("/")
    if env:
        return env
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    return f"{proto}://{host}"


def google_redirect(request: Request, response: Response) -> str:
    state = secrets.token_urlsafe(16)
    response.set_cookie("hs_oauth_state", state, max_age=600, httponly=True, samesite="lax",
                        secure=_secure(request), path="/")
    q = urlencode(dict(client_id=os.getenv("GOOGLE_CLIENT_ID"), redirect_uri=f"{base_url(request)}/auth/google/callback",
                       response_type="code", scope="openid email profile", state=state, prompt="select_account"))
    return f"https://accounts.google.com/o/oauth2/v2/auth?{q}"


def google_finish(request: Request, code: str, state: str) -> int:
    if not state or not hmac.compare_digest(state, request.cookies.get("hs_oauth_state", "")):
        raise HTTPException(400, "Sign-in expired. Please try again.")
    tok = requests.post("https://oauth2.googleapis.com/token", timeout=20, data=dict(
        code=code, client_id=os.getenv("GOOGLE_CLIENT_ID"), client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        redirect_uri=f"{base_url(request)}/auth/google/callback", grant_type="authorization_code"))
    if tok.status_code != 200:
        raise HTTPException(400, "Google sign-in failed. Please try again.")
    info = requests.get("https://openidconnect.googleapis.com/v1/userinfo", timeout=20,
                        headers={"Authorization": f"Bearer {tok.json()['access_token']}"}).json()
    if not info.get("email_verified"):
        raise HTTPException(400, "Your Google email isn't verified.")
    sub, email = info["sub"], info["email"].lower()
    u = db.user_by("google_sub", sub) or db.user_by("email", email)
    if u:
        if not u["google_sub"]:
            db.update_user(u["id"], google_sub=sub)      # link Google to an existing email account
        return u["id"]
    invite = request.cookies.get("hs_invite", "")
    gated = not signups_open()
    check_invite(invite)                                  # existing members always get in; new ones need a code
    uid = _new_user(email, info.get("name", ""), google_sub=sub)
    if gated:
        db.use_invite(invite)
    return uid
