# AGENTS.md

This file provides always-on guidance for AI coding agents working in this repository. Task-specific procedures live in project skills so they load only when needed.

## Project Skills

Use these repo skills for detailed workflows:

- `book-corners-issues` — GitHub issue creation and Book Corners project fields.
- `book-corners-ui-workflow` — templates, views, forms, JavaScript, HTMX, static assets, CSS, browser checks, and translations.
- `book-corners-api-workflow` — API endpoints, schemas, search behavior, API docs, and docs changelog.
- `book-corners-ops-workflow` — deployment, hosting, environment config, Dokku, Grafana Loki, and production debugging.
- `book-corners-local-dev` — local services, tests, migrations, CSS, seed data, dev server, and docs commands.
- `book-corners-domain` — Django/PostGIS architecture, models, migrations, indexes, fixtures, seed data, and dependencies.

## Session Start Workflow

Before making code or documentation changes in this repo:

1. Switch back to `master`.
2. Pull the latest changes with `git pull --ff-only`.
3. Create a new branch with a short, descriptive name related to the feature being added or the bug being fixed.
4. Make the requested changes on that branch.

Do not start work from an old feature branch unless the user explicitly asks to continue that branch.

## Pull Requests

- PR descriptions must be reviewer-facing and include `Context`, `What changed`, `Notes / constraints`, and `Testing`.
- Do not use terse `Summary` / `Validation` bodies unless explicitly requested.

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
