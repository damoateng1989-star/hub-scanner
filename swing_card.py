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


_GAMMA_PATH_SHARE = 0.25      # an in-path shelf counts when its |gex| reaches
                              # this share of the largest supplied shelf (spec
                              # §5; the same 0.25 NEXT_LEVEL_SHARE uses — here
                              # it is only a wording threshold, never a label)
GAMMA_LADDER_N = 4            # top 4 positive + top 4 negative strikes for gamma context (was the card's 2+2)
_GAMMA_CONFLUENCE_PCT = 0.01  # a gamma strike within 1% of the trigger or the
                              # target counts as at that price (spec §5; the
                              # same 1% the GEX Lab triggers T3/T4 use)


def _gex_size(v):
    """A gamma size the way the app shows it (Patch 167's M format)."""
    return f"${abs(v) / 1e6:.2f}M"


def _gamma_facts(sym, spot, sh):
    """Monthly-basis gamma facts for the card (Patch 173; fail-open -> None).

    From gamma.walls() (call wall / put wall / flip on the monthly >=7 DTE
    basis) and the gamma.shelves() ladder card() already fetched — the chain
    is cached per symbol per day, so the walls call is a cache hit, not a
    second download. Facts only: no line is composed here, and nothing here
    can change the card's trigger, target, stop, or grade. The nearest-
    expiry next_levels() data is deliberately NOT used (intraday horizon),
    and no level is ever labelled "next" (Patch 169 stands)."""
    try:
        w = gamma.walls(sym, spot)
    except Exception:
        w = None
    if not w and not sh:
        return None
    flip = (w or {}).get("flip")
    regime = None
    if flip is not None:
        regime = "long" if spot >= flip else "short"
    shelves = []
    for r in (sh or {}).get("shelves") or []:
        try:
            shelves.append(dict(strike=float(r["strike"]), gex=float(r["gex"]),
                                side=r.get("side")))
        except Exception:
            continue
    try:
        as_of = gamma.chain_as_of(sym)
    except Exception:
        as_of = None
    return dict(expiry=(w or {}).get("expiry") or (sh or {}).get("expiry"),
                as_of=as_of, spot=spot,
                call_wall=(w or {}).get("call_wall"),
                put_wall=(w or {}).get("put_wall"),
                flip=flip, regime=regime, shelves=shelves,
                path=None, confluence=[], lines=[])


def _near(strike, price):
    """Within the confluence tolerance (1%) of the play's price."""
    return bool(price) and abs(strike - price) <= _GAMMA_CONFLUENCE_PCT * price


def _gamma_confluence(gx, d, trigger, target):
    """Gamma strikes that land on the play's own prices (spec §5).

    For each of the target and the trigger: the directional wall if it is
    within tolerance, else the nearest shelf strike within tolerance.
    Returns a list of {at, strike, what, price}, target first. A strike
    named here is agreement with the plan's own levels — it is never also
    counted as an obstacle or an in-path shelf (see _gamma_lines)."""
    out = []
    wall = gx.get("call_wall") if d == 1 else gx.get("put_wall")
    wall_word = "call wall" if d == 1 else "put wall"
    shelves = gx.get("shelves") or []
    # only real piles count: a shelf must reach the same 25% size bar the path check uses (walls always count)
    biggest = max((abs(s["gex"]) for s in shelves), default=0.0)
    shelves = [s for s in shelves if biggest and abs(s["gex"]) >= _GAMMA_PATH_SHARE * biggest]
    for at, price in (("target", target), ("trigger", trigger)):
        hit = None
        if wall is not None and _near(wall, price):
            hit = dict(at=at, strike=wall, what=wall_word, price=price)
        else:
            near = [s for s in shelves if _near(s["strike"], price)]
            if near:
                s = min(near, key=lambda s: abs(s["strike"] - price))
                hit = dict(at=at, strike=s["strike"], what="gamma shelf", price=price)
        if hit:
            out.append(hit)
    return out


