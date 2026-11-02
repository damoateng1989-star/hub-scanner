"""More setups for more trading styles: triangles, channels, support and resistance.

Every pattern uses the same rules as hubs so results are comparable:
  - work on 4-hour candles, act only on a candle CLOSE outside the pattern
  - target = the next shelf (nearest earlier hub beyond the break), else 1 x ATR
  - failed = a close back inside; timed out = HORIZON candles without either
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import engine

KINDS = {
    "hub":        "Hub",
    "triangle":   "Symmetrical triangle",
    "channel_down": "Descending channel",
    "channel_up": "Ascending channel",
    "sr":         "Support & resistance",
}
STYLE_OF = {"hub": "hub", "triangle": "triangle", "channel_down": "channel", "channel_up": "channel", "sr": "sr"}
STYLES = {   # what members pick in Settings
    "hub":      ("Hubs", "Tight sideways ranges that break out. NextShelf's core setup."),
    "triangle": ("Triangles", "Lower highs and higher lows squeezing into a point."),
    "channel":  ("Channels", "Price trending between two parallel lines, up or down."),
    "sr":       ("Support & resistance", "Horizontal levels price has tested 3+ times."),
}

# ---------------------------------------------------------------- sloped patterns (triangles, channels)
CH_MIN, CH_MAX = 12, 40            # channel window in 4H candles (6 to 20 sessions)
TRI_MIN, TRI_MAX = 8, 28


def _classify_triangle(mh, ml, w0, w1, A):
    flat = engine.TRI_FLAT * A
    if not (w0 > 0 and 0 < w1 <= 0.6 * w0 and w0 <= 4.0 * A):
        return None
    return "triangle" if (mh < -flat and ml > flat) else None


def _classify_channel(mh, ml, w0, w1, A):
    if not (w0 > 0 and 1.0 * A <= w0 <= 8.0 * A and 0.6 * w0 <= w1 <= 1.6 * w0):
        return None
    trend = 0.03 * A
    if mh < -trend and ml < -trend and abs(mh - ml) <= 0.5 * max(abs(mh), abs(ml)):
        return "channel_down"
    if mh > trend and ml > trend and abs(mh - ml) <= 0.5 * max(abs(mh), abs(ml)):
        return "channel_up"
    return None


def _find_sloped(c4: pd.DataFrame, atr: np.ndarray, lo_len: int, hi_len: int, classify) -> list[dict]:
    hi, lo, cl = c4["High"].to_numpy(float), c4["Low"].to_numpy(float), c4["Close"].to_numpy(float)
    fh, fl = engine._Fit(hi), engine._Fit(lo)
    n, pats, cur = len(c4), [], None

    def fit(a, b, A):
        if not A or A != A:
            return None
        mh, ch = fh.line(a, b)
        ml, cl0 = fl.line(a, b)
        L = b - a
        w0, w1 = ch - cl0, (mh * L + ch) - (ml * L + cl0)
        kind = classify(mh, ml, w0, w1, A)
        if not kind:
            return None
        x = np.arange(L + 1)
        up, dn = mh * x + ch, ml * x + cl0
        h, l_ = hi[a:b + 1], lo[a:b + 1]
        tol = 0.35 * A
        if ((h <= up + tol) & (l_ >= dn - tol)).mean() < 0.85:
            return None
        if (np.abs(h - up) <= tol).sum() < 2 or (np.abs(l_ - dn) <= tol).sum() < 2:
            return None
        if not (dn[-1] - 0.1 * A <= cl[b] <= up[-1] + 0.1 * A):
            return None                      # the newest close must still be inside: a close outside is the break
        return dict(a=a, b=b, mh=mh, ch=ch, ml=ml, cl=cl0, kind=kind)

    for b in range(lo_len - 1, n):
        best = None
        for L in range(min(hi_len, b + 1), lo_len - 1, -1):
            best = fit(b - L + 1, b, atr[b])
            if best:
                break
        if best and cur and best["a"] <= cur["b"] + 1 and best["kind"] == cur["kind"]:
            cur = best if best["a"] <= cur["a"] or best["b"] - best["a"] >= cur["b"] - cur["a"] else dict(cur, b=b)
        elif best:
            if cur:
                pats.append(cur)
            cur = best
        elif cur:
            pats.append(cur)
            cur = None
    if cur:
        pats.append(cur)
    for p in pats:
        p.update(start_i=p["a"], end_i=p["b"], sessions=(p["b"] - p["a"] + 1) // 2, active=(p["b"] == n - 1))
    return pats


def lines_at(p: dict, j: int) -> tuple[float, float]:
    k = j - p["a"]
    return p["mh"] * k + p["ch"], p["ml"] * k + p["cl"]


# ---------------------------------------------------------------- support & resistance
PIVOT_K = 3            # a swing high/low needs 3 candles either side
SR_TOUCHES = 3         # a level needs 3+ touches
SR_MEMORY = 160        # forget levels not touched in ~80 sessions
SR_HELD = 8            # it must have held for the 8 closes (4 sessions) before the break


def _sr_levels(c4: pd.DataFrame, atr: np.ndarray):
    """Walk forward and yield (j, levels known at the close of candle j)."""
    hi, lo = c4["High"].to_numpy(float), c4["Low"].to_numpy(float)
    n, levels, out = len(c4), [], []
    for j in range(n):
        i = j - PIVOT_K                      # pivot at i is confirmed at j
        if i >= PIVOT_K and atr[j] == atr[j]:
            w = slice(i - PIVOT_K, i + PIVOT_K + 1)
            for price, is_pivot in ((hi[i], hi[i] == hi[w].max()), (lo[i], lo[i] == lo[w].min())):
                if not is_pivot:
                    continue
                tol = 0.4 * atr[j]
                near = min((L for L in levels if abs(L["price"] - price) <= tol), key=lambda L: abs(L["price"] - price), default=None)
                if near:
                    near["price"] = (near["price"] * near["touches"] + price) / (near["touches"] + 1)
                    near["touches"] += 1
                    near["last"] = i
                else:
                    levels.append(dict(price=float(price), touches=1, first=i, last=i, broken=None))
        levels = [L for L in levels if j - L["last"] <= SR_MEMORY]
        out.append([dict(L) for L in levels if L["touches"] >= SR_TOUCHES])   # snapshot: no peeking ahead
    return out


# ---------------------------------------------------------------- outcomes (shared)
def _outcome(close: pd.Series, i0: int, d: int, edge: float, target: float, s: engine.Settings):
    n = len(close)
    outcome, oi = "pending", None
    for j in range(i0, min(i0 + 1 + s.horizon, n)):
        c = close.iloc[j]
        if (c - target) * d >= 0:
            outcome, oi = "follow-through", j
            break
        if j > i0 and (c - edge) * d <= 0:
            outcome, oi = "snap-back", j
            break
    else:
        if i0 + s.horizon < n:
            outcome, oi = "stalled", i0 + s.horizon
    entry = float(close.iloc[i0])
    chase = outcome == "follow-through" and oi == i0
    ret = None
    if outcome != "pending" and not chase:
        exit_px = target if outcome == "follow-through" else float(close.iloc[oi])
        ret = d * (exit_px / entry - 1) * 100
    fwd = d * (close.iloc[i0 + s.horizon] / close.iloc[i0] - 1) * 100 if i0 + s.horizon < n else None
    return dict(outcome=outcome, oi=oi, entry=entry, ret=ret, chase=chase, fwd=fwd)


def _target(res: dict, start_i: int, d: int, edge: float, atr_v: float, s: engine.Settings, levels=None):
    cur = dict(start_i=start_i, end_i=start_i, high=edge, low=edge)
    shelf = engine.find_shelf(res["hubs"], cur, d, edge, atr_v, s) if res["hubs"] else None
    cands = []
    if shelf:
        cands.append((shelf["level"], shelf))
    gap = s.shelf_min_atr * atr_v
    for L in levels or []:
        if (L["price"] - edge) * d >= gap:
            cands.append((L["price"], dict(low=L["price"], high=L["price"], level=L["price"], sr=True)))
    if cands:
        return min(cands, key=lambda c: abs(c[0] - edge))
    return edge + d * s.target_atr * atr_v, None


# ---------------------------------------------------------------- analysis
def analyze_all(c4: pd.DataFrame, res: dict, s: engine.Settings) -> dict:
    """All non-hub setups for one stock: patterns found, past breakouts with results, and what's live now."""
    close, atr_s = res["close"], res["atr"]
    atr = atr_s.to_numpy(float)
    n = len(close)
    out = dict(trades=[], live=[])
    if n < 40:
        return out

    sloped = _find_sloped(c4, atr, TRI_MIN, TRI_MAX, _classify_triangle) + _find_sloped(c4, atr, CH_MIN, CH_MAX, _classify_channel)
    for p in sloped:
        A = float(atr[min(p["end_i"], n - 1)])
        if p["active"]:
            up, dn = lines_at(p, n - 1)
            out["live"].append(dict(kind=p["kind"], state="forming", upper=up, lower=dn, sessions=p["sessions"],
                                    start_i=p["a"], end_i=n - 1, p=p))
            continue
        brk = None
        for j in range(p["end_i"] + 1, min(p["end_i"] + 1 + 4, n)):     # the break must come right after the pattern
            up, dn = lines_at(p, j)
            if up <= dn:
                break
            c = close.iloc[j]
            if c > up:
                brk = (j, 1, up)
                break
            if c < dn:
                brk = (j, -1, dn)
                break
        if not brk:
            continue
        i0, d, edge = brk
        target, shelf = _target(res, p["a"], d, edge, float(atr[i0]), s)
        t = dict(kind=p["kind"], hub=dict(start_i=p["a"], end_i=p["end_i"], low=min(lines_at(p, p["end_i"])),
                                          high=max(lines_at(p, p["end_i"])), sessions=p["sessions"]),
                 p=p, i0=i0, direction=d, edge=edge, target=target, shelf=shelf, **_outcome(close, i0, d, edge, target, s))
        out["trades"].append(t)

    # support & resistance: a close through a level tested 3+ times
    lv_at = _sr_levels(c4, atr)
    cooldown = {}
    for j in range(1, n):
        A = atr[j]
        if A != A:
            continue
        c, cp = close.iloc[j], close.iloc[j - 1]
        for L in lv_at[j - 1]:
            key = L["first"]
            if cooldown.get(key, -999) > j:
                continue
            m = 0.15 * A
            d = 1 if (c > L["price"] + m and cp <= L["price"] + m) else -1 if (c < L["price"] - m and cp >= L["price"] - m) else 0
            if not d or j < SR_HELD:
                continue
            prior = close.iloc[j - SR_HELD:j]
            if not ((prior <= L["price"] + m).all() if d == 1 else (prior >= L["price"] - m).all()):
                continue                     # the level must have actually held for a while before it broke
            cooldown[key] = j + s.horizon
            edge = L["price"]
            target, shelf = _target(res, L["first"], d, edge, float(A), s, lv_at[j - 1])
            out["trades"].append(dict(kind="sr", hub=dict(start_i=L["first"], end_i=j - 1, low=edge, high=edge, sessions=L["touches"]),
                                      level=dict(price=edge, touches=L["touches"], first=L["first"], last=L["last"]),
                                      i0=j, direction=d, edge=edge, target=target, shelf=shelf, **_outcome(close, j, d, edge, target, s)))
    # S/R now: nearest tested level above and below the last close
    last, A = float(close.iloc[-1]), float(atr[-1])
    lv = lv_at[-1]
    out["levels_now"] = sorted(lv, key=lambda L: L["price"])
    above = min((L for L in lv if L["price"] >= last), key=lambda L: L["price"] - last, default=None)
    below = max((L for L in lv if L["price"] < last), key=lambda L: L["price"], default=None)
    if above or below:
        near = None
        if above and above["price"] - last <= 0.25 * A:
            near = "resistance"
        elif below and last - below["price"] <= 0.25 * A:
            near = "support"
        out["live"].append(dict(kind="sr", state="testing" if near else "between", near=near,
                                upper=above["price"] if above else None, lower=below["price"] if below else None,
                                upper_touches=above["touches"] if above else 0, lower_touches=below["touches"] if below else 0))

    # breakouts still in play (pending) become live too: the newest one per setup type
    newest = {}
    for t in out["trades"]:
        if t["outcome"] == "pending" and t["i0"] >= n - s.horizon and t["i0"] >= newest.get(t["kind"], {"i0": -1})["i0"]:
            newest[t["kind"]] = t
    for t in newest.values():
        if True:
            out["live"].append(dict(kind=t["kind"], state="breakout_up" if t["direction"] == 1 else "breakdown",
                                    edge=t["edge"], target=t["target"], target_kind="shelf" if t["shelf"] else "atr",
                                    candles=n - 1 - t["i0"], since=close.index[t["i0"]].isoformat(), trade=t))
    return out


