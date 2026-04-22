# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Working Rules

**Always ask for explicit yes/no approval before making any changes to any file.** Present what you plan to change and wait for confirmation before editing or writing.

## Project Stack
- Primary language: Python
- Key frameworks: FastAPI for APIs
- Always prefer Python-native solutions unless explicitly asked otherwise
- When building API services, include proper error handling, input validation, and API key management patterns

## Setup

```bash
uv sync              # install runtime deps + create .venv
uv sync --group dev  # also install pytest, black, flake8
```

## Commands

```bash
# First-ever run: fetch all PRs from last 2 years for all authors
uv run python src/services/scheduler.py --mode bootstrap

# Daily incremental fetch (new PRs + updated PRs from last 24 hrs + cleanup)
uv run python src/services/scheduler.py --mode incremental

# Launch the Streamlit web UI (reads from local files only — run bootstrap first)
uv run streamlit run src/ui/app.py

# Standalone fetch without scheduler (no date filter, all PRs ever)
uv run python src/services/pr_service.py

# Run tests
uv run pytest

# Lint
uv run flake8 src/

# Format
uv run black src/
```

### Cron (6 AM PST = 14:00 UTC)
```
0 14 * * * cd /path/to/app && uv run python src/services/scheduler.py --mode incremental
```

## Environment Setup

Requires a `.env` file with:

```
GITHUB_TOKEN=<GitHub Personal Access Token>
AZURE_OPENAI_API_BASE=<Azure OpenAI endpoint URL>
AZURE_OPENAI_API_KEY=<Azure OpenAI API key>
AZURE_OPENAI_API_VERSION=2024-02-01

# Langfuse observability (optional — app runs fine without these)
LANGFUSE_PUBLIC_KEY=<Langfuse public key>
LANGFUSE_SECRET_KEY=<Langfuse secret key>
LANGFUSE_HOST=https://cloud.langfuse.com
```

The virtual environment is at `.venv/` (Python 3.11).

## Architecture

This tool fetches GitHub PR review comments for a configured list of developers, organizes them into threaded conversations, saves them to text files, then uses Azure OpenAI GPT-4o via LangChain to summarize patterns, quality trends, and recurring issues. A Streamlit UI provides interactive access.

**Two-phase pipeline:**

1. **Data collection** (`main.py`): Fetches PRs authored by each person in `config.AUTHORS`, filters to specific repos (`foundational-data-models`, `standard-solution-views`), organizes comments into parent-reply threads via `CommentProcessor`, and writes to `data/combined_comments_<author>.txt`.

2. **Analysis** (`ui.py`): Streamlit UI reads the pre-built text files and sends them to `LLMService` (LangChain + Azure OpenAI GPT-4o), which streams back a structured summary including issue types, comment patterns, quality rating (1–10), and PR count.

## Source Layout and Layer Reasoning

```
src/
  api/          External API clients — anything that makes network calls
  core/         Pure business logic — no I/O, no framework deps, easiest to unit test
  db/           All persistence — SQLite (app.db) and fetch watermark (.fetch_state.json)
  services/     Application services — orchestrates across layers, owns workflows
  ui/           Presentation layer — everything Streamlit touches stays here
  config.py     Top-level config loaded by all layers
  utils.py      Shared stdlib helpers
```

**`api/`** — Only layer allowed to call GitHub's REST API. Nothing else in the codebase makes HTTP requests.

**`core/`** — Pure transformation logic. `comment_processor.py` threads comments; `comment_printer.py` formats and writes per-PR files. No external API calls, no DB access. Easiest to unit test in isolation.

**`db/`** — All persistence in one place. `database.py` owns SQLite (users, teams, usage log). `fetch_state.py` owns the JSON watermark file that tracks which PRs have been fetched.

**`services/`** — Orchestration layer. `pr_service.py` ties api + core together to fetch and save one PR. `scheduler.py` runs the bootstrap and daily incremental jobs. `llm_service.py` wraps LangChain + Azure OpenAI.

**`ui/`** — Streamlit UI and browser-side identity. `app.py` is the entry point. `user_identity.py` runs JavaScript in the browser via `streamlit-javascript` to collect the device fingerprint — kept in `ui/` because it has a direct Streamlit dependency.

**Key module responsibilities:**

- `src/config.py` — env vars, `AUTHORS` list, Azure model config
- `src/api/github_client.py` — GitHub API: PR search, line/review/issue comments
- `src/core/comment_processor.py` — chronological thread organization
- `src/core/comment_printer.py` — formats threads, writes per-PR text files
- `src/db/database.py` — SQLite: users, teams, team_members, usage_log
- `src/db/fetch_state.py` — JSON watermark: tracks fetched PR numbers per author
- `src/services/pr_service.py` — fetches + saves a single PR end-to-end
- `src/services/scheduler.py` — bootstrap + daily incremental job (3 passes)
- `src/services/llm_service.py` — LangChain AzureChatOpenAI comparative analysis
- `src/ui/app.py` — Streamlit application entry point
- `src/ui/user_identity.py` — browser fingerprint + localStorage anonymous ID

