"""
URL configuration for bap project.

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

from django.contrib import admin
from django.urls import include, path

from core import views as core_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("django_observability.urls")),
    path(
        "ondc-site-verification.html",
        core_views.ondc_site_verification_view,
        name="ondc-site-verification",
    ),
    path("on_subscribe", core_views.on_subscribe_view, name="on_subscribe"),
    path("api/v1/auth/signup", core_views.signup_view, name="signup"),
    path("api/v1/auth/login", core_views.login_view, name="login"),
    path("api/v1/auth/logout", core_views.logout_view, name="logout"),
    path("api/v1/auth/me", core_views.me_view, name="me"),
    path("api/v1/bookings", core_views.bookings_list_view, name="bookings-list"),
    path("api/v1/search", core_views.search_trigger_view, name="search-trigger"),
    path(
        "api/v1/search/<str:transaction_id>",
        core_views.search_results_view,
        name="search-results",
    ),
    path("on_search", core_views.on_search_view, name="on_search"),
    path("api/v1/select", core_views.select_trigger_view, name="select-trigger"),
    path(
        "api/v1/select/<str:transaction_id>",
        core_views.select_result_view,
        name="select-result",
    ),
    path("on_select", core_views.on_select_view, name="on_select"),
    path("api/v1/init", core_views.init_trigger_view, name="init-trigger"),
    path(
        "api/v1/init/<str:transaction_id>",
        core_views.init_result_view,
        name="init-result",
    ),
    path("on_init", core_views.on_init_view, name="on_init"),
    path("api/v1/confirm", core_views.confirm_trigger_view, name="confirm-trigger"),
    path(
        "api/v1/confirm/<str:transaction_id>",
        core_views.confirm_result_view,
        name="confirm-result",
    ),
    path("on_confirm", core_views.on_confirm_view, name="on_confirm"),
    path("api/v1/status", core_views.status_trigger_view, name="status-trigger"),
    path(
        "api/v1/status/<str:transaction_id>",
        core_views.status_result_view,
        name="status-result",
    ),
    path("on_status", core_views.on_status_view, name="on_status"),
    path("api/v1/cancel", core_views.cancel_trigger_view, name="cancel-trigger"),
    path(
        "api/v1/cancel/<str:transaction_id>",
        core_views.cancel_result_view,
        name="cancel-result",
    ),
    path("on_cancel", core_views.on_cancel_view, name="on_cancel"),
    path("api/v1/update", core_views.update_trigger_view, name="update-trigger"),
    path(
        "api/v1/update/<str:transaction_id>",
        core_views.update_result_view,
        name="update-result",
    ),
    path("on_update", core_views.on_update_view, name="on_update"),
    path("api/v1/track", core_views.track_trigger_view, name="track-trigger"),
    path(
        "api/v1/track/<str:transaction_id>",
        core_views.track_result_view,
        name="track-result",
    ),
    path("on_track", core_views.on_track_view, name="on_track"),
]
