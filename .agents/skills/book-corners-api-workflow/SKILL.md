---
name: book-corners-api-workflow
description: Update Book Corners API behavior and documentation. Use when changing API endpoints, API schemas, API URL routing, search behavior used by the API, OpenAPI output, or API-facing request/response shapes.
---

# Book Corners API Workflow

Use this workflow for API-facing changes in `libraries/api.py`, `libraries/api_schemas.py`, `libraries/search.py`, URL routing, or related docs.

## Documentation

When any API endpoint, parameter, request schema, response schema, or API search behavior changes:

1. Update the relevant markdown file under `docs/`.
2. Update `docs/changelog.md` with a brief entry under a version heading.
3. Keep docs markdown changes with the code changes.

For list and search changes, the usual docs file is `docs/libraries/list-and-search.md`.

`docs/openapi.json` is generated and gitignored. Do not commit it. The docs CI workflow regenerates it automatically on push.

## Local Docs Commands

Export the generated OpenAPI schema when you need to inspect it locally:

```bash
python manage.py export_openapi_schema > docs/openapi.json
```

Build and preview docs locally:

```bash
zensical serve
zensical build
```

## Website Parity

If an API change mirrors a website feature, coordinate with the website behavior:

- New website filters often need API filter support.
- Removed website form fields may require API schema changes.
- New resource pages may need matching API endpoints.

If the user requests a website-only feature, ask whether API parity is needed before implementing the website change.

## Completion Checks

Before finishing API work:

- Run the relevant API tests.
- Confirm docs markdown reflects the final API behavior.
- Confirm `docs/changelog.md` includes API feature additions, changes, or removals.
- Leave generated `docs/openapi.json` uncommitted.
