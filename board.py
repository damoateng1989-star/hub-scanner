"""Hub Board (market-wide scan), public track record, and chart data for ticker pages."""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import threading
import time

import pandas as pd

import data
import db
import engine
import gamma
import patterns
import scanner
import universe

log = logging.getLogger("board")
_lock = threading.Lock()
BOARD_DAYS = int(os.getenv("BOARD_DAYS", os.getenv("SCAN_DAYS", 260)))
REGIME_INDEX = os.getenv("REGIME_INDEX", "SPY").strip().upper()
REGIME_DAYS = int(os.getenv("REGIME_DAYS", 50))
MIN_DOLLAR_VOLUME = float(os.getenv("MIN_DOLLAR_VOLUME", 10_000_000))   # 20-day avg daily $ volume
MIN_MARKET_CAP = float(os.getenv("MIN_MARKET_CAP", 25_000_000_000))      # market cap, dollars


def record_start() -> str:
    """The track record only counts breakouts after the board first ran (no hindsight)."""
    v = db.get_setting("record_start")
    if not v:
        v = pd.Timestamp.now(tz=engine.NY).isoformat()
        db.set_setting("record_start", v)
    return v


RULES = "rules"   # the live record of today's rules: A retests (hubs, double bottoms) and gap fades, longs only


def rules_start() -> str:
    """The live record of today's rules counts only entries after it started (no hindsight). The earlier
    breakout-close log is archived, not deleted."""
    v = db.get_setting("rules_record_start")
    if not v:
        v = pd.Timestamp.now(tz=engine.NY).isoformat()
        db.set_setting("rules_record_start", v)
    return v


FRESH_A: list[dict] = []     # A setups confirmed on the latest candle during this sweep (for the "every A setup" alert)


def _record_rules(sym: str, c4: pd.DataFrame, res: dict, pa: dict | None, start: pd.Timestamp):
    """Log the trades today's rules would take, the way the alerts give them: hub and double-bottom retests that grade
    A (entry = the retest close; stop 1 ATR for hubs, the far side for doubles; aim the target; 10 candles), and
    gap fades (their own daily plan). Shorts are watch only, so they are never logged."""
    import lab_exits
    idx, s = res["close"].index, scanner.SETTINGS
    close = res["close"].to_numpy(float)
    low, high, atr = c4["Low"].to_numpy(float), c4["High"].to_numpy(float), res["atr"].to_numpy(float)

    def put(t, kind, label, grade, j, entry, stop):
        if idx[j] <= start:
            return
        # the same close-based exits as lab_exits._exit (target, stop, or time), keeping which candle it happened on
        outcome, ret, oi = "pending", None, None
        for k in range(j + 1, min(j + 1 + s.horizon, len(close))):
            if close[k] >= t["target"]:
                outcome, ret, oi = "follow-through", (t["target"] / entry - 1) * 100, k
                break
            if close[k] <= stop:
                outcome, ret, oi = "snap-back", (close[k] / entry - 1) * 100, k
                break
        else:
            if j + s.horizon < len(close):
                oi = j + s.horizon
                outcome, ret = "stalled", (close[oi] / entry - 1) * 100
        h = t["hub"]
        if j == len(close) - 1 and grade == "A":              # confirmed on the candle that just closed
            FRESH_A.append(dict(symbol=sym, kind=kind, setup=label, entry=round(entry, 2), stop=round(stop, 2),
                                target=round(t["target"], 2), entry_time=idx[j].isoformat()))
        db.upsert_signal(id=f"{RULES}|{kind}|{sym}|{idx[t['i0']].isoformat()}", pattern=RULES, setup=label, grade=grade,
                         symbol=sym, direction=1, hub_low=round(h["low"], 2), hub_high=round(h["high"], 2),
                         hub_sessions=h.get("sessions"), breakout_time=idx[t["i0"]].isoformat(), entry_time=idx[j].isoformat(),
                         entry=round(entry, 2), target=round(t["target"], 2), stop=round(stop, 2),
                         target_kind=t.get("target_kind") or ("shelf" if t.get("shelf") else "atr"),
                         outcome=outcome, outcome_time=idx[oi].isoformat() if oi is not None else None,
                         ret_pct=None if ret is None else round(ret, 2), chase=0, rvol=engine.rvol(c4, j))

    for t in res["trades"]:                                   # hubs
        if t["direction"] != 1:
            continue
        rt = lab_exits._retest_entry(close, low, high, atr, t["i0"], 1, t["edge"])
        if rt:
            j, entry = rt
            g = engine.retest_grade(t, entry, float(atr[j]))
            if g.get("grade"):
                put(t, "hub", "Hub retest", g["grade"], j, entry, g["stop"])
    for t in (pa or {}).get("trades", []):
        if t["kind"] == "double_bottom":
            rt = lab_exits._retest_entry(close, low, high, atr, t["i0"], 1, t["edge"])
            if rt:
                j, entry = rt
                g = engine.double_retest_grade(t, entry, engine.candle_end(idx[j].tz_convert(engine.NY)).hour == 16)
                if g.get("grade"):
                    put(t, "double_bottom", "Double bottom retest", g["grade"], j, entry, g["stop"])
        elif t["kind"] == "gapfade" and idx[t["i0"]] > start:
            h = t["hub"]
            if t["i0"] == len(close) - 1 and t.get("stop"):
                FRESH_A.append(dict(symbol=sym, kind="gapfade", setup="Gap fade & rip", entry=round(t["entry"], 2),
                                    stop=round(t["stop"], 2), target=round(t["target"], 2), entry_time=idx[t["i0"]].isoformat()))
            db.upsert_signal(id=f"{RULES}|gapfade|{sym}|{idx[t['i0']].isoformat()}", pattern=RULES, setup="Gap fade & rip", grade=None,
                             symbol=sym, direction=1, hub_low=round(h["low"], 2), hub_high=round(h["high"], 2),
                             hub_sessions=h.get("sessions"), breakout_time=idx[t["i0"]].isoformat(), entry_time=idx[t["i0"]].isoformat(),
                             entry=round(t["entry"], 2), target=round(t["target"], 2), stop=round(t["stop"], 2) if t.get("stop") else None,
                             target_kind="2R", outcome=t["outcome"],
                             outcome_time=idx[t["oi"]].isoformat() if t.get("oi") is not None else None,
                             ret_pct=None if t["ret"] is None else round(t["ret"], 2), chase=0, rvol=None)


