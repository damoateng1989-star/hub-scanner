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
import os
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
    for col, typ in (("clear_path", "INTEGER"), ("blocking", "TEXT"), ("tape", "INTEGER")):   # Nov 26: the two checks
        try:
            db._conn.execute(f"ALTER TABLE cross_setups ADD COLUMN {col} {typ}")
        except Exception:
            pass
    db._conn.commit()
    _p222_dedupe()
    try:       # Patch 222 §2, enforced by the database too: one live row per symbol, whatever two threads do at once
        db._conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS cross_one_live ON cross_setups(symbol) WHERE state='live'")
        db._conn.commit()
    except Exception as e:
        log.warning("cross one-live index not created: %s", e)


def _p222_dedupe() -> None:
    """Patch 222 §4, once: a row that opened while an earlier row for the same symbol was still live is the phantom
    second episode §2 now prevents (a hub whose measured edges/start drifted got a new ep_key). It is deleted, not
    re-scored, and the closed summary restates itself from the table. The removed rows are kept in the setting
    cross_p222_removed (shown on /api/admin/cross-log) as the evidence."""
    rows = _rows("ORDER BY cross_at, id")
    if db.get_setting("cross_p222_done"):
        # second pass (Nov 26): the same symbol twice on the same candle is one episode; keep the first row
        if db.get_setting("cross_p222b_done"):
            return
        seen, dup = set(), []
        for r in rows:
            k = (r["symbol"], r["cross_candle"])
            (dup.append(r) if k in seen else seen.add(k))
        for r in dup:
            db._run("DELETE FROM cross_setups WHERE id=?", (r["id"],))
            # and the alerts that phantom row sent (its entry + its verdict), so the feed shows the episode once
            db._run("DELETE FROM alerts WHERE id=? OR id LIKE ?", (r["ep_key"], f"cross#{r['id']}|%"))
        old = json.loads(db.get_setting("cross_p222_removed") or "[]")
        db.set_setting("cross_p222_removed", json.dumps(old + dup, default=str))
        db.set_setting("cross_p222b_done", _now())
        if dup:
            log.warning("cross: removed %d same-candle duplicates: %s", len(dup), ", ".join(f"{r['symbol']}#{r['id']}" for r in dup))
        return
    removed = []
    for i, r in enumerate(rows):
        for o in rows[:i]:
            if o["symbol"] != r["symbol"] or o["id"] in {x["id"] for x in removed}:
                continue
            end = o.get("exit_at")
            if o["cross_at"] <= r["cross_at"] and (o["state"] == "live" or (end and r["cross_at"] < end)):
                removed.append(r)
                break
    for r in removed:
        db._run("DELETE FROM cross_setups WHERE id=?", (r["id"],))
    db.set_setting("cross_p222_removed", json.dumps(removed, default=str))
    db.set_setting("cross_p222_done", _now())
    if removed:
        log.warning("Patch 222: removed %d phantom cross rows: %s", len(removed),
                    ", ".join(f"{r['symbol']}#{r['id']}" for r in removed))


def _live_syms() -> set[str]:
    return {r["symbol"] for r in db._run("SELECT symbol FROM cross_setups WHERE state='live'").fetchall()}


def _money(v) -> str:
    return f"${v:,.2f}"


def _now() -> str:
    return pd.Timestamp.now(tz="UTC").isoformat()


def _rows(where: str = "", args: tuple = ()) -> list[dict]:
    return [dict(r) for r in db._run(f"SELECT * FROM cross_setups {where}", args).fetchall()]


def _set(row_id: int, **kv) -> None:
    db._run(f"UPDATE cross_setups SET {', '.join(f'{k}=?' for k in kv)} WHERE id=?", (*kv.values(), row_id))


