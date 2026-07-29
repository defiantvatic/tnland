"""SQLite-backed disk cache.

Government GIS endpoints are slow and some of them (Overpass especially) ask
you not to hammer them. Every outbound response is cached on disk so that
re-opening the same parcel is instant and re-running a report costs nothing.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from . import config

_DB_PATH = Path.home() / ".tnland" / "cache.sqlite"
_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        _conn.execute(
            "CREATE TABLE IF NOT EXISTS cache ("
            " key TEXT PRIMARY KEY,"
            " value TEXT NOT NULL,"
            " created REAL NOT NULL)"
        )
        _conn.commit()
    return _conn


def make_key(*parts: Any) -> str:
    blob = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def get(key: str, ttl_days: float | None = None) -> Any | None:
    ttl = config.CACHE_TTL_DAYS if ttl_days is None else ttl_days
    with _lock:
        cur = _connect().execute(
            "SELECT value, created FROM cache WHERE key = ?", (key,)
        )
        row = cur.fetchone()
    if row is None:
        return None
    value, created = row
    if ttl is not None and time.time() - created > ttl * 86400:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def put(key: str, value: Any) -> None:
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT OR REPLACE INTO cache (key, value, created) VALUES (?, ?, ?)",
            (key, json.dumps(value, default=str), time.time()),
        )
        conn.commit()


def clear() -> int:
    with _lock:
        conn = _connect()
        n = conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
        conn.execute("DELETE FROM cache")
        conn.commit()
    return int(n)


def stats() -> dict[str, Any]:
    with _lock:
        conn = _connect()
        n = conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
    size = _DB_PATH.stat().st_size if _DB_PATH.exists() else 0
    return {"entries": int(n), "bytes": size, "path": str(_DB_PATH)}
