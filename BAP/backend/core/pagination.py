"""Cursor-based pagination helpers (API_CONVENTIONS.md: `?cursor=&limit=`, response
includes `next_cursor: string | null`, "avoids the classic offset-pagination
correctness problem under concurrent writes"). Two real call sites in this app as of
§3.6 (livetracker2.md): search results and the customer's booking history — a small,
shared helper rather than duplicating the query-param parsing twice.
"""

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def parse_limit(raw: str | None, *, default: int = DEFAULT_LIMIT, maximum: int = MAX_LIMIT) -> int:
    """A client-supplied limit that's missing, not an integer, zero, or negative
    falls back to `default` rather than erroring — pagination controls are a
    convenience, not a strict contract the caller must get exactly right. Always
    capped at `maximum` regardless of what the caller asks for."""
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if value <= 0:
        return default
    return min(value, maximum)
