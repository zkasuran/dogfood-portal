"""Root URL configuration for the DOGFOOD portal."""
from django.contrib import admin
from django.urls import include, path

from core import views

urlpatterns = [
    path("", views.home, name="home"),
    path("health", views.health, name="health"),
    path("health/", views.health),
    path("projects", views.gallery, name="gallery"),
    path("projects/", views.gallery),
    path("admin/", admin.site.urls),
    path("api/", include("core.urls")),
]