def _board_status(c4: pd.DataFrame, res: dict) -> dict:
    st = engine.current_status(c4, res, scanner.SETTINGS)
    st.pop("spark", None)
    p = st["last_close"]
    if st.get("hub") and st["state"] in ("forming", "watching"):
        h = st["hub"]
        width = max(h["high"] - h["low"], 1e-9)
        st["pos"] = round((p - h["low"]) / width, 3)            # 0 = at hub low, 1 = at hub high
        st["to_high_pct"] = round((h["high"] / p - 1) * 100, 2)
        st["to_low_pct"] = round((h["low"] / p - 1) * 100, 2)
        st["near_edge"] = "up" if st["pos"] >= 0.75 else "down" if st["pos"] <= 0.25 else None
    if st.get("target") is not None:
        st["to_target_pct"] = round((st["target"] / p - 1) * 100, 2)
    return st


# ---------------------------------------------------------------- hub-rule changes vs the record
# The record is rebuilt from price history on every sweep. When the hub rules change (MIN_SESSIONS 5 -> 7, Patch 70: 4-candle floor),
# the new rules would "find" different past breakouts and quietly rewrite history. So: past signals keep
# their old outcomes (resolved with the rules they were logged under), and only breakouts after the change
# are logged under the new rules.
_RULE_KEYS = ("min_sessions", "min_candles", "k", "horizon", "target_atr", "shelf_min_atr", "vol_mult", "vol_window", "min_room_pct")
_OLD_DEFAULTS = dict(min_sessions=5, vol_mult=0.0, min_room_pct=0.0)     # the rules the record was built under before tracking began


def _rules_now() -> dict:
    return {k: getattr(scanner.SETTINGS, k) for k in _RULE_KEYS}


def _rules_check():
    cur = _rules_now()
    try:
        stored = json.loads(db.get_setting("record_rules") or "null")
    except Exception:
        stored = None
    had_candles = bool(stored) and "min_candles" in stored
    if stored is None:
        # first run with this tracking: the stored record was built under the previous default (5 sessions)
        stored = dict(cur, **_OLD_DEFAULTS)
    else:
        stored = dict(cur, **{k: v for k, v in _OLD_DEFAULTS.items() if k not in stored}, **stored)   # keys added later: old values
    if not had_candles:      # before Patch 70 the hub window was always 2 candles per session
        stored["min_candles"] = 2 * int(stored["min_sessions"])
    if stored != cur:
        db.set_setting("record_rules_prev", json.dumps(stored))
        try:      # from the last sweep under the old rules, so breakouts since then get logged under the new ones
            last = json.loads(db.get_setting("lab") or "{}").get("updated")
        except Exception:
            last = None
        db.set_setting("rules_changed_at", pd.Timestamp(last or pd.Timestamp.now(tz="UTC")).tz_convert("UTC").isoformat())
        log.info("hub rules changed %s -> %s: past signals keep their old outcomes", stored, cur)
    db.set_setting("record_rules", json.dumps(cur))


def _rules_changed_at():
    v = db.get_setting("rules_changed_at")
    return pd.Timestamp(v) if v else None


def _legacy_settings():
    try:
        prev = json.loads(db.get_setting("record_rules_prev") or "null")
        if prev and "min_room_pct" not in prev:
            prev["min_room_pct"] = 0.0                                     # before the room rule: every breakout counted
        if prev and "min_candles" not in prev and "min_sessions" in prev:
            prev["min_candles"] = 2 * int(prev["min_sessions"])      # pre-Patch-70 rules: window = 2 x sessions
        return dataclasses.replace(scanner.SETTINGS, **{k: v for k, v in prev.items() if k in _RULE_KEYS}) if prev else None
    except Exception:
        return None


