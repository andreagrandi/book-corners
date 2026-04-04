from django.urls import path

from manage.views.dashboard import dashboard

app_name = "manage"

urlpatterns = [
    path("", dashboard, name="dashboard"),
]
