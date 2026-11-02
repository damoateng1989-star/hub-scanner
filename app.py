"""Web app + scheduler + Telegram bot, all in one process.

Run locally:   uvicorn app:app --reload
Run in prod:   uvicorn app:app --host 0.0.0.0 --port $PORT   (one worker only)
"""
from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

import json  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import secrets  # noqa: E402
import threading  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from pathlib import Path  # noqa: E402

from apscheduler.schedulers.background import BackgroundScheduler  # noqa: E402
from apscheduler.triggers.cron import CronTrigger  # noqa: E402
from fastapi import Depends, FastAPI, HTTPException, Request  # noqa: E402
from fastapi.concurrency import run_in_threadpool  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import pandas as pd  # noqa: E402

import auth  # noqa: E402
import engine  # noqa: E402
import legal  # noqa: E402
import options  # noqa: E402
import patterns  # noqa: E402
import board  # noqa: E402
import brief  # noqa: E402
import data  # noqa: E402
import db  # noqa: E402
import notify  # noqa: E402
import paper  # noqa: E402
import phone  # noqa: E402
import scanner  # noqa: E402
import telegram_bot  # noqa: E402
import universe  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("app")

# Scan a few minutes after each 4H close (13:30 and 16:00 ET). The delay covers the
# free Alpaca plan's 15-minute delay on full-market (SIP) data.
SCAN_TIMES = [t.strip() for t in os.getenv("SCAN_TIMES", "13:47,16:17").split(",") if t.strip()]
# Pre-close warning ~15 minutes before each 4H close (uses Alpaca's free real-time IEX price). Empty = off.
PRECLOSE_TIMES = [t.strip() for t in os.getenv("PRECLOSE_TIMES", "13:15,15:45").split(",") if t.strip()]
# Hub Board: market-wide scan, a few minutes after the watchlist scan. Empty = off.
BOARD_TIMES = [t.strip() for t in os.getenv("BOARD_TIMES", "13:52,16:22").split(",") if t.strip()]
# Paper trading: entries/exits ~2 minutes before each 4H close.
ENTRY_TIMES = [t.strip() for t in os.getenv("ENTRY_TIMES", "13:28,15:58").split(",") if t.strip()]


def add_for_user(u: dict, raw: str) -> dict:
    sym = scanner.clean_symbol(raw)
    mine = db.user_symbols(u["id"])
    if sym not in mine and not auth.is_pro(u) and len(mine) >= auth.FREE_TICKER_LIMIT:
        raise HTTPException(402, f"Free accounts can watch {auth.FREE_TICKER_LIMIT} tickers. Upgrade to Pro for unlimited.")
    return scanner.add_symbol(sym, u["id"])


