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
- [x] Configure `tailwind.config.js` to scan Django templates
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
- [ ] Confirm account policy: username unique + email unique (case-insensitive)
- [ ] Keep login with username OR email + password for local auth
- [ ] Keep email required for local registration
- [ ] Define linking rule: same verified Google email must map to one existing local user
- [ ] Define fallback behavior if provider email is missing or not verified

#### 3.5.2 — User data hardening for unique email
- [ ] Audit existing users for duplicate emails ignoring case
- [ ] Normalize stored emails (trim + lowercase) via migration before constraint
- [ ] Add database-level unique constraint for email (case-insensitive strategy)
- [ ] Update forms/validation so duplicate emails fail with a clear error message
- [ ] Add migration tests for normalization + uniqueness behavior

#### 3.5.3 — Google Cloud setup (local first)
- [ ] Create Google Cloud project (free tier, no billing required for basic sign-in)
- [ ] Configure OAuth consent screen (External + Testing mode)
- [ ] Create OAuth client type: Web application
- [ ] Add local redirect URI: `http://localhost:8000/accounts/google/login/callback/`
- [ ] Add local authorized origin: `http://localhost:8000`
- [ ] Document production URI to add later: `https://<domain>/accounts/google/login/callback/`

#### 3.5.4 — Django allauth integration (Google only)
- [ ] Install `django-allauth`
- [ ] Enable required apps in settings:
  - [ ] `django.contrib.sites`
  - [ ] `allauth`
  - [ ] `allauth.account`
  - [ ] `allauth.socialaccount`
  - [ ] `allauth.socialaccount.providers.google`
- [ ] Configure authentication backends to include model backend + allauth backend
- [ ] Add and configure `SITE_ID`
- [ ] Add allauth account settings (email, login, signup behavior aligned with current UX)
- [ ] Add SocialApp/provider config via admin or settings-backed env values

#### 3.5.5 — URL routing + web login UX
- [ ] Include allauth URLs under `/accounts/`
- [ ] Keep `/login/` and `/register/` as the main entry pages
- [ ] Add "Continue with Google" button to login template
- [ ] Add "Continue with Google" button to register template
- [ ] Preserve safe `next` redirect behavior after social login
- [ ] Keep logout as POST-only and unchanged

#### 3.5.6 — Social account linking behavior
- [ ] If Google verified email matches existing user email: attach social account, do not create new user
- [ ] If no matching user exists: create user and generate unique username
- [ ] Prevent duplicate users when the same Google account logs in repeatedly
- [ ] Handle conflict edge cases deterministically (race conditions / simultaneous signups)
- [ ] Ensure future profile fields remain independent from auth method

#### 3.5.7 — Environment variables and secrets flow
- [ ] Add placeholders to `.env.example`:
  - [ ] `GOOGLE_OAUTH_CLIENT_ID`
  - [ ] `GOOGLE_OAUTH_CLIENT_SECRET`
- [ ] Local dev: set values in `.env` / `.envrc`
- [ ] Production: configure Dokku env vars with `dokku config:set` (or GitHub Actions automation)
- [ ] Keep code identical across environments; only env values and OAuth console URIs differ

#### 3.5.8 — Test coverage
- [ ] Keep current auth tests green (register/login/logout/navbar behavior)
- [ ] Add tests for email uniqueness (including case-insensitive duplicates)
- [ ] Add tests for Google-first signup (new user created)
- [ ] Add tests for Google login linking to an existing local account by email
- [ ] Add tests for provider denial/cancel callback handling
- [ ] Add tests for invalid callback/state mismatch handling

#### 3.5.9 — Manual QA (required before moving to API phase)
- [ ] Local smoke test: login with Google from `http://localhost:8000/` end-to-end
- [ ] Verify session is created and navbar switches to authenticated state
- [ ] Verify logout works and returns to anonymous navbar state
- [ ] Verify existing local account with same email is reused (no duplicate row)
- [ ] Verify normal username/email + password flow still works exactly as before