# ---------------------------------------------------------------- the population
def plan_from_status(sym: str, st: dict, allow_forming: bool = False) -> dict | None:
    """The cross plan for a scan status, or None when the hub isn't in the measured population."""
    if not st:
        return None
    h, atr = st.get("hub") or {}, st.get("atr")
    # Nov 26 (VICI): the Lab's population is a FINISHED hub's first touch of its top within 'horizon' closes of the end,
    # unless a 4H close below the hub came first. Live, that is a forming hub (the touch on the candle that ends it) or a
    # hub that ended a few candles ago ('watching') whose top hasn't been touched since and that never closed below.
    if st.get("state") == "watching":
        try:
            if h.get("closed_below") or int(h.get("closes_since_end") or 0) >= HORIZON \
                    or (h.get("high_since_end") is not None and float(h["high_since_end"]) >= float(h["high"])):
                return None
            if "closes_since_end" not in h:     # a status written before this field existed: can't verify, skip
                return None
        except (TypeError, ValueError, KeyError):
            return None
    elif allow_forming and st.get("state") == "forming":
        pass                                  # the cross-replay Lab measures the forming case too (live never passes this)
    else:
        # Nov 26 (owner): no crosses on a still-forming hub. Live can't tell a real cross from a poke that closes back
        # inside (the hub just keeps going), and the Lab, in hindsight, never counted those. A hub that ended at an
        # earlier close is the same event live and in the Lab. Fewer alerts, none outside the measured population.
        return None
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


