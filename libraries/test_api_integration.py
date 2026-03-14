import json

import pytest
from django.core.cache import cache

from libraries.models import Library
from libraries.tests import _build_uploaded_photo


@pytest.mark.django_db
class TestFullApiWorkflow:
    """End-to-end integration test covering the full API user journey.
    Exercises register, login, submit, list, search, detail, report, photo, and statistics."""

    def setup_method(self):
        """Clear the cache before each test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    def test_full_user_workflow(self, client, tmp_path, settings):
        """Walk through the entire API lifecycle from registration to statistics.
        Verifies that all endpoints work together as a cohesive workflow."""
        settings.MEDIA_ROOT = tmp_path
        settings.API_RATE_LIMIT_ENABLED = False
        settings.AUTH_RATE_LIMIT_ENABLED = False

        # 1. Register
        register_response = client.post(
            "/api/v1/auth/register",
            data=json.dumps({
                "username": "integration_user",
                "email": "integration@example.com",
                "password": "SecurePass123!",
            }),
            content_type="application/json",
        )
        assert register_response.status_code == 201
        tokens = register_response.json()
        assert "access" in tokens
        assert "refresh" in tokens
        access_token = tokens["access"]

        # 2. Login
        login_response = client.post(
            "/api/v1/auth/login",
            data=json.dumps({
                "username": "integration_user",
                "password": "SecurePass123!",
            }),
            content_type="application/json",
        )
        assert login_response.status_code == 200
        login_tokens = login_response.json()
        assert "access" in login_tokens
        access_token = login_tokens["access"]

        # 3. Me
        me_response = client.get(
            "/api/v1/auth/me",
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
        )
        assert me_response.status_code == 200
        me_data = me_response.json()
        assert me_data["username"] == "integration_user"
        assert me_data["email"] == "integration@example.com"

        # 4. Submit library
        photo = _build_uploaded_photo()
        submit_response = client.post(
            "/api/v1/libraries/",
            data={
                "name": "Integration Test Library",
                "description": "A library created during integration testing.",
                "address": "42 Test Street",
                "city": "Berlin",
                "country": "DE",
                "postal_code": "10115",
                "latitude": "52.5200",
                "longitude": "13.4050",
                "photo": photo,
            },
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
        )
        assert submit_response.status_code == 201
        library_data = submit_response.json()
        slug = library_data["slug"]
        assert slug
        assert library_data["name"] == "Integration Test Library"
        assert library_data["city"] == "Berlin"

        # Verify pending status in DB
        library = Library.objects.get(slug=slug)
        assert library.status == Library.Status.PENDING

        # 5. Detail (own pending) — owner can see their pending library
        detail_pending_response = client.get(
            f"/api/v1/libraries/{slug}",
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
        )
        assert detail_pending_response.status_code == 200
        assert detail_pending_response.json()["slug"] == slug

        # Verify anonymous user cannot see pending library
        detail_anonymous_response = client.get(f"/api/v1/libraries/{slug}")
        assert detail_anonymous_response.status_code == 404

        # 6. Approve library (simulates moderation via ORM)
        library.status = Library.Status.APPROVED
        library.save()

        # 7. List — approved library appears in listing
        list_response = client.get("/api/v1/libraries/")
        assert list_response.status_code == 200
        list_data = list_response.json()
        slugs = [item["slug"] for item in list_data["items"]]
        assert slug in slugs
        assert "pagination" in list_data

        # 8. Search — library found by name
        search_response = client.get(
            "/api/v1/libraries/",
            data={"q": "Integration Test"},
        )
        assert search_response.status_code == 200
        search_slugs = [item["slug"] for item in search_response.json()["items"]]
        assert slug in search_slugs

        # 9. Detail (public) — approved library visible without auth
        detail_public_response = client.get(f"/api/v1/libraries/{slug}")
        assert detail_public_response.status_code == 200
        assert detail_public_response.json()["slug"] == slug

        # 10. Report
        report_response = client.post(
            f"/api/v1/libraries/{slug}/report",
            data={
                "reason": "incorrect_info",
                "details": "The address is slightly wrong.",
            },
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
        )
        assert report_response.status_code == 201
        report_data = report_response.json()
        assert report_data["reason"] == "incorrect_info"
        assert "id" in report_data

        # 11. Community photo
        community_photo = _build_uploaded_photo()
        photo_response = client.post(
            f"/api/v1/libraries/{slug}/photo",
            data={
                "caption": "A nice angle from the street",
                "photo": community_photo,
            },
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
        )
        assert photo_response.status_code == 201
        photo_data = photo_response.json()
        assert photo_data["status"] == "pending"
        assert photo_data["caption"] == "A nice angle from the street"
        assert "id" in photo_data

        # 12. Statistics
        statistics_response = client.get("/api/v1/statistics/")
        assert statistics_response.status_code == 200
        stats = statistics_response.json()
        assert stats["total_approved"] >= 1
