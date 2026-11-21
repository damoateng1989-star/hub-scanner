"""Swing Play Card — Sika's read for a swing setup (Patch 152).

Display only. Reads the engine state, the options plan, the GEX shelves and
the regime; computes no new levels. The only grade it computes is the setup
grade /api/grade already computes (engine.setup_grade on the backtest trades).
No trading, no alert, no record entry, no Lab log.
"""
import json
import logging
import os
from datetime import date

import db
import engine
import flow_signal
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
    weekly = None
    try:       # the nearest expiry's walls (weeklies included), for confluence only; never labelled "next"
        nl = gamma.next_levels(sym, spot, fetch=False)
        mexp = (w or {}).get("expiry") or (sh or {}).get("expiry")
        if nl and nl.get("expiry_nt") and nl.get("expiry_nt") != (str(mexp) if mexp else None):
            weekly = dict(expiry=nl["expiry_nt"], call_wall=nl.get("call_wall_nt"), put_wall=nl.get("put_wall_nt"))
    except Exception:
        weekly = None
    try:       # gamma added up across the nearest 8 expiries: the strikes every expiry piles onto
        sm = gamma.summed(sym, spot, fetch=False)
    except Exception:
        sm = None
    return dict(expiry=(w or {}).get("expiry") or (sh or {}).get("expiry"),
                as_of=as_of, spot=spot, weekly=weekly, summed=sm,
                call_wall=(w or {}).get("call_wall"),
                put_wall=(w or {}).get("put_wall"),
                flip=flip, regime=regime, shelves=shelves,
                path=None, confluence=[], lines=[])


def _tol(price, atr=None):
    """How close counts as 'at' a price: a quarter of the stock's 4-hour ATR, kept between 0.4% and 1.5% of price,
    so a quiet KO and a jumpy SMCI are judged fairly. Without an ATR it falls back to the flat 1%."""
    if not price:
        return 0.0
    if not atr or atr <= 0:
        return _GAMMA_CONFLUENCE_PCT * price
    return min(max(0.25 * atr, 0.004 * price), 0.015 * price)


def _near(strike, price, tol=None):
    """Within the confluence tolerance of the play's price."""
    if not price:
        return False
    return abs(strike - price) <= (tol if tol is not None else _GAMMA_CONFLUENCE_PCT * price)


def _round_step(p):
    return 1 if p < 20 else 5 if p < 100 else 10 if p < 500 else 50 if p < 2000 else 100


def price_levels(c4):
    """Non-gamma levels price tends to react at, from the card's own candles: old highs and lows (daily swing
    pivots, last ~6 months), and the 50- and 200-day averages. Round numbers are matched on the fly."""
    try:
        d = engine.complete_days(c4)
    except Exception:
        return []
    if d is None or len(d) < 30:
        return []
    out = []
    h, l, c = d["High"].to_numpy(float), d["Low"].to_numpy(float), d["Close"].to_numpy(float)
    n, k = len(d), 3
    for i in range(max(k, n - 126), n - k):
        if h[i] == max(h[i - k:i + k + 1]):
            out.append(dict(price=float(h[i]), what="old high"))
        if l[i] == min(l[i - k:i + k + 1]):
            out.append(dict(price=float(l[i]), what="old low"))
    for p in (9, 21, 50, 200):                       # daily simple moving averages
        if n >= p:
            out.append(dict(price=float(c[-p:].mean()), what=f"daily {p}-SMA", kind="ma"))
    try:
        c4c = c4["Close"].to_numpy(float)
        for p in (9, 21, 50):                        # 4-hour simple moving averages
            if len(c4c) >= p:
                out.append(dict(price=float(c4c[-p:].mean()), what=f"4H {p}-SMA", kind="ma"))
    except Exception:
        pass
    return out


