"""Next-day check: how each confirmed setup did on the session after it confirmed.

The live record (board._record_rules) logs every confirmed setup with its entry, stop and target. This looks at
the next full session after the entry: where it opened, its high and low, where it closed, and whether it touched
the stop or the target during the day. Measured from the entry price. Runs after the close (NEXTDAY_TIME) and
messages admins on Telegram. Information only: it changes no alert, no setup and no track-record outcome.
"""
from __future__ import annotations

import html
import logging
import os

import pandas as pd

import data
import db
import engine
import telegram_bot

log = logging.getLogger("nextday")
NEXTDAY_TIME = os.getenv("NEXTDAY_TIME", "16:35").strip()    # New York time, weekdays (before the 4:45 recap)
LOOKBACK_DAYS = 120

with db._lock:
    db._conn.execute("""CREATE TABLE IF NOT EXISTS next_day (
        signal_id TEXT PRIMARY KEY, symbol TEXT, setup TEXT, grade TEXT, entry REAL, stop REAL, target REAL,
        entry_time TEXT, day TEXT, open REAL, high REAL, low REAL, close REAL,
        rest_pct REAL, open_pct REAL, high_pct REAL, low_pct REAL, close_pct REAL,
        hit_stop INTEGER, hit_target INTEGER, checked_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    db._conn.commit()
    for _c, _d in [("kind", "TEXT DEFAULT 'setup'"), ("direction", "INTEGER DEFAULT 1"), ("fav_pct", "REAL")]:
        try:   # added later: hub breakouts and breakdowns too (kind='breakout'), measured in the trade's direction
            db._conn.execute(f"ALTER TABLE next_day ADD COLUMN {_c} {_d}")
            db._conn.commit()
        except Exception:
            pass


def _pct(x: float, e: float) -> float:
    return round((x / e - 1) * 100, 2)


def _pending() -> list[dict]:
    """Confirmed setups (the live record) and hub breakouts/breakdowns (the breakout close) with no next-day row yet."""
    rows = db._run("""SELECT s.*, CASE WHEN s.pattern='rules' THEN 'setup' ELSE 'breakout' END AS kind,
                             COALESCE(s.entry_time, s.breakout_time) AS e_time
                      FROM signals s LEFT JOIN next_day n ON n.signal_id = s.id
                      WHERE COALESCE(s.pattern, 'hub') IN ('rules', 'hub') AND s.entry IS NOT NULL
                        AND COALESCE(s.entry_time, s.breakout_time) IS NOT NULL AND n.signal_id IS NULL
                      ORDER BY e_time""").fetchall()
    cut = pd.Timestamp.now(tz=engine.NY) - pd.Timedelta(days=LOOKBACK_DAYS)
    out = []
    for r in rows:
        r = dict(r)
        r["entry_time"] = r.pop("e_time")
        try:
            if pd.Timestamp(r["entry_time"]).tz_convert(engine.NY) >= cut:
                out.append(r)
        except Exception:
            pass
    return out


def check(sig: dict, c4: pd.DataFrame) -> dict | None:
    """The next-day row for one confirmed setup, or None while that session hasn't finished."""
    d = engine.complete_days(c4)
    if d.empty:
        return None
    t = pd.Timestamp(sig["entry_time"]).tz_convert(engine.NY)
    entry_day = t.normalize()
    days = d.index.tz_convert(engine.NY).normalize()
    after = [i for i, x in enumerate(days) if x > entry_day]
    if not after:
        return None
    i = after[0]
    o, h, lo, c = (float(d[k].iloc[i]) for k in ("Open", "High", "Low", "Close"))
    if not float(sig["entry"]) > 0 or abs(o / float(sig["entry"]) - 1) > 0.4:
        return None                                # a split or a bad print, not a real next-day move: skip it
    e, stop, tgt = float(sig["entry"]), sig.get("stop"), sig.get("target")
    kind, dr = sig.get("kind") or "setup", int(sig.get("direction") or 1)
    setup = sig.get("setup")
    if kind == "breakout":                         # a hub break fails on a close back inside: "stop" = the hub edge
        stop = sig.get("hub_high") if dr == 1 else sig.get("hub_low")
        setup = "Breakout" if dr == 1 else "Breakdown"
    rest = None                                    # entered on the 1:30 close: what the rest of that day did
    if engine.candle_end(t).hour < 16:
        same = [k for k, x in enumerate(days) if x == entry_day]
        if same:
            rest = _pct(float(d["Close"].iloc[same[0]]), e)
    hit_stop = stop is not None and (lo <= float(stop) if dr == 1 else h >= float(stop))
    hit_tgt = tgt is not None and (h >= float(tgt) if dr == 1 else lo <= float(tgt))
    return dict(signal_id=sig["id"], symbol=sig["symbol"], setup=setup, grade=sig.get("grade"), kind=kind, direction=dr,
                entry=e, stop=stop, target=tgt, entry_time=sig["entry_time"], day=str(days[i].date()),
                open=round(o, 2), high=round(h, 2), low=round(lo, 2), close=round(c, 2), rest_pct=rest,
                open_pct=_pct(o, e), high_pct=_pct(h, e), low_pct=_pct(lo, e), close_pct=_pct(c, e),
                fav_pct=round(_pct(c, e) * dr, 2), hit_stop=int(hit_stop), hit_target=int(hit_tgt))


def _save(r: dict):
    cols = ", ".join(r)
    db._run(f"INSERT OR REPLACE INTO next_day ({cols}) VALUES ({', '.join('?' * len(r))})", tuple(r.values()))


def run(notify: bool = True) -> list[dict]:
    pend = _pending()
    if not pend:
        return []
    syms = sorted({p["symbol"] for p in pend})
    bars = {}
    for k in range(0, len(syms), 40):
        try:
            bars.update(data.get_bars_many(syms[k:k + 40], LOOKBACK_DAYS + 10))
        except Exception as e:
            log.warning("next-day bars failed: %s", e)
    done = []
    now = pd.Timestamp.now(tz=engine.NY)
    for p in pend:
        raw = bars.get(p["symbol"])
        if raw is None or raw.empty:
            continue
        try:
            r = check(p, engine.to_4h(raw, now=now))
        except Exception as e:
            log.warning("next-day check %s failed: %s", p["symbol"], e)
            continue
        if r:
            _save(r)
            done.append(r)
    log.info("next-day check: %d new of %d waiting", len(done), len(pend))
    if notify and done:
        today = [r for r in done if r["day"] == str(now.date())] or done[-10:]
        try:
            msg = text(today)
            for u in db.all_users():
                if u.get("is_admin") and u.get("telegram_chat"):
                    telegram_bot.send(msg, chat_id=u["telegram_chat"], silent=True)
        except Exception as e:
            log.warning("next-day message failed: %s", e)
    return done


def _line(r: dict) -> str:
    sign = lambda x: f"{(0.0 if abs(x) < 0.005 else x):+.2f}%"
    back = "back inside the hub" if r.get("kind") == "breakout" else "stop"
    flag = " · 🎯 touched target" if r["hit_target"] else f" · ⛔ touched {back}" if r["hit_stop"] else ""
    fav = r.get("fav_pct") if r.get("fav_pct") is not None else r["close_pct"]
    icon = "🟢" if fav > 0 else "🔴" if fav < 0 else "⚪"
    return (f"{icon} <b>{html.escape(r['symbol'])}</b> {html.escape(r['setup'] or '')} · entry ${r['entry']:,.2f}\n"
            f"   open {sign(r['open_pct'])} · high {sign(r['high_pct'])} · low {sign(r['low_pct'])} · close <b>{sign(r['close_pct'])}</b>{flag}")


def text(rows: list[dict]) -> str:
    st = stats()
    out = ["📋 <b>Next-day check</b> · how yesterday's setups and breakouts did today"]
    for kind, title, stop_word in (("setup", "Confirmed setups", "the stop"), ("breakout", "Breakouts & breakdowns", "back inside the hub")):
        rs, s = [r for r in rows if (r.get("kind") or "setup") == kind], st.get(kind) or {}
        if not rs:
            continue
        out.append(f"\n<b>{title}</b>\n" + "\n".join(_line(r) for r in rs[:12]) + (f"\n…and {len(rs) - 12} more" if len(rs) > 12 else ""))
        if s.get("n"):
            out.append(f"<i>All {s['n']} so far: {s['green_pct']}% went their way by the next close, avg {s['avg_fav']:+.2f}%, "
                       f"{s['stop_pct']}% touched {stop_word}, {s['target_pct']}% touched the target.</i>")
    return "\n".join(out)


def rows(limit: int = 200) -> list[dict]:
    return [dict(r) for r in db._run("SELECT * FROM next_day ORDER BY day DESC, symbol LIMIT ?", (limit,)).fetchall()]


def _agg(rs: list[dict]) -> dict:
    n = len(rs)
    if not n:
        return dict(n=0)
    f = lambda k: round(sum(r[k] for r in rs) / n, 2)
    fav = [r["fav_pct"] if r.get("fav_pct") is not None else r["close_pct"] * (r.get("direction") or 1) for r in rs]
    return dict(n=n, green_pct=round(100 * sum(x > 0 for x in fav) / n), avg_fav=round(sum(fav) / n, 2), avg_close=f("close_pct"),
                avg_open=f("open_pct"), avg_high=f("high_pct"), avg_low=f("low_pct"),
                stop_pct=round(100 * sum(r["hit_stop"] for r in rs) / n), target_pct=round(100 * sum(r["hit_target"] for r in rs) / n))


def stats() -> dict:
    """Totals per kind ('setup' = confirmed setups, 'breakout' = hub breakouts/breakdowns), each split by setup."""
    out = {}
    for kind in ("setup", "breakout"):
        rs = [r for r in rows(20000) if (r.get("kind") or "setup") == kind]
        out[kind] = _agg(rs)
        out[kind]["by_setup"] = [dict(setup=k, **_agg([r for r in rs if (r["setup"] or "Other") == k]))
                                 for k in sorted({r["setup"] or "Other" for r in rs})]
    return out
