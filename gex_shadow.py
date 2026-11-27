"""Patch 227: precise GEX, shadow mode. One row per (symbol, day) comparing the current walls / flip / net VEX
with the precise (IV-surface) version from gamma.precise_shadow.
Storage: DATA_DIR/gex/shadow_log.json (append-only, same pattern as gex_log.py).
No alert fires from this module. Nothing displayed reads it. It never fetches a chain and never raises."""
import json
import logging
import os
import statistics
import threading
from collections import Counter
from datetime import date
from pathlib import Path

log = logging.getLogger("gex_shadow")
LOG_PATH = Path(os.getenv("DATA_DIR", "data")) / "gex" / "shadow_log.json"
_LOCK = threading.Lock()


def _load() -> list:
    try:
        rows = json.loads(LOG_PATH.read_text())
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def _save(rows: list) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = LOG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, separators=(",", ":")))
    os.replace(tmp, LOG_PATH)


def record(sym: str, spot) -> bool:
    """Write today's row for sym if it isn't there yet. Cache-only: if the chain isn't cached, writes nothing."""
    try:
        import gamma
        sym = (sym or "").upper()
        day = date.today().isoformat()
        with _LOCK:
            rows = _load()
            if any(r.get("day") == day and r.get("symbol") == sym for r in rows):
                return False
        res = gamma.precise_shadow(sym, spot)
        if not res:
            return False
        row = {"day": day, "symbol": sym, "spot": round(float(spot), 2), "expiry": res.get("expiry"),
               "chain_as_of": gamma.chain_as_of(sym), "surface_points": res.get("surface_points", 0),
               "cur": res.get("cur"), "new": res.get("new"),
               "vex_cur": res.get("vex_cur"), "vex_new": res.get("vex_new"),
               "vex_fitted_expiries": res.get("vex_fitted_expiries")}
        if res.get("new") is None:
            row["reason"] = res.get("reason") or "unknown"
        with _LOCK:
            rows = _load()
            if any(r.get("day") == day and r.get("symbol") == sym for r in rows):
                return False
            rows.append(row)
            _save(rows)
        return True
    except Exception as e:
        log.warning("gex_shadow record failed for %s: %s", sym, e)
        return False


def stats() -> dict:
    """The read for the Lab / admin check (spec §5)."""
    try:
        rows = _load()
        ok = [r for r in rows if r.get("new") and r.get("cur")]
        def pct(xs):
            return round(100 * sum(xs) / len(xs), 1) if xs else None
        cw = [r["new"].get("call_wall") == r["cur"].get("call_wall") for r in ok if r["cur"].get("call_wall") is not None]
        pw = [r["new"].get("put_wall") == r["cur"].get("put_wall") for r in ok if r["cur"].get("put_wall") is not None]
        dflip = [abs(r["new"]["flip"] - r["cur"]["flip"]) / r["spot"] * 100 for r in ok
                 if r["new"].get("flip") is not None and r["cur"].get("flip") is not None and r.get("spot")]
        dvex = [abs(r["vex_new"] - r["vex_cur"]) / abs(r["vex_cur"]) * 100 for r in rows
                if r.get("vex_new") is not None and r.get("vex_cur")]
        return dict(rows=len(rows), sessions=len({r.get("day") for r in rows}), symbols=len({r.get("symbol") for r in rows}),
                    call_wall_same_pct=pct(cw), put_wall_same_pct=pct(pw),
                    flip_compared=len(dflip),
                    flip_median_abs_delta_pct_of_spot=round(statistics.median(dflip), 3) if dflip else None,
                    flip_moved_over_half_pct=pct([d > 0.5 for d in dflip]),
                    flip_one_side_only=sum(1 for r in ok if (r["new"].get("flip") is None) != (r["cur"].get("flip") is None)),
                    vex_median_abs_delta_pct=round(statistics.median(dvex), 2) if dvex else None,
                    unavailable_pct=pct([not r.get("new") for r in rows]),
                    unavailable_by_reason=dict(Counter(r.get("reason") for r in rows if not r.get("new"))))
    except Exception as e:
        return {"error": str(e)[:200]}
