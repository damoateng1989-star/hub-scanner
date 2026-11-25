"""Patch 218 (Nov 24): the first trigger cross of a qualified hub is a setup (hub longs only).

Measured by the trigger-cross Lab test (lab_trigcross.py, Arm A, Nov 24 4:47 PM run): +0.56% per signal, PF 1.8,
3,576 signals over two years, passing every check. This module is Arm A, live:

  population  a hub the scan calls "forming" (inside the hub), 4+ sessions, room 1.5+ from the trigger to the
              target (the Lab's signal filters — nothing narrower, nothing wider)
  trigger     the hub high (the Lab's trigger; the card's heads-up trigger can sit a little higher)
  entry       the first touch of the trigger: a buy-stop at the trigger
  target      the next shelf from the hub high (engine.target_for, as the Lab), booked on a 4H close at/above it
  stop        trigger − 1 ATR(14)
  kill        a 4H close back inside the hub (below the hub high) — closes only, never wicks
  horizon     10 four-hour closes, counted from the cross candle's own close

One setup per hub episode (symbol + hub high/low + hub start). The table doubles as the forward log (§7): each row is
scored twice when it closes — from the trigger (the Lab's convention) and from the price first seen at the touch
(what a real stop order would have met). NextShelf never places an order: the setup is information, the trade is the
member's.
"""
from __future__ import annotations

import json
import logging

import pandas as pd

import db
import engine

log = logging.getLogger("cross")
ROOM_MIN, SESS_MIN, HORIZON = 1.5, 4, 10
NY = "America/New_York"


def init() -> None:
    db._conn.executescript("""
    CREATE TABLE IF NOT EXISTS cross_setups (
      id INTEGER PRIMARY KEY,
      symbol TEXT NOT NULL,
      ep_key TEXT NOT NULL UNIQUE,     -- "{sym}|cross|{hub_high}|{hub_low}|{hub_start}"
      trigger REAL NOT NULL, target REAL NOT NULL, stop REAL NOT NULL,
      hub_high REAL NOT NULL, hub_low REAL NOT NULL, atr REAL,
      cross_at TEXT NOT NULL,          -- when the touch was seen (UTC ISO)
      cross_candle TEXT NOT NULL,      -- the 4H candle the touch happened in (its start, UTC ISO)
      first_px REAL,                   -- the price first seen at the touch (gap included)
      at_close INTEGER DEFAULT 0,      -- first seen at a 4H close (the minute check missed the touch)
      state TEXT NOT NULL DEFAULT 'live',   -- live | target | stop | dead | expired
      closes INTEGER DEFAULT 0,        -- 4H closes counted so far
      last_close TEXT,                 -- the last 4H candle evaluated
      exit_px REAL, exit_at TEXT,
      ret_trig REAL, ret_touch REAL,   -- % from the trigger / from the first touch
      touched INTEGER DEFAULT 0,       -- the target was touched intraday (one alert)
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS cross_sym ON cross_setups(symbol, state);
    """)
    db._conn.commit()


def _money(v) -> str:
    return f"${v:,.2f}"


def _now() -> str:
    return pd.Timestamp.now(tz="UTC").isoformat()


def _rows(where: str = "", args: tuple = ()) -> list[dict]:
    return [dict(r) for r in db._run(f"SELECT * FROM cross_setups {where}", args).fetchall()]


def _set(row_id: int, **kv) -> None:
    db._run(f"UPDATE cross_setups SET {', '.join(f'{k}=?' for k in kv)} WHERE id=?", (*kv.values(), row_id))


# ---------------------------------------------------------------- the population
def plan_from_status(sym: str, st: dict) -> dict | None:
    """The cross plan for a scan status, or None when the hub isn't in the measured population."""
    if not st or st.get("state") != "forming":
        return None
    h, atr = st.get("hub") or {}, st.get("atr")
    try:
        hi, lo, sess = float(h["high"]), float(h["low"]), int(h.get("sessions") or 0)
        atr = float(atr)
    except (KeyError, TypeError, ValueError):
        return None
    if sess < SESS_MIN or not atr or atr <= 0:
        return None
    trig = hi
    target = st.get("cross_target")
    if target is None:                      # a status written before Patch 218: the shelf the card shows, else 1 ATR past
        sh = st.get("shelf_up")
        target = sh["level"] if sh else st.get("target_up")
    if target is None:
        return None
    target = float(target)
    w = hi - lo if hi > lo else atr
    mid = hi - w / 2
    room = (target - trig) / (trig - mid) if trig > mid else None
    if room is None or room < ROOM_MIN or target <= trig:
        return None
    return dict(symbol=sym, trigger=round(trig, 2), target=round(target, 2), stop=round(trig - atr, 2),
                hub_high=round(hi, 2), hub_low=round(lo, 2), atr=round(atr, 2), room=round(room, 2),
                ep_key=f"{sym}|cross|{hi:.2f}|{lo:.2f}|{h.get('start') or ''}")


