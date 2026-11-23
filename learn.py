"""NextShelf Learn: free, public lessons on the NextShelf method, options basics and trading around a day job.

Pages: /learn (course index) and /learn/<slug> (one lesson). Rendered into static/page.html like the legal pages.
Educational content only. Keep claims general and true; no promises about returns.
"""
from __future__ import annotations

import html
from pathlib import Path

STATIC = Path(__file__).parent / "static"

# (module title, module blurb, [(slug, title, minutes, body_html, takeaways, check)])
# check = (question, [options], index of the right answer, why)
MODULES = [
    ("Foundations", "What NextShelf looks for, and why.", [
        ("what-is-a-hub", "What a hub is, and why price pauses", 3, """
<p>Most of the time a stock isn't trending. It's <b>pausing</b>: buyers and sellers roughly agree on a price, and the stock drifts sideways in a tight range. NextShelf calls that range a <b>hub</b>.</p>
<p>A hub is defined by closes, not by every tick. When the 4-hour candles keep <b>closing inside the same narrow band for at least 4 four-hour candles</b>, that band is the hub. The top of the band is the <b>hub top</b>; the bottom is the <b>hub bottom</b>.</p>
<h2>Why hubs matter</h2>
<p>A pause stores up decisions. Traders who wanted to buy are waiting for proof; traders who wanted to sell are waiting too. When price finally <b>closes outside</b> the range, a lot of those decisions get made at once, which is why moves out of a tight range can be quick.</p>
<p>That's not a guarantee. Plenty of hubs break, wobble and slide straight back in. That's why NextShelf pairs every breakout with a plan for being wrong.</p>
<h2>What to do while a stock is inside its hub</h2>
<p><b>Nothing.</b> Inside the hub, price is noise: small moves up and down that go nowhere. Trading inside it is how most people lose money while "waiting for the move." Let the stock show its hand first.</p>""",
         ["A hub is a tight range where 4-hour candles keep closing for 4+ four-hour candles.",
          "Moves out of a hub can be fast because many traders were waiting.",
          "Inside the hub there's no trade. Wait for a close outside it."],
         ("A stock has closed inside $50–$51 for six sessions. What's the trade?",
          ["Buy near $50, sell near $51", "No trade until a 4-hour close outside the range", "Short it, because it can't go higher"],
          1, "Inside the hub, price is noise. The setup only starts on a 4-hour close outside it.")),
        ("why-4-hour-candles", "Why NextShelf uses 4-hour candles", 3, """
<p>A candle is just a summary of price over a stretch of time: where it opened, where it closed, and its high and low. The stretch you pick changes what you see.</p>
<ul><li><b>1-minute and 5-minute candles</b> are mostly noise. They demand constant attention and punish anyone with a job.</li>
<li><b>Daily candles</b> are calm, but setups arrive slowly and stops have to be wide.</li>
<li><b>4-hour candles</b> sit in between: clear enough to ignore the noise, fast enough to catch a move within days.</li></ul>
<h2>Only two decision points a day</h2>
<p>US stocks trade from 9:30 AM to 4:00 PM Eastern. NextShelf splits that into two 4-hour candles: one that closes at <b>1:30 PM</b> and one that closes at <b>4:00 PM</b>. Those two closes are the only moments a NextShelf setup can confirm.</p>
<p>That's the whole point for someone at work: you don't need to watch the screen all day. NextShelf warns you about 15 minutes before each close if a stock is outside its hub, and confirms the result after the close.</p>""",
         ["4-hour candles filter out most intraday noise.",
          "There are two closes a day: 1:30 PM and 4:00 PM Eastern.",
          "Setups only confirm at those closes, so you don't need to watch all day."],
         ("When can a NextShelf breakout be confirmed?",
          ["Any minute price trades above the hub", "Only at a 4-hour close (1:30 PM or 4:00 PM ET)", "Only at the market open"],
          1, "The setup is a 4-hour close outside the hub, and those closes happen at 1:30 PM and 4:00 PM.")),
        ("close-not-wick", "Close, not wick: what counts as a breakout", 3, """
<p>During a candle, price can poke above the hub top and fall back before the candle ends. That poke shows up as a thin line on the chart, called a <b>wick</b>. A wick is an attempt that failed.</p>
<p>NextShelf only counts a breakout when the candle <b>closes</b> outside the hub. The close is where the buyers and sellers left things after four hours of arguing. It's a much stronger statement than a brief spike.</p>
<h2>Why this rule saves money</h2>
<p>Many "breakouts" are wicks that reverse within minutes. Traders who buy the spike get caught as price snaps back inside. Waiting for the close means you miss the very first part of some moves, but you skip a lot of the false ones.</p>
<h2>The 15-minute warning</h2>
<p>Because the close is what matters, NextShelf checks about 15 minutes before each close (1:15 PM and 3:45 PM). If price is outside the hub at that moment, you get a heads-up: if the candle closes there, that's the breakout. Some traders act in those last minutes; others wait for the confirmed close. Both are valid choices, as long as you know which one you're making.</p>""",
         ["A wick is a failed attempt; the close is the decision.",
          "NextShelf only counts a 4-hour close outside the hub.",
          "The 15-minute warning tells you a close outside the hub is likely."],
         ("Price jumps 1% above the hub top at 11 AM, then closes back inside at 1:30 PM. Is that a breakout?",
          ["Yes, it went above the hub", "No, the candle closed back inside: it was a wick", "Only if volume was high"],
          1, "Only the 4-hour close counts. A spike that closes back inside is a wick.")),
        ("extended-hours", "Pre-market and after hours: what counts", 3, """
<p>The stock market's main session runs from <b>9:30 AM to 4:00 PM Eastern</b>. Around it there are two quieter sessions: <b>pre-market</b> from 4:00 to 9:30 AM, and <b>after hours</b> from 4:00 to 8:00 PM. Prices move in both, often on news or earnings.</p>
<h2>What NextShelf counts</h2>
<p>Only the main session. Hubs, breakouts, retests, grades and alerts are all built from the two regular 4-hour closes (1:30 PM and 4:00 PM). A stock that jumps above its hub after hours hasn't broken out: the next regular close decides it.</p>
<h2>Why: thin trading</h2>
<p>Far fewer shares change hands outside the main session. A few orders can push the price a long way, and spreads are wide. A price that looks dramatic at 6 PM can be gone by 9:31 AM.</p>
<h2>Seeing it on the chart</h2>
<p>On the 1H, 2H and 3H views, the <b>Extended</b> button adds those sessions to the chart: pre-market is shaded amber, after hours blue. The legend shows the latest extended price against the 4:00 PM close. It's there for context, so you can see an earnings reaction or a gap forming, not to trade on.</p>""",
         ["Main session: 9:30 AM to 4:00 PM ET. Pre-market 4:00 to 9:30 AM, after hours 4:00 to 8:00 PM.",
          "Setups, grades and alerts use regular-hours closes only.",
          "Extended prices are thin and jumpy: context, not confirmation."],
         ("A stock's hub top is $50. After hours it trades at $51.20 on news. What happened to the setup?",
          ["It broke out, because it's above $50", "Nothing yet; the next regular 4-hour close decides it", "It failed, because after-hours moves always reverse"],
          1, "After-hours prices don't confirm anything. A regular 4-hour close above $50 would.")),
        ("shelves", "Shelves: where price may go next", 3, """
<p>Old hubs don't disappear. When price spent days pausing at a level in the past, that level often matters again later. NextShelf calls those earlier ranges <b>shelves</b>.</p>
<p>When a stock breaks out of its current hub, NextShelf looks for the <b>nearest shelf in the direction of the break</b>. That shelf becomes the aim: the place where price has paused before, and where it may pause again.</p>
<h2>When there's no shelf nearby</h2>
<p>Sometimes there isn't an earlier range in reach. Then NextShelf uses a target based on how much the stock normally moves (its average true range, or ATR), so the aim is still realistic for that stock.</p>
<h2>An aim, not a promise</h2>
<p>A shelf is a reasonable place to take profits, not a prediction. Some breakouts stop short; some blow through. The Shelf card shows how often past breakouts on that stock actually reached the shelf, so you can judge for yourself.</p>""",
         ["Shelves are earlier hubs that can act as stopping points.",
          "The nearest shelf in the breakout's direction is the aim.",
          "No shelf in reach? The aim is based on the stock's normal move (ATR)."],
         ("A stock breaks out above its hub at $100. Earlier shelves sit at $104 and $112. What's the aim?",
          ["$112, the bigger move", "$104, the nearest shelf in the direction of the break", "There is no aim"],
          1, "NextShelf aims for the nearest shelf in the direction of the breakout.")),
    ]),
    ("Trading the setup", "Turning a setup into a plan you can stick to.", [
        ("trade-your-first-setup", "Your first setup, start to finish", 5, """
<p>A setup starts with a stock that has been coiling inside a <b>hub</b> — a price zone it keeps bouncing around in — and then <b>closes outside it</b> on a 4-hour candle. That close is the <b>heads-up</b>, not the entry. The entry comes when price <b>pulls back to the hub edge and holds it</b>: the retest. This lesson walks through the whole thing once, start to finish.</p>
<div style="margin:18px 0;padding:14px 6px 6px;border:1px solid #e3e7ec;border-radius:16px;background:#f7f8f9;"><svg viewBox="0 0 640 300" role="img" aria-label="Diagram: candles coiling inside a hub, then one candle closing above the hub top" style="display:block;width:100%;height:auto;"><line x1="40" y1="52" x2="600" y2="52" stroke="#8a9aae" stroke-width="1.5" stroke-dasharray="6 5"/><text x="40" y="42" font-family="JetBrains Mono, monospace" font-size="12" fill="#8a9aae">next shelf — the aim</text><rect x="40" y="150" width="560" height="64" fill="rgba(47,84,235,0.08)"/><line x1="40" y1="150" x2="600" y2="150" stroke="#2F54EB" stroke-width="1.5" stroke-dasharray="6 5"/><line x1="40" y1="214" x2="600" y2="214" stroke="#2F54EB" stroke-width="1.5" stroke-dasharray="6 5"/><text x="40" y="142" font-family="JetBrains Mono, monospace" font-size="12" fill="#2F54EB">hub top</text><text x="40" y="232" font-family="JetBrains Mono, monospace" font-size="12" fill="#2F54EB">hub bottom</text><g stroke="#141412" stroke-width="2"><line x1="107" y1="162" x2="107" y2="202"/><rect x="100" y="170" width="14" height="24" fill="#141412" stroke="none"/><line x1="187" y1="170" x2="187" y2="206"/><rect x="180" y="178" width="14" height="20" fill="#141412" stroke="none"/><line x1="267" y1="158" x2="267" y2="200"/><rect x="260" y="166" width="14" height="26" fill="#141412" stroke="none"/><line x1="347" y1="172" x2="347" y2="206"/><rect x="340" y="180" width="14" height="18" fill="#141412" stroke="none"/><line x1="427" y1="164" x2="427" y2="202"/><rect x="420" y="172" width="14" height="22" fill="#141412" stroke="none"/></g><line x1="508" y1="88" x2="508" y2="96" stroke="#3DD68C" stroke-width="2"/><rect x="500" y="96" width="16" height="54" fill="#3DD68C"/><text x="600" y="76" text-anchor="end" font-family="JetBrains Mono, monospace" font-size="12" fill="#141412">breakout close — the heads-up</text><text x="40" y="282" font-family="JetBrains Mono, monospace" font-size="12" fill="#8a9aae">entry: the retest of the hub top (next lesson)</text></svg><p style="font-size:13px;color:#5b6672;margin:6px 12px 8px;"><b style="color:#141412;">The setup, drawn.</b> Candles coil inside the hub for days. One 4-hour candle closes above the hub top — that's the heads-up. The entry is the pullback to the hub top that holds; the next shelf above is the aim.</p></div>
<h2>1. Pick your watchlist</h2>
<p>NextShelf only watches the tickers you put on your watchlist, and only those can alert you. The free tier covers 3 tickers. Pick names you actually follow — the alert is only useful if you know the stock.</p>
<h2>2. The breakout alert: a heads-up</h2>
<p>There are two 4-hour closes a day: <b>1:30 PM</b> and <b>4:00 PM</b> Eastern. When a 4-hour candle closes outside the hub, you get a <b>breakout alert</b> by Telegram (and a text for Pro). It says "wait for the retest": don't chase that close. Breakouts on light volume don't count at all, and each one is labelled <b>Strong</b> or <b>Weak</b> so you know how convincing it was.</p>
<h2>3. The retest alert: the entry</h2>
<p>Over the next 5 four-hour candles, NextShelf watches for price to <b>pull back to the hub edge and close back above it</b> (below it, for a breakdown). When that happens you get one <b>retest alert</b>, graded:</p>
<ul><li><b>A setup — entry on this close.</b> It comes with the plan: entry at the retest close, a stop 1 ATR past it, and the next shelf as the aim. Pro members get a phone call and an options plan.</li>
<li><b>Watch only.</b> The retest happened, but it missed a bar (not enough room to the aim, or a hub that was too short). The alert says which. No entry.</li></ul>
<p>The next lesson explains the grade and why it exists.</p>
<h2>4. Place it in your own broker</h2>
<p>NextShelf never trades for you and never holds your money. The card has links to open the ticker in Webull or Robinhood — you place the order there, at the size you worked out from your risk. The position-size lesson shows how to work that out from the distance to your exit.</p>
<h2>5. Manage it with the three exits</h2>
<p>After you're in, there are only three ways out: price reaches the shelf, a 4-hour candle closes past your stop (1 ATR from the entry on an A setup), or 10 four-hour candles pass — about a week — without either happening. Nothing else. No watching the screen all day.</p>
<h2>6. Check the record</h2>
<p>Every setup NextShelf calls is logged on the Results page, wins and losses, nothing removed. Before you trust a setup with real money, look at how it has actually performed.</p>
<h2>Three rules for your first few</h2>
<p><b>Enter on the retest, not the breakout.</b> The breakout close is where most of the losing trades started: price ran up to get there and gave it all back. The retest gets you in near the edge, so being wrong costs less. Breakouts that never come back simply aren't traded.</p>
<p><b>Only A setups are entries.</b> A "watch only" retest is information, not a trade. If the retest window (5 candles) passes, the setup is over.</p>
<div style="margin:18px 0;padding:14px 6px 6px;border:1px solid #e3e7ec;border-radius:16px;background:#f7f8f9;"><svg viewBox="0 0 640 300" role="img" aria-label="Diagram: the first-half rule. Between the hub edge and halfway is the entry zone; past halfway is chasing." style="display:block;width:100%;height:auto;"><rect x="180" y="30" width="240" height="120" fill="rgba(220,0,88,0.07)"/><rect x="180" y="150" width="240" height="120" fill="rgba(61,214,140,0.10)"/><line x1="300" y1="30" x2="300" y2="270" stroke="#141412" stroke-width="2"/><line x1="180" y1="30" x2="420" y2="30" stroke="#8a9aae" stroke-width="1.5" stroke-dasharray="6 5"/><line x1="180" y1="150" x2="420" y2="150" stroke="#8a9aae" stroke-width="1.5" stroke-dasharray="6 5"/><line x1="180" y1="270" x2="420" y2="270" stroke="#8a9aae" stroke-width="1.5" stroke-dasharray="6 5"/><circle cx="300" cy="210" r="7" fill="#3DD68C"/><circle cx="300" cy="90" r="7" fill="#DC0058"/><text x="440" y="34" font-family="JetBrains Mono, monospace" font-size="12" fill="#141412">aim — next shelf</text><text x="440" y="154" font-family="JetBrains Mono, monospace" font-size="12" fill="#141412">halfway</text><text x="440" y="274" font-family="JetBrains Mono, monospace" font-size="12" fill="#141412">hub edge</text><text x="170" y="214" text-anchor="end" font-family="JetBrains Mono, monospace" font-size="12" fill="#141412">enter here ✓</text><text x="170" y="94" text-anchor="end" font-family="JetBrains Mono, monospace" font-size="12" fill="#141412">here = chasing ✗</text></svg><p style="font-size:13px;color:#5b6672;margin:6px 12px 8px;"><b style="color:#141412;">The first-half rule, drawn.</b> Between the hub edge and halfway to the aim, the reward left is bigger than the risk back. Past halfway, it's the other way round — skip it.</p></div>
<p><b>Never hold through earnings.</b> The alert warns you when a print is near. The setup is a swing into the move, not a bet on the report.</p>
<h2>Worked example: one real watchlist</h2>
<p>Here's what this looked like on one real day — the Nov 9 close, on a watchlist of 11 tickers. Levels change every close, so check the board for today's numbers. The shape is what matters.</p>
<p><b>Heads-up — HONA.</b> Broke out on the 1:30 PM close at $164.18, out of a $149.59–$161.72 warehouse hub. That close isn't the entry. The plan is to wait for a 4-hour candle that pulls back near the hub top ($161.72) and closes above it. If that retest grades A, the entry is that close, the stop 1 ATR below it, and the aim the next shelf at $207.41. A gamma wall sits at $165, right above the hub top — the first test after any entry.</p>
<div style="margin:18px 0;padding:14px 6px 6px;border:1px solid #e3e7ec;border-radius:16px;background:#f7f8f9;"><svg viewBox="0 0 640 320" role="img" aria-label="Diagram: HONA at the Nov 9 close. Hub 149.59 to 161.72, entry 164.18, gamma wall 165, halfway 184.57, target shelf 207.41." style="display:block;width:100%;height:auto;"><rect x="180" y="214" width="240" height="46" fill="rgba(47,84,235,0.08)"/><line x1="180" y1="214" x2="420" y2="214" stroke="#2F54EB" stroke-width="1.5" stroke-dasharray="6 5"/><line x1="180" y1="260" x2="420" y2="260" stroke="#2F54EB" stroke-width="1.5" stroke-dasharray="6 5"/><line x1="300" y1="30" x2="300" y2="280" stroke="#141412" stroke-width="2"/><line x1="180" y1="40" x2="420" y2="40" stroke="#8a9aae" stroke-width="1.5" stroke-dasharray="6 5"/><line x1="180" y1="127" x2="420" y2="127" stroke="#8a9aae" stroke-width="1.5" stroke-dasharray="6 5"/><line x1="180" y1="201" x2="420" y2="201" stroke="#DC0058" stroke-width="1.5" stroke-dasharray="6 5"/><circle cx="300" cy="205" r="7" fill="#3DD68C"/><line x1="178" y1="195" x2="292" y2="202" stroke="#141412" stroke-width="1" opacity="0.45"/><text x="440" y="44" font-family="JetBrains Mono, monospace" font-size="12" fill="#141412">next shelf $207.41</text><text x="440" y="131" font-family="JetBrains Mono, monospace" font-size="12" fill="#141412">halfway $184.57</text><text x="440" y="205" font-family="JetBrains Mono, monospace" font-size="12" fill="#DC0058">gamma wall $165</text><text x="170" y="192" text-anchor="end" font-family="JetBrains Mono, monospace" font-size="12" fill="#141412">entry $164.18</text><text x="170" y="222" text-anchor="end" font-family="JetBrains Mono, monospace" font-size="12" fill="#2F54EB">hub top $161.72</text><text x="170" y="264" text-anchor="end" font-family="JetBrains Mono, monospace" font-size="12" fill="#2F54EB">hub bottom $149.59</text></svg><p style="font-size:13px;color:#5b6672;margin:6px 12px 8px;"><b style="color:#141412;">HONA, Nov 9 close.</b> Warehouse hub, 6 sessions. Breakout close $164.18 · wall $165 · target $207.41. The entry waits for a retest of the hub top at $161.72.</p></div>
<p><b>Watch — ABBV, GOOG, GOOGL.</b> All three are pressing the top of their hubs: ABBV at $294.17 under a $294.89 top, GOOG at $287.61 under $289.92, GOOGL at $289.61 under $291.67. Nothing to do until a 4-hour candle closes above the hub top, and then the entry is still the retest.</p>
<p><b>Wait — the rest.</b> AAPL, AMZN, ARM, NVDA, QQQ, SPCX: no live setup. IBM's and ARM's breakdowns are days old — moves that already happened, not entries. NVDA reports Nov 17, so nothing new goes on there into earnings.</p>
<p>Start by watching a few alerts play out, and paper-trade them in your broker's practice account before any real money. If a setup's plan doesn't make sense to you, skip it — there will be another one at the next close.</p>""",
         ["The breakout close is a heads-up; the entry is the retest of the hub edge.",
          "Retest alerts are graded: an A setup comes with the entry plan, a watch doesn't.",
          "Three exits only: the shelf, the stop, or about a week (10 candles)."],
         ("NextShelf alerts you to a breakout. Where does the trade actually happen?",
          ["NextShelf places it for you", "In your own broker, using the plan on the card", "By replying to the Telegram alert"],
          1, "NextShelf never trades for you or holds your money. You place the order in your own broker.")),
        ("the-retest", "Wait for the retest: the A setup", 4, """
<p>NextShelf replayed <b>every hub breakout across 543 stocks over two years</b>, more than 22,000 of them. Entering at the breakout close lost money on average: wins averaged about +1%, but losses averaged about −2%, because a failed breakout gives back the whole run-up you paid for. No exit rule fixed that. Changing <i>where you get in</i> did.</p>
<div style="margin:18px 0;padding:14px 6px 6px;border:1px solid #e3e7ec;border-radius:16px;background:#f7f8f9;"><svg viewBox="0 0 640 300" role="img" aria-label="Diagram: a breakout close above the hub top, a pullback to the hub top that closes above it (the retest entry), and a stop 1 ATR below" style="display:block;width:100%;height:auto;"><line x1="40" y1="44" x2="600" y2="44" stroke="#8a9aae" stroke-width="1.5" stroke-dasharray="6 5"/><text x="40" y="34" font-family="JetBrains Mono, monospace" font-size="12" fill="#8a9aae">next shelf — the aim</text><rect x="40" y="160" width="560" height="60" fill="rgba(47,84,235,0.08)"/><line x1="40" y1="160" x2="600" y2="160" stroke="#2F54EB" stroke-width="1.5" stroke-dasharray="6 5"/><line x1="40" y1="220" x2="600" y2="220" stroke="#2F54EB" stroke-width="1.5" stroke-dasharray="6 5"/><text x="40" y="240" font-family="JetBrains Mono, monospace" font-size="12" fill="#2F54EB">hub</text><g fill="#141412"><rect x="100" y="176" width="14" height="26"/><rect x="160" y="182" width="14" height="22"/><rect x="220" y="172" width="14" height="28"/></g><rect x="290" y="112" width="16" height="56" fill="#3DD68C"/><text x="298" y="102" text-anchor="middle" font-family="JetBrains Mono, monospace" font-size="12" fill="#141412">breakout: heads-up</text><rect x="360" y="122" width="14" height="22" fill="#DC0058"/><line x1="447" y1="132" x2="447" y2="170" stroke="#3DD68C" stroke-width="2"/><rect x="440" y="140" width="14" height="16" fill="#3DD68C"/><circle cx="447" cy="148" r="20" fill="none" stroke="#141412" stroke-width="1.5"/><text x="600" y="96" text-anchor="end" font-family="JetBrains Mono, monospace" font-size="12" fill="#141412">retest: dips to the top,</text><text x="600" y="112" text-anchor="end" font-family="JetBrains Mono, monospace" font-size="12" fill="#141412">closes above it = entry</text><line x1="420" y1="196" x2="600" y2="196" stroke="#DC0058" stroke-width="1.5" stroke-dasharray="4 4"/><text x="600" y="212" text-anchor="end" font-family="JetBrains Mono, monospace" font-size="12" fill="#DC0058">stop: 1 ATR below the entry</text></svg><p style="font-size:13px;color:#5b6672;margin:6px 12px 8px;"><b style="color:#141412;">The retest, drawn.</b> The breakout close is the heads-up. A later candle dips back to the hub top and closes above it: that close is the entry, with a stop 1 ATR below.</p></div>
<h2>What counts as a retest</h2>
<p>Within <b>5 four-hour candles</b> of the breakout, a candle that <b>dips back to within a quarter of an ATR of the hub edge</b> and <b>closes on the breakout side, within half an ATR of the edge</b>. A close back inside the hub first ends it: the breakout failed. Only the first retest counts.</p>
<h2>The A setup</h2>
<p>The retest alone helped, but it still lost money on average. A small set of retests made money, and held up in both halves of the two years and without the five best stocks. Those are A setups:</p>
<ul><li><b>Room 1.5× or more:</b> the distance to the next shelf is at least 1.5 times the distance back to the middle of the hub.</li>
<li><b>A real hub:</b> it held for <b>4 or more sessions</b>.</li>
<li><b>Stop 1 ATR from the entry</b>, on a 4-hour close.</li></ul>

<p>Measured on longs (A+ setups — A retests with a clear path): 233 trades, a little over half winners, <b>+0.5% per trade</b> on average. The same rule on the short side lost money in the test, so short retests arrive as watch only. A retest that misses a bar arrives as <b>watch only</b>, with the reason. The next two lessons explain the split.</p>
<h2>Double bottoms and tops</h2>
<p>Double tops and bottoms get the same treatment: the breakout through the neckline is a heads-up, and the entry is the retest of the neckline. Their A setup is a retest that closes at <b>4:00 PM</b> and isn't past halfway to the aim, with the stop past the far side of the pattern.</p>
<h2>What this costs</h2>
<p>Fewer trades. Breakouts that run away without coming back are never entered, and the win rate is lower than chasing every breakout. In exchange, the losses are smaller and the average trade was positive. These are past results, not a promise.</p>""",
         ["The breakout close lost money on average; the retest entry cut the losses.",
          "A setup = room 1.5×+, a 4+ session hub, stop 1 ATR from the entry.",
          "Missed retests aren't traded, and a watch-only retest isn't an entry."],
         ("A hub broke out at $105 over a $100–$104 hub. Two candles later one dips to $104.10 and closes at $104.60. What is that?",
          ["A failed breakout", "The retest: a pullback to the edge that held", "Nothing; only the breakout matters"],
          1, "It pulled back to the hub top and closed above it, close to the edge. That's the retest, and if it grades A it's the entry.")),
        ("clear-path", "Clear path: the road to the target, and the A+ grade", 5, """
<p>The previous lesson graded the retest. This one grades the <b>road</b>: the stretch of price between the hub edge, where you enter, and the next shelf, which is the aim. Sometimes that road is empty. Sometimes it has furniture in it.</p>
<h2>What a stack is</h2>
<p>Old price levels don't disappear. An old high, an older hub's edge, a cluster of moving averages — each is a place where traders acted before and may act again. One level on its own is a speed bump. A <b>stack</b> is two or more <i>different kinds</i> of level clustered close together: several groups of traders all watching the same spot. Stacks act like walls, because that's where the resting orders are.</p>
<p>A <b>clear path</b> means no stack sits between the entry and the target. If you know the shelf language: a clear path is an <b>air pocket</b>, a stretch with very little supply in it. Price tends to travel through air pockets quickly, in either direction. A stack is the opposite — a crowded shelf where price fights for every inch.</p>
<div style="margin:18px 0;padding:12px 16px;border:1px solid #e3e7ec;border-radius:16px;background:#f7f8f9;"><p style="margin:0;"><b>In plain words:</b> you're driving from the entry to the target. A clear path is an open highway. A stack in the way is a row of parked trucks — you might still get through, but expect the trip to stall, slow down, or turn around.</p></div>
<h2>Why the top grade is A+</h2>
<p>A grade is not an opinion about one stock. It's the measured history of hundreds of past setups that looked like this one, printed next to the setup so you can judge it yourself. When the Lab split A retests by the road, they turned out to be two populations wearing one letter:</p>
<ul><li><b>Clear path:</b> +0.53% per trade, profit factor 1.41 (233 trades).</li>
<li><b>Stack in the way:</b> +0.01% per trade, profit factor 1.00 (105 trades) — flat, and negative in the more recent half.</li>
<li><b>All A retests together:</b> +0.37% per trade, profit factor 1.27 (338 trades). The blocked trades were pulling the average down.</li></ul>
<p>So the grade now tells the truth about which population you're in. The top letter is <b>A+</b>: an A retest whose road to the target was clear <i>at the retest</i>. An A retest with a stack in the way steps down and carries <b>its own measured record</b> — never the A+ record, and never the all-breakouts record. On the board, a live setup with an open road wears a <b>Clear path</b> chip next to its grade. A blocked setup wears no chip; its record speaks for it.</p>
<div style="margin:18px 0;padding:12px 16px;border:1px solid #e3e7ec;border-radius:16px;background:#f7f8f9;"><p style="margin:0;"><b>In plain words:</b> two students were sharing one report card — the one who did the extra work kept getting averaged with the one who didn't. Now each has their own card, and the top card is labelled A+ so you can tell at a glance which one you're looking at.</p></div>
<h2>Two clocks</h2>
<p>The Clear path chip on the board is a <b>live</b> fact: the road as it stands right now. It can appear or disappear as new levels form. The <b>grade's</b> reading was taken once, at the retest itself, because that's the moment the Lab measured. Same road, two clocks — the chip tells you what the path looks like today; the grade tells you what it looked like when it counted.</p>
<div style="margin:18px 0;padding:12px 16px;border:1px solid #e3e7ec;border-radius:16px;background:#f7f8f9;"><p style="margin:0;"><b>In plain words:</b> the chip is today's weather on the road. The grade is the weather on the day the trade was measured. Both are useful — just don't mistake one for the other.</p></div>""",
         ["A stack is 2+ kinds of price level clustered together; a clear path has none between entry and target.",
          "A+ means an A retest with a clear path at the retest. Blocked retests carry their own record.",
          "The board chip is the live road; the grade's reading was taken at the retest."],
         ("A retest grades A, but a stack sits between the entry and the target. What record does it carry?",
          ["The A+ record — it still graded A", "Its own measured record, stepped down from A+", "The overall breakout record"],
          1, "A+ is reserved for A retests with a clear path. A blocked retest is a different, weaker population and carries its own numbers.")),
        ("flow-confirmed", "Flow confirmed, and the number under the number", 5, """
<p>On the day of a break, somebody was either buying aggressively or they weren't. NextShelf measures that from the options tape — and prints it as a fact, never a forecast.</p>
<h2>What "flow confirmed" measures</h2>
<p>Take the money spent buying calls at the asking price, subtract the money spent buying puts at the asking price, across the break day and the session before it. If that net number is large compared with the stock's own recent norm, the break was <b>backed</b>: real money pushed it. That is <b>flow confirmed</b>. Most breaks aren't: in the Lab's main test, about two in three classifiable breaks had no decisive flow at all.</p>
<p>Measured on A retests, the confirmed group returned <b>+0.45% per trade, profit factor 1.31, across 102 trades</b>, against −0.10% for breaks with no decisive flow — and the result held up when re-run on a second population. Two honest limits. The reading is taken at the <i>break</i>: it is hindsight about the break, delivered overnight — which is still in time for the only trade NextShelf takes, the retest, days later. And confirmed retests still won only about <b>half</b> their trades. The edge is not being right more often; it's that the winners were bigger than the losers.</p>
<div style="margin:18px 0;padding:12px 16px;border:1px solid #e3e7ec;border-radius:16px;background:#f7f8f9;"><p style="margin:0;"><b>In plain words:</b> when the stock stepped out of its box, was anyone actually pushing, or did it just drift out? Flow confirmed means we checked the tape and found real money behind the move — like checking whether a door was opened by a crowd or by the wind. It doesn't promise where price goes next. It tells you the break had muscle.</p></div>
<h2>When both badges agree</h2>
<p>An A+ retest that is also flow confirmed is the best-certified setup on this site. But don't add the two edges together — we measured that too. On clear-path trades, the flow-confirmed group returned +0.51% per trade (profit factor 1.37), about the same as clear-path trades overall. The likely reason: both badges are footprints of the same thing — real buying interest with room to run. So use the combination as a <b>ranking</b>, not a multiplier: if three A+ retests are live in the same week and you can only take one, the tape-confirmed one goes first.</p>
<div style="margin:18px 0;padding:12px 16px;border:1px solid #e3e7ec;border-radius:16px;background:#f7f8f9;"><p style="margin:0;"><b>In plain words:</b> a clear road <i>and</i> a crowd pushing the car is the best-looking setup we can show you. Just don't add the two scores together — they're grading the same strength twice. Use the second badge to choose between good setups, not to expect twice the result.</p></div>
<h2>The number under the number: n</h2>
<p>Every average on this site is really two numbers: the result, and <b>n</b> — how many trades produced it. The clear-path + flow-confirmed group above rests on 49 trades. The A+ record rests on 233. The flow verdict rests on 102. Small n wobbles: with 49 trades, two lucky winners can pull the average up by themselves. That's why the Lab enforces its rules — under 30 trades is directional only, a setup needs 100+ before it can graduate, and every record is re-checked with its five best trades removed. Read the n first, then decide how much weight the average can carry.</p>
<p><b>Profit factor</b>, quickly: total winnings divided by total losses. PF 1.31 means the group made $1.31 for every $1.00 it lost. A system can win only half its trades and still be profitable, if its winners are bigger than its losers. Win rate tells you how <i>often</i>; profit factor tells you how <i>much</i>.</p>
<div style="margin:18px 0;padding:12px 16px;border:1px solid #e3e7ec;border-radius:16px;background:#f7f8f9;"><p style="margin:0;"><b>In plain words:</b> if a restaurant has 4 reviews averaging 5 stars, you shrug. If it has 400 reviews averaging 4.6, you book a table. Same kind of average, very different evidence. n is the review count under every trading record.</p></div>
<h2>Backward proof, forward proof</h2>
<p>The Lab numbers in these lessons come from a <b>backtest</b> — history, replayed. Backtests have a known weakness: a rule chosen because it looked good in the past will usually look a little worse in the future. Expect some decay. So every graded setup is also logged <b>live, going forward</b>, from the day its rule shipped. That forward record is the proof that counts: a grade is a backtest's promise, and the live log is where it keeps it — or doesn't.</p>
<div style="margin:18px 0;padding:12px 16px;border:1px solid #e3e7ec;border-radius:16px;background:#f7f8f9;"><p style="margin:0;"><b>In plain words:</b> studying old exam papers tells you a student was prepared. The grades that matter are the ones earned on exams that haven't happened yet. We keep both report cards, and we show you both.</p></div>""",
         ["Flow confirmed = aggressive net buying on the tape at the break, measured against the stock's own norm.",
          "A+ plus flow confirmed ranks setups; measured, the edges don't add together.",
          "n is the trade count under every average. Under 30 is directional only; graduation needs 100+."],
         ("Two records both average +0.50% per trade. One rests on 320 trades, the other on 31. Which deserves more trust?",
          ["The 31 — fresher trades count more", "They're equal — the average is the same", "The 320 — a large sample can't be carried by a few lucky trades"],
          2, "n is the evidence under the average. A small sample can be dragged around by two or three lucky trades; a large one can't.")),
        ("sector-pulse", "Sector pulse: is the wind at your back?", 3, """
<p>Stocks don't move alone. Money rotates between groups: for a few weeks it pours into technology, then into energy, then into utilities. A breakout in a group that money is rotating into has a tailwind. One in a group money is leaving is fighting it.</p>
<h2>How the pulse measures it</h2>
<p>NextShelf compares each of the 11 sector funds with the S&amp;P 500. <b>5D</b> and <b>20D</b> are the sector's return over the last 5 and 20 trading days <i>minus</i> the market's. +2.00 means the sector beat the market by 2 percentage points; −2.00 means it lagged by 2.</p>
<ul><li><b>Rotating in</b>: beating the market over both 5 and 20 days.</li>
<li><b>Rotating out</b>: lagging over both.</li>
<li><b>Mixed</b>: one up, one down. The story is changing.</li>
<li>The <b>arrow</b> shows whether the 20-day number is rising or falling compared with a week ago.</li></ul>
<h2>Where it lives</h2>
<p>The full table sits above the market map on Browse setups. Today shows one line: the leading and lagging sector, and when setups bunch up, a note like "4 of today's 6 live setups are in Technology."</p>
<h2>How to use it</h2>
<p>As context only. The pulse never changes a grade, an alert or the order of the board. A strong setup in a fading sector can still work; it's just worth knowing the wind is in its face.</p>""",
         ["The pulse is each sector's return minus the S&P 500's, over 5 and 20 days.",
          "Rotating in = beating the market on both; rotating out = lagging on both.",
          "It's context, never a grade: it doesn't change alerts or rankings."],
         ("Technology shows 5D +1.40 and 20D +3.10. What does that mean?",
          ["Tech stocks rose 1.4% and 3.1%", "Tech beat the S&P 500 by 1.4 and 3.1 points: rotating in", "Every tech setup is now an A"],
          1, "The numbers are relative to the market. Positive on both means rotating in. It's context, not a grade.")),
        ("the-game-plan", "The game plan: aim, and where you're wrong", 3, """
<p>Every NextShelf setup comes with the same three parts. Knowing all three <i>before</i> you trade is what turns a setup into a plan.</p>
<ul><li><b>The entry:</b> the retest close, once a 4-hour close outside the hub has given the heads-up.</li>
<li><b>The aim:</b> the next shelf (or the ATR target).</li>
<li><b>Where you're wrong:</b> a 4-hour close 1 ATR past your entry on an A setup. Before you're in, a close back inside the hub means the breakout failed and there's nothing to enter.</li></ul>
<h2>Why "wrong if" comes first</h2>
<p>Most losses get big because there was no exit planned. If you decide in advance where the trade ends, you never have to decide under pressure.</p>
<h2>The time limit</h2>
<p>Breakouts that work tend to move within days. NextShelf gives each one 10 four-hour candles (about a week). If it hasn't reached the aim or failed by then, it's marked as timed out. Money stuck in a trade that's going nowhere can't be used for the next setup.</p>""",
         ["Entry, aim and 'wrong if' are decided before the trade.",
          "On an A setup, a 4-hour close 1 ATR past the entry ends it.",
          "Each setup gets about a week (10 four-hour candles)."],
         ("You entered an A retest at $50 with a stop 1 ATR ($1) below. A 4-hour candle closes at $48.80. What does the plan say?",
          ["Hold, it might come back", "The stop was hit: exit", "Buy more at a better price"],
          1, "A close past the stop is the 'wrong if' condition. The plan says the trade is over.")),
        ("meet-sika-and-lookout", "Meet your crew: Sika and Lookout", 4, """
<p>NextShelf has two helpers. Neither one trades for you, and neither one replaces your judgment. Think of them as two colleagues with very clear job descriptions: one explains, one watches.</p>
<h2>Sika: the explainer</h2>
<div class="cbox"><small>Job description</small><b>Sika · the robot in the corner</b><br>
<b>Role:</b> explain setups and charts in plain English, and find setups worth a look.<br>
<b>Works:</b> on every stock's panel, and in Ask Sika from Today.<br>
<b>Duties:</b><ul>
<li><b>Explain this chart</b>: what the hub, the shelf and the trigger mean for this stock right now.</li>
<li><b>What do the lines mean?</b>: every line on the chart, one sentence each.</li>
<li><b>When is it wrong?</b>: the level where the plan stops working, and why.</li>
<li><b>Find setups</b>: ask for live breakouts with room to run, and Sika lists them from the latest scan.</li>
<li>Answer your own questions about a setup, grounded in NextShelf's rules and the stock's past prices.</li></ul>
<b>Never:</b> places, changes or cancels an order; tells you what to buy; makes up numbers that aren't on the card.<br>
<b>Reports to:</b> you. Sika's reads are education, not financial advice.</div>
<p>Use Sika <b>before</b> a trade, while you're deciding. If a setup confuses you, ask before you act.</p>
<h2>Lookout: the watcher</h2>
<div class="cbox"><small>Job description</small><b>Lookout · the two green eyes</b><br>
<b>Role:</b> watch a trade you've already taken and tap you when the plan says something changed.<br>
<b>Works:</b> from the moment you say "Yes, watch it" until you tap <b>I'm out</b> (or the watch winds down on its own).<br>
<b>Duties:</b> one alert each for:<ul>
<li><b>The plan dies</b>: a 4-hour close past the kill level. Closes, not wicks.</li>
<li><b>The target is touched</b>: the first touch.</li>
<li><b>Time is up</b>: the setup's window to work has closed.</li>
<li><b>Earnings are tomorrow</b>: the evening before a report inside the watch.</li></ul>
<b>Never:</b> buys, sells or touches an order; holds your money; shares your watches. They never enter the track record.<br>
<b>Reports to:</b> you. You manage the trade; Lookout only taps you on the shoulder.</div>
<p>Use Lookout <b>after</b> you're in, so you don't have to stare at the chart all day. While it's watching, its eyes pulse green on Today and on the ticket; when the watch ends, they go grey.</p>
<h2>How they work together</h2>
<p>Sika helps you decide. You place the trade yourself. Lookout keeps watch. Your Trade Diary records what happened and what you'd do better. At every step, the decision is yours.</p>""",
         ["Sika explains: charts, lines, when a plan is wrong, and which setups are live.",
          "Lookout watches a trade you took: plan dead, target touched, time up, earnings tomorrow.",
          "Neither one ever trades. You make every decision."],
         ("You bought a call on a breakout and want to stop checking the chart at work. Who do you ask?",
          ["Sika, to close the trade for you", "Lookout, to watch it and tap you when the plan changes", "Neither, NextShelf trades it automatically"],
          1, "Lookout watches and alerts; it never trades. Sika explains before you trade. NextShelf never places trades on its own.")),
        ("lookout", "Lookout: let NextShelf watch your trade", 3, """
<p>The hardest part of a swing trade isn't the entry. It's the days after: checking the price at work, wondering if a dip means it's over, forgetting earnings are on Thursday. <b>Lookout</b> does the watching for you.</p>
<h2>Starting a watch</h2>
<p>After you take a trade, tap <b>I'm in — watch this</b> on the swing play card (or say yes when Lookout offers right after an order goes through). Add your fill price and size if you like (both optional), then <b>Yes, watch it</b>. Lookout binds to the exact plan the card showed you: its trigger, target and the level where the idea is wrong.</p>
<h2>What it taps you about</h2>
<ul><li><b>The plan dies</b>: a 4-hour close back past the stop. Closes only: a dip during the candle that recovers by the close doesn't count, the same rule as the setups themselves.</li>
<li><b>The target is touched</b>: the first touch only. Whether you take profit or hold for the next shelf is up to you.</li>
<li><b>Time runs out</b>: every setup gets a window to work, the same clock the track record uses. When it closes, you hear about it.</li>
<li><b>Earnings are tomorrow</b>: the evening before a report that lands inside the watch, because the plan says be out before the print.</li></ul>
<h2>What it never does</h2>
<p>Lookout never buys, sells, or touches an order. The trade is yours from start to finish; Lookout just taps you on the shoulder. When you're done, tap <b>I'm out</b> on Today's Watching list. Your closed watches stay in a private log and never enter the track record.</p>""",
         ["Tap \"I'm in — watch this\" after you take a trade; Lookout binds to the card's plan.",
          "It alerts once each: plan dead (on a close), target touched, time up, earnings tomorrow.",
          "It never trades. Your watches are private and never part of the record."],
         ("You're watching a trade with a stop at $95. At 11 AM the stock dips to $94, and the 1:30 PM candle closes at $96. What does Lookout do?",
          ["Sends \"the plan is dead\" at 11 AM", "Nothing: the 1:30 close is above $95, so the plan is alive", "Sells your position"],
          1, "Lookout judges the stop on 4-hour closes, never on dips, and it never trades.")),
        ("dont-chase", "Don't chase: the first-half rule", 3, """
<p>The worst time to enter a breakout is after most of the move has happened. You pay a high price, the remaining reward is small, and the distance to your exit is large.</p>
<h2>The first-half rule</h2>
<p>If you missed the close, only take the trade if price is still in the <b>first half of the move</b>, between the hub edge and the halfway point to the aim. Past that, the reward left is usually smaller than the risk back to the hub. Skip it.</p>
<p>NextShelf builds this in: the breakout alert itself is only a heads-up, and the entry is the retest, which is close to the edge by design. A breakout that has already reached its aim is marked "skipped (too far)" in the track record.</p>
<h2>There's always another setup</h2>
<p>Hundreds of stocks are scanned after every close. Missing one move costs nothing. Chasing it often costs a lot.</p>""",
         ["Late entries have small rewards and big risks.",
          "Only enter in the first half of the move to the aim.",
          "Missing a trade is free; chasing one isn't."],
         ("Hub top $50, aim $54. Price is now $53.60. Should you enter?",
          ["Yes, it's still going up", "No, it's well past the halfway point ($52)", "Only with options"],
          1, "Halfway to the aim is $52. At $53.60 most of the reward is gone.")),
        ("position-size", "Position size: risk a small, fixed slice", 4, """
<p>How much you buy matters more than what you buy. A good setup with too much money in it can still wreck an account.</p>
<h2>Start from the loss, not the gain</h2>
<p>Many traders cap the loss on any one trade at a small slice of their account, often <b>0.5% to 1%</b>. Then they work out the size from the distance to their exit:</p>
<div class="cbox"><small>The formula</small><b>Shares = (account × risk %) ÷ (entry − exit price)</b><br>
$10,000 account, 1% risk = $100. Retest entry $50, stop 1 ATR below at $48 ($2 away). $100 ÷ $2 = <b>50 shares</b>.</div>
<p>If the trade fails, you lose about $100, not $1,000. A string of losses (which every method has) stays survivable.</p>
<h2>Gaps can make losses bigger</h2>
<p>Prices can jump overnight, past your exit. Earnings, news and market-wide shocks cause gaps. Smaller sizes are the only real protection.</p>""",
         ["Decide the most you'll lose first, then size the trade.",
          "Many traders risk 0.5–1% of their account per trade.",
          "Gaps can push losses past your planned exit."],
         ("$20,000 account, 1% risk, entry $40, exit at $38. How many shares?",
          ["100", "500", "1,000"],
          0, "1% of $20,000 is $200. The exit is $2 away. $200 ÷ $2 = 100 shares.")),
        ("earnings-and-gaps", "Earnings and gaps", 3, """
<p>A few times a year, each company reports its results. The stock can move a lot overnight on the news, and it often <b>opens far from where it closed</b>. That's a gap.</p>
<h2>Why this matters for a breakout</h2>
<p>A hub, a shelf and a "wrong if" level all assume price moves step by step. A gap ignores them: it can skip straight past your aim, or straight past your exit, before you can do anything.</p>
<h2>How NextShelf flags it</h2>
<p>When a stock reports within two weeks, it gets a violet <b>Earnings</b> pill, and the Shelf card warns you. Alerts get an extra line when earnings are within a week.</p>
<h2>What traders do about it</h2>
<ul><li>Close the trade before earnings, or</li><li>Hold a smaller position that can survive a gap against you, or</li><li>Skip setups that would still be open on the earnings date.</li></ul>
<p>Holding through earnings isn't wrong, but it's a separate bet from the breakout.</p>""",
         ["Earnings can gap a stock past every level on the chart.",
          "NextShelf shows an Earnings pill and a warning in the Shelf card.",
          "Holding through earnings is a separate decision from the breakout."],
         ("Your breakout's exit is $48. The stock reports overnight and opens at $44. What happened?",
          ["The exit protected you at $48", "It gapped past the exit; the loss is bigger than planned", "Nothing, gaps are rare"],
          1, "Exit orders can't fill at prices the stock never traded at. Gaps are why size matters around earnings.")),
    ]),
    ("Options for swing traders", "Calls, puts, and why being right isn't always enough.", [
        ("calls-and-puts", "Calls and puts in plain English", 4, """
<p>An option is a contract about a stock's price by a certain date (the <b>expiry</b>). One contract usually covers 100 shares.</p>
<ul><li>A <b>call</b> gains value when the stock rises. It's the bullish choice for a breakout up.</li>
<li>A <b>put</b> gains value when the stock falls. It's the bearish choice for a breakdown.</li></ul>
<p>Each contract has a <b>strike price</b>. An "at the money" option has a strike near today's price. "In the money" means the strike is already on the winning side (below the price for a call, above it for a put).</p>
<h2>What you pay, and what you can lose</h2>
<p>When you buy an option you pay a <b>premium</b>, for example $2.00 per share, or $200 per contract. That premium is the most you can lose. Options often do expire worthless, so treat the premium as money you're prepared to lose entirely.</p>
<h2>Delta: how much it moves with the stock</h2>
<p>An option's <b>delta</b> is roughly how much its price moves when the stock moves $1. An at-the-money option is usually around 0.5: a $1 move in the stock is worth about $0.50 on the option.</p>""",
         ["Calls rise with the stock; puts rise when it falls.",
          "The premium you pay is the most you can lose when buying.",
          "Delta ≈ how much the option moves per $1 move in the stock."],
         ("A stock breaks down out of its hub. Which option matches the setup?",
          ["A call", "A put", "Either one"],
          1, "A breakdown is bearish. Puts gain value when the stock falls.")),
        ("time-decay", "Time decay: why being right can still lose", 4, """
<p>Part of an option's price is <b>time value</b>: you're paying for the chance that the stock moves before expiry. That part shrinks every day, and it shrinks faster as expiry gets closer. That shrinking is called <b>time decay</b> (or theta).</p>
<h2>The small-move trap</h2>
<p>If the stock moves your way but only a little, the gain from the move can be <b>smaller than what you lose to time decay</b>. You were right about the direction and still lost money on the option.</p>
<p>That's why the Shelf card can show a figure like "-18% even if right": the aim is so close that the option is estimated to lose value even if the stock gets there. When you see that, the setup isn't worth trading with options.</p>
<h2>What helps</h2>
<ul><li>Give the trade <b>more time</b> than you think it needs. NextShelf picks an expiry around three times the typical days to the shelf.</li>
<li>Prefer setups with <b>more room to the aim</b>.</li>
<li>Check the estimate at the aim before you buy, not after.</li></ul>""",
         ["Time value shrinks every day, faster near expiry.",
          "A small move can be worth less than the decay.",
          "'Even if right' losses mean: skip options on that setup."],
         ("A put shows '≈ -18% even if right'. What does that mean?",
          ["It's a great deal", "Even if the stock reaches the aim, the put is estimated to lose about 18%", "The stock will fall 18%"],
          1, "The move to the aim is too small to pay for the option's time value.")),
        ("implied-volatility", "Implied volatility and IV crush", 3, """
<p><b>Implied volatility (IV)</b> is the market's guess of how much a stock will move, built into the option's price. Higher IV means more expensive options.</p>
<h2>IV crush</h2>
<p>Before earnings, IV usually climbs: everyone expects a big move, so options cost more. Right after the report, the uncertainty is gone and IV drops sharply. That drop is called <b>IV crush</b>.</p>
<p>The result surprises a lot of new traders: the stock moves the way they guessed, and their option still loses value, because the crush wiped out more than the move added.</p>
<h2>How NextShelf handles it</h2>
<p>Every options estimate says which IV it assumes (today's), and the Options tab warns you when earnings fall before the contract expires. If volatility falls, the real result will be worse than the estimate.</p>""",
         ["IV is the expected move priced into an option.",
          "IV often drops right after earnings (IV crush).",
          "NextShelf's estimates assume today's IV; falling IV means less."],
         ("You buy a call before earnings. The stock rises 2%, but your call loses value. The most likely reason?",
          ["The broker made an error", "IV crush after the report", "Calls lose value when stocks rise"],
          1, "After earnings, IV usually drops. That can outweigh a small move in your favour.")),
        ("options-what-if", "The what-if tools: volatility, time and the profit chart", 4, """
<p>An option's value depends on three things moving at once: the stock's price, the time left, and implied volatility. The options card shows one estimate at the aim. Open <b>Show the math</b> to see how that estimate changes when the other two move.</p>
<h2>The volatility table</h2>
<p>Same stock prices, three volatility levels: <b>IV −30%</b>, <b>IV unchanged</b>, and <b>IV +30%</b>, at the aim and if the setup fails. Each cell shows the option's value and the profit or loss per contract. When earnings fall before the contract expires, the −30% column is highlighted: after a report IV usually drops, and that's the column to plan around.</p>
<h2>The profit chart</h2>
<p>Below the table, drag across the chart to read the profit or loss at any stock price. Green above zero, red below, flattening at the most you can lose. The markers show Now, Fail, Breakeven and Target.</p>
<h2>The date slider</h2>
<p>Slide from today to expiry and watch the curve sag: that's time decay. The green tick marks the day the setup typically reaches its aim. Compare it with expiry and you'll see why a slow move can lose money even when you're right about the direction.</p>
<h2>Modeled, not quotes</h2>
<p>Every number comes from a pricing model (Black-Scholes) using the market's current inputs. Real prices will differ, especially when the bid-ask spread is wide. Use the tools to understand the shape of the bet, not as a promise of what you'll get.</p>""",
         ["Show the math has a volatility table (IV −30%, unchanged, +30%) at the aim and on failure.",
          "Drag the profit chart for any price; slide the date to see time decay.",
          "Everything is modeled, not a quote: real fills differ, especially with wide spreads."],
         ("Earnings land before your call expires. Which column of the volatility table should you plan around?",
          ["IV +30%, because earnings move stocks", "IV unchanged, because it's the middle", "IV −30%, because IV usually drops after the report"],
          2, "After earnings, implied volatility usually contracts, so the −30% column is the realistic case.")),
        ("expiry-and-strike", "Choosing expiry and strike", 3, """
<p>Two choices decide most of how an options trade behaves.</p>
<h2>Expiry: buy time</h2>
<p>Short-dated options are cheap for a reason: they decay fast and leave no room for a slow move. For swing trades, many traders pick an expiry with <b>well more time than the trade should need</b>. NextShelf aims for about three times the typical days to the shelf, between 2 and 6 weeks out.</p>
<h2>Strike: at the money or one strike in</h2>
<ul><li><b>At the money:</b> cheaper, moves about half as much as the stock (delta ~0.5).</li>
<li><b>One strike in the money:</b> costs more, but moves more with the stock and has less time value to lose.</li></ul>
<p>Far out-of-the-money options look cheap and show huge payoffs in calculators. They need a big, fast move and usually expire worthless. That's the payoff-chasing trap.</p>
<h2>Watch the spread</h2>
<p>The gap between the bid and ask price is a cost too. Wide spreads mean you lose money the moment you buy. Use a limit order near the middle.</p>""",
         ["Buy more time than the trade needs.",
          "At the money is cheaper; one strike in moves more with the stock.",
          "Far out-of-the-money options rarely pay. Watch the bid/ask spread."],
         ("Which option is usually the 'lottery ticket' trap?",
          ["One strike in the money, 4 weeks out", "Far out of the money, expiring this week", "At the money, 5 weeks out"],
          1, "Cheap, far out-of-the-money, short-dated options need a big fast move and usually expire worthless.")),
    ]),
    ("The trader with a day job", "Making the method fit your life.", [
        ("find-a-setups", "Where to find A setups when you log in", 3, """
<p>You open NextShelf with five minutes to spare. Here's where to look, in order.</p>
<h2>1. The hero card on Today</h2>
<p>Today is the first screen after you log in. Under the brief and the three chips (VIX, Retesting now, Waiting) sits one larger card: the <b>hero</b>. When any setup grades <b>A</b>, the best one is the hero: one at the edge right now first, otherwise one still waiting for its retest. The letter in the hero's top-right corner is the grade.</p>
<h2>2. The rest of "Retesting now"</h2>
<p>Under the hero are the other setups at the edge, with A grades listed first. Each one is a tap away from its chart and its plan. "At the edge" means the next 4-hour close decides it.</p>
<h2>3. The A only chip on the board</h2>
<p>On the board (Browse setups), tap <b>A only</b> to see just the live breakouts and breakdowns whose setup grades A. <b>Setups</b>, the default chip, shows everything that can become a trade; <b>All</b> shows the whole table.</p>
<h2>What the A means</h2>
<p>The grade is the setup's measured past record across every stock NextShelf scans, not a guess about this one stock. <b>A+</b>: an A retest with a clear path to the target. That's the top letter, then A, B, C and D. It describes what similar setups did before; it doesn't promise what this one will do, which is why every A still comes with a stop.</p>
<h2>Or let it come to you</h2>
<p>Turn on <b>Every A setup</b> (under Retesting now, or in Account) and NextShelf messages you whenever an A setup confirms on any stock it scans, not just your watchlist: about 8 a week. That's the version for a busy day.</p>""",
         ["Today's hero card shows the best A setup first, with its grade in the corner.",
          "Retesting now lists the others at the edge, A grades first; the board's A only chip filters to A.",
          "The grade is a past record across all stocks, not a promise. Every A still has a stop."],
         ("You log in at lunch. Where's the quickest place to see the best A setup?",
          ["The market map", "The hero card at the top of Today", "The Lab"],
          1, "Today's hero card puts the best A setup first, right under the brief and the chips.")),
        ("set-it-before-work", "Set it before work: bracket orders", 4, """
<p>You can't watch the 1:30 PM or 4:00 PM close from a meeting. One way around that is to place the order in the morning and let your broker handle it.</p>
<h2>The bracket order</h2>
<p>Most brokers offer a <b>bracket order</b>: one entry order, with a stop-loss and a profit target attached. NextShelf's "Set it before work" plan gives you all three numbers:</p>
<ul><li><b>Entry:</b> a stop order just past the hub edge.</li><li><b>Stop-loss:</b> the middle of the hub.</li><li><b>Target:</b> the next shelf.</li></ul>
<h2>The trade-off, honestly</h2>
<p>A stop order fires the moment price <b>touches</b> it, including on a wick that closes back inside the hub. NextShelf's own setup waits for the close. So this version will catch some false breakouts the alert would have skipped. It's a trade-off between convenience and accuracy.</p>
<p>It's also the <b>breakout version</b>, not the retest entry NextShelf now alerts on. The retest needs a 4-hour close, so it can't be fully automated. If you can check in at 1:30 PM or 4:00 PM, the retest alert is the better entry.</p>
<h2>Keep it fresh</h2>
<p>Use a <b>day order</b> and set it again each morning. A forgotten order can fill weeks later, long after the setup changed.</p>""",
         ["A bracket order = entry + stop-loss + target in one go.",
          "Stop entries fire on a touch, so they catch some false breakouts.",
          "Use day orders and reset them each morning."],
         ("Why can a before-work stop order fill on a false breakout?",
          ["Stop orders wait for the close", "Stop orders fire when price touches them, even on a wick", "Brokers ignore stop orders"],
          1, "A stop order triggers on a touch. NextShelf's alert waits for the 4-hour close.")),
        ("reading-the-track-record", "Reading a track record honestly", 3, """
<p>The Results page logs every setup NextShelf finds, with its outcome. Nothing is removed. That's useful only if you read it carefully.</p>
<h2>Win rate isn't everything</h2>
<p>A method can win 40% of the time and still make money if the wins are bigger than the losses. Another can win 70% and lose money if the losses are huge. Look at the <b>average per setup</b> next to the win rate.</p>
<h2>Small samples lie</h2>
<p>A stock with 2 setups can show 100% or 0% by pure luck. Trust patterns that hold up over many setups, and use the minimum-setups filter on the win-rate ranking.</p>
<h2>Past results aren't a promise</h2>
<p>Markets change. A setup that worked in a calm market can struggle in a wild one. The track record tells you how things went, not how they will go.</p>""",
         ["Read win rate together with the average per setup.",
          "Ignore results from just a handful of setups.",
          "A track record describes the past, not the future."],
         ("Setup A wins 70% but averages -0.4% per setup. Setup B wins 45% and averages +0.8%. Which made money?",
          ["Setup A", "Setup B", "Both"],
          1, "Average per setup is what you'd have made or lost. B's wins were bigger than its losses.")),
        ("rule-one", "Rule #1: don't force trades", 3, """
<p>Most of the damage in trading comes from trades nobody needed to take: boredom trades, revenge trades after a loss, trades taken because a chat room was excited.</p>
<h2>Waiting is a position</h2>
<p>Inside a hub there's no trade. With no setup on your watchlist, there's no trade. On those days, the right move is to do nothing, and that's a perfectly good day.</p>
<h2>Signs you're forcing it</h2>
<ul><li>You're looking for a reason to buy, instead of reacting to a close.</li><li>You moved your exit because the trade went against you.</li><li>You're trading to "win back" a loss.</li><li>You're sizing up because you're sure.</li></ul>
<h2>Let NextShelf do the waiting</h2>
<p>The point of alerts is to let the market come to you. Close the app, go to work, and let a 4-hour close outside a hub be the only thing that gets your attention.</p>""",
         ["Most big losses come from trades that didn't need taking.",
          "No setup means no trade, and that's a good day.",
          "Let the alert, not boredom or FOMO, start a trade."],
         ("You lost on a trade this morning and want to make it back today. What does Rule #1 say?",
          ["Find another trade quickly", "Wait for a real setup; don't force a trade", "Double the size on the next one"],
          1, "Revenge trading is forcing it. The next trade should come from a setup, not from the loss.")),
    ]),
    ("More setups", "The two other setups NextShelf alerts on: double tops & bottoms, and the gap fade & rip.", [
        ("double-tops-and-bottoms", "Double tops and bottoms", 3, """
<p>A <b>double top</b> is two tests of the same high, a few sessions apart, with a real dip between them. A <b>double bottom</b> is the mirror: two tests of the same low with a bounce between.</p>
<h2>What NextShelf checks</h2>
<ul><li>A real move into the first peak (a rally into a top, a drop into a bottom).</li><li>The two peaks within about a third of an ATR of each other, 3 to 30 sessions apart.</li><li>A dip (or bounce) between them at least 2 ATR deep. The low of that dip is the <b>neckline</b>.</li></ul>
<h2>The trigger</h2>
<p>A 4-hour <b>close through the neckline</b> within about 10 sessions of the second peak is the heads-up. A double top breaks down; a double bottom breaks up. If price makes a new high (or low) first, the pattern is void.</p>
<h2>The entry: the retest</h2>
<p>Like hubs, the entry is the <b>retest</b>: within 5 four-hour candles, a pullback to the neckline that closes back on the breakout side. It's an <b>A setup</b> when that retest is the <b>4:00 PM close</b> and price isn't past halfway to the aim. The stop is a close past the <b>far side</b> of the pattern: below the two lows for a double bottom. Measured on double bottoms, about 3 in 4 of those trades won. Double tops lost money on the same rule, so their retests arrive as watch only.</p>
<p>That far-side stop is wider than a hub's, so size the position from it.</p>""",
         ["Two tests of the same level, with a real dip between.",
          "The close through the neckline is the heads-up; the retest is the entry.",
          "A setup: a 4:00 PM retest, not past halfway, stop past the far side."],
         ("Where does a double top trigger?",
          ["At the second peak", "On a 4-hour close below the neckline", "On any red candle"],
          1, "The second peak is only a test. The setup confirms on a close through the neckline.")),
        ("gap-fade-and-rip", "Post-earnings gap fade and rip", 3, """
<p>This one uses <b>daily</b> candles. A stock gaps up at least 5% at the open, often on earnings, then <b>fades</b>: it closes well off the high, leaving a long upper wick.</p>
<h2>The trigger</h2>
<p>A daily <b>close back above the gap-day high</b> within 5 sessions. That's the "rip": buyers took back the whole fade. It's long only.</p>
<h2>Aim and wrong</h2>
<p>You're wrong on a close below the lowest low since the gap. The aim is twice the distance from entry to that low (2R). Earnings gaps can swing hard, so size small.</p>
<h2>How it tested</h2>
<p>This was the strongest setup in the two-year test: about +2.8% per trade over 341 trades. Fewer setups, but the best average. The last 6 months of the test were slightly negative, so size small. It's live and out of beta: each alert carries the plan (entry, stop, 2R target, 10 days), and you manage the trade.</p>""",
         ["Gap up 5%+, then a fade with a long upper wick.",
          "Trigger: a daily close back above the gap-day high within 5 sessions.",
          "Stop: below the lowest low since the gap. Aim: 2R."],
         ("A stock gaps up 8% on earnings and closes near its low. Two days later it closes above the gap-day high. What happened?",
          ["A gap fade and rip triggered", "A double top formed", "Nothing; it's too late"],
          0, "Gap, fade, then a close back above the gap-day high within 5 sessions: that's the reclaim.")),
        ("flags", "Flags: a sharp move, then a short pause", 3, """
<p style="padding:10px 14px;border-radius:10px;background:#f4f5f7;border:1px solid #e3e7ec;font-size:14px"><b>Paused.</b> NextShelf isn't alerting on this setup right now: the two-year test found no version of it that made money. It's here so you can recognise it on a chart.</p>
<p>A flag starts with a <b>pole</b>: a fast, mostly one-way move of at least 3 times the stock's normal 4-hour range (its ATR), over 2 to 6 four-hour candles. Then price <b>pauses</b> for 2 to 6 sessions in a small band that drifts slightly against the move, or sideways. That pause is the flag.</p>
<h2>The trigger</h2>
<p>A 4-hour <b>close outside the flag in the direction of the pole</b>. A bull flag breaks up; a bear flag breaks down. A flag that gives back more than half the pole isn't a flag any more, and NextShelf drops it.</p>
<h2>Aim and wrong</h2>
<p>The aim is the next shelf in that direction (or one ATR if there isn't one). You're wrong on a close back inside the flag.</p>""",
         ["Pole first: a fast move of 3+ ATR.",
          "Flag: a short pause that drifts against the pole or sideways.",
          "Trade the close out of the flag, with the pole's direction."],
         ("A stock rallies hard, then drifts down gently for three sessions. A 4-hour candle closes above the drift. What is it?",
          ["A bull flag breakout", "A double top", "No setup"],
          0, "A sharp move up, a short drift against it, then a close out the top: that's a bull flag breaking out.")),
        ("triangles", "Triangles: a squeeze into a point", 3, """
<p style="padding:10px 14px;border-radius:10px;background:#f4f5f7;border:1px solid #e3e7ec;font-size:14px"><b>Paused.</b> NextShelf isn't alerting on this setup right now: the two-year test found no version of it that made money. It's here so you can recognise it on a chart.</p>
<p>A symmetrical triangle forms when <b>highs keep getting lower and lows keep getting higher</b>, so the range narrows toward a point. It's like a hub that's being squeezed from both sides.</p>
<h2>What NextShelf checks</h2>
<p>Lines fitted to the highs and lows over 4 to 14 sessions, the top sloping down and the bottom sloping up, with the range shrinking to well under two-thirds of where it started.</p>
<h2>The trigger</h2>
<p>A 4-hour <b>close outside either line</b>. Triangles can break either way, so the direction comes from the close, not from a guess. The aim is the next shelf; you're wrong on a close back inside.</p>""",
         ["Lower highs plus higher lows = a narrowing triangle.",
          "It can break either way: let the close decide.",
          "Same plan as a hub: aim for the next shelf, out on a close back inside."],
         ("A triangle is squeezing toward its point. Which way will it break?",
          ["Always up", "Always down", "Wait for a 4-hour close outside a line"],
          2, "Triangles don't tell you the direction in advance. The close does.")),
        ("channels", "Channels: trending between two lines", 3, """
<p style="padding:10px 14px;border-radius:10px;background:#f4f5f7;border:1px solid #e3e7ec;font-size:14px"><b>Paused.</b> NextShelf isn't alerting on this setup right now: the two-year test found no version of it that made money. It's here so you can recognise it on a chart.</p>
<p>A channel is a trend that stays between <b>two roughly parallel lines</b>. In an ascending channel both lines slope up; in a descending channel both slope down. NextShelf looks for channels lasting 6 to 20 sessions.</p>
<h2>The trigger</h2>
<p>A 4-hour <b>close outside the channel</b>. A close below a rising channel, or above a falling one, says the trend has broken. A close out the other side says it's speeding up.</p>
<h2>Aim and wrong</h2>
<p>The aim is the next shelf beyond the break (or one ATR). You're wrong on a close back inside the channel.</p>""",
         ["Two parallel lines, both sloping the same way.",
          "A close outside means the trend changed pace or broke.",
          "Out on a close back inside the channel."],
         ("A stock in a rising channel closes a 4-hour candle below the lower line. What does that suggest?",
          ["The uptrend has broken", "The trend is speeding up", "Nothing"],
          0, "Closing below the bottom of a rising channel means price left the trend.")),
        ("support-and-resistance", "Support and resistance", 3, """
<p style="padding:10px 14px;border-radius:10px;background:#f4f5f7;border:1px solid #e3e7ec;font-size:14px"><b>Paused.</b> NextShelf isn't alerting on this setup right now: the two-year test found no version of it that made money. It's here so you can recognise it on a chart.</p>
<p>Some prices keep coming back. A level that price has turned at <b>three or more times</b> is support (below price) or resistance (above). NextShelf tracks those levels and forgets ones that haven't been touched in about 80 sessions.</p>
<h2>The trigger</h2>
<p>A 4-hour <b>close through a tested level</b>, after the level held for at least the previous 4 sessions. Breaking resistance is a long; breaking support is a short.</p>
<h2>Still on the map</h2>
<p>The levels themselves still show on the board and the Shelf card, as a map of where price has turned before. A close through one just isn't an alert any more.</p>""",
         ["A level needs 3+ touches to count.",
          "Trade the close through it, after it held for a while.",
          "Near a level is a heads-up, not a trade."],
         ("A stock has bounced off $40 four times and is at $40.30. Is that a trade?",
          ["Yes, buy the bounce", "Not yet; it's a heads-up until a close through a level", "Yes, short it"],
          1, "Being near support is a heads-up. NextShelf's setup starts on a close through the level.")),
        ("flush-and-reclaim", "Flush and reclaim", 3, """
<p style="padding:10px 14px;border-radius:10px;background:#f4f5f7;border:1px solid #e3e7ec;font-size:14px"><b>Paused.</b> NextShelf isn't alerting on this setup right now: the two-year test found no version of it that made money. It's here so you can recognise it on a chart.</p>
<p>This one uses <b>daily</b> candles. A stock drops <b>5% or more in a day</b>, often on earnings. Sellers rush out. Then, over the next days, buyers walk it all the way back.</p>
<h2>The level</h2>
<p>The level is where it fell from: the <b>highest close of the 5 days before the drop</b>. That's the price the sellers were sure was too high.</p>
<h2>The trigger</h2>
<p>A <b>daily close back above that level within 10 sessions</b> of the drop, on normal volume or better. A wick above it during the day doesn't count; the close does. It's long only.</p>
<h2>Aim and wrong</h2>
<p>You're wrong on a daily close back below the level. The aim is the next shelf above. These moves can be fast, so size small.</p>""",
         ["A 5%+ down day, often on earnings.",
          "Trigger: a daily close back above the level it fell from, within 10 sessions.",
          "Wrong: a daily close back below that level. Aim: the next shelf."],
         ("A stock closed at $100, dropped 8% on earnings, and six days later trades at $101 midday but closes at $99.50. Is it a setup?",
          ["Yes, it traded above $100", "Not yet; it needs a daily close above $100", "No, it's too late"],
          1, "Only the daily close counts. A close above $100 within 10 sessions is the trigger.")),
        ("failed-breakdowns", "Failed breakdowns and breakouts", 3, """
<p style="padding:10px 14px;border-radius:10px;background:#f4f5f7;border:1px solid #e3e7ec;font-size:14px"><b>Paused.</b> NextShelf isn't alerting on this setup right now: the two-year test found no version of it that made money. It's here so you can recognise it on a chart.</p>
<p>Sometimes a hub breaks the wrong way first. Price closes a 4-hour candle <b>below</b> the hub, sellers pile in, stops get hit... and then it turns straight back up. Those trapped sellers have to buy back, and that fuels the move.</p>
<h2>The trigger</h2>
<p>Within <b>3 candles</b> of the fake-out, a 4-hour <b>close back above the hub top</b> on at least <b>1.2× normal volume</b>. A close back inside the hub isn't enough; it has to clear the far side. That's a failed breakdown, and it's a long.</p>
<p>The mirror is a <b>failed breakout</b>: a close above the hub, then within 3 candles a close back below the hub bottom. That's a short.</p>
<h2>Aim and wrong</h2>
<p>Enter around the reclaim close. You're wrong on a 4-hour close below the <b>fake-out low</b> (the lowest point of the trap). The aim is the next shelf above, same as a hub breakout.</p>""",
         ["A close outside the hub that fails within 3 candles.",
          "Trigger: a 4H close through the OTHER side of the hub on 1.2× volume.",
          "Stop: past the fake-out low (or high). Aim: the next shelf."],
         ("A hub sits at $100–$104. A candle closes at $98, the next at $101, the next at $105 on heavy volume. When is the setup?",
          ["At $98, the breakdown", "At $101, back inside the hub", "At $105, the close above the hub top"],
          2, "A close back inside isn't the trigger. The close above the hub top on strong volume is.")),
    ]),
]

