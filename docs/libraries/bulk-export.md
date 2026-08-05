# Bulk Download

Authenticated users can download the complete catalogue of approved Book Corners libraries as a pre-generated GeoJSON file. The export is intended for offline reuse and is the supported way to retrieve every approved record; do not paginate or scrape the normal library-list endpoint to copy the catalogue.

Every library whose current status is `approved` is included, regardless of how it was submitted or imported. Pending and rejected libraries are excluded. Signed-in website users can use the download card in the Contribution center, while programmatic clients can use the JWT API endpoints.

## Download URLs

The recommended artifact is the precompressed GeoJSON file. The raw GeoJSON and its metadata remain available for clients that need them.

| Purpose | Website session | JWT API |
|---------|-----------------|---------|
| Download page | `GET /data/libraries/` | — |
| Current compressed GeoJSON | `GET /data/libraries/latest.geojson.gz` | `GET /api/v1/libraries/export/latest.geojson.gz` |
| Current raw GeoJSON | `GET /data/libraries/latest.geojson` | `GET /api/v1/libraries/export/latest.geojson` |
| Current metadata | `GET /data/libraries/metadata.json` | `GET /api/v1/libraries/export/metadata.json` |
| Current immutable artifact | `GET /data/libraries/{filename}` | `GET /api/v1/libraries/export/{filename}` |

Website routes require a signed-in Book Corners session. API routes require a JWT access token:

```text
Authorization: Bearer <access-token>
```

Download the recommended gzip artifact:

```bash
curl --location \
  --header "Authorization: Bearer $BOOK_CORNERS_ACCESS_TOKEN" \
  --output libraries.geojson.gz \
  https://bookcorners.org/api/v1/libraries/export/latest.geojson.gz
```

The compressed response has MIME type `application/gzip`. The uncompressed response has MIME type `application/geo+json`, and metadata uses `application/json`.

Only the raw GeoJSON, gzip GeoJSON, and metadata filenames listed in the active manifest are downloadable. Arbitrary paths and retained historical files return `404`.

## GeoJSON structure

The raw artifact is UTF-8 encoded GeoJSON. Its top-level fields are:

| Field | Value |
|-------|-------|
| `type` | Always `FeatureCollection`. |
| `book_corners_schema_version` | Integer version of the public export schema; currently `1`. |
| `features` | Approved libraries ordered by ascending Book Corners `id`. |

Each item in `features` is a GeoJSON `Feature` with a WGS 84 `Point` geometry. Coordinates use GeoJSON order: `[longitude, latitude]`.

All documented properties are present on every feature:

| Property | Type and null behavior | Meaning |
|----------|------------------------|---------|
| `id` | integer | Stable Book Corners record identifier. Use this to identify and reconcile records. |
| `slug` | string | Book Corners URL slug for the library detail page. |
| `name` | string | Public library name; may be an empty string. |
| `description` | string | Public description; may be an empty string. |
| `photo_url` | absolute URI string | Current primary-photo URL, or an empty string when no primary photo is available. |
| `address` | string | Street or place address; may be an empty string. |
| `city` | string | City or locality. |
| `country` | string | Two-letter country code. |
| `postal_code` | string | Postal or ZIP code; may be an empty string. |
| `wheelchair_accessible` | string | `yes`, `no`, `limited`, or an empty string when unknown. |
| `capacity` | integer or `null` | Estimated book capacity, or `null` when unknown. |
| `is_indoor` | boolean or `null` | Whether the library is indoors, or `null` when unknown. |
| `is_lit` | boolean or `null` | Whether the library is lit, or `null` when unknown. |
| `website` | URI string | Public website, or an empty string when unavailable. |
| `contact` | string | Public contact information, or an empty string when unavailable. |
| `source` | string or `null` | Provenance label supplied for the record, or `null` when unavailable. |
| `operator` | string | Public operator name, or an empty string when unavailable. |
| `brand` | string | Public brand name, or an empty string when unavailable. |
| `external_id` | string or `null` | Identifier assigned by the named source, or `null` when unavailable. |
| `created_at` | UTC date-time string | Record creation time in RFC 3339 format ending in `Z`. |
| `updated_at` | UTC date-time string | Last live-record update time in RFC 3339 format ending in `Z`. |

The nullable values are limited to `capacity`, `is_indoor`, `is_lit`, `source`, and `external_id`. Other unknown optional values use an empty string. A schema change increments `book_corners_schema_version` and produces a new immutable artifact.

### Excluded and transformed data

The export contains the live public values of approved libraries only:

- `location` is transformed into the GeoJSON `Point` geometry, and the primary `photo` storage name is transformed into `photo_url`.
- The library fields `photo_thumbnail`, `pending_photo`, `pending_photo_thumbnail`, and `pending_changes` are excluded.
- The library fields `status`, `rejection_reason`, `created_by`, `submission_origin`, `osm_submission_allowed`, and `osm_submission_allowed_at` are excluded.
- Pending and rejected library rows are excluded entirely.
- User accounts, authentication data, reports and comments, community-photo submissions, favourites, moderation queues, social-post records, and OpenStreetMap contribution or audit records are excluded.

