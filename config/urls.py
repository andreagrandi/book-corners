"""
URL configuration for config project.

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
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from config.api import api
from libraries.views import (
    home,
    latest_entries,
    library_detail,
    style_preview,
    submit_library,
    submit_library_confirmation,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("users.urls")),
    path("", home, name="home"),
    path("latest-entries/", latest_entries, name="latest_entries"),
    path("library/<slug:slug>/", library_detail, name="library_detail"),
    path("submit/", submit_library, name="submit_library"),
    path(
        "submit/confirmation/",
        submit_library_confirmation,
        name="submit_library_confirmation",
    ),
    path("style-preview/", style_preview, name="style_preview"),
    path("api/v1/", api.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
