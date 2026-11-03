"""Options swing plans: how a NextShelf breakout could be expressed with calls or puts.

Educational only. Uses the live option chain (Alpaca; demo prices otherwise) and Black-Scholes to estimate
what a contract might be worth if price reaches the next shelf, or if the breakout fails.
Estimates assume implied volatility stays the same; real prices will differ.
"""
from __future__ import annotations

import math
import os
import re
import time
from datetime import date, timedelta

import numpy as np
import pandas as pd
import requests

import data
import engine

MIN_DTE, PREF_DTE, MAX_DTE = 14, 21, 45     # days to expiry: room for the swing without paying for months
RATE = 0.04                                   # risk-free rate for estimates
_CACHE: dict[str, tuple[float, list]] = {}
OCC = re.compile(r"^([A-Z.]+)(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")


# ---------------------------------------------------------------- Black-Scholes
def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(S: float, K: float, T: float, iv: float, call: bool) -> float:
    """Option value; T in years. At or past expiry it's the intrinsic value."""
    if T <= 1e-6 or iv <= 1e-6:
        return max(0.0, (S - K) if call else (K - S))
    d1 = (math.log(S / K) + (RATE + iv * iv / 2) * T) / (iv * math.sqrt(T))
    d2 = d1 - iv * math.sqrt(T)
    if call:
        return S * _ncdf(d1) - K * math.exp(-RATE * T) * _ncdf(d2)
    return K * math.exp(-RATE * T) * _ncdf(-d2) - S * _ncdf(-d1)


def bs_delta(S: float, K: float, T: float, iv: float, call: bool) -> float:
    if T <= 1e-6 or iv <= 1e-6:
        return (1.0 if S > K else 0.0) if call else (-1.0 if S < K else 0.0)
    d1 = (math.log(S / K) + (RATE + iv * iv / 2) * T) / (iv * math.sqrt(T))
    return _ncdf(d1) if call else _ncdf(d1) - 1


# ---------------------------------------------------------------- option chain
def _parse(occ: str):
    m = OCC.match(occ)
    if not m:
        return None
    _, yy, mm, dd, cp, k = m.groups()
    return date(2000 + int(yy), int(mm), int(dd)), cp == "C", int(k) / 1000


def chain(sym: str, spot: float, iv_guess: float) -> list[dict]:
    """Contracts expiring in MIN_DTE..MAX_DTE days with strikes within ~15% of spot."""
    key = f"{sym}|{round(spot, 1)}"
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    rows = _alpaca_chain(sym, spot) if data.provider() == "alpaca" else _demo_chain(sym, spot, iv_guess)
    _CACHE[key] = (time.time(), rows)
    return rows


def _alpaca_chain(sym: str, spot: float) -> list[dict]:
    key, secret = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("ALPACA_API_KEY and ALPACA_SECRET_KEY are not set")
    today = date.today()
    params = dict(feed=os.getenv("ALPACA_OPTIONS_FEED", "indicative"), limit=1000,
                  expiration_date_gte=(today + timedelta(days=MIN_DTE - 4)).isoformat(),
                  expiration_date_lte=(today + timedelta(days=MAX_DTE)).isoformat(),
                  strike_price_gte=round(spot * 0.85, 2), strike_price_lte=round(spot * 1.15, 2))
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    out, token = [], None
    for _ in range(5):
        if token:
            params["page_token"] = token
        r = requests.get(f"https://data.alpaca.markets/v1beta1/options/snapshots/{sym.replace('.', '')}",
                         headers=headers, params=params, timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"Alpaca options error {r.status_code}: {r.text[:200]}")
        j = r.json()
        for occ, snap in (j.get("snapshots") or {}).items():
            p = _parse(occ)
            if not p:
                continue
            exp, call, k = p
            q, t, g = snap.get("latestQuote") or {}, snap.get("latestTrade") or {}, snap.get("greeks") or {}
            out.append(dict(symbol=occ, expiry=exp, call=call, strike=k, bid=float(q.get("bp") or 0), ask=float(q.get("ap") or 0),
                            last=float(t.get("p") or 0), iv=snap.get("impliedVolatility"), delta=g.get("delta")))
        token = j.get("next_page_token")
        if not token:
            break
    return out


def _step(spot: float) -> float:
    return 1 if spot < 50 else 2.5 if spot < 200 else 5 if spot < 600 else 10


def _demo_chain(sym: str, spot: float, iv: float) -> list[dict]:
    """Made-up but realistic chain for the demo: weekly Friday expiries, Black-Scholes prices, a small spread."""
    today, out, step = date.today(), [], _step(spot)
    fridays = [today + timedelta(days=d) for d in range(MIN_DTE - 4, MAX_DTE + 1) if (today + timedelta(days=d)).weekday() == 4]
    k0 = round(spot / step) * step
    for exp in fridays:
        T = (exp - today).days / 365
        for i in range(-8, 9):
            k = round(k0 + i * step, 2)
            if k <= 0:
                continue
            for call in (True, False):
                v = bs_price(spot, k, T, iv, call)
                if v < 0.05:
                    continue
                half = max(0.02, v * 0.015)
                occ = f"{sym.replace('.', '')}{exp:%y%m%d}{'C' if call else 'P'}{int(round(k * 1000)):08d}"
                out.append(dict(symbol=occ, expiry=exp, call=call, strike=k, bid=round(v - half, 2), ask=round(v + half, 2),
                                last=round(v, 2), iv=iv, delta=bs_delta(spot, k, T, iv, call)))
    return out


