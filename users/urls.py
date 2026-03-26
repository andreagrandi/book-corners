from django.urls import path

from users.views import (
    change_email_view,
    change_password_view,
    delete_account_view,
    login_view,
    logout_view,
    register_view,
)

urlpatterns = [
    path("register/", register_view, name="register"),
    path("login/", login_view, name="login"),
    path("logout/", logout_view, name="logout"),
    path("account/email/", change_email_view, name="change_email"),
    path("account/password/", change_password_view, name="change_password"),
    path("account/delete/", delete_account_view, name="delete_account"),
]
