"""
Streamlit adapter for the Identity Service.

Extracts HTTP headers from st.context (Streamlit-specific) and delegates
the actual ID computation to identity_service.compute_anon_id(), which is
framework-agnostic.
"""

import os
import sys

_src_dir     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_project_dir = os.path.dirname(_src_dir)
sys.path.insert(0, _project_dir)

import streamlit as st
from identity_service import compute_anon_id


def get_anon_id() -> tuple[str, dict]:
    """
    Derives a stable anonymous ID from the browser's HTTP request headers.

    Returns (anon_id, metadata) where:
      anon_id  — 16-character hex string, same browser = same ID every time
      metadata — dict of raw signals stored in the DB for reference
    """
    headers = dict(st.context.headers)
    return compute_anon_id(headers)
