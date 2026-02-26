# Getting Started

This guide walks you through registering an account, obtaining a JWT token, and making your first authenticated API request.

## 1. Register an account

=== "curl"

    ```bash
    curl -X POST https://bookcorners.org/api/v1/auth/register \
      -H "Content-Type: application/json" \
      -d '{
        "username": "janedoe",
        "email": "jane@example.com",
        "password": "s3cure!Pass"
      }'
    ```

=== "Python"

    ```python
    import requests

    resp = requests.post(
        "https://bookcorners.org/api/v1/auth/register",
        json={
            "username": "janedoe",
            "email": "jane@example.com",
            "password": "s3cure!Pass",
        },
    )
    tokens = resp.json()
    ```

**Response** (`201 Created`):

```json
{
  "access": "eyJhbGciOiJIUzI1NiIs...",
  "refresh": "eyJhbGciOiJIUzI1NiIs..."
}
```

Save both tokens. The `access` token is short-lived; the `refresh` token lets you obtain new access tokens without re-authenticating.

## 2. Make an authenticated request

Use the `access` token in the `Authorization` header:

=== "curl"

    ```bash
    curl https://bookcorners.org/api/v1/auth/me \
      -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..."
    ```

=== "Python"

    ```python
    resp = requests.get(
        "https://bookcorners.org/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['access']}"},
    )
    print(resp.json())
    ```

**Response** (`200 OK`):

```json
{
  "id": 1,
  "username": "janedoe",
  "email": "jane@example.com"
}
```

## 3. Browse libraries (no auth required)

Public endpoints like listing libraries don't require authentication:

=== "curl"

    ```bash
    curl "https://bookcorners.org/api/v1/libraries/?city=Berlin&page_size=5"
    ```

=== "Python"

    ```python
    resp = requests.get(
        "https://bookcorners.org/api/v1/libraries/",
        params={"city": "Berlin", "page_size": 5},
    )
    data = resp.json()
    for lib in data["items"]:
        print(lib["name"], lib["city"])
    ```

## Next steps

- [Authentication](authentication.md) — full token lifecycle details
- [List & Search](libraries/list-and-search.md) — all search and filter options
- [Submit a Library](libraries/submit.md) — add a new library with a photo
