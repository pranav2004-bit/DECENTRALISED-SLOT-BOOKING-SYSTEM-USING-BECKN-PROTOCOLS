"""Phase 2.6 Registry Observability & Ops tests: /metrics must reflect real
subscribe/lookup/verify activity, not just be a static placeholder."""

import json

import pytest
from django.test import Client

from core import metrics


@pytest.fixture(autouse=True)
def _reset_metrics():
    metrics._counters.clear()
    metrics._latency_sum.clear()
    metrics._latency_count.clear()
    yield


@pytest.fixture
def client():
    return Client()


@pytest.mark.django_db
def test_metrics_reflects_real_subscribe_and_lookup_counts(client):
    client.post(
        "/subscribe", data=json.dumps({"not": "valid"}), content_type="application/json"
    )  # counts as a request + an error
    client.post("/lookup", data=json.dumps({}), content_type="application/json")
    client.post("/lookup", data=json.dumps({}), content_type="application/json")

    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.content.decode()

    assert 'registry_requests_total{metric="subscribe_requests_total"} 1' in body
    assert 'registry_requests_total{metric="subscribe_errors_total"} 1' in body
    assert 'registry_requests_total{metric="lookup_requests_total"} 2' in body
    assert "registry_request_latency_seconds_sum" in body
    assert 'registry_request_latency_seconds_count{metric="subscribe"} 1' in body
    assert 'registry_request_latency_seconds_count{metric="lookup"} 2' in body


def test_render_metrics_direct_unit_test():
    metrics.increment("test_counter", amount=3)
    with metrics.timed("test_latency"):
        pass
    lines = metrics.render_metrics()
    joined = "\n".join(lines)
    assert 'registry_requests_total{metric="test_counter"} 3' in joined
    assert 'registry_request_latency_seconds_count{metric="test_latency"} 1' in joined
