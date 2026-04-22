"""
PR Comments Scheduler
=====================
Runs independently of the Streamlit UI. Keeps data/prs/ up to date so the
UI never has to call the GitHub API.

Usage:
    # First ever run — fetches all PRs from the last 2 years for all authors
    python3 src/services/scheduler.py --mode bootstrap

    # Daily job (cron at 6 AM PST = 14:00 UTC)
    python3 src/services/scheduler.py --mode incremental

Cron entry:
    0 14 * * * cd /path/to/app && /path/to/.venv/bin/python src/services/scheduler.py --mode incremental

Two-year rolling window: PR files older than 730 days are deleted automatically
during each incremental run.

Parallelism: comment fetching (3 API calls per PR) runs in a thread pool.
MAX_FETCH_WORKERS controls how many PRs are fetched concurrently.
FetchState is only mutated in the main thread (after futures complete) to
avoid race conditions on the JSON watermark file.
"""

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import AUTHORS
from api.github_client import GitHubClient
from db.fetch_state import FetchState
from services.pr_service import fetch_and_save_pr, DATA_PRS_DIR

ROLLING_WINDOW_DAYS = 730  # 2 years
MAX_FETCH_WORKERS   = 5    # PRs fetched in parallel; tune based on API rate limits


def _fetch_new_pr(client, author, pr):
    """Fetch and save a single new PR. Returns (pr_number, pr_date) on success."""
    owner, repo = pr["repository_url"].split("/")[-2:]
    fetch_and_save_pr(client, author, pr, owner, repo, overwrite=False)
    return pr["number"], pr["created_at"][:10]


def _refetch_updated_pr(client, author, pr):
    """Re-fetch a PR that received new comments. Returns pr_number on success."""
    owner, repo = pr["repository_url"].split("/")[-2:]
    fetch_and_save_pr(client, author, pr, owner, repo, overwrite=True)
    return pr["number"]


def bootstrap():
    """
    First-ever run. Fetches all PRs from the last 2 years for every author.
    Safe to re-run — PRs already on disk are skipped via fetch state.
    Comment fetching runs in parallel (MAX_FETCH_WORKERS threads).
    FetchState is updated sequentially in the main thread as futures complete.
    """
    print("=" * 60)
    print("BOOTSTRAP: Fetching all PRs from the last 2 years")
    print(f"  Parallel workers: {MAX_FETCH_WORKERS}")
    print("=" * 60)

    client    = GitHubClient()
    state     = FetchState()
    today     = date.today()
    date_from = today - timedelta(days=ROLLING_WINDOW_DAYS)

    for author in AUTHORS:
        print(f"\nAuthor: {author}")
        prs = client.search_prs_by_author(author, date_from=date_from, date_to=today)
        print(f"  Found {len(prs)} PRs in the last 2 years")

        # Filter out already-fetched PRs before submitting to thread pool
        prs_to_fetch = [pr for pr in prs if not state.is_fetched(author, pr["number"])]
        print(f"  {len(prs_to_fetch)} PR(s) to fetch ({len(prs) - len(prs_to_fetch)} already cached)")

        with ThreadPoolExecutor(max_workers=MAX_FETCH_WORKERS) as executor:
            futures = {
                executor.submit(_fetch_new_pr, client, author, pr): pr
                for pr in prs_to_fetch
            }
            done = 0
            for future in as_completed(futures):
                try:
                    pr_number, pr_date = future.result()
                    state.mark_fetched(author, pr_number, pr_date)
                    done += 1
                    print(f"  [{done}/{len(prs_to_fetch)}] Fetched PR #{pr_number}")
                except Exception as e:
                    pr = futures[future]
                    print(f"  Error fetching PR #{pr['number']}: {e}")

    state.set_last_full_fetch()
    state.save()
    print("\n=== BOOTSTRAP complete ===")


