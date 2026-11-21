"""The Screening Room (Patch 195): full-length public-domain films, played from archive.org's own embed.

NextShelf hosts the catalog, the posters and the page; archive.org hosts and streams the film, so a member
watching a two-hour film never touches this server. One featured film a day for everyone (a seeded cycle
across both shelves, like the Waiting Room's date key), plus the catalog as two finite rails.

The rights gate: a film is listed only with an archive.org identifier AND a stated public-domain basis
(copied from the curation file, pd-stack/candidates.md). An entry missing either is dropped at load —
no basis, no film.
"""
from __future__ import annotations

import json
import random
from datetime import date
from pathlib import Path

_DIR = Path(__file__).resolve().parent
POSTERS = _DIR / "static" / "cinema"
VEINS = ("First Frames", "The Pits", "Old Wall Street", "Money School", "Booms & Busts", "Classics")
SHELVES = (("market", "Market History"), ("classics", "Classics"))


def _load() -> list[dict]:
    try:
        raw = json.loads((_DIR / "cinema" / "films.json").read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for f in raw if isinstance(raw, list) else []:
        if not (isinstance(f, dict) and str(f.get("archive_id") or "").strip() and str(f.get("pd_basis") or "").strip()
                and f.get("title") and f.get("vein") in VEINS):
            continue                                   # the gate: no identifier or no public-domain basis, no film
        f = dict(f)
        f["id"] = f.get("id") or f["archive_id"]
        f["shelf"] = "classics" if f["vein"] == "Classics" else "market"
        out.append(f)
    return out


FILMS: list[dict] = _load()


def _cycle(n: int) -> list[dict]:
    """One pass through the whole catalog, in a fixed shuffle per cycle; the first film never repeats the previous
    cycle's last, so no film is featured two days running across a cycle boundary."""
    order = random.Random(f"cinema-{n}").sample(FILMS, len(FILMS))
    if len(order) > 1:
        prev = random.Random(f"cinema-{n - 1}").sample(FILMS, len(FILMS))[-1]
        if order[0]["id"] == prev["id"]:
            order = order[1:] + order[:1]
    return order


def feature_for(d: date) -> dict | None:
    if not FILMS:
        return None
    n = len(FILMS)
    o = d.toordinal()
    return _cycle(o // n)[o % n]


def _poster(f: dict) -> str:
    p = f.get("poster")
    if p and (POSTERS / p).is_file():
        return f"/cinema-media/{p}"                     # self-hosted, cut from the film
    return f"https://archive.org/services/img/{f['archive_id']}"   # until one is cut: archive.org's own item image


def _public(f: dict) -> dict:
    return dict(id=f["id"], title=f["title"], year=f.get("year"), runtime=f.get("runtime"), vein=f["vein"],
                shelf=f["shelf"], description=f.get("description") or "", poster=_poster(f),
                embed=f"https://archive.org/embed/{f['archive_id']}", basis=f["pd_basis"])


def payload(d: date) -> dict:
    feat = feature_for(d)
    return {"date": d.isoformat(), "count": len(FILMS),
            "feature": _public(feat) if feat else None,
            "shelves": [{"key": k, "title": t, "films": [_public(f) for f in FILMS if f["shelf"] == k]}
                        for k, t in SHELVES]}


def poster_names() -> set[str]:
    return {f["poster"] for f in FILMS if f.get("poster")}
