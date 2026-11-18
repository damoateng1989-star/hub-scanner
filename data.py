"""Market data providers. Each returns intraday bars (tz-aware index, Open/High/Low/Close)."""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import zlib
from pathlib import Path

import numpy as np
import pandas as pd
import requests

COLS = ["Open", "High", "Low", "Close", "Volume"]

# Persistent Alpaca bars cache. DATA_DIR matches db.py without importing it.
_LOG = logging.getLogger(__name__)
_BARS_DIR = Path(os.getenv("DATA_DIR", "data")) / "bars"
_BARS_DIR.mkdir(parents=True, exist_ok=True)


def _bars_path(symbol: str, days: int) -> Path:
    return _BARS_DIR / f"{symbol.upper()}_{int(days)}d.json"


def _disk_read(symbol: str, days: int) -> pd.DataFrame | None:
    """Read a validated UTC OHLCV frame; any cache problem is a cache miss."""
    try:
        payload = json.loads(_bars_path(symbol, days).read_text(encoding="utf-8"))
        bars = payload["bars"]
        if not isinstance(bars, list) or not bars or not isinstance(payload.get("fetched_at"), str):
            return None
        if any(not isinstance(row, list) or len(row) != 6 for row in bars):
            return None
        idx = pd.to_datetime([row[0] for row in bars], unit="s", utc=True, errors="raise")
        df = pd.DataFrame([row[1:] for row in bars], index=idx, columns=COLS)
        for col in COLS:
            df[col] = pd.to_numeric(df[col], errors="raise")
        if df.empty or not isinstance(df.index, pd.DatetimeIndex) or str(df.index.tz) != "UTC":
            return None
        return df.sort_index()
    except Exception:
        return None


def _disk_write(symbol: str, days: int, df: pd.DataFrame) -> None:
    """Best-effort atomic JSON write. A cache failure must never fail a chart."""
    if df is None or df.empty:
        return
    tmp_name = None
    try:
        path = _bars_path(symbol, days)
        path.parent.mkdir(parents=True, exist_ok=True)
        frame = df.reindex(columns=COLS).sort_index()
        if frame.empty:
            return
        if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
            raise ValueError("bars index must be timezone-aware")
        idx = frame.index.tz_convert("UTC")
        bars = []
        for ts, row in zip(idx, frame.itertuples(index=False, name=None)):
            vals = [round(float(v), 4) for v in row[:4]] + [int(row[4])]
            bars.append([int(ts.timestamp()), *vals])
        payload = {"fetched_at": pd.Timestamp.now(tz="UTC").isoformat(), "bars": bars}
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent),
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as f:
            tmp_name = f.name
            json.dump(payload, f, separators=(",", ":"), allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception as exc:
        _LOG.warning("Could not persist Alpaca bars for %s (%sd): %s", symbol, days, exc)
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def _alpaca_latest_starts(symbols: list[str], end: pd.Timestamp, feed: str,
                          headers: dict[str, str]) -> dict[str, pd.Timestamp | None]:
    """Get each symbol's latest 30-minute bar start. Isolated failures return None."""
    latest: dict[str, pd.Timestamp | None] = {}
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    start = end - pd.Timedelta(days=14)
    for symbol in symbols:
        try:
            r = requests.get(f"https://data.alpaca.markets/v2/stocks/{symbol}/bars",
                             headers=headers, timeout=15,
                             params={"timeframe": "30Min", "start": start.strftime(fmt),
                                     "end": end.strftime(fmt), "limit": 1, "sort": "desc",
                                     "adjustment": "all", "feed": feed})
            if r.status_code != 200:
                raise RuntimeError(f"Alpaca error {r.status_code}: {r.text[:200]}")
            bars = (r.json().get("bars") or [])
            latest[symbol] = pd.to_datetime(bars[0]["t"], utc=True) if bars else None
        except Exception as exc:
            _LOG.warning("Alpaca bars freshness check failed for %s: %s", symbol, exc)
            latest[symbol] = None
    return latest



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
_PREMARKET: dict | None = None
_PREMARKET_AT = 0.0


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


def _alpaca_snapshots(symbols: list[str], feed: str | None = None) -> dict[str, dict]:
    headers = {"APCA-API-KEY-ID": os.getenv("ALPACA_API_KEY", ""), "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET_KEY", "")}
    out = {}
    snapshot_feed = feed or os.getenv("ALPACA_LIVE_FEED", "iex")
    for i in range(0, len(symbols), 100):
        r = requests.get("https://data.alpaca.markets/v2/stocks/snapshots", headers=headers, timeout=15,
                         params={"symbols": ",".join(symbols[i:i + 100]), "feed": snapshot_feed})
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
                            change_pct=round((float(px) / float(prev) - 1) * 100, 2) if prev else None,
                            latestTrade=t, prevDailyBar=snap.get("prevDailyBar") or {})
    return out


