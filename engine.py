"""Hub detection logic: 4H candles, hubs, breakouts, outcomes."""
from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np
import pandas as pd

NY = "America/New_York"
OPEN_MIN = 9 * 60 + 30     # 9:30
MID_MIN = 13 * 60 + 30     # 13:30
CLOSE_MIN = 16 * 60        # 16:00


@dataclass
class Settings:
    min_sessions: int = 5      # hub must last at least this many sessions
    k: float = 1.5             # closes must stay within k x 4H ATR(14)
    horizon: int = 10          # 4H candles to judge a breakout
    target_atr: float = 1.0    # fallback target beyond hub edge, in ATR, when there is no shelf
    shelf_min_atr: float = 0.5 # a shelf must sit at least this far (in ATR) beyond the hub edge

    @property
    def window(self) -> int:
        return 2 * self.min_sessions   # two 4H candles per regular session

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            min_sessions=int(os.getenv("MIN_SESSIONS", 5)),
            k=float(os.getenv("HUB_K", 1.5)),
            horizon=int(os.getenv("HORIZON", 10)),
            target_atr=float(os.getenv("TARGET_ATR", 1.0)),
            shelf_min_atr=float(os.getenv("SHELF_MIN_ATR", 0.5)),
        )


def to_4h(bars: pd.DataFrame, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Regular-hours intraday bars -> 4H candles (9:30-13:30, 13:30-16:00 ET).

    Buckets are assigned by wall-clock time, so daylight-saving changes don't shift them.
    Candles that haven't finished yet (relative to `now`) are dropped.
    """
    if bars.empty:
        return bars
    df = bars.tz_convert(NY)
    mins = df.index.hour * 60 + df.index.minute
    df = df[(mins >= OPEN_MIN) & (mins < CLOSE_MIN)]
    if df.empty:
        return df
    mins = df.index.hour * 60 + df.index.minute
    second = mins >= MID_MIN
    day = df.index.normalize()
    start = day + pd.to_timedelta(np.where(second, MID_MIN, OPEN_MIN), unit="m")
    end = day + pd.to_timedelta(np.where(second, CLOSE_MIN, MID_MIN), unit="m")
    df = df.assign(_start=start, _end=end)
    out = df.groupby("_start").agg(
        Open=("Open", "first"), High=("High", "max"), Low=("Low", "min"),
        Close=("Close", "last"), _end=("_end", "first"),
        **({"Volume": ("Volume", "sum")} if "Volume" in df.columns else {}))
    if now is not None:
        out = out[out["_end"] <= pd.Timestamp(now).tz_convert(NY)]
    out.index.name = None
    return out.drop(columns="_end")


def candle_end(ts: pd.Timestamp) -> pd.Timestamp:
    """4H candles are labelled by their start time; this returns when they close."""
    return ts + pd.Timedelta(hours=4) if ts.hour == 9 else ts.normalize() + pd.Timedelta(hours=16)


def find_shelf(hubs: list, cur: dict, d: int, edge: float, atr_v: float, s: Settings) -> dict | None:
    """Next shelf = the nearest earlier hub zone beyond `edge` in direction d (1 up, -1 down)."""
    older = [h for h in hubs if h is not cur and h["end_i"] < cur["start_i"]]
    gap = s.shelf_min_atr * atr_v
    if d == 1:
        c = [h for h in older if h["low"] >= edge + gap]
        best = min(c, key=lambda h: h["low"], default=None)
        level = best["low"] if best else None
    else:
        c = [h for h in older if h["high"] <= edge - gap]
        best = max(c, key=lambda h: h["high"], default=None)
        level = best["high"] if best else None
    if not best:
        return None
    return dict(low=best["low"], high=best["high"], level=level, start_i=best["start_i"], end_i=best["end_i"])


def target_for(hubs: list, cur: dict, d: int, atr_v: float, s: Settings) -> tuple[float, dict | None]:
    """Target = near edge of the next shelf, or target_atr x ATR beyond the hub edge if there's none."""
    edge = cur["high"] if d == 1 else cur["low"]
    shelf = find_shelf(hubs, cur, d, edge, atr_v, s)
    return (shelf["level"], shelf) if shelf else (edge + d * s.target_atr * atr_v, None)


def analyze(c4: pd.DataFrame, s: Settings) -> dict:
    close = c4["Close"]
    n = len(close)
    prev = close.shift()
    tr = pd.concat([c4.High - c4.Low, (c4.High - prev).abs(), (c4.Low - prev).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()

    w = s.window
    width = close.rolling(w).max() - close.rolling(w).min()
    tight = (width <= s.k * atr).to_numpy()

    # --- hubs: runs of consecutive "tight" windows
    hubs = []
    i = 0
    while i < n:
        if tight[i]:
            j = i
            while j + 1 < n and tight[j + 1]:
                j += 1
            si = i - w + 1
            seg, first = close.iloc[si:j + 1], close.iloc[si:i + 1]
            hubs.append(dict(start_i=si, formed_i=i, end_i=j,
                             low=float(seg.min()), high=float(seg.max()),
                             formed_low=float(first.min()), formed_high=float(first.max()),
                             sessions=(j - si + 1) // 2, active=(j == n - 1)))
            i = j + 1
        else:
            i += 1

    # --- breakouts and outcomes
    trades = []
    for h in hubs:
        if h["active"]:
            continue
        brk = None
        for i in range(h["end_i"] + 1, min(h["end_i"] + 1 + s.horizon, n)):
            c = close.iloc[i]
            if c > h["high"]:
                brk = (i, 1)
                break
            if c < h["low"]:
                brk = (i, -1)
                break
        if brk is None:
            continue
        i0, d = brk
        edge = h["high"] if d == 1 else h["low"]
        target, shelf = target_for(hubs, h, d, float(atr.iloc[i0]), s)
        outcome, oi = "pending", None
        # the breakout close itself can already reach the target
        for j in range(i0, min(i0 + 1 + s.horizon, n)):
            c = close.iloc[j]
            if (c - target) * d >= 0:
                outcome, oi = "follow-through", j
                break
            if (c - edge) * d <= 0:
                outcome, oi = "snap-back", j
                break
        else:
            if i0 + s.horizon < n:
                outcome, oi = "stalled", i0 + s.horizon
        fwd = None
        if i0 + s.horizon < n:
            fwd = d * (close.iloc[i0 + s.horizon] / close.iloc[i0] - 1) * 100
        # trade result if you entered at the breakout close:
        #   follow-through -> exit at target, snap-back -> exit at that close, stalled -> exit at time limit
        entry = float(close.iloc[i0])
        chase = outcome == "follow-through" and oi == i0   # already past target at entry: no trade
        ret = None
        if outcome != "pending" and not chase:
            exit_px = target if outcome == "follow-through" else float(close.iloc[oi])
            ret = d * (exit_px / entry - 1) * 100
        trades.append(dict(hub=h, i0=i0, direction=d, edge=edge, target=target, shelf=shelf,
                           outcome=outcome, oi=oi, fwd=fwd, entry=entry, ret=ret, chase=chase))

    # --- events (for alerts), in time order
    events = []
    idx = close.index
    for h in hubs:
        events.append(dict(type="hub_formed", time=idx[h["formed_i"]],
                           close=float(close.iloc[h["formed_i"]]),
                           low=h["formed_low"], high=h["formed_high"], sessions=s.min_sessions))
    for t in trades:
        h = t["hub"]
        events.append(dict(type="breakout" if t["direction"] == 1 else "breakdown",
                           time=idx[t["i0"]], close=float(close.iloc[t["i0"]]),
                           low=h["low"], high=h["high"], sessions=h["sessions"], target=t["target"],
                           shelf=t["shelf"]))
        if t["outcome"] != "pending":
            events.append(dict(type=t["outcome"], time=idx[t["oi"]], close=float(close.iloc[t["oi"]]),
                               low=h["low"], high=h["high"], target=t["target"], shelf=t["shelf"],
                               direction=t["direction"], candles=t["oi"] - t["i0"]))
    events.sort(key=lambda e: e["time"])
    return dict(close=close, atr=atr, hubs=hubs, trades=trades, events=events)


RVOL_STRONG, RVOL_NORMAL = 1.5, 1.0   # breakout volume vs a normal candle in the same slot


def rvol(c4: pd.DataFrame, i: int, n: int = 20) -> float | None:
    """Volume of candle i compared with the average of the last n candles in the same slot
    (the 9:30-1:30 and 1:30-4:00 candles trade very different amounts, so each is compared with its own kind)."""
    try:
        if "Volume" not in c4 or i is None:
            return None
        v = c4["Volume"].to_numpy(float)
        if not (v[i] > 0):
            return None
        hrs = c4.index.hour
        prev = [v[j] for j in range(i - 1, -1, -1) if hrs[j] == hrs[i] and v[j] > 0][:n]
        if len(prev) < 5:
            return None
        avg = sum(prev) / len(prev)
        return round(v[i] / avg, 2) if avg > 0 else None
    except Exception:
        return None


def rvol_word(r: float | None) -> str | None:
    if r is None:
        return None
    return "strong" if r >= RVOL_STRONG else "normal" if r >= RVOL_NORMAL else "light"


WICK_LOOKBACK = 240        # 4H candles (~4 months) to look back for an old high / low


def old_extremes(c4: pd.DataFrame, price: float) -> dict:
    """The nearest earlier swing high above price and swing low below it, from wicks (not closes).
    Shown only as a heads-up where a move might stall when there's no shelf: never a target."""
    out = {"old_high": None, "old_low": None}
    if c4 is None or len(c4) < 10 or "High" not in c4:
        return out
    d = c4.iloc[-WICK_LOOKBACK:-1]
    hi, lo = d["High"].to_numpy(), d["Low"].to_numpy()
    k = 3
    highs = [(float(hi[i]), d.index[i]) for i in range(k, len(d) - k) if hi[i] == hi[i - k:i + k + 1].max() and hi[i] > price]
    lows = [(float(lo[i]), d.index[i]) for i in range(k, len(d) - k) if lo[i] == lo[i - k:i + k + 1].min() and lo[i] < price]
    if highs:
        v, t = min(highs, key=lambda x: x[0])
        out["old_high"] = dict(level=round(v, 2), time=t.isoformat())
    if lows:
        v, t = max(lows, key=lambda x: x[0])
        out["old_low"] = dict(level=round(v, 2), time=t.isoformat())
    return out


def current_status(c4: pd.DataFrame, res: dict, s: Settings) -> dict:
    st = _current_status(c4, res, s)
    try:      # volume on the breakout candle (information only: it doesn't change the rules)
        t = next((t for t in res["trades"] if t["outcome"] == "pending"), None) if st.get("state") in ("breakout_up", "breakdown") else None
        if t:
            st["rvol"] = rvol(c4, t["i0"])
    except Exception:
        pass
    try:
        ex = old_extremes(c4, float(res["close"].iloc[-1]))
        if not st.get("shelf_up") and ex["old_high"]:
            st["old_high"] = ex["old_high"]
        if not st.get("shelf_down") and ex["old_low"]:
            st["old_low"] = ex["old_low"]
    except Exception:
        pass
    return st


def _current_status(c4: pd.DataFrame, res: dict, s: Settings) -> dict:
    close = res["close"]
    n = len(close)
    st = dict(last_close=round(float(close.iloc[-1]), 2), last_candle=close.index[-1].isoformat(),
              last_candle_close=candle_end(close.index[-1]).isoformat(),
              state="none", label="No active setup", hub=None,
              spark=[round(float(x), 2) for x in close.iloc[-40:]])
    if not res["hubs"]:
        return st
    h = res["hubs"][-1]
    st["hub"] = dict(low=round(h["low"], 2), high=round(h["high"], 2), sessions=h["sessions"],
                     start=close.index[h["start_i"]].isoformat(), end=close.index[h["end_i"]].isoformat(),
                     spark_from=h["start_i"] - (n - len(st["spark"])), spark_to=h["end_i"] - (n - len(st["spark"])))
    atr_now = float(res["atr"].iloc[-1])
    base = len(close) - len(st["spark"])
    price = float(close.iloc[-1])

    def shelf_json(sh, is_target=False):
        if not sh:
            return None
        return dict(low=round(sh["low"], 2), high=round(sh["high"], 2), level=round(sh["level"], 2),
                    spark_from=sh["start_i"] - base, spark_to=sh["end_i"] - base, target=is_target)

    def from_hub():
        """Shelves measured from the current hub's edges (while a setup is in play)."""
        st["shelf_up"] = shelf_json(find_shelf(res["hubs"], h, 1, h["high"], atr_now, s))
        st["shelf_down"] = shelf_json(find_shelf(res["hubs"], h, -1, h["low"], atr_now, s))

    def from_price():
        """No setup in play: nearest hub zone fully above / fully below today's price."""
        above = [x for x in res["hubs"] if x["low"] > price]
        below = [x for x in res["hubs"] if x["high"] < price]
        up = min(above, key=lambda x: x["low"], default=None)
        dn = max(below, key=lambda x: x["high"], default=None)
        st["shelf_up"] = shelf_json(dict(up, level=up["low"])) if up else None
        st["shelf_down"] = shelf_json(dict(dn, level=dn["high"])) if dn else None

    if h["active"]:
        from_hub()
        st.update(state="forming", label=f"Inside hub ({h['sessions']} sessions)")
        return st
    trade = next((t for t in res["trades"] if t["hub"] is h), None)
    if trade is None:
        if n - 1 - h["end_i"] < s.horizon:
            from_hub()
            st.update(state="watching", label="Hub ended, watching for breakout")
        else:
            from_price()
        return st
    if trade["outcome"] == "pending":
        from_hub()
        st["target"] = round(trade["target"], 2)
        st["direction"] = "up" if trade["direction"] == 1 else "down"
        key = "shelf_up" if trade["direction"] == 1 else "shelf_down"
        st[key] = shelf_json(trade["shelf"], True)
        st["target_kind"] = "shelf" if trade["shelf"] else "atr"
        if trade["shelf"] is None:
            edge = h["high"] if trade["direction"] == 1 else h["low"]
            near = edge + trade["direction"] * 0.75 * float(res["atr"].iloc[trade["i0"]])
            far = edge + trade["direction"] * 1.25 * float(res["atr"].iloc[trade["i0"]])
            st["target_zone"] = dict(low=round(min(near, far), 2), high=round(max(near, far), 2))
        k = n - 1 - trade["i0"]
        word = "Breakout up" if trade["direction"] == 1 else "Breakdown"
        st.update(state="breakout_up" if trade["direction"] == 1 else "breakdown",
                  label=f"{word}, {k}/{s.horizon} candles")
        return st
    from_price()
    if n - 1 - trade["oi"] < s.horizon:
        st.update(state=trade["outcome"], label=trade["outcome"].capitalize())
    return st


def stats(trades: list) -> dict:
    done = [t for t in trades if t["outcome"] != "pending"]
    out = dict(n=len(done))
    for o in ("follow-through", "snap-back", "stalled"):
        out[o] = round(100 * sum(t["outcome"] == o for t in done) / len(done), 1) if done else 0.0
    fwd = [t["fwd"] for t in done if t["fwd"] is not None]
    out["avg_fwd_pct"] = round(float(np.mean(fwd)), 2) if fwd else None
    rets = [t["ret"] for t in done if t.get("ret") is not None]
    wins, losses = [r for r in rets if r > 0], [r for r in rets if r <= 0]
    out["trades"] = len(rets)
    out["win_rate"] = round(100 * len(wins) / len(rets), 1) if rets else None
    out["avg_win_pct"] = round(float(np.mean(wins)), 2) if wins else None
    out["avg_loss_pct"] = round(float(np.mean(losses)), 2) if losses else None
    out["expectancy_pct"] = round(float(np.mean(rets)), 2) if rets else None
    for d, name in ((1, "up"), (-1, "down")):
        sub = [t for t in done if t["direction"] == d]
        out[name] = dict(n=len(sub), follow_through=round(
            100 * sum(t["outcome"] == "follow-through" for t in sub) / len(sub), 1) if sub else 0.0)
    return out


def preclose_check(res: dict, price: float, s: Settings) -> dict | None:
    """Is price currently outside a hub that hasn't broken out yet? (checked before the 4H close)"""
    if not res["hubs"] or price is None:
        return None
    h = res["hubs"][-1]
    n = len(res["close"])
    if not h["active"]:
        if any(t["hub"] is h for t in res["trades"]) or n - 1 - h["end_i"] >= s.horizon:
            return None
    d = 1 if price > h["high"] else -1 if price < h["low"] else 0
    if d == 0:
        return None
    edge = h["high"] if d == 1 else h["low"]
    target, shelf = target_for(res["hubs"], h, d, float(res["atr"].iloc[-1]), s)
    return dict(direction=d, price=price, low=h["low"], high=h["high"], sessions=h["sessions"],
                edge=edge, target=target, shelf=shelf, reached=(price - target) * d >= 0)


def current_candle_start(now: pd.Timestamp) -> pd.Timestamp:
    now = pd.Timestamp(now).tz_convert(NY)
    mins = now.hour * 60 + now.minute
    return now.normalize() + pd.Timedelta(minutes=MID_MIN if mins >= MID_MIN else OPEN_MIN)


# ============================================================ tightening hubs (triangles), test mode
TRI_MIN, TRI_MAX = 8, 24          # window length in 4H candles (4 to 12 sessions)
TRI_CONTRACT = float(os.getenv("TRI_CONTRACT", 0.6))   # end width must be <= 60% of start width
TRI_MAX_ATR = float(os.getenv("TRI_MAX_ATR", 4.0))     # start width at most 4 x ATR (a consolidation, not a trend)
TRI_FLAT = 0.03                   # |slope| below 3% of ATR per candle counts as flat


def _line(y: np.ndarray):
    """Least-squares line through y over x = 0..n-1 -> (slope, intercept)."""
    x = np.arange(len(y), dtype=float)
    xm, ym = x.mean(), y.mean()
    m = ((x - xm) * (y - ym)).sum() / ((x - xm) ** 2).sum()
    return m, ym - m * xm


def _tri_fit(hi, lo, a, b, A):
    if not A or A != A:
        return None
    mh, ch = _line(hi[a:b + 1])
    ml, cl = _line(lo[a:b + 1])
    L = b - a
    w0, w1 = ch - cl, (mh * L + ch) - (ml * L + cl)
    if not (w0 > 0 and 0 < w1 <= TRI_CONTRACT * w0 and w0 <= TRI_MAX_ATR * A):
        return None
    flat = TRI_FLAT * A
    if mh < -flat and ml > flat:
        kind = "symmetric"
    elif abs(mh) <= flat and ml > flat:
        kind = "ascending"
    elif mh < -flat and abs(ml) <= flat:
        kind = "descending"
    else:
        return None                     # wedges/channels: trending, not tightening
    x = np.arange(L + 1)
    up, dn = mh * x + ch, ml * x + cl
    inside = ((hi[a:b + 1] <= up + 0.3 * A) & (lo[a:b + 1] >= dn - 0.3 * A)).mean()
    touches_up = (np.abs(hi[a:b + 1] - up) <= 0.3 * A).sum()
    touches_dn = (np.abs(lo[a:b + 1] - dn) <= 0.3 * A).sum()
    if inside < 0.85 or touches_up < 2 or touches_dn < 2:
        return None
    return dict(a=a, b=b, mh=mh, ch=ch, ml=ml, cl=cl, kind=kind)


class _Fit:
    """O(1) least-squares lines over any window, using prefix sums (fast enough for 100+ stocks)."""
    def __init__(self, y: np.ndarray):
        j = np.arange(len(y), dtype=float)
        z = lambda v: np.concatenate([[0.0], np.cumsum(v)])
        self.S1, self.Sj, self.Sjj, self.Sy, self.Sjy = z(np.ones_like(j)), z(j), z(j * j), z(y), z(j * y)

    def line(self, a: int, b: int):
        n = b - a + 1
        sj, sjj = self.Sj[b + 1] - self.Sj[a], self.Sjj[b + 1] - self.Sjj[a]
        sy, sjy = self.Sy[b + 1] - self.Sy[a], self.Sjy[b + 1] - self.Sjy[a]
        sx, sxx, sxy = sj - a * n, sjj - 2 * a * sj + n * a * a, sjy - a * sy     # shift x so the window starts at 0
        m = (n * sxy - sx * sy) / (n * sxx - sx * sx)
        return m, (sy - m * sx) / n


def find_tightening(c4: pd.DataFrame, atr: pd.Series) -> list[dict]:
    hi, lo, A = c4["High"].to_numpy(float), c4["Low"].to_numpy(float), atr.to_numpy(float)
    fh, fl = _Fit(hi), _Fit(lo)
    n, pats, cur = len(c4), [], None

    def fit(a, b, Ab):
        if not Ab or Ab != Ab:
            return None
        mh, ch = fh.line(a, b)
        ml, cl = fl.line(a, b)
        L = b - a
        w0, w1 = ch - cl, (mh * L + ch) - (ml * L + cl)
        if not (w0 > 0 and 0 < w1 <= TRI_CONTRACT * w0 and w0 <= TRI_MAX_ATR * Ab):
            return None                       # cheap rejection: most windows stop here
        flat = TRI_FLAT * Ab
        if mh < -flat and ml > flat:
            kind = "symmetric"
        elif abs(mh) <= flat and ml > flat:
            kind = "ascending"
        elif mh < -flat and abs(ml) <= flat:
            kind = "descending"
        else:
            return None
        x = np.arange(L + 1)
        up, dn = mh * x + ch, ml * x + cl
        h, l_ = hi[a:b + 1], lo[a:b + 1]
        if ((h <= up + 0.3 * Ab) & (l_ >= dn - 0.3 * Ab)).mean() < 0.85:
            return None
        if (np.abs(h - up) <= 0.3 * Ab).sum() < 2 or (np.abs(l_ - dn) <= 0.3 * Ab).sum() < 2:
            return None
        return dict(a=a, b=b, mh=mh, ch=ch, ml=ml, cl=cl, kind=kind)

    for b in range(TRI_MIN - 1, n):
        best = None
        for L in range(min(TRI_MAX, b + 1), TRI_MIN - 1, -1):
            best = fit(b - L + 1, b, A[b])
            if best:
                break
        if best and cur and best["a"] <= cur["b"] + 1:
            cur = best if best["a"] <= cur["a"] or best["b"] - best["a"] >= cur["b"] - cur["a"] else dict(cur, b=b)
        elif best:
            if cur:
                pats.append(cur)
            cur = best
        elif cur and b - cur["b"] > 0:
            pats.append(cur)
            cur = None
    if cur:
        pats.append(cur)
    for p in pats:
        a, b = p["a"], p["b"]
        p.update(start_i=a, end_i=b, low=float(lo[a:b + 1].min()), high=float(hi[a:b + 1].max()),
                 sessions=(b - a + 1) // 2, active=(b == n - 1))
    return pats


def tri_lines(p: dict, j: int) -> tuple[float, float]:
    """Upper and lower line values at candle j (lines extended past the pattern)."""
    k = j - p["a"]
    return p["mh"] * k + p["ch"], p["ml"] * k + p["cl"]


def analyze_tightening(c4: pd.DataFrame, res: dict, s: Settings) -> dict:
    close, atr = res["close"], res["atr"]
    n = len(close)
    pats = find_tightening(c4, atr)
    trades = []
    for p in pats:
        if p["active"]:
            continue
        brk = None
        for j in range(p["end_i"] + 1, min(p["end_i"] + 1 + s.horizon, n)):
            up, dn = tri_lines(p, j)
            if up <= dn:
                break                   # past the apex
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
        target, shelf = target_for(res["hubs"], dict(p, high=edge, low=edge), d, float(atr.iloc[i0]), s) \
            if res["hubs"] else (edge + d * s.target_atr * float(atr.iloc[i0]), None)
        if shelf is None:
            target = edge + d * s.target_atr * float(atr.iloc[i0])
        outcome, oi = "pending", None
        for j in range(i0, min(i0 + 1 + s.horizon, n)):
            c = close.iloc[j]
            if (c - target) * d >= 0:
                outcome, oi = "follow-through", j
                break
            if (c - edge) * d <= 0:
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
        trades.append(dict(hub=p, i0=i0, direction=d, edge=edge, target=target, shelf=shelf,
                           outcome=outcome, oi=oi, fwd=fwd, entry=entry, ret=ret, chase=chase))
    return dict(patterns=pats, trades=trades)
