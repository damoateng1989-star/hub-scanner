"""Evening recap: after the 4:00 PM close, what happened today and the plan for tomorrow.

For swing traders with day jobs: the 4H closes land at 1:30 and 4:00 PM, mid-workday. This sums up
both in one message they can read after work. Built from the latest market sweep (no extra data calls).
"""
from __future__ import annotations

import html
import json
import logging
import os

import pandas as pd

import auth
import board
import brief
import db
import earnings
import engine
import telegram_bot

log = logging.getLogger("recap")
RECAP_TIME = os.getenv("RECAP_TIME", "16:45").strip()     # New York time, weekdays, after the 4:17 PM sweep
APP_URL = brief.APP_URL
_money, _n = brief._money, brief._n


def _k(r: dict) -> int:
    """4H candles since the breakout (0 = the breakout candle itself)."""
    try:
        return int((r.get("label") or "").split(", ")[-1].split("/")[0])
    except Exception:
        return 99


def _edge(r: dict) -> float | None:
    h = r.get("hub") or {}
    return h.get("high") if r.get("state") == "breakout_up" else h.get("low")


def _earn(sym: str) -> str:
    try:
        e = earnings.lookup(sym)
    except Exception:
        e = None
    return f" · 📅 earnings {e['label']}" if e and e["days"] <= 7 else ""


def next_session(today) -> dict:
    """Patch 222 §5: the next day the market actually opens, from the NYSE holiday / early-close calendar the bar logic
    already uses (flow_signal), and — when that day is an early close — the next full session after it."""
    import flow_signal
    from datetime import timedelta
    d0 = today.date() if hasattr(today, "date") else today

    def nxt(d):
        d += timedelta(days=1)
        while not flow_signal.is_trading_day(d):
            d += timedelta(days=1)
        return d
    lbl = lambda d: d.strftime("%a %b ") + str(d.day)
    full = lambda d: d.strftime("%A %b ") + str(d.day)
    d = nxt(d0)
    out = dict(date=d.isoformat(), short=lbl(d), long=full(d), early=d.isoformat() in flow_signal.NYSE_EARLY_CLOSES)
    if out["early"]:
        f = nxt(d)
        while f.isoformat() in flow_signal.NYSE_EARLY_CLOSES:
            f = nxt(f)
        out.update(full_short=lbl(f), full_long=full(f))
    return out


def today_session(now) -> dict:
    """Patch 226 §B1: today's session from the same calendar (trading day? early close?) plus the next session."""
    import flow_signal
    d0 = now.date() if hasattr(now, "date") else now
    return dict(today=d0.isoformat(), trading=bool(flow_signal.is_trading_day(d0)),
                early=d0.isoformat() in flow_signal.NYSE_EARLY_CLOSES, next=next_session(now))


