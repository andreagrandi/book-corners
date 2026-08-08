---
name: book-corners-sentry-workflow
description: Inspect and diagnose Book Corners Sentry issues with the read-only Sentire CLI. Use when a user provides a Sentry issue or alert URL, asks what happened in production, needs an exception or event investigated, or wants Sentry evidence before a code fix.
---

# Book Corners Sentry Workflow

Use Sentire before browser automation or log-only inference when a Sentry issue URL is available. Sentire is read-only: it retrieves issue and event data but does not resolve, assign, or mutate Sentry issues.

## Preflight

1. Confirm Sentire is installed:

   ```bash
   command -v sentire
   command -v jq
   sentire --version
   ```

2. If it is missing on macOS, install the released CLI:

   ```bash
   brew install andreagrandi/tap/sentire
   ```

3. Confirm the current command schema before choosing fields:

   ```bash
   sentire describe inspect
   ```

4. Use `SENTRY_API_TOKEN` or `~/.config/sentire/config.json` for authentication. The environment variable takes precedence. Never print the environment variable or read the config file into tool output. If the config file exists, keep its permissions at `600`.

Book Corners defines its Sentry token in `.envrc`. Run every authenticated Sentire command through `direnv exec .` so the current repository value is loaded for that subprocess. Do not trust an inherited token merely because `DIRENV_DIR` or `SENTRY_API_TOKEN` is already set; non-interactive tool shells can retain stale values. Do not source `.envrc` directly or print the token while checking it.

Sentire automatically redacts its configured token from normal error output. Do not place tokens in URLs or command arguments, where they cannot be redacted.

Before inspecting an issue, verify that the active credential can access the target organization:

```bash
set -o pipefail
direnv exec . sentire org list-projects <org-slug> |
  jq 'if type == "array" then map({slug,name}) else . end'
```

If this returns HTTP 403 and both authentication methods are configured, retry once with the config-file credential without exposing either token:

```bash
direnv exec . env -u SENTRY_API_TOKEN sentire org list-projects <org-slug> |
  jq 'if type == "array" then map({slug,name}) else . end'
```

If both credentials return 403, stop and ask for a token that can access the target organization with `org:read`, `project:read`, and `event:read`. Do not fall back to logs and claim the Sentry issue itself was inspected.

## Inspect an Issue URL

Keep the user-provided URL quoted. Notification query parameters are accepted; if Sentire rejects them, retry with the canonical `https://<org>.sentry.io/issues/<numeric-id>/` path derived from the same URL.

Start with compact event metadata:

```bash
set -o pipefail
direnv exec . sentire inspect "<sentry-issue-url>" |
  jq 'if type == "object" and has("error") then . else {
    id,eventID,projectID,groupID,title,message,culprit,platform,dateCreated,
    environment,release,tags,exception,errors
  } end'
```

Filter the JSON after Sentire returns it so structured API errors remain visible. Sentire 0.4.0 can emit an empty result when `--fields` removes the `error` and `code` keys from an error response.

Read these fields before fetching heavier payloads:

- `title`, `message`, and `exception`: the immediate failure.
- `culprit` and project-owned stack frames: the likely code boundary.
- `dateCreated`, `environment`, and `release`: when and where it occurred.
- `tags`: job, request, runtime, or deployment context.
- `errors`: Sentry ingestion problems that may make evidence incomplete.

Fetch full debugging data only when the compact response is insufficient:

```bash
direnv exec . sentire inspect "<sentry-issue-url>" |
  jq 'if type == "object" and has("error") then . else {
    id,eventID,entries,breadcrumbs,contexts,request
  } end'
```

`entries`, `breadcrumbs`, `contexts`, and `request` can be large or contain user data. Inspect only the sections needed for diagnosis, do not repeat personal data in reports, and never paste raw payloads into GitHub.

## Check Issue History

The recommended event may differ from the alerting event. When timing, frequency, or event variation matters:

1. Discover the supported fields:

   ```bash
   sentire describe events get-issue
   sentire describe events list-issue
   ```

2. Fetch issue status and occurrence metadata:

   ```bash
   direnv exec . sentire events get-issue <org-slug> <issue-id> \
     --fields id,shortId,title,culprit,firstSeen,lastSeen,count,userCount,status,level,priority,project,permalink
   ```

3. List recent events without fetching every full payload:

   ```bash
   direnv exec . sentire events list-issue <org-slug> <issue-id> \
     --fields id,eventID,dateCreated,message,tags \
     --format ndjson --limit 20
   ```

4. Fetch a specific event when needed:

   ```bash
   direnv exec . sentire events get-issue-event <org-slug> <issue-id> <event-id>
   ```

The event selector also accepts `latest`, `oldest`, and `recommended`.

## Correlate and Diagnose

1. Locate every project-owned frame with `rg` and inspect the surrounding code and tests.
2. Use `book-corners-ops-workflow` and Loki only when Sentry lacks operational context, or to verify whether a caught exception allowed the job or request to continue.
3. Distinguish observed evidence from inference. State the exact exception, failing boundary, impact, recurrence, and whether downstream work completed.
4. If the user requests a fix, follow the repository session-start workflow, make the smallest change, and use `book-corners-local-dev` for targeted verification.
5. Do not claim the production incident is fixed until the change is deployed and recurrence has been checked. A local test or open PR is not a production resolution.

## Failures

Use Sentire's exit code to choose the next step:

- `2`: authentication is missing or invalid. Ask the user to configure or refresh the token without pasting it into chat.
- `3`: Sentry API, timeout, cancellation, or rate-limit failure. Retry once when appropriate, then report the API error.
- `4`: invalid URL, slug, ID, or output format. Validate the canonical issue URL and numeric issue ID.

For current agent-oriented documentation, run `sentire context`. Prefer JSON or NDJSON for machine parsing; table, text, and Markdown are for human display only.
