"""Small, deterministic admin pages without serializing the entire store."""
from itertools import islice
import json
from collections.abc import Mapping

PAGE_SIZE = 10


def paginate(records, params=None):
    params = params or {}
    try:
        requested = max(1, int(params.get("page", 1)))
    except (ValueError, TypeError, OverflowError):
        requested = 1
    query = str(params.get("q") or "").strip().casefold()
    is_mapping = isinstance(records, Mapping)
    if query:
        entries = records.items() if is_mapping else enumerate(records)
        matched = [
            (key, value) for key, value in entries
            if query in str(key).casefold()
            or query in json.dumps(value, ensure_ascii=False).casefold()
        ]
        total = len(matched)
        page = min(requested, max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE))
        selected = matched[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
        data = dict(selected) if is_mapping else [value for _, value in selected]
    else:
        total = len(records)
        page = min(requested, max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE))
        start = (page - 1) * PAGE_SIZE
        data = dict(islice(records.items(), start, start + PAGE_SIZE)) if is_mapping else records[start:start + PAGE_SIZE]
    return {"ok": True, "data": data, "total": total, "page": page,
            "size": PAGE_SIZE, "pages": max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)}
