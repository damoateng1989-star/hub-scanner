"""Trade Diary (Nov 23): every member's private trade journal.

Trades arrive three ways:
- manual:  the member logs it.
- lookout: a Lookout watch opens a diary trade (open), and closing the watch with an exit closes it.
- broker:  the linked brokerage's filled orders (read-only, via SnapTrade) are paired into round trips. Orders placed
           from the NextShelf ticket carry a tag (setup, plan, whether the setup was ready) so the diary knows why.
Automatic trades never overwrite what the member wrote (why, lesson, grade, setup, timing).
NextShelf only RECORDS trades here; nothing in this module places, changes or cancels an order.
Diaries are private: nothing here feeds the record, the Lab, the board or any aggregate.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone

import db

log = logging.getLogger("diary")

SETUPS = ["Hub breakout", "Hub breakdown", "Hub retest", "Gap fade & rip", "Double bottom", "Double top",
          "Flush & reclaim", "Floor bounce", "Other", "No setup"]
TIMINGS = {"trigger": "Waited for the trigger", "early": "Entered early", "notyet": "Took a \"Not yet\"",
           "against": "Against the plan", "nosetup": "No setup"}
GRADES = ("A", "B", "C", "D")
_MEMBER = ("why", "lesson", "grade", "setup", "timing", "fees", "risk")
_OPENS = {"BUY", "BUY_TO_OPEN", "SELL_SHORT", "SELL_TO_OPEN"}
_CLOSES = {"SELL_TO_CLOSE", "BUY_TO_CLOSE", "BUY_TO_COVER"}
_FILLED = {"EXECUTED", "FILLED", "PARTIAL", "PARTIALLY_FILLED"}
_SYNC_T: dict[int, float] = {}


def init() -> None:
    with db._lock:
        db._conn.executescript("""
        CREATE TABLE IF NOT EXISTS diary_trades (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          ticker TEXT NOT NULL,
          instrument TEXT NOT NULL DEFAULT 'stock',   -- stock | option
          contract TEXT, occ TEXT,
          side INTEGER NOT NULL DEFAULT 1,            -- 1 long, -1 short (the position, not the market view)
          qty REAL, entry_price REAL, exit_price REAL,
          opened_at TEXT, closed_at TEXT,
          fees REAL DEFAULT 0, risk REAL,
          setup TEXT DEFAULT '', grade TEXT DEFAULT '', timing TEXT DEFAULT '',
          why TEXT DEFAULT '', lesson TEXT DEFAULT '',
          plan TEXT,                                  -- {"edge","target","kill"} from the card / Lookout
          source TEXT NOT NULL DEFAULT 'manual',      -- manual | lookout | broker
          src_key TEXT, lookout_id INTEGER,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS diary_src ON diary_trades(user_id, src_key);
        CREATE INDEX IF NOT EXISTS diary_user ON diary_trades(user_id, opened_at);
        CREATE TABLE IF NOT EXISTS diary_tags (
          user_id INTEGER NOT NULL, order_ref TEXT NOT NULL, ticker TEXT, occ TEXT,
          setup TEXT, timing TEXT, plan TEXT, created_at TEXT NOT NULL,
          PRIMARY KEY (user_id, order_ref)
        );
        """)
        db._conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _f(v, lo=None, hi=None):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x or (lo is not None and x < lo) or (hi is not None and x > hi):
        return None
    return x


def _ts(v) -> str | None:
    s = str(v or "").strip()
    if not s:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s + "T14:30:00Z"
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


FUT_MULT = {"MNQ": 2, "MES": 5, "NQ": 20, "ES": 50}


def _mult(r: dict) -> float:
    if r.get("instrument") == "future":         # dollars per index point for the contract (Nov 23)
        return FUT_MULT.get(str(r.get("ticker") or "").upper(), 1)
    return 100 if r.get("instrument") == "option" else 1


def _derive(r: dict) -> dict:
    """P&L and R, always computed from the stored fields (never trusted from the client)."""
    d = dict(r)
    try:
        d["plan"] = json.loads(d.get("plan") or "null")
    except ValueError:
        d["plan"] = None
    e, x, q = d.get("entry_price"), d.get("exit_price"), d.get("qty")
    d["closed"] = x is not None and e is not None and bool(q)
    d["pnl"] = round((x - e) * q * _mult(d) * (d.get("side") or 1) - (d.get("fees") or 0), 2) if d["closed"] else None
    risk = d.get("risk")
    if not risk and e and q:
        if d.get("instrument") == "option" and (d.get("side") or 1) == 1:
            risk = e * q * 100                                  # a bought option can lose its whole premium
        elif d["plan"] and d["plan"].get("kill") and d.get("instrument") in ("stock", "future"):
            risk = abs(e - float(d["plan"]["kill"])) * q * (_mult(d) if d.get("instrument") == "future" else 1)
    d["risk_used"] = round(risk, 2) if risk else None
    d["r"] = round(d["pnl"] / risk, 2) if d["pnl"] is not None and risk else None
    d["needs_note"] = not (d.get("why") or "").strip()
    return d


def _setup(v) -> str:
    # the board's own setup names (e.g. "Hub breakout", a named gap fade) are kept; anything odd is trimmed
    return re.sub(r"[^\w &'+./()-]", "", str(v or "")).strip()[:40]


# ---------------------------------------------------------------- member CRUD
def _clean(body: dict, partial: bool = False) -> dict:
    out: dict = {}
    if not partial or "ticker" in body:
        t = re.sub(r"[^A-Z0-9.\-]", "", str(body.get("ticker") or "").upper())[:12]
        if not t:
            raise ValueError("Enter a ticker")
        out["ticker"] = t
    if not partial or "instrument" in body:
        out["instrument"] = body.get("instrument") if body.get("instrument") in ("option", "future") else "stock"
    if "contract" in body:
        out["contract"] = str(body.get("contract") or "")[:60] or None
    if not partial or "side" in body:
        out["side"] = -1 if str(body.get("side")) in ("-1", "short") else 1
    for k, lo, hi in (("qty", 0, 1e7), ("entry_price", 0, 1e7), ("exit_price", 0, 1e7), ("fees", 0, 1e6), ("risk", 0, 1e8)):
        if not partial or k in body:
            out[k] = _f(body.get(k), lo, hi)
    if not partial or "fees" in body:
        out["fees"] = out.get("fees") or 0
    if not partial and not (out.get("qty") and out.get("entry_price")):
        raise ValueError("Enter the size and your entry price")
    for k in ("opened_at", "closed_at"):
        if not partial or k in body:
            out[k] = _ts(body.get(k))
    if not partial and not out.get("opened_at"):
        out["opened_at"] = _now()
    if "setup" in body:
        out["setup"] = _setup(body.get("setup"))
    if "grade" in body:
        out["grade"] = body.get("grade") if body.get("grade") in GRADES else ""
    if "timing" in body:
        out["timing"] = body.get("timing") if body.get("timing") in TIMINGS else ""
    for k in ("why", "lesson"):
        if k in body:
            out[k] = str(body.get(k) or "")[:4000]
    if out.get("exit_price") is not None and not out.get("closed_at") and (not partial or "closed_at" in body):
        out["closed_at"] = out.get("opened_at") or _now()
    return out


def list_trades(user: dict) -> dict:
    rows = db._run("SELECT * FROM diary_trades WHERE user_id=? ORDER BY COALESCE(closed_at, opened_at) DESC, id DESC LIMIT 3000",
                   (user["id"],)).fetchall()
    return dict(trades=[_derive(dict(r)) for r in rows], setups=SETUPS, timings=TIMINGS, grades=list(GRADES),
                broker=_broker_ok(user), synced_at=db.get_setting(f"diary_sync_{user['id']}"))


def get(user: dict, tid: int) -> dict:
    r = db._run("SELECT * FROM diary_trades WHERE id=? AND user_id=?", (tid, user["id"])).fetchone()
    if not r:
        raise LookupError("Trade not found")
    return _derive(dict(r))


def create(user: dict, body: dict) -> dict:
    c = _clean(body)
    c.update(user_id=user["id"], source="manual", created_at=_now(), updated_at=_now())
    cols = ",".join(c)
    with db._lock:
        cur = db._conn.execute(f"INSERT INTO diary_trades ({cols}) VALUES ({','.join('?' * len(c))})", tuple(c.values()))
        db._conn.commit()
    return get(user, cur.lastrowid)


def update(user: dict, tid: int, body: dict) -> dict:
    cur = get(user, tid)
    c = _clean(body, partial=True)
    if cur["source"] == "broker":            # the broker's fills are the truth for an automatic trade
        for k in ("ticker", "instrument", "side", "qty", "entry_price", "exit_price", "opened_at", "closed_at", "contract"):
            c.pop(k, None)
    if not c:
        return cur
    c["updated_at"] = _now()
    with db._lock:
        db._conn.execute(f"UPDATE diary_trades SET {','.join(k + '=?' for k in c)} WHERE id=? AND user_id=?",
                         (*c.values(), tid, user["id"]))
        db._conn.commit()
    return get(user, tid)


def delete(user: dict, tid: int) -> None:
    get(user, tid)
    with db._lock:
        db._conn.execute("DELETE FROM diary_trades WHERE id=? AND user_id=?", (tid, user["id"]))
        db._conn.commit()


# ---------------------------------------------------------------- NextShelf order tags
def tag_order(user: dict, body: dict) -> dict:
    """Called by the options ticket after a successful order: why it was taken, so the broker sync can file it."""
    ref = str(body.get("order_ref") or "")[:80]
    occ = re.sub(r"\s+", "", str(body.get("occ") or "").upper())[:30] or None
    ticker = re.sub(r"[^A-Z0-9.\-]", "", str(body.get("ticker") or "").upper())[:12]
    if not ref:
        ref = f"occ:{occ or ticker}:{int(time.time())}"
    plan = body.get("plan") if isinstance(body.get("plan"), dict) else None
    plan = {k: _f(plan.get(k), 0) for k in ("edge", "target", "kill")} if plan else None
    with db._lock:
        db._conn.execute("INSERT OR REPLACE INTO diary_tags (user_id, order_ref, ticker, occ, setup, timing, plan, created_at) VALUES (?,?,?,?,?,?,?,?)",
                         (user["id"], ref, ticker, occ, _setup(body.get("setup")),
                          body.get("timing") if body.get("timing") in TIMINGS else "", json.dumps(plan) if plan else None, _now()))
        db._conn.commit()
    return dict(ok=True)


def _tag_for(uid: int, order_id: str, key: str, opened: str) -> dict | None:
    r = db._run("SELECT * FROM diary_tags WHERE user_id=? AND order_ref=?", (uid, order_id)).fetchone()
    if r:
        return dict(r)
    # the ticket may not know the broker's order id: fall back to the same contract tagged within a day before the fill
    rows = db._run("SELECT * FROM diary_tags WHERE user_id=? AND (occ=? OR (occ IS NULL AND ticker=?)) ORDER BY created_at DESC",
                   (uid, key, key)).fetchall()
    for r in rows:
        if _hours(r["created_at"], opened) is not None and -2 <= _hours(r["created_at"], opened) <= 26:
            return dict(r)
    return None


def _hours(a: str, b: str):
    try:
        da = datetime.fromisoformat(str(a).replace("Z", "+00:00"))
        dbb = datetime.fromisoformat(str(b).replace("Z", "+00:00"))
        if da.tzinfo is None:
            da = da.replace(tzinfo=timezone.utc)
        if dbb.tzinfo is None:
            dbb = dbb.replace(tzinfo=timezone.utc)
        return (dbb - da).total_seconds() / 3600
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------- Lookout
def _lookout_setup(kind: str, direction: int) -> str:
    k = str(kind or "hub")
    if k.startswith("double"):
        return "Double top" if direction == -1 else "Double bottom"
    if k.startswith("gap"):
        return "Gap fade & rip"
    return "Hub breakdown" if direction == -1 else "Hub breakout"


def from_lookout_start(user: dict, w: dict) -> None:
    """A Lookout watch opens an (open) diary trade, unless the broker sync already has this position."""
    try:
        if not w.get("entry_price") or not w.get("qty"):
            return
        occ = w.get("contract_occ")
        if occ and db._run("SELECT 1 FROM diary_trades WHERE user_id=? AND occ=? AND exit_price IS NULL", (user["id"], occ)).fetchone():
            db._run("UPDATE diary_trades SET lookout_id=?, plan=COALESCE(plan, ?) WHERE user_id=? AND occ=? AND exit_price IS NULL",
                    (w["id"], json.dumps(dict(edge=w.get("edge"), target=w.get("target"), kill=w.get("kill"))), user["id"], occ))
            db._conn.commit()
            return
        c = dict(user_id=user["id"], ticker=w["ticker"], instrument="option" if w.get("contract") else "stock",
                 contract=w.get("contract"), occ=occ, side=1 if (w.get("contract") or w.get("direction", 1) == 1) else -1,
                 qty=float(w["qty"]), entry_price=float(w["entry_price"]), opened_at=_now(), fees=0,
                 setup=_lookout_setup(w.get("kind"), w.get("direction", 1)), timing="",
                 plan=json.dumps(dict(edge=w.get("edge"), target=w.get("target"), kill=w.get("kill"))),
                 source="lookout", src_key=f"lk:{w['id']}", lookout_id=w["id"], created_at=_now(), updated_at=_now())
        with db._lock:
            db._conn.execute(f"INSERT OR IGNORE INTO diary_trades ({','.join(c)}) VALUES ({','.join('?' * len(c))})", tuple(c.values()))
            db._conn.commit()
    except Exception as e:
        log.warning("diary lookout start skipped: %s", e)


def from_lookout_close(user_id: int, watch_id: int, exit_price) -> None:
    try:
        xp = _f(exit_price, 0)
        if xp is None:
            return
        db._run("UPDATE diary_trades SET exit_price=?, closed_at=?, updated_at=? WHERE user_id=? AND lookout_id=? AND source='lookout' AND exit_price IS NULL",
                (xp, _now(), _now(), user_id, watch_id))
        db._conn.commit()
    except Exception as e:
        log.warning("diary lookout close skipped: %s", e)


# ---------------------------------------------------------------- broker sync (read-only)
def _broker_ok(user: dict) -> bool:
    try:
        import snaptrade
        ok = bool(user.get("is_admin")) or int(user["id"]) in set(json.loads(db.get_setting("broker_testers") or "[]"))   # owner + testers
        return bool(snaptrade.configured() and ok and snaptrade._db_secret(snaptrade._uid(user)))
    except Exception:
        return False


def _signed(action: str, qty: float, pos: float) -> float | None:
    a = action.upper()
    if a in ("BUY", "BUY_TO_OPEN", "BUY_TO_CLOSE", "BUY_TO_COVER"):
        return qty
    if a in ("SELL", "SELL_TO_CLOSE", "SELL_SHORT", "SELL_TO_OPEN"):
        return -qty
    return None


def round_trips(fills: list[dict]) -> list[dict]:
    """Pair filled orders (oldest first) into trades per instrument. A close with nothing open (the opening order is
    older than the history window) is skipped rather than guessed."""
    by: dict[str, list] = {}
    for f in fills:     # per account too (Nov 23: Webull + Robinhood), so a buy in one never pairs with a sell in the other
        by.setdefault((f.get("acct") or "", f["key"]), []).append(f)
    out = []
    for (_acct, key), fs in by.items():
        pos, cur = 0.0, None
        for f in fs:
            s = _signed(f["action"], f["qty"], pos)
            if s is None or not f["qty"] or f["price"] is None:
                continue
            if pos == 0:
                if f["action"] in _CLOSES or (f["action"] == "SELL" and f["kind"] == "stock"):
                    continue
                cur = dict(key=key, kind=f["kind"], ticker=f["ticker"], occ=f.get("occ"), contract=f.get("contract"),
                           side=1 if s > 0 else -1, open_qty=0.0, open_cost=0.0, close_qty=0.0, close_val=0.0,
                           opened_at=f["time"], closed_at=None, first_id=f["id"], ids=[])
            same = (s > 0) == (cur["side"] > 0)
            q = abs(s)
            if same:
                cur["open_qty"] += q
                cur["open_cost"] += q * f["price"]
                pos += s
            else:
                q = min(q, abs(pos))
                cur["close_qty"] += q
                cur["close_val"] += q * f["price"]
                pos += q if s > 0 else -q
            cur["ids"].append(f["id"])
            if abs(pos) < 1e-9:
                pos = 0.0
                cur["closed_at"] = f["time"]
                out.append(cur)
                cur = None
        if cur:
            out.append(cur)
    for t in out:
        t["entry"] = round(t["open_cost"] / t["open_qty"], 4) if t["open_qty"] else None
        t["exit"] = round(t["close_val"] / t["close_qty"], 4) if t["closed_at"] and t["close_qty"] else None
        t["qty"] = t["open_qty"]
    return out


def _fills(orders: list[dict]) -> list[dict]:
    out = []
    for o in orders:
        if str(o.get("status") or "").upper() not in _FILLED or not (o.get("filled") or 0) > 0:
            continue
        opt = o.get("kind") == "option"
        key = (o.get("occ") if opt else o.get("symbol")) or ""
        if not key:
            continue
        label = None
        if opt and o.get("expiry"):
            d = datetime.strptime(o["expiry"], "%Y-%m-%d")
            k = o.get("strike")
            label = f"{o.get('underlying')} {d.strftime('%b')} {d.day} ${k:g} {'Call' if o.get('option_type') == 'CALL' else 'Put'}"
        out.append(dict(id=str(o.get("id") or ""), key=key, kind="option" if opt else "stock",
                        ticker=(o.get("underlying") if opt else o.get("symbol")) or key, occ=o.get("occ") if opt else None,
                        contract=label, action=str(o.get("action") or "").upper(), qty=float(o.get("filled") or 0),
                        price=o.get("avg_fill"), time=_ts(o.get("updated") or o.get("placed")) or _now(), acct=str(o.get("account_id") or "")))
    return sorted(out, key=lambda f: f["time"])


def sync_broker(user: dict, force: bool = False) -> dict:
    if not _broker_ok(user):
        return dict(ok=False, reason="No linked brokerage")
    if not force and time.time() - _SYNC_T.get(user["id"], 0) < 120:
        return dict(ok=True, skipped=True)
    _SYNC_T[user["id"]] = time.time()
    import snaptrade
    trips = round_trips(_fills(snaptrade.order_history(dict(user), days=90)))
    added = updated = 0
    for t in trips:
        src = f"brk:{t['key']}:{t['first_id']}"
        row = db._run("SELECT * FROM diary_trades WHERE user_id=? AND src_key=?", (user["id"], src)).fetchone()
        base = dict(ticker=t["ticker"][:12], instrument=t["kind"], contract=t["contract"], occ=t["occ"], side=t["side"],
                    qty=t["qty"], entry_price=t["entry"], exit_price=t["exit"], opened_at=t["opened_at"], closed_at=t["closed_at"])
        if row:
            with db._lock:
                db._conn.execute(f"UPDATE diary_trades SET {','.join(k + '=?' for k in base)}, updated_at=? WHERE id=?",
                                 (*base.values(), _now(), row["id"]))
                db._conn.commit()
            updated += 1
            continue
        extra = dict(setup="", timing="", why="", lesson="", grade="", plan=None, lookout_id=None, fees=0, risk=None)
        tag = _tag_for(user["id"], t["first_id"], t["key"], t["opened_at"])
        if tag:
            extra.update(setup=tag.get("setup") or "", timing=tag.get("timing") or "", plan=tag.get("plan"))
        # a Lookout trade for the same contract opened around the same time becomes this trade (keeping what was written)
        lk = db._run("""SELECT * FROM diary_trades WHERE user_id=? AND source='lookout' AND (occ=? OR (occ IS NULL AND ticker=? AND instrument=?))
                        ORDER BY opened_at DESC""", (user["id"], t["occ"], t["ticker"], t["kind"])).fetchall()
        lk = next((dict(r) for r in lk if (h := _hours(t["opened_at"], r["opened_at"])) is not None and abs(h) <= 72), None)
        if lk:
            for k in _MEMBER + ("plan", "lookout_id"):
                if lk.get(k) not in (None, ""):
                    extra[k] = lk[k]
        c = dict(user_id=user["id"], **base, **extra, source="broker", src_key=src, created_at=_now(), updated_at=_now())
        with db._lock:
            if lk:
                db._conn.execute("DELETE FROM diary_trades WHERE id=?", (lk["id"],))
            db._conn.execute(f"INSERT INTO diary_trades ({','.join(c)}) VALUES ({','.join('?' * len(c))})", tuple(c.values()))
            db._conn.commit()
        added += 1
    db.set_setting(f"diary_sync_{user['id']}", _now())
    return dict(ok=True, added=added, updated=updated)
init()      # the tables exist as soon as anything imports the diary
