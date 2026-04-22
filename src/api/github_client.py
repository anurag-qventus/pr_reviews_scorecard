import os
import sys
import requests
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import HEADERS


class GitHubClient:
    @staticmethod
    def search_prs_by_author(author, date_from=None, date_to=None, updated_after=None,
                             per_page=100, max_pages=10):
        """
        Fetch PRs authored by `author`.

        date_from / date_to : datetime.date — filter by PR creation date (inclusive).
        updated_after       : datetime.date — filter by last-updated date (for catching
                              older PRs that received new comments in the last 24 hrs).

        Note: date_from/date_to and updated_after are mutually exclusive in practice.
        Use one or the other per call.
        """
        created_filter = ""
        if date_from and date_to:
            created_filter = f"+created:{date_from.isoformat()}..{date_to.isoformat()}"
        elif date_from:
            created_filter = f"+created:>={date_from.isoformat()}"
        elif date_to:
            created_filter = f"+created:<={date_to.isoformat()}"

        updated_filter = ""
        if updated_after:
            updated_filter = f"+updated:>={updated_after.isoformat()}"

        prs = []
        for page in range(1, max_pages + 1):
            url = (
                f"https://api.github.com/search/issues"
                f"?q=is:pr+author:{author}{created_filter}{updated_filter}"
                f"&per_page={per_page}&page={page}"
            )
            try:
                resp = requests.get(url, headers=HEADERS)
                resp.raise_for_status()
            except requests.exceptions.HTTPError as err:
                status = resp.status_code
                if status == 422:
                    print(
                        f"  [SKIP] 422 Unprocessable Entity for author '{author}'. "
                        f"Possible causes: username does not exist on GitHub, or the "
                        f"search query is too broad. Skipping this author."
                    )
                elif status == 403:
                    print(
                        f"  [SKIP] 403 Forbidden for author '{author}'. "
                        f"API rate limit likely exceeded. Skipping this author."
                    )
                else:
                    print(
                        f"  [SKIP] HTTP {status} error for author '{author}': {err}. "
                        f"Skipping this author."
                    )
                return []
            except requests.exceptions.RequestException as err:
                print(f"  [SKIP] Network error for author '{author}': {err}. Skipping.")
                return []

            prs.extend(resp.json().get("items", []))
            if "next" not in resp.links:
                break
            time.sleep(1)
        return prs

    def get_line_comments(self, owner, repo, pr_number):
        return self._get_json(
            f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/comments"
        )

    def get_review_comments(self, owner, repo, pr_number):
        return self._get_json(
            f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
        )

    def get_issue_comments(self, owner, repo, pr_number):
        return self._get_json(
            f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments"
        )

    def _get_json(self, url):
        resp = requests.get(url, headers=HEADERS)
        resp.raise_for_status()
        return resp.json()