def _statuses() -> dict[str, dict]:
    """Every scanned stock's latest status: the board's universe, plus watchlist stocks the board doesn't sweep."""
    out = {}
    for r in db.board_rows():
        try:
            out[r["symbol"]] = json.loads(r["status"] or "{}")
        except Exception:
            pass
    for r in db.list_tickers():
        if r["symbol"] in out:
            continue
        try:
            out[r["symbol"]] = json.loads(r.get("status") or "{}")
        except Exception:
            pass
    return out


def _exists(ep_key: str) -> bool:
    return bool(db._run("SELECT 1 FROM cross_setups WHERE ep_key=?", (ep_key,)).fetchone())


def _create(p: dict, first_px: float, candle_start: pd.Timestamp, at_close: bool, seen_at: str | None = None) -> dict | None:
    try:
        cur = db._run("""INSERT OR IGNORE INTO cross_setups (symbol, ep_key, trigger, target, stop, hub_high, hub_low, atr,
            cross_at, cross_candle, first_px, at_close, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (p["symbol"], p["ep_key"], p["trigger"], p["target"], p["stop"], p["hub_high"], p["hub_low"], p["atr"],
             seen_at or _now(), candle_start.tz_convert("UTC").isoformat(), round(float(first_px), 2), int(at_close), _now()))
    except Exception as e:
        log.warning("cross setup insert failed for %s: %s", p.get("symbol"), e)
        return None
    if not cur.rowcount:
        return None
    return _rows("WHERE id=?", (cur.lastrowid,))[0]


# ---------------------------------------------------------------- the minute check (§3)
def check(now: pd.Timestamp | None = None) -> list[dict]:
    """Every minute in market hours: the first touch of each qualified hub's trigger creates its setup.
    Also the target's first intraday touch on live setups (one line; the record books the target on the close)."""
    import data
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    if not data.market_open(now):
        return []
    plans = [p for sym, st in _statuses().items() if (p := plan_from_status(sym, st)) and not _exists(p["ep_key"])]
    live = _rows("WHERE state='live' AND touched=0")
    syms = sorted({p["symbol"] for p in plans} | {r["symbol"] for r in live})
    if not syms:
        return []
    try:
        prices = data.latest_prices(syms)
    except Exception as e:
        log.warning("cross check prices failed: %s", e)
        return []
    start = engine.current_candle_start(now)
    made = []
    for p in plans:
        import scanner
        px = scanner.price_past(prices.get(p["symbol"]), p["trigger"], False, now)   # Nov 25: fresh + minute bar confirms
        if px is None:
            continue
        row = _create(p, px, start, at_close=False, seen_at=now.tz_convert("UTC").isoformat())
        if row:
            made.append(row)
            _alert_new(row)
    for r in live:
        px = (prices.get(r["symbol"]) or {}).get("price")
        if px is not None and px >= r["target"]:
            _set(r["id"], touched=1)
            _follow(r, "touch", f"🎯 {r['symbol']}: touched the cross target {_money(r['target'])} (now {_money(px)}). "
                       f"The record books the target on a 4H close at or above it.")
    if made:
        log.info("cross setups created: %s", ", ".join(r["symbol"] for r in made))
    return made


# ---------------------------------------------------------------- at each 4H close (§3 at_close, exits)
def on_close(sym: str, c4: pd.DataFrame, old_status: dict | None) -> None:
    """Called by the scan for each stock with its fresh 4H candles and the status it had BEFORE this close.
    1) A touch the minute check never saw (a wick between polls, a missed minute) still creates the setup, at_close.
    2) Live setups get their 4H-close verdicts: target, stop, dead (back inside), or expired after 10 closes."""
    if c4 is None or len(c4) < 2:
        return
    try:
        p = plan_from_status(sym, old_status or {})
        if p and not _exists(p["ep_key"]):
            # the candles since the status was written (normally just the one that closed)
            since = pd.Timestamp((old_status or {}).get("last_candle") or c4.index[-2])
            now_ny = pd.Timestamp.now(tz=NY)
            new = c4[(c4.index > since) & [engine.candle_end(t.tz_convert(NY)) <= now_ny for t in c4.index]]
            hit = new[new["High"] >= p["trigger"]]
            if len(hit):
                t = hit.index[0]
                row = _create(p, max(p["trigger"], float(hit["Open"].iloc[0])), t, at_close=True,
                              seen_at=engine.candle_end(t.tz_convert(NY)).tz_convert("UTC").isoformat())
                if row:
                    _alert_new(row)
    except Exception as e:
        log.warning("cross at-close check failed for %s: %s", sym, e)
    for r in _rows("WHERE symbol=? AND state='live'", (sym,)):
        try:
            _walk(r, c4)
        except Exception as e:
            log.warning("cross exit check failed for %s #%s: %s", sym, r["id"], e)


def _walk(r: dict, c4: pd.DataFrame) -> None:
    """The Lab's exits on completed 4H closes from the cross candle on (the cross candle's own close counts first)."""
    t0 = pd.Timestamp(r["cross_candle"])
    last = pd.Timestamp(r["last_close"]) if r.get("last_close") else None
    now_ny = pd.Timestamp.now(tz=NY)
    done = [t for t in c4.index if t >= t0 and engine.candle_end(t.tz_convert(NY)) <= now_ny]
    n = int(r.get("closes") or 0)
    for t in done:
        if last is not None and t <= last:
            continue
        n += 1
        c = float(c4.loc[t, "Close"])
        verdict = exit_px = None
        if c >= r["target"]:
            verdict, exit_px = "target", r["target"]
        elif c <= r["stop"]:
            verdict, exit_px = "stop", c
        elif c < r["hub_high"]:
            verdict, exit_px = "dead", c
        elif n >= HORIZON:
            verdict, exit_px = "expired", c
        if verdict:
            _resolve(r, verdict, exit_px, t, n)
            return
        _set(r["id"], closes=n, last_close=t.isoformat())
        last = t


def _resolve(r: dict, verdict: str, exit_px: float, t: pd.Timestamp, n: int) -> None:
    rt = round((exit_px / r["trigger"] - 1) * 100, 3)
    ru = round((exit_px / r["first_px"] - 1) * 100, 3) if r.get("first_px") else None
    end = engine.candle_end(t.tz_convert(NY))
    _set(r["id"], state=verdict, closes=n, last_close=t.isoformat(), exit_px=round(exit_px, 2),
         exit_at=end.tz_convert("UTC").isoformat(), ret_trig=rt, ret_touch=ru)
    when = end.strftime("%-I:%M %p")
    line = {"target": f"✅ {r['symbol']}: cross setup reached the target — 4H close at or above {_money(r['target'])} ({when} close). {rt:+.2f}% from the trigger.",
            "stop": f"🛑 {r['symbol']}: cross setup stopped — 4H close {_money(exit_px)} at or under the stop {_money(r['stop'])} ({when} close). {rt:+.2f}% from the trigger.",
            "dead": f"❌ {r['symbol']}: cross setup dead — 4H close {_money(exit_px)} back inside the hub ({when} close). {rt:+.2f}% from the trigger.",
            "expired": f"⌛ {r['symbol']}: cross setup expired — 10 four-hour closes without the target or the stop ({when} close, {_money(exit_px)}). {rt:+.2f}% from the trigger."}[verdict]
    _follow(r, verdict, line)


# ---------------------------------------------------------------- alerts (§5) and who gets them (§6)
def record() -> dict:
    import scanner
    return scanner.UNIVERSE_RECORD["hub"]["cross"]


def record_line() -> str:
    r = record()
    return f"Record: cross entries {r['avg']:+.2f}% per signal · PF {r['pf']:.1f} · {r['n']:,} signals (2-yr test)"


def alert_text(r: dict) -> str:
    t = pd.Timestamp(r["cross_at"]).tz_convert(NY)
    when = f"at the {engine.candle_end(pd.Timestamp(r['cross_candle']).tz_convert(NY)).strftime('%-I:%M %p')} close" if r.get("at_close") \
        else f"at {t.strftime('%-I:%M %p')} ET"
    gap = ""
    if r.get("first_px") and r["first_px"] > r["trigger"] * 1.002:
        gap = f"\nFirst seen at {_money(r['first_px'])}: a stop order fills above the trigger on a gap."
    return (f"▲ LONG · CALLS — {r['symbol']} hub cross\n"
            f"Hub {_money(r['hub_low'])}–{_money(r['hub_high'])} · crossed {_money(r['trigger'])} {when}\n"
            f"{record_line()}\n"
            f"TRIGGER {_money(r['trigger'])} · TARGET {_money(r['target'])} · STOP {_money(r['stop'])}\n"
            f"Dead on a 4H close back inside the hub.{gap}")


def _tape_confirmed(sym: str) -> bool:
    """The flow classification, where one exists for today (normally only after a break is classified)."""
    try:
        import alert_format
        day = pd.Timestamp.now(tz=NY).strftime("%Y-%m-%d")
        return bool(alert_format.tape_text(db.flow_signal_for(sym, 1, day), 1))
    except Exception:
        return False


def _alert_new(r: dict) -> None:
    """§6 as drafted: admins every cross; members when the stock is on their watchlist or the cross is tape-confirmed.
    Everyone else sees it on the board. The forward-log row exists whatever this decides."""
    import earnings
    import notify
    msg = alert_text(r) + earnings.alert_line(r["symbol"])
    if not db.add_alert(f"{r['ep_key']}", r["symbol"], "cross", r["cross_candle"], msg):
        return
    sent = set()
    try:
        for u in db.watchers(r["symbol"]):
            sent.add(u["id"])
        notify.symbol_alert(r["symbol"], "cross", msg, style="hub", candle_start=pd.Timestamp(r["cross_candle"]))
    except Exception as e:
        log.warning("cross alert (watchers) failed for %s: %s", r["symbol"], e)
    tape = _tape_confirmed(r["symbol"])
    for u in db.all_users():
        if u["id"] in sent or not (u["is_admin"] or tape):
            continue
        try:
            notify.user_alert(u, msg + ("\nTape confirmed." if tape and not u["is_admin"] else ""), f"/t/{r['symbol']}", f"{r['symbol']}-cross")
        except Exception as e:
            log.warning("cross alert failed for user %s: %s", u.get("id"), e)


def _follow(r: dict, kind: str, line: str) -> None:
    """Follow-ups go to whoever could have had the first alert: watchers, admins, members if it was tape-confirmed
    (approximated by today's tape: the follow-up is one line either way)."""
    import notify
    try:
        if db.add_alert(f"{r['ep_key']}|{kind}", r["symbol"], "cross", r["cross_candle"], line):
            sent = {u["id"] for u in db.watchers(r["symbol"])}
            notify.symbol_alert(r["symbol"], "cross", line, style="hub")
            for u in db.all_users():
                if u["is_admin"] and u["id"] not in sent:
                    notify.user_alert(u, line, f"/t/{r['symbol']}", f"{r['symbol']}-cross")
    except Exception as e:
        log.warning("cross follow-up failed for %s: %s", r["symbol"], e)


# ---------------------------------------------------------------- reads: board, cards, recap
def _public(r: dict) -> dict:
    out = {k: r[k] for k in ("id", "symbol", "trigger", "target", "stop", "hub_high", "hub_low", "first_px", "state",
                             "closes", "exit_px", "ret_trig", "ret_touch", "at_close", "touched")}
    out["cross_at"] = r["cross_at"]
    out["exit_at"] = r.get("exit_at")
    out["closes_left"] = max(0, HORIZON - int(r.get("closes") or 0)) if r["state"] == "live" else 0
    try:
        import alert_format
        out["window"] = alert_format.window_after_candle(pd.Timestamp(r["cross_candle"]), HORIZON - 1)
    except Exception:
        out["window"] = None
    return out


def board_rows() -> list[dict]:
    """Live setups plus the ones resolved since yesterday's open (they show their state and reason)."""
    since = (pd.Timestamp.now(tz=NY).normalize() - pd.offsets.BDay(1)).tz_convert("UTC").isoformat()
    rows = _rows("WHERE state='live' OR exit_at >= ? ORDER BY cross_at DESC", (since,))
    return [_public(r) for r in rows]


def for_symbol(sym: str) -> dict | None:
    rows = _rows("WHERE symbol=? ORDER BY cross_at DESC LIMIT 1", (sym,))
    if not rows:
        return None
    r = rows[0]
    if r["state"] != "live" and (pd.Timestamp.now(tz="UTC") - pd.Timestamp(r.get("exit_at") or r["cross_at"])) > pd.Timedelta(days=3):
        return None
    out = _public(r)
    out["record"] = record()
    return out


def forward_summary() -> dict:
    """§7: the closed rows, scored from the trigger (the Lab's convention) and from the first touch (the live fill)."""
    rows = _rows("WHERE state != 'live'")
    n = len(rows)
    if not n:
        return dict(n=0, live=len(_rows("WHERE state='live'")))
    a = [r["ret_trig"] or 0 for r in rows]
    b = [r["ret_touch"] if r["ret_touch"] is not None else (r["ret_trig"] or 0) for r in rows]
    return dict(n=n, live=len(_rows("WHERE state='live'")), avg_trig=round(sum(a) / n, 2), avg_touch=round(sum(b) / n, 2),
                fill_cost=round(sum(a) / n - sum(b) / n, 2), wins=sum(1 for x in a if x > 0),
                since=min(r["cross_at"] for r in rows)[:10])


init()      # the table exists as soon as anything imports this module
