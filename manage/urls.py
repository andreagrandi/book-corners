from django.urls import path

from manage.views.dashboard import dashboard
from manage.views.libraries import library_list
from manage.views.photos import photo_list
from manage.views.reports import report_list
from manage.views.users import user_list

app_name = "manage"

urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("libraries/", library_list, name="library_list"),
    path("photos/", photo_list, name="photo_list"),
    path("reports/", report_list, name="report_list"),
    path("users/", user_list, name="user_list"),
]
