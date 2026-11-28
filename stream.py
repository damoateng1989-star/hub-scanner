"""Live trades, tick by tick: one websocket to Alpaca, fanned out to every open app over Server-Sent Events.

The server holds a single connection to Alpaca's stream (the free plan allows one) and subscribes to the
symbols that open apps are watching. It tries the full-market feed (sip, Algo Trader Plus) first and drops
to the free IEX feed when the account isn't subscribed; the free feed is capped at 30 symbols, so the most
recently opened ones win. Every trade also refreshes data._LIVE, so /api/live and the trigger alerts see it.

With the demo data provider there is no Alpaca, so a random walk stands in for the tape (local testing).
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import random
import time

import data
import darkpool

log = logging.getLogger("stream")
URL = "wss://stream.data.alpaca.markets/v2/{feed}"
IEX_CAP = 30
IDLE_CLOSE_S = 120            # nobody watching for this long: hang up on Alpaca


class _Hub:
    def __init__(self):
        self.clients: dict[int, tuple[asyncio.Queue, list[str]]] = {}
        self.ids = itertools.count(1)
        self.feed: str | None = None          # the feed that worked: "sip" or "iex"
        self.status = "idle"                  # idle | connecting | live | error: …
        self.task: asyncio.Task | None = None
        self.changed: asyncio.Event | None = None
        self.subscribed: set[str] = set()
        self.extra: set[str] = set()          # Nov 28: watchlist kept subscribed in market hours for dark pool prints

    # ---------------------------------------------------------------- clients
    def register(self, symbols: list[str]) -> tuple[int, asyncio.Queue]:
        cid, q = next(self.ids), asyncio.Queue(maxsize=400)
        self.clients[cid] = (q, symbols)
        self._ensure_running()
        self.changed.set()
        return cid, q

    def unregister(self, cid: int) -> None:
        self.clients.pop(cid, None)
        if self.changed:
            self.changed.set()

    def wanted(self) -> list[str]:
        """Symbols open apps want, newest client first (so the free 30-symbol cap favours what was just opened)."""
        out: dict[str, None] = {}
        for cid in sorted(self.clients, reverse=True):
            for s in self.clients[cid][1]:
                out.setdefault(s, None)
        for s in sorted(self.extra):
            out.setdefault(s, None)
        syms = list(out)
        return syms[:IEX_CAP] if self.feed == "iex" else syms

    def _ensure_running(self) -> None:
        if self.changed is None:
            self.changed = asyncio.Event()
        if self.task is None or self.task.done():
            self.task = asyncio.get_running_loop().create_task(self._run())

    # ---------------------------------------------------------------- fan-out
    def _publish(self, sym: str, price: float, size: float, ts: str | None) -> None:
        chg = None
        hit = data._LIVE.get(sym)
        if hit:
            v = dict(hit[1])
            prev = (v.get("prevDailyBar") or {}).get("c")
            chg = round((price / float(prev) - 1) * 100, 2) if prev else v.get("change_pct")
            v.update(price=round(price, 2), time=ts or v.get("time"), change_pct=chg)
            data._LIVE[sym] = (time.time(), v)          # the 30-second poll and the trigger alerts see this trade too
        msg = dict(S=sym, p=round(price, 4), s=size, chg=chg)
        for q, syms in list(self.clients.values()):
            if sym in syms:
                if q.full():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                q.put_nowait(msg)

    # ---------------------------------------------------------------- the connection
    async def _run(self) -> None:
        backoff, idle_since = 2, None
        while True:
            if not self.clients and not self.extra:
                idle_since = idle_since or time.time()
                if time.time() - idle_since > IDLE_CLOSE_S:
                    self.status, self.task = "idle", None
                    return
                self.changed.clear()
                try:
                    await asyncio.wait_for(self.changed.wait(), 10)
                except asyncio.TimeoutError:
                    pass
                continue
            idle_since = None
            try:
                if data.provider() != "alpaca":
                    await self._demo()
                else:
                    await self._alpaca()
                backoff = 2
            except asyncio.CancelledError:
                raise
            except Exception as e:                 # dropped / refused: try again, slower each time (max 60 s)
                self.status = f"error: {str(e)[:120]}"
                log.warning("price stream: %s (retry in %ss)", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(60, backoff * 2)

    async def _alpaca(self) -> None:
        import websockets
        feeds = [self.feed] if self.feed else list(dict.fromkeys([os.getenv("ALPACA_STREAM_FEED", "sip"), "iex"]))
        last_err = None
        for feed in feeds:
            self.status = "connecting"
            async with websockets.connect(URL.format(feed=feed), ping_interval=20, max_size=2 ** 22) as ws:
                await ws.recv()                                    # [{"T":"success","msg":"connected"}]
                await ws.send(json.dumps({"action": "auth", "key": os.getenv("ALPACA_API_KEY", ""),
                                          "secret": os.getenv("ALPACA_SECRET_KEY", "")}))
                reply = json.loads(await ws.recv())
                err = next((m for m in reply if m.get("T") == "error"), None)
                if err:
                    last_err = f"{feed}: {err.get('code')} {err.get('msg')}"
                    if err.get("code") in (409, 402) and feed != "iex":   # not subscribed to the full feed: use IEX
                        log.info("price stream: %s, falling back to IEX", last_err)
                        continue
                    raise RuntimeError(last_err)
                self.feed, self.status, self.subscribed = feed, "live", set()
                log.info("price stream live on %s", feed)
                await self._pump(ws)
                return
        raise RuntimeError(last_err or "no feed")

    async def _sync(self, ws) -> None:
        want = set(self.wanted())
        add, drop = sorted(want - self.subscribed), sorted(self.subscribed - want)
        if drop:
            await ws.send(json.dumps({"action": "unsubscribe", "trades": drop}))
        if add:
            await ws.send(json.dumps({"action": "subscribe", "trades": add}))
        self.subscribed = want

    async def _pump(self, ws) -> None:
        await self._sync(ws)
        while self.clients or self.extra:
            self.changed.clear()
            recv = asyncio.ensure_future(ws.recv())
            chg = asyncio.ensure_future(self.changed.wait())
            done, _ = await asyncio.wait({recv, chg}, timeout=30, return_when=asyncio.FIRST_COMPLETED)
            if chg in done:
                await self._sync(ws)
            else:
                chg.cancel()
            if recv in done:
                for m in json.loads(recv.result()):
                    if m.get("T") == "t" and m.get("p"):
                        px, sz = float(m["p"]), float(m.get("s") or 0)
                        try:
                            darkpool.record(m["S"], px, sz, m.get("x"), m.get("c"), m.get("t"))
                        except Exception:
                            pass
                        self._publish(m["S"], px, sz, m.get("t"))
                    elif m.get("T") == "error":
                        log.warning("price stream error: %s", m)
            else:
                recv.cancel()
                try:
                    await recv
                except BaseException:
                    pass

    async def _demo(self) -> None:
        """No Alpaca locally: a gentle random walk from the last demo price, a few prints a second."""
        self.feed, self.status = "demo", "live"
        while self.clients or self.extra:
            syms = self.wanted()
            missing = [s for s in syms if s not in data._LIVE]
            if missing:
                await asyncio.to_thread(data.latest_prices, missing)
            for s in syms:
                hit = data._LIVE.get(s)
                if hit and hit[1].get("price"):
                    px = float(hit[1]["price"]) * (1 + random.gauss(0, .0004))
                    self._publish(s, px, random.choice([100, 100, 200, 500, 1200]), None)
                    if s in self.extra and random.random() < .01:      # demo stand-in for an off-exchange block
                        from datetime import datetime as _d, timezone as _tz
                        darkpool.record(s, px, random.choice([10_000, 40_000, 120_000]), "D", [], _d.now(_tz.utc).isoformat())
            await asyncio.sleep(random.uniform(.25, .6))


HUB = _Hub()


async def keep_darkpool() -> None:
    """Nov 28: in market hours keep the shared watchlist on the one Alpaca socket, so dark pool prints are caught
    even when no app is open. Outside the window let go (the socket closes once nobody watches)."""
    while True:
        try:
            want = set(await asyncio.to_thread(darkpool.watch_symbols)) if darkpool.market_window() else set()
            if want != HUB.extra:
                HUB.extra = want
                if want:
                    HUB._ensure_running()
                if HUB.changed:
                    HUB.changed.set()
                if not want:
                    darkpool.flush(); darkpool.prune()
        except Exception as e:
            log.warning("dark pool keeper: %s", e)
        await asyncio.sleep(60)


async def seed(symbols: list[str]) -> None:
    """Make sure each symbol has yesterday's close cached, so a tick can show today's % change."""
    missing = [s for s in symbols if s not in data._LIVE]
    if missing:
        try:
            await asyncio.to_thread(data.latest_prices, missing)
        except Exception:
            pass


async def events(request, symbols: list[str]):
    """The Server-Sent Events body: ticks for these symbols, coalesced every ~200 ms (high/low/volume kept)."""
    await seed(symbols)
    cid, q = HUB.register(symbols)
    try:
        yield f"event: hello\ndata: {json.dumps(dict(feed=HUB.feed, status=HUB.status))}\n\n"
        while True:
            if await request.is_disconnected():
                break
            try:
                first = await asyncio.wait_for(q.get(), 15)
            except asyncio.TimeoutError:
                yield ": ping\n\n"                     # keeps proxies (and Render) from closing a quiet stream
                continue
            await asyncio.sleep(.2)
            batch = [first]
            while not q.empty():
                batch.append(q.get_nowait())
            agg: dict[str, dict] = {}
            for m in batch:
                a = agg.get(m["S"])
                if not a:
                    agg[m["S"]] = dict(S=m["S"], p=m["p"], hi=m["p"], lo=m["p"], v=m["s"], chg=m["chg"])
                else:
                    a.update(p=m["p"], hi=max(a["hi"], m["p"]), lo=min(a["lo"], m["p"]), v=a["v"] + m["s"],
                             chg=m["chg"] if m["chg"] is not None else a["chg"])
            yield f"data: {json.dumps(dict(feed=HUB.feed, ticks=list(agg.values())))}\n\n"
    finally:
        HUB.unregister(cid)
