# Book Corners — Project Plan

> A community-driven directory of little free libraries: those small book exchange spots
> found in public spaces where you can leave or take a book for free.

**Status:** Draft — iterate and refine as needed.

---

## Tech Stack

- **Backend:** Python, Django, Django Ninja (REST API)
- **Database:** PostgreSQL + PostGIS
- **Frontend:** Django templates, HTMX, TailwindCSS + daisyUI
- **Minimal JavaScript** — only where strictly necessary (e.g. Leaflet map init)
- **Geocoding:** Nominatim — OpenStreetMap's free geocoding service.
  Converts addresses to coordinates and vice versa. Public API, no key needed.
  Usage policy: max 1 req/sec, custom User-Agent. Use via `geopy` library.
  More than sufficient for the expected scale of this project.
- **Address autocomplete:** Photon (photon.komoot.io) — free, open-source,
  built on OSM data, designed for typeahead/autocomplete. No API key needed.
  Use Nominatim for the final geocode on submit, Photon for the typing experience.
- **Maps:** Leaflet + OpenStreetMap tiles
- **Search:** PostgreSQL full-text search (`SearchVector` / `SearchQuery`)
- **Local dev:** Docker Compose (app + PostgreSQL/PostGIS)
- **Deployment (later):** Dokku on a VPS — reuses the same Dockerfile from local dev
- **Media storage:** Local filesystem. Django's storage backend is swappable, so
  migrating to S3-compatible storage later is a config change, not a rewrite.

---

## Data Model (rough)

### User

Django custom user model from the start (changing later is painful).
Keep it minimal: email, username, password. Extend if needed.

### Library

| Field       | Type                  | Notes                                        |
|-------------|-----------------------|----------------------------------------------|
| name        | CharField             | Optional — many libraries don't have a name  |
| slug        | SlugField             | Auto-generated from city + street, see below |
| description | TextField             | Optional                                     |
| photo       | ImageField            | Single photo for now                         |
| location    | PointField (PostGIS)  | SRID 4326                                    |
| address     | CharField             | Human-readable, displayed                    |
| city        | CharField             | For filtering/search                         |
| country     | CharField             | ISO code or choices                          |
| postal_code | CharField             | Optional                                     |
| status      | CharField (choices)   | pending / approved / rejected                |
| created_by  | FK to User            |                                              |
| created_at  | DateTimeField         |                                              |
| updated_at  | DateTimeField         |                                              |

**Slugs:** Auto-generated from city + street (e.g. `florence-via-rosina`). If the user
provides a name, include it. Duplicates get a numeric suffix (`florence-via-rosina-2`).
Users never see or manage slugs — they're purely for clean URLs and SEO.

**Future-proofing for multiple photos:** Start with a single `ImageField` on the model.
When ready, create a `LibraryPhoto` model (FK to Library, photo, ordering, is_primary)
and migrate the existing photo field into it.

### Report

| Field      | Type                | Notes                                          |
|------------|---------------------|-------------------------------------------------|
| library    | FK to Library       |                                                 |
| created_by | FK to User          |                                                 |
| reason     | CharField (choices) | damaged, missing, incorrect_info, inappropriate, other |
| details    | TextField           | Free-text explanation                           |
| photo      | ImageField          | Optional — to show damage, missing library, etc |
| status     | CharField (choices) | open / resolved / dismissed                     |
| created_at | DateTimeField       |                                                 |

---

## Authentication Strategy

**Goal:** Support both web sessions and mobile API clients without rearchitecting later.

### Phase 1 (MVP)
- **Web:** Django session-based auth (standard).
- **API:** JWT via `django-ninja-jwt` or similar. Access + refresh token pair.
- Use Django's built-in user model for both.
- Login and register endpoints in the API return JWT tokens.

### Phase 2 (current next step)
- **Google social auth (web):** Add `django-allauth` with Google provider.
  django-allauth integrates with Django's auth system, so existing sessions and
  JWT flows continue to work.

### Phase 3 (iOS app / later)
- **Sign in with Apple:** Add when the iOS app starts and Apple Developer Program
  enrollment is available.
- **Mobile social auth extension:** Keep backend auth additive so iOS can later use
  provider token exchange endpoints without reworking web auth.

### What this means in practice
- Build all auth around Django's User model from day one.
- The API always checks JWT; the web always checks session. Django Ninja supports
  multiple auth backends per endpoint, so a single view can accept either.
- Adding or extending OAuth providers later is an additive change — no rewrites needed.
- Some API endpoints (list, search, detail) require no auth at all.

---

## Pages & Routes

| Page              | URL (indicative)       | Auth?  | Notes                                           |
|-------------------|------------------------|--------|--------------------------------------------------|
| Homepage          | `/`                    | No     | Hero/explainer, latest entries grid               |
| About             | `/about/`              | No     | Project mission, contribution info, creator notes |
| Map               | `/map/`                | No     | Dedicated full map with all approved libraries    |
| Search            | `/search/`             | No     | Search form + results, HTMX live filtering       |
| Library detail    | `/library/<slug>/`     | No     | Photo, info, small map pin, report button        |
| Submit library    | `/submit/`             | Yes    | Photo upload, address with autocomplete, EXIF   |
| Login             | `/login/`              | No     |                                                  |
| Register          | `/register/`           | No     |                                                  |
| User dashboard    | `/dashboard/`          | Yes    | User's submissions and their statuses            |
| Moderation        | Django admin           | Staff  | Review pending entries, manage reports            |

### Homepage layout
- Navbar with links to Map, Search, Submit, Login/Register.
- Hero section: brief explanation of what the project is, how it works (leave a book,
  take a book), and a call to action (explore the map, submit a library).
- Latest entries: a card grid showing the most recent approved libraries (photo
  thumbnail, city, short description). Loaded via HTMX partial for easy refresh.

### Map page
- Dedicated page with a large Leaflet map taking most of the viewport.
- All approved libraries shown as markers. Clustered markers for dense areas
  (Leaflet.markercluster plugin).
- Click a marker → popup with library name/photo thumbnail + link to detail page.

### HTMX interactions
- Homepage "latest entries" list: loaded as a partial, can paginate without
  full page reload.
- Search results: form submits via HTMX, results replace a target div.
- Report form: modal or inline form, submitted via HTMX.

---

## REST API

Base path: `/api/v1/`

| Endpoint                    | Method | Auth? | Notes                                 |
|-----------------------------|--------|-------|---------------------------------------|
| `/auth/register`            | POST   | No    | Returns JWT tokens                    |
| `/auth/login`               | POST   | No    | Returns JWT tokens                    |
| `/auth/refresh`             | POST   | No    | Refresh token → new access token      |
| `/auth/me`                  | GET    | Yes   | Current user profile                  |
| `/libraries`                | GET    | No    | List + search (query params)          |
| `/libraries/latest`         | GET    | No    | Latest N approved entries             |
| `/libraries/{id}`           | GET    | No    | Detail                                |
| `/libraries`                | POST   | Yes   | Submit new (status=pending)           |
| `/libraries/{id}/report`    | POST   | Yes   | Report an issue                       |

### Search query params (on GET `/libraries`)
- `q` — free text (name, description) via PostgreSQL full-text search
- `city`, `country`, `postal_code` — filter fields
- `lat`, `lng`, `radius` — proximity search (PostGIS `ST_DWithin`)
- `page`, `page_size` — pagination

---

## Location Flow (Submit)

When a user submits a new library:

1. **Photo upload** — backend extracts EXIF GPS metadata (using `Pillow` or `exifread`).
2. **If GPS found** — reverse geocode via Nominatim to get a human-readable address.
   Pre-fill the address fields. User can review and correct.
3. **If no GPS** — user types an address with autocomplete suggestions (Photon API,
   debounced via HTMX or minimal JS).
4. **Address → coordinates** — forward geocode via Nominatim on submit, store as PostGIS point.
5. **Always store both** the PointField and the human-readable address fields.

