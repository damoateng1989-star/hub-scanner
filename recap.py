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
    return dict(swept_at=upd.strftime("%-I:%M %p").replace("AM", "AM").lstrip("0") if upd is not None else None,
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
    when = d.get("swept_at") or "?"
    if d.get("swept_day") and d["swept_day"] != d["date"]:
        when = f"{d['swept_day']} {when}"
    L = [f"🌙 NextShelf evening recap · {d['date']}",
         f"Sweep at {when}: {d['universe']} stocks · {_n(d['new_count'], 'new breakout')} · {d['near_count']} at the edge going into tomorrow"]
    if not d.get("fresh"):
        L.append("⚠️ Today's after-the-close sweep hasn't finished, so this uses the earlier one. Check the app for the final 4:00 PM closes.")
    L.append("")
    if d["pro"]:
        if d["new"]:
            L.append("🚀 Broke out today (confirmed on the close)")
            L += ["• " + _plan(c) for c in d["new"]]
            L.append("")
        if d["near"]:
            L.append("👀 Watch tomorrow (at the edge of a hub)")
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
    L += ["", "Next decision points: tomorrow's 4H closes at 1:30 PM and 4:00 PM ET.",
          "Don't force trades. Let the trade come to you.", "Educational, not financial advice."]
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
