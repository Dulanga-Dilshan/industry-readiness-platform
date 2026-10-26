import heapq
from datetime import datetime, timezone

_OLDEST = datetime.min.replace(tzinfo=timezone.utc)


def newest(jobs, limit=None):
    """Jobs newest first. Jobs without a date go last. With a limit, uses
    heapq.nlargest (O(n log k)) instead of sorting everything."""
    key = lambda j: j.posted_at or _OLDEST
    if limit:
        return heapq.nlargest(limit, jobs, key=key)
    return sorted(jobs, key=key, reverse=True)
