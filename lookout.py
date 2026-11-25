"""Patch 208: Lookout. NextShelf watches a member's position against the plan the card showed them and taps them when
something changes. It NEVER places, modifies or closes an order (Nov 11: the user manages the trade; Nov 17: no auto-trading).

- kill:     on each 4H close (1:30 PM and 4:00 PM verdicts), a close back past `kill` -> 'dead'. Closes only, never wicks.
- target:   per-minute, first touch of `target` -> 'target_hit' (the watch stays open; one alert only).
- clock:    the first 4H close after `window_end` -> 'window_closed'. window_end = the engine's horizon (10 four-hour
            candles from the breakout), the same clock the published record uses for "ran out of time".
- earnings: at the close of the session before `earnings_date`, one alert.
- closes:   'I'm out' by the member; auto-close 24h after 'dead' / 'window_closed'; and (owner with a connected broker)
            when the watched contract has left the account after having been seen there.
Members' watches are private: nothing here feeds the record, the Lab, the board or any aggregate.
Every evaluation is fail-open per row: a bad row is logged and skipped.
"""
from __future__ import annotations

import json
import logging
import math
import re
import time

import pandas as pd

import db

log = logging.getLogger("lookout")
NY = "America/New_York"
_LIVE = ("watching", "target_hit")            # still evaluated for kill / clock / earnings
_OPEN = ("watching", "target_hit", "dead", "window_closed")   # shown as live on Today until closed


def init() -> None:
    db._conn.executescript("""
    CREATE TABLE IF NOT EXISTS watched_positions (
      id INTEGER PRIMARY KEY,
      user_id INTEGER NOT NULL,
      ticker TEXT NOT NULL,
      kind TEXT,                -- hub | double | gapfade
      direction INTEGER NOT NULL DEFAULT 1,   -- 1 long (calls / shares), -1 short (puts)
      contract TEXT,            -- e.g. "WMT Dec 4 $128 Call"; NULL for shares
      contract_occ TEXT,        -- e.g. "WMT261204C00128000"; links the watch to the broker position
      qty REAL,
      entry_price REAL,
      edge REAL, target REAL, kill REAL,
      window_end TEXT,
      earnings_date TEXT,
      state TEXT NOT NULL DEFAULT 'watching',
      state_at TEXT,
      alerted TEXT DEFAULT '',  -- which one-time alerts fired: target,clock,earnings,dead
      last_close TEXT,          -- the last 4H candle evaluated (no double verdicts)
      seen_position INTEGER DEFAULT 0,
      created_at TEXT NOT NULL,
      closed_at TEXT, close_reason TEXT, exit_price REAL
    );
    CREATE INDEX IF NOT EXISTS watched_user ON watched_positions(user_id, state);
    """)
    db._conn.commit()


def _now() -> str:
    return pd.Timestamp.now(tz="UTC").isoformat()


def _rows(where: str = "", args: tuple = ()) -> list[dict]:
    return [dict(r) for r in db._run(f"SELECT * FROM watched_positions {where}", args).fetchall()]


def _set(row_id: int, **kv) -> None:
    keys = ", ".join(f"{k}=?" for k in kv)
    db._run(f"UPDATE watched_positions SET {keys} WHERE id=?", (*kv.values(), row_id))


def _money(v) -> str:
    return f"${v:,.2f}"


# ---------------------------------------------------------------- the plan's clock
def window_end_for(ticker: str) -> str:
    """ISO date the plan's clock runs out: the breakout candle + the engine's horizon (10 four-hour candles = 5 sessions).
    Before a breakout (or for a setup without a pending hub trade) the clock starts now."""
    import engine
    import scanner
    try:
        c4 = scanner.load_4h(ticker, scanner.SCAN_DAYS)
        res = engine.analyze(c4, scanner.SETTINGS)
        n, H = len(c4), scanner.SETTINGS.horizon
        t = next((t for t in res["trades"] if t["outcome"] == "pending"), None)
        left = H - (n - 1 - t["i0"]) if t else H
        last = c4.index[-1].tz_convert(NY).normalize().tz_localize(None)
    except Exception:
        left, last = 10, pd.Timestamp.now(tz=NY).normalize().tz_localize(None)
    days = max(1, math.ceil(max(left, 1) / 2))          # two 4H candles per session
    return (last + pd.offsets.BDay(days)).date().isoformat()


