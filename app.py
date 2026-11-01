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
from contextlib import asynccontextmanager  # noqa: E402
from pathlib import Path  # noqa: E402

from apscheduler.schedulers.background import BackgroundScheduler  # noqa: E402
from apscheduler.triggers.cron import CronTrigger  # noqa: E402
from fastapi import Depends, FastAPI, HTTPException  # noqa: E402
from fastapi.concurrency import run_in_threadpool  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.security import HTTPBasic, HTTPBasicCredentials  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import data  # noqa: E402
import db  # noqa: E402
import paper  # noqa: E402
import phone  # noqa: E402
import scanner  # noqa: E402
import telegram_bot  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("app")

# Scan a few minutes after each 4H close (13:30 and 16:00 ET). The delay covers the
# free Alpaca plan's 15-minute delay on full-market (SIP) data.
SCAN_TIMES = [t.strip() for t in os.getenv("SCAN_TIMES", "13:47,16:17").split(",") if t.strip()]
# Pre-close warning ~15 minutes before each 4H close (uses Alpaca's free real-time IEX price). Empty = off.
PRECLOSE_TIMES = [t.strip() for t in os.getenv("PRECLOSE_TIMES", "13:15,15:45").split(",") if t.strip()]
# Paper trading: entries/exits ~2 minutes before each 4H close.
ENTRY_TIMES = [t.strip() for t in os.getenv("ENTRY_TIMES", "13:28,15:58").split(",") if t.strip()]


def handle_command(cmd: str, args: list[str]) -> str:
    if cmd == "/paper":
        if args and args[0].lower() in ("on", "off"):
            paper.set_enabled(args[0].lower() == "on")
        return paper.summary_text()
    if cmd == "/testcall":
        phone.call("Hub scanner test call. Phone alerts are working.")
        return "Calling you now."
    if cmd in ("/start", "/help"):
        return (scanner.HELP + "\n\n/paper: paper-trading journal\n/paper on | /paper off: turn auto paper trading on or off"
                "\n/testcall: test the phone-call alert")
    return scanner.handle_command(cmd, args)


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
    sched.add_job(paper.sync, CronTrigger(day_of_week="mon-fri", hour="9-16", minute="*/5",
                                          timezone="America/New_York"),
                  misfire_grace_time=120, coalesce=True, max_instances=1)
    sched.start()
    log.info("Scheduler on: scans %s, pre-close %s ET (provider: %s)",
             ", ".join(SCAN_TIMES), ", ".join(PRECLOSE_TIMES) or "off", data.provider())
    telegram_bot.start_polling(handle_command)
    yield
    sched.shutdown(wait=False)


app = FastAPI(title="Hub Scanner", lifespan=lifespan)
_basic = HTTPBasic(auto_error=False)


def auth(creds: HTTPBasicCredentials | None = Depends(_basic)):
    pw = os.getenv("APP_PASSWORD", "")
    if not pw:
        return
    if not creds or not secrets.compare_digest(creds.password.encode(), pw.encode()):
        raise HTTPException(401, "Password required", headers={"WWW-Authenticate": "Basic"})


class AddBody(BaseModel):
    symbol: str


@app.get("/health")
def health():
    return {"ok": True}


STATIC = Path(__file__).parent / "static"


@app.get("/favicon.svg")
def favicon():
    return FileResponse(STATIC / "favicon.svg", media_type="image/svg+xml")


@app.get("/favicon.ico")
def favicon_ico():
    return FileResponse(STATIC / "favicon.svg", media_type="image/svg+xml")


@app.get("/apple-touch-icon.png")
def touch_icon():
    return FileResponse(STATIC / "apple-touch-icon.png", media_type="image/png")


@app.get("/", dependencies=[Depends(auth)])
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/info", dependencies=[Depends(auth)])
def info():
    s = scanner.SETTINGS
    return dict(provider=data.provider(), telegram=telegram_bot.enabled(),
                telegram_linked=bool(telegram_bot.owner_chat()),
                phone=phone.enabled(), call_on=sorted(phone.call_on()), scan_times=SCAN_TIMES, preclose_times=PRECLOSE_TIMES, entry_times=ENTRY_TIMES,
                settings=dict(min_sessions=s.min_sessions, k=s.k, horizon=s.horizon, target_atr=s.target_atr,
                              shelf_min_atr=s.shelf_min_atr))


@app.get("/api/tickers", dependencies=[Depends(auth)])
def tickers():
    return [dict(symbol=r["symbol"], scanned_at=r["scanned_at"], status=json.loads(r["status"] or "{}"))
            for r in db.list_tickers()]


@app.post("/api/tickers", dependencies=[Depends(auth)])
async def add_ticker(body: AddBody):
    try:
        return await run_in_threadpool(scanner.add_symbol, body.symbol)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.delete("/api/tickers/{symbol}", dependencies=[Depends(auth)])
def remove_ticker(symbol: str):
    if not db.remove_ticker(symbol.upper()):
        raise HTTPException(404, "not on the watchlist")
    return {"ok": True}


@app.get("/api/alerts", dependencies=[Depends(auth)])
def alerts(limit: int = 50):
    return db.recent_alerts(min(limit, 200))


@app.post("/api/scan", dependencies=[Depends(auth)])
async def scan_now():
    sent = await run_in_threadpool(scanner.scan_all)
    return {"alerts": len(sent)}


@app.post("/api/precheck", dependencies=[Depends(auth)])
async def precheck_now():
    sent = await run_in_threadpool(scanner.preclose_all)
    return {"alerts": len(sent)}


@app.post("/api/testcall", dependencies=[Depends(auth)])
def test_call():
    try:
        return {"sid": phone.call("Hub scanner test call. Phone alerts are working.")}
    except Exception as e:
        raise HTTPException(400, str(e))


class ToggleBody(BaseModel):
    on: bool


@app.get("/api/paper", dependencies=[Depends(auth)])
async def paper_summary():
    return await run_in_threadpool(paper.summary)


@app.post("/api/paper/toggle", dependencies=[Depends(auth)])
def paper_toggle(body: ToggleBody):
    paper.set_enabled(body.on)
    return {"enabled": paper.enabled()}


@app.get("/api/backtest/{symbol}", dependencies=[Depends(auth)])
async def backtest(symbol: str):
    try:
        return await run_in_threadpool(scanner.backtest, symbol)
    except Exception as e:
        raise HTTPException(400, str(e))
