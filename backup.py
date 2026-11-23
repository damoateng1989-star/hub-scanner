"""Nightly database backup (Nov 23).

1. A consistent snapshot of hubscanner.db (SQLite's online backup, safe while the app runs), gzipped into
   DATA_DIR/backups/. The newest 14 are kept. These survive deploys and restarts, but live on the same disk.
2. Off the server: when BACKUP_S3_BUCKET, BACKUP_S3_KEY and BACKUP_S3_SECRET are set, the snapshot is also uploaded
   to that bucket (any S3-compatible storage: Cloudflare R2, Backblaze B2, AWS S3). BACKUP_S3_ENDPOINT sets the
   host (R2: https://<account>.r2.cloudflarestorage.com), BACKUP_S3_REGION the region ("auto" for R2).
The owner can also download the latest snapshot from Admin. Backups hold every member's data: keep the bucket private.
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import hmac
import json
import logging
import os
import shutil
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote, urlparse

import requests

import db

log = logging.getLogger("backup")
KEEP = 14
DIR = db.DATA_DIR / "backups"
_STATUS = db.DATA_DIR / "backups" / "status.json"


def _status(**kw) -> dict:
    DIR.mkdir(parents=True, exist_ok=True)
    try:
        st = json.loads(_STATUS.read_text())
    except (OSError, ValueError):
        st = {}
    st.update(kw)
    _STATUS.write_text(json.dumps(st))
    return st


def status() -> dict:
    try:
        st = json.loads(_STATUS.read_text())
    except (OSError, ValueError):
        st = {}
    files = sorted(DIR.glob("hubscanner-*.db.gz")) if DIR.exists() else []
    st.update(count=len(files), latest=files[-1].name if files else None,
              latest_mb=round(files[-1].stat().st_size / 1e6, 2) if files else None,
              offsite=offsite_configured(), on_disk=str(db.DATA_DIR).startswith("/data"))
    return st


def offsite_configured() -> bool:
    return all(os.getenv(k, "").strip() for k in ("BACKUP_S3_BUCKET", "BACKUP_S3_KEY", "BACKUP_S3_SECRET"))


def snapshot() -> Path:
    DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d-%H%M")
    raw = DIR / f".tmp-{stamp}.db"
    out = DIR / f"hubscanner-{stamp}.db.gz"
    dst = sqlite3.connect(raw)
    try:
        with db._lock:
            db._conn.backup(dst)
    finally:
        dst.close()
    with open(raw, "rb") as f, gzip.open(out, "wb", compresslevel=6) as g:
        shutil.copyfileobj(f, g)
    raw.unlink(missing_ok=True)
    for old in sorted(DIR.glob("hubscanner-*.db.gz"))[:-KEEP]:
        old.unlink(missing_ok=True)
    return out


# ---------------------------------------------------------------- S3-compatible upload (AWS Signature V4, no SDK)
def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def upload(path: Path) -> str:
    bucket = os.getenv("BACKUP_S3_BUCKET").strip()
    akey, secret = os.getenv("BACKUP_S3_KEY").strip(), os.getenv("BACKUP_S3_SECRET").strip()
    region = os.getenv("BACKUP_S3_REGION", "auto").strip() or "auto"
    endpoint = os.getenv("BACKUP_S3_ENDPOINT", "").strip().rstrip("/") or f"https://s3.{region}.amazonaws.com"
    host = urlparse(endpoint).netloc
    obj = f"nextshelf/{path.name}"
    uri = f"/{bucket}/{quote(obj)}"
    body = path.read_bytes()
    payload_hash = hashlib.sha256(body).hexdigest()
    now = dt.datetime.now(dt.timezone.utc)
    amz_date, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    headers = {"host": host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
    signed = ";".join(sorted(headers))
    canonical = "\n".join(["PUT", uri, "", "".join(f"{k}:{headers[k]}\n" for k in sorted(headers)), signed, payload_hash])
    scope = f"{day}/{region}/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    k = _sign(_sign(_sign(_sign(("AWS4" + secret).encode(), day), region), "s3"), "aws4_request")
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    auth = f"AWS4-HMAC-SHA256 Credential={akey}/{scope}, SignedHeaders={signed}, Signature={sig}"
    r = requests.put(endpoint + uri, data=body, timeout=120,
                     headers={"x-amz-content-sha256": payload_hash, "x-amz-date": amz_date, "Authorization": auth,
                              "Content-Type": "application/gzip"})
    if r.status_code >= 300:
        raise RuntimeError(f"upload failed ({r.status_code})")
    return obj


def run() -> dict:
    """The nightly job (and Admin's "Back up now")."""
    t0 = time.time()
    try:
        p = snapshot()
    except Exception as e:
        log.exception("backup snapshot failed")
        return _status(last_error=f"Snapshot failed: {type(e).__name__}", last_error_at=_now())
    st = _status(last_at=_now(), last_file=p.name, last_mb=round(p.stat().st_size / 1e6, 2), last_error=None)
    if offsite_configured():
        try:
            st = _status(offsite_at=_now(), offsite_file=upload(p), offsite_error=None)
        except Exception as e:
            log.warning("backup upload failed: %s", e)
            st = _status(offsite_error=str(e)[:160], offsite_error_at=_now())
    log.info("backup done in %.1fs: %s", time.time() - t0, p.name)
    return st


def latest() -> Path | None:
    files = sorted(DIR.glob("hubscanner-*.db.gz")) if DIR.exists() else []
    return files[-1] if files else None


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
