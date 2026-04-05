# Favourites

Manage your list of favourite libraries. All endpoints require authentication.

---

## List Favourites

`GET /api/v1/libraries/favourites`

Return the authenticated user's favourite libraries, ordered by when they were favourited (newest first). Only libraries that are still approved are included.

**Auth required:** Yes (`Bearer` token)

### Query parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `page` | integer | `1` | Page number (1-indexed) |
| `page_size` | integer | `20` | Items per page (1-50) |

### Examples

=== "curl"

    ```bash
    curl https://bookcorners.org/api/v1/libraries/favourites \
      -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..."
    ```

=== "Python"

    ```python
    import requests

    resp = requests.get(
        "https://bookcorners.org/api/v1/libraries/favourites",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    print(resp.json())
    ```

=== "Swift"

    ```swift
    var request = URLRequest(url: URL(string: "https://bookcorners.org/api/v1/libraries/favourites")!)
    request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
    let (data, _) = try await URLSession.shared.data(for: request)
    ```

### Response (`200 OK`)

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
      "is_favourited": true
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

### Errors

| Status | Cause |
|--------|-------|
| `401` | Missing or invalid authentication token |
| `429` | Rate limit exceeded (see [Rate Limiting](../rate-limiting.md)) |

---

## Mark as Favourite

`POST /api/v1/libraries/{slug}/favourite`

Add an approved library to the authenticated user's favourites.

**Auth required:** Yes (`Bearer` token)

### Path parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `slug` | string | URL slug of the library to favourite |

### Examples

=== "curl"

    ```bash
    curl -X POST https://bookcorners.org/api/v1/libraries/florence-via-rosina-15-corner-books/favourite \
      -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..."
    ```

=== "Python"

    ```python
    import requests

    resp = requests.post(
        "https://bookcorners.org/api/v1/libraries/florence-via-rosina-15-corner-books/favourite",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    print(resp.status_code)  # 201 (new) or 200 (already favourited)
    ```

=== "Swift"

    ```swift
    var request = URLRequest(url: URL(string: "https://bookcorners.org/api/v1/libraries/florence-via-rosina-15-corner-books/favourite")!)
    request.httpMethod = "POST"
    request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
    let (data, response) = try await URLSession.shared.data(for: request)
    ```

### Response (`201 Created`)

Returned when the library is newly added to favourites.

```json
{
  "message": "Library added to favourites."
}
```

### Response (`200 OK`)

Returned when the library is already in the user's favourites.

```json
{
  "message": "Library is already in your favourites."
}
```

### Errors

| Status | Cause |
|--------|-------|
| `401` | Missing or invalid authentication token |
| `404` | Library not found or not in approved status |
| `429` | Rate limit exceeded (see [Rate Limiting](../rate-limiting.md)) |

---

## Remove from Favourites

`DELETE /api/v1/libraries/{slug}/favourite`

Remove an approved library from the authenticated user's favourites. This operation is idempotent: it returns `204` whether the favourite existed or not.

**Auth required:** Yes (`Bearer` token)

### Path parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `slug` | string | URL slug of the library to unfavourite |

### Examples

=== "curl"

    ```bash
    curl -X DELETE https://bookcorners.org/api/v1/libraries/florence-via-rosina-15-corner-books/favourite \
      -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..."
    ```

=== "Python"

    ```python
    import requests

    resp = requests.delete(
        "https://bookcorners.org/api/v1/libraries/florence-via-rosina-15-corner-books/favourite",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    print(resp.status_code)  # 204
    ```

=== "Swift"

    ```swift
    var request = URLRequest(url: URL(string: "https://bookcorners.org/api/v1/libraries/florence-via-rosina-15-corner-books/favourite")!)
    request.httpMethod = "DELETE"
    request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
    let (_, response) = try await URLSession.shared.data(for: request)
    ```

### Response (`204 No Content`)

Empty response body.

### Errors

| Status | Cause |
|--------|-------|
| `401` | Missing or invalid authentication token |
| `404` | Library not found or not in approved status |
| `429` | Rate limit exceeded (see [Rate Limiting](../rate-limiting.md)) |
