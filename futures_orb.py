"""Futures opening range (Nov 23): the owner-only live forward test of the one futures rule the Lab passed.

Rule (MNQ): the 1-hour opening range (9:30-10:30 ET, regular session). The first 30-minute bar that CLOSES outside
it before 2 PM is the entry (one trade a day). Stop at the other side of the range; target 1R. A bar that touches both
the stop and the target counts the stop. If neither is hit by the 4 PM close, the test closes it at the last price.

NextShelf alerts; the member manages the trade. Nothing is ordered. Every day is scored automatically (in index points
and dollars per contract after the Lab's round-trip cost), so the live record can be compared with the backtest.
Data: Massive 30-minute futures aggregates for the front contract (MASSIVE_API_KEY).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

import db
import futures_lab as fl

log = logging.getLogger("futures_orb")
NY = "America/New_York"
RULE = "1-hour range · enter on a 30-minute close outside · stop at the other side · target 1R"
BACKTEST_KEY = "60-close-far-1r-both"


def products() -> list[str]:
    return [p for p in os.getenv("FUTURES_ORB", "MNQ").upper().replace(" ", "").split(",") if p in fl.POINT_USD]


def init() -> None:
    with db._lock:
        db._conn.executescript("""
        CREATE TABLE IF NOT EXISTS fut_orb (
          day TEXT NOT NULL, product TEXT NOT NULL, contract TEXT,
          hi REAL, lo REAL, state TEXT NOT NULL DEFAULT 'building',   -- building|set|long|short|done|nobreak|nodata
          entry REAL, stop REAL, target REAL, entry_at TEXT,
          exit_price REAL, exit_at TEXT, how TEXT, pts REAL, net_usd REAL,
          alerted TEXT NOT NULL DEFAULT '', bars TEXT, updated_at TEXT,
          PRIMARY KEY (day, product));
        """)
        try:
            db._conn.execute("ALTER TABLE fut_orb ADD COLUMN trimmed INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass
        db._conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def front_contract(product: str, day: date) -> str:
    for c in fl.contracts(product, day - timedelta(days=120), day + timedelta(days=1)):
        if c["front_from"] <= day < c["roll"]:
            return c["ticker"]
    cs = fl.contracts(product, day, day + timedelta(days=100))
    return cs[0]["ticker"]


def today_bars(ticker: str, day: date) -> pd.DataFrame:
    """Today's 30-minute bars (ET index, bar START times), straight from Massive (no cache). Follows next_url, since a
    page can stop short of the whole day (a Monday-night check came back ending at 2 PM)."""
    url, params, rows = f"{fl.BASE}/futures/v1/aggs/{ticker}", {
        "resolution": "30min", "window_start.gte": (day - timedelta(days=1)).isoformat(), "window_start.lte": (day + timedelta(days=1)).isoformat(),
        "limit": 5000, "sort": "window_start.asc", "apiKey": fl._key()}, []
    for _ in range(6):
        r = requests.get(url, params=params, timeout=30)
        if r.status_code in (401, 403):
            raise RuntimeError(f"Massive refused the key ({r.status_code})")
        r.raise_for_status()
        j = r.json()
        rows += [(fl._ts(b.get("window_start") or b.get("timestamp") or b.get("t")), float(b["open"]), float(b["high"]),
                  float(b["low"]), float(b["close"])) for b in (j.get("results") or [])]
        nxt = j.get("next_url")
        if not nxt:
            break
        url, params = nxt, {"apiKey": fl._key()}
    df = pd.DataFrame(rows, columns=["t", "Open", "High", "Low", "Close"]).drop_duplicates("t").set_index("t").sort_index()
    if not df.empty:
        df.index = df.index.tz_convert(NY)
        df = df[df.index.date == day]
    return df


def clean(bars: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Bad prints (Nov 23): Massive's 9:30 MNQ bar on Nov 23 had wicks ~350 points past the whole day's high and low.
    A wick more than 3x the day's median bar range beyond the candle's body is treated as a bad print and trimmed to
    one median range past the body. Returns (bars, how many wicks were trimmed)."""
    if bars is None or len(bars) < 6:
        return bars, 0
    b = bars.copy()
    med = float((b["High"] - b["Low"]).median())
    if not med > 0:
        return b, 0
    top, bot = b[["Open", "Close"]].max(axis=1), b[["Open", "Close"]].min(axis=1)
    hi_bad, lo_bad = (b["High"] - top) > 3 * med, (bot - b["Low"]) > 3 * med
    b.loc[hi_bad, "High"] = top[hi_bad] + med
    b.loc[lo_bad, "Low"] = bot[lo_bad] - med
    return b, int(hi_bad.sum() + lo_bad.sum())


