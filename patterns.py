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
    "flag_bull":  "Bull flag",
    "flag_bear":  "Bear flag",
    "double_top": "Double top",
    "double_bottom": "Double bottom",
    "gapfade":  "Post-earnings gap fade & rip",
    "fail_down": "Failed breakdown",
    "fail_up":  "Failed breakout",
}
STYLE_OF = {"hub": "hub", "triangle": "triangle", "channel_down": "channel", "channel_up": "channel", "sr": "sr",
            "flag_bull": "flag", "flag_bear": "flag", "double_top": "double", "double_bottom": "double",
            "gapfade": "gapfade", "fail_down": "fakeout", "fail_up": "fakeout"}
STYLES = {   # what members pick in Settings
    "hub":      ("Hubs", "Tight sideways ranges that break out. NextShelf's core setup."),
    "triangle": ("Triangles", "Lower highs and higher lows squeezing into a point."),
    "channel":  ("Channels", "Price trending between two parallel lines, up or down."),
    "sr":       ("Support & resistance", "Horizontal levels price has tested 3+ times."),
    "flag":     ("Flags", "A sharp move (the pole), then a short pause that drifts against it. Trades with the move."),
    "double":   ("Double tops & bottoms", "Two tests of the same high or low, then a close through the middle (the neckline)."),
    "gapfade":  ("Post-earnings gap fade & rip", "A gap up that fades, then closes back above the gap-day high. Trades the reclaim. "
                 "The classic case is an earnings gap, but it fires on the shape, whatever caused the gap."),
    "fakeout":  ("Failed breakdowns & breakouts", "A hub breaks one way, then within 3 candles closes back through the other side "
                 "on strong volume. The trapped traders fuel the move. Wrong below the fake-out low (or above its high)."),
}

FAKE_WITHIN = 3         # the fake-out must come within this many candles of the hub, and the reclaim within this many of the fake-out
FAKE_VOL = 1.2          # the reclaim candle needs this much of normal volume for its time slot

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


# ---------------------------------------------------------------- flags: a pole, then a short pause against it
POLE_MIN, POLE_MAX = 2, 6          # pole length in 4H candles
FLAG_MIN, FLAG_MAX = 4, 12         # flag length in 4H candles (2 to 6 sessions)
POLE_ATR = 3.0                     # the pole must move at least 3 x ATR