The `source` and `external_id` properties are provenance metadata. They may be absent, are not guaranteed to be unique, and must not replace the Book Corners `id` as the record identifier.

## Metadata and checksums

The metadata document describes the exact raw and gzip artifacts. Its fields are:

| Field | Meaning |
|-------|---------|
| `metadata_version` | Version of the metadata document format; currently `2`. |
| `title`, `scope`, `format`, `media_type` | Human-readable dataset description and the raw GeoJSON MIME type. |
| `generated_at` | UTC RFC 3339 time when this data version was generated. |
| `record_count` | Number of GeoJSON features. |
| `schema.version` | Public GeoJSON schema version. |
| `schema.sha256` | SHA-256 of the canonical schema definition. |
| `schema.definition` | Machine-readable geometry and property type definition. |
| `data.filename`, `data.byte_size`, `data.sha256` | Immutable raw GeoJSON filename, size, and SHA-256. |
| `gzip.filename`, `gzip.byte_size`, `gzip.sha256` | Immutable gzip filename, size, and SHA-256. |
| `license` | ODbL 1.0 name and license URL. |
| `attribution` | Linked Book Corners and OpenStreetMap contributor credits. |
| `photo_notice` | Reminder that photo-file rights are separate from database rights. |

Validate a completed download against the matching metadata:

```bash
sha256sum libraries.geojson.gz
```

Immutable filenames use this convention:

```text
libraries-<UTC timestamp>-<schema SHA-256 prefix>-<data SHA-256 prefix>.geojson
libraries-<UTC timestamp>-<schema SHA-256 prefix>-<data SHA-256 prefix>.geojson.gz
libraries-<UTC timestamp>-<schema SHA-256 prefix>-<data SHA-256 prefix>.metadata.json
```

The timestamp is formatted as `YYYYMMDDTHHMMSSffffffZ`; each checksum prefix contains 12 hexadecimal characters.

## Refreshes, caching, and availability

The scheduled generator checks the approved catalogue once per day. It creates a new immutable version only when either the deterministic raw GeoJSON checksum or the schema checksum changes. An unchanged run updates the latest successful check time while preserving the existing filenames, `generated_at`, and content checksums.

Under normal operation, a newly approved or updated record can therefore take up to 24 hours to appear. The download page shows when the catalogue was last checked and when the active data version was generated. If a generation attempt fails, the last valid version remains active and can become more than 24 hours old until a later run succeeds.

Latest aliases return strong `ETag` and `Last-Modified` headers with `Cache-Control: private, no-cache`. Clients can send `If-None-Match` or `If-Modified-Since` and receive `304 Not Modified` when the bytes are unchanged. Immutable URLs use one-year private immutable caching.

After a successful check, the generator retains the active version and seven previous complete versions in persistent storage. Retention is version-based, not a seven-day expiry, and only the active version is downloadable.

A disabled delivery feature or an unknown artifact returns `404`. An enabled service without a valid current artifact returns `503` with the normal API error shape. Bulk-download requests are not subject to the paginated API read rate limit.

## License, attribution, and photos

The complete exported database is made available under the [Open Data Commons Open Database License (ODbL) v1.0](https://opendatacommons.org/licenses/odbl/1-0/). The license permits reuse subject to its notice, attribution, and share-alike conditions. In summary:

- When publicly conveying the database or a derivative database, include the ODbL URI and keep the database-right and license notices intact.
- A publicly used derivative database must use ODbL 1.0, a permitted later version, or a compatible license, and the ODbL conditions for machine-readable access apply.
- A publicly used produced work must include a notice that its data came from the Book Corners export and is available under ODbL 1.0.

Consult the license text for the complete conditions.

When attribution is required for the database, credit both [Book Corners](https://bookcorners.org/) and [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), and link to the ODbL 1.0 license. For example:

> Contains information from the Book Corners approved library export, made available under ODbL 1.0. Attribution: Book Corners and OpenStreetMap contributors.

ODbL covers the exported database, not independent rights in each linked image file. `photo_url` identifies media displayed by Book Corners; it does not grant permission to copy, redistribute, or relicense that image.

The [Contributor Agreement v1.0](https://bookcorners.org/contributor-agreement/1.0/en/) selects [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) for Images contributed by a person who accepted that agreement. That image licence requires appropriate credit, a link to the licence, indication of changes, and share-alike treatment for adaptations where required. It is separate from the ODbL licence for the database.

The export currently contains `photo_url` but does not provide per-image licence or contributor metadata. Do not assume that every linked image is available under CC BY-SA 4.0: imported images, legacy images without a matching agreement acceptance, and third-party images may have different rights or no reuse permission. Verify the applicable image rights before copying or redistributing an image.

## Report incorrect data

Use a feature's `slug` to open `https://bookcorners.org/library/{slug}/`, sign in, and select **Report an issue** on the library detail page. Reports are sent to Book Corners moderators and are not included in the export.
