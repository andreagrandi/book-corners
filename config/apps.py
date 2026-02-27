from django.contrib.admin.apps import AdminConfig


class BookCornersAdminConfig(AdminConfig):
    """App config that swaps in the custom BookCorners admin site.
    Uses Django's documented default_site mechanism."""

    default_site = "config.admin.BookCornersAdminSite"