# paused setups are off the site (Nov 12): their lessons are kept in this file but not published
_OFF_LESSONS = {"flags", "triangles", "channels", "support-and-resistance", "flush-and-reclaim", "failed-breakdowns"}
MODULES = [(t, d, [l for l in ls if l[0] not in _OFF_LESSONS]) for t, d, ls in MODULES]
LESSONS = [(m, l) for m in MODULES for l in m[2]]


# ---------------------------------------------------------------- the glossary (Nov 22): every term, in plain words
# (term, explanation, lesson slug or None). Keep it in step with the lessons and the product: a term that changes on
# the site changes here too.
GLOSSARY = [
    ("Price structure", [
        ("Candle", "A summary of price over a stretch of time: where it opened, where it closed, and its high and low.", "why-4-hour-candles"),
        ("4-hour candle", "NextShelf's working timeframe. The trading day splits into two: one candle closes at 1:30 PM and one at 4:00 PM Eastern. Clear enough to ignore the noise, fast enough to catch a move within days.", "why-4-hour-candles"),
        ("The two closes", "1:30 PM and 4:00 PM Eastern, the only moments a NextShelf setup can confirm. You don't need to watch the screen in between.", "why-4-hour-candles"),
        ("Wick", "The thin line above or below a candle's body: price poked there during the candle but didn't stay. A wick outside the hub is an attempt that failed, not a breakout.", "close-not-wick"),
        ("Hub", "A pause: a tight sideways range where 4-hour candles keep closing inside the same narrow band, for at least 4 of them. Moves out of a tight range can be quick, because a lot of waiting decisions get made at once.", "what-is-a-hub"),
        ("Hub top / hub bottom", "The two edges of the hub. A close above the top is a breakout; a close below the bottom is a breakdown. Also called the hub edges.", "what-is-a-hub"),
        ("Warehouse / Ledge", "The hub's own quality score, 0 to 6, from how long it lasted, how tight it was and its volume. 4 to 6 is a Warehouse (a solid pause); 0 to 3 is a Ledge (a thin one).", None),
        ("Shelf", "An earlier range where price paused before. Old pauses often matter again, so a shelf is a likely place for price to stall.", "shelves"),
        ("Next shelf", "The nearest shelf in the direction of the break. It's the aim of the trade.", "shelves"),
        ("Air pocket", "A stretch of price with very little in it: no shelves, no stacks. Price tends to move through air pockets quickly, in either direction.", "clear-path"),
        ("Extended hours", "Trading outside the main 9:30 AM to 4:00 PM session: pre-market (4:00 to 9:30 AM) and after hours (4:00 to 8:00 PM ET). Thin and jumpy. NextShelf's setups use regular-hours closes only; the chart's Extended button shows these sessions shaded, for context.", "extended-hours"),
    ]),
    ("The trade", [
        ("Breakout / breakdown", "A 4-hour candle that closes outside the hub: above the top (breakout) or below the bottom (breakdown). It's the heads-up, not the entry.", "trade-your-first-setup"),
        ("Heads-up", "What the breakout alert is. It says \"wait for the retest\": don't chase that close.", "trade-your-first-setup"),
        ("15-minute warning", "About 15 minutes before each close (1:15 PM and 3:45 PM), NextShelf warns you if a stock is outside its hub: if the candle closes there, that's the breakout.", "close-not-wick"),
        ("Trigger", "The price a close has to clear for the setup to fire, usually the hub edge (the neckline for a double top or bottom).", "trade-your-first-setup"),
        ("Retest", "The entry. After a breakout, the first close back near the edge (within half an ATR), within 5 four-hour candles, that holds on the breakout side. A breakout that never comes back isn't traded.", "the-retest"),
        ("Retest window", "The 5 four-hour candles after a breakout in which a retest can happen. The board shows when it closes (\"Retest · until Wed 1:30 PM\").", "the-retest"),
        ("Retesting now / at the edge", "A setup where price is back at the edge right now: the next 4-hour close decides it.", "find-a-setups"),
        ("Failed breakout", "A close back inside the hub before the retest. The breakout failed and there is nothing to enter.", "the-game-plan"),
        ("Watch only", "A retest that misses one of the A rules, or a setup whose side lost money in the test (short hub retests, double tops). It's shown with the reason, but it isn't an entry.", "the-retest"),
        ("The game plan", "The three parts every setup comes with, decided before you trade: the entry, the aim, and where you're wrong.", "the-game-plan"),
        ("Aim / target", "Where the trade takes profit: the next shelf, or an ATR-based target when no shelf is in reach. An aim, not a promise.", "shelves"),
        ("Wrong if / stop", "Where the plan says you were wrong. On an A hub setup it's a 4-hour close 1 ATR past your entry.", "the-game-plan"),
        ("ATR (average true range)", "How much a stock normally moves in one candle. NextShelf uses it to size stops, targets and \"close to the edge\" for each stock, so a quiet stock and a wild one are judged fairly.", "shelves"),
        ("Room (reward to risk)", "The distance to the next shelf divided by the distance back to the middle of the hub. An A setup needs 1.5× or more.", "the-retest"),
        ("Sessions", "Trading days. An A hub setup needs a hub that held for 4 or more sessions.", "the-retest"),
        ("10 candles / timed out", "Each trade gets 10 four-hour candles, about a week. If it hasn't reached the aim or the stop by then, it's marked timed out.", "the-game-plan"),
        ("Follow-through / snap-back / stalled", "The three ways a trade ends: it reached the aim, it hit the stop, or it ran out of time.", None),
        ("Chasing / the first-half rule", "Buying after most of the move has happened. If you missed the close, only take the trade while price is in the first half of the move, between the edge and halfway to the aim.", "dont-chase"),
        ("Skipped (too far)", "A breakout that had already reached its aim before it could be entered. Logged in the track record, never traded.", "dont-chase"),
        ("Position size", "How much you buy, worked out from the loss you'll accept: shares = (account × risk %) ÷ (entry − stop).", "position-size"),
        ("R and 2R", "R is the distance from your entry to your stop: one unit of risk. A 2R target is twice that distance in your favour.", "gap-fade-and-rip"),
    ]),
    ("Grades and evidence", [
        ("Setup grade", "A letter printed next to a setup: its measured past record, on that stock where there's enough history, otherwise across every stock NextShelf scans. It describes what similar setups did, not what this one will do.", "find-a-setups"),
        ("The letters", "A+ is the top: an A retest with a clear path to the target. Then A, B, C and D. D means losses outweighed wins; C is about break-even.", "find-a-setups"),
        ("A setup", "A hub retest that passes the measured rules: room 1.5× or more, a hub of 4+ sessions, a stop 1 ATR from the entry. Longs only.", "the-retest"),
        ("A+", "An A retest whose path to the target was clear at the retest. The strongest grade NextShelf gives.", "clear-path"),
        ("Stack", "Two or more different kinds of price level clustered close together: an old high, an older hub edge, a cluster of moving averages, a gamma level. Stacks act like walls.", "clear-path"),
        ("Clear path", "No stack between the entry and the target. On the board, a blue Clear path chip shows the road as it stands right now; the grade's own reading is taken at the retest.", "clear-path"),
        ("Stack in the way", "A stack sits between the entry and the target. An A retest like this steps down from A+ and carries its own, weaker record.", "clear-path"),
        ("Confluence", "Another kind of level sitting right on a setup's trigger or target.", None),
        ("Strong / weak breakout", "Strong means the breakout candle had 1.2× normal volume or more and closed at least 0.2 ATR past the edge. Weak breaks fail more often. Breakouts on light volume don't count at all.", "trade-your-first-setup"),
        ("Volume (Vol 1.2×)", "The breakout candle's volume compared with a normal candle at that time of day.", None),
        ("Average per trade", "The average gain or loss per trade. It's what decides whether a rule works, not the win rate.", "reading-the-track-record"),
        ("Win rate", "The share of trades that won. A method can win 40% of the time and make money if the wins are bigger than the losses.", "reading-the-track-record"),
        ("Profit factor (PF)", "Total winnings divided by total losses. PF 1.31 means $1.31 made for every $1.00 lost. Win rate tells you how often; profit factor tells you how much.", "flow-confirmed"),
        ("n (trade count)", "How many trades an average rests on. Under 30 is directional only; a rule needs 100+ before it can leave beta.", "flow-confirmed"),
        ("Early / later half", "The test split in two by time. A rule that only works in one half is probably luck.", None),
        ("Without the top 5", "The record recomputed with its five best stocks removed, so a couple of lucky names can't carry it.", "flow-confirmed"),
        ("Backtest", "History replayed: every past setup scored the way the rule trades it. Expect a little decay going forward, because the rule was chosen on the same data.", "flow-confirmed"),
        ("Track record / forward log", "Every live setup logged from the day its rule shipped, with its outcome. Nothing is removed. This is the proof that counts.", "reading-the-track-record"),
        ("Beta / ready to leave beta", "A rule stays in beta until it passes every check in the Lab: 100+ trades, a positive average, PF 1.2+, positive in both halves, positive over the last 6 months, and positive without its 5 best stocks.", None),
    ]),
    ("Other setups", [
        ("Double top / double bottom", "Two tests of the same high (top) or low (bottom), 3 to 30 sessions apart, with a real dip or bounce of at least 2 ATR between them.", "double-tops-and-bottoms"),
        ("Neckline", "The low of the dip between the two peaks of a double top (or the high of the bounce in a double bottom). A close through it is the heads-up; the retest of it is the entry.", "double-tops-and-bottoms"),
        ("Gap", "When a stock opens far from where it closed, often on earnings or news. A gap can skip straight past an aim or a stop.", "earnings-and-gaps"),
        ("Gap fade & rip", "A stock gaps up 5%+ and fades into a long upper wick, then closes back above the gap-day high within 5 sessions. Daily candles, long only, stop below the lowest low since the gap, 2R target.", "gap-fade-and-rip"),
        ("Earnings pill", "The violet badge on a stock that reports within two weeks. Alerts add a line when earnings are within a week.", "earnings-and-gaps"),
    ]),
    ("Options and the tape", [
        ("Option", "A contract about a stock's price by a certain date. One contract usually covers 100 shares.", "calls-and-puts"),
        ("Call / put", "A call gains value when the stock rises; a put gains value when it falls.", "calls-and-puts"),
        ("Strike", "The price written into the option. At the money: near today's price. In the money: already on the winning side.", "calls-and-puts"),
        ("Expiry", "The date the option ends. NextShelf aims for about three times the typical days to the shelf, 2 to 6 weeks out.", "expiry-and-strike"),
        ("Premium", "What you pay for an option, and the most you can lose on it.", "calls-and-puts"),
        ("Delta", "Roughly how much an option's price moves when the stock moves $1. At the money is about 0.5.", "calls-and-puts"),
        ("Time decay (theta)", "The part of an option's price you pay for time, shrinking every day and faster near expiry. A small move your way can still lose money.", "time-decay"),
        ("Implied volatility (IV)", "The market's guess of how much a stock will move, built into the option's price. Higher IV, pricier options.", "implied-volatility"),
        ("IV crush", "The sharp drop in IV right after earnings. It can wipe out the gain from a move that went your way.", "implied-volatility"),
        ("What-if table", "Inside Show the math: the option's modeled value and profit or loss at IV −30%, unchanged and +30%, at the aim and on failure. With earnings before expiry, the −30% column is the one to plan around.", "options-what-if"),
        ("Profit chart and date slider", "Drag across the chart for the profit or loss at any stock price; slide from today to expiry to watch time decay. Modeled with Black-Scholes, not quotes.", "options-what-if"),
        ("Bid-ask spread", "The gap between the price buyers pay and sellers get. Wide spreads cost you the moment you buy; use a limit order near the middle.", "expiry-and-strike"),
        ("The tape / flow confirmed", "Money spent buying calls at the ask minus money spent buying puts at the ask, over the break day and the session before. When it's large for that stock, the break was backed by real money: flow confirmed. A past fact about the break, not a forecast.", "flow-confirmed"),
        ("Gamma / GEX", "Gamma exposure: where options dealers are positioned by strike. Big gamma strikes can act like magnets or walls for price.", None),
        ("Call wall / put wall", "The strike with the most call (or put) gamma for an expiry. It often behaves like a ceiling (call wall) or a floor (put wall).", None),
        ("GEX heat map", "The grid beside the chart showing gamma by strike and expiry, in millions of dollars. The brightest cells are the strongest levels.", None),
    ]),
    ("Chart tools", [
        ("Volume profile", "The bars on the left of the chart: how much traded at each price. Long bars are prices the market accepted; short bars are air.", None),
        ("POC (point of control)", "The price with the most volume traded in the profile. Price often gravitates back to it.", None),
        ("Moving averages", "The average close over the last 9, 21, 50 or 200 candles. NextShelf counts them as a level only when two or more cluster together.", "clear-path"),
        ("Ichimoku cloud", "The shaded band on the chart (settings 9 · 26 · 52). Shown for context only: it is never part of a grade or an alert.", None),
        ("VIX", "The market's fear gauge, from S&P 500 option prices. High VIX means a jumpy market, where gaps and false breaks are more common.", None),
        ("Sector pulse", "Each of the 11 sectors compared with the S&P 500 over 5 and 20 days. Rotating in means beating the market on both; rotating out means lagging on both. Context only: never a grade.", "sector-pulse"),
        ("Relative strength (5D / 20D)", "A sector's return minus the S&P 500's over the last 5 or 20 trading days, in percentage points. +2.00 means it beat the market by 2 points.", "sector-pulse"),
    ]),
    ("In the app", [
        ("Today", "The first screen after you log in: the brief, the three chips (VIX, Retesting now, Waiting) and the hero card.", "find-a-setups"),
        ("Hero card", "The larger card on Today. When any setup grades A, the best one goes here: one at the edge right now first.", "find-a-setups"),
        ("Board (Browse setups)", "Every setup across the stocks NextShelf scans. Chips filter it: Setups (the default), A only, All, and more.", "find-a-setups"),
        ("Watchlist", "The tickers you follow. Only those can alert you; the free tier covers 3.", "trade-your-first-setup"),
        ("Morning brief", "A weekday message at the time you choose: the stocks closest to a breakout, breakouts still in play, and your watchlist.", None),
        ("Sika", "NextShelf's explainer: the assistant on each stock's panel (the robot in the corner), and Ask Sika on Today for finding live setups. Tap it on a stock for \"Explain this chart\", \"What do the lines mean?\" and \"When is it wrong?\", or ask your own question. Plain English from NextShelf's rules and past prices. Education only.", "meet-sika-and-lookout"),
        ("Results", "The track record page: every setup NextShelf found, with its outcome.", "reading-the-track-record"),
        ("Lookout", "NextShelf's watcher (the two green eyes): opt-in watching for a trade you took: it taps you when the plan dies (on a close), the target is touched, time runs out, or earnings are tomorrow. It never trades, and your watches stay private.", "lookout"),
        ("Trade diary", "Your private log of every trade: what you bought, why you took it, what you'd do better and a grade. Trades you watch with Lookout (or place through a linked brokerage) land there on their own. It shows your P&L calendar, win rate and the patterns in your own trading.", None),
        ("Profit factor", "Money made on winners divided by money lost on losers. Above 1 means your wins outweigh your losses; 2 means you made $2 for every $1 lost.", None),
        ("R (R-multiple)", "A trade's result measured in units of what you risked. Risk $100 and make $250: that's +2.5R. Lose the full $100: -1R. For a bought option the risk is the premium you paid.", None),
        ("The kill", "The level where the plan is wrong, judged on a 4-hour close (never a dip during the candle). In Lookout, a close past it means \"the plan is dead\".", "lookout"),
        ("The window (the clock)", "The time a setup gets to work: 10 four-hour candles from the breakout, the same clock the track record uses for \"ran out of time\".", "lookout"),
        ("The Lab", "Where every rule is tested on two years of history before it reaches you, and re-tested as data comes in.", None),
    ]),
]


