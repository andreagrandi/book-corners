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
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path
from django.views.generic import TemplateView

from config.api import api
from config.views import health
from libraries.sitemaps import LibrarySitemap, StaticViewSitemap
from users.views import set_language_view
from libraries.views import (
    about_page,
    dashboard,
    home,
    latest_entries,
    library_detail,
    map_libraries_geojson,
    map_libraries_list,
    map_page,
    privacy_page,
    stats_page,
    style_preview,
    submit_library,
    submit_library_confirmation,
    submit_library_photo,
    submit_library_report,
    submit_library_photo_metadata,
    toggle_favourite,
)

sitemaps = {
    "static": StaticViewSitemap,
    "libraries": LibrarySitemap,
}

handler404 = "config.error_views.page_not_found"
handler500 = "config.error_views.server_error"

urlpatterns = [
    path("i18n/setlang/", set_language_view, name="set_language"),
    path("health/", health, name="health"),
    path("admin/", admin.site.urls),
    path("manage/", include("manage.urls")),
    path("accounts/", include("allauth.urls")),
    path("", include("users.urls")),
    path("", home, name="home"),
    path("sitemap.xml", sitemap, {"sitemaps": sitemaps}, name="sitemap"),
    path(
        "robots.txt",
        TemplateView.as_view(template_name="robots.txt", content_type="text/plain"),
        name="robots_txt",
    ),
    path("about/", about_page, name="about_page"),
    path("privacy/", privacy_page, name="privacy_page"),
    path("stats/", stats_page, name="stats_page"),
    path("map/", map_page, name="map_page"),
    path("map/libraries.geojson", map_libraries_geojson, name="map_libraries_geojson"),
    path("map/libraries/list/", map_libraries_list, name="map_libraries_list"),
    path("dashboard/", dashboard, name="dashboard"),
    path("latest-entries/", latest_entries, name="latest_entries"),
    path("library/<slug:slug>/", library_detail, name="library_detail"),
    path("library/<slug:slug>/report/", submit_library_report, name="submit_library_report"),
    path("library/<slug:slug>/submit-photo/", submit_library_photo, name="submit_library_photo"),
    path("library/<slug:slug>/favourite/", toggle_favourite, name="toggle_favourite"),
    path("submit/", submit_library, name="submit_library"),
    path(
        "submit/confirmation/",
        submit_library_confirmation,
        name="submit_library_confirmation",
    ),
    path(
        "submit/photo-metadata/",
        submit_library_photo_metadata,
        name="submit_library_photo_metadata",
    ),
    path("style-preview/", style_preview, name="style_preview"),
    path("api/v1/", api.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
