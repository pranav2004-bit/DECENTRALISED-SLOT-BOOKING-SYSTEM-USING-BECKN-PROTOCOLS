"""Customer's booking history (livetracker2.md §3.6) — a genuinely new endpoint, not
an existing one this phase merely paginates. Before this phase, a customer had no way
to see their own past bookings at all: only per-transaction result-polling endpoints
(search/select/init/confirm/status/cancel/update/track results) existed, each scoped
to one transaction_id the caller must already know. This is the first real "list my
own things" endpoint in the project — found to be missing during §3.6's own design
audit of its pagination bullet, which assumed this endpoint already existed.

A "booking" is any SearchSession that reached a real confirmed Order at least once
(confirmed_order is not null) — cancellation/reschedule after that point doesn't
remove it from history, it's still a real past booking, just with updated state.
IDOR-safe by construction: always filtered to the caller's own `customer`, never
accepting a customer id from the request.
"""

from .models import SearchSession


def get_customer_bookings(*, customer, cursor: str | None = None, limit: int = 20) -> dict:
    """Real DB-level cursor pagination (unlike search_service.get_search_results'
    in-memory bpp_id cursor over a JSON list) — `id` is a genuine, monotonically
    increasing DB primary key, so `id < cursor` + `ORDER BY -id` is the standard,
    correct cursor scheme for a real queryset: newest-first, stable under concurrent
    inserts (a new booking arriving mid-poll only ever sorts *before* the cursor
    position, never shifting already-returned pages)."""
    queryset = SearchSession.objects.filter(
        customer=customer, confirmed_order__isnull=False
    ).order_by("-id")

    if cursor:
        try:
            queryset = queryset.filter(id__lt=int(cursor))
        except ValueError:
            pass

    page = list(queryset[: limit + 1])
    has_more = len(page) > limit
    page = page[:limit]

    return {
        "bookings": [
            {
                "transaction_id": session.transaction_id,
                "domain": session.domain,
                "confirmed_order": session.confirmed_order,
                "cancelled_order": session.cancelled_order,
                "updated_order": session.updated_order,
                "created_at": session.created_at.isoformat(),
            }
            for session in page
        ],
        "next_cursor": str(page[-1].id) if has_more and page else None,
    }
