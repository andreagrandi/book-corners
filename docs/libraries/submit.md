# Submit a Library

`POST /api/v1/libraries/`

Submit a new library location with a photo. The library starts in **pending** status and must be approved by a moderator before it appears publicly.

**Auth required:** Yes (`Bearer` token)

**Content type:** `multipart/form-data`

## Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | No | Display name (max 255 chars) |
| `description` | string | No | Free-text description (max 2000 chars) |
| `address` | string | Yes | Street address (max 255 chars) |
| `city` | string | Yes | City name (max 100 chars) |
| `country` | string | Yes | ISO 3166-1 alpha-2 country code (max 2 chars) |
| `postal_code` | string | No | Postal or ZIP code (max 20 chars) |
| `wheelchair_accessible` | string | No | Wheelchair accessibility: `yes`, `no`, or `limited` |
| `capacity` | int | No | Approximate book capacity |
| `is_indoor` | bool | No | Whether the library is inside a building |
| `is_lit` | bool | No | Whether the library is illuminated at night |
| `website` | string | No | External website link (max 500 chars) |
| `contact` | string | No | Contact information — email, phone, etc. (max 255 chars) |
| `operator` | string | No | Organisation that maintains the library (max 255 chars) |
| `brand` | string | No | Network or brand name (max 255 chars) |
| `latitude` | float | Yes | Latitude (-90 to 90, WGS 84) |
| `longitude` | float | Yes | Longitude (-180 to 180, WGS 84) |
| `photo` | file | Yes | Photo of the library (JPEG/PNG, max 8 MB) |

## Examples

=== "curl"

    ```bash
    curl -X POST https://bookcorners.org/api/v1/libraries/ \
      -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..." \
      -F "name=Corner Books" \
      -F "description=A cozy little free library near the park entrance." \
      -F "address=Friedrichstr. 12" \
      -F "city=Berlin" \
      -F "country=DE" \
      -F "postal_code=10117" \
      -F "latitude=52.52" \
      -F "longitude=13.405" \
      -F "photo=@library.jpg"
    ```

=== "Python"

    ```python
    import requests

    resp = requests.post(
        "https://bookcorners.org/api/v1/libraries/",
        headers={"Authorization": f"Bearer {access_token}"},
        data={
            "name": "Corner Books",
            "description": "A cozy little free library near the park entrance.",
            "address": "Friedrichstr. 12",
            "city": "Berlin",
            "country": "DE",
            "postal_code": "10117",
            "latitude": 52.52,
            "longitude": 13.405,
        },
        files={"photo": open("library.jpg", "rb")},
    )
    print(resp.json())
    ```

## Response (`201 Created`)

```json
{
  "id": 42,
  "slug": "berlin-friedrichstr-12-corner-books",
  "name": "Corner Books",
  "description": "A cozy little free library near the park entrance.",
  "photo_url": "/media/libraries/photos/2025/06/corner-books.jpg",
  "thumbnail_url": "/media/libraries/thumbnails/2025/06/corner-books.jpg",
  "lat": 52.52,
  "lng": 13.405,
  "address": "Friedrichstr. 12",
  "city": "Berlin",
  "country": "DE",
  "postal_code": "10117",
  "wheelchair_accessible": "",
  "capacity": null,
  "is_indoor": null,
  "is_lit": null,
  "website": "",
  "contact": "",
  "source": "",
  "operator": "",
  "brand": "",
  "created_at": "2025-06-15T14:30:00Z"
}
```

!!! note
    The returned library will have **pending** status. It won't appear in public listing or search results until approved by a moderator. You can view your pending library via the [detail endpoint](detail.md) while authenticated.

## Errors

| Status | Cause |
|--------|-------|
| `400` | Invalid fields or photo format |
| `413` | Photo exceeds 8 MB size limit |
| `422` | Request validation error (missing required fields, out-of-range values) |
| `429` | Rate limit exceeded (see [Rate Limiting](../rate-limiting.md)) |
