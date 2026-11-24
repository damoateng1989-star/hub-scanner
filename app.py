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
import requests  # noqa: E402
import secrets  # noqa: E402
import threading  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from pathlib import Path  # noqa: E402

from apscheduler.schedulers.background import BackgroundScheduler  # noqa: E402
from apscheduler.triggers.cron import CronTrigger  # noqa: E402
from fastapi import Depends, FastAPI, HTTPException, Request  # noqa: E402
from fastapi.concurrency import run_in_threadpool  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import pandas as pd  # noqa: E402

import auth  # noqa: E402
import engine  # noqa: E402
import legal  # noqa: E402
import learn  # noqa: E402
import options  # noqa: E402
import patterns  # noqa: E402
import board  # noqa: E402
import gamma  # noqa: E402
import push  # noqa: E402
import stream  # noqa: E402
import brief  # noqa: E402
import recap  # noqa: E402
import data  # noqa: E402
import earnings  # noqa: E402
import db  # noqa: E402
import notify  # noqa: E402
import paper  # noqa: E402
import paper_opt  # noqa: E402
import phone  # noqa: E402
import scanner  # noqa: E402
import snaptrade  # noqa: E402
import swing_card  # noqa: E402
import sika_chat  # noqa: E402
import sika_find  # noqa: E402
import flow_signal  # noqa: E402
import telegram_bot  # noqa: E402
import universe  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("app")

# Scan a few minutes after each 4H close (13:30 and 16:00 ET). The delay covers the
# free Alpaca plan's 15-minute delay on full-market (SIP) data.
SCAN_TIMES = [t.strip() for t in os.getenv("SCAN_TIMES", "13:47,16:17").split(",") if t.strip()]
# Premarket gap-onto-a-shelf alert, weekdays before the open. Empty = off.
PREMARKET_TIME = os.getenv("PREMARKET_TIME", "09:00").strip()
# Pre-close warning ~15 minutes before each 4H close (uses Alpaca's free real-time IEX price). Empty = off.
EARLY_TIMES = [t.strip() for t in os.getenv("EARLY_TIMES", "13:31").split(",") if t.strip()]   # early read after the 1:30 PM close
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
            text += "\n/backtest NVDA: past breakout results\n/testcall: test phone-call alerts\n/testsms: test text-message alerts"
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
    if cmd == "/testsms":
        if not pro:
            return "Phone alerts are a Pro feature."
        phone.text("NextShelf test text. SMS alerts are working.", u["phone"])
        return "Texting you now."
    if cmd in ("/scan", "/precheck") and admin:
        return scanner.handle_command(cmd, args)
    if cmd == "/paper" and admin:
        if args and args[0].lower() in ("on", "off"):
            paper.set_enabled(args[0].lower() == "on")
        return paper.summary_text()
    return "Unknown command. /help for the list."


def _snapshot_gex_watchlist():
    """Five-minute, watchlist-only GEX capture. 09:35 through the 16:00 close = 78 slots."""
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _ZoneInfo
    now = _dt.now(_ZoneInfo("America/New_York"))
    minute = now.hour * 60 + now.minute
    if now.weekday() >= 5 or minute < 9 * 60 + 35 or minute > 16 * 60:
        return
    symbols = [r["symbol"] for r in db.list_tickers()]
    if not symbols:
        return
    spots = data.latest_prices(symbols)
    result = gamma.snapshot_watchlist(symbols, spots)
    log.info("GEX snapshots: %s saved, %s skipped", result.get("saved", 0), result.get("skipped", 0))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:      # a Lab test cut off by a restart is marked stopped, not resumed (it used to resume itself and OOM-loop)
        import lab_exits
        lab_exits.resume_if_interrupted()
    except Exception as e:
        log.warning("lab resume failed: %s", e)
    sched = BackgroundScheduler(timezone="America/New_York")
    # Patch 208: Lookout — the kill and the clock on each 4H close, 3 minutes after that close's scan; earnings eve
    import lookout as _lookout
    for t in SCAN_TIMES:
        h, m = (int(x) for x in t.split(":"))
        m += 3; h += m // 60; m %= 60
        sched.add_job(_lookout.evaluate_closes, CronTrigger(day_of_week="mon-fri", hour=h, minute=m, timezone="America/New_York"),
                      misfire_grace_time=1800, coalesce=True, max_instances=1)
    import backup as _backup   # Nov 23: nightly database backup (local snapshots + off-server when a bucket is set)
    sched.add_job(_backup.run, CronTrigger(hour=2, minute=17, timezone="America/New_York"), id="db_backup", replace_existing=True,
                  misfire_grace_time=3600)
    sched.add_job(_lookout.evaluate_earnings_eve, CronTrigger(day_of_week="mon-fri", hour=16, minute=25, timezone="America/New_York"),
                  misfire_grace_time=1800, coalesce=True, max_instances=1)
    for t in SCAN_TIMES:
        h, m = t.split(":")
        sched.add_job(scanner.scan_all, CronTrigger(day_of_week="mon-fri", hour=int(h), minute=int(m),
                                                    timezone="America/New_York"),
                      misfire_grace_time=1800, coalesce=True, max_instances=1)
    if PREMARKET_TIME:
        h, m = PREMARKET_TIME.split(":")
        sched.add_job(scanner.premarket_all, CronTrigger(day_of_week="mon-fri", hour=int(h), minute=int(m),
                                                         timezone="America/New_York"),
                      misfire_grace_time=900, coalesce=True, max_instances=1)
    for t in EARLY_TIMES:
        h, m = t.split(":")
        sched.add_job(scanner.early_all, CronTrigger(day_of_week="mon-fri", hour=int(h), minute=int(m),
                                                     timezone="America/New_York"),
                      misfire_grace_time=240, coalesce=True, max_instances=1)
    # live price crosses a trigger or neckline: checked every minute in market hours (one alert per stock per 4H candle)
    sched.add_job(scanner.trigger_all, CronTrigger(day_of_week="mon-fri", hour="9-15", minute=f"*/{max(1, int(os.getenv('TRIGGER_EVERY_MIN', 1)))}", timezone="America/New_York"),
                  misfire_grace_time=120, coalesce=True, max_instances=1, id="trigger_alerts", replace_existing=True)
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
    # paper OPTIONS experiment (off unless switched on): same setups, same windows, its own journal
    # Nov 23 (owner): one window, 10:30 AM ET, A+ setups only (hub A retests with a clear path)
    for h_, m_, w_ in ((10, 30, "1030"),):
        sched.add_job(paper_opt.enter_daily_opt, CronTrigger(day_of_week="mon-fri", hour=h_, minute=m_, timezone="America/New_York"),
                      kwargs={"window": w_}, misfire_grace_time=900, coalesce=True, max_instances=1, id=f"paper_opt_{w_}", replace_existing=True)
    for old_ in ("paper_opt_morning", "paper_opt_midday"):
        try:
            sched.remove_job(old_)
        except Exception:
            pass
    sched.add_job(paper_opt.sync_opt, CronTrigger(day_of_week="mon-fri", hour="9-16", minute="*/5", timezone="America/New_York"),
                  coalesce=True, max_instances=1, id="paper_opt_sync", replace_existing=True)
    try:   # Nov 23: the futures opening range forward test (owner-only alerts; needs MASSIVE_API_KEY)
        import futures_orb
        sched.add_job(futures_orb.tick, CronTrigger(day_of_week="mon-fri", hour="10-16", minute="2,12,32,42", timezone="America/New_York"),
                      coalesce=True, max_instances=1, id="futures_orb", replace_existing=True)
    except Exception as e:
        log.warning("futures orb job not scheduled: %s", e)
    for t in ENTRY_TIMES:
        h, m = t.split(":")
        sched.add_job(paper_opt.run_check_opt, CronTrigger(day_of_week="mon-fri", hour=int(h), minute=int(m), timezone="America/New_York"),
                      misfire_grace_time=60, coalesce=True, max_instances=1, id=f"paper_opt_check_{t}", replace_existing=True)
    # paper trading: one confirmed breakout a day from the whole board
    # Nov 24 (owner): the morning entry moves from 9:35 to 10:30 AM (same candle: yesterday's 4:00 PM close)
    sched.add_job(paper.enter_daily, CronTrigger(day_of_week="mon-fri", hour=10, minute=30, timezone="America/New_York"),
                  args=["morning"], misfire_grace_time=600, coalesce=True, max_instances=1, id="paper_morning", replace_existing=True)
    sched.add_job(paper.enter_daily, CronTrigger(day_of_week="mon-fri", hour=13, minute=58, timezone="America/New_York"),
                  args=["midday"], misfire_grace_time=900, coalesce=True, max_instances=1, id="paper_midday", replace_existing=True)
    sched.add_job(earnings.refresh, CronTrigger(hour="6,12,18", minute=5, timezone="America/New_York"),
                  kwargs={"force": True}, id="earnings", replace_existing=True)
    sched.add_job(earnings.refresh_past, CronTrigger(hour=6, minute=10, timezone="America/New_York"),
                  kwargs={"force": True}, id="earnings_past", replace_existing=True)
    if brief.BRIEF_TIME:      # morning brief, weekdays before the open
        bh, bm = map(int, brief.BRIEF_TIME.split(":"))
        sched.add_job(brief.send_all, CronTrigger(day_of_week="mon-fri", hour=bh, minute=bm, timezone="America/New_York"),
                      id="brief", replace_existing=True)
    import nextday
    if nextday.NEXTDAY_TIME:  # next-day check: how yesterday's confirmed setups did today
        nh, nm = map(int, nextday.NEXTDAY_TIME.split(":"))
        sched.add_job(nextday.run, CronTrigger(day_of_week="mon-fri", hour=nh, minute=nm, timezone="America/New_York"),
                      id="nextday", replace_existing=True)
    if recap.RECAP_TIME:      # evening recap, weekdays after the close
        rh, rm = map(int, recap.RECAP_TIME.split(":"))
        sched.add_job(recap.send_all, CronTrigger(day_of_week="mon-fri", hour=rh, minute=rm, timezone="America/New_York"),
                      id="recap", replace_existing=True)
    for t in BOARD_TIMES:
        h, m = t.split(":")
        sched.add_job(board.scan_board, CronTrigger(day_of_week="mon-fri", hour=int(h), minute=int(m),
                                                    timezone="America/New_York"),
                      misfire_grace_time=1800, coalesce=True, max_instances=1)
    if BOARD_TIMES and not db.board_rows():      # first start: fill the board shortly after the site is up
        threading.Timer(20, board.scan_board).start()
    # Patch 174: post-close flow classification — the evening's hub breakouts get their tape
    # classification stored in flow_signal. Internal build: a no-op unless FLOW_TAPE_PROVIDER
    # is configured, and it writes data only — nothing member-facing reads the table without
    # the visibility gate (swing_card.flow_allowed).
    sched.add_job(flow_signal.classify_recent, CronTrigger(day_of_week="mon-fri", hour=18, minute=30,
                                                           timezone="America/New_York"),
                  misfire_grace_time=1800, coalesce=True, max_instances=1, id="flow_classify", replace_existing=True)
    # Patch 139: compact Cboe GEX snapshots for the shared watchlist only (78 slots/day).
    sched.add_job(_snapshot_gex_watchlist, CronTrigger(day_of_week="mon-fri", hour="9-16", minute="*/5",
                                                      timezone="America/New_York"),
                  misfire_grace_time=120, coalesce=True, max_instances=1, id="gex_watchlist_snapshots", replace_existing=True)
    sched.add_job(paper.sync, CronTrigger(day_of_week="mon-fri", hour="9-16", minute="*/5",
                                          timezone="America/New_York"),
                  misfire_grace_time=120, coalesce=True, max_instances=1)
    sched.start()
    # Patch 102: rebuild the in-memory chart cache after every restart, off the request path.
    _chart_warm = threading.Timer(30, board.warm_charts)
    _chart_warm.daemon = True
    _chart_warm.start()
    log.info("Scheduler on: scans %s, pre-close %s ET (provider: %s)",
             ", ".join(SCAN_TIMES), ", ".join(PRECLOSE_TIMES) or "off", data.provider())
    telegram_bot.start_polling(handle_command)
    yield
    sched.shutdown(wait=False)


