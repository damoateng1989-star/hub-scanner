"""Futures Lab (Nov 23): does the NextShelf hub/retest edge hold on CME micro index futures?

Research only, owner-only, nothing live. It:
1. downloads 30-minute bars for each quarterly contract (MES, MNQ) from Massive (MASSIVE_API_KEY);
2. stitches them into one continuous, back-adjusted series (roll 8 days before the third-Friday expiry;
   older prices shifted by the price gap at each roll, so a roll never looks like a breakout);
3. builds 4-hour candles on the futures session (Globex opens 6 PM ET: 6-10 PM, 10 PM-2 AM, 2-6 AM, 6-10 AM,
   10 AM-2 PM, 2-5 PM);
4. replays them through the SAME trade replay as the stock Lab (lab_exits.trades_for), unchanged rules;
5. scores the live rules (hub A retests longs/shorts, ...) and the breakout/retest groups, per contract.

The stock engine's horizon is 10 candles. Futures print ~6 four-hour candles a day (stocks ~2), so the run also
scores a "same calendar time" variant with a 30-candle horizon. Results are saved to the "futures_lab" setting.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

import db

log = logging.getLogger("futures_lab")
BASE = "https://api.massive.com"
PRODUCTS = {"MES": "Micro E-mini S&P 500 ($5/pt)", "MNQ": "Micro E-mini Nasdaq-100 ($2/pt)"}
MONTHS = {3: "H", 6: "M", 9: "U", 12: "Z"}
ROLL_DAYS = 8
DIR = db.DATA_DIR / "futures"
_RUN = {"thread": None}


def _key() -> str:
    return os.getenv("MASSIVE_API_KEY", "").strip()


def configured() -> bool:
    return bool(_key())


def _gap() -> float:     # Massive's free plan allows 5 calls a minute; a paid plan can set MASSIVE_RATE_S=0
    try:
        return float(os.getenv("MASSIVE_RATE_S", "12.5"))
    except ValueError:
        return 12.5


# ---------------------------------------------------------------- contracts and rolls
def third_friday(y: int, m: int) -> date:
    d = date(y, m, 1)
    return d + timedelta(days=(4 - d.weekday()) % 7 + 14)


def contracts(product: str, start: date, end: date) -> list[dict]:
    """Quarterly contracts covering [start, end], each with the window it is the front month in."""
    out, y, m = [], start.year, start.month
    m = next(q for q in (3, 6, 9, 12, 15) if q >= m)
    if m == 15:
        y, m = y + 1, 3
    prev_roll = start
    while prev_roll <= end:
        exp = third_friday(y, m)
        roll = exp - timedelta(days=ROLL_DAYS)
        if roll > start:
            out.append(dict(ticker=f"{product}{MONTHS[m]}{y % 10}", expiry=exp, roll=roll, front_from=prev_roll))
            prev_roll = roll
        m += 3
        if m > 12:
            y, m = y + 1, 3
    return out


# ---------------------------------------------------------------- download (cached per contract)
def _ts(v) -> pd.Timestamp:
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
        n = int(v)
        unit = "ns" if n > 1e17 else "us" if n > 1e14 else "ms" if n > 1e11 else "s"
        return pd.Timestamp(n, unit=unit, tz="UTC")
    t = pd.Timestamp(v)
    return t.tz_localize("America/Chicago") if t.tzinfo is None else t      # Massive's aggregates are in Central Time


def bars(ticker: str, start: date, end: date, refresh: bool = False) -> pd.DataFrame:
    DIR.mkdir(parents=True, exist_ok=True)
    f = DIR / f"{ticker}_30m.csv"
    if f.exists() and not refresh:
        df = pd.read_csv(f, index_col=0)
        df.index = pd.to_datetime(df.index, utc=True)
        return df
    rows, url = [], f"{BASE}/futures/v1/aggs/{ticker}"
    params = {"resolution": "30min", "window_start.gte": start.isoformat(), "window_start.lte": end.isoformat(),
              "limit": 50000, "sort": "window_start.asc", "apiKey": _key()}
    for _ in range(60):
        r = requests.get(url, params=params, timeout=60)
        if r.status_code == 429:
            time.sleep(65)
            continue
        if r.status_code in (401, 403):
            raise RuntimeError(f"Massive refused the key ({r.status_code}). Check MASSIVE_API_KEY and the futures plan.")
        r.raise_for_status()
        j = r.json()
        for b in j.get("results") or []:
            rows.append((_ts(b.get("window_start") or b.get("timestamp") or b.get("t")), float(b["open"]), float(b["high"]),
                         float(b["low"]), float(b["close"]), float(b.get("volume") or 0)))
        nxt = j.get("next_url")
        time.sleep(_gap())
        if not nxt:
            break
        url, params = nxt, {"apiKey": _key()}
    df = pd.DataFrame(rows, columns=["t", "Open", "High", "Low", "Close", "Volume"]).drop_duplicates("t").set_index("t").sort_index()
    df.to_csv(f)
    return df


# ---------------------------------------------------------------- continuous series and the futures 4-hour session
def continuous(product: str, start: date, end: date, fetch=bars, progress=None) -> pd.DataFrame:
    """Front-month 30-minute bars, back-adjusted (absolute) at each roll."""
    cs = contracts(product, start, end)
    segs = []
    for k, c in enumerate(cs):
        if progress:
            progress(f"{product}: {c['ticker']} ({k + 1}/{len(cs)})")
        df = fetch(c["ticker"], c["front_from"] - timedelta(days=3), min(c["roll"], end) + timedelta(days=1))
        if df is None or df.empty:
            continue
        lo = pd.Timestamp(c["front_from"], tz="America/New_York")
        hi = pd.Timestamp(c["roll"], tz="America/New_York")
        segs.append((c, df, df[(df.index >= lo) & (df.index < hi)]))
    if not segs:
        return pd.DataFrame()
    adj = [0.0] * len(segs)
    for i in range(len(segs) - 1, 0, -1):          # walk back from the newest: shift older segments by each roll gap
        _, df_new, _ = segs[i]
        _, df_old, part_old = segs[i - 1]
        if part_old.empty:
            adj[i - 1] = adj[i]
            continue
        t_roll = part_old.index[-1]
        new_px = df_new[df_new.index <= t_roll]["Close"]
        gap = float(new_px.iloc[-1] - part_old["Close"].iloc[-1]) if len(new_px) else 0.0
        adj[i - 1] = adj[i] + gap
    out = []
    for (c, _, part), a in zip(segs, adj):
        p = part.copy()
        for col in ("Open", "High", "Low", "Close"):
            p[col] = p[col] + a
        out.append(p)
    return pd.concat(out).sort_index()


def four_hour(df30: pd.DataFrame) -> pd.DataFrame:
    """4-hour candles anchored to the 6 PM ET Globex open (the last one, 2-5 PM, is 3 hours)."""
    if df30.empty:
        return df30
    et = df30.index.tz_convert("America/New_York")
    shifted = et - pd.Timedelta(hours=18)
    key = shifted.floor("4h") + pd.Timedelta(hours=18)
    g = df30.groupby(key)
    c4 = pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(), "Low": g["Low"].min(),
                       "Close": g["Close"].last(), "Volume": g["Volume"].sum()})
    c4.index = pd.DatetimeIndex(c4.index).tz_convert("America/New_York")
    return c4[c4["Volume"] > 0]


# ---------------------------------------------------------------- the test
def _stats(rows: list[dict], key: str) -> dict | None:
    rs = [r["ex"][key][0] for r in rows if key in r.get("ex", {})]
    if not rs:
        return None
    wins = [x for x in rs if x > 0]
    loss = [x for x in rs if x <= 0]
    pf = (sum(wins) / abs(sum(loss))) if loss and sum(loss) else None
    return dict(n=len(rs), win=round(100 * len(wins) / len(rs), 1), avg=round(sum(rs) / len(rs), 3),
                pf=round(pf, 2) if pf else None, total=round(sum(rs), 2))


def score(rows: list[dict]) -> dict:
    import lab_exits
    live = []
    for key, label, test, ex in lab_exits.LIVE_RULES:
        sub = [r for r in rows if test(r)]
        live.append(dict(key=key, label=label, exit=ex, stats=_stats(sub, ex)))
    groups = []
    for gk, glabel, _ in lab_exits.GROUPS:
        for entry in ("breakout", "retest"):
            for d, side in ((1, "longs"), (-1, "shorts")):
                sub = [r for r in rows if r["grp"] == gk and r.get("entry", "breakout") == entry and r["d"] == d]
                if sub:
                    groups.append(dict(group=glabel, entry=entry, side=side, edge=_stats(sub, "edge"), atr10=_stats(sub, "atr10")))
    return dict(live=live, groups=groups, trades=len(rows))


def status() -> dict:
    try:
        return json.loads(db.get_setting("futures_lab") or "{}")
    except ValueError:
        return {}


def _save(**kw) -> None:
    st = status()
    st.update(kw)
    db.set_setting("futures_lab", json.dumps(st, default=str))


def run(products=("MES", "MNQ"), years: int = 2, fetch=bars) -> dict:
    import dataclasses
    import engine
    import lab_exits
    import scanner
    end = date.today()
    start = end - timedelta(days=int(365 * years) - 5)
    _save(state="running", started=datetime.now(timezone.utc).isoformat(), progress="starting", error=None)
    res = {}
    try:
        for p in products:
            df30 = continuous(p, start, end, fetch=fetch, progress=lambda m: _save(progress=m))
            c4 = four_hour(df30)
            out = dict(name=PRODUCTS.get(p, p), bars_30m=len(df30), candles_4h=len(c4),
                       first=str(c4.index[0]) if len(c4) else None, last=str(c4.index[-1]) if len(c4) else None)
            if len(c4) >= 120:
                s = scanner.SETTINGS
                out["standard"] = score(lab_exits.trades_for(p, c4, s, None))
                s30 = dataclasses.replace(s, horizon=max(s.horizon, 30))
                out["calendar"] = score(lab_exits.trades_for(p, c4, s30, None))
            else:
                out["error"] = "Not enough candles for a test."
            res[p] = out
            _save(progress=f"{p} done", results=res)
        _save(state="done", finished=datetime.now(timezone.utc).isoformat(), progress="done", results=res,
              window=f"{start} to {end}", horizon=dict(standard=scanner.SETTINGS.horizon, calendar=max(scanner.SETTINGS.horizon, 30)))
    except Exception as e:
        log.exception("futures lab failed")
        _save(state="error", error=str(e)[:300])
    return status()


def start_background(products=("MES", "MNQ"), years: int = 2) -> bool:
    t = _RUN.get("thread")
    if t and t.is_alive():
        return False
    t = threading.Thread(target=run, kwargs=dict(products=products, years=years), daemon=True, name="futures-lab")
    _RUN["thread"] = t
    t.start()
    return True
