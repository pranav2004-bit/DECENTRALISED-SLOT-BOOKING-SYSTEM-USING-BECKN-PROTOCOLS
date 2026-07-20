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
    path("select", core_views.select_view, name="select"),
    path("on_select", core_views.on_select_view, name="on_select"),
    path("init", core_views.init_view, name="init"),
    path("on_init", core_views.on_init_view, name="on_init"),
    path("confirm", core_views.confirm_view, name="confirm"),
    path("on_confirm", core_views.on_confirm_view, name="on_confirm"),
    path("status", core_views.status_view, name="status"),
    path("on_status", core_views.on_status_view, name="on_status"),
    path("cancel", core_views.cancel_view, name="cancel"),
    path("on_cancel", core_views.on_cancel_view, name="on_cancel"),
    path("update", core_views.update_view, name="update"),
    path("on_update", core_views.on_update_view, name="on_update"),
    path("track", core_views.track_view, name="track"),
    path("on_track", core_views.on_track_view, name="on_track"),
]
