"""Nov 28 (owner): options flow — ADMIN-ONLY layer for now (display rights for exchange data go to the lawyer first).

Every minute in market hours, for the shared watchlist (first FLOW_MAX tickers):
  1. Alpaca option snapshots for contracts within ±7% of price and the next ~30 days -> the contract list + quotes
  2. Alpaca option trades for those contracts since the last poll
  3. Prints in the same contract and the same second on 2+ exchanges = a SWEEP; one big print = a BLOCK
     Kept when the dollar size (price × size × 100) clears the ticker's floor ($250K; $1M for index ETFs/mega caps)
  4. Side from the contract's quote at the poll: at/near ask = bought, at/near bid = sold, else mid
     Read: calls bought / puts sold = Bullish, puts bought / calls sold = Bearish, mid = Neutral (a read, not a signal:
     a bought put can be a hedge)

Stored one JSON line per event in DATA_DIR/flow/{SYM}_{YYYY-MM-DD}.jsonl, kept 10 days.
Display only: never a grade, a setup, or an alert input.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger("flow")
NY = ZoneInfo("America/New_York")
DIR = Path(os.getenv("DATA_DIR", "data")) / "flow"
FLOW_MAX = int(os.getenv("FLOW_MAX", "12"))
BIG = {"SPY", "QQQ", "IWM", "DIA", "AAPL", "NVDA", "TSLA", "MSFT", "AMZN", "META", "GOOGL", "GOOG", "AMD"}
_LAST: dict[str, str] = {}          # symbol -> ISO start for the next trades read
_LOCK = threading.Lock()
DIAG: dict = {}


def floor_usd(sym: str) -> float:
    return 1_000_000 if sym.upper() in BIG else 250_000


def _h() -> dict:
    return {"APCA-API-KEY-ID": os.getenv("ALPACA_API_KEY", ""), "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET_KEY", "")}


def _occ(sym: str):
    import gamma
    return gamma._occ(sym)          # (expiry date, is_call, strike)


def _wall(ts: str) -> int:
    t = datetime.fromisoformat((ts or "")[:19]).replace(tzinfo=timezone.utc).astimezone(NY)
    return int(datetime(t.year, t.month, t.day, t.hour, t.minute, t.second, tzinfo=timezone.utc).timestamp())


def _contracts(sym: str, spot: float) -> dict[str, tuple[float, float]]:
    """{OCC: (bid, ask)} near the money, next ~30 days."""
    params = dict(feed=os.getenv("ALPACA_OPTIONS_FEED", "indicative"), limit=1000,
                  expiration_date_gte=date.today().isoformat(), expiration_date_lte=(date.today() + timedelta(days=30)).isoformat(),
                  strike_price_gte=f"{spot * .93:.2f}", strike_price_lte=f"{spot * 1.07:.2f}")
    out, token = {}, None
    for _ in range(6):
        if token:
            params["page_token"] = token
        r = requests.get(f"https://data.alpaca.markets/v1beta1/options/snapshots/{sym.replace('.', '')}", headers=_h(), params=params, timeout=20)
        r.raise_for_status()
        j = r.json()
        for occ, sn in (j.get("snapshots") or {}).items():
            q = sn.get("latestQuote") or {}
            out[occ] = (float(q.get("bp") or 0), float(q.get("ap") or 0))
        token = j.get("next_page_token")
        if not token:
            break
    return out


def _trades(occs: list[str], start: str, end: str) -> dict[str, list]:
    out: dict[str, list] = {}
    for i in range(0, len(occs), 100):
        params = dict(symbols=",".join(occs[i:i + 100]), start=start, end=end, limit=10000, sort="asc")
        token = None
        for _ in range(5):
            if token:
                params["page_token"] = token
            r = requests.get("https://data.alpaca.markets/v1beta1/options/trades", headers=_h(), params=params, timeout=20)
            r.raise_for_status()
            j = r.json()
            for occ, ts in (j.get("trades") or {}).items():
                out.setdefault(occ, []).extend(ts)
            token = j.get("next_page_token")
            if not token:
                break
    return out


def _side(px: float, bid: float, ask: float) -> str:
    if bid <= 0 or ask <= 0 or ask < bid:
        return "mid"
    sp = ask - bid
    if px >= ask - .25 * sp:
        return "ask"
    if px <= bid + .25 * sp:
        return "bid"
    return "mid"


def _read(is_call: bool, side: str) -> str:
    if side == "mid":
        return "Neutral"
    bought = side == "ask"
    return "Bullish" if (is_call == bought) else "Bearish"


def events_from(sym: str, trades: dict[str, list], quotes: dict) -> list[dict]:
    """Group each contract's prints by second: 2+ exchanges in one second = sweep, else each print may be a block."""
    out = []
    fl = floor_usd(sym)
    for occ, ts in trades.items():
        p = _occ(occ)
        if not p:
            continue
        exp, is_call, k = p
        by: dict[str, list] = {}
        for t in ts:
            by.setdefault((t.get("t") or "")[:19], []).append(t)
        for sec, grp in by.items():
            usd = sum(float(t["p"]) * float(t["s"]) * 100 for t in grp)
            exch = {t.get("x") for t in grp}
            if len(exch) >= 2 and usd >= fl:
                size = sum(float(t["s"]) for t in grp)
                px = usd / (size * 100)
                kind, rows = "Sweep", [(px, size, usd)]
            else:
                rows = [(float(t["p"]), float(t["s"]), float(t["p"]) * float(t["s"]) * 100) for t in grp]
                rows = [r for r in rows if r[2] >= fl]
                kind = "Block"
            for px, size, u in rows:
                bid, ask = quotes.get(occ, (0, 0))
                side = _side(px, bid, ask)
                out.append(dict(t=_wall(sec), occ=occ, exp=exp.isoformat(), right="C" if is_call else "P", strike=k,
                                kind=kind, usd=round(u), size=int(size), price=round(px, 2), side=side, read=_read(is_call, side)))
    return out


