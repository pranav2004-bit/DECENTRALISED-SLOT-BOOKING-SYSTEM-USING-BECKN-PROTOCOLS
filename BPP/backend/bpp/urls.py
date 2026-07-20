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
    path("api/v1/auth/signup", core_views.business_signup_view, name="business-signup"),
    path("api/v1/auth/login", core_views.business_login_view, name="business-login"),
    path("api/v1/auth/logout", core_views.business_logout_view, name="business-logout"),
    path("api/v1/auth/me", core_views.business_me_view, name="business-me"),
    path("api/v1/resources", core_views.resource_create_view, name="resource-create"),
    path(
        "api/v1/resources/<uuid:resource_id>/availability",
        core_views.resource_availability_create_view,
        name="resource-availability-create",
    ),
    path("api/v1/catalog/resources", core_views.resources_list_view, name="resources-list"),
    path("search", core_views.search_view, name="search"),
]
