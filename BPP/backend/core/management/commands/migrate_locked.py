from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection

# Arbitrary but fixed — must be identical across every process that could race on the
# same database, which is exactly why it's a hardcoded constant, not per-instance config.
_MIGRATION_ADVISORY_LOCK_KEY = 42723841


class Command(BaseCommand):
    help = (
        "Runs `migrate` under a Postgres session-level advisory lock (livetracker7.md "
        "Phase 2, real gap found live). entrypoint.sh's own plain `migrate --noinput` is "
        "unsafe against a genuinely fresh database when more than one container built "
        "from the same image starts against it concurrently (e.g. bpp-backend + "
        "bpp-worker, or bpp-medical-backend + bpp-medical-worker) — both processes race "
        "to CREATE TABLE the same brand-new schema, and the loser crashes with a real "
        "'relation already exists' / duplicate-key error, not a harmless no-op. Never "
        "hit in practice against this project's own long-lived dev volumes (already "
        "fully migrated, so a second concurrent `migrate` is a fast no-op) — only "
        "surfaced once Phase 2 created genuinely fresh multi-container-sharing-one-DB "
        "pairs for the first time. `pg_advisory_lock` is session-scoped: held for this "
        "process's one DB connection/session only, and automatically released if the "
        "process dies without calling unlock, so it can never deadlock a future start."
    )

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s)", [_MIGRATION_ADVISORY_LOCK_KEY])
        try:
            call_command("migrate", interactive=False)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [_MIGRATION_ADVISORY_LOCK_KEY])