def _resolve_orphans(sym: str, c4: pd.DataFrame, start: pd.Timestamp, produced: set):
    """Pending signals the current rules no longer produce (logged under the old rules, or a name the
    universe filter now skips): finish them with the rules they were logged under. Never logs new ones."""
    pend = {i for i in db.pending_signal_ids(sym) - produced if not i.startswith("T|")}
    if not pend:
        return
    legacy = _legacy_settings()
    for st, gate in ([(legacy, True)] if legacy else []) + [(scanner.SETTINGS, True), (scanner.SETTINGS, False)]:
        # the rules they were logged under first; last pass without the pattern volume rule (signals logged before it)
        if not pend:
            break
        try:
            res = engine.analyze(c4, st)
            idx = res["close"].index
            hub = [t for t in res["trades"] if f"{sym}|{idx[t['i0']].isoformat()}" in pend]
            if hub:
                _record(sym, c4, res, start, trades=hub, only_existing=True)
            pa = patterns.analyze_all(c4, res, st, vol_gate=gate, live_only=False)   # finish signals logged before Patch 82 too
            for kind in patterns.KINDS:
                if kind != "hub":
                    tr = [x for x in pa["trades"] if x["kind"] == kind and f"{kind}|{sym}|{idx[x['i0']].isoformat()}" in pend]
                    if tr:
                        _record(sym, c4, res, start, kind, tr, only_existing=True)
            pend = {i for i in db.pending_signal_ids(sym) - produced if not i.startswith("T|")}
        except Exception as e:
            log.warning("orphan resolve failed for %s: %s", sym, e)
            return


def _regime_tag(direction: int, bt=None) -> str | None:
    """"with" if the breakout runs with the stored market regime, "against" if it fights it,
    None when no regime is stored yet (fail open: untagged signals stay in the stats).
    Only breakouts from the last few days are tagged: the stored regime says nothing about an old
    breakout (a newly added ticker's history, or a rebuilt record), so those stay untagged."""
    try:
        if bt is not None and pd.Timestamp.now(tz="UTC") - pd.Timestamp(bt).tz_convert("UTC") > pd.Timedelta(days=4):
            return None
        rg = json.loads(db.get_setting("regime") or "null")
    except Exception:
        return None
    if not rg or rg.get("regime") not in ("up", "down"):
        return None
    return "with" if (rg["regime"] == "up") == (direction == 1) else "against"


def _record(sym: str, c4: pd.DataFrame, res: dict, start: pd.Timestamp, pattern: str = "hub", trades: list | None = None,
            only_existing: bool = False) -> set:
    """Upsert this symbol's signals. Returns the signal ids it touched."""
    idx = res["close"].index
    changed = _rules_changed_at()
    ids = set()
    for t in (res["trades"] if trades is None else trades):
        bt = idx[t["i0"]]
        if bt <= start:
            continue
        h = t["hub"]
        sid = (f"{sym}|{bt.isoformat()}" if pattern == "hub" else f"T|{sym}|{bt.isoformat()}" if pattern == "tightening"
               else f"{pattern}|{sym}|{bt.isoformat()}")
        if changed is not None and bt < changed and not only_existing:
            continue       # before the rule change: never back-fill, never rewrite (old signals are finished by _resolve_orphans)
        if only_existing and db.get_signal(sid) is None:
            continue
        ids.add(sid)
        db.upsert_signal(
            id=sid, pattern=pattern,
            symbol=sym, direction=t["direction"],
            hub_low=round(h["low"], 2), hub_high=round(h["high"], 2), hub_sessions=h["sessions"],
            breakout_time=bt.isoformat(), entry=round(t["entry"], 2), target=round(t["target"], 2),
            target_kind=t.get("target_kind") or ("shelf" if t["shelf"] else "atr"),
            outcome=t["outcome"], outcome_time=idx[t["oi"]].isoformat() if t["oi"] is not None else None,
            ret_pct=None if t["ret"] is None else round(t["ret"], 2), chase=int(bool(t["chase"])),
            regime=_regime_tag(t["direction"], bt),
            grade=t["hub"].get("grade"),
            rvol=engine.rvol(c4, t["i0"]))
    return ids


def _dollar_volume_20d(bars) -> float | None:
    """20-day average daily dollar volume from 30-min bars (Volume x Close, per NY session).
    None when there is not enough data to judge (fewer than 5 sessions, or no Volume
    column): fail open, never block a name on missing data."""
    if bars is None or bars.empty or "Volume" not in bars.columns:
        return None
    day = bars.index.tz_convert("America/New_York").normalize()
    dv = (bars["Volume"] * bars["Close"]).groupby(day).sum()
    dv = dv[dv > 0].tail(20)
    if len(dv) < 5:
        return None
    return float(dv.mean())


