"""Chat (Nov 23): member chat rooms.

- Pro members (and the owner) post; every signed-in member reads.
- Four topic rooms. Messages are plain text; $TICKER tags become chips on the page.
- Moderation: a filter blocks links, contact details and pump phrases before a message posts; members report
  messages to an Admin queue, where the owner deletes them or mutes the author.
- Names shown are "First L." from the account name, never an email or phone number.
NextShelf's own content rules apply: setups and lessons, not tips. Nothing here feeds the record or the board.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone

import db

ROOMS = [
    ("general", "general", "Anything about trading the NextShelf way"),
    ("hub-breakouts", "hub-breakouts", "Hubs, retests and the 4-hour verdicts"),
    ("options", "options", "Contracts, spreads, IV and timing"),
    ("wins-and-lessons", "wins-and-lessons", "What worked, what didn't, what you'd do better"),
]
ROOM_IDS = {r[0] for r in ROOMS}
MAX_LEN = 800

_LINK = re.compile(r"(https?://|www\.|\b[a-z0-9-]+\.(com|net|org|io|co|ly|gg|me|app|xyz|info|biz|us|link|to)\b|t\.me/|discord\.gg)", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE = re.compile(r"(?<!\$)(?<![\d.])(\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?![\d.])")
_PUMP = [
    "guaranteed", "guarantee", "can't lose", "cant lose", "free money", "100% sure", "easy money", "to the moon",
    "buy now", "buy it now", "sell now", "load up", "all in", "go all in", "next 10x", "10x", "100x", "don't miss",
    "dont miss", "last chance", "dm me", "message me", "text me", "whatsapp", "telegram", "signal group", "join my",
    "pump", "insider", "inside info",
]


def init() -> None:
    with db._lock:
        db._conn.executescript("""
        CREATE TABLE IF NOT EXISTS chat_messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          room TEXT NOT NULL, user_id INTEGER NOT NULL, body TEXT NOT NULL,
          created_at TEXT NOT NULL, deleted_at TEXT, deleted_by INTEGER
        );
        CREATE INDEX IF NOT EXISTS chat_room ON chat_messages(room, id);
        CREATE TABLE IF NOT EXISTS chat_reports (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          message_id INTEGER NOT NULL, reporter_id INTEGER NOT NULL, reason TEXT,
          created_at TEXT NOT NULL, resolved_at TEXT, resolution TEXT,
          UNIQUE(message_id, reporter_id)
        );
        CREATE TABLE IF NOT EXISTS chat_mutes (
          user_id INTEGER PRIMARY KEY, until TEXT NOT NULL, reason TEXT, by_user INTEGER, created_at TEXT NOT NULL
        );
        """)
        db._conn.commit()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def display_name(row: dict) -> str:
    raw = str(row.get("name") or "")
    n = re.sub(r"[^\w' .-]", "", raw).strip()
    if n and "@" not in raw and not re.search(r"\d{4,}|\.(com|net|org|io)\b", raw, re.I):   # never an email or a number
        parts = n.split()
        return (parts[0][:18] + (" " + parts[-1][0].upper() + "." if len(parts) > 1 else "")).strip()
    return "Member " + str(int(hashlib.sha1(f"ns-chat-{row.get('id')}".encode()).hexdigest()[:6], 16) % 9000 + 1000)


def check(body: str) -> str | None:
    """Why a message can't post, in plain words, or None if it's fine."""
    t = body.strip()
    if not t:
        return "Write a message first."
    if len(t) > MAX_LEN:
        return f"Keep it under {MAX_LEN} characters."
    if _LINK.search(t):
        return "Links aren't allowed in chat. Tag a ticker with $ instead, like $WMT."
    if _EMAIL.search(t) or _PHONE.search(t):
        return "No emails or phone numbers in chat. Keep contact details private."
    low = t.lower()
    hit = next((p for p in _PUMP if re.search(r"(?<!\w)" + re.escape(p) + r"(?!\w)", low)), None)
    if hit:
        return f"\"{hit}\" isn't allowed here. Share the setup and your reasoning, not a tip or a promise."
    if len(re.findall(r"[A-Z]", t)) > 30 and sum(c.isupper() for c in t) / max(1, sum(c.isalpha() for c in t)) > 0.7:
        return "Easy on the caps."
    return None


def muted_until(uid: int) -> str | None:
    r = db._run("SELECT until FROM chat_mutes WHERE user_id=?", (uid,)).fetchone()
    return r["until"] if r and r["until"] > _iso(_now()) else None


def _row(r: dict, me: dict, reported: set) -> dict:
    deleted = bool(r.get("deleted_at"))
    return dict(id=r["id"], room=r["room"], at=r["created_at"], name=display_name(dict(id=r["user_id"], name=r.get("name"))),
                badge="Owner" if r.get("is_admin") else "Pro" if (r.get("plan") or "free") != "free" else "",
                body="" if deleted else r["body"], deleted=deleted, mine=r["user_id"] == me["id"],
                reported=r["id"] in reported, seed=r["user_id"] % 6)


def messages(me: dict, room: str, after: int = 0, before: int = 0, limit: int = 80) -> dict:
    if room not in ROOM_IDS:
        raise LookupError("No such room")
    q = """SELECT m.*, u.name, u.plan, u.is_admin FROM chat_messages m LEFT JOIN users u ON u.id=m.user_id WHERE m.room=?"""
    args: list = [room]
    if after:
        q += " AND m.id>? ORDER BY m.id ASC LIMIT ?"
        args += [after, limit]
    else:
        if before:
            q += " AND m.id<?"
            args.append(before)
        q += " ORDER BY m.id DESC LIMIT ?"
        args.append(limit)
    rows = [dict(r) for r in db._run(q, tuple(args)).fetchall()]
    if not after:
        rows.reverse()
    reported = {r["message_id"] for r in db._run("SELECT message_id FROM chat_reports WHERE reporter_id=?", (me["id"],)).fetchall()}
    return dict(room=room, messages=[_row(r, me, reported) for r in rows])


def post(me: dict, room: str, body: str, can_post: bool) -> dict:
    if room not in ROOM_IDS:
        raise LookupError("No such room")
    if not can_post:
        raise PermissionError("Posting is for Pro members. You can read every room.")
    m = muted_until(me["id"])
    if m:
        raise PermissionError("You're muted in chat for now. You can still read every room.")
    why = check(body)
    if why:
        raise ValueError(why)
    recent = db._run("SELECT COUNT(*) n FROM chat_messages WHERE user_id=? AND created_at>?",
                     (me["id"], _iso(_now() - timedelta(minutes=1)))).fetchone()["n"]
    if recent >= 8:
        raise ValueError("You're posting fast. Give it a minute.")
    text = re.sub(r"\n{3,}", "\n\n", body.strip())
    with db._lock:
        cur = db._conn.execute("INSERT INTO chat_messages (room, user_id, body, created_at) VALUES (?,?,?,?)",
                               (room, me["id"], text, _iso(_now())))
        db._conn.commit()
    r = dict(db._run("""SELECT m.*, u.name, u.plan, u.is_admin FROM chat_messages m LEFT JOIN users u ON u.id=m.user_id WHERE m.id=?""",
                     (cur.lastrowid,)).fetchone())
    return _row(r, me, set())


def delete(me: dict, mid: int) -> None:
    r = db._run("SELECT * FROM chat_messages WHERE id=?", (mid,)).fetchone()
    if not r:
        raise LookupError("Message not found")
    if r["user_id"] != me["id"] and not me.get("is_admin"):
        raise PermissionError("You can only delete your own messages.")
    with db._lock:
        db._conn.execute("UPDATE chat_messages SET deleted_at=?, deleted_by=? WHERE id=?", (_iso(_now()), me["id"], mid))
        db._conn.execute("UPDATE chat_reports SET resolved_at=?, resolution='deleted' WHERE message_id=? AND resolved_at IS NULL",
                         (_iso(_now()), mid))
        db._conn.commit()


def report(me: dict, mid: int, reason: str) -> None:
    r = db._run("SELECT user_id FROM chat_messages WHERE id=? AND deleted_at IS NULL", (mid,)).fetchone()
    if not r:
        raise LookupError("Message not found")
    if r["user_id"] == me["id"]:
        raise ValueError("That's your own message.")
    with db._lock:
        db._conn.execute("INSERT OR IGNORE INTO chat_reports (message_id, reporter_id, reason, created_at) VALUES (?,?,?,?)",
                         (mid, me["id"], str(reason or "")[:200], _iso(_now())))
        db._conn.commit()


# ---------------------------------------------------------------- Admin
def queue() -> list[dict]:
    rows = db._run("""SELECT r.message_id, COUNT(*) n, MIN(r.created_at) first_at, GROUP_CONCAT(r.reason, ' | ') reasons,
                             m.body, m.room, m.user_id, m.created_at, u.name, u.email
                      FROM chat_reports r JOIN chat_messages m ON m.id=r.message_id LEFT JOIN users u ON u.id=m.user_id
                      WHERE r.resolved_at IS NULL AND m.deleted_at IS NULL GROUP BY r.message_id ORDER BY n DESC, first_at""").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["author"] = display_name(dict(id=d["user_id"], name=d.get("name")))
        d["muted_until"] = muted_until(d["user_id"])
        d.pop("email", None)
        out.append(d)
    return out


def resolve(mid: int, action: str) -> None:
    with db._lock:
        db._conn.execute("UPDATE chat_reports SET resolved_at=?, resolution=? WHERE message_id=? AND resolved_at IS NULL",
                         (_iso(_now()), action[:20], mid))
        db._conn.commit()


def mute(admin: dict, uid: int, hours: int, reason: str = "") -> str:
    until = _iso(_now() + timedelta(hours=max(1, min(int(hours), 24 * 365))))
    with db._lock:
        db._conn.execute("INSERT OR REPLACE INTO chat_mutes (user_id, until, reason, by_user, created_at) VALUES (?,?,?,?,?)",
                         (uid, until, reason[:200], admin["id"], _iso(_now())))
        db._conn.commit()
    return until


def unmute(uid: int) -> None:
    with db._lock:
        db._conn.execute("DELETE FROM chat_mutes WHERE user_id=?", (uid,))
        db._conn.commit()


init()
