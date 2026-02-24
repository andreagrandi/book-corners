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
```

## End-to-end smoke test (Docker + browser)

After UI/template/static changes, always run this check before considering the task done:

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

## Architecture

Django 6 project with PostGIS for geospatial data. Two apps:

- **users** — Custom `AbstractUser` model (`AUTH_USER_MODEL = "users.User"`), minimal extension for future flexibility.
- **libraries** — Core domain. `Library` model stores little free library locations with a PostGIS `PointField` (SRID 4326). `Report` model tracks user-submitted issues about libraries. Both use status workflows managed through Django admin actions (approve/reject libraries, resolve/dismiss reports).

Admin uses `GISModelAdmin` for the Library model to support map-based editing.

URL routing currently only exposes the Django admin (`/admin/`). REST API via Django Ninja is planned.

## Key Patterns

- **Slug generation**: `Library.save()` auto-generates unique slugs from `city + address + name`, with numeric suffixes for duplicates, truncated to fit `max_length`.
- **Database config**: Uses `dj-database-url` to parse `DATABASE_URL` env var. GIS library paths (`GDAL_LIBRARY_PATH`, `GEOS_LIBRARY_PATH`) are read from environment in `config/settings.py`.
- **Test fixtures**: Shared fixtures (`user`, `admin_user`, `admin_client`) in root `conftest.py`. App-specific fixtures (`library`, `admin_library`, `admin_report`) in `libraries/tests.py`.
- **Environment**: `.envrc` (direnv) sets `DATABASE_URL`, `GDAL_LIBRARY_PATH`, and `GEOS_LIBRARY_PATH` for local macOS development. `.env.example` has Docker equivalents.

## Dependencies

Python 3.14. PostGIS 17. Key packages: `django`, `psycopg2-binary`, `Pillow`, `dj-database-url`, `pytest`, `pytest-django`, `gunicorn`.
ALWAYS use uv to install packages but do not use its lock system. Use simple requirements files instead.
