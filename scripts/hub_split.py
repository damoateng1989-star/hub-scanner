"""Patches 27/28, Step 0: does a longer or wider hub follow through better? Run on the server: python scripts/hub_split.py
About 40-ish closed signals is directional, not proof."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db  # noqa: E402

sigs = [s for s in db.all_signals(5000, "hub") if s["outcome"] not in ("pending", None) and not s["chase"]]


def show(name, sub):
    done = [s for s in sub if s["ret_pct"] is not None]
    ft = sum(1 for s in done if s["outcome"] == "follow-through")
    print(f"{name:>22}: {len(done):3} closed, {ft:3} reached ({round(100 * ft / len(done), 1) if done else '–'}%), "
          f"avg {round(sum(s['ret_pct'] for s in done) / len(done), 2) if done else '–'}%")


show("2 sessions", [s for s in sigs if (s["hub_sessions"] or 0) <= 2])
show("3+ sessions", [s for s in sigs if (s["hub_sessions"] or 0) >= 3])
widths = sorted((s["hub_high"] - s["hub_low"]) / s["entry"] for s in sigs if s["entry"])
med = widths[len(widths) // 2] if widths else 0.02
show(f"wide hubs (>={med:.1%})", [s for s in sigs if s["entry"] and (s["hub_high"] - s["hub_low"]) / s["entry"] >= med])
show(f"narrow hubs (<{med:.1%})", [s for s in sigs if s["entry"] and (s["hub_high"] - s["hub_low"]) / s["entry"] < med])
show("graded warehouse", [s for s in sigs if s.get("grade") == "warehouse"])
show("graded ledge", [s for s in sigs if s.get("grade") == "ledge"])
