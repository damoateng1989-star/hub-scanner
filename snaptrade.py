"""SnapTrade brokerage integration for NextShelf.

Secrets are server-side only. Pending checked trades are short-lived, user-bound,
and single-use. This module never places an unchecked order.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger("snaptrade")

_DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
_STORE = _DATA_DIR / "snaptrade_users.json"
_DB_STORE = _DATA_DIR / "hubscanner.db"
_LOCK = threading.RLock()
_PENDING: dict[str, dict[str, Any]] = {}
_PREVIEW_TTL = 300

try:
    from snaptrade_client import SnapTrade, SnapTradeAuth
except Exception:  # Keep the rest of NextShelf available if the optional SDK is absent.
    SnapTrade = None
    SnapTradeAuth = None


def configured() -> bool:
    return bool(os.getenv("SNAPTRADE_CLIENT_ID", "").strip()
                and os.getenv("SNAPTRADE_CONSUMER_KEY", "").strip())


def _client():
    if not configured() or SnapTrade is None or SnapTradeAuth is None:
        raise RuntimeError("broker trading not configured")
    # SDK 13.x requires an explicit Commercial API-key auth object.
    # Passing client_id/consumer_key directly raises TypeError before any HTTP request.
    return SnapTrade(
        auth=SnapTradeAuth.commercial_api_key(
            client_id=os.environ["SNAPTRADE_CLIENT_ID"].strip(),
            consumer_key=os.environ["SNAPTRADE_CONSUMER_KEY"].strip(),
        )
    )


def _load() -> dict[str, dict[str, str]]:
    try:
        data = json.loads(_STORE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        log.exception("Could not read SnapTrade user store")
        raise RuntimeError("broker credential store unavailable")


def _save(data: dict[str, dict[str, str]]) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(_STORE.parent),
                                         prefix=_STORE.name + ".", suffix=".tmp",
                                         delete=False) as f:
            tmp = f.name
            os.chmod(tmp, 0o600)
            json.dump(data, f, separators=(",", ":"), allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, _STORE)
        try:
            os.chmod(_STORE, 0o600)
        except OSError:
            pass
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _uid(user: dict) -> str:
    # Immutable internal database ID; never use a mutable email address.
    return "nextshelf-" + str(int(user["id"]))


def _db_secret(uid: str) -> str | None:
    """Read the SnapTrade user secret from the same persistent DATA_DIR as NextShelf's DB."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(_DB_STORE, timeout=10) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS snaptrade_credentials (
                user_id TEXT PRIMARY KEY,
                user_secret TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        row = conn.execute(
            "SELECT user_secret FROM snaptrade_credentials WHERE user_id = ?", (uid,)
        ).fetchone()
    return str(row[0]) if row and row[0] else None


def _db_save_secret(uid: str, secret: str) -> None:
    """Persist credentials transactionally alongside the app's existing SQLite data."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(_DB_STORE, timeout=10) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS snaptrade_credentials (
                user_id TEXT PRIMARY KEY,
                user_secret TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            INSERT INTO snaptrade_credentials (user_id, user_secret, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                user_secret = excluded.user_secret,
                updated_at = CURRENT_TIMESTAMP
        """, (uid, secret))
        conn.commit()


def credential_status(user: dict) -> dict:
    """Safe diagnostics only; never returns the secret itself."""
    uid = _uid(user)
    with _LOCK:
        secret = _db_secret(uid)
        source = "sqlite" if secret else None
        if not secret:
            legacy = _load().get(uid, {})
            secret = legacy.get("user_secret") if isinstance(legacy, dict) else None
            if secret:
                _db_save_secret(uid, str(secret))
                source = "legacy-file-migrated"
    return {
        "registered_user_id": uid,
        "credentials_available": bool(secret),
        "credential_source": source or "missing",
        "reconnect_required": not bool(secret),
    }


def _credentials(user: dict) -> tuple[str, str]:
    uid = _uid(user)
    with _LOCK:
        secret = _db_secret(uid)
        if secret:
            return uid, secret
        # One-time migration from the original JSON store used by the first prototype.
        record = _load().get(uid)
        secret = record.get("user_secret") if isinstance(record, dict) else None
        if secret:
            _db_save_secret(uid, str(secret))
            return uid, str(secret)
    raise LookupError("SnapTrade credentials are missing from persistent storage; connection recovery is required")


