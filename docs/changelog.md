# Changelog

## v1.6.0

- Library submissions are now enriched with AI-generated name and description when the user leaves those fields blank. The description is also used as image alt text for improved accessibility.

## v1.5.0

- Social login endpoint (`POST /auth/social`) exchanges native Apple or Google identity tokens for JWT token pairs. Supports automatic account creation, email-based account linking, and Apple first sign-in name capture.

## v1.4.0

- Country list endpoint (`GET /api/v1/libraries/countries/`) returns all countries with approved libraries and counts, ordered by count descending

## v1.3.0

- Community photo endpoint (`POST /api/v1/libraries/{slug}/photo`) is now documented
- Fixed `q` search parameter description: searches name and description (not address)
- Fixed image format documentation: JPEG/PNG/WEBP accepted (not just JPEG/PNG)
- Fixed `GET /api/v1/statistics/` path in docs (was missing `/api/v1/` prefix)
- Added `POST /libraries/{slug}/photo` and `GET /statistics/` to rate-limiting documentation

## v1.2.0

- Login endpoint (`POST /auth/login`) now accepts email address in the `username` field, matching the web login flow. Email lookup is case-insensitive and the identifier is trimmed before authentication.

## v1.1.0

- Public statistics endpoint (`GET /api/v1/statistics/`) with totals, top countries, and cumulative growth series
- Community photo submissions count towards the "libraries with photos" statistic

## v1.0.0

*Initial release*

- JWT authentication (register, login, refresh, me)
- Library listing with text search, city/country/postal code filtering, and proximity search
- Library detail by slug with owner visibility for pending submissions
- Library submission with photo upload and moderation workflow
- Issue reporting with reason categories and optional photo
- Paginated responses with navigation metadata
- Rate limiting for read, write, and auth endpoints
- Consistent error response format across all endpoints