def explain(sym: str, lookback: int = 8) -> list[dict]:
    """Nov 26 (owner: DE, VICI, AXON broke out with no cross alert): for each of the last few 4H candles, the status the
    stock had going INTO it, and why the cross plan was or wasn't open. Read-only."""
    import engine
    import scanner
    c4 = scanner.load_4h(sym, scanner.SCAN_DAYS)
    out = []
    for j in range(max(60, len(c4) - lookback), len(c4)):
        prev = c4.iloc[:j]
        try:
            res = engine.analyze(prev, scanner.SETTINGS)
            st = engine.current_status(prev, res, scanner.SETTINGS)
        except Exception as e:
            out.append(dict(candle=str(c4.index[j]), error=str(e)[:120]))
            continue
        h = st.get("hub") or {}
        why = []
        if st.get("state") == "forming":
            why.append("the hub was still forming: only a hub that ended at an earlier close gets a cross entry")
        elif st.get("state") != "watching":
            why.append(f"state going in was '{st.get('state')}', not a just-ended hub")
        elif st.get("state") == "watching":
            if h.get("closed_below"):
                why.append("a 4H close below the hub came first")
            if h.get("high_since_end") is not None and h.get("high") is not None and float(h["high_since_end"]) >= float(h["high"]):
                why.append("its top was already touched after the hub ended (the first cross is the one measured)")
            if int(h.get("closes_since_end") or 0) >= HORIZON:
                why.append(f"more than {HORIZON} closes since the hub ended")
        sess = int(h.get("sessions") or 0)
        if sess < SESS_MIN:
            why.append(f"hub only {sess} sessions (needs {SESS_MIN})")
        tgt = st.get("cross_target") or ((st.get("shelf_up") or {}).get("level")) or st.get("target_up")
        room = None
        try:
            hi, lo = float(h["high"]), float(h["low"])
            mid = hi - (hi - lo) / 2
            room = (float(tgt) - hi) / (hi - mid) if tgt and hi > mid else None
        except Exception:
            pass
        if room is not None and room < ROOM_MIN:
            why.append(f"room {room:.2f}× half the hub (needs {ROOM_MIN})")
        if tgt is None:
            why.append("no target above")
        p = plan_from_status(sym, st)
        out.append(dict(candle=str(c4.index[j]), high=round(float(c4["High"].iloc[j]), 2), close=round(float(c4["Close"].iloc[j]), 2),
                        state_in=st.get("state"), hub=[h.get("low"), h.get("high")], sessions=sess, target=tgt,
                        room=round(room, 2) if room is not None else None, plan_open=bool(p),
                        trigger=p["trigger"] if p else None, crossed=bool(p and float(c4["High"].iloc[j]) >= p["trigger"]),
                        why_not=why if not p else []))
    out.append(dict(rows=[_public(r) for r in _rows("WHERE symbol=? ORDER BY cross_at DESC LIMIT 5", (sym,))]))
    return out


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
    # Nov 26 (UNH twice again): the 4H sweep and the scan both call on_close for the same close. The first created the
    # row and killed it on that same close, so no row was 'live' when the second came in with a slightly different
    # hub measure, and it opened a second episode. One episode per symbol per candle, whatever state the first is in.
    cc = candle_start.tz_convert("UTC").isoformat()
    if db._run("SELECT 1 FROM cross_setups WHERE symbol=? AND cross_candle=? LIMIT 1", (p["symbol"], cc)).fetchone():
        return None
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
    held = _live_syms()      # Patch 222 §2: a symbol with a live episode never opens a second one
    plans = [p for sym, st in _statuses().items()
             if sym not in held and (p := plan_from_status(sym, st)) and not _exists(p["ep_key"])]
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
        import scanner
        px = scanner.price_past(prices.get(r["symbol"]), r["target"] - 0.005, False, now)   # Nov 25: confirmed touch
        if px is not None:
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
        if p and sym not in _live_syms() and not _exists(p["ep_key"]):      # Patch 222 §2
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
    # Patch 222 §3: out of 'live' exactly once. A second call (two sweeps walking the same row) changes nothing, says nothing.
    cur = db._run("""UPDATE cross_setups SET state=?, closes=?, last_close=?, exit_px=?, exit_at=?, ret_trig=?, ret_touch=?
                     WHERE id=? AND state='live'""",
                  (verdict, n, t.isoformat(), round(exit_px, 2), end.tz_convert("UTC").isoformat(), rt, ru, r["id"]))
    if not cur.rowcount:
        return
    when = end.strftime("%-I:%M %p")
    import alert_style            # Nov 25: the 🎯 / 🔴 / ⚪ trade-over card
    head = {"target": f"at the shelf {_money(r['target'])}", "stop": f"at the stop {_money(r['stop'])} (4H close {_money(exit_px)})",
            "dead": f"closed back inside the hub at {_money(exit_px)}", "expired": f"10 closes without the target or the stop · {_money(exit_px)}"}[verdict]
    det = {"target": f"{_money(r['trigger'])} → {_money(r['target'])} · {n} candle{'s' if n != 1 else ''} · {when} close",
           "stop": f"The loss the plan allowed · {n} candle{'s' if n != 1 else ''} · {when} close",
           "dead": f"Out before the stop · the rule did its job · {when} close",
           "expired": f"The plan's time ran out · {when} close"}[verdict]
    plain, html = alert_style.trade_over(verdict, r["symbol"], "cross entry", rt, head, det)
    _follow(r, verdict, plain, html)
    return
    line = {"target": f"✅ {r['symbol']}: cross setup reached the target — 4H close at or above {_money(r['target'])} ({when} close). {rt:+.2f}% from the trigger.",
            "stop": f"🛑 {r['symbol']}: cross setup stopped — 4H close {_money(exit_px)} at or under the stop {_money(r['stop'])} ({when} close). {rt:+.2f}% from the trigger.",
            "dead": f"❌ {r['symbol']}: cross setup dead — 4H close {_money(exit_px)} back inside the hub ({when} close). {rt:+.2f}% from the trigger.",
            "expired": f"⌛ {r['symbol']}: cross setup expired — 10 four-hour closes without the target or the stop ({when} close, {_money(exit_px)}). {rt:+.2f}% from the trigger."}[verdict]
    _follow(r, verdict, line)


# ---------------------------------------------------------------- alerts (§5) and who gets them (§6)
def record() -> dict:
    """Nov 26: live alerts only just-ended hubs, so once the trigger-cross test has been re-run with the split, the
    record is that slice's own numbers; until then the original Arm A record (all crosses)."""
    import scanner
    try:
        import lab_trigcross
        sp = ((lab_trigcross.status().get("result") or {}).get("split") or {}).get("just_ended") or {}
        if sp.get("n") and sp.get("avg") is not None and sp.get("pf") is not None:
            return dict(avg=float(sp["avg"]), pf=float(sp["pf"]), n=int(sp["n"]))
    except Exception:
        pass
    return scanner.UNIVERSE_RECORD["hub"]["cross"]


