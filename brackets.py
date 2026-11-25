"""Nov 25 (owner): exits attached to an option buy — a "bracket" done the way SnapTrade allows on Webull.

SnapTrade can't open an option position and its exits in one order (opening option OCOs aren't supported). It can
place a one-cancels-other exit pair on a HELD long option: a Limit take-profit and a Stop stop-loss, both
SELL_TO_CLOSE, Day only. So a bracket here is:

  waiting_fill  the buy-to-open is working; checked every minute in market hours
  ready         ('ask me first' mode) it filled, or it's a new day: waiting for the member's tap to place the pair
  armed         it filled: the exit pair is placed (and placed again each morning while the position is held,
                because the pair is Day-only on Webull)
  done          the position is gone (an exit filled, or the member closed it)
  cancelled     the entry never filled (cancelled, rejected, expired), or the member stopped managing it
  failed        the broker refused the exit pair; the member is told at once and nothing is retried that day

Owner-only, like every live order, until the lawyer review. NextShelf places only what the member confirmed: the
entry, and the exit pair they set on the same ticket.
"""
from __future__ import annotations

import logging

import pandas as pd

import db

log = logging.getLogger("brackets")
NY = "America/New_York"
_FILLED = ("FILLED", "EXECUTED")
_DEAD = ("CANCELED", "CANCELLED", "REJECTED", "EXPIRED", "FAILED")


def init() -> None:
    db._conn.executescript("""
    CREATE TABLE IF NOT EXISTS brackets (
      id INTEGER PRIMARY KEY,
      user_id INTEGER NOT NULL,
      account_id TEXT NOT NULL,
      occ TEXT NOT NULL,
      units INTEGER NOT NULL,
      take_profit REAL NOT NULL,
      stop_loss REAL NOT NULL,
      entry_order_id TEXT,
      state TEXT NOT NULL DEFAULT 'waiting_fill',
      armed_on TEXT,                -- the ET date the exit pair was last placed (Day orders)
      note TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT
    );
    CREATE INDEX IF NOT EXISTS brackets_state ON brackets(state);
    """)
    cols = {r[1] for r in db._conn.execute("PRAGMA table_info(brackets)").fetchall()}
    if "mode" not in cols:     # Nov 25 (owner): 'auto' places the exits itself; 'ask' waits for the member's tap
        db._conn.execute("ALTER TABLE brackets ADD COLUMN mode TEXT DEFAULT 'auto'")
    db._conn.commit()


def _now() -> str:
    return pd.Timestamp.now(tz="UTC").isoformat()


def _today() -> str:
    return pd.Timestamp.now(tz=NY).strftime("%Y-%m-%d")


def _set(bid: int, **kv) -> None:
    kv["updated_at"] = _now()
    db._run(f"UPDATE brackets SET {', '.join(f'{k}=?' for k in kv)} WHERE id=?", (*kv.values(), bid))


def _row(bid: int) -> dict:
    return dict(db._run("SELECT * FROM brackets WHERE id=?", (bid,)).fetchone())


def create(user: dict, account_id: str, occ: str, units: int, tp: float, sl: float, entry_order_id: str | None = None,
           state: str = "waiting_fill", mode: str = "auto") -> dict:
    mode = "ask" if mode == "ask" else "auto"
    cur = db._run("""INSERT INTO brackets (user_id, account_id, occ, units, take_profit, stop_loss, entry_order_id, state, created_at, mode)
                     VALUES (?,?,?,?,?,?,?,?,?,?)""", (int(user["id"]), account_id, occ, int(units), float(tp), float(sl),
                                                     entry_order_id, state, _now(), mode))
    return public(_row(cur.lastrowid))


def exits_now(user: dict, account_id: str, occ: str, units: int, tp: float, sl: float, mode: str = "auto") -> dict:
    """'Exits only': the member holds the contract already — place the pair now and keep it armed each morning."""
    import snaptrade
    tp, sl = round(float(tp), 2), round(float(sl), 2)
    if not (0 < sl < tp):
        raise ValueError("The stop-loss must be below the take-profit, both above zero")
    res = snaptrade.option_oco(user, account_id, occ, units, tp, sl)
    b = create(user, account_id, occ, units, tp, sl, state="armed", mode=mode)
    _set(b["id"], armed_on=_today(), note="Exit pair placed")
    out = public(_row(b["id"]))
    out["result"] = res
    return out


def public(r: dict) -> dict:
    word = {"waiting_fill": "Waiting for the buy to fill", "ready": "Waiting for your tap", "armed": "Exits working", "done": "Closed",
            "cancelled": "Not managed", "failed": "Exits refused"}.get(r["state"], r["state"])
    return {k: r.get(k) for k in ("id", "account_id", "occ", "units", "take_profit", "stop_loss", "state", "note", "created_at", "armed_on", "mode")} | {"word": word}


def list_for(user: dict, active_only: bool = False) -> list[dict]:
    q = "SELECT * FROM brackets WHERE user_id=?" + (" AND state IN ('waiting_fill','ready','armed','failed')" if active_only else "") + " ORDER BY id DESC LIMIT 30"
    return [public(dict(r)) for r in db._run(q, (int(user["id"]),)).fetchall()]


