"""The Stacked Shelf: a hub retest whose levels are backed by other kinds of level (Nov 19).

Rules (all must hold for a Stacked Shelf):
  1. Structure — a hub breakout confirmed by a 4H close, then the retest entry (the existing setup).
  2. Stacked trigger — at least one other kind of level within tolerance of the hub edge.
  3. Stacked target — aim at the nearest stack (2+ kinds) beyond the entry; failing that the shelf, if the shelf
     itself has a level on it.
  4. Clear path — no stack of 2+ kinds between the trigger and the target.
  5. Wrong if — a 4H close back through the stacked zone (the existing retest stop).
Tolerance = a quarter of the 4H ATR, kept between 0.4% and 1.5% of price. Round numbers never count on their own,
and moving averages count only as a cluster of two or more.

Price-only levels (old highs/lows, 4H/daily moving averages, round numbers, older hub edges) are rebuilt from past
candles with no look-ahead, so the Lab can test the setup over years. Gamma levels join on the live card only
(option chains aren't stored historically); the confluence log measures them going forward.
"""
from __future__ import annotations

import numpy as np

KIND = {"high": "old high/low", "ma": "moving averages", "round": "round number", "hub": "older hub",
        "wall": "options wall", "shelf": "gamma", "summed": "gamma"}


def tol(price: float, atr: float | None) -> float:
    if not price:
        return 0.0
    if not atr or atr != atr or atr <= 0:
        return 0.01 * price
    return min(max(0.25 * atr, 0.004 * price), 0.015 * price)


def round_step(p: float) -> float:
    return 1 if p < 20 else 5 if p < 100 else 10 if p < 500 else 50 if p < 2000 else 100


def _kind(c):
    return "gamma" if c["group"] in ("shelf", "summed") else c["group"]


def kinds_of(items) -> set:
    """The kinds of level that count toward a stack. Round numbers never count on their own (there is one every few
    dollars), and moving averages count only as a cluster of two or more (one average is almost always nearby)."""
    ks = {_kind(c) for c in items if c["group"] not in ("round", "ma")}
    if sum(1 for c in items if c["group"] == "ma") >= 2:
        ks.add("ma")
    return ks


def clusters(cands: list[dict], tl: float) -> list[dict]:
    """Merge levels within 2×tol of the first level in the group; each cluster: price (anchor), kinds, items."""
    cs = sorted(cands, key=lambda c: c["price"])
    out, cur = [], []
    for c in cs:
        if cur and c["price"] - cur[0]["price"] > 2 * tl:
            out.append(cur)
            cur = []
        cur.append(c)
    if cur:
        out.append(cur)
    res = []
    for g in out:
        anchor = next((c for k in ("wall", "summed", "hub", "shelf", "high", "ma", "round") for c in g if c["group"] == k), g[0])
        res.append(dict(price=anchor["price"], kinds=len(kinds_of(g)), items=g))
    return res


def check(cands: list[dict], d: int, trigger: float, entry: float, target: float, tl: float) -> dict:
    """The Stacked Shelf rules 2-4 for one play. cands are levels OTHER than this hub's own edges."""
    near = lambda p, x: abs(p - x) <= tl
    trig_kinds = kinds_of([c for c in cands if near(c["price"], trigger)])
    tgt_kinds = kinds_of([c for c in cands if near(c["price"], target) and c["group"] != "hub"])   # the shelf IS an older hub
    beyond = [c for c in cands if d * (c["price"] - (entry + d * tl)) > 0]
    stacks = [s for s in clusters(beyond, tl) if s["kinds"] >= 2]
    stack_target = min((s["price"] for s in stacks), key=lambda p: abs(p - entry)) if stacks else None
    # clear path: no 2+ stack strictly between the trigger zone and the shelf target's zone
    lo, hi = sorted((trigger + d * tl, target - d * tl))
    blocking = [s for s in stacks if lo < s["price"] < hi]
    out = dict(trig_stacked=bool(trig_kinds), trig_kinds=sorted(trig_kinds), target_stacked=bool(tgt_kinds),
               tgt_kinds=sorted(tgt_kinds), stack_target=stack_target, clear_path=not blocking,
               blocking=[round(s["price"], 2) for s in blocking[:3]])
    out["stacked"] = out["trig_stacked"] and out["clear_path"] and (out["target_stacked"] or stack_target is not None)
    return out


# ---------------------------------------------------------------- price-only levels with no look-ahead (Lab)
class History:
    """Precomputed so each trade's levels at bar j are cheap: daily pivots with their confirmation day, rolling
    daily and 4H averages, and the hubs that had already ended."""

    def __init__(self, c4, res):
        import engine
        self.idx = c4.index
        self.c4close = c4["Close"].to_numpy(float)
        d = engine.complete_days(c4)
        self.dday = d.index.tz_convert(engine.NY).normalize() if len(d) else d.index
        h, l, c = d["High"].to_numpy(float), d["Low"].to_numpy(float), d["Close"].to_numpy(float)
        k, n = 3, len(d)
        self.piv = []                                       # (confirmed_at_day_index, price, label)
        for i in range(k, n - k):
            if h[i] == h[i - k:i + k + 1].max():
                self.piv.append((i + k, float(h[i]), "old high"))
            if l[i] == l[i - k:i + k + 1].min():
                self.piv.append((i + k, float(l[i]), "old low"))
        self.dcum = np.concatenate([[0.0], np.cumsum(c)])
        self.c4cum = np.concatenate([[0.0], np.cumsum(self.c4close)])
        self.hubs = res.get("hubs") or []
        import engine as _e
        self._ny = _e.NY

    def at(self, j: int, own_hub=None, price: float | None = None) -> list[dict]:
        t = self.idx[j].tz_convert(self._ny).normalize()
        di = int(np.searchsorted(self.dday, t, side="left"))     # daily bars strictly before bar j's day
        out = []
        own = None          # the hub's own highs/lows are its edges, not a second kind of level: skip pivots inside it
        if own_hub is not None and own_hub.get("start_i") is not None:
            a0 = int(np.searchsorted(self.dday, self.idx[own_hub["start_i"]].tz_convert(self._ny).normalize(), side="left"))
            a1 = int(np.searchsorted(self.dday, self.idx[min(own_hub.get("end_i", j), j)].tz_convert(self._ny).normalize(), side="right"))
            own = (a0 - 1, a1 + 1)
        for conf, p, lab in self.piv:
            if own and own[0] <= conf - 3 <= own[1]:
                continue
            if conf < di and conf >= di - 130:
                out.append(dict(price=p, what=lab, group="high"))
        for n in (9, 21, 50, 200):
            if di >= n:
                out.append(dict(price=(self.dcum[di] - self.dcum[di - n]) / n, what=f"daily {n}-SMA", group="ma"))
        for n in (9, 21, 50):
            if j + 1 >= n:
                out.append(dict(price=(self.c4cum[j + 1] - self.c4cum[j + 1 - n]) / n, what=f"4H {n}-SMA", group="ma"))
        own_start = own_hub.get("start_i", j) if own_hub is not None else j
        for h in self.hubs:      # an older hub counts only if it ended well before this one began (not a piece of it)
            if h is own_hub or h.get("end_i", 10 ** 9) >= own_start - 20:
                continue
            out.append(dict(price=float(h["high"]), what="older hub high", group="hub"))
            out.append(dict(price=float(h["low"]), what="older hub low", group="hub"))
        if price:
            step = round_step(price)
            base = round(price / step) * step
            for m in range(-12, 13):
                out.append(dict(price=base + m * step, what="round number", group="round"))
        return out
