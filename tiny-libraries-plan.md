# Little Free Libraries — Project Plan

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

### Phase 2 (iOS app / later)
- **OAuth2 / Social auth:** Add `django-allauth` (supports Apple, Google, etc.).
  django-allauth integrates with Django's auth system, so existing sessions and
  JWT flows continue to work.
- **Sign in with Apple:** Required for iOS apps with third-party login. django-allauth
  has a provider for it.

### What this means in practice
- Build all auth around Django's User model from day one.
- The API always checks JWT; the web always checks session. Django Ninja supports
  multiple auth backends per endpoint, so a single view can accept either.
- Adding OAuth later is an additive change — no rewrites needed.
- Some API endpoints (list, search, detail) require no auth at all.

---

## Pages & Routes

| Page              | URL (indicative)       | Auth?  | Notes                                           |
|-------------------|------------------------|--------|--------------------------------------------------|
| Homepage          | `/`                    | No     | Hero/explainer, latest entries grid               |
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
- [ ] Install `django-ninja` and `django-ninja-jwt`
- [ ] Create API router at `/api/v1/`
- [ ] Add `/auth/register` endpoint (create user, return JWT pair)
- [ ] Add `/auth/login` endpoint (validate credentials, return JWT pair)
- [ ] Add `/auth/refresh` endpoint (refresh token → new access token)
- [ ] Add `/auth/me` endpoint (returns current user info, requires JWT)
- [ ] Add a basic test for each auth endpoint

#### 1.6 — TailwindCSS + daisyUI integration
- [ ] Add Node/npm tooling: `package.json` with tailwindcss, daisyui, postcss, autoprefixer
- [ ] Configure `tailwind.config.js` to scan Django templates
- [ ] Set up input CSS file with Tailwind directives and daisyUI plugin
- [ ] Add npm script to build CSS and a watch mode for development
- [ ] Add a Tailwind CSS watcher service to `docker-compose.yml` (or document the dev command)
- [ ] Configure Django `STATICFILES_DIRS` to include the compiled CSS output
- [ ] Verify: a template using daisyUI classes renders correctly

#### 1.7 — Base template + navbar/footer
- [ ] Create `base.html` template: HTML boilerplate, meta tags, CSS/JS includes
- [ ] Add responsive navbar using daisyUI (links: Home, Map, Search, Submit, Login/Register)
- [ ] Add simple footer (project name, credits)
- [ ] Add HTMX script include in base template
- [ ] Create a placeholder homepage view and URL to verify the base template renders

