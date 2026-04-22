"""
Configuration for the Identity Service.

DB_PATH defaults to data/identity.db relative to the project root.
Override with the IDENTITY_DB_PATH environment variable when deploying
this service independently.
"""

import os

# When running inside this repo: identity_service/ → project root → data/
# When deployed standalone: override via environment variable
DB_PATH = os.environ.get(
    "IDENTITY_DB_PATH",
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "data", "identity.db")
    ),
)
