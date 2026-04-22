import json
import os
from datetime import datetime

# src/db/ → src/ → project root → data/
STATE_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'data', '.fetch_state.json')
)


class FetchState:
    """
    In-memory representation of data/.fetch_state.json.

    Load once at the start of a scheduler run, mutate in memory,
    then call save() once at the end. This avoids repeated disk I/O
    during a run that processes hundreds of PRs.
    """

    def __init__(self):
        self._state = self._load()

    def _load(self):
        if not os.path.exists(STATE_FILE):
            return {
                "last_full_fetch": None,
                "last_incremental_fetch": None,
                "authors": {}
            }
        with open(STATE_FILE, 'r') as f:
            return json.load(f)

    def save(self):
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, 'w') as f:
            json.dump(self._state, f, indent=2)
        print(f"Fetch state saved to: {STATE_FILE}")

    def _author_entry(self, author):
        if author not in self._state["authors"]:
            self._state["authors"][author] = {
                "fetched_pr_numbers": [],
                "oldest_pr_date": None
            }
        return self._state["authors"][author]

    def is_fetched(self, author, pr_number):
        return pr_number in self._author_entry(author)["fetched_pr_numbers"]

    def mark_fetched(self, author, pr_number, pr_created_date):
        """pr_created_date: date object or 'YYYY-MM-DD' string."""
        entry = self._author_entry(author)
        if pr_number not in entry["fetched_pr_numbers"]:
            entry["fetched_pr_numbers"].append(pr_number)

        date_str = (
            pr_created_date
            if isinstance(pr_created_date, str)
            else pr_created_date.isoformat()
        )
        if entry["oldest_pr_date"] is None or date_str < entry["oldest_pr_date"]:
            entry["oldest_pr_date"] = date_str

    def remove_pr(self, author, pr_number):
        entry = self._author_entry(author)
        if pr_number in entry["fetched_pr_numbers"]:
            entry["fetched_pr_numbers"].remove(pr_number)

    def get_last_incremental_fetch_date(self):
        """Returns the date of the last incremental fetch, or None if never run."""
        ts = self._state.get("last_incremental_fetch")
        if ts:
            return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").date()
        return None

    def set_last_full_fetch(self):
        self._state["last_full_fetch"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    def set_last_incremental_fetch(self):
        self._state["last_incremental_fetch"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
