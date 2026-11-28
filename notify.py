"""Route alerts to the users watching each symbol, according to their plan."""
from __future__ import annotations

import logging

import threading

import db
import phone
import push
import telegram_bot

log = logging.getLogger("notify")
PRO_ONLY_TYPES = {"preclose"}        # pre-close warnings are a Pro feature
# Patch 218: "cross" (a hub's first trigger cross, a measured setup) reaches members who watch the stock.
# Nov 20: members get the trade and nothing else — the breakout heads-up, the "back at the edge" heads-up, and the
# retest itself (hub and double bottom/top). Hub formed, early read, unconfirmed, pre-close, premarket gap, the live
# trigger-cross note, other-pattern alerts and the every-A digest are admin-only now. Their own price alerts and the
# daily summaries go by other paths and are unaffected. MEMBER_ALERT_TYPES (comma list) overrides.
import os as _os
MEMBER_TYPES = set(filter(None, (_os.getenv("MEMBER_ALERT_TYPES") or
                                 "breakout,breakdown,retesting_now,retest,retest_double,cross").split(",")))
MEMBER_TYPES.add("cross")       # Patch 218 §6: always — the gate (watchlist / tape) lives in cross.py
MEMBER_BREAKOUT_STYLES = {"hub", "double", "gapfade"}    # breakouts of setups that have a trade plan (not triangles etc.)


def member_wants(u: dict, alert_type: str, style: str) -> bool:
    """Does this alert reach this member? Admins get everything."""
    if u["is_admin"]:
        return True
    if alert_type not in MEMBER_TYPES:
        return False
    if alert_type in ("breakout", "breakdown") and style not in MEMBER_BREAKOUT_STYLES:
        return False
    return True


def _pro(u: dict) -> bool:
    return u["plan"] == "pro" or bool(u["is_admin"])


def quiet_now(u: dict, now=None) -> bool:
    """Inside the member's quiet hours (New York time)? Then no phone calls or texts, and Telegram arrives without a sound."""
    q = (u.get("quiet") or "").strip()
    if not q or "-" not in q:
        return False
    try:
        import pandas as pd
        t = (now or pd.Timestamp.now(tz="America/New_York"))
        cur = t.hour * 60 + t.minute
        a, b = [int(x.split(":")[0]) * 60 + int(x.split(":")[1]) for x in q.split("-")]
    except Exception:
        return False
    return a <= cur < b if a < b else (cur >= a or cur < b)


def styles_of(u: dict) -> set[str]:
    """Setups a member wants alerts for. Hubs by default; extra setups (triangles, channels, S/R) are Pro."""
    picked = {x for x in (u.get("styles") or "hub").split(",") if x}
    return picked if _pro(u) else picked & {"hub"} or {"hub"}


def late_close(candle_start) -> bool:
    """Is this the 1:30-4:00 PM candle (confirmed after the market has closed)?"""
    try:
        import pandas as _pd
        t = _pd.Timestamp(candle_start).tz_convert("America/New_York")
        return t.hour * 60 + t.minute >= 13 * 60 + 30
    except Exception:
        return False


