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
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE, name TEXT, password_hash TEXT, google_sub TEXT UNIQUE,
        plan TEXT DEFAULT 'free', is_admin INTEGER DEFAULT 0,
        telegram_chat TEXT, link_code TEXT, phone TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY, user_id INTEGER, expires_at TEXT
    );
    CREATE TABLE IF NOT EXISTS reset_tokens (
        token_hash TEXT PRIMARY KEY, user_id INTEGER, expires_at TEXT, used INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS watchlist (
        user_id INTEGER, symbol TEXT, added_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, symbol)
    );
    CREATE TABLE IF NOT EXISTS subscribers (
        email TEXT PRIMARY KEY, source TEXT, token TEXT, status TEXT DEFAULT 'subscribed',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, unsubscribed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, email TEXT, topic TEXT, message TEXT,
        user_id INTEGER, handled INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS board (
        symbol TEXT PRIMARY KEY, status TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS signals (
        id TEXT PRIMARY KEY, symbol TEXT, direction INTEGER,
        hub_low REAL, hub_high REAL, hub_sessions INTEGER,
        breakout_time TEXT, entry REAL, target REAL, target_kind TEXT,
        outcome TEXT, outcome_time TEXT, ret_pct REAL, chase INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS push_subs (
        endpoint TEXT PRIMARY KEY, user_id INTEGER, p256dh TEXT, auth TEXT, device TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS invites (
        code TEXT PRIMARY KEY, note TEXT, max_uses INTEGER DEFAULT 1, uses INTEGER DEFAULT 0,
        active INTEGER DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
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
    try:   # added later: which pattern produced a signal ("hub" or "tightening")
        _conn.execute("ALTER TABLE signals ADD COLUMN pattern TEXT DEFAULT 'hub'")
        _conn.commit()
    except sqlite3.OperationalError:
        pass
    try:   # added later: breakout-candle volume vs normal (information, for the volume split in the track record)
        _conn.execute("ALTER TABLE signals ADD COLUMN rvol REAL")
        _conn.commit()
    except sqlite3.OperationalError:
        pass
    try:   # added later: market regime at breakout time ("with" / "against" the tape), for the regime filter
        _conn.execute("ALTER TABLE signals ADD COLUMN regime TEXT")
        _conn.commit()
    except sqlite3.OperationalError:
        pass
    try:   # added later: hub quality grade ("warehouse" / "ledge") at breakout time, for the grade split
        _conn.execute("ALTER TABLE signals ADD COLUMN grade TEXT")
        _conn.commit()
    except sqlite3.OperationalError:
        pass
    for _col, _ddl in [("setup", "TEXT"), ("stop", "REAL"), ("entry_time", "TEXT")]:
        try:   # added later: the live record of today's rules (which setup, its stop, when the retest entry came)
            _conn.execute(f"ALTER TABLE signals ADD COLUMN {_col} {_ddl}")
            _conn.commit()
        except sqlite3.OperationalError:
            pass
    for _col, _ddl in [("kind", "TEXT DEFAULT 'share'"), ("occ", "TEXT"), ("expiry", "TEXT"), ("strike", "REAL"),
                       ("opt_iv", "REAL"), ("opt_delta", "REAL"), ("opt_premium", "REAL")]:
        try:   # added later: the paper OPTIONS experiment shares the journal table, kept apart by kind
            _conn.execute(f"ALTER TABLE paper_trades ADD COLUMN {_col} {_ddl}")
            _conn.commit()
        except sqlite3.OperationalError:
            pass
    try:   # added later: last sign-in, for the admin dashboard
        _conn.execute("ALTER TABLE users ADD COLUMN last_login_at TEXT")
        _conn.commit()
    except sqlite3.OperationalError:
        pass
    try:   # added later: morning brief on/off (on by default)
        _conn.execute("ALTER TABLE users ADD COLUMN brief INTEGER DEFAULT 1")
        _conn.commit()
    except sqlite3.OperationalError:
        pass
    try:   # added later: which 4H closes to alert on: NULL/"both" = 1:30 and 4:00 PM, "midday" = only the 1:30 PM close
        _conn.execute("ALTER TABLE users ADD COLUMN closes TEXT")
        _conn.commit()
    except sqlite3.OperationalError:
        pass
    try:   # added later: quiet hours "HH:MM-HH:MM" New York time (no calls, silent Telegram); NULL = off
        _conn.execute("ALTER TABLE users ADD COLUMN quiet TEXT")
        _conn.commit()
    except sqlite3.OperationalError:
        pass
    try:   # added later: evening recap on/off (on by default)
        _conn.execute("ALTER TABLE users ADD COLUMN recap INTEGER DEFAULT 1")
        _conn.commit()
    except sqlite3.OperationalError:
        pass
    try:   # added later: which setups a member wants alerts for ("hub,triangle,channel,sr"); NULL = hubs only
        _conn.execute("ALTER TABLE users ADD COLUMN styles TEXT")
        _conn.commit()
    except sqlite3.OperationalError:
        pass


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


def get_alert(alert_id: str) -> dict | None:
    r = _run("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
    return dict(r) if r else None


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


def open_trades(kind: str = "share") -> list[dict]:
    return [dict(r) for r in _run("SELECT * FROM paper_trades WHERE status='open' AND COALESCE(kind,'share')=? ORDER BY id",
                                  (kind,)).fetchall()]


def all_trades(limit: int = 200, kind: str = "share") -> list[dict]:
    return [dict(r) for r in _run("SELECT * FROM paper_trades WHERE COALESCE(kind,'share')=? ORDER BY id DESC LIMIT ?",
                                  (kind, limit)).fetchall()]


def hub_traded(hub_key: str) -> bool:
    return _run("SELECT 1 FROM paper_trades WHERE hub_key=? LIMIT 1", (hub_key,)).fetchone() is not None


# --- hub board
def set_board(symbol: str, status: str):
    _run("INSERT INTO board(symbol, status, updated_at) VALUES (?,?,CURRENT_TIMESTAMP) "
         "ON CONFLICT(symbol) DO UPDATE SET status=excluded.status, updated_at=CURRENT_TIMESTAMP", (symbol, status))


def delete_board(symbol: str):
    _run("DELETE FROM board WHERE symbol=?", (symbol,))


def pending_signal_ids(symbol: str) -> set[str]:
    return {r[0] for r in _run("SELECT id FROM signals WHERE symbol=? AND outcome='pending'", (symbol,)).fetchall()}


def board_rows() -> list[dict]:
    return [dict(r) for r in _run("SELECT * FROM board ORDER BY symbol").fetchall()]


# --- track record
def upsert_signal(**f):
    cols = ", ".join(f)
    updates = ", ".join(f"{k}=excluded.{k}" for k in ("outcome", "outcome_time", "ret_pct", "chase", "target", "target_kind", "stop", "setup", "entry_time") if k in f or k == "outcome") + ", rvol=COALESCE(excluded.rvol, signals.rvol)"
    _run(f"INSERT INTO signals ({cols}) VALUES ({', '.join('?' * len(f))}) ON CONFLICT(id) DO UPDATE SET {updates}",
         tuple(f.values()))


def get_signal(sid: str) -> dict | None:
    r = _run("SELECT * FROM signals WHERE id=?", (sid,)).fetchone()
    return dict(r) if r else None


def all_signals(limit: int = 500, pattern: str = "hub") -> list[dict]:
    return [dict(r) for r in _run("SELECT * FROM signals WHERE COALESCE(pattern,'hub')=? ORDER BY breakout_time DESC LIMIT ?",
                                  (pattern, limit)).fetchall()]


# --- users
USER_FIELDS = ("email", "name", "password_hash", "google_sub", "plan", "is_admin", "telegram_chat", "link_code", "phone", "styles", "brief", "recap", "quiet", "closes")


def create_user(**f) -> int:
    cols = ", ".join(f)
    return _run(f"INSERT INTO users ({cols}) VALUES ({', '.join('?' * len(f))})", tuple(f.values())).lastrowid


def update_user(uid: int, **f):
    assert all(k in USER_FIELDS for k in f)
    _run(f"UPDATE users SET {', '.join(f'{k}=?' for k in f)} WHERE id=?", (*f.values(), uid))


def touch_login(uid: int):
    _run("UPDATE users SET last_login_at=CURRENT_TIMESTAMP WHERE id=?", (uid,))


def get_user(uid: int) -> dict | None:
    r = _run("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return dict(r) if r else None


def user_by(field: str, value) -> dict | None:
    assert field in ("email", "google_sub", "telegram_chat", "link_code")
    r = _run(f"SELECT * FROM users WHERE {field}=?", (value,)).fetchone()
    return dict(r) if r else None


def all_users() -> list[dict]:
    return [dict(r) for r in _run("SELECT * FROM users ORDER BY id").fetchall()]


def user_count() -> int:
    return _run("SELECT COUNT(*) AS n FROM users").fetchone()["n"]


# --- sessions / reset tokens (only hashes are stored)
def add_session(token_hash: str, uid: int, expires_at: str):
    _run("INSERT INTO sessions VALUES (?,?,?)", (token_hash, uid, expires_at))


def session_user(token_hash: str, now: str) -> dict | None:
    r = _run("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?",
             (token_hash, now)).fetchone()
    return dict(r) if r else None


def touch_session(token_hash: str, expires_at: str):
    _run("UPDATE sessions SET expires_at=? WHERE token_hash=?", (expires_at, token_hash))


def delete_session(token_hash: str):
    _run("DELETE FROM sessions WHERE token_hash=?", (token_hash,))


def delete_user_sessions(uid: int):
    _run("DELETE FROM sessions WHERE user_id=?", (uid,))


def add_reset(token_hash: str, uid: int, expires_at: str):
    _run("INSERT INTO reset_tokens(token_hash, user_id, expires_at) VALUES (?,?,?)", (token_hash, uid, expires_at))


def use_reset(token_hash: str, now: str) -> int | None:
    r = _run("SELECT user_id FROM reset_tokens WHERE token_hash=? AND used=0 AND expires_at>?", (token_hash, now)).fetchone()
    if not r:
        return None
    _run("UPDATE reset_tokens SET used=1 WHERE token_hash=?", (token_hash,))
    return r["user_id"]


# --- per-user watchlists (the tickers table is the shared scan set)
def user_symbols(uid: int) -> list[str]:
    return [r["symbol"] for r in _run("SELECT symbol FROM watchlist WHERE user_id=? ORDER BY symbol", (uid,)).fetchall()]


def watch(uid: int, symbol: str) -> bool:
    return _run("INSERT OR IGNORE INTO watchlist(user_id, symbol) VALUES (?,?)", (uid, symbol)).rowcount == 1


def unwatch(uid: int, symbol: str) -> bool:
    ok = _run("DELETE FROM watchlist WHERE user_id=? AND symbol=?", (uid, symbol)).rowcount == 1
    if ok and not _run("SELECT 1 FROM watchlist WHERE symbol=? LIMIT 1", (symbol,)).fetchone():
        remove_ticker(symbol)       # nobody watches it any more: stop scanning it
    return ok


def watchers(symbol: str) -> list[dict]:
    return [dict(r) for r in _run("SELECT u.* FROM watchlist w JOIN users u ON u.id=w.user_id WHERE w.symbol=?",
                                  (symbol,)).fetchall()]


def alerts_for_symbols(symbols: list[str], limit: int = 50, exclude_types: tuple = ()) -> list[dict]:
    if not symbols:
        return []
    q = f"SELECT * FROM alerts WHERE symbol IN ({','.join('?' * len(symbols))})"
    args = list(symbols)
    if exclude_types:
        q += f" AND type NOT IN ({','.join('?' * len(exclude_types))})"
        args += list(exclude_types)
    q += " ORDER BY candle_time DESC, rowid DESC LIMIT ?"
    return [dict(r) for r in _run(q, (*args, limit)).fetchall()]


# --- email list
def add_message(name: str, email: str, topic: str, message: str, user_id: int | None) -> int:
    return _run("INSERT INTO messages(name, email, topic, message, user_id) VALUES (?,?,?,?,?)",
                (name, email, topic, message, user_id)).lastrowid


def list_messages(limit: int = 100) -> list[dict]:
    return [dict(r) for r in _run("SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


def set_message_handled(mid: int, handled: bool):
    _run("UPDATE messages SET handled=? WHERE id=?", (int(handled), mid))


def add_subscriber(email: str, source: str, token: str) -> str:
    """Returns 'new', 'again' (was unsubscribed, now back) or 'exists'."""
    r = _run("SELECT status FROM subscribers WHERE email=?", (email,)).fetchone()
    if not r:
        _run("INSERT INTO subscribers(email, source, token) VALUES (?,?,?)", (email, source, token))
        return "new"
    if r["status"] != "subscribed":
        _run("UPDATE subscribers SET status='subscribed', unsubscribed_at=NULL WHERE email=?", (email,))
        return "again"
    return "exists"


def subscriber_token(email: str) -> str | None:
    r = _run("SELECT token FROM subscribers WHERE email=?", (email,)).fetchone()
    return r["token"] if r else None


def unsubscribe(token: str) -> str | None:
    r = _run("SELECT email FROM subscribers WHERE token=?", (token,)).fetchone()
    if not r:
        return None
    _run("UPDATE subscribers SET status='unsubscribed', unsubscribed_at=CURRENT_TIMESTAMP WHERE token=?", (token,))
    return r["email"]


def subscribers() -> list[dict]:
    return [dict(r) for r in _run("SELECT email, source, status, created_at, unsubscribed_at FROM subscribers "
                                  "ORDER BY created_at DESC").fetchall()]


# --- invite codes (while sign-ups are closed, only people with a code can create an account)
def add_invite(code: str, note: str = "", max_uses: int = 1):
    _run("INSERT INTO invites(code, note, max_uses) VALUES (?,?,?)", (code, note, max_uses))


def invites() -> list[dict]:
    return [dict(r) for r in _run("SELECT * FROM invites ORDER BY created_at DESC").fetchall()]


def invite_ok(code: str) -> bool:
    r = _run("SELECT max_uses, uses, active FROM invites WHERE code=?", ((code or "").strip().upper(),)).fetchone()
    return bool(r and r["active"] and r["uses"] < r["max_uses"])


def use_invite(code: str):
    _run("UPDATE invites SET uses = uses + 1 WHERE code=?", ((code or "").strip().upper(),))


def set_invite_active(code: str, active: bool):
    _run("UPDATE invites SET active=? WHERE code=?", (int(active), code.strip().upper()))


# ---------------------------------------------------------------- web push (iPhone / Android / desktop notifications)
def add_push_sub(user_id: int, endpoint: str, p256dh: str, auth: str, device: str = ""):
    _run("INSERT INTO push_subs (endpoint, user_id, p256dh, auth, device) VALUES (?,?,?,?,?) "
         "ON CONFLICT(endpoint) DO UPDATE SET user_id=excluded.user_id, p256dh=excluded.p256dh, auth=excluded.auth, device=excluded.device",
         (endpoint, user_id, p256dh, auth, device[:120]))


def push_subs(user_id: int) -> list[dict]:
    return [dict(r) for r in _run("SELECT * FROM push_subs WHERE user_id=?", (user_id,)).fetchall()]


def del_push_sub(endpoint: str, user_id: int | None = None):
    if user_id is None:
        _run("DELETE FROM push_subs WHERE endpoint=?", (endpoint,))
    else:
        _run("DELETE FROM push_subs WHERE endpoint=? AND user_id=?", (endpoint, user_id))
