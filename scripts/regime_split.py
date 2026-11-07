"""What the regime filter would have done to the closed hub record.

Run on the server (same env as the app): python scripts/regime_split.py
If "against the tape" isn't clearly worse than "with the trend", reconsider the filter."""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import board  # noqa: E402
import data   # noqa: E402
import db     # noqa: E402

bars = data.get_bars(board.REGIME_INDEX, 400)
daily = bars["Close"].groupby(bars.index.tz_convert("America/New_York").normalize()).last()
sma = daily.rolling(board.REGIME_DAYS).mean()
sigs = [s for s in db.all_signals(5000, "hub") if s["outcome"] not in ("pending", None) and not s["chase"]]
with_trend, against = [], []
for s in sigs:
    d = pd.Timestamp(s["breakout_time"]).tz_convert("America/New_York").normalize()
    prev = sma.index[sma.index < d]          # the regime as it stood before the breakout day
    if not len(prev) or pd.isna(sma.loc[prev[-1]]):
        continue
    k = prev[-1]
    (with_trend if (daily.loc[k] > sma.loc[k]) == (s["direction"] == 1) else against).append(s)
for name, sub in (("with the trend", with_trend), ("against the tape", against)):
    done = [s for s in sub if s["ret_pct"] is not None]
    ft = sum(1 for s in done if s["outcome"] == "follow-through")
    print(f"{name}: {len(done)} closed, {ft} reached the shelf "
          f"({round(100 * ft / len(done), 1) if done else '–'}%), "
          f"avg {round(sum(s['ret_pct'] for s in done) / len(done), 2) if done else '–'}%")
