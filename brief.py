"""Morning brief: before the open on weekdays, what's closest to a breakout and what's on your watchlist.

Built from the latest market sweep (no extra data calls). Sent by Telegram and email, and shown on Today.
"""
from __future__ import annotations

import html
import json
import logging
import os

import pandas as pd

import auth
import board
import db
import engine
import telegram_bot
import universe

log = logging.getLogger("brief")
BRIEF_TIME = os.getenv("BRIEF_TIME", "08:45").strip()      # New York time, weekdays
APP_URL = os.getenv("APP_URL", "").rstrip("/")


def _money(x) -> str:
    return f"${x:,.2f}" if x is not None else "–"


def _n(k: int, word: str) -> str:
    return f"{k} {word}" + ("" if k == 1 else "s")


def _edge_dist(r: dict) -> float:
    return abs(r["to_high_pct"] if r.get("near_edge") == "up" else r["to_low_pct"])


def build(u: dict | None) -> dict:
    """Everything one member's brief needs, as plain data."""
    b = board.board()
    pro = auth.is_pro(u)
    near = sorted(b["near"], key=_edge_dist)
    live = sorted(b["live"], key=lambda r: abs(r.get("to_target_pct") or 99))
    forming = b["forming"]
    def shelf_of(r):
        sh = r.get("shelf_up") if r["near_edge"] == "up" else r.get("shelf_down")
        return sh["level"] if sh else None
    closest = [dict(symbol=r["symbol"], side=r["near_edge"], price=r.get("last_close"), low=r["hub"]["low"], high=r["hub"]["high"],
                    sessions=r["hub"]["sessions"], dist=round(_edge_dist(r), 2), shelf=shelf_of(r))
               for r in near[:5]]
    in_play = [dict(symbol=r["symbol"], up=r["state"] == "breakout_up", target=r.get("target"), to_target=r.get("to_target_pct"),
                    candles=(r.get("label") or "").split(", ")[-1]) for r in live[:3]]
    resting = [dict(symbol=r["symbol"], low=r["hub"]["low"], high=r["hub"]["high"], sessions=r["hub"]["sessions"])
               for r in forming[:3]]
    mine = []
    if u:
        for sym in db.user_symbols(u["id"]):
            row = db.get_ticker(sym) or {}
            st = json.loads(row.get("status") or "{}")
            if st.get("state") in ("forming", "watching", "breakout_up", "breakdown"):
                mine.append(dict(symbol=sym, state=st["state"], low=(st.get("hub") or {}).get("low"),
                                 high=(st.get("hub") or {}).get("high"), target=st.get("target"), price=st.get("last_close")))
        watching = len(db.user_symbols(u["id"]))
    else:
        watching = 0
    return dict(date=pd.Timestamp.now(tz=engine.NY).strftime("%a %b %-d"), pro=pro, universe=b["universe"],
                universe_name=universe.describe(), live_count=len(b["live"]), near_count=len(b["near"]),
                forming_count=len(forming), closest=closest if pro else [], in_play=in_play if pro else [],
                resting=resting, mine=mine, watching=watching, updated=b.get("updated"))


def _mine_line(m: dict) -> str:
    if m["state"] in ("forming", "watching"):
        return f"{m['symbol']}: resting in a hub {_money(m['low'])}–{_money(m['high'])}. Trade only on a 4H close outside it."
    up = m["state"] == "breakout_up"
    return f"{m['symbol']}: {'broke out ↑' if up else 'broke down ↓'}, aiming for {_money(m['target'])}."


def text(d: dict) -> str:
    """Telegram version."""
    L = [f"☀️ NextShelf morning brief · {d['date']}",
         f"Last sweep: {d['universe']} stocks · {_n(d['live_count'], 'breakout')} in play · {d['near_count']} at the edge of a hub", ""]
    if d["pro"]:
        if d["closest"]:
            L.append("🎯 Closest to a breakout today")
            for c in d["closest"]:
                L.append(f"• {c['symbol']} at the {'top' if c['side'] == 'up' else 'bottom'} of its hub {_money(c['low'])}–{_money(c['high'])}"
                         + (" · right at the edge" if c['dist'] < 0.05 else f" · {c['dist']}% to a {'breakout' if c['side'] == 'up' else 'breakdown'}")
                         + (f" · next shelf {_money(c['shelf'])}" if c.get("shelf") else ""))
            L.append("")
        if d["in_play"]:
            L.append("🚀 Breakouts still in play")
            for c in d["in_play"]:
                L.append(f"• {c['symbol']} {'↑' if c['up'] else '↓'} aiming for {_money(c['target'])}"
                         + (f" ({c['to_target']:+.1f}%)" if c.get("to_target") is not None else "") + (f" · {c['candles']}" if c.get("candles") else ""))
            L.append("")
    else:
        L += [f"🔒 {_n(d['near_count'], 'stock')} {'is' if d['near_count'] == 1 else 'are'} at the edge of a hub today. Pro shows the 5 closest to breaking out.", ""]
        if d["resting"]:
            L.append("🟦 Resting in a hub")
            L += [f"• {c['symbol']} {_money(c['low'])}–{_money(c['high'])} ({c['sessions']} sessions)" for c in d["resting"]]
            L.append("")
    L.append("⭐ Your watchlist")
    if d["mine"]:
        L += ["• " + _mine_line(m) for m in d["mine"][:10]]
    elif d["watching"]:
        L.append("Nothing on your watchlist is setting up. That's a good day to do nothing.")
    else:
        L.append("Your watchlist is empty. Add a few stocks and NextShelf will watch them for you.")
    L += ["", "Don't force trades. Let the trade come to you.", "Educational, not financial advice."]
    if APP_URL:
        L.append(f"{APP_URL}/app#today")
    return "\n".join(L)


