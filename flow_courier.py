"""Flow courier — runs on the dev PC, beside the Theta Terminal.

Fetches the server's flow work list, pulls each needed day's options tape
from the LOCAL Theta Terminal, reduces it with flow_signal's own
aggregate_theta_day (the one computation — never a copy), and posts the
daily aggregates back. Raw tape never leaves this machine; only the
seven aggregate numbers per symbol-day cross the wire.

Env: NEXTSHELF_URL (default https://hub-scanner.onrender.com),
FLOW_INGEST_TOKEN (required), THETA_TERMINAL_URL (default the Terminal's
http://127.0.0.1:25510). DATA_DIR is pointed at a scratch folder before
the app imports: flow_signal imports db, which opens a local database
file at import — the courier never reads it.

Exit code is non-zero when anything failed, so a scheduled run that did
nothing shows as a failure, not a green check. A day the Terminal has no
data for (HTTP 472 inside the provider) simply isn't posted: the
breakout stays pending and a later run retries while it is still live
(the server's 10-day window) — a missing day is never zero-filled.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import date

os.environ.setdefault("DATA_DIR", os.path.join(os.path.expanduser("~"), ".flow-courier-data"))

import flow_signal  # noqa: E402  (after DATA_DIR is set, deliberately)

BASE = os.getenv("NEXTSHELF_URL", "https://hub-scanner.onrender.com").rstrip("/")
TOKEN = os.getenv("FLOW_INGEST_TOKEN", "")
CHUNK = 250


def _req(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"X-Flow-Token": TOKEN,
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def main() -> int:
    if not TOKEN:
        print("FLOW_INGEST_TOKEN is not set — nothing to do, and that is a failure.")
        return 2
    try:
        work = _req("GET", "/api/flow/pending")
    except Exception as e:
        print(f"could not read the work list: {e}")
        return 2
    pending = work.get("pending") or []
    if not pending:
        print("nothing pending — every live breakout is classified.")
        return 0
    provider = flow_signal.ThetaTerminalProvider()
    rows: list[dict] = []
    failures = 0
    for item in pending:
        sym = item["symbol"]
        needed = item.get("days_needed") or []
        if not needed:
            print(f"  {sym}: all days stored, still unclassified "
                  f"(a day had no data) — stays pending by design")
            continue
        got = 0
        for iso in needed:
            agg = provider.daily_aggregate(sym, date.fromisoformat(iso))
            if agg is None:
                print(f"  {sym} {iso}: no aggregate (no data or pull failed) — left unposted")
                failures += 1
                continue
            rows.append(dict(symbol=sym, day=iso, provider="theta_terminal", **agg))
            got += 1
        print(f"  {sym}: pulled {got}/{len(needed)} day(s)")
    stored = classified = 0
    for i in range(0, len(rows), CHUNK):
        try:
            res = _req("POST", "/api/flow/aggregates", {"rows": rows[i:i + CHUNK]})
            stored += res.get("stored", 0)
            classified += res.get("classified", 0)
        except Exception as e:
            failures += 1
            print(f"POST chunk at {i} failed: {e}")
    print(f"done: {len(pending)} pending breakout(s), {stored} day(s) stored, "
          f"{classified} classification(s) written, {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
