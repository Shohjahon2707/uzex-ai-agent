from django.urls import path

from . import views

urlpatterns = [
    path("", views.index),
    path("api/ask", views.ask),
    path("api/health", views.health),
]
