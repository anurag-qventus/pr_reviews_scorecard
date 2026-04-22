import os


def _fmt_time(ts):
    """Returns a compact timestamp string, e.g. '2024-01-15 10:05'."""
    if not ts:
        return 'N/A'
    return ts[:16].replace('T', ' ')


def save_pr_threads(pr_title, pr_number, threads, abs_file_path):
    """
    Writes all comment threads for a single PR to abs_file_path.

    Always overwrites — each PR has its own dedicated file so there
    is no need to append. Re-fetching an updated PR just overwrites.

    threads: list of {'comment': {...}, 'replies': [{...}, ...]}
             as returned by organize_comments(). Already in chronological order.

    Each comment dict must have:
      - 'user': {'login': str}
      - 'body': str
      - 'created_at': str  (ISO 8601)
      - '_type': 'line' | 'review' | 'issue'
    Line comments additionally have 'path' and 'line'/'original_line'.
    Review comments additionally have 'state'.
    """
    os.makedirs(os.path.dirname(abs_file_path), exist_ok=True)

    with open(abs_file_path, 'w', encoding='utf-8') as f:
        f.write(f"=== PR #{pr_number}: {pr_title} ===\n\n")

        for thread in threads:
            root = thread['comment']
            body = (root.get('body') or '').strip()
            if not body and not thread['replies']:
                continue

            user  = root['user']['login']
            ts    = _fmt_time(root.get('created_at'))
            ctype = root.get('_type', 'issue')

            if ctype == 'line':
                path = root.get('path', '')
                line = root.get('line') or root.get('original_line', 'N/A')
                f.write(f"[Line Comment | {path}:{line} | {ts}]\n")
            elif ctype == 'review':
                state = root.get('state', '')
                f.write(f"[Review | {state} | {ts}]\n")
            else:
                f.write(f"[Comment | {ts}]\n")

            if body:
                f.write(f"  {user}: {body}\n")

            for reply in thread['replies']:
                r_body = (reply.get('body') or '').strip()
                if not r_body:
                    continue
                r_user = reply['user']['login']
                r_ts   = _fmt_time(reply.get('created_at'))
                f.write(f"    ↳ {r_user} ({r_ts}): {r_body}\n")

            f.write('\n')

        f.write(f"=== PR #{pr_number} END ===\n\n")

    print(f"Saved PR #{pr_number} to: {abs_file_path}")