def _row(day: str, p: str) -> dict | None:
    r = db._run("SELECT * FROM fut_orb WHERE day=? AND product=?", (day, p)).fetchone()
    return dict(r) if r else None


def _save(day: str, p: str, **kw) -> None:
    kw["updated_at"] = _now()
    if not _row(day, p):
        db._run("INSERT INTO fut_orb (day, product) VALUES (?,?)", (day, p))
    db._run(f"UPDATE fut_orb SET {', '.join(f'{k}=?' for k in kw)} WHERE day=? AND product=?", (*kw.values(), day, p))
    db._conn.commit()


def _fmt(x: float) -> str:
    return f"{x:,.2f}" if x % 1 else f"{x:,.0f}"


def _alert(text: str) -> None:
    try:
        import notify
        notify.admins(text)
    except Exception as e:
        log.warning("futures alert failed: %s", e)


def step(p: str, bars: pd.DataFrame, row: dict, now: pd.Timestamp) -> tuple[dict, list[str]]:
    """Advance one product's day with the bars that have CLOSED by `now`. Pure: returns (changes, alerts)."""
    done = bars[bars.index + pd.Timedelta(minutes=30) <= now]
    t = done.index.strftime("%H:%M")
    rth = done[(t >= "09:30") & (t < "16:00")]
    ch, msgs = {}, []
    state = row.get("state") or "building"
    al = set((row.get("alerted") or "").split(",")) - {""}
    pv, cost = fl.POINT_USD[p], fl.cost_points(p)
    if state == "building":
        first = rth[rth.index.strftime("%H:%M").isin(["09:30", "10:00"])]
        if len(first) == 2:
            hi, lo = float(first["High"].max()), float(first["Low"].min())
            ch.update(hi=hi, lo=lo, state="set")
            state = "set"
            row = dict(row, hi=hi, lo=lo)
            if "range" not in al:
                msgs.append(f"{p} range set: {_fmt(lo)}–{_fmt(hi)} ({_fmt(hi - lo)} pts). Watching for a 30-minute close outside it until 2 PM.")
                al.add("range")
        elif now.strftime("%H:%M") >= "11:00":
            ch.update(state="nodata")
    if state == "set":
        hi, lo = row["hi"], row["lo"]
        after = rth[rth.index.strftime("%H:%M") >= "10:30"]
        for ts, b in after.iterrows():
            if ts.strftime("%H:%M") >= "14:00":
                ch.update(state="nobreak")
                state = "nobreak"
                break
            d = 1 if b["Close"] > hi else -1 if b["Close"] < lo else 0
            if d:
                entry = float(b["Close"])
                stop = lo if d == 1 else hi
                target = entry + d * abs(entry - stop)
                ch.update(state="long" if d == 1 else "short", entry=entry, stop=stop, target=target,
                          entry_at=str(ts + pd.Timedelta(minutes=30)))
                state = ch["state"]
                row = dict(row, **ch)
                if "entry" not in al:
                    risk = abs(entry - stop) * pv
                    msgs.append(f"{p} closed {'above' if d == 1 else 'below'} the range {'↑' if d == 1 else '↓'}. Entry ~{_fmt(entry)} · "
                                f"stop {_fmt(stop)} (other side) · target {_fmt(target)} (1R). Risk ~${risk:,.0f} per contract.")
                    al.add("entry")
                break
        else:
            if now.strftime("%H:%M") >= "14:00" and state == "set":
                ch.update(state="nobreak")
                state = "nobreak"
    if state in ("long", "short"):
        d = 1 if state == "long" else -1
        e_at = pd.Timestamp(row["entry_at"])
        later = rth[rth.index >= e_at]
        exit_px = how = x_at = None
        for ts, b in later.iterrows():
            if (b["Low"] <= row["stop"]) if d == 1 else (b["High"] >= row["stop"]):
                exit_px = min(row["stop"], float(b["Open"])) if d == 1 else max(row["stop"], float(b["Open"]))
                how, x_at = "stop", ts
                break
            if (b["High"] >= row["target"]) if d == 1 else (b["Low"] <= row["target"]):
                exit_px, how, x_at = row["target"], "target", ts
                break
        if exit_px is None and len(rth) and rth.index[-1].strftime("%H:%M") == "15:30":
            exit_px, how, x_at = float(rth["Close"].iloc[-1]), "close", rth.index[-1]
        if exit_px is not None:
            pts = (exit_px - row["entry"]) * d
            net = (pts - cost) * pv
            ch.update(state="done", exit_price=exit_px, how=how, exit_at=str(x_at), pts=round(pts, 2), net_usd=round(net, 2))
            if "exit" not in al:
                word = {"target": "hit the target ✓ +1R", "stop": "hit the stop", "close": "closed at 4 PM"}[how]
                msgs.append(f"{p} {word}: {pts:+,.2f} pts · {'+' if net >= 0 else '−'}${abs(net):,.2f} per contract after costs. That's today's one trade.")
                al.add("exit")
    ch["alerted"] = ",".join(sorted(al))
    return ch, msgs


