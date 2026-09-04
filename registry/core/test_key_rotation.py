"""livetracker8.md §1.2: Registry's own signing/encryption identity key rotation —
replaces the previous "manually re-run Subscribe with a new keypair" process (which
doesn't even apply to Registry's own key; there was no process at all before this).

Covers both layers: `registry_keys.py`'s file-level rotate functions directly, and the
`rotate_registry_keys` management command (the actual operator/scheduler-facing surface).
"""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from core.models import AuditLogEntry
from core.registry_keys import (
    NoExistingKeyError,
    get_registry_signing_keys,
    is_rotation_due,
    key_age_days,
    rotate_encryption_keys,
    rotate_signing_keys,
)


def test_rotate_signing_keys_raises_when_no_key_exists_yet(tmp_path, settings):
    """Rotation replaces an existing identity — it must not silently create a first
    one (that's a different, deliberate code path with its own DEBUG/TESTING gate)."""
    settings.SIGNING_PRIVATE_KEY_PATH = str(tmp_path / "signing.json")
    with pytest.raises(NoExistingKeyError):
        rotate_signing_keys()


def test_rotate_signing_keys_replaces_the_key_and_backs_up_the_old_one(tmp_path, settings):
    signing_path = tmp_path / "signing.json"
    settings.SIGNING_PRIVATE_KEY_PATH = str(signing_path)

    original_pub, original_priv = get_registry_signing_keys()
    new_pub, new_priv = rotate_signing_keys()

    assert new_pub != original_pub
    assert new_priv != original_priv
    # The rotation must be immediately visible with no restart/cache to clear — this is
    # the actual "zero window" claim the Test Gate cares about, proven at the file-read
    # level here and at the live-running-container level in the Test Gate evidence.
    assert get_registry_signing_keys() == (new_pub, new_priv)

    backups = list(tmp_path.glob("signing.json.bak.*"))
    assert len(backups) == 1
    import json

    backed_up = json.loads(backups[0].read_text())
    assert (backed_up["public_key"], backed_up["private_key"]) == (original_pub, original_priv)


@pytest.mark.django_db
def test_rotate_signing_keys_writes_an_audit_log_entry(tmp_path, settings):
    settings.SIGNING_PRIVATE_KEY_PATH = str(tmp_path / "signing.json")
    get_registry_signing_keys()  # provision the first key so rotation has something to rotate

    from core import registry_service

    registry_service.rotate_registry_key(key_type="signing")

    entry = AuditLogEntry.objects.get(event_type="REGISTRY_SIGNING_KEY_ROTATED")
    assert entry.subscriber_id == "__registry__"
    assert entry.detail["key_type"] == "signing"


@pytest.mark.django_db
def test_rotate_registry_keys_command_rotates_both_by_default(tmp_path, settings):
    settings.SIGNING_PRIVATE_KEY_PATH = str(tmp_path / "signing.json")
    settings.ENCRYPTION_PRIVATE_KEY_PATH = str(tmp_path / "encryption.json")
    get_registry_signing_keys()
    from core.registry_keys import get_registry_encryption_keys

    get_registry_encryption_keys()

    from io import StringIO

    out = StringIO()
    # --force: this test is about the key-type default (both), not the due-check —
    # a key created moments ago isn't due yet, that behavior has its own tests below.
    call_command("rotate_registry_keys", force=True, stdout=out)

    assert "signing" in out.getvalue()
    assert "encryption" in out.getvalue()
    assert AuditLogEntry.objects.filter(event_type="REGISTRY_SIGNING_KEY_ROTATED").exists()
    assert AuditLogEntry.objects.filter(event_type="REGISTRY_ENCRYPTION_KEY_ROTATED").exists()


