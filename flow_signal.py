"""Flow confluence — the tape classification of a breakout (Patch 174).

Phase 1 of flow-confluence-build-spec.md: for hub retest plays, the
aggressive-buy classification of the play's own breakout, computed from the
options tape by EXACTLY the computation the Flow Lab test passed
(flow-confirmation-lab-test-spec.md section 1, as implemented in the Lab
runner's flow_common.py and analyze_flow.py). The constants, the calendar,
the aggregation, and the classifier below mirror the runner's — the patch's
acceptance check 2 pins the two implementations to the same answers on the
Lab's saved data.

This module is the ONLY tape reader for this feature. It never reads
flow_log and never calls gamma.flow_proxy: the proxy is a different,
unmeasured measurement and is never substituted (spec section 2).
Fail-open everywhere: no provider configured, provider down, or a needed
day unavailable -> no classification, no row, and every surface renders
exactly as before this patch.
"""
from __future__ import annotations

import json
import logging
import os
import statistics
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import db

log = logging.getLogger("flow_signal")

NY = ZoneInfo("America/New_York")

# --- The test's constants (flow-confirmation-lab-test-spec.md section 1;
# --- identical names/values to the Lab runner's flow_common.py — the live
# --- build uses the test's definitions, not copies that may drift; the
# --- patch's acceptance check 2 verifies the agreement on real data).
FLOW_WINDOW_DAYS = 2      # breakout day + 1 trading day before
FLOW_MIN_ABS = 250_000    # absolute net-premium floor (dollars)
FLOW_MIN_MULT = 2.0       # net premium must exceed this x the 30-day median
FLOW_MEDIAN_DAYS = 30     # median window, trading days before the flow window

# --- The measured record the lines cite (Flow Lab results, Nov 19, 2026).
# --- Constants, never computed live; they change only through a
# --- re-measurement and a revision of the spec (spec section 4).
FLOW_RECORD = {
    "confirmed": {"avg_pct": 0.45, "n": 102, "pf": 1.31},
    "against": {"avg_pct": -0.49, "n": 95, "pf": 0.74},
    "none": {"avg_pct": -0.10, "n": 376, "pf": 0.93},
}

# --- NYSE full-day closures (mirrors flow_common.py's calendar: only
# --- full-day closures matter — options still trade on early-close days,
# --- so those days count as trading days). Covers Oct 2024 through Dec 2026.
NYSE_HOLIDAYS = {
    # 2024
    "2024-11-28", "2024-12-25",
    # 2025 (incl. Jan 9, national day of mourning for President Carter)
    "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18",
    "2025-05-26", "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27",
    "2025-12-25",
    # 2026 (Jul 4 falls on a Saturday, so the closure is observed Fri Jul 3)
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
}


# Early closes (1:00 PM ET): the session still trades, so these stay trading days above; the app uses them
# to name the right deadline (one 4-hour close that day, at 1:00 PM). Through Dec 2026, like the closures.
from engine import EARLY_CLOSES as NYSE_EARLY_CLOSES      # Nov 27: one list, shared with the candle builder


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in NYSE_HOLIDAYS


def prev_trading_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def trading_days_before(d: date, n: int) -> list[date]:
    """The n trading days strictly before d, oldest first."""
    out = []
    cur = d
    while len(out) < n:
        cur = prev_trading_day(cur)
        out.append(cur)
    return list(reversed(out))


def flow_days_for_breakout(breakout: date) -> tuple[list[date], list[date]]:
    """For one breakout: (window days, median days) — flow_common's split.

    Window  = breakout day + the 1 trading day before it (spec section 3).
    Median  = the 30 trading days before the window starts.
    """
    window = trading_days_before(breakout, FLOW_WINDOW_DAYS - 1) + [breakout]
    median = trading_days_before(window[0], FLOW_MEDIAN_DAYS)
    return window, median


