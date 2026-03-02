# Changelog

## v1.1.0

- Public statistics endpoint (`GET /statistics/`) with totals, top countries, and cumulative growth series
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
