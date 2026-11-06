"""Dealer gamma walls, self-computed from the free Cboe delayed chain (Patch 23).

One calculation per candidate ticker, cached per symbol per day. Fail-open:
any fetch/parse failure returns None and the signal proceeds unfiltered.

Per-strike GEX here is gamma x OI x 100. The spot scaling is left out on purpose:
it doesn't change which strike is biggest, and the ranking is all the filter needs.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import date, timedelta

import requests

log = logging.getLogger("gamma")

_CACHE: dict[tuple[str, str], dict | None] = {}   # (symbol, YYYY-MM-DD) -> walls or None
_FAILED: dict[str, float] = {}                    # symbol -> time of last failed fetch (retry after an hour)
_LOCK = threading.Lock()
_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{}.json"
_BAND = 0.10                                      # only strikes within ±10% of spot
_RETRY = 3600


def _occ(option: str):
    """Parse an OCC symbol -> (expiry date, is_call, strike). None if unparseable."""
    m = re.match(r"^([A-Z.]+)(\d{6})([CP])(\d{8})$", (option or "").strip())
    if not m:
        return None
    y, mo, d = int(m.group(2)[:2]) + 2000, int(m.group(2)[2:4]), int(m.group(2)[4:6])
    try:
        return date(y, mo, d), m.group(3) == "C", int(m.group(4)) / 1000.0
    except ValueError:
        return None


def _monthly(date_: date) -> bool:
    """Third Friday of the month."""
    first = date(date_.year, date_.month, 1)
    friday = first + timedelta(days=(4 - first.weekday()) % 7)
    return date_ == friday + timedelta(weeks=2)


def _compute(contracts: list, spot: float, today: date) -> dict | None:
    by_exp: dict[date, list] = {}
    for c in contracts:
        p = _occ(c.get("option", ""))
        if not p:
            continue
        exp, call, k = p
        if abs(k - spot) / spot > _BAND:
            continue
        by_exp.setdefault(exp, []).append((call, k, float(c.get("gamma") or 0), float(c.get("open_interest") or 0)))
    swing = [e for e in by_exp if (e - today).days >= 7]          # no weeklies under 7 days, no 0DTE
    monthlies = [e for e in swing if _monthly(e)]
    exp = min(monthlies) if monthlies else min(swing, default=None)
    if not exp:
        return None
    call_gex: dict[float, float] = {}
    put_gex: dict[float, float] = {}
    for call, k, g, oi in by_exp[exp]:
        book = call_gex if call else put_gex
        book[k] = book.get(k, 0.0) + g * oi * 100
    call_gex = {k: v for k, v in call_gex.items() if v > 0}
    put_gex = {k: v for k, v in put_gex.items() if v > 0}
    cw = max(call_gex, key=call_gex.get) if call_gex else None
    pw = max(put_gex, key=put_gex.get) if put_gex else None
    flip, cum = None, 0.0
    for k in sorted(set(call_gex) | set(put_gex)):
        prev, cum = cum, cum + call_gex.get(k, 0.0) - put_gex.get(k, 0.0)
        if prev < 0 <= cum:
            flip = k
            break
    return {"call_wall": cw, "put_wall": pw, "flip": flip, "expiry": exp.isoformat()}


def walls(symbol: str, spot: float | None, fetch: bool = True) -> dict | None:
    """{'call_wall', 'put_wall', 'flip', 'expiry'} or None. Never raises.
    fetch=False reads the cache only (for page loads: they never trigger a download)."""
    sym = (symbol or "").upper()
    key = (sym, date.today().isoformat())
    if key in _CACHE:
        return _CACHE[key]
    if not fetch or not spot or spot <= 0:
        return None
    if time.time() - _FAILED.get(sym, 0) < _RETRY:
        return None
    out = None
    try:
        r = requests.get(_URL.format(sym), headers={"User-Agent": "NextShelf/1.0"}, timeout=20)
        r.raise_for_status()
        contracts = (r.json().get("data") or {}).get("options") or []
        out = _compute(contracts, float(spot), date.today())
    except Exception as e:
        log.warning("walls failed for %s: %s", sym, e)
        _FAILED[sym] = time.time()        # a blip shouldn't blank the whole day: try again in an hour
        return None
    with _LOCK:
        _CACHE[key] = out
    return out


def attach(c: dict, fetch: bool = True) -> dict:
    """Add wall fields to a candidate dict (symbol, d, edge, target, price), in place. Never raises.
    A call wall (put wall for shorts) between the edge and the target becomes the target."""
    c["gamma_wall"] = c["gamma_flip"] = None
    c["wall_in_path"] = c["gamma_capped"] = False
    try:
        d, edge, target = c["d"], c["edge"], c["target"]
        if not edge or not target:
            return c
        w = walls(c["symbol"], c.get("price"), fetch=fetch)
        if not w:
            return c
        wall = w["call_wall"] if d == 1 else w["put_wall"]
        c["gamma_wall"], c["gamma_flip"] = wall, w["flip"]
        if wall and (edge < wall < target if d == 1 else target < wall < edge):
            c["wall_in_path"] = c["gamma_capped"] = True
            c["shelf_target"] = target
            c["target"] = wall            # the wall, not the shelf, is the take-profit now
    except Exception as e:
        log.warning("attach failed for %s: %s", c.get("symbol"), e)
    return c


def line(c: dict) -> str:
    """One-line wall summary for an alert / paper message. '' when no data."""
    try:
        wall, flip, target = c.get("gamma_wall"), c.get("gamma_flip"), c["target"]
        if not wall:
            return ""
        d, edge = c["d"], c["edge"]
        name = "Call wall" if d == 1 else "Put wall"
        pct = (wall / edge - 1) * 100 * d
        if c.get("wall_in_path"):
            return f"🧱 {name} ${wall:.2f} ({pct:+.1f}% from entry) · target capped at the wall"
        flip_s = f" · flip ${flip:.2f}" if flip else ""
        return f"🧱 Open gamma to ${target:.2f} · {name.lower()} ${wall:.2f}{flip_s}"
    except Exception:
        return ""
