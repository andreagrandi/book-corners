# Hosting

This document covers the infrastructure that runs Book Corners in production.

## Architecture overview

Book Corners runs on a single Hetzner VPS managed by [Dokku](https://dokku.com/), a self-hosted PaaS that provides Heroku-like deployments using Docker containers.

```
┌─────────────────────────────────────────────────────┐
│  Hetzner VPS (ARM64)                                │
│                                                     │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │  Dokku   │  │  PostgreSQL  │  │    nginx     │  │
│  │  (app)   │──│  + PostGIS   │  │  (reverse    │  │
│  │          │  │              │  │   proxy)     │  │
│  └──────────┘  └──────────────┘  └──────┬───────┘  │
│                                         │          │
└─────────────────────────────────────────┼──────────┘
                                          │
                              ┌───────────┴──────────┐
                              │  Cloudflare (proxy)  │
                              │  bookcorners.org     │
                              └──────────────────────┘
```

**Components on the VPS:**
- **Dokku** — manages the app lifecycle, nginx config, and Docker containers
- **PostgreSQL 17 + PostGIS** — via the Dokku Postgres plugin, using the `imresamu/postgis` image (ARM64-compatible)
- **nginx** — reverse proxy managed by Dokku, terminates connections from Cloudflare
- **Let's Encrypt** — TLS certificates via the Dokku letsencrypt plugin, auto-renewed

**External services:**
- **Cloudflare** — DNS and CDN proxy for `bookcorners.org` and `www.bookcorners.org`
- **GitHub Pages** — hosts the API docs at `developers.bookcorners.org`
- **Sentry** — error tracking (free developer plan)
- **UptimeRobot** — uptime monitoring (free tier), checks `/health/` every 5 minutes
- **Resend** — transactional email for admin notifications (new submissions, reports)
- **Mastodon + Bluesky** — automated social media posting of approved libraries
- **BorgBase** — offsite backup storage (planned)

## VPS setup

### Server requirements

- Ubuntu 22.04 or 24.04
- SSH access via a non-root deploy user with sudo privileges

### Deploy user

A dedicated `deploy` user handles Dokku operations:

```bash
ssh root@vps.bookcorners.org
adduser deploy
usermod -aG sudo deploy

# Copy SSH key
mkdir -p /home/deploy/.ssh
cp /root/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys
chown -R deploy:deploy /home/deploy/.ssh
chmod 700 /home/deploy/.ssh
chmod 600 /home/deploy/.ssh/authorized_keys
```

### Dokku installation

```bash
ssh deploy@vps.bookcorners.org

# Install Dokku (check https://dokku.com/docs/getting-started/installation/ for latest)
wget -NP . https://dokku.com/install/v0.36.6/bootstrap.sh
sudo DOKKU_TAG=v0.36.6 bash bootstrap.sh

# Set the global domain
sudo dokku domains:set-global bookcorners.org

# Add your SSH key for git push access
cat ~/.ssh/authorized_keys | sudo dokku ssh-keys:add admin
```

### App creation

```bash
sudo dokku apps:create book-corners
```

## DNS configuration (Cloudflare)

Three A records, all pointing to the VPS IP:

| Record | Target | Proxy |
|--------|--------|-------|
| `bookcorners.org` | `<VPS_IP>` | Proxied (orange cloud) |
| `www.bookcorners.org` | `<VPS_IP>` | Proxied (orange cloud) |
| `vps.bookcorners.org` | `<VPS_IP>` | DNS only (grey cloud) |

The `vps.` subdomain bypasses Cloudflare and is used for SSH access and git push to Dokku.

**Cloudflare SSL/TLS mode:** Full (Strict) — Cloudflare verifies the Let's Encrypt certificate on the origin server.

## Database

The Dokku Postgres plugin manages the database. The ARM64-compatible PostGIS image is used because the VPS runs on Hetzner Ampere (ARM64).

```bash
# Install the Postgres plugin
sudo dokku plugin:install https://github.com/dokku/dokku-postgres.git postgres

# Create the database with PostGIS support
sudo POSTGRES_IMAGE="imresamu/postgis" POSTGRES_IMAGE_VERSION="17-3.6-bookworm" dokku postgres:create book-corners-db

# Link it to the app (sets DATABASE_URL automatically)
sudo dokku postgres:link book-corners-db book-corners
```

Verify the link:

```bash
sudo dokku config:show book-corners | grep DATABASE_URL
```

## Persistent storage

Dokku containers are ephemeral. Media uploads are stored on a mounted host directory that survives redeployments.

```bash
# Create the host directory
sudo mkdir -p /var/lib/dokku/data/storage/book-corners/media
sudo chown -R 32767:32767 /var/lib/dokku/data/storage/book-corners/media

# Mount it into the container
sudo dokku storage:mount book-corners /var/lib/dokku/data/storage/book-corners/media:/app/media
```

Django's `MEDIA_ROOT` resolves to `/app/media` inside the container, which maps to the persistent host path.

## SSL / TLS

```bash
# Install the letsencrypt plugin
sudo dokku plugin:install https://github.com/dokku/dokku-letsencrypt.git

# Set the notification email
sudo dokku letsencrypt:set book-corners email your-email@example.com

# Enable SSL (app must be running first)
sudo dokku letsencrypt:enable book-corners

# Set up automatic renewal
sudo dokku letsencrypt:cron-job --add
```

After enabling Let's Encrypt, set Cloudflare SSL/TLS mode to **Full (Strict)**.

## Domains

```bash
sudo dokku domains:set book-corners bookcorners.org www.bookcorners.org
```

## Environment variables

All production configuration is set via Dokku environment variables. The app reads these from the environment at runtime — the codebase is identical across environments.

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

Use `--no-restart` before the first deploy to avoid restart errors when no container exists yet.

### Google OAuth (optional)

```bash
sudo dokku config:set --no-restart book-corners \
  GOOGLE_OAUTH_CLIENT_ID="<your-client-id>" \
  GOOGLE_OAUTH_CLIENT_SECRET="<your-client-secret>"
```

Add the production redirect URI in Google Cloud Console:
- Authorized origin: `https://bookcorners.org`
- Redirect URI: `https://bookcorners.org/accounts/google/login/callback/`

### Social media posting (optional)

```bash
sudo dokku config:set book-corners \
  MASTODON_INSTANCE_URL="https://mastodon.social" \
  MASTODON_ACCESS_TOKEN="<your-access-token>" \
  BLUESKY_HANDLE="bookcorners.bsky.social" \
  BLUESKY_APP_PASSWORD="<your-app-password>"
```

Credentials can be added independently — if only one platform is configured, the other is silently skipped.

### Email notifications (optional)

```bash
sudo dokku config:set book-corners \
  RESEND_API_KEY="<your-resend-api-key>" \
  ADMIN_NOTIFICATION_EMAIL="<admin-email-address>"
```

Admin notifications are sent when new libraries are submitted. Falls back to console output if not configured.

### Sentry error tracking (optional)

```bash
sudo dokku config:set book-corners \
  SENTRY_DSN="<your-sentry-dsn>"
```

Sentry is on the free developer plan. Events are dropped when quota is exhausted, not billed.

### Generate a secret key

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(50))"
```

### View current configuration

```bash
sudo dokku config:show book-corners
```

## Key paths on the VPS

| What | Path |
|------|------|
| Dokku app (bare git repo) | `/home/dokku/book-corners/` |
| App source checkout (ops scripts) | `/home/deploy/book-corners/` |
| Backup/restore scripts (symlinks) | `/home/deploy/backup.sh`, `/home/deploy/restore.sh` |
| Media files (mounted into container) | `/var/lib/dokku/data/storage/book-corners/media/` |
| Postgres data (managed by plugin) | `/var/lib/dokku/services/postgres/book-corners-db/` |
| Backup log | `/var/log/book-corners-backup.log` |

### Useful commands

```bash
# View app logs
sudo dokku logs book-corners --tail