def _gid(term: str) -> str:
    import re
    return "g-" + re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-")


def glossary_body() -> str:
    titles = {l[0]: l[1] for _, _, ls in MODULES for l in ls}
    n = sum(len(t) for _, t in GLOSSARY)
    parts = [f'<p class="lu-lede">Every term NextShelf uses, in plain words. {n} terms. Each one links to the lesson that teaches it, where there is one.</p>',
             '<input class="gl-q" type="search" placeholder="Search a term (retest, ATR, A+…)" aria-label="Search the glossary" oninput="glFilter(this.value)">',
             '<div class="gl-jump">' + "".join(f'<a href="#gc{i}">{_e(c)}</a>' for i, (c, _) in enumerate(GLOSSARY)) + '</div>']
    for i, (cat, terms) in enumerate(GLOSSARY):
        rows = []
        for term, expl, slug in terms:
            more = f' <a class="gl-l" href="/learn/{slug}">{_e(titles[slug])} →</a>' if slug in titles else ""
            rows.append(f'<div class="gl-t" id="{_gid(term)}" data-k="{_e((term + " " + expl).lower())}"><dt>{_e(term)}</dt><dd>{_e(expl)}{more}</dd></div>')
        parts.append(f'<section class="gl-c" id="gc{i}"><h2>{_e(cat)}</h2><dl>{"".join(rows)}</dl></section>')
    parts.append('<p class="gl-none" hidden>No term matches. Try a shorter word.</p>')
    parts.append('<p class="lu-fine">Educational content only, not financial advice. Trading involves risk, including the loss of your money.</p>')
    return "".join(parts)