def symbol_alert(symbol: str, alert_type: str, text: str, speech: str | None = None, style: str = "hub",
                 pro_extra=None, candle_start=None, members: bool = True, flow_extra: str | None = None,
                 conv: dict | None = None, tg_html: str | None = None, tg_buttons: list | None = None, silent: bool = False):
    """Telegram to every watcher (Pro-only types to Pro users); texts to Pro users with a number; phone calls to Pro users with a number (preclose + breakouts only).
    pro_extra: a line (or a function that makes one, called once) added for Pro members, e.g. the options swing idea.
    conv: Patch 213's conviction format (alert_format.compose) — when given, it replaces `text` exactly as composed:
    the tape segment only for recipients the flow gate allows, push gets its one-line form, texts never carry the tape."""
    extra_cache = {}
    silent_type = bool(silent)
    if conv is None and tg_html is None:       # Nov 25: the family look (colour header, Telegram card, silent heads-ups)
        try:
            import alert_style
            text, tg_html, tg_buttons, silent_type = alert_style.dress(alert_type, symbol, text)
        except Exception as e:
            log.warning("alert_style.dress failed for %s %s: %s", symbol, alert_type, e)
    for u in db.watchers(symbol):
        if not member_wants(u, alert_type, style):
            continue
        if not members and not u["is_admin"]:      # this one failed the Lab (shorts, B grades, double tops): admin only
            continue
        if alert_type in PRO_ONLY_TYPES and not _pro(u):
            continue
        if style not in styles_of(u):
            continue
        if candle_start is not None and (u.get("closes") or "both") == "midday" and late_close(candle_start):
            continue                     # this member only trades the 1:30 PM close; the recap covers the 4:00 PM one
        quiet = quiet_now(u)
        hush = quiet or silent_type              # heads-ups land silently on Telegram and push; texts/calls unchanged
        msg = text
        pro_line = None
        if pro_extra and _pro(u):
            if "v" not in extra_cache:
                try:
                    extra_cache["v"] = pro_extra() if callable(pro_extra) else pro_extra
                except Exception:
                    extra_cache["v"] = None
            if extra_cache["v"]:
                msg = f"{text}\n{extra_cache['v']}"
                pro_line = extra_cache["v"]
        rich = msg                       # Patch 182: the tape line, for recipients the flow gate allows
        flow_ok = False
        if flow_extra:
            try:
                import swing_card
                if swing_card.flow_allowed(u):
                    rich = f"{msg}\n{flow_extra}"
                    flow_ok = True
            except Exception:
                pass                     # never delays or blocks the alert
        if conv:                         # Patch 213: the record + the plan, exactly as composed
            tape_ok = False
            if conv.get("tg_rich"):
                try:
                    import swing_card
                    tape_ok = swing_card.flow_allowed(u)
                except Exception:
                    tape_ok = False
            tg_txt = conv["tg_rich"] if tape_ok else conv["tg_plain"]
            card = (conv.get("tg_html_rich") if tape_ok else None) or conv.get("tg_html")   # Nov 25: the entry card
            if u["telegram_chat"] and card:
                telegram_bot.send(card, u["telegram_chat"], silent=quiet, html=True, buttons=conv.get("buttons"))
            elif u["telegram_chat"]:
                telegram_bot.send(tg_txt, u["telegram_chat"], silent=quiet)
            _push(u["id"], tg_txt, f"/t/{symbol}", f"{symbol}-{alert_type}", quiet,
                  title=conv["title"], body=conv["push_rich"] if tape_ok else conv["push_plain"])
            if _pro(u) and u["phone"] and u.get("sms", 1) != 0 and not quiet:
                phone.safe_text(conv["tg_plain"], to=u["phone"])      # texts and calls never carry the tape line
        else:
            if u["telegram_chat"] and tg_html:       # Nov 25: a designed Telegram card; push/texts stay plain
                import html as _hh
                card = tg_html + (f"\n{_hh.escape(pro_line)}" if pro_line else "") + (f"\n{_hh.escape(flow_extra)}" if flow_ok else "")
                telegram_bot.send(card, u["telegram_chat"], silent=hush, html=True, buttons=tg_buttons)
            elif u["telegram_chat"]:
                telegram_bot.send(rich, u["telegram_chat"], silent=hush)
            _push(u["id"], rich, f"/t/{symbol}", f"{symbol}-{alert_type}", hush)
            if _pro(u) and u["phone"] and u.get("sms", 1) != 0 and not quiet:
                phone.safe_text(msg, to=u["phone"])     # texts and calls never carry the tape line
        if speech and _pro(u) and u["phone"] and u.get("calls", 1) != 0 and phone.wants(alert_type) and not quiet:
            phone.safe_call(speech, to=u["phone"])


