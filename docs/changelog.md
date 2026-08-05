# Changelog

## Unreleased

- The public Contributor Agreement v1.0 documents the ODbL-compatible data grant, CC BY-SA 4.0 image licence, attribution and reuse requirements, agreement-version changes, account deletion effects, and possible OpenStreetMap reuse without promising any write-back.
- Authenticated users can download the complete approved-library GeoJSON export and its metadata through `GET /api/v1/libraries/export/latest.geojson` and `GET /api/v1/libraries/export/metadata.json` with a JWT Bearer token. Current immutable artifact URLs are also available through the API.
- `GET /api/v1/libraries/export/latest.geojson.gz` provides the recommended precompressed download while the raw GeoJSON endpoint remains available.
- API descriptions, examples, and documentation now use neutral public-bookcase terminology while preserving genuine source-provided brand data.

## v1.18.0

- Authenticated library submissions can include an optional, default-off `osm_submission_allowed` choice for that individual submission. The create response and authenticated `GET /api/v1/libraries/mine` items expose the stored choice, while public responses and owner edits do not.
- Library records now keep durable `legacy`, `user`, `staff`, or `import` submission provenance and the timestamp of an explicit true OSM permission. Existing records remain default-off legacy entries, and imports and direct staff creates cannot record user permission.
- Recording permission only allows possible later manual administrator review. It never triggers or guarantees an OpenStreetMap contribution.
- Administrators can now filter OSM candidates, run read-only duplicate checks, inspect sanitized match warnings, record resolutions, and withdraw permission with an append-only audit history. This workflow never writes to OpenStreetMap.

## v1.17.0

- Updates to approved libraries now stage only the proposed fields and replacement photo for moderation. The existing approved library remains publicly available with the same ID and slug until the update is approved; rejecting the update discards only the proposal.
- Staff moderation lists and detail responses expose staged values as pending previews, and moderation approval or rejection applies to the proposal without withdrawing the live library.
- Unchanged approved-library edits no longer create moderator notifications or move the item in the review queue.
- The moderation dashboard now previews proposed values, and library queue rows distinguish new submissions from staged edits.
- Replacing a library's live photo now removes superseded unreferenced files after the database update commits while preserving files shared with community photos.
- Setting a staged library update to `pending` through the moderation API is now documented and tested as an idempotent `200 OK` operation.

## v1.16.0

- Library, replacement, report, and community photo uploads now accept files up to 10 MB in JPEG, PNG, WebP, HEIC, and HEIF formats. Accepted photos are normalized to optimized JPEGs targeting a stored size of about 500 KB for consistent storage and browser rendering.

## v1.15.0

- Refresh tokens now last 365 days instead of 1 day, so API clients stay signed in until the refresh token expires or the user logs out. Access token lifetime is unchanged (5 minutes).

## v1.14.0

- Contribution center endpoints allow authenticated users to list their own submitted libraries, reports, and community photos with moderation status via `GET /api/v1/libraries/mine`, `GET /api/v1/libraries/mine/reports`, and `GET /api/v1/libraries/mine/photos`.

## v1.13.0

- Auth device endpoints allow iOS clients to register and unregister APNs tokens with `POST /api/v1/auth/devices` and `DELETE /api/v1/auth/devices/{token}`.
- Approval, rejection, and moderation-work events now queue APNs push notifications when APNs credentials are configured.
- Invalid APNs tokens are removed when delivery returns an inactive or bad-token response.

## v1.12.0

- Auth profile endpoint (`GET /api/v1/auth/me`) now includes `is_staff` so clients can detect staff users after login.
- Staff moderation endpoints allow staff users to load dashboard counts, list all libraries across moderation statuses, view pending/rejected library details, and set library status via `GET /api/v1/libraries/moderation`, `GET /api/v1/libraries/moderation/{slug}`, and `PATCH /api/v1/libraries/moderation/{slug}`. Authenticated non-staff users receive a structured `403` response.
- Staff report moderation endpoints allow listing user reports and setting report status via `GET /api/v1/libraries/moderation/reports` and `PATCH /api/v1/libraries/moderation/reports/{report_id}`.
- Staff photo moderation endpoints allow listing community photo submissions and setting photo status via `GET /api/v1/libraries/moderation/photos` and `PATCH /api/v1/libraries/moderation/photos/{photo_id}`.

## v1.11.0

- Library update endpoint (`PATCH /api/v1/libraries/{slug}`) allows authenticated submitters to edit their own pending or approved library submissions, with optional replacement photos. Approved-library edits were changed in v1.17.0 to keep the live version available during review.

## v1.10.0

- Library list endpoint (`GET /api/v1/libraries/`) accepts a new `search` query parameter for a global simple search across name, description, city, address, and postal code. It supports substring matches (useful for partial addresses and postal codes) and ranks name matches above other field matches when full-text search is available. The existing `q` parameter is unchanged and remains a focused name/description search with full-text ranking. When both parameters are provided, `search` takes precedence.

## v1.9.0

- Library submit endpoint (`POST /api/v1/libraries/`): the `address` field is now conditionally optional. It may be empty only when coordinates (`latitude` and `longitude`) are provided, which supports libraries located in places without a classic street address (e.g. inside a park). Coordinates remain mandatory; an empty address with no coordinates is rejected. The `address` field in library response payloads may now be an empty string.

## v1.8.0

- Favourites: authenticated users can mark (`POST /libraries/{slug}/favourite`) and unmark (`DELETE /libraries/{slug}/favourite`) approved libraries as favourites.
- Favourites list endpoint (`GET /api/v1/libraries/favourites`) returns the user's favourited libraries with pagination, ordered by when they were favourited.
- Library detail, list, and latest endpoints now include an `is_favourited` field indicating whether the authenticated user has favourited the library.

## v1.7.0

- Change email endpoint (`PATCH /auth/me/email`) allows authenticated users to update their email address with uniqueness validation.
- Change password endpoint (`PUT /auth/me/password`) allows authenticated users to change their password with current password verification and Django password policy validation.
- Delete account endpoint (`DELETE /auth/me`) allows authenticated users to permanently delete their account with password confirmation.

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
