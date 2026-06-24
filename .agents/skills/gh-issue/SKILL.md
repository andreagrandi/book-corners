---
name: gh-issue
description: >-
  Create a GitHub issue/ticket for the Book Corners backend/web repo. Use
  whenever the user wants to file a bug, create a ticket, open an issue, report
  a problem, or request a feature/enhancement/documentation change for
  andreagrandi/book-corners.
---

# Create a GitHub issue for book-corners

Use this workflow to file issues on `andreagrandi/book-corners` and add them to
the **Book Corners** project board:
`https://github.com/users/andreagrandi/projects/2`.

Draft first. Issue creation is outward-facing: show the title, body, labels,
priority, and area, let the user edit, and only run `gh issue create` after the
user approves. If the description is too thin for a clear Problem and at least
one Acceptance Criterion, ask 1-2 concise questions first. If priority or area
is not stated, ask before creating the issue.

## Classify

Apply exactly two labels:

- Repo label: always `book-corners`.
- Type label: exactly one of `bug`, `enhancement`, or `documentation`.

Do not invent labels. Area belongs in the project Area field, not labels. Other
repo labels such as `dependencies`, `github_actions`, `python`, `docker`, or
`javascript` are not used for new work-item creation unless the user explicitly
requests them.

Verified label IDs are reference-only; pass label names to `gh issue create`.

| Label | REST ID | Node ID |
|-------|---------|---------|
| `book-corners` | `10998968609` | `LA_kwDORXKGvM8AAAACj5bxIQ` |
| `bug` | `10274894877` | `LA_kwDORXKGvM8AAAACZG50HQ` |
| `documentation` | `10274894883` | `LA_kwDORXKGvM8AAAACZG50Iw` |
| `enhancement` | `10274894891` | `LA_kwDORXKGvM8AAAACZG50Kw` |

Priority defaults are not allowed in this repo workflow. Ask the user if they
did not state priority.

- High: production outage, data loss, security/privacy issue, auth is broken,
  core API flows are blocked, map/search submissions are blocked, notification
  delivery is broken, or `master` CI is red.
- Medium: important user-facing bug or feature with a workaround, single-area
  degradation, or release-blocking polish that does not stop core use.
- Low: cosmetic polish, docs, refactor/tech debt, minor developer experience, or
  nice-to-have enhancement.

Area is a required project field. Ask the user if it is not stated or inferable.

- API: Django Ninja endpoints, schemas, serializers, auth/session behavior, API
  docs, integrations used by clients.
- Admin: Django admin, moderation, staff operations, manage interfaces.
- Search: search parameters, ranking, filtering, indexing, search docs.
- Map: GeoJSON, coordinates, map data endpoints, clustering/fallback behavior.
- Notifications: APNs, email/social notification tasks, device tokens.
- Operations: deployment, hosting, environment config, backups, monitoring, CI,
  project tooling.
- Testing: unit, browser, integration, fixtures, mocks, test infrastructure.
- UX: public web templates, user dashboard, forms, copy, accessibility, empty or
  error states.

## Body Template

Use this template unless the user provides a better structure:

```markdown
## Problem
<What's wrong or missing, and where in the backend/web app>

## Proposed change
<What should happen instead>

## Acceptance Criteria
- [ ] <Done condition 1>
- [ ] <Done condition 2>
```

Title: short, imperative or problem-focused, with no component prefix.

## File It After Approval

Optional duplicate check:

```sh
gh issue list --repo andreagrandi/book-corners --search "<keywords>" --state open
```

Create the issue:

```sh
URL=$(gh issue create --repo andreagrandi/book-corners \
  --title "<title>" \
  --body "<body>" \
  --label "book-corners" \
  --label "<bug|enhancement|documentation>")
```

Add it to the Book Corners project and capture the item ID:

```sh
ITEM_ID=$(gh project item-add 2 --owner andreagrandi \
  --url "$URL" --format json --jq .id)
```

Project ID: `PVT_kwHOAAm1584BYNOT`

Set Project to `book-corners`:

```sh
gh project item-edit --id "$ITEM_ID" --project-id PVT_kwHOAAm1584BYNOT \
  --field-id PVTSSF_lAHOAAm1584BYNOTzhTUrB4 \
  --single-select-option-id 1e714f28
```

Set Priority:

- High: `b925d2e0`
- Medium: `23f4e2d2`
- Low: `89b1cb1e`

```sh
gh project item-edit --id "$ITEM_ID" --project-id PVT_kwHOAAm1584BYNOT \
  --field-id PVTSSF_lAHOAAm1584BYNOTzhTUrCA \
  --single-select-option-id <priority-option-id>
```

Set Area:

- API: `c4e6b87d`
- Admin: `ba5fc051`
- Search: `82f936e5`
- Map: `97b54a1b`
- Notifications: `4ec3ad2e`
- Operations: `144b587b`
- Testing: `3aa57aae`
- UX: `9574e84e`

```sh
gh project item-edit --id "$ITEM_ID" --project-id PVT_kwHOAAm1584BYNOT \
  --field-id PVTSSF_lAHOAAm1584BYNOTzhTUrB8 \
  --single-select-option-id <area-option-id>
```

Set Status to Todo:

```sh
gh project item-edit --id "$ITEM_ID" --project-id PVT_kwHOAAm1584BYNOT \
  --field-id PVTSSF_lAHOAAm1584BYNOTzhTUq48 \
  --single-select-option-id f75ad846
```

Report the issue URL and the selected type, priority, area, and project field.

## Link Dependencies

Only do this when the user explicitly says the issue is blocked by or blocking
another issue. `gh` has no native issue-dependency command, so use the REST API.
The body uses the other issue's database `id`, not its `#number`.

```sh
# This issue <N> is blocked by <BLOCKER>.
BLOCKER_ID=$(gh api repos/andreagrandi/book-corners/issues/<BLOCKER> --jq .id)
gh api --method POST -H "X-GitHub-Api-Version: 2026-03-10" \
  repos/andreagrandi/book-corners/issues/<N>/dependencies/blocked_by \
  -F issue_id="$BLOCKER_ID"
```

List or remove blocked-by relationships:

```sh
gh api repos/andreagrandi/book-corners/issues/<N>/dependencies/blocked_by \
  -H "X-GitHub-Api-Version: 2026-03-10" --jq '.[] | "#\(.number) \(.title)"'

gh api --method DELETE -H "X-GitHub-Api-Version: 2026-03-10" \
  repos/andreagrandi/book-corners/issues/<N>/dependencies/blocked_by/<BLOCKER_ID>
```

## Notes

- Project fields were verified with `gh project field-list 2 --owner andreagrandi`.
- If project commands fail because of missing scopes, run `gh auth refresh -s project`.
- Never create the GitHub issue without adding it to the Book Corners project and
  setting Project, Priority, Area, and Status.