def register(user: dict) -> dict:
    client = _client()
    uid = _uid(user)
    with _LOCK:
        # Reuse the existing user record whenever its secret is still available.
        existing = _db_secret(uid)
        if not existing:
            legacy = _load().get(uid, {})
            existing = legacy.get("user_secret") if isinstance(legacy, dict) else None
            if existing:
                _db_save_secret(uid, str(existing))
        if existing:
            return {"registered": True, "already_registered": True}
        response = client.authentication.register_snap_trade_user(user_id=uid)
        body = _plain(getattr(response, "body", response))
        secret = (body.get("userSecret") or body.get("user_secret")) if isinstance(body, dict) else None
        if not secret:
            raise RuntimeError("SnapTrade did not return a user secret")
        # Save to durable app SQLite before reporting success. Keep legacy JSON mirror
        # temporarily for rollback compatibility, but SQLite is now authoritative.
        _db_save_secret(uid, str(secret))
        store = _load()
        store[uid] = {"user_secret": str(secret)}
        _save(store)
    return {"registered": True, "already_registered": False}


def portal(user: dict) -> str:
    client = _client()
    uid, secret = _credentials(user)
    response = client.authentication.login_snap_trade_user(
        user_id=uid, user_secret=secret, connection_type="trade-if-available",
        connection_portal_version="v4", dark_mode=True,
    )
    body = _plain(getattr(response, "body", response))
    if isinstance(body, dict):
        url = body.get("redirectURI") or body.get("redirectUri") or body.get("redirect_uri") or body.get("url")
    else:
        url = None
    if not url:
        raise RuntimeError("SnapTrade did not return a connection URL")
    return str(url)


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if hasattr(value, "to_dict"):
        try:
            return _plain(value.to_dict())
        except Exception:
            pass
    if hasattr(value, "model_dump"):
        try:
            return _plain(value.model_dump(by_alias=True))
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        return {str(k): _plain(v) for k, v in vars(value).items() if not str(k).startswith("_")}
    return str(value)


def _body(response):
    return _plain(getattr(response, "body", response))


def _account_id(account: dict) -> str:
    return str(account.get("id") or account.get("accountId") or account.get("account_id") or "")


def _account_rows(user: dict) -> list[dict]:
    client = _client()
    uid, secret = _credentials(user)
    rows = _body(client.account_information.list_user_accounts(user_id=uid, user_secret=secret))
    if isinstance(rows, dict):
        rows = rows.get("accounts") or rows.get("data") or []
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def accounts(user: dict) -> list[dict]:
    result = []
    for a in _account_rows(user):
        aid = _account_id(a)
        number = str(a.get("number") or a.get("account_number") or "")
        masked = ("•••• " + number[-4:]) if number else ""
        total = a.get("total_value") or a.get("equity") or a.get("balance")
        if isinstance(total, dict):
            total = total.get("total") or total.get("value") or total.get("amount")
        if not isinstance(total, (str, int, float)):
            total = None
        result.append({
            "account_id": aid,
            "brokerage": a.get("brokerage_name") or a.get("brokerage") or a.get("institution_name") or "Brokerage",
            "name": a.get("name") or a.get("account_name") or "Investment account",
            "number_masked": masked,
            "total_value": total,
            "currency": a.get("currency") or "USD",
        })
    return result


def _owned_account(user: dict, account_id: str) -> dict:
    if not account_id or not any(_account_id(a) == account_id for a in _account_rows(user)):
        raise LookupError("Brokerage account not found for this member")
    return {}


def positions(user: dict, account_id: str) -> Any:
    client = _client()
    uid, secret = _credentials(user)
    _owned_account(user, account_id)
    return _body(client.account_information.get_user_account_positions(
        user_id=uid, user_secret=secret, account_id=account_id))


def orders(user: dict, account_id: str) -> Any:
    client = _client()
    uid, secret = _credentials(user)
    _owned_account(user, account_id)
    return _body(client.account_information.get_user_account_orders(
        user_id=uid, user_secret=secret, account_id=account_id, state="all", days=30))