---

## UI Approach

### TailwindCSS + daisyUI — no mockups needed

daisyUI provides themed, ready-made components (buttons, cards, modals, navbars, forms)
on top of Tailwind. No custom JS needed. Pages will be built directly in code using
daisyUI components as building blocks. Iterate by feedback: "move this here, make that
bigger, remove this section." Themes can be swapped with a single attribute later.

### Minimal JS policy
JavaScript only for:
- Leaflet map initialization and interaction
- EXIF extraction on client-side as a convenience (backend is the source of truth)
- Address autocomplete (Photon API, debounced fetch — or achievable with HTMX)
- Any small glue code HTMX can't handle

Everything else is server-rendered Django templates + HTMX.

---

## Implementation Phases

### Phase 1 — Project Skeleton & Local Dev

#### 1.1 — Django project init + custom user model
- [x] Create Django project (`startproject`) and a `users` app
- [x] Define custom User model (AbstractUser, email/username/password)
- [x] Set `AUTH_USER_MODEL` in settings
- [x] Create initial migration for users app
- [x] Verify `createsuperuser` works

#### 1.2 — Docker setup
- [x] Write Dockerfile for the Django app (Python, uv for dependency management, gunicorn)
- [x] Write `docker-compose.yml` with `app` + `db` (PostGIS image) services
- [x] Add volume for PostgreSQL data persistence
- [x] Configure Django settings to read DB connection from env vars
- [x] Verify `docker compose up` starts both services and Django connects to PostGIS

#### 1.3 — Library and Report models
- [x] Create `libraries` app
- [x] Define Library model with all fields from the data model table
- [x] Implement slug auto-generation logic (city + address, optional name, numeric suffix for dupes)
- [x] Define Report model with all fields from the data model table
- [x] Create and run migrations
- [x] Add a basic test that creates a Library and a Report in the DB

#### 1.4 — Django admin for moderation
- [x] Register Library in admin with list_display (name, city, status, created_at)
- [x] Add list_filter on status, city, country
- [x] Add admin actions: approve, reject (bulk status change)
- [x] Register Report in admin with list_display (library, reason, status, created_at)
- [x] Add admin action: resolve, dismiss reports
- [x] Verify the admin workflow: create library → set pending → approve/reject

#### 1.5 — Django Ninja API scaffold + JWT auth
- [x] Install `django-ninja` and `django-ninja-jwt`
- [x] Create API router at `/api/v1/`
- [x] Add `/auth/register` endpoint (create user, return JWT pair)
- [x] Add `/auth/login` endpoint (validate credentials, return JWT pair)
- [x] Add `/auth/refresh` endpoint (refresh token → new access token)
- [x] Add `/auth/me` endpoint (returns current user info, requires JWT)
- [x] Add a basic test for each auth endpoint

#### 1.6 — TailwindCSS + daisyUI integration
- [x] Add Node/npm tooling: `package.json` with tailwindcss, daisyui, postcss, autoprefixer
- [x] Configure Tailwind content scanning for Django templates (`tailwind.config.js` in v3; `@source` in v4)
- [x] Set up input CSS file with Tailwind directives and daisyUI plugin
- [x] Add npm script to build CSS and a watch mode for development
- [x] Add a Tailwind CSS watcher service to `docker-compose.yml` (or document the dev command)
- [x] Configure Django `STATICFILES_DIRS` to include the compiled CSS output
- [x] Verify: a template using daisyUI classes renders correctly

#### 1.7 — Base template + navbar/footer
- [x] Create `base.html` template: HTML boilerplate, meta tags, CSS/JS includes
- [x] Add responsive navbar using daisyUI (links: Home, Map, Search, Submit, Login/Register)
- [x] Add simple footer (project name, credits)
- [x] Add HTMX script include in base template
- [x] Create a placeholder homepage view and URL to verify the base template renders

#### 1.8 — CI with GitHub Actions
- [x] Create `.github/workflows/ci.yml`
- [x] Set up a job that runs on push and pull requests
- [x] Use a PostGIS service container for the test database
- [x] Install Python dependencies with uv and run `nox -s tests`
- [x] Build Tailwind CSS as part of the pipeline (so you can test it still builds properly)
- [x] Verify the pipeline passes on the current codebase

---

### Phase 2 — Core Web Pages

#### 2.1 — Registration and login pages
- [x] Create registration page with form (username, email, password, confirm password)
- [x] Wire up Django's `UserCreationForm` (or custom form for the custom user model)
- [x] Create login page with form (username/email, password)
- [x] Wire up Django's `AuthenticationForm` and `login()` view
- [x] Add logout view (POST-only, redirect to homepage)
- [x] Update navbar: show Login/Register when anonymous, show username + Logout when authenticated
- [x] Add a test for registration and login flows

#### 2.2 — Homepage: hero section + latest entries
- [x] Create homepage view that fetches latest N approved libraries
- [x] Build hero section in template: heading, short description, CTA buttons (Explore Map, Submit)
- [x] Build latest-entries card grid: each card shows photo thumbnail, name/city, short description
- [x] Extract the card grid into an HTMX partial template (`_latest_entries.html`)
- [x] Add HTMX loading for the card grid (hx-get, hx-target, hx-trigger="load")
- [x] Add "Load more" / pagination on the partial if more entries exist

#### 2.3 — Library detail page
- [x] Create detail view that fetches a library by slug
- [x] Build detail template: photo (full size), name, description, address, city/country
- [x] Embed a small map (OpenStreetMap, tell me if/how I need to get the API key) on the page to show the location
- [x] Add a "Report" button (links to report form, visible to authenticated users)
- [x] Handle 404 for non-existent slugs
- [x] You must be able to click a library in the home and reach the detail page
- [x] Add a test for the detail view (approved library visible, pending library returns 404)

#### 2.4 — Submit library form: basic photo upload + address fields
- [x] Create submit view (login required) with a form
- [x] Form fields: photo (file upload), name (optional), description (optional), address, city, country, postal_code
- [x] Submission page should have an embedded map, once the user types the Country, the City and the Street, the map should
    be centered to that street and the user should be able to move the pointer to the exact location. That location
    should be used for the coordinates
- [x] Check if it's possible to have a selector for the countries with a list of countries ordered alphabetcally
    and the possibility to type and search
- [x] Handle file upload: save photo via ImageField
- [x] Set status=pending and created_by=current user on save
- [x] Redirect to a confirmation page after successful submission
- [x] Add a test for submission (authenticated user creates a pending library)

#### 2.5 — Submit form: EXIF GPS extraction
- [x] Add utility function: extract GPS coordinates from an uploaded image using Pillow/exifread
- [x] On photo upload, attempt EXIF extraction on the backend
- [x] If GPS found: reverse geocode via Nominatim (geopy) to get address, city, country, postal_code
- [x] Pre-fill address fields with the geocoded result (return as JSON or render pre-filled form)
- [x] Add a test with a sample geotagged image

#### 2.6 — Submit form: Photon address autocomplete
- [x] Add address autocomplete input using Photon API (photon.komoot.io)
- [x] Implement as HTMX partial or minimal JS: debounced fetch on keystrokes, render suggestion list
- [x] On suggestion select: populate address, city, country, postal_code fields
- [x] Set coordinates before submit from autocomplete/map interactions so users can refine the marker
- [x] Store both the PointField and human-readable address fields

#### 2.7 — User dashboard
- [x] Create dashboard view (login required) listing the current user's submissions
- [x] Show each library: photo thumbnail, name/address, status badge (pending/approved/rejected)
- [x] Link each entry to its detail page
- [x] Add a test for the dashboard view

#### 2.8 — Search page
- [x] Create search view with a form: text query (q), city, country, postal_code filters
- [x] Implement PostgreSQL full-text search on Library name + description (SearchVector/SearchQuery)
- [x] Filter by city/country/postal_code when provided
- [x] Render results as a card grid (reuse the library card partial)
- [x] Wire up HTMX: form submits via hx-get, results replace a target div
- [x] Add a test for search (text match, filter match, no results)

