from django.urls import path

from manage.views.dashboard import dashboard
from manage.views.libraries import (
    ai_enrich,
    ai_enrich_apply,
    find_duplicates,
    import_geojson,
    library_approve,
    library_bulk_action,
    library_detail,
    library_list,
    library_reject,
)
from manage.views.photos import photo_approve, photo_bulk_action, photo_list, photo_reject
from manage.views.reports import (
    report_bulk_action,
    report_dismiss,
    report_list,
    report_resolve,
)
from manage.views.users import user_detail, user_list

app_name = "manage"

urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("libraries/", library_list, name="library_list"),
    path("libraries/import/", import_geojson, name="import_geojson"),
    path("libraries/duplicates/", find_duplicates, name="find_duplicates"),
    path("libraries/<int:pk>/", library_detail, name="library_detail"),
    path("libraries/<int:pk>/approve/", library_approve, name="library_approve"),
    path("libraries/<int:pk>/reject/", library_reject, name="library_reject"),
    path("libraries/<int:pk>/ai-enrich/", ai_enrich, name="ai_enrich"),
    path("libraries/<int:pk>/ai-enrich/apply/", ai_enrich_apply, name="ai_enrich_apply"),
    path("libraries/bulk-action/", library_bulk_action, name="library_bulk_action"),
    path("photos/", photo_list, name="photo_list"),
    path("photos/<int:pk>/approve/", photo_approve, name="photo_approve"),
    path("photos/<int:pk>/reject/", photo_reject, name="photo_reject"),
    path("photos/bulk-action/", photo_bulk_action, name="photo_bulk_action"),
    path("reports/", report_list, name="report_list"),
    path("reports/<int:pk>/resolve/", report_resolve, name="report_resolve"),
    path("reports/<int:pk>/dismiss/", report_dismiss, name="report_dismiss"),
    path("reports/bulk-action/", report_bulk_action, name="report_bulk_action"),
    path("users/", user_list, name="user_list"),
    path("users/<int:pk>/", user_detail, name="user_detail"),
]
