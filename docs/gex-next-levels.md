# GEX Next Levels — spec

**Status:** Drafted Nov 18, 2026 (~5:35 PM EST), at the user's direction ("Yes" to drafting the spec, after "How can we make a formula that works like this?"). Updated Nov 18 ~5:39 PM EST: heat-map row marking for the two next levels added at the user's direction.
**Source:** PANW, Wed Nov 18, 2026. The stock beat and raised after the close (adjusted EPS $1.05 vs $0.97 expected, revenue $3.44B vs $3.31B, full-year guide lifted to $14.52–14.57B) and ran to $376 after hours. The GEX grid the user was looking at had already marked the landing zones: the Nov 20 expiry 375 strike held 438.4K of gamma — the biggest near-term wall — and the 330 strike held 1,776.4K on Nov 27. The stock first dipped to ~$324 after the print (into the 320–330 zone) and then ripped to $376, a dollar above the 375 wall. The user asked how to make a formula that works like that.
**Scope:** Display only. No trading, no new alerts, no record or Lab changes. No new data source — the levels come from the free Cboe chain NextShelf already reads. The formula is the GEX math in `gamma.py`, plus three pieces it does not have yet: near-term walls, next levels, and a regime flag.

## 1. What it is

Three additions to the GEX already on the ticker page and the shelf card:

1. **Near-term walls** — a second set of walls computed on the NEAREST expiry with chain data (weeklies included, no 7-day floor), alongside the existing monthly walls. The PANW 375 wall was the Nov 20 expiry, 2 days out — the monthly rule skips it, so the level that mattered most is one the card never shows.
2. **Next levels** — the nearest significant gamma level ABOVE the current price and the nearest BELOW it: the nearest strike in each direction where |net GEX| is a local max and at least 25% of the largest |net GEX| on that expiry (25% is the PROPOSED threshold — his call, §10). This is the "where does it stop next" number.
3. **Regime flag** — a label, not a signal: whether the current price sits in SHORT gamma (dealers short gamma, moves accelerate) or LONG gamma (dealers long gamma, moves dampen and price pins), read off the flip.

It is a map of where the money sits, not a forecast: every level is a strike with real open interest behind it, and the formula says where a move is likely to stop, never which way it goes. The print supplied PANW's direction; the formula only supplied the landing zone.

## 2. What it is not

- **Not a prediction.** Nothing in the chain knows earnings, a beat, or a guide raise. If the print had missed, the same formula points down at the big put-side concentrations (PANW: 330 and 300) instead of up at 375.
- **Not a trade signal.** The regime flag is a label. It does not change a grade, does not fire an alert, and does not cap a target. The wall-in-path target cap in `gamma.attach()` keeps using the monthly walls (§8).
- **Not a replacement for the monthly walls.** The monthly walls, the shelves ladder, and the heat map stay exactly as they are. The near-term walls are a second, shorter-horizon read alongside them.
- **Not a Lab change.** The Lab already measures the monthly walls (Patch 149, T1–T4). Whether the near-term walls join the Lab log is a follow-up decision for him (§10), not part of this spec.

## 3. Inputs (all already in-house)

