from django.urls import path

from users.views import (
    accept_contributor_agreement_view,
    change_email_view,
    change_password_view,
    delete_account_view,
    login_view,
    logout_view,
    register_view,
    social_register_view,
)

urlpatterns = [
    path("register/", register_view, name="register"),
    path(
        "register/social/<str:provider_id>/",
        social_register_view,
        name="social_register",
    ),
    path("login/", login_view, name="login"),
    path("logout/", logout_view, name="logout"),
    path("account/email/", change_email_view, name="change_email"),
    path("account/password/", change_password_view, name="change_password"),
    path(
        "account/contributor-agreement/",
        accept_contributor_agreement_view,
        name="accept_contributor_agreement",
    ),
    path("account/delete/", delete_account_view, name="delete_account"),
]