def handle_command(cmd: str, args: list[str], u: dict) -> str:
    pro, admin = auth.is_pro(u), bool(u["is_admin"])
    if cmd in ("/start", "/help"):
        text = ("NextShelf commands:\n/add NVDA TSLA: watch tickers\n/remove NVDA: stop watching\n"
                "/list: your watchlist\n/status NVDA: one ticker")
        if pro:
            text += "\n/backtest NVDA: past breakout results\n/testcall: test phone-call alerts"
        if admin:
            text += "\n\nAdmin:\n/scan, /precheck: run now\n/paper, /paper on, /paper off"
        return text + ("" if pro else "\n\nUpgrade to Pro for pre-close warnings, calls and backtests.") + "\nNot financial advice."
    if cmd == "/add":
        if not args:
            return "Usage: /add NVDA TSLA"
        out = []
        for a in args:
            try:
                r = add_for_user(u, a)
                out.append("Watching " + scanner.describe(r["symbol"], r["status"]))
            except Exception as e:
                out.append(f"{a.upper()}: {getattr(e, 'detail', e)}")
        return "\n".join(out)
    if cmd == "/remove":
        return "\n".join(f"{a.upper()}: {'removed' if db.unwatch(u['id'], a.upper()) else 'not on your list'}"
                         for a in args) or "Usage: /remove NVDA"
    if cmd == "/list":
        syms = db.user_symbols(u["id"])
        if not syms:
            return "Your watchlist is empty. Add one with /add NVDA"
        return "\n".join(scanner.describe(s_, json.loads((db.get_ticker(s_) or {}).get("status") or "{}")
                                           or {"error": "not scanned yet"}) for s_ in syms)
    if cmd == "/status":
        if not args or args[0].upper() not in db.user_symbols(u["id"]):
            return "Usage: /status NVDA (for a ticker on your watchlist)"
        return scanner.handle_command(cmd, args)
    if cmd == "/backtest":
        return scanner.handle_command(cmd, args) if pro else "Backtests are a Pro feature."
    if cmd == "/testcall":
        if not pro:
            return "Phone alerts are a Pro feature."
        phone.call("Next Shelf test call. Phone alerts are working.", u["phone"])
        return "Calling you now."
    if cmd in ("/scan", "/precheck") and admin:
        return scanner.handle_command(cmd, args)
    if cmd == "/paper" and admin:
        if args and args[0].lower() in ("on", "off"):
            paper.set_enabled(args[0].lower() == "on")
        return paper.summary_text()
    return "Unknown command. /help for the list."


@asynccontextmanager
async def lifespan(_app: FastAPI):
    sched = BackgroundScheduler(timezone="America/New_York")
    for t in SCAN_TIMES:
        h, m = t.split(":")
        sched.add_job(scanner.scan_all, CronTrigger(day_of_week="mon-fri", hour=int(h), minute=int(m),
                                                    timezone="America/New_York"),
                      misfire_grace_time=1800, coalesce=True, max_instances=1)
    for t in PRECLOSE_TIMES:
        h, m = t.split(":")
        sched.add_job(scanner.preclose_all, CronTrigger(day_of_week="mon-fri", hour=int(h), minute=int(m),
                                                        timezone="America/New_York"),
                      misfire_grace_time=300, coalesce=True, max_instances=1)
    for t in ENTRY_TIMES:
        h, m = t.split(":")
        sched.add_job(paper.run_check, CronTrigger(day_of_week="mon-fri", hour=int(h), minute=int(m),
                                                   timezone="America/New_York"),
                      misfire_grace_time=60, coalesce=True, max_instances=1)
    if brief.BRIEF_TIME:      # morning brief, weekdays before the open
        bh, bm = map(int, brief.BRIEF_TIME.split(":"))
        sched.add_job(brief.send_all, CronTrigger(day_of_week="mon-fri", hour=bh, minute=bm, timezone="America/New_York"),
                      id="brief", replace_existing=True)
    for t in BOARD_TIMES:
        h, m = t.split(":")
        sched.add_job(board.scan_board, CronTrigger(day_of_week="mon-fri", hour=int(h), minute=int(m),
                                                    timezone="America/New_York"),
                      misfire_grace_time=1800, coalesce=True, max_instances=1)
    if BOARD_TIMES and not db.board_rows():      # first start: fill the board shortly after the site is up
        threading.Timer(20, board.scan_board).start()
    sched.add_job(paper.sync, CronTrigger(day_of_week="mon-fri", hour="9-16", minute="*/5",
                                          timezone="America/New_York"),
                  misfire_grace_time=120, coalesce=True, max_instances=1)
    sched.start()
    log.info("Scheduler on: scans %s, pre-close %s ET (provider: %s)",
             ", ".join(SCAN_TIMES), ", ".join(PRECLOSE_TIMES) or "off", data.provider())
    telegram_bot.start_polling(handle_command)
    yield
    sched.shutdown(wait=False)


app = FastAPI(title="NextShelf", lifespan=lifespan)
User = Depends(auth.require_user)
Maybe = Depends(auth.current_user)        # signed in or a visitor (None)
Pro = Depends(auth.require_pro)
Admin = Depends(auth.require_admin)


