"""Patch 207: sector pulse. The eleven SPDR sector funds against SPY, plus where today's live setups cluster.

Context only, like GEX and the VIX: it never touches a grade, an alert or a ranking.
- rs5 / rs20: the fund's 5- / 20-trading-day return minus SPY's, in percentage points. Positive = beating the market.
- trend: rs20 now vs rs20 five trading days ago (rising / falling at >= 0.25pt, else flat).
- state: both rs5 and rs20 > 0 -> "Rotating in", both < 0 -> "Rotating out", else "Mixed".
Built after each board sweep (13:52 / 16:22 ET) and cached to DATA_DIR/pulse.json. Fail-open everywhere: a short or
missing series drops that sector's row; a total failure means no strip, never an error on Today.
Daily closes come from the 4H candles the board already loads (engine.to_daily), so no new data source.
"""
from __future__ import annotations

import json
import logging
import os
from collections import Counter
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
PATH = DATA_DIR / "pulse.json"

FUNDS = [("XLK", "Technology"), ("XLF", "Financials"), ("XLE", "Energy"), ("XLV", "Health Care"),
         ("XLY", "Consumer Discretionary"), ("XLP", "Consumer Staples"), ("XLI", "Industrials"), ("XLB", "Materials"),
         ("XLU", "Utilities"), ("XLRE", "Real Estate"), ("XLC", "Communication Services")]
# sectors.json uses the GICS names; the pulse uses the fund names (only Technology differs)
GICS_TO_PULSE = {"Information Technology": "Technology"}


def _closes(sym: str) -> pd.Series | None:
    import engine
    import scanner
    try:
        c4 = scanner.load_4h(sym, scanner.SCAN_DAYS)
        d = engine.to_daily(c4)
        s = d["Close"].dropna()
        return s if len(s) >= 26 else None
    except Exception as e:
        log.info("pulse: no closes for %s (%s)", sym, e)
        return None


def _ret(s: pd.Series, n: int, back: int = 0) -> float:
    """% return over n trading days, ending `back` days ago."""
    end = len(s) - 1 - back
    return (float(s.iloc[end]) / float(s.iloc[end - n]) - 1) * 100


def rows(closes: dict[str, pd.Series]) -> list[dict]:
    """The ranked rows from daily closes (pure: used by build() and the QA checks)."""
    spy = closes.get("SPY")
    if spy is None:
        return []
    out = []
    for sym, name in FUNDS:
        s = closes.get(sym)
        if s is None:
            continue
        both = pd.concat([s, spy], axis=1, join="inner").dropna()    # same dates for the fund and SPY
        if len(both) < 26:
            continue
        f, m = both.iloc[:, 0], both.iloc[:, 1]
        r5, r20 = _ret(f, 5), _ret(f, 20)
        rs5, rs20 = r5 - _ret(m, 5), r20 - _ret(m, 20)
        rs20_prev = _ret(f, 20, 5) - _ret(m, 20, 5)
        d = rs20 - rs20_prev
        out.append(dict(symbol=sym, sector=name, ret5=round(r5, 2), ret20=round(r20, 2), rs5=round(rs5, 2),
                        rs20=round(rs20, 2), trend="rising" if d >= 0.25 else "falling" if d <= -0.25 else "flat",
                        state="Rotating in" if rs5 > 0 and rs20 > 0 else "Rotating out" if rs5 < 0 and rs20 < 0 else "Mixed",
                        as_of=str(both.index[-1].date())))
    out.sort(key=lambda r: r["rs20"], reverse=True)
    return out


def build() -> dict | None:
    """Compute and cache. Returns the payload (None on total failure; the old cache is kept)."""
    try:
        closes = {s: c for s in ["SPY"] + [f for f, _ in FUNDS] if (c := _closes(s)) is not None}
        rs = rows(closes)
        if not rs:
            return None
        out = dict(rows=rs, as_of=max(r["as_of"] for r in rs), built=pd.Timestamp.now(tz="UTC").isoformat())
        tmp = PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(out))
        tmp.replace(PATH)
        return out
    except Exception:
        log.exception("pulse build failed")
        return None


def load() -> dict | None:
    try:
        return json.loads(PATH.read_text())
    except Exception:
        return None


def cluster(live: list[dict]) -> str | None:
    """'4 of today's 6 live setups are in Technology.' when >= 3 live setups and the top sector holds >= 2.
    ETFs and tickers without a sector are left out of both counts."""
    import universe
    secs = []
    for r in live or []:
        sym = str(r.get("symbol") or "").upper()
        if not sym or sym in universe.ETFS:
            continue
        g = universe.SECTORS.get(sym)
        if g:
            secs.append(GICS_TO_PULSE.get(g, g))
    if len(secs) < 3:
        return None
    name, k = Counter(secs).most_common(1)[0]
    return f"{k} of today's {len(secs)} live setups are in {name}." if k >= 2 else None


def payload(live: list[dict]) -> dict | None:
    p = load()
    if p is None:
        p = build()            # first run after a deploy: build once from the banked bars
    if not p or not p.get("rows"):
        return None
    return dict(p, cluster=cluster(live))