_EXT: dict[str, tuple[float, dict]] = {}


def extended_price(sym: str) -> dict:
    """The latest pre-market (4:00-9:30 AM ET) or after-hours (4:00-8:00 PM ET) trade for one stock.
    Feeds, best first: full-market SIP (needs a paid Alpaca data plan for recent prices), then IEX (free, real time,
    but IEX only trades about 8:00 AM-5:00 PM ET), then 15-minute-delayed SIP (free). session=None outside those
    windows or with no trade yet; `why` says what happened. Display only: never triggers anything."""
    sym = sym.upper()
    now = time.time()
    hit = _EXT.get(sym)
    if hit and now - hit[0] < 60:
        return hit[1]
    et = pd.Timestamp.now(tz="America/New_York")
    mins = et.hour * 60 + et.minute
    window = "pre" if 240 <= mins < 570 else "post" if 960 <= mins < 1200 else None
    out = dict(session=None, price=None, at=None, feed=None, delayed=False, why=None)
    if et.weekday() >= 5 or not window:
        out["why"] = "outside pre-market and after-hours"
    elif provider() != "alpaca":
        out["why"] = "no live data provider"
    else:
        errors = []
        for feed in [f for f in dict.fromkeys([os.getenv("ALPACA_EXT_FEED", "sip"), "iex", "delayed_sip"]) if f]:
            try:
                snap = _alpaca_snapshots([sym], feed=feed).get(sym) or {}
            except Exception as e:
                errors.append(f"{feed}: {str(e)[:80]}")
                continue
            t = snap.get("latestTrade") or {}
            if not (t.get("p") and t.get("t")):
                errors.append(f"{feed}: no trade")
                continue
            ts = pd.Timestamp(t["t"])
            ts = (ts.tz_localize("UTC") if ts.tzinfo is None else ts).tz_convert("America/New_York")
            tm = ts.hour * 60 + ts.minute
            in_win = (240 <= tm < 570) if window == "pre" else (960 <= tm < 1200)
            if ts.date() == et.date() and in_win:
                out = dict(session=window, price=round(float(t["p"]), 2), at=ts.isoformat(), feed=feed,
                           delayed=feed == "delayed_sip", why=None)
                break
            errors.append(f"{feed}: last trade {ts.strftime('%b %d %H:%M')} ET is not in this session")
        if out["session"] is None:
            out["why"] = "; ".join(errors) or "no trade"
            logging.getLogger(__name__).info("extended price %s: %s", sym, out["why"])
    _EXT[sym] = (now, out)
    return out