class AddBody(BaseModel):
    symbol: str


class SignupBody(BaseModel):
    email: str
    password: str
    name: str = ""


class LoginBody(BaseModel):
    email: str
    password: str


class EmailBody(BaseModel):
    email: str


class ResetBody(BaseModel):
    token: str
    password: str


class PhoneBody(BaseModel):
    phone: str = ""


class PlanBody(BaseModel):
    plan: str


class ToggleBody(BaseModel):
    on: bool


STATIC = Path(__file__).parent / "static"


# ---------------------------------------------------------------- public files
@app.get("/health")
def health():
    return {"ok": True}


@app.get("/favicon.svg")
def favicon():
    return FileResponse(STATIC / "favicon.svg", media_type="image/svg+xml")


@app.get("/favicon.ico")
def favicon_ico():
    return FileResponse(STATIC / "favicon.svg", media_type="image/svg+xml")


@app.get("/apple-touch-icon.png")
def touch_icon():
    return FileResponse(STATIC / "apple-touch-icon.png", media_type="image/png")


@app.get("/fonts/{name}")
def fonts(name: str):
    """Self-hosted Geist fonts (SIL Open Font License): sharp, fast, no third-party font requests."""
    if name not in ("Geist.woff2", "GeistMono.woff2", "OFL.txt"):
        raise HTTPException(404)
    return FileResponse(STATIC / "fonts" / name, media_type="font/woff2" if name.endswith("woff2") else "text/plain",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/vendor/lightweight-charts.js")
def lwc():
    return FileResponse(STATIC / "vendor" / "lightweight-charts.js", media_type="application/javascript")


# ---------------------------------------------------------------- pages (sign-in required)
@app.get("/login")
def login_page(request: Request):
    if auth.current_user(request):
        return RedirectResponse("/app", 302)
    return FileResponse(STATIC / "login.html")


@app.get("/owner")
def owner_signin(request: Request, key: str = ""):
    """Rescue sign-in for the site owner: /owner?key=YOUR_OWNER_KEY (set OWNER_KEY on the server). No password needed."""
    want = os.getenv("OWNER_KEY", "").strip()
    auth.throttle("owner:" + auth.client_ip(request), limit=10, window=3600)
    if not want or len(want) < 12 or not secrets.compare_digest(key, want):
        raise HTTPException(404)
    admin = next((u for u in db.all_users() if u["is_admin"]), None)
    if not admin:
        return RedirectResponse("/login#signup", 302)      # no account yet: the first sign-up becomes the owner
    resp = RedirectResponse("/app", 302)
    auth.start_session(resp, request, admin["id"])
    return resp


@app.get("/")
def landing():
    """Public homepage. Signed-in members see an "Open app" button instead of Login / Sign Up."""
    return FileResponse(STATIC / "landing.html")


@app.get("/app")
def index(request: Request):
    """The app. Open to visitors (they can browse the Hub Board and charts); members get their cookie refreshed."""
    resp = FileResponse(STATIC / "index.html")
    if auth.current_user(request):
        resp.set_cookie(auth.COOKIE, request.cookies[auth.COOKIE], max_age=auth.SESSION_DAYS * 86400, httponly=True,
                        samesite="lax", secure=auth._secure(request), path="/")
    return resp


@app.get("/t/{symbol}")
def ticker_page(symbol: str):
    return FileResponse(STATIC / "ticker.html")


# ---------------------------------------------------------------- trust pages: terms, privacy, risk disclaimer, contact
@app.get("/terms", response_class=HTMLResponse)
def terms_page():
    return legal.render("terms")


@app.get("/privacy", response_class=HTMLResponse)
def privacy_page():
    return legal.render("privacy")


@app.get("/disclaimer", response_class=HTMLResponse)
def disclaimer_page():
    return legal.render("disclaimer")


@app.get("/contact", response_class=HTMLResponse)
def contact_page():
    return legal.render("contact")


class ContactBody(BaseModel):
    name: str = ""
    email: str
    topic: str = "General question"
    message: str
    website: str = ""          # honeypot


@app.post("/api/contact")
def contact_send(body: ContactBody, request: Request):
    import html as _h
    import re as _re
    auth.throttle("contact:" + auth.client_ip(request), limit=5, window=3600)
    if body.website:
        return {"ok": True}
    email = body.email.strip().lower()
    msg = body.message.strip()
    if not _re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(email) > 200:
        raise HTTPException(400, "Please enter a valid email address.")
    if len(msg) < 5:
        raise HTTPException(400, "Please write a short message.")
    name, topic = body.name.strip()[:120], body.topic.strip()[:60] or "General question"
    u = auth.current_user(request)
    db.add_message(name, email, topic, msg[:5000], u["id"] if u else None)
    to = os.getenv("CONTACT_EMAIL", "").strip() or next((x["email"] for x in db.all_users() if x["is_admin"]), "")
    if to:
        auth.send_email(to, f"NextShelf contact: {topic}",
                        f"<p><b>From:</b> {_h.escape(name or '(no name)')} &lt;{_h.escape(email)}&gt;</p><p><b>Topic:</b> {_h.escape(topic)}</p>"
                        f"<p style='white-space:pre-wrap'>{_h.escape(msg[:5000])}</p>")
    try:
        notify.admins(f"✉️ New message ({topic}) from {email}:\n{msg[:500]}")
    except Exception:
        pass
    return {"ok": True}


@app.get("/api/admin/messages")
def admin_messages(u: dict = Admin):
    return db.list_messages()


class HandledBody(BaseModel):
    handled: bool = True


@app.post("/api/admin/messages/{mid}")
def admin_message_handled(mid: int, body: HandledBody, u: dict = Admin):
    db.set_message_handled(mid, body.handled)
    return {"ok": True}


# ---------------------------------------------------------------- auth
@app.get("/auth/config")
def auth_config():
    return {"google": auth.google_enabled(), "free_limit": auth.FREE_TICKER_LIMIT}


@app.post("/auth/signup")
def signup(body: SignupBody, request: Request):
    auth.throttle("signup:" + auth.client_ip(request), limit=10, window=3600)
    uid = auth.signup(body.email, body.password, body.name)
    resp = JSONResponse({"ok": True})
    auth.start_session(resp, request, uid)
    return resp


@app.post("/auth/login")
def do_login(body: LoginBody, request: Request):
    key = "login:" + body.email.strip().lower() + "|" + auth.client_ip(request)
    auth.throttle(key, limit=10, window=900, record=False)     # only wrong passwords count
    try:
        uid = auth.login(body.email, body.password)
    except HTTPException as e:
        if "Wrong password" in str(e.detail):
            auth.record_failure(key)
        raise
    auth.clear_attempts(key)
    resp = JSONResponse({"ok": True})
    auth.start_session(resp, request, uid)
    return resp


@app.post("/auth/logout")
def logout(request: Request):
    resp = JSONResponse({"ok": True})
    auth.end_session(resp, request)
    return resp


@app.post("/auth/forgot")
def forgot(body: EmailBody, request: Request):
    auth.throttle("forgot:" + body.email.strip().lower(), limit=3, window=3600)
    auth.start_reset(body.email, auth.base_url(request))
    return {"ok": True}


@app.post("/auth/reset")
def reset(body: ResetBody, request: Request):
    uid = auth.finish_reset(body.token, body.password)
    resp = JSONResponse({"ok": True})
    auth.start_session(resp, request, uid)
    return resp


@app.get("/auth/google")
def google_start(request: Request):
    if not auth.google_enabled():
        return RedirectResponse("/login#error=Google+sign-in+isn't+set+up+yet", 302)
    resp = RedirectResponse("/", 302)
    resp.headers["location"] = auth.google_redirect(request, resp)
    return resp


@app.get("/auth/google/callback")
def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error or not code:
        return RedirectResponse("/login#error=Google+sign-in+was+cancelled", 302)
    try:
        uid = auth.google_finish(request, code, state)
    except HTTPException as e:
        return RedirectResponse(f"/login#error={e.detail.replace(' ', '+')}", 302)
    resp = RedirectResponse("/app", 302)
    resp.delete_cookie("hs_oauth_state", path="/")
    auth.start_session(resp, request, uid)
    return resp


# ---------------------------------------------------------------- account
@app.get("/api/me")
def me(u: dict | None = Maybe):
    if not u:
        return dict(guest=True, plan="guest", is_admin=False, ticker_limit=auth.FREE_TICKER_LIMIT)
    bot = telegram_bot.bot_username()
    return dict(email=u["email"], name=u["name"], plan="pro" if auth.is_pro(u) else "free", is_admin=bool(u["is_admin"]),
                ticker_limit=None if auth.is_pro(u) else auth.FREE_TICKER_LIMIT,
                telegram=dict(enabled=telegram_bot.enabled(), linked=bool(u["telegram_chat"]),
                              link=f"https://t.me/{bot}?start={u['link_code']}" if bot else None),
                phone=u["phone"], calls_available=phone.enabled(), has_password=bool(u["password_hash"]),
                google=bool(u["google_sub"]), styles=sorted(notify.styles_of(u)), brief=u.get("brief", 1) != 0,
                brief_time=brief.BRIEF_TIME, email_on=bool(os.getenv("RESEND_API_KEY")),
                styles_picked=[x for x in (u.get("styles") or "hub").split(",") if x],
                style_options=[dict(key=k, name=v[0], about=v[1]) for k, v in patterns.STYLES.items()])


class StylesBody(BaseModel):
    styles: list[str]


@app.get("/api/brief")
def brief_today(u: dict | None = Maybe):
    """Today's morning brief (the same one that goes out by Telegram and email)."""
    return brief.build(u)


class OnBody(BaseModel):
    on: bool


@app.post("/api/me/brief")
def set_brief(body: OnBody, u: dict = User):
    db.update_user(u["id"], brief=1 if body.on else 0)
    return {"brief": body.on}


@app.post("/api/brief/test")
def brief_test(request: Request, u: dict = User):
    auth.throttle(f"brief:{u['id']}", limit=3, window=3600)
    sent = brief.send_to(u)
    if not (sent["telegram"] or sent["email"]):
        raise HTTPException(400, "Connect Telegram (or ask the site owner to turn on email) to receive the brief.")
    return sent


@app.post("/api/me/styles")
def set_styles(body: StylesBody, u: dict = User):
    picked = [x for x in dict.fromkeys(body.styles) if x in patterns.STYLES]
    if not picked:
        raise HTTPException(400, "Pick at least one setup")
    db.update_user(u["id"], styles=",".join(picked))
    return {"styles": picked}


@app.post("/api/me/phone")
def set_phone(body: PhoneBody, u: dict = Pro):
    try:
        num = phone.clean_number(body.phone) if body.phone.strip() else None
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.update_user(u["id"], phone=num)
    return {"phone": num}


@app.delete("/api/me/telegram")
def unlink_telegram(u: dict = User):
    import secrets as _s
    db.update_user(u["id"], telegram_chat=None, link_code=_s.token_urlsafe(9))
    return {"ok": True}


@app.post("/api/testcall")
def test_call(u: dict = Pro):
    try:
        return {"sid": phone.call("Next Shelf test call. Phone alerts are working.", u["phone"])}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/admin/users")
def admin_users(u: dict = Admin):
    return [dict(id=x["id"], email=x["email"], name=x["name"], plan=x["plan"], is_admin=bool(x["is_admin"]),
                 created_at=x["created_at"], tickers=len(db.user_symbols(x["id"])), telegram=bool(x["telegram_chat"]))
            for x in db.all_users()]


@app.post("/api/admin/users/{uid}/plan")
def admin_set_plan(uid: int, body: PlanBody, u: dict = Admin):
    if body.plan not in ("free", "pro"):
        raise HTTPException(400, "plan must be free or pro")
    if not db.get_user(uid):
        raise HTTPException(404, "no such user")
    db.update_user(uid, plan=body.plan)
    return {"ok": True}


# ---------------------------------------------------------------- data
def storage_persistent() -> bool | None:
    """On Render, data survives deploys only on a mounted disk. None = can't tell (not on Render)."""
    if not os.getenv("RENDER"):
        return None
    p = db.DATA_DIR.resolve()
    return any(os.path.ismount(x) for x in [p, *p.parents] if str(x) != "/")


@app.get("/api/info")
def info(u: dict | None = Maybe):
    s = scanner.SETTINGS
    return dict(storage_ok=storage_persistent() is not False, universe_name=universe.describe(), universe_size=len(universe.board_universe()),
                provider=data.provider(), telegram=telegram_bot.enabled(), telegram_linked=bool(u and u["telegram_chat"]),
                phone=bool(u and u["phone"]) and phone.enabled() and auth.is_pro(u), call_on=sorted(phone.call_on()),
                scan_times=SCAN_TIMES, preclose_times=PRECLOSE_TIMES if auth.is_pro(u) else [],
                entry_times=ENTRY_TIMES, board_times=BOARD_TIMES,
                settings=dict(min_sessions=s.min_sessions, k=s.k, horizon=s.horizon, target_atr=s.target_atr,
                              shelf_min_atr=s.shelf_min_atr))


@app.get("/api/live")
def live_prices(symbols: str = ""):
    """Live prices for watching (refreshed every ~30s by the app during market hours). Signals still use 4H closes."""
    syms = [x.strip().upper() for x in symbols.split(",") if scanner.SYMBOL_RE.match(x.strip().upper())][:150]
    return dict(market_open=data.market_open(), as_of=pd.Timestamp.now(tz="UTC").isoformat(),
                prices=data.latest_prices(syms) if syms else {})


@app.get("/api/tickers")
def tickers(u: dict | None = Maybe):
    if not u:
        return []
    out = []
    for sym in db.user_symbols(u["id"]):
        row = db.get_ticker(sym) or {}
        out.append(dict(symbol=sym, scanned_at=row.get("scanned_at"), status=json.loads(row.get("status") or "{}")))
    return out


@app.post("/api/tickers")
async def add_ticker(body: AddBody, u: dict = User):
    try:
        return await run_in_threadpool(add_for_user, u, body.symbol)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e))


