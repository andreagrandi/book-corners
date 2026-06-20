---
name: book-corners-ops-workflow
description: Handle Book Corners deployment, hosting, production logs, infrastructure, and environment configuration. Use for Dokku, VPS, DNS, SSL, Grafana Loki, production debugging, deploys, rollbacks, backups, or operational commands.
---

# Book Corners Ops Workflow

Use this workflow for deployment, hosting, infrastructure, production logging, and environment configuration.

## Reference Docs

Before making assumptions about production, hosting, or operational commands, consult:

- `README.md`: project overview, local setup, management commands, and tech stack.
- `HOSTING.md`: VPS setup, DNS, SSL, environment variables, and external services.
- `DEPLOYMENT.md`: CI/CD, manual deploy, rollback, backups, Dockerfile, and troubleshooting.

## Production Logs

Production logs are shipped to Grafana Cloud Loki via Dokku Vector.

Use `app="book-corners"` as the default stream selector. Legacy examples using `source="book-corners"` may still return older entries, but new queries should use `app`.

Grafana Explore datasource:

```text
grafanacloud-andreagrandi-logs
```

Preferred Grafana UI flow:

1. Open Explore.
2. Select datasource `grafanacloud-andreagrandi-logs`.
3. Keep Builder mode.
4. Add label filter `app = book-corners`.
5. Set time range to Last 24 hours.
6. Run the query.

## LogCLI Queries

Recent app logs:

```bash
logcli query '{app="book-corners"}' --since 24h --limit 50
```

Errors only:

```bash
logcli query '{app="book-corners"} | json | level="error"' --since 24h --limit 50
```

Search for text:

```bash
logcli query '{app="book-corners"} |= "search term"' --since 24h --limit 50
```

Tail logs live:

```bash
logcli query '{app="book-corners"}' --tail
```

Ignore common bot noise:

```bash
logcli query '{app="book-corners"} != "wp-admin/setup-config.php" != "wordpress/wp-admin/setup-config.php"' --since 24h --limit 50
```

LogCLI requires `LOKI_ADDR`, `LOKI_USERNAME`, and `LOKI_PASSWORD`.

## VPS Verification

After Loki setup or reconfiguration, verify Vector health:

```bash
sudo dokku logs:report book-corners
sudo dokku logs:vector-logs 2>&1 | tail -80
```

If logs stop after reconfiguration, rerun `scripts/setup_loki.sh` from `HOSTING.md`. The script validates token auth with a direct Loki push and expects HTTP `204` before applying Dokku config.
