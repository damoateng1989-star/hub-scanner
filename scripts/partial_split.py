"""Exit-rule experiments on the same breakouts (Patch 35 Step 0 and follow-ups). Measurement only: changes nothing.

Run on the server for real numbers:  python scripts/partial_split.py [SYMBOLS...]   (default: the board universe)

Every rule enters at the breakout close and works on 4H closes only (the product's closes-only rule):
  A  today          all out at the shelf; out on a close back inside the hub; out at the time limit
  B  half at shelf  (Patch 35) half out at the shelf, the rest rides with a breakeven stop
  C  BE at halfway  once a close gets halfway from entry to the shelf, the stop moves to entry (breakeven)
  D  half at halfway  half out on the first close halfway to the shelf, the rest goes for the shelf with a breakeven stop
A stop is filled at the close that breaks it (no intrabar fills), so a breakeven stop can still lose a little."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine   # noqa: E402
import scanner  # noqa: E402
import universe  # noqa: E402

HALF = float(os.getenv("HALFWAY", 0.5))     # "halfway" = this share of the way from entry to the shelf


def walk(close, i0, d, entry, edge, target, horizon, n, rule):
    """Return (pct, outcome) for one trade, or None if it's still open."""
    pct = lambda px: d * (px / entry - 1) * 100
    mid = entry + HALF * (target - entry)
    stop, banked, frac_left, reached_mid = edge, 0.0, 1.0, False
    end = min(i0 + 1 + horizon, n)
    for j in range(i0, end):
        c = close.iloc[j]
        if j > i0:
            if (c - stop) * d <= 0:                                          # stop broken on a close
                return banked + frac_left * pct(c), ("breakeven" if stop == entry else "snap-back")
        if (c - target) * d >= 0:                                            # shelf reached
            if rule == "B" and frac_left == 1.0 and j > i0:
                banked, frac_left, stop = 0.5 * pct(target), 0.5, entry       # half out, rest rides at breakeven
                continue
            if rule == "B" and frac_left < 1.0:
                continue                                                     # runner: only the stop or the clock end it
            return banked + frac_left * pct(target), "follow-through"
        if j > i0 and not reached_mid and (c - mid) * d >= 0:
            reached_mid = True
            if rule == "C":
                stop = entry
            elif rule == "D":
                banked, frac_left, stop = 0.5 * pct(c), 0.5, entry
    if i0 + horizon < n:
        return banked + frac_left * pct(close.iloc[i0 + horizon]), "stalled"
    return None


def summary(r):
    w, l = [x for x in r if x > 0], [x for x in r if x <= 0]
    avg = lambda a: round(float(sum(a) / len(a)), 2) if a else None
    return f"n={len(r):4}  avg {avg(r):+6.2f}%  win {round(100 * len(w) / len(r), 1):5}%  avg win {avg(w) or 0:+5.2f}%  avg loss {avg(l) or 0:+6.2f}%"


syms = sys.argv[1:] or universe.board_universe()
s = scanner.SETTINGS
rows = []           # one row per trade that is closed under every rule
for sym in syms:
    try:
        c4 = scanner.load_4h(sym, scanner.SCAN_DAYS)
        res = engine.analyze(c4, s)
    except Exception as e:
        print("skip", sym, e)
        continue
    close, n = res["close"], len(res["close"])
    for t in res["trades"]:
        if t["outcome"] == "pending" or t["chase"]:
            continue
        got = {r: walk(close, t["i0"], t["direction"], t["entry"], t["edge"], t["target"], s.horizon, n, r) for r in "ABCD"}
        if all(got.values()):
            rows.append(got)

print(f"{len(rows)} closed breakouts (chases left out, same trades in every row)\n")
for r, name in (("A", "A today"), ("B", "B half at shelf"), ("C", "C BE at halfway"), ("D", "D half at halfway")):
    print(f"{name:18} {summary([x[r][0] for x in rows])}")
saved = [x for x in rows if x["A"][1] == "snap-back" and x["C"][1] == "breakeven"]
print(f"\nC turned {len(saved)} snap-backs into breakeven exits; "
      f"it also cut short {sum(1 for x in rows if x['A'][1] == 'follow-through' and x['C'][1] == 'breakeven')} trades that would have reached the shelf.")
