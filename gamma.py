"""Dealer gamma walls, self-computed from the free Cboe delayed chain (Patch 23).

One calculation per candidate ticker, cached per symbol per day. Fail-open:
any fetch/parse failure returns None and the signal proceeds unfiltered.

Per-strike GEX here is gamma x OI x 100. The spot scaling is left out on purpose:
it doesn't change which strike is biggest, and the ranking is all the filter needs.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger("gamma")

_CHAIN_CACHE: dict[tuple[str, str], list] = {}    # (symbol, YYYY-MM-DD) -> slim contracts (one fetch/day)
_IV_CACHE: dict[tuple[str, str], dict] = {}       # Nov 25: (symbol, day) -> {(expiry, is_call, strike): implied vol}, for VEX
_FETCHED_AT: dict[tuple[str, str], str] = {}      # (symbol, YYYY-MM-DD) -> when that chain was fetched (ISO, NY)
_FAILED: dict[str, float] = {}                    # symbol -> time of last failed fetch (retry after an hour)
_LOCK = threading.Lock()
_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{}.json"
_BAND = 0.20                                      # strikes within ±20% of spot: wide enough that the
                                                  # max-GEX strike sits well inside (gamma at 20% OTM on a
                                                  # >=7 DTE monthly is negligible), so the band edge can
                                                  # no longer decide the wall
_RETRY = 3600
_NY = ZoneInfo("America/New_York")
_GEX_DIR = Path(os.getenv("DATA_DIR", "data")) / "gex"
_GEX_DIR.mkdir(parents=True, exist_ok=True)
_BOARD_RV_TOP = 3


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
    """Raw Cboe entries -> (expiry, is_call, strike, gamma, oi, volume) tuples."""
    out = []
    for c in contracts:
        if isinstance(c, tuple):
            out.append(c if len(c) >= 6 else (*c, 0) if len(c) == 5 else c)
            continue
        p = _occ(c.get("option", ""))
        if not p:
            continue
        try:
            out.append((p[0], p[1], p[2], float(c.get("gamma") or 0),
                        float(c.get("open_interest") or 0), float(c.get("volume") or 0)))
        except (TypeError, ValueError):
            continue
    return out


def _compute(contracts: list, spot: float, today: date) -> dict | None:
    """Walls at this spot. Takes raw Cboe entries or the slim tuples from _slim()."""
    by_exp: dict[date, list] = {}
    for exp, call, k, g, oi, v in _slim(contracts):
        if abs(k - spot) / spot > _BAND:
            continue
        by_exp.setdefault(exp, []).append((call, k, g, oi, v))
    swing = [e for e in by_exp if (e - today).days >= 7]          # no weeklies under 7 days, no 0DTE
    monthlies = [e for e in swing if _monthly(e)]
    exp = min(monthlies) if monthlies else min(swing, default=None)
    if not exp:
        return None
    call_gex: dict[float, float] = {}
    put_gex: dict[float, float] = {}
    for call, k, g, oi, v in by_exp[exp]:
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


# ---------------------------------------------------------------- Nov 23: the chain from Alpaca (gamma + open interest)
_ALPACA_DAYS = 90          # expiries out to ~10 weeks: the monthly >=7 DTE and the 8 nearest expiries summed() uses
_TRADING_BASES = ("https://api.alpaca.markets", "https://paper-api.alpaca.markets")
_TRADING_BASE: list = []


def _alpaca_ok() -> bool:
    return os.getenv("DATA_PROVIDER", "alpaca").strip().lower() == "alpaca" and bool(os.getenv("ALPACA_API_KEY")) and bool(os.getenv("ALPACA_SECRET_KEY"))


def _alpaca_headers() -> dict:
    return {"APCA-API-KEY-ID": os.getenv("ALPACA_API_KEY", ""), "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET_KEY", "")}


def _alpaca_oi(sym: str, lo: float | None, hi: float | None, exp_lte: str) -> dict:
    """{OCC symbol: open interest} from Alpaca's contracts list (trading API; live keys first, then paper)."""
    params = dict(underlying_symbols=sym, status="active", limit=10000, expiration_date_gte=date.today().isoformat(),
                  expiration_date_lte=exp_lte)
    if lo and hi:
        params.update(strike_price_gte=f"{lo:.2f}", strike_price_lte=f"{hi:.2f}")
    bases = _TRADING_BASE or list(_TRADING_BASES)
    last = None
    for base in bases:
        out, token, ok = {}, None, False
        for _ in range(10):
            if token:
                params["page_token"] = token
            r = requests.get(f"{base}/v2/options/contracts", headers=_alpaca_headers(), params=params, timeout=20)
            if r.status_code in (401, 403):
                last = r.status_code
                break
            r.raise_for_status()
            j = r.json()
            ok = True
            for c in j.get("option_contracts") or []:
                try:
                    out[c["symbol"]] = float(c.get("open_interest") or 0)
                except (TypeError, ValueError):
                    pass
            token = j.get("next_page_token")
            if not token:
                break
        if ok:
            if not _TRADING_BASE:
                _TRADING_BASE.append(base)
            return out
    raise RuntimeError(f"Alpaca contracts refused ({last})")


def _alpaca_contracts(sym: str, spot: float | None) -> list:
    """Slim tuples (expiry, is_call, strike, gamma, oi, volume) built from Alpaca: greeks from the option snapshots,
    open interest from the contracts list. Same shape as the Cboe path, so every caller is unchanged."""
    lo = hi = None
    if spot and spot > 0:
        lo, hi = spot * (1 - _BAND - 0.05), spot * (1 + _BAND + 0.05)
    exp_lte = (date.today() + timedelta(days=_ALPACA_DAYS)).isoformat()
    params = dict(feed=os.getenv("ALPACA_OPTIONS_FEED", "indicative"), limit=1000, expiration_date_gte=date.today().isoformat(),
                  expiration_date_lte=exp_lte)
    if lo and hi:
        params.update(strike_price_gte=f"{lo:.2f}", strike_price_lte=f"{hi:.2f}")
    snaps, token = {}, None
    for _ in range(16):
        if token:
            params["page_token"] = token
        r = requests.get(f"https://data.alpaca.markets/v1beta1/options/snapshots/{sym.replace('.', '')}",
                         headers=_alpaca_headers(), params=params, timeout=20)
        r.raise_for_status()
        j = r.json()
        snaps.update(j.get("snapshots") or {})
        token = j.get("next_page_token")
        if not token:
            break
    oi = _alpaca_oi(sym.replace(".", ""), lo, hi, exp_lte)
    out, ivs = [], {}
    for occ, sn in snaps.items():
        p = _occ(occ)
        if not p:
            continue
        g = (sn.get("greeks") or {}).get("gamma")
        if g is None:
            continue
        vol = (sn.get("dailyBar") or {}).get("v") or 0
        out.append((p[0], p[1], p[2], float(g), float(oi.get(occ, 0)), float(vol)))
        try:
            if sn.get("impliedVolatility"):
                ivs[(p[0], p[1], p[2])] = float(sn["impliedVolatility"])
        except (TypeError, ValueError):
            pass
    if not out:
        raise RuntimeError("no Alpaca option greeks returned")
    with _LOCK:
        for k in [k for k in _IV_CACHE if k[1] != date.today().isoformat()]:
            del _IV_CACHE[k]
        _IV_CACHE[(sym.upper(), date.today().isoformat())] = ivs
    return out