def _e(s: str) -> str:
    return html.escape(s)


def index_body() -> str:
    n = len(LESSONS)
    mins = sum(l[2] for _, l in LESSONS)
    parts = [f'<p class="lu-lede">Short, free lessons on the NextShelf method: hubs, shelves and closes, the game plan, options basics, '
             f'how to trade around a day job, and the other setups on the board. {n} lessons · about {mins} minutes in total.</p>',
             f'<a class="gl-card" href="/learn/glossary"><span class="lu-n">Aa</span><span><b>Glossary</b><small>Every term on NextShelf in plain words: '
             f'{sum(len(t) for _, t in GLOSSARY)} terms, from ATR to A+.</small></span><span class="lu-m">→</span></a>']
    k = 0
    for mi, (title, blurb, lessons) in enumerate(MODULES, 1):
        rows = []
        for slug, t, m, *_ in lessons:
            k += 1
            rows.append(f'<a class="lu-l" href="/learn/{slug}"><span class="lu-n">{k:02d}</span><span class="lu-t">{_e(t)}</span>'
                        f'<span class="lu-m">{m} min</span></a>')
        parts.append(f'<section class="lu-mod" id="m{mi}"><div class="lu-mh"><small>Module {mi}</small><h2>{_e(title)}</h2><p>{_e(blurb)}</p></div>'
                     f'<div class="lu-ls">{"".join(rows)}</div></section>')
    parts.append('<div class="lu-cta"><b>Want it in your inbox?</b> Join the waitlist and get the free 5-day Hub Breakout Course by email.'
                 ' <a href="/#join">Join the waitlist →</a></div>')
    parts.append('<p class="lu-fine">Educational content only, not financial advice. Trading involves risk, including the loss of your money.</p>')
    return "".join(parts)


