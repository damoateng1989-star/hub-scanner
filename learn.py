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
<p>A hub is defined by closes, not by every tick. When the 4-hour candles keep <b>closing inside the same narrow band for at least 5 sessions</b>, that band is the hub. The top of the band is the <b>hub top</b>; the bottom is the <b>hub bottom</b>.</p>
<h2>Why hubs matter</h2>
<p>A pause stores up decisions. Traders who wanted to buy are waiting for proof; traders who wanted to sell are waiting too. When price finally <b>closes outside</b> the range, a lot of those decisions get made at once, which is why moves out of a tight range can be quick.</p>
<p>That's not a guarantee. Plenty of hubs break, wobble and slide straight back in. That's why NextShelf pairs every breakout with a plan for being wrong.</p>
<h2>What to do while a stock is inside its hub</h2>
<p><b>Nothing.</b> Inside the hub, price is noise: small moves up and down that go nowhere. Trading inside it is how most people lose money while "waiting for the move." Let the stock show its hand first.</p>""",
         ["A hub is a tight range where 4-hour candles keep closing for 5+ sessions.",
          "Moves out of a hub can be fast because many traders were waiting.",
          "Inside the hub there's no trade. Wait for a close outside it."],
         ("A stock has closed inside $50–$51 for six sessions. What's the trade?",
          ["Buy near $50, sell near $51", "No trade until a 4-hour close outside the range", "Short it, because it can't go higher"],
          1, "Inside the hub, price is noise. The setup only starts on a 4-hour close outside it.")),
        ("why-4-hour-candles", "Why NextShelf uses 4-hour candles", 3, """
<p>A candle is just a summary of price over a stretch of time: where it opened, where it closed, and its high and low. The stretch you pick changes what you see.</p>
<ul><li><b>1-minute and 5-minute candles</b> are mostly noise. They demand constant attention and punish anyone with a job.</li>
<li><b>Daily candles</b> are calm, but signals arrive slowly and stops have to be wide.</li>
<li><b>4-hour candles</b> sit in between: clear enough to ignore the noise, fast enough to catch a move within days.</li></ul>
<h2>Only two decision points a day</h2>
<p>US stocks trade from 9:30 AM to 4:00 PM Eastern. NextShelf splits that into two 4-hour candles: one that closes at <b>1:30 PM</b> and one that closes at <b>4:00 PM</b>. Those two closes are the only moments a NextShelf signal can confirm.</p>
<p>That's the whole point for someone at work: you don't need to watch the screen all day. NextShelf warns you about 15 minutes before each close if a stock is outside its hub, and confirms the result after the close.</p>""",
         ["4-hour candles filter out most intraday noise.",
          "There are two closes a day: 1:30 PM and 4:00 PM Eastern.",
          "Signals only confirm at those closes, so you don't need to watch all day."],
         ("When can a NextShelf breakout be confirmed?",
          ["Any minute price trades above the hub", "Only at a 4-hour close (1:30 PM or 4:00 PM ET)", "Only at the market open"],
          1, "The signal is a 4-hour close outside the hub, and those closes happen at 1:30 PM and 4:00 PM.")),
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
    ("Trading the setup", "Turning a signal into a plan you can stick to.", [
        ("the-game-plan", "The game plan: aim, and where you're wrong", 3, """
<p>Every NextShelf setup comes with the same three parts. Knowing all three <i>before</i> you trade is what turns a signal into a plan.</p>
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
<p>A stop order fires the moment price <b>touches</b> it, including on a wick that closes back inside the hub. NextShelf's own signal waits for the close. So this version will catch some false breakouts the alert would have skipped. It's a trade-off between convenience and accuracy.</p>
<h2>Keep it fresh</h2>
<p>Use a <b>day order</b> and set it again each morning. A forgotten order can fill weeks later, long after the setup changed.</p>""",
         ["A bracket order = entry + stop-loss + target in one go.",
          "Stop entries fire on a touch, so they catch some false breakouts.",
          "Use day orders and reset them each morning."],
         ("Why can a before-work stop order fill on a false breakout?",
          ["Stop orders wait for the close", "Stop orders fire when price touches them, even on a wick", "Brokers ignore stop orders"],
          1, "A stop order triggers on a touch. NextShelf's alert waits for the 4-hour close.")),
        ("reading-the-track-record", "Reading a track record honestly", 3, """
<p>The Results page logs every signal NextShelf finds, with its outcome. Nothing is removed. That's useful only if you read it carefully.</p>
<h2>Win rate isn't everything</h2>
<p>A method can win 40% of the time and still make money if the wins are bigger than the losses. Another can win 70% and lose money if the losses are huge. Look at the <b>average per signal</b> next to the win rate.</p>
<h2>Small samples lie</h2>
<p>A stock with 2 signals can show 100% or 0% by pure luck. Trust patterns that hold up over many signals, and use the minimum-signals filter on the win-rate ranking.</p>
<h2>Past results aren't a promise</h2>
<p>Markets change. A setup that worked in a calm market can struggle in a wild one. The track record tells you how things went, not how they will go.</p>""",
         ["Read win rate together with the average per signal.",
          "Ignore results from just a handful of signals.",
          "A track record describes the past, not the future."],
         ("Setup A wins 70% but averages -0.4% per signal. Setup B wins 45% and averages +0.8%. Which made money?",
          ["Setup A", "Setup B", "Both"],
          1, "Average per signal is what you'd have made or lost. B's wins were bigger than its losses.")),
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
]

LESSONS = [(m, l) for m in MODULES for l in m[2]]


def _e(s: str) -> str:
    return html.escape(s)


def index_body() -> str:
    n = len(LESSONS)
    mins = sum(l[2] for _, l in LESSONS)
    parts = [f'<p class="lu-lede">Short, free lessons on the NextShelf method: hubs, shelves and closes, the game plan, options basics, '
             f'and how to trade around a day job. {n} lessons · about {mins} minutes in total.</p>']
    k = 0
    for mi, (title, blurb, lessons) in enumerate(MODULES, 1):
        rows = []
        for slug, t, m, *_ in lessons:
            k += 1
            rows.append(f'<a class="lu-l" href="/learn/{slug}"><span class="lu-n">{k:02d}</span><span class="lu-t">{_e(t)}</span>'
                        f'<span class="lu-m">{m} min</span></a>')
        parts.append(f'<section class="lu-mod"><div class="lu-mh"><small>Module {mi}</small><h2>{_e(title)}</h2><p>{_e(blurb)}</p></div>'
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
