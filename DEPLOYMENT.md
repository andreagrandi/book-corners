# Deployment

This document covers how code gets from a developer's machine to production, including the CI/CD pipeline, manual deployment, rollback procedures, and backups.

## How deployment works

Every push to `master` that passes CI is automatically deployed to production via GitHub Actions. The pipeline:

1. Runs the full test suite against a PostGIS service container
2. Builds Tailwind CSS to verify frontend compilation
3. If tests pass and the push is to `master`, deploys to Dokku via git push

Dokku then:
1. Builds a Docker image using the multi-stage `Dockerfile` (CSS build + Python app)
2. Runs `collectstatic` (inside the Dockerfile)
3. Runs `python manage.py migrate --noinput` (from `app.json` predeploy hook)
4. Starts the new container with gunicorn
5. Runs the health check (`/health/`) before routing traffic
6. Removes the old container

## CI/CD pipeline

### CI workflow (`.github/workflows/ci.yml`)

Runs on every push and pull request.

**Test job:**
- Spins up a PostGIS 17 service container
- Installs Python 3.14 + uv, Node.js 22
- Installs GIS system libraries (gdal, geos, proj)
- Builds Tailwind CSS (`npm ci && npm run build:css`)
- Runs the test suite (`uvx nox -s tests`)

**Deploy job** (master only, after tests pass):
- Checks out the full git history
- Configures SSH with the Dokku deploy key
- Pushes to the Dokku remote

### Docs workflow (`.github/workflows/docs.yml`)

Runs on pushes to master when docs or API files change. Exports the OpenAPI schema from a live Django instance, builds the documentation site with Zensical, and deploys to GitHub Pages at `developers.bookcorners.org`.

### Required GitHub secrets

| Secret | Description |
|--------|-------------|
| `DOKKU_SSH_PRIVATE_KEY` | SSH private key for pushing to Dokku |
| `DOKKU_HOST` | VPS hostname (e.g., `vps.bookcorners.org`) |

## Manual deployment

When CI/CD is unavailable or you need to deploy a hotfix:

```bash
# From your local machine, in the book-corners directory
git push dokku master
```

The Dokku remote should already be configured:

```bash
# If not, add it:
git remote add dokku dokku@vps.bookcorners.org:book-corners
```

### Monitoring the deploy

```bash
# Watch build output in real-time during git push

# After deploy, check status on the VPS:
sudo dokku ps:report book-corners
sudo dokku logs book-corners --tail
```

## Post-deploy smoke checklist

After every deploy, verify these manually or review in monitoring:

- [ ] Homepage loads with styles: `https://bookcorners.org/`
- [ ] Static CSS returns 200: `https://bookcorners.org/static/css/app.css`
- [ ] Login page works: `https://bookcorners.org/login/`
- [ ] Map page loads with markers: `https://bookcorners.org/map/`
- [ ] Admin panel accessible: `https://bookcorners.org/admin/`
- [ ] Health check passes: `https://bookcorners.org/health/`

## Rollback

### Revert to the previous release

If a deploy introduces a bug, revert the commit on `master` and push again:

```bash
git revert HEAD
git push origin master
# CI will run tests and auto-deploy the revert
```

For an immediate rollback without waiting for CI:

```bash
git revert HEAD
git push dokku master
```

### Rebuild from the current deployed code

If the container is misbehaving but the code is correct:

```bash
sudo dokku ps:rebuild book-corners
```

### Deploy a specific commit

```bash
# Push a specific commit to Dokku
git push dokku <commit-sha>:refs/heads/master
```

## Database migrations

Migrations run automatically on every deploy via the `app.json` predeploy hook:

```json
{
  "scripts": {
    "dokku": {
      "predeploy": "python manage.py migrate --noinput"
    }
  }
}
```

If a migration fails, the deploy is aborted and the previous container keeps running.

### Running migrations manually

```bash
sudo dokku run book-corners python manage.py migrate
```

### Checking migration status

```bash
sudo dokku run book-corners python manage.py showmigrations
```

## Backups (planned)