# Check app status
sudo dokku ps:report book-corners

# Run a Django management command
sudo dokku run book-corners python manage.py <command>

# Open a Django shell
sudo dokku run book-corners python manage.py shell
```

## Post-deploy setup (one-time)

After the first successful deploy:

```bash
# Create a superuser
sudo dokku run book-corners python manage.py createsuperuser

# Fix the Django Sites framework domain (used by allauth)
sudo dokku run book-corners python manage.py shell -c "
from django.contrib.sites.models import Site
site = Site.objects.get(id=1)
site.domain = 'bookcorners.org'
site.name = 'Book Corners'
site.save()
print(f'Site updated: {site.domain}')
"
```

## Monitoring

- **UptimeRobot** monitors `https://bookcorners.org/health/` every 5 minutes
- **Sentry** error tracking (free developer plan) — reports unhandled exceptions and API 500s

## Cost sheet

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
| Transactional email (Resend free tier) | Yes | $0 | $0 | 3k emails/month free, admin notifications |

\* If you already have BorgBase capacity, incremental cost can be near zero.

### Optional costs (only when needed)

| Item | Est. monthly | Notes |
|------|--------------|-------|
| Transactional email provider upgrade | $0–20 | Only if free tier limits are exceeded or user-facing emails are needed |
| Paid uptime monitoring upgrade | $7–20+ | Only if free tier limits become restrictive |
| Paid error monitoring upgrade | $26+ | Only if free Sentry quotas are consistently exceeded |

### Practical budget ranges

- **Lean MVP baseline:** ~$6–10/month (VPS + domain amortized, free monitoring, existing backup capacity).
- **Safer baseline with paid backup headroom:** ~$10–18/month.
- **With optional paid monitoring/email upgrades:** ~$20–45+/month.

### Cost control rules

- Keep Sentry on free plan and do not enable paid add-ons by default.
- Use free-tier uptime monitoring until false positives or feature limits justify upgrades.
- Keep Resend on free tier; admin notifications are low volume.
- Review storage growth monthly (DB dumps + media) to avoid surprise backup costs.

### Quota behavior notes

- Sentry free plan is quota-based: when quota is exhausted, new events are dropped/rejected.
- Existing accepted events remain available according to plan retention window.
