import json

import pytest
from django.http import Http404
from django.test import RequestFactory

from config.api import handle_not_found


@pytest.mark.django_db
class TestAPIErrorHandlers:
    """Tests for shared API exception handlers in config/api.py."""

    def test_404_handler_returns_structured_json(self):
        """Verify the Http404 handler produces structured JSON.
        Confirms the shared error shape with message and null details."""
        factory = RequestFactory()
        request = factory.get("/api/v1/libraries/nonexistent/")

        response = handle_not_found(request, Http404())

        assert response.status_code == 404
        body = json.loads(response.content)
        assert body["message"] == "Not found."
        assert body["details"] is None

    def test_422_validation_error_includes_details(self, client):
        """Verify malformed request bodies return 422 with error details.
        Confirms the validation handler exposes field-level errors."""
        response = client.post(
            "/api/v1/auth/register",
            data={},
            content_type="application/json",
        )

        assert response.status_code == 422
        body = response.json()
        assert body["message"] == "Validation error."
        assert "errors" in body["details"]

    def test_existing_auth_endpoints_still_work(self, client):
        """Verify auth endpoints remain functional after handler changes.
        Guards against regressions from the exception handler refactor."""
        response = client.post(
            "/api/v1/auth/register",
            data={
                "username": "errortest",
                "password": "StrongPass123!",
                "email": "errortest@example.com",
            },
            content_type="application/json",
        )

        assert response.status_code == 201
        body = response.json()
        assert "access" in body
        assert "refresh" in body
