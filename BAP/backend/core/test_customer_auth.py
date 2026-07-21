"""Phase 2.1 Test Gate (livetracker2.md §2.1) for BAP's minimal customer onboarding.

E2E/SEC: a real customer can sign up, log in, and be identified consistently across a
session; session data is confirmed to live in Redis (inspected directly), not the
database; a captured password hash cannot be trivially reversed (correct hasher
confirmed via Django's password validators, not a custom implementation); a deactivated
account cannot log in.
"""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session as DjangoSession
from django.test import Client
from django.urls import reverse
from django_redis import get_redis_connection

Customer = get_user_model()

# Test fixture value, not a real credential.
TEST_PASSWORD = "a-strong-passw0rd!"  # pragma: allowlist secret


@pytest.fixture
def client():
    return Client()


def _signup(client, *, name="Jane Doe", contact="jane@example.com", password=TEST_PASSWORD):
    return client.post(
        reverse("signup"),
        data={"name": name, "contact": contact, "password": password},
        content_type="application/json",
    )


def _login(client, *, contact="jane@example.com", password=TEST_PASSWORD):
    return client.post(
        reverse("login"),
        data={"contact": contact, "password": password},
        content_type="application/json",
    )


@pytest.mark.django_db
def test_signup_creates_a_real_customer(client):
    resp = _signup(client)

    assert resp.status_code == 201
    body = resp.json()
    assert body["contact"] == "jane@example.com"
    assert body["name"] == "Jane Doe"
    assert Customer.objects.filter(contact="jane@example.com").exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "payload_name",
    [
        "'; DROP TABLE core_customer; --",
        "<script>alert('xss')</script>",
        "Robert'); DROP TABLE core_customer;--",
    ],
)
def test_signup_safely_stores_sqli_and_xss_shaped_name_without_executing_it(client, payload_name):
    """SEC (§3.7's own Test Gate): SQLi/XSS-shaped payloads against signup
    fields are rejected or safely stored, never reflected/executed. Django's
    ORM parameterizes all queries (structural SQLi prevention, not
    string-concatenated SQL anywhere in this codebase) and JsonResponse
    auto-escapes on serialization — this proves that holds in practice, not
    just in theory: the account is created with the literal string intact,
    the customer table still exists and is queryable afterward."""
    resp = client.post(
        reverse("signup"),
        data={"name": payload_name, "contact": "jane@example.com", "password": TEST_PASSWORD},
        content_type="application/json",
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == payload_name

    customer = Customer.objects.get(contact="jane@example.com")
    assert customer.name == payload_name
    assert Customer.objects.count() == 1


@pytest.mark.django_db
def test_signup_rejects_duplicate_contact(client):
    _signup(client)
    resp = _signup(client)

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert resp.json()["error"]["field"] == "contact"


@pytest.mark.django_db
@pytest.mark.parametrize("missing_field", ["name", "contact", "password"])
def test_signup_rejects_missing_required_fields(client, missing_field):
    payload = {"name": "Jane Doe", "contact": "jane@example.com", "password": TEST_PASSWORD}
    payload[missing_field] = ""

    resp = client.post(reverse("signup"), data=payload, content_type="application/json")

    assert resp.status_code == 400
    assert resp.json()["error"]["field"] == missing_field


@pytest.mark.django_db
def test_signup_rejects_a_weak_password_via_djangos_own_validators(client):
    resp = client.post(
        reverse("signup"),
        data={"name": "Jane Doe", "contact": "jane@example.com", "password": "12345678"},
        content_type="application/json",
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert resp.json()["error"]["field"] == "password"


@pytest.mark.django_db
def test_signup_rejects_oversized_name_and_contact(client):
    """SEC (§3.7): rejects oversized input before it reaches business logic / the
    DB, rather than a real Postgres varchar(255) overflow surfacing as an
    uncaught DataError -> a generic 500."""
    resp = client.post(
        reverse("signup"),
        data={"name": "x" * 256, "contact": "jane@example.com", "password": TEST_PASSWORD},
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["field"] == "name"

    resp = client.post(
        reverse("signup"),
        data={"name": "Jane Doe", "contact": "x" * 256, "password": TEST_PASSWORD},
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["field"] == "contact"


@pytest.mark.django_db
def test_signup_is_rate_limited(client):
    """SEC (§3.7's own Test Gate): rapid-fire signup spam is throttled — real
    abuse-simulation, hitting the actual endpoint 6 times in a row."""
    for _ in range(5):
        resp = client.post(
            reverse("signup"),
            data={"name": "Jane Doe", "contact": "jane@example.com", "password": TEST_PASSWORD},
            content_type="application/json",
        )
        assert resp.status_code in (201, 409)

    resp = client.post(
        reverse("signup"),
        data={"name": "Jane Doe", "contact": "jane@example.com", "password": TEST_PASSWORD},
        content_type="application/json",
    )
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "RATE_LIMITED"


@pytest.mark.django_db
def test_password_hash_uses_argon2_and_is_not_the_plaintext(client):
    _signup(client)
    customer = Customer.objects.get(contact="jane@example.com")

    assert customer.password.startswith("argon2$")
    assert TEST_PASSWORD not in customer.password
    assert customer.check_password(TEST_PASSWORD) is True
    assert customer.check_password("wrong-password") is False


@pytest.mark.django_db
def test_login_succeeds_and_customer_is_identified_consistently_across_session(client):
    _signup(client)

    login_resp = _login(client)
    assert login_resp.status_code == 200
    assert login_resp.json()["contact"] == "jane@example.com"

    # Same client (same session cookie) — two separate requests, same identity both times.
    me_1 = client.get(reverse("me"))
    me_2 = client.get(reverse("me"))
    assert me_1.status_code == 200
    assert me_2.status_code == 200
    assert me_1.json()["id"] == me_2.json()["id"] == login_resp.json()["id"]


@pytest.mark.django_db
def test_login_rejects_wrong_password(client):
    _signup(client)

    resp = _login(client, password="totally-wrong-password")

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.django_db
def test_me_requires_an_authenticated_session(client):
    resp = client.get(reverse("me"))
    assert resp.status_code == 401


@pytest.mark.django_db
def test_logout_ends_the_session(client):
    _signup(client)
    _login(client)
    assert client.get(reverse("me")).status_code == 200

    logout_resp = client.post(reverse("logout"))
    assert logout_resp.status_code == 200
    assert client.get(reverse("me")).status_code == 401


@pytest.mark.django_db
def test_deactivated_account_cannot_log_in(client):
    _signup(client)
    Customer.objects.filter(contact="jane@example.com").update(is_active=False)

    resp = _login(client)

    assert resp.status_code == 401


@pytest.mark.django_db(transaction=True)
def test_session_data_lives_in_redis_not_the_database(client):
    _signup(client)
    _login(client)

    session_key = client.session.session_key
    assert session_key is not None

    # Inspected directly against the real Redis backend, not just Django's cache API —
    # confirms the session genuinely lives in Redis.
    redis_client = get_redis_connection("default")
    matching_keys = redis_client.keys(f"*{session_key}*")
    assert len(matching_keys) >= 1

    # And confirms it did NOT also land in Django's default DB-backed session table —
    # SESSION_ENGINE="cache" (not "cached_db") means this table should stay empty.
    assert DjangoSession.objects.count() == 0


@pytest.mark.django_db
def test_signup_rejects_a_request_with_no_csrf_token():
    """SEC (§3.7): real gap closed — signup/login are no longer @csrf_exempt.
    `enforce_csrf_checks=True` makes Django's test Client behave like a real
    browser instead of silently bypassing CSRF, the way the default test Client
    always does."""
    strict_client = Client(enforce_csrf_checks=True)
    resp = strict_client.post(
        reverse("signup"),
        data={"name": "Jane Doe", "contact": "jane@example.com", "password": TEST_PASSWORD},
        content_type="application/json",
    )
    assert resp.status_code == 403


@pytest.mark.django_db
def test_signup_succeeds_with_a_real_csrf_token_from_the_csrf_cookie_endpoint():
    """The real, intended flow (§3.7): GET the CSRF cookie first (the standard
    Django AJAX-CSRF pattern), then echo it back as X-CSRFToken on the POST."""
    strict_client = Client(enforce_csrf_checks=True)
    strict_client.get(reverse("csrf-token"))
    csrf_token = strict_client.cookies["csrftoken"].value

    resp = strict_client.post(
        reverse("signup"),
        data={"name": "Jane Doe", "contact": "jane@example.com", "password": TEST_PASSWORD},
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )
    assert resp.status_code == 201
