from django.urls import path

from manage.views.dashboard import dashboard
from manage.views.libraries import (
    library_approve,
    library_bulk_action,
    library_detail,
    library_list,
    library_reject,
)
from manage.views.photos import photo_list
from manage.views.reports import report_list
from manage.views.users import user_list

app_name = "manage"

urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("libraries/", library_list, name="library_list"),
    path("libraries/<int:pk>/", library_detail, name="library_detail"),
    path("libraries/<int:pk>/approve/", library_approve, name="library_approve"),
    path("libraries/<int:pk>/reject/", library_reject, name="library_reject"),
    path("libraries/bulk-action/", library_bulk_action, name="library_bulk_action"),
    path("photos/", photo_list, name="photo_list"),
    path("reports/", report_list, name="report_list"),
    path("users/", user_list, name="user_list"),
]