def build(u: dict | None) -> dict:
    b = board.board()
    pro = auth.is_pro(u)
    today = pd.Timestamp.now(tz=engine.NY)
    new = [r for r in b["live"] if _k(r) <= 1]
    held = [r for r in b["live"] if _k(r) > 1]
    near = sorted(b["near"], key=brief._edge_dist)[:3]
    new_rows = [dict(symbol=r["symbol"], up=r["state"] == "breakout_up", close=r.get("last_close"), edge=_edge(r),
                     target=r.get("target"), to_target=r.get("to_target_pct"), earn=_earn(r["symbol"]),
                     shelf=r.get("target_kind") != "atr") for r in new[:5]]
    near_rows = [dict(symbol=r["symbol"], side=r["near_edge"], low=r["hub"]["low"], high=r["hub"]["high"], earn=_earn(r["symbol"]))
                 for r in near]
    mine, events = [], []
    if u:
        syms = db.user_symbols(u["id"])
        start = today.normalize().tz_convert("UTC").isoformat()
        for a in db.alerts_for_symbols(syms, 60, exclude_types=("preclose",)):
            if (a.get("candle_time") or "") >= start:
                events.append(a["message"].split("\n")[0])
        for sym in syms:
            st = json.loads((db.get_ticker(sym) or {}).get("status") or "{}")
            if st.get("state") in ("breakout_up", "breakdown", "forming", "watching"):
                mine.append(dict(symbol=sym, state=st["state"], low=(st.get("hub") or {}).get("low"),
                                 high=(st.get("hub") or {}).get("high"), target=st.get("target"), earn=_earn(sym)))
    upd = None
    try:
        upd = pd.Timestamp(b.get("updated")).tz_localize("UTC").tz_convert(engine.NY) if b.get("updated") else None
    except Exception:
        upd = None
    fresh = bool(upd is not None and upd.date() == today.date() and upd.hour * 60 + upd.minute >= 16 * 60)
    try:      # Patch 218 §7: the cross forward log, both scorings (shown once the first row has closed)
        import cross
        xs = cross.forward_summary() if (u or {}).get("is_admin") else dict(n=0)   # Nov 26: crosses are a heads-up; the log is the owner's
        rec = cross.record(); xs.update(rec_avg=rec["avg"], rec_pf=rec.get("pf"), rec_n=rec.get("n"), rec_slice=rec.get("slice", ""))
    except Exception:
        xs = dict(n=0)
    return dict(cross=xs, next=next_session(today), swept_at=upd.strftime("%-I:%M %p").replace("AM", "AM").lstrip("0") if upd is not None else None,
                swept_day=upd.strftime("%a %b %-d") if upd is not None else None, fresh=fresh,
                date=today.strftime("%a %b %-d"), pro=pro, universe=b["universe"], new_count=len(new), held_count=len(held),
                near_count=len(b["near"]), new=new_rows if pro else [], near=near_rows if pro else [],
                events=events[:8], mine=mine[:10], watching=len(db.user_symbols(u["id"])) if u else 0)


def _plan(c: dict) -> str:
    up = c["up"]
    return (f"{c['symbol']} {'broke out' if up else 'broke down'} · heading to {'next shelf' if c.get('shelf', True) else 'target'} · closed {_money(c['close'])}\n"
            f"   Target {_money(c['target'])}" + (f" ({c['to_target']:+.1f}%)" if c.get("to_target") is not None else "")
            + f" · wrong if a 4H close is back {'below' if up else 'above'} {_money(c['edge'])}{c['earn']}")


def _mine_line(m: dict) -> str:
    if m["state"] in ("forming", "watching"):
        return f"{m['symbol']}: still in its hub {_money(m['low'])}–{_money(m['high'])}. Nothing to do.{m['earn']}"
    up = m["state"] == "breakout_up"
    return f"{m['symbol']}: {'breakout' if up else 'breakdown'} in play, aiming for {_money(m['target'])}.{m['earn']}"