def _fetch_contracts(symbol: str, force: bool = False, spot: float | None = None) -> list:
    """Slim chain cache; normally once/day, with force=True for scheduled GEX snapshots. [] on failure."""
    sym = (symbol or "").upper()
    today = date.today().isoformat()
    key = (sym, today)
    with _LOCK:
        if not force and key in _CHAIN_CACHE:
            return _CHAIN_CACHE[key]
    if not force and time.time() - _FAILED.get(sym, 0) < _RETRY:
        return []
    try:
        if _alpaca_ok():                  # Nov 23: Cboe's free feed started refusing the server (403); Alpaca is the licensed source
            contracts = _alpaca_contracts(sym, spot)
        else:
            r = requests.get(_URL.format(sym), headers={"User-Agent": "NextShelf/1.0"}, timeout=20)
            r.raise_for_status()
            contracts = _slim((r.json().get("data") or {}).get("options") or [])
    except Exception as e:
        log.warning("walls failed for %s: %s", sym, str(e)[:160])
        _FAILED[sym] = time.time()        # a blip shouldn't blank the whole day: try again in an hour
        return []
    with _LOCK:
        for k in [k for k in _CHAIN_CACHE if k[1] != today]:     # yesterday's chains: drop them
            del _CHAIN_CACHE[k]
        for k in [k for k in _FETCHED_AT if k[1] != today]:
            del _FETCHED_AT[k]
        _CHAIN_CACHE[key] = contracts
        _FETCHED_AT[key] = datetime.now(_NY).isoformat(timespec="seconds")
    return contracts


def chain_as_of(symbol: str) -> str | None:
    """When today's cached chain for this symbol was fetched (ISO, New York), or None."""
    with _LOCK:
        return _FETCHED_AT.get(((symbol or "").upper(), date.today().isoformat()))


NEXT_LEVEL_SHARE = 0.25     # a node counts when its |net GEX| is at least this share of the expiry's largest


SUMMED_EXPIRIES = 8
_DAILY_READY = False


def _daily_table():
    """One row per symbol per day: the summed net GEX by strike, so a strike's change since the last session can
    be shown. Written whenever summed() runs (the last write of the day wins); kept 30 days."""
    global _DAILY_READY
    if _DAILY_READY:
        return
    import db
    with db._lock:
        db._conn.execute("CREATE TABLE IF NOT EXISTS gex_daily (symbol TEXT, day TEXT, net TEXT, PRIMARY KEY (symbol, day))")
        db._conn.commit()
    _DAILY_READY = True


def _daily_record(sym: str, net: dict) -> dict | None:
    """Save today's summed net-by-strike for sym and return the previous session's ({strike: gex}) or None."""
    try:
        import db
        _daily_table()
        today = datetime.now(_NY).date().isoformat()
        db._run("INSERT OR REPLACE INTO gex_daily (symbol, day, net) VALUES (?, ?, ?)",
                (sym, today, json.dumps({f"{k:g}": round(v, 1) for k, v in net.items()}, separators=(",", ":"))))
        db._run("DELETE FROM gex_daily WHERE day < ?", ((datetime.now(_NY).date() - timedelta(days=30)).isoformat(),))
        r = db._run("SELECT day, net FROM gex_daily WHERE symbol = ? AND day < ? ORDER BY day DESC LIMIT 1", (sym, today)).fetchone()
        if not r:
            return None
        return dict(day=r["day"], net={float(k): float(v) for k, v in json.loads(r["net"]).items()})
    except Exception as e:
        log.warning("gex daily record failed for %s: %s", sym, e)
        return None


def summed(symbol: str, spot: float | None, fetch: bool = True, n_exp: int = SUMMED_EXPIRIES) -> dict | None:
    """Net gamma per strike ADDED UP across the nearest n_exp expiries (weeklies included), the way multi-expiry
    GEX profiles show it: the strikes where every expiry piles up stand out. Returns {'expiries', 'nodes'} where
    nodes are the local maxima of |summed net GEX| that reach 25% of the largest ({'strike', 'gex'} in dollar
    notional, sorted by size), or None. Display/confluence only. Never raises."""
    try:
        sym = (symbol or "").upper()
        if not spot or float(spot) <= 0:
            return None
        spot = float(spot)
        if fetch:
            contracts = _fetch_contracts(sym, spot=float(spot))
        else:
            with _LOCK:
                contracts = _CHAIN_CACHE.get((sym, date.today().isoformat()), [])
        if not contracts:
            return None
        today = datetime.now(_NY).date()
        rows = [(exp, call, k, g, oi) for exp, call, k, g, oi, v in _slim(contracts) if exp >= today and abs(k - spot) / spot <= 0.15]
        exps = sorted({r[0] for r in rows})[:n_exp]
        if not exps:
            return None
        keep = set(exps)
        net: dict[float, float] = {}
        for exp, call, k, g, oi in rows:
            if exp in keep:
                v = g * oi * 100 * spot
                net[k] = net.get(k, 0.0) + (v if call else -v)
        ks = sorted(net)
        top = max((abs(v) for v in net.values()), default=0.0)
        nodes = [dict(strike=k, gex=round(net[k], 1)) for i, k in enumerate(ks)
                 if top > 0 and abs(net[k]) >= NEXT_LEVEL_SHARE * top
                 and (i == 0 or abs(net[k]) > abs(net[ks[i - 1]])) and (i == len(ks) - 1 or abs(net[k]) > abs(net[ks[i + 1]]))]
        nodes.sort(key=lambda n: -abs(n["gex"]))
        nodes = nodes[:8]
        # day over day: the same strike's summed net GEX at the last session ("Node Momentum")
        prev = _daily_record(sym, net)
        movers = []
        if prev:
            pn = prev["net"]
            for n in nodes:
                if n["strike"] in pn:
                    n["prev"] = round(pn[n["strike"]], 1)
                    n["change"] = round(n["gex"] - pn[n["strike"]], 1)
            ch = {k: net[k] - pn.get(k, 0.0) for k in set(net) | set(pn) if k in net}
            topc = max((abs(v) for v in ch.values()), default=0.0)
            movers = sorted(({"strike": k, "gex": round(net[k], 1), "change": round(v, 1)} for k, v in ch.items()
                             if topc and abs(v) >= 0.25 * topc), key=lambda m: -abs(m["change"]))[:6]
        return dict(expiries=[e.isoformat() for e in exps], nodes=nodes,
                    since=prev["day"] if prev else None, movers=movers)
    except Exception as e:
        log.warning("summed gamma failed for %s: %s", symbol, e)
        return None


