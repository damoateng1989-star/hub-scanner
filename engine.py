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
    min_sessions: int = 2      # hub must last at least this many sessions
    min_candles: int = 4       # ... and at least this many 4H candles (the binding floor)
    k: float = 1.5             # closes must stay within k x 4H ATR(14)
    horizon: int = 10          # 4H candles to judge a breakout
    target_atr: float = 1.0    # fallback target beyond hub edge, in ATR, when there is no shelf
    shelf_min_atr: float = 0.5 # a shelf must sit at least this far (in ATR) beyond the hub edge
    vol_mult: float = 1.0      # rule #5: breakout candle volume must clear this x the same-slot average (0 = off)
    vol_window: int = 20       # same-slot 4H candles averaged for the volume baseline
    min_room_pct: float = 0.0  # skip a breakout whose target is closer than this (% from the breakout close); 0 = off

    @property
    def window(self) -> int:
        return self.min_candles       # the hub window is counted in candles now, not sessions

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            min_sessions=int(os.getenv("MIN_SESSIONS", 2)),
            min_candles=int(os.getenv("MIN_HUB_CANDLES", 4)),
            k=float(os.getenv("HUB_K", 1.5)),
            horizon=int(os.getenv("HORIZON", 10)),
            target_atr=float(os.getenv("TARGET_ATR", 1.0)),
            shelf_min_atr=float(os.getenv("SHELF_MIN_ATR", 0.5)),
            vol_mult=float(os.getenv("VOLUME_MULT", 1.0)),
            vol_window=int(os.getenv("VOLUME_WINDOW", 20)),
            min_room_pct=float(os.getenv("MIN_ROOM_PCT", 0.0)),
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



def to_daily(c4: pd.DataFrame) -> pd.DataFrame:
    """4H candles -> daily candles (regular hours only, finished candles).
    Aggregates the 4H frame, so the daily view can never disagree with the
    4H view and costs no new fetch. No partial-day candle: only finished
    4H candles are in the frame."""
    if c4.empty:
        return c4
    df = c4.tz_convert(NY)
    agg = dict(Open=("Open", "first"), High=("High", "max"), Low=("Low", "min"), Close=("Close", "last"))
    if "Volume" in df.columns:
        agg["Volume"] = ("Volume", "sum")
    out = df.groupby(df.index.normalize()).agg(**agg)
    out.index.name = None
    return out

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


def slot_volume_avg(c4: pd.DataFrame, window: int = 20) -> pd.Series | None:
    """For each 4H candle, the average volume of the previous `window` candles in the same slot
    (9:30-1:30 vs 1:30-4:00 trade very different amounts: same baseline as engine.rvol). NaN with < 5 of them."""
    if "Volume" not in c4.columns:
        return None
    v = c4["Volume"].astype(float).where(lambda x: x > 0)
    return v.groupby(c4.index.hour).transform(lambda x: x.shift().rolling(window, min_periods=5).mean())


def _vol_ok(vol: pd.Series | None, vol_avg: pd.Series | None, i: int, s: Settings) -> bool:
    """Rule #5: the breakout candle's volume must clear vol_mult x its same-slot average."""
    if s.vol_mult <= 0 or vol is None or vol_avg is None:
        return True            # gate off, or no volume data: fail open, never block on missing data
    avg = vol_avg.iloc[i]
    if pd.isna(avg) or avg <= 0 or pd.isna(vol.iloc[i]):
        return True            # not enough history yet: fail open
    return vol.iloc[i] >= s.vol_mult * avg