def quality_skip(sym: str, dv20: float | None, mc: float | None) -> str | None:
    """'illiquid' / 'smallcap' when a name fails a universe filter, else None (missing data = pass)."""
    if dv20 is not None and dv20 < MIN_DOLLAR_VOLUME:
        return "illiquid"
    if mc is not None and mc < MIN_MARKET_CAP:
        return "smallcap"
    return None


# live progress of the market sweep, read by the scanner agent on Discover
SCAN = dict(running=False, done=0, total=0, current=None, started=None, finished=None, found=[])
FIND_STATES = {"breakout_up": "breakout", "breakdown": "breakdown"}


BATCH = 40


def scan_board() -> int:
    """Scan the whole universe (plus your watchlist). Runs after each 4H close."""
    if not _lock.acquire(blocking=False):      # a sweep is already running
        return 0
    try:
        n = _scan_board()
    finally:
        SCAN.update(running=False, current=None, finished=pd.Timestamp.now(tz="UTC").isoformat())
        _lock.release()
    try:      # tell the owner it's done (after the lock is released, so a slow send never blocks the next sweep)
        import notify as _notify
        found = SCAN.get("found") or []
        live = sum(1 for f in found if f.get("kind") in ("breakout", "breakdown"))
        mins = ""
        try:
            mins = f" · {round((pd.Timestamp(SCAN['finished']) - pd.Timestamp(SCAN['started'])).total_seconds() / 60)} min"
        except Exception:
            pass
        _notify.admins(f"🧹 Sweep done: {SCAN.get('done', n)}/{SCAN.get('total', n)} tickers · {len(found)} setups · {live} live breakouts{mins}")
    except Exception as e:
        log.warning("sweep-done note failed: %s", e)
    return n


def _scan_board() -> int:
    if True:
        watch = {r["symbol"] for r in db.list_tickers()}
        FRESH_A.clear()
        syms = sorted(set(universe.board_universe()) | watch)
        start = pd.Timestamp(record_start())
        rules_start_ts = pd.Timestamp(rules_start())
        now = pd.Timestamp.now(tz="UTC")
        SCAN.update(running=True, done=0, total=len(syms), current="loading prices", found=[],
                    started=pd.Timestamp.now(tz="UTC").isoformat())
        _rules_check()
        done = 0
        skipped_illiquid = 0
        skipped_smallcap = 0
        lab_hub, lab_tri, lab_live, lab_pat = [], [], [], {}
        bars, batch_end = {}, 0
        for k, sym in enumerate(syms):
            if k >= batch_end:        # download 40 stocks at a time, so memory stays small on any server size
                bars = None
                batch = syms[k:k + BATCH]
                batch_end = k + BATCH
                try:
                    bars = data.get_bars_many(batch, BOARD_DAYS)
                except Exception as e:
                    log.warning("board batch %s..%s failed: %s", batch[0], batch[-1], e)
                    bars = {}
            time.sleep(0.01)          # let web requests through between stocks so the site stays quick during a scan
            SCAN.update(current=sym, done=k)
            try:
                raw = bars.pop(sym, pd.DataFrame())
                dv20 = _dollar_volume_20d(raw)
                mc = universe.market_cap(sym)
                skip = None if sym in watch else quality_skip(sym, dv20, mc)
                if skip:
                    if skip == "illiquid":
                        skipped_illiquid += 1
                    else:
                        skipped_smallcap += 1
                    db.delete_board(sym)          # not shown, not counted (and no stale row left behind)
                    if db.pending_signal_ids(sym):   # signals already logged still get their outcome; no new ones
                        c4 = engine.to_4h(raw, now=now)
                        if len(c4) >= 30:
                            _resolve_orphans(sym, c4, start, set())
                    continue
                c4 = engine.to_4h(raw, now=now)
                if len(c4) < scanner.SETTINGS.window + 15:
                    continue
                res = engine.analyze(c4, scanner.SETTINGS)
                if sym == REGIME_INDEX:
                    rg = engine.regime_from_closes(c4, REGIME_DAYS)
                    if rg:
                        rg.update(index=REGIME_INDEX, days=REGIME_DAYS, as_of=pd.Timestamp.now(tz=engine.NY).isoformat())
                        db.set_setting("regime", json.dumps(rg))
                bs = _board_status(c4, res)
                if dv20 is not None:
                    bs["dv20"] = round(dv20)
                if mc is not None:
                    bs["mc"] = mc
                db.set_board(sym, json.dumps(bs))
                kind = FIND_STATES.get(bs.get("state"))
                if not kind and bs.get("state") == "forming" and bs.get("near_edge"):
                    kind = "at edge"
                if kind:
                    SCAN["found"].append(dict(symbol=sym, kind=kind))
                produced = _record(sym, c4, res, start)
                lab_hub += res["trades"]
                try:      # more setups: triangles, channels, support & resistance
                    pa = patterns.analyze_all(c4, res, scanner.SETTINGS)
                    bs["setups"] = patterns.live_json(sym, c4, res, pa)
                    db.set_board(sym, json.dumps(bs))
                    for kind in patterns.KINDS:
                        if kind == "hub":
                            continue
                        tr = [x for x in pa["trades"] if x["kind"] == kind]
                        produced |= _record(sym, c4, res, start, kind, tr)
                        lab_pat.setdefault(kind, []).extend(tr)
                    for st in bs["setups"]:
                        if st["state"] in ("breakout_up", "breakdown"):
                            SCAN["found"].append(dict(symbol=sym, kind="breakout" if st["state"] == "breakout_up" else "breakdown", setup=st["name"]))
                except Exception as e:
                    log.warning("pattern scan failed for %s: %s", sym, e)
                    pa = None
                try:      # the live record of today's rules
                    _record_rules(sym, c4, res, pa, rules_start_ts)
                except Exception as e:
                    log.warning("rules record failed for %s: %s", sym, e)
                try:      # test mode: tightening hubs (triangles), admin-only
                    tri = engine.analyze_tightening(c4, res, scanner.SETTINGS)
                    _record(sym, c4, res, start, "tightening", tri["trades"])
                    lab_tri += tri["trades"]
                    lab_live += _tri_live(sym, c4, res, tri)
                except Exception as e:
                    log.warning("tightening scan failed for %s: %s", sym, e)
                _resolve_orphans(sym, c4, start, produced)    # signals from the old rules: finish them, don't drop them
                done += 1
            except Exception as e:
                log.warning("board scan failed for %s: %s", sym, e)
        db.set_setting("lab", json.dumps(dict(
            updated=pd.Timestamp.now(tz=engine.NY).isoformat(), symbols=done, days=BOARD_DAYS,
            skipped_illiquid=skipped_illiquid, skipped_smallcap=skipped_smallcap,
            regime=json.loads(db.get_setting("regime") or "null"),
            hub=engine.stats(lab_hub), tightening=engine.stats(lab_tri), live=lab_live,
            patterns={k: engine.stats(v) for k, v in lab_pat.items()})))
        log.info("board scan done: %d/%d symbols (%d skipped: illiquid, %d skipped: small-cap)",
                 done, len(syms), skipped_illiquid, skipped_smallcap)
        threading.Thread(target=_warm_gamma, daemon=True).start()   # walls for the fresh breakouts, off the hot path
        if FRESH_A:
            import notify
            threading.Thread(target=notify.every_a_alert, args=(list(FRESH_A),), daemon=True).start()
        return done


