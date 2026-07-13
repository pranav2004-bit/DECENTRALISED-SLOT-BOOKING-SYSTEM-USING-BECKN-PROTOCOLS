from django.urls import include, path


def _broken_view(request):
    raise ValueError("deliberate test exception")


urlpatterns = [
    path("", include("django_observability.urls")),
    path("__test_exception__", _broken_view),
]