def analyze(c4: pd.DataFrame, s: Settings) -> dict:
    close = c4["Close"]
    n = len(close)
    prev = close.shift()
    tr = pd.concat([c4.High - c4.Low, (c4.High - prev).abs(), (c4.Low - prev).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    vol = c4["Volume"] if "Volume" in c4.columns else None
    vol_avg = slot_volume_avg(c4, s.vol_window) if vol is not None else None

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

    for h in hubs:
        h.update(_hub_grade(c4, atr, h, s))

    # --- breakouts and outcomes
    trades = []
    for h in hubs:
        if h["active"]:
            continue
        brk = None
        for i in range(h["end_i"] + 1, min(h["end_i"] + 1 + s.horizon, n)):
            c = close.iloc[i]
            dd = 1 if c > h["high"] else -1 if c < h["low"] else 0
            if dd == 0:
                continue
            if not _vol_ok(vol, vol_avg, i, s):
                continue       # closed outside, but no volume on the break: keep watching
            brk = (i, dd)
            break
        if brk is None:
            continue
        i0, d = brk
        edge = h["high"] if d == 1 else h["low"]
        target, shelf = target_for(hubs, h, d, float(atr.iloc[i0]), s)
        room = d * (target / float(close.iloc[i0]) - 1) * 100
        if s.min_room_pct > 0 and room < s.min_room_pct:
            h["no_room"] = dict(i0=i0, direction=d, target=float(target), room=round(room, 2))
            continue           # the target is too close to be worth it (or already passed): no trade
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
                           shelf=t["shelf"], grade=h["grade"], grade_score=h["score"]))
        if t["outcome"] != "pending":
            events.append(dict(type=t["outcome"], time=idx[t["oi"]], close=float(close.iloc[t["oi"]]),
                               low=h["low"], high=h["high"], target=t["target"], shelf=t["shelf"],
                               direction=t["direction"], candles=t["oi"] - t["i0"]))
    events.sort(key=lambda e: e["time"])
    return dict(close=close, atr=atr, hubs=hubs, trades=trades, events=events, volume=vol, volume_avg=vol_avg)


def _hub_grade(c4: pd.DataFrame, atr: pd.Series, h: dict, s: Settings) -> dict:
    """Warehouse vs ledge, scored 0-6 from sessions + width + volume. Missing data
    scores a component neutral (1) rather than blocking the grade.
    Volume is a proxy (no volume profile): candles traded during the hub vs the 20 candles
    before it started, so the hub isn't compared with itself."""
    base = s.min_sessions
    sess = h["sessions"]
    sess_pts = 0 if sess < base + 2 else 1 if sess <= base + 4 else 2
    a = atr.iloc[h["end_i"]] if h["end_i"] < len(atr) else None
    width_atr = (h["high"] - h["low"]) / a if a and a == a and a > 0 else None
    width_pts = 1 if width_atr is None else 0 if width_atr < 0.75 else 1 if width_atr <= 1.5 else 2
    ratio = None
    try:
        if "Volume" in c4.columns:
            vol = c4["Volume"]
            si, ei = max(h["start_i"], 0), h["end_i"]
            before = vol.iloc[max(0, si - 20):si]
            avg = float(before.mean()) if len(before) >= 10 else None
            got = float(vol.iloc[si:ei + 1].mean())
            ratio = got / avg if avg and avg > 0 and got == got else None
    except Exception:
        ratio = None
    vol_pts = 1 if ratio is None else 0 if ratio < 1.0 else 1 if ratio <= 1.5 else 2
    score = sess_pts + width_pts + vol_pts
    return dict(grade="warehouse" if score >= 4 else "ledge", score=score,
                parts=dict(sessions=sess_pts, width=width_pts, volume=vol_pts),
                width_atr=None if width_atr is None else round(float(width_atr), 2),
                vol_ratio=None if ratio is None else round(ratio, 2))


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


def regime_from_closes(c4: pd.DataFrame, days: int = 50) -> dict | None:
    """Market regime from an index's 4H candles: the last daily close vs its N-day
    average. Returns {"regime": "up"|"down", "close": ..., "sma": ...}, or None when
    there is not enough history to judge (fewer than `days` daily closes): fail open."""
    if c4 is None or c4.empty or "Close" not in c4.columns:
        return None
    daily = c4["Close"].groupby(c4.index.normalize()).last()
    if len(daily) < days:
        return None
    sma = float(daily.rolling(days).mean().iloc[-1])
    close = float(daily.iloc[-1])
    return dict(regime="up" if close > sma else "down", close=round(close, 2), sma=round(sma, 2))


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
                     grade=h.get("grade"), score=h.get("score"), parts=h.get("parts"),
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
        st.update(state="forming", label=f"Inside hub ({h['sessions']} sessions)", min_room_pct=s.min_room_pct)
        if s.window > 1 and atr_now == atr_now and atr_now > 0:      # the close that would actually end the hub (not just widen it)
            prev = res["close"].iloc[-(s.window - 1):]
            st["trigger_up"] = round(max(h["high"], float(prev.min()) + s.k * atr_now), 2)
            st["trigger_down"] = round(min(h["low"], float(prev.max()) - s.k * atr_now), 2)
        return st
    trade = next((t for t in res["trades"] if t["hub"] is h), None)
    if trade is None and h.get("no_room") and n - 1 - h["no_room"]["i0"] < s.horizon:
        from_price()
        nr = h["no_room"]
        st.update(state="no_room", label=f"{'Broke out' if nr['direction'] == 1 else 'Broke down'}, but " + (f"the target was only {nr['room']:.1f}% away" if nr["room"] > 0 else "it closed past the target already") + ": no trade")
        return st
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


