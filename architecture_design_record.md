# Architecture Design Record

## Purpose

This document records the architectural decisions made for the PR Review Comments Summarization tool. It is intended as a reference for future development and onboarding.

---

## Problem Statement

The original application fetched GitHub PR comments at the time the user clicked Submit in the UI. This caused unacceptable latency because:
- GitHub API pagination is slow (multiple requests per PR)
- All authors × all PRs × all comment types were fetched in the foreground
- The user had to wait for all API calls before the LLM could even start

The goal is to give the user an effectively instant response on Submit (only the LLM call should be in the critical path).

---

## Core Principle: Separate Data Collection from the UI

The UI must never call the GitHub API. It reads only from local files.

```
┌─────────────────────────────────────────────────┐
│  BACKGROUND (scheduled, no user involvement)    │
│                                                 │
│  scheduler.py  ──►  GitHub API  ──►  data/prs/  │
│  (runs at 6 AM PST daily)                       │
└─────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│  UI (real-time, user-facing)                    │
│                                                 │
│  User selects range  ──►  reads data/prs/       │
│                       ──►  LLM  ──►  response   │
└─────────────────────────────────────────────────┘
```

---

## Storage Design

### Per-PR files (replaces single per-author file)

```
data/
  prs/
    abhi-j13/
      2024-03-15_1234.txt     ← YYYY-MM-DD_PR_NUMBER.txt
      2024-04-01_1235.txt
      ...
    mishal-qventus/
      2024-05-10_2001.txt
      ...
  .fetch_state.json
```

**Why per-PR files instead of one big file per author:**
- Incremental updates add new files without touching existing ones
- Date range filtering = select files by filename prefix (no parsing needed)
- Re-fetching an updated PR = overwrite just that one file, not the entire dataset
- A failed fetch cannot corrupt the full dataset

### Filename convention: `YYYY-MM-DD_PR_NUMBER.txt`

The date prefix is the PR creation date. This allows the UI to filter by date range
using only filenames — no file content needs to be read for filtering.

### `.fetch_state.json`

Tracks fetch history to enable incremental updates.

```json
{
  "last_full_fetch": "2026-04-06",
  "last_incremental_fetch": "2026-04-06T06:00:00Z",
  "authors": {
    "abhi-j13": {
      "fetched_pr_numbers": [1234, 1235, 1300],
      "oldest_pr_date": "2024-04-06"
    }
  }
}
```

---

## Scheduler Design (`scheduler.py`)

### Bootstrap (first ever run)

Run once when the application goes live in production.

```
for each author in AUTHORS:
    fetch all PRs created in last 2 years
    for each PR:
        fetch line comments + review comments + issue comments
        organize into chronological threads
        write to data/prs/{author}/{date}_{pr_number}.txt
    write fetch_state.json
```

### Daily incremental fetch (6 AM PST via cron)

Runs every day. Two passes:

**Pass 1 — New PRs (created in last 24 hours):**
```
query: is:pr + author:{author} + created:>=yesterday
for each result not in fetched_pr_numbers:
    fetch all comments → write new file → add to fetched_pr_numbers
```

**Pass 2 — Updated PRs (existing PRs that received new comments in last 24 hours):**
```
query: is:pr + author:{author} + updated:>=yesterday
for each result already in fetched_pr_numbers:
    re-fetch all comments → overwrite existing file
```

This second pass is critical because in active developer teams, older PRs
routinely receive new review comments days or weeks after they were first created.
Without this pass, those new comments would never be captured.

**Pass 3 — Rolling window cleanup:**
```
delete any PR files with date prefix older than today - 730 days
update oldest_pr_date in fetch_state.json
```

### Cron configuration

```
0 14 * * * cd /path/to/app && /path/to/.venv/bin/python src/scheduler.py
```
(14:00 UTC = 06:00 PST)

---

## UI Design

### What changes

The Submit button no longer calls the GitHub API. It only:
1. Reads PR files for the selected author within the selected date range (local disk, ~instant)
2. Concatenates them into a single prompt text
3. Calls the LLM (~15–30 seconds, unavoidable)
4. Displays the result

### Date range constraints

