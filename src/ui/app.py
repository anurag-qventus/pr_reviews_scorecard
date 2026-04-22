import os
import sys
from datetime import date, timedelta

_src_dir     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../src
_project_dir = os.path.dirname(_src_dir)                                     # project root
sys.path.insert(0, _src_dir)
sys.path.insert(0, _project_dir)  # makes identity_service importable

import streamlit as st

from services.llm_service import LLMService
from services.pr_service import DATA_PRS_DIR, collect_pr_text
from utils import extract_score
from identity_service import (
    init_db, upsert_user, get_team_name, update_team_name,
    get_team_members, add_team_member, remove_team_member, log_usage,
)
from ui.user_identity import get_anon_id
from utils import generate_pdf_bytes

ROLLING_WINDOW_DAYS = 730  # must match scheduler.py

# ---------------------------------------------------------------------------
# DB init (safe to call on every startup)
# ---------------------------------------------------------------------------
init_db()

# ---------------------------------------------------------------------------
# Anonymous user identification
# ---------------------------------------------------------------------------
# Derived from HTTP request headers (User-Agent + Accept-Language).
# Resolves instantly — no JavaScript, no loading state.
# Cached in session_state so the hash is only computed once per session.

if 'anon_id' not in st.session_state:
    anon_id, metadata = get_anon_id()
    st.session_state['anon_id']  = anon_id
    st.session_state['metadata'] = metadata
    upsert_user(anon_id, metadata)

anon_id  = st.session_state['anon_id']
metadata = st.session_state.get('metadata', {})

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Sidebar — identity + team management
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### Your Identity")
    st.caption(f"ID: `{anon_id[:8]}...`")
    if metadata.get("timezone"):
        st.caption(f"Timezone: {metadata['timezone']}")
    if metadata.get("platform"):
        st.caption(f"Platform: {metadata['platform']}")

    st.divider()

    st.markdown("### My Team")
    current_team_name = get_team_name(anon_id)
    new_team_name = st.text_input("Team name", value=current_team_name, key="team_name_input")
    if st.button("Save Team Name"):
        if new_team_name.strip():
            update_team_name(anon_id, new_team_name.strip())
            st.success("Team name saved.")
            st.rerun()
        else:
            st.warning("Team name cannot be empty.")

    st.divider()

    st.markdown("**Add Member**")
    new_username = st.text_input("GitHub username", key="new_username").strip()
    new_display  = st.text_input("Display name (optional)", key="new_display").strip()
    if st.button("+ Add Member"):
        if new_username:
            add_team_member(anon_id, new_username, new_display)
            st.success(f"Added `{new_username}`.")
            st.rerun()
        else:
            st.warning("GitHub username is required.")

    st.divider()

    members = get_team_members(anon_id)
    if members:
        st.markdown("**Team Members**")
        for m in members:
            label = m['display_name'] or m['github_username']
            col_name, col_btn = st.columns([3, 1])
            with col_name:
                st.markdown(f"`{m['github_username']}`  {label}")
            with col_btn:
                if st.button("✕", key=f"remove_{m['github_username']}"):
                    remove_team_member(anon_id, m['github_username'])
                    st.rerun()
    else:
        st.caption("No members yet. Add members above.")

# ---------------------------------------------------------------------------
# Main area
# ---------------------------------------------------------------------------
st.title("PR Reviews Score Card")

members = get_team_members(anon_id)

if not members:
    st.info(
        "Your team has no members yet. "
        "Add GitHub usernames in the **My Team** sidebar to get started."
    )
    st.stop()

user_options = {
    (m['display_name'] or m['github_username']): m['github_username']
    for m in members
}

duration_options = {
    "3 Months": 90,
    "6 Months": 180,
    "1 Year":   365,
}

today    = date.today()
min_date = today - timedelta(days=ROLLING_WINDOW_DAYS)

# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------
col_user, col_mode = st.columns(2)

with col_user:
    user_label = st.selectbox("Select User:", list(user_options.keys()), index=0)

with col_mode:
    mode = st.radio("Date Range Mode:", ["Preset Duration", "Custom Date Range"], horizontal=True)

user_login = user_options[user_label]

# ---------------------------------------------------------------------------
# Date range
# ---------------------------------------------------------------------------
date_range_label = ""

if mode == "Preset Duration":
    duration_label = st.selectbox("Time Duration:", list(duration_options.keys()), index=0)
    span_days      = duration_options[duration_label]

    current_from  = today - timedelta(days=span_days)
    current_to    = today
    previous_from = today - timedelta(days=span_days * 2)
    previous_to   = today - timedelta(days=span_days + 1)

    date_range_label = duration_label