def impact(user: dict, payload: dict) -> dict:
    client = _client()
    uid, secret = _credentials(user)
    account_id = str(payload.get("account_id") or "")
    _owned_account(user, account_id)
    symbol = str(payload.get("symbol") or "").strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", symbol):
        raise ValueError("Enter a valid stock ticker")
    action = str(payload.get("action") or "").upper()
    if action not in ("BUY", "SELL"):
        raise ValueError("Side must be BUY or SELL")
    units = payload.get("units")
    if isinstance(units, bool) or not isinstance(units, int) or not 1 <= units <= 10000:
        raise ValueError("Quantity must be a whole number from 1 to 10,000")
    order_type = str(payload.get("order_type") or "Market").title()
    if order_type not in ("Market", "Limit"):
        raise ValueError("Only market and limit stock orders are supported")
    price = payload.get("price")
    if order_type == "Limit":
        try:
            price = float(price)
        except (TypeError, ValueError):
            raise ValueError("A valid limit price is required")
        if not 0 < price < 10000000:
            raise ValueError("Enter a valid limit price")
    else:
        price = None

    matches = _body(client.reference_data.symbol_search_user_account(
        user_id=uid, user_secret=secret, account_id=account_id, substring=symbol))
    if isinstance(matches, dict):
        matches = matches.get("symbols") or matches.get("data") or []
    match = next((m for m in (matches or []) if isinstance(m, dict)
                  and str(m.get("symbol") or m.get("ticker") or "").upper() == symbol), None)
    if not match:
        raise LookupError("This ticker is not available for trading in the selected account")
    universal_id = match.get("id") or match.get("universal_symbol_id") or match.get("universalSymbolId")
    if not universal_id:
        raise RuntimeError("SnapTrade did not return a symbol identifier")

    response = client.trading.get_order_impact(
        user_id=uid, user_secret=secret, account_id=account_id,
        action=action, order_type=order_type, price=price, stop=None,
        time_in_force="Day", units=units, universal_symbol_id=str(universal_id),
        notional_value=None,
    )
    body = _body(response)
    trade = body.get("trade") if isinstance(body, dict) else None
    trade_id = (trade.get("id") if isinstance(trade, dict) else None) or (body.get("tradeId") or body.get("trade_id") if isinstance(body, dict) else None)
    if not trade_id:
        raise RuntimeError("SnapTrade did not return a checked trade ID")
    preview_id = uuid.uuid4().hex
    with _LOCK:
        now = time.time()
        for key in [k for k, v in _PENDING.items() if v["expires"] <= now]:
            _PENDING.pop(key, None)
        _PENDING[preview_id] = {
            "trade_id": str(trade_id), "user_id": int(user["id"]),
            "account_id": account_id, "symbol": symbol, "action": action,
            "units": units, "order_type": order_type, "expires": now + _PREVIEW_TTL,
        }
    # Never expose SnapTrade's tradeId to the browser.
    safe_body = dict(body) if isinstance(body, dict) else {"impact": body}
    safe_body.pop("tradeId", None)
    safe_body.pop("trade_id", None)
    if isinstance(safe_body.get("trade"), dict):
        safe_body["trade"].pop("id", None)
    return {"preview_id": preview_id, "expires_in_seconds": _PREVIEW_TTL,
            "order": {"symbol": symbol, "action": action, "units": units,
                      "order_type": order_type, "limit_price": price},
            "impact": safe_body}


def place_checked_order(user: dict, preview_id: str) -> Any:
    client = _client()
    uid, secret = _credentials(user)
    with _LOCK:
        pending = _PENDING.pop(str(preview_id), None)
    if not pending or pending.get("kind") == "option" or pending["expires"] <= time.time():
        raise LookupError("Order preview expired. Run the impact preview again.")
    if pending["user_id"] != int(user["id"]):
        raise PermissionError("This order preview belongs to another member")
    response = client.trading.place_order(
        trade_id=pending["trade_id"], user_id=uid, user_secret=secret,
        wait_to_confirm=True,
    )
    return _body(response)


# ---------------------------------------------------------------- options (owner, Nov 22): single-leg orders like
# TradingView's options ticket. Same contract as stocks: preview first (SnapTrade's option impact), the previewed order is
# kept server-side, single-use, user-bound and short-lived, and only that exact order can be placed after an explicit
# confirm. Nothing is ever placed without a preview.
_OCC_IN = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")
_OPT_ACTIONS = ("BUY_TO_OPEN", "SELL_TO_CLOSE")


def occ21(symbol: str) -> str:
    """'AVGO261211C00305000' -> 'AVGO  261211C00305000' (root padded to 6, the 21-character OCC format SnapTrade wants)."""
    s = re.sub(r"\s+", "", str(symbol or "").upper())
    m = _OCC_IN.match(s)
    if not m:
        raise ValueError("Pick a valid option contract")
    root, ymd, cp, strike = m.groups()
    return f"{root:<6}{ymd}{cp}{strike}"