def premarket_movers() -> dict:
    """Top premarket movers among names passing the board's liquidity filters."""
    global _PREMARKET, _PREMARKET_AT
    now = time.time()
    if _PREMARKET is not None and now - _PREMARKET_AT < 300:
        return _PREMARKET
    empty = dict(gainers=[], losers=[], as_of=None)
    try:
        if provider() != "alpaca":
            return empty
        import universe

        symbols = universe.board_universe()
        days = int(os.getenv("BOARD_DAYS", os.getenv("SCAN_DAYS", "260")))
        min_dv = float(os.getenv("MIN_DOLLAR_VOLUME", 10_000_000))
        min_cap = float(os.getenv("MIN_MARKET_CAP", 25_000_000_000))
        etfs = set(universe.ETFS)
        eligible = []
        # Use only bars already persisted by the board scan; never trigger a universe-wide download here.
        for sym in symbols:
            bars = _disk_read(sym, days)
            dv20 = None
            if bars is not None and not bars.empty and "Volume" in bars.columns:
                day = bars.index.tz_convert("America/New_York").normalize()
                dv = (bars["Volume"] * bars["Close"]).groupby(day).sum()
                dv = dv[dv > 0].tail(20)
                if len(dv) >= 5:
                    dv20 = float(dv.mean())
            # Match board.quality_skip: insufficient volume history is unknown and fails open.
            if dv20 is not None and dv20 < min_dv:
                continue
            mc = universe.market_cap(sym)
            if sym.upper() not in etfs and mc is not None and mc < min_cap:
                continue
            eligible.append(sym)
        if not eligible:
            return empty
        snaps = _alpaca_snapshots(eligible, feed="sip")
        if not snaps:
            return empty
        et_now = pd.Timestamp.now(tz="America/New_York")
        today = et_now.date()
        cutoff = et_now.normalize() + pd.Timedelta(hours=9, minutes=30)
        movers = []
        for sym, snap in snaps.items():
            trade = snap.get("latestTrade") or {}
            prev = (snap.get("prevDailyBar") or {}).get("c")
            px, stamp = trade.get("p"), trade.get("t")
            if not px or not prev or not stamp:
                continue
            ts = pd.Timestamp(stamp)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            ts_et = ts.tz_convert("America/New_York")
            if ts_et.date() != today or ts_et >= cutoff:
                continue
            change = round((float(px) / float(prev) - 1) * 100, 2)
            movers.append(dict(symbol=sym, price=round(float(px), 2), change_pct=change))
        if not movers:
            return empty
        movers.sort(key=lambda x: x["change_pct"])
        result = dict(gainers=sorted((x for x in movers if x["change_pct"] > 0),
                                     key=lambda x: x["change_pct"], reverse=True)[:5],
                      losers=[x for x in movers if x["change_pct"] < 0][:5],
                      as_of=pd.Timestamp.now(tz="UTC").isoformat())
        if result["gainers"] or result["losers"]:
            _PREMARKET, _PREMARKET_AT = result, now
            return result
        return empty
    except Exception as e:
        logging.getLogger(__name__).warning("premarket movers unavailable: %s", e)
        return empty


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
    if not symbols:
        return {}
    feed = os.getenv("ALPACA_FEED", "sip").lower()
    end = pd.Timestamp.now(tz="UTC")
    if feed == "sip":   # free plan: SIP data is available after a 15-minute delay
        end -= pd.Timedelta(minutes=16)
    start = end - pd.Timedelta(days=days)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}

    # Disk is below scanner.py's in-memory cache. Only Alpaca data reaches this path.
    cached = {sym: df for sym in symbols if (df := _disk_read(sym, days)) is not None}
    latest = _alpaca_latest_starts(list(cached), end, feed, headers) if cached else {}
    out: dict[str, pd.DataFrame] = {}
    fetch_symbols = []
    for sym in symbols:
        df = cached.get(sym)
        if df is None:
            fetch_symbols.append(sym)
            continue
        disk_last = df.index[-1]
        alpaca_last = latest.get(sym)
        if alpaca_last is not None and alpaca_last > disk_last:
            fetch_symbols.append(sym)
        else:
            # Equal means current. A failed check (None) or older provider result
            # keeps the usable disk copy; the next chart load retries freshness.
            out[sym] = df

    if not fetch_symbols:
        return out

    params = dict(symbols=",".join(fetch_symbols), timeframe="30Min", start=start.strftime(fmt), end=end.strftime(fmt),
                  limit=10000, adjustment="all", feed=feed)
    rows: dict[str, list] = {s: [] for s in fetch_symbols}
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
    for sym, rs in rows.items():
        if not rs:
            out[sym] = pd.DataFrame(columns=COLS)
            continue
        df = pd.DataFrame(rs)
        df.index = pd.to_datetime(df["t"], utc=True)
        df = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"}).reindex(columns=COLS).sort_index()
        out[sym] = df
        _disk_write(sym, days, df)
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
