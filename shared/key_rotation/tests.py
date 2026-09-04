"""Tests for the shared key-rotation primitives (livetracker8.md §2.2 — extracted from
Registry's own §1.2 implementation once Gateway needed the identical logic).
"""

import json
import os
import time

import pytest

from .rotation import (
    NoExistingKeyError,
    atomic_write_key_file,
    backup_key_file,
    is_rotation_due,
    key_age_days,
    read_key_file,
    restore_key_file,
)


def test_atomic_write_then_read_round_trips(tmp_path):
    path = tmp_path / "key.json"
    atomic_write_key_file(str(path), "pub", "priv")
    assert read_key_file(str(path)) == ("pub", "priv")


def test_atomic_write_leaves_no_leftover_temp_file(tmp_path):
    path = tmp_path / "key.json"
    atomic_write_key_file(str(path), "pub", "priv")
    leftovers = [p for p in tmp_path.iterdir() if p.name != "key.json"]
    assert leftovers == []


def test_backup_key_file_raises_when_nothing_exists_yet(tmp_path):
    with pytest.raises(NoExistingKeyError):
        backup_key_file(str(tmp_path / "key.json"))


def test_backup_key_file_creates_a_timestamped_copy_with_the_same_content(tmp_path):
    path = tmp_path / "key.json"
    atomic_write_key_file(str(path), "pub", "priv")
    backup_path = backup_key_file(str(path))
    assert backup_path.startswith(str(path)) and ".bak." in backup_path
    assert json.loads(open(backup_path).read()) == {
        "public_key": "pub",
        "private_key": "priv",  # pragma: allowlist secret
    }


def test_restore_key_file_puts_the_backed_up_content_back(tmp_path):
    path = tmp_path / "key.json"
    atomic_write_key_file(str(path), "old-pub", "old-priv")
    backup_path = backup_key_file(str(path))
    atomic_write_key_file(str(path), "new-pub", "new-priv")
    assert read_key_file(str(path)) == ("new-pub", "new-priv")

    restore_key_file(str(path), backup_path)
    assert read_key_file(str(path)) == ("old-pub", "old-priv")


def test_key_age_days_is_none_when_no_key_exists(tmp_path):
    assert key_age_days(str(tmp_path / "key.json")) is None


def test_key_age_days_is_near_zero_for_a_freshly_written_key(tmp_path):
    path = tmp_path / "key.json"
    atomic_write_key_file(str(path), "pub", "priv")
    age = key_age_days(str(path))
    assert age is not None
    assert 0 <= age < 0.01


def test_is_rotation_due_false_for_a_fresh_key_true_once_old_enough(tmp_path):
    path = tmp_path / "key.json"
    atomic_write_key_file(str(path), "pub", "priv")
    assert is_rotation_due(str(path), rotation_days=90) is False

    old_time = time.time() - (91 * 86400)
    os.utime(path, (old_time, old_time))
    assert is_rotation_due(str(path), rotation_days=90) is True


def test_is_rotation_due_false_for_a_nonexistent_key(tmp_path):
    assert is_rotation_due(str(tmp_path / "key.json"), rotation_days=90) is False
