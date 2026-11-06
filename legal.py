"""Public trust pages: Terms, Privacy, Risk disclaimer, Contact.

Rendered server-side so the business email and mailing address come from server settings
(CONTACT_EMAIL, MAILING_ADDRESS) instead of being written into the code.
Have a lawyer review these before charging customers.
"""
from __future__ import annotations

import html
import os
from pathlib import Path

UPDATED = "November 2, 2026"
STATIC = Path(__file__).parent / "static"


def _email() -> str:
    return os.getenv("CONTACT_EMAIL", "").strip()


def _address() -> str:
    return os.getenv("MAILING_ADDRESS", "").strip()


def _contact_line() -> str:
    e, a = _email(), _address()
    parts = []
    if e:
        parts.append(f'email <a href="mailto:{html.escape(e)}">{html.escape(e)}</a>')
    parts.append('use our <a href="/contact">contact form</a>')
    if a:
        parts.append(f"write to NextShelf, {html.escape(a)}")
    return ", ".join(parts[:-1]) + (", or " if len(parts) > 1 else "") + parts[-1]


def terms() -> str:
    return f"""
<p class="lead">These terms explain the rules for using NextShelf. By creating an account or using the site, you agree to them.</p>

<h2>1. What NextShelf is</h2>
<p>NextShelf is an educational software tool. It scans stock price data for chart patterns (such as hubs, flags, double tops and bottoms, triangles, channels and
support and resistance levels), sends alerts, and shows how similar patterns behaved in the past. NextShelf does not know your
finances, goals or risk tolerance, and nothing on the site is a recommendation to buy, sell or hold any security.</p>

<h2>2. Not financial advice</h2>
<p>NextShelf is not a broker-dealer, investment adviser or financial planner, and is not registered as one. Alerts, targets ("shelves"),
plans (including options swing plans), track records and every other part of the service are general information for educational purposes only. You alone decide
whether to place any trade, and you are fully responsible for those decisions and their results. Please read our
<a href="/disclaimer">risk disclaimer</a>.</p>

<h2>3. Your account</h2>
<ul>
<li>You must be at least 18 years old and able to form a binding contract.</li>
<li>Give accurate information and keep your password safe. You're responsible for activity on your account.</li>
<li>One person per account. Don't share, resell or redistribute alerts or data from NextShelf without written permission.</li>
</ul>

<h2>4. Plans and payments</h2>
<p>NextShelf offers a free plan and a paid Pro plan. Prices and features are shown in the pricing section on the homepage and may change; we'll tell you
before a price change affects you. Paid plans renew automatically until you cancel. You can cancel anytime, and you'll keep Pro until
the end of the period you've paid for. Except where the law requires otherwise, payments are non-refundable.</p>

<h2>5. Acceptable use</h2>
<p>Don't misuse the service. That includes trying to break into accounts or systems, scraping or overloading the site, reverse
engineering it, using it for anything illegal (including market manipulation), or harassing other members.</p>

<h2>6. Data and accuracy</h2>
<p>Market data comes from third-party providers and may be delayed, incomplete or wrong. Alerts can arrive late or not at all because of
outages, provider problems, or delivery issues with email, Telegram or phone carriers. Pattern detection follows fixed rules and will
sometimes flag setups that fail. Past results, including our track record and backtests, do not guarantee future results.</p>

<h2>7. Our content</h2>
<p>The NextShelf name, logo, software, text and design belong to NextShelf. You may use the service for your own personal trading
education, but not copy or resell it.</p>

<h2>8. Ending your account</h2>
<p>You can stop using NextShelf and ask us to delete your account at any time. We may suspend or close accounts that break these terms.</p>

<h2>9. No warranty</h2>
<p>NextShelf is provided "as is" and "as available", without warranties of any kind, express or implied, including fitness for a
particular purpose, accuracy or uninterrupted availability.</p>

<h2>10. Limitation of liability</h2>
<p>To the fullest extent allowed by law, NextShelf and its owners will not be liable for any trading losses, lost profits, or
indirect, incidental or consequential damages arising from your use of the service. Our total liability for any claim is limited to
the amount you paid us in the 12 months before the claim.</p>

<h2>11. Changes</h2>
<p>We may update these terms. If the changes are significant, we'll let you know by email or in the app. Continuing to use NextShelf
after changes take effect means you accept them.</p>

<h2>12. Governing law</h2>
<p>These terms are governed by the laws of the Commonwealth of Massachusetts, USA, without regard to conflict-of-law rules.</p>

<h2>13. Contact</h2>
<p>Questions about these terms? {_contact_line()}.</p>
"""