# ---------------------------------------------------------------- start / list / close
_OCC = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")


def cross_window_end(ticker: str) -> str | None:
    """Patch 218 §8: a cross watch's clock — 10 four-hour closes counted from the cross candle's own close."""
    try:
        import alert_format
        import cross
        import engine
        r = cross.for_symbol(ticker)
        if not r or r["state"] != "live":
            return None
        row = db._run("SELECT cross_candle FROM cross_setups WHERE id=?", (r["id"],)).fetchone()
        end = engine.candle_end(pd.Timestamp(row["cross_candle"]).tz_convert(NY))
        t = alert_format._closes_after(end.date(), end.hour * 60 + end.minute, cross.HORIZON - 1)
        return t.date().isoformat() if t is not None else None
    except Exception:
        return None


def preview(ticker: str, kind: str = "hub") -> dict:
    """What the window says before the member opts in: the date the plan's clock runs out and any earnings inside it."""
    t = str(ticker or "").strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", t):
        raise ValueError("Unknown ticker")
    wend = (cross_window_end(t) if kind == "cross" else None) or window_end_for(t)
    try:
        import earnings
        e = earnings.lookup(t)
        edate = e["date"] if e and e["date"] <= wend else None
    except Exception:
        edate = None
    return dict(window_end=wend, earnings_date=edate)


def start_watch(user: dict, p: dict) -> dict:
    ticker = str(p.get("ticker") or "").strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", ticker):
        raise ValueError("Unknown ticker")
    d = -1 if int(p.get("direction") or 1) == -1 else 1
    try:
        edge, target, kill = (float(p[k]) for k in ("edge", "target", "kill"))
    except (KeyError, TypeError, ValueError):
        raise ValueError("The plan needs a trigger, a target and a stop")
    if not all(x > 0 and math.isfinite(x) for x in (edge, target, kill)):
        raise ValueError("The plan needs a trigger, a target and a stop")
    if d == 1 and not (kill < target) or d == -1 and not (kill > target):
        raise ValueError("The stop and target are on the wrong sides for this direction")
    occ = re.sub(r"\s+", "", str(p.get("contract_occ") or "").upper()) or None
    if occ and not _OCC.match(occ):
        occ = None
    num = lambda k: (lambda v: v if v and v > 0 else None)(float(p[k])) if p.get(k) not in (None, "") else None
    if db._run("SELECT 1 FROM watched_positions WHERE user_id=? AND ticker=? AND state IN ('watching','target_hit') AND IFNULL(contract_occ,'')=IFNULL(?,'')",
               (user["id"], ticker, occ)).fetchone():
        raise ValueError("You're already watching this position")
    try:
        import earnings
        e = earnings.lookup(ticker)
        edate = e["date"] if e else None
    except Exception:
        edate = None
    wend = (cross_window_end(ticker) if str(p.get("kind") or "") == "cross" else None) or window_end_for(ticker)
    if edate and edate > wend:
        edate = None                                      # outside the watch: no earnings-eve alert
    cur = db._run("""INSERT INTO watched_positions (user_id, ticker, kind, direction, contract, contract_occ, qty, entry_price,
        edge, target, kill, window_end, earnings_date, state, state_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'watching', ?, ?)""",
        (user["id"], ticker, str(p.get("kind") or "hub")[:20], d, (str(p.get("contract") or "")[:60] or None), occ,
         num("qty"), num("entry_price"), edge, target, kill, wend, edate, _now(), _now()))
    # fix 3: the ticket's touch-based safety-net alerts would contradict a close-based watch; Lookout replaces them
    off = db._run("DELETE FROM price_alerts WHERE user_id=? AND symbol=? AND fired_at IS NULL AND label IN ('trigger','entry','stop','target')",
                  (user["id"], ticker)).rowcount
    try:   # Nov 23: the watch also opens a trade in the member's Trade Diary
        import diary
        w = db._run("SELECT * FROM watched_positions WHERE id=?", (cur.lastrowid,)).fetchone()
        if w:
            diary.from_lookout_start(user, dict(w))
    except Exception as e:
        log.warning("diary link skipped: %s", e)
    return dict(id=cur.lastrowid, window_end=wend, earnings_date=edate, safety_alerts_off=off)


