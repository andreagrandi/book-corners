import json
import sys

from django.core.management.base import BaseCommand

from config.api import api


class Command(BaseCommand):
    """Export the OpenAPI schema as JSON to stdout.
    Useful for feeding the schema into documentation pipelines."""

    help = "Export the OpenAPI schema as JSON to stdout."

    def handle(self, *args, **options):
        """Generate the OpenAPI schema and write it as formatted JSON.
        Uses path_prefix to resolve mount point outside an HTTP context."""
        schema = api.get_openapi_schema(path_prefix="/api/v1/")
        json.dump(schema, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