def next_levels(symbol: str, spot: float | None, fetch: bool = True) -> dict | None:
    """GEX next levels (spec Nov 18, PANW): on the NEAREST expiry with chain data (weeklies and 0DTE included),
    the near-term call/put wall and flip (same math as _compute), the nearest significant node above and below
    spot (a local max of |net GEX| that is >= 25% of that expiry's largest), and a regime label off the flip
    (no flip: the sign of the expiry's total net GEX). Display only: never feeds attach(), alerts or the record.
    None when the chain can't be read. Never raises."""
    try:
        sym = (symbol or "").upper()
        if not spot or float(spot) <= 0:
            return None
        spot = float(spot)
        if fetch:
            contracts = _fetch_contracts(sym, spot=float(spot))
        else:
            with _LOCK:
                contracts = _CHAIN_CACHE.get((sym, date.today().isoformat()), [])
        if not contracts:
            return None
        today = datetime.now(_NY).date()
        by_exp: dict[date, list] = {}
        for exp, call, k, g, oi, v in _slim(contracts):
            if exp < today or abs(k - spot) / spot > _BAND:
                continue
            by_exp.setdefault(exp, []).append((call, k, g, oi))
        if not by_exp:
            return None
        exp = min(by_exp)
        call_gex: dict[float, float] = {}
        put_gex: dict[float, float] = {}
        for call, k, g, oi in by_exp[exp]:
            book = call_gex if call else put_gex
            book[k] = book.get(k, 0.0) + g * oi * 100
        call_gex = {k: v for k, v in call_gex.items() if v > 0}
        put_gex = {k: v for k, v in put_gex.items() if v > 0}
        if not call_gex and not put_gex:
            return None
        cw = max(call_gex, key=call_gex.get) if call_gex else None
        pw = max(put_gex, key=put_gex.get) if put_gex else None
        strikes = sorted(set(call_gex) | set(put_gex))
        flip, cum = None, 0.0
        for k in strikes:
            prev, cum = cum, cum + call_gex.get(k, 0.0) - put_gex.get(k, 0.0)
            if prev < 0 <= cum:
                flip = k
                break
        net = [(k, (call_gex.get(k, 0.0) - put_gex.get(k, 0.0)) * spot) for k in strikes]   # dollar notional
        total = sum(v for _, v in net)
        top = max((abs(v) for _, v in net), default=0.0)
        nodes = [(k, v) for i, (k, v) in enumerate(net)
                 if 0 < i < len(net) - 1 and abs(v) > abs(net[i - 1][1]) and abs(v) > abs(net[i + 1][1])
                 and top > 0 and abs(v) >= NEXT_LEVEL_SHARE * top]
        lvl = lambda kv: None if kv is None else {"strike": kv[0], "gex": round(kv[1], 1),
                                                  "distance_pct": round((kv[0] - spot) / spot * 100, 1)}
        up = min((n for n in nodes if n[0] >= spot), key=lambda n: n[0], default=None)
        down = max((n for n in nodes if n[0] < spot), key=lambda n: n[0], default=None)
        regime = ("short" if spot < flip else "long") if flip is not None else ("short" if total < 0 else "long")
        return {"expiry_nt": exp.isoformat(), "call_wall_nt": cw, "put_wall_nt": pw, "flip_nt": flip,
                "total_net_nt": round(total, 1), "next_up": lvl(up), "next_down": lvl(down), "regime": regime,
                "share": NEXT_LEVEL_SHARE}
    except Exception as e:
        log.warning("next levels failed for %s: %s", symbol, e)
        return None


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
            contracts = _fetch_contracts(sym, spot=float(spot))
        else:
            with _LOCK:
                contracts = _CHAIN_CACHE.get((sym, date.today().isoformat()), [])
        if not contracts:
            return None
        return _compute(contracts, float(spot), date.today())
    except Exception as e:
        log.warning("walls compute failed for %s: %s", symbol, e)
        return None