def test_rotate_registry_keys_command_reports_a_clean_error_when_nothing_to_rotate(
    tmp_path, settings
):
    """A real, previously-unprovisioned deployment shouldn't get a confusing traceback —
    a clean, actionable CommandError instead. --force to bypass the due-check (a
    nonexistent key is never "due"), reaching the actual rotate-and-fail path this
    test is about."""
    settings.SIGNING_PRIVATE_KEY_PATH = str(tmp_path / "signing.json")
    settings.ENCRYPTION_PRIVATE_KEY_PATH = str(tmp_path / "encryption.json")

    with pytest.raises(CommandError):
        call_command("rotate_registry_keys", key_type="signing", force=True)


def test_key_age_days_is_none_when_no_key_exists(tmp_path):
    assert key_age_days(str(tmp_path / "signing.json")) is None


def test_key_age_days_is_near_zero_for_a_freshly_written_key(tmp_path, settings):
    settings.SIGNING_PRIVATE_KEY_PATH = str(tmp_path / "signing.json")
    get_registry_signing_keys()
    age = key_age_days(str(tmp_path / "signing.json"))
    assert age is not None
    assert 0 <= age < 0.01  # well under a day old


def test_is_rotation_due_false_for_a_fresh_key_and_true_once_old_enough(tmp_path):
    import os

    path = tmp_path / "signing.json"
    path.write_text('{"public_key": "x", "private_key": "y"}')
    assert is_rotation_due(str(path), rotation_days=90) is False

    # Backdate the file's mtime to simulate a genuinely old key, rather than waiting
    # 90 real days — the function only reads mtime, so this is a faithful simulation.
    old_time = __import__("time").time() - (91 * 86400)
    os.utime(path, (old_time, old_time))
    assert is_rotation_due(str(path), rotation_days=90) is True


def test_is_rotation_due_false_for_a_nonexistent_key(tmp_path):
    """A key that was never provisioned isn't "due" — that's first-provisioning's own
    job (get_registry_*_keys), not rotation's."""
    assert is_rotation_due(str(tmp_path / "signing.json"), rotation_days=90) is False


@pytest.mark.django_db
def test_command_skips_a_key_that_is_not_due_yet_without_force(tmp_path, settings):
    settings.SIGNING_PRIVATE_KEY_PATH = str(tmp_path / "signing.json")
    settings.ENCRYPTION_PRIVATE_KEY_PATH = str(tmp_path / "encryption.json")
    original_pub, _ = get_registry_signing_keys()

    from io import StringIO

    out = StringIO()
    call_command("rotate_registry_keys", key_type="signing", stdout=out)

    assert "not due" in out.getvalue()
    assert get_registry_signing_keys()[0] == original_pub  # unchanged
    assert not AuditLogEntry.objects.filter(event_type="REGISTRY_SIGNING_KEY_ROTATED").exists()


@pytest.mark.django_db
def test_command_rotates_a_due_key_without_force(tmp_path, settings):
    """The real scheduler-facing behavior: an old-enough key rotates on a plain,
    unforced invocation — exactly what `registry-scheduler`'s daily `docker exec`
    relies on, no --force involved."""
    import os
    import time

    settings.SIGNING_PRIVATE_KEY_PATH = str(tmp_path / "signing.json")
    settings.ENCRYPTION_PRIVATE_KEY_PATH = str(tmp_path / "encryption.json")
    original_pub, _ = get_registry_signing_keys()
    old_time = time.time() - (91 * 86400)
    os.utime(tmp_path / "signing.json", (old_time, old_time))

    from io import StringIO

    out = StringIO()
    call_command("rotate_registry_keys", key_type="signing", stdout=out)

    assert "Rotated" in out.getvalue()
    assert get_registry_signing_keys()[0] != original_pub
    assert AuditLogEntry.objects.filter(event_type="REGISTRY_SIGNING_KEY_ROTATED").exists()


def test_rotate_encryption_keys_also_replaces_and_backs_up(tmp_path, settings):
    from core.registry_keys import get_registry_encryption_keys

    settings.ENCRYPTION_PRIVATE_KEY_PATH = str(tmp_path / "encryption.json")
    original_pub, _ = get_registry_encryption_keys()
    new_pub, _ = rotate_encryption_keys()

    assert new_pub != original_pub
    assert list(tmp_path.glob("encryption.json.bak.*"))
