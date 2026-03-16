"""astrodash_project URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.2/topics/http/urls/
"""
from django.contrib import admin
from django.urls import include
from django.urls import path
from django.views.generic import RedirectView

urlpatterns = [
    path("admin/", admin.site.urls, name='admin'),
    path("astrodash/api/v1/", include("astrodash.api_urls")),
    path("astrodash/", include("astrodash.urls")),
    path("", include("users.urls")),
    path("oidc/", include("mozilla_django_oidc.urls")),
    path("", RedirectView.as_view(url="/astrodash/", permanent=False)),
]