Backup infrastructure is planned using [Borg](https://www.borgbackup.org/) with [BorgBase](https://www.borgbase.com/) as the offsite target.

### What gets backed up

- **Database** — Full PostgreSQL dump via `dokku postgres:export`
- **Media files** — User-uploaded photos from `/var/lib/dokku/data/storage/book-corners/media/`

### Backup scripts

Operational scripts will be versioned in the repo under `scripts/`:
- `scripts/backup.sh` — nightly backup (database dump + media archive)
- `scripts/restore.sh` — selective restore from any archive

### Backup schedule

Nightly at 3 AM server time via cron:

```bash
0 3 * * * /home/deploy/backup.sh >> /var/log/book-corners-backup.log 2>&1
```

### Database restore procedure

```bash
# Extract the dump from a borg archive
export BORG_PASSPHRASE="<your-passphrase>"
borg extract "$BORG_REPO::<archive-name>" tmp/book-corners-backup/db.dump

# Restore to a test database first
sudo dokku postgres:create book-corners-db-test
sudo dokku postgres:import book-corners-db-test < tmp/book-corners-backup/db.dump

# Verify the data
sudo dokku postgres:connect book-corners-db-test
# In psql: SELECT count(*) FROM libraries_library;

# If everything looks good, restore to production
sudo dokku postgres:import book-corners-db < db.dump

# Clean up test database
sudo dokku postgres:destroy book-corners-db-test --force
```

### Media restore procedure

```bash
export BORG_PASSPHRASE="<your-passphrase>"

# List available archives
borg list "$BORG_REPO"

# Extract media files
borg extract "$BORG_REPO::<archive-name>" \
  var/lib/dokku/data/storage/book-corners/media/ \
  --strip-components 7

# Copy restored files to the production media path
sudo cp -r media/* /var/lib/dokku/data/storage/book-corners/media/
sudo chown -R 32767:32767 /var/lib/dokku/data/storage/book-corners/media/
```

## Updating ops scripts on the VPS

The backup and restore scripts are symlinked from a checkout of the repo on the VPS:

```bash
# Update to latest
ssh -t deploy@vps.bookcorners.org "cd ~/book-corners && git pull"
```

## Dockerfile overview

The production image is built in two stages:

**Stage 1 — CSS builder** (node:22-alpine):
- Installs npm dependencies
- Compiles Tailwind CSS to `static/css/app.css`

**Stage 2 — Python app** (python:3.14-slim):
- Installs system dependencies (GDAL, GEOS, Proj, libjpeg, zlib, gettext)
- Installs Python packages via uv
- Copies compiled CSS from stage 1
- Runs `compilemessages` (compiles .po translation files to .mo)
- Runs `collectstatic`
- Serves via gunicorn on `$PORT`

## Health checks

The app exposes a `/health/` endpoint that Dokku checks during deployment. If the health check fails after 3 attempts, the deploy is rolled back automatically.

Configuration in `app.json`:

```json
{
  "healthchecks": {
    "web": [{
      "type": "startup",
      "name": "web check",
      "path": "/health/",
      "attempts": 3,
      "timeout": 5,
      "wait": 5
    }]
  }
}
```

## Troubleshooting

### Deploy fails during build

Check the build output for errors. Common causes:
- PostGIS extension not available — verify the database image
- `collectstatic` fails — check WhiteNoise and STATIC_ROOT configuration
- npm install fails — check `package.json` and `package-lock.json` are committed

### Deploy fails during migration

```bash
# Check migration status
sudo dokku run book-corners python manage.py showmigrations

# View the full error
sudo dokku logs book-corners --tail
```

The old container keeps running if migration fails. Fix the migration and push again.

### App is running but returning errors

```bash
# View recent logs
sudo dokku logs book-corners --tail

# Check the process status
sudo dokku ps:report book-corners

# Check environment variables
sudo dokku config:show book-corners

# Run a management command to debug
sudo dokku run book-corners python manage.py check --deploy
```

### Static files not loading

```bash
# Verify collectstatic ran during build (check deploy output)
# WhiteNoise serves static files — no separate nginx config needed

# Check the static file URL
curl -I https://bookcorners.org/static/css/app.css
```

### SSL certificate issues

```bash
# Check certificate status
sudo dokku letsencrypt:list

# Renew manually if needed
sudo dokku letsencrypt:enable book-corners

# Verify Cloudflare SSL mode is "Full (Strict)"
```
