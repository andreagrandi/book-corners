# My Contributions

Authenticated users can list their own library submissions, reports, and community photo submissions with moderation status. These endpoints power the mobile contribution center and only ever return records owned by the caller.

All endpoints require a JWT access token and use standard pagination.

## List my libraries

`GET /api/v1/libraries/mine`

Return the authenticated user's submitted libraries across all moderation statuses. Pending submissions are listed first, then remaining submissions newest first.

**Auth required:** Yes (`Bearer` token)

### Query parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `page` | integer | `1` | Page number (1-indexed) |
| `page_size` | integer | `20` | Items per page (1-50) |

### Response (`200 OK`)

Each item uses the standard library response fields plus `status` and `rejection_reason`.

```json
{
  "items": [
    {
      "id": 42,
      "slug": "florence-via-rosina-15-corner-books",
      "name": "Corner Books",
      "description": "A cozy book exchange near the park.",
      "photo_url": "/media/libraries/photos/corner-books.jpg",
      "thumbnail_url": "/media/libraries/thumbnails/corner-books.jpg",
      "lat": 43.7696,
      "lng": 11.2558,
      "address": "Via Rosina 15",
      "city": "Florence",
      "country": "IT",
      "postal_code": "50123",
      "wheelchair_accessible": "yes",
      "capacity": 50,
      "is_indoor": false,
      "is_lit": true,
      "website": "",
      "contact": "",
      "source": "",
      "operator": "",
      "brand": "",
      "created_at": "2025-06-15T14:30:00Z",
      "is_favourited": false,
      "status": "pending",
      "rejection_reason": ""
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

## List my reports

`GET /api/v1/libraries/mine/reports`

Return the authenticated user's submitted reports, newest first.

**Auth required:** Yes (`Bearer` token)

### Query parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `page` | integer | `1` | Page number (1-indexed) |
| `page_size` | integer | `20` | Items per page (1-50) |

### Response (`200 OK`)

```json
{
  "items": [
    {
      "id": 7,
      "library": {
        "id": 42,
        "slug": "florence-via-rosina-15-corner-books",
        "name": "Corner Books",
        "city": "Florence",
        "country": "IT",
        "status": "approved"
      },
      "reason": "damaged",
      "status": "open",
      "created_at": "2025-06-15T14:30:00Z"
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

## List my community photos

`GET /api/v1/libraries/mine/photos`

Return the authenticated user's community photo submissions, newest first.

**Auth required:** Yes (`Bearer` token)

### Query parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `page` | integer | `1` | Page number (1-indexed) |
| `page_size` | integer | `20` | Items per page (1-50) |

### Response (`200 OK`)

```json
{
  "items": [
    {
      "id": 12,
      "library": {
        "id": 42,
        "slug": "florence-via-rosina-15-corner-books",
        "name": "Corner Books",
        "city": "Florence",
        "country": "IT",
        "status": "approved"
      },
      "caption": "A sunny day at the library.",
      "photo_url": "/media/libraries/user_photos/photo.jpg",
      "thumbnail_url": "/media/libraries/user_photos/thumbnails/photo.jpg",
      "status": "pending",
      "created_at": "2025-06-15T14:30:00Z"
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

## Errors

| Status | Cause |
|--------|-------|
| `401` | Missing or invalid authentication token |
| `422` | Invalid pagination parameter |
| `429` | Rate limit exceeded (see [Rate Limiting](../rate-limiting.md)) |
