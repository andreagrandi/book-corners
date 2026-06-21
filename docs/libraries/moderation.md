# Staff Moderation

Staff moderation endpoints allow admin clients to review and update libraries, reports, and community photo submissions through the API.

All endpoints require a JWT access token for a user with `is_staff=true`. Authenticated non-staff users receive:

```json
{
  "message": "Staff access required."
}
```

with status `403 Forbidden`.

## Summary

`GET /api/v1/libraries/moderation/summary`

Return moderation dashboard counts for staff clients.

**Auth required:** Yes (`Bearer` token, staff account)

```json
{
  "pending_libraries_count": 4,
  "open_reports_count": 2,
  "pending_photos_count": 5,
  "total_pending": 11,
  "total_libraries": 350,
  "total_users": 128
}
```

## List libraries

`GET /api/v1/libraries/moderation`

Return a paginated staff list of libraries across all moderation statuses.

**Auth required:** Yes (`Bearer` token, staff account)

### Query parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `status` | string | `all` | `all`, `pending`, `approved`, or `rejected` |
| `q` | string | — | Search name, address, or city |
| `country` | string | — | Filter by ISO 3166-1 alpha-2 country code |
| `source` | string | — | Filter by source text |
| `page` | int | `1` | Page number (1–1000) |
| `page_size` | int | `20` | Items per page (1–50) |

`GET /api/v1/libraries/moderation/pending` is also available as a convenience shortcut for pending submissions. It accepts the same `q`, `country`, `source`, `page`, and `page_size` parameters.

### Response (`200 OK`)

Each item uses the standard library response fields plus `status`, `rejection_reason`, and `created_by`.

```json
{
  "items": [
    {
      "id": 42,
      "slug": "florence-via-rosina-15-corner-books",
      "name": "Corner Books",
      "description": "A cozy little free library near the park entrance.",
      "photo_url": "/media/libraries/photos/corner-books.jpg",
      "thumbnail_url": "/media/libraries/thumbnails/corner-books.jpg",
      "lat": 43.7696,
      "lng": 11.2558,
      "address": "Via Rosina 15",
      "city": "Florence",
      "country": "IT",
      "postal_code": "50123",
      "wheelchair_accessible": "",
      "capacity": null,
      "is_indoor": null,
      "is_lit": null,
      "website": "",
      "contact": "",
      "source": "",
      "operator": "",
      "brand": "",
      "created_at": "2026-06-15T14:30:00Z",
      "is_favourited": false,
      "status": "pending",
      "rejection_reason": "",
      "created_by": {
        "id": 1,
        "username": "janedoe"
      }
    }
  ],
  "pagination": {
    "page": 1,
    "page_size": 20,
    "total": 1,
    "total_pages": 1,
    "has_next": false,
    "has_previous": false
  }
}
```

## Get a library

`GET /api/v1/libraries/moderation/{slug}`

Return any library by slug for staff users, including pending and rejected libraries hidden from the public detail endpoint.

## Update library status

`PATCH /api/v1/libraries/moderation/{slug}`

Set a library moderation status by slug.

**Auth required:** Yes (`Bearer` token, staff account)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `status` | string | Yes | New status: `pending`, `approved`, or `rejected` |
| `rejection_reason` | string | No | Optional reason stored when rejecting the library |

=== "Approve"

    ```bash
    curl -X PATCH https://bookcorners.org/api/v1/libraries/moderation/florence-via-rosina-15-corner-books \
      -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..." \
      -H "Content-Type: application/json" \
      -d '{"status": "approved"}'
    ```

=== "Reject"

    ```bash
    curl -X PATCH https://bookcorners.org/api/v1/libraries/moderation/florence-via-rosina-15-corner-books \
      -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..." \
      -H "Content-Type: application/json" \
      -d '{"status": "rejected", "rejection_reason": "Duplicate submission."}'
    ```

Returns the updated library using the same response shape as the library moderation list.

## List reports

`GET /api/v1/libraries/moderation/reports`

Return a paginated staff list of user-submitted reports.

**Auth required:** Yes (`Bearer` token, staff account)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `status` | string | `all` | `all`, `open`, `resolved`, or `dismissed` |
| `reason` | string | `all` | `all`, `damaged`, `missing`, `incorrect_info`, `inappropriate`, or `other` |
| `page` | int | `1` | Page number (1–1000) |
| `page_size` | int | `20` | Items per page (1–50) |

Each report includes the target library summary, reporter summary, reason, details, optional `photo_url`, `status`, and `created_at`.

## Update report status

`PATCH /api/v1/libraries/moderation/reports/{report_id}`

Set a report status.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `status` | string | Yes | New status: `open`, `resolved`, or `dismissed` |

```bash
curl -X PATCH https://bookcorners.org/api/v1/libraries/moderation/reports/7 \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..." \
  -H "Content-Type: application/json" \
  -d '{"status": "resolved"}'
```

## List community photos

`GET /api/v1/libraries/moderation/photos`

Return a paginated staff list of community photo submissions.

**Auth required:** Yes (`Bearer` token, staff account)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `status` | string | `all` | `all`, `pending`, `approved`, or `rejected` |
| `page` | int | `1` | Page number (1–1000) |
| `page_size` | int | `20` | Items per page (1–50) |

Each photo includes the parent library summary, submitter summary, caption, `photo_url`, `thumbnail_url`, `status`, and `created_at`.

## Update community photo status

`PATCH /api/v1/libraries/moderation/photos/{photo_id}`

Set a community photo status. Approved photos are promoted to the parent library's primary image by the model.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `status` | string | Yes | New status: `pending`, `approved`, or `rejected` |

```bash
curl -X PATCH https://bookcorners.org/api/v1/libraries/moderation/photos/12 \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..." \
  -H "Content-Type: application/json" \
  -d '{"status": "approved"}'
```

## Errors

| Status | Cause |
|--------|-------|
| `401` | Missing or invalid authentication token |
| `403` | Authenticated user is not staff |
| `404` | Library, report, or photo does not exist |
| `422` | Invalid payload or unsupported status/filter values |
| `429` | Rate limit exceeded (see [Rate Limiting](../rate-limiting.md)) |
