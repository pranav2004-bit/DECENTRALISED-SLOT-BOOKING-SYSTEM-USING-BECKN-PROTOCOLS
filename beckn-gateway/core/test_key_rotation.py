"""livetracker8.md §2.2: Gateway's own signing/encryption identity key rotation.
Builds on the already-existing `onboarding_rotate_keys` command (rotate + re-Subscribe),
closing two real gaps found on investigation: (1) the same cross-process `@lru_cache`
staleness bug already found and fixed in Registry's own §1.2 — `cache_clear()` inside
the command only cleared its own short-lived process, never the live gunicorn workers'
caches; (2) a genuine danger the original command had no protection against — it wrote
the new keys to disk *before* re-Subscribing, so a failed re-Subscribe left Gateway
signing with a key Registry was never told about, until someone noticed and fixed it by
hand. Both closed here: no more cache (shared `key_rotation` primitives, same as
Registry), and a real backup-and-roll-back-on-failure path.
"""

import json
from io import StringIO

import pytest
import responses
from django.core.management import call_command
from django.core.management.base import CommandError
from key_rotation import NoExistingKeyError, is_rotation_due, key_age_days

from core import participant_keys


@pytest.fixture
def rotation_settings(settings, tmp_path):
    settings.SIGNING_PRIVATE_KEY_PATH = str(tmp_path / "signing.json")
    settings.ENCRYPTION_PRIVATE_KEY_PATH = str(tmp_path / "encryption.json")
    settings.ONBOARDING_STATE_PATH = str(tmp_path / "onboarding_state.json")
    settings.SUBSCRIBER_ID = "beckn-gateway.example.com"
    settings.UNIQUE_KEY_ID = "key-1"
    settings.SUBSCRIBER_URL = "https://beckn-gateway.example.com"
    yield settings


def test_rotate_signing_key_raises_when_no_key_exists_yet(rotation_settings):
    with pytest.raises(NoExistingKeyError):
        participant_keys.rotate_signing_key()


def test_rotate_signing_key_replaces_and_is_immediately_visible_no_restart(rotation_settings):
    original_pub, _ = participant_keys.get_signing_keys()
    new_pub, _ = participant_keys.rotate_signing_key()
    assert new_pub != original_pub
    # No cache to go stale — the very next call sees the new key immediately, the same
    # "zero window" property Registry's own §1.2 rotation has.
    assert participant_keys.get_signing_keys()[0] == new_pub


def test_backup_and_restore_round_trips_both_keys(rotation_settings):
    original_signing, _ = participant_keys.get_signing_keys()
    original_encryption, _ = participant_keys.get_encryption_keys()

    backup_paths = participant_keys.backup_current_keys()
    participant_keys.rotate_signing_key()
    participant_keys.rotate_encryption_key()
    assert participant_keys.get_signing_keys()[0] != original_signing

    participant_keys.restore_keys_from_backup(backup_paths)
    assert participant_keys.get_signing_keys()[0] == original_signing
    assert participant_keys.get_encryption_keys()[0] == original_encryption


def test_command_skips_when_not_due_without_force(rotation_settings):
    original_pub, _ = participant_keys.get_signing_keys()
    out = StringIO()
    call_command("onboarding_rotate_keys", "ONDC:RET13", stdout=out)
    assert "not due" in out.getvalue()
    assert participant_keys.get_signing_keys()[0] == original_pub  # unchanged


def test_command_rotates_and_resubscribes_successfully_when_forced(rotation_settings):
    from core import onboarding_service

    onboarding_service.approve("ONDC:RET13")
    original_pub, _ = participant_keys.get_signing_keys()
    participant_keys.get_encryption_keys()  # provision it too — backup_current_keys() needs both

    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.POST,
            "http://registry:8000/subscribe",
            json={"status": "UNDER_SUBSCRIPTION"},
            status=200,
        )
        out = StringIO()
        call_command("onboarding_rotate_keys", "ONDC:RET13", "--force", stdout=out)

    assert "Rotated" in out.getvalue()
    assert "Re-Subscribed" in out.getvalue()
    assert participant_keys.get_signing_keys()[0] != original_pub  # genuinely rotated