# --- One day of Theta bulk trade_quote -> the day's flow aggregate ------------
def aggregate_theta_day(raw: dict) -> dict:
    """Reduce one /v2/bulk_hist/option/trade_quote response (all expiries, one
    root, one day) to the aggressive-buy premium totals the signal needs.

    A port of the Lab runner's flow_common.aggregate_theta_day, rule for
    rule. Aggressor rule (the test's section 1: "bought at the ask" is what
    the signal is built from): a trade is an aggressive BUY when its price
    >= the ask of the NBBO quote paired with it. Premium = price x size
    x 100 (per contract). Trades priced below the ask (aggressive sells,
    mid-market) never enter FLOW_NET — the signal is built from buys only.
    Trades whose paired quote is missing or crossed/locked at zero cannot
    be classified; they are counted separately and excluded, never guessed.

    Column positions are read from the response's own header.format list,
    so this does not depend on Theta's column order.
    """
    header = (raw or {}).get("header") or {}
    fmt = header.get("format") or []
    try:
        i_price = fmt.index("price")
        i_size = fmt.index("size")
        i_bid = fmt.index("bid")
        i_ask = fmt.index("ask")
    except ValueError:
        raise ValueError(f"unexpected Theta format columns: {fmt!r}")

    call_buy = 0.0
    put_buy = 0.0
    n_trades = 0
    n_classified = 0
    n_unclassified = 0
    contracts = 0
    for block in (raw.get("response") or []):
        contract = block.get("contract") or {}
        right = contract.get("right")
        if right not in ("C", "P"):
            continue
        contracts += 1
        for tick in block.get("ticks") or []:
            n_trades += 1
            price = tick[i_price]
            size = tick[i_size]
            bid = tick[i_bid]
            ask = tick[i_ask]
            if price is None or size is None or not ask or ask <= 0 or bid is None or bid < 0:
                n_unclassified += 1
                continue
            n_classified += 1
            if price >= ask:  # paid the ask (or above): an aggressive buy
                premium = price * size * 100.0
                if right == "C":
                    call_buy += premium
                else:
                    put_buy += premium
    return dict(
        call_buy_premium=round(call_buy, 2),
        put_buy_premium=round(put_buy, 2),
        net=round(call_buy - put_buy, 2),   # this day's FLOW_NET contribution
        trades=n_trades,
        classified=n_classified,
        unclassified=n_unclassified,
        contracts=contracts,
    )


# --- Providers -----------------------------------------------------------------
class FlowTapeProvider:
    """The feed contract (spec section 5): per (symbol, trading day), the
    day's aggressive-buy aggregate, produced by the in-house aggregation
    above — the provider supplies TAPE, never its own flow labels.

    daily_aggregate returns the aggregate dict (at least {"net": float}),
    or None when the day cannot be obtained (no data, provider error).
    None aborts the classification — it is never read as a zero-flow day.
    A day the provider answers with a valid, empty tape is a real zero:
    the aggregate's net is 0.0, exactly as the Lab's saved "empty" days."""

    name = "none"

    def daily_aggregate(self, symbol: str, day: date) -> dict | None:
        raise NotImplementedError


class ThetaTerminalProvider(FlowTapeProvider):
    """Development/internal provider: the Theta Terminal's historical REST
    API — the same requests the Lab runner's pull_flow.py made
    (GET /v2/bulk_hist/option/trade_quote?root=SYM&exp=0, one day per call,
    exp=0 = every expiry). This is how the feature is built and validated
    internally on Options Standard (personal/internal use). It is NOT a
    member-facing feed: the Terminal runs on the same machine as the app
    (the dev PC), and Standard's licence does not cover member display.

    Base URL: env THETA_TERMINAL_URL, default http://127.0.0.1:25510
    (port 25510 is the Terminal's REST port)."""

    name = "theta_terminal"

    def __init__(self, base_url: str | None = None):
        self.base = (base_url or os.getenv("THETA_TERMINAL_URL",
                                           "http://127.0.0.1:25510")).rstrip("/")

    def daily_aggregate(self, symbol: str, day: date) -> dict | None:
        ymd = day.strftime("%Y%m%d")
        url = (f"{self.base}/v2/bulk_hist/option/trade_quote?root={symbol}&exp=0"
               f"&start_date={ymd}&end_date={ymd}")
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                raw = json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code == 472:      # Theta NO_DATA: a definitive "no data for
                return None        # this request" — not a zero-flow day
            log.warning("flow tape %s %s: HTTP %s", symbol, ymd, e.code)
            return None
        except Exception as e:
            log.warning("flow tape %s %s: %s", symbol, ymd, e)
            return None
        header = raw.get("header") or {}
        if header.get("error_type") or header.get("error_msg") or "format" not in header:
            return None
        try:
            return aggregate_theta_day(raw)
        except Exception as e:
            log.warning("flow tape %s %s: aggregate failed: %s", symbol, ymd, e)
            return None