def _poll_one(sym: str, spot: float, now_utc: datetime) -> int:
    start = _LAST.get(sym) or (now_utc - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    quotes = _contracts(sym, spot)
    if not quotes:
        DIAG[sym] = dict(at=end, contracts=0)
        return 0
    tr = _trades(list(quotes), start, end)
    ev = events_from(sym, tr, quotes)
    _LAST[sym] = end
    if ev:
        DIR.mkdir(parents=True, exist_ok=True)
        day = datetime.now(NY).date().isoformat()
        with _LOCK, open(DIR / f"{sym}_{day}.jsonl", "a", encoding="utf-8") as fh:
            fh.write("\n".join(json.dumps(e, separators=(",", ":")) for e in ev) + "\n")
    DIAG[sym] = dict(at=end, contracts=len(quotes), prints=sum(len(v) for v in tr.values()), events=len(ev))
    return len(ev)


def run() -> dict:
    """Every minute, 9:31–16:00 ET on trading days. Never raises."""
    now = datetime.now(NY)
    m = now.hour * 60 + now.minute
    if now.weekday() >= 5 or not (9 * 60 + 31 <= m <= 16 * 60 + 1):
        _LAST.clear()
        return dict(skipped="market closed")
    try:
        import data
        import db
        syms = [r["symbol"] for r in db.list_tickers()][:FLOW_MAX]
        spots = data.latest_prices(syms)
    except Exception as exc:
        return dict(error=str(exc)[:200])
    now_utc = datetime.now(timezone.utc) - timedelta(seconds=20)       # let the last seconds' prints land
    def one(s):
        it = spots.get(s)
        px = it.get("price") if isinstance(it, dict) else it
        try:
            return _poll_one(s, float(px), now_utc) if px else 0
        except Exception as exc:
            DIAG[s] = dict(error=str(exc)[:160])
            return 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        n = sum(pool.map(one, syms))
    if now.hour == 16:
        prune()
    return dict(events=n, symbols=len(syms))


def prune(days: int = 10) -> None:
    cut = (datetime.now(NY).date() - timedelta(days=days)).isoformat()
    try:
        for p in DIR.glob("*.jsonl"):
            if p.stem.rsplit("_", 1)[-1] < cut:
                p.unlink()
    except Exception:
        pass


def events(symbol: str, days: int = 5) -> list[dict]:
    sym = symbol.upper()
    today = datetime.now(NY).date()
    out = []
    for back in range(days + 3, -1, -1):
        p = DIR / f"{sym}_{(today - timedelta(days=back)).isoformat()}.jsonl"
        if p.exists():
            for ln in p.read_text(encoding="utf-8").splitlines():
                try:
                    out.append(json.loads(ln))
                except Exception:
                    pass
    out.sort(key=lambda e: e["t"])
    return out