def _gamma_confluence(gx, d, trigger, target, atr=None, levels=None):
    """Levels that land on the play's own trigger or target (spec §5, widened Nov 19).

    For each of the target and the trigger, every kind of level within tolerance (_tol): the monthly directional
    wall, the weekly (nearest-expiry) wall, the nearest gamma shelf of real size (25%+ of the biggest), an old
    high or low, the 50/200-day average, and a round number. Returns a flat list of
    {at, price, strike, what, kind} (kind 'gamma' or 'price'), target first. A strike named here is agreement
    with the plan's own levels — never also counted as an obstacle or an in-path shelf (see _gamma_lines)."""
    gx = gx or {}
    out = []
    wall = gx.get("call_wall") if d == 1 else gx.get("put_wall")
    wall_word = "call wall" if d == 1 else "put wall"
    wk = gx.get("weekly") or {}
    wwall = wk.get("call_wall") if d == 1 else wk.get("put_wall")
    shelves = gx.get("shelves") or []
    biggest = max((abs(s["gex"]) for s in shelves), default=0.0)
    shelves = [s for s in shelves if biggest and abs(s["gex"]) >= _GAMMA_PATH_SHARE * biggest]
    for at, price in (("target", target), ("trigger", trigger)):
        if not price:
            continue
        tol = _tol(price, atr)
        hits, used = [], set()
        def add(strike, what, kind):
            key = round(float(strike), 2)
            if key in used:
                return
            used.add(key)
            hits.append(dict(at=at, price=price, strike=float(strike), what=what, kind=kind))
        if wall is not None and _near(wall, price, tol):
            add(wall, wall_word, "gamma")
        if wwall is not None and _near(wwall, price, tol):
            add(wwall, f"weekly {wall_word}", "gamma")
        near = [s for s in shelves if _near(s["strike"], price, tol)]
        if near:
            sv = min(near, key=lambda s: abs(s["strike"] - price))
            add(sv["strike"], "gamma shelf", "gamma")
        sn = [x for x in ((gx.get("summed") or {}).get("nodes") or []) if _near(x["strike"], price, tol)]
        if sn:
            x = min(sn, key=lambda x: abs(x["strike"] - price))
            add(x["strike"], "summed gamma node", "gamma")
        mas = [x for x in (levels or []) if x.get("kind") == "ma" and _near(x["price"], price, tol)]
        if mas:          # a cluster of moving averages counts once, named in full
            mean = sum(x["price"] for x in mas) / len(mas)
            hits.append(dict(at=at, price=price, strike=float(mean), what="moving averages", kind="price",
                             names=[x["what"] for x in mas], n=len(mas)))
        for kind_word in ("old high", "old low"):
            cands = [x for x in (levels or []) if x["what"] == kind_word and _near(x["price"], price, tol)]
            if cands:
                x = min(cands, key=lambda x: abs(x["price"] - price))
                add(x["price"], kind_word, "price")
        step = _round_step(price)
        r = round(price / step) * step
        if _near(r, price, tol):
            add(r, "round number", "price")
        out.extend(hits)
    return out


def _level_phrase(c):
    w = c["what"]
    if w == "moving averages":
        names = [n.replace("-SMA", "") for n in c.get("names") or []]
        if len(names) == 1:
            return f"the {c['names'][0]} at {_money(c['strike'])}"
        return f"{len(names)} moving averages ({_join(names)}) around {_money(c['strike'])}"
    if w == "summed gamma node":
        return f"the {_money(c['strike'])} gamma node (all expiries added up)"
    if w == "round number":
        return f"the ${c['strike']:,.0f} round number"
    if w in ("old high", "old low"):
        return f"an {w} at {_money(c['strike'])}"
    if w.endswith("average"):
        return f"the {w} at {_money(c['strike'])}"
    return f"the {_money(c['strike'])} {w}"


def _join(xs):
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def _confluence_line(conf, trigger, target):
    if not conf:
        return None
    by = {at: [c for c in conf if c["at"] == at] for at in ("trigger", "target")}
    if by["trigger"] and by["target"]:
        return (f"Confluence at both ends — the {_money(trigger)} trigger: {_join([_level_phrase(c) for c in by['trigger']])}; "
                f"the {_money(target)} target: {_join([_level_phrase(c) for c in by['target']])}.")
    at = "target" if by["target"] else "trigger"
    hits, price = by[at], (target if at == "target" else trigger)
    if len(hits) == 1:
        return (f"Confluence: {_level_phrase(hits[0])} sits at the {_money(price)} {at} — two kinds of level point "
                f"at the same place.")
    return f"Confluence: {len(hits)} levels agree at the {_money(price)} {at} — {_join([_level_phrase(c) for c in hits])}."


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