class LicensedProvider(FlowTapeProvider):
    """The member-facing feed (spec section 5, Decision 1) — a stub, on
    purpose. It gets wired when the business licence lands and the user
    picks the vendor (Theta business vs UW business vs Massive); that
    vendor's API details deliberately do not exist in this patch. Until
    then it supplies nothing, so no classification is ever written from
    it and the feature stays dark on this provider."""

    name = "licensed"

    def daily_aggregate(self, symbol: str, day: date) -> dict | None:
        return None


class StoredTapeProvider(FlowTapeProvider):
    """Reads the daily aggregates the PC courier stored in flow_tape_day
    (Patch 177, flow-wiring spec). In the internal phase the courier is
    the table's only writer and carries only Theta Terminal tape, so the
    stamp stays truthful; this provider is removed when the licensed
    feed lands and a real provider replaces the courier."""

    name = "theta_terminal (via pc courier)"

    def daily_aggregate(self, symbol: str, day: date) -> dict | None:
        return db.flow_tape_for(symbol, day.isoformat())


def get_provider() -> FlowTapeProvider | None:
    """Env FLOW_TAPE_PROVIDER: 'theta_terminal' (internal/dev) | 'licensed'
    (stub until Decision 1) | unset / anything else -> None, and the
    feature is dark: the classification job no-ops, no rows are written,
    and every card renders exactly as before this patch."""
    kind = os.getenv("FLOW_TAPE_PROVIDER", "").strip().lower()
    if kind == "theta_terminal":
        return ThetaTerminalProvider()
    if kind == "licensed":
        return LicensedProvider()
    return None


# --- The classifier: analyze_flow.classify's computation, verbatim -------------
def classify(provider: FlowTapeProvider, symbol: str, direction: int,
             breakout_date: date) -> dict | None:
    """Classify one breakout from the provider's daily aggregates.

    Window FLOW_NET vs max($250k, 2 x signed 30-day median), scored against
    the breakout's direction — the test's section 3, unchanged. Returns
    dict(state, flow_net, threshold, median_net, window, provider, as_of),
    or None when any window/median day is unavailable (the Lab's 'unknown':
    excluded, never guessed) — the caller writes no row, and a later job
    run retries while the breakout is still live."""
    window, median_days = flow_days_for_breakout(breakout_date)
    nets = {}
    for d in window + median_days:
        agg = provider.daily_aggregate(symbol, d)
        if agg is None:
            return None
        nets[d] = float(agg["net"])
    window_net = sum(nets[d] for d in window)
    median_net = statistics.median(nets[d] for d in median_days)
    thr = max(FLOW_MIN_ABS, FLOW_MIN_MULT * median_net)
    if window_net > thr:
        flow = "bullish"
    elif window_net < -thr:
        flow = "bearish"
    else:
        flow = "none"
    if flow == "none":
        state = "none"
    elif (direction == 1 and flow == "bullish") or (direction == -1 and flow == "bearish"):
        state = "confirmed"
    else:
        state = "against"
    return dict(state=state, flow_net=round(window_net, 2),
                threshold=round(thr, 2), median_net=round(median_net, 2),
                window=[window[0].isoformat(), window[-1].isoformat()],
                provider=provider.name,
                as_of=datetime.now(NY).isoformat())


