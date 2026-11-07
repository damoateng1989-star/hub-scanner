"""Patch 35 Step 0: all-or-nothing (0) vs half-out-at-the-shelf (0.5), on the same breakouts.
Run on the server for real numbers: python scripts/partial_split.py [SYMBOLS...]  (default: the board universe)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine   # noqa: E402
import scanner  # noqa: E402
import universe  # noqa: E402


def rets(c4, res, frac, s):
    close, n, out = res["close"], len(res["close"]), []
    for t in res["trades"]:
        if t["outcome"] == "pending" or t["chase"]:
            continue
        i0, d, entry, target = t["i0"], t["direction"], t["entry"], t["target"]
        first = t["ret"]
        if t["outcome"] == "follow-through" and frac > 0:
            runner = None
            for k in range(t["oi"] + 1, min(i0 + 1 + s.horizon, n)):
                if (close.iloc[k] - entry) * d <= 0:
                    runner = 0.0
                    break
            else:
                if i0 + s.horizon < n:
                    runner = d * (close.iloc[i0 + s.horizon] / entry - 1) * 100
            if runner is None:
                continue          # runner still open: leave it out of both runs
            out.append((t["outcome"], first, frac * first + (1 - frac) * runner))
        else:
            out.append((t["outcome"], first, first))
    return out


def summary(rows, idx):
    r = [x[idx] for x in rows]
    w, l = [x for x in r if x > 0], [x for x in r if x <= 0]
    avg = lambda a: round(sum(a) / len(a), 2) if a else None
    return dict(n=len(r), avg=avg(r), win_rate=round(100 * len(w) / len(r), 1) if r else None, avg_win=avg(w), avg_loss=avg(l))


syms = sys.argv[1:] or universe.board_universe()
rows = []
for sym in syms:
    try:
        c4 = scanner.load_4h(sym, scanner.SCAN_DAYS)
        res = engine.analyze(c4, scanner.SETTINGS)
        rows += rets(c4, res, 0.5, scanner.SETTINGS)
    except Exception as e:
        print("skip", sym, e)
print("all-or-nothing (0):", summary(rows, 1))
print("half out (0.5):    ", summary(rows, 2))
changed = [x for x in rows if x[1] != x[2]]
print(f"trades whose return changed: {len(changed)}, all of them '{set(x[0] for x in changed) or '-'}'")