def record_line() -> str:
    r = record()
    return f"Record: cross entries {r['avg']:+.2f}% per signal · PF {r['pf']:.1f} · {r['n']:,} signals (2-yr test)"


def _plan_nums(r: dict) -> dict:
    tg, tp, sp = float(r["trigger"]), float(r["target"]), float(r["stop"])
    up, dn = (tp / tg - 1) * 100, (sp / tg - 1) * 100
    rr = (tp - tg) / (tg - sp) if tg > sp else None
    return dict(up=up, dn=dn, rr=rr)


def _when(r: dict) -> str:
    t = pd.Timestamp(r["cross_at"]).tz_convert(NY)
    return (f"at the {engine.candle_end(pd.Timestamp(r['cross_candle']).tz_convert(NY)).strftime('%-I:%M %p')} close"
            if r.get("at_close") else f"at {t.strftime('%-I:%M %p')} ET")


def alert_html(r: dict, extra: str = "") -> str:
    """Nov 25 (owner, design A 'the ticket'): the Telegram card for a cross entry. Same facts as alert_text."""
    import html as _h
    n, sym = _plan_nums(r), _h.escape(r["symbol"])
    w = lambda s: s.ljust(9)
    plan = (f"⚡ {w('TRIGGER')}{_money(r['trigger'])}\n"
            f"🎯 {w('TARGET')}{_money(r['target'])}  {n['up']:+.1f}%\n"
            f"🛑 {w('STOP')}{_money(r['stop'])}  {n['dn']:+.1f}%"
            + (f"\n⚖️ {w('REWARD')}{n['rr']:.1f}× the risk" if n["rr"] else ""))
    gap = ""
    if r.get("first_px") and r["first_px"] > r["trigger"] * 1.002:
        gap = f"\n<i>First seen at {_money(r['first_px'])}: a stop order fills above the trigger on a gap.</i>"
    rec = record()
    return (f"🟣 <b>CROSS ENTRY · {sym}</b>\n"
            f"<i>LONG · CALLS · hub cross {_h.escape(_when(r))}</i>\n\n"
            f"<b>{_money(r['trigger'])} → {_money(r['target'])}</b>\n"
            f"{n['up']:+.1f}% to the next shelf\n"
            f"<pre>{_h.escape(plan)}</pre>"
            f"<blockquote>Dead on a 4H close back inside {_money(r['hub_low'])}–{_money(r['hub_high'])}</blockquote>"
            f"Record: {rec['avg']:+.2f}% per signal · PF {rec['pf']:.1f} · {rec['n']:,} signals (2-yr test){gap}"
            + (f"\n{_h.escape(extra.strip())}" if extra and extra.strip() else ""))


def alert_buttons(sym: str) -> list:
    base = os.getenv("APP_URL", "").strip().rstrip("/") or "https://nextshelfapp.com"
    return [("📈 Open chart", f"{base}/t/{sym}"), ("📘 How cross entries work", f"{base}/learn/the-cross")]


def alert_text(r: dict) -> str:
    t = pd.Timestamp(r["cross_at"]).tz_convert(NY)
    when = f"at the {engine.candle_end(pd.Timestamp(r['cross_candle']).tz_convert(NY)).strftime('%-I:%M %p')} close" if r.get("at_close") \
        else f"at {t.strftime('%-I:%M %p')} ET"
    gap = ""
    if r.get("first_px") and r["first_px"] > r["trigger"] * 1.002:
        gap = f"\nFirst seen at {_money(r['first_px'])}: a stop order fills above the trigger on a gap."
    n = _plan_nums(r)        # plain version (push, texts, the alert log): design A's facts, no markup
    return (f"🟣 CROSS ENTRY · {r['symbol']} — LONG · CALLS\n"
            f"Hub cross {when}: {_money(r['trigger'])} → {_money(r['target'])} ({n['up']:+.1f}% to the next shelf)\n"
            f"⚡ Trigger {_money(r['trigger'])} · 🎯 Target {_money(r['target'])} · 🛑 Stop {_money(r['stop'])} ({n['dn']:+.1f}%)"
            + (f" · ⚖️ {n['rr']:.1f}× the risk" if n["rr"] else "") + "\n"
            f"Dead on a 4H close back inside {_money(r['hub_low'])}–{_money(r['hub_high'])}.\n"
            f"{record_line()}{gap}")


