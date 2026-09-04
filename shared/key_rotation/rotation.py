"""Generic key-file rotation primitives, shared between Registry (`registry/core/
registry_keys.py`) and Gateway (`beckn-gateway/core/participant_keys.py`) —
livetracker8.md §2.2's own explicit ask: "ideally shared tooling ... rather than two
separate implementations." Originally built for Registry's own identity key rotation
(§1.2), extracted here once Gateway needed the identical backup/atomic-write/due-check
logic rather than reimplementing it a second time.

Deliberately does NOT know anything about *which* keys these are, what encodes them, or
what happens after a rotation (e.g. Gateway's own re-Subscribe step) — this is pure
file-level machinery, reused by callers with very different surrounding logic.
"""

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


class NoExistingKeyError(Exception):
    """Raised when asked to rotate a key that hasn't been provisioned yet — rotation
    replaces an existing identity, it doesn't silently create a first one (that's each
    caller's own load-or-generate flow, not rotation's)."""


def read_key_file(path_str: str) -> tuple[str, str]:
    data = json.loads(Path(path_str).read_text())
    return data["public_key"], data["private_key"]


def atomic_write_key_file(path_str: str, public_key: str, private_key: str) -> None:
    """Write-to-temp-then-`os.replace` so a rotation can never leave a half-written key
    file behind if the process dies mid-write — `os.replace` is atomic on both POSIX and
    Windows within the same filesystem."""
    path = Path(path_str)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({"public_key": public_key, "private_key": private_key}, f)
        os.replace(tmp_path, path)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def backup_key_file(path_str: str) -> str:
    """Copies the current key file to a timestamped backup path before it gets
    overwritten — a rotation must never destroy the only copy of a key still needed to
    verify/decrypt something from moments before the rotation (matching this project's
    own established backup-before-destructive-change discipline,
    `CLOUD_KEY_PERSISTENCE.md`'s "a backup with no known restore procedure is not a
    working safety net"). Returns the backup path. Raises `NoExistingKeyError` if there
    is nothing to back up."""
    path = Path(path_str)
    if not path.exists():
        raise NoExistingKeyError(f"No existing key at {path_str} to back up before rotating.")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    backup_path = path.with_name(f"{path.name}.bak.{timestamp}")
    backup_path.write_text(path.read_text())
    return str(backup_path)


def restore_key_file(path_str: str, backup_path_str: str) -> None:
    """Rolls a rotation back — restores the pre-rotation key from its backup. Needed
    when rotation has a side effect that can independently fail (Gateway's own
    re-Subscribe call to Registry, §2.2): the new key must not stay live on disk if the
    step that tells Registry about it never actually succeeded, or Gateway ends up
    signing with a key Registry doesn't recognize until someone notices and fixes it
    manually."""
    atomic_write_key_file(path_str, *read_key_file(backup_path_str))


def key_age_days(path_str: str) -> float | None:
    """How many days old the key at `path_str` is (by file mtime, updated on every real
    rotation/creation), or `None` if no key exists there yet."""
    path = Path(path_str)
    if not path.exists():
        return None
    return (time.time() - path.stat().st_mtime) / 86400


def is_rotation_due(path_str: str, rotation_days: int) -> bool:
    """A key that doesn't exist yet isn't "due" — that's first-provisioning's own job,
    not rotation's."""
    age = key_age_days(path_str)
    return age is not None and age >= rotation_days
