"""Authentication Service + Authorization Service — stubs for Phase 1
(per BPP_details_v1.1.md §10, listed as two distinct services, unlike BAP's
combined "Authentication & Authorization Service"). Real provider authentication
and role/permission checks land with the Provider Management Module in a future
business-workflow tracker.
"""


class AuthenticationError(Exception):
    pass


class AuthorizationError(Exception):
    pass


def authenticate_provider_session(*, session_token: str) -> dict:
    """NOT YET IMPLEMENTED — future business-workflow tracker (Provider Management Module)."""
    raise NotImplementedError("Real provider session authentication is out of this tracker's scope")


def authorize_provider_action(*, provider_id: str, action: str) -> bool:
    """NOT YET IMPLEMENTED — future business-workflow tracker."""
    raise NotImplementedError("Real provider authorization checks are out of this tracker's scope")