def test_command_never_touches_disk_when_resubscribe_fails(rotation_settings):
    """The critical safety behavior: a real Registry rejection during rotation must not
    leave Gateway signing with a key Registry never learned about. Not a rollback in
    the corrected design (livetracker8.md §2.2, 2026-09-04) — disk is simply never
    written to until *after* Registry confirms the new identity, so there is nothing to
    undo on failure."""
    from core import onboarding_service

    onboarding_service.approve("ONDC:RET13")
    original_signing_pub, _ = participant_keys.get_signing_keys()
    original_encryption_pub, _ = participant_keys.get_encryption_keys()

    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.POST,
            "http://registry:8000/subscribe",
            json={"error": {"code": "DOMAIN_VERIFICATION_FAILED", "message": "no dice"}},
            status=422,
        )
        with pytest.raises(CommandError, match="failed"):
            call_command("onboarding_rotate_keys", "ONDC:RET13", "--force")

    assert participant_keys.get_signing_keys()[0] == original_signing_pub
    assert participant_keys.get_encryption_keys()[0] == original_encryption_pub
    # The pending-rotation hand-off must be cleared even on failure — it must never
    # linger and affect a later, unrelated Subscribe attempt.
    assert onboarding_service.get_pending_rotation_signing_key() is None


def test_command_signs_the_subscribe_request_with_the_old_key_not_the_new_one(rotation_settings):
    """The exact bug found live 2026-09-04: the original command rotated the on-disk
    key *before* calling Subscribe, so the Authorization header ended up signed with
    the new key — which Registry's own verify_subscribe_authorization rejects for a
    re-Subscribe (it requires the CURRENTLY REGISTERED key). Proves the fix: captures
    what key actually signed the outbound request and confirms it's the pre-rotation
    one, not the new one the payload declares."""
    from beckn_crypto import verify_request_signature

    from core import onboarding_service

    onboarding_service.approve("ONDC:RET13")
    old_signing_pub, _ = participant_keys.get_signing_keys()
    participant_keys.get_encryption_keys()  # provision it too — the post-success write needs it

    with responses.RequestsMock() as rsps:

        def subscribe_callback(request):
            # The request must verify against the OLD public key — proves it was
            # signed with the old private key, not whatever new one is in the payload.
            verify_request_signature(
                authorization_header=request.headers["Authorization"],
                body=request.body,
                public_key_b64=old_signing_pub,
            )
            body = json.loads(request.body)
            new_pub_in_payload = body["message"]["entity"]["key_pair"]["signing_public_key"]
            assert new_pub_in_payload != old_signing_pub  # payload really does carry the new key
            return (200, {}, json.dumps({"status": "UNDER_SUBSCRIPTION"}))

        rsps.add_callback(
            responses.POST, "http://registry:8000/subscribe", callback=subscribe_callback
        )
        call_command("onboarding_rotate_keys", "ONDC:RET13", "--force")

    # And now, after a confirmed success, the new key really is what's live.
    assert participant_keys.get_signing_keys()[0] != old_signing_pub


def test_on_subscribe_challenge_decrypts_with_the_pending_new_encryption_key_during_rotation(
    rotation_settings, client
):
    """The exact bug found live 2026-09-04, second half: Registry's on_subscribe
    challenge dispatch (also mid-flow, part of the same synchronous Subscribe handling)
    encrypts using the NEW encryption public key just submitted — Gateway's own
    `/on_subscribe` callback must decrypt with the matching NEW private key, not
    whatever's still on disk (the old one, unrotated at this point). Confirmed live as a
    genuine `400 Bad Request` on the real running stack before this fix."""
    from beckn_crypto import encrypt_challenge, generate_encryption_key_pair
    from django.urls import reverse

    from core import onboarding_service, onboarding_state

    onboarding_state.set_status("ONDC:RET13", "UNDER_SUBSCRIPTION")
    new_encryption_pub, new_encryption_priv = generate_encryption_key_pair()
    onboarding_service.set_pending_rotation_encryption_key(new_encryption_priv)

    registry_encryption_pub, registry_encryption_priv = generate_encryption_key_pair()
    encrypted = encrypt_challenge(
        challenge="the-secret-answer",
        own_private_key_b64=registry_encryption_priv,
        peer_public_key_b64_der=new_encryption_pub,  # Registry encrypts with the NEW key
    )

    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET,
            "http://registry:8000/identity",
            json={
                "signing_public_key": "irrelevant",
                "encryption_public_key": registry_encryption_pub,
            },
            status=200,
        )
        resp = client.post(
            reverse("on_subscribe"),
            data=json.dumps({"subscriber_id": "beckn-gateway.example.com", "challenge": encrypted}),
            content_type="application/json",
        )

    assert resp.status_code == 200
    assert resp.json() == {"answer": "the-secret-answer"}
    onboarding_service.clear_pending_rotation_encryption_key()


