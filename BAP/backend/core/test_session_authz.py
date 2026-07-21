"""Phase 3.7 Test Gate (livetracker2.md §3.7) piece owned by BAP: the core IDOR fix.
`resolve_owned_session()` is the single choke point all 8 trigger/result flows route
through — thoroughly unit-tested here; per-view wiring is proven separately in
test_confirm.py/test_cancel.py's own new IDOR tests (representative flows, not all 8,
since they share this exact code path verbatim).
"""

import pytest
from django.contrib.auth import get_user_model

from core.models import SearchSession
from core.session_authz import SessionAccessError, resolve_owned_session

Customer = get_user_model()


def _customer(*, contact):
    return Customer.objects.create_user(contact=contact, name="Jane Doe", password="a-strong-pw!")


@pytest.mark.django_db
def test_resolve_owned_session_raises_404_for_a_nonexistent_transaction():
    with pytest.raises(SessionAccessError) as exc_info:
        resolve_owned_session(transaction_id="nonexistent", requesting_customer=None)
    assert exc_info.value.status_code == 404


@pytest.mark.django_db
def test_resolve_owned_session_allows_unrestricted_access_to_an_anonymous_session():
    """No real owner to protect — matches this project's established
    anonymous-browsing UX (§3.1)."""
    SearchSession.objects.create(transaction_id="txn-1", domain="ONDC:RET13", customer=None)
    session = resolve_owned_session(transaction_id="txn-1", requesting_customer=None)
    assert session.transaction_id == "txn-1"


@pytest.mark.django_db
def test_resolve_owned_session_allows_the_owning_customer():
    me = _customer(contact="me@example.com")
    SearchSession.objects.create(transaction_id="txn-1", domain="ONDC:RET13", customer=me)
    session = resolve_owned_session(transaction_id="txn-1", requesting_customer=me)
    assert session.transaction_id == "txn-1"


@pytest.mark.django_db
def test_resolve_owned_session_raises_401_for_an_unauthenticated_caller_on_an_owned_session():
    me = _customer(contact="me@example.com")
    SearchSession.objects.create(transaction_id="txn-1", domain="ONDC:RET13", customer=me)
    with pytest.raises(SessionAccessError) as exc_info:
        resolve_owned_session(transaction_id="txn-1", requesting_customer=None)
    assert exc_info.value.status_code == 401


@pytest.mark.django_db
def test_resolve_owned_session_raises_403_for_a_different_authenticated_customer():
    """SEC (§3.7's own Test Gate): Customer A can never view or act on Customer
    B's booking, and gets a distinguishable 403, not a 404-leaked or silent
    allow."""
    me = _customer(contact="me@example.com")
    someone_else = _customer(contact="someone-else@example.com")
    SearchSession.objects.create(transaction_id="txn-1", domain="ONDC:RET13", customer=me)
    with pytest.raises(SessionAccessError) as exc_info:
        resolve_owned_session(transaction_id="txn-1", requesting_customer=someone_else)
    assert exc_info.value.status_code == 403