def shelves(symbol: str, spot: float | None, fetch: bool = True, n: int = 2) -> dict | None:
    """Top GEX shelves near spot, on the same expiry walls uses (nearest monthly >=7d).
    Returns {'expiry', 'spot', 'shelves': [{'strike', 'gex', 'side'}...]} sorted by strike desc,
    or None. side is 'call' when net GEX is positive, 'put' when negative.
    Top n positive and top n negative nodes by absolute net GEX, so the ladder has
    both a ceiling and a floor. Never raises; fail-open."""
    try:
        sym = (symbol or "").upper()
        if not spot or float(spot) <= 0:
            return None
        spot = float(spot)
        if fetch:
            contracts = _fetch_contracts(sym, spot=float(spot))
        else:
            with _LOCK:
                contracts = _CHAIN_CACHE.get((sym, date.today().isoformat()), [])
        if not contracts:
            return None
        today = date.today()
        by_exp: dict[date, list] = {}
        for exp, call, k, g, oi, v in _slim(contracts):
            if abs(k - spot) / spot > _BAND:
                continue
            by_exp.setdefault(exp, []).append((call, k, g, oi, v))
        swing = [e for e in by_exp if (e - today).days >= 7]
        monthlies = [e for e in swing if _monthly(e)]
        exp = min(monthlies) if monthlies else min(swing, default=None)
        if not exp:
            return None
        net: dict[float, float] = {}
        gross: dict[float, float] = {}
        for call, k, g, oi, v in by_exp[exp]:
            ge = g * oi * 100 * float(spot)
            net[k] = net.get(k, 0.0) + (ge if call else -ge)
            gross[k] = gross.get(k, 0.0) + abs(ge)
        pos = sorted(((k, v) for k, v in net.items() if v > 0), key=lambda kv: -kv[1])[:n]
        neg = sorted(((k, v) for k, v in net.items() if v < 0), key=lambda kv: kv[1])[:n]
        # Balanced strikes: huge gross but a small net — both sides cancel, so the
        # net-only ladder misses them (SPCX $150 is both walls and has net ~0).
        # Include one if its gross beats the largest |net| already in the ladder.
        picked = {k for k, _ in pos + neg}
        ladder_net = max([abs(v) for _, v in pos + neg], default=0.0)
        bal = sorted(((k, gross[k]) for k in gross if k not in picked and abs(net.get(k, 0.0)) < 0.25 * gross[k]),
                     key=lambda kv: -kv[1])
        bal = [(k, net.get(k, 0.0)) for k, _ in bal if gross[k] > ladder_net][:n]
        both_strikes = {k for k, _ in bal}
        rows = [{"strike": k, "gex": round(v, 1), "gross": round(gross[k], 1),
                 "side": "both" if k in both_strikes else ("call" if v > 0 else "put")}
                for k, v in sorted(pos + neg + bal, key=lambda kv: -kv[0])]
        if not rows:
            return None
        return {"expiry": exp.isoformat(), "spot": spot, "shelves": rows}
    except Exception as e:
        log.warning("shelves compute failed for %s: %s", symbol, e)
        return None


_GRID_BAND = 0.10
_GRID_MAX_ROWS = 41        # the strikes nearest the price, contiguous (Nov 25: every strike in the band, not the top ones)
_GRID_MAX_EXP = 13         # Nov 25 (owner): the wide board, every listed expiry out to ~3 months


def _vanna(s: float, k: float, t: float, iv: float, r: float = 0.04) -> float:
    """Black-Scholes vanna (dDelta/dVol, per 1.00 of vol), same for calls and puts. 0 when the inputs can't price."""
    import math
    try:
        if s <= 0 or k <= 0 or t <= 0 or iv <= 0:
            return 0.0
        sq = iv * math.sqrt(t)
        d1 = (math.log(s / k) + (r + iv * iv / 2) * t) / sq
        d2 = d1 - sq
        return -math.exp(-d1 * d1 / 2) / math.sqrt(2 * math.pi) * d2 / iv
    except (ValueError, ZeroDivisionError):
        return 0.0


def grid(symbol: str, spot: float | None, fetch: bool = True) -> dict | None:
    """Net-GEX grid per strike and expiry for the watch-only heat map.
    Uses the four nearest expiries with chain data, including 0DTE; this is
    intentionally a different horizon from the monthly walls/shelves.
    Returns aligned expiry columns and strike rows, or None. Never raises."""
    try:
        sym = (symbol or "").upper()
        if not spot or float(spot) <= 0:
            return None
        spot = float(spot)
        if fetch:
            contracts = _fetch_contracts(sym, spot=float(spot))
        else:
            with _LOCK:
                contracts = _CHAIN_CACHE.get((sym, date.today().isoformat()), [])
        if not contracts:
            return None
        by_exp: dict[date, list] = {}
        for exp, call, k, g, oi, v in _slim(contracts):
            if abs(k - spot) / spot > _GRID_BAND:
                continue
            by_exp.setdefault(exp, []).append((call, k, g, oi, v))
        exps = sorted(by_exp)[:_GRID_MAX_EXP]
        if not exps:
            return None
        net: dict[float, list] = {}
        gross: dict[float, list] = {}
        vex: dict[float, list] = {}
        with _LOCK:
            ivs = dict(_IV_CACHE.get((sym, date.today().isoformat()), {}))
        today = date.today()
        for ei, exp in enumerate(exps):
            for call, k, g, oi, v in by_exp[exp]:
                ge = g * oi * 100 * float(spot)
                row = net.setdefault(k, [0.0] * len(exps))
                row[ei] += ge if call else -ge
                grow = gross.setdefault(k, [0.0] * len(exps))
                grow[ei] += abs(ge)
                iv = ivs.get((exp, call, k))
                if iv and oi:
                    ve = _vanna(spot, k, max(0.5, (exp - today).days) / 365, iv) * 0.01 * oi * 100 * spot
                    vex.setdefault(k, [0.0] * len(exps))[ei] += ve if call else -ve
        rows = [{"strike": k, "gex": [round(x, 1) for x in vals],
                 "gross": [round(x, 1) for x in gross[k]],
                 **({"vex": [round(x, 1) for x in vex[k]]} if k in vex else {})}
                for k, vals in net.items() if any(vals) or any(gross[k])]
        if not rows:
            return None
        rows.sort(key=lambda r: abs(r["strike"] - spot))          # Nov 25 (owner): every strike around the price, evenly
        rows = rows[:_GRID_MAX_ROWS]
        rows.sort(key=lambda r: -r["strike"])
        total = round(sum(sum(v) for v in net.values()), 1)           # the whole band, every listed expiry: the regime
        out = {"expiries": [e.isoformat() for e in exps], "spot": spot, "rows": rows, "net_total": total}
        if vex:
            for r in rows:
                r.setdefault("vex", [0.0] * len(exps))
            out["vex_total"] = round(sum(sum(v) for v in vex.values()), 1)
        return out
    except Exception as e:
        log.warning("grid compute failed for %s: %s", symbol, e)
        return None


