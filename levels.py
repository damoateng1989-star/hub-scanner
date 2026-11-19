"""Levels to watch: the strongest stacked price levels near the current price, for the shelf card and Sika.

Every kind of level NextShelf knows is gathered — the hub's edges and triggers, options walls (monthly and
weekly), gamma shelves of real size, gamma added up across the nearest 8 expiries, old highs and lows, 4H/daily
moving averages and round numbers — then levels that sit within a quarter of the stock's 4H ATR of each other
are merged into one stack. The stacks with the most distinct kinds of level win: up to 3 above price and 3
below. Information only: it changes no trigger, target, stop, grade or alert, and makes no claim that a stack
will hold (the Lab's Confluence check measures that).
"""
from __future__ import annotations

import logging
import threading
import time

import engine
import gamma
import swing_card

log = logging.getLogger("levels")
BAND = 0.08            # look within ±8% of price
_CACHE: dict = {}
_LOCK = threading.Lock()
TTL = 300

GROUP = {"hub": "the hub", "wall": "options wall", "shelf": "gamma", "summed": "gamma", "high": "old high/low",
         "ma": "moving averages", "round": "round number"}


def _cands(sym, c4, res, st, last, with_gamma):
    out = []
    add = lambda p, what, grp: out.append(dict(price=float(p), what=what, group=grp)) if p else None
    h = res["hubs"][-1] if res.get("hubs") else None
    if h and h.get("active", True):
        add(h["low"], "hub low", "hub")
        add(h["high"], "hub high", "hub")
    if st.get("trigger_up"):
        add(st["trigger_up"], "long trigger", "hub")
    if st.get("trigger_down"):
        add(st["trigger_down"], "short trigger", "hub")
    for x in swing_card.price_levels(c4):
        add(x["price"], x["what"], "ma" if x.get("kind") == "ma" else "high")
    step = swing_card._round_step(last)
    k0 = int(last * (1 - BAND) // step)
    for k in range(k0, k0 + int(2 * BAND * last / step) + 3):
        add(k * step, f"${k * step:,.0f} round number", "round")
    if with_gamma:
        try:
            w = gamma.walls(sym, last)
            if w:
                add(w.get("call_wall"), "call wall", "wall")
                add(w.get("put_wall"), "put wall", "wall")
            nl = gamma.next_levels(sym, last, fetch=False)
            if nl:
                add(nl.get("call_wall_nt"), "weekly call wall", "wall")
                add(nl.get("put_wall_nt"), "weekly put wall", "wall")
            sh = gamma.shelves(sym, last, fetch=False, n=swing_card.GAMMA_LADDER_N)
            rows = (sh or {}).get("shelves") or []
            big = max((abs(r["gex"]) for r in rows), default=0.0)
            for r in rows:
                if big and abs(r["gex"]) >= 0.25 * big:
                    add(r["strike"], f"gamma shelf {abs(r['gex']) / 1e6:.2f}M", "shelf")
            sm = gamma.summed(sym, last, fetch=False)
            for n in (sm or {}).get("nodes") or []:
                chg = n.get("change")
                ch = "" if chg is None else f", net {'+' if chg >= 0 else '−'}{abs(chg) / 1e6:.2f}M since last session"
                add(n["strike"], f"gamma node {abs(n['gex']) / 1e6:.2f}M (all expiries{ch})", "summed")
        except Exception as e:
            log.warning("levels gamma %s: %s", sym, e)
    return [c for c in out if abs(c["price"] - last) <= BAND * last]


def _stacks(cands, last, tol):
    cands = sorted(cands, key=lambda c: c["price"])
    stacks, cur = [], []
    for c in cands:
        if cur and c["price"] - cur[0]["price"] > 2 * tol:
            stacks.append(cur)
            cur = []
        cur.append(c)
    if cur:
        stacks.append(cur)
    out = []
    for s in stacks:
        import stacked
        groups = {c["group"] for c in s}
        if not stacked.kinds_of(s):
            continue                                  # round numbers alone, or a single moving average, are not levels to watch
        anchor = next((c for g in ("wall", "summed", "hub", "shelf", "high") for c in s if c["group"] == g), None)
        price = anchor["price"] if anchor else sum(c["price"] for c in s) / len(s)
        kinds = len(stacked.kinds_of(s))           # same counting as the Stacked Shelf: no round numbers, MAs only as a cluster
        score = kinds + (1 if groups & {"wall", "summed"} else 0) + (0.5 if "hub" in groups else 0)
        seen, items = set(), []
        for c in sorted(s, key=lambda c: ("wall", "summed", "shelf", "hub", "high", "ma", "round").index(c["group"])):
            label = c["what"] if c["group"] != "round" else "round number"
            if label in seen:
                continue
            seen.add(label)
            items.append(dict(what=label, price=round(c["price"], 2), group=c["group"]))
        plan = None
        if groups & {"wall", "summed"}:      # a plan for each big gamma zone: where it counts as cleared or as holding
            hi, lo = round(price + tol, 2), round(price - tol, 2)
            plan = (f"Clears on a 4H close above ${hi:,.2f} · rejected while closes stay under ${lo:,.2f}" if price > last
                    else f"Breaks on a 4H close below ${lo:,.2f} · holds while closes stay above ${hi:,.2f}")
        out.append(dict(plan=plan, price=round(price, 2), side="above" if price > last else "below",
                        distance_pct=round((price / last - 1) * 100, 2), kinds=kinds, score=score,
                        gamma=bool(groups & {"wall", "shelf", "summed"}), items=items))
    return out


def compute(sym: str, with_gamma: bool = True) -> dict:
    import scanner
    sym = scanner.clean_symbol(sym)
    key = (sym, with_gamma)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < TTL:
            return hit[1]
    c4 = scanner.load_4h(sym, scanner.SCAN_DAYS)
    if len(c4) < 40:
        raise ValueError("not enough price history")
    res = engine.analyze(c4, scanner.SETTINGS)
    st = engine.current_status(c4, res, scanner.SETTINGS)
    last = float(res["close"].iloc[-1])
    atr = float(res["atr"].iloc[-1])
    tol = swing_card._tol(last, atr)
    stacks = _stacks(_cands(sym, c4, res, st, last, with_gamma), last, tol)
    pick = lambda side: sorted(sorted([s for s in stacks if s["side"] == side], key=lambda s: (-s["score"], abs(s["distance_pct"])))[:3],
                               key=lambda s: -s["price"])
    movers, since = [], None
    if with_gamma:
        try:
            sm = gamma.summed(sym, last, fetch=False)
            movers, since = (sm or {}).get("movers") or [], (sm or {}).get("since")
        except Exception:
            pass
    stk = None
    try:       # the Stacked Shelf checklist, for a live hub breakout
        import stacked
        if st.get("state") in ("breakout_up", "breakdown") and st.get("target"):
            d = 1 if st.get("direction", "up") == "up" else -1
            h = res["hubs"][-1] if res.get("hubs") else None
            edge = (st.get("retest") or {}).get("edge") or (h and (h["high"] if d == 1 else h["low"]))
            if edge:
                own = {round(x, 2) for x in (h and (h["high"], h["low"]) or ()) } | {round(float(st.get(k) or 0), 2) for k in ("trigger_up", "trigger_down")}
                # the same price levels the Lab test uses (no pivots from inside this hub, older hubs only if well
                # before it), plus the gamma levels for Pro
                cands = stacked.History(c4, res).at(len(c4) - 1, own_hub=h, price=last)
                cands = [c for c in cands if round(c["price"], 2) not in own and abs(c["price"] - last) <= BAND * last]
                cands += [c for c in _cands(sym, c4, res, st, last, with_gamma) if c["group"] in ("wall", "shelf", "summed")]
                chk = stacked.check(cands, d, float(edge), last, float(st["target"]), tol)
                rules = [("Hub breakout confirmed by a 4H close", True),
                         ("Trigger stacked: " + (", ".join(stacked.KIND.get(k, k) for k in chk["trig_kinds"]) or "nothing else on the hub edge"), chk["trig_stacked"]),
                         ("Target on a stack: " + (f"nearest stack ${chk['stack_target']:,.2f}" if chk["stack_target"] else "no stack beyond the entry")
                          + (f"; the ${float(st['target']):,.2f} shelf has {', '.join(stacked.KIND.get(k, k) for k in chk['tgt_kinds'])} on it" if chk["target_stacked"] else ""),
                          chk["target_stacked"] or chk["stack_target"] is not None),
                         ("Clear path to the target" if chk["clear_path"] else "Stack in the way at " + ", ".join(f"${p:,.2f}" for p in chk["blocking"]), chk["clear_path"])]
                stk = dict(name="Stacked Shelf", ok=chk["stacked"], passed=sum(1 for _, v in rules if v), total=len(rules),
                           rules=[dict(text=t, ok=bool(v)) for t, v in rules], stack_target=chk["stack_target"], direction=d)
        if stk is None:      # a live post-earnings gap fade & rip: the Stacked Gap Fade checklist
            import patterns
            gp = {"trades": [], "live": []}
            patterns._gapfade(c4, res["close"], len(c4), scanner.SETTINGS, gp)
            lv = next((x for x in gp["live"] if x.get("state") == "breakout_up" and x.get("trade")), None)
            if lv:
                t = lv["trade"]
                cands = stacked.History(c4, res).at(len(c4) - 1, own_hub=t["hub"], price=last)
                cands = [c for c in cands if abs(c["price"] - last) <= BAND * last]
                cands += [c for c in _cands(sym, c4, res, st, last, with_gamma) if c["group"] in ("wall", "shelf", "summed")]
                chk = stacked.check(cands, 1, float(t["edge"]), last, float(t["target"]), tol)
                rules = [("Gap fade & rip confirmed: a daily close back above the gap-day high", True),
                         ("Gap-day high stacked: " + (", ".join(stacked.KIND.get(k, k) for k in chk["trig_kinds"]) or "nothing else on it"), chk["trig_stacked"]),
                         ("Target on a stack: " + (f"nearest stack ${chk['stack_target']:,.2f}" if chk["stack_target"] else "no stack beyond the entry")
                          + (f"; the ${float(t['target']):,.2f} 2R target has {', '.join(stacked.KIND.get(k, k) for k in chk['tgt_kinds'])} on it" if chk["target_stacked"] else ""),
                          chk["target_stacked"] or chk["stack_target"] is not None),
                         ("Clear path to the 2R target" if chk["clear_path"] else "Stack in the way at " + ", ".join(f"${p:,.2f}" for p in chk["blocking"]), chk["clear_path"])]
                stk = dict(name="Stacked Gap Fade", ok=chk["stacked"], passed=sum(1 for _, v in rules if v), total=len(rules),
                           rules=[dict(text=t_, ok=bool(v)) for t_, v in rules], stack_target=chk["stack_target"], direction=1)
    except Exception as e:
        log.warning("stacked shelf %s: %s", sym, e)
    out = dict(symbol=sym, price=round(last, 2), tolerance=round(tol, 2), gamma=with_gamma, stacked=stk,
               above=pick("above"), below=pick("below"), movers=movers, since=since)
    with _LOCK:
        _CACHE[key] = (time.time(), out)
    return out
