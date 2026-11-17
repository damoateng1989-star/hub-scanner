"""Swing Play Card — Sika's read for a swing setup (Patch 152).

Display only. Reads the engine state, the options plan, the GEX shelves and
the regime; computes no new levels. The only grade it computes is the setup
grade /api/grade already computes (engine.setup_grade on the backtest trades).
No trading, no alert, no record entry, no Lab log.
"""
import json
import logging
from datetime import date

import db
import engine
import gamma
import options

log = logging.getLogger("swing_card")


def _money(x):
    return f"${x:,.2f}" if x is not None else "–"


def _fmt_date(iso):
    """'2026-12-11' -> 'Dec 11' (the alert_line format in options.py)."""
    try:
        return date.fromisoformat(iso).strftime("%b %-d")
    except Exception:
        return iso or ""


def _regime_line():
    """Market context for the Confirm line, from the stored regime (fail-open)."""
    try:
        raw = db.get_setting("regime")
        rg = json.loads(raw) if raw else None
    except Exception:
        return None
    if not rg or rg.get("regime") not in ("up", "down"):
        return None
    word = "uptrend" if rg["regime"] == "up" else "downtrend"
    idx = rg.get("index")
    return f"Market: {word}" + (f" ({idx})" if idx else "")


def _grade(sym, kind, d):
    """The setup's 2-year record grade in this direction — the same numbers
    /api/grade returns (engine.setup_grade on the backtest trades). This is
    the setup grade, NOT the retest grade: the retest grade only exists once
    a retest close prints, so it cannot be computed at breakout time."""
    import scanner
    if kind == "hub":
        tr = scanner._bt_trades(sym)
    else:
        tr = scanner._bt_pattern_trades(sym, kind)
    return engine.setup_grade(tr, d)


def _wall_beyond(sh, d, level):
    """Next GEX wall beyond `level` in direction d, from gamma.shelves() (fail-open)."""
    if not sh:
        return None
    rows = sh.get("shelves") or []
    if d == 1:
        cands = [r["strike"] for r in rows if r.get("side") in ("call", "both") and r["strike"] > level]
        return min(cands) if cands else None
    cands = [r["strike"] for r in rows if r.get("side") in ("put", "both") and r["strike"] < level]
    return max(cands) if cands else None


def _extension(d, target, res, hub, S, atr, sh):
    """What happens past the target: the next shelf, else the next GEX wall, else new highs/lows."""
    nxt, kind = None, None
    if hub is not None:
        found = engine.find_shelf(res["hubs"], hub, d, target, atr, S)
        if found:
            nxt, kind = found["level"], "shelf"
    if nxt is None:
        w = _wall_beyond(sh, d, target)
        if w is not None:
            nxt, kind = w, "wall"
    if nxt is None:
        return dict(text=f"{'over' if d == 1 else 'under'} {_money(target)} = new {'highs' if d == 1 else 'lows'}", price=None)
    word = "next shelf" if kind == "shelf" else ("call wall" if d == 1 else "put wall")
    return dict(text=f"{'over' if d == 1 else 'under'} {_money(target)} = {word} {_money(nxt)}", price=nxt)


def _contract_for(op, d):
    """First contract of the options plan in direction d, or None. Reference only."""
    if not op:
        return None
    pl = next((x for x in op.get("plans") or [] if x.get("direction") == d), None)
    if not pl or not pl.get("contracts"):
        return None
    c = pl["contracts"][0]
    return dict(expiry=_fmt_date(pl["expiry"]), strike=c["strike"], side=pl["side"], cost_mid=c["mid"])


def _side(d, edge, target, res, hub, S, atr, sh, op, regime, role):
    """One side of the card: trigger / targets / extension / confirm / wrong-if / contract."""
    confirm = [f"4H close {'above' if d == 1 else 'below'} {_money(edge)}"]
    if regime:
        confirm.append(regime)
    return dict(
        side="calls" if d == 1 else "puts",
        label=f"{'Calls' if d == 1 else 'Puts'} ({'favored' if role == 'favored' else 'only on a break'})",
        trigger=dict(text=f"{'holds above' if d == 1 else 'loses'} {_money(edge)}", price=edge),
        targets=[_money(target)],
        extension=_extension(d, target, res, hub, S, atr, sh),
        confirm=confirm,
        wrong_if=f"4H close back {'below' if d == 1 else 'above'} {_money(edge)}",
        contract=_contract_for(op, d),
    )


def _opposite(d, edge, target, st, th, res, hub, S, atr, sh, op, regime):
    """The mirror side: a break of the other edge. None when no opposite edge exists."""
    # The other edge of the same structure: the pattern's own hub first, else the engine hub.
    # For a gap fade the trade's hub is {low: fade low (the stop), high: gap-day high}, so the
    # opposite trigger is the fade low — AMAT: $329.29.
    other = None
    if th and th.get("low") is not None and th.get("high") is not None:
        other = th["low"] if d == 1 else th["high"]
    elif st.get("hub"):
        other = st["hub"]["low"] if d == 1 else st["hub"]["high"]
    if other is None:
        return None
    # The opposite target: the shelf on the other side, else 1 ATR past the edge (Patch 144 fallback).
    shelf = st.get("shelf_down" if d == 1 else "shelf_up")
    tgt = shelf["level"] if shelf else round(other - d * S.target_atr * atr, 2)
    return _side(-d, other, tgt, res, hub, S, atr, sh, op, regime, "opposite")


