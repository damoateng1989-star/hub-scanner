"""Phone-call alerts through Twilio: your phone rings and a voice reads the alert."""
from __future__ import annotations

import logging
import os
from xml.sax.saxutils import escape

import requests

log = logging.getLogger("phone")


def _cfg():
    return (os.getenv("TWILIO_ACCOUNT_SID", "").strip(), os.getenv("TWILIO_AUTH_TOKEN", "").strip(),
            os.getenv("TWILIO_FROM", "").strip(), os.getenv("CALL_TO", "").strip())


def enabled() -> bool:
    """Twilio is configured (each Pro user adds their own number in Account)."""
    sid, token, from_, _ = _cfg()
    return all((sid, token, from_))


def call_on() -> set[str]:
    """Which alerts ring your phone: preclose, breakout (covers breakdowns), follow-through, snap-back."""
    return {x.strip().lower() for x in os.getenv("CALL_ON", "preclose,breakout").split(",") if x.strip()}


def wants(alert_type: str) -> bool:
    kind = "breakout" if alert_type in ("breakout", "breakdown") else alert_type
    return enabled() and kind in call_on()


def spell(sym: str) -> str:
    """'NVDA' -> 'N. V. D. A.' so the voice reads letters, not a word."""
    return " ".join(f"{c}." if c.isalpha() else c for c in sym)


def clean_number(n: str) -> str:
    digits = "".join(c for c in (n or "") if c.isdigit() or c == "+")
    if not digits.startswith("+"):
        digits = "+1" + digits if len(digits) == 10 else "+" + digits
    if not 11 <= len(digits) <= 16:
        raise ValueError("Enter a full phone number, e.g. +1 617 555 0123")
    return digits


def call(speech: str, to: str | None = None) -> str:
    """Place the call. Returns the Twilio call id. The message is read twice."""
    sid, token, from_, default_to = _cfg()
    to = to or default_to
    if not all((sid, token, from_)):
        raise RuntimeError("Phone calls aren't set up on this site yet (Twilio)")
    if not to:
        raise RuntimeError("Add your phone number first")
    twiml = (f'<Response><Pause length="1"/><Say voice="Polly.Joanna" loop="2">{escape(speech)}</Say>'
             f'<Say voice="Polly.Joanna">Details are in Telegram. Goodbye.</Say></Response>')
    r = requests.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json", auth=(sid, token),
                      data={"To": to, "From": from_, "Twiml": twiml}, timeout=20)
    if r.status_code >= 300:
        raise RuntimeError(f"Twilio error {r.status_code}: {r.text[:200]}")
    return r.json().get("sid", "")


def safe_call(speech: str, to: str | None = None):
    """Call, but never let a phone problem break scanning."""
    try:
        cid = call(speech, to)
        log.info("call placed %s", cid)
    except Exception as e:
        log.warning("call failed: %s", e)
