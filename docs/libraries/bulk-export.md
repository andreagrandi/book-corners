# Bulk Download

Authenticated users can download the complete catalogue of approved Book Corners libraries as a pre-generated GeoJSON file. The export is designed for offline use and avoids paginating or scraping the normal library-list endpoint. Signed-in website users can use the download card in the Contribution center; programmatic clients can use the JWT API endpoints below.

The API endpoints require an access token:

```text
Authorization: Bearer <access-token>
```

## Current compressed GeoJSON

`GET /api/v1/libraries/export/latest.geojson.gz`

Downloads the recommended pre-generated gzip artifact with `application/gzip`. This avoids transferring the much larger raw GeoJSON or spending request-time CPU on compression.

```bash
curl --location \
  --header "Authorization: Bearer $BOOK_CORNERS_ACCESS_TOKEN" \
  --output libraries.geojson.gz \
  https://bookcorners.org/api/v1/libraries/export/latest.geojson.gz
```

## Current raw GeoJSON

`GET /api/v1/libraries/export/latest.geojson`

Downloads the uncompressed complete GeoJSON artifact with `application/geo+json` and an attachment filename matching its immutable version.

```bash
curl --location \
  --header "Authorization: Bearer $BOOK_CORNERS_ACCESS_TOKEN" \
  --output libraries.geojson \
  https://bookcorners.org/api/v1/libraries/export/latest.geojson
```

## Metadata

`GET /api/v1/libraries/export/metadata.json`

Returns the matching metadata document, including raw and gzip filenames, byte sizes, SHA-256 checksums, record count, schema, ODbL 1.0 terms, Book Corners/OpenStreetMap attribution, and photo-URL notice.

## Immutable artifacts

The metadata and authenticated download page link to the current immutable artifact URLs:

```text
GET /api/v1/libraries/export/{filename}
```

Only the raw GeoJSON, gzip GeoJSON, and metadata filenames listed in the active manifest are available. Arbitrary paths and retained historical files return `404`.

## Caching and availability

Latest aliases return strong `ETag` and `Last-Modified` headers with `Cache-Control: private, no-cache`; clients can use `If-None-Match` or `If-Modified-Since` and receive `304 Not Modified` when the bytes are unchanged. Immutable URLs use one-year private immutable caching.

The scheduled generator checks the export daily and may leave it unchanged. Export generation does not run during application deployment. After publishing a changed export successfully, the generator removes older recognized artifacts while retaining the active version and seven previous complete versions; this is version-based retention, not a seven-day expiry. A disabled delivery feature or an unknown artifact returns `404`; an enabled service without a valid current artifact returns `503` with the normal API error shape. These bulk-download requests are not subject to the paginated API read rate limit.
