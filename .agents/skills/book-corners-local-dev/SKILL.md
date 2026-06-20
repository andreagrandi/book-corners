---
name: book-corners-local-dev
description: Run Book Corners local development commands. Use when starting services, running tests, applying migrations, building CSS, running the dev server, seeding demo data, or building local docs.
---

# Book Corners Local Development

Use these commands for local development and verification.

## Services

Start PostGIS for tests:

```bash
docker compose up db -d
```

Start the app, database, and Tailwind watcher:

```bash
docker compose up app db tailwind
```

## CSS

Build CSS once:

```bash
npm run build:css
```

`static/css/app.css` is generated and gitignored. Regenerate it when needed, but do not commit it.

## Tests

Run all unit and integration tests, excluding browser E2E:

```bash
nox -s tests
```

Run browser E2E tests:

```bash
nox -s e2e
```

Run one test:

```bash
nox -s tests -- libraries/tests.py::TestLibraryModel::test_create_library_with_all_fields
```

Run tests with verbose output:

```bash
nox -s tests -- -v
```

Nox is configured to use `uv` for package installation.

## Django

Apply migrations:

```bash
python manage.py migrate
```

Run the dev server:

```bash
python manage.py runserver
```

Seed local demo data:

```bash
python manage.py seed_libraries --reset --count 36 --images-dir libraries_examples --seed 42
```

Seed inside the Docker app container:

```bash
docker compose exec app python manage.py seed_libraries --reset --count 36 --images-dir libraries_examples --seed 42
```

The seed command reuses images from `libraries_examples/` when available.

## Docs

Export OpenAPI schema for local inspection:

```bash
python manage.py export_openapi_schema > docs/openapi.json
```

Preview docs:

```bash
zensical serve
```

Build docs:

```bash
zensical build
```
