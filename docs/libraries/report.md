# Report an Issue

`POST /api/v1/libraries/{slug}/report`

Report a problem with an approved library. Reports are reviewed by moderators.

**Auth required:** Yes (`Bearer` token)

**Content type:** `multipart/form-data`

## Path parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `slug` | string | URL slug of the library to report |

## Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `reason` | string | Yes | Issue category (see values below) |
| `details` | string | No | Free-text description of the issue (max 2000 chars) |
| `photo` | file | No | Photo showing the issue (JPEG/PNG/WEBP, max 5 MB) |

### Reason values

| Value | Description |
|-------|-------------|
| `damaged` | The library is physically damaged |
| `missing` | The library is no longer at this location |
| `incorrect_info` | Listed information (address, name, etc.) is wrong |
| `inappropriate` | Inappropriate content found in the library |
| `other` | Other issue not covered above |

## Examples

=== "curl"

    ```bash
    curl -X POST https://bookcorners.org/api/v1/libraries/berlin-friedrichstr-12-corner-books/report \
      -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..." \
      -F "reason=damaged" \
      -F "details=The door hinge is broken and books are getting wet." \
      -F "photo=@issue.jpg"
    ```

=== "Python"

    ```python
    import requests

    resp = requests.post(
        "https://bookcorners.org/api/v1/libraries/berlin-friedrichstr-12-corner-books/report",
        headers={"Authorization": f"Bearer {access_token}"},
        data={
            "reason": "damaged",
            "details": "The door hinge is broken and books are getting wet.",
        },
        files={"photo": open("issue.jpg", "rb")},
    )
    print(resp.json())
    ```

### Report without a photo

=== "curl"

    ```bash
    curl -X POST https://bookcorners.org/api/v1/libraries/berlin-friedrichstr-12-corner-books/report \
      -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..." \
      -F "reason=missing" \
      -F "details=The library box has been removed."
    ```

=== "Python"

    ```python
    resp = requests.post(
        "https://bookcorners.org/api/v1/libraries/berlin-friedrichstr-12-corner-books/report",
        headers={"Authorization": f"Bearer {access_token}"},
        data={
            "reason": "missing",
            "details": "The library box has been removed.",
        },
    )
    ```

## Response (`201 Created`)

```json
{
  "id": 7,
  "reason": "damaged",
  "created_at": "2025-06-15T14:30:00Z"
}
```

## Errors

| Status | Cause |
|--------|-------|
| `400` | Invalid reason value or photo format |
| `404` | Library not found or not in approved status |
| `413` | Photo exceeds 5 MB size limit |
| `422` | Request validation error (missing reason) |
| `429` | Rate limit exceeded (see [Rate Limiting](../rate-limiting.md)) |
