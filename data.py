"""Market data providers. Each returns intraday bars (tz-aware index, Open/High/Low/Close)."""
from __future__ import annotations

import os
import zlib

import numpy as np
import pandas as pd
import requests

COLS = ["Open", "High", "Low", "Close"]


def provider() -> str:
    return os.getenv("DATA_PROVIDER", "alpaca").strip().lower()


def get_bars(symbol: str, days: int) -> pd.DataFrame:
    p = provider()
    if p == "alpaca":
        return _alpaca(symbol, days)
    if p == "yfinance":
        return _yfinance(symbol, days)
    if p == "demo":
        return _demo(symbol, days)
    raise ValueError(f"Unknown DATA_PROVIDER '{p}' (use alpaca, yfinance or demo)")


def latest_price(symbol: str) -> float | None:
    """Live price for the pre-close check (Alpaca uses the free real-time IEX feed)."""
    p = provider()
    if p == "alpaca":
        r = requests.get(f"https://data.alpaca.markets/v2/stocks/{symbol}/trades/latest",
                         headers={"APCA-API-KEY-ID": os.getenv("ALPACA_API_KEY", ""),
                                  "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET_KEY", "")},
                         params={"feed": "iex"}, timeout=15)
        if r.status_code != 200:
            raise RuntimeError(f"Alpaca error {r.status_code}: {r.text[:200]}")
        return float(r.json()["trade"]["p"])
    if p == "yfinance":
        import yfinance as yf
        return float(yf.Ticker(symbol).fast_info["last_price"])
    bars = get_bars(symbol, 5)
    return float(bars["Close"].iloc[-1]) if not bars.empty else None


def get_bars_many(symbols: list[str], days: int) -> dict[str, pd.DataFrame]:
    """Bars for many symbols at once (Alpaca batches 40 per request; others loop)."""
    if provider() != "alpaca":
        out = {}
        for s in symbols:
            try:
                out[s] = get_bars(s, days)
            except Exception:
                out[s] = pd.DataFrame(columns=COLS)
        return out
    out = {}
    for i in range(0, len(symbols), 40):
        out.update(_alpaca_many(symbols[i:i + 40], days))
    return out


def _alpaca(symbol: str, days: int) -> pd.DataFrame:
    return _alpaca_many([symbol], days).get(symbol, pd.DataFrame(columns=COLS))


def _alpaca_many(symbols: list[str], days: int) -> dict[str, pd.DataFrame]:
    key, secret = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("ALPACA_API_KEY and ALPACA_SECRET_KEY are not set")
    feed = os.getenv("ALPACA_FEED", "sip").lower()
    end = pd.Timestamp.now(tz="UTC")
    if feed == "sip":   # free plan: SIP data is available after a 15-minute delay
        end -= pd.Timedelta(minutes=16)
    start = end - pd.Timedelta(days=days)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    params = dict(symbols=",".join(symbols), timeframe="30Min", start=start.strftime(fmt), end=end.strftime(fmt),
                  limit=10000, adjustment="all", feed=feed)
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    rows: dict[str, list] = {s: [] for s in symbols}
    while True:
        r = requests.get("https://data.alpaca.markets/v2/stocks/bars", headers=headers, params=params, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"Alpaca error {r.status_code}: {r.text[:200]}")
        js = r.json()
        for sym, bars in (js.get("bars") or {}).items():
            rows.setdefault(sym, []).extend(bars or [])
        token = js.get("next_page_token")
        if not token:
            break
        params["page_token"] = token
    out = {}
    for sym, rs in rows.items():
        if not rs:
            out[sym] = pd.DataFrame(columns=COLS)
            continue
        df = pd.DataFrame(rs)
        df.index = pd.to_datetime(df["t"], utc=True)
        out[sym] = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close"})[COLS].sort_index()
    return out


def _yfinance(symbol: str, days: int) -> pd.DataFrame:
    import yfinance as yf
    df = yf.Ticker(symbol).history(period=f"{min(days, 729)}d", interval="1h", prepost=False)
    return df[COLS] if not df.empty else pd.DataFrame(columns=COLS)


def _demo(symbol: str, days: int) -> pd.DataFrame:
    """Fake but realistic-looking prices, so you can try the app without any API keys."""
    rng = np.random.default_rng(zlib.crc32(symbol.encode()))
    now = pd.Timestamp.now(tz="America/New_York")
    first = pd.Timestamp("2023-01-02", tz="America/New_York")   # fixed start = same prices on every call
    sessions = pd.bdate_range(first, now.normalize(), tz="America/New_York")
    offsets = pd.to_timedelta([9 * 60 + 30 + 30 * k for k in range(13)], unit="m")
    idx = pd.DatetimeIndex([d + o for d in sessions for o in offsets])
    idx = idx[idx + pd.Timedelta(minutes=30) <= now]
    vol = np.repeat(rng.choice([0.0012, 0.004, 0.006], size=len(sessions) // 3 + 2,
                               p=[0.35, 0.4, 0.25]), 39)[:len(idx)]
    close = (50 + rng.random() * 400) * np.exp(np.cumsum(rng.normal(0, 1, len(idx)) * vol))
    spread = close * 0.002
    df = pd.DataFrame({"Open": close, "High": close + spread, "Low": close - spread, "Close": close}, index=idx)
    return df[df.index >= now - pd.Timedelta(days=days)].tz_convert("UTC")
