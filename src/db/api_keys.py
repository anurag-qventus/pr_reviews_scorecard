"""
API Key Store
=============
Manages API keys for authenticating external callers.

Schema
------
  api_keys — key, label, created_at, is_active
"""

import os
import secrets
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.environ.get(
    "APP_DB_PATH",
    os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'app.db')),
)

_CREATE_API_KEYS = """
CREATE TABLE IF NOT EXISTS api_keys (
    key        TEXT PRIMARY KEY,
    label      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    is_active  INTEGER NOT NULL DEFAULT 1
)
"""


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def init_db():
    """Creates the api_keys table if it does not exist. Safe to call on every startup."""
    with _connect() as conn:
        conn.execute(_CREATE_API_KEYS)


def create_key(label: str) -> str:
    """Generates a new API key, stores it, and returns the key string."""
    key = "prs_" + secrets.token_hex(16)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO api_keys (key, label, created_at, is_active) VALUES (?, ?, ?, 1)",
            (key, label, _now()),
        )
    return key


def validate_key(key: str) -> bool:
    """Returns True if the key exists and is active."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT is_active FROM api_keys WHERE key = ?", (key,)
        ).fetchone()
        return bool(row and row["is_active"])


def list_keys() -> list[dict]:
    """Returns all keys ordered by creation time (newest first)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT key, label, created_at, is_active FROM api_keys ORDER BY created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]


def revoke_key(key: str) -> bool:
    """Marks a key as inactive. Returns True if the key was found."""
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE api_keys SET is_active = 0 WHERE key = ?", (key,)
        )
        return cursor.rowcount > 0