def lesson_body(slug: str) -> tuple[str, str] | None:
    for i, ((mtitle, _, _), (s, title, mins, body, takeaways, check)) in enumerate(LESSONS):
        if s != slug:
            continue
        q, opts, right, why = check
        prev = LESSONS[i - 1][1] if i > 0 else None
        nxt = LESSONS[i + 1][1] if i + 1 < len(LESSONS) else None
        quiz = "".join(f'<button type="button" data-ok="{1 if j == right else 0}">{_e(o)}</button>' for j, o in enumerate(opts))
        out = (f'<p class="lu-meta">Lesson {i + 1} of {len(LESSONS)} · {_e(mtitle)} · {mins} min read</p>{body}'
               f'<div class="lu-take"><small>Key points</small><ul>{"".join(f"<li>{_e(t)}</li>" for t in takeaways)}</ul></div>'
               f'<div class="lu-quiz" data-why="{_e(why)}"><small>Check yourself</small><p><b>{_e(q)}</b></p><div class="lu-opts">{quiz}</div>'
               f'<p class="lu-why" hidden></p></div>'
               f'<div class="lu-nav">{f"<a href=/learn/{prev[0]}>← {_e(prev[1])}</a>" if prev else "<a href=/learn>← All lessons</a>"}'
               f'{f"<a class=next href=/learn/{nxt[0]}>{_e(nxt[1])} →</a>" if nxt else "<a class=next href=/learn>Back to all lessons →</a>"}</div>'
               f'<p class="lu-fine">Educational content only, not financial advice. <a href="/board">See today\'s setups →</a></p>')
        return title, out
    return None


