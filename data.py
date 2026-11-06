"""Market data providers. Each returns intraday bars (tz-aware index, Open/High/Low/Close)."""
from __future__ import annotations

import os
import time
import zlib

import numpy as np
import pandas as pd
import requests

COLS = ["Open", "High", "Low", "Close", "Volume"]


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


def close_at(symbol: str, end: pd.Timestamp) -> float | None:
    """Closing price at `end` from the real-time IEX feed: the last 1-minute bar that finished by then.
    Used for the early read right after the 1:30 PM close, before the 15-minute-delayed full data arrives."""
    if provider() == "alpaca":
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        e = pd.Timestamp(end).tz_convert("UTC")
        r = requests.get(f"https://data.alpaca.markets/v2/stocks/{symbol}/bars",
                         headers={"APCA-API-KEY-ID": os.getenv("ALPACA_API_KEY", ""),
                                  "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET_KEY", "")},
                         params={"timeframe": "1Min", "start": (e - pd.Timedelta(minutes=10)).strftime(fmt),
                                 "end": e.strftime(fmt), "feed": "iex", "limit": 20}, timeout=15)
        if r.status_code != 200:
            raise RuntimeError(f"Alpaca error {r.status_code}: {r.text[:200]}")
        bars = [b for b in (r.json().get("bars") or []) if pd.Timestamp(b["t"]) < e]
        return float(bars[-1]["c"]) if bars else None
    return latest_price(symbol)


# ---------------------------------------------------------------- live prices (watching only; signals still use 4H closes)
_LIVE: dict[str, tuple[float, dict]] = {}
LIVE_TTL = 15          # seconds: everyone shares one fetch, so Alpaca's free rate limit is never a problem


def market_open(now: pd.Timestamp | None = None) -> bool:
    """Regular session, Mon-Fri 9:30-16:00 New York (market holidays aren't checked)."""
    now = (now or pd.Timestamp.now(tz="UTC")).tz_convert("America/New_York")
    mins = now.hour * 60 + now.minute
    return now.weekday() < 5 and 9 * 60 + 30 <= mins < 16 * 60


def latest_prices(symbols: list[str]) -> dict[str, dict]:
    """Latest trade price + change vs yesterday's close, for many symbols at once (cached for LIVE_TTL)."""
    now = time.time()
    want = [s for s in dict.fromkeys(symbols) if s]
    stale = [s for s in want if s not in _LIVE or now - _LIVE[s][0] > LIVE_TTL]
    if stale:
        try:
            fresh = _alpaca_snapshots(stale) if provider() == "alpaca" else _demo_live(stale)
        except Exception:
            fresh = {}
        for s, v in fresh.items():
            _LIVE[s] = (now, v)
    return {s: _LIVE[s][1] for s in want if s in _LIVE}


def _alpaca_snapshots(symbols: list[str]) -> dict[str, dict]:
    headers = {"APCA-API-KEY-ID": os.getenv("ALPACA_API_KEY", ""), "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET_KEY", "")}
    out = {}
    for i in range(0, len(symbols), 100):
        r = requests.get("https://data.alpaca.markets/v2/stocks/snapshots", headers=headers, timeout=15,
                         params={"symbols": ",".join(symbols[i:i + 100]), "feed": os.getenv("ALPACA_LIVE_FEED", "iex")})
        if r.status_code != 200:
            raise RuntimeError(f"Alpaca error {r.status_code}: {r.text[:200]}")
        for sym, snap in (r.json() or {}).items():
            if not snap:
                continue
            t = snap.get("latestTrade") or {}
            prev = (snap.get("prevDailyBar") or {}).get("c")
            px = t.get("p") or (snap.get("minuteBar") or {}).get("c") or (snap.get("dailyBar") or {}).get("c")
            if not px:
                continue
            out[sym] = dict(price=round(float(px), 2), time=t.get("t"),
                            change_pct=round((float(px) / float(prev) - 1) * 100, 2) if prev else None)
    return out


