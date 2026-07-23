"""Race-reproduction test for a real, previously-latent thread-safety bug in
`participant_keys.py`'s lazy signing/encryption-key generation — see
`_load_or_generate`'s own comment for the exact mechanism. `functools.lru_cache`
does not guarantee only one thread runs the wrapped function on a cache miss, and
the old check-then-write body raced on `path.write_text()`, occasionally producing
`json.JSONDecodeError` under real concurrent first callers (reproduced live via
`core/test_select.py::test_concurrent_select_on_the_same_slot_yields_exactly_one_winner`
failing intermittently in full-suite runs). Calls `_load_or_generate()` directly,
bypassing `lru_cache`, so this test exercises the actual race window rather than
whatever `lru_cache`'s own internal locking happens to mask on a given run.
"""

import json
from concurrent.futures import ThreadPoolExecutor

from beckn_crypto import generate_signing_key_pair
from core import participant_keys


def test_concurrent_first_callers_never_see_a_corrupt_or_partial_key_file(tmp_path):
    """Many real threads racing `_load_or_generate()` against the same fresh,
    non-existent path must never raise, and must all agree on the same one real
    generated key pair — not each generate and overwrite their own."""
    path = tmp_path / "signing.json"
    n_threads = 25
    results: list[tuple[str, str]] = []
    errors: list[Exception] = []

    def attempt():
        try:
            results.append(
                participant_keys._load_or_generate(
                    str(path), generate_signing_key_pair, "signing"
                )
            )
        except Exception as exc:  # noqa: BLE001 - captured for assertion, not swallowed
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=n_threads) as executor:
        futures = [executor.submit(attempt) for _ in range(n_threads)]
        for future in futures:
            future.result()

    assert errors == []
    assert len(results) == n_threads
    assert len(set(results)) == 1

    data = json.loads(path.read_text())
    assert (data["public_key"], data["private_key"]) == results[0]


def test_load_or_generate_reads_back_an_already_persisted_key_unchanged(tmp_path):
    path = tmp_path / "signing.json"
    first = participant_keys._load_or_generate(str(path), generate_signing_key_pair, "signing")

    second = participant_keys._load_or_generate(str(path), generate_signing_key_pair, "signing")

    assert second == first