@app.delete("/api/tickers/{symbol}")
def remove_ticker(symbol: str, u: dict = User):
    if not db.unwatch(u["id"], symbol.upper()):
        raise HTTPException(404, "not on your watchlist")
    return {"ok": True}


@app.get("/api/alerts")
def alerts(limit: int = 50, u: dict | None = Maybe):
    if not u:
        return []
    hide = () if auth.is_pro(u) else tuple(notify.PRO_ONLY_TYPES)
    return db.alerts_for_symbols(db.user_symbols(u["id"]), min(limit, 200), hide)


@app.post("/api/scan")
async def scan_now(u: dict = Admin):
    sent = await run_in_threadpool(scanner.scan_all)
    return {"alerts": len(sent)}


_MY_SCAN_AT: dict[int, float] = {}


@app.post("/api/scan/mine")
async def scan_my_list(u: dict = User):
    """Scout, on demand: re-check every ticker on this member's list against the latest 4H candles."""
    import time as _t
    last = _MY_SCAN_AT.get(u["id"], 0)
    if _t.time() - last < 60:
        raise HTTPException(429, f"Scout just checked your list. Try again in {int(60 - (_t.time() - last))}s.")
    _MY_SCAN_AT[u["id"]] = _t.time()
    syms = db.user_symbols(u["id"])

    def run():
        out = []
        with scanner._scan_lock:
            for sym in syms:
                try:
                    st, _ = scanner.scan_symbol(sym)
                    out.append(dict(symbol=sym, state=st.get("state"), label=st.get("label")))
                except Exception as e:
                    out.append(dict(symbol=sym, error=str(e)))
        return out
    return {"results": await run_in_threadpool(run), "at": pd.Timestamp.now(tz="UTC").isoformat()}


