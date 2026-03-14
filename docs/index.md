# Book Corners API

Welcome to the **Book Corners API** — a REST API for discovering, submitting, and reporting little free libraries around the world.

## Base URL

```
https://bookcorners.org/api/v1/
```

## Features

- **Search & discover** libraries by text, city, country, postal code, or proximity
- **Submit** new library locations with photos for community moderation
- **Report** issues with existing libraries (damaged, missing, incorrect info)
- **JWT authentication** with access/refresh token lifecycle
- **Geospatial queries** powered by PostGIS

## Quick links

| Topic | Description |
|-------|-------------|
| [Getting Started](getting-started.md) | Register, authenticate, and make your first request |
| [Authentication](authentication.md) | Token lifecycle and all auth endpoints |
| [List & Search](libraries/list-and-search.md) | Browse and search the library catalogue |
| [Submit a Library](libraries/submit.md) | Add a new library with a photo |
| [Report an Issue](libraries/report.md) | Flag problems with a library |
| [Submit a Community Photo](libraries/submit-photo.md) | Add a photo to an existing library |
| [Statistics](statistics.md) | Platform-wide aggregate statistics |
| [Errors](errors.md) | Error response format and status codes |
| [Rate Limiting](rate-limiting.md) | Request limits and 429 handling |
| [API Reference](reference/openapi.md) | Interactive OpenAPI / Swagger UI |