def mine(user: dict) -> dict:
    rows = _rows("WHERE user_id=? ORDER BY id DESC LIMIT 60", (user["id"],))
    today = pd.Timestamp.now(tz=NY).normalize().tz_localize(None)
    for r in rows:
        r.pop("alerted", None); r.pop("last_close", None)
        try:
            r["days_left"] = max(0, len(pd.bdate_range(today, pd.Timestamp(r["window_end"]))) - 1)
        except Exception:
            r["days_left"] = None
    return dict(live=[r for r in rows if r["state"] in _OPEN], closed=[r for r in rows if r["state"] == "closed"][:30])


def close_watch(user: dict, row_id: int, exit_price=None, reason: str = "member") -> dict:
    r = db._run("SELECT * FROM watched_positions WHERE id=? AND user_id=?", (row_id, user["id"])).fetchone()
    if not r:
        raise LookupError("Watch not found")
    try:
        xp = float(exit_price) if exit_price not in (None, "") else None
    except (TypeError, ValueError):
        xp = None
    _set(row_id, state="closed", state_at=_now(), closed_at=_now(), close_reason=reason, exit_price=xp)
    if xp is not None:
        import diary
        diary.from_lookout_close(user["id"], row_id, xp)
    return dict(ok=True)


# ---------------------------------------------------------------- alerts
def _alert(r: dict, key: str, text: str) -> None:
    import notify
    u = db.get_user(r["user_id"])
    if not u:
        return
    try:
        notify.user_alert(u, text, f"/t/{r['ticker']}", f"{r['ticker']}-lookout-{r['id']}-{key}")
    except Exception as e:
        log.warning("lookout alert failed (%s): %s", r["id"], e)


def _fired(r: dict, key: str) -> bool:
    return key in (r.get("alerted") or "").split(",")


def _mark(r: dict, key: str) -> str:
    keys = [k for k in (r.get("alerted") or "").split(",") if k] + [key]
    return ",".join(dict.fromkeys(keys))


# ---------------------------------------------------------------- evaluation
def evaluate_touches(now: pd.Timestamp | None = None) -> int:
    """Per minute in market hours (called from scanner.trigger_all): first touch of the target; housekeeping."""
    import data
    now = now or pd.Timestamp.now(tz="UTC")
    n = 0
    try:
        _auto_close(now)
    except Exception:
        log.exception("lookout auto-close")
    rows = _rows("WHERE state IN ('watching')")
    if not rows or not data.market_open(now):
        return 0
    try:
        prices = data.latest_prices(sorted({r["ticker"] for r in rows}))
    except Exception as e:
        log.warning("lookout prices: %s", e)
        return 0
    for r in rows:
        try:
            if _fired(r, "target"):
                continue
            import scanner      # Nov 25: a touch needs a fresh trade AND the 1-minute bar at the target
            lv = r["target"] - 0.005 if r["direction"] == 1 else r["target"] + 0.005
            if scanner.price_past(prices.get(r["ticker"]), lv, r["direction"] != 1, now) is not None:
                _set(r["id"], state="target_hit", state_at=_now(), alerted=_mark(r, "target"))
                _alert(r, "target", f"🎯 {r['ticker']} touched the target, {_money(r['target'])}. First touch — your plan's shelf is here.")
                n += 1
        except Exception:
            log.exception("lookout touch row %s", r.get("id"))
    return n


