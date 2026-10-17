import pytest
from django.contrib.auth import get_user_model

from users.contributor_agreements import CURRENT_CONTRIBUTOR_AGREEMENT_VERSION
from users.models import ContributorAgreementAcceptance

User = get_user_model()


@pytest.fixture
def user(db):
    """Handle user.
    Supports the module workflow with a focused operation."""
    return User.objects.create_user(
        username="testuser",
        password="testpass123",
    )


@pytest.fixture
def agreement_accepted(user):
    """Record current contributor agreement acceptance for the default user.
    Lets web contribution tests run without the agreement checkbox."""
    return ContributorAgreementAcceptance.objects.create(
        user=user,
        agreement_version=CURRENT_CONTRIBUTOR_AGREEMENT_VERSION,
        channel=ContributorAgreementAcceptance.Channel.WEB_CREDENTIAL_REGISTRATION,
    )


@pytest.fixture
def admin_user(db):
    """Handle admin user.
    Supports the module workflow with a focused operation."""
    return User.objects.create_superuser(
        username="admin",
        password="adminpass123",
    )


@pytest.fixture
def admin_client(admin_user, client):
    """Handle admin client.
    Supports the module workflow with a focused operation."""
    client.force_login(admin_user)
    return client
