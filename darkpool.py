"""Nov 28 (owner): dark pool prints — ADMIN-ONLY layer for now (display rights for exchange data go to the lawyer first).

A dark pool print here = a stock trade reported off-exchange (Alpaca SIP exchange code "D": FINRA ADF / TRF) of at
least $1M. They come off the one Alpaca websocket the server already holds (stream.py): during market hours the
shared watchlist is kept subscribed in the background, and every trade passes record() — a dict lookup and a
multiply for the 99.9% that aren't big off-exchange prints.

Stored one JSON line per print in DATA_DIR/darkpool/{SYM}_{YYYY-MM-DD}.jsonl, kept 10 days.
Display only: never a grade, a setup, or an alert input.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("darkpool")
NY = ZoneInfo("America/New_York")
DIR = Path(os.getenv("DATA_DIR", "data")) / "darkpool"
MIN_USD = 1_000_000
SKIP_COND = {"W", "4", "7", "9", "Q", "O", "M", "6", "V"}   # average-price, derivatively priced, open/close/official, etc.
MAX_SYMBOLS = 25
_LOCK = threading.Lock()
_BUF: list[tuple[str, str]] = []
_LAST_FLUSH = [0.0]


def _wall(ts: str | None) -> tuple[int, str]:
    """RFC3339 UTC trade time -> (ET wall clock as a UTC epoch, the way candles are stored; ET date)."""
    try:
        base = (ts or "")[:19]                       # "2026-11-27T15:42:01" (Alpaca sends UTC with nanoseconds)
        t = datetime.fromisoformat(base).replace(tzinfo=timezone.utc)
    except Exception:
        t = datetime.now(timezone.utc)
    e = t.astimezone(NY)
    return int(datetime(e.year, e.month, e.day, e.hour, e.minute, e.second, tzinfo=timezone.utc).timestamp()), e.date().isoformat()


def record(sym: str, price: float, size: float, exch: str | None, conds, ts: str | None) -> None:
    """Called for every streamed trade. Cheap exit for everything that isn't a $1M+ off-exchange print."""
    if exch != "D" or price * size < MIN_USD:
        return
    if conds and any(c in SKIP_COND for c in conds):
        return
    t, day = _wall(ts)
    line = json.dumps({"t": t, "p": round(price, 4), "s": int(size), "usd": round(price * size)}, separators=(",", ":"))
    with _LOCK:
        _BUF.append((f"{sym.upper()}_{day}", line))
    import time as _t
    if _t.time() - _LAST_FLUSH[0] > 5 or len(_BUF) > 50:
        flush()


def flush() -> None:
    import time as _t
    with _LOCK:
        items, _BUF[:] = list(_BUF), []
        _LAST_FLUSH[0] = _t.time()
    if not items:
        return
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        by: dict[str, list[str]] = {}
        for k, ln in items:
            by.setdefault(k, []).append(ln)
        for k, lines in by.items():
            with open(DIR / f"{k}.jsonl", "a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
    except Exception as exc:
        log.warning("dark pool flush failed: %s", exc)


def prune(days: int = 10) -> None:
    cut = (datetime.now(NY).date() - timedelta(days=days)).isoformat()
    try:
        for p in DIR.glob("*.jsonl"):
            if p.stem.rsplit("_", 1)[-1] < cut:
                p.unlink()
    except Exception:
        pass


def prints(symbol: str, days: int = 7) -> list[dict]:
    flush()
    sym = symbol.upper()
    today = datetime.now(NY).date()
    out: list[dict] = []
    for back in range(days + 3, -1, -1):
        p = DIR / f"{sym}_{(today - timedelta(days=back)).isoformat()}.jsonl"
        if not p.exists():
            continue
        for ln in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
    out.sort(key=lambda r: r["t"])
    return out


def market_window(now: datetime | None = None) -> bool:
    now = now or datetime.now(NY)
    m = now.hour * 60 + now.minute
    return now.weekday() < 5 and 9 * 60 + 25 <= m <= 16 * 60 + 5


def watch_symbols() -> list[str]:
    import db
    return [r["symbol"] for r in db.list_tickers()][:MAX_SYMBOLS]
