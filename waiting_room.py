"""The Waiting Room (Patch 178): one slow, muted scene from somewhere in the world each morning, with a
three-city guess. Entertainment with the product's discipline: no score, no streaks, no notifications.

The scene for a day is a deterministic shuffle-bag over the pool: no state, the same on every instance.
The public payload never carries the answer (city only appears as one of three options)."""
from __future__ import annotations

import json
import random
from datetime import date
from pathlib import Path

_DIR = Path(__file__).resolve().parent
POOL: list[dict] = json.loads((_DIR / "waiting_room" / "scenes.json").read_text(encoding="utf-8"))
MEDIA = _DIR / "static" / "waiting-room"


def _order(cycle: int) -> list[dict]:
    return random.Random(cycle).sample(POOL, len(POOL))


# Hand-picked days (date -> scene id); everything else follows the rotation.
OVERRIDES = {"2026-11-20": "istanbul"}


def scene_for_date(d: date) -> dict:
    pick = OVERRIDES.get(d.isoformat())
    if pick:
        for s in POOL:
            if s["id"] == pick:
                return s
    n = len(POOL)
    o = d.toordinal()
    cycle, pos = o // n, o % n
    order = _order(cycle)
    if n > 1 and order[0]["id"] == _order(cycle - 1)[-1]["id"]:     # never the same scene across a cycle boundary
        order[0], order[1] = order[1], order[0]
    return order[pos]


def media_ready(scene: dict) -> bool:
    """The card shows only when the scene's poster is on disk (the clips ship separately)."""
    return (MEDIA / f"{scene['id']}.webp").is_file()


def public_payload(d: date) -> dict:
    s = scene_for_date(d)
    opts = [s["city"], *s["decoys"][:2]]
    random.Random(d.toordinal()).shuffle(opts)
    has_video = (MEDIA / f"{s['id']}.mp4").is_file()
    return {"date": d.isoformat(), "headline": s["headline"], "hint": s["hint"],
            "video": f"/waiting-room/{s['id']}.mp4" if has_video else None,
            "poster": f"/waiting-room/{s['id']}.webp", "seconds": s.get("seconds"), "options": opts}


def reveal(d: date, choice: str) -> dict:
    s = scene_for_date(d)
    return {"correct": choice == s["city"], "city": s["city"], "country": s["country"], "tz": s["tz"],
            "scene_weather": s.get("scene_weather", ""), "caption": s.get("caption", ""), "credit": s.get("credit", "")}
