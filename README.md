# Hub Scanner

A place where trading set ups are found.

*Shelf to shelf: find the hub, trade the break to the next shelf.*

A watchlist website plus a Telegram bot that scans your tickers after every 4H close and alerts you when:

- ⚠️ **a breakout is about to print** (~15 min before the 4H close): price is trading outside a hub that hasn't broken out yet, so you can enter at the close
- 🟦 **a new hub forms**: 4H closes held inside a tight range (K × ATR) for 4+ sessions
- 🚀 / 🔻 **price breaks out**: the first 4H close above or below the hub, with a plan (entry, exit level, target) and the ticker's past results
- ✅ **follow-through**: a 4H close reaches the **next shelf** before closing back inside the hub

**Shelves:** a shelf is an earlier hub zone. When price breaks out of the current hub, the target is the nearest shelf in that direction: its bottom edge for an upside breakout, its top edge for a downside one. To count, a shelf must sit at least 0.5 × ATR beyond the hub edge. If there's no shelf in range, the target falls back to 1 × ATR, and the alert says so. Each ticker card shows the shelf above and below the current hub and draws them on its mini chart.
- ↩️ **snap-back**: a 4H close comes back inside the hub first
- ⏸ **stalled**: neither happens within 10 candles

4H candles follow regular hours: **9:30–1:30** and **1:30–4:00 ET**. On weekdays, the **pre-close checks** run at **1:15 PM** and **3:45 PM ET**, using Alpaca's free real-time IEX price. The **scans** run at **1:47 PM** and **4:17 PM ET**; the extra minutes allow for the 15-minute delay on Alpaca's free full-market data.

**How to use it to trade the follow-through:** the pre-close warning is your heads-up. If price is still outside the hub as the candle closes, that close is the breakout, and the follow-through is the move you're trying to catch. The breakout alert confirms it after the fact and includes the plan. Check the ticker's backtest first: "per trade" shows the average result of entering at every breakout close, exiting at the target or on a close back inside the hub. If it's negative, the setup hasn't paid on that ticker.

Each event is alerted once. A newly added ticker starts quiet: you get alerts for events from the next candle onward, not its whole history.

---

## Accounts and plans

People sign up with **email and password** or **Google**. Each person has their own watchlist, Telegram chat and phone number.

- **The first account created becomes the admin** (Pro, plus admin tools). It inherits the existing watchlist and Telegram chat. To make another email an admin, set `ADMIN_EMAIL`.
- **Free:** up to 3 tickers (`FREE_TICKER_LIMIT`), Telegram breakout alerts, the Hub Board's "Inside a hub" and "Just finished" lists, the track record and charts.
- **Pro:** unlimited tickers, pre-close warnings, the full Hub Board (live breakouts and stocks at the edge), phone calls and backtests.
- **Admin:** Account → Members lists everyone and lets you switch a person between Free and Pro (until online payments are added). Paper trading and the "Scan now" buttons are admin-only.
- **Telegram:** each person taps **Connect Telegram** under Account, which opens the bot with a private link code.

### Google sign-in setup (about 10 minutes)
1. Go to **console.cloud.google.com**, create a project (e.g. "Hub Scanner"), then open **APIs & Services → OAuth consent screen**. Choose **External**, fill in the app name and your email, and save.
2. Go to **Credentials → Create credentials → OAuth client ID**, with **Application type: Web application**.
3. Under **Authorized redirect URIs**, add `https://YOUR-SITE.onrender.com/auth/google/callback`.
4. Copy the **Client ID** and **Client secret** into Render as `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, and set `APP_URL=https://YOUR-SITE.onrender.com`.
5. While the consent screen is in "Testing", only test users you add can sign in. Click **Publish app** when you're ready for everyone.

### Password-reset emails (Resend, free tier)
1. Sign up at **resend.com**, add and verify your domain (or use their test sender while trying it out), and create an API key.
2. Set `RESEND_API_KEY` and `EMAIL_FROM="Hub Scanner <noreply@yourdomain.com>"` in Render.

Without Resend, reset links are written to the Render logs instead of being emailed, which is handy for testing.

---

## Pages

- **Landing page** (`/`): the public homepage with the headline, product preview, how it works, features, pricing and FAQ. Signed-in members skip it and go straight to the app.
- **Watchlist** (`/app`): your tickers, with a summary, mini chart and price ladder for each, plus recent alerts.
- **Hub Board** (`/app#board`): every Nasdaq-100 stock plus your watchlist, scanned a few minutes after each 4H close (1:52 and 4:22 PM ET). It's grouped into *Breaking out now*, *At the edge of a hub*, *Inside a hub* and *Just finished*, and you can tap **+ Watch** to add any stock to your list. To scan your own set of stocks instead, set `BOARD_UNIVERSE=AAPL,MSFT,...`.
- **Track record** (`/app#record`): every breakout the board detects from the day it first runs, with its result and return. It's logged automatically, and nothing is removed.
- **Ticker page** (`/t/NVDA`): a full 4H candlestick chart with every hub drawn, the shelves above and below, and breakout markers labelled hit / failed / timed out, plus that stock's past results.
- **Paper trades** (`/app#paper`): the automatic paper-trading journal.