def tick(now: pd.Timestamp | None = None, fetch=today_bars, force: bool = False) -> list[str]:
    if not fl.configured():
        return []
    init()
    now = now or pd.Timestamp.now(tz=NY)
    if now.weekday() >= 5:
        return []
    day = now.date()
    out = []
    for p in products():
        row = _row(day.isoformat(), p) or {}
        if force and row:            # "Check now": score today again from scratch (alerts already sent stay sent)
            row = dict(alerted=row.get("alerted") or "", contract=row.get("contract"))
            db._run("UPDATE fut_orb SET state='building', hi=NULL, lo=NULL, entry=NULL, stop=NULL, target=NULL, entry_at=NULL, "
                    "exit_price=NULL, exit_at=NULL, how=NULL, pts=NULL, net_usd=NULL WHERE day=? AND product=?", (day.isoformat(), p))
            db._conn.commit()
        if row.get("state") in ("done", "nobreak", "nodata"):
            continue
        try:
            tk = row.get("contract") or front_contract(p, day)
            bars = fetch(tk, day)
        except Exception as e:
            log.warning("futures orb fetch %s failed: %s", p, e)
            continue
        bars, trimmed = clean(bars)
        ch, msgs = step(p, bars, row, now)
        ch["trimmed"] = trimmed
        keep = bars[(bars.index.strftime("%H:%M") >= "09:30") & (bars.index.strftime("%H:%M") < "16:00")]
        ch["bars"] = json.dumps([[str(i)[11:16], round(float(r.Open), 2), round(float(r.High), 2), round(float(r.Low), 2), round(float(r.Close), 2)]
                                 for i, r in keep.iterrows()])
        _save(day.isoformat(), p, contract=tk, **ch)
        late = now.strftime("%H:%M") > "16:45"          # a check long after the close scores the day quietly
        for m in msgs:
            if not late:
                _alert(m)
        out += msgs
        if ch.get("state") == "done":
            close_diary(day.isoformat(), p)
    return out