def email_html(d: dict, footer: str) -> str:
    e = html.escape
    sec = lambda title, items: (f'<h3 style="margin:22px 0 8px;font-size:15px">{title}</h3><ul style="margin:0;padding-left:18px;color:#333">'
                                + "".join(f"<li style='margin:4px 0'>{i}</li>" for i in items) + "</ul>") if items else ""
    parts = [f'<div style="font-family:Inter,Arial,sans-serif;max-width:560px;margin:auto;color:#111">',
             f'<p style="color:#16a34a;font-weight:700;margin:0">NextShelf morning brief</p><h2 style="margin:4px 0 6px">{e(d["date"])}</h2>',
             f'<p style="color:#555;margin:0">Last sweep: {d["universe"]} stocks · <b>{d["live_count"]}</b> breakout{"" if d["live_count"] == 1 else "s"} in play · <b>{d["near_count"]}</b> at the edge of a hub.</p>']
    if d["pro"]:
        parts.append(sec("🎯 Closest to a breakout today", [
            f"<b>{e(c['symbol'])}</b> at the {'top' if c['side'] == 'up' else 'bottom'} of its hub {_money(c['low'])}–{_money(c['high'])}"
            + (" · right at the edge" if c['dist'] < 0.05 else f" · {c['dist']}% to a {'breakout' if c['side'] == 'up' else 'breakdown'}") + (f" · next shelf {_money(c['shelf'])}" if c.get("shelf") else "")
            for c in d["closest"]]))
        parts.append(sec("🚀 Breakouts still in play", [
            f"<b>{e(c['symbol'])}</b> {'↑' if c['up'] else '↓'} aiming for {_money(c['target'])}"
            + (f" ({c['to_target']:+.1f}%)" if c.get("to_target") is not None else "") for c in d["in_play"]]))
    else:
        parts.append(f'<p style="margin:18px 0 0;padding:12px;border-radius:10px;background:#f4f6f8">🔒 {d["near_count"]} stocks are at the edge of a hub today. '
                     f'Pro shows the 5 closest to breaking out.</p>')
        parts.append(sec("🟦 Resting in a hub", [f"<b>{e(c['symbol'])}</b> {_money(c['low'])}–{_money(c['high'])} ({c['sessions']} sessions)" for c in d["resting"]]))
    mine = [e(_mine_line(m)) for m in d["mine"][:10]] or [
        "Nothing on your watchlist is setting up. That's a good day to do nothing." if d["watching"]
        else "Your watchlist is empty. Add a few stocks and NextShelf will watch them for you."]
    parts.append(sec("⭐ Your watchlist", mine))
    if APP_URL:
        parts.append(f'<p style="margin:22px 0"><a href="{APP_URL}/app#today" style="background:#16a34a;color:#fff;padding:10px 16px;border-radius:999px;'
                     f'text-decoration:none;font-weight:700">Open NextShelf</a></p>')
    parts.append(f'<p style="margin-top:22px;font-weight:700;color:#16a34a">Don\'t force trades. Let the trade come to you.</p>'
                 f'<p style="color:#777;font-size:12px">Educational, not financial advice. {footer}</p></div>')
    return "".join(parts)


def send_to(u: dict) -> dict:
    d = build(u)
    sent = dict(telegram=False, email=False)
    if u.get("telegram_chat"):
        telegram_bot.send(text(d), u["telegram_chat"])
        sent["telegram"] = True
    if u.get("email") and os.getenv("RESEND_API_KEY"):
        addr = os.getenv("MAILING_ADDRESS", "").strip()
        footer = (f"NextShelf · {html.escape(addr)} · " if addr else "") + (
            f'<a href="{APP_URL}/app#account" style="color:#777">Turn off the morning brief</a>' if APP_URL else "Turn it off in Settings.")
        sent["email"] = auth.send_email(u["email"], f"NextShelf morning brief · {d['date']}", email_html(d, footer))
    return sent


def send_all() -> int:
    """Scheduled: every member with the brief on (default) gets it."""
    n = 0
    for u in db.all_users():
        if u.get("brief", 1) == 0:
            continue
        try:
            s = send_to(u)
            n += bool(s["telegram"] or s["email"])
        except Exception as e:
            log.warning("brief failed for user %s: %s", u.get("id"), e)
    log.info("morning brief sent to %d members", n)
    return n