---

## What you need (all free to start)

| | What | Where |
|---|---|---|
| 1 | Telegram bot token | Telegram → search **@BotFather** → `/newbot` → follow the prompts → copy the token |
| 2 | Market data keys | Sign up at **alpaca.markets** → Paper trading dashboard → **API Keys** → Generate. Copy the key and the secret. Paper-account keys work for market data. |
| 3 | A GitHub account | github.com |
| 4 | Hosting | **Railway** (railway.app) is recommended. It must run 24/7, so avoid free tiers that sleep. Check Railway's current pricing; it's typically a few dollars a month. |

---

## Deploy on Railway (about 20 minutes)

1. **Put the code on GitHub.** Create a new **private** repository and upload every file from this folder, keeping `static/index.html` inside `static/`. Don't upload a `.env` file.
2. **Create the app.** In Railway, go to **New Project → Deploy from GitHub repo** and pick your repo. Railway detects Python and uses the `Procfile` start command.
3. **Add a volume** so your watchlist survives redeploys. In the service, open **Settings → Volumes → Add volume** and set the mount path to `/data`.
4. **Set the variables.** In the service, open **Variables → Raw editor** and paste:
   ```
   DATA_PROVIDER=alpaca
   ALPACA_API_KEY=your-key
   ALPACA_SECRET_KEY=your-secret
   ALPACA_FEED=sip
   TELEGRAM_BOT_TOKEN=your-bot-token
      DATA_DIR=/data
   ```
5. **Get a web address.** Open **Settings → Networking → Generate domain**.
6. **Open the site and create your account.** The first account becomes the admin. Then add tickers such as NVDA, AAPL and TSLA.
7. **Link Telegram.** On the site, open **Account → Connect Telegram** and press **Start** in the bot.