def _option_form(payload: dict) -> dict:
    occ = occ21(payload.get("contract") or "")
    action = str(payload.get("action") or "").upper()
    if action not in _OPT_ACTIONS:
        raise ValueError("Side must be Buy to open or Sell to close")
    units = payload.get("units")
    if isinstance(units, bool) or not isinstance(units, int) or not 1 <= units <= 100:
        raise ValueError("Contracts must be a whole number from 1 to 100")
    order_type = str(payload.get("order_type") or "LIMIT").upper()
    if order_type not in ("LIMIT", "MARKET"):
        raise ValueError("Only limit and market option orders are supported")
    price = None
    if order_type == "LIMIT":
        try:
            price = round(float(payload.get("price")), 2)
        except (TypeError, ValueError):
            raise ValueError("A valid limit price is required")
        if not 0 < price < 100000:
            raise ValueError("Enter a valid limit price")
    tif = str(payload.get("time_in_force") or "Day").strip().upper()
    tif = {"DAY": "Day", "GTC": "GTC"}.get(tif)
    if not tif:
        raise ValueError("Time in force must be Day or GTC")
    if tif == "GTC" and order_type != "LIMIT":
        raise ValueError("Good-till-cancelled needs a limit price")
    return dict(occ=occ, action=action, units=units, order_type=order_type, price=price, tif=tif)


def _option_call(client, fn, uid: str, secret: str, account_id: str, f: dict):
    return fn(account_id=account_id, user_id=uid, user_secret=secret, order_type=f["order_type"], time_in_force=f.get("tif") or "Day",
              limit_price=None if f["price"] is None else f"{f['price']:.2f}",
              price_effect=None if f["order_type"] == "MARKET" else ("DEBIT" if f["action"] == "BUY_TO_OPEN" else "CREDIT"),
              legs=[{"instrument": {"symbol": f["occ"], "instrument_type": "OPTION"}, "action": f["action"], "units": f["units"]}])


def option_impact(user: dict, payload: dict) -> dict:
    client = _client()
    uid, secret = _credentials(user)
    account_id = str(payload.get("account_id") or "")
    _owned_account(user, account_id)
    f = _option_form(payload)
    body = _body(_option_call(client, client.trading.get_option_impact, uid, secret, account_id, f))
    preview_id = uuid.uuid4().hex
    with _LOCK:
        now = time.time()
        for key in [k for k, v in _PENDING.items() if v["expires"] <= now]:
            _PENDING.pop(key, None)
        _PENDING[preview_id] = {"kind": "option", "user_id": int(user["id"]), "account_id": account_id, "form": f,
                                "expires": now + _PREVIEW_TTL}
    return {"preview_id": preview_id, "expires_in_seconds": _PREVIEW_TTL,
            "order": {"contract": f["occ"], "action": f["action"], "units": f["units"], "order_type": f["order_type"],
                      "limit_price": f["price"], "time_in_force": f["tif"]},
            "impact": body if isinstance(body, dict) else {"impact": body}}


def place_checked_option(user: dict, preview_id: str) -> Any:
    client = _client()
    uid, secret = _credentials(user)
    with _LOCK:
        pending = _PENDING.pop(str(preview_id), None)
    if not pending or pending.get("kind") != "option" or pending["expires"] <= time.time():
        raise LookupError("Order preview expired. Run the preview again.")
    if pending["user_id"] != int(user["id"]):
        raise PermissionError("This order preview belongs to another member")
    _owned_account(user, pending["account_id"])
    return _body(_option_call(client, client.trading.place_mleg_order, uid, secret, pending["account_id"], pending["form"]))


# ---------------------------------------------------------------- positions & orders view (owner, Nov 22)
def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _occ_parts(occ: str) -> dict:
    s = re.sub(r"\s+", "", str(occ or "").upper())
    m = _OCC_IN.match(s)
    if not m:
        return {}
    root, ymd, cp, k = m.groups()
    return dict(underlying=root, expiry=f"20{ymd[:2]}-{ymd[2:4]}-{ymd[4:]}", option_type="CALL" if cp == "C" else "PUT",
                strike=int(k) / 1000, occ=s)


