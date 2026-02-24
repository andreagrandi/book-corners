import pytest
from django.contrib.auth import get_user_model

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
