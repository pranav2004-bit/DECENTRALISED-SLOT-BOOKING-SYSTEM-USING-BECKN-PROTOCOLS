"""Session-ownership authorization (livetracker2.md §3.7) — closes a real IDOR gap
found by design audit before implementing: none of BAP's 8 trigger/result view pairs
(search/select/init/confirm/status/cancel/update/track) checked *who* was asking —
every one resolved its `SearchSession` purely by `transaction_id`. Any client who
learned another customer's `transaction_id` could freely poll their results, or
trigger a real `/cancel`/`/update` against a booking that isn't theirs.

Deliberately distinct from the wire-layer `holder_ref == context["transaction_id"]`
check already established at BPP (§3.3-§3.5): that one protects BPP from a wrong or
malicious *BAP* — a cross-organization trust boundary. This one protects one
*customer* from another customer of the *same* BAP — a boundary no earlier phase
was ever asked to enforce.
"""

from .models import SearchSession


class SessionAccessError(Exception):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def resolve_owned_session(*, transaction_id: str, requesting_customer) -> SearchSession:
    """Resolves a `SearchSession` by `transaction_id`, enforcing ownership:

    - Genuinely nonexistent `transaction_id` -> `SessionAccessError(404)`.
    - Exists, has an owning customer, caller isn't authenticated at all ->
      `SessionAccessError(401)`.
    - Exists, has an owning customer, caller is authenticated as a *different*
      customer -> `SessionAccessError(403)` — a deliberately distinguishable code
      from 404 (unlike the wire layer's identical-message existence-hiding),
      matching this bullet's own Test Gate wording; `transaction_id` is already an
      effectively unguessable UUID, so this doesn't meaningfully leak existence.
    - Exists and is still anonymous (`customer IS NULL`) -> returned unrestricted,
      matching this project's established anonymous-browsing UX (§3.1) — there is
      no real owner to protect.
    - Exists and belongs to `requesting_customer` -> returned.
    """
    try:
        session = SearchSession.objects.get(transaction_id=transaction_id)
    except SearchSession.DoesNotExist:
        raise SessionAccessError("no such search transaction", 404) from None

    if session.customer_id is not None:
        if requesting_customer is None or not requesting_customer.is_authenticated:
            raise SessionAccessError("authentication required", 401)
        if session.customer_id != requesting_customer.id:
            raise SessionAccessError("you do not have access to this transaction", 403)

    return session
