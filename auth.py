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


def throttle(key: str, limit: int = 8, window: int = 600):
    """Simple brute-force guard: at most `limit` attempts per `window` seconds per key."""
    now = time.time()
    _attempts[key] = [t for t in _attempts[key] if now - t < window]
    if len(_attempts[key]) >= limit:
        raise HTTPException(429, "Too many attempts. Try again in a few minutes.")
    _attempts[key].append(now)


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


def signup(email: str, password: str, name: str = "") -> int:
    email = clean_email(email)
    if len(password or "") < 8:
        raise HTTPException(400, "Use at least 8 characters for your password")
    existing = db.user_by("email", email)
    if existing:
        if existing["password_hash"] is None and existing["google_sub"]:
            raise HTTPException(400, "This email signs in with Google. Use the Google button.")
        raise HTTPException(400, "An account with this email already exists. Sign in instead.")
    return _new_user(email, name.strip()[:80], password_hash=hash_password(password))


def login(email: str, password: str) -> int:
    email = clean_email(email)
    u = db.user_by("email", email)
    if not u or not check_password(password, u["password_hash"]):
        if u and not u["password_hash"] and u["google_sub"]:
            raise HTTPException(400, "This email signs in with Google. Use the Google button.")
        raise HTTPException(400, "Wrong email or password")
    return u["id"]


# ---------------------------------------------------------------- password reset (email via Resend)
def send_email(to: str, subject: str, html: str) -> bool:
    key = os.getenv("RESEND_API_KEY", "").strip()
    sender = os.getenv("EMAIL_FROM", "Hub Scanner <onboarding@resend.dev>")
    if not key:
        log.warning("RESEND_API_KEY not set; email to %s not sent: %s", to, subject)
        return False
    r = requests.post("https://api.resend.com/emails", headers={"Authorization": f"Bearer {key}"},
                      json={"from": sender, "to": [to], "subject": subject, "html": html}, timeout=20)
    if r.status_code >= 300:
        log.warning("email failed: %s", r.text[:200])
        return False
    return True


def start_reset(email: str, base_url: str):
    """Always behaves the same whether or not the email exists (no account probing)."""
    u = db.user_by("email", (email or "").strip().lower())
    if not u:
        return
    token = secrets.token_urlsafe(32)
    db.add_reset(_sha(token), u["id"], (_now() + pd.Timedelta(hours=1)).isoformat())
    link = f"{base_url}/login#reset={token}"
    sent = send_email(u["email"], "Reset your Hub Scanner password",
                      f"<p>Someone asked to reset the password for your Hub Scanner account.</p>"
                      f"<p><a href=\"{link}\">Choose a new password</a> (link works for 1 hour).</p>"
                      f"<p>If this wasn't you, ignore this email.</p>")
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
    return _new_user(email, info.get("name", ""), google_sub=sub)
