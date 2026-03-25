# Rate Limiting

The API enforces per-client rate limits to ensure fair usage. Limits are applied per IP address within a sliding time window.

## Rate limit tiers

All windows are **5 minutes** (300 seconds).

| Tier | Endpoints | Max requests per window |
|------|-----------|------------------------|
| **Read** | `GET /libraries/`, `GET /libraries/latest`, `GET /libraries/{slug}`, `GET /libraries/countries/`, `GET /statistics/` | 60 |
| **Write** | `POST /libraries/`, `POST /libraries/{slug}/report`, `POST /libraries/{slug}/photo` | 10 |
| **Auth — Login** | `POST /auth/login` | 10 |
| **Auth — Register** | `POST /auth/register` | 5 |
| **Auth — Refresh** | `POST /auth/refresh` | 15 |
| **Auth — Social** | `POST /auth/social` | 10 |

## 429 response format

When you exceed a rate limit, the API returns `429 Too Many Requests` with the standard [error format](errors.md) and a `retry_after` field in the `details` object:

```json
{
  "message": "Too many requests. Please try again later.",
  "details": {
    "retry_after": 42
  }
}
```

!!! note
    The `retry_after` value is the number of **seconds** until the current rate limit window resets. It is included in the JSON response body (`details.retry_after`), not as an HTTP header.

## Handling rate limits

=== "curl"

    ```bash
    # Check the response status code
    curl -s -o /tmp/response.json -w "%{http_code}" \
      https://bookcorners.org/api/v1/libraries/

    # If 429, read retry_after from the response body
    cat /tmp/response.json | jq '.details.retry_after'
    ```

=== "Python"

    ```python
    import time
    import requests

    resp = requests.get("https://bookcorners.org/api/v1/libraries/")

    if resp.status_code == 429:
        retry_after = resp.json()["details"]["retry_after"]
        print(f"Rate limited. Retrying in {retry_after} seconds...")
        time.sleep(retry_after)
    ```