def _gamma_path(gx, d, trigger, target, skip=frozenset()):
    """Classify the gamma path between the trigger and the target (spec §5).

    Returns (kind, strike, gex): "obstacle" — the directional wall strictly
    between trigger and target; "shelf" — the largest in-path shelf at or
    above the share threshold; "air_pocket" — shelves exist but none sits in
    the path; None — no shelves to judge by, or an in-path shelf exists but
    none reaches the threshold (no line is composed for that case). Strikes
    in `skip` (the confluent ones) are excluded throughout."""
    lo, hi = (target, trigger) if d == -1 else (trigger, target)
    wall = gx.get("call_wall") if d == 1 else gx.get("put_wall")
    if wall is not None and wall not in skip and lo < wall < hi:
        return ("obstacle", wall, None)
    shelves = gx.get("shelves") or []
    if not shelves:
        return (None, None, None)
    in_path = [s for s in shelves
               if lo < s["strike"] < hi and s["strike"] not in skip]
    if not in_path:
        return ("air_pocket", None, None)
    biggest = max(abs(s["gex"]) for s in shelves)
    strong = [s for s in in_path
              if biggest and abs(s["gex"]) >= _GAMMA_PATH_SHARE * biggest]
    if not strong:
        return (None, None, None)
    s = max(strong, key=lambda s: abs(s["gex"]))
    return ("shelf", s["strike"], s["gex"])


def _gamma_lines(gx, d, trigger, target):
    """The card's gamma context lines for the favored play (spec §5).

    At most two lines, composed here in code — never by the model — in this
    priority order: the confluence line (a gamma strike on the play's own
    trigger or target), one path line (obstacle > in-path shelf > air
    pocket), and, only when the name is short gamma, the regime line. The
    wording is fixed; the only variables are the card's own prices. Also
    records the confluence and path readings on the facts dict, so chat
    answers from the same numbers."""
    if not gx:
        return []
    conf = _gamma_confluence(gx, d, trigger, target)
    gx["confluence"] = conf
    kind, strike, gex = _gamma_path(gx, d, trigger, target,
                                    {c["strike"] for c in conf})
    gx["path"] = kind
    out = []
    if len(conf) == 2:
        t = next(c for c in conf if c["at"] == "trigger")
        g = next(c for c in conf if c["at"] == "target")
        out.append(f"Confluence: gamma sits at both ends of this play — the "
                   f"{_money(t['strike'])} {t['what']} at the {_money(trigger)} trigger "
                   f"and the {_money(g['strike'])} {g['what']} at the {_money(target)} target.")
    elif conf:
        c = conf[0]
        out.append(f"Confluence: the {_money(c['strike'])} {c['what']} sits at the "
                   f"{_money(c['price'])} {c['at']} — gamma and the price levels "
                   f"point at the same place.")
    if kind == "obstacle":
        word = "call wall" if d == 1 else "put wall"
        out.append(f"Gamma: the {_money(strike)} {word} sits between the "
                   f"{_money(trigger)} trigger and the {_money(target)} target "
                   f"— the move has to clear that shelf to get there.")
    elif kind == "shelf":
        out.append(f"Gamma: the biggest shelf in the path is {_money(strike)} "
                   f"at {_gex_size(gex)} of gamma — expect the move to slow "
                   f"or stick there.")
    elif kind == "air_pocket":
        out.append(f"Gamma: no shelf of size between the {_money(trigger)} trigger "
                   f"and the {_money(target)} target — thin air in between, so the "
                   f"path can move fast either way once it goes.")
    if gx.get("regime") == "short" and gx.get("flip") is not None:
        out.append(f"Gamma: spot is below the {_money(gx['flip'])} flip — short "
                   f"gamma, so breaks tend to accelerate once they go; the levels "
                   f"matter more, not less.")
    return out[:2]


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
    # Gamma context (Patch 173): facts ride on every card state; the composed
    # lines are added only for a live favored play, in the live branch below.
    # A wider ladder (top 4 call + top 4 put strikes) for gamma context only, so confluence and the path read more
    # than the 2+2 shelves the card's own levels use (same cached chain; the measured card is unchanged).
    try:
        sh_wide = gamma.shelves(sym, last, n=GAMMA_LADDER_N)
    except Exception:
        sh_wide = None
    out["gamma"] = _gamma_facts(sym, last, sh_wide or sh)

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
            if out["gamma"]:
                out["gamma"]["lines"] = _gamma_lines(out["gamma"], d, edge, target)
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
