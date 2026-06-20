---
name: book-corners-ui-workflow
description: Handle Book Corners website UI work and validation. Use when changing Django templates, views, forms, JavaScript, HTMX interactions, static assets, CSS/Tailwind, browser behavior, or user-facing website text.
---

# Book Corners UI Workflow

Use this workflow for website-facing changes. Keep source changes in templates, `assets/`, scripts, views, or forms; `static/css/app.css` is generated and gitignored.

## Feature Parity

When adding or removing a user-facing website feature, ask whether the same change should also be reflected in the API.

Examples:

- Adding a new listing filter: ask if the API list endpoint should support the same filter.
- Removing a form field: ask if the corresponding API input schema should drop the field.
- Adding a resource page: ask if a matching API endpoint is needed.

## Translations

Every user-facing string must be translatable.

1. Wrap template strings with `{% trans %}`.
2. Wrap Python strings with `gettext` or `gettext_lazy`.
3. For inline JavaScript strings inside Django templates, use `{% trans %}` directly in the string literal.
4. Add or update the matching `msgid` and `msgstr` in `locale/it/LC_MESSAGES/django.po`.
5. Run `python manage.py makemessages -l it --no-wrap`.
6. Run `python manage.py compilemessages`.
7. Avoid `%(name)s` placeholders for JavaScript-only variables. Use `{name}` placeholders and JavaScript `.replace("{name}", value)` instead.

## E2E Tests

After changes that touch templates, views, JavaScript, HTMX interactions, URL routing, or static assets, run:

```bash
nox -s e2e
```

The E2E tests require PostGIS running and CSS built:

```bash
docker compose up db -d
npm run build:css
```

The suite covers homepage HTMX loading and pagination, map page Leaflet initialization and GeoJSON fetches, submit form autocomplete/geocoding/submission, library detail report/photo interactions, and statistics Chart.js rendering. External geocoding and map tile APIs are mocked by the browser tests.

Add E2E coverage in `tests/e2e/` when adding new pages or JavaScript interactions.

## Docker Smoke Test

After UI/template/static changes, validate real rendering before finishing.

Apply migrations first if model changes were made:

```bash
python manage.py migrate
```

For Docker runtime:

```bash
docker compose exec app python manage.py migrate
```

Start the full stack and rebuild the app image when needed:

```bash
docker compose up -d --build app db tailwind
docker compose ps
```

Verify homepage and compiled CSS responses:

```bash
curl -I http://localhost:8000/
curl -I http://localhost:8000/static/css/app.css
```

Both should return `200 OK`.

Inspect logs:

```bash
docker compose logs --no-color --tail=120 app tailwind
```

Expected:

- `tailwind` stays running in watch mode.
- App logs do not repeatedly show `Not Found: /static/css/app.css`.

Validate real rendering with Playwright or the in-app browser:

- Open `http://localhost:8000/`.
- Capture a snapshot or screenshot.
- Check browser console errors.
- Check network requests, including static assets.

Treat unstyled pages as blockers.

## CSS Recovery

If a page renders without styles:

1. Rebuild CSS in the running stack:

   ```bash
   docker compose up -d app db tailwind
   docker compose exec tailwind npm run build:css
   ```

2. Verify the stylesheet endpoint:

   ```bash
   curl -I http://localhost:8000/static/css/app.css
   ```

   Expected: `200 OK` and non-trivial `Content-Length`.

3. Hard refresh the browser with `Cmd+Shift+R`.
4. If still broken, restart and inspect logs:

   ```bash
   docker compose restart app tailwind
   docker compose logs --no-color --tail=120 app tailwind
   ```

5. Treat repeated `Not Found: /static/css/app.css` or `500` responses for CSS as blockers.