@app.post("/api/precheck")
async def precheck_now(u: dict = Admin):
    sent = await run_in_threadpool(scanner.preclose_all)
    return {"alerts": len(sent)}


@app.get("/api/board")
def board_data(u: dict | None = Maybe):
    b = board.board()
    mine = set(db.user_symbols(u["id"])) if u else set()
    for group in ("live", "near", "forming", "recent"):
        for r in b[group]:
            r["watching"] = r["symbol"] in mine
    if not auth.is_pro(u):                  # free: the timely groups are counts only
        b["locked"] = True
        b["live_count"], b["near_count"] = len(b["live"]), len(b["near"])
        b["live"], b["near"] = [], []
        b["setups_locked"] = sum(1 for x in b["setups"] if x["state"] in ("breakout_up", "breakdown", "testing"))
        b["setups"] = [x for x in b["setups"] if x["state"] not in ("breakout_up", "breakdown", "testing")]
    for x in b["setups"]:
        x["watching"] = x["symbol"] in mine
    return b


@app.post("/api/board/scan")
async def board_scan_now(u: dict = Admin):
    if board.SCAN["running"]:
        return {"started": False, "running": True}
    threading.Thread(target=board.scan_board, daemon=True).start()   # the scanner agent shows progress
    return {"started": True}