# ---------------------------------------------------------------- GEX board (Patch 139): all expiries, watchlist snapshots, replay
def _board_grid(contracts: list, spot: float) -> dict | None:
    """Compact all-expiry grid. Rows are ranked by total gross, then shown by strike."""
    by_exp: dict[date, list] = {}
    for exp, call, k, g, oi, v in _slim(contracts):
        if abs(k - spot) / spot <= _GRID_BAND:
            by_exp.setdefault(exp, []).append((call, k, g, oi, v))
    exps = sorted(by_exp)
    if not exps:
        return None
    net: dict[float, list[float]] = {}
    gross: dict[float, list[float]] = {}
    for ei, exp in enumerate(exps):
        for call, k, g, oi, v in by_exp[exp]:
            ge = float(g) * float(oi) * 100 * float(spot)
            net.setdefault(k, [0.0] * len(exps))[ei] += ge if call else -ge
            gross.setdefault(k, [0.0] * len(exps))[ei] += abs(ge)
    rows = []
    for k, vals in net.items():
        gr = gross[k]
        total = sum(vals)
        total_gross = sum(gr)
        if total or total_gross:
            rows.append({"strike": k, "gex": [round(x, 1) for x in vals],
                         "gross": [round(x, 1) for x in gr],
                         "total": round(total, 1), "total_gross": round(total_gross, 1)})
    if not rows:
        return None
    # Label the full strike curve before the 25-row display cap, so FLIP/TB use
    # true adjacent strikes rather than adjacent rows in the ranked display.
    ordered = sorted(rows, key=lambda r: float(r["strike"]))
    flips = []
    for i, row in enumerate(ordered):
        row["labels"] = []
        if i:
            prior = float(ordered[i - 1]["total"])
            here = float(row["total"])
            if (prior < 0 <= here) or (prior > 0 >= here):
                row["labels"].append("FLIP")
                flips.append(row)
        if 0 < i < len(ordered) - 1:
            a = abs(float(ordered[i - 1]["total"]))
            b = abs(float(row["total"]))
            c = abs(float(ordered[i + 1]["total"]))
            if b > a and b > c:
                row["labels"].append("TB")
    if flips:
        min(flips, key=lambda r: abs(float(r["strike"]) - float(spot)))["labels"].append("GF")
    positive = [r for r in rows if float(r["total"]) > 0]
    negative = [r for r in rows if float(r["total"]) < 0]
    if positive:
        max(positive, key=lambda r: r["total"])["labels"].append("CEILING")
    if negative:
        min(negative, key=lambda r: r["total"])["labels"].append("OS")
    wall_data = _compute(contracts, float(spot), datetime.now(_NY).date())
    if wall_data:
        for wall_key in ("call_wall", "put_wall"):
            wall = wall_data.get(wall_key)
            hit = next((r for r in rows if wall is not None and abs(float(r["strike"]) - float(wall)) < 0.001), None)
            if hit and "CEILING" not in hit["labels"] and "Wall" not in hit["labels"]:
                hit["labels"].append("Wall")
    ranked = sorted(rows, key=lambda r: r["total_gross"], reverse=True)
    selected = ranked[:_GRID_MAX_ROWS]
    # Keep the defining levels visible even when they are not among the 25
    # largest gross strikes; fill by replacing the weakest non-mandatory row.
    mandatory = [r for r in rows if any(tag in r["labels"] for tag in ("GF", "CEILING", "OS", "Wall"))]
    nearest = min(rows, key=lambda r: abs(float(r["strike"]) - float(spot)))
    if nearest not in mandatory:
        mandatory.append(nearest)
    for required in mandatory:
        if required not in selected:
            replace_at = next((i for i in range(len(selected) - 1, -1, -1)
                               if selected[i] not in mandatory), len(selected) - 1)
            selected[replace_at] = required
    rows = sorted(selected, key=lambda r: -r["strike"])
    return {"expiries": [e.isoformat() for e in exps],
            "spot": round(float(spot), 4), "rows": rows}


def _snapshot_path(symbol: str, day: str, hhmm: str) -> Path:
    return _GEX_DIR / f"{symbol.upper()}_{day}_{hhmm}.json"


def _snapshot_times(symbol: str, day: str | None = None) -> list[str]:
    day = day or datetime.now(_NY).date().isoformat()
    prefix = f"{symbol.upper()}_{day}_"
    try:
        return sorted(p.name[len(prefix):-5] for p in _GEX_DIR.glob(prefix + "*.json")
                      if re.fullmatch(r"\d{4}", p.name[len(prefix):-5]))
    except Exception as exc:
        log.warning("GEX replay listing failed for %s: %s", symbol, exc)
        return []


def _read_snapshot(symbol: str, hhmm: str, day: str | None = None) -> dict | None:
    day = day or datetime.now(_NY).date().isoformat()
    try:
        payload = json.loads(_snapshot_path(symbol, day, hhmm).read_text(encoding="utf-8"))
        if payload.get("symbol") != symbol.upper() or payload.get("at", "")[11:16].replace(":", "") != hhmm:
            return None
        return payload
    except Exception:
        return None


def has_snapshots(symbol: str) -> bool:
    return bool(_snapshot_times(symbol))


def _momentum(current: float, previous: float | None) -> float | None:
    if previous is None:
        return None
    return round((current - previous) / max(abs(previous), 1.0) * 100, 1)


def _decorate_board(payload: dict, previous: dict | None, walls_: dict | None) -> dict:
    """Attach per-expiry and total momentum, plus descriptive labels based on total net GEX."""
    rows = payload.get("rows") or []
    prev_by_strike = {round(float(r["strike"]), 4): r for r in (previous or {}).get("rows", [])}
    old_expiries = (previous or {}).get("expiries", [])
    current_expiries = payload.get("expiries", [])
    old_exp_index = {exp: i for i, exp in enumerate(old_expiries)}
    for row in rows:
        old = prev_by_strike.get(round(float(row["strike"]), 4))
        old_gex = old.get("gex", []) if old else []
        row["mom"] = [
            _momentum(v, old_gex[old_exp_index[exp]])
            if old and exp in old_exp_index and old_exp_index[exp] < len(old_gex) else None
            for exp, v in zip(current_expiries, row.get("gex", []))
        ]
        row["total_mom"] = _momentum(float(row.get("total", 0)),
                                      float(old.get("total", 0)) if old else None)
        row.setdefault("labels", [])
    positive = [r for r in rows if float(r.get("total", 0)) > 0]
    weakening = sorted((r for r in positive if r.get("total_mom") is not None and r["total_mom"] < 0),
                       key=lambda r: float(r["total"]), reverse=True)[:_BOARD_RV_TOP]
    for row in weakening:
        row["labels"].append("RV")
    return payload


