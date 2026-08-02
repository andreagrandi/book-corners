# Bulk Download

Authenticated users can download the complete catalogue of approved Book Corners libraries as a pre-generated GeoJSON file. The export is designed for offline use and avoids paginating or scraping the normal library-list endpoint. Signed-in website users can use the download card in the Contribution center; programmatic clients can use the JWT API endpoints below.

The API endpoints require an access token:

```text
Authorization: Bearer <access-token>
```

## Current GeoJSON

`GET /api/v1/libraries/export/latest.geojson`

Downloads the current complete GeoJSON artifact with `application/geo+json` and an attachment filename matching its immutable version.

```bash
curl --location \
  --header "Authorization: Bearer $BOOK_CORNERS_ACCESS_TOKEN" \
  --output libraries.geojson \
  https://bookcorners.org/api/v1/libraries/export/latest.geojson
```

## Metadata

`GET /api/v1/libraries/export/metadata.json`

Returns the matching metadata document, including the GeoJSON filename, record count, SHA-256 checksums, schema, ODbL 1.0 terms, Book Corners/OpenStreetMap attribution, and photo-URL notice.

## Immutable artifacts

The metadata and authenticated download page link to the current immutable artifact URLs:

```text
GET /api/v1/libraries/export/{filename}
```

Only the GeoJSON and metadata filenames listed in the active manifest are available. Arbitrary paths and retained historical files return `404`.

## Caching and availability

Latest aliases return strong `ETag` and `Last-Modified` headers with `Cache-Control: private, no-cache`; clients can use `If-None-Match` or `If-Modified-Since` and receive `304 Not Modified` when the bytes are unchanged. Immutable URLs use one-year private immutable caching.

The export is checked daily and may be unchanged. A disabled delivery feature or an unknown artifact returns `404`; an enabled service without a valid current artifact returns `503` with the normal API error shape. These bulk-download requests are not subject to the paginated API read rate limit.
