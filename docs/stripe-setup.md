# Stripe checkout — turning it on

NextShelf Pro: **$29 / month, 7-day free trial** (`billing.py`). Card details are taken on Stripe's own pages
(Checkout and the customer portal) — never on nextshelfapp.com. Until the keys below are set, billing is off and
the Account page keeps its "contact the site owner" text.

1. **Stripe → Products → Add product**: "NextShelf Pro", recurring, **$29.00 USD monthly**. Copy its price id (`price_…`).
2. **Developers → API keys**: copy the secret key (`sk_live_…`, or `sk_test_…` to try it first).
3. **Developers → Webhooks → Add endpoint**: `https://nextshelfapp.com/api/billing/webhook`, events
   `checkout.session.completed`, `customer.subscription.created`, `customer.subscription.updated`,
   `customer.subscription.deleted`. Copy the signing secret (`whsec_…`).
4. **Settings → Billing → Customer portal**: turn it on (allow cancelling and updating the payment method).
5. **Render → Environment**: add `STRIPE_SECRET_KEY`, `STRIPE_PRICE_ID`, `STRIPE_WEBHOOK_SECRET`. Optional:
   `STRIPE_TRIAL_DAYS` (default 7; 0 = no trial). Save — Render redeploys.

Test mode first: with `sk_test_…` keys, pay with card `4242 4242 4242 4242`, any future date, any CVC. The member
turns Pro within seconds of the payment; the Account page shows "Free trial until …" and **Manage billing**.

Rules the code follows: Pro while the subscription is trialing, active or past_due; Free once it is canceled,
unpaid or expired. The trial is offered once per member. Admins and members upgraded by hand from the admin page
are never changed by Stripe.
