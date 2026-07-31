"""
URL configuration for gateway project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.urls import include, path

from core import views as core_views

urlpatterns = [
    path("", include("django_observability.urls")),
    path(
        "ondc-site-verification.html",
        core_views.ondc_site_verification_view,
        name="ondc-site-verification",
    ),
    path("on_subscribe", core_views.on_subscribe_view, name="on_subscribe"),
    path("search", core_views.search_view, name="search"),
    path("on_search", core_views.on_search_view, name="on_search"),
    # livetracker4.md §1.4 (2026-07-31): select/init/confirm/status/cancel/update/
    # track/rating/support routes retired — per the real Beckn protocol
    # (protocol_compliance_notes_v1.1.md §P), only /search routes through Gateway.
    # Those 9 actions now dispatch directly BAP<->BPP (see gateway/core/routing.py's
    # module docstring for the full story).
]
