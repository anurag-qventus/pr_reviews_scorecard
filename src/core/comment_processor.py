from datetime import datetime


def _parse_time(ts):
    if not ts:
        return datetime.min
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")


def organize_comments(comments):
    """
    Organizes comments into chronologically-ordered thread lists.

    - Input comments must all have a 'created_at' field (normalize before calling).
    - Top-level comments (no in_reply_to_id) become thread roots.
    - Replies are attached to their root thread; deep nesting (reply-to-reply)
      is flattened to the root thread so no comments are dropped.
    - Orphan replies (parent not in this comment set) are treated as roots.
    - Both threads and replies within each thread are in chronological order.

    Returns: list of {'comment': {...}, 'replies': [{...}, ...]} sorted by root created_at.
    """
    sorted_comments = sorted(comments, key=lambda c: _parse_time(c.get('created_at')))

    threads = {}      # root_id -> {'comment': ..., 'replies': [...]}
    id_to_root = {}   # comment_id -> root_id (for flattening deep nesting)

    for comment in sorted_comments:
        cid = comment['id']
        reply_to = comment.get('in_reply_to_id')

        if reply_to is None:
            threads[cid] = {'comment': comment, 'replies': []}
            id_to_root[cid] = cid
        else:
            root_id = id_to_root.get(reply_to)
            if root_id is not None:
                threads[root_id]['replies'].append(comment)
                id_to_root[cid] = root_id
            else:
                # Orphan reply — treat as new root
                threads[cid] = {'comment': comment, 'replies': []}
                id_to_root[cid] = cid

    return list(threads.values())