| Input | Source | What it supplies |
|---|---|---|
| Options chain | `gamma._fetch_contracts` — the free Cboe delayed chain (`cdn.cboe.com/api/global/delayed_quotes/options/{SYM}.json`), slimmed by `gamma._slim` to (expiry, is_call, strike, gamma, oi, volume); cached once per symbol per day (`_CHAIN_CACHE`, `gamma.py` L44); fail-open | Gamma and open interest per strike per expiry — the only raw inputs the formulas need |
| Spot price | The same spot `GET /api/gamma/{symbol}` already resolves (`app.py` L2246–2262: live price, falling back to the board row's last close) | The price the walls and next levels are measured from |
| Monthly walls | `gamma.walls()` (`gamma.py` L246–286) | The existing call wall / put wall / flip on the nearest monthly expiry ≥ 7 DTE — the baseline the near-term walls sit next to |
| Grid | `gamma.grid()` (`gamma.py` L410–500) | The 4 nearest expiries including 0DTE/weeklies, per-strike net GEX — the nearest-expiry column is the same data the next levels read |

The applier must verify the exact field names against the live code before using them (the live deploy is ahead of GitHub main in places). Do not invent a field that is not in the live code.

## 4. The formulas

### 4.1 Base: per-strike GEX (already live — restated, not changed)

For each strike K on an expiry, from the chain's own gamma and open interest:

- Call GEX(K) = Γ_call(K) × OI_call(K) × 100 × spot
- Put GEX(K) = −Γ_put(K) × OI_put(K) × 100 × spot
- Net GEX(K) = Call GEX(K) + Put GEX(K)

(`gamma.py` L8–11: `walls()` leaves the spot scaling out because it does not change the ranking; `shelves()`/`grid()` multiply by spot for dollar notional, L354/L468. Either way the ranking — and every formula below — is identical. Values are displayed in K, as the heat map already does.)

Existing definitions, unchanged:

- **Call wall** = the strike with the largest call GEX above spot; **put wall** = the strike with the largest put GEX below spot (`_compute`, L164–180).
- **Flip** = the strike where cumulative net GEX, added up from low strikes to high, crosses from negative to positive (`_compute`, L182–192).
- **Strongest pull** = the strike with the largest absolute net GEX (Patch 141: largest absolute TOTAL across the expiries shown).

### 4.2 Near-term walls

A second call of the same wall math, on a different expiry:

- **Expiry:** the NEAREST expiry with chain data. No 7-day floor, weeklies included, 0DTE included when the chain has it. (The monthly rule is `swing = [e for e in by_exp if (e - today).days >= 7]` then the nearest monthly — `_compute` L154–158. The near-term rule drops both filters: `exp_nt = min(by_exp)`.)
- **Math:** identical to `_compute` on that expiry — call wall = max call GEX, put wall = max put GEX, flip = the cumulative net-GEX zero crossing — over strikes within ±20% of spot (`_BAND`, L52). The build adds an expiry parameter to `_compute` (or a sibling function reusing it); it does NOT change `_compute`'s default, and `walls()` keeps returning the monthly walls.
- **Output fields:** `call_wall_nt`, `put_wall_nt`, `flip_nt`, `expiry_nt` (ISO date). Each is `null` when the chain has no data for that side (e.g. `flip_nt` is `null` when the cumulative net GEX never crosses zero on that expiry — see the PANW worked example, §7).
- **Decay caveat:** near-expiry gamma moves fast. A 0–2 DTE wall can shift, shrink, or vanish as spot moves and time passes, and the chain itself is 15 minutes delayed (§6). So the near-term walls are recomputed from the cached chain at the CURRENT spot on every call — exactly as `walls()` already does (L246–256) — and the response always carries `as_of` (§4.5). When the near-term expiry passes, the walls roll to the next expiry automatically: the nearest expiry with chain data is always the one used.
- **What it is for:** the magnet horizon. The monthly wall is the swing shelf; the near-term wall is where the next move stops. PANW: the monthly wall never showed 375; the near-term wall did.

### 4.3 Next levels

Computed on the NEAREST expiry (the same expiry as §4.2 — proposed basis, §10):

1. Take the nearest expiry's per-strike net GEX curve (net GEX per strike, §4.1).
2. Let `M` = the largest |net GEX| on that expiry. The significance bar is `0.25 × M` — **25%, PROPOSED, his call** (§10).
3. A strike is a **significant node** when it is a local max of |net GEX| — |net GEX| greater than both adjacent strikes on the curve — AND |net GEX| ≥ 0.25 × M. (The local-max test already exists in code: `_board_grid` marks TB rows exactly this way, `gamma.py` L584–594, `b > a and b > c` on |total|.)
4. **Next level up** = the nearest significant node strictly above spot. **Next level down** = the nearest significant node strictly below spot. A strike essentially at the price (within rounding) counts on its own side and is shown with its distance, which will read ≈ 0%.
5. Either side is `null` when no strike in that direction qualifies — e.g. price has run past every significant node on the expiry. That is itself the read: there is no significant wall left in that direction on the nearest expiry.
6. Each level carries its distance from spot: `distance_pct = (strike − spot) / spot × 100`, one decimal.

Why the 25% bar: without it, "next level" is just the nearest strike — every $2.50 step qualifies and the answer is noise. With it, only strikes carrying real positioning count. The bar is a share of the expiry's own biggest node, so it scales across tickers and across quiet vs crowded chains.

### 4.4 Regime flag

Read off the near-term flip (`flip_nt`, §4.2):

- **Spot below the flip → "SHORT γ"** (short gamma): dealers are net short gamma at this price, so their hedging buys into strength and sells into weakness — moves accelerate in the direction they start.
- **Spot above the flip → "LONG γ"** (long gamma): dealers are net long gamma, so their hedging sells strength and buys weakness — moves dampen and price pins near the flip.
- **No flip on the expiry (`flip_nt` is `null`) → fall back to the sign of the TOTAL net GEX on that expiry:** negative total → SHORT γ; positive total → LONG γ. (A flip exists only when the cumulative curve crosses zero; a one-sided chain never crosses. PANW Nov 20 is exactly this case — §7.)
- The flag is a label next to the next-levels line, e.g. `SHORT γ — moves accelerate` / `LONG γ — moves dampen, price pins`. It is not a trade signal, not a grade input, and not an alert condition.

The flag follows the next-levels basis decision (§10): if the basis moves to the monthly expiry, the flag reads off the monthly flip instead.

### 4.5 Output schema

No new endpoint. `GET /api/gamma/{symbol}` (`app.py` L2236–2270) already returns `dict(symbol, walls, shelves, grid)`; it gains one key, `next_levels`:

```json
{
  "symbol": "PANW",
  "spot": 339.92,
  "as_of": "2026-11-18T16:00:00-05:00",
  "chain_as_of": "2026-11-18T09:40:00-05:00",
  "walls": { "call_wall": 380.0, "put_wall": 300.0, "flip": 345.0, "expiry": "2026-12-18" },
  "next_levels": {
    "expiry_nt": "2026-11-20",
    "call_wall_nt": 375.0,
    "put_wall_nt": 300.0,
    "flip_nt": null,
    "total_net_nt": -96500.0,
    "next_up": { "strike": 340.0, "gex": 240800.0, "distance_pct": 0.0 },
    "next_down": { "strike": 300.0, "gex": -423300.0, "distance_pct": -11.7 },
    "regime": "short"
  }
}
```

- `walls` / `shelves` / `grid` are byte-for-byte what the endpoint returns today. The monthly `walls` values in the example above are illustrative — only the `next_levels` block is specified here.
- `as_of` is the time the payload was computed (the spot's time); `chain_as_of` is when the chain behind it was fetched (§6). Both are shown to the reader.
- `gex` is the per-strike net GEX in dollars (notional, × spot — the same units as `grid()`), signed: positive for call-side nodes, negative for put-side nodes.
- `regime` is `"short"` or `"long"`.
- `next_levels` is `null` when the chain cannot be read (fail-open, §6) — the endpoint still returns `walls`/`shelves`/`grid` as today, and the frontend hides the next-levels line rather than showing invented levels.

## 5. Where it surfaces

Three places, display only — two show the line, the third marks the levels on the heat map:

1. **Ticker page, GEX shelves section** — under the shelves ladder, one line:
   `Next levels ↑ $340.00 (+0.0%) · ↓ $300.00 (−11.7%) · Nov 20 · SHORT γ`
   The expiry shown is `expiry_nt`; the regime flag sits at the end of the line. When a side is `null`, that side reads `—` with no number, e.g. `↑ — (no significant wall above on Nov 20)`.
2. **Shelf card, GEX context line** — the same next-levels line appended to the GEX context the card already shows (the `gamma.line()` wall/flip/strongest-pull line, `gamma.py` L1086–1144). The monthly wall line is unchanged; the next-levels line follows it.
3. **Ticker page, heat map** — the two next-level strike rows are marked so they jump out of the grid: the row is outlined (the app's existing border/accent tokens, no new colours) and the strike cell carries a small `NEXT ↑` / `NEXT ↓` tag. The tag names the direction only; the expiry is the one on the next-levels line (item 1). The grid's values, ranking, and columns do not change — only the two rows gain a marker. When a side is `null`, no row is marked on that side. If a next-level strike is not among the heat map's displayed rows (top 25 by total |net GEX|), it is simply not marked — the row is never forced into the grid.

- **No new navigation, no new tab, no new page.** The GEX board (Discover) is unchanged in v1 — the board already shows the full grid; the next-levels line is a per-ticker read, not a board feature.
- **No Pro-gate change.** The line rides on the existing `GET /api/gamma/{symbol}` payload, so whatever gate that endpoint has today applies unchanged. (The endpoint takes the signed-in `User` dependency today, `app.py` L2238 — the build does not tighten or loosen it.)
- **No polling change.** The line is computed with the existing gamma fetch on page load; it does not add a refresh loop. Between 4H closes it is static except for the spot it is measured from, which the endpoint already resolves live.

## 6. Accuracy / grounding rules

- **Levels come only from the live chain.** A strike appears in the next-levels line only if its gamma and open interest are in the Cboe chain for that symbol. No level is ever invented, interpolated, or carried over from another ticker or another day.
- **The Cboe chain is delayed ~15 minutes.** The values are the delayed chain, not the live tape. The line shows `chain_as_of` so the reader can see the lag.
- **The chain is cached once per symbol per day** (`_CHAIN_CACHE`, `gamma.py` L44; yesterday's chains are dropped, L236–240). Watchlist chains are force-refreshed by the 5-minute snapshot job (`snapshot_symbol`, L772–824; scheduled `app.py` L436–442). A non-watchlist ticker's chain is the morning fetch until the next day — the `chain_as_of` timestamp is what makes that visible.
- **Stale-chain note.** When `chain_as_of` predates the current session's open (9:30 AM ET), the line carries a short stale note, e.g. `chain from 09:40 — levels from the morning fetch`. When the fetch fails, the payload is `null` and the line is hidden — never a stale level shown as fresh.
- **Never a forecast.** The line names levels, not a direction. It never says price "will" reach a level, never picks a side, and never attaches a probability. The regime flag describes dealer positioning at the current price; it is not a call.
- **Fail-open throughout.** Any fetch/parse failure returns `null` for the `next_levels` block and the rest of the page renders as today — the same fail-open rule as the module docstring (`gamma.py` L3–5).

## 7. Worked example — PANW, Wed Nov 18, 2026

Chain values below are from the Nov 20 / Nov 27 columns of the GEX grid the user was looking at (morning snapshot, values in K, notional). The formula recomputes from the current chain at the current spot; the example applies the formulas to that snapshot at two spots — the close and the after-hours print — so the reader can see the line move with price. The monthly walls are not in this example: the Nov 20 expiry is 2 days out, below the 7-day floor, so `walls()` never shows it.

**Nov 20 expiry, per-strike net GEX (K):** 375: +438.4 · 350: +189.7 · 340: −240.8 · 330: −43.3 · 320: −100.2 · 300: −423.3 · 290: −247.4 (other strikes smaller). Largest |net GEX| M = 438.4K (375). Significance bar = 25% × 438.4K = 109.6K. Qualifying local maxima (greater than both adjacent strikes, ≥ 109.6K): **375, 350, 340, 300, 290**. Note 320 is a local max (100.2K beats its neighbours) but fails the bar — it is not a next level.

**At the close, spot $339.92 (+9.9% on the day):**
- `call_wall_nt` = **$375** (438.4K) · `put_wall_nt` = **$300** (423.3K) · `expiry_nt` = Nov 20.
- `flip_nt` = **null** — the cumulative net GEX on Nov 20 never crosses zero (it ends at ≈ −96.5K). Fallback: total net GEX is negative → regime **SHORT γ**.
- `next_up` = **$340** (+0.0% — the close is sitting on the 340 node) · `next_down` = **$300** (−11.7%).
- Line reads: `Next levels ↑ $340.00 (+0.0%) · ↓ $300.00 (−11.7%) · Nov 20 · SHORT γ`

**After hours, spot $376:**
- `next_up` = **null** — no qualifying node above $376 on Nov 20 (377.5 carries 0.0K). `next_down` = **$375** (−0.3% — price is sitting on the wall).
- Line reads: `Next levels ↑ — · ↓ $375.00 (−0.3%) · Nov 20 · SHORT γ`
- The day's arc in the same terms: the post-print dip to ~$324 sat between the 340 node above and the 300 node below; the Nov 27 330 strike (1,776.4K) is the largest single-expiry concentration on the board — the strongest pull on the all-expiry totals basis — and the dip landed just under it; the rip then ran to the 375 wall and stopped $1 above it. Heat map: at the close the 340 row carries `NEXT ↑` and the 300 row `NEXT ↓`; after hours the 375 row carries `NEXT ↓` and no row carries `NEXT ↑`.

## 8. What it does NOT change

- `walls()`, `shelves()`, `grid()`, `_board_grid()`, `board()`, `flow_proxy()`. None of their math, expiry selection, or return shapes change. The near-term computation is additive.
- `gamma.attach()` and `gamma.line()`. The wall-in-path target cap and the alert/paper GEX line keep using the monthly walls. The next-levels line is display-only and is not appended to alerts.
- The engine, the triggers/shelves, the grades, the alerts, the paper trader, the record, and the Lab. None of those are touched.
- The GEX board (Discover), the shelves ladder, and the snapshot store. The heat map's data is unchanged — same `grid()` values, ranking, and columns — the only change is the two next-level row markers (§5 item 3). No new snapshot, no new scheduled job, no new DB table, no new endpoint.
- Patch 1 (earnings blackout for alerts). This is not an alert, so Patch 1 does not apply to it.
- No new data vendor, no new API key, no new env var. The Cboe chain is already fetched; the near-term read reuses the same cached chain, so it adds no Cboe requests.

## 9. Acceptance / verification

1. On PANW (Nov 20 expiry live in the chain), the `next_levels` block returns `expiry_nt` Nov 20, `call_wall_nt` $375, `put_wall_nt` $300, `flip_nt` null, regime `short`, and the next levels from §7 at the live spot — recomputed from the chain, not hardcoded to the example.
2. On a ticker whose nearest expiry is a monthly (e.g. the nearest expiry IS the monthly), the `_nt` walls equal the monthly walls — the two reads agree, they do not contradict.
3. On a ticker with no chain (fetch fails), `next_levels` is `null`, the endpoint still returns `walls`/`shelves`/`grid` as today, and the frontend hides the line.
4. The ticker page shows the next-levels line under the shelves ladder with the expiry and regime flag; the shelf card shows the same line in its GEX context. A side with no qualifying node renders as `—`, not as a fabricated number.
5. The heat map outlines the next-up and next-down strike rows with the `NEXT ↑` / `NEXT ↓` tags; a side with no qualifying node marks no row on that side; the grid's values and ordering are unchanged.
6. The stale-chain note appears when `chain_as_of` predates the 9:30 AM ET open; the `as_of` / `chain_as_of` times are visible on the line.
7. No new alert fires, no new record entry is written, the paper trader is untouched, and the monthly wall line (alerts, card, `attach()` cap) is byte-for-byte unchanged.
8. No level on the line is a number that is not in the chain for that symbol on that expiry.

## 10. Open decisions (his call)

1. **The 25% threshold.** 25% of the expiry's largest |net GEX| is the proposed bar for "significant". Lower (15%) names more levels and more noise; higher (50%) names only the one or two biggest walls. The PANW example is the calibration case: at 25%, 320 (100.2K) is excluded and 340/350/375/300/290 qualify.
2. **Nearest-expiry vs monthly basis for the next levels.** Proposed: the nearest expiry, because that is the magnet horizon — the level that catches the next move. The alternative: compute the next levels on the monthly expiry (the same basis as `walls()`/`shelves()`), which is slower-moving and matches the swing shelves, but misses the wall that mattered on PANW. The regime flag follows whichever basis is picked.
3. **Whether the near-term walls join the Lab log.** The Lab already measures the monthly walls (Patch 149: T1 close above the call wall, T2 close below the put wall, T3 close within 1% of the strongest pull, T4 close within 1% of the flip). Adding near-term-wall triggers (same four shapes, on `expiry_nt`) is a follow-up decision — it needs its own log or a tagged extension of the existing one, and it starts after the content pause like the rest of the Lab work. Not part of this spec.
