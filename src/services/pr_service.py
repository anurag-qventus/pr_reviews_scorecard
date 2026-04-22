import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import AUTHORS
from api.github_client import GitHubClient
from core.comment_processor import organize_comments
from core.comment_printer import save_pr_threads

ALLOWED_REPOS = {"foundational-data-models", "standard-solution-views"}

# src/services/ → src/ → project root → data/prs/
DATA_PRS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'prs')
)


def _normalize_comments(comments, comment_type):
    """Tags each comment with its type and normalizes timestamp to 'created_at'."""
    for c in comments:
        c['_type'] = comment_type
        if 'submitted_at' in c and 'created_at' not in c:
            c['created_at'] = c['submitted_at']
    return comments


def fetch_and_save_pr(client, author, pr, owner, repo, overwrite=False):
    """
    Fetches all comment types for a single PR, organizes into chronological
    threads, and writes to data/prs/{author}/{YYYY-MM-DD}_{pr_number}.txt.

    overwrite=False : skip if the file already exists (bootstrap / new PR).
    overwrite=True  : always re-fetch and overwrite (daily update pass for PRs
                      that received new comments on existing threads).
    """
    if repo not in ALLOWED_REPOS:
        return

    pr_number       = pr["number"]
    pr_created_date = pr["created_at"][:10]  # YYYY-MM-DD

    author_dir    = os.path.join(DATA_PRS_DIR, author)
    abs_file_path = os.path.join(author_dir, f"{pr_created_date}_{pr_number}.txt")

    if not overwrite and os.path.exists(abs_file_path):
        print(f"  Skipping PR #{pr_number} — file already exists")
        return

    print(f"  Fetching PR #{pr_number}: {pr['title']}")

    line_comments   = client.get_line_comments(owner, repo, pr_number)
    review_comments = client.get_review_comments(owner, repo, pr_number)
    issue_comments  = client.get_issue_comments(owner, repo, pr_number)

    _normalize_comments(line_comments,   'line')
    _normalize_comments(review_comments, 'review')
    _normalize_comments(issue_comments,  'issue')

    threads = organize_comments(line_comments + review_comments + issue_comments)
    save_pr_threads(pr['title'], pr_number, threads, abs_file_path)


def process_author_prs(client, author, date_from=None, date_to=None):
    """Fetches and saves all PRs for an author within an optional date range."""
    prs = client.search_prs_by_author(author, date_from=date_from, date_to=date_to)
    print(f"\nAuthor: {author}, PR Count: {len(prs)}")
    for pr in prs:
        owner, repo = pr["repository_url"].split("/")[-2:]
        fetch_and_save_pr(client, author, pr, owner, repo, overwrite=False)


def collect_pr_text(author_login: str, date_from: date, date_to: date) -> str:
    """
    Scans data/prs/{author}/ for PR files within [date_from, date_to].
    Filters by filename date prefix (YYYY-MM-DD) — no file content parsed.
    """
    author_dir = os.path.join(DATA_PRS_DIR, author_login)
    if not os.path.exists(author_dir):
        return ""
    parts = []
    for filename in sorted(os.listdir(author_dir)):
        if not filename.endswith('.txt'):
            continue
        try:
            file_date = date.fromisoformat(filename.split('_')[0])
        except ValueError:
            continue
        if date_from <= file_date <= date_to:
            with open(os.path.join(author_dir, filename), 'r', encoding='utf-8') as f:
                parts.append(f.read())
    return '\n'.join(parts)


def main():
    client = GitHubClient()
    for author in AUTHORS:
        print(f"\nProcessing author: {author}")
        process_author_prs(client, author)


if __name__ == "__main__":
    main()
