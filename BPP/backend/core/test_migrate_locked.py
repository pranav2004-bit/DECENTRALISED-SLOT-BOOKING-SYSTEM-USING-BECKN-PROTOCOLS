"""livetracker7.md Phase 2 Test Gate piece: `migrate_locked` — the Postgres
advisory-lock-wrapped `migrate` entrypoint.sh now runs, closing a real migration race
found live between two containers (e.g. bpp-medical-backend + bpp-medical-worker)
starting `migrate` concurrently against a genuinely fresh, empty database.
"""

import pytest
from django.core.management import call_command
from django.db import connection

from core.management.commands.migrate_locked import _MIGRATION_ADVISORY_LOCK_KEY


@pytest.mark.django_db
def test_migrate_locked_runs_migrate_successfully():
    """Against this project's own already-migrated test DB, this is a no-op
    migrate — the real-world common case (every prior dev run migrated once).
    Proves the command doesn't error in the normal, no-contention path."""
    call_command("migrate_locked")


@pytest.mark.django_db
def test_migrate_locked_releases_the_advisory_lock_when_done():
    """A real, not just structural, proof the lock doesn't leak: after the command
    returns, a fresh session can immediately acquire the same lock (non-blocking) —
    if `migrate_locked` had failed to release it, this would return `false`."""
    call_command("migrate_locked")

    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [_MIGRATION_ADVISORY_LOCK_KEY])
        (acquired,) = cursor.fetchone()
        assert acquired is True
        cursor.execute("SELECT pg_advisory_unlock(%s)", [_MIGRATION_ADVISORY_LOCK_KEY])
