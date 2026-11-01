"""Tiny SQLite store: watchlist, alert history, settings."""
from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

_conn = sqlite3.connect(DATA_DIR / "hubscanner.db", check_same_thread=False)
_conn.row_factory = sqlite3.Row
_lock = threading.Lock()

with _lock:
    _conn.executescript("""
    CREATE TABLE IF NOT EXISTS tickers (
        symbol TEXT PRIMARY KEY,
        added_at TEXT DEFAULT CURRENT_TIMESTAMP,
        last_candle TEXT,
        status TEXT,
        scanned_at TEXT
    );
    CREATE TABLE IF NOT EXISTS alerts (
        id TEXT PRIMARY KEY,
        symbol TEXT,
        type TEXT,
        candle_time TEXT,
        message TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS paper_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT, direction INTEGER, qty INTEGER,
        hub_key TEXT, hub_low REAL, hub_high REAL, edge REAL,
        target REAL, target_kind TEXT,
        entry_candle TEXT, opened_at TEXT DEFAULT CURRENT_TIMESTAMP,
        entry_price REAL, entry_order TEXT, tp_order TEXT,
        status TEXT DEFAULT 'open',
        closed_at TEXT, exit_price REAL, exit_reason TEXT,
        pnl REAL, pnl_pct REAL, candles_held INTEGER
    );
    """)
    _conn.commit()


def _run(sql: str, args: tuple = ()):
    with _lock:
        cur = _conn.execute(sql, args)
        _conn.commit()
        return cur


def list_tickers() -> list[dict]:
    return [dict(r) for r in _run("SELECT * FROM tickers ORDER BY symbol").fetchall()]


def get_ticker(symbol: str) -> dict | None:
    r = _run("SELECT * FROM tickers WHERE symbol=?", (symbol,)).fetchone()
    return dict(r) if r else None


def add_ticker(symbol: str) -> bool:
    return _run("INSERT OR IGNORE INTO tickers(symbol) VALUES (?)", (symbol,)).rowcount == 1


def remove_ticker(symbol: str) -> bool:
    return _run("DELETE FROM tickers WHERE symbol=?", (symbol,)).rowcount == 1


def update_ticker(symbol: str, last_candle: str | None, status: str):
    _run("UPDATE tickers SET last_candle=?, status=?, scanned_at=CURRENT_TIMESTAMP WHERE symbol=?",
         (last_candle, status, symbol))


def add_alert(alert_id: str, symbol: str, type_: str, candle_time: str, message: str) -> bool:
    """Returns False if this exact alert was already recorded (prevents duplicates)."""
    return _run("INSERT OR IGNORE INTO alerts(id, symbol, type, candle_time, message) VALUES (?,?,?,?,?)",
                (alert_id, symbol, type_, candle_time, message)).rowcount == 1


def recent_alerts(limit: int = 50) -> list[dict]:
    return [dict(r) for r in _run("SELECT * FROM alerts ORDER BY candle_time DESC, rowid DESC LIMIT ?",
                                  (limit,)).fetchall()]


def get_setting(key: str) -> str | None:
    r = _run("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else None


def set_setting(key: str, value: str):
    _run("INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
         (key, value))


# --- paper trades
def add_trade(**f) -> int:
    cols = ", ".join(f)
    return _run(f"INSERT INTO paper_trades ({cols}) VALUES ({', '.join('?' * len(f))})", tuple(f.values())).lastrowid


def update_trade(trade_id: int, **f):
    sets = ", ".join(f"{k}=?" for k in f)
    _run(f"UPDATE paper_trades SET {sets} WHERE id=?", (*f.values(), trade_id))


def open_trades() -> list[dict]:
    return [dict(r) for r in _run("SELECT * FROM paper_trades WHERE status='open' ORDER BY id").fetchall()]


def all_trades(limit: int = 200) -> list[dict]:
    return [dict(r) for r in _run("SELECT * FROM paper_trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


def hub_traded(hub_key: str) -> bool:
    return _run("SELECT 1 FROM paper_trades WHERE hub_key=? LIMIT 1", (hub_key,)).fetchone() is not None