def _demo_live(symbols: list[str]) -> dict[str, dict]:
    """Demo: the last demo close plus a small drift that changes every few seconds."""
    out, tick = {}, int(time.time() // 5)
    for s in symbols:
        b = _demo(s, 5)
        if b.empty:
            continue
        last = float(b["Close"].iloc[-1])
        prev = float(b["Close"].iloc[-14]) if len(b) > 14 else last
        wiggle = np.random.default_rng(zlib.crc32(f"{s}{tick}".encode())).normal(0, 0.0025)
        px = last * (1 + wiggle)
        out[s] = dict(price=round(px, 2), time=pd.Timestamp.now(tz="UTC").isoformat(), change_pct=round((px / prev - 1) * 100, 2))
    return out


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
        out[sym] = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"}).reindex(columns=COLS).sort_index()
    return out


def _yfinance(symbol: str, days: int) -> pd.DataFrame:
    import yfinance as yf
    df = yf.Ticker(symbol).history(period=f"{min(days, 729)}d", interval="1h", prepost=False)
    return df.reindex(columns=COLS) if not df.empty else pd.DataFrame(columns=COLS)


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
    move = np.abs(np.diff(np.log(close), prepend=np.log(close[0]))) / np.maximum(vol, 1e-9)
    volume = np.round(rng.lognormal(13, 0.35, len(idx)) * (0.6 + 0.5 * np.minimum(move, 4)))
    df = pd.DataFrame({"Open": close, "High": close + spread, "Low": close - spread, "Close": close, "Volume": volume}, index=idx)
    return df[df.index >= now - pd.Timedelta(days=days)].tz_convert("UTC")


# ---------------------------------------------------------------- exchange per symbol (for Webull links)
# Webull quote URLs need the exchange (webull.com/quote/nasdaq-aapl). Resolve it from the provider,
# cache it, and return None when unknown: a guessed Webull link is worse than none.
_EXCHANGE_CACHE: dict[str, str | None] = {}
_EXCHANGE_MAP = {"NASDAQ": "nasdaq", "NMS": "nasdaq", "NGM": "nasdaq", "NCM": "nasdaq",
                 "NYSE": "nyse", "NYQ": "nyse",
                 "ARCA": "nysearca", "NYSEARCA": "nysearca", "PCX": "nysearca",
                 "AMEX": "amex", "ASE": "amex"}
_ALPACA_ASSETS = {"loaded": 0.0}


def _load_alpaca_exchanges():
    """One bulk call for every US equity (paper endpoint; read-only). Retries at most hourly on failure."""
    if time.time() - _ALPACA_ASSETS["loaded"] < 3600:
        return
    _ALPACA_ASSETS["loaded"] = time.time()
    try:
        r = requests.get("https://paper-api.alpaca.markets/v2/assets",
                         headers={"APCA-API-KEY-ID": os.getenv("ALPACA_API_KEY", ""),
                                  "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET_KEY", "")},
                         params={"status": "active", "asset_class": "us_equity"}, timeout=30)
        if r.status_code >= 300:
            return
        for a in r.json():
            sym = str(a.get("symbol", "")).upper()
            if sym:
                _EXCHANGE_CACHE[sym] = _EXCHANGE_MAP.get(str(a.get("exchange", "")).upper())
    except Exception:
        pass


def exchange_for(symbol: str) -> str | None:
    """Webull slug for a symbol ('nasdaq' / 'nyse' / 'nysearca' / 'amex'), or None. Never raises."""
    sym = (symbol or "").upper()
    if sym in _EXCHANGE_CACHE:
        return _EXCHANGE_CACHE[sym]
    p = provider()
    try:
        if p == "alpaca":
            _load_alpaca_exchanges()
            return _EXCHANGE_CACHE.get(sym)          # not cached on a miss, so the hourly reload can fill it
        if p == "yfinance":
            import yfinance as yf
            _EXCHANGE_CACHE[sym] = _EXCHANGE_MAP.get(str(yf.Ticker(sym).info.get("exchange", "")).upper())
            return _EXCHANGE_CACHE[sym]
    except Exception:
        return None
    return None                                      # demo: no real exchange, so no Webull link