def privacy() -> str:
    return f"""
<p class="lead">This policy explains what NextShelf collects, why, and the choices you have. The short version: we collect what we need
to run your account and send your alerts, we don't sell your data, and you can ask us to delete it.</p>

<h2>What we collect</h2>
<ul>
<li><b>Account details:</b> your email address, name, and a securely hashed password (we never see or store your actual password).
If you sign in with Google, we receive your Google account ID, name and email.</li>
<li><b>Your watchlist and settings:</b> the tickers you follow, the setups you trade, your plan, and alert preferences.</li>
<li><b>Alert connections you choose:</b> your Telegram chat ID if you connect Telegram, and your phone number if you turn on phone-call alerts.</li>
<li><b>Email list:</b> your email address if you join our early-access list.</li>
<li><b>Messages you send us</b> through the contact form.</li>
<li><b>Technical data:</b> a sign-in cookie that keeps you logged in, and IP addresses kept briefly to block abuse (such as repeated failed logins).
Small preferences like a collapsed sidebar are saved in your own browser.</li>
<li><b>Payments:</b> when paid plans launch, payments are handled by our payment processor. We don't store your full card number.</li>
</ul>

<h2>How we use it</h2>
<ul>
<li>To run your account, scan your watchlist, and send the alerts you asked for.</li>
<li>To send service emails (like password resets) and, if you joined the list, product news. Every marketing email has an unsubscribe link.</li>
<li>To keep NextShelf secure and to fix problems.</li>
</ul>
<p>We don't sell or rent your personal information, and we don't use third-party advertising trackers.</p>

<h2>Who we share it with</h2>
<p>Only the service providers needed to run NextShelf, and only for that purpose: website hosting, email delivery, Telegram
(to deliver your alerts), our phone-call provider (if you turn on calls), Google (if you use Google sign-in), and our payment processor.
We may also share information if the law requires it.</p>

<h2>How long we keep it</h2>
<p>We keep your information while your account is open. If you delete your account or ask us to, we delete your personal information
within 30 days, except where we must keep records by law.</p>

<h2>Your choices and rights</h2>
<ul>
<li>See, correct or delete your information, or get a copy of it, by contacting us.</li>
<li>Unsubscribe from emails using the link in any email.</li>
<li>Disconnect Telegram or remove your phone number in Settings at any time.</li>
</ul>
<p>Depending on where you live (for example California or the EU/UK), you may have additional rights under local law. Contact us and we'll help.</p>

<h2>Security</h2>
<p>Passwords are hashed, connections are encrypted (HTTPS), and access to data is limited. No system is perfectly secure, so please use a strong, unique password.</p>

<h2>Children</h2>
<p>NextShelf is for adults. We don't knowingly collect information from anyone under 18.</p>

<h2>Changes</h2>
<p>If we change this policy in a meaningful way, we'll tell you by email or in the app before the change takes effect.</p>

<h2>Contact</h2>
<p>For privacy questions or requests, {_contact_line()}.</p>
"""