def _next_sweep() -> str | None:
    """When the next scheduled market sweep runs (weekdays, BOARD_TIMES in New York time)."""
    now = pd.Timestamp.now(tz=engine.NY)
    for d in range(8):
        day = (now + pd.Timedelta(days=d)).normalize()
        if day.weekday() >= 5:
            continue
        for t in sorted(BOARD_TIMES):
            h, m = map(int, t.split(":"))
            when = day + pd.Timedelta(hours=h, minutes=m)
            if when > now:
                return when.isoformat()
    return None


@app.get("/api/scanner")
def scanner_status(request: Request):
    """Live state of the market-scanning agent shown on Discover."""
    u = auth.current_user(request)
    full = bool(u and (u.get("is_admin") or u.get("plan") == "pro"))
    sc = dict(board.SCAN)
    found = sc.pop("found", [])
    sc["found_count"] = len(found)
    sc["found"] = found[-12:] if full else []       # names of fresh finds are a Pro feature, like the board
    sc["next"] = _next_sweep()
    return sc


@app.get("/api/record")
def record_data(pattern: str = "hub", u: dict = User):
    if pattern not in patterns.KINDS:
        raise HTTPException(400, "unknown setup")
    return board.track_record(pattern)


@app.get("/api/options/{symbol}")
async def options_plan(symbol: str, u: dict = Pro):
    """Educational options swing plan for the current setup (Pro)."""
    try:
        return await run_in_threadpool(options.plan, symbol)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/chart/{symbol}")
