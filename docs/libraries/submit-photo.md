# Submit a Community Photo

`POST /api/v1/libraries/{slug}/photo`

Submit a community photo for an approved library. The photo starts in **pending** status and must be approved by a moderator before it appears publicly. Each user can submit up to 3 photos per library.

**Auth required:** Yes (`Bearer` token)

**Content type:** `multipart/form-data`

## Path parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `slug` | string | URL slug of the library to add a photo to |

## Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `photo` | file | Yes | Photo of the library (JPEG/PNG/WEBP, max 8 MB) |
| `caption` | string | No | Optional caption for the photo (max 200 chars) |

## Examples

=== "curl"

    ```bash
    curl -X POST https://bookcorners.org/api/v1/libraries/berlin-friedrichstr-12-corner-books/photo \
      -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..." \
      -F "photo=@community-photo.jpg" \
      -F "caption=Summer view from the park side"
    ```

=== "Python"

    ```python
    import requests

    resp = requests.post(
        "https://bookcorners.org/api/v1/libraries/berlin-friedrichstr-12-corner-books/photo",
        headers={"Authorization": f"Bearer {access_token}"},
        data={"caption": "Summer view from the park side"},
        files={"photo": open("community-photo.jpg", "rb")},
    )
    print(resp.json())
    ```

### Submit without a caption

=== "curl"

    ```bash
    curl -X POST https://bookcorners.org/api/v1/libraries/berlin-friedrichstr-12-corner-books/photo \
      -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..." \
      -F "photo=@community-photo.jpg"
    ```

=== "Python"

    ```python
    resp = requests.post(
        "https://bookcorners.org/api/v1/libraries/berlin-friedrichstr-12-corner-books/photo",
        headers={"Authorization": f"Bearer {access_token}"},
        files={"photo": open("community-photo.jpg", "rb")},
    )
    ```

## Response (`201 Created`)

```json
{
  "id": 15,
  "caption": "Summer view from the park side",
  "status": "pending",
  "created_at": "2025-06-15T14:30:00Z"
}
```

!!! note
    The photo will have **pending** status. It won't appear on the library detail page until approved by a moderator. Each user can submit up to 3 photos per library (rejected photos do not count towards this limit).

## Errors

| Status | Cause |
|--------|-------|
| `400` | Invalid photo format, or per-user limit of 3 photos reached for this library |
| `404` | Library not found or not in approved status |
| `413` | Photo exceeds 8 MB size limit |
| `422` | Request validation error |
| `429` | Rate limit exceeded (see [Rate Limiting](../rate-limiting.md)) |
