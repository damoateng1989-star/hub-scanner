"""Nov 28 (owner, option 3): largest-GEX-node touches.

Two parts, both CONTEXT ONLY (GEX never feeds a grade, a setup or a member alert):
  1. A hidden forward log: every time a watchlist ticker reaches its largest net-GEX strike (nearest expiry that has
     gamma), record it and, 30 and 60 minutes later, what price did: pinned, rejected (back the way it came) or
     broke through. stats() feeds the admin GEX check page.
  2. A silent heads-up to ADMINS ONLY while the log collects evidence.

Runs right after each 5-minute watchlist snapshot (market hours), so it reads the snapshot just written.
"""
from __future__ import annotations

import html as _h
import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("node_watch")
NY = ZoneInfo("America/New_York")
LOG_PATH = Path(os.getenv("DATA_DIR", "data")) / "node_log.json"
_LOCK = threading.Lock()
TOUCH = 0.0015          # within 0.15% of the strike counts as "at" it (or crossing it between two checks)
DECIDE = 0.004          # 60 min later: more than 0.4% past it = broke through, 0.4% back = rejected, else pinned
_PREV: dict[str, float] = {}     # last spot seen per symbol (one process; a restart just skips one comparison)


def _load() -> list[dict]:
    try:
        return json.loads(LOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(rows: list[dict]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = LOG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows[-5000:], separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, LOG_PATH)


def _money(x: float) -> str:
    return f"${x:,.2f}"


def _fmt(v: float) -> str:
    a = abs(v)
    s = f"{a / 1e9:.2f}B" if a >= 1e9 else f"{a / 1e6:.2f}M" if a >= 1e6 else f"{a / 1e3:.1f}K" if a >= 1e3 else f"{a:.0f}"
    return ("-" if v < 0 else "+") + "$" + s


def _largest(sym: str) -> tuple[float, float] | None:
    """(strike, net GEX) of the biggest node in today's latest snapshot, nearest expiry that has gamma."""
    import gamma
    day = datetime.now(NY).date().isoformat()
    times = gamma._snapshot_times(sym, day)
    if not times:
        return None
    snap = gamma._read_snapshot(sym, times[-1], day)
    if not snap:
        return None
    pts = gamma._glow_pick(snap.get("rows") or [], float(snap.get("spot") or 0))
    if not pts:
        return None
    k, v = max(pts, key=lambda p: abs(p[1]))
    return float(k), float(v)


def _settle(rows: list[dict], spots: dict, now: datetime) -> None:
    for r in rows:
        if r.get("outcome"):
            continue
        px = spots.get(r["symbol"])
        if not px:
            continue
        age = (now - datetime.fromisoformat(r["at"])).total_seconds() / 60
        k, side = r["strike"], r["side"]           # side: +1 came from above, -1 came from below
        rel = (px - k) / k * side                   # > 0: back on the side it came from
        r["best_back"] = max(r.get("best_back", 0.0), rel)
        r["best_past"] = max(r.get("best_past", 0.0), -rel)
        if age >= 30 and "px30" not in r:
            r["px30"] = round(px, 4)
        if age >= 60:
            r["px60"] = round(px, 4)
            r["outcome"] = "broke through" if rel < -DECIDE else "rejected" if rel > DECIDE else "pinned"


def run(symbols: list[str], spots: dict) -> dict:
    """Called after each watchlist snapshot. Never raises."""
    now = datetime.now(NY)
    flat = {}
    for s in symbols:
        it = spots.get(s) or spots.get(s.upper())
        p = it.get("price") if isinstance(it, dict) else it
        try:
            if p and float(p) > 0:
                flat[s.upper()] = float(p)
        except Exception:
            pass
    touched = 0
    try:
        with _LOCK:
            rows = _load()
            _settle(rows, flat, now)
            today = now.date().isoformat()
            seen = {(r["symbol"], r["strike"], r["at"][:10]) for r in rows}
            new = []
            for sym, px in flat.items():
                prev = _PREV.get(sym)
                _PREV[sym] = px
                node = _largest(sym)
                if not node:
                    continue
                k, v = node
                near = abs(px - k) / k <= TOUCH
                crossed = prev is not None and (prev - k) * (px - k) <= 0
                if not (near or crossed) or (sym, k, today) in seen:
                    continue
                side = 1 if (prev if prev is not None else px) >= k else -1
                row = dict(symbol=sym, at=now.isoformat(timespec="seconds"), strike=k, gex=round(v, 1),
                           sign="+" if v > 0 else "-", spot=round(px, 4), side=side)
                rows.append(row); new.append(row); seen.add((sym, k, today))
            _save(rows)
        for row in new:
            touched += 1
            _heads_up(row)
    except Exception as exc:
        log.warning("node watch skipped: %s", exc)
    return dict(touched=touched)


def _heads_up(r: dict) -> None:
    import db
    import notify
    sym, k, v = r["symbol"], r["strike"], r["gex"]
    pos = v > 0
    t = datetime.fromisoformat(r["at"]).strftime("%-I:%M %p")
    what = "Positive gamma here tends to slow price and pin it." if pos else "Negative gamma here: moves can speed up through it."
    came = "from above" if r["side"] == 1 else "from below"
    msg = (f"🧲 {sym} is at its largest GEX node {_money(k)} · {t} ET\n"
           f"{_fmt(v)} net gamma, nearest expiry. Price {_money(r['spot'])}, arriving {came}. {what}\n"
           f"Context only, admin test: logged to see what price does next.")
    card = (f"🧲 <b>{_h.escape(sym)} is at its largest GEX node {_money(k)}</b> · <i>{t} ET</i>\n"
            f"{_fmt(v)} net gamma, nearest expiry. Price {_money(r['spot'])}, arriving {came}.\n"
            f"<blockquote>{what}</blockquote><i>Context only · admin test · being logged</i>")
    for u in db.all_users():
        if not u["is_admin"]:
            continue
        try:
            notify.user_alert(u, msg, f"/t/{sym}", f"{sym}-gexnode", tg_html=card, silent=True)
        except Exception as exc:
            log.warning("GEX node heads-up failed for user %s: %s", u.get("id"), exc)


def stats() -> dict:
    rows = _load()
    done = [r for r in rows if r.get("outcome")]
    out = dict(rows=len(rows), open=len(rows) - len(done), path=str(LOG_PATH))
    for sign in ("+", "-"):
        d = [r for r in done if r["sign"] == sign]
        out["positive_gamma" if sign == "+" else "negative_gamma"] = dict(
            n=len(d), **{o: sum(r["outcome"] == o for r in d) for o in ("pinned", "rejected", "broke through")})
    out["latest"] = rows[-10:]
    return out
