# Errors

All API errors return a consistent JSON structure, making it straightforward to handle failures in client code.

## Error response format

```json
{
  "message": "Human-readable error description.",
  "details": null
}
```

| Field | Type | Description |
|-------|------|-------------|
| `message` | string | Always present. A human-readable description of the error. |
| `details` | object or null | Optional structured data. Contains field-level validation errors or rate limit info when applicable. |

## Status codes

| Code | Meaning | When it happens |
|------|---------|-----------------|
| `400` | Bad Request | Invalid input (bad email, duplicate username, unsupported photo format) |
| `401` | Unauthorized | Missing/invalid JWT token, or bad credentials on login |
| `404` | Not Found | Library slug doesn't exist or isn't visible to the current user |
| `413` | Payload Too Large | Uploaded photo exceeds the size limit (8 MB for libraries, 5 MB for reports) |
| `422` | Unprocessable Entity | Request validation failed (missing required fields, out-of-range values) |
| `429` | Too Many Requests | Rate limit exceeded (see [Rate Limiting](rate-limiting.md)) |
| `500` | Internal Server Error | Unexpected server error |

## Examples

### Validation error (422)

```json
{
  "message": "Validation error.",
  "details": {
    "errors": [
      {
        "loc": ["body", "address"],
        "msg": "field required",
        "type": "missing"
      }
    ]
  }
}
```

### Duplicate username (400)

```json
{
  "message": "Username already exists.",
  "details": null
}
```

### Not found (404)

```json
{
  "message": "Not found.",
  "details": null
}
```

### Rate limited (429)

```json
{
  "message": "Too many requests. Please try again later.",
  "details": {
    "retry_after": 42
  }
}
```

### Internal server error (500)

```json
{
  "message": "Internal server error.",
  "details": null
}
```

!!! note
    In production, `500` errors always return the generic message above. Debug details are never exposed to API clients.