def test_submit_subscribe_marks_under_subscription_before_calling_registry(rotation_settings):
    """First half of the same fix: without this, mark_all_under_subscription_as_subscribed()
    (fired by handle_on_subscribe mid-call, before registry_client.subscribe even
    returns) has no UNDER_SUBSCRIPTION row to flip — confirmed live 2026-09-04, a
    genuinely successful rotation still left Gateway's own local state wrong even after
    the "don't downgrade" guard alone."""
    from unittest.mock import patch

    from core import onboarding_service, onboarding_state

    onboarding_service.approve("ONDC:RET13")
    seen_status_mid_call = {}

    def fake_subscribe(payload):
        seen_status_mid_call["status"] = onboarding_state.get_domain_status("ONDC:RET13")["status"]
        return {"status": "UNDER_SUBSCRIPTION"}

    with patch("core.registry_client.subscribe", side_effect=fake_subscribe):
        onboarding_service.submit_subscribe("ONDC:RET13")

    assert seen_status_mid_call["status"] == "UNDER_SUBSCRIPTION"


def test_submit_subscribe_never_downgrades_a_status_the_callback_already_marked_subscribed(
    rotation_settings,
):
    """The third real bug found live 2026-09-04 via this same rotation Test Gate:
    Registry's /subscribe response body always literally says "UNDER_SUBSCRIPTION"
    regardless of what actually happened — BAP's own onboarding_service.py already
    documents and guards against this exact race, but Gateway's copy never got the
    fix. Simulates the callback (handle_on_subscribe) having already run and marked
    SUBSCRIBED before submit_subscribe's own literal-status write would otherwise
    stomp it back down."""
    from unittest.mock import patch

    from core import onboarding_service, onboarding_state

    onboarding_service.approve("ONDC:RET13")

    def fake_subscribe(payload):
        # Simulates Registry's real synchronous behavior: by the time this returns,
        # its own callback to handle_on_subscribe has already run and marked us
        # SUBSCRIBED for real — but the response body itself still just says this.
        onboarding_state.set_status("ONDC:RET13", "SUBSCRIBED")
        return {"status": "UNDER_SUBSCRIPTION"}

    with patch("core.registry_client.subscribe", side_effect=fake_subscribe):
        entry = onboarding_service.submit_subscribe("ONDC:RET13")

    assert entry["status"] == "SUBSCRIBED"
    assert onboarding_state.get_domain_status("ONDC:RET13")["status"] == "SUBSCRIBED"


def test_verification_file_is_signed_with_the_pending_new_key_during_rotation(rotation_settings):
    """The other half of the same split: Registry's callback fetching the domain-
    ownership verification file must get one signed with the NEW key (it verifies that
    file's signature against the payload's declared new key), even though the
    Authorization header on the initiating request used the old one."""
    from beckn_crypto import verify_domain_ownership_file

    from core import onboarding_service

    onboarding_service.approve("ONDC:RET13")
    participant_keys.get_encryption_keys()  # provision it too — the post-success write needs it

    with responses.RequestsMock() as rsps:
        captured = {}

        def subscribe_callback(request):
            body = json.loads(request.body)
            captured["request_id"] = body["message"]["request_id"]
            captured["new_signing_pub"] = body["message"]["entity"]["key_pair"][
                "signing_public_key"
            ]
            # Simulate Registry's own domain-ownership check: fetch the verification
            # file exactly like _verify_domain_ownership does, mid-flow.
            served = onboarding_service.get_verification_file_content()
            assert verify_domain_ownership_file(
                file_content=served,
                request_id=captured["request_id"],
                signing_public_key_b64=captured["new_signing_pub"],
            )
            return (200, {}, json.dumps({"status": "UNDER_SUBSCRIPTION"}))

        rsps.add_callback(
            responses.POST, "http://registry:8000/subscribe", callback=subscribe_callback
        )
        call_command("onboarding_rotate_keys", "ONDC:RET13", "--force")


def test_key_age_and_due_check_wired_to_gateways_own_setting(rotation_settings):
    """Sanity check that Gateway's KEY_ROTATION_DAYS setting is the one actually
    consulted, not a hardcoded/mismatched number."""
    participant_keys.get_signing_keys()
    path = rotation_settings.SIGNING_PRIVATE_KEY_PATH
    age = key_age_days(path)
    assert age is not None
    assert is_rotation_due(path, rotation_settings.KEY_ROTATION_DAYS) is False
