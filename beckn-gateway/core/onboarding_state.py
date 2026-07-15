"""File-backed onboarding state for Gateway (Phase 3.3). Gateway is deliberately
stateless — beckn_gateway_details_v1.1.md §4: no database, and INSTALLED_APPS excludes
auth/sessions/admin — so this can't be a Django model the way BAP/BPP's OnboardingStatus
is. Persists to a small JSON file instead, mirroring participant_keys.py's own
load-or-generate approach: no new hard dependency on Postgres or the optional [BETA]
Redis cache. Not safe for concurrent writers across processes (fine for a manually-
driven onboarding CLI flow — the same limitation already exists for key-file persistence).
"""

import json
from pathlib import Path
from threading import Lock

from django.conf import settings

_lock = Lock()

_DEFAULT_DOMAIN_ENTRY = {"approved_for_subscribe": False, "status": "NOT_STARTED", "last_error": ""}


def _state_path() -> Path:
    return Path(settings.ONBOARDING_STATE_PATH)


def _load() -> dict:
    path = _state_path()
    if not path.exists():
        return {"domains": {}, "verification_request_id": None}
    return json.loads(path.read_text())


def _save(state: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))


def get_domain_status(domain: str) -> dict:
    return _load()["domains"].get(domain, dict(_DEFAULT_DOMAIN_ENTRY))


def approve(domain: str) -> dict:
    with _lock:
        state = _load()
        entry = state["domains"].setdefault(domain, dict(_DEFAULT_DOMAIN_ENTRY))
        entry["approved_for_subscribe"] = True
        if entry["status"] == "NOT_STARTED":
            entry["status"] = "AWAITING_APPROVAL"
        _save(state)
        return entry


def set_status(domain: str, status: str, *, last_error: str = "") -> dict:
    with _lock:
        state = _load()
        entry = state["domains"].setdefault(domain, dict(_DEFAULT_DOMAIN_ENTRY))
        entry["status"] = status
        entry["last_error"] = last_error
        _save(state)
        return entry


def mark_all_under_subscription_as_subscribed() -> None:
    with _lock:
        state = _load()
        for entry in state["domains"].values():
            if entry["status"] == "UNDER_SUBSCRIPTION":
                entry["status"] = "SUBSCRIBED"
        _save(state)


def reset(domain: str) -> dict:
    """Clears a domain's local onboarding state back to NOT_STARTED — the rollback path
    for a failed or abandoned mid-onboarding attempt (livetracker1.md 3.4)."""
    with _lock:
        state = _load()
        entry = dict(_DEFAULT_DOMAIN_ENTRY)
        state["domains"][domain] = entry
        _save(state)
        return entry


def get_verification_request_id() -> str | None:
    return _load().get("verification_request_id")


def set_verification_request_id(request_id: str) -> None:
    with _lock:
        state = _load()
        state["verification_request_id"] = request_id
        _save(state)
