"""Nov 28 (owner): GEX benchmark — NextShelf vs Bullflow. ADMIN-ONLY MEASURING INSTRUMENT.

Follows "GEX Benchmark Protocol: NextShelf vs Bullflow" (the doc). Bullflow numbers are never shown to members,
never feed an alert, grade, Sika answer or export: they are read, compared with ours, scored, and logged.

Readings: 10:00, 12:00 and 15:30 ET on trading days, for BULLFLOW_SYMBOLS (default SPY, QQQ + four watchlist names).
Needs BULLFLOW_API_KEY on the server; without it every call is a no-op.

Three layers, each compared like with like (both sides computed the same way from per-strike, per-expiry net GEX):
  nearest  — nearest expiry that has gamma (our glow / largest-node layer)
  swing    — nearest monthly expiry 7+ days out (our Shelf-card walls' framing)
  all      — every expiry added up (our heat map)
Scores per layer: largest node, call wall (max +GEX), put wall (min −GEX), flip (zero cross nearest spot),
top-3 positive / negative overlap, Spearman rank correlation of net GEX by strike within ±10% of spot.
Dollar sizes differ by construction (theirs: $ per 1% move; ours: $ per $1 × spot), so scores use position and rank.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("bullflow_bench")
NY = ZoneInfo("America/New_York")
LOG_PATH = Path(os.getenv("DATA_DIR", "data")) / "bullflow_bench.json"
_LOCK = threading.Lock()
API = "https://api.bullflow.io/v1/data/netgex"
BAND = 0.10


def enabled() -> bool:
    return bool(os.getenv("BULLFLOW_API_KEY", "").strip())


def symbols() -> list[str]:
    raw = os.getenv("BULLFLOW_SYMBOLS", "SPY,QQQ,PANW,AAPL,NVDA,TSLA")
    return [s.strip().upper() for s in raw.split(",") if s.strip()][:10]


# ---------------------------------------------------------------- shared maths
def _third_friday(d: date) -> bool:
    return d.weekday() == 4 and 15 <= d.day <= 21


def _layers(per_exp: dict[date, dict[float, float]], today: date) -> dict[str, dict[float, float]]:
    """per_exp: expiry -> {strike: net}. Returns {layer: {strike: net}}."""
    exps = sorted(e for e in per_exp if e >= today)
    out: dict[str, dict[float, float]] = {}
    near = next((e for e in exps if any(per_exp[e].values())), None)
    if near:
        out["nearest"] = dict(per_exp[near]); out["_nearest_exp"] = near.isoformat()      # type: ignore
    swing = next((e for e in exps if (e - today).days >= 7 and _third_friday(e)), None) \
        or next((e for e in exps if (e - today).days >= 7), None)
    if swing:
        out["swing"] = dict(per_exp[swing]); out["_swing_exp"] = swing.isoformat()        # type: ignore
    tot: dict[float, float] = {}
    for e in exps:
        for k, v in per_exp[e].items():
            tot[k] = tot.get(k, 0.0) + v
    if tot:
        out["all"] = tot
    return out


def _levels(net: dict[float, float], spot: float) -> dict:
    band = {k: v for k, v in net.items() if abs(k / spot - 1) <= 0.20}
    if not band:
        return {}
    pos = sorted((kv for kv in band.items() if kv[1] > 0), key=lambda kv: -kv[1])
    neg = sorted((kv for kv in band.items() if kv[1] < 0), key=lambda kv: kv[1])
    largest = max(band.items(), key=lambda kv: abs(kv[1]))[0]
    flip = None
    ks = sorted(band)
    cum, prev_k, prev_c, best = 0.0, None, None, None
    for k in ks:
        cum += band[k]
        if prev_c is not None and (prev_c <= 0 < cum or prev_c >= 0 > cum):
            x = prev_k + (k - prev_k) * (abs(prev_c) / (abs(prev_c) + abs(cum))) if (prev_c - cum) else k
            if best is None or abs(x - spot) < abs(best - spot):
                best = x
        prev_k, prev_c = k, cum
    flip = round(best, 2) if best is not None else None
    return dict(largest=largest, call_wall=pos[0][0] if pos else None, put_wall=neg[0][0] if neg else None,
                flip=flip, top_pos=[k for k, _ in pos[:3]], top_neg=[k for k, _ in neg[:3]])


def _spearman(a: dict[float, float], b: dict[float, float], spot: float) -> float | None:
    ks = [k for k in a if k in b and abs(k / spot - 1) <= BAND]
    if len(ks) < 5:
        return None
    def ranks(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        r = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            for t in range(i, j + 1):
                r[order[t]] = (i + j) / 2
            i = j + 1
        return r
    ra, rb = ranks([a[k] for k in ks]), ranks([b[k] for k in ks])
    n = len(ks); ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = sum((x - ma) ** 2 for x in ra) ** .5; vb = sum((y - mb) ** 2 for y in rb) ** .5
    return round(cov / (va * vb), 3) if va and vb else None


def _strike_step(ks: list[float]) -> float:
    ks = sorted(set(ks))
    d = sorted(b - a for a, b in zip(ks, ks[1:]) if b - a > 0)
    return d[len(d) // 2] if d else 1.0


def _score(ours: dict, theirs: dict, o_net: dict, t_net: dict, spot: float) -> dict:
    step = _strike_step(list(o_net) + list(t_net))
    def dist(a, b):
        return None if a is None or b is None else round(abs(a - b) / step, 1)
    s = dict(step=step,
             largest_same=ours.get("largest") == theirs.get("largest"),
             largest_strikes=dist(ours.get("largest"), theirs.get("largest")),
             call_wall_strikes=dist(ours.get("call_wall"), theirs.get("call_wall")),
             put_wall_strikes=dist(ours.get("put_wall"), theirs.get("put_wall")),
             flip_pct=None if ours.get("flip") is None or theirs.get("flip") is None
             else round(abs(ours["flip"] - theirs["flip"]) / spot * 100, 2),
             top_pos_overlap=len(set(ours.get("top_pos", [])) & set(theirs.get("top_pos", []))),
             top_neg_overlap=len(set(ours.get("top_neg", [])) & set(theirs.get("top_neg", []))),
             rank_corr=_spearman(o_net, t_net, spot))
    return s


# ---------------------------------------------------------------- the two sides
def _theirs(sym: str) -> tuple[dict, float | None, str | None]:
    import requests
    r = requests.get(API, params={"ticker": sym, "key": os.getenv("BULLFLOW_API_KEY", "").strip()}, timeout=20)
    r.raise_for_status()
    j = r.json()
    per: dict[date, dict[float, float]] = {}
    for s in j.get("strikes") or []:
        try:
            e = datetime.strptime(str(s["expiration"]).replace("-", ""), "%Y%m%d").date()
            k = float(s["strike"]); v = float(s.get("net_gex") or 0)
            per.setdefault(e, {})[k] = per.setdefault(e, {}).get(k, 0.0) + v
        except Exception:
            continue
    return per, (float(j["spot_price"]) if j.get("spot_price") else None), j.get("as_of")


def _ours(sym: str, spot: float) -> tuple[dict, dict | None, str | None]:
    import gamma
    contracts = gamma._fetch_contracts(sym, spot=float(spot))
    per: dict[date, dict[float, float]] = {}
    for exp, call, k, g, oi, v in gamma._slim(contracts or []):
        ge = float(g) * float(oi) * 100 * float(spot)
        per.setdefault(exp, {})[k] = per.setdefault(exp, {}).get(k, 0.0) + (ge if call else -ge)
    return per, gamma.walls(sym, spot, fetch=False), gamma.chain_as_of(sym)


# ---------------------------------------------------------------- one reading
def _load() -> list[dict]:
    try:
        return json.loads(LOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(rows: list[dict]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = LOG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows[-3000:], separators=(",", ":"), default=str), encoding="utf-8")
    os.replace(tmp, LOG_PATH)


def read_one(sym: str) -> dict:
    now = datetime.now(NY)
    row: dict = dict(symbol=sym, at=now.isoformat(timespec="seconds"))
    try:
        t_per, t_spot, t_asof = _theirs(sym)
        row["their_as_of"] = t_asof
        import data
        spot = t_spot or data.latest_price(sym)
        if not spot:
            row["error"] = "no spot"; return row
        o_per, o_walls, o_asof = _ours(sym, float(spot))
        row.update(spot=round(float(spot), 4), our_chain_as_of=o_asof, our_shelf_walls=o_walls)
        today = now.date()
        tl, ol = _layers(t_per, today), _layers(o_per, today)
        row["layers"] = {}
        for name in ("nearest", "swing", "all"):
            if name not in tl or name not in ol:
                continue
            tv, ov = _levels(tl[name], float(spot)), _levels(ol[name], float(spot))
            row["layers"][name] = dict(ours=ov, theirs=tv, our_exp=ol.get(f"_{name}_exp"), their_exp=tl.get(f"_{name}_exp"),
                                      score=_score(ov, tv, ol[name], tl[name], float(spot)))
    except Exception as exc:
        row["error"] = str(exc)[:200]
    return row


def run(force: bool = False) -> dict:
    """Scheduled at 10:00 / 12:00 / 15:30 ET. Never raises."""
    if not enabled():
        return dict(skipped="no BULLFLOW_API_KEY")
    now = datetime.now(NY)
    if not force and now.weekday() >= 5:
        return dict(skipped="weekend")
    out = [read_one(s) for s in symbols()]
    with _LOCK:
        rows = _load(); rows.extend(out); _save(rows)
    return dict(read=len(out), errors=sum(1 for r in out if r.get("error")))


def stats() -> dict:
    rows = _load()
    ok = [r for r in rows if r.get("layers")]
    res: dict = dict(enabled=enabled(), symbols=symbols(), readings=len(rows), usable=len(ok),
                     errors=[r for r in rows if r.get("error")][-5:], path=str(LOG_PATH))
    for name in ("nearest", "swing", "all"):
        sc = [r["layers"][name]["score"] for r in ok if name in r["layers"]]
        if not sc:
            continue
        def rate(f):
            vals = [f(s) for s in sc]; vals = [v for v in vals if v is not None]
            return f"{sum(vals)}/{len(vals)}" if vals else None
        corr = [s["rank_corr"] for s in sc if s["rank_corr"] is not None]
        res[name] = dict(n=len(sc),
                         largest_same=rate(lambda s: s["largest_same"]),
                         largest_within_1=rate(lambda s: None if s["largest_strikes"] is None else s["largest_strikes"] <= 1),
                         call_wall_within_2=rate(lambda s: None if s["call_wall_strikes"] is None else s["call_wall_strikes"] <= 2),
                         put_wall_within_2=rate(lambda s: None if s["put_wall_strikes"] is None else s["put_wall_strikes"] <= 2),
                         flip_within_half_pct=rate(lambda s: None if s["flip_pct"] is None else s["flip_pct"] <= 0.5),
                         top_pos_2of3=rate(lambda s: s["top_pos_overlap"] >= 2),
                         top_neg_2of3=rate(lambda s: s["top_neg_overlap"] >= 2),
                         rank_corr_median=sorted(corr)[len(corr) // 2] if corr else None)
    res["latest"] = ok[-3:]
    return res