def _find_flags(c4: pd.DataFrame, atr: np.ndarray) -> list[dict]:
    hi, lo, cl = c4["High"].to_numpy(float), c4["Low"].to_numpy(float), c4["Close"].to_numpy(float)
    fh, fl = engine._Fit(hi), engine._Fit(lo)
    n, out, p = len(c4), [], POLE_MAX
    while p < n - FLAG_MIN:
        A = atr[p]
        if A != A or not A:
            p += 1
            continue
        pole = None
        for k in range(POLE_MIN, POLE_MAX + 1):
            mv = cl[p] - cl[p - k]
            if abs(mv) >= POLE_ATR * A:
                path = np.abs(np.diff(cl[p - k:p + 1])).sum()
                if path and abs(mv) / path >= 0.7:          # mostly one-way, not a whipsaw
                    pole = (k, 1 if mv > 0 else -1, abs(mv))
                    break
        if not pole or (pole[1] == 1 and cl[p] < hi[p - pole[0]:p + 1].max() - 0.5 * A) or (pole[1] == -1 and cl[p] > lo[p - pole[0]:p + 1].min() + 0.5 * A):
            p += 1
            continue
        k, d, height = pole
        top = hi[p - k:p + 1].max() if d == 1 else lo[p - k:p + 1].min()
        best = None
        for b in range(p + FLAG_MIN, min(p + FLAG_MAX, n - 1) + 1):
            a = p + 1
            mh, ch = fh.line(a, b)
            ml, cl0 = fl.line(a, b)
            L = b - a
            w0, w1 = ch - cl0, (mh * L + ch) - (ml * L + cl0)
            if not (0 < w1 <= w0 * 1.15 and w0 <= min(0.6 * height, 3.0 * A)):
                break                                         # too wide / widening: no longer a flag
            drift = (mh + ml) / 2
            if (d == 1 and drift > 0.05 * A) or (d == -1 and drift < -0.05 * A):
                break                                         # a flag drifts against the pole, or sideways
            seg_lo, seg_hi = lo[a:b + 1].min(), hi[a:b + 1].max()
            if (d == 1 and seg_lo < top - 0.5 * height) or (d == -1 and seg_hi > top + 0.5 * height):
                break                                         # gave back more than half the pole
            x = np.arange(L + 1)
            up, dn = mh * x + ch, ml * x + cl0
            if ((hi[a:b + 1] <= up + 0.35 * A) & (lo[a:b + 1] >= dn - 0.35 * A)).mean() < 0.85:
                break
            if not (dn[-1] - 0.1 * A <= cl[b] <= up[-1] + 0.1 * A):
                break                                         # a close outside is the break
            best = dict(a=a, b=b, mh=mh, ch=ch, ml=ml, cl=cl0)
        if best:
            kind = "flag_bull" if d == 1 else "flag_bear"
            best.update(kind=kind, only_dir=d, pole_start=p - k, pole_height=height, start_i=best["a"], end_i=best["b"],
                        sessions=(best["b"] - best["a"] + 1) // 2, active=(best["b"] == n - 1))
            out.append(best)
            p = best["b"] + 1
        else:
            p += 1
    return out


# ---------------------------------------------------------------- double tops and bottoms
DBL_GAP_MIN, DBL_GAP_MAX = 6, 60   # candles between the two peaks (3 to 30 sessions)
DBL_DEPTH_ATR = 2.0                # the dip between them must be at least 2 x ATR deep
DBL_TREND_ATR = 2.5                # a real move into the first peak: at least 2.5 x ATR over the 15 candles before it
DBL_WAIT = 20                      # the neckline break must come within 20 candles of the second peak


def _pivots(arr: np.ndarray, high: bool) -> list[int]:
    k, out = PIVOT_K, []
    for i in range(k, len(arr) - k):
        w = arr[i - k:i + k + 1]
        if arr[i] == (w.max() if high else w.min()):
            out.append(i)
    return out


def _find_doubles(c4: pd.DataFrame, atr: np.ndarray, close: pd.Series) -> list[dict]:
    hi, lo = c4["High"].to_numpy(float), c4["Low"].to_numpy(float)
    cl = close.to_numpy(float)
    n, out, used = len(c4), [], set()
    for top, arr, other in ((True, hi, lo), (False, lo, hi)):
        piv = _pivots(arr, top)
        for x, i2 in enumerate(piv):
            A = atr[i2]
            if A != A or not A or i2 in used:
                continue
            for i1 in reversed(piv[:x]):
                gap = i2 - i1
                if gap < DBL_GAP_MIN:
                    continue
                if gap > DBL_GAP_MAX:
                    break
                if abs(arr[i1] - arr[i2]) > 0.35 * A:
                    continue
                before = cl[max(0, i1 - 15):i1]
                if not len(before) or (top and arr[i1] - before.min() < DBL_TREND_ATR * A) or (not top and before.max() - arr[i1] < DBL_TREND_ATR * A):
                    continue                          # a double top needs a rally into it (a double bottom, a drop)
                mid = other[i1 + 1:i2]
                if not len(mid):
                    continue
                neck = float(mid.min() if top else mid.max())
                peak = float(max(arr[i1], arr[i2]) if top else min(arr[i1], arr[i2]))
                if abs(peak - neck) < DBL_DEPTH_ATR * A:
                    continue
                # nothing between the peaks may go beyond them
                if (top and arr[i1:i2 + 1].max() > peak + 0.1 * A) or (not top and arr[i1:i2 + 1].min() < peak - 0.1 * A):
                    continue
                d = -1 if top else 1
                conf = i2 + PIVOT_K                      # the second peak is only known PIVOT_K candles later
                brk, dead = None, None
                for j in range(conf, min(i2 + DBL_WAIT, n)):
                    c = cl[j]
                    if (top and c > peak + 0.3 * A) or (not top and c < peak - 0.3 * A):
                        dead = j                         # made a new high/low instead: pattern void
                        break
                    if (top and c < neck) or (not top and c > neck):
                        brk = j
                        break
                active = brk is None and dead is None and conf <= n - 1 and i2 >= n - 15
                if brk is None and not active:
                    continue
                kind = "double_top" if top else "double_bottom"
                up, dn = (peak, neck) if top else (neck, peak)
                out.append(dict(kind=kind, a=i1, b=i2, end_i=(brk - 1) if brk else n - 1, start_i=i1, i1=i1, i2=i2,
                                mh=0.0, ch=up, ml=0.0, cl=dn, neck=neck, peak=peak, brk=brk, d=d, active=active,
                                sessions=(i2 - i1) // 2))
                used.add(i2)
                break
    return out


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

    sloped = (_find_sloped(c4, atr, TRI_MIN, TRI_MAX, _classify_triangle) + _find_sloped(c4, atr, CH_MIN, CH_MAX, _classify_channel)
              + _find_flags(c4, atr))
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
        if not brk or (p.get("only_dir") and brk[1] != p["only_dir"]):
            continue                         # flags only trade in the direction of the pole
        i0, d, edge = brk
        target, shelf = _target(res, p["a"], d, edge, float(atr[i0]), s)
        t = dict(kind=p["kind"], hub=dict(start_i=p["a"], end_i=p["end_i"], low=min(lines_at(p, p["end_i"])),
                                          high=max(lines_at(p, p["end_i"])), sessions=p["sessions"]),
                 p=p, i0=i0, direction=d, edge=edge, target=target, shelf=shelf, **_outcome(close, i0, d, edge, target, s))
        out["trades"].append(t)

    # double tops and bottoms: a close through the neckline
    for p in _find_doubles(c4, atr, close):
        if p["active"]:
            out["live"].append(dict(kind=p["kind"], state="forming", upper=p["ch"], lower=p["cl"], sessions=p["sessions"],
                                    start_i=p["a"], end_i=n - 1, p=p))
            continue
        i0, d, edge = p["brk"], p["d"], p["neck"]
        target, shelf = _target(res, p["a"], d, edge, float(atr[i0]), s)
        out["trades"].append(dict(kind=p["kind"], hub=dict(start_i=p["a"], end_i=i0 - 1, low=p["cl"], high=p["ch"], sessions=p["sessions"]),
                                  p=p, i0=i0, direction=d, edge=edge, target=target, shelf=shelf, **_outcome(close, i0, d, edge, target, s)))

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

    _gapfade(c4, close, n, s, out)
    _fakeouts(c4, res, close, atr, n, s, out)

    # breakouts still in play (pending) become live too: the newest one per setup type
    newest = {}
    for t in out["trades"]:
        if t["kind"] == "gapfade":
            continue                         # daily clock: handled in _gapfade
        if t["outcome"] == "pending" and t["i0"] >= n - s.horizon and t["i0"] >= newest.get(t["kind"], {"i0": -1})["i0"]:
            newest[t["kind"]] = t
    for t in newest.values():
        if True:
            out["live"].append(dict(kind=t["kind"], state="breakout_up" if t["direction"] == 1 else "breakdown",
                                    edge=t["edge"], target=t["target"], target_kind="shelf" if t["shelf"] else "atr",
                                    candles=n - 1 - t["i0"], since=close.index[t["i0"]].isoformat(), trade=t))
    return out


def _fakeouts(c4: pd.DataFrame, res: dict, close: pd.Series, atr: np.ndarray, n: int, s: engine.Settings, out: dict):
    """Failed breakdown (long) / failed breakout (short): a 4H close outside a hub, then within FAKE_WITHIN candles
    a close back through the OTHER side of the hub on FAKE_VOL x normal volume. Stop = the fake-out extreme."""
    hi, lo = c4["High"].to_numpy(float), c4["Low"].to_numpy(float)
    vol, vavg = res.get("volume"), res.get("volume_avg")
    seen = set()
    for h in res["hubs"]:
        if h.get("active"):
            continue
        top, bot, e = float(h["high"]), float(h["low"]), h["end_i"]
        j1 = fd = None
        for j in range(e + 1, min(e + 1 + FAKE_WITHIN, n)):
            c = close.iloc[j]
            if c < bot:
                j1, fd = j, -1
                break
            if c > top:
                j1, fd = j, 1
                break
        if j1 is None or j1 in seen:
            continue
        seen.add(j1)
        d = -fd                                  # the trade goes the other way
        kind = "fail_down" if d == 1 else "fail_up"
        edge = top if d == 1 else bot
        i0 = None
        for j in range(j1 + 1, min(j1 + 1 + FAKE_WITHIN, n)):
            c = close.iloc[j]
            if (c - edge) * d > 0:
                ok = True
                if vol is not None and vavg is not None:
                    va = vavg.iloc[j]
                    ok = va == va and va > 0 and float(vol.iloc[j]) >= FAKE_VOL * float(va)
                if ok:
                    i0 = j
                break                            # the first close back through decides it, volume or not
        p = dict(a=h["start_i"], end_i=e, mh=0.0, ch=top, ml=0.0, cl=bot, fake_i=j1, fake_dir=fd)
        if i0 is None:
            last_chance = j1 + FAKE_WITHIN
            back_in = (float(close.iloc[-1]) - (bot if d == 1 else top)) * d >= 0   # only once price is back inside the hub
            if last_chance > n - 1 and back_in and not any(
                    (close.iloc[j] - edge) * d > 0 for j in range(j1 + 1, n)):
                ext = float(lo[j1:].min()) if d == 1 else float(hi[j1:].max())
                p.update(stop=ext)
                out["live"].append(dict(kind=kind, state="forming", upper=top if d == 1 else ext, lower=ext if d == 1 else bot,
                                        sessions=h["sessions"], start_i=h["start_i"], end_i=n - 1, p=p,
                                        trigger_px=edge, stop=ext, left=max(0, last_chance - (n - 1))))
            continue
        stop = float(lo[j1:i0 + 1].min()) if d == 1 else float(hi[j1:i0 + 1].max())
        p.update(stop=stop)
        target, shelf = _target(res, h["start_i"], d, edge, float(atr[i0]), s)
        o = _outcome(close, i0, d, stop, target, s)   # wrong only on a close past the fake-out extreme
        out["trades"].append(dict(kind=kind, hub=dict(start_i=h["start_i"], end_i=e, low=bot, high=top, sessions=h["sessions"]),
                                  p=p, i0=i0, direction=d, edge=edge, target=target, shelf=shelf, stop=stop, **o))


def _gapfade(c4: pd.DataFrame, close: pd.Series, n: int, s: engine.Settings, out: dict):
    """Post-earnings gap fade & rip (Patch 38): found on daily candles, mapped back onto the 4H frame so the
    record, alerts and charts treat it like every other setup. A day maps to its last 4H candle (the close)."""
    try:
        daily = engine.complete_days(c4)
        if len(daily) < engine.PEG_MIN_DAILY:
            return
        res_d = engine.analyze_gapfade(daily, s)
    except Exception:
        return
    days = c4.index.tz_convert(engine.NY).normalize()
    last_of = {}
    first_of = {}
    for j, dday in enumerate(days):
        last_of[dday] = j
        first_of.setdefault(dday, j)
    dkey = daily.index.tz_convert(engine.NY).normalize() if daily.index.tz is not None else daily.index
    to4 = lambda di: last_of.get(dkey[di])
    for t in res_d["trades"]:
        st = t["pattern"]
        i0, g = to4(t["i0"]), first_of.get(dkey[st["gap_i"]])
        if i0 is None or g is None:
            continue
        oi = to4(t["oi"]) if t["oi"] is not None else None
        p = dict(a=g, end_i=max(g, i0 - 1), mh=0.0, ch=st["gap_high"], ml=0.0, cl=st["stop"], gap=st, gapfade=True)
        out["trades"].append(dict(kind="gapfade", hub=dict(start_i=g, end_i=max(g, i0 - 1), low=st["stop"], high=st["gap_high"],
                                                           sessions=t["i0"] - st["gap_i"] + 1),
                                  p=p, i0=i0, direction=1, edge=st["gap_high"], target=t["target"], shelf=None, target_kind="2R",
                                  outcome=t["outcome"], oi=oi, entry=t["entry"], ret=t["ret"], ret_r=t["ret_r"], chase=False,
                                  fwd=t["fwd"], stop=st["stop"], gap_pct=st["gap_pct"], gap_day=dkey[st["gap_i"]].strftime("%b %-d")))
        if t["outcome"] == "pending":       # reclaim fired, trade still running: live as a breakout
            out["live"].append(dict(kind="gapfade", state="breakout_up", edge=st["gap_high"], target=t["target"], target_kind="2R",
                                    candles=len(daily) - 1 - t["i0"], since=close.index[i0].isoformat(), trade=out["trades"][-1]))
    pend = res_d.get("pending")
    if pend:                                # gapped and faded, reclaim not fired yet: watch it
        g = first_of.get(dkey[pend["gap_i"]])
        if g is not None:
            p = dict(a=g, end_i=n - 1, mh=0.0, ch=pend["gap_high"], ml=0.0, cl=pend["fade_low"], gap=pend, gapfade=True)
            out["live"].append(dict(kind="gapfade", state="forming", upper=pend["gap_high"], lower=pend["fade_low"],
                                    sessions=len(daily) - 1 - pend["gap_i"], start_i=g, end_i=n - 1, p=p,
                                    gap_pct=pend["gap_pct"], left=pend["left"]))


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
            if k != "gapfade":                   # gap fades run on daily candles
                row["strength"] = engine.break_strength(c4, res["atr"], t["i0"], t["direction"], t["edge"], float(close.iloc[t["i0"]]))
            if k == "sr":
                row.update(touches=t["level"]["touches"])
            elif k in ("fail_down", "fail_up"):
                row.update(stop=round(t["stop"], 2), hub_top=round(t["hub"]["high"], 2), hub_bottom=round(t["hub"]["low"], 2))
            elif k == "gapfade":
                row.update(stop=round(t["stop"], 2), gap_pct=round(t["gap_pct"] * 100, 1), gap_day=t["gap_day"],
                           candles_unit="days", to_target_pct=round((t["target"] / last - 1) * 100, 2))
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
            if k.startswith("double"):           # only the neckline side is a trigger
                row.update(neck=round(p["neck"], 2), peak=round(p["peak"], 2), trigger="down" if k == "double_top" else "up")
                row["near_edge"] = row["near_edge"] if row["near_edge"] == row["trigger"] else None
            if k.startswith("flag"):
                row.update(trigger="up" if k == "flag_bull" else "down", pole_pct=round(p["pole_height"] / last * 100, 1))
                row["near_edge"] = row["near_edge"] if row["near_edge"] == row["trigger"] else None
            if k in ("fail_down", "fail_up"):    # the trigger is a close back through the far side of the hub
                tr = "up" if k == "fail_down" else "down"
                row.update(trigger=tr, trigger_px=round(L["trigger_px"], 2), stop=round(L["stop"], 2), candles_left=L["left"],
                           hub_top=round(p["ch"], 2), hub_bottom=round(p["cl"], 2))
                row["near_edge"] = tr if (last - L["trigger_px"]) * (1 if tr == "up" else -1) >= -0.25 * float(res["atr"].iloc[-1]) else None
            if k == "gapfade":                   # the trigger is a daily close back above the gap-day high
                row.update(trigger="up", gap_pct=round(L["gap_pct"] * 100, 1), days_left=L["left"], sessions_unit="days")
                row["near_edge"] = "up" if pos >= 0.8 else None
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
        ev.append(dict(type="breakout" if t["direction"] == 1 else "breakdown", kind=t["kind"], time=idx[t["i0"]], i0=t["i0"],
                       direction=t["direction"],
                       close=t["entry"], edge=t["edge"], target=t["target"], shelf=t["shelf"],
                       touches=t.get("level", {}).get("touches"), stop=t.get("stop"), gap_pct=t.get("gap_pct"),
                       gap_day=t.get("gap_day")))
    return ev