- **Minimum date**: `today - 730 days` (2-year rolling window — data before this is not kept)
- **Maximum date**: today
- Date pickers enforce this range so users cannot select a range with no data

### Mode: Preset vs Custom

| Mode | Behaviour |
|---|---|
| Preset (3M / 6M / 1Y) | Dates auto-calculated, no overlap guaranteed |
| Custom | Four date pickers with validation — `previous_to` must be strictly before `current_from` |

### Optional future optimisation: Pre-computed LLM summaries

For the three preset durations (3M / 6M / 1Y), summaries can be pre-generated each
morning as part of the scheduler run and cached. Then:
- Preset duration selected → instant (cached summary served directly)
- Custom date range selected → real-time LLM call (~15–30 sec)

---

## LLM Token Management (`llm_service.py`)

### Problem

GPT-4o has a 128k token context limit. For developers with many PRs over a long period
(e.g. 1 Year), the combined current + previous PR text can exceed this limit, causing a
`context_length_exceeded` error.

### Decision: Two-phase summarization (only when needed)

Rather than blindly truncating (which silently loses data) or always summarizing
(which adds unnecessary latency), the service applies summarization **only when a
period's text exceeds its token budget**.

```
Token budget breakdown:
  Model max:          128 000 tokens
  Prompt + output:  -   5 000 tokens (overhead reserve)
  Available:          123 000 tokens
  Per period:          61 500 tokens  (split evenly between current and previous)
```

### Flow

```
generate_comparative_response()
  │
  ├─ _maybe_summarize(current_text,  budget=61 500)
  │     ├─ count tokens via tiktoken (o200k_base encoding — matches GPT-4o)
  │     ├─ if within budget → return unchanged (zero extra LLM calls)
  │     └─ if over budget:
  │           split into 40 000-token chunks (on token boundaries, not characters)
  │           for each chunk → _summarize_chunk() → 1 LLM call per chunk
  │           join summaries → return condensed text
  │
  ├─ _maybe_summarize(previous_text, budget=61 500)  ← same logic
  │
  └─ final comparative analysis prompt → 1 LLM call → response
```

### Why split on token boundaries, not character/line boundaries

Splitting on characters or lines risks cutting a PR comment mid-sentence, making the
last item in a chunk incomplete and misleading to the summarizer. Token-boundary splits
guarantee each chunk is a valid, complete UTF-8 string that fits exactly within the
model's input window.

### Why 40 000 tokens per summary chunk

- Well below the 61 500 per-period budget, so the summary output of each chunk fits
  comfortably in the final prompt
- Large enough that most over-budget cases need only 2 chunks (2 extra LLM calls)
- Leaves headroom for the summarizer's own output tokens

### Summarization prompt design

The summarizer is explicitly instructed to preserve:
- Every distinct issue type
- Recurring patterns across PRs
- Quality signals
- PR numbers (for traceability)

This ensures the final comparative analysis receives a complete picture of the period,
just in a more compact form.

### Extra LLM calls

| Scenario | Extra calls |
|---|---|
| Both periods within budget | 0 |
| One period over budget, fits in 1 chunk | 1 |
| Both periods over budget, 2 chunks each | 4 |

---

## LLM Observability (Langfuse)

### Goal

Provide visibility into LLM call latency, token usage, and error rates per request without coupling the application code to any specific tracing framework.

### Decision: `@observe` decorators only

Langfuse supports two integration styles for LangChain applications:

| Style | How it works | Requires |
|---|---|---|
| `CallbackHandler` | Passes a callback into every `.invoke()` call | `langchain.callbacks` (removed in LangChain 1.x) |
| `@observe` decorator | Wraps Python functions as Langfuse spans | Only `langfuse` package — no LangChain version dependency |

The codebase uses LangChain 1.x. The `CallbackHandler` approach requires `langchain.callbacks.base` which was removed in LangChain 1.0. The `@observe` decorator approach is framework-agnostic and works with any version. It was chosen to avoid tying the observability layer to a specific LangChain version.

### Instrumented functions

