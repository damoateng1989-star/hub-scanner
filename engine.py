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
    min_sessions: int = 4      # hub must last at least this many sessions
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
            min_sessions=int(os.getenv("MIN_SESSIONS", 4)),
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
        Close=("Close", "last"), _end=("_end", "first"))
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


def current_status(c4: pd.DataFrame, res: dict, s: Settings) -> dict:
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
    def shelf_json(sh, is_target=False):
        if not sh:
            return None
        return dict(low=round(sh["low"], 2), high=round(sh["high"], 2), level=round(sh["level"], 2),
                    spark_from=sh["start_i"] - base, spark_to=sh["end_i"] - base, target=is_target)
    st["shelf_up"] = shelf_json(find_shelf(res["hubs"], h, 1, h["high"], atr_now, s))
    st["shelf_down"] = shelf_json(find_shelf(res["hubs"], h, -1, h["low"], atr_now, s))
    if h["active"]:
        st.update(state="forming", label=f"Inside hub ({h['sessions']} sessions)")
        return st
    trade = next((t for t in res["trades"] if t["hub"] is h), None)
    if trade is None:
        if n - 1 - h["end_i"] < s.horizon:
            st.update(state="watching", label="Hub ended, watching for breakout")
        return st
    st["target"] = round(trade["target"], 2)
    st["direction"] = "up" if trade["direction"] == 1 else "down"
    key = "shelf_up" if trade["direction"] == 1 else "shelf_down"
    st[key] = shelf_json(trade["shelf"], True)   # the shelf used as this breakout's target
    st["target_kind"] = "shelf" if trade["shelf"] else "atr"
    if trade["outcome"] == "pending":
        k = n - 1 - trade["i0"]
        word = "Breakout up" if trade["direction"] == 1 else "Breakdown"
        st.update(state="breakout_up" if trade["direction"] == 1 else "breakdown",
                  label=f"{word}, {k}/{s.horizon} candles")
    elif n - 1 - trade["oi"] < s.horizon:
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
