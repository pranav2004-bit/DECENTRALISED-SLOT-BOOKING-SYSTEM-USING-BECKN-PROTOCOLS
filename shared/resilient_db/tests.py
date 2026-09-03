from unittest.mock import patch

import django
import psycopg2
import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(DATABASES={}, USE_TZ=True)
    django.setup()

from resilient_db.base import RETRY_ATTEMPTS, DatabaseWrapper  # noqa: E402

CONN_PARAMS = {"dbname": "irrelevant_for_these_tests"}


def _wrapper():
    # alias/settings_dict content is irrelevant here — none of these tests reach an
    # actual connection, only get_new_connection's own retry loop is under test.
    return DatabaseWrapper({"NAME": "test", "OPTIONS": {}}, alias="default")


def test_succeeds_immediately_when_first_attempt_works():
    wrapper = _wrapper()
    with patch(
        "resilient_db.base.PostgresDatabaseWrapper.get_new_connection",
        return_value="real-connection",
    ) as mocked:
        result = wrapper.get_new_connection(CONN_PARAMS)
    assert result == "real-connection"
    assert mocked.call_count == 1


def test_retries_once_then_succeeds_on_cold_start():
    wrapper = _wrapper()
    with (
        patch(
            "resilient_db.base.PostgresDatabaseWrapper.get_new_connection",
            side_effect=[
                psycopg2.OperationalError("could not translate host name"),
                "real-connection",
            ],
        ) as mocked,
        patch("resilient_db.base.time.sleep") as mocked_sleep,
    ):
        result = wrapper.get_new_connection(CONN_PARAMS)
    assert result == "real-connection"
    assert mocked.call_count == 2
    mocked_sleep.assert_called_once()


def test_raises_after_exhausting_all_retries_on_a_real_failure():
    wrapper = _wrapper()
    with (
        patch(
            "resilient_db.base.PostgresDatabaseWrapper.get_new_connection",
            side_effect=psycopg2.OperationalError("password authentication failed"),
        ) as mocked,
        patch("resilient_db.base.time.sleep"),
        pytest.raises(psycopg2.OperationalError, match="password authentication failed"),
    ):
        wrapper.get_new_connection(CONN_PARAMS)
    assert mocked.call_count == RETRY_ATTEMPTS


def test_does_not_retry_non_operational_errors():
    """A bug in application code (e.g. TypeError) must surface immediately, never get
    silently retried/delayed as if it were a transient connection issue."""
    wrapper = _wrapper()
    with (
        patch(
            "resilient_db.base.PostgresDatabaseWrapper.get_new_connection",
            side_effect=TypeError("not a connection problem"),
        ) as mocked,
        pytest.raises(TypeError),
    ):
        wrapper.get_new_connection(CONN_PARAMS)
    assert mocked.call_count == 1
