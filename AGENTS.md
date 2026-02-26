# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Start PostGIS database (required for tests)
docker compose up db -d

# Start app + db + Tailwind watcher for local development
docker compose up app db tailwind

# Build CSS once
npm run build:css

# Run all tests
nox -s tests

# Note: nox is configured to use uv for package installation

# Run a single test
nox -s tests -- libraries/tests.py::TestLibraryModel::test_create_library_with_all_fields

# Run tests with verbose output
nox -s tests -- -v

# Apply migrations
python manage.py migrate

# Run dev server
python manage.py runserver

# Seed local demo data (reuses images from libraries_examples/ if available)
python manage.py seed_libraries --reset --count 36 --images-dir libraries_examples --seed 42

# Same seed command inside Docker app container
docker compose exec app python manage.py seed_libraries --reset --count 36 --images-dir libraries_examples --seed 42

# Build and preview docs locally
python manage.py export_openapi_schema > docs/openapi.json
zensical serve

# Build docs
zensical build
```

## End-to-end smoke test (Docker + browser)

After UI/template/static changes, always run this check before considering the task done:

### Migration safety check (required)

Before restarting the app or running smoke checks after model changes, always apply migrations first.

```bash
# Local runtime
python manage.py migrate

# Docker runtime
docker compose exec app python manage.py migrate
```

If you see errors like `column ... does not exist`, treat that as a migration mismatch and run migrations before any further debugging.

1. Start the full stack and rebuild app image when needed:

```bash
docker compose up -d --build app db tailwind
docker compose ps
```

2. Verify HTTP responses for homepage and compiled CSS:

```bash
curl -I http://localhost:8000/
curl -I http://localhost:8000/static/css/app.css
```

Expected: both return `200 OK`.

3. Inspect logs for runtime issues:

```bash
docker compose logs --no-color --tail=120 app tailwind
```

Expected:
- `tailwind` container stays running (watch mode active)
- no repeated `Not Found: /static/css/app.css` in app logs

4. Validate real rendering with Playwright tools:

- Open `http://localhost:8000/`
- Capture snapshot/screenshot
- Check browser console for errors
- Check network requests (including static assets)

If the page looks unstyled in browser, treat it as a blocker and debug Docker static file serving before moving on.

### CSS recovery playbook (unstyled page)

If a page suddenly renders without styles, run this checklist before doing anything else:

1. Rebuild CSS in the running stack:

```bash
docker compose up -d app db tailwind
docker compose exec tailwind npm run build:css
```

2. Verify the stylesheet endpoint:

```bash
curl -I http://localhost:8000/static/css/app.css
```

Expected:
- `200 OK`
- non-trivial `Content-Length` (typically tens of KB, not a tiny fallback file)

3. Hard refresh the browser (`Cmd+Shift+R`) to clear cached CSS.

4. If still broken, restart and inspect logs:

```bash
docker compose restart app tailwind
docker compose logs --no-color --tail=120 app tailwind
```

5. Treat repeated `Not Found: /static/css/app.css` or `500` responses for that path as blockers.

Note: `static/css/app.css` is generated and gitignored. Regenerate it when needed, but do not commit it.

## Code style expectations

- Add a docstring to every new function, method, and test function.
- When touching existing code, add missing docstrings in the edited scope before finishing.
- Keep docstrings to exactly two lines: a concise summary sentence and one intent sentence.
- Do not include args/kwargs/returns sections in docstrings.

## Working tree hygiene (required)

- Do not tell the user to run cleanup commands that the agent can run directly.
- If branch switching, pulling, or other git operations are blocked by generated local changes, resolve them automatically when safe.
- Treat `static/css/app.css` as a generated asset that is intentionally gitignored: regenerate it with `npm run build:css` when needed, but do not commit it.
- When frontend code is intentionally changed, commit source changes only (`assets/`, templates, scripts) and verify styling with a fresh `npm run build:css` before finishing.

## Architecture

Django 6 project with PostGIS for geospatial data. Two apps:

- **users** — Custom `AbstractUser` model (`AUTH_USER_MODEL = "users.User"`), minimal extension for future flexibility.
- **libraries** — Core domain. `Library` model stores little free library locations with a PostGIS `PointField` (SRID 4326). `Report` model tracks user-submitted issues about libraries. Both use status workflows managed through Django admin actions (approve/reject libraries, resolve/dismiss reports).

Admin uses `GISModelAdmin` for the Library model to support map-based editing.

URL routing currently includes web pages (`/`, `/login/`, `/register/`, `/latest-entries/`) plus Django admin (`/admin/`). API is scaffolded at `/api/v1/` and will be expanded.

## Key Patterns

- **Slug generation**: `Library.save()` auto-generates unique slugs from `city + address + name`, with numeric suffixes for duplicates, truncated to fit `max_length`.
- **Database config**: Uses `dj-database-url` to parse `DATABASE_URL` env var. GIS library paths (`GDAL_LIBRARY_PATH`, `GEOS_LIBRARY_PATH`) are read from environment in `config/settings.py`.
- **Test fixtures**: Shared fixtures (`user`, `admin_user`, `admin_client`) in root `conftest.py`. App-specific fixtures (`library`, `admin_library`, `admin_report`) in `libraries/tests.py`.
- **Environment**: `.envrc` (direnv) sets `DATABASE_URL`, `GDAL_LIBRARY_PATH`, and `GEOS_LIBRARY_PATH` for local macOS development. `.env.example` has Docker equivalents.
- **Seed data command**: `seed_libraries` can reset and generate realistic sample `Library` rows with geospatial points and status mix. It accepts `--reset`, `--count`, `--seed`, and `--images-dir` and will reuse local images when provided.
- **Local seed images**: `libraries_examples/` is intentionally gitignored so each developer can use their own local photo set for seeding.

## Dependencies

Python 3.14. PostGIS 17. Key packages: `django`, `psycopg2-binary`, `Pillow`, `dj-database-url`, `pytest`, `pytest-django`, `gunicorn`.
ALWAYS use uv to install packages but do not use its lock system. Use simple requirements files instead.
