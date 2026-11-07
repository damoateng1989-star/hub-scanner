"""How many board names each cutoff would skip. Run on the server (needs the data keys): python scripts/liquidity.py"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import board      # noqa: E402
import data       # noqa: E402
import universe   # noqa: E402

syms = universe.board_universe()
dv = {}
for i in range(0, len(syms), 40):
    batch = syms[i:i + 40]
    bars = data.get_bars_many(batch, 40)
    for s in batch:
        v = board._dollar_volume_20d(bars.get(s))
        if v is not None:
            dv[s] = v
print(f"dollar volume known for {len(dv)}/{len(syms)} names")
for cut in (5e6, 10e6, 25e6, 50e6, 100e6):
    low = sorted(s for s, v in dv.items() if v < cut)
    print(f"below ${cut/1e6:.0f}M/day: {len(low)}  {' '.join(low[:15])}{' …' if len(low) > 15 else ''}")
try:
    caps = json.load(open(os.path.join(os.path.dirname(board.__file__), "market_caps.json")))["caps"]
    for cut in (10e9, 25e9, 50e9):
        print(f"below ${cut/1e9:.0f}B market cap: {sum(1 for v in caps.values() if v < cut)}")
except FileNotFoundError:
    print("market_caps.json missing: the market-cap filter is off (fails open)")