def snapshot_symbol(symbol: str, spot: float | None) -> bool:
    """Force-refresh one watchlist chain and persist its compact all-expiry grid. Fail-open."""
    sym = (symbol or "").upper()
    try:
        if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", sym) or not spot or float(spot) <= 0:
            return False
        contracts = _fetch_contracts(sym, force=True, spot=float(spot))
        if not contracts:
            return False
        now = datetime.now(_NY)
        now = now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)
        hhmm = now.strftime("%H%M")
        grid_ = _board_grid(contracts, float(spot))
        if not grid_:
            return False
        payload = {"symbol": sym, "at": now.isoformat(timespec="seconds"),
                   "spot": grid_["spot"], "expiries": grid_["expiries"], "rows": grid_["rows"],
                   "walls": _compute(contracts, float(spot), now.date())}
        _GEX_DIR.mkdir(parents=True, exist_ok=True)
        path = _snapshot_path(sym, now.date().isoformat(), hhmm)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False), encoding="utf-8")
        os.replace(tmp, path)
        summed(sym, float(spot), fetch=False)      # also records today's summed gamma for the day-over-day change
        try:      # Patch 227: the shadow log's one writer hook. Cache-only, first snapshot of the day wins, never raises.
            import gex_shadow
            gex_shadow.record(sym, float(spot))
        except Exception as exc:
            log.warning("gex_shadow hook skipped for %s: %s", sym, exc)
        return True
    except Exception as exc:
        log.warning("GEX snapshot skipped for %s: %s", sym, exc)
        return False


def snapshot_watchlist(symbols: list[str], spots: dict[str, object]) -> dict:
    """Snapshot only the shared watchlist, with bounded parallel Cboe requests."""
    now = datetime.now(_NY)
    if now.weekday() >= 5 or now.hour * 60 + now.minute < 9 * 60 + 35 or now.hour * 60 + now.minute > 16 * 60:
        return {"saved": 0, "skipped": len(symbols)}
    try:
        for p in _GEX_DIR.glob("*.json"):
            # Nov 27: prune snapshot files only (SYM_YYYY-MM-DD_HHMM.json). The old test also matched
            # gex_log's wall_log.json in this folder ("wall" != today) and deleted Patch 149's log every 5 minutes.
            m = re.fullmatch(r"[A-Z0-9.\-]+_(\d{4}-\d{2}-\d{2})_\d{4}\.json", p.name)
            if m and m.group(1) != now.date().isoformat():
                try:
                    p.unlink()
                except OSError:
                    pass
    except Exception as exc:
        log.warning("GEX snapshot prune failed: %s", exc)
    def one(sym: str) -> bool:
        item = spots.get(sym) or spots.get(sym.upper())
        price = item.get("price") if isinstance(item, dict) else item
        return snapshot_symbol(sym, price)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(one, list(dict.fromkeys(symbols))))
    return {"saved": sum(bool(x) for x in results), "skipped": len(results) - sum(bool(x) for x in results)}


def board(symbol: str, spot: float | None, at: str | None = None) -> dict | None:
    """All-expiry GEX board. at=HHMM replays a saved watchlist snapshot. Fail-open."""
    try:
        sym = (symbol or "").upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", sym):
            return None
        day = datetime.now(_NY).date().isoformat()
        times = _snapshot_times(sym, day)
        if at is not None:
            if not re.fullmatch(r"\d{4}", at):
                return None
            current = _read_snapshot(sym, at, day)
            if not current:
                return None
            idx = times.index(at) if at in times else -1
            previous = _read_snapshot(sym, times[idx - 1], day) if idx > 0 else None
            payload = {"symbol": sym, "spot": current.get("spot"), "at": at,
                       "expiries": current.get("expiries", []), "rows": current.get("rows", []),
                       "replay": times, "has_snapshots": True}
            return _decorate_board(payload, previous, current.get("walls"))
        if not spot or float(spot) <= 0:
            return None
        with _LOCK:
            contracts = _CHAIN_CACHE.get((sym, date.today().isoformat()), [])
        if not contracts:
            contracts = _fetch_contracts(sym, spot=float(spot))
        if not contracts:
            return None
        grid_ = _board_grid(contracts, float(spot))
        if not grid_:
            return None
        # The in-memory chain is refreshed by the latest scheduled snapshot. Compare it
        # with the snapshot before that refresh, avoiding an artificial zero-momentum read.
        previous = _read_snapshot(sym, times[-2], day) if len(times) >= 2 else None
        w = _compute(contracts, float(spot), datetime.now(_NY).date())
        payload = {"symbol": sym, "spot": grid_["spot"], "at": datetime.now(_NY).strftime("%H:%M"),
                   "expiries": grid_["expiries"], "rows": grid_["rows"],
                   "replay": times, "has_snapshots": bool(times)}
        return _decorate_board(payload, previous, w)
    except Exception as exc:
        log.warning("GEX board failed for %s: %s", symbol, exc)
        return None


def flow_proxy(symbol: str, spot: float | None, direction: int) -> dict | None:
    """Volume-vs-OI proxy from the delayed Cboe chain, not Pulse/tape flow.
    Returns {call_voi, put_voi, side, expiry} or None. Never raises."""
    try:
        if not spot or float(spot) <= 0:
            return None
        sym = (symbol or "").upper()
        with _LOCK:
            contracts = _CHAIN_CACHE.get((sym, date.today().isoformat()), [])
        if not contracts:
            return None
        today_ny = datetime.now(ZoneInfo("America/New_York")).date()
        expiries = sorted({x[0] for x in contracts
                           if len(x) >= 6 and (x[0] - today_ny).days >= 1 and x[5] > 0})
        if not expiries:
            return None
        exp = expiries[0]
        near = [x for x in contracts if len(x) >= 6 and x[0] == exp
                and abs(float(x[2]) - float(spot)) / float(spot) <= 0.10]
        if not near or not any(x[5] > 0 for x in near):
            return None
        totals = {True: [0.0, 0.0], False: [0.0, 0.0]}
        for _, call, strike, gamma_value, oi, volume in near:
            totals[bool(call)][0] += float(volume or 0)
            totals[bool(call)][1] += float(oi or 0)
        call_voi = round(totals[True][0] / totals[True][1], 2) if totals[True][1] > 0 else None
        put_voi = round(totals[False][0] / totals[False][1], 2) if totals[False][1] > 0 else None
        side = "call" if call_voi is not None and put_voi is not None and call_voi > 2 and call_voi > put_voi else (
               "put" if call_voi is not None and put_voi is not None and put_voi > 2 and put_voi > call_voi else "none")
        return {"call_voi": call_voi, "put_voi": put_voi, "side": side, "expiry": exp.isoformat()}
    except Exception as e:
        log.warning("flow proxy failed for %s: %s", symbol, e)
        return None


