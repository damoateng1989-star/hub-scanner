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


def _pct(x: float, e: float) -> float:
    return round((x / e - 1) * 100, 2)


def _pending() -> list[dict]:
    """Confirmed setups (the live record) that have no next-day row yet."""
    rows = db._run("""SELECT s.* FROM signals s LEFT JOIN next_day n ON n.signal_id = s.id
                      WHERE s.pattern='rules' AND s.entry IS NOT NULL AND s.entry_time IS NOT NULL AND n.signal_id IS NULL
                      ORDER BY s.entry_time""").fetchall()
    cut = pd.Timestamp.now(tz=engine.NY) - pd.Timedelta(days=LOOKBACK_DAYS)
    out = []
    for r in rows:
        r = dict(r)
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
    e, stop, tgt = float(sig["entry"]), sig.get("stop"), sig.get("target")
    rest = None                                    # entered on the 1:30 close: what the rest of that day did
    if engine.candle_end(t).hour < 16:
        same = [k for k, x in enumerate(days) if x == entry_day]
        if same:
            rest = _pct(float(d["Close"].iloc[same[0]]), e)
    return dict(signal_id=sig["id"], symbol=sig["symbol"], setup=sig.get("setup"), grade=sig.get("grade"),
                entry=e, stop=stop, target=tgt, entry_time=sig["entry_time"], day=str(days[i].date()),
                open=round(o, 2), high=round(h, 2), low=round(lo, 2), close=round(c, 2), rest_pct=rest,
                open_pct=_pct(o, e), high_pct=_pct(h, e), low_pct=_pct(lo, e), close_pct=_pct(c, e),
                hit_stop=int(stop is not None and lo <= float(stop)), hit_target=int(tgt is not None and h >= float(tgt)))


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
    flag = " · 🎯 touched target" if r["hit_target"] else " · ⛔ touched stop" if r["hit_stop"] else ""
    icon = "🟢" if r["close_pct"] > 0 else "🔴" if r["close_pct"] < 0 else "⚪"
    return (f"{icon} <b>{html.escape(r['symbol'])}</b> {html.escape(r['setup'] or '')} · entry ${r['entry']:,.2f}\n"
            f"   open {sign(r['open_pct'])} · high {sign(r['high_pct'])} · low {sign(r['low_pct'])} · close <b>{sign(r['close_pct'])}</b>{flag}")


def text(rows: list[dict]) -> str:
    s = stats()
    head = "📋 <b>Next-day check</b> · how yesterday's confirmed setups did today"
    foot = (f"\n\nAll {s['n']} checked: {s['green_pct']}% closed green the next day, avg {s['avg_close']:+.2f}%, "
            f"{s['stop_pct']}% touched the stop, {s['target_pct']}% touched the target." if s["n"] else "")
    return head + "\n\n" + "\n".join(_line(r) for r in rows) + foot


def rows(limit: int = 200) -> list[dict]:
    return [dict(r) for r in db._run("SELECT * FROM next_day ORDER BY day DESC, symbol LIMIT ?", (limit,)).fetchall()]


def _agg(rs: list[dict]) -> dict:
    n = len(rs)
    if not n:
        return dict(n=0)
    f = lambda k: round(sum(r[k] for r in rs) / n, 2)
    return dict(n=n, green_pct=round(100 * sum(r["close_pct"] > 0 for r in rs) / n), avg_close=f("close_pct"),
                avg_open=f("open_pct"), avg_high=f("high_pct"), avg_low=f("low_pct"),
                stop_pct=round(100 * sum(r["hit_stop"] for r in rs) / n), target_pct=round(100 * sum(r["hit_target"] for r in rs) / n))


def stats() -> dict:
    rs = rows(5000)
    out = _agg(rs)
    out["by_setup"] = [dict(setup=k, **_agg([r for r in rs if (r["setup"] or "Other") == k])) for k in sorted({r["setup"] or "Other" for r in rs})]
    return out