def _fresh_break(r: dict) -> bool:
    """Broke out on the latest candle or the one before (label 'n/10 candles', n <= 1)."""
    import re as _re
    m = _re.search(r"(\d+)/\d+", r.get("label") or "")
    return bool(m) and int(m.group(1)) <= 1


def _gamma_input(r: dict) -> dict | None:
    hub = r.get("hub") or {}
    d = 1 if r.get("state") == "breakout_up" else -1
    edge = hub.get("high") if d == 1 else hub.get("low")
    if not edge or not r.get("target"):
        return None
    return {"symbol": r["symbol"], "d": d, "edge": edge, "target": r["target"], "price": r.get("last_close")}


def _warm_gamma():
    """Fetch gamma walls once a day for fresh confirmed breakouts only (a handful), never the whole universe."""
    try:
        for r in board()["live"]:
            if _fresh_break(r) and (g := _gamma_input(r)):
                gamma.walls(g["symbol"], g["price"])
    except Exception as e:
        log.warning("gamma warm-up failed: %s", e)


def _tri_live(sym: str, c4: pd.DataFrame, res: dict, tri: dict) -> list[dict]:
    """Tightening hubs that are active now, or broke out within the last few candles."""
    n, close, out = len(c4), res["close"], []
    for p in tri["patterns"]:
        if p["active"]:
            up, dn = engine.tri_lines(p, n - 1)
            out.append(dict(symbol=sym, kind=p["kind"], state="forming", sessions=p["sessions"],
                            upper=round(up, 2), lower=round(dn, 2), last=round(float(close.iloc[-1]), 2)))
    for t in tri["trades"]:
        if n - 1 - t["i0"] <= 4:
            out.append(dict(symbol=sym, kind=t["hub"]["kind"], state="breakout_up" if t["direction"] == 1 else "breakdown",
                            sessions=t["hub"]["sessions"], edge=round(t["edge"], 2), target=round(t["target"], 2),
                            outcome=t["outcome"], last=round(float(close.iloc[-1]), 2)))
    return out


