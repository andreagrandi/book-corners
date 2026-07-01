---
name: book-corners-domain
description: Apply Book Corners Django/PostGIS domain knowledge. Use when changing models, migrations, queries, admin behavior, seed data, fixtures, geospatial behavior, slug generation, dependencies, or core library/user app architecture.
---

# Book Corners Domain

Use this reference for core application architecture and domain patterns.

## Architecture

Book Corners is a Django 6 project with PostGIS geospatial data.

Apps:

- `users`: custom `AbstractUser` model configured as `AUTH_USER_MODEL = "users.User"`.
- `libraries`: core little free library domain.

The `Library` model stores little free library locations with a PostGIS `PointField` using SRID 4326. The `Report` model tracks user-submitted issues about libraries.

Library and report moderation use status workflows managed through Django admin actions, including approving or rejecting libraries and resolving or dismissing reports.

Admin uses `GISModelAdmin` for `Library` so map-based editing is available.

Current URL routing includes web pages such as `/`, `/login/`, `/register/`, and `/latest-entries/`, Django admin at `/admin/`, and API routes scaffolded under `/api/v1/`.

## Database Indexes

When adding a model field or modifying queries, ensure fields used in `filter()`, `order_by()`, `list_filter`, or `values_list()` have a database index.

Add indexes in the model `Meta.indexes` list and generate a migration. Skip indexes for low-cardinality boolean fields or fields that are rarely queried.

## Key Patterns

Slug generation:

- `Library.save()` auto-generates unique slugs from `city + address + name`.
- Duplicate slugs receive numeric suffixes.
- Generated slugs are truncated to fit `max_length`.

Database configuration:

- `dj-database-url` parses `DATABASE_URL`.
- `GDAL_LIBRARY_PATH` and `GEOS_LIBRARY_PATH` are read from the environment in `config/settings.py`.

Test fixtures:

- Shared fixtures: `user`, `admin_user`, and `admin_client` in root `conftest.py`.
- App fixtures: `library`, `admin_library`, and `admin_report` in `libraries/tests.py`.
- E2E fixtures: `e2e_user`, `approved_libraries`, `single_library`, `mock_external_apis`, and `authenticated_page` in `tests/e2e/conftest.py`.

Environment:

- `.envrc` configures local macOS `DATABASE_URL` (discovered dynamically via `scripts/local_env.py`), `GDAL_LIBRARY_PATH`, and `GEOS_LIBRARY_PATH`.
- `.env.example` contains Docker equivalents.

Seed data:

- `seed_libraries` can reset and generate realistic `Library` rows with geospatial points and mixed statuses.
- Options include `--reset`, `--count`, `--seed`, and `--images-dir`.
- `libraries_examples/` is intentionally gitignored so each developer can use local photo sets.

Dependencies:

- Python 3.14.
- PostGIS 17.
- Key packages include `django`, `psycopg2-binary`, `Pillow`, `dj-database-url`, `pytest`, `pytest-django`, `gunicorn`, and `structlog`.
- Always use `uv` to install Python packages.
- Do not use a `uv` lock file; use simple requirements files.