# --- The post-close classification job ------------------------------------------
def _live_breakouts(max_age_days: int = 10) -> list[tuple[str, int, str]]:
    """(symbol, direction, breakout_date ISO) for the hub breakouts live on
    the board — classify_recent's candidate derivation, unchanged: board()
    live rows only, the breakout candle derived exactly as the scanner's
    Patch 112 read derives it (retest.candles_since back from the last
    candle of the symbol's 4H series), at most max_age_days old."""
    import board
    import scanner

    out: list[tuple[str, int, str]] = []
    try:
        live = board.board().get("live") or []
    except Exception as e:
        log.warning("flow classify: board read failed: %s", e)
        return out
    today = datetime.now(NY).date()
    for r in live:
        sym = r.get("symbol")
        try:
            state = r.get("state")
            if not sym or state not in ("breakout_up", "breakdown"):
                continue
            d = 1 if state == "breakout_up" else -1
            k = (r.get("retest") or {}).get("candles_since")
            if k is None:
                continue
            c4 = scanner.load_4h(sym, scanner.SCAN_DAYS)
            i0 = len(c4) - 1 - int(k)
            if i0 < 0:
                continue
            bdate = c4.index[i0].tz_convert("America/New_York").date()
            if bdate > today or (today - bdate).days > max_age_days:
                continue
            out.append((sym, d, bdate.isoformat()))
        except Exception as e:
            log.warning("flow classify: breakout derivation failed for %s: %s", sym, e)
    return out


def classify_recent(max_age_days: int = 10, provider: FlowTapeProvider | None = None) -> int:
    """Store classifications for the hub breakouts live on the board.

    Runs post-close (app.py schedules it weekday evenings). Candidates are
    the board's current live rows — board()['live'] holds hub breakouts
    only (pattern setups live in board()['setups'], never touched here) —
    so candidates are recent by construction, and breakouts that predate
    the feed are never backfilled (spec section 4). The breakout date is
    derived exactly as the scanner's Patch 112 read derives it: the
    breakout candle is st['retest']['candles_since'] back from the last
    candle of the symbol's 4H series. First write wins (db.log_flow_signal
    is INSERT OR IGNORE); a breakout already classified is skipped before
    any provider call is spent on it. Returns the rows written.

    One honesty note (spec section 5, timeliness): rows are written from
    the evening pull. The provider's end-of-day completeness must be
    verified on a sample before this job is relied on (verification
    step 8 in the patch) — a provider whose day is not final until the
    next morning delays classifications by a day, it does not corrupt
    them, because an unavailable day aborts the classification instead.

    An explicit provider may be passed (the wiring patch's ingest endpoint
    passes StoredTapeProvider); the default is unchanged.
    """
    if provider is None:
        provider = get_provider()
    if provider is None:
        return 0
    written = 0
    for sym, d, iso in _live_breakouts(max_age_days):
        try:
            if db.flow_signal_for(sym, d, iso):
                continue
            res = classify(provider, sym, d, date.fromisoformat(iso))
            if res is None:
                continue
            db.log_flow_signal(sym, d, iso, res["state"], res["flow_net"],
                               res["threshold"], res["median_net"],
                               res["window"][0], res["window"][1],
                               res["provider"], res["as_of"])
            written += 1
        except Exception as e:
            log.warning("flow classify failed for %s: %s", sym, e)
    if written:
        log.info("flow classify: wrote %d classification(s)", written)
    return written


def pending_breakouts(max_age_days: int = 10) -> list[dict]:
    """The courier's work list (flow-wiring spec section 3): live breakouts
    with no stored classification, each with the window + median days the
    stored tape still lacks (days already in flow_tape_day are subtracted,
    so repeat runs only fetch what is missing). A breakout whose days are
    all stored but which classify() still cannot score — a day the
    Terminal had no data for — comes back with an empty days_needed: it
    stays visibly pending instead of being silently dropped."""
    out = []
    for sym, d, iso in _live_breakouts(max_age_days):
        if db.flow_signal_for(sym, d, iso):
            continue
        window, median_days = flow_days_for_breakout(date.fromisoformat(iso))
        needed = [x.isoformat() for x in window + median_days
                  if not db.flow_tape_for(sym, x.isoformat())]
        out.append(dict(symbol=sym, direction=d, breakout_date=iso,
                        days_needed=needed))
    return out
