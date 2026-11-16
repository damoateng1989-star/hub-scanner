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


def _fetch_contracts(symbol: str, force: bool = False) -> list:
    """Chain for (symbol, today), fetched once a day and kept slim. [] on failure (fail-open). Never raises."""
    sym = (symbol or "").upper()
    today = date.today().isoformat()
    key = (sym, today)
    with _LOCK:
        if not force and key in _CHAIN_CACHE:
            return _CHAIN_CACHE[key]
    if not force and time.time() - _FAILED.get(sym, 0) < _RETRY:
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
            contracts = _fetch_contracts(sym)
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
            ge = g * oi * 100
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
_GRID_MAX_ROWS = 25
_GRID_MAX_EXP = 4


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
            contracts = _fetch_contracts(sym)
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
        for ei, exp in enumerate(exps):
            for call, k, g, oi, v in by_exp[exp]:
                ge = g * oi * 100
                row = net.setdefault(k, [0.0] * len(exps))
                row[ei] += ge if call else -ge
                grow = gross.setdefault(k, [0.0] * len(exps))
                grow[ei] += abs(ge)
        rows = [{"strike": k, "gex": [round(x, 1) for x in vals],
                 "gross": [round(x, 1) for x in gross[k]]}
                for k, vals in net.items() if any(vals) or any(gross[k])]
        if not rows:
            return None
        rows.sort(key=lambda r: -sum(abs(x) for x in r["gex"]))
        rows = rows[:_GRID_MAX_ROWS]
        rows.sort(key=lambda r: -r["strike"])
        return {"expiries": [e.isoformat() for e in exps], "spot": spot, "rows": rows}
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
            ge = float(g) * float(oi) * 100
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
    rows.sort(key=lambda r: r["total_gross"], reverse=True)
    rows = rows[:_GRID_MAX_ROWS]
    rows.sort(key=lambda r: -r["strike"])
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
    for row in rows:
        old = prev_by_strike.get(round(float(row["strike"]), 4))
        old_gex = old.get("gex", []) if old else []
        row["mom"] = [_momentum(v, old_gex[i] if i < len(old_gex) else None)
                      for i, v in enumerate(row.get("gex", []))]
        row["total_mom"] = _momentum(float(row.get("total", 0)),
                                      float(old.get("total", 0)) if old else None)
        row["labels"] = []
    ordered = sorted(rows, key=lambda r: float(r["strike"]))
    flips = []
    for i in range(1, len(ordered)):
        a, b = float(ordered[i - 1].get("total", 0)), float(ordered[i].get("total", 0))
        if (a < 0 <= b) or (a > 0 >= b):
            ordered[i]["labels"].append("FLIP")
            flips.append(ordered[i])
    positive = [r for r in rows if float(r.get("total", 0)) > 0]
    negative = [r for r in rows if float(r.get("total", 0)) < 0]
    if positive:
        max(positive, key=lambda r: r["total"])["labels"].append("CEILING")
    if negative:
        min(negative, key=lambda r: r["total"])["labels"].append("OS")
    if walls_:
        for key in ("call_wall", "put_wall"):
            wall = walls_.get(key)
            if wall is not None:
                hit = next((r for r in rows if abs(float(r["strike"]) - float(wall)) < 0.001), None)
                if hit and "CEILING" not in hit["labels"]:
                    hit["labels"].append("Wall")
    if flips:
        min(flips, key=lambda r: abs(float(r["strike"]) - float(payload.get("spot") or 0)))["labels"].append("GF")
    for i in range(1, len(ordered) - 1):
        a, b, c = (abs(float(ordered[j].get("total", 0))) for j in (i - 1, i, i + 1))
        if b > a and b > c:
            ordered[i]["labels"].append("TB")
    weakening = sorted((r for r in positive if r.get("total_mom") is not None and r["total_mom"] < 0),
                       key=lambda r: float(r["total"]), reverse=True)[:_BOARD_RV_TOP]
    for row in weakening:
        row["labels"].append("RV")
    return payload


def snapshot_symbol(symbol: str, spot: float | None) -> bool:
    """Force-refresh one watchlist chain and persist its compact all-expiry grid. Fail-open."""
    sym = (symbol or "").upper()
    try:
        if not re.fullmatch(r"[A-Z][A-Z0-9.\\-]{0,9}", sym) or not spot or float(spot) <= 0:
            return False
        contracts = _fetch_contracts(sym, force=True)
        if not contracts:
            return False
        now = datetime.now(_NY)
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
            if p.name.rsplit("_", 2)[-2] != now.date().isoformat():
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
        if not re.fullmatch(r"[A-Z][A-Z0-9.\\-]{0,9}", sym):
            return None
        day = datetime.now(_NY).date().isoformat()
        times = _snapshot_times(sym, day)
        if at is not None:
            if not re.fullmatch(r"\\d{4}", at):
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
            contracts = _fetch_contracts(sym)
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