STYLE = """<style>
.lu-lede { font-size: 17px; line-height: 1.6; color: var(--muted); }
.lu-mod { margin-top: 34px; }
.lu-mh small, .lu-take small, .lu-quiz small { display: block; font-size: 11px; font-weight: 600; letter-spacing: .14em; text-transform: uppercase; color: var(--faint, #8a9aae); }
.lu-mh h2 { margin: 4px 0 2px !important; font-size: 22px !important; } .lu-mh p { margin: 0 0 12px; color: var(--muted); }
.lu-ls { display: grid; gap: 8px; }
.lu-l { display: grid; grid-template-columns: 34px 1fr auto; align-items: center; gap: 12px; padding: 14px 16px; border-radius: 14px; text-decoration: none !important;
  border: 1px solid var(--line); background: var(--panel); color: var(--ink) !important; transition: border-color .15s, transform .15s; }
.lu-l:hover { border-color: #3DD68C; transform: translateY(-1px); }
.lu-n { display: grid; place-items: center; width: 30px; height: 30px; border-radius: 9px; font-weight: 700; font-size: 13px; background: rgba(61,214,140,.12); color: #3DD68C; }
.lu-t { font-weight: 600; } .lu-m { font-size: 13px; color: var(--muted); }
.lu-cta { margin-top: 30px; padding: 18px; border-radius: 16px; border: 1px solid rgba(61,214,140,.4); background: rgba(61,214,140,.06); color: var(--muted); }
.lu-cta b { color: var(--ink); } .lu-cta a, .lu-cta a:hover { color: #141412 !important; font-weight: 700; }
.lu-meta { font-size: 13px; color: var(--faint, #8a9aae); }
.lu-take, .lu-quiz { margin-top: 26px; padding: 18px; border-radius: 16px; border: 1px solid var(--line); background: var(--panel); }
.lu-take ul { margin: 8px 0 0; }
.lu-opts { display: grid; gap: 8px; margin-top: 10px; }
.lu-opts button { text-align: left; padding: 12px 14px; border-radius: 12px; border: 1px solid var(--line); background: transparent; color: var(--ink); font: 500 15px var(--sans); cursor: pointer; }
.lu-opts button:hover { border-color: var(--muted); }
.lu-opts button.ok { border-color: #3DD68C; background: rgba(61,214,140,.1); } .lu-opts button.no { border-color: #FF6B5E; background: rgba(255,107,94,.08); }
.lu-why { margin: 10px 0 0; color: var(--muted); }
.lu-nav { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-top: 30px; }
.lu-nav a { padding: 11px 16px; border-radius: 999px; border: 1px solid var(--line); color: var(--ink) !important; text-decoration: none !important; font-weight: 600; font-size: 14px; }
.lu-nav a.next { margin-left: auto; background: var(--ink); color: var(--bg, #070b10) !important; border-color: var(--ink); }
.lu-fine { margin-top: 26px; font-size: 13px; color: var(--faint, #8a9aae); }
html[data-theme="light"] .lu-nav a.next { background: #0d1117; color: #fff !important; border-color: #0d1117; }
.gl-card { display: grid; grid-template-columns: 34px 1fr auto; align-items: center; gap: 12px; margin-top: 18px; padding: 16px; border-radius: 16px;
  border: 1px solid rgba(61,214,140,.4); background: rgba(61,214,140,.06); color: var(--ink) !important; text-decoration: none !important; }
.gl-card:hover { border-color: #3DD68C; } .gl-card b { display: block; } .gl-card small { display: block; color: var(--muted); font-size: 14px; margin-top: 2px; }
.gl-q { width: 100%; box-sizing: border-box; margin-top: 18px; padding: 13px 16px; border-radius: 14px; border: 1px solid var(--line); background: var(--panel); color: var(--ink); font: 500 16px var(--sans); }
.gl-jump { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.gl-jump a { padding: 7px 12px; border-radius: 999px; border: 1px solid var(--line); font-size: 13px; font-weight: 600; color: var(--ink) !important; text-decoration: none !important; }
.gl-c { margin-top: 30px; scroll-margin-top: 80px; } .gl-c h2 { font-size: 20px !important; margin: 0 0 8px !important; }
.gl-c dl { margin: 0; display: grid; gap: 8px; }
.gl-t { padding: 14px 16px; border-radius: 14px; border: 1px solid var(--line); background: var(--panel); scroll-margin-top: 80px; }
.gl-t:target { border-color: #3DD68C; }
.gl-t dt { font-weight: 700; color: var(--ink); } .gl-t dd { margin: 4px 0 0; color: var(--muted); line-height: 1.55; }
.gl-l { font-weight: 600; font-size: 14px; overflow-wrap: anywhere; }
.gl-c[hidden], .gl-t[hidden], .gl-jump[hidden], .gl-none[hidden] { display: none !important; }
.gl-c dl, .gl-t { min-width: 0; }
.gl-none { color: var(--muted); margin-top: 20px; }
</style>
<script>
function glFilter(q) {
  q = (q || "").trim().toLowerCase(); let any = false;
  document.querySelectorAll(".gl-c").forEach(c => { let n = 0;
    c.querySelectorAll(".gl-t").forEach(t => { const hit = !q || (t.dataset.k || "").includes(q); t.hidden = !hit; if (hit) n++; });
    c.hidden = !n; if (n) any = true; });
  const none = document.querySelector(".gl-none"); if (none) none.hidden = any;
  const j = document.querySelector(".gl-jump"); if (j) j.hidden = !!q;
}
document.querySelectorAll(".lu-quiz").forEach(q => q.querySelectorAll("button").forEach(b => b.onclick = () => {
  const ok = b.dataset.ok === "1";
  q.querySelectorAll("button").forEach(x => { x.classList.remove("ok", "no"); if (x.dataset.ok === "1") x.classList.add("ok"); });
  if (!ok) b.classList.add("no");
  const w = q.querySelector(".lu-why"); w.hidden = false; w.innerHTML = (ok ? "<b>Right.</b> " : "<b>Not quite.</b> ") + q.dataset.why;
}));
</script>"""