---

### Phase 3 — Map Page

#### 3.1 — Full map page with markers
- [x] Create a map page template and use OpenStreetMap (if API key is needed, tell me how to get it)
- [x] Create `/map/` view that returns the map page template
- [x] Add Leaflet CSS/JS to the template (CDN or local static)
- [x] Initialize Leaflet map centered on a sensible default (e.g. Europe or user's region)
- [x] Create a JSON endpoint (or Django Ninja endpoint) returning approved libraries as GeoJSON (id, name, lat, lng, photo thumbnail URL)
- [x] Fetch the GeoJSON on page load and add markers to the map

#### 3.2 — Marker clustering
- [x] Add Leaflet.markercluster plugin (CSS + JS)
- [x] Wrap markers in a MarkerClusterGroup
- [x] Verify clustering works with a batch of test data points

#### 3.3 — Marker popups
- [x] On marker click: show a popup with library name, photo thumbnail, city
- [x] Add a "View details" link in the popup pointing to the detail page (`/library/<slug>/`)

#### 3.4 — Small map on detail page
- [x] Add a small Leaflet map on the library detail page
- [x] Show a single pin at the library's coordinates
- [x] Set appropriate zoom level for a neighborhood view

### Phase 3.5 — Google Auth (Web-first)

Goal for this phase:
- [ ] Add Google login for the web app now, without introducing Apple login yet.
- [ ] Keep the existing username/email + password flow unchanged.
- [ ] Implement this in a way that can be extended for iOS later (additive, no rewrite).

#### 3.5.1 — Auth policy and constraints (before coding)
- [x] Confirm account policy: username unique + email unique (case-insensitive)
- [x] Keep login with username OR email + password for local auth
- [x] Keep email required for local registration
- [x] Define linking rule: same verified Google email must map to one existing local user
- [x] Define fallback behavior if provider email is missing or not verified

#### 3.5.2 — User data hardening for unique email
- [x] Audit existing users for duplicate emails ignoring case
- [x] Normalize stored emails (trim + lowercase) via migration before constraint
- [x] Add database-level unique constraint for email (case-insensitive strategy)
- [x] Update forms/validation so duplicate emails fail with a clear error message
- [x] Add migration tests for normalization + uniqueness behavior

#### 3.5.3 — Google Cloud setup (local first)
- [x] Create Google Cloud project (free tier, no billing required for basic sign-in)
- [x] Configure OAuth consent screen (External + Testing mode)
- [x] Create OAuth client type: Web application
- [x] Add local redirect URI: `http://localhost:8000/accounts/google/login/callback/`
- [x] Add local authorized origin: `http://localhost:8000`
- [x] Document production URI to add later: `https://<domain>/accounts/google/login/callback/`

#### 3.5.4 — Django allauth integration (Google only)
- [x] Install `django-allauth`
- [x] Enable required apps in settings:
  - [x] `django.contrib.sites`
  - [x] `allauth`
  - [x] `allauth.account`
  - [x] `allauth.socialaccount`
  - [x] `allauth.socialaccount.providers.google`
- [x] Configure authentication backends to include model backend + allauth backend
- [x] Add and configure `SITE_ID`
- [x] Add allauth account settings (email, login, signup behavior aligned with current UX)
- [x] Add SocialApp/provider config via admin or settings-backed env values

#### 3.5.5 — URL routing + web login UX
- [x] Include allauth URLs under `/accounts/`
- [x] Keep `/login/` and `/register/` as the main entry pages
- [x] Add "Continue with Google" button to login template
- [x] Add "Continue with Google" button to register template
- [x] Preserve safe `next` redirect behavior after social login
- [x] Keep logout as POST-only and unchanged
- [x] Hide Google button when OAuth env vars are not configured

#### 3.5.6 — Social account linking behavior
- [x] If Google verified email matches existing user email: attach social account, do not create new user
- [x] If no matching user exists: create user and generate unique username
- [x] Prevent duplicate users when the same Google account logs in repeatedly
- [x] Handle conflict edge cases deterministically (race conditions / simultaneous signups)
- [x] Ensure future profile fields remain independent from auth method

#### 3.5.7 — Environment variables and secrets flow
- [x] Add placeholders to `.env.example`:
  - [x] `GOOGLE_OAUTH_CLIENT_ID`
  - [x] `GOOGLE_OAUTH_CLIENT_SECRET`
- [x] Local dev: set values in `.env` / `.envrc`
- [ ] Production: configure Dokku env vars with `dokku config:set` (or GitHub Actions automation)
- [x] Keep code identical across environments; only env values and OAuth console URIs differ
- [x] Add OAuth setup docs (origins, redirect URIs) to `.env.example`

#### 3.5.8 — Test coverage
- [x] Keep current auth tests green (register/login/logout/navbar behavior)
- [x] Add tests for email uniqueness (including case-insensitive duplicates)
- [x] Add tests for Google-first signup (new user created)
- [x] Add tests for Google login linking to an existing local account by email
- [x] Add tests for provider denial/cancel callback handling
- [x] Add tests for invalid callback/state mismatch handling
- [x] Add tests for Google button visibility (enabled/disabled states)
- [x] Add tests for custom adapters (signup permission, email normalization)
- [x] Add tests for context processor

#### 3.5.9 — Manual QA (required before moving to API phase)
- [x] Local smoke test: login with Google from `http://localhost:8000/` end-to-end
- [x] Verify session is created and navbar switches to authenticated state
- [x] Verify logout works and returns to anonymous navbar state
- [x] Verify existing local account with same email is reused (no duplicate row)
- [x] Verify normal username/email + password flow still works exactly as before

#### 3.5.10 — Documentation updates
- [x] Add setup guide for Google OAuth in local development
- [x] Document common OAuth errors and fixes (`redirect_uri_mismatch`, host mismatch, wrong callback path)
- [x] Document how to add production redirect URI when the domain is ready
- [x] Document secret management strategy for GitHub Actions + Dokku

#### 3.5.11 — Definition of done
- [x] Google login works locally for web users
- [x] Existing auth flow remains backward-compatible
- [x] Email uniqueness is enforced safely at DB and form level
- [x] No Apple login work is introduced in this phase
- [x] The project is ready to add iOS support later as an extension, not a rewrite

---

### Phase 4 — API Completion

**Auth model note:** The API uses JWT tokens but the **same Django User model** as the web app.
A user who registers on the web can call `/api/v1/auth/login` with the same credentials to get
a JWT token, and vice versa. Google OAuth users work too — they have a regular Django user
under the hood. When the iOS app comes, it will use the same auth endpoints and the same
accounts. There is no separate "API user" concept.

**Restructuring rationale:** Cross-cutting concerns (error handling, pagination, rate limiting)
are built first (4.1) so every endpoint is consistent from the start. Read-only endpoints are
grouped together (4.2–4.3), then write endpoints (4.4–4.5). Search logic is extracted into a
shared module so views and API reuse the same code. Library detail uses `{slug}` (not `{id}`)
to match the existing web URL convention. Developer documentation gets its own substantial
step (4.7) as a full portal at `developers.bookcorners.org`.

#### 4.1 — API infrastructure: errors, pagination, rate limiting

Establish shared foundations so all subsequent endpoints are consistent from the start.

- [x] Create `libraries/api_schemas.py` with Pydantic schemas:
  - `LibraryOut` — id, slug, name, description, photo_url, thumbnail_url, lat, lng, address,
    city, country, postal_code, created_at
  - `LibraryListOut` — list of `LibraryOut` + pagination metadata
  - `LibrarySearchParams` — q, city, country, postal_code, lat, lng, radius_km, page, page_size
  - `LibrarySubmitIn` — name, description, address, city, country, postal_code, latitude, longitude
  - `ReportIn` — reason (enum), details
  - `ReportOut` — confirmation after creation
  - `PaginationMeta` — page, page_size, total, total_pages, has_next, has_previous
- [x] Create `libraries/api_pagination.py`:
  - `paginate_queryset(qs, page, page_size, max_page_size=50)` → (items, PaginationMeta)
  - Reuse clamping pattern from `_parse_page_number()` in `libraries/views.py`
- [x] Create `libraries/api_security.py` — generalize `users/security.py` pattern for public endpoints:
  - Settings: `API_RATE_LIMIT_READ_REQUESTS` (default 60/window), `API_RATE_LIMIT_WRITE_REQUESTS` (default 10/window)
- [x] Modify `config/api.py` — add exception handlers (400/404/422/500) producing
  `{"message": "...", "details": {...}}`, extending existing `ErrorOut` from `users/api.py`
- [x] Add settings to `config/settings.py` for public API rate limits
- [x] Add tests: `libraries/test_api_pagination.py`, `libraries/test_api_errors.py`

#### 4.2 — Library list + detail endpoints (public, read-only)

- [ ] Create `libraries/api.py` with `library_router = Router(tags=["libraries"])`
- [ ] `GET /api/v1/libraries` — paginated list of approved libraries using `LibraryListOut`
- [ ] `GET /api/v1/libraries/{slug}` — single library detail, 404 for non-approved
  (except: owner can see their own pending entry via JWT)
- [ ] Register router in `config/api.py`: `api.add_router("/libraries/", library_router)`
- [ ] Reuse queryset from `_get_latest_entries_page()` and visibility logic from
  `_get_detail_visible_library()` in `libraries/views.py`
- [ ] Add tests in `libraries/test_api_libraries.py`:
  list pagination, status filtering, detail by slug, 404s, owner visibility of pending

#### 4.3 — Search, filtering, proximity + latest endpoint

- [ ] Create `libraries/search.py` — extract search logic into framework-agnostic functions:
  - `apply_text_search(qs, q)` — from `_apply_text_search()` in `libraries/views.py`
  - `run_library_search(q, city, country, postal_code, lat, lng, radius_km)` — from
    `_run_library_search()` in `libraries/views.py`
- [ ] Refactor `libraries/views.py` to import from `libraries/search.py` (no duplication)
- [ ] Extend `GET /api/v1/libraries` to accept `LibrarySearchParams` query parameters:
  `q` (full-text), `city`, `country`, `postal_code`, `lat`, `lng`, `radius_km`
- [ ] Add `GET /api/v1/libraries/latest` — most recent approved (default 10, max 50, query param `limit`)
- [ ] Add tests: text search, field filters, proximity (mocked geocoding), combined filters,
  latest with limit clamping

#### 4.4 — Submit library endpoint (authenticated, write)

- [ ] `POST /api/v1/libraries` — multipart with photo + fields, JWT required
- [ ] Response: `{201: LibraryOut, 400: ErrorOut, 401: ErrorOut, 413: ErrorOut, 429: ErrorOut}`
- [ ] Reuse photo validation from `_validate_uploaded_photo()` in `libraries/forms.py`
- [ ] Reuse lat/lng validation from `LibrarySubmissionForm` in `libraries/forms.py`
- [ ] `Library.save()` handles slug generation + photo optimization automatically
- [ ] Set status=pending, created_by from JWT user
- [ ] Rate limited with write-tier limits
- [ ] Add tests: auth required, valid submission, photo validation, lat/lng range checks

#### 4.5 — Report endpoint (authenticated, write)

- [ ] `POST /api/v1/libraries/{slug}/report` — reason (enum), details, optional photo. JWT required
- [ ] Response: `{201: ReportOut, 400: ErrorOut, 401: ErrorOut, 404: ErrorOut, 429: ErrorOut}`
- [ ] Reuse `Report.Reason` choices from `libraries/models.py`
- [ ] Validate target library exists and is approved
- [ ] Rate limited with write-tier limits
- [ ] Add tests: auth required, valid report, 404 for missing library, reason validation

#### 4.6 — OpenAPI schema polish + export command

Ensure the auto-generated schema is documentation-quality, then create a management command
to export it for the docs pipeline.

- [ ] Enhance `NinjaAPI()` in `config/api.py`: add `description`, `version="1.0.0"`, `servers` list
- [ ] Add `Field(description=..., examples=[...])` to all schema fields in `libraries/api_schemas.py`
- [ ] Add descriptions to auth schemas and endpoints in `users/api.py`
- [ ] Add `summary`/`description` to each endpoint decorator in `libraries/api.py`
- [ ] Create `libraries/management/commands/export_openapi_schema.py`:
  `python manage.py export_openapi_schema > docs/openapi.json`
- [ ] Verify: `/api/v1/docs` shows descriptions, examples, proper response schemas

#### 4.7 — Developer documentation portal (developers.bookcorners.org)

**Tooling:** MkDocs Material + OpenAPI rendering plugin. Python-native (matches project),
supports prose docs alongside auto-generated API reference, builds to static HTML for
GitHub Pages. Better than Redoc alone (no prose docs) or Docusaurus (Node-based, heavier).

**Content structure:**
```
docs/
  index.md                     # Welcome, what is Book Corners API
  getting-started.md           # Register → get token → first request (curl + Python examples)
  authentication.md            # JWT flow, token lifecycle, refresh, Bearer header usage
  libraries/
    list-and-search.md         # GET /libraries with query params, curl + Python examples
    detail.md                  # GET /libraries/{slug}
    submit.md                  # POST /libraries (multipart photo upload examples)
    report.md                  # POST /libraries/{slug}/report
  reference/
    openapi.md                 # Embedded Swagger/Redoc rendering from openapi.json
  rate-limiting.md             # Rate limit policy, Retry-After, error handling
  errors.md                    # Error response format, common status codes
  changelog.md                 # API changelog
```

- [ ] Create `docs/` directory with all markdown content above
- [ ] Create `mkdocs.yml` — MkDocs Material config (site_name, theme with teal primary,
  nav structure, search + render_swagger plugins)
- [ ] Create `requirements-docs.txt` — `mkdocs-material`, `mkdocs-render-swagger-plugin`
- [ ] Write prose docs with curl + Python code examples for every endpoint
- [ ] Each page covers: endpoint URL/method, auth requirements, parameters, code examples,
  response examples, error scenarios

**CI/CD pipeline:**

- [ ] Create `.github/workflows/docs.yml`:
  - Triggers on push to master when `docs/`, `mkdocs.yml`, or API code files change
  - Spins up PostGIS service (needed for Django to load and export schema)
  - Runs `python manage.py export_openapi_schema > docs/openapi.json`
  - Builds with `mkdocs build --strict`
  - Deploys to `gh-pages` branch via `peaceiris/actions-gh-pages` with
    `cname: developers.bookcorners.org`
- [ ] Enable GitHub Pages in repo settings (source: `gh-pages` branch)
- [ ] Add custom domain `developers.bookcorners.org` in repo Settings → Pages

**DNS + GitHub Pages setup:**

- [ ] In the `book-corners` repo Settings → Pages, set source to `gh-pages` branch
- [ ] In the `book-corners` repo Settings → Pages, set custom domain to `developers.bookcorners.org`
  (GitHub associates this domain with the book-corners repo specifically — it routes by
  matching the incoming Host header to the repo that claims that custom domain)
- [ ] In Cloudflare (bookcorners.org zone): add CNAME record
  `developers` → `andreagrandi.github.io` (DNS only, grey cloud)
  Note: this is the same target as all GitHub Pages under the `andreagrandi` account.
  GitHub disambiguates by custom domain, not by CNAME target. This does not conflict
  with the personal site because each repo registers its own custom domain.
- [ ] GitHub Pages handles TLS via its own Let's Encrypt integration

**Freshness guarantee:** The CI workflow re-exports the OpenAPI schema from the live Django
app on every relevant push, so API changes automatically propagate to docs. No manual step.

- [ ] Verify: `mkdocs serve` locally previews the docs site
- [ ] Verify: push to master triggers docs CI → `developers.bookcorners.org` is live

#### 4.8 — Integration verification

- [ ] Add schema validation: `openapi-spec-validator` check in CI or nox session
- [ ] Create `libraries/test_api_integration.py` — full workflow:
  register → login → submit library → list → search → detail → report
- [ ] Verify pagination consistency across pages (no duplicates, correct totals)
- [ ] Verify error response consistency across all endpoints
- [ ] Update `CLAUDE.md` with new commands:
  `python manage.py export_openapi_schema`, `mkdocs serve`

---

### Phase 5 — Polish & Hardening

#### 5.1 — Photo optimization
- [x] On upload: resize photo to a max dimension (e.g. 1600px wide)
- [x] Compress JPEG quality (e.g. 85%)
- [x] Generate a thumbnail version (e.g. 400px wide) for card grids and popups
- [x] Store both original (resized) and thumbnail
- [x] Add a test that verifies resizing/compression

#### 5.2 — Report flow polish
- [x] Build report form as an HTMX inline form or modal on the detail page
- [x] Submit via HTMX, show success/error message without page reload
- [x] Ensure reports are visible and manageable in Django admin
- [x] Add a test for the full report submission flow

#### 5.3 — SEO basics
- [x] Add meta title and description to all pages (base template + per-page overrides)
- [x] Add Open Graph tags for library detail pages (photo, title, description)
- [x] Generate a `sitemap.xml` using Django's sitemap framework
- [x] Add `robots.txt`

#### 5.4 — Error pages
- [x] Create custom 404 template (friendly message, link to homepage/map)
- [x] Create custom 500 template (friendly message, apology)
- [x] Wire up Django's handler404 and handler500

#### 5.5 — Security review
- [x] Verify CSRF protection is active on all forms
- [x] Verify file upload validation (allowed types, max size)
- [x] Review rate limiting on auth endpoints (prevent brute force)
- [x] Review input validation on all user-facing fields
- [x] Run Django's `check --deploy` and address warnings
- [x] Verify `DEBUG=False` and secret key management for production

#### 5.6 — First public deploy gate (single environment)
- [ ] Confirm there is no staging environment and production is public from first deploy
- [ ] Complete 5.4 (error pages) and 5.5 (security review) before DNS cutover
- [ ] Test core flows locally with production-like settings (`DEBUG=False`): home, map, submit, login, admin
- [ ] Decide email policy for MVP: keep welcome/reset email flows disabled until provider is configured
- [ ] Create a one-page incident response note (who gets alerted, rollback steps, backup restore reference)

---

### Phase 6 — Deployment

Prerequisites: VPS accessible via `ssh root@bookcorners.org`, domain `bookcorners.org` registered.

#### 6.1 — Create deploy user

Dokku and day-to-day operations should run as a non-root user. Firewall is managed
externally via Hetzner.

- [x] Create a non-root deploy user with sudo access
  ```bash
  ssh root@vps.bookcorners.org           # use vps. subdomain (DNS-only, not proxied)
  adduser deploy                         # set a strong password (needed for sudo)
  usermod -aG sudo deploy
  ```
- [x] Copy the authorized SSH key to the deploy user
  ```bash
  # Still on the VPS as root:
  mkdir -p /home/deploy/.ssh
  cp /root/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys
  chown -R deploy:deploy /home/deploy/.ssh
  chmod 700 /home/deploy/.ssh
  chmod 600 /home/deploy/.ssh/authorized_keys
  ```
- [x] Verify key-based login works for the deploy user
  ```bash
  # From your Mac (new terminal — use vps. subdomain since main domain is Cloudflare-proxied):
  ssh deploy@vps.bookcorners.org
  ```

#### 6.2 — DNS setup (Cloudflare)

DNS is managed via Cloudflare. Three A records, all pointing to the VPS IP.

- [x] Create three A records in Cloudflare DNS:
  - `bookcorners.org` → `<VPS_IP>` — **Proxied** (orange cloud)
  - `www.bookcorners.org` → `<VPS_IP>` — **Proxied** (orange cloud)
  - `vps.bookcorners.org` → `<VPS_IP>` — **DNS only** (grey cloud, for SSH access)
- [x] Leave Cloudflare SSL/TLS mode at default for now. It will be set to
  "Full (Strict)" after Let's Encrypt is configured in step 6.9.
- [x] Verify the DNS-only record works for SSH:
  ```bash
  ssh deploy@vps.bookcorners.org
  ```

#### 6.3 — Install Dokku on the VPS

Dokku requires Ubuntu 22.04 or 24.04. All commands run on the VPS.

- [x] SSH into the VPS and install Dokku
  ```bash
  ssh deploy@vps.bookcorners.org

  # Install Dokku (check https://dokku.com/docs/getting-started/installation/ for latest version)
  wget -NP . https://dokku.com/install/v0.36.6/bootstrap.sh
  sudo DOKKU_TAG=v0.36.6 bash bootstrap.sh
  ```
  This takes 5-10 minutes. It installs Dokku, nginx (as reverse proxy), and Docker.
- [x] Set the global domain so Dokku knows your hostname
  ```bash
  sudo dokku domains:set-global bookcorners.org
  ```
- [x] Add your SSH public key to Dokku (so you can `git push` to it)
  ```bash
  # On the VPS — use the same key you use for SSH:
  cat ~/.ssh/authorized_keys | sudo dokku ssh-keys:add admin
  ```
- [x] Verify Dokku is running:
  ```bash
  sudo dokku version
  ```

#### 6.4 — Create the Dokku app and prepare the repository

- [x] Create the app on the VPS
  ```bash
  # On the VPS:
  sudo dokku apps:create book-corners
  ```
- [x] Create a `Procfile` in the project root (on your Mac, in the repo).
  Dokku reads this to know how to run the app.
  ```
  web: gunicorn config.wsgi:application --bind 0.0.0.0:$PORT
  ```
  Note: Dokku sets `$PORT` automatically. Do not hardcode 8000.
- [x] Create `app.json` in the project root for pre-deploy hooks (migrations).
  Dokku runs these automatically on every deploy.
  ```json
  {
    "scripts": {
      "dokku": {
        "predeploy": "python manage.py migrate --noinput"
      }
    }
  }
  ```
- [x] Add the Dokku remote to your local git repo
  ```bash
  # From your Mac, in the book-corners directory:
  git remote add dokku dokku@vps.bookcorners.org:book-corners
  ```
- [x] Commit the Procfile and app.json (do not deploy yet — database and storage aren't ready)

#### 6.5 — Database (PostGIS)

Dokku uses plugins for services. The standard `dokku-postgres` plugin supports PostGIS
via image configuration. All commands run on the VPS.

Note: The official `postgis/postgis` image is amd64-only. Since the VPS is ARM64
(Hetzner Ampere), use `imresamu/postgis` which provides multi-arch builds (amd64 + arm64).

- [x] Install the Dokku Postgres plugin
  ```bash
  sudo dokku plugin:install https://github.com/dokku/dokku-postgres.git postgres
  ```
- [x] Create the database service using the PostGIS image and link it to the app
  ```bash
  sudo POSTGRES_IMAGE="imresamu/postgis" POSTGRES_IMAGE_VERSION="17-3.6-bookworm" dokku postgres:create book-corners-db
  sudo dokku postgres:link book-corners-db book-corners
  ```
  This automatically sets `DATABASE_URL` as an env var on the app. Verify:
  ```bash
  sudo dokku config:show book-corners | grep DATABASE_URL
  ```
  The URL should start with `postgres://`. Django's `dj-database-url` handles this,
  but the PostGIS engine needs to be set. See env vars step below.

#### 6.6 — Persistent storage for media uploads

Dokku containers are ephemeral — files written inside them are lost on redeploy.
Media uploads must be stored on a mounted host directory.

- [x] Create the host directory and set permissions
  ```bash
  # On the VPS:
  sudo mkdir -p /var/lib/dokku/data/storage/book-corners/media
  sudo chown -R 32767:32767 /var/lib/dokku/data/storage/book-corners/media
  ```
  (32767 is the default `herokuishuser` UID inside Dokku containers)
- [x] Mount it into the app container
  ```bash
  sudo dokku storage:mount book-corners /var/lib/dokku/data/storage/book-corners/media:/app/media
  ```
- [x] Verify:
  ```bash
  sudo dokku storage:report book-corners
  ```
  Django's `MEDIA_ROOT = BASE_DIR / "media"` resolves to `/app/media` inside the
  container, which now maps to persistent host storage. No settings change needed.

#### 6.7 — Environment variables

Set all production environment variables on the VPS. Do this before the first deploy.

- [x] Generate a secure SECRET_KEY
  ```bash
  python3 -c "import secrets; print(secrets.token_urlsafe(50))"
  ```
- [x] Set all required env vars in one command (to avoid multiple restarts)
  ```bash
  sudo dokku config:set --no-restart book-corners \
    DJANGO_SECRET_KEY="<generated-secret-key>" \
    DJANGO_DEBUG="false" \
    DJANGO_ALLOWED_HOSTS="bookcorners.org,www.bookcorners.org" \
    DJANGO_CSRF_TRUSTED_ORIGINS="https://bookcorners.org,https://www.bookcorners.org" \
    DJANGO_SECURE_SSL_REDIRECT="true" \
    DJANGO_SESSION_COOKIE_SECURE="true" \
    DJANGO_CSRF_COOKIE_SECURE="true" \
    DJANGO_SECURE_HSTS_SECONDS="31536000" \
    NOMINATIM_USER_AGENT="bookcorners.org/1.0"
  ```
  Note: `--no-restart` prevents Dokku from trying to restart the app before the first
  deploy. The vars will take effect on the next deploy/restart.
- [x] `CSRF_TRUSTED_ORIGINS` — already in `config/settings.py` (line 64)
- [x] Optionally set Google OAuth vars (can also be added later):
  ```bash
  sudo dokku config:set --no-restart book-corners \
    GOOGLE_OAUTH_CLIENT_ID="<your-client-id>" \
    GOOGLE_OAUTH_CLIENT_SECRET="<your-client-secret>"
  ```
  Remember to add the production redirect URI in Google Cloud Console:
  - Authorized origin: `https://bookcorners.org`
  - Redirect URI: `https://bookcorners.org/accounts/google/login/callback/`
- [x] Database engine set to `postgis` — already in `config/settings.py` (line 162)

#### 6.8 — Domain + SSL

DNS should have propagated by now (from step 6.2).

- [x] Set the app domain on Dokku
  ```bash
  # On the VPS:
  sudo dokku domains:set book-corners bookcorners.org www.bookcorners.org
  ```
- [x] Install the Let's Encrypt plugin
  ```bash
  sudo dokku plugin:install https://github.com/dokku/dokku-letsencrypt.git
  ```
- [x] Configure the email for Let's Encrypt notifications
  ```bash
  sudo dokku letsencrypt:set book-corners email your-email@example.com
  ```
- [x] SSL certificates will be generated after the first deploy (the app must be running
  for Let's Encrypt to verify the domain). Come back to this after step 6.9.

#### 6.9 — First deploy

This is the moment of truth. Everything above must be in place.

- [x] Pre-deploy checklist:
  - [x] `Procfile` committed
  - [x] `app.json` committed
  - [x] `CSRF_TRUSTED_ORIGINS` added to settings.py and committed
  - [x] Database engine set to `postgis` in settings.py and committed
  - [x] All env vars set on the VPS (`sudo dokku config:show book-corners`)
  - [x] DNS is configured (`ssh deploy@vps.bookcorners.org` works)
- [x] Push to Dokku from your Mac
  ```bash
  git push dokku master
  ```
  Dokku will:
  1. Detect the Dockerfile and build the image (multi-stage: CSS + Python)
  2. Run `collectstatic` (inside the Dockerfile)
  3. Run `python manage.py migrate --noinput` (from app.json predeploy)
  4. Start the container with the Procfile command
  5. Configure nginx to proxy to the container
- [x] Watch the build output for errors. Common issues:
  - PostGIS extension not available → check the database image used
  - `collectstatic` fails → check STATIC_ROOT and WhiteNoise config
  - Migration fails → check DATABASE_URL is linked
- [x] Verify the app is running
  ```bash
  # On the VPS:
  sudo dokku ps:report book-corners
  sudo dokku logs book-corners --tail
  ```
- [x] Test HTTP access (SSL not yet enabled):
  ```bash
  curl -I http://bookcorners.org
  ```
  Expected: `200 OK` (or `301` redirect if SSL redirect is on — temporarily set
  `DJANGO_SECURE_SSL_REDIRECT=false` if needed for this check)
- [x] Now enable SSL (requires the app to be running):
  ```bash
  sudo dokku letsencrypt:enable book-corners
  ```
- [x] Set up automatic certificate renewal (cron job):
  ```bash
  sudo dokku letsencrypt:cron-job --add
  ```
- [x] Verify HTTPS:
  ```bash
  curl -I https://bookcorners.org
  ```
  Expected: `200 OK`, valid TLS certificate
- [x] Re-enable SSL redirect if you disabled it:
  ```bash
  sudo dokku config:set book-corners DJANGO_SECURE_SSL_REDIRECT="true"
  ```
- [x] Now set Cloudflare SSL/TLS mode to **"Full (Strict)"**
  (Cloudflare dashboard → SSL/TLS → Overview). This ensures Cloudflare verifies
  the Let's Encrypt certificate on your origin server.

#### 6.10 — Post-deploy setup

These are one-time tasks after the first successful deploy.

- [x] Create a superuser for the Django admin
  ```bash
  # On the VPS:
  sudo dokku run book-corners python manage.py createsuperuser
  ```
- [x] Fix the Django Sites framework domain (allauth uses this).
  The default Site object has `example.com`. Update it:
  ```bash
  sudo dokku run book-corners python manage.py shell -c "
  from django.contrib.sites.models import Site
  site = Site.objects.get(id=1)
  site.domain = 'bookcorners.org'
  site.name = 'Book Corners'
  site.save()
  print(f'Site updated: {site.domain}')
  "
  ```
- [x] Smoke test all critical pages:
  - [x] Homepage: `https://bookcorners.org/`
  - [x] Static CSS loads: `https://bookcorners.org/static/css/app.css`
  - [x] Login page: `https://bookcorners.org/login/`
  - [x] Registration: `https://bookcorners.org/register/`
  - [x] Map page: `https://bookcorners.org/map/`
  - [x] Submit page (requires login): `https://bookcorners.org/submit/`
  - [x] Admin: `https://bookcorners.org/admin/`
  - [x] Sitemap: `https://bookcorners.org/sitemap.xml`
- [x] Verify Google OAuth works (if configured):
  - [x] "Continue with Google" button appears on login page
  - [x] Full OAuth flow completes and creates/links account

#### 6.11 — Continuous deployment (GitHub Actions)

Automate deploys so that every push to `master` that passes CI gets deployed.

- [x] Generate a dedicated SSH key pair for GitHub Actions (on your Mac):
  ```bash
  ssh-keygen -t ed25519 -f ~/.ssh/dokku_deploy -N "" -C "github-actions-deploy"
  ```
- [x] Add the public key to Dokku on the VPS:
  ```bash
  cat ~/.ssh/dokku_deploy.pub | ssh deploy@vps.bookcorners.org "sudo dokku ssh-keys:add github-actions"
  ```
- [x] Add the private key as a GitHub Actions secret:
  - Go to the repo → Settings → Secrets and variables → Actions
  - Add secret `DOKKU_SSH_PRIVATE_KEY` with the contents of `~/.ssh/dokku_deploy`
  - Add secret `DOKKU_HOST` with value `vps.bookcorners.org`
- [x] Add a deploy job to `.github/workflows/ci.yml` that runs after tests pass:
  ```yaml
  deploy:
    needs: tests
    if: github.ref == 'refs/heads/master' && github.event_name == 'push'
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Set up SSH
        run: |
          mkdir -p ~/.ssh
          echo "${{ secrets.DOKKU_SSH_PRIVATE_KEY }}" > ~/.ssh/dokku_deploy
          chmod 600 ~/.ssh/dokku_deploy
          ssh-keyscan -H ${{ secrets.DOKKU_HOST }} >> ~/.ssh/known_hosts

      - name: Deploy to Dokku
        run: |
          git remote add dokku dokku@${{ secrets.DOKKU_HOST }}:book-corners
          GIT_SSH_COMMAND="ssh -i ~/.ssh/dokku_deploy" git push dokku master
  ```
- [x] Test the pipeline: push a small commit to master and verify it deploys automatically
- [x] Verify full pipeline: push → CI tests pass → deploy → app is running with new code

#### 6.12 — Backups

Backups are essential before the site has any real user data. Set up nightly database
dumps and media backups. BorgBase is the planned offsite target.

- [ ] Install borg on the VPS
  ```bash
  sudo apt-get update && sudo apt-get install -y borgbackup
  ```
- [ ] Set up a BorgBase repository (https://www.borgbase.com/):
  - Ensure the VPS has an SSH key pair (borg needs it to authenticate with BorgBase):
    ```bash
    # Check if a key already exists
    ssh -t deploy@vps.bookcorners.org "ls -la ~/.ssh/id_*.pub"

    # If no key exists, generate one
    ssh -t deploy@vps.bookcorners.org "ssh-keygen -t ed25519 -N '' -C 'deploy@vps.bookcorners.org'"

    # Print the public key to copy into BorgBase
    ssh -t deploy@vps.bookcorners.org "cat ~/.ssh/id_ed25519.pub"
    ```
  - Create a BorgBase account and go to **Account → SSH Keys**
  - Paste the VPS public key and save it
  - Create a new repository in BorgBase
  - Note the repository URL (e.g., `ssh://xxxxx@xxxxx.repo.borgbase.com/./repo`)
- [ ] Initialize the borg repository from the VPS
  ```bash
  # Set a strong passphrase and save it securely
  export BORG_PASSPHRASE="<your-passphrase>"
  borg init --encryption=repokey ssh://xxxxx@xxxxx.repo.borgbase.com/./repo
  ```
- [ ] Version the backup and restore scripts in the repo under `scripts/`:
  - `scripts/backup.sh` — nightly backup script
  - `scripts/restore.sh` — restore script with selective restore support
  - See the actual script files in the repo for contents
- [ ] Deploy scripts to the VPS and make them executable:
  ```bash
  # Dokku stores the app source at /home/dokku/book-corners/ (as a bare git repo).
  # The repo is checked out on each deploy but is not a convenient working tree.
  #
  # For ops scripts, clone the repo to the deploy user's home instead:
  ssh -t deploy@vps.bookcorners.org "git clone https://github.com/andreagrandi/book-corners.git ~/book-corners"

  # To update scripts later after pushing changes to GitHub:
  ssh -t deploy@vps.bookcorners.org "cd ~/book-corners && git pull"

  # Symlink scripts to a convenient location:
  ssh -t deploy@vps.bookcorners.org "ln -sf ~/book-corners/scripts/backup.sh ~/backup.sh"
  ssh -t deploy@vps.bookcorners.org "ln -sf ~/book-corners/scripts/restore.sh ~/restore.sh"
  ```
- [ ] Test the backup script:
  ```bash
  ssh -t deploy@vps.bookcorners.org "sudo ~/backup.sh"
  ```
- [ ] Add a nightly cron job (e.g., 3 AM server time):
  ```bash
  sudo crontab -e
  # Add:
  0 3 * * * /home/deploy/backup.sh >> /var/log/book-corners-backup.log 2>&1
  ```
- [ ] Test backup restore (verify backups actually work):
  - **Database**: restore to a temporary Dokku Postgres service to avoid touching production:
    ```bash
    # Create a throwaway DB service
    sudo dokku postgres:create book-corners-db-test

    # Extract the dump from the latest archive
    mkdir -p /tmp/restore-test && cd /tmp/restore-test
    export BORG_PASSPHRASE="<your-passphrase>"
    borg extract "$BORG_REPO::<latest-archive>" tmp/book-corners-backup/db.dump

    # Import into the test service
    sudo dokku postgres:import book-corners-db-test < tmp/book-corners-backup/db.dump

    # Verify: connect and spot-check a table
    sudo dokku postgres:connect book-corners-db-test
    # In psql: SELECT count(*) FROM libraries_library; then \q

    # Tear down
    sudo dokku postgres:destroy book-corners-db-test --force
    rm -rf /tmp/restore-test
    ```
  - **Media**: restore a small subset to a temp folder (avoids filling disk):
    ```bash
    mkdir -p /tmp/restore-test && cd /tmp/restore-test
    export BORG_PASSPHRASE="<your-passphrase>"

    # List media paths inside the archive to pick a small subset
    borg list "$BORG_REPO::<latest-archive>" | grep media | head -20

    # Extract only a few files (e.g., one subfolder or date prefix)
    borg extract "$BORG_REPO::<latest-archive>" \
      var/lib/dokku/data/storage/book-corners/media/library_photos/2026 \
      --strip-components 7

    # Verify files are intact
    ls -la
    file *.jpg  # should report valid image files

    # Cleanup
    rm -rf /tmp/restore-test
    ```
  - Mark this step done only after both DB and media restores succeed
- [ ] Add a monthly restore-test reminder (calendar or cron that logs a warning):
  ```bash
  # Add to deploy user's crontab:
  0 9 1 * * echo "[REMINDER] Test backup restore for book-corners" | logger -t backup-reminder
  ```

#### 6.13 — Monitoring and alerting baseline

- [x] Set up uptime monitoring with UptimeRobot (free tier):
  - Monitor `https://bookcorners.org/health` (HTTP 200 check)
  - Set check interval to 5 minutes
  - Configure alert contact (email or Telegram)
- [ ] Set up error tracking with Sentry (free developer plan):
  - Create a Sentry project for Django
  - Install `sentry-sdk` and add to `requirements.txt`
  - Add Sentry DSN to Dokku env vars:
    ```bash
    sudo dokku config:set book-corners SENTRY_DSN="https://xxxxx@xxxxx.ingest.sentry.io/xxxxx"
    ```
  - Add Sentry initialization to `config/settings.py` (only when DSN is set)
  - Keep on free plan — events are dropped when quota is exhausted, not billed
- [ ] Add backup health monitoring:
  - Add a heartbeat check (e.g., healthchecks.io free tier) that the backup script
    pings on success. If no ping arrives by 6 AM, you get alerted.
  - Add to the end of `backup.sh`:
    ```bash
    curl -fsS --retry 3 https://hc-ping.com/<your-check-uuid> > /dev/null
    ```
- [ ] Run one test incident: temporarily break something (e.g., wrong ALLOWED_HOSTS),
  verify alerts fire, then fix it

#### 6.13b — VPS paths reference

Key paths on the VPS for quick reference:

| What | Path |
|------|------|
| Dokku app (bare git repo) | `/home/dokku/book-corners/` |
| App source checkout (for ops scripts) | `/home/deploy/book-corners/` |
| Backup/restore scripts (symlinks) | `/home/deploy/backup.sh`, `/home/deploy/restore.sh` |
| Media files (mounted into container) | `/var/lib/dokku/data/storage/book-corners/media/` |
| Postgres data (managed by Dokku plugin) | `/var/lib/dokku/services/postgres/book-corners-db/` |
| App logs | `sudo dokku logs book-corners --tail` |
| Backup log | `/var/log/book-corners-backup.log` |

To update ops scripts after a push to GitHub:
```bash
ssh -t deploy@vps.bookcorners.org "cd ~/book-corners && git pull"
```

#### 6.14 — Deploy runbook (reference document)

Write a brief runbook and keep it accessible (e.g., in a `docs/` directory or a wiki).
It should cover:

- [ ] **Manual deploy sequence** (when CI/CD is down or you need to deploy a hotfix):
  ```bash
  git push dokku master
  # Watch output for errors
  sudo dokku ps:report book-corners
  sudo dokku logs book-corners --tail
  ```
- [ ] **Rollback to previous release**:
  ```bash
  # List recent deploys:
  sudo dokku ps:report book-corners

  # Revert to the previous Docker image:
  sudo dokku ps:rebuild book-corners
  # Or for a specific commit: git push the old commit to dokku
  ```
- [ ] **Database restore** (from backup):
  ```bash
  borg extract "$BORG_REPO::<archive-name>"
  sudo dokku postgres:import book-corners-db < db.dump
  ```
- [ ] **Post-deploy smoke checklist**:
  - Homepage loads with styles
  - Static CSS returns 200
  - Login/register works
  - Map page loads with markers
  - Admin panel accessible
  - Submit flow works (for logged-in user)
- [ ] **Emergency contacts and escalation** — who gets alerted and how

---

## Cost Sheet (single public VPS)

Assumptions:
- One public Hetzner VPS, Dokku-managed services on same host, no staging.
- PostgreSQL/PostGIS, app, and reverse proxy run on the VPS.
- Cloudflare for domain registration and DNS.

### Baseline recurring costs

| Item | Required now? | Est. monthly | Est. yearly | Notes |
|------|----------------|--------------|-------------|-------|
| Hetzner VPS (small instance) | Yes | $5–8 | $60–96 | App + DB + Dokku on one server |
| Domain (Cloudflare Registrar) | Yes | $0.7–1.5 | $8–18 | Depends on TLD, billed yearly |
| SSL (Let's Encrypt) | Yes | $0 | $0 | Free certs |
| Dokku | Yes | $0 | $0 | Open source |
| PostgreSQL + PostGIS (self-hosted) | Yes | $0 | $0 | Included in VPS cost |
| BorgBase backup storage | Yes (recommended) | $0–8* | $0–96* | Reuse existing plan if possible |
| Uptime monitoring (UptimeRobot free tier) | Recommended | $0 | $0 | Paid tier optional later |
| Error tracking (Sentry developer plan) | Recommended | $0 | $0 | Quota-limited free usage |

\* If you already have BorgBase capacity, incremental cost can be near zero.

### Optional costs (only when needed)

| Item | Est. monthly | Notes |
|------|--------------|-------|
| Transactional email provider | $0–20 | Keep disabled for MVP, enable later for password reset/welcome mail |
| Paid uptime monitoring upgrade | $7–20+ | Only if free tier limits become restrictive |
| Paid error monitoring upgrade | $26+ | Only if free Sentry quotas are consistently exceeded |

### Practical budget ranges

- **Lean MVP baseline:** ~$6–10/month (VPS + domain amortized, free monitoring, existing backup capacity).
- **Safer baseline with paid backup headroom:** ~$10–18/month.
- **With optional paid monitoring/email upgrades:** ~$20–45+/month.

### Cost control rules

- Keep Sentry on free plan and do not enable paid add-ons by default.
- Use free-tier uptime monitoring until false positives or feature limits justify upgrades.
- Keep email features disabled until there is a real product need.
- Review storage growth monthly (DB dumps + media) to avoid surprise backup costs.

### Quota behavior notes

- Sentry free plan is quota-based: when quota is exhausted, new events are dropped/rejected.
- Existing accepted events remain available according to plan retention window.

---

### Phase 7 — Future Enhancements (out of scope for now)

- [ ] "Report this library" should be "Report an issue with this library"
- [ ] Multi language support with language selector (at least English and Italian)
- [ ] Multiple photos per library (LibraryPhoto model)
- [ ] Sign in with Apple (after Apple Developer Program enrollment)
- [ ] Custom moderation dashboard (beyond Django admin)
- [ ] User profiles (public page with contributions)
- [ ] Favorites / bookmarks
- [ ] "Library near me" geolocation prompt
- [ ] iOS app

---

### Phase 8 — Advanced: Rolling Sandbox PR Deploy (single preview environment)

#### 8.1 — Sandbox architecture and safety boundaries
- [ ] Use one rolling preview environment at `sandbox.mywebsite.com` (latest eligible PR always wins)
- [ ] Keep sandbox fully isolated from production app, DB, storage, and secrets
- [ ] Add sandbox host settings (`ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`) for the sandbox domain
- [ ] Protect sandbox with Basic Auth or IP allowlisting
- [ ] Disable non-essential outbound side effects in sandbox (transactional email, payment/webhook side effects)

#### 8.2 — Dokku sandbox app setup
- [ ] Create dedicated Dokku app (`dokku apps:create book-corners-sandbox`)
- [ ] Attach sandbox domain (`dokku domains:add book-corners-sandbox sandbox.mywebsite.com`)
- [ ] Enable TLS for sandbox (Dokku letsencrypt plugin)
- [ ] Configure sandbox-only env vars (`DEBUG=False`, unique `SECRET_KEY`, sandbox-safe settings)
- [ ] Mount sandbox media storage separate from production

#### 8.3 — GitHub Actions deploy gating (manual PRs only)
- [ ] Add `.github/workflows/deploy-sandbox.yml` for `pull_request` events (`opened`, `synchronize`, `reopened`, `labeled`)
- [ ] Restrict deploys to PRs authored by the project owner (`github.event.pull_request.user.login == <your-github-username>`)
- [ ] Explicitly exclude bot authors (`dependabot[bot]` and other bots)
- [ ] Require an opt-in PR label (e.g. `deploy-sandbox`) before deploying
- [ ] Add workflow concurrency (`group: sandbox-deploy`, `cancel-in-progress: true`) so newest push overrides older runs

#### 8.4 — Database refresh on every sandbox deploy
- [ ] Decide and document one default mode: `recreate+seed` (recommended) or `clone sanitized baseline`
- [ ] If `recreate+seed`: recreate sandbox DB service each deploy, link, run migrations, and load deterministic test seed
- [ ] If `clone sanitized baseline`: clone from a sanitized baseline DB service, link, then run migrations
- [ ] Never use unsanitized production data in sandbox
- [ ] Add post-refresh sanity checks (DB connectivity, migration state, key table counts, PostGIS query)

#### 8.5 — Deploy sequence and verification
- [ ] Deploy PR head commit to `book-corners-sandbox` (never to production app)
- [ ] Run `python manage.py migrate` in sandbox after deploy
- [ ] Refresh sandbox data (seed or sanitized clone workflow)
- [ ] Run smoke checks for critical routes and static assets (`/`, `/login/`, `/map/`, `/static/css/app.css`)
- [ ] Post PR comment with sandbox URL, deployed commit SHA, and smoke-check result

#### 8.6 — Rollback and operational controls
- [ ] Add `workflow_dispatch` for manual sandbox redeploy by PR number or commit SHA
- [ ] Define behavior on PR close (reset sandbox to `main` or leave latest deployed state) and document it
- [ ] Keep last successful sandbox release reference for quick rollback
- [ ] Document sandbox incident flow (failed deploy, failed migration, failed data refresh)

#### 8.7 — Definition of done
- [ ] Only owner-authored, manually opted-in PRs deploy to sandbox
- [ ] Dependabot/bot PRs never override sandbox
- [ ] Every sandbox deploy gets a fresh or sanitized DB refresh
- [ ] `sandbox.mywebsite.com` consistently points to the latest eligible PR deploy
- [ ] Deploy logs and smoke results are visible in Actions and linked from the PR