#### 3.5.10 — Documentation updates
- [ ] Add setup guide for Google OAuth in local development
- [ ] Document common OAuth errors and fixes (`redirect_uri_mismatch`, host mismatch, wrong callback path)
- [ ] Document how to add production redirect URI when the domain is ready
- [ ] Document secret management strategy for GitHub Actions + Dokku

#### 3.5.11 — Definition of done
- [ ] Google login works locally for web users
- [ ] Existing auth flow remains backward-compatible
- [ ] Email uniqueness is enforced safely at DB and form level
- [ ] No Apple login work is introduced in this phase
- [ ] The project is ready to add iOS support later as an extension, not a rewrite

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

#### 5.6 — First public deploy gate (single environment)
- [ ] Confirm there is no staging environment and production is public from first deploy
- [ ] Complete 5.4 (error pages) and 5.5 (security review) before DNS cutover
- [ ] Test core flows locally with production-like settings (`DEBUG=False`): home, map, submit, login, admin
- [ ] Define launch scope freeze for first deploy week (only bugfixes, no large feature work)
- [ ] Decide email policy for MVP: keep welcome/reset email flows disabled until provider is configured
- [ ] Create a one-page incident response note (who gets alerted, rollback steps, backup restore reference)

---

### Phase 6 — Deployment

#### 6.1 — Dokku setup
- [ ] Provision VPS and install Dokku
- [ ] Create Dokku app (`dokku apps:create book-corners`)
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

#### 6.6 — Backups to BorgBase (no downtime)
- [ ] Add nightly PostgreSQL dump job using `pg_dump -Fc` (transaction-safe, no service stop)
- [ ] Add backup job for media files directory used by Dokku storage mount
- [ ] Push DB dumps + media backups to BorgBase with `borg create`
- [ ] Add retention policy with `borg prune` (daily / weekly / monthly)
- [ ] Keep repository passphrase and backup credentials in secure env vars (not in git)

#### 6.7 — Automated restore verification (separate DB)
- [ ] Add periodic restore drill that restores latest dump into a temporary database
- [ ] Run sanity checks on restored DB (schema, migrations table, sample counts, PostGIS query)
- [ ] Drop temporary restore-check database after validation
- [ ] Alert if restore verification fails
- [ ] Document manual full restore procedure for emergency recovery

#### 6.8 — Monitoring and alerting baseline
- [ ] Add uptime monitoring for `/` and one authenticated-critical route
- [ ] Add heartbeat monitoring for backup and restore-check jobs
- [ ] Add error tracking (Sentry free tier or equivalent) with conservative quota settings
- [ ] Configure alert delivery channel(s) (email, Telegram, or Slack)
- [ ] Run one test incident to verify end-to-end alert delivery

#### 6.9 — First production deploy runbook
- [ ] Document exact deploy sequence (build, migrate, restart, smoke checks)
- [ ] Document rollback sequence (previous release rollback + DB restore decision matrix)
- [ ] Add post-deploy smoke checklist (home, static CSS, submit flow, admin login)
- [ ] Define maintenance communication template for planned downtime or incidents

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

- [x] Find a better name and rename the home page title
- [x] Find a logo for the project
- [x] Improve the footer (remove mention of Django, HTMX and add link to the GitHub repository)
- [ ] "Report this library" should be "Report an issue with this library"
- [ ] Multi language support with language selector (at least English and Italian)
- [x] An About page which explain the project more in details
- [ ] Multiple photos per library (LibraryPhoto model)
- [ ] Sign in with Apple (after Apple Developer Program enrollment)
- [ ] Custom moderation dashboard (beyond Django admin)
- [ ] User profiles (public page with contributions)
- [ ] Favorites / bookmarks
- [ ] "Library near me" geolocation prompt
- [ ] iOS app