def attach(c: dict, fetch: bool = True) -> dict:
    """Add wall fields to a candidate dict (symbol, d, edge, target, price), in place. Never raises.
    A call wall (put wall for shorts) between the edge and the target becomes the target."""
    c["gamma_wall"] = c["gamma_flip"] = None
    c["wall_in_path"] = c["gamma_capped"] = False
    c["gamma_expiry"] = c["gamma_call_wall"] = c["gamma_put_wall"] = c["gamma_strongest"] = None
    try:
        d, edge, target = c["d"], c["edge"], c["target"]
        if not edge or not target:
            return c
        w = walls(c["symbol"], c.get("price"), fetch=fetch)
        if not w:
            return c
        wall = w["call_wall"] if d == 1 else w["put_wall"]
        c["gamma_wall"], c["gamma_flip"] = wall, w["flip"]
        # Expiry + both walls + strongest pull on the same monthly horizon as the walls.
        c["gamma_expiry"] = w.get("expiry")
        c["gamma_call_wall"], c["gamma_put_wall"] = w.get("call_wall"), w.get("put_wall")
        sh = shelves(c["symbol"], c.get("price"), fetch=fetch)
        if sh and sh.get("shelves"):
            strongest = max(sh["shelves"], key=lambda r: abs(r["gex"]))
            c["gamma_strongest"] = strongest["strike"]
        if wall and (edge < wall < target if d == 1 else target < wall < edge):
            c["wall_in_path"] = c["gamma_capped"] = True
            c["shelf_target"] = target
            c["target"] = wall            # the wall, not the shelf, is the take-profit now
    except Exception as e:
        log.warning("attach failed for %s: %s", c.get("symbol"), e)
    return c


def line(c: dict) -> str:
    """One-line wall summary for an alert / paper message. '' when no data.
    Adds the expiry (+ days out), both walls, and strongest monthly pull."""
    try:
        wall, flip, target = c.get("gamma_wall"), c.get("gamma_flip"), c["target"]
        if not wall:
            return ""
        d, edge = c["d"], c["edge"]
        name = "Call wall" if d == 1 else "Put wall"
        pct = (wall / edge - 1) * 100 * d
        exp_s = ""
        exp = c.get("gamma_expiry")
        if exp:
            try:
                days = (date.fromisoformat(exp) - date.today()).days
                exp_d = date.fromisoformat(exp).strftime("%b %d")
                exp_s = f" · GEX {exp_d} ({days}d)"
            except Exception:
                exp_s = f" · GEX {exp}"
        other = c.get("gamma_put_wall") if d == 1 else c.get("gamma_call_wall")
        other_name = "put wall" if d == 1 else "call wall"
        other_s = f" · {other_name} ${other:.2f}" if other else ""
        sp = c.get("gamma_strongest")
        sp_s = f" · strongest pull ${sp:.2f}" if sp else ""
        if c.get("wall_in_path"):
            return f"🧱 {name} ${wall:.2f} ({pct:+.1f}% from entry) · target capped at the wall{other_s}{sp_s}{exp_s}"
        flip_s = f" · flip ${flip:.2f}" if flip else ""
        return f"🧱 Open gamma to ${target:.2f} · {name.lower()} ${wall:.2f}{other_s}{flip_s}{sp_s}{exp_s}"
    except Exception:
        return ""


# ---------------------------------------------------------------- Patch 227: precise GEX, SHADOW MODE
# A second computation beside _compute/grid, for gex_shadow's log only. Nothing displayed reads it, it never
# fetches (cache-only), and it never raises. Same expiry selection, strikes, OI and sign convention (calls +,
# puts -) as the current one: only gamma's sigma source (a per-expiry IV smile) and the flip method change.
_SURF_CACHE: dict[tuple[str, str, str], object] = {}    # (symbol, day, expiry) -> fitted smile or None; dies with the day
_SURF_R = 0.04                                          # matches _vanna


def _smile_fit(points: list, spot: float):
    """points: [(strike, iv)]. Fits sigma(m) = a + b*m + c*m^2, m = ln(K/S), by least squares (3x3 normal
    equations, pure Python). Returns a callable sigma(K, S) with the spec's clamps, or None if under-determined."""
    import math
    if len(points) < 6 or len({k for k, _ in points}) < 4:
        return None
    xs = [(math.log(k / spot), iv) for k, iv in points if k > 0 and iv > 0]
    if len(xs) < 6:
        return None
    s = [sum(m ** p for m, _ in xs) for p in range(5)]                 # sums of m^0..m^4
    t = [sum(iv * m ** p for m, iv in xs) for p in range(3)]           # sums of iv*m^0..m^2
    A = [[s[0], s[1], s[2]], [s[1], s[2], s[3]], [s[2], s[3], s[4]]]
    det = lambda M: (M[0][0] * (M[1][1] * M[2][2] - M[1][2] * M[2][1]) - M[0][1] * (M[1][0] * M[2][2] - M[1][2] * M[2][0])
                     + M[0][2] * (M[1][0] * M[2][1] - M[1][1] * M[2][0]))
    D = det(A)
    if not D or abs(D) < 1e-18:
        return None
    coef = []
    for i in range(3):
        M = [row[:] for row in A]
        for r in range(3):
            M[r][i] = t[r]
        coef.append(det(M) / D)
    a, b, c = coef
    ks = sorted(points)
    k_lo, k_hi = ks[0][0], ks[-1][0]
    iv_lo = sum(iv for k, iv in points if k == k_lo) / sum(1 for k, _ in points if k == k_lo)
    iv_hi = sum(iv for k, iv in points if k == k_hi) / sum(1 for k, _ in points if k == k_hi)

    def sigma(k: float, s_ref: float) -> float:
        m = math.log(k / s_ref)
        v = a + b * m + c * m * m
        if k < k_lo:
            v = min(max(v, 0.5 * iv_lo), 2 * iv_lo)
        elif k > k_hi:
            v = min(max(v, 0.5 * iv_hi), 2 * iv_hi)
        return min(max(v, 0.05), 2.50)
    sigma.n = len(points)
    return sigma