#### 1.8 — CI with GitHub Actions
- [ ] Create `.github/workflows/ci.yml`
- [ ] Set up a job that runs on push and pull requests
- [ ] Use a PostGIS service container for the test database
- [ ] Install Python dependencies with uv and run `python manage.py test`
- [ ] Build Tailwind CSS as part of the pipeline (or skip if tests don't need it)
- [ ] Verify the pipeline passes on the current codebase

---

### Phase 2 — Core Web Pages

#### 2.1 — Registration and login pages
- [ ] Create registration page with form (username, email, password, confirm password)
- [ ] Wire up Django's `UserCreationForm` (or custom form for the custom user model)
- [ ] Create login page with form (username/email, password)
- [ ] Wire up Django's `AuthenticationForm` and `login()` view
- [ ] Add logout view (POST-only, redirect to homepage)
- [ ] Update navbar: show Login/Register when anonymous, show username + Logout when authenticated
- [ ] Add a test for registration and login flows

#### 2.2 — Homepage: hero section + latest entries
- [ ] Create homepage view that fetches latest N approved libraries
- [ ] Build hero section in template: heading, short description, CTA buttons (Explore Map, Submit)
- [ ] Build latest-entries card grid: each card shows photo thumbnail, name/city, short description
- [ ] Extract the card grid into an HTMX partial template (`_latest_entries.html`)
- [ ] Add HTMX loading for the card grid (hx-get, hx-target, hx-trigger="load")
- [ ] Add "Load more" / pagination on the partial if more entries exist

#### 2.3 — Library detail page
- [ ] Create detail view that fetches a library by slug
- [ ] Build detail template: photo (full size), name, description, address, city/country
- [ ] Add a "Report" button (links to report form, visible to authenticated users)
- [ ] Handle 404 for non-existent slugs
- [ ] Add a test for the detail view (approved library visible, pending library returns 404)

#### 2.4 — Submit library form: basic photo upload + address fields
- [ ] Create submit view (login required) with a form
- [ ] Form fields: photo (file upload), name (optional), description (optional), address, city, country, postal_code
- [ ] Handle file upload: save photo via ImageField
- [ ] Set status=pending and created_by=current user on save
- [ ] Redirect to detail page (or dashboard) after successful submission
- [ ] Add a test for submission (authenticated user creates a pending library)

#### 2.5 — Submit form: EXIF GPS extraction
- [ ] Add utility function: extract GPS coordinates from an uploaded image using Pillow/exifread
- [ ] On photo upload, attempt EXIF extraction on the backend
- [ ] If GPS found: reverse geocode via Nominatim (geopy) to get address, city, country, postal_code
- [ ] Pre-fill address fields with the geocoded result (return as JSON or render pre-filled form)
- [ ] Add a test with a sample geotagged image

#### 2.6 — Submit form: Photon address autocomplete
- [ ] Add address autocomplete input using Photon API (photon.komoot.io)
- [ ] Implement as HTMX partial or minimal JS: debounced fetch on keystrokes, render suggestion list
- [ ] On suggestion select: populate address, city, country, postal_code fields
- [ ] On final form submit: forward geocode the address via Nominatim to get coordinates
- [ ] Store both the PointField and human-readable address fields

#### 2.7 — User dashboard
- [ ] Create dashboard view (login required) listing the current user's submissions
- [ ] Show each library: photo thumbnail, name/address, status badge (pending/approved/rejected)
- [ ] Link each entry to its detail page
- [ ] Add a test for the dashboard view

#### 2.8 — Search page
- [ ] Create search view with a form: text query (q), city, country, postal_code filters
- [ ] Implement PostgreSQL full-text search on Library name + description (SearchVector/SearchQuery)
- [ ] Filter by city/country/postal_code when provided
- [ ] Render results as a card grid (reuse the library card partial)
- [ ] Wire up HTMX: form submits via hx-get, results replace a target div
- [ ] Add a test for search (text match, filter match, no results)

---

### Phase 3 — Map Page

#### 3.1 — Full map page with markers
- [ ] Create `/map/` view that returns the map page template
- [ ] Add Leaflet CSS/JS to the template (CDN or local static)
- [ ] Initialize Leaflet map centered on a sensible default (e.g. Europe or user's region)
- [ ] Create a JSON endpoint (or Django Ninja endpoint) returning approved libraries as GeoJSON (id, name, lat, lng, photo thumbnail URL)
- [ ] Fetch the GeoJSON on page load and add markers to the map

#### 3.2 — Marker clustering
- [ ] Add Leaflet.markercluster plugin (CSS + JS)
- [ ] Wrap markers in a MarkerClusterGroup
- [ ] Verify clustering works with a batch of test data points

#### 3.3 — Marker popups
- [ ] On marker click: show a popup with library name, photo thumbnail, city
- [ ] Add a "View details" link in the popup pointing to the detail page (`/library/<slug>/`)

#### 3.4 — Small map on detail page
- [ ] Add a small Leaflet map on the library detail page
- [ ] Show a single pin at the library's coordinates
- [ ] Set appropriate zoom level for a neighborhood view

---

### Phase 4 — API Completion

#### 4.1 — Library list + detail endpoints
- [ ] `GET /api/v1/libraries` — return paginated list of approved libraries
- [ ] Define response schema (LibraryOut) with all public fields
- [ ] `GET /api/v1/libraries/{id}` — return a single library by ID
- [ ] Handle 404 for non-existent or non-approved libraries
- [ ] Add tests for both endpoints

#### 4.2 — Library search + filtering
- [ ] Add query params: `q` (full-text), `city`, `country`, `postal_code`
- [ ] Add proximity params: `lat`, `lng`, `radius` — use PostGIS `ST_DWithin`
- [ ] Combine filters (text + location + field filters)
- [ ] Add tests for each filter type and combinations

#### 4.3 — Latest libraries endpoint
- [ ] `GET /api/v1/libraries/latest` — return N most recent approved libraries
- [ ] Parameterize N with a query param (default 10, max 50)
- [ ] Add test

#### 4.4 — Submit library endpoint
- [ ] `POST /api/v1/libraries` — create a new library (requires JWT)
- [ ] Define request schema (LibraryIn) with validation
- [ ] Handle photo upload via multipart form data
- [ ] Set status=pending, created_by from JWT
- [ ] Geocode address → coordinates if not provided
- [ ] Add tests (authenticated, unauthenticated, validation errors)

#### 4.5 — Report endpoint
- [ ] `POST /api/v1/libraries/{id}/report` — create a report (requires JWT)
- [ ] Define request schema (ReportIn) with reason choices and details
- [ ] Handle optional photo upload
- [ ] Set status=open, created_by from JWT
- [ ] Add tests (valid report, missing library, unauthenticated)

#### 4.6 — Consistent error responses + validation
- [ ] Define a standard error response schema (code, message, details)
- [ ] Add exception handlers in Django Ninja for 400, 401, 403, 404, 422
- [ ] Ensure all endpoints return consistent error shapes
- [ ] Add tests for error cases

#### 4.7 — Pagination
- [ ] Implement cursor or offset pagination on list/search endpoints
- [ ] Add `page` and `page_size` query params
- [ ] Return pagination metadata (total, next, previous)
- [ ] Add tests for pagination boundaries

#### 4.8 — API docs + rate limiting
- [ ] Verify Django Ninja auto-generates OpenAPI schema at `/api/v1/docs`
- [ ] Review and annotate schemas for clarity (descriptions, examples)
- [ ] Add rate limiting on public endpoints (django-ratelimit or middleware)
- [ ] Add test that rate limiting triggers on excessive requests

---

### Phase 5 — Polish & Hardening

#### 5.1 — Photo optimization
- [ ] On upload: resize photo to a max dimension (e.g. 1600px wide)
- [ ] Compress JPEG quality (e.g. 85%)
- [ ] Generate a thumbnail version (e.g. 400px wide) for card grids and popups
- [ ] Store both original (resized) and thumbnail
- [ ] Add a test that verifies resizing/compression

#### 5.2 — Report flow polish
- [ ] Build report form as an HTMX inline form or modal on the detail page
- [ ] Submit via HTMX, show success/error message without page reload
- [ ] Ensure reports are visible and manageable in Django admin
- [ ] Add a test for the full report submission flow

#### 5.3 — SEO basics
- [ ] Add meta title and description to all pages (base template + per-page overrides)
- [ ] Add Open Graph tags for library detail pages (photo, title, description)
- [ ] Generate a `sitemap.xml` using Django's sitemap framework
- [ ] Add `robots.txt`

#### 5.4 — Error pages
- [ ] Create custom 404 template (friendly message, link to homepage/map)
- [ ] Create custom 500 template (friendly message, apology)
- [ ] Wire up Django's handler404 and handler500

#### 5.5 — Security review
- [ ] Verify CSRF protection is active on all forms
- [ ] Verify file upload validation (allowed types, max size)
- [ ] Review rate limiting on auth endpoints (prevent brute force)
- [ ] Review input validation on all user-facing fields
- [ ] Run Django's `check --deploy` and address warnings
- [ ] Verify `DEBUG=False` and secret key management for production

---

### Phase 6 — Deployment

#### 6.1 — Dokku setup
- [ ] Provision VPS and install Dokku
- [ ] Create Dokku app (`dokku apps:create tiny-libraries`)
- [ ] Set environment variables (SECRET_KEY, DATABASE_URL, ALLOWED_HOSTS, DEBUG=False)

#### 6.2 — Database
- [ ] Install Dokku PostgreSQL plugin with PostGIS support
- [ ] Create and link database to the app
- [ ] Run migrations on first deploy

#### 6.3 — Domain + SSL
- [ ] Configure custom domain (`dokku domains:add`)
- [ ] Install and configure Dokku letsencrypt plugin
- [ ] Verify HTTPS works

#### 6.4 — Media storage
- [ ] Set up persistent storage volume for media files (`dokku storage:mount`)
- [ ] Configure Django `MEDIA_ROOT` to use the mounted volume
- [ ] Document future migration path to S3-compatible storage

#### 6.5 — Continuous deployment
- [ ] Add deploy step to the existing GitHub Actions CI pipeline: push to Dokku remote on main branch merge
- [ ] Verify full pipeline: push → tests → deploy

---

### Phase 7 — Future Enhancements (out of scope for now)

- [ ] Multiple photos per library (LibraryPhoto model)
- [ ] OAuth2 / Sign in with Apple (django-allauth)
- [ ] Custom moderation dashboard (beyond Django admin)
- [ ] User profiles (public page with contributions)
- [ ] Favorites / bookmarks
- [ ] "Library near me" geolocation prompt
- [ ] Internationalization (i18n)
- [ ] iOS app
