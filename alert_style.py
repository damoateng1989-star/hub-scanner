"""Nov 25 (owner): one family look for every alert.

Colour lives in the header emoji (Telegram has no text colour) and, in the app, in the strip beside each alert:
  🟢 retest entry (the A trade)      🟣 cross entry (the early one)     🔵 breakout confirmed / early read
  🟡 heads-ups (trigger, pre-close, back at the edge, premarket) — sent SILENTLY
  🟠 a correction that cancels an earlier alert
  🎯 target hit   🔴 stopped   ⚪ dead / expired / stalled

dress() re-heads a plain alert and builds its Telegram card; entry cards (alert_format.compose, cross.alert_html) are
built where the numbers live. Push and texts keep plain text, headed by the same emoji.
"""
from __future__ import annotations

import html as _h
import os
import re

SILENT = {"trigger", "preclose", "premarket_gap", "snap-back", "stalled", "xretest"}

EMOJI = {
    "retest": "🟢", "retest_double": "🟢",
    "cross": "🟣",
    "breakout": "🔵", "breakdown": "🔵", "early": "🔵",
    "trigger": "🟡", "preclose": "🟡", "retesting_now": "🟡", "premarket_gap": "🟡",
    "unconfirmed": "🟠",
    "follow-through": "🎯", "snap-back": "⚪", "stalled": "⚪",
}
APP_COLORS = {"🟢": "#22C55E", "🟣": "#A855F7", "🔵": "#3B82F6", "🟡": "#FACC15", "🟠": "#FB923C",
              "🎯": "#FBBF24", "🔴": "#EF4444", "⚪": "#94A3B8"}

# a leading run of emoji / symbols / spaces on the headline (⚡ ⚠️ 🚀 🔻 🔄 👀 ✅ ↩️ ⏸ 🌅 ▲ …)
_LEAD = re.compile(r"^[\s←-⯿☀-➿\U0001F000-\U0001FAFF️‍▲▼]+")


def base_url() -> str:
    return os.getenv("APP_URL", "").strip().rstrip("/") or "https://nextshelfapp.com"


def buttons(sym: str, learn: str | None = None, learn_label: str | None = None, record: bool = False) -> list:
    b = [("📈 Open chart", f"{base_url()}/t/{sym}")]
    if record:
        b.append(("📊 See the record", f"{base_url()}/app#record"))
    elif learn:
        b.append((learn_label or "📘 Learn", f"{base_url()}/learn/{learn}"))
    return b


def esc(s) -> str:
    return _h.escape(str(s))


def reheader(text: str, emoji: str) -> str:
    head, sep, rest = (text or "").partition("\n")
    return f"{emoji} {_LEAD.sub('', head).strip()}{sep}{rest}"


def generic_html(text: str) -> str:
    """Headline bold; 'Candle closed' / 'Not advice' style footers in italics; everything else as is."""
    lines = (text or "").split("\n")
    out = [f"<b>{esc(lines[0])}</b>"] if lines else []
    for ln in lines[1:]:
        if ln.startswith(("Candle closed", "Not advice")):
            out.append(f"<i>{esc(ln)}</i>")
        else:
            out.append(esc(ln))
    return "\n".join(out)


def dress(alert_type: str, symbol: str, text: str) -> tuple[str, str | None, list | None, bool]:
    """(plain text, Telegram HTML or None, buttons or None, send silently?) for one plain alert."""
    emoji = EMOJI.get(alert_type)
    if not emoji or not text:
        return text, None, None, False
    if alert_type in ("retest", "retest_double") and "WATCH ONLY" in text.split("\n", 1)[0]:
        emoji = "🔵"                   # a watch-only retest isn't an entry: it doesn't wear the entry colour
    plain = reheader(text, emoji)
    silent = alert_type in SILENT or (alert_type == "retesting_now" and "RETEST" not in plain.split("\n", 1)[0])
    learn = {"breakout": ("the-retest", "📘 Why wait for the retest"), "early": ("the-retest", "📘 Why wait for the retest"),
             "retest": ("the-retest", "📘 Why the retest"), "retest_double": ("double-tops-and-bottoms", "📘 Double bottoms"),
             "retesting_now": ("the-retest", "📘 Why the retest")}.get(alert_type)
    rec = alert_type in ("follow-through", "snap-back", "stalled")
    btns = buttons(symbol, *(learn or (None, None)), record=rec) if (learn or rec or alert_type == "unconfirmed") else None
    if alert_type in ("trigger", "preclose", "premarket_gap"):
        btns = None                    # heads-ups stay small: no buttons
    return plain, generic_html(plain), btns, silent


def trade_over(kind: str, sym: str, setup: str, ret_pct: float | None, headline_detail: str, detail: str) -> tuple[str, str]:
    """🎯 / 🔴 / ⚪ result card: (plain, html). kind: target | stop | dead | expired."""
    emoji, word = {"target": ("🎯", "TARGET HIT"), "stop": ("🔴", "STOPPED"),
                   "dead": ("⚪", "DEAD"), "expired": ("⚪", "EXPIRED")}[kind]
    res = f"{ret_pct:+.1f}%" if ret_pct is not None else ""
    plain = f"{emoji} {word} · {sym} · {setup}\n{res} {headline_detail}".rstrip() + f"\n{detail}"
    html = (f"{emoji} <b>{word} · {esc(sym)}</b> <i>{esc(setup)}</i>\n"
            + (f"<b>{esc(res)}</b> " if res else "") + f"{esc(headline_detail)}\n{esc(detail)}")
    return plain, html
