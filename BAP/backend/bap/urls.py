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
]
