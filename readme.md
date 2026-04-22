# PR Review Comments Summarization

A tool that fetches GitHub pull request review comments for a configured list of developers, organizes them into chronological conversation threads, and uses Azure OpenAI GPT-4o to generate comparative quality analyses across two time periods.

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Architecture Overview](#architecture-overview)
3. [Entry Points](#entry-points)
4. [REST API](#rest-api)
5. [File-by-File Reference](#file-by-file-reference)
6. [Dependency Lineage](#dependency-lineage)
7. [Data Flow](#data-flow)
8. [Data Directory Layout](#data-directory-layout)
9. [Observability](#observability)
10. [Environment Setup](#environment-setup)

---

## Quick Start

```bash
# 1. Copy and fill in credentials
cp .env.example .env

# 2. Install dependencies
pip install -r requirements.txt

# 3. First-ever run: fetch all PRs from the last 2 years (run once)
python3 src/scheduler.py --mode bootstrap

# 4. Launch the UI (reads from local files — no GitHub API calls)
python3 -m streamlit run src/ui.py

# 5. Add to cron for daily refresh at 6 AM PST (14:00 UTC)
# 0 14 * * * cd /path/to/app && /path/to/.venv/bin/python src/scheduler.py --mode incremental
```

---

## Architecture Overview

The system is split into two completely independent layers:

```
┌──────────────────────────────────────────────────────────┐
│  BACKGROUND LAYER  (scheduler.py — no user involvement)  │
│                                                          │
│  GitHub API  →  comment_processor  →  data/prs/{author}/│
│                                                          │
│  Runs at 6 AM PST daily via cron.                        │
│  The UI never calls the GitHub API.                      │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│  UI LAYER  (ui.py — real-time, user-facing)              │
│                                                          │
│  data/prs/{author}/  →  LLMService  →  Streamlit output │
│                                                          │
│  On Submit: reads local files (~instant) + LLM (~15-30s) │
└──────────────────────────────────────────────────────────┘
```

The UI never waits for GitHub API calls. All PR data is pre-fetched and stored locally as plain-text files. The only latency on Submit is the LLM call itself.

---

## Entry Points

There are three entry points. Each serves a distinct purpose:

### 1. `python3 src/scheduler.py --mode bootstrap`
**When to run:** Once, when the application first goes live in production.
Fetches all PRs authored by everyone in `config.AUTHORS` from the last 2 years. Writes one text file per PR to `data/prs/{author}/`. Safe to re-run — already-fetched PRs are skipped.

### 2. `python3 src/scheduler.py --mode incremental`
**When to run:** Daily via cron (6 AM PST).
Three-pass job:
- Pass 1: fetch newly created PRs (last 24 hrs)
- Pass 2: re-fetch PRs updated in the last 24 hrs (older PRs that received new review comments)
- Pass 3: delete PR files older than 730 days (rolling 2-year window)

### 3. `python3 -m streamlit run src/ui.py`
**When to run:** Any time after bootstrap has completed.
Launches the web UI. Reads only from local files. No GitHub API calls.

### 4. `python3 src/main.py` *(standalone utility)*
Fetches PRs for all authors with no date filter. Used for ad-hoc runs outside the scheduler. Does not use or update `fetch_state.json`.

---

## REST API

The FastAPI service exposes the analysis as a paid API. Customers authenticate with an API key; you manage keys with an admin secret.

### Start the server

```bash
uv run uvicorn src.api.rest:app --host 0.0.0.0 --port 8000
```

Interactive docs available at `http://localhost:8000/docs`.

---

### Admin — API key management

All admin endpoints require the `X-Admin-Secret` header (set `ADMIN_SECRET` in `.env`).

**Create a key**
```bash
curl -X POST http://localhost:8000/admin/keys \
  -H "X-Admin-Secret: <ADMIN_SECRET>" \
  -H "Content-Type: application/json" \
  -d '{"label": "customer-name"}'
```
```json
{"key": "prs_abc123...", "label": "customer-name", "created_at": "...", "is_active": true}
```

**List all keys**
```bash
curl http://localhost:8000/admin/keys \
  -H "X-Admin-Secret: <ADMIN_SECRET>"
```

**Revoke a key**
```bash
curl -X DELETE http://localhost:8000/admin/keys/prs_abc123... \
  -H "X-Admin-Secret: <ADMIN_SECRET>"
```

---

### `POST /analyze` — custom date range

Accepts explicit date ranges and one or more GitHub usernames.

#### For multiple users
```bash
curl -X POST http://localhost:8000/analyze \
  -H "X-API-Key: prs_abc123..." \
  -H "Content-Type: application/json" \
  -d '{
    "github_usernames": ["john-doe1", "jane-doe2"],
    "current_from": "2024-01-01",
    "current_to": "2024-03-31",
    "previous_from": "2023-10-01",
    "previous_to": "2023-12-31",
    "generate_pdf": false
  }'
```

#### For single users
```bash
curl -X POST http://localhost:8000/analyze \
  -H "X-API-Key: prs_abc123..." \
  -H "Content-Type: application/json" \
  -d '{
    "github_usernames": ["john-doe1"],
    "current_from": "2024-01-01",
    "current_to": "2024-03-31",
    "previous_from": "2023-10-01",
    "previous_to": "2023-12-31",
    "generate_pdf": false
  }'
```

**Python**
```python
import requests

response = requests.post(
    "http://localhost:8000/analyze",
    headers={"X-API-Key": "prs_abc123..."},
    json={
        "github_usernames": ["john-doe", "jane-doe"],
        "current_from": "2024-01-01",
        "current_to": "2024-03-31",
        "previous_from": "2023-10-01",
        "previous_to": "2023-12-31",
    },
)
for result in response.json():
    if result["error"]:
        print(f"{result['github_username']}: {result['error']}")
    else:
        print(f"{result['github_username']}: {result['current_score']} / 10")
        print(result["analysis"])
```

---

### `POST /analyze/preset` — preset duration

Computes date ranges automatically from a named duration. Accepted values: `"3 Months"`, `"6 Months"`, `"1 Year"`.

| Preset | Current period | Previous period |
|---|---|---|
| 3 Months | today-90 → today | today-180 → today-91 |
| 6 Months | today-180 → today | today-360 → today-181 |
| 1 Year | today-365 → today | today-730 → today-366 |

```bash
curl -X POST http://localhost:8000/analyze/preset \
  -H "X-API-Key: prs_abc123..." \
  -H "Content-Type: application/json" \
  -d '{
    "github_usernames": ["john-doe", "jane-doe"],
    "duration_label": "3 Months",
    "generate_pdf": false
  }'
```

#### Generate PDF for single user in current folder from where this api is being called by user
```bash
curl -X POST http://localhost:8000/analyze/preset \
  -H "X-API-Key: prs_abc123..." \
  -H "Content-Type: application/json" \
  -d '{
    "github_usernames": ["john-doe"],
    "duration_label": "3 Months",
    "generate_pdf": true
  }' \
  -O -J
```

#### Generate PDF for single user in given folder from where this api is being called by user
```bash
curl -X POST http://localhost:8000/analyze/preset \
  -H "X-API-Key: prs_abc123..." \
  -H "Content-Type: application/json" \
  -d '{
    "github_usernames": ["john-doe"],
    "duration_label": "3 Months",
    "generate_pdf": true
  }' \
  --output ~/Downloads/pr_scorecard.pdf
```

**Python**
```python
import requests

response = requests.post(
    "http://localhost:8000/analyze/preset",
    headers={"X-API-Key": "prs_abc123..."},
    json={
        "github_usernames": ["john-doe", "jane-doe"],
        "duration_label": "3 Months",   # "3 Months" | "6 Months" | "1 Year"
    },
)
for result in response.json():
    if result["error"]:
        print(f"{result['github_username']}: {result['error']}")
    else:
        print(f"{result['github_username']}: {result['current_score']} / 10")
        print(result["analysis"])
```

---

### Response schema

Both endpoints return a list — one entry per username.

```json
[
  {
    "github_username": "john-doe",
    "duration_label": "3 Months",
    "current_from": "2024-01-01",
    "current_to": "2024-03-31",
    "previous_from": "2023-10-01",
    "previous_to": "2023-12-31",
    "current_score": 7.0,
    "previous_score": 6.0,
    "analysis": "...",
    "error": null
  }
]
```

If a username has no local PR data, `error` is set and `analysis` is empty — the rest of the list still returns.

---

## File-by-File Reference

### `src/config.py`
**Purpose:** Central configuration. Loaded by almost every other module.

| Symbol | Type | Description |
|---|---|---|
| `AUTHORS` | `list[str]` | GitHub usernames to track (e.g. `"abhi-j13"`) |
| `GITHUB_TOKEN` | `str` | Personal Access Token read from `.env` |
| `AZURE_OPENAI_API_BASE` | `str` | Azure OpenAI endpoint URL from `.env` |
| `AZURE_OPENAI_API_KEY` | `str` | Azure OpenAI API key from `.env` |
| `AZURE_OPENAI_API_VERSION` | `str` | Hardcoded to `"2024-02-01"` |
| `AZURE_MODEL_NAME` | `str` | `"gpt-4o"` |
| `HEADERS` | `dict` | HTTP headers for all GitHub API requests |
| `DATA_DIR` | `str` | `"data/"` — legacy path constant |

**Imports:** `dotenv`, `os`
**Imported by:** `github_client`, `llm_service`, `main`, `scheduler`

---

### `src/github_client.py`
**Purpose:** Thin wrapper around the GitHub REST API. All network calls to GitHub are made here.

#### `class GitHubClient`

#### `search_prs_by_author(author, date_from, date_to, updated_after, per_page, max_pages)`
Searches GitHub for pull requests by a given author using the Issues Search API.

- `date_from` / `date_to`: `datetime.date` — filters by PR **creation** date. Appends `created:START..END` to the query.
- `updated_after`: `datetime.date` — filters by **last-updated** date. Appends `updated:>=DATE` to the query. Used in the incremental scheduler Pass 2 to find PRs that received new comments since the last run.
- Paginates automatically up to `max_pages` (default 10 × 100 results = 1000 PRs max).
- Returns a list of raw GitHub issue/PR dicts.

#### `get_line_comments(owner, repo, pr_number)`
Calls `GET /repos/{owner}/{repo}/pulls/{pr_number}/comments`.
Returns inline diff comments — comments left on specific lines of changed code. These are the only comment type that supports threading via `in_reply_to_id`.

#### `get_review_comments(owner, repo, pr_number)`
Calls `GET /repos/{owner}/{repo}/pulls/{pr_number}/reviews`.
Returns review submission events (APPROVE, CHANGES_REQUESTED, COMMENT). These have a `submitted_at` field (not `created_at`) and no `in_reply_to_id` — they are always standalone.

#### `get_issue_comments(owner, repo, pr_number)`
Calls `GET /repos/{owner}/{repo}/issues/{pr_number}/comments`.
Returns general PR discussion comments (not tied to any specific line). Have `created_at`, no `in_reply_to_id`.

#### `_get_json(url)` *(private)*
Makes a single authenticated GET request and returns the parsed JSON. Used by all three comment-fetching methods.

**Imports:** `requests`, `time`, `config.HEADERS`
**Imported by:** `main`, `scheduler`

---

### `src/fetch_state.py`
**Purpose:** Manages `data/.fetch_state.json` — the persistent record of which PRs have been fetched. Prevents redundant API calls on every scheduler run.

The design loads the entire state into memory at the start of a run, operates in-memory, then flushes once at the end. This avoids hundreds of disk reads/writes when processing a large backlog.

#### `class FetchState`

#### `__init__()`
Loads `data/.fetch_state.json` into `self._state`. If the file does not exist, starts with an empty state structure.

#### `save()`
Serializes `self._state` back to `data/.fetch_state.json`. Must be called explicitly at the end of each scheduler run.

#### `is_fetched(author, pr_number) → bool`
Returns `True` if `pr_number` is in `self._state["authors"][author]["fetched_pr_numbers"]`.

#### `mark_fetched(author, pr_number, pr_created_date)`
Adds `pr_number` to the fetched list and updates `oldest_pr_date` if this PR is older than any previously recorded PR.

#### `remove_pr(author, pr_number)`
Removes a PR number from the fetched list. Called in Pass 3 of the incremental job when a file is deleted for being outside the rolling window.

#### `get_last_incremental_fetch_date() → date | None`
Returns the date of the last successful incremental run. Available for more sophisticated incremental logic.

#### `set_last_full_fetch()` / `set_last_incremental_fetch()`
Record the current UTC timestamp in the state file under `last_full_fetch` / `last_incremental_fetch`.

#### `_author_entry(author)` *(private)*
Returns the state sub-dict for an author, creating it if it doesn't exist.

**Imports:** `json`, `os`, `datetime`
**Imported by:** `scheduler`

---

### `src/comment_processor.py`
**Purpose:** Takes a flat list of raw GitHub comment dicts (mixed types, unsorted) and returns an ordered list of conversation threads.

#### `organize_comments(comments) → list`
The core threading algorithm.

**Input:** A flat list of comment dicts. All must have a `created_at` field (normalize before calling). May contain line comments, review submissions, and issue comments mixed together.

**Algorithm:**
1. Sort all comments chronologically by `created_at`.
2. Iterate in sorted order. For each comment:
   - If `in_reply_to_id` is `None` → create a new thread root.
   - If `in_reply_to_id` points to a known comment → attach to that comment's root thread (flattens deep nesting: reply-to-reply is attached to the original root, not lost).
   - If `in_reply_to_id` points to an unknown comment (orphan) → treat as a new root.
3. Return threads as a list sorted by root comment's `created_at`.

**Key guarantee:** Replies always stay inside their parent thread. A reply at 10:05 is never displaced from its root at 10:00 by a standalone comment at 10:03. Thread order is by root timestamp only.

**Output:** `[{'comment': {...}, 'replies': [{...}, ...]}, ...]`

#### `_parse_time(ts) → datetime` *(private)*
Converts an ISO 8601 string (`"2024-01-15T10:05:00Z"`) to a `datetime` object for sorting. Returns `datetime.min` for missing timestamps.

**Imports:** `datetime`
**Imported by:** `main`

---

### `src/comment_printer.py`
**Purpose:** Formats organized comment threads into human-readable text and writes them to disk.

#### `save_pr_threads(pr_title, pr_number, threads, abs_file_path)`
Writes all threads for a single PR to `abs_file_path` in `"w"` (overwrite) mode.

Since each PR has its own dedicated file (`data/prs/{author}/YYYY-MM-DD_{pr_number}.txt`), there is no need to append — a fresh write is always correct. Re-fetching an updated PR simply overwrites the old file.

**Output format per thread:**
```
[Line Comment | path/to/file.py:42 | 2024-01-15 10:00]
  alice: This function looks wrong
    ↳ bob (2024-01-15 10:05): You're right, fixing it

[Review | CHANGES_REQUESTED | 2024-01-15 10:30]
  charlie: Please add null checks throughout

[Comment | 2024-01-15 11:00]
  dave: LGTM after changes
```

Empty-body entries (e.g. silent approvals with no comment text) are filtered out.

#### `_fmt_time(ts) → str` *(private)*
Converts `"2024-01-15T10:05:00Z"` → `"2024-01-15 10:05"` for readable output.

**Imports:** `os`
**Imported by:** `main`

---

### `src/main.py`
**Purpose:** Orchestration layer. Ties together GitHub fetching, comment processing, and file writing for a single PR or a set of PRs. Called by the scheduler.

#### `fetch_and_save_pr(client, author, pr, owner, repo, overwrite)`
The atomic unit of work. Processes one PR end-to-end:

1. Checks if `repo` is in `ALLOWED_REPOS` — skips if not.
2. Constructs the output path: `data/prs/{author}/{YYYY-MM-DD}_{pr_number}.txt`.
3. If `overwrite=False` and the file already exists → skips (used in bootstrap and Pass 1).
4. If `overwrite=True` → re-fetches regardless (used in Pass 2 for updated PRs).
5. Fetches line comments, review comments, issue comments via `GitHubClient`.
6. Calls `_normalize_comments()` on each type.
7. Merges all three into one list and passes to `organize_comments()`.
8. Calls `save_pr_threads()` to write the output file.

#### `_normalize_comments(comments, comment_type)` *(private)*
Tags each comment with `_type` (`'line'`, `'review'`, or `'issue'`) and renames `submitted_at` → `created_at` for review submissions so all types have a uniform timestamp field before merging.

#### `process_author_prs(client, author, date_from, date_to)`
Fetches all PRs for one author within an optional date range, then calls `fetch_and_save_pr()` for each. Used by the standalone `main()` entrypoint.

#### `main()`
Iterates over `config.AUTHORS` and calls `process_author_prs()` for each with no date filter. Standalone utility — does not interact with `fetch_state.json`.

**Constants:**
- `ALLOWED_REPOS`: `{"foundational-data-models", "standard-solution-views"}` — only PRs from these repos are processed.
- `DATA_PRS_DIR`: absolute path to `data/prs/` — imported by `scheduler` and `ui`.

**Imports:** `os`, `config`, `github_client`, `comment_processor`, `comment_printer`
**Imported by:** `scheduler`, `ui` (for `DATA_PRS_DIR` constant)

---

### `src/scheduler.py`
**Purpose:** The background data pipeline. Runs independently of the UI. Keeps `data/prs/` up to date so the UI never needs to call GitHub.

#### `bootstrap()`
**Run once when the app goes live.**
- Fetches all PRs for all authors created in the last 2 years (`date_from = today - 730 days`).
- Skips PRs already recorded in `FetchState` (safe to re-run after failures).
- Calls `fetch_and_save_pr(..., overwrite=False)` for each new PR.
- Updates `FetchState` and calls `state.save()`.

#### `incremental()`
**Run daily at 6 AM PST.**

Three passes per author:

**Pass 1 — New PRs (created in last 24 hrs)**
- Queries `search_prs_by_author(date_from=yesterday, date_to=today)`.
- For each result not already in `FetchState`: fetch, save, mark as fetched.

**Pass 2 — Updated PRs (new comments on old PRs)**
- Queries `search_prs_by_author(updated_after=yesterday)` — finds any PR by this author that was touched in the last 24 hours, regardless of when it was created.
- For each result already in `FetchState` and within the rolling window: re-fetch with `overwrite=True` to capture new comments.
- This pass is critical for active teams where review discussions on older PRs are common.

**Pass 3 — Rolling window cleanup**
- Scans `data/prs/{author}/` for files with a date prefix older than `today - 730 days`.
- Deletes those files and removes the corresponding PR numbers from `FetchState`.

**Constant:** `ROLLING_WINDOW_DAYS = 730`

**Imports:** `argparse`, `os`, `sys`, `datetime`, `config`, `github_client`, `fetch_state`, `main`

---

### `src/llm_service.py`
**Purpose:** Wraps Azure OpenAI GPT-4o via LangChain. Constructs prompts and returns analysis text.

#### `class LLMService`

#### `__init__()`
Initializes the `AzureChatOpenAI` LangChain client using credentials from `config`. Sets `temperature=0` and `top_p=0.01` for deterministic, focused output.

#### `generate_llm_response(user_login)`
Legacy single-period analysis method. Reads `data/combined_comments_{user_login}.txt` and asks GPT-4o for: types of issues identified, whether comments were repetitive, quality trend over time, conclusion, issues to fix, PR count, and quality rating (1–10).

#### `generate_comparative_response(user_login, current_text, previous_text, duration_label)`
Two-period comparative analysis. Receives pre-read text strings (the UI reads and concatenates PR files before calling this — `llm_service` does no file I/O).

Sends both periods to GPT-4o in a single prompt asking for:
- Current period summary (issue types, repetition, PR count, quality 1–10)
- Previous period summary (same dimensions)
- Comparative analysis (improvements/regressions, recurring issues, overall improvement score, key recommendation)

`duration_label` (e.g. `"3 Months"` or `"Custom Range"`) is embedded in the prompt for context.

**Imports:** `openai`, `langchain_community`, `langchain`, `langchain_core`, `config`
**Imported by:** `ui`

---

### `src/ui.py`
**Purpose:** Streamlit web application. The user-facing interface. Makes zero GitHub API calls.

#### `collect_pr_text(author_login, date_from, date_to) → str`
Scans `data/prs/{author_login}/` for `.txt` files whose filename date prefix (`YYYY-MM-DD`) falls within `[date_from, date_to]`. Concatenates and returns all matching file contents.

Date filtering is done entirely on the filename — no file content is parsed — making this very fast even with hundreds of PR files.

#### UI Layout

```
[ Select User ▼ ]   [ Preset Duration | Custom Date Range (radio) ]

If Preset:   [ Time Duration ▼: 3 Months | 6 Months | 1 Year ]
If Custom:   Current Period:  [ From 📅 ] [ To 📅 ]
             Previous Period: [ From 📅 ] [ To 📅 ]

[ Submit ]  [ Clear Output ]

### {User} — {Range} Comparative Analysis
Current period: YYYY-MM-DD → YYYY-MM-DD
Previous period: YYYY-MM-DD → YYYY-MM-DD
──────────────────────────────────────────
{LLM response}
```

#### Date validation (blocks Submit if any fail)
- Current `From` must be before current `To`
- Previous `From` must be before previous `To`
- `previous_to` must be strictly before `current_from` — no overlap, no touching

#### Date range constraints
- All date pickers enforce `min_value = today - 730 days` (2-year rolling window)
- Preset durations auto-calculate non-overlapping ranges:

| Preset | Current period | Previous period |
|---|---|---|
| 3 Months | today-90 → today | today-180 → today-91 |
| 6 Months | today-180 → today | today-360 → today-181 |
| 1 Year | today-365 → today | today-730 → today-366 |

#### Submit flow
1. `collect_pr_text()` for current period → `current_text` (~instant, local disk)
2. `collect_pr_text()` for previous period → `previous_text` (~instant, local disk)
3. If both empty: display warning to run bootstrap first
4. `LLMService.generate_comparative_response()` → response (~15–30 sec)

**Imports:** `os`, `sys`, `uuid`, `datetime`, `streamlit`, `llm_service`, `main.DATA_PRS_DIR`

---

### `src/utils.py`
**Purpose:** Standalone helper utilities. Not used in the current main pipeline.

#### `save_json(filename, data)`
Serializes `data` to a JSON file at the given path.

#### `count_words(filename) → int`
Reads a text file and returns the total word count. Originally used to check if a comments file was within LLM token limits.

**Imports:** `json`, `os`
**Status:** Not imported by any active pipeline module.

---

### `src/pr_parser.py`
**Purpose:** Legacy utility, not used in the current pipeline. Originally loaded PR comment JSON files and converted them to threaded text for early LLM experiments.

#### `load_and_combine_pr_comments(data_dir) → list[str]`
Reads all `.json` files in `data_dir` and calls `extract_threaded_comments()` on each.

#### `extract_threaded_comments(pr_data) → list[str]`
Converts a PR comment dict (with `line_comments`, `review_comments`, `issue_comments` keys) into a list of formatted thread strings.

#### `comments_thread(comment_block) → str`
Formats a single comment + its replies into a readable string.

**Status:** Unused. Superseded by `comment_processor.py` + `comment_printer.py`.

---

## Dependency Lineage

```
config.py
  └── consumed by: github_client, llm_service, main, scheduler

github_client.py
  └── depends on: config
  └── consumed by: main, scheduler

fetch_state.py
  └── depends on: stdlib only
  └── consumed by: scheduler

comment_processor.py
  └── depends on: stdlib only
  └── consumed by: main

comment_printer.py
  └── depends on: stdlib only
  └── consumed by: main

main.py
  └── depends on: config, github_client, comment_processor, comment_printer
  └── consumed by: scheduler, ui (DATA_PRS_DIR constant only)

scheduler.py
  └── depends on: config, github_client, fetch_state, main
  └── consumed by: cron / CLI only

llm_service.py
  └── depends on: config, openai, langchain
  └── consumed by: ui

ui.py
  └── depends on: llm_service, main (DATA_PRS_DIR), streamlit
  └── consumed by: Streamlit runner only

utils.py       → not imported by any active module
pr_parser.py   → not imported by any active module
```

---

## Data Flow

```
GitHub API
    │
    │  search_prs_by_author()        ← date/updated filters applied here
    ▼
GitHubClient
    │
    │  get_line_comments()           ← inline diff comments, support threading
    │  get_review_comments()         ← review submissions (APPROVE etc.), standalone
    │  get_issue_comments()          ← general PR discussion, standalone
    ▼
main._normalize_comments()           ← tags _type, renames submitted_at → created_at
    │
    ▼
comment_processor.organize_comments()
    │  - merges all 3 types into one list
    │  - sorts by created_at
    │  - builds parent→reply threads via in_reply_to_id
    │  - flattens deep nesting (reply-to-reply → root thread)
    │  - returns list of threads sorted by root timestamp
    ▼
comment_printer.save_pr_threads()
    │  - writes data/prs/{author}/YYYY-MM-DD_{pr_number}.txt
    │  - always "w" mode (one file per PR, overwrite on re-fetch)
    ▼
data/prs/{author}/
    │
    │  (when user clicks Submit in UI)
    │
    ▼
ui.collect_pr_text()                 ← filters files by filename date prefix, no parsing
    │
    ▼
llm_service.generate_comparative_response()
    │  - builds prompt with current + previous period text
    │  - calls Azure OpenAI GPT-4o via LangChain
    ▼
Streamlit UI output
```

---

## Data Directory Layout

```
data/
  prs/
    abhi-j13/
      2024-03-15_1234.txt       ← one file per PR: YYYY-MM-DD_{pr_number}.txt
      2024-04-01_1235.txt
      ...
    mishal-qventus/
      ...
  .fetch_state.json             ← tracks fetched PR numbers + last fetch timestamps
```

### Per-PR file format

```
=== PR #1234: Fix null pointer in silver models ===

[Line Comment | models/silver/file.sql:42 | 2024-03-15 10:00]
  reviewer_alice: This join will fail on nulls
    ↳ author_bob (2024-03-15 10:15): Fixed, added COALESCE

[Review | CHANGES_REQUESTED | 2024-03-15 10:30]
  reviewer_charlie: Please add unit test coverage for this edge case

[Comment | 2024-03-15 14:00]
  reviewer_alice: LGTM after the fix

=== PR #1234 END ===
```

### `.fetch_state.json` format

```json
{
  "last_full_fetch": "2026-04-06T14:00:00Z",
  "last_incremental_fetch": "2026-04-07T14:00:00Z",
  "authors": {
    "abhi-j13": {
      "fetched_pr_numbers": [1234, 1235, 1300],
      "oldest_pr_date": "2024-04-06"
    }
  }
}
```

---

## Observability

All LLM calls are traced with [Langfuse](https://langfuse.com) using `@observe` decorators. Each API request produces one root trace with all LLM calls nested inside it.

**Trace hierarchy:**
```
api-analyze / api-analyze-preset          ← one trace per API request
  └── pr-scorecard-analysis               ← one span per username
        ├── maybe-summarize (current)     ← token budget check
        │     └── summarize-chunk × N    ← only if over 61 500 token budget
        ├── maybe-summarize (previous)
        │     └── summarize-chunk × N
        └── [final comparative analysis LLM call]
```

**Setup:** Add to `.env`:
```
LANGFUSE_PUBLIC_KEY=<your public key>
LANGFUSE_SECRET_KEY=<your secret key>
LANGFUSE_HOST=https://cloud.langfuse.com
```

Observability is optional — the app runs normally without these keys. Traces are visible at `https://cloud.langfuse.com` (or your self-hosted Langfuse instance).

---

## Environment Setup

Create a `.env` file in the project root:

```
GITHUB_TOKEN=<GitHub Personal Access Token with repo read access>
AZURE_OPENAI_API_BASE=<Azure OpenAI endpoint, e.g. https://your-resource.openai.azure.com/>
AZURE_OPENAI_API_KEY=<Azure OpenAI API key>
AZURE_OPENAI_API_VERSION=2024-02-01

# Langfuse observability (optional)
LANGFUSE_PUBLIC_KEY=<your public key>
LANGFUSE_SECRET_KEY=<your secret key>
LANGFUSE_HOST=https://cloud.langfuse.com
```

Virtual environment is at `.venv/` (Python 3.11).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
