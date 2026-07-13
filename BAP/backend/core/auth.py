"""Authentication & Authorization Service — stub for Phase 1 (per BAP_details_v1.1.md
§10). Real buyer authentication (BAP Web Application ↔ BAP Backend, per §6) lands with
the Buyer Management Module in a future business-workflow tracker.
"""


class AuthenticationError(Exception):
    pass


def authenticate_buyer_session(*, session_token: str) -> dict:
    """NOT YET IMPLEMENTED — future business-workflow tracker (Buyer Management Module)."""
    raise NotImplementedError("Real buyer session authentication is out of this tracker's scope")
