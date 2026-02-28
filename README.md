# Book Corners

A community-driven directory of little free libraries — those small book exchange spots found in public spaces where you can leave or take a book for free.

Book Corners lets anyone discover nearby little free libraries on an interactive map, submit new ones with photo and location, and report issues to keep the directory accurate.

**Live site:** [bookcorners.org](https://bookcorners.org)
**API docs:** [developers.bookcorners.org](https://developers.bookcorners.org)

## Features

- **Interactive map** — Browse all approved libraries on a full-page Leaflet map with marker clustering and popups
- **Search and filtering** — Full-text search across names and descriptions, filter by city/country/postal code
- **Library submission** — Upload a photo, pick a location on the map, and submit a new library for moderation
- **EXIF GPS extraction** — Photos with GPS metadata auto-populate the address fields via reverse geocoding
- **Address autocomplete** — Type-ahead suggestions powered by Photon (OpenStreetMap data)
- **Photo optimization** — Uploaded images are resized, compressed, and thumbnailed automatically
- **Community photos** — Users can submit additional photos for existing libraries, moderated by admins
- **Community reporting** — Flag damaged, missing, or incorrectly listed libraries
- **GeoJSON import** — Admins can bulk-import libraries from GeoJSON files (e.g., Overpass Turbo exports) with duplicate detection
- **Duplicate detection** — Find and merge duplicate libraries by normalized address and geographic proximity
- **User authentication** — Register with username/email or sign in with Google OAuth
- **Admin dashboard** — Moderation dashboard with queue counts, plus approve/reject workflows for submissions, photos, and reports
- **Email notifications** — Admin email alerts for new submissions via Resend
- **Social media posting** — Approved libraries are automatically posted to Mastodon and Bluesky every 2 days
- **Internationalization** — English and Italian with a language switcher; all user-facing strings are translatable
- **SEO** — Sitemap, robots.txt, Open Graph tags on detail pages
- **REST API** — JWT-authenticated API for all core operations (list, search, submit, report)

## Tech stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.14, Django 6, Django Ninja |
| Database | PostgreSQL 17 + PostGIS |
| Frontend | Django templates, HTMX, TailwindCSS 4 + daisyUI 5 |
| Maps | Leaflet + OpenStreetMap tiles |
| Geocoding | Nominatim (reverse) + Photon (autocomplete) |
| Auth | Session-based (web) + JWT (API), Google OAuth via django-allauth |
| Email | Resend (transactional admin notifications) |
| Social | Mastodon + Bluesky automated posting |
| i18n | Django i18n (English + Italian) |
| Error tracking | Sentry |
| Static files | WhiteNoise |
| Monitoring | UptimeRobot + Sentry |
| Deployment | Dokku on Hetzner VPS, CI/CD via GitHub Actions |

## Local development

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose
- [Node.js 22+](https://nodejs.org/) (for Tailwind CSS builds)
- [Python 3.14](https://www.python.org/downloads/)
- [uv](https://docs.astral.sh/uv/) (Python package installer)

### Quick start with Docker

The fastest way to get everything running:

```bash
# Clone the repository
git clone https://github.com/andreagrandi/book-corners.git
cd book-corners

# Copy environment template
cp .env.example .env

# Start the full stack (app + PostGIS + Tailwind watcher)
docker compose up app db tailwind
```

The app will be available at [http://localhost:8000](http://localhost:8000).

### Running without Docker (macOS)

If you prefer running Django directly on your machine, you still need Docker for PostGIS:

```bash
# Install GIS libraries via Homebrew
brew install gdal geos proj

# Start only the database
docker compose up db -d

# Set up direnv (loads DATABASE_URL and GIS library paths from .envrc)
brew install direnv
eval "$(direnv hook zsh)"  # or bash
direnv allow

# Create a virtual environment and install dependencies
python -m venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt

# Install frontend dependencies and build CSS
npm ci
npm run build:css

# Apply migrations and start the dev server
python manage.py migrate
python manage.py runserver
```

In a separate terminal, start the Tailwind watcher for live CSS rebuilds:

```bash
npm run watch:css
```

### Seed demo data

Populate the database with sample libraries for development:

```bash
# Local runtime
python manage.py seed_libraries --reset --count 36 --images-dir libraries_examples --seed 42

# Docker runtime
docker compose exec app python manage.py seed_libraries --reset --count 36 --images-dir libraries_examples --seed 42
```

- `--reset` deletes existing `Report` and `Library` rows first
- `--count` controls how many libraries are generated
- `--images-dir` points to local seed images (images are reused automatically)
- `--seed` makes generated data deterministic

If no images are found in the selected directory, the command generates placeholder images.

The `libraries_examples/` directory is gitignored — each developer can use their own photos.

### GeoJSON import (admin)

Admins can bulk-import libraries from GeoJSON files (e.g., Overpass Turbo exports) via the Django admin interface. The import runs as a background task with automatic duplicate detection based on normalized addresses and geographic proximity.

### Social media posting

Approved libraries with photos are automatically posted to Mastodon and Bluesky every 2 days via Dokku cron. Each library is posted once.

To manually trigger a post (useful for seeding profiles on the first day):

```bash
# Preview what would be posted (no credentials needed)
python manage.py post_random_library --dry-run

# Post one library
python manage.py post_random_library

# On the VPS via Dokku
dokku run book-corners python manage.py post_random_library
```

Run the command multiple times to post several libraries in one session. Each invocation picks a different unposted library.

If credentials for a platform are missing, that platform is silently skipped.

### Translations

The project supports English and Italian. To update translations after changing user-facing strings:

```bash
python manage.py makemessages -l it --no-wrap
# Edit locale/it/LC_MESSAGES/django.po
python manage.py compilemessages
```

### Create a superuser

```bash
python manage.py createsuperuser
```

Access the admin at [http://localhost:8000/admin/](http://localhost:8000/admin/).

### Google OAuth (optional)

To enable "Continue with Google" locally:

1. Create a project in [Google Cloud Console](https://console.cloud.google.com/)
2. Configure the OAuth consent screen (External, Testing mode)
3. Create an OAuth 2.0 Client ID (Web application)
4. Add `http://localhost:8000` as an authorized origin
5. Add `http://localhost:8000/accounts/google/login/callback/` as a redirect URI
6. Set `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` in your `.env`

The Google sign-in button only appears when both variables are set.

## Running tests

Tests require a running PostGIS database:

```bash
# Start the database
docker compose up db -d

# Run the full test suite
nox -s tests

# Run a specific test
nox -s tests -- libraries/tests.py::TestLibraryModel::test_create_library_with_all_fields

# Run with verbose output
nox -s tests -- -v
```

## REST API

The API is available at `/api/v1/` with interactive documentation at `/api/v1/docs`.

Key endpoints:

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/v1/auth/register` | POST | No | Create account, returns JWT tokens |
| `/api/v1/auth/login` | POST | No | Authenticate, returns JWT tokens |
| `/api/v1/auth/refresh` | POST | No | Refresh an access token |
| `/api/v1/auth/me` | GET | JWT | Current user profile |
| `/api/v1/libraries` | GET | No | List and search libraries |
| `/api/v1/libraries/latest` | GET | No | Most recent approved libraries |
| `/api/v1/libraries/{slug}` | GET | No | Library detail |
| `/api/v1/libraries` | POST | JWT | Submit a new library |
| `/api/v1/libraries/{slug}/report` | POST | JWT | Report an issue |

Full API documentation: [developers.bookcorners.org](https://developers.bookcorners.org)

## Project structure

```
book-corners/
├── assets/              # Frontend source (Tailwind CSS input)
├── config/              # Django project settings, URLs, API config
├── libraries/           # Core app: Library, Report, LibraryPhoto, SocialPost models, views, API
├── users/               # Custom User model, auth views, JWT endpoints
├── templates/           # Django HTML templates
├── locale/              # Translation files (Italian .po/.mo)
├── static/              # Generated CSS (gitignored)
├── media/               # User uploads (gitignored)
├── docs/                # API documentation source (Zensical/MkDocs)
├── scripts/             # Operational scripts (backup, restore)
├── docker-compose.yml   # Local development services
├── Dockerfile           # Multi-stage production build
├── Procfile             # Dokku process definition
├── app.json             # Dokku deployment hooks + cron jobs
├── noxfile.py           # Test session configuration
├── requirements.txt     # Python dependencies
└── package.json         # Node.js / Tailwind dependencies
```

## API documentation

The developer documentation is built with [Zensical](https://zensical.com/) and deployed to GitHub Pages.

```bash
# Build and preview docs locally
python manage.py export_openapi_schema > docs/openapi.json
zensical serve
```

The CI pipeline automatically regenerates the OpenAPI schema and deploys docs on every push to master that touches API or docs files.

## Contributing

Contributions are welcome. Here is how to get started:

1. Fork the repository and clone your fork
2. Follow the [local development](#local-development) instructions above
3. Create a feature branch from `master`
4. Make your changes and add tests where appropriate
5. Run the test suite to make sure everything passes: `nox -s tests`
6. Open a pull request against `master`

### Code style

- Python: double quotes, type hints, descriptive test names
- Every function and test should have a two-line docstring (summary + intent)
- End files with a blank line, no trailing whitespace

### CI pipeline

Pull requests automatically run:
- The full test suite against PostGIS
- Tailwind CSS build verification

Pushes to `master` that pass CI are automatically deployed to production.

## Hosting and deployment

- **[HOSTING.md](HOSTING.md)** — Infrastructure overview, VPS setup, DNS, SSL, and environment configuration
- **[DEPLOYMENT.md](DEPLOYMENT.md)** — Deploy process, CI/CD pipeline, rollback procedures, and backups

## License

This project is licensed under the [MIT License](LICENSE).
