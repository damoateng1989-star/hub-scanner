"""Nov 27 (owner): pre-market / after-hours retest heads-ups.

When a setup that's waiting for its retest touches the edge and closes back on the breakout side on a 30-minute
candle OUTSIDE market hours (4:00-9:30 AM or 4:00-8:00 PM ET), the stock's watchers and the admins get a silent
heads-up. Same touch rule as the 4H retest (within 0.25 x the breakout ATR, close back within 0.5 x ATR).

It is context, not an entry: NextShelf only counts 9:30-4:00, so the 4H retest, grades, the record and the
🟢 entry alert are unchanged. One heads-up per setup per session."""
import html as _h
import logging

import pandas as pd

import board
import data
import db
import engine
import notify

log = logging.getLogger("xhours")
NY = "America/New_York"


def _money(x: float) -> str:
    return f"${x:,.2f}"


def _window(now_et: pd.Timestamp) -> str | None:
    """Which extended session the 30-minute candle that just closed belongs to."""
    m = now_et.hour * 60 + now_et.minute
    try:
        import flow_signal
        early = now_et.date().isoformat() in flow_signal.NYSE_EARLY_CLOSES
    except Exception:
        early = False
    close = 13 * 60 if early else 16 * 60
    if 4 * 60 + 30 <= m <= 9 * 60 + 35:
        return "pre"
    if close + 30 <= m <= 20 * 60 + 5:
        return "post"
    return None


def _candidates() -> list[dict]:
    b = board.board()
    out, seen = [], set()
    for r in list(b.get("live", [])) + list(b.get("setups", [])):
        rt = r.get("retest") or {}
        if (not rt or rt.get("candles_left", 0) <= 0 or rt.get("retested") or not (rt.get("edge") and rt.get("atr") and rt.get("after"))
                or r["symbol"] in seen):
            continue
        seen.add(r["symbol"])
        out.append(dict(symbol=r["symbol"], edge=float(rt["edge"]), atr=float(rt["atr"]), after=int(rt["after"]),
                        d=int(rt.get("dir", 1) or 1), kind=r.get("name") or "Hub"))
    return out[:40]


def tick(now: pd.Timestamp | None = None) -> dict:
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    et = now.tz_convert(NY)
    win = _window(et)
    if win is None or et.weekday() >= 5:
        return dict(skipped="outside pre-market / after-hours")
    sent = 0
    for c in _candidates():
        try:
            bars = data.get_bars(c["symbol"], 5)
            f = engine.to_hours_ext(bars, 0.5, now=now)
            if f.empty:
                continue
            f = f[(f["session"] == win) & (f.index >= pd.Timestamp(c["after"], unit="s", tz="UTC"))]
            f = f[f.index + pd.Timedelta(minutes=30) <= now]                  # completed candles only
            f = f[f.index.tz_convert(NY).date == et.date()]
            if f.empty:
                continue
            bar = f.iloc[-1]
            e, a = c["edge"], c["atr"]
            if c["d"] == 1:
                ok = bar["Low"] <= e + engine.RETEST_TOUCH * a and e < bar["Close"] <= e + engine.RETEST_NEAR * a
            else:
                ok = bar["High"] >= e - engine.RETEST_TOUCH * a and e - engine.RETEST_NEAR * a <= bar["Close"] < e
            if not ok:
                continue
            if _send(c, win, f.index[-1], float(bar["Close"])):
                sent += 1
        except Exception as ex:
            log.warning("extended-hours retest check failed for %s: %s", c["symbol"], ex)
    return dict(window=win, checked=len(_candidates()), sent=sent)


def _send(c: dict, win: str, t: pd.Timestamp, close: float) -> bool:
    sym, e = c["symbol"], c["edge"]
    when = "pre-market" if win == "pre" else "after hours"
    side = "above" if c["d"] == 1 else "below"
    tm = t.tz_convert(NY) + pd.Timedelta(minutes=30)
    clock = tm.strftime("%-I:%M %p")
    nxt = "the 9:30 open" if win == "pre" else "the next session"
    msg = (f"{'🌅' if win == 'pre' else '🌙'} {sym} held its retest edge {_money(e)} {when} · {clock} ET\n"
           f"A 30-minute candle touched {_money(e)} and closed {side} it at {_money(close)}.\n"
           f"Not counted: NextShelf only counts 9:30–4:00. The retest still needs a regular-session 4H close {side} "
           f"{_money(e)}, from {nxt}. Heads-up only, not an entry.")
    card = (f"{'🌅' if win == 'pre' else '🌙'} <b>{_h.escape(sym)} held its retest edge {_money(e)} {when}</b> · <i>{clock} ET</i>\n"
            f"A 30-minute candle touched {_money(e)} and closed {side} it at {_money(close)}.\n"
            f"<blockquote>Not counted: NextShelf only counts 9:30–4:00. The retest still needs a regular-session "
            f"4H close {side} {_money(e)}, from {nxt}.</blockquote><i>Heads-up only, not an entry</i>")
    key = f"xret:{sym}:{e}:{t.tz_convert(NY).date().isoformat()}:{win}"
    if not db.add_alert(key, sym, "xretest", t.isoformat(), msg):
        return False
    done = set()
    try:
        for u in db.watchers(sym):
            done.add(u["id"])
        notify.symbol_alert(sym, "xretest", msg, style="hub", candle_start=t, tg_html=card, silent=True)
    except Exception as ex:
        log.warning("extended retest heads-up (watchers) failed for %s: %s", sym, ex)
    for u in db.all_users():
        if u["id"] in done or not u["is_admin"]:
            continue
        try:
            notify.user_alert(u, msg, f"/t/{sym}", f"{sym}-xretest", tg_html=card, silent=True)
        except Exception as ex:
            log.warning("extended retest heads-up failed for user %s: %s", u.get("id"), ex)
    return True