def _gamma_lines(gx, d, trigger, target, atr=None, levels=None):
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
    conf = _gamma_confluence(gx, d, trigger, target, atr, levels)
    gx["confluence"] = conf
    kind, strike, gex = _gamma_path(gx, d, trigger, target,
                                    {c["strike"] for c in conf if c["kind"] == "gamma"})
    gx["path"] = kind
    out = []
    line = _confluence_line(conf, trigger, target)
    if line:
        out.append(line)
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


def _flow_pct(x):
    """A record percentage the way the spec's lines print it: signed, two
    decimals, the app's minus sign (−0.10%, not -0.10%)."""
    return f"{x:+.2f}%".replace("-", "−")


def _flow_facts(sym, d, breakout_date):
    """The stored tape classification of this play's breakout (Patch 174;
    fail-open -> None).

    Reads the flow_signal row the post-close job wrote — never a live
    provider call, never a recomputation (the spec section 4 read-the-row
    rule: at card time the window has moved on, and a recomputation would
    not be the measurement that was stored), and never the flow_log proxy
    row. No row -> None, and the card carries no flow key at all."""
    if not breakout_date:
        return None
    try:
        row = db.flow_signal_for(sym, d, breakout_date)
    except Exception:
        row = None
    if not row:
        return None
    return dict(state=row["state"], breakout_date=row["breakout_date"],
                window=[row["window_start"], row["window_end"]],
                flow_net=row["flow_net"], threshold=row["threshold"],
                provider=row["provider"], as_of=row["as_of"],
                record=flow_signal.FLOW_RECORD, lines=[])


def _flow_lines(fx, d):
    """The card's flow line (Patch 174; the spec section 7 templates).

    Phase 1 composes at most one line, here in code — never by the model.
    'confirmed' states the classification and cites the measured record;
    'against' composes no line. Revised Nov 21 (Patch 182): the stacked ×
    flow test showed the state positive-but-unstable (+0.77%/PF 1.57 against
    the primary's −0.49%/PF 0.74), not reliably bad; the facts stay on the
    card for chat, the warning line goes. 'none' composes no line: it is the majority state, and
    a 'nothing happened' line on most plays is noise — the facts stay on
    the card for chat. The only variable in the wording is the net
    premium, in the app's M format (Patch 167, via _gex_size)."""
    rec = fx["record"]
    c, n, a = rec["confirmed"], rec["none"], rec["against"]
    net = _gex_size(fx["flow_net"])
    if fx["state"] == "confirmed":
        record = (f"Flow-confirmed retests measured {_flow_pct(c['avg_pct'])} per trade in the "
                  f"Flow Lab test ({c['n']} trades, PF {c['pf']:.2f}); retests without decisive "
                  f"flow measured {_flow_pct(n['avg_pct'])} ({n['n']} trades).")
        if d == 1:
            return [f"Flow: aggressive buying confirmed this breakout — {net} net aggressive-buy "
                    f"premium over the two sessions into the break. {record}"]
        return [f"Flow: aggressive put buying confirmed this breakdown — net aggressive-buy "
                f"premium of −{net} over the two sessions into the break. {record}"]
    return []


def tape_short_line(row, d):
    """Patch 182: the board card's (and the retest alert's) one-line form of a
    stored classification. Confirmed only — 'none' is the majority state and a
    nothing-happened line is noise; 'against' composes no line anywhere (see
    _flow_lines). Fact only, no record: the swing card carries the citation."""
    if not row or row.get("state") != "confirmed":
        return None
    net = _gex_size(row["flow_net"])          # already "$x.xxM", as in _flow_lines
    if d == 1:
        return (f"Tape flow: aggressive buying confirmed this breakout — "
                f"{net} net aggressive-buy premium over the two sessions into the break.")
    return (f"Tape flow: aggressive put buying confirmed this breakdown — "
            f"net aggressive-buy premium of −{net} over the two sessions into the break.")


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


