# OpenStreetMap Contribution Design

**Status:** Accepted implementation contract for [issue #124](https://github.com/andreagrandi/book-corners/issues/124)

**Last reviewed:** 2026-07-30

This document defines how Book Corners may prepare and manually contribute a
user-submitted public bookcase to OpenStreetMap (OSM). It defines future work; it
does not enable or perform an OSM write.

## Decision summary

- Permission is an unchecked, per-submission boolean. It defaults to `false`.
- Existing records and clients that omit the field are not opted in.
- There is no account-level OSM preference or dashboard setting.
- A true value permits later review. It never submits, queues, schedules, or
  guarantees an OSM contribution.
- The public library API does not expose the permission. The create response and
  the authenticated submitter's contribution response do.
- Later owner edits cannot enable or change the permission.
- Only approved, directly user-submitted, opted-in libraries with no unresolved
  update, duplicate, or previous contribution can become candidates.
- OSM imports, legacy records, staff-created records, ownerless records, and
  records with unknown provenance are ineligible.
- Every write is initiated and confirmed by an authorized administrator after a
  fresh duplicate check and exact tag preview.
- A dedicated OSM import account and an OAuth 2.0 token with only `write_api`
  permission are used. Book Corners never collects a submitter's OSM credentials.
- Account details, private contacts, photos, AI-generated text, and internal
  database identifiers are never sent to OSM.
- Production writes remain disabled until the OSM import/automated-edit review,
  local community consultation, and data-licensing gates in this document are
  complete.

## Submission permission and provenance

### Library fields

The existing `Library` row is the submission record. Add these fields in
[issue #126](https://github.com/andreagrandi/book-corners/issues/126):

| Field | Type and default | Purpose |
| --- | --- | --- |
| `osm_submission_allowed` | `BooleanField(default=False)` | The submitter permitted later manual OSM review for this submission. Do not add a database index to this low-cardinality field. |
| `osm_submission_allowed_at` | nullable timestamp | When a true permission was recorded. It remains null when the value is false or omitted. |
| `submission_origin` | indexed text choice, default `legacy` | Durable provenance with `legacy`, `user`, `staff`, and `import` values. |

The data migration must set every existing row to
`osm_submission_allowed=false`, `osm_submission_allowed_at=null`, and
`submission_origin=legacy`. It must not infer consent or direct submission from
`created_by`, because the GeoJSON importer also records a staff user in that
field.

New records set provenance as follows:

- Authenticated web and API creates set `submission_origin=user`.
- Django admin creates set `submission_origin=staff`.
- GeoJSON and future bulk imports set `submission_origin=import`.
- No import or staff workflow accepts `osm_submission_allowed=true`.

`source` and `external_id` remain data-origin metadata. They are additional
safety signals, not substitutes for `submission_origin`.

### API contract

`POST /api/v1/libraries/` accepts this optional multipart form field:

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `osm_submission_allowed` | boolean | `false` | Permit an administrator to consider this submission for a later manual OSM contribution. |

The server stores the value only after authenticated validation succeeds. A
true value has no external side effect.

The `201 Created` response uses a submission-specific response schema that
extends the existing library response with `osm_submission_allowed`. The same
field is added to each item returned by authenticated
`GET /api/v1/libraries/mine`, so a client can confirm the stored choice after
reconnecting.

The field is intentionally absent from public list and detail responses,
favourites, statistics, and unauthenticated schemas. It is permission metadata,
not a public property of a bookcase.

`PATCH /api/v1/libraries/{slug}` does not accept this field. An attempted update
is rejected as an unsupported field and does not change the stored value. Owner
edits, including staged edits to approved libraries, preserve the original
submission permission.

If a submitter asks to withdraw permission before contribution, staff may only
change `true` to `false`; staff cannot grant permission on the submitter's
behalf. The withdrawal and actor are recorded in the audit model described
below. The field is read-only in the generic Django admin; withdrawal uses a
dedicated action that enforces this direction. Enabling permission after the
original submission is outside this rollout and would require a new explicit
user-facing consent flow.

## Eligibility

A library is eligible for an OSM preview only when every condition below is
true:

1. `status=approved`.
2. `submission_origin=user`.
3. `created_by` still references the authenticated submitter.
4. `osm_submission_allowed=true`.
5. `source` does not identify OpenStreetMap and `external_id` does not contain
   an OSM element identifier.
6. No staged `pending_changes` or `pending_photo` awaits moderation.
7. No OSM element has already been recorded for the library.
8. The latest duplicate check is successful, has no unresolved possible
   duplicate, and is recent enough for the final confirmation.
9. The global policy, licensing, credentials, and production feature gates are
   enabled.

The same predicate is used for admin filters, the preview page, and the
transaction immediately before a write. The write path must re-read the row
with a database lock and re-evaluate the predicate; it must not trust a stale
admin page.

Deleting an account sets `created_by` to null under the existing model
relationship and therefore makes an unsubmitted library ineligible. Approval or
opt-in alone is never sufficient.

## OSM state and audit records

Add a one-to-one `OpenStreetMapContribution` model in
[issue #127](https://github.com/andreagrandi/book-corners/issues/127). It stores
the current operational state without overloading `Library.source`.

| Field | Purpose |
| --- | --- |
| `library` | One-to-one link to `Library`. |
| `status` | Indexed choice: `unchecked`, `no_match`, `possible_duplicate`, `already_present`, `submitting`, `contributed`, or `failed`. |
| `checked_at` | Time of the most recent completed duplicate check. |
| `duplicate_candidates` | Sanitized JSON list of OSM element type, ID, distance, relevant tags, and match signals. |
| `osm_element_type` | `node`, `way`, or `relation`; the initial create path only creates a node. |
| `osm_element_id` | OSM element ID when an existing or created feature is confirmed. |
| `changeset_id` | OSM changeset used for the contribution. |
| `contributed_at` | Time Book Corners confirmed the OSM result. |
| `contributed_by` | Staff user who confirmed the write. |
| `last_attempt_key` | Unique UUID used to serialize and reconcile one write attempt. |
| `last_error_code` | Stable internal error category without credentials or response bodies. |
| `last_error_message` | Sanitized operator-facing failure detail. |

Add a conditional uniqueness constraint for non-null
`(osm_element_type, osm_element_id)`. One Book Corners library may link to one
OSM feature, and the same OSM feature cannot satisfy two Book Corners
contributions.

Add an append-only `OpenStreetMapContributionEvent` model. Each event stores the
library/contribution, event type, outcome, staff actor when applicable,
timestamp, attempt UUID, and sanitized JSON details. Event types cover:

- permission withdrawal;
- duplicate check and its candidate snapshot;
- duplicate resolution with the staff reason;
- write started with the exact outgoing coordinate, tags, and changeset tags;
- write succeeded with the OSM element and changeset IDs;
- write failed or required reconciliation.

The event table is the audit history; the one-to-one record is the current
filterable state. Tokens, client secrets, private contacts, photos, and full HTTP
headers or bodies must never enter either model or application logs.

## Duplicate detection and conflation

The existing `find_duplicates` command detects Book Corners records by
normalized address and by same-street proximity within 100 metres. OSM
conflation is a separate check against current OSM data and must not reuse a
cached Book Corners-only result.

### Candidate query

For a manual preview, query a configured Overpass-compatible endpoint for
nodes, ways, and relations within 100 metres of the submitted point. Request:

- every `amenity=public_bookcase` feature;
- nearby features with `amenity=library`, `amenity=give_box`, or `shop=books`
  for secondary warnings; and
- the tags needed to compare `name`, address, `brand`, and `operator`.

The checker calculates distance to nodes and to the representative point of ways
and relations. It stores only the element type, element ID, distance, relevant
public tags, and triggered signals.

### Match signals

A result becomes a blocking `possible_duplicate` when any condition is true:

- an `amenity=public_bookcase` is within 25 metres;
- an `amenity=public_bookcase` within 100 metres has the same normalized name;
- an `amenity=public_bookcase` within 100 metres has the same normalized street
  and house number;
- a feature within 100 metres has matching normalized `brand` or `operator`
  together with a compatible book-sharing tag; or
- its OSM element ID is already linked to any Book Corners library.

Normalization trims whitespace, case-folds Unicode, removes punctuation for
comparison, and compares address components only when both sides contain them.
Missing tags never count as proof that no duplicate exists.

Other returned features are warnings in the preview. An authorized
administrator must still inspect the map and tags before confirming no match.

### Resolution and freshness

A possible duplicate blocks creation. The administrator must choose one of
these recorded outcomes:

- **Same feature:** link the existing OSM element, mark the contribution
  `already_present`, and do not create anything.
- **Different feature:** record a reason explaining the distinction. A fresh
  duplicate check is still required immediately before creation.
- **Unclear:** leave `possible_duplicate` and stop.

A failed query, rate limit, timeout, malformed response, or check older than 15
minutes blocks the write. The final confirmation always reruns the check; cached
results may render the preview but cannot authorize a write.

## Supported OSM mapping

The initial integration creates only a node at the approved, reviewed
`Library.location`. It does not modify existing OSM geometry.

| Book Corners value | OSM output | Rule |
| --- | --- | --- |
| Library type | `amenity=public_bookcase` | Always present. |
| `name` | `name=*` | Only when staff verifies it is a real on-site proper name. Never send an AI-generated or generic display name. |
| `capacity` | `capacity=*` | Positive integer only. |
| `is_indoor=true` | `location=indoor` | Omit for false or unknown. |
| `wheelchair_accessible` | `wheelchair=yes`, `no`, or `limited` | Map only the existing explicit values. |
| `is_lit` | `lit=yes` or `lit=no` | Omit when unknown. |
| `brand` | `brand=*` | Only when visible or otherwise independently verified. |
| `operator` | `operator=*` | Only when it is the public operator of the physical bookcase. |
| `website` | `website=*` | Only when it is a public page specifically for the bookcase or its operator. |

The admin preview starts with this allowlist and shows the exact final tags.
Unsupported fields cannot be added through free-form admin input.

The initial mapping deliberately excludes:

- `description`, because it may contain AI-generated or subjective text;
- combined Book Corners `address`, city, country, and postal code, because the
  current model cannot reliably produce a structured OSM address;
- `contact` and every account, email, phone, or private-contact field;
- Book Corners photos, thumbnails, captions, and media URLs;
- Book Corners IDs, slugs, moderation state, timestamps, and internal source
  metadata;
- `source` or `external_id` as element tags; and
- fields copied from an incompatible third-party map or dataset.

Adding another object tag or creating areas, ways, or relations changes the
consulted import scope and requires a new design review and, where applicable,
renewed OSM community approval.

## Changesets and API workflow

Every library is checked and confirmed individually. A one-off confirmation may
use a one-node changeset, but the system must not create a stream of one-object
changesets from a bulk selection. Repeated confirmations in one operator session
must be grouped by a small geographic area using a reviewed batch and one
changeset. A changeset never spans unrelated cities or regions. The final
grouping thresholds are recorded in the consulted OSM plan; changing them later
requires renewed consultation.

The changeset includes:

| Tag | Value |
| --- | --- |
| `comment` | Human-readable text such as `Add a reviewed public bookcase in Florence`. |
| `created_by` | `Book Corners/<deployed version>`. |
| `source` | The source wording approved during import consultation, expected to identify an individually reviewed Book Corners user submission. |
| `description` | Public OSM wiki URL documenting the import/automated-edit plan and contact route. |
| `mechanical` | `yes`, unless the recorded OSM consultation explicitly requires a different classification. |
| `review_requested` | `yes` during the initial monitored rollout. |

Attribution is carried by the approved `source` value, the linked wiki plan, the
dedicated account profile, and an OSM Contributors wiki entry if the consultation
requires one. If the approved data permission has a concise licence identifier,
record it as `source:license` on the changeset. Do not add attribution, licence,
or source metadata to the public-bookcase element itself unless the OSM
consultation specifically requires it.

The implementation uses the OSM API v0.6 workflow:

1. Revalidate eligibility and acquire the local write lock.
2. Record the `write_started` audit event and set `status=submitting`.
3. Create the changeset with `PUT /api/0.6/changeset/create` and immediately
   persist its ID.
4. Create the reviewed node with `POST /api/0.6/nodes`, or use the diff upload
   endpoint for a reviewed same-area batch.
5. Persist the returned node ID, close the changeset, and record success.
6. If closing fails after the node was created, preserve the successful element
   ID and reconcile the changeset rather than creating another node.

OSM changesets are not atomic. If a create response is lost or ambiguous, the
system must download the recorded changeset and reconcile its contents before
offering a retry. It must never blindly retry an OSM write.

## Authentication and editing account

Use a dedicated import account following the OSM naming convention, for example
`BookCorners_Import`. Its public profile links to:

- the responsible Book Corners project/account;
- the published import or automated-edit plan;
- a monitored contact route; and
- the relevant consultation record.

The account accepts the OSM contributor terms and is used only for this
documented workflow. Staff members do not contribute through personal OSM
accounts, and submitters are never asked for OSM access.

Register Book Corners as a confidential OAuth 2.0 application. Authorize the
dedicated account once with only the `write_api` scope. Do not request profile,
preferences, GPX, notes, diary, messaging, or other unrelated scopes.

The runtime sends the bearer token in the authorization header and verifies
`allow_write_api` through `GET /api/0.6/permissions` before enabling the admin
action. OSM access tokens do not currently expire automatically, but they can be
revoked; a missing permission or `401` response disables writes until an
operator rotates the token. The development OSM server uses a separate account,
application, and token because it does not share production data.

## Policy and licensing gates

Book Corners must treat this workflow as an external-data import and a
script-assisted edit unless the OSM community explicitly records a different
classification. Individual admin review does not waive the import requirements.

Before the first production write:

1. Publish an OSM wiki plan covering the dataset, rights, field mapping,
   conflation, software source, account, changeset tags, quality assurance,
   rollback, frequency, and community opt-out route.
2. Discuss the plan on the OSM Community Forum and with the appropriate local
   communities. Do not write until at least the documented import-review period
   has passed and concerns are resolved.
3. Record confirmation that Book Corners may release the submitted factual data
   under terms compatible with OSM's ODbL. A privacy notice or checkbox that
   merely permits submission is not, by itself, a data licence.
4. Make the frontend consent copy state that the submitter supplied factual
   information they are entitled to share, that it may be published in OSM
   under OSM's terms, and that it was not copied from an incompatible source
   such as Google Maps.
5. Publish the exact source attribution accepted during consultation and use it
   consistently in the wiki plan, account profile, and changesets.
6. Document how Book Corners will stop, investigate, and if necessary revert
   its changes in response to community concerns or opt-out requests.

These are hard launch gates. If licensing or community approval is uncertain,
the feature remains disabled even when a submitter opted in.

The rollback procedure is manual: disable writes, preserve the affected
changeset and audit records, notify the consulted community, and use an
experienced OSM mapper or the Data Working Group to plan a revert. Book Corners
must not run an automatic inverse edit because later mapper changes can make a
blind rollback destructive.

## Configuration, credentials, and service limits

All settings are environment configuration. Suggested names are:

| Setting | Requirement |
| --- | --- |
| `OSM_WRITE_ENABLED` | Defaults to `false`. It is the production kill switch. |
| `OSM_API_BASE_URL` | Required when enabled. Production and development URLs must be explicitly selected. |
| `OSM_OAUTH_ACCESS_TOKEN` | Secret bearer token for the dedicated account. |
| `OSM_OVERPASS_URL` | Configured duplicate-query provider. |
| `OSM_USER_AGENT` | Required application/version plus a monitored contact URL. |
| `OSM_PLAN_URL` | Published OSM wiki plan used in changeset metadata. |
| `OSM_DUPLICATE_RADIUS_METERS` | Defaults to `100`. |
| `OSM_DUPLICATE_CHECK_MAX_AGE_SECONDS` | Defaults to `900` (15 minutes). |

Store secrets through Dokku configuration, following the existing production
secret-management pattern. Do not store the bearer token in the database,
repository, admin forms, audit JSON, or logs. OAuth client credentials used for
manual token rotation are operator secrets and need not be available to the web
process.

OSM does not promise a fixed request allowance for this use case. The client
must:

- send a valid, contactable User-Agent containing the application version;
- query API capabilities rather than hard-code server element limits;
- keep this workflow manual and low-volume with one in-flight write;
- set bounded connect and response timeouts;
- respect `Retry-After` for `429` responses;
- retry read-only duplicate checks at most three times with bounded exponential
  backoff; and
- never automatically retry a create, update, or delete request with an
  ambiguous outcome.

Duplicate-check failures remain visible and block contribution. Write failures
record a sanitized stable error, preserve all known OSM IDs, and require
reconciliation before an administrator can retry.

## Deployment boundaries

Deliver the epic in this order:

1. **#126:** add default-off permission and provenance, API input, private
   responses, migrations, and tests. No OSM client or external side effect.
2. **#127:** add state/audit models, duplicate checks, admin filters, warnings,
   and resolution. No OSM write.
3. **#125:** add the exact preview, confirmation, OAuth client, reconciliation,
   and manual write action behind `OSM_WRITE_ENABLED=false`.
4. Complete the policy, licensing, account, credentials, development-server,
   and community gates.
5. Enable the web and iOS opt-in interfaces only after steps 1-4 are safely
   deployed. Existing and omitted values remain false, so backend-first
   deployment creates no candidates.
6. Perform a small, monitored production rollout. Keep the kill switch available
   and review every early changeset and any community feedback.

There is no task queue or periodic job for OSM contribution. Background AI
enrichment, moderation approval, and receipt of a true API field must never call
the OSM client.

## Verification required by implementation tickets

Automated tests must use a fake OSM transport and prove:

- omitted and explicit false permissions remain ineligible;
- different submissions by one user store independent values;
- legacy, imported, staff, ownerless, rejected, unapproved, and
  pending-update records are blocked;
- public responses do not expose permission;
- owner edits cannot enable or change permission;
- duplicate signals, stale checks, failed checks, and unresolved candidates
  block writes;
- permissions, provenance, approval, duplicate state, and prior contribution
  are rechecked inside the write lock;
- exact node and changeset payloads contain only allowlisted values;
- private contacts, photos, account data, and AI-generated text never enter
  payloads, audit data, or logs;
- concurrent or repeated actions cannot create a second feature;
- ambiguous outcomes require reconciliation rather than retry; and
- no test reaches the production OSM service.

Manual pre-production verification uses
`https://master.apis.dev.openstreetmap.org` with its separate account and token.
The operator must inspect the created node, changeset tags, audit history,
duplicate preview, error handling, and kill switch before production can be
considered.

## Sources reviewed

- [OSM Import Guidelines](https://wiki.openstreetmap.org/wiki/Import/Guidelines)
- [OSM Automated Edits code of conduct](https://wiki.openstreetmap.org/wiki/Automated_Edits_code_of_conduct)
- [OSM API v0.6](https://wiki.openstreetmap.org/wiki/Api06)
- [OSM OAuth 2.0](https://wiki.openstreetmap.org/wiki/OAuth)
- [OSM API Usage Policy](https://operations.osmfoundation.org/policies/api/)
- [`amenity=public_bookcase` tagging](https://wiki.openstreetmap.org/wiki/Tag%3Aamenity%3Dpublic_bookcase)
- [OSM changesets](https://wiki.openstreetmap.org/wiki/Changeset)