def stop(user: dict, bid: int) -> dict:
    r = db._run("SELECT * FROM brackets WHERE id=? AND user_id=?", (bid, int(user["id"]))).fetchone()
    if not r:
        raise LookupError("Not found")
    _set(bid, state="cancelled", note="You stopped managing it. Exit orders already at the broker stay until they fill, expire or you cancel them.")
    return public(_row(bid))


def _tell(user: dict, text: str) -> None:
    try:
        import notify
        notify.user_alert(user, text, "/app#positions", "bracket")
    except Exception as e:
        log.warning("bracket notice failed: %s", e)


def _occ_key(s: str) -> str:
    return "".join(str(s or "").split()).upper()


def tick(now: pd.Timestamp | None = None) -> None:
    """Every minute in market hours: fills arm their exits; held positions get their Day exit pair each morning."""
    import data
    import snaptrade
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    if not data.market_open(now):
        return
    rows = [dict(r) for r in db._run("SELECT * FROM brackets WHERE state IN ('waiting_fill','ready','armed')").fetchall()]
    if not rows:
        return
    today = _today()
    by_acct: dict[tuple, list] = {}
    for r in rows:
        by_acct.setdefault((r["user_id"], r["account_id"]), []).append(r)
    for (uid, acct), group in by_acct.items():
        u = db.get_user(uid)
        if not u or not u.get("is_admin"):          # owner-only until the lawyer review
            continue
        try:
            pf = snaptrade.portfolio(u, acct)
        except Exception as e:
            log.info("bracket check: portfolio failed for %s: %s", acct, e)
            continue
        held = {}
        for p in pf.get("positions") or []:
            if p.get("kind") == "option" and p.get("occ"):
                held[_occ_key(p["occ"])] = held.get(_occ_key(p["occ"]), 0) + (p.get("units") or 0)
        orders = {o["id"]: o for o in pf.get("orders") or [] if o.get("id")}
        for r in group:
            k = _occ_key(r["occ"])
            try:
                if r["state"] == "waiting_fill":
                    o = orders.get(str(r["entry_order_id"] or ""))
                    st = (o or {}).get("status", "")
                    if (o and st in _FILLED) or (not o and held.get(k, 0) >= r["units"]):
                        _arm_or_ask(u, r, today, first=True)
                    elif o and st in _DEAD:
                        _set(r["id"], state="cancelled", note=f"The buy was {st.lower()}, so no exits were placed.")
                        _tell(u, f"Bracket: the buy for {r['occ']} was {st.lower()}. No exits placed.")
                elif r["state"] in ("armed", "ready"):
                    if held.get(k, 0) <= 0:
                        _set(r["id"], state="done", note="The position is closed.")
                        _tell(u, f"Bracket: {r['occ']} is closed. The exit pair is done.")
                    elif r["state"] == "armed" and (r.get("armed_on") or "") < today and now.tz_convert(NY).hour * 60 + now.tz_convert(NY).minute >= 9 * 60 + 31:
                        _arm_or_ask(u, r, today, first=False)      # the pair is Day-only: put it back each morning
            except Exception as e:
                log.warning("bracket #%s check failed: %s", r["id"], e)


def _arm_or_ask(u: dict, r: dict, today: str, first: bool) -> None:
    """'auto' places the pair now; 'ask' waits for the member's tap (and asks again each morning)."""
    if (r.get("mode") or "auto") != "ask":
        return _arm(u, r, today, first)
    _set(r["id"], state="ready", note="The buy filled. Tap to place your exits." if first else "A new day: tap to put your exits back.")
    _tell(u, (f"🟡 Your buy for {r['occ']} filled. Tap to place your exits: take profit ${r['take_profit']:.2f}, stop ${r['stop_loss']:.2f}. "
              f"Until you do, the position has no exits." if first else
              f"🟡 Yesterday's exits for {r['occ']} expired. Tap to put them back for today (take profit ${r['take_profit']:.2f}, stop ${r['stop_loss']:.2f})."))


def arm_now(user: dict, bid: int) -> dict:
    """The member's tap in 'ask' mode: place the pair now."""
    r = db._run("SELECT * FROM brackets WHERE id=? AND user_id=?", (bid, int(user["id"]))).fetchone()
    if not r:
        raise LookupError("Not found")
    r = dict(r)
    if r["state"] not in ("ready", "failed"):
        raise ValueError("These exits aren't waiting for a tap")
    _arm(user, r, _today(), first=not r.get("armed_on"))
    return public(_row(bid))


def _arm(u: dict, r: dict, today: str, first: bool) -> None:
    import snaptrade
    try:
        snaptrade.option_oco(u, r["account_id"], r["occ"], r["units"], r["take_profit"], r["stop_loss"])
    except Exception as e:
        msg = getattr(e, "body", None) or str(e)
        _set(r["id"], state="failed", note=f"The broker refused the exit pair: {str(msg)[:160]}")
        _tell(u, f"⚠️ Bracket: the broker refused the exit pair for {r['occ']}. Your position has no exits. Set them in your broker.")
        return
    _set(r["id"], state="armed", armed_on=today, note="Exit pair placed" + ("" if first else " again for today"))
    if first:
        _tell(u, f"✅ Bracket: the buy for {r['occ']} filled. Exits placed: take profit ${r['take_profit']:.2f}, stop ${r['stop_loss']:.2f} (one cancels the other).")


init()