def disclaimer() -> str:
    return f"""
<p class="lead">Trading stocks involves substantial risk, including the possible loss of all the money you invest. Please read this before using NextShelf's signals.</p>

<h2>Educational information only</h2>
<p>Everything on NextShelf, including alerts, setups, targets, game plans, options swing plans, the Sika assistant, lessons and track records, is general
educational information. It is not personalized investment advice and is not an offer or recommendation to buy or sell any security.
NextShelf is not a registered investment adviser or broker-dealer.</p>

<h2>Past results don't predict future results</h2>
<p>Our track record and backtests show how setups behaved in the past under fixed rules. They assume entry at the 4-hour close and exit
at the target, on a close back inside the pattern, or at a time limit. They don't include commissions, slippage, taxes, gaps, or the
fact that real fills can differ from closing prices. Real trading results will differ, sometimes a lot.</p>

<h2>Signals can be wrong or late</h2>
<p>Breakouts fail often. Market data can be delayed or inaccurate, and alerts can arrive late or not at all. Never rely on a single alert
or tool to make a decision.</p>

<h2>Options carry extra risk</h2>
<p>NextShelf's options swing plans are educational examples of how a setup could be expressed with calls or puts. They are not
recommendations to buy any contract. Options are complex and can lose 100% of their value quickly, especially close to expiry.
Estimated option values use a standard pricing model (Black-Scholes) and assume implied volatility stays the same; real prices,
bid/ask spreads and fills will differ. Option quotes may be delayed or indicative rather than live exchange prices. Before trading
options, read the <a href="https://www.theocc.com/company-information/documents-and-archives/options-disclosure-document" target="_blank" rel="noopener">Characteristics and Risks of Standardized Options</a>
and make sure your broker has approved you for options trading.</p>

<h2>Your responsibility</h2>
<ul>
<li>Only trade with money you can afford to lose.</li>
<li>Decide your own position size and exits, and use risk controls.</li>
<li>Do your own research, and consider talking to a licensed financial professional.</li>
</ul>

<h2>No guarantees</h2>
<p>NextShelf makes no promise of profit or any particular result. By using NextShelf, you accept full responsibility for your trading decisions.</p>
<p>Questions? {_contact_line()}.</p>
"""


def contact() -> str:
    e, a = _email(), _address()
    side = []
    if e:
        side.append(f'<div class="cbox"><small>Email</small><a href="mailto:{html.escape(e)}">{html.escape(e)}</a></div>')
    if a:
        side.append(f'<div class="cbox"><small>Mail</small><span>NextShelf<br>{html.escape(a)}</span></div>')
    side.append('<div class="cbox"><small>Response time</small><span>Usually within 1–2 business days.</span></div>')
    return f"""
<p class="lead">Questions, feedback, a bug, or a privacy request? Send us a message and a real person will read it.</p>
<div class="contact-grid">
<form class="cform" id="cform" onsubmit="return sendContact(event)">
  <label>Your name<input name="name" autocomplete="name" maxlength="120"></label>
  <label>Email<input name="email" type="email" required autocomplete="email" maxlength="200"></label>
  <label>Topic<select name="topic"><option>General question</option><option>Account or billing</option><option>Bug report</option>
    <option>Feature idea</option><option>Privacy request</option></select></label>
  <label>Message<textarea name="message" required rows="6" maxlength="5000"></textarea></label>
  <input class="hp" name="website" tabindex="-1" autocomplete="off" aria-hidden="true">
  <button type="submit" id="csend">Send message</button>
  <p class="cmsg" id="cmsg" role="status"></p>
</form>
<div class="cside">{''.join(side)}</div>
</div>
<script>
async function sendContact(e) {{
  e.preventDefault();
  const f = e.target, b = document.getElementById("csend"), m = document.getElementById("cmsg");
  b.disabled = true; b.textContent = "Sending…"; m.className = "cmsg"; m.textContent = "";
  try {{
    const r = await fetch("/api/contact", {{method: "POST", headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify(Object.fromEntries(new FormData(f)))}});
    const j = await r.json().catch(() => ({{}}));
    if (!r.ok) throw new Error(j.detail || "Something went wrong. Please try again.");
    f.reset(); m.className = "cmsg ok"; m.textContent = "Thanks! Your message is on its way. We'll reply by email.";
  }} catch (err) {{ m.className = "cmsg err"; m.textContent = err.message; }}
  b.disabled = false; b.textContent = "Send message";
}}
</script>
"""


PAGES = {
    "terms": ("Terms of Service", terms, True),
    "privacy": ("Privacy Policy", privacy, True),
    "disclaimer": ("Risk Disclaimer", disclaimer, True),
    "contact": ("Contact us", contact, False),
}


def render(slug: str) -> str:
    title, body, dated = PAGES[slug]
    shell = (STATIC / "page.html").read_text()
    return (shell.replace("{{TITLE}}", title)
                 .replace("{{UPDATED}}", f'<p class="updated">Last updated {UPDATED}</p>' if dated else "")
                 .replace("{{BODY}}", body())
                 .replace("{{SLUG}}", slug))
