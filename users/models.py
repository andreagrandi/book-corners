from typing import Any

from django.contrib.auth.models import AbstractUser
from django.db.models import Q, UniqueConstraint
from django.db.models.functions import Lower


class User(AbstractUser):
    """Custom user model. Extend as needed."""

    class Meta:
        db_table = "users"
        constraints = [
            UniqueConstraint(
                Lower("email"),
                name="unique_email_ci",
                condition=Q(email__gt=""),
            ),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Normalize email to lowercase before persisting.
        Ensures case-insensitive uniqueness across all code paths."""
        if self.email:
            self.email = self.email.strip().lower()
        super().save(*args, **kwargs)
