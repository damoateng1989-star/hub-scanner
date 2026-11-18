"""Setup journal (owner only): notes on stocks breaking out, with attachments (chart screenshots, PDFs),
tagged by setup and outcome so the setups can be tuned from what actually happened."""
from __future__ import annotations

import base64
import json
import re
import uuid
from pathlib import Path

import db

FILES = db.DATA_DIR / "journal"
FILES.mkdir(parents=True, exist_ok=True)

SETUPS = ["Hub breakout", "Hub breakdown", "Hub retest", "Gap fade & rip", "Double bottom", "Double top",
          "Flush & reclaim", "Floor bounce", "Other"]
OUTCOMES = ["Watching", "Worked", "Failed", "Missed it", "Alert was wrong"]
MAX_FILES, MAX_BYTES = 8, 6 * 1024 * 1024
TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif", "image/heic": ".heic",
         "application/pdf": ".pdf", "text/plain": ".txt", "text/csv": ".csv"}
SAFE = re.compile(r"^\d+_[0-9a-f]{12}\.[a-z]{3,4}$")

with db._lock:
    db._conn.execute("""CREATE TABLE IF NOT EXISTS journal (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, symbol TEXT, setup TEXT, outcome TEXT,
        note TEXT, context TEXT, files TEXT DEFAULT '[]',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    db._conn.commit()


def _row(r) -> dict:
    d = dict(r)
    d["files"] = json.loads(d.get("files") or "[]")
    return d


def _clean(body: dict) -> dict:
    sym = re.sub(r"[^A-Z0-9.\-]", "", str(body.get("symbol") or "").upper())[:12]
    setup = body.get("setup") if body.get("setup") in SETUPS else "Other"
    outcome = body.get("outcome") if body.get("outcome") in OUTCOMES else "Watching"
    return dict(symbol=sym, setup=setup, outcome=outcome, note=str(body.get("note") or "")[:20000],
                context=str(body.get("context") or "")[:2000])


def _save_files(entry_id: int, files: list) -> list[dict]:
    out = []
    for f in files or []:
        typ = str(f.get("type") or "").lower()
        if typ not in TYPES:
            raise ValueError(f"{f.get('name') or 'A file'}: only images, PDFs and text files can be attached")
        raw = str(f.get("data") or "")
        raw = raw.split(",", 1)[1] if raw.startswith("data:") else raw
        blob = base64.b64decode(raw, validate=False)
        if len(blob) > MAX_BYTES:
            raise ValueError(f"{f.get('name') or 'A file'} is over 6 MB")
        name = f"{entry_id}_{uuid.uuid4().hex[:12]}{TYPES[typ]}"
        (FILES / name).write_bytes(blob)
        out.append(dict(file=name, name=str(f.get("name") or name)[:120], type=typ, size=len(blob)))
    return out


def list_entries(uid: int, symbol: str = "", setup: str = "", outcome: str = "") -> list[dict]:
    q, a = "SELECT * FROM journal WHERE user_id=?", [uid]
    if symbol:
        q, a = q + " AND symbol=?", a + [symbol.upper()]
    if setup:
        q, a = q + " AND setup=?", a + [setup]
    if outcome:
        q, a = q + " AND outcome=?", a + [outcome]
    return [_row(r) for r in db._run(q + " ORDER BY created_at DESC, id DESC LIMIT 500", tuple(a)).fetchall()]


def get(uid: int, eid: int) -> dict | None:
    r = db._run("SELECT * FROM journal WHERE id=? AND user_id=?", (eid, uid)).fetchone()
    return _row(r) if r else None


def create(uid: int, body: dict) -> dict:
    c = _clean(body)
    if not c["symbol"] and not c["note"].strip():
        raise ValueError("Add a ticker or a note")
    if len(body.get("files") or []) > MAX_FILES:
        raise ValueError(f"Up to {MAX_FILES} attachments per note")
    cur = db._run("INSERT INTO journal(user_id, symbol, setup, outcome, note, context) VALUES (?,?,?,?,?,?)",
                  (uid, c["symbol"], c["setup"], c["outcome"], c["note"], c["context"]))
    eid = cur.lastrowid
    try:
        files = _save_files(eid, body.get("files"))
    except Exception:
        db._run("DELETE FROM journal WHERE id=?", (eid,))
        raise
    db._run("UPDATE journal SET files=? WHERE id=?", (json.dumps(files), eid))
    return get(uid, eid)


def update(uid: int, eid: int, body: dict) -> dict:
    e = get(uid, eid)
    if not e:
        raise KeyError(eid)
    c = _clean({**e, **{k: v for k, v in body.items() if k in ("symbol", "setup", "outcome", "note", "context")}})
    drop = set(body.get("remove") or [])
    keep = [f for f in e["files"] if f["file"] not in drop]
    for f in e["files"]:
        if f["file"] in drop and SAFE.match(f["file"]):
            (FILES / f["file"]).unlink(missing_ok=True)
    if len(keep) + len(body.get("files") or []) > MAX_FILES:
        raise ValueError(f"Up to {MAX_FILES} attachments per note")
    keep += _save_files(eid, body.get("files"))
    db._run("UPDATE journal SET symbol=?, setup=?, outcome=?, note=?, context=?, files=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND user_id=?", (c["symbol"], c["setup"], c["outcome"], c["note"], c["context"], json.dumps(keep), eid, uid))
    return get(uid, eid)


def delete(uid: int, eid: int):
    e = get(uid, eid)
    if not e:
        return
    for f in e["files"]:
        if SAFE.match(f["file"]):
            (FILES / f["file"]).unlink(missing_ok=True)
    db._run("DELETE FROM journal WHERE id=? AND user_id=?", (eid, uid))


def file_path(uid: int, name: str) -> Path | None:
    """Only files that belong to one of this user's notes."""
    if not SAFE.match(name):
        return None
    eid = int(name.split("_", 1)[0])
    e = get(uid, eid)
    if not e or not any(f["file"] == name for f in e["files"]):
        return None
    p = FILES / name
    return p if p.exists() else None


def summary(uid: int) -> list[dict]:
    """Outcome counts per setup: which setups the notes say are working."""
    rows = db._run("SELECT setup, outcome, COUNT(*) n FROM journal WHERE user_id=? GROUP BY setup, outcome", (uid,)).fetchall()
    by = {}
    for r in rows:
        by.setdefault(r["setup"], {})[r["outcome"]] = r["n"]
    return [dict(setup=s, counts=by[s], total=sum(by[s].values())) for s in SETUPS if s in by]