# ---------------------------------------------------------------- the plan
def _realized_iv(close: pd.Series) -> float:
    """Annualized volatility from 4H closes (2 candles per session, 252 sessions)."""
    r = np.log(close).diff().dropna().tail(120)
    v = float(r.std() * math.sqrt(2 * 252)) if len(r) > 10 else 0.35
    return min(max(v, 0.12), 1.5)


def _history(res: dict, d: int) -> dict:
    tr = [t for t in res["trades"] if t["outcome"] != "pending" and t["direction"] == d]
    hit = [t for t in tr if t["outcome"] == "follow-through" and not t["chase"]]
    days = sorted((t["oi"] - t["i0"]) / 2 for t in hit)       # 2 candles per trading day
    return dict(n=len(tr), reached=len(hit), reached_pct=round(100 * len(hit) / len(tr), 1) if tr else None,
                median_days=days[len(days) // 2] if days else None, slowest_days=days[-1] if days else None)


def _pick_expiry(rows: list[dict], call: bool, need_days: float) -> date | None:
    today = date.today()
    exps = sorted({r["expiry"] for r in rows if r["call"] == call and MIN_DTE <= (r["expiry"] - today).days <= MAX_DTE})
    if not exps:
        exps = sorted({r["expiry"] for r in rows if r["call"] == call and (r["expiry"] - today).days >= 7})
    if not exps:
        return None
    want = max(PREF_DTE, int(need_days * 7 / 5 * 3))            # ~3x the typical time to the shelf, in calendar days
    return min(exps, key=lambda e: abs((e - today).days - want))


def _contract_view(c: dict, S_entry: float, target: float, fail_px: float, days_to_target: float, iv_fallback: float, label: str,
                   prep: bool = False) -> dict:
    today = date.today()
    dte = (c["expiry"] - today).days
    call = c["call"]
    quote = (c["bid"] + c["ask"]) / 2 if c["bid"] > 0 and c["ask"] > 0 else c["last"]
    iv = float(c["iv"]) if c.get("iv") else iv_fallback
    T0 = dte / 365
    # waiting for the breakout: estimate what it would cost with the stock AT the hub edge, not where it is now
    mid = bs_price(S_entry, c["strike"], T0, iv, call) if prep else quote
    delta = bs_delta(S_entry, c["strike"], T0, iv, call) if prep or c.get("delta") is None else float(c["delta"])
    t_hit = max(0.0, dte - days_to_target * 7 / 5) / 365
    at_shelf = bs_price(target, c["strike"], t_hit, iv, call)
    at_fail = bs_price(fail_px, c["strike"], max(0.0, dte - 1) / 365, iv, call)
    spread = (c["ask"] - c["bid"]) / mid if mid and c["bid"] > 0 else None
    return dict(label=label, symbol=c["symbol"], strike=c["strike"], bid=round(c["bid"], 2), ask=round(c["ask"], 2), mid=round(mid, 2),
                delta=round(abs(delta), 2), iv=round(iv * 100, 1), breakeven=round(c["strike"] + mid if call else c["strike"] - mid, 2),
                cost=round(mid * 100, 2), at_shelf=round(at_shelf, 2), at_shelf_pct=round((at_shelf / mid - 1) * 100) if mid else None,
                at_fail=round(at_fail, 2), at_fail_pct=round((at_fail / mid - 1) * 100) if mid else None,
                wide_spread=bool(spread and spread > 0.15), quote_now=round(quote, 2), estimated=prep)


def _one(sym: str, rows: list[dict], d: int, entry: float, edge: float, target: float, target_kind: str,
         res: dict, iv_fallback: float, mode: str, setup: str) -> dict | None:
    call = d == 1
    hist = _history(res, d)
    need = max(hist["median_days"] or 3, 2) if hist["reached"] >= 3 else 3     # too few examples: assume ~3 trading days
    atr = float(res["atr"].iloc[-1])
    fail_px = edge - d * 0.3 * atr            # a failed breakout usually closes a little back inside the hub
    exp = _pick_expiry(rows, call, need)
    if not exp:
        return None
    cs = sorted([r for r in rows if r["call"] == call and r["expiry"] == exp], key=lambda r: r["strike"])
    if not cs:
        return None
    atm = min(cs, key=lambda r: abs(r["strike"] - entry))
    i = cs.index(atm)
    itm = cs[i - 1] if call and i > 0 else cs[i + 1] if not call and i + 1 < len(cs) else None
    prep = mode == "prep"
    views = [_contract_view(atm, entry, target, fail_px, need, iv_fallback, "At the money", prep)]
    if itm:
        views.append(_contract_view(itm, entry, target, fail_px, need, iv_fallback, "One strike in the money", prep))
    return dict(direction=d, side="call" if call else "put", setup=setup, mode=mode, entry=round(entry, 2), edge=round(edge, 2),
                target=round(target, 2), target_kind=target_kind, target_pct=round((target / entry - 1) * 100, 2),
                expiry=exp.isoformat(), dte=(exp - date.today()).days, contracts=views, history=hist,
                days_to_target=need, fail_px=round(fail_px, 2))


_FROZEN: dict[str, tuple[str, dict]] = {}   # symbol -> (session, plan) kept while the market is closed


def _session_key(now: pd.Timestamp | None = None) -> str:
    """The trading session whose close prices we're showing: today after the open on a weekday, else the last weekday."""
    t = (now or pd.Timestamp.now(tz="UTC")).tz_convert("America/New_York")
    d = t.normalize()
    if t.weekday() >= 5 or t.hour * 60 + t.minute < 9 * 60 + 30:
        d -= pd.Timedelta(days=1)
        while d.weekday() >= 5:
            d -= pd.Timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def plan(sym: str) -> dict:
    """While the market is closed the plan is frozen at the last close: quotes don't move, so the numbers shouldn't either."""
    import scanner
    sym = scanner.clean_symbol(sym)
    if data.market_open():
        _FROZEN.pop(sym, None)
        out = _plan(sym)
        out.update(market_open=True, as_of=pd.Timestamp.now(tz="UTC").isoformat())
        return out
    key = _session_key()
    hit = _FROZEN.get(sym)
    if hit and hit[0] == key:
        return hit[1]
    out = _plan(sym)
    out.update(market_open=False, as_of=pd.Timestamp.now(tz="UTC").isoformat(), session=key)
    _FROZEN[sym] = (key, out)
    return out


def _plan(sym: str) -> dict:
    import patterns
    import scanner
    sym = scanner.clean_symbol(sym)
    c4 = scanner.load_4h(sym, scanner.SCAN_DAYS)      # same lookback as the scanner and the board
    if len(c4) < 40:
        raise ValueError("not enough price history")
    S = scanner.SETTINGS
    res = engine.analyze(c4, S)
    st = engine.current_status(c4, res, S)
    last = float(res["close"].iloc[-1])
    iv0 = _realized_iv(res["close"])
    out = dict(symbol=sym, price=round(last, 2), demo=data.provider() == "demo", plans=[], mode="none")
    try:
        import earnings
        out["earnings"] = earnings.lookup(sym)
    except Exception:
        out["earnings"] = None
    rows = chain(sym, last, iv0)
    if not rows:
        out["message"] = "No listed options were found for this stock."
        return out
    state = st.get("state")
    if state in ("breakout_up", "breakdown"):
        d = 1 if state == "breakout_up" else -1
        h = st["hub"]
        p = _one(sym, rows, d, last, h["high"] if d == 1 else h["low"], st["target"], st.get("target_kind", "atr"), res, iv0, "live", "Hub")
        out.update(mode="live", plans=[p] if p else [])
    else:
        try:      # a live breakout on another setup (triangle, channel, support/resistance)
            pa = patterns.analyze_all(c4, res, S)
            lv = next((x for x in pa["live"] if x["state"] in ("breakout_up", "breakdown")), None)
        except Exception:
            lv = None
        if lv:
            t = lv["trade"]
            p = _one(sym, rows, t["direction"], last, t["edge"], t["target"], lv["target_kind"], res, iv0, "live", patterns.KINDS[lv["kind"]])
            out.update(mode="live", plans=[p] if p else [])
        elif state in ("forming", "watching") and st.get("hub"):
            h, up, dn = st["hub"], st.get("shelf_up"), st.get("shelf_down")
            atr = float(res["atr"].iloc[-1])
            pl = []
            for d, edge, sh in ((1, h["high"], up), (-1, h["low"], dn)):
                tgt = sh["level"] if sh else edge + d * S.target_atr * atr
                p = _one(sym, rows, d, edge, edge, tgt, "shelf" if sh else "atr", res, iv0, "prep", "Hub")
                if p:
                    pl.append(p)
            out.update(mode="prep", plans=pl)
        else:
            out["message"] = "No breakout or hub right now, so there's no options swing to plan. Let the trade come to you."
    return out


def alert_line(sym: str, d: int, close: float, edge: float, target: float) -> str | None:
    """One-line options idea for Pro alerts (educational)."""
    try:
        p = plan(sym)
        pl = next((x for x in p["plans"] if x["direction"] == d), None)
        if not pl or not pl["contracts"]:
            return None
        c = pl["contracts"][0]
        exp = date.fromisoformat(pl["expiry"]).strftime("%b %-d")
        gain = f" · est. {c['at_shelf_pct']:+d}% at the shelf" if c.get("at_shelf_pct") is not None else ""
        return (f"🎯 Options swing (educational): {exp} ${c['strike']:g} {pl['side']}s ({pl['dte']} days) ≈ ${c['mid']:.2f}"
                f"{gain} · max risk ${c['cost']:,.0f}/contract")
    except Exception:
        return None