async def chart_data(symbol: str, u: dict | None = Maybe):
    try:
        d = await run_in_threadpool(board.chart, symbol, bool(u and u["is_admin"]))
    except Exception as e:
        raise HTTPException(400, str(e))
    d["watching"] = bool(u) and d["symbol"] in db.user_symbols(u["id"])
    d["guest"] = not u
    return d


@app.get("/api/admin/lab")
def admin_lab(u: dict = Admin):
    return board.lab()


@app.get("/api/backtest/{symbol}")
async def backtest(symbol: str, u: dict = Pro):
    try:
        return await run_in_threadpool(scanner.backtest, symbol)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/paper")
async def paper_summary(u: dict = Admin):
    return await run_in_threadpool(paper.summary)


@app.post("/api/paper/toggle")
def paper_toggle(body: ToggleBody, u: dict = Admin):
    paper.set_enabled(body.on)
    return {"enabled": paper.enabled()}


# ---------------------------------------------------------------- email list (waitlist)
class SubscribeBody(BaseModel):
    email: str
    source: str = "landing"
    website: str = ""          # honeypot: real people leave it empty


def _welcome_html(unsub: str) -> str:
    return f"""<div style="font-family:Inter,Arial,sans-serif;max-width:520px;margin:auto;color:#111">
<h2 style="margin:0 0 12px">You're on the NextShelf list</h2>
<p>Thanks for signing up. NextShelf finds stocks resting in tight ranges ("hubs"), warns you before a breakout candle
closes, and targets the next shelf.</p>
<p>We'll email you when new features launch and when the full version opens. No spam, and you can leave anytime.</p>
<p style="color:#777;font-size:12px;margin-top:28px">NextShelf is an educational tool, not financial advice.<br>
{_mailing_address()}<a href="{unsub}" style="color:#777">Unsubscribe</a></p></div>"""


