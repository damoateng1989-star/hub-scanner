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

_CHAIN_CACHE: dict[tuple[str, str], list] = {}    # (symbol, YYYY-MM-DD) -> slim contracts (one fetch/day)
_FAILED: dict[str, float] = {}                    # symbol -> time of last failed fetch (retry after an hour)
_LOCK = threading.Lock()
_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{}.json"
_BAND = 0.20                                      # strikes within ±20% of spot: wide enough that the
                                                  # max-GEX strike sits well inside (gamma at 20% OTM on a
                                                  # >=7 DTE monthly is negligible), so the band edge can
                                                  # no longer decide the wall
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


def _slim(contracts: list) -> list:
    """Raw Cboe entries -> (expiry, is_call, strike, gamma, oi) tuples: a fraction of the memory of the raw dicts."""
    out = []
    for c in contracts:
        if isinstance(c, tuple):
            out.append(c)
            continue
        p = _occ(c.get("option", ""))
        if not p:
            continue
        try:
            out.append((p[0], p[1], p[2], float(c.get("gamma") or 0), float(c.get("open_interest") or 0)))
        except (TypeError, ValueError):
            continue
    return out


def _compute(contracts: list, spot: float, today: date) -> dict | None:
    """Walls at this spot. Takes raw Cboe entries or the slim tuples from _slim()."""
    by_exp: dict[date, list] = {}
    for exp, call, k, g, oi in _slim(contracts):
        if abs(k - spot) / spot > _BAND:
            continue
        by_exp.setdefault(exp, []).append((call, k, g, oi))
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


def _fetch_contracts(symbol: str) -> list:
    """Chain for (symbol, today), fetched once a day and kept slim. [] on failure (fail-open). Never raises."""
    sym = (symbol or "").upper()
    today = date.today().isoformat()
    key = (sym, today)
    with _LOCK:
        if key in _CHAIN_CACHE:
            return _CHAIN_CACHE[key]
    if time.time() - _FAILED.get(sym, 0) < _RETRY:
        return []
    try:
        r = requests.get(_URL.format(sym), headers={"User-Agent": "NextShelf/1.0"}, timeout=20)
        r.raise_for_status()
        contracts = _slim((r.json().get("data") or {}).get("options") or [])
    except Exception as e:
        log.warning("walls failed for %s: %s", sym, e)
        _FAILED[sym] = time.time()        # a blip shouldn't blank the whole day: try again in an hour
        return []
    with _LOCK:
        for k in [k for k in _CHAIN_CACHE if k[1] != today]:     # yesterday's chains: drop them
            del _CHAIN_CACHE[k]
        _CHAIN_CACHE[key] = contracts
    return contracts


def walls(symbol: str, spot: float | None, fetch: bool = True) -> dict | None:
    """{'call_wall', 'put_wall', 'flip', 'expiry'} or None. Never raises.
    The chain is fetched once per symbol per day and cached; the walls are recomputed
    from that chain at the CURRENT spot on every call, so a $1 spot move can no longer
    leave this morning's wall on the card all day.
    fetch=False reads the cached chain only (for page loads: they never trigger a download)."""
    try:
        sym = (symbol or "").upper()
        if not spot or float(spot) <= 0:
            return None
        if fetch:
            contracts = _fetch_contracts(sym)
        else:
            with _LOCK:
                contracts = _CHAIN_CACHE.get((sym, date.today().isoformat()), [])
        if not contracts:
            return None
        return _compute(contracts, float(spot), date.today())
    except Exception as e:
        log.warning("walls compute failed for %s: %s", symbol, e)
        return None


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
