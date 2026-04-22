"""
Identity Service
================
Stable anonymous user identification, team management, and usage tracking.

Public API
----------
from identity_service import (
    compute_anon_id,   # derive anon_id from HTTP headers
    init_db,           # initialise SQLite schema
    upsert_user,       # insert or update user record
    get_user,
    get_team_name,
    update_team_name,
    get_team_members,
    add_team_member,
    remove_team_member,
    log_usage,
)

Future deployment
-----------------
This package is designed to be extracted into a standalone HTTP service
(e.g. FastAPI). Each function maps directly to a REST endpoint:

  compute_anon_id  →  POST   /identity
  upsert_user      →  PUT    /users/{anon_id}
  get_team_name    →  GET    /teams/{anon_id}
  update_team_name →  PATCH  /teams/{anon_id}
  get_team_members →  GET    /teams/{anon_id}/members
  add_team_member  →  POST   /teams/{anon_id}/members
  remove_team_member → DELETE /teams/{anon_id}/members/{username}
  log_usage        →  POST   /usage
"""

from identity_service.identity import compute_anon_id
from identity_service.store import (
    init_db,
    upsert_user,
    get_user,
    get_team_name,
    update_team_name,
    get_or_create_team,
    get_team_members,
    add_team_member,
    remove_team_member,
    log_usage,
)

__all__ = [
    "compute_anon_id",
    "init_db",
    "upsert_user",
    "get_user",
    "get_team_name",
    "update_team_name",
    "get_or_create_team",
    "get_team_members",
    "add_team_member",
    "remove_team_member",
    "log_usage",
]