def evaluate_closes(now: pd.Timestamp | None = None) -> int:
    """After each 4H close's scan (1:30 PM and 4:00 PM verdicts): the kill (closes only) and the clock."""
    import scanner
    now = now or pd.Timestamp.now(tz="UTC")
    n = 0
    for r in _rows("WHERE state IN ('watching','target_hit')"):
        try:
            c4 = scanner.load_4h(r["ticker"], scanner.SCAN_DAYS)
            if c4.empty:
                continue
            ts, close = c4.index[-1], float(c4["Close"].iloc[-1])
            if r["last_close"] and r["last_close"] >= ts.isoformat():
                continue                                   # this candle was already judged
            if pd.Timestamp(ts) < pd.Timestamp(r["created_at"]) - pd.Timedelta(hours=4):
                _set(r["id"], last_close=ts.isoformat()); continue   # a close from before the watch existed
            at = "1:30" if ts.tz_convert(NY).hour < 13 else "4:00"
            kv = dict(last_close=ts.isoformat())
            dead = close < r["kill"] if r["direction"] == 1 else close > r["kill"]
            if dead:
                kv.update(state="dead", state_at=_now(), alerted=_mark(r, "dead"))
                side = "fell back below" if r["direction"] == 1 else "rose back above"
                _set(r["id"], **kv)
                _alert(r, "dead", f"⛔ {r['ticker']} — the plan is dead. The {at} close {side} {_money(r['kill'])}. "
                                  "What you do next is yours; the plan this trade was built on is over.")
                n += 1
                continue
            day = ts.tz_convert(NY).date().isoformat()
            if r["window_end"] and day > r["window_end"] and not _fired(r, "clock"):
                kv.update(state="window_closed", state_at=_now(), alerted=_mark(r, "clock"))
                _set(r["id"], **kv)
                _alert(r, "clock", f"⏱ {r['ticker']} — the window this plan gave itself has closed.")
                n += 1
                continue
            _set(r["id"], **kv)
        except Exception:
            log.exception("lookout close row %s", r.get("id"))
    return n


def evaluate_earnings_eve(now: pd.Timestamp | None = None) -> int:
    """At the session close: one alert when the next session is the earnings date."""
    now = (now or pd.Timestamp.now(tz="UTC")).tz_convert(NY)
    nxt = (now.normalize().tz_localize(None) + pd.offsets.BDay(1)).date().isoformat()
    n = 0
    for r in _rows("WHERE state IN ('watching','target_hit') AND earnings_date IS NOT NULL"):
        try:
            if r["earnings_date"] == nxt and not _fired(r, "earnings"):
                _set(r["id"], alerted=_mark(r, "earnings"))
                _alert(r, "earnings", f"📅 {r['ticker']} reports tomorrow. The plan says be out before the print.")
                n += 1
        except Exception:
            log.exception("lookout earnings row %s", r.get("id"))
    return n


_POS_T: dict[int, float] = {}


def _auto_close(now: pd.Timestamp) -> None:
    # 24h after 'dead' / 'window_closed' the watch closes itself if the member hasn't
    cut = (now - pd.Timedelta(hours=24)).isoformat()
    for r in _rows("WHERE state IN ('dead','window_closed') AND state_at < ?", (cut,)):
        _set(r["id"], state="closed", closed_at=_now(), close_reason="auto_" + r["state"])
    # fix 8: owner with a connected broker — close a watch once its contract has left the account (seen first, so an
    # unfilled limit order never closes its own watch). Checked at most every 10 minutes per member.
    rows = _rows("WHERE state IN ('watching','target_hit','dead','window_closed') AND contract_occ IS NOT NULL")
    by_user: dict[int, list] = {}
    for r in rows:
        by_user.setdefault(r["user_id"], []).append(r)
    for uid, rs in by_user.items():
        if time.time() - _POS_T.get(uid, 0) < 600:
            continue
        _POS_T[uid] = time.time()
        u = db.get_user(uid)
        if not u or not u["is_admin"]:
            continue
        try:
            import snaptrade
            if not snaptrade.configured():
                continue
            accts = snaptrade.accounts(dict(u))
            held = set()
            for a in accts or []:
                aid = a.get("account_id") or a.get("id")
                for pz in snaptrade.portfolio(dict(u), aid).get("positions", []):
                    if pz.get("occ") and (pz.get("units") or 0) != 0:
                        held.add(pz["occ"])
        except Exception as e:
            log.info("lookout position check skipped for %s: %s", uid, e)
            continue
        for r in rs:
            if r["contract_occ"] in held:
                if not r["seen_position"]:
                    _set(r["id"], seen_position=1)
            elif r["seen_position"]:
                _set(r["id"], state="closed", closed_at=_now(), close_reason="position_closed")


init()      # the table exists as soon as anything imports Lookout
