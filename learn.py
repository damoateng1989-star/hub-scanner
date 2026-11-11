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
<p>A setup is one thing: a stock that has been coiling inside a <b>hub</b> — a price zone it keeps bouncing around in — and then <b>closes outside it</b> on a 4-hour candle. That close is the trigger, and it comes with a plan: where to get in, where you're wrong, and where to take profit. This lesson walks through the whole thing once, start to finish.</p>
<div style="margin:18px 0;padding:14px 6px 6px;border:1px solid #e3e7ec;border-radius:16px;background:#f7f8f9;"><svg viewBox="0 0 640 300" role="img" aria-label="Diagram: candles coiling inside a hub, then one candle closing above the hub top" style="display:block;width:100%;height:auto;"><line x1="40" y1="52" x2="600" y2="52" stroke="#8a9aae" stroke-width="1.5" stroke-dasharray="6 5"/><text x="40" y="42" font-family="JetBrains Mono, monospace" font-size="12" fill="#8a9aae">next shelf — the aim</text><rect x="40" y="150" width="560" height="64" fill="rgba(47,84,235,0.08)"/><line x1="40" y1="150" x2="600" y2="150" stroke="#2F54EB" stroke-width="1.5" stroke-dasharray="6 5"/><line x1="40" y1="214" x2="600" y2="214" stroke="#2F54EB" stroke-width="1.5" stroke-dasharray="6 5"/><text x="40" y="142" font-family="JetBrains Mono, monospace" font-size="12" fill="#2F54EB">hub top</text><text x="40" y="232" font-family="JetBrains Mono, monospace" font-size="12" fill="#2F54EB">hub bottom</text><g stroke="#141412" stroke-width="2"><line x1="107" y1="162" x2="107" y2="202"/><rect x="100" y="170" width="14" height="24" fill="#141412" stroke="none"/><line x1="187" y1="170" x2="187" y2="206"/><rect x="180" y="178" width="14" height="20" fill="#141412" stroke="none"/><line x1="267" y1="158" x2="267" y2="200"/><rect x="260" y="166" width="14" height="26" fill="#141412" stroke="none"/><line x1="347" y1="172" x2="347" y2="206"/><rect x="340" y="180" width="14" height="18" fill="#141412" stroke="none"/><line x1="427" y1="164" x2="427" y2="202"/><rect x="420" y="172" width="14" height="22" fill="#141412" stroke="none"/></g><line x1="508" y1="88" x2="508" y2="96" stroke="#3DD68C" stroke-width="2"/><rect x="500" y="96" width="16" height="54" fill="#3DD68C"/><text x="600" y="76" text-anchor="end" font-family="JetBrains Mono, monospace" font-size="12" fill="#141412">breakout close — the trigger</text><text x="40" y="282" font-family="JetBrains Mono, monospace" font-size="12" fill="#8a9aae">stop: a 4H close back inside the hub</text></svg><p style="font-size:13px;color:#5b6672;margin:6px 12px 8px;"><b style="color:#141412;">The setup, drawn.</b> Candles coil inside the hub for days. One 4-hour candle closes above the hub top — that's the trigger. The next shelf above is the aim; a close back inside is where you're wrong.</p></div>
<h2>1. Pick your watchlist</h2>
<p>NextShelf only watches the tickers you put on your watchlist, and only those can alert you. The free tier covers 3 tickers. Pick names you actually follow — the alert is only useful if you know the stock.</p>
<h2>2. Wait for the alert</h2>
<p>There are two 4-hour closes a day: <b>1:30 PM</b> and <b>4:00 PM</b> Eastern. Either close can be the trigger: if a 4-hour candle closes outside the hub, that's the breakout. About 15 minutes before each close, Pro members get a warning if a stock is sitting outside its hub. After the close, the alert arrives on Telegram (free) or by text and phone call (Pro).</p>
<h2>3. Read the card</h2>
<p>Every alert carries the same three numbers: the <b>trigger</b> (the close outside the hub), the <b>aim</b> (the next shelf in the direction of the break), and <b>where you're wrong</b> (a 4-hour close back inside the hub). The stop matters most: you know what you lose if you're wrong before any money goes in.</p>
<h2>4. Place it in your own broker</h2>
<p>NextShelf never trades for you and never holds your money. The card has links to open the ticker in Webull or Robinhood — you place the order there, at the size you worked out from your risk. The position-size lesson shows how to work that out from the distance to your exit.</p>
<h2>5. Manage it with the three exits</h2>
<p>After you're in, there are only three ways out: price reaches the shelf, a 4-hour candle closes back inside the hub (a snap-back), or 10 four-hour candles pass — about a week — without either happening. Nothing else. No watching the screen all day.</p>
<h2>6. Check the record</h2>
<p>Every setup NextShelf calls is logged on the Results page, wins and losses, nothing removed. Before you trust a setup with real money, look at how it has actually performed.</p>
<h2>Three rules for your first few</h2>
<p><b>Only take fresh breakouts.</b> On the board that's 0 out of 10 candles. A breakout that's already 6 candles old is a move that already happened, not an entry.</p>
<p><b>Don't chase.</b> If price is already past halfway to the aim by the time you see the alert, skip it. The plan only works at the breakout close.</p>
<div style="margin:18px 0;padding:14px 6px 6px;border:1px solid #e3e7ec;border-radius:16px;background:#f7f8f9;"><svg viewBox="0 0 640 300" role="img" aria-label="Diagram: the first-half rule. Between the hub edge and halfway is the entry zone; past halfway is chasing." style="display:block;width:100%;height:auto;"><rect x="180" y="30" width="240" height="120" fill="rgba(220,0,88,0.07)"/><rect x="180" y="150" width="240" height="120" fill="rgba(61,214,140,0.10)"/><line x1="300" y1="30" x2="300" y2="270" stroke="#141412" stroke-width="2"/><line x1="180" y1="30" x2="420" y2="30" stroke="#8a9aae" stroke-width="1.5" stroke-dasharray="6 5"/><line x1="180" y1="150" x2="420" y2="150" stroke="#8a9aae" stroke-width="1.5" stroke-dasharray="6 5"/><line x1="180" y1="270" x2="420" y2="270" stroke="#8a9aae" stroke-width="1.5" stroke-dasharray="6 5"/><circle cx="300" cy="210" r="7" fill="#3DD68C"/><circle cx="300" cy="90" r="7" fill="#DC0058"/><text x="440" y="34" font-family="JetBrains Mono, monospace" font-size="12" fill="#141412">aim — next shelf</text><text x="440" y="154" font-family="JetBrains Mono, monospace" font-size="12" fill="#141412">halfway</text><text x="440" y="274" font-family="JetBrains Mono, monospace" font-size="12" fill="#141412">hub edge</text><text x="170" y="214" text-anchor="end" font-family="JetBrains Mono, monospace" font-size="12" fill="#141412">enter here ✓</text><text x="170" y="94" text-anchor="end" font-family="JetBrains Mono, monospace" font-size="12" fill="#141412">here = chasing ✗</text></svg><p style="font-size:13px;color:#5b6672;margin:6px 12px 8px;"><b style="color:#141412;">The first-half rule, drawn.</b> Between the hub edge and halfway to the aim, the reward left is bigger than the risk back. Past halfway, it's the other way round — skip it.</p></div>
<p><b>Never hold through earnings.</b> The alert warns you when a print is near. The setup is a swing into the move, not a bet on the report.</p>
<h2>Worked example: one real watchlist</h2>
<p>Here's what this looked like on one real day — the Nov 9 close, on a watchlist of 11 tickers. Levels change every close, so check the board for today's numbers. The shape is what matters.</p>
<p><b>Trade now — HONA.</b> Broke out on the 1:30 PM close at $164.18, out of a $149.59–$161.72 warehouse hub. Stop is a 4-hour close back inside (below $161.72); the aim is the next shelf at $207.41. The catch: a gamma wall sits at $165, right above the entry — the first test. Still in the first half of the move (halfway is $184.57), so the entry is valid.</p>
<div style="margin:18px 0;padding:14px 6px 6px;border:1px solid #e3e7ec;border-radius:16px;background:#f7f8f9;"><svg viewBox="0 0 640 320" role="img" aria-label="Diagram: HONA at the Nov 9 close. Hub 149.59 to 161.72, entry 164.18, gamma wall 165, halfway 184.57, target shelf 207.41." style="display:block;width:100%;height:auto;"><rect x="180" y="214" width="240" height="46" fill="rgba(47,84,235,0.08)"/><line x1="180" y1="214" x2="420" y2="214" stroke="#2F54EB" stroke-width="1.5" stroke-dasharray="6 5"/><line x1="180" y1="260" x2="420" y2="260" stroke="#2F54EB" stroke-width="1.5" stroke-dasharray="6 5"/><line x1="300" y1="30" x2="300" y2="280" stroke="#141412" stroke-width="2"/><line x1="180" y1="40" x2="420" y2="40" stroke="#8a9aae" stroke-width="1.5" stroke-dasharray="6 5"/><line x1="180" y1="127" x2="420" y2="127" stroke="#8a9aae" stroke-width="1.5" stroke-dasharray="6 5"/><line x1="180" y1="201" x2="420" y2="201" stroke="#DC0058" stroke-width="1.5" stroke-dasharray="6 5"/><circle cx="300" cy="205" r="7" fill="#3DD68C"/><line x1="178" y1="195" x2="292" y2="202" stroke="#141412" stroke-width="1" opacity="0.45"/><text x="440" y="44" font-family="JetBrains Mono, monospace" font-size="12" fill="#141412">next shelf $207.41</text><text x="440" y="131" font-family="JetBrains Mono, monospace" font-size="12" fill="#141412">halfway $184.57</text><text x="440" y="205" font-family="JetBrains Mono, monospace" font-size="12" fill="#DC0058">gamma wall $165</text><text x="170" y="192" text-anchor="end" font-family="JetBrains Mono, monospace" font-size="12" fill="#141412">entry $164.18</text><text x="170" y="222" text-anchor="end" font-family="JetBrains Mono, monospace" font-size="12" fill="#2F54EB">hub top $161.72</text><text x="170" y="264" text-anchor="end" font-family="JetBrains Mono, monospace" font-size="12" fill="#2F54EB">hub bottom $149.59</text></svg><p style="font-size:13px;color:#5b6672;margin:6px 12px 8px;"><b style="color:#141412;">HONA, Nov 9 close.</b> Warehouse hub, 6 sessions. Entry $164.18 · wall $165 · target $207.41. The wall sits almost on top of the entry — the first test of the breakout.</p></div>
<p><b>Watch — ABBV, GOOG, GOOGL.</b> All three are pressing the top of their hubs: ABBV at $294.17 under a $294.89 top, GOOG at $287.61 under $289.92, GOOGL at $289.61 under $291.67. Nothing to do until a 4-hour candle closes above the hub top.</p>
<p><b>Wait — the rest.</b> AAPL, AMZN, ARM, NVDA, QQQ, SPCX: no live setup. IBM's breakdown and ARM's bear flag are days old — moves that already happened, not entries. NVDA reports Nov 17, so nothing new goes on there into earnings.</p>
<p>Start by watching a few alerts play out, and paper-trade them in your broker's practice account before any real money. If a setup's plan doesn't make sense to you, skip it — there will be another one at the next close.</p>""",
         ["A setup is a hub breakout with a plan: trigger, aim, and where you're wrong.",
          "After every close your watchlist sorts into three buckets: trade now, watch, wait.",
          "Only fresh breakouts (0 of 10 candles) are entries — and never chase past halfway to the aim."],
         ("NextShelf alerts you to a breakout. Where does the trade actually happen?",
          ["NextShelf places it for you", "In your own broker, using the plan on the card", "By replying to the Telegram alert"],
          1, "NextShelf never trades for you or holds your money. You place the order in your own broker.")),
        ("the-game-plan", "The game plan: aim, and where you're wrong", 3, """
<p>Every NextShelf setup comes with the same three parts. Knowing all three <i>before</i> you trade is what turns a setup into a plan.</p>
<ul><li><b>The trigger:</b> a 4-hour close outside the hub.</li>
<li><b>The aim:</b> the next shelf (or the ATR target).</li>
<li><b>Where you're wrong:</b> a 4-hour close back inside the hub. The breakout didn't hold, so the reason for the trade is gone.</li></ul>
<h2>Why "wrong if" comes first</h2>
<p>Most losses get big because there was no exit planned. If you decide in advance that a close back inside the hub ends the trade, you never have to decide under pressure.</p>
<h2>The time limit</h2>
<p>Breakouts that work tend to move within days. NextShelf gives each one 10 four-hour candles (about a week). If it hasn't reached the aim or failed by then, it's marked as timed out. Money stuck in a trade that's going nowhere can't be used for the next setup.</p>""",
         ["Trigger, aim and 'wrong if' are decided before the trade.",
          "A 4-hour close back inside the hub means the breakout failed.",
          "Each setup gets about a week (10 four-hour candles)."],
         ("You bought a breakout. The next 4-hour candle closes back inside the hub. What does the plan say?",
          ["Hold, it might come back", "The breakout failed: exit", "Buy more at a better price"],
          1, "A close back inside the hub is the 'wrong if' condition. The plan says the trade is over.")),
        ("dont-chase", "Don't chase: the first-half rule", 3, """
<p>The worst time to enter a breakout is after most of the move has happened. You pay a high price, the remaining reward is small, and the distance to your exit is large.</p>
<h2>The first-half rule</h2>
<p>If you missed the close, only take the trade if price is still in the <b>first half of the move</b>, between the hub edge and the halfway point to the aim. Past that, the reward left is usually smaller than the risk back to the hub. Skip it.</p>
<p>NextShelf applies the same idea to its own alerts: a breakout that has already reached its aim doesn't ring your phone, and it's marked as "skipped (too far)" in the track record.</p>
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
$10,000 account, 1% risk = $100. Entry $50, exit if back inside the hub at $48 ($2 away). $100 ÷ $2 = <b>50 shares</b>.</div>
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
        ("set-it-before-work", "Set it before work: bracket orders", 4, """
<p>You can't watch the 1:30 PM or 4:00 PM close from a meeting. One way around that is to place the order in the morning and let your broker handle it.</p>
<h2>The bracket order</h2>
<p>Most brokers offer a <b>bracket order</b>: one entry order, with a stop-loss and a profit target attached. NextShelf's "Set it before work" plan gives you all three numbers:</p>
<ul><li><b>Entry:</b> a stop order just past the hub edge.</li><li><b>Stop-loss:</b> the middle of the hub.</li><li><b>Target:</b> the next shelf.</li></ul>
<h2>The trade-off, honestly</h2>
<p>A stop order fires the moment price <b>touches</b> it, including on a wick that closes back inside the hub. NextShelf's own setup waits for the close. So this version will catch some false breakouts the alert would have skipped. It's a trade-off between convenience and accuracy.</p>
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
    ("More setups (beta)", "The other patterns on the board, read the same way: a 4-hour close, an aim, and where you're wrong.", [
        ("flags", "Flags: a sharp move, then a short pause", 3, """
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
        ("double-tops-and-bottoms", "Double tops and bottoms", 3, """
<p>A <b>double top</b> is two tests of the same high, a few sessions apart, with a real dip between them. A <b>double bottom</b> is the mirror: two tests of the same low with a bounce between.</p>
<h2>What NextShelf checks</h2>
<ul><li>A real move into the first peak (a rally into a top, a drop into a bottom).</li><li>The two peaks within about a third of an ATR of each other, 3 to 30 sessions apart.</li><li>A dip (or bounce) between them at least 2 ATR deep. The low of that dip is the <b>neckline</b>.</li></ul>
<h2>The trigger</h2>
<p>A 4-hour <b>close through the neckline</b> within about 10 sessions of the second peak. A double top breaks down; a double bottom breaks up. If price makes a new high (or low) first, the pattern is void.</p>""",
         ["Two tests of the same level, with a real dip between.",
          "The neckline is the middle low (or high).",
          "Trade the close through the neckline, not the second peak."],
         ("Where does a double top trigger?",
          ["At the second peak", "On a 4-hour close below the neckline", "On any red candle"],
          1, "The second peak is only a test. The setup confirms on a close through the neckline.")),
        ("triangles", "Triangles: a squeeze into a point", 3, """
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
<p>Some prices keep coming back. A level that price has turned at <b>three or more times</b> is support (below price) or resistance (above). NextShelf tracks those levels and forgets ones that haven't been touched in about 80 sessions.</p>
<h2>The trigger</h2>
<p>A 4-hour <b>close through a tested level</b>, after the level held for at least the previous 4 sessions. Breaking resistance is a long; breaking support is a short.</p>
<h2>Near a level</h2>
<p>The board also shows stocks <b>sitting near</b> support or resistance before they break. That's a heads-up, not a trade. The setup starts on the close through.</p>""",
         ["A level needs 3+ touches to count.",
          "Trade the close through it, after it held for a while.",
          "Near a level is a heads-up, not a trade."],
         ("A stock has bounced off $40 four times and is at $40.30. Is that a trade?",
          ["Yes, buy the bounce", "Not yet; it's a heads-up until a close through a level", "Yes, short it"],
          1, "Being near support is a heads-up. NextShelf's setup starts on a close through the level.")),
        ("gap-fade-and-rip", "Post-earnings gap fade and rip", 3, """
<p>This one uses <b>daily</b> candles. A stock gaps up at least 5% at the open, often on earnings, then <b>fades</b>: it closes well off the high, leaving a long upper wick.</p>
<h2>The trigger</h2>
<p>A daily <b>close back above the gap-day high</b> within 5 sessions. That's the "rip": buyers took back the whole fade. It's long only.</p>
<h2>Aim and wrong</h2>
<p>You're wrong on a close below the lowest low since the gap. The aim is twice the distance from entry to that low (2R). Earnings gaps can swing hard, so size small.</p>""",
         ["Gap up 5%+, then a fade with a long upper wick.",
          "Trigger: a daily close back above the gap-day high within 5 sessions.",
          "Stop: below the lowest low since the gap. Aim: 2R."],
         ("A stock gaps up 8% on earnings and closes near its low. Two days later it closes above the gap-day high. What happened?",
          ["A gap fade and rip triggered", "A double top formed", "Nothing; it's too late"],
          0, "Gap, fade, then a close back above the gap-day high within 5 sessions: that's the reclaim.")),
        ("failed-breakdowns", "Failed breakdowns and breakouts", 3, """
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

LESSONS = [(m, l) for m in MODULES for l in m[2]]


def _e(s: str) -> str:
    return html.escape(s)


def index_body() -> str:
    n = len(LESSONS)
    mins = sum(l[2] for _, l in LESSONS)
    parts = [f'<p class="lu-lede">Short, free lessons on the NextShelf method: hubs, shelves and closes, the game plan, options basics, '
             f'how to trade around a day job, and the other setups on the board. {n} lessons · about {mins} minutes in total.</p>']
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
</style>
<script>
document.querySelectorAll(".lu-quiz").forEach(q => q.querySelectorAll("button").forEach(b => b.onclick = () => {
  const ok = b.dataset.ok === "1";
  q.querySelectorAll("button").forEach(x => { x.classList.remove("ok", "no"); if (x.dataset.ok === "1") x.classList.add("ok"); });
  if (!ok) b.classList.add("no");
  const w = q.querySelector(".lu-why"); w.hidden = false; w.innerHTML = (ok ? "<b>Right.</b> " : "<b>Not quite.</b> ") + q.dataset.why;
}));
</script>"""


def render(slug: str | None = None) -> str | None:
    shell = (STATIC / "page.html").read_text()
    if slug:
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