def lab() -> dict:
    """Admin-only comparison: plain hubs vs tightening hubs."""
    snap = json.loads(db.get_setting("lab") or "{}")
    fwd = {}
    for pat in ("hub", "tightening"):
        sigs = db.all_signals(1000, pat)
        done = [x for x in sigs if x["outcome"] not in ("pending", None) and not x["chase"]]
        rets = [x["ret_pct"] for x in done if x["ret_pct"] is not None]
        fwd[pat] = dict(signals=len(sigs), closed=len(done),
                        reached=round(100 * sum(x["outcome"] == "follow-through" for x in done) / len(done), 1) if done else None,
                        win_rate=round(100 * sum(r > 0 for r in rets) / len(rets), 1) if rets else None,
                        avg_ret=round(sum(rets) / len(rets), 2) if rets else None)
    return dict(snapshot=snap, forward=fwd, since=record_start(), recent=db.all_signals(40, "tightening"))


def board() -> dict:
    rows = []
    for r in db.board_rows():
        st = json.loads(r["status"] or "{}")
        st["symbol"] = r["symbol"]
        rows.append(st)
    watch = {r["symbol"] for r in db.list_tickers()}
    for r in rows:
        r["watching"] = r["symbol"] in watch
    live = [r for r in rows if r.get("state") in ("breakout_up", "breakdown")]
    live.sort(key=lambda r: abs(r.get("to_target_pct") or 99))
    for r in live:      # gamma wall line, from the per-day cache only: a page load never downloads a chain
        g = _gamma_input(r)
        if g:
            gamma.attach(g, fetch=False)
            r["gamma_line"] = gamma.line(g) or None
            r["gamma_capped"] = g["gamma_capped"]
            r["gamma_wall"] = g["gamma_wall"]
    forming = [r for r in rows if r.get("state") in ("forming", "watching")]
    near = [r for r in forming if r.get("near_edge")]
    near.sort(key=lambda r: -abs(r["pos"] - 0.5))
    forming.sort(key=lambda r: -(r.get("hub") or {}).get("sessions", 0))
    recent = [r for r in rows if r.get("state") in ("follow-through", "snap-back", "stalled")]
    updated = max((r["updated_at"] for r in db.board_rows()), default=None)
    setups = []      # every non-hub setup on the board, one row each
    for r in rows:
        for st in r.get("setups") or []:
            if st["state"] == "between":
                continue
            setups.append(dict(st, symbol=r["symbol"], last_close=r.get("last_close"), watching=r["watching"]))
    rank = {"breakout_up": 0, "breakdown": 0, "testing": 1, "forming": 2}
    setups.sort(key=lambda x: (rank.get(x["state"], 9), x["symbol"]))
    for r in rows:
        r.pop("setups", None)
    return dict(updated=updated, universe=len(rows), live=live, near=near, forming=forming, recent=recent, setups=setups,
                regime=json.loads(db.get_setting("regime") or "null"))


def _summary(sigs: list) -> dict:
    closed = [s for s in sigs if s["outcome"] not in ("pending", None) and not s["chase"]]
    rets = [s["ret_pct"] for s in closed if s["ret_pct"] is not None]
    return dict(signals=len(sigs), closed=len(closed),
                reached=round(100 * sum(s["outcome"] == "follow-through" for s in closed) / len(closed), 1) if closed else None,
                win_rate=round(100 * sum(r > 0 for r in rets) / len(rets), 1) if rets else None,
                avg_ret=round(sum(rets) / len(rets), 2) if rets else None)


def by_pattern() -> dict:
    return {k: dict(_summary(db.all_signals(1000, k)), name=patterns.KINDS[k]) for k in patterns.KINDS}


def _by_grade(closed: list[dict]) -> dict:
    """Did warehouse hubs follow through better than ledges? (labels only until the gate is on)"""
    out = {}
    for name in ("warehouse", "ledge"):
        g = [s for s in closed if s.get("grade") == name]
        rets = [s["ret_pct"] for s in g if s["ret_pct"] is not None]
        out[name] = dict(n=len(g), reached=sum(1 for s in g if s["outcome"] == "follow-through"),
                         win_rate=round(100 * sum(1 for r in rets if r > 0) / len(rets), 1) if rets else None,
                         avg_ret=round(sum(rets) / len(rets), 2) if rets else None)
    return out


def _by_volume(closed: list[dict]) -> dict:
    """Did breakouts on strong volume do better than light ones? (labels only, the rules don't use volume)"""
    out = {}
    for name in ("strong", "normal", "light"):
        g = [s for s in closed if s.get("rvol") is not None and engine.rvol_word(s["rvol"]) == name]
        rets = [s["ret_pct"] for s in g if s["ret_pct"] is not None]
        out[name] = dict(n=len(g), reached=sum(1 for s in g if s["outcome"] == "follow-through"),
                         win_rate=round(100 * sum(1 for r in rets if r > 0) / len(rets), 1) if rets else None,
                         avg_ret=round(sum(rets) / len(rets), 2) if rets else None)
    return out