def _position_row(p: dict) -> dict | None:
    ins = p.get("instrument") or {}
    kind = str(ins.get("kind") or "").lower()
    units, price, cost = _num(p.get("units")), _num(p.get("price")), _num(p.get("cost_basis"))
    if not units:
        return None
    row = dict(kind=kind or "other", units=units, price=price, cost=cost, symbol=ins.get("symbol") or "", description=ins.get("description"))
    if kind == "option":
        und = ins.get("underlying") or {}
        mult = _num(ins.get("multiplier")) or 100
        row.update(_occ_parts(ins.get("symbol")))
        row.update(underlying=(und.get("symbol") if isinstance(und, dict) else None) or row.get("underlying"),
                   option_type=ins.get("option_type") or row.get("option_type"), strike=_num(ins.get("strike_price")) or row.get("strike"),
                   expiry=str(ins.get("expiration_date") or row.get("expiry") or "")[:10], mult=mult)
    else:
        row["mult"] = 1
    m = row["mult"]
    row["value"] = round(units * price * m, 2) if price is not None else None
    row["paid"] = round(units * cost * m, 2) if cost is not None else None
    if row["value"] is not None and row["paid"]:
        row["pl"] = round(row["value"] - row["paid"], 2)
        row["pl_pct"] = round(row["pl"] / abs(row["paid"]) * 100, 1)
    return row


_OPEN_STATES = {"PENDING", "ACCEPTED", "OPEN", "PARTIAL", "PARTIALLY_FILLED", "QUEUED", "TRIGGERED", "ACTIVATED", "PENDING_NEW", "NEW", "REPLACE_PENDING"}


def _order_row(o: dict) -> dict:
    opt = o.get("option_symbol") or {}
    uni = o.get("universal_symbol") or {}
    status = str(o.get("status") or "").upper()
    row = dict(id=str(o.get("brokerage_order_id") or ""), status=status, action=str(o.get("action") or "").upper(),
               qty=_num(o.get("total_quantity")), filled=_num(o.get("filled_quantity")), order_type=o.get("order_type"),
               limit=_num(o.get("limit_price")), avg_fill=_num(o.get("execution_price")), tif=o.get("time_in_force"),
               placed=o.get("time_placed"), updated=o.get("time_updated") or o.get("time_executed"),
               open=status in _OPEN_STATES)
    if isinstance(opt, dict) and opt.get("ticker"):
        row.update(kind="option", **_occ_parts(opt.get("ticker")))
        und = opt.get("underlying_symbol") or {}
        if isinstance(und, dict) and und.get("symbol"):
            row["underlying"] = und["symbol"]
    else:
        row.update(kind="stock", symbol=(uni.get("symbol") if isinstance(uni, dict) else None) or o.get("symbol") or "")
    return row


def portfolio(user: dict, account_id: str) -> dict:
    """Open positions (stocks and options, with value and P/L from the broker's own prices) and the last 7 days of orders."""
    client = _client()
    uid, secret = _credentials(user)
    _owned_account(user, account_id)
    pb = _body(client.account_information.get_all_account_positions(user_id=uid, user_secret=secret, account_id=account_id))
    plist = pb.get("results") if isinstance(pb, dict) else pb
    ob = _body(client.account_information.get_user_account_orders(user_id=uid, user_secret=secret, account_id=account_id, state="all", days=7))
    olist = ob if isinstance(ob, list) else (ob.get("orders") or ob.get("results") or []) if isinstance(ob, dict) else []
    positions = [r for r in (_position_row(p) for p in (plist or []) if isinstance(p, dict)) if r]
    orders = sorted((_order_row(o) for o in olist if isinstance(o, dict)), key=lambda r: str(r.get("placed") or ""), reverse=True)
    fresh = pb.get("data_freshness") if isinstance(pb, dict) else None
    return dict(positions=positions, orders=orders[:40], as_of=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), freshness=fresh)


def cancel(user: dict, account_id: str, brokerage_order_id: str) -> Any:
    client = _client()
    uid, secret = _credentials(user)
    _owned_account(user, account_id)
    if not re.fullmatch(r"[A-Za-z0-9_.:\-]{1,80}", str(brokerage_order_id or "")):
        raise ValueError("Unknown order")
    return _body(client.trading.cancel_order(account_id=account_id, user_id=uid, user_secret=secret, brokerage_order_id=str(brokerage_order_id)))


def order_history(user: dict, days: int = 90) -> list[dict]:
    """Trade Diary: every order on every linked account over the last `days` days (read-only), oldest first."""
    client = _client()
    uid, secret = _credentials(user)
    out = []
    for a in _account_rows(user):
        aid = _account_id(a)
        ob = _body(client.account_information.get_user_account_orders(user_id=uid, user_secret=secret, account_id=aid, state="all", days=days))
        olist = ob if isinstance(ob, list) else (ob.get("orders") or ob.get("results") or []) if isinstance(ob, dict) else []
        for o in olist:
            if isinstance(o, dict):
                r = _order_row(o)
                r["account_id"] = aid
                out.append(r)
    return sorted(out, key=lambda r: str(r.get("updated") or r.get("placed") or ""))