def every_a_alert(items: list[dict]):
    """Opt-in: every A setup confirmed in a board sweep, across all stocks, in one message per member.
    Skips stocks the member watches (they already get that stock's own alert) and setups they haven't picked."""
    import patterns
    fresh = []
    for x in items:                                           # once per setup, even if a sweep reruns
        key = f"every_a|{x['symbol']}|{x['kind']}|{x['entry_time']}"
        try:
            if db.add_alert(key, x["symbol"], "every_a", x["entry_time"], x["setup"]):
                fresh.append(x)
        except Exception:
            fresh.append(x)
    if not fresh:
        return 0
    money = lambda v: f"${v:,.2f}"
    sent = 0
    for u in db.all_users():
        if not u.get("a_all"):
            continue
        if not u["is_admin"] and "every_a" not in MEMBER_TYPES:      # Nov 20: members get the trade only
            continue
        mine = set(db.user_symbols(u["id"]))
        late = all(late_close(x["entry_time"]) for x in fresh)
        if (u.get("closes") or "both") == "midday" and late:
            continue
        picks = [x for x in fresh if x["symbol"] not in mine and patterns.STYLE_OF.get(x["kind"], "hub") in styles_of(u)]
        if not picks:
            continue
        lines = [f"🅰️ {len(picks)} A setup{'s' if len(picks) != 1 else ''} just confirmed (all stocks)"]
        for x in picks[:12]:
            rr = (x["target"] - x["entry"]) / (x["entry"] - x["stop"]) if x["entry"] > x["stop"] else 0
            lines.append(f"• {x['symbol']} · {x['setup']}: entry {money(x['entry'])} · stop {money(x['stop'])} · target {money(x['target'])}"
                         + (f" · {rr:.1f}R" if rr else ""))
        if len(picks) > 12:
            lines.append(f"…and {len(picks) - 12} more on the Setups page.")
        msg = "\n".join(lines)
        quiet = quiet_now(u)
        if u["telegram_chat"]:
            telegram_bot.send(msg, u["telegram_chat"], silent=quiet)
        _push(u["id"], msg, "/app#setups", "every-a", quiet)
        if _pro(u) and u["phone"] and u.get("sms", 1) != 0 and not quiet:
            phone.safe_text(msg, to=u["phone"])
        sent += 1
    return sent


def board_retest_alert(items: list[dict]):
    """Nov 24: board retest alerts — any stock on the board back at its edge after a breakout (Today's Retesting now),
    for members who opted in (Account → Board retests). Members: A+ only, in the conviction format (Patch 213).
    Admins can pick every retest; the rest come marked WATCH ONLY. Watchlist stocks are left out (they alert on their own).
    One message per member per sweep; each stock once per breakout."""
    import alert_format as af
    fresh = []
    for x in items:
        try:
            if db.add_alert(f"board_rt|{x['symbol']}|{x['breakout']}", x["symbol"], "board_rt", x["candle"].isoformat(),
                            f"{x['symbol']} back at the edge {x['edge']:.2f}"):
                fresh.append(x)
        except Exception:
            fresh.append(x)
    if not fresh:
        return 0
    money = lambda v: f"${v:,.2f}"
    for x in fresh:
        x["window"] = af.window_after_candle(x["candle"], x["left"])
        x["earnings"] = af.earnings_label(x["symbol"])
        tape = None
        try:
            tape = af.tape_text(db.flow_signal_for(x["symbol"], 1, pd_date(x["breakout"])), 1)
        except Exception:
            tape = None
        x["conv"] = af.compose(
            sym=x["symbol"], grade="A+", setup="retest", rec_key="hub_a_plus", trigger=x["edge"],
            trigger_what="4H close above the edge", entry=x["edge"], entry_state="the retest — at the edge now",
            exit_px=x["target"], exit_name="the next shelf" if x["shelf"] else "1 ATR past the hub",
            stop_rule=f"4H close back under {money(x['stop'])}", stop_short=f"4H close under {money(x['stop'])}",
            tape_line=tape, window=x["window"], earnings=x["earnings"]) if x["clear"] else None
    rec = af.record("hub_a_plus")
    sent = 0
    for u in db.all_users():
        mode = u.get("board_rt") or "off"
        if mode == "off":
            continue
        if mode == "all" and not u["is_admin"]:
            mode = "aplus"
        if (u.get("closes") or "both") == "midday" and all(late_close(x["candle"]) for x in fresh):
            continue
        mine = set(db.user_symbols(u["id"]))
        aplus = [x for x in fresh if x["conv"] and x["symbol"] not in mine]
        rest = [x for x in fresh if not x["conv"] and x["symbol"] not in mine] if mode == "all" else []
        if not aplus and not rest:
            continue
        tape_ok = False
        try:
            import swing_card
            tape_ok = swing_card.flow_allowed(u)
        except Exception:
            pass
        foot = "From the board · not on your watchlist"
        if len(aplus) == 1 and not rest:
            c = aplus[0]["conv"]
            tg = (c["tg_rich"] if tape_ok and c["tg_rich"] else c["tg_plain"]) + f"\n\n{foot}"
            plain = c["tg_plain"] + f"\n\n{foot}"
            title, body = c["title"] + " · at the edge", (c["push_rich"] if tape_ok and c["push_rich"] else c["push_plain"])
        else:
            lines = []
            if aplus:
                n = len(aplus)
                lines += [f"👀 {n} board stock{'s are' if n != 1 else ' is'} back at the edge · A+ RETESTS", ""]
                if rec:
                    lines += [af.record_line(rec), ""]
                for x in aplus[:10]:
                    lines.append(f"{x['symbol']}  entry {money(x['edge'])} · exit {money(x['target'])} · stop: 4H close under "
                                 f"{money(x['stop'])}" + (f" · window {x['window']}" if x["window"] else "")
                                 + (f" · 📅 earnings {x['earnings']}" if x["earnings"] else ""))
                if n > 10:
                    lines.append(f"…and {n - 10} more on Today's Retesting now.")
                lines += ["", "Each one: a 4H close that holds above its edge is the entry."]
            if rest:
                if lines:
                    lines.append("")
                lines.append(f"{'Also back at the edge' if aplus else f'👀 {len(rest)} board stock' + ('s' if len(rest) != 1 else '') + ' back at the edge'} · WATCH ONLY (not the tested setup):")
                for x in rest[:12]:
                    lines.append(f"{x['symbol']} {'↑' if x['d'] == 1 else '↓'} edge {money(x['edge'])} · target {money(x['target'])}")
            lines += ["", foot]
            tg = plain = "\n".join(lines)
            syms = [x["symbol"] for x in aplus] or [x["symbol"] for x in rest]
            title = f"{len(aplus) or len(rest)} board stock{'s' if (len(aplus) or len(rest)) != 1 else ''} back at the edge" + (" · A+" if aplus else " · watch only")
            body = ", ".join(syms[:8]) + (f" +{len(syms) - 8}" if len(syms) > 8 else "") + ". Each plan is in the app."
        quiet = quiet_now(u)
        if u["telegram_chat"]:
            telegram_bot.send(tg, u["telegram_chat"], silent=quiet)
        _push(u["id"], tg, "/app#today", "board-rt", quiet, title=title, body=body)
        if _pro(u) and u["phone"] and u.get("sms", 1) != 0 and not quiet:
            phone.safe_text(plain, to=u["phone"])
        sent += 1
    return sent