def track_record(pattern: str = "hub") -> dict:
    sigs = db.all_signals(1000, pattern)
    if pattern == RULES:      # entries are the retest close, so sort by when the entry came
        sigs.sort(key=lambda x: x.get("entry_time") or x.get("breakout_time") or "", reverse=True)
    closed = [s for s in sigs if s["outcome"] not in ("pending", None) and not s["chase"]]
    rets = [s["ret_pct"] for s in closed if s["ret_pct"] is not None]
    wins = [r for r in rets if r > 0]
    by = {o: sum(1 for s in closed if s["outcome"] == o) for o in ("follow-through", "snap-back", "stalled")}
    return dict(
        since=rules_start() if pattern == RULES else record_start(),
        stats=dict(signals=len(sigs), closed=len(closed), open=sum(1 for s in sigs if s["outcome"] == "pending"),
                   chases=sum(1 for s in sigs if s["chase"]),
                   against_trend=sum(1 for s in sigs if s.get("regime") == "against"),
                   win_rate=round(100 * len(wins) / len(rets), 1) if rets else None,
                   avg_ret=round(sum(rets) / len(rets), 2) if rets else None,
                   avg_win=round(sum(wins) / len(wins), 2) if wins else None,
                   avg_loss=round(sum(r for r in rets if r <= 0) / max(1, len(rets) - len(wins)), 2)
                   if len(rets) > len(wins) else None,
                   by_outcome=by, by_volume=_by_volume(closed), by_grade=_by_grade(closed)),
        signals=sigs[:200], pattern=pattern, patterns=by_pattern(), by_symbol=_by_symbol(closed))


def _by_symbol(closed: list[dict]) -> list[dict]:
    """Win rate per stock for one setup (completed signals only, chases excluded)."""
    agg: dict[str, list[float]] = {}
    for s_ in closed:
        if s_["ret_pct"] is not None:
            agg.setdefault(s_["symbol"], []).append(s_["ret_pct"])
    out = []
    for sym, rets in agg.items():
        w = sum(1 for r in rets if r > 0)
        out.append(dict(symbol=sym, n=len(rets), wins=w, win_rate=round(100 * w / len(rets), 1),
                        avg_ret=round(sum(rets) / len(rets), 2)))
    return out


def _seg(p, idx, t, a, b, kind, live):
    ua, da = patterns.lines_at(p, a)
    ub, db_ = patterns.lines_at(p, b)
    return dict(kind=kind, name=patterns.KINDS[kind], live=live, start=t(idx[a]), end=t(idx[b]),
                ua=round(ua, 2), ub=round(ub, 2), la=round(da, 2), lb=round(db_, 2))


_CHART_CACHE: dict = {}      # (sym, tf, lab_mode) -> (expires, payload): candles only change at a 4-hour close
_CHART_LOCK = threading.Lock()


def _chart_expiry(now: pd.Timestamp) -> pd.Timestamp:
    """Keep a chart until just after the next 4-hour close (when new candles exist); 30 minutes outside market hours."""
    if not data.market_open(now):
        return now + pd.Timedelta(minutes=30)
    end = engine.candle_end(engine.current_candle_start(now))
    return end.tz_convert("UTC") + pd.Timedelta(minutes=3)


def chart(sym: str, tf: str = "4h", lab_mode: bool = False, fresh: bool = False) -> dict:
    """The stock panel's chart, served from a cache until the next close; only the forming candle is refreshed per call."""
    sym = scanner.clean_symbol(sym)
    if tf not in ("4h", "1d"):
        tf = "4h"
    t0, now = time.time(), pd.Timestamp.now(tz="UTC")
    key = (sym, tf, bool(lab_mode))
    hit = _CHART_CACHE.get(key)
    if hit and hit[0] > now and not fresh:
        out = dict(hit[1])
        out["forming"] = _forming(sym) if tf == "4h" else None
        out["timing"] = dict(cached=True, total=round((time.time() - t0) * 1000))
        return out
    out = _build_chart(sym, tf, lab_mode)
    with _CHART_LOCK:
        if len(_CHART_CACHE) > 400:                 # stay small on a 512 MB server
            for k in sorted(_CHART_CACHE, key=lambda k: _CHART_CACHE[k][0])[:100]:
                _CHART_CACHE.pop(k, None)
        _CHART_CACHE[key] = (_chart_expiry(now), {k: v for k, v in out.items() if k not in ("forming", "timing")})
    out["timing"]["total"] = round((time.time() - t0) * 1000)
    return out


_FORMING: dict = {}          # sym -> (time, candle): the forming candle, refreshed at most once a minute


def _forming(sym: str):
    hit = _FORMING.get(sym)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    try:
        c = scanner.forming_candle(sym)
    except Exception:
        c = None
    _FORMING[sym] = (time.time(), c)
    return c


def warm_charts(symbols: list[str] | None = None):
    """After a scan: build the 4H charts for watchlist stocks so the panel opens instantly."""
    syms = symbols if symbols is not None else [r["symbol"] for r in db.list_tickers()]
    n = 0
    for s in syms[:150]:
        try:
            _CHART_CACHE.pop((s, "4h", False), None)
            chart(s, "4h", False)
            n += 1
        except Exception:
            pass
        time.sleep(0.05)                            # leave room for live requests
    log.info("warmed %d charts", n)


