# Update a Library

`PATCH /api/v1/libraries/{slug}`

Update a library you submitted. The library must be in **pending** or **approved** status and must belong to the authenticated user. Every successful edit returns the library to **pending** status for moderator review before it appears publicly again.

**Auth required:** Yes (`Bearer` token)

**Content type:** `multipart/form-data`

## Fields

All fields are optional. Omitted fields keep their current value. Provide at least one field.

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Display name (max 255 chars) |
| `description` | string | Free-text description (max 2000 chars) |
| `address` | string | Street address (max 255 chars). May be empty when coordinates identify the location. |
| `city` | string | City name (max 100 chars) |
| `country` | string | ISO 3166-1 alpha-2 country code (2 chars) |
| `postal_code` | string | Postal or ZIP code (max 20 chars) |
| `wheelchair_accessible` | string | Wheelchair accessibility: `yes`, `no`, or `limited` |
| `capacity` | int | Approximate book capacity |
| `is_indoor` | bool | Whether the library is inside a building |
| `is_lit` | bool | Whether the library is illuminated at night |
| `website` | string | External website link (max 500 chars) |
| `contact` | string | Contact information - email, phone, etc. (max 255 chars) |
| `operator` | string | Organisation that maintains the library (max 255 chars) |
| `brand` | string | Network or brand name (max 255 chars) |
| `latitude` | float | Latitude (-90 to 90, WGS 84). Must be sent with `longitude`. |
| `longitude` | float | Longitude (-180 to 180, WGS 84). Must be sent with `latitude`. |
| `photo` | file | Optional replacement photo (JPEG/PNG/WEBP, max 8 MB). Omit to keep the current photo. |

## Examples

=== "curl"

    ```bash
    curl -X PATCH https://bookcorners.org/api/v1/libraries/berlin-friedrichstr-12-corner-books \
      -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..." \
      -F "description=Freshly repainted with more children's books." \
      -F "capacity=60"
    ```

=== "Python"

    ```python
    import requests

    resp = requests.patch(
        "https://bookcorners.org/api/v1/libraries/berlin-friedrichstr-12-corner-books",
        headers={"Authorization": f"Bearer {access_token}"},
        data={
            "description": "Freshly repainted with more children's books.",
            "capacity": 60,
        },
    )
    print(resp.json())
    ```

### Replace the photo

=== "curl"

    ```bash
    curl -X PATCH https://bookcorners.org/api/v1/libraries/berlin-friedrichstr-12-corner-books \
      -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..." \
      -F "photo=@updated-library.jpg"
    ```

## Response (`200 OK`)

The response uses the same library object shape as the [detail endpoint](detail.md).

```json
{
  "id": 42,
  "slug": "berlin-friedrichstr-12-corner-books",
  "name": "Corner Books",
  "description": "Freshly repainted with more children's books.",
  "photo_url": "/media/libraries/photos/2025/06/corner-books.jpg",
  "thumbnail_url": "/media/libraries/thumbnails/2025/06/corner-books.jpg",
  "lat": 52.52,
  "lng": 13.405,
  "address": "Friedrichstr. 12",
  "city": "Berlin",
  "country": "DE",
  "postal_code": "10117",
  "wheelchair_accessible": "",
  "capacity": 60,
  "is_indoor": null,
  "is_lit": null,
  "website": "",
  "contact": "",
  "source": "",
  "operator": "",
  "brand": "",
  "created_at": "2025-06-15T14:30:00Z",
  "is_favourited": false
}
```

!!! note
    The updated library is set back to **pending** status. It will not appear in public listing or search results again until approved by a moderator.

## Errors

| Status | Cause |
|--------|-------|
| `400` | No fields were provided, coordinates were incomplete, or the photo format is invalid |
| `401` | Missing or invalid bearer token |
| `404` | Library not found, not owned by the authenticated user, rejected, or otherwise not editable |
| `413` | Photo exceeds 8 MB size limit |
| `422` | Request validation error (field too long, invalid type, out-of-range coordinate) |
| `429` | Rate limit exceeded (see [Rate Limiting](../rate-limiting.md)) |
