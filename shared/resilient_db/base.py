"""Django DB engine wrapping django.db.backends.postgresql with a short retry on the
*first* connection attempt only — works around Neon free-tier's "scale to zero when
inactive" behavior, where a database's first connection after being idle can fail
(surfaces as psycopg2.OperationalError, "could not translate host name" / "No address
associated with hostname") and succeeds immediately on retry. Confirmed live, repeatedly,
2026-09-02 (RUNBOOK.md's "Postgres moved to Neon" note) — not a hypothetical.

The real permanent fix is a paid Neon plan (always-on compute, never suspends) — not
affordable right now. This is deliberate, proportionate defense-in-depth in the meantime:
generically helps with any transient connection blip, not just Neon cold starts
specifically, same category of problem shared/resilient_http's circuit breaker already
handles for HTTP calls. A real, non-transient failure (wrong password, database doesn't
exist) still fails — just a few seconds slower, after exhausting the retries — never
silently masked.

Usage: point an app's DATABASES["default"]["ENGINE"] at "resilient_db.base" instead of
"django.db.backends.postgresql".
"""

import logging
import time

import psycopg2
from django.db.backends.postgresql.base import DatabaseWrapper as PostgresDatabaseWrapper

logger = logging.getLogger("resilient_db")

RETRY_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 2


class DatabaseWrapper(PostgresDatabaseWrapper):
    def get_new_connection(self, conn_params):
        last_error = None
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                return super().get_new_connection(conn_params)
            except psycopg2.OperationalError as exc:
                last_error = exc
                if attempt == RETRY_ATTEMPTS:
                    break
                logger.warning(
                    "Database connection attempt %d/%d failed (%s) — retrying in %ds. "
                    "Expected for a Neon free-tier cold start; a real problem if this "
                    "keeps happening once the database is warm.",
                    attempt,
                    RETRY_ATTEMPTS,
                    exc,
                    RETRY_DELAY_SECONDS,
                )
                time.sleep(RETRY_DELAY_SECONDS)
        raise last_error
