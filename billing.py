"""Stripe billing for NextShelf Pro (Nov 22 2026): $29/month with a 7-day free trial.

Checkout and the customer portal are Stripe-hosted pages, so card details never touch this server. Stripe's REST API is
called directly with `requests` (no SDK). Configuration, all from the environment (set on Render):

  STRIPE_SECRET_KEY      sk_live_... (or sk_test_... while testing)
  STRIPE_PRICE_ID        price_... — the recurring $29/month price created in the Stripe dashboard
  STRIPE_WEBHOOK_SECRET  whsec_... — from the webhook endpoint https://nextshelfapp.com/api/billing/webhook
  STRIPE_TRIAL_DAYS      optional, default 7 (0 turns the trial off)
  PUBLIC_URL             optional, default https://nextshelfapp.com

Without STRIPE_SECRET_KEY + STRIPE_PRICE_ID, billing is simply off: the endpoints answer 503 and the Account page keeps
its old "contact the site owner" text, so deploying this changes nothing until the keys are set.

The webhook is the source of truth for the plan: a member is Pro while their subscription is trialing, active or
past_due (Stripe is still retrying the card), and goes back to free when it is canceled, unpaid or expired. Members
upgraded by hand (no Stripe customer) and admins are never touched by a webhook.
"""
import hashlib
import hmac
import json
import logging
import os
import time

import requests

import db

log = logging.getLogger("billing")
API = "https://api.stripe.com/v1"
PRO_STATUSES = {"trialing", "active", "past_due"}
PRICE_LABEL = "$29 / month"


def _key() -> str:
    return os.getenv("STRIPE_SECRET_KEY", "").strip()


def _price() -> str:
    return os.getenv("STRIPE_PRICE_ID", "").strip()


def trial_days() -> int:
    try:
        return max(0, int(os.getenv("STRIPE_TRIAL_DAYS", "7")))
    except ValueError:
        return 7


def public_url() -> str:
    return os.getenv("PUBLIC_URL", "https://nextshelfapp.com").rstrip("/")


def enabled() -> bool:
    return bool(_key() and _price())


def _post(path: str, data: dict) -> dict:
    r = requests.post(f"{API}{path}", data=data, auth=(_key(), ""), timeout=20)
    body = r.json() if r.content else {}
    if r.status_code >= 400:
        msg = (body.get("error") or {}).get("message") or f"Stripe error {r.status_code}"
        log.warning("stripe %s failed: %s", path, msg)
        raise RuntimeError(msg)
    return body


def _get(path: str) -> dict:
    r = requests.get(f"{API}{path}", auth=(_key(), ""), timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"Stripe error {r.status_code}")
    return r.json()


# ---------------------------------------------------------------- checkout + portal
def checkout_url(u: dict) -> str:
    """A Stripe Checkout page for the Pro subscription. The trial is offered once per member."""
    base = public_url()
    data = {
        "mode": "subscription",
        "line_items[0][price]": _price(),
        "line_items[0][quantity]": "1",
        "client_reference_id": str(u["id"]),
        "metadata[user_id]": str(u["id"]),
        "subscription_data[metadata][user_id]": str(u["id"]),
        "allow_promotion_codes": "true",
        "success_url": f"{base}/app?billing=success#account",
        "cancel_url": f"{base}/app?billing=cancel#account",
    }
    if u.get("stripe_customer"):
        data["customer"] = u["stripe_customer"]
    else:
        data["customer_email"] = u["email"]
    days = trial_days()
    if days and not u.get("trial_used"):
        data["subscription_data[trial_period_days]"] = str(days)
    return _post("/checkout/sessions", data)["url"]


def portal_url(u: dict) -> str:
    """Stripe's customer portal: update the card, see invoices, cancel."""
    if not u.get("stripe_customer"):
        raise RuntimeError("No billing account yet")
    return _post("/billing_portal/sessions", {"customer": u["stripe_customer"], "return_url": f"{public_url()}/app#account"})["url"]


# ---------------------------------------------------------------- webhook
def verify(payload: bytes, header: str, secret: str | None = None, tolerance: int = 300) -> dict:
    """Check Stripe's signature (HMAC-SHA256 of "timestamp.payload") and return the event. Raises ValueError."""
    secret = secret if secret is not None else os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise ValueError("webhook secret not set")
    parts = {}
    for item in (header or "").split(","):
        k, _, v = item.strip().partition("=")
        parts.setdefault(k, []).append(v)
    try:
        ts = int(parts["t"][0])
    except (KeyError, ValueError):
        raise ValueError("bad signature header")
    if abs(time.time() - ts) > tolerance:
        raise ValueError("signature too old")
    want = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(want, v) for v in parts.get("v1", [])):
        raise ValueError("signature mismatch")
    return json.loads(payload)


def _apply_subscription(u: dict, sub: dict):
    status = sub.get("status") or ""
    end = sub.get("current_period_end") or (sub.get("items", {}).get("data") or [{}])[0].get("current_period_end")
    fields = dict(stripe_sub=sub.get("id"), sub_status=status,
                  sub_period_end=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(end))) if end else None,
                  sub_cancel_at_end=1 if sub.get("cancel_at_period_end") else 0)
    if sub.get("trial_start") or status == "trialing":
        fields["trial_used"] = 1
    if not u.get("is_admin"):
        fields["plan"] = "pro" if status in PRO_STATUSES else "free"
    db.update_user(u["id"], **fields)
    log.info("billing: user %s subscription %s -> %s", u["id"], sub.get("id"), status)


def handle(event: dict) -> str:
    """Apply one verified Stripe event. Safe to receive twice (Stripe retries)."""
    kind, obj = event.get("type", ""), (event.get("data") or {}).get("object") or {}
    if kind == "checkout.session.completed":
        uid = obj.get("client_reference_id") or (obj.get("metadata") or {}).get("user_id")
        u = db.get_user(int(uid)) if uid and str(uid).isdigit() else None
        if not u:
            return "no user"
        if obj.get("customer"):
            db.update_user(u["id"], stripe_customer=obj["customer"])
            u = db.get_user(u["id"])
        if obj.get("subscription"):
            sub = obj["subscription"] if isinstance(obj["subscription"], dict) else _get(f"/subscriptions/{obj['subscription']}")
            _apply_subscription(u, sub)
        return "linked"
    if kind in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
        u = db.user_by("stripe_customer", obj.get("customer")) if obj.get("customer") else None
        if not u:
            uid = (obj.get("metadata") or {}).get("user_id")
            u = db.get_user(int(uid)) if uid and str(uid).isdigit() else None
            if u and obj.get("customer"):
                db.update_user(u["id"], stripe_customer=obj["customer"])
                u = db.get_user(u["id"])
        if not u:
            return "no user"
        if kind == "customer.subscription.deleted":
            obj = dict(obj, status="canceled")
        _apply_subscription(u, obj)
        return obj.get("status") or "updated"
    return "ignored"


def status(u: dict) -> dict:
    return dict(enabled=enabled(), price=PRICE_LABEL, trial_days=0 if u.get("trial_used") else trial_days(),
                plan="pro" if (u.get("plan") == "pro" or u.get("is_admin")) else "free",
                status=u.get("sub_status"), period_end=u.get("sub_period_end"),
                cancel_at_end=bool(u.get("sub_cancel_at_end")), has_billing=bool(u.get("stripe_customer")),
                manual=bool(u.get("plan") == "pro" and not u.get("stripe_customer")))
