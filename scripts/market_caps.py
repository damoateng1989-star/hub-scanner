"""Regenerate market_caps.json from the stockanalysis.com index list pages.

Run from the repo root: python scripts/market_caps.py
If it prints far fewer than ~500 names, the page layout changed: fix the parser, don't ship a thin snapshot."""
import json
import re
from datetime import date
from urllib.request import Request, urlopen

URLS = ["https://stockanalysis.com/list/sp-500-stocks/",
        "https://stockanalysis.com/list/nasdaq-100-stocks/"]
HDRS = {"User-Agent": "Mozilla/5.0 (NextShelf snapshot refresh)"}


def parse_mc(s):
    m = re.match(r"^([\d.]+)\s*([TBMK])$", s.strip().upper())
    if not m:
        return None
    return round(float(m.group(1)) * {"T": 1e12, "B": 1e9, "M": 1e6, "K": 1e3}[m.group(2)])


caps = {}
for url in URLS:
    src = urlopen(Request(url, headers=HDRS), timeout=30).read().decode("utf-8", "ignore")
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", src, re.S)[1:]:
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) < 7:
            continue
        mc = parse_mc(cells[6])
        if cells[1] and mc:
            caps[cells[1].upper()] = mc

out = {"as_of": str(date.today()), "source": "stockanalysis.com list pages (S&P 500 + Nasdaq-100)", "caps": caps}
with open("market_caps.json", "w") as f:
    json.dump(out, f, indent=1, sort_keys=True)
print(f"wrote market_caps.json: {len(caps)} names, as of {out['as_of']}")
