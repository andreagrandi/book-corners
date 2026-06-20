---
name: book-corners-issues
description: Create Book Corners GitHub issues and add them to the Book Corners GitHub Project. Use when the user asks to add a ticket, create an issue, file a bug, or record an enhancement/documentation work item for this repository.
---

# Book Corners Issues

Use this workflow when creating a GitHub issue for `andreagrandi/book-corners`. Creating the issue alone is not enough; it must also be added to the Book Corners project and assigned project fields.

## Create The Issue

Create the issue with the repo label and exactly one type label:

```bash
gh issue create --repo andreagrandi/book-corners \
  --title "<concise title>" \
  --body "<description>" \
  --label "book-corners" \
  --label "<type>"
```

- Always apply the `book-corners` label.
- Pick one type label: `bug`, `enhancement`, or `documentation`.
- Do not invent labels. Area belongs in the project Area field, not labels.

## Add Project Item

Add the issue to the Book Corners project and capture the item ID:

```bash
ITEM_ID=$(gh project item-add 2 --owner andreagrandi \
  --url <issue-url> --format json --jq .id)
```

Project URL: `https://github.com/users/andreagrandi/projects/2`

## Set Project Fields

Project ID: `PVT_kwHOAAm1584BYNOT`

Set the Project field to `book-corners` for every issue from this repo:

```bash
gh project item-edit --id "$ITEM_ID" --project-id PVT_kwHOAAm1584BYNOT \
  --field-id PVTSSF_lAHOAAm1584BYNOTzhTUrB4 \
  --single-select-option-id 1e714f28
```

Set Priority. Ask the user before creating the issue if priority is not stated.

- High: `b925d2e0`
- Medium: `23f4e2d2`
- Low: `89b1cb1e`

```bash
gh project item-edit --id "$ITEM_ID" --project-id PVT_kwHOAAm1584BYNOT \
  --field-id PVTSSF_lAHOAAm1584BYNOTzhTUrCA \
  --single-select-option-id <priority-option-id>
```

Set Area. Ask the user before creating the issue if area is not stated.

- API: `c4e6b87d`
- Admin: `ba5fc051`
- Search: `82f936e5`
- Map: `97b54a1b`
- Notifications: `4ec3ad2e`
- Operations: `144b587b`
- Testing: `3aa57aae`
- UX: `9574e84e`

```bash
gh project item-edit --id "$ITEM_ID" --project-id PVT_kwHOAAm1584BYNOT \
  --field-id PVTSSF_lAHOAAm1584BYNOTzhTUrB8 \
  --single-select-option-id <area-option-id>
```

Set Status to Todo for new issues:

```bash
gh project item-edit --id "$ITEM_ID" --project-id PVT_kwHOAAm1584BYNOT \
  --field-id PVTSSF_lAHOAAm1584BYNOTzhTUq48 \
  --single-select-option-id f75ad846
```

Follow the conventions of existing project issues. Do not invent new labels, project fields, or option IDs.
