"""Forward log for the GEX wall Lab test (Patch 149, spec gex-wall-lab-test-spec.md).
Watchlist only. One row per wall touch at a 4H close; outcomes settle as candles arrive.
Storage: DATA_DIR/gex/wall_log.json (disk, same pattern as data.py / Patch 139).
No alert fires from this module. No change to the scanner's alerts, the record, or the paper trader."""
import json
import os
from datetime import date
from pathlib import Path

import pandas as pd

import engine
import gamma
import patterns

LOG_PATH = Path(os.getenv("DATA_DIR", "data")) / "gex" / "wall_log.json"
WITHIN = 0.01


def _load() -> list:
    try:
        rows = json.loads(LOG_PATH.read_text())
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def _save(rows: list) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text(json.dumps(rows, indent=1))


def _metrics(rs: list) -> dict:
    rets = [r["return_pct"] for r in rs if r.get("return_pct") is not None]
    n = len(rets)
    avg = sum(rets) / n if n else None
    wins = [x for x in rets if x > 0]
    losses = [x for x in rets if x <= 0]
    pf = (sum(wins) / -sum(losses)) if losses and sum(losses) != 0 else (None if not wins else float("inf"))
    f50 = sum(rets[:50]) / len(rets[:50]) if n >= 50 else None
    n50 = (sum(rets[50:100]) / len(rets[50:100]) if n >= 100 else
           sum(rets[50:]) / len(rets[50:]) if n > 50 else None)
    return dict(n=n, avg=avg, pf=pf, first50_avg=f50, next50_avg=n50,
                passes=bool(n >= 100 and avg is not None and avg > 0 and f50 is not None and f50 > 0
                            and n50 is not None and n50 > 0))


def stats() -> dict:
    """Gate numbers per trigger type and combined. Overlapping labels count once in combined."""
    rows = sorted((r for r in _load() if r.get("outcome") in ("follow-through", "snap-back", "stalled")),
                  key=lambda r: (r.get("close_time") or "", r.get("ticker") or "", r.get("direction") or 0))
    out = {}
    for trig in ("T1", "T2", "T3-long", "T3-short", "T4-long", "T4-short"):
        out[trig] = _metrics([r for r in rows if r.get("key") == trig])
    combined, seen = [], set()
    for r in rows:
        identity = (r.get("ticker"), r.get("close_time"), r.get("direction"))
        if identity not in seen:
            seen.add(identity)
            combined.append(r)
    out["combined"] = _metrics(combined)
    return out


def check_close(sym: str, c4: pd.DataFrame, res: dict, s) -> list:
    """Check the latest 4H close for GEX wall touches (T1-T4) and append open rows.
    Settings are passed by the hook; this module does not import scanner. Never raises."""
    close_s = res["close"]
    if len(close_s) < 2 or c4.empty:
        return []
    i0 = len(close_s) - 1
    entry = float(close_s.iloc[i0])
    # 4H candles are labelled by their start; the afternoon candle closes at 4:00 PM.
    close_time = engine.candle_end(c4.index[-1])
    atr_v = float(res["atr"].iloc[-1])
    if not atr_v or pd.isna(atr_v) or atr_v <= 0:
        return []
    try:
        w = gamma.walls(sym, entry, fetch=True)
        sh = gamma.shelves(sym, entry, fetch=True)
    except Exception:
        return []
    if not w:
        return []
    sp = max(sh["shelves"], key=lambda r: abs(r["gex"]))["strike"] if sh and sh.get("shelves") else None
    flip = w.get("flip")
    expiry = w.get("expiry") or (sh.get("expiry") if sh else None)
    days_out = (date.fromisoformat(expiry) - date.today()).days if expiry else None

    cands = []
    if w.get("call_wall") and entry > w["call_wall"]:
        cands.append(("T1", 1, float(w["call_wall"])))
    if w.get("put_wall") and entry < w["put_wall"]:
        cands.append(("T2", -1, float(w["put_wall"])))
    if sp and abs(entry - sp) / sp <= WITHIN:
        cands.extend([("T3", 1, float(sp)), ("T3", -1, float(sp))])
    if flip and abs(entry - flip) / flip <= WITHIN:
        cands.extend([("T4", 1, float(flip)), ("T4", -1, float(flip))])
    if not cands:
        return []

    rows = _load()
    seen = {(r.get("ticker"), r.get("close_time"), r.get("trigger"), r.get("direction")) for r in rows}
    appended = []
    for trig, d, edge in cands:
        key = (f"{trig}-long" if trig in ("T3", "T4") and d == 1 else
               f"{trig}-short" if trig in ("T3", "T4") else trig)
        identity = (sym, close_time.isoformat(), trig, d)
        if identity in seen:
            continue
        shelf = engine.find_shelf(res["hubs"], {"start_i": i0}, d, entry, atr_v, s)
        if not shelf:
            continue
        target = float(shelf["level"])
        stop = entry - d * atr_v
        overlap = [t for (t, _, _) in cands if t != trig]
        row = dict(ticker=sym, date=close_time.date().isoformat(),
                   close_time=close_time.isoformat(), trigger=trig, direction=d, key=key,
                   wall_strike=edge, close=entry, entry=entry, target=target, stop=round(stop, 2),
                   gex_expiry=expiry, days_to_expiry=days_out,
                   overlap=bool(overlap), overlap_types=overlap,
                   outcome="pending", return_pct=None, closed_date=None,
                   entry_time=c4.index[-1].isoformat())
        rows.append(row)
        appended.append(row)
        seen.add(identity)
    if appended:
        _save(rows)
    return appended


def settle(sym: str, c4: pd.DataFrame, res: dict, s) -> list:
    """Settle open rows with patterns._outcome unchanged; return rows closed on this sweep."""
    rows = _load()
    close_s = res["close"]
    changed = []
    for r in rows:
        if r.get("ticker") != sym or r.get("outcome") != "pending":
            continue
        try:
            i0 = close_s.index.get_loc(pd.Timestamp(r["entry_time"]))
        except Exception:
            continue
        o = patterns._outcome(close_s, i0, r["direction"], r["wall_strike"], r["target"], s)
        if o["outcome"] == "pending":
            continue
        if o.get("chase"):
            r["outcome"], r["return_pct"] = "excluded", None
        else:
            r["outcome"] = o["outcome"]
            r["return_pct"] = None if o["ret"] is None else round(o["ret"], 2)
        r["closed_date"] = close_s.index[o["oi"]].date().isoformat() if o.get("oi") is not None else None
        changed.append(r)
    if changed:
        _save(rows)
    return changed