def pd_date(iso: str) -> str:
    import pandas as _pd
    return _pd.Timestamp(iso).tz_convert("America/New_York").date().isoformat()


def user_alert(u: dict, text: str, url: str, tag: str | None = None, tg_html: str | None = None, tg_buttons: list | None = None,
               silent: bool = False):
    """One message to one member (their own price alerts): Telegram, push, and a text for Pro members with a number."""
    quiet = quiet_now(u) or silent
    if u.get("telegram_chat") and tg_html:
        telegram_bot.send(tg_html, u["telegram_chat"], silent=quiet, html=True, buttons=tg_buttons)
    elif u.get("telegram_chat"):
        telegram_bot.send(text, u["telegram_chat"], silent=quiet)
    _push(u["id"], text, url, tag, quiet)
    if _pro(u) and u.get("phone") and u.get("sms", 1) != 0 and not quiet:
        phone.safe_text(text, to=u["phone"])


def _push(user_id: int, text: str, url: str, tag: str | None = None, silent: bool = False,
          title: str | None = None, body: str | None = None):
    """iPhone / browser notification, off the alert loop's thread so a slow push service never delays Telegram.
    title/body: given explicitly by Patch 213's one-line push (the whole line rides in the body, never cut)."""
    if title is None or body is None:
        title, body = push.split(text)
    threading.Thread(target=push.send, args=(user_id, title, body, url, tag, silent), daemon=True).start()


def admins(text: str):
    """Messages only the site owner should get (e.g. paper trading)."""
    for u in db.all_users():
        if u["is_admin"]:
            if u["telegram_chat"]:
                telegram_bot.send(text, u["telegram_chat"])
            _push(u["id"], text, "/app#paper", None)