def preclose_check(res: dict, price: float, s: Settings, forming_volume: float | None = None) -> dict | None:
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
    # While the hub is still active, a close just past its edge can keep the window tight: the hub only widens.
    # The close has to clear the last (window - 1) closes by more than k x ATR to end the hub and count.
    trigger = edge
    if h["active"] and s.window > 1:
        prev, A = res["close"].iloc[-(s.window - 1):], float(res["atr"].iloc[-1])
        if A == A and A > 0:
            trigger = max(edge, float(prev.min()) + s.k * A) if d == 1 else min(edge, float(prev.max()) - s.k * A)
    target, shelf = target_for(res["hubs"], h, d, float(res["atr"].iloc[-1]), s)
    vol_ok = None
    if forming_volume is not None and s.vol_mult > 0 and res.get("volume") is not None:
        try:      # the forming candle's projected volume vs the same slot's average
            v, idx = res["volume"].astype(float), res["close"].index
            slot = current_candle_start(pd.Timestamp.now(tz="UTC")).hour
            prev = v[(idx.hour == slot) & (v > 0)].tail(s.vol_window)
            if len(prev) >= 5 and prev.mean() > 0:
                vol_ok = bool(forming_volume >= s.vol_mult * prev.mean())
        except Exception:
            vol_ok = None
    est = max(price, trigger) if d == 1 else min(price, trigger)          # the soonest a counting close could be
    if s.min_room_pct > 0 and d * (target / est - 1) * 100 < s.min_room_pct:
        return None        # not enough room to the target: it wouldn't be a setup, so no heads-up
    return dict(direction=d, price=price, low=h["low"], high=h["high"], sessions=h["sessions"],
                edge=edge, trigger=round(trigger, 2), widens=(price - trigger) * d <= 0,
                target=target, shelf=shelf, reached=(price - target) * d >= 0, vol_ok=vol_ok)


GRADE_MIN = 5    # fewer finished setups than this: no grade


def setup_grade(trades: list, d: int | None = None) -> dict:
    """How this setup has done on this stock, graded on profit factor (total won / total lost), not the win rate:
    small wins and bigger losses can lose money at a decent win rate. A 1.5+, B 1.1+, C 0.9+ (about break-even), D below."""
    done = [t for t in trades if t["outcome"] != "pending" and t.get("ret") is not None and (d is None or t["direction"] == d)]
    n = len(done)
    if n < GRADE_MIN:
        return dict(grade="?", word="Not enough history", n=n)
    rets = [t["ret"] for t in done]
    avg = float(np.mean(rets)); win = 100 * sum(r > 0 for r in rets) / n
    won, lost = sum(r for r in rets if r > 0), -sum(r for r in rets if r <= 0)
    pf = won / lost if lost > 0 else (9.99 if won > 0 else 0.0)     # total won / total lost; above 1 made money
    g, w = ("A", "Strong") if pf >= 1.5 else ("B", "Fair") if pf >= 1.1 else ("C", "Weak") if pf >= 0.9 else ("D", "Poor")
    return dict(grade=g, word=w, n=n, avg=round(avg, 2), win=round(win, 1), pf=round(pf, 2),
                reached=sum(t["outcome"] == "follow-through" for t in done))


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


# ---------------------------------------------------------------- post-earnings gap fade & rip (daily candles), Patch 38
PEG_GAP = 0.05           # gap up vs prior close (3% / 4% / 5% tested; 5% wins marginally; untested past 5%)
PEG_FADE_WICK = 0.5      # gap-day upper wick must be at least this x the day's range
PEG_RECLAIM_MAX = 5      # the reclaim (close back above the gap-day high) must come within this many sessions
PEG_TARGET_R = 2.0       # target = entry + 2R
PEG_MIN_DAILY = 60       # daily candles needed before the detector runs


