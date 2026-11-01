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
    return all(_cfg())


def call_on() -> set[str]:
    """Which alerts ring your phone: preclose, breakout (covers breakdowns), follow-through, snap-back."""
    return {x.strip().lower() for x in os.getenv("CALL_ON", "preclose,breakout").split(",") if x.strip()}


def wants(alert_type: str) -> bool:
    kind = "breakout" if alert_type in ("breakout", "breakdown") else alert_type
    return enabled() and kind in call_on()


def spell(sym: str) -> str:
    """'NVDA' -> 'N. V. D. A.' so the voice reads letters, not a word."""
    return " ".join(f"{c}." if c.isalpha() else c for c in sym)


def call(speech: str) -> str:
    """Place the call. Returns the Twilio call id. The message is read twice."""
    sid, token, from_, to = _cfg()
    if not all((sid, token, from_, to)):
        raise RuntimeError("Twilio isn't set up (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM, CALL_TO)")
    twiml = (f'<Response><Pause length="1"/><Say voice="Polly.Joanna" loop="2">{escape(speech)}</Say>'
             f'<Say voice="Polly.Joanna">Details are in Telegram. Goodbye.</Say></Response>')
    r = requests.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json", auth=(sid, token),
                      data={"To": to, "From": from_, "Twiml": twiml}, timeout=20)
    if r.status_code >= 300:
        raise RuntimeError(f"Twilio error {r.status_code}: {r.text[:200]}")
    return r.json().get("sid", "")


def safe_call(speech: str):
    """Call, but never let a phone problem break scanning."""
    try:
        cid = call(speech)
        log.info("call placed %s", cid)
    except Exception as e:
        log.warning("call failed: %s", e)
