"""
Anonymous Identity
==================
Derives a stable anonymous ID from HTTP request headers.

This module is framework-agnostic: it accepts a plain dict of headers
and works with Streamlit, FastAPI, Flask, or any other HTTP framework.

Strategy:
  SHA-256(User-Agent + Accept-Language)[:16]

Why VPN-safe:
  User-Agent and Accept-Language are browser/OS properties. They do not
  change when a VPN connects, reconnects, or switches exit nodes.

Uniqueness in practice:
  User-Agent encodes OS, OS version, browser, browser version, and
  architecture. Combined with language preference, collisions within a
  small team are extremely unlikely.

Future deployment:
  When extracted as a standalone HTTP service, this function becomes
  the core of a POST /identity endpoint that accepts headers in the
  request body and returns the anon_id + metadata as JSON.
"""

import hashlib


def compute_anon_id(headers: dict) -> tuple[str, dict]:
    """
    Derives a stable anonymous ID from HTTP request headers.

    Parameters
    ----------
    headers : dict
        HTTP request headers. Expects 'User-Agent' and 'Accept-Language'.

    Returns
    -------
    anon_id : str
        16-character hex string. Same browser + OS = same ID every time.
    metadata : dict
        Raw signals stored for reference (user_agent, language, etc.).
    """
    user_agent = headers.get("User-Agent", "")
    language   = headers.get("Accept-Language", "")

    signals = f"{user_agent}|{language}"
    anon_id = hashlib.sha256(signals.encode()).hexdigest()[:16]

    metadata = {
        "user_agent": user_agent,
        "language":   language,
        "timezone":   "",
        "platform":   "",
        "screen":     "",
    }

    return anon_id, metadata