def _gapfade_scan(d: pd.DataFrame) -> tuple[list[dict], dict | None]:
    """Daily candles -> (entered setups, the setup still pending at the last candle or None). Long only.
    Gap day: open >= prior close x (1 + PEG_GAP) with an upper wick >= half the day's range. Entry: the first
    close above the gap-day high within PEG_RECLAIM_MAX sessions. Dead: a close below the lowest low so far.
    Stop: the lowest low from the gap day through the entry candle. A new gap while one is pending replaces it."""
    n = len(d)
    if n < PEG_MIN_DAILY:
        return [], None
    close, open_, high, low = (d[c].to_numpy(float) for c in ("Close", "Open", "High", "Low"))
    setups, pending, i = [], None, 1
    while i < n:
        if not (open_[i] >= close[i - 1] * (1 + PEG_GAP) and high[i] > low[i]
                and (high[i] - close[i]) >= PEG_FADE_WICK * (high[i] - low[i])):
            i += 1
            continue
        g, gap_high, fade_low, i0, nxt = i, float(high[i]), float(low[i]), None, None
        last_k = min(g + PEG_RECLAIM_MAX, n - 1)
        state = "expired"
        for k in range(g + 1, last_k + 1):
            if (open_[k] >= close[k - 1] * (1 + PEG_GAP) and high[k] > low[k]
                    and (high[k] - close[k]) >= PEG_FADE_WICK * (high[k] - low[k])):
                nxt, state = k, "superseded"           # a new gap-and-fade replaces this one
                break
            if close[k] < fade_low:                    # closed below the lowest low so far: dead
                state = "dead"
                break
            fade_low = min(fade_low, float(low[k]))
            if close[k] > gap_high:                    # reclaim
                i0, state = k, "entered"
                break
        if i0 is not None:
            entry = float(close[i0])
            stop = float(min(low[g:i0 + 1]))
            R = entry - stop
            if R > 0:
                setups.append(dict(gap_i=g, i0=i0, entry=entry, stop=stop, target=entry + PEG_TARGET_R * R, risk=R,
                                   gap_open=float(open_[g]), gap_high=gap_high, gap_pct=float(open_[g] / close[g - 1] - 1),
                                   fade_low=stop))
            i = i0 + 1
            continue
        if state == "expired" and g + PEG_RECLAIM_MAX > n - 1:     # window still open at the last candle: pending
            pending = dict(gap_i=g, gap_open=float(open_[g]), gap_high=gap_high, fade_low=fade_low,
                           gap_pct=float(open_[g] / close[g - 1] - 1), left=g + PEG_RECLAIM_MAX - (n - 1))
        i = nxt if nxt is not None else g + 1
    return setups, pending


def find_gapfade(d: pd.DataFrame) -> list[dict]:
    return _gapfade_scan(d)[0]


def analyze_gapfade(daily: pd.DataFrame, s: Settings) -> dict:
    """Daily frame -> gap fade & rip trades with the close-based 2R / stop / horizon walk.
    Trades carry `ret` (%) for the record and `ret_r` (R units) for the backtest. Indices are daily."""
    close = daily["Close"]
    n = len(close)
    setups, pending = _gapfade_scan(daily)
    trades = []
    for st in setups:
        i0, entry, stop, target, R = st["i0"], st["entry"], st["stop"], st["target"], st["risk"]
        outcome, oi, ret_r = "pending", None, None
        for j in range(i0 + 1, min(i0 + 1 + s.horizon, n)):
            c = close.iloc[j]
            if c >= target:
                outcome, oi, ret_r = "follow-through", j, PEG_TARGET_R
                break
            if c <= stop:
                outcome, oi, ret_r = "snap-back", j, -1.0
                break
        else:
            if i0 + s.horizon < n:
                outcome, oi = "stalled", i0 + s.horizon
                ret_r = (close.iloc[i0 + s.horizon] - entry) / R
        trades.append(dict(pattern=st, i0=i0, direction=1, edge=st["gap_high"], target=target, shelf=None,
                           outcome=outcome, oi=oi, entry=entry, ret=None if ret_r is None else ret_r * R / entry * 100,
                           ret_r=ret_r, chase=False, stop=stop, risk=R,
                           fwd=None if i0 + s.horizon >= n else float(close.iloc[i0 + s.horizon] / entry - 1) * 100))
    return dict(patterns=[t["pattern"] for t in trades], trades=trades, pending=pending)


def complete_days(c4: pd.DataFrame) -> pd.DataFrame:
    """Daily candles from the 4H frame, dropping today's if its 1:30-4:00 candle hasn't closed yet
    (a midday 'daily close' is just the 1:30 close)."""
    d = to_daily(c4)
    if len(d) and len(c4):
        last = c4.index[-1].tz_convert(NY)
        if last.hour < 13:
            d = d.iloc[:-1]
    return d