**Data flow:** GitHub API → `api/github_client` → `services/pr_service` → `core/comment_processor` + `core/comment_printer` → `data/prs/{author}/` → `ui/app.py` → `services/llm_service` → Streamlit output

**Multi-user identity:** On load, `ui/user_identity.py` runs JavaScript in the browser to collect a device fingerprint (User-Agent, screen, timezone, platform, language, color depth, CPU cores). SHA-256 of these signals becomes the anonymous ID — stable across VPN changes. Stored in browser localStorage for future visits. Identity and team configuration persisted in `data/app.db` (SQLite).

**Team management:** Each user defines their own team via the sidebar (add/remove GitHub usernames with display names). The main dropdown is populated from their own team. `config.AUTHORS` is used only by the background scheduler, not the UI.

## Comment Threading Design

GitHub exposes three distinct comment types per PR, with different threading capabilities:

| Type | API endpoint | Has `in_reply_to_id` | Forms threads? |
|---|---|---|---|
| Line comments | `GET /pulls/{pr}/comments` | Yes | Yes — inline diff reply chains |
| Review submissions | `GET /pulls/{pr}/reviews` | No | No — always standalone |
| Issue comments | `GET /issues/{pr}/comments` | No | No — always standalone |

**How `organize_comments()` works:** All three types are merged into one list, sorted by `created_at`, then threaded. The chronological sort applies to **thread roots only** — replies always stay bound to their parent thread. A reply at 10:05 to a root at 10:00 will not be displaced by a standalone comment at 10:03; the output order is by root timestamp, with replies nested inside their thread.

**No cross-type mixing:** `in_reply_to_id` in line comments always references another line comment's ID (GitHub IDs are globally unique). Review submissions and issue comments never carry `in_reply_to_id`, so they can never accidentally attach to a line thread. The `id_to_root` map in `organize_comments()` ensures deep replies (reply-to-reply) are flattened to the correct root thread rather than dropped.

**Example — threads are never mixed across boundaries:**

```
Input comments (mixed types, unsorted):
  A  [line,  10:00, id=100]              ← root
  B  [line,  10:05, id=101, reply_to=100] ← reply to A
  C  [issue, 10:03, id=200]              ← standalone

organize_comments processing (sorted by time):
  A → new root thread
  C → new root thread (no in_reply_to_id)
  B → attached to A's thread via in_reply_to_id=100

Output (threads ordered by root timestamp):
  Thread 1 [root A at 10:00]
    A: "This function looks wrong"
    ↳ B (10:05): "You're right, fixing it"   ← stays inside A's thread

  Thread 2 [root C at 10:03]
    C: "Overall the PR looks good"            ← standalone, not mixed with A/B
```

B (at 10:05) does **not** appear between A and C even though C's timestamp (10:03) falls between them — replies are always kept inside their parent thread.

**Known limitation:** If a review submission body references specific inline line threads (e.g. "see my inline comments"), the code cannot link them — GitHub treats them as separate API resources. They appear as adjacent but separate threads ordered by time.


## Observability

LLM calls are traced with [Langfuse](https://langfuse.com) using `@observe` decorators only — no callback handlers, no `langfuse_context`.

**Traced functions:**

| Decorator | File | What it captures |
|---|---|---|
| `@observe(name="api-analyze")` | `src/api/rest.py` | Root trace per `/analyze` API request |
| `@observe(name="api-analyze-preset")` | `src/api/rest.py` | Root trace per `/analyze/preset` API request |
| `@observe(name="pr-scorecard-analysis")` | `src/services/llm_service.py` | Full comparative analysis call (nested under API trace) |
| `@observe(name="maybe-summarize")` | `src/services/llm_service.py` | Per-period token budget check + conditional summarization |
| `@observe(name="summarize-chunk")` | `src/services/llm_service.py` | Individual chunk summarization LLM call |

**Trace hierarchy per request:**
```
api-analyze / api-analyze-preset
  └── pr-scorecard-analysis  (one per username)
        ├── maybe-summarize  (current period)
        │     └── summarize-chunk × N  (only if over token budget)
        ├── maybe-summarize  (previous period)
        │     └── summarize-chunk × N  (only if over token budget)
        └── [final LLM call]
```

Observability is optional — if the Langfuse env vars are absent, the app runs normally and traces are silently skipped.

## Code Quality or ## Development Practices
- After writing or editing Python code, always do a quick syntax check and test run before moving on
- When generating regex patterns, test them against a sample of the actual data/output format before integrating
- For text/PDF generation, always use UTF-8 compatible libraries and test with special characters