def flow_allowed(user) -> bool:
    """Patch 174's visibility gate for the flow key (the user's Nov 19
    call: the business licence waits until he opens up to members).

    Env FLOW_CONFLUENCE_VISIBILITY:
      'internal' (the default) — admin accounts only: the feature is
        built and validated internally, on Standard, while the display
        licence waits for the member opening;
      'members' — every Pro user: the launch step, to be set only once
        the licence is in hand;
      'off' — nobody, not even admin.
    The two endpoints that serve the card and the chat pass the result
    into card()/answer() as include_flow; every other caller of card()
    gets the card exactly as before this patch."""
    mode = os.getenv("FLOW_CONFLUENCE_VISIBILITY", "internal").strip().lower()
    if mode == "members":
        return True
    if mode == "off":
        return False
    return bool(user and user.get("is_admin"))


def card(sym, include_flow=False):
    """Sika's swing-play read for one symbol. Raises ValueError when there is not
    enough price history (the endpoint turns it into a 400, like /api/options).
    include_flow (Patch 174) is passed only by the two endpoints, after swing_card.flow_allowed() has judged the
    caller; it defaults off so every other caller gets the card exactly as before."""
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
    bdate = None  # the live play's breakout date (Patch 174); read by hub plays only in phase 1
    state = st.get("state")
    if state in ("breakout_up", "breakdown"):
        d = 1 if state == "breakout_up" else -1
        edge = st["hub"]["high"] if d == 1 else st["hub"]["low"]
        live = (d, edge, st["target"], "Hub", "hub", st["hub"])
        # The breakout candle's New York date (Patch 174): the candle
        # st["retest"] counts back from (engine.current_status sets
        # candles_since = n-1-i0 for the pending trade) — the same
        # derivation the scanner's Patch 112 read uses.
        try:
            i0 = len(c4) - 1 - int(st["retest"]["candles_since"])
            bdate = c4.index[i0].tz_convert("America/New_York").date().isoformat()
        except Exception:
            bdate = None
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
            # A pattern row's breakout date rides on the row ('since' is
            # the breakout candle's timestamp — patterns.py's derivation).
            # Doubles and gap fades were not in the Flow Lab test and never
            # read it — the flow attach below is hub-only; this is here so
            # a hub row that ever arrives by this path is keyed correctly.
            bdate = str(lv.get("since") or "")[:10] or None

    if live:
        d, edge, target, setup_name, kind, th = live
        out["setup"] = setup_name
        g = _grade(sym, kind, d)
        out["grade"] = g.get("grade")
        out["state"] = "live"
        out["context"] = _context(op)
        # Flow confluence (Patch 174): the stored tape classification of
        # THIS play's breakout — hub plays only (the tested population),
        # and only for callers the visibility gate lets through. Facts
        # here; the composed line is added in the A/B branch below.
        if include_flow and kind == "hub" and bdate:
            fx = _flow_facts(sym, d, bdate)
            if fx is not None:
                out["flow"] = fx
        if g.get("grade") in ("A", "B"):
            out["favored"] = _side(d, edge, target, res, hub, S, atr, sh, op, regime, "favored")
            out["opposite"] = _opposite(d, edge, target, st, th, res, hub, S, atr, sh, op, regime)
            lv = price_levels(c4)
            if out["gamma"]:
                out["gamma"]["lines"] = _gamma_lines(out["gamma"], d, edge, target, atr, lv)
            else:      # no options chain: price-level confluence still counts
                conf = _gamma_confluence({}, d, edge, target, atr, lv)
                line = _confluence_line(conf, edge, target)
                out["levels"] = dict(confluence=conf, lines=[line] if line else [])
            if out.get("flow"):
                out["flow"]["lines"] = _flow_lines(out["flow"], d)
        else:
            # Spec §4.2: the favored side is only set for a live setup graded A or B.
            # A live setup graded C, D or "?" is watch only — the card says so instead of
            # presenting it as a favored play.
            move = 'breakout' if d == 1 else 'breakdown'
            if g.get("grade") in (None, "?"):      # never show a bare "?" to a person
                out["message"] = (f"Live {setup_name} {move}. This setup hasn't traded enough times on "
                                  f"{sym} to grade yet, so it's watch only, with no favored side.")
            else:
                word = str(g.get("word") or "").strip()
                out["message"] = (f"Live {setup_name} {move}, graded {g.get('grade')}"
                                  f"{f' ({word})' if word else ''}. Watch only, no favored side.")
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