app = FastAPI(title="NextShelf", lifespan=lifespan)
from fastapi.middleware.gzip import GZipMiddleware  # noqa: E402
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=6)   # pages and data ~4x smaller over the wire
User = Depends(auth.require_user)
Maybe = Depends(auth.current_user)        # signed in or a visitor (None)
Pro = Depends(auth.require_pro)
Admin = Depends(auth.require_admin)


# Nov 23 (owner): broker LINKING for members the owner switches on (testing). Read-only: they connect Webull to see
# positions and sync their Trade Diary. Placing, previewing and cancelling orders stay Admin-only (lawyer review first).
def broker_testers() -> set[int]:
    import json as _json
    try:
        return {int(x) for x in _json.loads(db.get_setting("broker_testers") or "[]")}
    except (ValueError, TypeError):
        return set()


def broker_link_ok(u: dict | None) -> bool:
    return bool(u) and not u.get("guest") and (bool(u.get("is_admin")) or int(u["id"]) in broker_testers())


def _require_broker_user(request: Request) -> dict:
    u = auth.require_user(request)
    if not broker_link_ok(u):
        raise HTTPException(403, "Linking a brokerage isn't open for your account yet.")
    return u


BrokerUser = Depends(_require_broker_user)


class AddBody(BaseModel):
    symbol: str


class SikaChatBody(BaseModel):
    symbol: str
    message: str
    history: list[dict] | None = None
    plain_english: str = ""


class SikaFindBody(BaseModel):
    kind: str
    limit: int = 8      # "show me all" asks for the whole list (capped at 60)


class FlowAggregatesBody(BaseModel):
    rows: list[dict] = []


class SignupBody(BaseModel):
    email: str
    password: str
    name: str = ""
    invite: str = ""


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


@app.get("/logo-mark-{theme}.png")
def logo_mark(theme: str):
    """Patch 203: the header mark, one per theme (white climb on dark, near-black climb on light)."""
    if theme not in ("dark", "light"):
        raise HTTPException(404)
    return FileResponse(STATIC / f"logo-mark-{theme}.png", media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/og-card.png")
def og_card():
    """Patch 200: the link-preview card (Open Graph / Twitter), 1200x630."""
    return FileResponse(STATIC / "og-card.png", media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})


@app.get("/apple-touch-icon.png")
def touch_icon():
    return FileResponse(STATIC / "apple-touch-icon.png", media_type="image/png")


