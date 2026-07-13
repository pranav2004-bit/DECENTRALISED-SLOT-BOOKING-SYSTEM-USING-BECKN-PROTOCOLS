"""Test-only URLconf — provides a deliberately-broken view so tests can exercise
ExceptionHandlingMiddleware without polluting the real registry/urls.py. Used via
settings.ROOT_URLCONF override in core/tests.py.
"""

from django.urls import include, path


def _broken_view(request):
    raise ValueError("deliberate test exception")


urlpatterns = [
    path("", include("django_observability.urls")),
    path("__test_exception__", _broken_view),
]
