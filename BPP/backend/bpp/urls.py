"""
URL configuration for bpp project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
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
    path("api/v1/auth/csrf", core_views.csrf_token_view, name="csrf-token"),
    path("api/v1/auth/signup", core_views.business_signup_view, name="business-signup"),
    path("api/v1/auth/login", core_views.business_login_view, name="business-login"),
    path("api/v1/auth/logout", core_views.business_logout_view, name="business-logout"),
    path("api/v1/auth/me", core_views.business_me_view, name="business-me"),
    path(
        "api/v1/auth/profile",
        core_views.business_profile_update_view,
        name="business-profile-update",
    ),
    path(
        "api/v1/auth/password",
        core_views.business_password_change_view,
        name="business-password-change",
    ),
    path("api/v1/resources", core_views.resource_create_view, name="resource-create"),
    path(
        "api/v1/resources/<uuid:resource_id>",
        core_views.resource_update_view,
        name="resource-update",
    ),
    path(
        "api/v1/resources/<uuid:resource_id>/status",
        core_views.resource_status_view,
        name="resource-status",
    ),
    path(
        "api/v1/resources/<uuid:resource_id>/delete",
        core_views.resource_delete_view,
        name="resource-delete",
    ),
    path(
        "api/v1/resources/<uuid:resource_id>/availability",
        core_views.resource_availability_create_view,
        name="resource-availability-create",
    ),
    path(
        "api/v1/resources/<uuid:resource_id>/availability/delete",
        core_views.resource_availability_delete_view,
        name="resource-availability-delete",
    ),
    path("api/v1/catalog/resources", core_views.resources_list_view, name="resources-list"),
    path("api/v1/orders", core_views.orders_list_view, name="orders-list"),
    path("api/v1/dashboard/stats", core_views.dashboard_stats_view, name="dashboard-stats"),
    path(
        "api/v1/orders/<uuid:booking_id>/fulfillment-status",
        core_views.order_fulfillment_status_update_view,
        name="order-fulfillment-status-update",
    ),
    path("api/v1/staff", core_views.staff_view, name="staff"),
    path(
        "api/v1/staff/<uuid:staff_id>/status",
        core_views.staff_status_view,
        name="staff-status",
    ),
    path(
        "api/v1/staff/<uuid:staff_id>/password",
        core_views.staff_password_reset_view,
        name="staff-password-reset",
    ),
    path(
        "api/v1/resources/<uuid:resource_id>/assign-staff",
        core_views.resource_assign_staff_view,
        name="resource-assign-staff",
    ),
    path(
        "api/v1/resources/<uuid:resource_id>/availability/block",
        core_views.resource_availability_block_view,
        name="resource-availability-block",
    ),
    path("api/v1/locations", core_views.locations_view, name="locations"),
    path(
        "api/v1/postal-directory/divisions",
        core_views.postal_divisions_view,
        name="postal-divisions",
    ),
    path(
        "api/v1/postal-directory/pincodes",
        core_views.postal_pincodes_view,
        name="postal-pincodes",
    ),
    path("search", core_views.search_view, name="search"),
    path("select", core_views.select_view, name="select"),
    path(
        "check_availability",
        core_views.check_availability_view,
        name="check-availability",
    ),
    path(
        "list_available_slots",
        core_views.list_available_slots_view,
        name="list-available-slots",
    ),
    path("init", core_views.init_view, name="init"),
    path("confirm", core_views.confirm_view, name="confirm"),
    path("payment_status", core_views.payment_status_view, name="payment-status"),
    path("status", core_views.status_view, name="status"),
    path("cancel", core_views.cancel_view, name="cancel"),
    path("update", core_views.update_view, name="update"),
    path("track", core_views.track_view, name="track"),
    path("rating", core_views.rating_view, name="rating"),
    path("support", core_views.support_view, name="support"),
]