def _tape_confirmed(sym: str) -> bool:
    """The flow classification, where one exists for today (normally only after a break is classified)."""
    try:
        import alert_format
        day = pd.Timestamp.now(tz=NY).strftime("%Y-%m-%d")
        return bool(alert_format.tape_text(db.flow_signal_for(sym, 1, day), 1))
    except Exception:
        return False


def _checks(r: dict) -> dict:
    """Nov 26 (owner): two checks on every cross, stored on the row and shown in the alert. Context only — the cross
    rules don't change; the forward log will say whether either one separates the winners.
      clear path: no stack of 2+ kinds of level (pivots, averages, older hubs) between the trigger and the target
                  — the Stacked Shelf rule, price-only, the same levels the Lab uses
      tape:       today's options flow leans the same way (the member gate already used this)"""
    out = dict(clear_path=None, blocking=None, tape=None)
    try:
        out["tape"] = int(_tape_confirmed(r["symbol"]))
    except Exception:
        pass
    try:
        import engine
        import scanner
        import stacked
        c4 = scanner.load_4h(r["symbol"], scanner.SCAN_DAYS)
        res = engine.analyze(c4, scanner.SETTINGS)
        h = res["hubs"][-1] if res.get("hubs") else None
        trig, tgt = float(r["trigger"]), float(r["target"])
        own = {round(float(x), 2) for x in (r["hub_high"], r["hub_low"])}
        cands = [c for c in stacked.History(c4, res).at(len(c4) - 1, own_hub=h, price=trig) if round(c["price"], 2) not in own]
        chk = stacked.check(cands, 1, trig, trig, tgt, stacked.tol(trig, r.get("atr")))
        out["clear_path"], out["blocking"] = int(bool(chk["clear_path"])), json.dumps(chk["blocking"])
    except Exception as e:
        log.warning("cross checks failed for %s: %s", r["symbol"], e)
    try:
        db._run("UPDATE cross_setups SET clear_path=?, blocking=?, tape=? WHERE id=?",
                (out["clear_path"], out["blocking"], out["tape"], r["id"]))
    except Exception:
        pass
    r.update(out)
    return out


def check_lines(r: dict) -> list[str]:
    cp, tp = r.get("clear_path"), r.get("tape")
    out = []
    if cp is not None:
        bl = json.loads(r.get("blocking") or "[]") if isinstance(r.get("blocking"), str) else (r.get("blocking") or [])
        out.append("✅ Clear path to the target" if cp else
                   "⚠️ Stack in the way at " + ", ".join(_money(x) for x in bl[:2]) + " (2+ kinds of level before the target)")
    if tp is not None:
        out.append("✅ Tape confirmed: today's options flow agrees" if tp else "· Tape not confirmed")
    return out