def _surface(sym: str, day: str, exp: date, spot: float, contracts_exp: list, ivs: dict):
    """The smile for one expiry, cached per (symbol, day, expiry). contracts_exp: [(call, k, g, oi, v)]."""
    key = (sym, day, exp.isoformat())
    with _LOCK:
        if key in _SURF_CACHE:
            return _SURF_CACHE[key]
    pts = [(k, ivs[(exp, call, k)]) for call, k, g, oi, v in contracts_exp
           if oi > 0 and abs(k - spot) / spot <= _BAND and ivs.get((exp, call, k))]
    fit = _smile_fit(pts, spot)
    with _LOCK:
        for k in [k for k in _SURF_CACHE if k[1] != day]:
            del _SURF_CACHE[k]
        _SURF_CACHE[key] = fit
    return fit


def _bs_gamma(s: float, k: float, t: float, iv: float, r: float = _SURF_R) -> float:
    import math
    try:
        if s <= 0 or k <= 0 or t <= 0 or iv <= 0:
            return 0.0
        sq = iv * math.sqrt(t)
        d1 = (math.log(s / k) + (r + iv * iv / 2) * t) / sq
        return math.exp(-d1 * d1 / 2) / math.sqrt(2 * math.pi) / (s * sq)
    except (ValueError, ZeroDivisionError, OverflowError):
        return 0.0


def precise_shadow(symbol: str, spot: float | None) -> dict | None:
    """Patch 227 shadow numbers from the CACHED chain only: {'cur': current walls, 'new': precise walls or None,
    'reason', 'expiry', 'surface_points', 'vex_cur', 'vex_new'}. None when no chain is cached. Never fetches, never raises."""
    import math
    try:
        sym = (symbol or "").upper()
        if not spot or float(spot) <= 0:
            return None
        spot = float(spot)
        day = date.today().isoformat()
        with _LOCK:
            contracts = list(_CHAIN_CACHE.get((sym, day), []))
            ivs = dict(_IV_CACHE.get((sym, day), {}))
        if not contracts:
            return None
        today = date.today()
        cur = _compute(contracts, spot, today)
        out = {"cur": {k: (cur or {}).get(k) for k in ("call_wall", "put_wall", "flip")} if cur else None,
               "expiry": (cur or {}).get("expiry"), "new": None, "reason": None, "surface_points": 0,
               "vex_cur": None, "vex_new": None}
        slim = _slim(contracts)
        # ---- walls + solved flip, on _compute's expiry
        by_exp: dict[date, list] = {}
        for exp, call, k, g, oi, v in slim:
            if abs(k - spot) / spot <= _BAND:
                by_exp.setdefault(exp, []).append((call, k, g, oi, v))
        if not cur:
            out["reason"] = "no_walls"
        elif not ivs:
            out["reason"] = "no_iv"
        else:
            exp = date.fromisoformat(cur["expiry"])
            rows = by_exp.get(exp, [])
            fit = _surface(sym, day, exp, spot, rows, ivs)
            if not fit:
                out["reason"] = "fit_failed"
            else:
                out["surface_points"] = getattr(fit, "n", 0)
                t = max((exp - today).days, 1) / 365
                cg: dict[float, float] = {}
                pg: dict[float, float] = {}
                for call, k, g, oi, v in rows:
                    book = cg if call else pg
                    book[k] = book.get(k, 0.0) + _bs_gamma(spot, k, t, fit(k, spot)) * oi * 100
                cg = {k: x for k, x in cg.items() if x > 0}
                pg = {k: x for k, x in pg.items() if x > 0}

                def net_at(s2: float) -> float:        # sticky-strike: sigma evaluated at m = ln(K/S')
                    return sum((1 if call else -1) * _bs_gamma(s2, k, t, fit(k, s2)) * oi * 100 for call, k, g, oi, v in rows)
                flip, prev_s, prev_v = None, None, None
                steps = int(round((1.25 - 0.80) / 0.0025))
                for i in range(steps + 1):
                    s2 = spot * (0.80 + 0.0025 * i)
                    val = net_at(s2)
                    if prev_v is not None and prev_v < 0 <= val:
                        flip = prev_s + (s2 - prev_s) * (-prev_v) / (val - prev_v) if val != prev_v else s2
                        break
                    prev_s, prev_v = s2, val
                out["new"] = {"call_wall": max(cg, key=cg.get) if cg else None,
                              "put_wall": max(pg, key=pg.get) if pg else None,
                              "flip": round(flip, 2) if flip is not None else None}
        # ---- net VEX over grid()'s contract set: current IV vs the surface's sigma
        if ivs:
            gb: dict[date, list] = {}
            for exp, call, k, g, oi, v in slim:
                if abs(k - spot) / spot <= _GRID_BAND:
                    gb.setdefault(exp, []).append((call, k, g, oi, v))
            vc = vn = 0.0
            fitted = 0
            exps_v = sorted(gb)[:_GRID_MAX_EXP]
            for exp in exps_v:
                fit_e = _surface(sym, day, exp, spot, by_exp.get(exp, gb[exp]), ivs)
                fitted += bool(fit_e)
                t = max(0.5, (exp - today).days) / 365
                for call, k, g, oi, v in gb[exp]:
                    iv = ivs.get((exp, call, k))
                    if not (iv and oi):
                        continue
                    sgn = 1 if call else -1
                    vc += sgn * _vanna(spot, k, t, iv) * 0.01 * oi * 100 * spot
                    # an expiry whose smile can't be fitted keeps its own IVs in both sums, so the delta is the surface's alone
                    vn += sgn * _vanna(spot, k, t, fit_e(k, spot) if fit_e else iv) * 0.01 * oi * 100 * spot
            out["vex_cur"] = round(vc, 1)
            out["vex_new"] = round(vn, 1) if fitted else None
            out["vex_fitted_expiries"] = f"{fitted}/{len(exps_v)}"
        return out
    except Exception as e:
        log.warning("precise_shadow failed for %s: %s", symbol, e)
        return None
