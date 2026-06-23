# Push Notifications

Book Corners supports iOS push notifications through Apple Push Notification service (APNs). Clients authenticate with the existing JWT flow, then register an APNs device token for the signed-in user.

## Client lifecycle

1. Sign in with `/api/v1/auth/login`, `/api/v1/auth/register`, or `/api/v1/auth/social`.
2. Ask iOS for notification permission.
3. Convert the APNs device token to a lowercase hex string.
4. Register it with `POST /api/v1/auth/devices`.
5. On logout, call `DELETE /api/v1/auth/devices/{token}` before discarding local credentials.

Device tokens can change over time. If iOS returns a new token, register the new token again. The server upserts by token and reassigns it to the current user.

## Environments

Each device token must be registered with the APNs environment that produced it:

| Environment | Use for |
|-------------|---------|
| `sandbox` | Development builds and sandbox APNs tokens |
| `production` | Distributed builds and production APNs tokens |

The server stores the environment per token so sandbox and production devices can coexist safely.

## Notification events

The backend queues push notifications for:

| Event | Recipient |
|-------|-----------|
| Library approved | Submitter |
| Library rejected | Submitter |
| New library submission | Staff users |
| Updated library needing review | Staff users |
| New community photo | Staff users |
| New library report | Staff users |

Push delivery runs in the existing background worker. If APNs reports that a token is invalid or unregistered, the server deletes that token.

## Backend configuration

APNs delivery is disabled unless all required credentials are configured:

| Variable | Description |
|----------|-------------|
| `APNS_AUTH_KEY` | `.p8` private key contents from Apple Developer |
| `APNS_KEY_ID` | 10-character APNs key ID |
| `APNS_TEAM_ID` | 10-character Apple Developer Team ID |
| `APNS_BUNDLE_ID` | iOS app bundle ID used as the APNs topic |
| `APNS_USE_SANDBOX` | Default environment only when no token environment is supplied |

Production deployments should set these with Dokku config:

```bash
dokku config:set book-corners APNS_AUTH_KEY='-----BEGIN PRIVATE KEY-----...'
dokku config:set book-corners APNS_KEY_ID=ABC123DEFG
dokku config:set book-corners APNS_TEAM_ID=DEF123GHIJ
dokku config:set book-corners APNS_BUNDLE_ID=org.bookcorners.app
```

`APNS_AUTH_KEY` can contain either real newlines or escaped `\n` sequences.