Keep the app at **one instance** (Railway's default). Two copies would double-poll Telegram.

---

## Automatic paper trading (forward test with fake money)

When it's on, the app trades every setup in your **Alpaca paper account**, using the same rules as the backtest:

| When | What happens |
|---|---|
| ~2 min before each 4H close (1:28 and 3:58 PM ET) | **Entry**: if price is outside a hub that hasn't broken out yet, buy (or short a breakdown) about `PAPER_TRADE_USD` worth at market. There's one trade per hub, and none if price is already at the target. A **limit order** is placed at the next shelf. |
| Every 5 minutes in market hours | If the shelf limit order filled: **follow-through**, trade closed. |
| ~2 min before each 4H close | **Snap-back**: if price is back inside the hub, close at market. **Time limit**: after 10 4H candles, close at market. |

Every open and close is sent to Telegram (🧪) and shown under **Paper trades** on the site, with win rate and P&L.

**Safety:** the trading address is hard-coded to Alpaca's *paper* endpoint (`paper-api.alpaca.markets`), so this app cannot place real-money orders even with live keys. Use the API keys from your **paper** account. In `demo` mode, or without Alpaca keys, a built-in simulator fills at the latest price instead.

**Turn it on:** use the switch on the site, send `/paper on` in Telegram, or set `PAPER_TRADING=on`. `/paper` shows the journal.

Things to know:
- Entries use the live price ~2 minutes before the close as a stand-in for the close. A late move can mean a trade whose candle didn't actually close outside the hub. It's then closed at the next check if price is back inside.
- Shorts need the stock to be shortable in your paper account. Set `PAPER_ALLOW_SHORT=off` for longs only.
- Paper fills are optimistic: no real queue, and no borrow fees for shorts.
- If you close a paper position yourself in Alpaca, also cancel its open limit order. The app's journal won't know about manual changes.

---

## Phone-call alerts (Twilio)

Your phone rings and a voice reads the alert twice. For example: *"Hub scanner. N. V. D. A. is trading above its hub, about 15 minutes before the 4 hour close. Price $82.37. Hub edge $82.04. Next shelf $84.30."* Calls are for the alerts you act on: **pre-close warnings** and **breakouts/breakdowns**. Everything else stays in Telegram. You won't get a call for a chase (price already at the target), and after downtime the app only calls about the newest candle, so you don't get a burst of old calls.

Setup (about 10 minutes):
1. Sign up at **twilio.com** and verify your own phone number.
2. In the Twilio console, **buy a phone number** with Voice capability. It costs a small monthly fee, and calls are billed per minute; check Twilio's current US pricing.
3. From the console home page, copy your **Account SID** and **Auth Token**.
4. Add these variables in Railway:
   ```
   TWILIO_ACCOUNT_SID=ACxxxxxxxx
   TWILIO_AUTH_TOKEN=xxxxxxxx
   TWILIO_FROM=+1XXXXXXXXXX     # your Twilio number
   CALL_TO=+1XXXXXXXXXX         # your cell
   ```
5. Send `/testcall` to your Telegram bot. Your phone should ring.

On a Twilio **trial** account, calls only reach numbers you've verified and start with a short trial message (press any key). Upgrading removes that. Save your Twilio number as a contact (e.g. "Hub Scanner") so the call isn't flagged as spam, and allow it through Do Not Disturb.

`CALL_ON` controls which alerts ring: any of `preclose`, `breakout`, `follow-through`, `snap-back` (default `preclose,breakout`).

---

## Telegram commands

```
/add NVDA TSLA     watch tickers
/remove NVDA       stop watching
/list              watchlist + current status
/status NVDA       one ticker
/scan              scan now
/precheck          run the pre-close check now
/paper             paper-trading journal
/paper on | off    turn automatic paper trading on or off
/testcall          test the phone-call alert
/backtest NVDA     ~2 years of breakout stats
/help
```

---

## Try it on your computer first (optional)

```bash
pip install -r requirements.txt
cp .env.example .env          # then set DATA_PROVIDER=demo to use fake prices, no keys needed
uvicorn app:app --reload
```
Open http://localhost:8000. `demo` mode makes up prices so you can click around. Switch to `alpaca` for real data.

---

## Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `MIN_SESSIONS` | 4 | Minimum hub length in sessions (2 × 4H candles each) |
| `HUB_K` | 1.5 | Hub tightness: all closes within K × 4H ATR(14). Lower is stricter. |
| `HORIZON` | 10 | 4H candles a breakout gets to follow through |
| `TARGET_ATR` | 1.0 | Fallback target when there's no shelf: distance beyond the hub edge, in ATR |
| `SHELF_MIN_ATR` | 0.5 | Minimum gap between the hub edge and a shelf, in ATR. Raise it to skip shelves that are too close to be worth trading. |
| `SCAN_TIMES` | 13:47,16:17 | Scan times, ET, weekdays |
| `PRECLOSE_TIMES` | 13:15,15:45 | Pre-close warning times, ET, weekdays. Leave empty to turn it off. |
| `ALPACA_FEED` | sip | `sip` = full-market prices with a 15-minute delay. `iex` = real-time but IEX-only, so you can scan at 13:31,16:01. |
| `PAPER_TRADING` | off | Automatic paper trading (can also be switched on the site or with `/paper on`) |
| `PAPER_TRADE_USD` | 5000 | Dollar size of each paper trade |
| `PAPER_ALLOW_SHORT` | on | Also trade breakdowns by shorting |
| `ENTRY_TIMES` | 13:28,15:58 | Paper entry/exit checks, ET, weekdays |
| `DATA_PROVIDER` | alpaca | `alpaca`, `yfinance` (free, unofficial, can break) or `demo` |

## Notes

- On holidays and half days there's no new candle, so nothing new is alerted.
- Earnings gaps can create or end a hub overnight. Treat those alerts with care.
- The pre-close price comes from IEX, one exchange. It can differ by a few cents from the official close, so a hub edge that close is a judgment call.
- "Per trade" assumes you fill exactly at the breakout close and at the target, with no slippage or commissions. Real results will be a little worse.
- The backtest uses about 2 years of 4H candles per ticker. That sample is small, so look at several tickers before trusting a pattern.
- This tool describes past price behaviour. It is not financial advice.

## Files

```
app.py            website, API, scheduler, starts the bot
scanner.py        scanning, alert wording, Telegram commands
engine.py         4H candles, hub/breakout/outcome logic, backtest stats
data.py           Alpaca / yfinance / demo price data
telegram_bot.py   sending messages and reading commands
phone.py          phone-call alerts (Twilio)
board.py          Hub Board, track record, chart data
universe.py       the stocks the Hub Board scans (Nasdaq-100)
static/ticker.html  ticker chart page (charts by TradingView Lightweight Charts, Apache-2.0)
auth.py           accounts: email/password, Google sign-in, sessions, password reset
notify.py         sends each alert to the users watching that ticker
static/login.html sign-in / sign-up page
static/landing.html public homepage
paper.py          automatic paper trading (Alpaca paper account or simulator)
db.py             SQLite storage (watchlist, alerts, paper trades)
static/index.html the watchlist page
```