# ---------------------------------------------------------------- the diary hookup: "I took it"
def take(user: dict, day: str, p: str, qty: float, entry: float | None = None) -> dict:
    import diary
    r = _row(day, p)
    if not r or r["state"] not in ("long", "short", "done") or not r.get("entry"):
        raise ValueError("There's no trade to log yet today")
    q = float(qty or 0)
    if not 0 < q <= 100:
        raise ValueError("Enter the number of contracts")
    e = float(entry) if entry else float(r["entry"])
    c = dict(user_id=user["id"], ticker=p, instrument="future", contract=r.get("contract"), side=1 if r["state"] == "long" or (r.get("target", 0) > r.get("entry", 0)) else -1,
             qty=q, entry_price=e, opened_at=diary._now(), fees=round(fl._fee_usd() * q, 2), setup="Opening range", timing="",
             plan=json.dumps(dict(edge=r["entry"], target=r["target"], kill=r["stop"])), source="lookout",
             src_key=f"orb:{day}:{p}", created_at=diary._now(), updated_at=diary._now())
    with db._lock:
        db._conn.execute(f"INSERT OR IGNORE INTO diary_trades ({','.join(c)}) VALUES ({','.join('?' * len(c))})", tuple(c.values()))
        db._conn.commit()
    if r["state"] == "done":
        close_diary(day, p)
    return dict(ok=True)


def close_diary(day: str, p: str) -> None:
    r = _row(day, p)
    if not r or r.get("exit_price") is None:
        return
    try:
        db._run("UPDATE diary_trades SET exit_price=?, closed_at=?, updated_at=? WHERE src_key=? AND exit_price IS NULL",
                (r["exit_price"], _now(), _now(), f"orb:{day}:{p}"))
        db._conn.commit()
    except Exception as e:
        log.warning("diary close skipped: %s", e)


# ---------------------------------------------------------------- what the app shows
def _stats(rows: list[dict]) -> dict:
    tr = [r for r in rows if r.get("net_usd") is not None]
    net = [r["net_usd"] for r in tr]
    wins, loss = [v for v in net if v > 0], [v for v in net if v <= 0]
    pf = sum(wins) / abs(sum(loss)) if loss and sum(loss) else None
    return dict(n=len(net), win=round(100 * len(wins) / len(net), 1) if net else None,
                avg=round(sum(net) / len(net), 2) if net else None, total=round(sum(net), 2),
                pf=round(pf, 2) if pf else None, days=len(rows))


def backtest(p: str) -> dict | None:
    for x in ((fl.status().get("results") or {}).get(p) or {}).get("orb") or []:
        if x.get("key") == BACKTEST_KEY:
            return x.get("net")
    return None


def view(user: dict | None = None) -> dict:
    init()
    today = pd.Timestamp.now(tz=NY).date().isoformat()
    out = dict(rule=RULE, configured=fl.configured(), products=[])
    for p in products():
        r = _row(today, p)
        if r:
            r["bars"] = json.loads(r.get("bars") or "[]")
            r.pop("alerted", None)
            if user:
                t = db._run("SELECT qty, entry_price FROM diary_trades WHERE user_id=? AND src_key=?", (user["id"], f"orb:{today}:{p}")).fetchone()
                r["taken"] = dict(t) if t else None
        hist = [dict(x) for x in db._run("SELECT day, product, contract, hi, lo, state, entry, stop, target, entry_at, exit_price, how, pts, net_usd "
                                          "FROM fut_orb WHERE product=? AND day<? ORDER BY day DESC LIMIT 60", (p, today)).fetchall()]
        allr = hist + ([r] if r and r.get("state") == "done" else [])
        out["products"].append(dict(product=p, name=fl.PRODUCTS.get(p, p), point_usd=fl.POINT_USD[p], cost_usd=round(fl.cost_points(p) * fl.POINT_USD[p], 2),
                                    today=r, history=hist[:30], stats=_stats(allr), backtest=backtest(p),
                                    since=min([x["day"] for x in allr], default=None)))
    return out
