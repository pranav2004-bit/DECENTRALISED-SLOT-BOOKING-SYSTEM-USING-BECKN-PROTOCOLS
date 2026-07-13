from django.urls import path

from . import views

urlpatterns = [
    path("health", views.health_view, name="health"),
    path("ready", views.ready_view, name="ready"),
    path("metrics", views.metrics_view, name="metrics"),
]
