# Library Detail

`GET /api/v1/libraries/{slug}`

Return a single library by its URL slug.

**Auth required:** No (but see visibility rules below)

## Visibility rules

- **Approved** libraries are visible to everyone
- **Pending** libraries are visible only to the authenticated user who submitted them
- **Rejected** libraries are never returned

If you're the owner of a pending library, include your `Authorization: Bearer` header to see it.

## Path parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `slug` | string | URL-friendly unique slug of the library |

## Examples

=== "curl"

    ```bash
    curl https://bookcorners.org/api/v1/libraries/berlin-friedrichstr-12-corner-books
    ```

=== "Python"

    ```python
    import requests

    resp = requests.get(
        "https://bookcorners.org/api/v1/libraries/berlin-friedrichstr-12-corner-books",
    )
    library = resp.json()
    ```

### Viewing your own pending library

=== "curl"

    ```bash
    curl https://bookcorners.org/api/v1/libraries/my-pending-library-slug \
      -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..."
    ```

=== "Python"

    ```python
    resp = requests.get(
        "https://bookcorners.org/api/v1/libraries/my-pending-library-slug",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    ```

## Response (`200 OK`)

```json
{
  "id": 42,
  "slug": "berlin-friedrichstr-12-corner-books",
  "name": "Corner Books",
  "description": "A cozy little free library near the park entrance.",
  "photo_url": "/media/libraries/photos/corner-books.jpg",
  "thumbnail_url": "/media/libraries/thumbnails/corner-books.jpg",
  "lat": 52.52,
  "lng": 13.405,
  "address": "Friedrichstr. 12",
  "city": "Berlin",
  "country": "DE",
  "postal_code": "10117",
  "created_at": "2025-06-15T14:30:00Z"
}
```

## Errors

| Status | Cause |
|--------|-------|
| `404` | Library not found or not visible to the current user |
| `429` | Rate limit exceeded (see [Rate Limiting](../rate-limiting.md)) |
