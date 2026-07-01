---
name: book-corners-local-dev
description: Run Book Corners local development commands. Use when starting services, running tests, applying migrations, building CSS, running the dev server, seeding demo data, or building local docs.
---

# Book Corners Local Development

Use these commands for local development and verification.

## Services

Host ports are dynamic ranges so other projects' containers never conflict: `db` maps to `5540-5550`, `app` to `8000-8010`, and Docker picks the first free port when the container starts. Container-internal ports are fixed (`db:5432`, app `8000`). Discover the assigned ports with:

```bash
python3 scripts/local_env.py database-url   # postgis://...@localhost:<port>/book_corners
python3 scripts/local_env.py app-url        # http://localhost:<port>
```

Start PostGIS manually (tests do NOT need this — nox starts it automatically):

```bash
docker compose up db -d
```

Start the app, database, and Tailwind watcher:

```bash
docker compose up app db tailwind
```

Open a database shell (no port knowledge needed):

```bash
docker compose exec db psql -U postgres book_corners
```

## CSS

Build CSS once:

```bash
npm run build:css
```

`static/css/app.css` is generated and gitignored. Regenerate it when needed, but do not commit it.

## Tests

Locally, every nox session starts the dockerized `db` service if needed, discovers its dynamic host port, and exports `DATABASE_URL` itself — run them with no manual setup and no environment variables. In CI (`CI` env var set) the ambient `DATABASE_URL` is used untouched.

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

Host-mode `manage.py` commands read `DATABASE_URL` from the environment; direnv exports it via `scripts/local_env.py` discovery (see `.envrc`). If the db container was restarted or recreated since entering the directory, run `direnv reload` (or export `DATABASE_URL=$(python3 scripts/local_env.py database-url)` inline).

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

Export OpenAPI schema for local inspection (needs the database; `python3 scripts/local_env.py ensure-db` starts it from cold):

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