def _build_chart(sym: str, tf: str, lab_mode: bool) -> dict:
    tm = {}
    t0 = time.time()
    c4 = scanner.load_4h(sym, scanner.SCAN_DAYS)
    tm["bars"] = round((time.time() - t0) * 1000)
    if c4.empty:
        raise ValueError("no price data")
    frame = engine.to_daily(c4) if tf == "1d" else c4     # the view's candles; all analysis stays on c4
    t1 = time.time()
    res = engine.analyze(c4, scanner.SETTINGS)
    st = engine.current_status(c4, res, scanner.SETTINGS)
    tm["analyze"] = round((time.time() - t1) * 1000)
    idx = c4.index

    def t(ts):   # chart library uses UTC seconds; shift so labels read in New York time
        return int(ts.tz_localize(None).timestamp())

    has_vol = "Volume" in frame.columns
    candles = [dict(time=t(i), open=round(r.Open, 2), high=round(r.High, 2), low=round(r.Low, 2), close=round(r.Close, 2),
                    volume=(int(r.Volume) if has_vol and pd.notna(r.Volume) else None))
               for i, r in frame.iterrows()]
    hubs = [dict(start=t(idx[h["start_i"]]), end=t(idx[h["end_i"]]), low=round(h["low"], 2), high=round(h["high"], 2),
                 sessions=h["sessions"], active=h["active"]) for h in res["hubs"]]
    trades = [dict(time=t(idx[x["i0"]]), direction=x["direction"], target=round(x["target"], 2),
                   outcome=x["outcome"], chase=x["chase"], ret=None if x.get("ret") is None else round(x["ret"], 3),
                   outcome_time=t(idx[x["oi"]]) if x["oi"] is not None else None) for x in res["trades"]]
    t2 = time.time()
    forming = _forming(sym) if tf == "4h" else None
    tm["forming"] = round((time.time() - t2) * 1000)
    out = dict(symbol=sym, tf=tf, candles=candles, hubs=hubs, trades=trades, status=st, forming=forming, timing=tm,
               stats=engine.stats(res["trades"]), watching=db.get_ticker(sym) is not None)
    try:      # other setups, drawn as lines on the chart
        t3 = time.time()
        pa = patterns.analyze_all(c4, res, scanner.SETTINGS)
        st["setups"] = patterns.live_json(sym, c4, res, pa, with_news=False)
        try:      # the possible catalyst comes from the board scan's copy (no news call while the panel waits)
            brow = db.board_row(sym)
            cats = {(x.get("kind"), x.get("since")): x.get("catalyst") for x in (json.loads(brow["status"] or "{}").get("setups") or [])} if brow else {}
            for x in st["setups"]:
                if cats.get((x.get("kind"), x.get("since"))):
                    x["catalyst"] = cats[(x["kind"], x["since"])]
        except Exception:
            pass
        tm["patterns"] = round((time.time() - t3) * 1000)
        draw = []
        for L in pa["live"]:
            if L["kind"] != "sr" and L["state"] == "forming":
                p = L["p"]
                draw.append(_seg(p, idx, t, p["a"], len(idx) - 1, L["kind"], True))
        for x in pa["trades"][-12:]:
            if x["kind"] != "sr" and x["i0"] >= len(idx) - 120:
                p = x["p"]
                draw.append(_seg(p, idx, t, p["a"], x["i0"], x["kind"], False))
        levels = []
        for L in pa.get("levels_now", []):
            levels.append(dict(price=round(L["price"], 2), touches=L["touches"], since=t(idx[L["first"]])))
        out["setups"] = dict(lines=draw, levels=levels,
                             trades=[dict(time=t(idx[x["i0"]]), direction=x["direction"], kind=x["kind"], outcome=x["outcome"],
                                          chase=x["chase"]) for x in pa["trades"] if x["i0"] >= len(idx) - 160],
                             stats={k: engine.stats([x for x in pa["trades"] if x["kind"] == k]) for k in patterns.KINDS if k != "hub"})
    except Exception as e:
        log.warning("chart setups failed for %s: %s", sym, e)
    if lab_mode:   # admin test mode: tightening hubs as two lines each
        tri = engine.analyze_tightening(c4, res, scanner.SETTINGS)
        out["tightening"] = []
        for p in tri["patterns"]:
            b = min(len(idx) - 1, p["end_i"] + 3)
            ua, la = engine.tri_lines(p, p["start_i"])
            ub, lb = engine.tri_lines(p, b)
            out["tightening"].append(dict(kind=p["kind"], start=t(idx[p["start_i"]]), end=t(idx[b]),
                                          ua=round(ua, 2), la=round(la, 2), ub=round(ub, 2), lb=round(lb, 2)))
        out["tightening_trades"] = [dict(time=t(idx[x["i0"]]), direction=x["direction"], outcome=x["outcome"],
                                         chase=x["chase"]) for x in tri["trades"]]
        out["tightening_stats"] = engine.stats(tri["trades"])
    return out