@app.get("/fonts/{name}")
def fonts(name: str):
    """Self-hosted fonts (SIL Open Font License): sharp, fast, no third-party font requests."""
    if name not in ("Geist.woff2", "GeistMono.woff2", "Newsreader.woff2", "NewsreaderItalic.woff2", "DMSans.woff2", "Inter.woff2", "InstrumentSerif.woff2", "InstrumentSerifItalic.woff2", "JetBrainsMono.woff2", "Manrope.woff2", "ManropeLogo-800.woff2", "ManropeLogo-700.woff2", "DSEG7.woff2", "OFL-DSEG.txt",
                    "OFL.txt", "OFL-Manrope.txt", "OFL-Newsreader.txt", "OFL-DMSans.txt", "OFL-Inter.txt", "OFL-InstrumentSerif.txt", "OFL-JetBrainsMono.txt"):
        raise HTTPException(404)
    return FileResponse(STATIC / "fonts" / name, media_type="font/woff2" if name.endswith("woff2") else "text/plain",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


# Android app (Nov 22 2026): a Trusted Web Activity opens this site full screen only if this file vouches for the
# app's signing key. The committed debug key (android/app/debug.keystore) is always listed, so test builds verify.
# For Google Play, add the SHA-256 fingerprints from Play Console → App integrity (the app signing key, and the
# upload key) to the ANDROID_CERT_SHA256 env var on Render, comma-separated. No code change needed.
ANDROID_PACKAGE = "com.nextshelf.app"
ANDROID_DEBUG_SHA256 = "20:C3:43:FE:66:4A:26:60:FE:22:A4:15:8D:33:27:CB:48:15:2A:31:FC:6D:BE:A1:12:6D:D5:53:58:B6:ED:C4"


@app.get("/.well-known/assetlinks.json")
def assetlinks():
    extra = [x.strip().upper() for x in os.getenv("ANDROID_CERT_SHA256", "").split(",") if x.strip()]
    prints = [ANDROID_DEBUG_SHA256] + [x for x in extra if x != ANDROID_DEBUG_SHA256]
    return JSONResponse([{"relation": ["delegate_permission/common.handle_all_urls"],
                          "target": {"namespace": "android_app", "package_name": ANDROID_PACKAGE,
                                     "sha256_cert_fingerprints": prints}}],
                        headers={"Cache-Control": "public, max-age=3600"})


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    """At the site root so it can control every page; never cached so updates roll out right away."""
    return FileResponse(STATIC / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/shots/{name}.webp")
def landing_shot(name: str):
    """Real app screenshots for the landing page strip (Nov 22, the owner's phone captures)."""
    if name not in ("board", "chart", "sika", "cinema"):
        raise HTTPException(404)
    return FileResponse(STATIC / "shots" / f"{name}.webp", media_type="image/webp",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/icon-{name}.png")
def pwa_icon(name: str):
    if name not in ("192", "512", "maskable-512"):
        raise HTTPException(404)
    return FileResponse(STATIC / f"icon-{name}.png", media_type="image/png",
                        headers={"Cache-Control": "public, max-age=604800"})


@app.get("/vendor/lightweight-charts.js")
def lwc():
    return FileResponse(STATIC / "vendor" / "lightweight-charts.js", media_type="application/javascript")


@app.get("/vendor/lightweight-charts-5.js")
def lwc5():
    """Lightweight Charts v5.3.0, self-hosted and pinned (the app's setup map). Was loaded from unpkg, unpinned."""
    return FileResponse(STATIC / "vendor" / "lightweight-charts-5.js", media_type="application/javascript",
                        headers={"Cache-Control": "public, max-age=604800"})


_BROKER_LOGOS = {"robinhood.svg": "image/svg+xml", "webull-mark.png": "image/png"}


@app.get("/vendor/{name}")
def broker_logo(name: str):
    """Broker marks for the Open-in links (whitelisted names only)."""
    f = STATIC / "vendor" / name
    if name not in _BROKER_LOGOS or not f.exists():
        raise HTTPException(404)
    return FileResponse(f, media_type=_BROKER_LOGOS[name], headers={"Cache-Control": "public, max-age=604800"})


# ---------------------------------------------------------------- pages (sign-in required)
@app.get("/login")
def login_page(request: Request):
    if auth.current_user(request):
        return RedirectResponse("/app", 302)
    return FileResponse(STATIC / "login.html")


@app.get("/signin")
def signin_redirect():
    return RedirectResponse("/login", 302)


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


@app.get("/week_events.json")
def week_events():
    """Public market calendar data used by the This Week card on Today."""
    return FileResponse(STATIC / "week_events.json", media_type="application/json")


@app.get("/app")
def index(request: Request):
    """The app. Members only: visitors who aren't signed in go to the sign-in page."""
    if not auth.current_user(request):
        return RedirectResponse("/login", 302)
    resp = FileResponse(STATIC / "index.html")
    if auth.current_user(request):
        resp.set_cookie(auth.COOKIE, request.cookies[auth.COOKIE], max_age=auth.SESSION_DAYS * 86400, httponly=True,
                        samesite="lax", secure=auth._secure(request), path="/")
    return resp


@app.get("/board")
def public_board():
    """Public Hub Board: counts only, no sign-in. Tickers stay members-only."""
    return FileResponse(STATIC / "board.html")


@app.get("/pricing")
def pricing_page():
    """No standalone pricing page: the pricing section lives on the homepage."""
    return RedirectResponse("/#pricing", 302)


@app.get("/faq")
def faq_page():
    """No standalone FAQ page: the FAQ lives on the homepage."""
    return RedirectResponse("/#faq", 302)


@app.get("/t/{symbol}")
def ticker_page(symbol: str, request: Request):
    if not auth.current_user(request):
        return RedirectResponse("/login", 302)
    return FileResponse(STATIC / "ticker.html")


# ---------------------------------------------------------------- trust pages: terms, privacy, risk disclaimer, contact
@app.get("/learn", response_class=HTMLResponse)
def learn_index():
    return learn.render()


@app.get("/learn/{slug}", response_class=HTMLResponse)
def learn_lesson(slug: str):
    page = learn.render(slug)
    if page is None:
        return RedirectResponse("/learn", 302)
    return page


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
    return {"google": auth.google_enabled(), "free_limit": auth.FREE_TICKER_LIMIT, "signups_open": auth.signups_open()}


@app.get("/auth/invite/{code}")
def invite_check(code: str, request: Request):
    """Lets the sign-up page tell someone their code works before they fill in the form."""
    auth.throttle("invite:" + auth.client_ip(request), limit=30, window=3600)
    return {"ok": auth.signups_open() or db.invite_ok(code)}


@app.post("/auth/signup")
def signup(body: SignupBody, request: Request):
    auth.throttle("signup:" + auth.client_ip(request), limit=10, window=3600)
    uid = auth.signup(body.email, body.password, body.name, body.invite)
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
def google_start(request: Request, invite: str = ""):
    if not auth.google_enabled():
        return RedirectResponse("/login#error=Google+sign-in+isn't+set+up+yet", 302)
    resp = RedirectResponse("/", 302)
    resp.headers["location"] = auth.google_redirect(request, resp)
    if invite:      # carried through Google's round trip so a new account can use it
        resp.set_cookie("hs_invite", invite.strip().upper()[:40], max_age=900, httponly=True, samesite="lax",
                        secure=auth._secure(request), path="/")
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
    resp.delete_cookie("hs_invite", path="/")
    auth.start_session(resp, request, uid)
    return resp


# ---------------------------------------------------------------- billing (Stripe, Nov 22: Pro $29/mo, 7-day trial)
import billing  # noqa: E402


@app.get("/api/billing")
def billing_status(u: dict = User):
    return billing.status(u)


@app.post("/api/billing/checkout")
def billing_checkout(u: dict = User):
    if not billing.enabled():
        raise HTTPException(503, "Payments aren't switched on yet")
    if auth.is_pro(u) and (u.get("sub_status") in billing.PRO_STATUSES or not u.get("stripe_customer")):
        raise HTTPException(409, "You're already on Pro")
    try:
        return dict(url=billing.checkout_url(u))
    except Exception as ex:
        raise HTTPException(502, f"Couldn't open checkout: {ex}")


@app.post("/api/billing/portal")
def billing_portal(u: dict = User):
    if not billing.enabled():
        raise HTTPException(503, "Payments aren't switched on yet")
    try:
        return dict(url=billing.portal_url(u))
    except Exception as ex:
        raise HTTPException(400, str(ex))


@app.post("/api/billing/webhook")
async def billing_webhook(request: Request):
    payload = await request.body()
    try:
        event = billing.verify(payload, request.headers.get("stripe-signature", ""))
    except ValueError as ex:
        log.warning("stripe webhook rejected: %s", ex)
        raise HTTPException(400, "bad signature")
    try:
        return dict(ok=True, result=billing.handle(event))
    except Exception:
        log.exception("stripe webhook %s failed", event.get("type"))
        raise HTTPException(500, "webhook failed")   # Stripe retries


# ---------------------------------------------------------------- company names
@app.get("/api/names")
def company_names():
    """Ticker -> company name for the scanned universe (static file; safe to cache)."""
    return JSONResponse(universe.NAMES, headers={"Cache-Control": "public, max-age=86400"})


# ---------------------------------------------------------------- account
@app.get("/api/me")
def me(u: dict | None = Maybe):
    if not u:
        return dict(guest=True, plan="guest", is_admin=False, ticker_limit=auth.FREE_TICKER_LIMIT)
    bot = telegram_bot.bot_username()
    return dict(email=u["email"], name=u["name"], plan="pro" if auth.is_pro(u) else "free", is_admin=bool(u["is_admin"]),
                broker_link=broker_link_ok(u), broker_trade=bool(u["is_admin"]),
                ticker_limit=None if auth.is_pro(u) else auth.FREE_TICKER_LIMIT,
                telegram=dict(enabled=telegram_bot.enabled(), linked=bool(u["telegram_chat"]),
                              link=f"https://t.me/{bot}?start={u['link_code']}" if bot else None),
                phone=u["phone"], calls_available=phone.enabled(), has_password=bool(u["password_hash"]),
                google=bool(u["google_sub"]), styles=sorted(notify.styles_of(u)), options=options_for(u), brief=u.get("brief", 1) != 0, recap=u.get("recap", 1) != 0, recap_time=recap.RECAP_TIME, a_all=bool(u.get("a_all")), board_rt=(u.get("board_rt") or "off") if (u.get("board_rt") != "all" or u.get("is_admin")) else "aplus", quiet=u.get("quiet") or "", closes=u.get("closes") or "both",
                brief_time=brief.BRIEF_TIME, email_on=bool(os.getenv("RESEND_API_KEY")),
                styles_picked=[x for x in (u.get("styles") or "hub").split(",") if x],
                style_options=[dict(key=k, name=v[0], about=v[1]) for k, v in patterns.STYLES.items() if k in patterns.LIVE_STYLES])


class StylesBody(BaseModel):
    styles: list[str]


@app.get("/api/brief")
def brief_today(u: dict = User):
    """Today's morning brief (the same one that goes out by Telegram and email)."""
    return brief.build(u)


class OnBody(BaseModel):
    on: bool


@app.get("/api/recap")
def recap_today(u: dict = User):
    """Today's evening recap (the same one that goes out by Telegram and email after the close)."""
    return recap.build(u)


@app.post("/api/me/brief")
def set_brief(body: OnBody, u: dict = User):
    db.update_user(u["id"], brief=1 if body.on else 0)
    return {"brief": body.on}


class PriceAlertBody(BaseModel):
    symbol: str
    price: float
    direction: int          # 1: alert when price reaches it from below, -1: from above
    label: str = "price"    # trigger | stop | target | price


@app.post("/api/alerts/price")
def add_price_alert(body: PriceAlertBody, u: dict = User):
    sym = scanner.clean_symbol(body.symbol)
    if body.price <= 0 or body.direction not in (1, -1) or body.label not in ("trigger", "stop", "target", "entry", "price"):
        raise HTTPException(400, "Bad alert")
    if len(db.price_alerts_for(u["id"])) >= 60:
        raise HTTPException(400, "You have 60 price alerts already. Remove a few first.")
    aid = db.add_price_alert(u["id"], sym, round(body.price, 2), body.direction, body.label)
    return {"id": aid}


@app.get("/api/alerts/price")
def list_price_alerts(symbol: str | None = None, u: dict = User):
    return {"alerts": db.price_alerts_for(u["id"], scanner.clean_symbol(symbol) if symbol else None)}


@app.delete("/api/alerts/price/{aid}")
def remove_price_alert(aid: int, u: dict = User):
    db.delete_price_alert(u["id"], aid)
    return {"ok": True}


class BoardRtBody(BaseModel):
    mode: str = "off"


@app.post("/api/me/board_rt")
def set_board_rt(body: BoardRtBody, u: dict = User):
    """Board retest alerts (Nov 24): off, A+ only, or every retest (admin only — the rest aren't tested setups)."""
    mode = body.mode if body.mode in ("off", "aplus", "all") else "off"
    if mode == "all" and not u.get("is_admin"):
        mode = "aplus"
    db.update_user(u["id"], board_rt=mode)
    return {"board_rt": mode}


@app.post("/api/me/every_a")
def set_every_a(body: OnBody, u: dict = User):
    db.update_user(u["id"], a_all=1 if body.on else 0)
    return {"a_all": body.on}


@app.post("/api/me/recap")
def set_recap(body: OnBody, u: dict = User):
    db.update_user(u["id"], recap=1 if body.on else 0)
    return {"recap": body.on}


class ClosesBody(BaseModel):
    closes: str


@app.post("/api/me/closes")
def set_closes(body: ClosesBody, u: dict = User):
    if body.closes not in ("both", "midday"):
        raise HTTPException(400, "Pick both closes or only the 1:30 PM close")
    db.update_user(u["id"], closes=None if body.closes == "both" else "midday")
    return {"closes": body.closes}


class PushSubBody(BaseModel):
    endpoint: str
    keys: dict
    device: str = ""


class PushEndBody(BaseModel):
    endpoint: str


@app.get("/api/push/key")
def push_key(u: dict = User):
    k = push.keys()
    if not k:
        raise HTTPException(503, "Notifications aren't available on the server yet")
    return {"key": k["public"], "devices": len(db.push_subs(u["id"]))}


@app.post("/api/push/subscribe")
def push_subscribe(body: PushSubBody, u: dict = User):
    p256dh, auth_ = (body.keys or {}).get("p256dh"), (body.keys or {}).get("auth")
    if not body.endpoint.startswith("https://") or not p256dh or not auth_:
        raise HTTPException(400, "That subscription doesn't look right")
    db.add_push_sub(u["id"], body.endpoint, p256dh, auth_, body.device)
    return {"ok": True, "devices": len(db.push_subs(u["id"]))}


@app.post("/api/push/unsubscribe")
def push_unsubscribe(body: PushEndBody, u: dict = User):
    db.del_push_sub(body.endpoint, u["id"])
    return {"ok": True, "devices": len(db.push_subs(u["id"]))}


@app.post("/api/push/test")
async def push_test(u: dict = User):
    n = await run_in_threadpool(push.send, u["id"], "🔔 NextShelf alerts are on",
                                "This is how a breakout alert will arrive on this device.", "/app#today", "test")
    if not n:
        raise HTTPException(400, "Couldn't reach this device. Turn alerts off and on again.")
    return {"sent": n}


class QuietBody(BaseModel):
    on: bool
    start: str = "09:30"
    end: str = "16:00"


@app.post("/api/me/quiet")
def set_quiet(body: QuietBody, u: dict = User):
    import re as _re
    ok = lambda t: bool(_re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t))
    if body.on and not (ok(body.start) and ok(body.end) and body.start != body.end):
        raise HTTPException(400, "Pick a start and end time, like 09:30 and 16:00")
    db.update_user(u["id"], quiet=f"{body.start}-{body.end}" if body.on else None)
    return {"quiet": f"{body.start}-{body.end}" if body.on else ""}


@app.post("/api/recap/test")
def recap_test(request: Request, u: dict = User):
    auth.throttle(f"recap:{u['id']}", limit=3, window=3600)
    sent = recap.send_to(u)
    if not (sent["telegram"] or sent["email"]):
        raise HTTPException(400, "Connect Telegram (or ask the site owner to turn on email) to receive the recap.")
    return sent


@app.post("/api/brief/test")
def brief_test(request: Request, u: dict = User):
    auth.throttle(f"brief:{u['id']}", limit=3, window=3600)
    sent = brief.send_to(u)
    if not (sent["telegram"] or sent["email"]):
        raise HTTPException(400, "Connect Telegram (or ask the site owner to turn on email) to receive the brief.")
    return sent


@app.post("/api/me/styles")
def set_styles(body: StylesBody, u: dict = User):
    picked = [x for x in dict.fromkeys(body.styles) if x in patterns.LIVE_STYLES]
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


@app.post("/api/testsms")
def test_sms(u: dict = Pro):
    try:
        return {"sid": phone.text("NextShelf test text. SMS alerts are working.", u["phone"])}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/admin/users")
def admin_users(u: dict = Admin):
    return [dict(id=x["id"], email=x["email"], name=x["name"], plan=x["plan"], is_admin=bool(x["is_admin"]),
                 created_at=x["created_at"], last_login_at=x.get("last_login_at"),
                 tickers=len(db.user_symbols(x["id"])), telegram=bool(x["telegram_chat"]))
            for x in db.all_users()]


@app.get("/api/admin/overview")
def admin_overview(u: dict = Admin):
    """Totals for the admin dashboard, from the existing tables."""
    users, subs = db.all_users(), db.subscribers()
    week = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    is_pro = lambda x: x["plan"] == "pro" or x["is_admin"]
    return dict(members=len(users), pro=sum(1 for x in users if is_pro(x)), free=sum(1 for x in users if not is_pro(x)),
                joined_week=sum(1 for x in users if (x["created_at"] or "") >= week),
                seen_week=sum(1 for x in users if (x.get("last_login_at") or "") >= week),
                waitlist=sum(1 for s in subs if s["status"] == "subscribed"),
                waitlist_unsub=sum(1 for s in subs if s["status"] != "subscribed"),
                waitlist_week=sum(1 for s in subs if (s["created_at"] or "") >= week))


@app.get("/api/admin/users/{uid}")
def admin_user_detail(uid: int, u: dict = Admin):
    """One member: contact and usage only. Never passwords, Google ids, link codes or tokens."""
    x = db.get_user(uid)
    if not x:
        raise HTTPException(404, "No such member")
    return dict(id=x["id"], email=x["email"], name=x["name"], plan=x["plan"], is_admin=bool(x["is_admin"]),
                created_at=x["created_at"], last_login_at=x.get("last_login_at"), telegram=bool(x["telegram_chat"]),
                phone=x.get("phone") or "", tickers=db.user_symbols(uid), broker_link=int(uid) in broker_testers())


class BrokerAccessBody(BaseModel):
    on: bool


@app.post("/api/admin/users/{uid}/broker")
def admin_broker_access(uid: int, body: BrokerAccessBody, u: dict = Admin):
    """Let one member link a brokerage, read-only (testing). Turning it off stops access; their link at SnapTrade
    stays until they disconnect it there."""
    import json as _json
    if not db.get_user(uid):
        raise HTTPException(404, "No such member")
    t = broker_testers()
    (t.add if body.on else t.discard)(int(uid))
    db.set_setting("broker_testers", _json.dumps(sorted(t)))
    return dict(ok=True, broker_link=body.on)


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
    _lab = json.loads(db.get_setting("lab") or "{}")
    return dict(storage_ok=storage_persistent() is not False, universe_name=universe.describe(), universe_size=len(universe.board_universe()),
                liquid_symbols=_lab.get("symbols"), skipped_illiquid=_lab.get("skipped_illiquid", 0),
                skipped_smallcap=_lab.get("skipped_smallcap", 0),
                provider=data.provider(), telegram=telegram_bot.enabled(), telegram_linked=bool(u and u["telegram_chat"]),
                phone=bool(u and u["phone"]) and phone.enabled() and auth.is_pro(u), call_on=sorted(phone.call_on()),
                scan_times=SCAN_TIMES, preclose_times=PRECLOSE_TIMES if auth.is_pro(u) else [],
                entry_times=ENTRY_TIMES, board_times=BOARD_TIMES,
                vix=scanner.vix_stored(),      # Patch 190: the stored VIX fact for Today's market pill (context, never a gate)
                market_holidays=sorted(flow_signal.NYSE_HOLIDAYS), early_closes=sorted(flow_signal.NYSE_EARLY_CLOSES),
                settings=dict(min_sessions=s.min_sessions, min_candles=s.min_candles, k=s.k, horizon=s.horizon, target_atr=s.target_atr,
                              shelf_min_atr=s.shelf_min_atr))


@app.get("/api/earnings")
def earnings_dates(symbols: str = ""):
    """Upcoming earnings (within 3 weeks) and the last report for up to 200 symbols: {SYM: {date, days, when, label, last}}."""
    syms = []
    for x in symbols.split(",")[:200]:
        try:
            syms.append(scanner.clean_symbol(x))
        except Exception:
            pass
    return earnings.many(syms)


@app.get("/api/live")
def live_prices(symbols: str = ""):
    """Live prices for watching (refreshed every ~30s by the app during market hours). Signals still use 4H closes."""
    syms = [x.strip().upper() for x in symbols.split(",") if scanner.SYMBOL_RE.match(x.strip().upper())][:150]
    return dict(market_open=data.market_open(), as_of=pd.Timestamp.now(tz="UTC").isoformat(),
                prices=data.latest_prices(syms) if syms else {})


@app.get("/api/stream")
async def stream_prices(request: Request, symbols: str = "", u: dict = User):
    """Live trades, tick by tick, as Server-Sent Events (one Alpaca websocket on the server, shared by everyone)."""
    syms = [x.strip().upper() for x in symbols.split(",") if scanner.SYMBOL_RE.match(x.strip().upper())][:40]
    if not syms:
        raise HTTPException(400, "no symbols")
    return StreamingResponse(stream.events(request, syms), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


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
        raise HTTPException(429, f"Sika just checked your list. Try again in {int(60 - (_t.time() - last))}s.")
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


def board_for(u: dict | None) -> bool:
    """Full Hub Board (live breakouts and stocks at the edge): Pro, or every signed-in member while BOARD_FOR_ALL is on (beta). Set it to false at launch."""
    if auth.is_pro(u):
        return True
    return bool(u) and os.getenv("BOARD_FOR_ALL", "true").strip().lower() in ("1", "true", "yes", "on")


@app.get("/api/pulse")
def pulse_data(u: dict = User):
    """Patch 207: sector rotation vs SPY plus the live-setup cluster line. Signed-in, like the board. Never errors."""
    try:
        import pulse
        return pulse.payload(board.board().get("live") or []) or dict(rows=[])
    except Exception:
        log.exception("pulse")
        return dict(rows=[])


@app.get("/api/cross/{symbol}")
def cross_for(symbol: str, u: dict | None = Maybe):
    """Patch 218: the stock's cross setup (live, or resolved in the last 3 days) with its per-signal record."""
    import cross
    return {"cross": cross.for_symbol(scanner.clean_symbol(symbol)) if u else None}


@app.get("/api/admin/cross-log")
def cross_log(u: dict = Admin):
    """Patch 218 §7: the forward log, both scorings."""
    import cross
    return dict(summary=cross.forward_summary(), rows=cross._rows("ORDER BY cross_at DESC LIMIT 200"))


@app.get("/api/board")
def board_data(u: dict | None = Maybe):
    b = board.board()
    if not u:      # visitors who aren't signed in: counts only, no stock names
        return dict(updated=b.get("updated"), universe=b.get("universe"), guest=True, locked=True,
                    live_count=len(b["live"]), near_count=len(b["near"]), forming_count=len(b["forming"]),
                    live=[], near=[], forming=[], recent=[], setups=[], setups_locked=0)
    try:      # Patch 218 §4: cross setups are public on the board (the phone is what the gate filters)
        import cross
        b["crosses"] = cross.board_rows()
    except Exception as e:
        log.warning("board crosses failed: %s", e)
        b["crosses"] = []
    mine = set(db.user_symbols(u["id"]))
    for group in ("live", "near", "forming", "recent", "setups", "crosses"):     # Patch 216: setups too (gap fades), per member
        for r in b.get(group) or []:
            r["watching"] = r["symbol"] in mine
    try:      # Patch 165: the board's live-first order needs each live row's setup grade. This is a
        import time as _time   # cache-only read of Sika's grade cache (Patch 159): the deadline is
        import sika_find       # already expired, so a miss returns "?" and never computes on the
        for r in b["live"]:    # request path; warm_board_grades fills the cache after each sweep.
            d = 1 if r.get("state") == "breakout_up" else -1
            r["grade"] = sika_find._grade_for(r["symbol"], "hub", d, _time.monotonic()).get("grade") or "?"
        for r in b.get("setups") or []:      # the same cache-only read for live double tops/bottoms and gap fades
            if r.get("state") in ("breakout_up", "breakdown") and r.get("kind") in ("double_bottom", "double_top", "gapfade"):
                d = 1 if r.get("state") == "breakout_up" else -1
                r["grade"] = sika_find._grade_for(r["symbol"], r["kind"], d, _time.monotonic()).get("grade") or "?"
    except Exception:
        for r in b["live"]:
            r.setdefault("grade", "?")
    if not board_for(u):                    # free: the timely groups are counts only
        b["locked"] = True
        b["live_count"], b["near_count"] = len(b["live"]), len(b["near"])
        b["live"], b["near"] = [], []
        b["setups_locked"] = sum(1 for x in b["setups"] if x["state"] in ("breakout_up", "breakdown", "testing"))
        b["setups"] = [x for x in b["setups"] if x["state"] not in ("breakout_up", "breakdown", "testing")]
    try:      # Patch 182: the real tape classification (Patch 174/177) on live
        import swing_card as _sc   # board rows, behind the same visibility gate
        if _sc.flow_allowed(u):    # as the swing card. Stored rows only; never the proxy's "flow" key.
            for r in b["live"]:
                d = 1 if r.get("state") == "breakout_up" else -1
                row = db.flow_signal_latest(r["symbol"], d)
                if row:
                    r["tape_flow"] = dict(state=row["state"], net=row["flow_net"],
                                          line=_sc.tape_short_line(row, d),
                                          date=row.get("breakout_date"))   # the panel names the break it's about
    except Exception:
        pass
    try:      # Patch 198 §6: the live path fact on live hub rows — the same
        import levels as _lv   # evaluation the ticker checklist shows (levels.compute, read from its cache). Fail-open
        for r in b["live"]:    # per row: any failure means no chip, never an error.
            try:
                stk = (_lv.cached(r["symbol"]) or {}).get("stacked")   # cached only; warms in the background
            except Exception:
                continue
            if stk and stk.get("name") == "Stacked Shelf" and stk.get("clear_path") is not None:
                r["path"] = dict(clear=bool(stk["clear_path"]), blocking=stk.get("blocking") or [])
    except Exception:
        pass
    for x in b["setups"]:
        x["watching"] = x["symbol"] in mine
    for group in ("live", "near", "forming", "recent", "setups"):   # for the Webull / Robinhood links
        for r in b.get(group) or []:
            r["exchange"] = data.exchange_for(r["symbol"])
    return b


@app.get("/api/extended/{symbol}")
async def extended_price(symbol: str, u: dict = User):
    """Pre-market / after-hours price for the shelf card header (display only; cached 15 s per stock)."""
    return await run_in_threadpool(data.extended_price, symbol.strip().upper()[:12])


@app.get("/api/premarket")
def premarket_data(u: dict | None = Maybe):
    if not u:
        return dict(locked=True, guest=True, gainers=[], losers=[], as_of=None)
    return dict(data.premarket_movers(), locked=False)


@app.get("/api/public/heatmap")
def public_heatmap():
    """Homepage market map: every tile in its real colour (breaking out, close to the trigger, breaking down) as a teaser.
    States only: no prices, levels or plans."""
    return _heatmap(None, True)


@app.get("/api/heatmap")
def heatmap(u: dict | None = Maybe):
    """Every stock on the board as a tile for the market map: sector, size (market cap) and where its setup stands."""
    return _heatmap(u, board_for(u))


def _heatmap(u: dict | None, full: bool):
    b = board.board()
    state = {}
    for r in b["forming"]:
        # amber only when a close at the trigger is a small move away: within half a typical 4-hour move (1% if unknown)
        tu, px, atr = r.get("trigger_up"), r.get("last_close"), r.get("atr")
        near = tu is not None and px is not None and 0 <= tu - px <= (0.5 * atr if atr else 0.01 * px)
        state[r["symbol"]] = "edge" if near else "resting"
    for x in b["setups"]:                       # measured patterns only
        if x.get("kind") in ("double_bottom", "gapfade") and x["state"] == "breakout_up":
            state[x["symbol"]] = "up"
        elif x.get("kind") == "double_top" and x["state"] == "breakdown":
            state.setdefault(x["symbol"], "down")
    for r in b["live"]:
        state[r["symbol"]] = "up" if r.get("state") == "breakout_up" else "down"
    tiles = []
    for s in universe.board_universe():
        st = state.get(s, "none")
        if not full and st in ("up", "down", "edge", "edge_dn"):
            st = "locked"                          # free plan: the timely states stay hidden, like the board
        tiles.append(dict(s=s, sec=universe.sector(s), cap=universe.tile_size(s), st=st))
    try:
        regime = json.loads(db.get_setting("regime") or "null")
    except Exception:
        regime = None
    return dict(updated=b.get("updated"), tiles=tiles, locked=not full, guest=not u, regime=regime)


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
    full = bool(u and (u.get("is_admin") or board_for(u)))
    sc = dict(board.SCAN)
    found = sc.pop("found", [])
    sc["found_count"] = len(found)
    sc["found"] = found[-12:] if full else []       # names of fresh finds are a Pro feature, like the board
    sc["next"] = _next_sweep()
    return sc


@app.get("/api/public/summary")
def public_summary():
    """Real numbers for the landing page: today's board and the hub track record (no symbols from the Pro groups)."""
    b = board.board()
    tr = board.track_record(board.RULES)      # the live record of today's rules (the old breakout-close log is archived)
    rec = tr["stats"]
    by = rec.get("by_outcome") or {}
    closed = rec.get("closed") or 0
    done = [x for x in (tr.get("signals") or []) if x.get("outcome") not in (None, "pending")]
    done.sort(key=lambda x: x.get("outcome_time") or x.get("breakout_time") or "", reverse=True)
    recent = [dict(symbol=x["symbol"], direction=x.get("direction"), outcome=x["outcome"], setup=x.get("setup")) for x in done[:14]]
    if len(recent) < 8:      # the live record is young: show finished breakouts and breakdowns from the earlier hub log as examples
        old = [x for x in db.all_signals(400, "hub") if x.get("outcome") not in (None, "pending")]
        old.sort(key=lambda x: x.get("outcome_time") or x.get("breakout_time") or "", reverse=True)
        recent += [dict(symbol=x["symbol"], direction=x.get("direction"), outcome=x["outcome"], setup="Hub breakdown" if x.get("direction") == -1 else "Hub breakout")
                   for x in old[:16 - len(recent)]]
    import lab_exits
    return dict(tested=lab_exits.tested_public(), recent=recent, updated=b.get("updated"), universe=len(universe.board_universe()), board_rows=b.get("universe"), forming=len(b["forming"]), live=len(b["live"]), near=len(b["near"]),
                signals=rec.get("signals"), closed=closed, open=rec.get("open"), reached=by.get("follow-through", 0),
                reached_pct=round(100 * by.get("follow-through", 0) / closed) if closed else None,
                win_rate=rec.get("win_rate"), avg_ret=rec.get("avg_ret"), since=tr.get("since"), next=_next_sweep(), times=sorted(BOARD_TIMES))


@app.get("/api/public/hero-chart")
async def public_hero_chart():
    """Small public chart payload for the AVGO landing-page preview; no account or watchlist data."""
    d = await run_in_threadpool(lambda: board.chart("AVGO", tf="4h", lab_mode=False, fresh=False))
    st = d.get("status") or {}
    hub = st.get("hub") or {}
    return dict(
        symbol="AVGO",
        candles=d.get("candles") or [],
        forming=d.get("forming"),
        status=dict(
            state=st.get("state"),
            direction=st.get("direction"),
            hub=dict(low=hub.get("low"), high=hub.get("high")) if hub else None,
            target=st.get("target"),
            shelf_up=st.get("shelf_up"),
            shelf_down=st.get("shelf_down"),
        ),
    )


# ---------------------------------------------------------------- chart drawings (Nov 24): Pro, saved on the account
def _draw_sym(symbol: str) -> str:
    s = (symbol or "").upper().strip()
    if not s or len(s) > 12 or not all(c.isalnum() or c in ".-" for c in s):
        raise HTTPException(400, "bad symbol")
    return s


@app.get("/api/drawings/{symbol}")
def drawings_get(symbol: str, u: dict = User):
    if not u or not auth.is_pro(u):
        raise HTTPException(403, "Drawing on the chart is a Pro feature")
    return {"drawings": json.loads(db.get_drawings(u["id"], _draw_sym(symbol)))}


class DrawBody(BaseModel):
    drawings: list = []


@app.put("/api/drawings/{symbol}")
def drawings_put(symbol: str, body: DrawBody, u: dict = User):
    if not u or not auth.is_pro(u):
        raise HTTPException(403, "Drawing on the chart is a Pro feature")
    ok = []
    for d in body.drawings[:60]:                         # keep it small: up to 60 drawings a stock, known shapes only
        if isinstance(d, dict) and d.get("t") in ("h", "line", "box", "text"):
            ok.append({k: d[k] for k in ("t", "p", "a", "b", "text", "c") if k in d})
    data = json.dumps(ok)
    if len(data) > 40000:
        raise HTTPException(400, "Too many drawings on this chart")
    db.set_drawings(u["id"], _draw_sym(symbol), data)
    return {"ok": True, "n": len(ok)}


@app.get("/api/record")
def record_data(pattern: str = "hub", u: dict = User):
    if pattern != board.RULES and pattern not in patterns.KINDS:
        raise HTTPException(400, "unknown setup")
    if pattern != board.RULES and not (u and u.get("is_admin")):
        raise HTTPException(403, "The earlier breakout-close log is archived")
    import lab_exits
    return dict(board.track_record(pattern), tested=lab_exits.tested_public())


def options_for(u: dict | None) -> bool:
    """Options plans: Pro only, unless OPTIONS_FOR_ALL is on (beta: every signed-in member). Set it to false at launch."""
    if not u:
        return False
    return auth.is_pro(u) or os.getenv("OPTIONS_FOR_ALL", "true").strip().lower() in ("1", "true", "yes", "on")


@app.get("/api/options/{symbol}")
async def options_plan(symbol: str, u: dict = User):
    """Educational options swing plan for the current setup (Pro; every member while OPTIONS_FOR_ALL is on)."""
    if not options_for(u):
        raise HTTPException(403, "Options plans are a Pro feature")
    try:
        return await run_in_threadpool(options.plan, symbol)
    except Exception as e:
        raise HTTPException(400, str(e))

@app.get("/api/swing-card/{symbol}")
async def swing_card_endpoint(symbol: str, u: dict = User):
    """Sika's swing-play read for the current setup (Pro; same gate as /api/options). Display only."""
    if not options_for(u):
        raise HTTPException(403, "Options plans are a Pro feature")
    try:
        return await run_in_threadpool(swing_card.card, symbol,
                                       include_flow=swing_card.flow_allowed(u))
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/stacked")
async def stacked_endpoint(u: dict = User):
    """The Stacked list on Today: live hub breakouts, gap fades and double bottoms with their stacked checklist.
    Pro (the board): the whole market. Free: only stocks on the user's watchlist. Gamma levels count for Pro only."""
    import levels
    b = board.board()
    mine = set(db.user_symbols(u["id"]))
    whole = bool(board_for(u))
    cands = [r["symbol"] for r in b["live"] if r.get("state") in ("breakout_up", "breakdown")]
    cands += [x["symbol"] for x in b["setups"] if x.get("state") == "breakout_up" and x.get("kind") in ("gapfade", "double_bottom")]
    mine_first = [c for c in cands if c in mine] + [c for c in cands if c not in mine]
    if not whole:
        mine_first = [c for c in mine_first if c in mine]
    out = levels.stacked_list_nowait(mine_first, bool(options_for(u)))
    for x in out["items"]:
        x["watching"] = x["symbol"] in mine
    return {**out, "whole_market": whole, "gamma": bool(options_for(u))}


@app.get("/api/levels/{symbol}")
async def levels_endpoint(symbol: str, u: dict = User):
    """Levels to watch: the strongest stacked levels near price. Everyone signed in gets the price levels;
    the options-based ones (walls, gamma) are Pro, the same gate as the options plan."""
    import levels
    pro = bool(options_for(u))
    try:
        out = await run_in_threadpool(levels.compute, symbol, pro)
    except Exception as e:
        raise HTTPException(400, str(e))
    return {**out, "gamma_locked": not pro}


@app.post("/api/sika/chat")
async def sika_chat_endpoint(body: SikaChatBody, u: dict = User):
    """Grounded conversational follow-up about the current Swing Play Card. Display only."""
    if not options_for(u):
        raise HTTPException(403, "Options plans are a Pro feature")
    if len((body.message or "").strip()) > 1200:
        raise HTTPException(400, "Message is too long")
    try:
        return await run_in_threadpool(
            sika_chat.answer, body.symbol, body.message, body.history or [], body.plain_english,
            include_flow=swing_card.flow_allowed(u)
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        log.warning("Sika chat unavailable for %s: %s", body.symbol[:12], type(e).__name__)
        raise HTTPException(503, "Sika couldn't answer that right now")


@app.post("/api/sika/find")
async def sika_find_endpoint(body: SikaFindBody, u: dict = User):
    """Deterministic setup scan for the Sika dialogue: tickers with the requested setup kind, from board rows. Display only."""
    if not options_for(u):
        raise HTTPException(403, "Options plans are a Pro feature")
    kind = (body.kind or "").strip().lower()
    if kind not in ("all", "hub", "double", "double_top", "double_bottom", "gapfade"):
        raise HTTPException(400, "Unknown setup kind")
    try:
        return await run_in_threadpool(sika_find.find, kind, max(1, min(60, body.limit or 8)))
    except Exception as e:
        log.warning("Sika find unavailable for %s: %s", kind, type(e).__name__)
        raise HTTPException(503, "Sika couldn't answer that right now")


def _flow_ingest_ok(request: Request) -> bool:
    """The flow courier's shared secret (Patch 177, flow-wiring spec
    section 3). App auth is session-cookie; a script on the PC has no
    session, so these two endpoints take a dedicated token. It can do
    exactly two things — read the work list, post aggregates — and the
    endpoints are inert until FLOW_INGEST_TOKEN is set."""
    tok = os.getenv("FLOW_INGEST_TOKEN", "")
    if not tok:
        return False
    return secrets.compare_digest(request.headers.get("x-flow-token", ""), tok)


@app.get("/api/flow/pending")
def flow_pending(request: Request):
    if not os.getenv("FLOW_INGEST_TOKEN"):
        raise HTTPException(404, "Not found")
    if not _flow_ingest_ok(request):
        raise HTTPException(403, "Bad flow token")
    return {"pending": flow_signal.pending_breakouts()}


@app.post("/api/flow/aggregates")
async def flow_aggregates(body: FlowAggregatesBody, request: Request):
    """The PC courier posts daily tape aggregates; the server stores them
    (first write wins) and classifies whatever the stored tape now
    completes. Aggregates only — raw tape never leaves the PC."""
    import re as _re
    from datetime import date as _date
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _ZoneInfo

    if not os.getenv("FLOW_INGEST_TOKEN"):
        raise HTTPException(404, "Not found")
    if not _flow_ingest_ok(request):
        raise HTTPException(403, "Bad flow token")
    rows = body.rows or []
    if len(rows) > 2000:
        raise HTTPException(400, "Too many rows")
    sym_ok = _re.compile(r"^[A-Z.]{1,10}$")
    stored = 0
    for row in rows:
        try:
            sym = str(row["symbol"]).upper()
            if not sym_ok.match(sym):
                continue
            day = _date.fromisoformat(str(row["day"])).isoformat()
            agg = {k: float(row[k]) for k in ("call_buy_premium", "put_buy_premium", "net")}
            agg.update({k: int(row[k]) for k in ("trades", "classified", "unclassified", "contracts")})
            # premiums and counts are non-negative by construction; net is signed and exempt
            if min(agg["call_buy_premium"], agg["put_buy_premium"], agg["trades"],
                   agg["classified"], agg["unclassified"], agg["contracts"]) < 0:
                continue
            if db.flow_tape_for(sym, day):
                continue
            db.log_flow_tape(sym, day, agg, (str(row.get("provider", ""))[:40] or "theta_terminal"),
                             _dt.now(_ZoneInfo("America/New_York")).isoformat())
            stored += 1
        except Exception:
            continue
    classified = await run_in_threadpool(
        flow_signal.classify_recent, 10, flow_signal.StoredTapeProvider())
    return {"stored": stored, "received": len(rows), "classified": classified}



# ---------------------------------------------------------------- The Waiting Room (Patch 178)
class WRGuessBody(BaseModel):
    choice: str = ""


def _wr_today():
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _ZoneInfo
    return _dt.now(_ZoneInfo("America/New_York")).date()


@app.get("/waiting-room/{name}")
def waiting_room_media(name: str):
    """The scenes' clips and posters — only the manifest's files (no directory listing, no other paths)."""
    import waiting_room as wr
    stem, _, ext = name.rpartition(".")
    if ext not in ("mp4", "webp") or stem not in {s["id"] for s in wr.POOL}:
        raise HTTPException(404, "Not found")
    f = wr.MEDIA / name
    if not f.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(f, media_type="video/mp4" if ext == "mp4" else "image/webp",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/waiting-room")
def waiting_room(u: dict = User):
    import waiting_room as wr
    today = _wr_today()
    if not wr.media_ready(wr.scene_for_date(today)):
        return Response(status_code=204)          # media not placed yet: the card stays hidden
    payload = wr.public_payload(today)
    g = db.wr_guess(u["id"], payload["date"])
    if g:
        payload["guessed"] = {**wr.reveal(today, g["choice"]), "choice": g["choice"], "correct": bool(g["correct"])}
    return payload


@app.post("/api/waiting-room/guess")
def waiting_room_guess(body: WRGuessBody, u: dict = User):
    import waiting_room as wr
    today = _wr_today()
    choice = str(body.choice or "")[:60]
    payload = wr.public_payload(today)
    if choice not in payload["options"] and choice != "__reveal__":
        raise HTTPException(400, "unknown option")
    r = wr.reveal(today, choice)
    db.save_wr_guess(u["id"], payload["date"], choice, r["correct"])
    return r


# ---------------------------------------------------------------- The Daily Stack (Patch 179)
class StackRevealBody(BaseModel):
    id: str = ""


@app.get("/stack-media/{name}")
def stack_media(name: str):
    """The stack's clips and posters — only the manifest's files (no directory listing, no other paths)."""
    import daily_stack as ds
    stem, _, ext = name.rpartition(".")
    if ext not in ("mp4", "jpg") or stem not in {c["id"] for c in ds.POOL}:
        raise HTTPException(404, "Not found")
    f = ds.MEDIA / name
    if not f.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(f, media_type="video/mp4" if ext == "mp4" else "image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/stack")
def stack_today(u: dict = User):
    import daily_stack as ds
    today = _wr_today()
    if not ds.media_ready(ds.stack_for_date(today)):
        return Response(status_code=204)          # media not placed yet: the entry card stays hidden
    return ds.public_payload(today)


@app.get("/cinema-media/{name}")
def cinema_media(name: str):
    """Screening Room posters only: the catalog's own files (the films themselves stream from archive.org)."""
    import cinema
    if name not in cinema.poster_names() or not name.endswith((".jpg", ".png", ".webp")):
        raise HTTPException(404, "Not found")
    f = cinema.POSTERS / name
    if not f.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(f, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/cinema")
def cinema_today(u: dict = User):
    """Patch 195: today's featured film and the two shelves (members only, like the Stack). 204 while the catalog is empty."""
    import cinema
    if not cinema.FILMS:
        return Response(status_code=204)
    return cinema.payload(_wr_today())


@app.post("/api/stack/reveal")
def stack_reveal(body: StackRevealBody, u: dict = User):
    import daily_stack as ds
    today = _wr_today()
    cid = str(body.id or "")[:60]
    if cid not in {c["id"] for c in ds.stack_for_date(today)}:
        raise HTTPException(400, "not in today's stack")
    return {"id": cid, "place": ds.place_for(cid)}

@app.get("/api/gamma/{symbol}")
async def gamma_walls(symbol: str, u: dict = User):
    """Gamma walls for the Shelf card (one Cboe chain per symbol per day, cached). Fail-open: walls=None."""
    sym = symbol.strip().upper()[:12]
    try:      # walls follow the current price (recomputed from the day's cached chain)
        spot = await run_in_threadpool(data.latest_price, sym)
    except Exception:
        spot = None
    row = None if spot else next((r for r in db.board_rows() if r["symbol"] == sym), None)
    if row:
        try:
            spot = json.loads(row["status"] or "{}").get("last_close")
        except Exception:
            spot = None
    w = await run_in_threadpool(gamma.walls, sym, spot) if spot else None
    s = await run_in_threadpool(gamma.shelves, sym, spot) if spot else None
    g = await run_in_threadpool(gamma.grid, sym, spot) if spot else None
    nl = await run_in_threadpool(gamma.next_levels, sym, spot, False) if spot else None    # display only (spec Nov 18)
    sm = await run_in_threadpool(gamma.summed, sym, spot, False) if spot else None    # all nearest expiries added up
    return dict(symbol=sym, walls=w, shelves=s, grid=g, next_levels=nl, summed=sm, spot=spot,
                as_of=pd.Timestamp.now(tz="America/New_York").isoformat(timespec="seconds"),
                chain_as_of=gamma.chain_as_of(sym))



@app.get("/api/gex/board/{symbol}")
async def gex_board(symbol: str, at: str | None = None, u: dict = User):
    """Watch-only all-expiry GEX board. at=HHMM selects a recorded watchlist snapshot."""
    import re
    sym = symbol.strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", sym):
        raise HTTPException(400, "Unknown symbol")
    if at is not None and not re.fullmatch(r"\d{4}", at):
        raise HTTPException(400, "at must be HHMM")
    on_watchlist = sym in db.user_symbols(u["id"])
    if at is not None and not on_watchlist:
        raise HTTPException(404, "Replay is available for your watchlist tickers only")
    spot = None
    if at is None:
        try:
            spot = await run_in_threadpool(data.latest_price, sym)
        except Exception:
            spot = None
        if not spot:
            row = next((r for r in db.board_rows() if r["symbol"] == sym), None)
            if row:
                try:
                    spot = json.loads(row["status"] or "{}").get("last_close")
                except Exception:
                    spot = None
    result = await run_in_threadpool(gamma.board, sym, spot, at)
    if at is not None and result is None:
        raise HTTPException(404, "No GEX snapshot for that time")
    if result and not on_watchlist:
        result["replay"] = []
        result["has_snapshots"] = False
        for row in result.get("rows", []):
            row["mom"] = [None for _ in row.get("gex", [])]
            row["total_mom"] = None
            row["labels"] = [label for label in row.get("labels", []) if label != "RV"]
    return result


@app.get("/api/gex/tickers")
def gex_tickers(u: dict = User):
    """Watchlist first, followed by the configured universe for on-demand board lookup."""
    watched = db.user_symbols(u["id"])
    watch_set = set(watched)
    ordered = list(watched) + [s for s in universe.board_universe() if s not in watch_set]
    return [dict(symbol=s, name=s, on_watchlist=s in watch_set,
                 has_snapshots=bool(s in watch_set and gamma.has_snapshots(s)))
            for s in ordered]


@app.get("/api/grade/{symbol}")
async def grade_for(symbol: str, kind: str = "hub", u: dict = User):
    """Setup grade for this stock (~2 years), the same numbers the alerts use. kind selects the setup measured:
    hub (default), gapfade, double_top, double_bottom. Always the named setup in the named direction, never another
    pattern's trades. `universe` is the pattern's record across the test universe, for when the stock's own history is thin."""
    import re
    sym = symbol.upper().strip()
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", sym):
        raise HTTPException(400, "Unknown symbol")
    if kind != "hub" and kind not in patterns.KINDS:
        raise HTTPException(400, "Unknown setup")
    def work():
        tr = scanner._bt_trades(sym) if kind == "hub" else scanner._bt_pattern_trades(sym, kind)
        return {"up": engine.setup_grade(tr, 1), "down": engine.setup_grade(tr, -1), "years": 2, "kind": kind,
                "universe": scanner.universe_record(sym, kind),
                # the trade NextShelf would actually take: this stock's A retests, and the A record across all stocks
                "retest": engine.setup_grade(scanner._bt_retest_trades(sym, kind), 1) if kind in ("hub", "double_bottom") else None,
                "universe_a": scanner.universe_a(kind) if kind in ("hub", "double_bottom") else None}
    return await run_in_threadpool(work)


@app.get("/api/news/{symbol}")
async def news_for(symbol: str, candles: int | None = None, u: dict = User):
    """Recent headlines for a stock and, when it has just broken out, the one that may explain it."""
    import news
    import re
    sym = symbol.upper().strip()
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", sym):
        raise HTTPException(400, "Unknown symbol")
    return await run_in_threadpool(lambda: news.for_card(sym, candles))


@app.get("/api/forming/{symbol}")
async def forming_candle(symbol: str, u: dict = User):
    """The 4H candle still forming, polled by the setup map while it's open (market hours only)."""
    sym = scanner.clean_symbol(symbol)
    try:
        c = await run_in_threadpool(scanner.forming_candle, sym)
    except Exception:
        c = None
    return dict(symbol=sym, candle=c)


@app.get("/api/chart/{symbol}")
async def chart_data(symbol: str, response: Response, tf: str = "4h", fresh: int = 0, ext: int = 0, u: dict = User):
    try:
        d = await run_in_threadpool(lambda: board.chart(symbol, tf=tf, lab_mode=bool(u and u["is_admin"]), fresh=bool(fresh), ext=bool(ext)))
    except Exception as e:
        raise HTTPException(400, str(e))
    tm = d.get("timing") or {}           # where the time went (browser dev tools show it under Timing; also logged)
    response.headers["Server-Timing"] = ", ".join(f"{k};dur={v}" for k, v in tm.items() if isinstance(v, (int, float)) and not isinstance(v, bool)) \
        + (", cache;desc=hit" if tm.get("cached") else "")
    if (tm.get("total") or 0) > 800:
        log.info("slow chart %s %s: %s", symbol, tf, tm)
    d["watching"] = bool(u) and d["symbol"] in db.user_symbols(u["id"])
    d["guest"] = not u
    d["exchange"] = data.exchange_for(symbol)
    return d


# ---------------------------------------------------------------- next-day check (owner only)
@app.get("/api/admin/nextday")
def admin_nextday(u: dict = Admin):
    import nextday
    return dict(rows=nextday.rows(200), stats=nextday.stats(), time=nextday.NEXTDAY_TIME)


@app.get("/api/admin/confluence")
def admin_confluence(u: dict = Admin):
    import confluence
    return dict(rows=confluence.stats())


@app.post("/api/admin/confluence/run")
async def admin_confluence_run(u: dict = Admin):
    import confluence
    return dict(tagged=await run_in_threadpool(confluence.tag_recent))


@app.post("/api/admin/nextday/run")
async def admin_nextday_run(u: dict = Admin):
    import nextday
    done = await run_in_threadpool(nextday.run, False)
    return dict(added=len(done))


# ---------------------------------------------------------------- setup journal (owner only)
@app.get("/api/admin/journal")
def admin_journal(symbol: str = "", setup: str = "", outcome: str = "", u: dict = Admin):
    import journal
    return dict(entries=journal.list_entries(u["id"], symbol, setup, outcome), summary=journal.summary(u["id"]),
                setups=journal.SETUPS, outcomes=journal.OUTCOMES, max_files=journal.MAX_FILES)


@app.post("/api/admin/journal")
async def admin_journal_add(request: Request, u: dict = Admin):
    import journal
    body = await request.json()
    try:
        return await run_in_threadpool(journal.create, u["id"], body)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.put("/api/admin/journal/{eid}")
async def admin_journal_edit(eid: int, request: Request, u: dict = Admin):
    import journal
    body = await request.json()
    try:
        return await run_in_threadpool(journal.update, u["id"], eid, body)
    except KeyError:
        raise HTTPException(404, "That note is gone")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/admin/journal/{eid}")
def admin_journal_delete(eid: int, u: dict = Admin):
    import journal
    journal.delete(u["id"], eid)
    return dict(ok=True)


@app.get("/api/admin/journal/file/{name}")
def admin_journal_file(name: str, u: dict = Admin):
    import journal
    p = journal.file_path(u["id"], name)
    if not p:
        raise HTTPException(404, "File not found")
    return FileResponse(p, headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


@app.get("/api/admin/lab")
def admin_lab(u: dict = Admin):
    return board.lab()


@app.get("/api/admin/lab/exits")
def admin_lab_exits(u: dict = Admin):
    import lab_exits
    return lab_exits.status()


@app.post("/api/admin/lab/exits/run")
def admin_lab_exits_run(years: int = 2, tests_only: bool = False, test: str = "", u: dict = Admin):
    import lab_exits
    if years not in lab_exits.YEARS_OK:
        raise HTTPException(400, "Pick 2, 3, 5 or 10 years")
    only = test if test in ("gapgo", "stacked", "gapstk", "dbstk", "regime", "pfprint", "flags") else tests_only
    return dict(started=lab_exits.start(years=years, only_tests=only), years=years, tests_only=bool(only), test=test or None)


@app.get("/api/admin/lab/trigcross")
def admin_lab_trigcross(u: dict = Admin):
    import lab_trigcross
    return lab_trigcross.status()


@app.post("/api/admin/lab/trigcross/run")
def admin_lab_trigcross_run(years: int = 2, u: dict = Admin):
    import lab_trigcross
    if years not in (2, 3, 5):
        raise HTTPException(400, "Pick 2, 3 or 5 years")
    ok = lab_trigcross.start(years=years)
    if not ok:
        raise HTTPException(409, "Another Lab test is running. Wait for it to finish.")
    return dict(started=True, years=years)


@app.post("/api/admin/lab/trigcross/stop")
def admin_lab_trigcross_stop(u: dict = Admin):
    import lab_trigcross
    lab_trigcross.stop()
    return dict(stopping=True)


@app.post("/api/admin/lab/exits/stop")
def admin_lab_exits_stop(restart: bool = False, u: dict = Admin):
    import lab_exits
    return lab_exits.stop(restart=restart)


@app.get("/api/backtest/{symbol}")
async def backtest(symbol: str, pattern: str = "hub", u: dict = Pro):
    try:
        return await run_in_threadpool(scanner.backtest, symbol, "gapfade" if pattern == "gapfade" else "hub")
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/paper/options")
async def paper_options(u: dict = Admin):
    """Admin only: the paper OPTIONS experiment. Never part of any public number."""
    return await run_in_threadpool(paper_opt.summary_opt)


@app.post("/api/paper/options/toggle")
def paper_options_toggle(body: OnBody, u: dict = Admin):
    paper_opt.set_enabled(body.on)
    return {"enabled": paper_opt.enabled()}


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
    course: bool = True        # Nov 24: the pop-up's "send me the 5-day course" box; unticked = list only, no Beehiiv


def _forward_to_beehiiv(email: str, source: str):
    """Also subscribe the email in Beehiiv, so the 5-day Hub Breakout Course starts.
    Needs BEEHIIV_API_KEY and BEEHIIV_PUBLICATION_ID; without them this is skipped quietly.
    Runs in the background so the sign-up form never waits on Beehiiv."""
    key, pub = os.getenv("BEEHIIV_API_KEY", "").strip(), os.getenv("BEEHIIV_PUBLICATION_ID", "").strip()
    if not key or not pub:
        return

    def go():
        try:
            r = requests.post(f"https://api.beehiiv.com/v2/publications/{pub}/subscriptions", timeout=15,
                              headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                              json={"email": email, "reactivate_existing": True, "send_welcome_email": True,
                                    "utm_source": "nextshelf", "utm_medium": source or "landing",
                                    "referring_site": os.getenv("APP_URL", "")})
            if r.status_code >= 300:
                log.warning("beehiiv subscribe failed (%s): %s", r.status_code, r.text[:200])
        except Exception as e:
            log.warning("beehiiv subscribe failed: %s", e)
    threading.Thread(target=go, daemon=True).start()


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
        if body.course:
            _forward_to_beehiiv(email, body.source[:40] or "landing")
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


# ---------------------------------------------------------------- invite codes (early access)
class InviteBody(BaseModel):
    note: str = ""
    max_uses: int = 1
    code: str = ""


# ---------------------------------------------------------------- Futures Lab (Nov 23): research only
@app.get("/api/admin/futures-lab")
def futures_lab_status(u: dict = Admin):
    import futures_lab
    st = futures_lab.status()
    st["configured"] = futures_lab.configured()
    st["running"] = bool(futures_lab._RUN.get("thread") and futures_lab._RUN["thread"].is_alive())
    return st


@app.post("/api/admin/futures-lab")
def futures_lab_start(u: dict = Admin):
    import futures_lab
    if not futures_lab.configured():
        raise HTTPException(400, "Add MASSIVE_API_KEY on Render first.")
    if not futures_lab.start_background():
        raise HTTPException(409, "The futures test is already running.")
    return dict(ok=True)


# ---------------------------------------------------------------- Futures opening range (Nov 23): owner-only forward test
@app.get("/api/admin/futures-orb")
def futures_orb_view(u: dict = Admin):
    import futures_orb
    return futures_orb.view(u)


class OrbTake(BaseModel):
    product: str = "MNQ"
    qty: float = 1
    entry: float | None = None


@app.post("/api/admin/futures-orb/take")
def futures_orb_take(body: OrbTake, u: dict = Admin):
    import futures_orb
    import pandas as pd
    try:
        return futures_orb.take(u, pd.Timestamp.now(tz="America/New_York").date().isoformat(), body.product.upper(), body.qty, body.entry)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/admin/futures-orb/chart")
def futures_orb_chart(product: str = "MNQ", days: int = 10, u: dict = Admin):
    import futures_orb
    try:
        return futures_orb.chart(product, days)
    except Exception as e:
        raise HTTPException(502, f"Couldn't load the futures chart: {str(e)[:160]}")


@app.post("/api/admin/futures-orb/check")
def futures_orb_check(u: dict = Admin):
    import futures_orb
    return dict(alerts=futures_orb.tick(force=True))


@app.get("/api/admin/setups-month")
def admin_setups_month(u: dict = Admin):
    return db.setups_month(30)


# ---------------------------------------------------------------- database backups (Nov 23)
@app.get("/api/admin/backup")
def admin_backup_status(u: dict = Admin):
    import backup
    return backup.status()


@app.post("/api/admin/backup")
async def admin_backup_now(u: dict = Admin):
    import backup
    return await run_in_threadpool(backup.run)


@app.get("/api/admin/backup/latest")
def admin_backup_download(u: dict = Admin):
    import backup
    p = backup.latest()
    if not p:
        raise HTTPException(404, "No backup yet. Tap Back up now first.")
    return FileResponse(p, media_type="application/gzip", filename=p.name, headers={"Cache-Control": "no-store"})


@app.get("/api/admin/invites")
def admin_invites(u: dict = Admin):
    return dict(signups_open=auth.signups_open(), invites=db.invites())


@app.post("/api/admin/invites")
def admin_new_invite(body: InviteBody, u: dict = Admin):
    import re as _re
    code = _re.sub(r"[^A-Z0-9-]", "", (body.code or "").upper())[:24] or ("NS-" + secrets.token_hex(3).upper())
    if len(code) < 4:
        raise HTTPException(400, "Use at least 4 letters or numbers for the code")
    try:
        db.add_invite(code, body.note.strip()[:80], max(1, min(int(body.max_uses), 1000)))
    except Exception:
        raise HTTPException(400, "That code already exists")
    return {"code": code}


@app.post("/api/admin/invites/{code}")
def admin_toggle_invite(code: str, body: ToggleBody, u: dict = Admin):
    db.set_invite_active(code, body.on)
    return {"ok": True}


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


# ---------------------------------------------------------------- SnapTrade brokerage (owner-only prototype)
class BrokerImpactBody(BaseModel):
    account_id: str
    symbol: str
    action: str
    units: int
    order_type: str = "Market"
    price: float | None = None


class BrokerOrderBody(BaseModel):
    preview_id: str
    confirm: bool = False


def _broker_error(exc: Exception, safe_message: str | None = None):
    if isinstance(exc, LookupError):
        raise HTTPException(404, str(exc))
    if isinstance(exc, PermissionError):
        raise HTTPException(403, str(exc))
    if isinstance(exc, ValueError):
        raise HTTPException(400, str(exc))
    if isinstance(exc, RuntimeError) and "not configured" in str(exc):
        raise HTTPException(503, "broker trading not configured")
    # Log only safe transport metadata; never log SnapTrade request/response bodies or credentials.
    status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
    reason = getattr(exc, "reason", None)
    log.error("SnapTrade request failed: exception=%s status=%s reason=%s",
              type(exc).__name__, status, str(reason)[:160] if reason else "unavailable")
    raise HTTPException(502, safe_message or "Brokerage service could not complete the request")


# ---------------------------------------------------------------- Patch 208: Lookout (a member's own watches; private)
class LookoutStart(BaseModel):
    ticker: str
    kind: str = "hub"
    direction: int = 1
    contract: str | None = None
    contract_occ: str | None = None
    qty: float | None = None
    entry_price: float | None = None
    edge: float
    target: float
    kill: float


class LookoutClose(BaseModel):
    exit_price: float | None = None


@app.post("/api/lookout/start")
async def lookout_start(body: LookoutStart, u: dict = User):
    import lookout
    try:
        return await run_in_threadpool(lookout.start_watch, u, body.dict())
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/lookout/preview")
async def lookout_preview(ticker: str, kind: str = "hub", u: dict = User):
    import lookout
    try:
        return await run_in_threadpool(lookout.preview, ticker, kind)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/lookout")
def lookout_mine(u: dict = User):
    import lookout
    return lookout.mine(u)


@app.post("/api/lookout/{row_id}/close")
def lookout_close(row_id: int, body: LookoutClose, u: dict = User):
    import lookout
    try:
        return lookout.close_watch(u, row_id, body.exit_price)
    except LookupError as e:
        raise HTTPException(404, str(e))


# ---------------------------------------------------------------- Chat (Nov 23): Pro posts, every member reads
def _chat_can_post(u: dict) -> bool:
    return bool(u.get("is_admin")) or auth.is_pro(u)


@app.get("/api/chat")
def chat_rooms(u: dict = User):
    import chat
    if u.get("guest"):
        raise HTTPException(401, "Sign in to read the chat")
    return dict(rooms=[dict(id=r[0], name=r[1], about=r[2]) for r in chat.ROOMS], can_post=_chat_can_post(u),
                shown_as=chat.display_name(u), has_name=bool((u.get("name") or "").strip()) and "@" not in (u.get("name") or ""),
                muted_until=chat.muted_until(u["id"]), is_admin=bool(u.get("is_admin")), max_len=chat.MAX_LEN)


class ChatName(BaseModel):
    name: str


@app.post("/api/chat/name")
def chat_name(body: ChatName, u: dict = User):
    """The name members see in chat: first name + last initial is shown. No emails, links or numbers."""
    import re as _re
    n = _re.sub(r"\s+", " ", body.name).strip()[:40]
    if len(n) < 2 or "@" in n or _re.search(r"\d{4,}|https?:|www\.|\.(com|net|org|io)\b", n, _re.I):
        raise HTTPException(400, "Use your first name (and a last name if you like). No emails, links or numbers.")
    db.update_user(u["id"], name=n)
    import chat
    return dict(ok=True, shown_as=chat.display_name(dict(id=u["id"], name=n)))


@app.get("/api/chat/{room}")
def chat_read(room: str, after: int = 0, before: int = 0, u: dict = User):
    import chat
    if u.get("guest"):
        raise HTTPException(401, "Sign in to read the chat")
    try:
        return chat.messages(u, room, after=max(0, after), before=max(0, before))
    except LookupError as e:
        raise HTTPException(404, str(e))


class ChatPost(BaseModel):
    body: str


@app.post("/api/chat/{room}")
def chat_post(room: str, body: ChatPost, u: dict = User):
    import chat
    if u.get("guest"):
        raise HTTPException(401, "Sign in to chat")
    try:
        return chat.post(u, room, body.body, _chat_can_post(u))
    except LookupError as e:
        raise HTTPException(404, str(e))
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


class ChatReport(BaseModel):
    reason: str = ""


@app.post("/api/chat/msg/{mid}/report")
def chat_report(mid: int, body: ChatReport, u: dict = User):
    import chat
    if u.get("guest"):
        raise HTTPException(401, "Sign in first")
    auth.throttle(f"chatrep:{u['id']}", limit=20, window=3600)
    try:
        chat.report(u, mid, body.reason)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return dict(ok=True)


@app.delete("/api/chat/msg/{mid}")
def chat_delete(mid: int, u: dict = User):
    import chat
    try:
        chat.delete(u, mid)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except PermissionError as e:
        raise HTTPException(403, str(e))
    return dict(ok=True)


@app.get("/api/admin/chat/reports")
def chat_queue(u: dict = Admin):
    import chat
    return dict(reports=chat.queue())


class ChatModBody(BaseModel):
    action: str                 # delete | dismiss | mute | unmute
    hours: int = 24


@app.post("/api/admin/chat/msg/{mid}")
def chat_moderate(mid: int, body: ChatModBody, u: dict = Admin):
    import chat
    r = db._run("SELECT user_id FROM chat_messages WHERE id=?", (mid,)).fetchone()
    if not r:
        raise HTTPException(404, "Message not found")
    if body.action == "delete":
        chat.delete(u, mid)
    elif body.action == "dismiss":
        chat.resolve(mid, "dismissed")
    elif body.action == "mute":
        chat.delete(u, mid)
        return dict(ok=True, muted_until=chat.mute(u, r["user_id"], body.hours, "reported message"))
    elif body.action == "unmute":
        chat.unmute(r["user_id"])
    else:
        raise HTTPException(400, "Unknown action")
    return dict(ok=True)


# ---------------------------------------------------------------- Trade Diary (every member, private; Nov 23)
@app.get("/api/diary")
async def diary_list(sync: int = 0, u: dict = User):
    import diary
    sync_info = None
    if sync:
        try:
            sync_info = await run_in_threadpool(diary.sync_broker, u, sync > 1)
        except Exception as e:
            log.info("diary sync failed for %s: %s", u["id"], e)
            sync_info = dict(ok=False, reason="Couldn't reach your brokerage. Your diary is still here.")
    out = await run_in_threadpool(diary.list_trades, u)
    out["sync"] = sync_info
    return out


@app.post("/api/diary")
async def diary_add(request: Request, u: dict = User):
    import diary
    try:
        return await run_in_threadpool(diary.create, u, await request.json())
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.put("/api/diary/{tid}")
async def diary_edit(tid: int, request: Request, u: dict = User):
    import diary
    try:
        return await run_in_threadpool(diary.update, u, tid, await request.json())
    except ValueError as e:
        raise HTTPException(400, str(e))
    except LookupError as e:
        raise HTTPException(404, str(e))


@app.delete("/api/diary/{tid}")
def diary_delete(tid: int, u: dict = User):
    import diary
    try:
        diary.delete(u, tid)
    except LookupError as e:
        raise HTTPException(404, str(e))
    return dict(ok=True)


@app.post("/api/diary/tag")
async def diary_tag(request: Request, u: dict = User):
    import diary
    return await run_in_threadpool(diary.tag_order, u, await request.json())


@app.post("/api/broker/register")
async def broker_register(u: dict = BrokerUser):
    try:
        return await run_in_threadpool(snaptrade.register, u)
    except Exception as e:
        _broker_error(e)


@app.post("/api/broker/portal")
async def broker_portal(broker: str = "", u: dict = BrokerUser):
    try:
        url = await run_in_threadpool(snaptrade.portal, u, broker or None)
        return {"url": url}
    except Exception as e:
        _broker_error(e)


@app.get("/api/broker/status")
async def broker_status(u: dict = BrokerUser):
    """Safe owner-only status check; reports credential presence, never credential values."""
    try:
        return await run_in_threadpool(snaptrade.credential_status, u)
    except Exception as e:
        _broker_error(e)


@app.get("/api/broker/accounts")
async def broker_accounts(u: dict = BrokerUser):
    try:
        rows = await run_in_threadpool(snaptrade.accounts, u)
        return {"accounts": rows, "connected": bool(rows)}
    except Exception as e:
        _broker_error(
            e,
            "SnapTrade could not return your brokerage accounts. Your saved NextShelf credentials are still present; this error does not mean your Webull authorization was deleted. Do not reconnect while this is investigated.",
        )


@app.get("/api/broker/accounts/{account_id}/positions")
async def broker_positions(account_id: str, u: dict = BrokerUser):
    try:
        return {"positions": await run_in_threadpool(snaptrade.positions, u, account_id)}
    except Exception as e:
        _broker_error(e)


@app.get("/api/broker/accounts/{account_id}/orders")
async def broker_orders(account_id: str, u: dict = BrokerUser):
    try:
        return {"orders": await run_in_threadpool(snaptrade.orders, u, account_id)}
    except Exception as e:
        _broker_error(e)


@app.post("/api/broker/impact")
async def broker_impact(body: BrokerImpactBody, u: dict = Admin):
    try:
        return await run_in_threadpool(snaptrade.impact, u, body.dict())
    except Exception as e:
        _broker_error(e)


class BrokerOptionImpactBody(BaseModel):
    account_id: str
    contract: str
    action: str
    units: int
    order_type: str = "LIMIT"
    price: float | None = None
    time_in_force: str = "Day"


_BROKER_PLAIN = [   # Nov 23 (owner): plain words for the brokerage's common refusals
    (("buying power", "insufficient fund", "insufficient balance", "not enough cash", "insufficient cash"), "Insufficient balance"),
    (("market is closed", "market closed", "outside trading hours", "not open for trading"), "The market's closed for this order"),
    (("options level", "option level", "not approved for option", "options trading is not enabled"), "Your account isn't approved for this options order"),
    (("price is too far", "price band", "limit price"), "Your broker refused that limit price"),
]


def _broker_plain(msg: str) -> str:
    import re
    low = msg.lower()
    for keys, plain in _BROKER_PLAIN:
        if any(k in low for k in keys):
            return plain
    msg = re.sub(r"^\s*Trade [0-9a-f-]{8,}:\s*", "", msg, flags=re.I)                   # "Trade <uuid>: ..."
    msg = re.sub(r"^\s*Order rejected by brokerage\s*[-:]\s*", "", msg, flags=re.I)
    return (msg.strip() or "Your broker refused the order")[:200]


def _snap_reason(e: Exception) -> str | None:
    """SnapTrade's own explanation for a refused options request (owner-only screens), never URLs or credentials:
    the API's JSON detail/message, or the SDK's argument-check message. Transport errors (which carry the request URL,
    including the user secret) are never echoed."""
    import json as _j
    body = getattr(e, "body", None)
    if body:
        try:
            d = _j.loads(body) if isinstance(body, (str, bytes)) else body
            msg = d.get("detail") or d.get("message") or d.get("error") if isinstance(d, dict) else None
            if msg:
                return _broker_plain(str(msg))
        except Exception:
            pass
    if type(e).__name__ in ("SchemaValidationError", "ApiTypeError", "ApiValueError"):
        return "Order format problem: " + str(e)[:200]
    return None


class BrokerCancelBody(BaseModel):
    account_id: str
    brokerage_order_id: str
    confirm: bool = False


@app.get("/api/broker/portfolio")
async def broker_portfolio(account_id: str, u: dict = BrokerUser):
    """Owner: open positions (stocks + options, value and P/L) and the last 7 days of orders, from the connected broker."""
    try:
        return await run_in_threadpool(snaptrade.portfolio, u, account_id)
    except Exception as e:
        _broker_error(e, _snap_reason(e))


@app.post("/api/broker/orders/cancel")
async def broker_cancel(body: BrokerCancelBody, u: dict = Admin):
    if body.confirm is not True:
        raise HTTPException(400, "Explicit confirmation is required")
    try:
        return {"result": await run_in_threadpool(snaptrade.cancel, u, body.account_id, body.brokerage_order_id)}
    except Exception as e:
        _broker_error(e, _snap_reason(e))


@app.get("/api/broker/options/chain/{symbol}")
async def broker_option_chain(symbol: str, u: dict = Admin):
    """The option chain for the options ticket (expiries within ~45 days, strikes within ±8%), bid/ask/mid per contract."""
    import options as _opt
    import scanner as _sc
    sym = _sc.clean_symbol(symbol)
    def run():
        c4 = _sc.load_4h(sym, _sc.SCAN_DAYS)
        spot = float(c4["Close"].iloc[-1])
        rows = _opt.chain(sym, spot, 0.35)
        out = [dict(symbol=r["symbol"], expiry=r["expiry"].isoformat(), call=bool(r["call"]), strike=r["strike"],
                    bid=round(r["bid"], 2), ask=round(r["ask"], 2),
                    mid=round((r["bid"] + r["ask"]) / 2, 2) if r["bid"] > 0 and r["ask"] > 0 else round(r.get("last") or 0, 2))
               for r in rows]
        return dict(symbol=sym, spot=round(spot, 2), contracts=sorted(out, key=lambda r: (r["expiry"], r["strike"])))
    try:
        return await run_in_threadpool(run)
    except Exception as e:
        raise HTTPException(400, f"option chain unavailable: {str(e)[:120]}")


@app.post("/api/broker/options/impact")
async def broker_option_impact(body: BrokerOptionImpactBody, u: dict = Admin):
    try:
        return await run_in_threadpool(snaptrade.option_impact, u, body.dict())
    except Exception as e:
        _broker_error(e, _snap_reason(e))


@app.post("/api/broker/options/order")
async def broker_option_order(body: BrokerOrderBody, u: dict = Admin):
    if body.confirm is not True:
        raise HTTPException(400, "Explicit order confirmation is required")
    try:
        return {"order": await run_in_threadpool(snaptrade.place_checked_option, u, body.preview_id)}
    except Exception as e:
        _broker_error(e, _snap_reason(e))


@app.post("/api/broker/order")
async def broker_order(body: BrokerOrderBody, u: dict = Admin):
    if body.confirm is not True:
        raise HTTPException(400, "Explicit order confirmation is required")
    try:
        result = await run_in_threadpool(snaptrade.place_checked_order, u, body.preview_id)
        return {"order": result}
    except Exception as e:
        _broker_error(e)