def _mailing_address() -> str:
    """Sender's postal address for the email footer (required by CAN-SPAM). Set MAILING_ADDRESS in the server settings."""
    import html
    addr = os.getenv("MAILING_ADDRESS", "").strip()
    return f"NextShelf · {html.escape(addr)}<br>" if addr else ""


@app.post("/api/subscribe")
def subscribe(body: SubscribeBody, request: Request):
    auth.throttle("sub:" + auth.client_ip(request), limit=6, window=3600)
    if body.website:                       # bot filled the hidden field: pretend it worked
        return {"ok": True, "status": "new"}
    email = auth.clean_email(body.email)
    token = secrets.token_urlsafe(18)
    status = db.add_subscriber(email, body.source[:40] or "landing", token)
    if status in ("new", "again"):
        unsub = f"{auth.base_url(request)}/unsubscribe?t={db.subscriber_token(email)}"
        try:
            auth.send_email(email, "You're on the NextShelf list", _welcome_html(unsub))
        except Exception as e:
            log.warning("welcome email failed: %s", e)
    return {"ok": True, "status": status}


@app.get("/unsubscribe")
def unsubscribe_page(t: str = ""):
    email = db.unsubscribe(t) if t else None
    msg = (f"<b>{email}</b> has been removed from the NextShelf email list." if email
           else "That unsubscribe link isn't valid. It may have been used already.")
    return HTMLResponse(f"""<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Unsubscribed · NextShelf</title><link rel="icon" href="/favicon.svg"></head>
<body style="font-family:Inter,-apple-system,sans-serif;background:#0B0B0D;color:#F4F4F6;display:grid;place-items:center;min-height:100vh;margin:0">
<div style="max-width:440px;padding:24px;text-align:center"><h1 style="font-size:26px">You're unsubscribed</h1>
<p style="color:#A1A1AA">{msg}</p><p><a href="/" style="color:#7AA2FF">Back to NextShelf</a></p></div></body></html>""")


@app.get("/api/admin/subscribers")
def admin_subscribers(u: dict = Admin):
    subs = db.subscribers()
    return dict(total=sum(s_["status"] == "subscribed" for s_ in subs), unsubscribed=sum(s_["status"] != "subscribed" for s_ in subs),
                recent=subs[:50])


@app.get("/api/admin/subscribers.csv")
def admin_subscribers_csv(u: dict = Admin):
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["email", "status", "source", "signed_up", "unsubscribed"])
    for s_ in db.subscribers():
        w.writerow([s_["email"], s_["status"], s_["source"], s_["created_at"], s_["unsubscribed_at"] or ""])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="nextshelf-email-list.csv"'})
