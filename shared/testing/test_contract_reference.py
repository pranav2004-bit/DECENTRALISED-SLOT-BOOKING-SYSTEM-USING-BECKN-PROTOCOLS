"""Reference example of the three test types defined in TESTING.md: unit, integration (mocked
HTTP), and contract (JSON schema). Run directly to prove the pattern works ahead of Phase 1
app code landing: pytest shared/testing/test_contract_reference.py -v
"""

import json
from pathlib import Path

import jsonschema
import pytest
import requests
import responses

SCHEMA_PATH = Path(__file__).parent / "contract_schemas" / "subscribe_request.schema.json"


def build_subscribe_payload(subscriber_id: str, domain: str) -> dict:
    """Stand-in for the real payload-builder function Phase 2.1 will implement."""
    return {
        "context": {"operation": {"ops_no": 2}},
        "message": {
            "request_id": "11111111-1111-1111-1111-111111111111",
            "timestamp": "2026-07-13T00:00:00.000Z",
            "entity": {
                "subscriber_id": subscriber_id,
                "unique_key_id": "22222222-2222-2222-2222-222222222222",
                "callback_url": "/on_subscribe",
                "country": "IND",
                "email_id": "ops@example.com",
                "mobile_no": 9999999999,
                "key_pair": {
                    "signing_public_key": "base64-signing-key",
                    "encryption_public_key": "base64-encryption-key",
                    "valid_from": "2026-07-13T00:00:00.000Z",
                    "valid_until": "2027-07-13T00:00:00.000Z",
                },
            },
            "network_participant": [
                {"subscriber_url": "https://example.com/ondc", "domain": domain, "type": "sellerApp"}
            ],
        },
    }


# --- UNIT TEST: pure function, no I/O ---
def test_build_subscribe_payload_sets_correct_ops_no_for_seller():
    payload = build_subscribe_payload("bpp.example.com", "ONDC:RET13")
    assert payload["context"]["operation"]["ops_no"] == 2
    assert payload["message"]["network_participant"][0]["type"] == "sellerApp"


# --- CONTRACT TEST: validate against the confirmed schema ---
def test_subscribe_payload_matches_confirmed_ondc_schema():
    schema = json.loads(SCHEMA_PATH.read_text())
    payload = build_subscribe_payload("bpp.example.com", "ONDC:RET13")
    jsonschema.validate(instance=payload, schema=schema)  # raises if non-conformant


def test_subscribe_payload_missing_required_field_fails_contract():
    schema = json.loads(SCHEMA_PATH.read_text())
    payload = build_subscribe_payload("bpp.example.com", "ONDC:RET13")
    del payload["message"]["entity"]["key_pair"]  # deliberately break it
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=payload, schema=schema)


# --- INTEGRATION TEST: mocked HTTP boundary (per TESTING.md — never a real network call here) ---
@responses.activate
def test_registry_subscribe_call_is_mocked_not_real():
    responses.add(
        responses.POST,
        "http://registry:8000/subscribe",
        json={"status": "UNDER_SUBSCRIPTION"},
        status=200,
    )
    # `responses` intercepts the `requests` library specifically, not raw urllib/http.client —
    # this is the correct pattern for the mocked-HTTP-boundary approach documented in TESTING.md.
    resp = requests.post("http://registry:8000/subscribe", json={})
    assert resp.json()["status"] == "UNDER_SUBSCRIPTION"
    assert len(responses.calls) == 1
    assert responses.calls[0].request.url == "http://registry:8000/subscribe"
