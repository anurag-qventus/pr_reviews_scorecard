"""
Identity Store
==============
All persistence for the identity service: users, teams, team members,
and usage logs. Backed by SQLite with WAL mode for concurrent access.

When this service is deployed independently, swap _connect() to point
at PostgreSQL or any other database — the function signatures stay the same.

Schema
------
  users        — one row per anonymous user, updated on each visit
  teams        — one team per user
  team_members — GitHub usernames belonging to each team
  usage_log    — every analysis run, for audit and history
"""

import os
import sqlite3
from datetime import datetime, timezone

from identity_service.config import DB_PATH

_CREATE_USERS = """
CREATE TABLE IF NOT EXISTS users (
    anon_id     TEXT PRIMARY KEY,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    user_agent  TEXT,
    timezone    TEXT,
    platform    TEXT,
    screen      TEXT,
    language    TEXT
)
"""

_CREATE_TEAMS = """
CREATE TABLE IF NOT EXISTS teams (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    anon_id     TEXT NOT NULL,
    team_name   TEXT NOT NULL DEFAULT 'My Team',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (anon_id) REFERENCES users(anon_id)
)
"""

_CREATE_TEAM_MEMBERS = """
CREATE TABLE IF NOT EXISTS team_members (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id          INTEGER NOT NULL,
    github_username  TEXT NOT NULL,
    display_name     TEXT,
    added_at         TEXT NOT NULL,
    UNIQUE(team_id, github_username),
    FOREIGN KEY (team_id) REFERENCES teams(id)
)
"""

_CREATE_USAGE_LOG = """
CREATE TABLE IF NOT EXISTS usage_log (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    anon_id              TEXT NOT NULL,
    analyzed_github_user TEXT NOT NULL,
    date_range_label     TEXT,
    current_from         TEXT,
    current_to           TEXT,
    previous_from        TEXT,
    previous_to          TEXT,
    timestamp            TEXT NOT NULL,
    FOREIGN KEY (anon_id) REFERENCES users(anon_id)
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


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def init_db():
    """Creates all tables if they do not exist. Safe to call on every startup."""
    with _connect() as conn:
        conn.execute(_CREATE_USERS)
        conn.execute(_CREATE_TEAMS)
        conn.execute(_CREATE_TEAM_MEMBERS)
        conn.execute(_CREATE_USAGE_LOG)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def upsert_user(anon_id: str, metadata: dict):
    """Inserts on first visit; updates last_seen only on return visits."""
    now = _now()
    with _connect() as conn:
        existing = conn.execute(
            "SELECT anon_id FROM users WHERE anon_id = ?", (anon_id,)
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE users SET last_seen = ? WHERE anon_id = ?", (now, anon_id)
            )
        else:
            conn.execute(
                """INSERT INTO users
                   (anon_id, first_seen, last_seen, user_agent, timezone, platform, screen, language)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    anon_id, now, now,
                    metadata.get("user_agent", ""),
                    metadata.get("timezone",   ""),
                    metadata.get("platform",   ""),
                    metadata.get("screen",     ""),
                    metadata.get("language",   ""),
                )
            )


def get_user(anon_id: str) -> dict | None:
    """Returns the user record as a dict, or None if not found."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE anon_id = ?", (anon_id,)
        ).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------

def get_or_create_team(anon_id: str) -> int:
    """Returns the team_id for this user, creating a default team if none exists."""
    now = _now()
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM teams WHERE anon_id = ?", (anon_id,)
        ).fetchone()
        if row:
            return row["id"]
        cursor = conn.execute(
            "INSERT INTO teams (anon_id, team_name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (anon_id, "My Team", now, now)
        )
        return cursor.lastrowid


def get_team_name(anon_id: str) -> str:
    """Returns the user's team name."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT team_name FROM teams WHERE anon_id = ?", (anon_id,)
        ).fetchone()
        return row["team_name"] if row else "My Team"


def update_team_name(anon_id: str, name: str):
    """Updates the team name for the given user."""
    now     = _now()
    team_id = get_or_create_team(anon_id)
    with _connect() as conn:
        conn.execute(
            "UPDATE teams SET team_name = ?, updated_at = ? WHERE id = ?",
            (name.strip(), now, team_id)
        )


# ---------------------------------------------------------------------------
# Team Members
# ---------------------------------------------------------------------------

def get_team_members(anon_id: str) -> list[dict]:
    """Returns [{'github_username': str, 'display_name': str|None}, ...] ordered by add time."""
    team_id = get_or_create_team(anon_id)
    with _connect() as conn:
        rows = conn.execute(
            """SELECT github_username, display_name FROM team_members
               WHERE team_id = ? ORDER BY added_at ASC""",
            (team_id,)
        ).fetchall()
        return [dict(row) for row in rows]


def add_team_member(anon_id: str, github_username: str, display_name: str = ""):
    """Adds a member. Silently ignores if the username already exists in this team."""
    team_id = get_or_create_team(anon_id)
    now     = _now()
    with _connect() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO team_members
               (team_id, github_username, display_name, added_at)
               VALUES (?, ?, ?, ?)""",
            (team_id, github_username.strip(), display_name.strip() or None, now)
        )


def remove_team_member(anon_id: str, github_username: str):
    """Removes a member from the user's team."""
    team_id = get_or_create_team(anon_id)
    with _connect() as conn:
        conn.execute(
            "DELETE FROM team_members WHERE team_id = ? AND github_username = ?",
            (team_id, github_username)
        )


# ---------------------------------------------------------------------------
# Usage Log
# ---------------------------------------------------------------------------

def log_usage(
    anon_id: str,
    analyzed_github_user: str,
    date_range_label: str,
    current_from,
    current_to,
    previous_from,
    previous_to,
):
    """Records an analysis run for audit and history purposes."""
    with _connect() as conn:
        conn.execute(
            """INSERT INTO usage_log
               (anon_id, analyzed_github_user, date_range_label,
                current_from, current_to, previous_from, previous_to, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                anon_id, analyzed_github_user, date_range_label,
                str(current_from), str(current_to),
                str(previous_from), str(previous_to),
                _now()
            )
        )