def live_json(sym: str, c4: pd.DataFrame, res: dict, pa: dict) -> list[dict]:
    """Compact, JSON-safe list of what each pattern says about this stock right now."""
    close, n = res["close"], len(res["close"])
    last = float(close.iloc[-1])
    idx = close.index
    out = []
    for L in pa["live"]:
        k = L["kind"]
        row = dict(kind=k, name=KINDS[k], style=STYLE_OF[k], state=L["state"])
        if L["state"] in ("breakout_up", "breakdown"):
            t = L["trade"]
            row.update(edge=round(t["edge"], 2), target=round(t["target"], 2), target_kind=L["target_kind"],
                       candles=L["candles"], since=L["since"], to_target_pct=round((t["target"] / last - 1) * 100, 2))
            if k == "sr":
                row.update(touches=t["level"]["touches"])
            else:
                p = t["p"]
                row["lines"] = _line_pts(p, idx, p["a"], p["end_i"])
        elif k == "sr":
            row.update(near=L["near"], upper=_r(L["upper"]), lower=_r(L["lower"]),
                       upper_touches=L["upper_touches"], lower_touches=L["lower_touches"])
        else:
            p = L["p"]
            row.update(upper=round(L["upper"], 2), lower=round(L["lower"], 2), sessions=L["sessions"],
                       lines=_line_pts(p, idx, p["a"], n - 1))
            w = L["upper"] - L["lower"]
            pos = (last - L["lower"]) / w if w > 0 else 0.5
            row["near_edge"] = "up" if pos >= 0.8 else "down" if pos <= 0.2 else None
        out.append(row)
    order = {"breakout_up": 0, "breakdown": 0, "testing": 1, "forming": 2, "between": 3}
    out.sort(key=lambda r: order.get(r["state"], 9))
    return out


def _r(v):
    return None if v is None else round(float(v), 2)


def _line_pts(p, idx, a, b):
    ua, da = lines_at(p, a)
    ub, db = lines_at(p, b)
    t = lambda i: int(idx[i].timestamp())
    return dict(t0=t(a), t1=t(b), u0=round(ua, 2), u1=round(ub, 2), d0=round(da, 2), d1=round(db, 2))


def events(sym: str, res: dict, pa: dict) -> list[dict]:
    """Alertable events for the scanner: one per new pattern breakout/breakdown."""
    idx = res["close"].index
    ev = []
    for t in pa["trades"]:
        ev.append(dict(type="breakout" if t["direction"] == 1 else "breakdown", kind=t["kind"], time=idx[t["i0"]],
                       close=t["entry"], edge=t["edge"], target=t["target"], shelf=t["shelf"],
                       touches=t.get("level", {}).get("touches")))
    return ev
