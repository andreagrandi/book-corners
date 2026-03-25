# Authentication

The Book Corners API uses **JWT (JSON Web Tokens)** for authentication. Authenticated endpoints require a `Bearer` token in the `Authorization` header.

## Token lifecycle

1. **Register** or **Login** to receive an access/refresh token pair
2. Use the **access token** for API requests (`Authorization: Bearer <access>`)
3. When the access token expires, use the **refresh token** to get a new one
4. When the refresh token expires, log in again

## Endpoints

### Social Login

`POST /api/v1/auth/social`

Exchange a native Apple or Google identity token for a JWT token pair. Designed for iOS/Android apps that authenticate via native SDKs (Sign in with Apple, Google Sign-In).

On first sign-in, a new user account is created automatically. If the email matches an existing account, the social identity is linked to it. Subsequent logins return tokens for the existing user.

**Auth required:** No

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `provider` | string | Yes | Social provider: `"apple"` or `"google"` |
| `id_token` | string | Yes | Identity token JWT from the native SDK (min 20 characters) |
| `first_name` | string | No | First name (Apple only provides on first sign-in, max 150 characters) |
| `last_name` | string | No | Last name (Apple only provides on first sign-in, max 150 characters) |

=== "curl"

    ```bash
    curl -X POST https://bookcorners.org/api/v1/auth/social \
      -H "Content-Type: application/json" \
      -d '{
        "provider": "apple",
        "id_token": "eyJraWQiOiJBSURPUEsxIi...",
        "first_name": "Jane",
        "last_name": "Doe"
      }'
    ```

=== "Swift"

    ```swift
    let body: [String: Any] = [
        "provider": "apple",
        "id_token": identityToken,
        "first_name": fullName?.givenName ?? "",
        "last_name": fullName?.familyName ?? ""
    ]
    var request = URLRequest(url: URL(string: "https://bookcorners.org/api/v1/auth/social")!)
    request.httpMethod = "POST"
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.httpBody = try JSONSerialization.data(withJSONObject: body)
    ```

**Success** (`200 OK`):

```json
{
  "access": "eyJhbGciOiJIUzI1NiIs...",
  "refresh": "eyJhbGciOiJIUzI1NiIs..."
}
```

**Errors:**

| Status | Message |
|--------|---------|
| `400` | `"Unsupported provider. Use 'apple' or 'google'."` |
| `400` | `"Invalid identity token."` |
| `429` | `"Too many social login attempts. Please try again later."` |

---

### Register

`POST /api/v1/auth/register`

Create a new user account and receive a token pair.

**Auth required:** No

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `username` | string | Yes | Unique username (3–150 characters) |
| `email` | string | Yes | Valid email address |
| `password` | string | Yes | Password (8–128 characters, validated against Django password policies) |

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
    print(resp.json())
    ```

**Success** (`201 Created`):

```json
{
  "access": "eyJhbGciOiJIUzI1NiIs...",
  "refresh": "eyJhbGciOiJIUzI1NiIs..."
}
```

**Errors:**

| Status | Message |
|--------|---------|
| `400` | `"Username already exists."` |
| `400` | `"Email already exists."` |
| `400` | `"Provide a valid email address."` |
| `400` | Password policy violation message |
| `429` | `"Too many registration attempts. Please try again later."` |

---

### Login

`POST /api/v1/auth/login`

Authenticate with credentials and receive a token pair.

The `username` field accepts either a **username** or an **email address**. When an email
is provided, the server resolves it to the corresponding account (case-insensitive lookup).
This matches the web login flow.

**Auth required:** No

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `username` | string | Yes | Username or email address |
| `password` | string | Yes | Account password |

=== "curl (username)"

    ```bash
    curl -X POST https://bookcorners.org/api/v1/auth/login \
      -H "Content-Type: application/json" \
      -d '{
        "username": "janedoe",
        "password": "s3cure!Pass"
      }'
    ```

=== "curl (email)"

    ```bash
    curl -X POST https://bookcorners.org/api/v1/auth/login \
      -H "Content-Type: application/json" \
      -d '{
        "username": "jane@example.com",
        "password": "s3cure!Pass"
      }'
    ```

=== "Python"

    ```python
    # Login by username
    resp = requests.post(
        "https://bookcorners.org/api/v1/auth/login",
        json={"username": "janedoe", "password": "s3cure!Pass"},
    )
    tokens = resp.json()

    # Login by email (same field, same endpoint)
    resp = requests.post(
        "https://bookcorners.org/api/v1/auth/login",
        json={"username": "jane@example.com", "password": "s3cure!Pass"},
    )
    tokens = resp.json()
    ```

**Success** (`200 OK`):

```json
{
  "access": "eyJhbGciOiJIUzI1NiIs...",
  "refresh": "eyJhbGciOiJIUzI1NiIs..."
}
```

**Errors:**

| Status | Message |
|--------|---------|
| `401` | `"Invalid credentials."` |
| `429` | `"Too many login attempts. Please try again later."` |

---

### Refresh

`POST /api/v1/auth/refresh`

Exchange a valid refresh token for a new access token.

**Auth required:** No

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `refresh` | string | Yes | Refresh token from login or registration |

=== "curl"

    ```bash
    curl -X POST https://bookcorners.org/api/v1/auth/refresh \
      -H "Content-Type: application/json" \
      -d '{"refresh": "eyJhbGciOiJIUzI1NiIs..."}'
    ```

=== "Python"

    ```python
    resp = requests.post(
        "https://bookcorners.org/api/v1/auth/refresh",
        json={"refresh": tokens["refresh"]},
    )
    new_access = resp.json()["access"]
    ```

**Success** (`200 OK`):

```json
{
  "access": "eyJhbGciOiJIUzI1NiIs..."
}
```

**Errors:**

| Status | Message |
|--------|---------|
| `401` | `"Invalid or expired refresh token."` |
| `429` | `"Too many refresh attempts. Please try again later."` |

---

### Me

`GET /api/v1/auth/me`

Return the profile of the currently authenticated user.

**Auth required:** Yes (`Bearer` token)

=== "curl"

    ```bash
    curl https://bookcorners.org/api/v1/auth/me \
      -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..."
    ```

=== "Python"

    ```python
    resp = requests.get(
        "https://bookcorners.org/api/v1/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    print(resp.json())
    ```

**Success** (`200 OK`):

```json
{
  "id": 1,
  "username": "janedoe",
  "email": "jane@example.com"
}
```

**Errors:**

| Status | Message |
|--------|---------|
| `401` | Unauthorized (missing or invalid token) |