def _alert_new(r: dict) -> None:
    """§6 as drafted: admins every cross; members when the stock is on their watchlist or the cross is tape-confirmed.
    Everyone else sees it on the board. The forward-log row exists whatever this decides."""
    import earnings
    import notify
    import html as _h
    _checks(r)
    # Nov 26 (owner): after the split Lab run (just-ended crosses +0.04%/signal, PF 1.06, fails its gate) a cross is a
    # 🟡 HEADS-UP, not an entry: silent, short, no buy-stop plan, no record claim, no result follow-ups. The row is
    # still logged and scored in the forward log (watch-only).
    tag = (" · clear path" if r.get("clear_path") == 1 else " · stack in the way" if r.get("clear_path") == 0 else "") + \
          (" · tape ✓" if r.get("tape") == 1 else "")
    msg = (f"🟡 {r['symbol']} crossed the top of its hub {_money(r['trigger'])} {_when(r)}\n"
           f"Heads-up, not an entry · it counts on a 4H close above with volume, then NextShelf waits for the retest{tag}")
    card = (f"🟡 <b>{_h.escape(r['symbol'])} crossed the top of its hub {_money(r['trigger'])}</b> <i>{_h.escape(_when(r))}</i>\n"
            f"Heads-up, not an entry · it counts on a 4H close above with volume, then NextShelf waits for the retest{_h.escape(tag)}")
    btns = None
    if not db.add_alert(f"{r['ep_key']}", r["symbol"], "cross", r["cross_candle"], msg):
        return
    sent = set()
    try:
        for u in db.watchers(r["symbol"]):
            sent.add(u["id"])
        notify.symbol_alert(r["symbol"], "cross", msg, style="hub", candle_start=pd.Timestamp(r["cross_candle"]),
                            tg_html=card, tg_buttons=btns, silent=True)
    except Exception as e:
        log.warning("cross alert (watchers) failed for %s: %s", r["symbol"], e)
    for u in db.all_users():          # a heads-up: watchers (above) and admins only — no tape-based member broadcast
        if u["id"] in sent or not u["is_admin"]:
            continue
        try:
            tc = ""          # the tape line is already in the card
            notify.user_alert(u, msg + tc, f"/t/{r['symbol']}", f"{r['symbol']}-cross", tg_html=card + tc, tg_buttons=btns, silent=True)
        except Exception as e:
            log.warning("cross alert failed for user %s: %s", u.get("id"), e)


def _follow(r: dict, kind: str, line: str, html: str | None = None) -> None:
    """Follow-ups go to whoever could have had the first alert: watchers, admins, members if it was tape-confirmed
    (approximated by today's tape: the follow-up is one line either way)."""
    # Nov 26: heads-up only — the cross is no longer an entry, so its results aren't sent; they stay in the forward log
    if kind in ("target", "stop", "dead", "expired", "touch"):
        log.info("cross %s %s (forward log only): %s", r["symbol"], kind, line.split("\n")[0])
        return
    import notify
    import alert_style
    html = html or alert_style.generic_html(line)
    btns = alert_style.buttons(r["symbol"], record=True)
    try:
        # Patch 222 §3 backstop: a verdict is keyed by the row id, so one row gives at most one line anywhere
        key = f"cross#{r['id']}|{kind}" if kind in ("target", "stop", "dead", "expired") else f"{r['ep_key']}|{kind}"
        if db.add_alert(key, r["symbol"], "cross", r["cross_candle"], line):
            sent = {u["id"] for u in db.watchers(r["symbol"])}
            notify.symbol_alert(r["symbol"], "cross", line, style="hub", tg_html=html, tg_buttons=btns)
            for u in db.all_users():
                if u["is_admin"] and u["id"] not in sent:
                    notify.user_alert(u, line, f"/t/{r['symbol']}", f"{r['symbol']}-cross", tg_html=html, tg_buttons=btns)
    except Exception as e:
        log.warning("cross follow-up failed for %s: %s", r["symbol"], e)


# ---------------------------------------------------------------- reads: board, cards, recap
def _public(r: dict) -> dict:
    out = {k: r[k] for k in ("id", "symbol", "trigger", "target", "stop", "hub_high", "hub_low", "first_px", "state",
                             "closes", "exit_px", "ret_trig", "ret_touch", "at_close", "touched")}
    out["clear_path"], out["tape"] = r.get("clear_path"), r.get("tape")
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
                since=min(r["cross_at"] for r in rows)[:10], splits=_splits(rows))


def _splits(rows: list) -> dict:
    """Nov 26: does either check separate the winners? Per group: n and avg from the trigger. Rows from before the
    checks existed (NULL) are left out of both sides."""
    out = {}
    for key, lab in (("clear_path", ("clear path", "stack in the way")), ("tape", ("tape confirmed", "tape not confirmed"))):
        for val, name in ((1, lab[0]), (0, lab[1])):
            g = [r["ret_trig"] or 0 for r in rows if r.get(key) == val]
            out[name] = dict(n=len(g), avg=round(sum(g) / len(g), 2) if g else None)
    return out


init()      # the table exists as soon as anything imports this module
