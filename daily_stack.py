"""The Daily Stack (Patch 179): ten short world clips a day in a finite, full-screen vertical feed.
No likes, comments, counts or notifications; it ends with "That's today's stack."
Deterministic shuffle-bag (pool 30, 10 a day, 3-day cycles); the place is withheld until asked."""
from __future__ import annotations

import json
import random
from datetime import date
from pathlib import Path

_DIR = Path(__file__).resolve().parent
POOL: list[dict] = json.loads((_DIR / "daily_stack" / "clips.json").read_text(encoding="utf-8"))
MEDIA = _DIR / "static" / "stack"
K = 10


def _order(cycle: int) -> list[dict]:
    return random.Random(cycle).sample(POOL, len(POOL))


def _cycle_order(cycle: int) -> list[dict]:
    """The cycle's order, with its first block kept clear of the previous cycle's last block, so no clip ever
    shows on two days running across a cycle boundary. The last block is never moved, so "the previous cycle's
    last block" is just the tail of its plain shuffle — no recursion, the same answer on every instance."""
    order = _order(cycle)
    head, tail = order[:-K], order[-K:]
    prev_last = {c["id"] for c in _order(cycle - 1)[-K:]}
    first = [c for c in head if c["id"] not in prev_last][:K]       # head holds >= K such clips (|prev_last| = K)
    return first + [c for c in head if c not in first] + tail


def stack_for_date(d: date) -> list[dict]:
    c = max(1, len(POOL) // K)
    o = d.toordinal()
    cycle, pos = o // c, o % c
    return _cycle_order(cycle)[pos * K: pos * K + K]


def media_ready(stack: list[dict]) -> bool:
    return all((MEDIA / f"{c['id']}.mp4").is_file() and (MEDIA / f"{c['id']}.jpg").is_file() for c in stack)


def public_payload(d: date) -> dict:
    st = stack_for_date(d)
    return {"date": d.isoformat(), "count": len(st),
            "clips": [{"id": c["id"], "video": f"/stack-media/{c['id']}.mp4", "poster": f"/stack-media/{c['id']}.jpg",
                       "category": c["category"], "caption": c["caption"], "seconds": c.get("seconds"),
                       "has_place": bool(c.get("place"))} for c in st]}


def place_for(clip_id: str) -> str | None:
    for c in POOL:
        if c["id"] == clip_id:
            return c.get("place")
    return None