def text(d: dict) -> str:
    nx = d.get("next") or next_session(pd.Timestamp.now(tz=engine.NY))
    when = d.get("swept_at") or "?"
    if d.get("swept_day") and d["swept_day"] != d["date"]:
        when = f"{d['swept_day']} {when}"
    L = [f"🌙 NextShelf evening recap · {d['date']}",
         f"Sweep at {when}: {d['universe']} stocks · {_n(d['new_count'], 'new breakout')} · {d['near_count']} at the edge going into the next session ({nx['short']})"]
    if not d.get("fresh"):
        L.append("⚠️ Today's after-the-close sweep hasn't finished, so this uses the earlier one. Check the app for the final 4:00 PM closes.")
    L.append("")
    if d["pro"]:
        if d["new"]:
            L.append("🚀 Broke out today (confirmed on the close)")
            L += ["• " + _plan(c) for c in d["new"]]
            L.append("")
        if d["near"]:
            L.append(f"👀 Watch the next session ({nx['short']}) (at the edge of a hub)")
            L += [f"• {c['symbol']} at the {'top' if c['side'] == 'up' else 'bottom'} of {_money(c['low'])}–{_money(c['high'])}{c['earn']}" for c in d["near"]]
            L.append("")
    else:
        L += [f"🔒 {_n(d['new_count'], 'stock')} broke out today. Pro shows each one with its target and exit level.", ""]
    L.append("⭐ Your watchlist today")
    if d["events"]:
        L += ["• " + e for e in d["events"]]
    if d["mine"]:
        L += ["• " + _mine_line(m) for m in d["mine"]]
    if not d["events"] and not d["mine"]:
        L.append("Quiet day on your watchlist. No trade is a fine trade." if d["watching"] else
                 "Your watchlist is empty. Add a few stocks and NextShelf will watch them while you work.")
    xs = d.get("cross") or {}
    if xs.get("n"):
        L += ["", "📈 Cross heads-ups (forward log, owner only)",
              f"• {_n(xs['n'], 'closed setup')} since {xs['since']}, {xs.get('live', 0)} live · "
              f"{xs['avg_trig']:+.2f}% per signal from the trigger · {xs['avg_touch']:+.2f}% from the first touch "
              f"(the gap is the fill cost: {xs['fill_cost']:+.2f}%)",
              # Nov 26: name the slice so the benchmark can't be mistaken for another test's number
              f"• The 2-year test{' (' + xs['rec_slice'] + ')' if xs.get('rec_slice') else ''}: {xs.get('rec_avg', 0):+.2f}% per signal "
              f"from the trigger" + (f" · PF {xs['rec_pf']:.2f} · n {xs['rec_n']:,}" if xs.get('rec_pf') is not None and xs.get('rec_n') else "") + "."]
    # Patch 222 §5: never promise a close that won't happen (holidays skipped; an early close named with its real end)
    nxt_line = (f"Next session: {nx['long']}, an early close at 1:00 PM ET. Full 4H decision points resume "
                f"{nx['full_long']} at 1:30 PM and 4:00 PM ET." if nx.get("early") else
                f"Next decision points: {nx['long']}'s 4H closes at 1:30 PM and 4:00 PM ET.")
    L += ["", nxt_line,
          "Don't force trades. Let the trade come to you.", "— Sika", "Educational, not financial advice."]   # Patch 226 §B2: her byline
    if APP_URL:
        L.append(f"{APP_URL}/app#today")
    return "\n".join(L)


def email_html(d: dict, footer: str) -> str:
    e = html.escape
    body = "".join(f"<p style='margin:0;white-space:pre-wrap'>{e(line) or '&nbsp;'}</p>" for line in text(d).split("\n")[1:])
    return (f'<div style="font-family:Inter,Arial,sans-serif;max-width:560px;margin:auto;color:#111">'
            f'<p style="color:#6d28d9;font-weight:700;margin:0">NextShelf evening recap</p><h2 style="margin:4px 0 14px">{e(d["date"])}</h2>'
            f'<div style="font-size:14px;line-height:1.6">{body}</div><p style="color:#777;font-size:12px;margin-top:20px">{footer}</p></div>')


def send_to(u: dict) -> dict:
    d = build(u)
    sent = dict(telegram=False, email=False)
    if u.get("telegram_chat"):
        telegram_bot.send(text(d), u["telegram_chat"])
        sent["telegram"] = True
    if u.get("email") and os.getenv("RESEND_API_KEY"):
        addr = os.getenv("MAILING_ADDRESS", "").strip()
        footer = (f"NextShelf · {html.escape(addr)} · " if addr else "") + (
            f'<a href="{APP_URL}/app#account" style="color:#777">Turn off the evening recap</a>' if APP_URL else "Turn it off in Settings.")
        sent["email"] = auth.send_email(u["email"], f"NextShelf evening recap · {d['date']}", email_html(d, footer))
    return sent


def send_all() -> int:
    n = 0
    for u in db.all_users():
        if u.get("recap", 1) == 0:
            continue
        try:
            s = send_to(u)
            n += bool(s["telegram"] or s["email"])
        except Exception as e:
            log.warning("recap failed for user %s: %s", u.get("id"), e)
    log.info("evening recap sent to %d members", n)
    return n
