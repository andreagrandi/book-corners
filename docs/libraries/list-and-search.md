# List & Search Libraries

## List libraries

`GET /api/v1/libraries/`

Return a paginated list of approved libraries with optional search filters.

**Auth required:** No

### Query parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `q` | string | — | Free-text search across name, description, and address (max 200 chars) |
| `city` | string | — | Filter by city name (case-insensitive, max 100 chars) |
| `country` | string | — | Filter by ISO 3166-1 alpha-2 country code (max 2 chars) |
| `postal_code` | string | — | Filter by postal / ZIP code (max 20 chars) |
| `has_photo` | bool | — | Filter by photo presence: `true` for libraries with a photo, `false` for those without |
| `lat` | float | — | Latitude for proximity search (-90 to 90, requires `lng` and `radius_km`) |
| `lng` | float | — | Longitude for proximity search (-180 to 180, requires `lat` and `radius_km`) |
| `radius_km` | int | — | Search radius in kilometres (1–100, requires `lat` and `lng`) |
| `page` | int | `1` | Page number (1–1000) |
| `page_size` | int | `20` | Items per page (1–50) |

### Examples

#### Text search

=== "curl"

    ```bash
    curl "https://bookcorners.org/api/v1/libraries/?q=corner+books&page_size=5"
    ```

=== "Python"

    ```python
    import requests

    resp = requests.get(
        "https://bookcorners.org/api/v1/libraries/",
        params={"q": "corner books", "page_size": 5},
    )
    data = resp.json()
    ```

#### Filter by city and country

=== "curl"

    ```bash
    curl "https://bookcorners.org/api/v1/libraries/?city=Berlin&country=DE"
    ```

=== "Python"

    ```python
    resp = requests.get(
        "https://bookcorners.org/api/v1/libraries/",
        params={"city": "Berlin", "country": "DE"},
    )
    ```

#### Proximity search

Find libraries within 5 km of a point:

=== "curl"

    ```bash
    curl "https://bookcorners.org/api/v1/libraries/?lat=52.52&lng=13.405&radius_km=5"
    ```

=== "Python"

    ```python
    resp = requests.get(
        "https://bookcorners.org/api/v1/libraries/",
        params={"lat": 52.52, "lng": 13.405, "radius_km": 5},
    )
    ```

### Response (`200 OK`)

```json
{
  "items": [
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
      "wheelchair_accessible": "yes",
      "capacity": 50,
      "is_indoor": false,
      "is_lit": true,
      "website": "",
      "contact": "",
      "source": "",
      "operator": "",
      "brand": "Little Free Library",
      "created_at": "2025-06-15T14:30:00Z"
    }
  ],
  "pagination": {
    "page": 1,
    "page_size": 20,
    "total": 142,
    "total_pages": 8,
    "has_next": true,
    "has_previous": false
  }
}
```

### Rate limiting

This endpoint uses the **read** rate limit tier. See [Rate Limiting](../rate-limiting.md).

---

## Latest libraries

`GET /api/v1/libraries/latest`

Return the most recently approved libraries as a flat list (no pagination).

**Auth required:** No

### Query parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | int | `10` | Number of results (1–50) |
| `has_photo` | bool | — | Filter by photo presence: `true` for libraries with a photo, `false` for those without |

=== "curl"

    ```bash
    curl "https://bookcorners.org/api/v1/libraries/latest?limit=5"
    ```

=== "Python"

    ```python
    resp = requests.get(
        "https://bookcorners.org/api/v1/libraries/latest",
        params={"limit": 5},
    )
    ```

### Response (`200 OK`)

```json
{
  "items": [
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
      "wheelchair_accessible": "yes",
      "capacity": 50,
      "is_indoor": false,
      "is_lit": true,
      "website": "",
      "contact": "",
      "source": "",
      "operator": "",
      "brand": "Little Free Library",
      "created_at": "2025-06-15T14:30:00Z"
    }
  ]
}
```