def incremental():
    """
    Daily job. Three passes:

    Pass 1 — New PRs created in the last 24 hours (parallel).
    Pass 2 — Existing PRs updated in the last 24 hours (parallel).
             This is the critical pass — active teams routinely comment on older PRs.
    Pass 3 — Delete PR files outside the 2-year rolling window (sequential).
    """
    print("=" * 60)
    print("INCREMENTAL FETCH")
    print(f"  Parallel workers: {MAX_FETCH_WORKERS}")
    print("=" * 60)

    client    = GitHubClient()
    state     = FetchState()
    today     = date.today()
    yesterday = today - timedelta(days=1)
    cutoff    = today - timedelta(days=ROLLING_WINDOW_DAYS)

    for author in AUTHORS:
        print(f"\nAuthor: {author}")

        # ------------------------------------------------------------------
        # Pass 1: New PRs created in the last 24 hours
        # ------------------------------------------------------------------
        print(f"  [Pass 1] New PRs created since {yesterday}")
        new_prs        = client.search_prs_by_author(author, date_from=yesterday, date_to=today)
        prs_to_fetch   = [pr for pr in new_prs if not state.is_fetched(author, pr["number"])]

        with ThreadPoolExecutor(max_workers=MAX_FETCH_WORKERS) as executor:
            futures = {
                executor.submit(_fetch_new_pr, client, author, pr): pr
                for pr in prs_to_fetch
            }
            new_count = 0
            for future in as_completed(futures):
                try:
                    pr_number, pr_date = future.result()
                    state.mark_fetched(author, pr_number, pr_date)
                    new_count += 1
                except Exception as e:
                    pr = futures[future]
                    print(f"  Error fetching PR #{pr['number']}: {e}")
        print(f"  → {new_count} new PR(s) fetched")

        # ------------------------------------------------------------------
        # Pass 2: PRs updated in the last 24 hours (re-fetch for new comments)
        # ------------------------------------------------------------------
        print(f"  [Pass 2] PRs updated since {yesterday} (re-fetch for new comments)")
        updated_prs  = client.search_prs_by_author(author, updated_after=yesterday)
        prs_to_refetch = [
            pr for pr in updated_prs
            if state.is_fetched(author, pr["number"])
            and pr["created_at"][:10] >= cutoff.isoformat()
        ]

        with ThreadPoolExecutor(max_workers=MAX_FETCH_WORKERS) as executor:
            futures = {
                executor.submit(_refetch_updated_pr, client, author, pr): pr
                for pr in prs_to_refetch
            }
            updated_count = 0
            for future in as_completed(futures):
                try:
                    future.result()
                    updated_count += 1
                except Exception as e:
                    pr = futures[future]
                    print(f"  Error re-fetching PR #{pr['number']}: {e}")
        print(f"  → {updated_count} existing PR(s) re-fetched")

        # ------------------------------------------------------------------
        # Pass 3: Delete files outside the 2-year rolling window (sequential)
        # ------------------------------------------------------------------
        print(f"  [Pass 3] Cleaning up PR files older than {cutoff}")
        author_dir    = os.path.join(DATA_PRS_DIR, author)
        deleted_count = 0
        if os.path.exists(author_dir):
            for filename in os.listdir(author_dir):
                if not filename.endswith('.txt'):
                    continue
                file_date_str = filename.split('_')[0]
                if file_date_str < cutoff.isoformat():
                    os.remove(os.path.join(author_dir, filename))
                    pr_number = int(filename.split('_')[1].replace('.txt', ''))
                    state.remove_pr(author, pr_number)
                    deleted_count += 1
        print(f"  → {deleted_count} old file(s) deleted")

    state.set_last_incremental_fetch()
    state.save()
    print("\n=== INCREMENTAL FETCH complete ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PR comments data scheduler")
    parser.add_argument(
        "--mode",
        choices=["bootstrap", "incremental"],
        required=True,
        help=(
            "bootstrap  : first-ever run, fetches all PRs from last 2 years\n"
            "incremental: daily run, fetches new + updated PRs from last 24 hrs"
        )
    )
    args = parser.parse_args()

    if args.mode == "bootstrap":
        bootstrap()
    else:
        incremental()
