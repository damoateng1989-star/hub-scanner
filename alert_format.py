"""Patch 213 (Nov 24): the conviction format for alerts — the measured record and the exact plan, stated plainly.

Only setups with a published measured record get it: hub A+ retests (A, long, clear path), double-bottom A retests and
the post-earnings gap fade & rip. Everything else keeps its plain text, and watch-only / ungraded alerts gain
"· WATCH ONLY" on the headline so no plain alert reads like a conviction alert.

Conviction comes from the record and the plan, never from a called direction: the format has no forecast words
(BANNED), and every price is one the signal already carries (trigger, entry reference, target, invalidation).
Record numbers are read from scanner.UNIVERSE_RECORD — the same source the setup cards use — never copied here.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd

BANNED = ("possible", "forming", "watch for", "keep an eye on", "might", "could", "should", "expecting", "likely")
PUSH_MAX = int(os.getenv("PUSH_MAX_CHARS", "230"))
NY = "America/New_York"

# key -> (record path in scanner.UNIVERSE_RECORD, record-line label, publishes a PF?)
RECORDS = {
    "hub_a_plus": (("hub", "a"), "Hub A+ retests", True),
    "double_a": (("double", "a"), "Double-bottom A retests", False),     # no PF is published for doubles
    "gapfade": (("gapfade", "record"), "Gap fade & rip", False),          # nor for the gap fade
}


def _money(x: float) -> str:
    return f"${x:,.2f}"


def record(key: str) -> dict | None:
    """The published numbers for a setup, straight from scanner.UNIVERSE_RECORD (single source)."""
    try:
        import scanner
        (a, b), label, has_pf = RECORDS[key]
        r = scanner.UNIVERSE_RECORD[a][b]
        return dict(label=label, avg=float(r["avg"]), n=int(r["n"]), win=float(r["win"]),
                    pf=float(r["pf"]) if has_pf and r.get("pf") is not None else None)
    except Exception:
        return None


def record_line(rec: dict) -> str:
    parts = [f"{rec['avg']:+.2f}% avg"] + ([f"PF {rec['pf']:.2f}"] if rec["pf"] is not None else []) \
        + [f"{rec['n']} trades", f"won {round(rec['win'])}%"]
    return f"{rec['label']}: " + " · ".join(parts)


def _push_record(rec: dict, short: bool = False) -> str:
    if short:
        return f"n={rec['n']}" + (f" · PF {rec['pf']:.2f}" if rec["pf"] is not None else "")
    if rec["pf"] is not None:
        return f"{rec['avg']:+.2f}% avg · PF {rec['pf']:.2f} · n={rec['n']}"
    return f"{rec['avg']:+.2f}% avg · n={rec['n']} · won {round(rec['win'])}%"


def _closes_after(day: date, hm: int, k: int) -> pd.Timestamp | None:
    """The k-th 4-hour close after (day, minutes past midnight ET): 1:30 PM and 4:00 PM on market days, 1:00 PM only on
    an early-close day, no weekends or NYSE holidays (the same rule the cards' 'window closes' uses)."""
    import flow_signal
    n = 0
    for step in range(0, 40):
        d = day + timedelta(days=step)
        if d.weekday() >= 5 or d.isoformat() in flow_signal.NYSE_HOLIDAYS:
            continue
        for c in ([780] if d.isoformat() in flow_signal.NYSE_EARLY_CLOSES else [810, 960]):
            if step == 0 and c <= hm:
                continue
            n += 1
            if n == k:
                return pd.Timestamp(d.isoformat(), tz=NY) + pd.Timedelta(minutes=c)
    return None


def window_after_candle(candle_start, k: int) -> str | None:
    """'Wed 1:30 PM': when the k-th 4-hour close after this candle's close lands."""
    try:
        import engine
        end = engine.candle_end(pd.Timestamp(candle_start).tz_convert(NY))
        t = _closes_after(end.date(), end.hour * 60 + end.minute, k)
        return _label(t, end)
    except Exception:
        return None


def window_trading_days(day_close, k: int) -> str | None:
    """'Tue Dec 8, 4:00 PM': the close k trading days after this daily close (the gap fade's 10-day clock)."""
    try:
        import flow_signal
        t0 = pd.Timestamp(day_close).tz_convert(NY)
        d, n = t0.date(), 0
        while n < k:
            d += timedelta(days=1)
            if d.weekday() < 5 and d.isoformat() not in flow_signal.NYSE_HOLIDAYS:
                n += 1
        c = 780 if d.isoformat() in flow_signal.NYSE_EARLY_CLOSES else 960
        return _label(pd.Timestamp(d.isoformat(), tz=NY) + pd.Timedelta(minutes=c), t0)
    except Exception:
        return None


def _label(t: pd.Timestamp | None, ref: pd.Timestamp) -> str | None:
    if t is None:
        return None
    tm = t.strftime("%-I:%M %p")
    return f"{t.strftime('%a')} {tm}" if (t.date() - ref.date()).days < 6 else f"{t.strftime('%a %b')} {t.day}, {tm}"


def compose(*, sym: str, grade: str, setup: str, rec_key: str, trigger: float, trigger_what: str, entry: float,
            entry_state: str, exit_px: float, exit_name: str, stop_rule: str, stop_short: str,
            tape_line: str | None = None, window: str | None = None, earnings: str | None = None, side: int = 1,
            stop_px: float | None = None) -> dict | None:
    """The conviction alert, in the four forms the notifier needs (Telegram/push × with/without the tape segment).
    Returns None when any slot is missing — the caller then sends its plain text (§1: never a partial plan)."""
    rec = record(rec_key)
    vals = (trigger, entry, exit_px)
    if rec is None or any(v is None or not pd.notna(v) for v in vals) or not stop_rule or not window:
        return None

    def tg(tape: bool) -> str:
        tag = "🟢 LONG · CALLS" if side == 1 else "SHORT · PUTS"   # Nov 25 (owner): the side, up front; 🟢 = an entry
        lines = [f"{tag} — {sym} — {grade} {setup.upper()}" + (" · TAPE CONFIRMED" if tape else ""), "", record_line(rec), "",
                 f"TRIGGER  {_money(trigger)} — {trigger_what}",
                 f"ENTRY    {_money(entry)} — {entry_state}",
                 f"EXIT     {_money(exit_px)} — {exit_name}",
                 f"STOP     {stop_rule} — dead", ""]
        if tape:
            lines.append(tape_line)
        lines.append(f"Window closes {window}")
        if earnings:      # owner, Nov 24: the earnings warning stays on conviction alerts (a gap can skip the stop)
            lines.append(f"📅 Earnings {earnings}: a gap can jump past the stop. Holding through it is a separate bet.")
        return "\n".join(lines)

    def push(tape: bool) -> str:
        head = f"{'🟢 LONG · CALLS' if side == 1 else 'SHORT · PUTS'} · {sym} · {grade} {setup.lower()}" + (", tape confirmed" if tape else "")
        plan = f"Trig {_money(trigger)} · Entry {_money(entry)} · Exit {_money(exit_px)} · Stop: {stop_short}."
        ern = f" Earnings {earnings}." if earnings else ""
        line = f"{head}. {plan} Record: {_push_record(rec)}.{ern}"
        if len(line) > PUSH_MAX:                  # shorten the record first — never cut the STOP or the earnings
            line = f"{head}. {plan} Record: {_push_record(rec, short=True)}.{ern}"
        return line

    def card(tape: bool) -> str:      # Nov 25 (owner): the 🟢 entry ticket, same facts as tg()
        import html as _h
        e = lambda x: _h.escape(str(x))
        sl_ = setup.lower()
        word = "RETEST ENTRY" if sl_ == "retest" else "DOUBLE-BOTTOM RETEST" if "double" in sl_ else f"{setup.upper()} ENTRY"
        up = (exit_px / entry - 1) * 100 * side
        rows = [f"{'🧱 EDGE' if 'retest' in setup.lower() else '⚡ TRIGGER':<11}{_money(trigger)}",
                f"{'🎯 TARGET':<11}{_money(exit_px)}  {up:+.1f}%"]
        rr = None
        if stop_px is not None and pd.notna(stop_px):
            dn = (stop_px / entry - 1) * 100 * side
            rows.append(f"{'🛑 STOP':<11}{_money(stop_px)}  {dn:+.1f}%")
            risk = abs(entry - stop_px)
            rr = abs(exit_px - entry) / risk if risk > 0 else None
        if rr:
            rows.append(f"{'⚖️ REWARD':<11}{rr:.1f}× the risk")
        parts = [f"🟢 <b>{e(word)} · {e(sym)}</b> <code>{e(grade)}</code>" + (" · <b>TAPE CONFIRMED</b>" if tape else ""),
                 f"<i>{'LONG · CALLS' if side == 1 else 'SHORT · PUTS'} · {e(entry_state)}</i>", "",
                 f"<b>{_money(entry)} → {_money(exit_px)}</b>", f"{up:+.1f}% to {e(exit_name)}",
                 f"<pre>{e(chr(10).join(rows))}</pre><blockquote>Dead on a {e(stop_rule)}</blockquote>"
                 f"Window closes {e(window)}", e(record_line(rec))]
        if tape and tape_line:
            parts.append(e(tape_line))
        if earnings:
            parts.append(f"📅 Earnings {e(earnings)}: a gap can jump past the stop. Holding through it is a separate bet.")
        return "\n".join(parts)

    has_tape = bool(tape_line)
    out = dict(tg_plain=tg(False), push_plain=push(False), title=f"{sym} · {grade} {setup.lower()}",
               tg_rich=tg(True) if has_tape else None, push_rich=push(True) if has_tape else None)
    try:
        out["tg_html"] = card(False)
        out["tg_html_rich"] = card(True) if has_tape else None
        import alert_style
        sl = setup.lower()
        ln = ("the-retest", "📘 Why the retest") if sl == "retest" else \
             ("double-tops-and-bottoms", "📘 Double bottoms") if "double" in sl else ("gap-fade-and-rip", "📘 Gap fade & rip")
        out["buttons"] = alert_style.buttons(sym, *ln)
    except Exception:
        out["tg_html"] = None      # the plain conviction text still goes out
    for k in ("tg_plain", "push_plain", "tg_rich", "push_rich"):
        if out[k] and any(w in out[k].lower() for w in BANNED):
            return None                            # §6 belt-and-braces: a banned word means the plain text goes out
    return out


def earnings_label(sym: str, within: int = 7) -> str | None:
    """'Thu Dec 3' when earnings are within a week (the same lookup and window as the plain alerts' earnings line)."""
    try:
        import earnings
        e = earnings.lookup(sym)
        return e["label"] if e and e["days"] <= within else None
    except Exception:
        return None


def watch_only(text: str) -> str:
    """§5: '· WATCH ONLY' on the headline (first line) of a watch-only or ungraded alert."""
    head, sep, rest = (text or "").partition("\n")
    if "WATCH ONLY" in head:
        return text
    head = head.replace(" — watch only", "").replace(" (watch only)", "").rstrip()     # say it once, in caps
    return f"{head} · WATCH ONLY{sep}{rest}"


def tape_text(row: dict | None, d: int) -> str | None:
    """'Tape confirmed — aggressive buying, net +$3.7M' (confirmed only; 'against' and 'none' stay silent)."""
    if not row or row.get("state") != "confirmed" or d != 1:
        return None
    try:
        return f"Tape confirmed — aggressive buying, net +${abs(float(row['flow_net'])) / 1e6:.1f}M"
    except Exception:
        return None
