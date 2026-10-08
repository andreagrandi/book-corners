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
  "description": "A cozy community bookcase near the park entrance.",
  "photo_url": "/media/libraries/photos/corner-books.jpg",
  "thumbnail_url": "/media/libraries/thumbnails/corner-books.jpg",
  "lat": 52.52,
  "lng": 13.405,
  "address": "Friedrichstr. 12",
  "city": "Berlin",
  "country": "DE",
  "postal_code": "10117",
  "wheelchair_accessible": "yes",
  "capacity": 50,
  "is_indoor": false,
  "is_lit": true,
  "website": "https://example.org/bookcases/12345",
  "contact": "info@example.org",
  "source": "OpenStreetMap",
  "operator": "City Library Association",
  "brand": "Local Book Exchange Network",
  "created_at": "2025-06-15T14:30:00Z",
  "is_favourited": false,
  "photo_origin": "user",
  "photo_license": "CC-BY-SA-4.0",
  "photo_author": "janedoe",
  "photo_source_url": null
}
```

The `is_favourited` field is `true` when the authenticated user has favourited this library, and `false` otherwise. For unauthenticated requests it is always `false`.

## Image licences

Every library response describes the main photo with four fields. Photos added through community photo submissions use the same rules, and the staff moderation and contribution photo responses carry the same four fields.

| Field | Type | Meaning |
|-------|------|---------|
| `photo_origin` | string or `null` | Who provided the photo: `user`, `external`, or `unknown`. A photo with no recorded origin reports `unknown`. `null` when the library has no photo. |
| `photo_license` | string or `null` | `CC-BY-SA-4.0` when a contributor uploaded the photo and has accepted the [Contributor Agreement](https://bookcorners.org/contributor-agreement/1.0/en/). Otherwise `null`. |
| `photo_author` | string or `null` | Username of the contributor, set only when `photo_license` is `CC-BY-SA-4.0`. Otherwise `null`. |
| `photo_source_url` | string or `null` | URL of the original image when known. Otherwise `null`. |

The rules behind these values:

- A photo is `CC-BY-SA-4.0` only when `photo_origin` is `user`, the author's account still exists, and the author has accepted the Contributor Agreement. Any version of the agreement counts, and the agreement covers photos uploaded before the acceptance.
- A photo is never `CC-BY-SA-4.0` without an author. If the author's account was deleted, `photo_license` and `photo_author` are `null` and `photo_origin` stays `user`.
- A `null` `photo_license` means no licence information is available for this image. See `photo_source_url` when it is present.
- Image URLs are provided so you can download the image files. Please serve images from your own hosting instead of linking directly to image files on bookcorners.org, because the site runs on limited resources.

The [bulk export](bulk-export.md) provides the same four properties for every approved library.

## Errors

| Status | Cause |
|--------|-------|
| `404` | Library not found or not visible to the current user |
| `429` | Rate limit exceeded (see [Rate Limiting](../rate-limiting.md)) |