def _context(op):
    """Context warnings, in order, only when they apply. v1 ships the earnings warning only:
    the spec's second item (a setup-specific note) has no source string in the code — there is no
    canonical short note to copy verbatim — so nothing is invented for it."""
    out = []
    if op and op.get("earnings"):
        er = op["earnings"]
        fav = next((p for p in op.get("plans") or [] if p.get("mode") == "live"), None)
        if fav and er.get("date") and er["date"] <= fav["expiry"]:
            # The same wording the Options swing section uses (optionsHTML, the ox-earn line).
            out.append(f"Earnings {er['label']}, before these contracts expire. Option prices usually "
                       "drop right after earnings (the \"IV crush\"), even when the stock moves your "
                       "way, and a gap can skip past your exit.")
    return out


def card(sym):
    """Sika's swing-play read for one symbol. Raises ValueError when there is not
    enough price history (the endpoint turns it into a 400, like /api/options)."""
    import patterns
    import scanner

    sym = scanner.clean_symbol(sym)
    c4 = scanner.load_4h(sym, scanner.SCAN_DAYS)
    if len(c4) < 40:
        raise ValueError("not enough price history")
    S = scanner.SETTINGS
    res = engine.analyze(c4, S)
    st = engine.current_status(c4, res, S)
    last = float(res["close"].iloc[-1])
    atr = float(res["atr"].iloc[-1])
    hub = res["hubs"][-1] if res["hubs"] else None
    as_of = st.get("last_candle_close") or st.get("last_candle")
    # Options plan: fail-open — a failed chain fetch must not kill the levels card.
    try:
        op = options.plan(sym)
    except Exception as e:
        log.warning("swing card %s: options plan failed: %s", sym, e)
        op = None
    # GEX shelves: fail-open (None when there is no chain for the symbol).
    try:
        sh = gamma.shelves(sym, last)
    except Exception:
        sh = None
    regime = _regime_line()
    out = dict(symbol=sym, as_of=as_of, setup=None, grade=None, state="waiting",
               favored=None, opposite=None, in_between=None, context=[], message=None)

    # --- the live setup: hub first, then patterns (the same order options._plan uses)
    live = None  # (direction, edge, target, setup_name, kind, trade_hub)
    state = st.get("state")
    if state in ("breakout_up", "breakdown"):
        d = 1 if state == "breakout_up" else -1
        edge = st["hub"]["high"] if d == 1 else st["hub"]["low"]
        live = (d, edge, st["target"], "Hub", "hub", st["hub"])
    else:
        try:
            pa = patterns.analyze_all(c4, res, S)
            lv = next((x for x in pa["live"] if x["state"] in ("breakout_up", "breakdown")), None)
        except Exception:
            lv = None
        if lv:
            d = 1 if lv["state"] == "breakout_up" else -1
            th = (lv.get("trade") or {}).get("hub")
            live = (d, lv["edge"], lv["target"], patterns.KINDS.get(lv["kind"], lv["kind"]), lv["kind"], th)

    if live:
        d, edge, target, setup_name, kind, th = live
        out["setup"] = setup_name
        g = _grade(sym, kind, d)
        out["grade"] = g.get("grade")
        out["state"] = "live"
        out["context"] = _context(op)
        if g.get("grade") in ("A", "B"):
            out["favored"] = _side(d, edge, target, res, hub, S, atr, sh, op, regime, "favored")
            out["opposite"] = _opposite(d, edge, target, st, th, res, hub, S, atr, sh, op, regime)
        else:
            # Spec §4.2: the favored side is only set for a live setup graded A or B.
            # A live setup graded C, D or "?" is watch only — the card says so instead of
            # presenting it as a favored play.
            out["message"] = (f"Live {setup_name} {'breakout' if d == 1 else 'breakdown'}, "
                              f"graded {g.get('grade')} ({g.get('word')}) — watch only, no favored side.")
        return out

    if state == "forming" and st.get("hub"):
        out["state"] = "in_between"
        out["setup"] = "Hub"
        hh = st["hub"]
        lo = st.get("trigger_down") or hh["low"]
        hi = st.get("trigger_up") or hh["high"]
        out["in_between"] = dict(low=lo, high=hi,
            text=f"In between ({_money(lo)} – {_money(hi)}), no edge here; let one side break first.")
        out["context"] = _context(op)
        return out

    out["state"] = "waiting"
    out["message"] = (op or {}).get("message") or st.get("label") or "No setup in play right now."
    out["context"] = _context(op)
    return out