else:
    st.markdown("**Current Period**")
    col_cf, col_ct = st.columns(2)
    with col_cf:
        current_from = st.date_input("From", value=today - timedelta(days=90),
                                     min_value=min_date, max_value=today, key="cur_from")
    with col_ct:
        current_to   = st.date_input("To",   value=today,
                                     min_value=min_date, max_value=today, key="cur_to")

    st.markdown("**Previous Period**")
    col_pf, col_pt = st.columns(2)
    with col_pf:
        previous_from = st.date_input("From", value=today - timedelta(days=180),
                                      min_value=min_date, max_value=today, key="prev_from")
    with col_pt:
        previous_to   = st.date_input("To",   value=today - timedelta(days=91),
                                      min_value=min_date, max_value=today, key="prev_to")

    date_range_label = "Custom Range"

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
date_error = None
if current_from > current_to:
    date_error = "Current period: 'From' date must be before 'To' date."
elif previous_from > previous_to:
    date_error = "Previous period: 'From' date must be before 'To' date."
elif previous_to >= current_from:
    date_error = (
        f"Date ranges overlap: previous period ends {previous_to} but "
        f"current period starts {current_from}. "
        f"Previous 'To' must be before {current_from}."
    )

if date_error:
    st.error(date_error)

# ---------------------------------------------------------------------------
# Submit / Clear
# ---------------------------------------------------------------------------
if 'response' not in st.session_state:
    st.session_state.response = ""
if 'current_score' not in st.session_state:
    st.session_state.current_score = None
if 'previous_score' not in st.session_state:
    st.session_state.previous_score = None

col_submit, col_clear = st.columns(2)

with col_submit:
    if st.button("Submit", disabled=date_error is not None):
        st.session_state.response = ""
        st.session_state.current_score = None
        st.session_state.previous_score = None

        with st.spinner("Loading PR data..."):
            current_text  = collect_pr_text(user_login, current_from, current_to)
            previous_text = collect_pr_text(user_login, previous_from, previous_to)

        if not current_text and not previous_text:
            st.warning(
                "No PR data found for either period. "
                "Please run the scheduler bootstrap first:\n\n"
                "`python3 src/services/scheduler.py --mode bootstrap`"
            )
        else:
            if not current_text:
                st.info("No PR data found for the current period.")
            if not previous_text:
                st.info("No PR data found for the previous period.")

            with st.spinner("Generating comparative analysis..."):
                try:
                    llm_service = LLMService()
                    st.session_state.response = llm_service.generate_comparative_response(
                        user_login=user_login,
                        current_text=current_text,
                        previous_text=previous_text,
                        duration_label=date_range_label
                    )
                    st.session_state.current_score  = extract_score(st.session_state.response, "current")
                    st.session_state.previous_score = extract_score(st.session_state.response, "previous")
                except Exception as e:
                    st.error(f"LLM service failed: {e}")
                    sys.exit(1)

            log_usage(
                anon_id=anon_id,
                analyzed_github_user=user_login,
                date_range_label=date_range_label,
                current_from=current_from,
                current_to=current_to,
                previous_from=previous_from,
                previous_to=previous_to,
            )
            st.success("Comparative analysis generated.")

with col_clear:
    if st.button("Clear Output"):
        st.session_state.response = ""
        st.session_state.current_score = None
        st.session_state.previous_score = None

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
if st.session_state.response:
    st.write(f"### {user_label} — {date_range_label} Comparative Analysis")
    st.write(f"**Current period:** {current_from} → {current_to}")
    st.write(f"**Previous period:** {previous_from} → {previous_to}")

    # Score metrics
    cur  = st.session_state.current_score
    prev = st.session_state.previous_score
    col_cs, col_ps = st.columns(2)
    with col_cs:
        delta = f"{cur - prev:+.1f}" if cur is not None and prev is not None else None
        st.metric("**Current Period Score**", f"{cur:.0f} / 10" if cur is not None else "N/A", delta=delta)
    with col_ps:
        st.metric("**Previous Period Score**", f"{prev:.0f} / 10" if prev is not None else "N/A")

    pdf_bytes = generate_pdf_bytes(
        user_label=user_label,
        date_range_label=date_range_label,
        current_from=current_from,
        current_to=current_to,
        previous_from=previous_from,
        previous_to=previous_to,
        current_score=st.session_state.current_score,
        previous_score=st.session_state.previous_score,
        report_text=st.session_state.response,
    )
    filename = f"pr_scorecard_{user_label.replace(' ', '_')}_{date_range_label.replace(' ', '_')}.pdf"
    st.download_button(
        label="Download PDF Report",
        data=pdf_bytes,
        file_name=filename,
        mime="application/pdf",
    )

    st.divider()
    st.write(st.session_state.response)