def render(slug: str | None = None) -> str | None:
    shell = (STATIC / "page.html").read_text()
    if slug == "glossary":
        title, body = "Glossary", glossary_body()
        crumbs = '<a href="/learn">Learn</a><a class="on">Glossary</a>'
    elif slug:
        got = lesson_body(slug)
        if not got:
            return None
        title, body = got
        crumbs = '<a href="/learn">Learn</a><a class="on">Lesson</a>'
    else:
        title, body = "Learn", index_body()
        crumbs = '<a class="on" href="/learn">Learn</a><a href="/board">Setups</a>'
    a = shell.index('<div class="toc">')
    b = shell.index("</div>", a) + len("</div>")
    shell = shell[:a] + f'<div class="toc">{crumbs}</div>' + shell[b:]
    css, js = STYLE.split("<script>", 1)
    h = shell.index("</head>")
    shell = shell[:h] + css + shell[h:]              # styles in <head>: no flash of the unstyled page
    i = shell.rindex("</body>")
    shell = shell[:i] + "<script>" + js + shell[i:]
    if not slug:                                     # the index gets the editorial headline
        shell = shell.replace("<h1>{{TITLE}}</h1>", "<h1>Learn the <em>setup.</em></h1>")
    return (shell.replace("{{TITLE}}", _e(title)).replace("{{UPDATED}}", "")
                 .replace("{{BODY}}", body).replace("{{SLUG}}", slug or "learn"))