| Span name | Function | Layer |
|---|---|---|
| `api-analyze` | `rest.analyze()` | REST API |
| `api-analyze-preset` | `rest.analyze_preset()` | REST API |
| `pr-scorecard-analysis` | `LLMService.generate_comparative_response()` | Service |
| `maybe-summarize` | `LLMService._maybe_summarize()` | Service |
| `summarize-chunk` | `LLMService._summarize_chunk()` | Service |

### Trace hierarchy

Each API request produces one root trace. All LLM calls are nested under it as child spans:

```
api-analyze / api-analyze-preset          ← root trace (one per HTTP request)
  └── pr-scorecard-analysis               ← one span per requested username
        ├── maybe-summarize (current)     ← records whether summarization fired
        │     └── summarize-chunk × N    ← only created if text exceeds 61 500 tokens
        ├── maybe-summarize (previous)    ← same
        │     └── summarize-chunk × N
        └── [final analysis LLM call]    ← the comparative analysis prompt
```

### What can be monitored

- **Latency**: end-to-end per request, and per LLM call individually
- **Summarization events**: `maybe-summarize` spans reveal when the token budget was exceeded and how many extra LLM calls were made
- **Error rate**: failed spans are flagged in Langfuse automatically
- **Cost tracking**: Langfuse infers token counts and cost from the model name + output when using Azure OpenAI

### Configuration

Three environment variables, all optional. The application runs normally if they are absent — Langfuse initializes but silently no-ops without valid credentials.

```
LANGFUSE_PUBLIC_KEY=<public key>
LANGFUSE_SECRET_KEY=<secret key>
LANGFUSE_HOST=https://cloud.langfuse.com   # or self-hosted URL
```

---

## Comment Threading Design

GitHub exposes three distinct comment types per PR with different threading capabilities:

| Type | API endpoint | Has `in_reply_to_id` | Forms threads? |
|---|---|---|---|
| Line comments | `GET /pulls/{pr}/comments` | Yes | Yes — inline diff reply chains |
| Review submissions | `GET /pulls/{pr}/reviews` | No | No — always standalone |
| Issue comments | `GET /issues/{pr}/comments` | No | No — always standalone |

**How `organize_comments()` works:** All three types are merged into one list, sorted
by `created_at`, then threaded. The chronological sort applies to **thread roots only**
— replies always stay bound to their parent thread. A reply at 10:05 to a root at 10:00
will not be displaced by a standalone comment at 10:03; output order is by root
timestamp, with replies nested inside their thread.

**Example — threads are never mixed across boundaries:**

```
Input comments (mixed types, unsorted):
  A  [line,  10:00, id=100]               ← root
  B  [line,  10:05, id=101, reply_to=100] ← reply to A
  C  [issue, 10:03, id=200]               ← standalone

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

B (at 10:05) does **not** appear between A and C even though C's timestamp (10:03)
falls between them — replies are always kept inside their parent thread.

**No cross-type mixing:** `in_reply_to_id` in line comments always references another
line comment's ID (GitHub IDs are globally unique). Review submissions and issue
comments never carry `in_reply_to_id`, so they can never accidentally attach to a line
thread. The `id_to_root` map in `organize_comments()` ensures deep replies
(reply-to-reply) are flattened to the correct root thread rather than dropped.

**Known limitation:** If a review submission body references specific inline line threads
(e.g. "see my inline comments"), the code cannot link them — GitHub treats them as
separate API resources. They appear as adjacent but separate threads ordered by time.

---

## Files Overview

| File | Responsibility |
|---|---|
| `src/scheduler.py` | Bootstrap + daily incremental fetch + rolling window cleanup |
| `src/fetch_state.py` | Read / write / query `.fetch_state.json` |
| `src/github_client.py` | GitHub API wrapper (PR search with date + update filters) |
| `src/comment_processor.py` | Thread organization, chronological sorting |
| `src/comment_printer.py` | Format threads and write per-PR text files |
| `src/main.py` | Orchestrates fetch → process → save for one author+period |
| `src/llm_service.py` | LangChain + Azure OpenAI GPT-4o, comparative analysis prompt |
| `src/ui.py` | Streamlit UI — reads local files only, no GitHub API calls |
| `src/config.py` | Env vars, author list, model config |
| `data/prs/{author}/` | Per-PR comment thread files |
| `data/.fetch_state.json` | Fetch watermark and dedup registry |
