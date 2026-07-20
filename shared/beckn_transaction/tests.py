import uuid

import pytest

from .ack import build_ack_response, build_nack_response
from .context import (
    PayloadValidationError,
    build_context,
    new_message_id,
    new_transaction_id,
    validate_context,
)


def _valid_context(**overrides) -> dict:
    base = {
        "domain": "ONDC:RET13",
        "location": {"country": {"code": "IND"}},
        "action": "search",
        "version": "1.1.0",
        "bap_id": "bap.local",
        "bap_uri": "http://bap:8000",
        "transaction_id": "txn-1",
        "message_id": "msg-1",
        "timestamp": "2026-07-19T00:00:00Z",
    }
    base.update(overrides)
    return base


def test_validate_context_accepts_a_fully_populated_context():
    validate_context(_valid_context())


@pytest.mark.parametrize(
    "field",
    [
        "domain",
        "location",
        "action",
        "version",
        "bap_id",
        "bap_uri",
        "transaction_id",
        "message_id",
        "timestamp",
    ],
)
def test_validate_context_rejects_each_missing_required_field(field):
    context = _valid_context()
    del context[field]
    with pytest.raises(PayloadValidationError) as exc_info:
        validate_context(context)
    assert exc_info.value.field == field


def test_validate_context_rejects_non_dict():
    with pytest.raises(PayloadValidationError):
        validate_context("not a dict")


def test_build_context_produces_a_context_that_passes_validation():
    context = build_context(
        domain="ONDC:RET13",
        action="search",
        version="1.1.0",
        bap_id="bap.local",
        bap_uri="http://bap:8000",
        transaction_id="txn-1",
        message_id="msg-1",
        location={"country": {"code": "IND"}},
        timestamp="2026-07-19T00:00:00Z",
    )
    validate_context(context)
    assert context["action"] == "search"
    assert "bpp_id" not in context


def test_build_context_includes_bpp_fields_only_when_provided():
    context = build_context(
        domain="ONDC:RET13",
        action="on_search",
        version="1.1.0",
        bap_id="bap.local",
        bap_uri="http://bap:8000",
        transaction_id="txn-1",
        message_id="msg-1",
        location={"country": {"code": "IND"}},
        timestamp="2026-07-19T00:00:00Z",
        bpp_id="bpp.local",
        bpp_uri="http://bpp:8000",
    )
    assert context["bpp_id"] == "bpp.local"
    assert context["bpp_uri"] == "http://bpp:8000"


def test_new_transaction_id_and_new_message_id_produce_distinct_real_uuids():
    txn_a, txn_b = new_transaction_id(), new_transaction_id()
    msg_a, msg_b = new_message_id(), new_message_id()
    assert txn_a != txn_b
    assert msg_a != msg_b
    assert uuid.UUID(txn_a) and uuid.UUID(msg_a)  # must parse as real UUIDs


def test_build_ack_response_shape():
    context = _valid_context()
    response = build_ack_response(context=context)
    assert response == {"context": context, "message": {"ack": {"status": "ACK"}}}


def test_build_nack_response_shape():
    context = _valid_context()
    error = {"code": "VALIDATION_ERROR", "message": "bad context"}
    response = build_nack_response(context=context, error=error)
    assert response == {
        "context": context,
        "message": {"ack": {"status": "NACK"}},
        "error": error,
    }
