# BillBuddy Render + Neon preparation

This project is prepared for one free Render FastAPI web service that builds
and serves the Vite frontend, plus a Neon PostgreSQL database. No cloud
resources are created by these instructions. Do not select a paid plan or
deploy until approved.

The Render free web service has an ephemeral local filesystem and may sleep
when idle. Bill data is stored in Neon, not on the Render service. The database
schema is created automatically when the API starts. The existing local
`backend/billbuddy.db` is not read, migrated, or modified.

## Environment variables

| Variable | Where | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | Backend only; secret | Neon PostgreSQL connection string. Keep the password private and use Neon's SSL-enabled URL. |
| `BILLBUDDY_API_KEY` | Backend and Bolna secret configuration; secret | Shared API credential for Bolna's protected bill writes, sent as `X-BillBuddy-Token`. Never put it in React or a `VITE_` variable. |
| `BOLNA_API_KEY` | Backend only; secret | Permanent Bolna API key used only to mint short-lived browser-call sessions. Never put it in React, a `VITE_` variable, or the response to the browser. |
| `BOLNA_AGENT_ID` | Backend only | ID of the existing BolnaBuddy agent to use for browser calls. Set this to the existing agent ID from the Bolna dashboard; the browser cannot select an agent. |
| `BILLBUDDY_DASHBOARD_PASSWORD` | Backend only; secret | Shared dashboard login password. |
| `BILLBUDDY_SESSION_SECRET` | Backend only; secret | Random signing key for the dashboard's HttpOnly session cookie. |
| `BILLBUDDY_FRONTEND_ORIGINS` | Backend | Comma-separated exact browser origins, without trailing slashes. In production, use the existing backend URL that serves the website and API. |
| `BILLBUDDY_COOKIE_SECURE` | Backend | `true` for HTTPS hosting; `false` only for local HTTP development. |
| `BILLBUDDY_COOKIE_SAMESITE` | Backend | Use `lax` for the single-origin HTTPS deployment. Do not use `none` as a workaround for cross-site hosting; third-party-cookie restrictions can still block it. |

The API keys, database URL, dashboard password, and session secret must be
entered only in backend secret configuration. In particular, `BOLNA_API_KEY`
must never be set as a frontend build variable. The frontend origin is a public
address, not a credential. The browser's localStorage remains a
display cache; it is no longer uploaded to seed PostgreSQL. Starting on Neon
therefore uses an empty database and does not import local SQLite or cached
browser data.

## Production dashboard cookie and CSRF setup

The production Vite build is served by FastAPI from the same Render web
service and origin as the API. Open the existing backend service URL to load
the website. The dashboard's host-only `HttpOnly`, `Secure`, `SameSite=Lax`
cookie is therefore first-party for dashboard requests; no cookie domain
sharing or weaker cookie flags are needed. Do not configure a separate static
site URL as the frontend origin.

Cookie-authenticated state-changing requests require both an exact `Origin`
match against `BILLBUDDY_FRONTEND_ORIGINS` and a session-bound CSRF token sent
in `X-BillBuddy-CSRF`. The dashboard keeps this token in memory and obtains it
from the login or authenticated session response. This protects login, logout,
and cookie-authenticated bill mutations from cross-origin requests; CORS alone
is not a CSRF control. Server-to-server bill requests authenticated with
`BILLBUDDY_API_KEY` continue to use that key and do not depend on a browser
origin or CSRF token.

Voice session creation uses a separate capability. A successful dashboard
password submission returns a cryptographically random, origin-bound
`voice:session` capability valid for 120 seconds and one use. The browser keeps
it only in React memory and sends it as a bearer token to `/voice/session`;
that route accepts no cookies, has no access to bill APIs, and still checks the
exact configured `Origin`, JSON content type, empty request body, and a
rate limit keyed by the backend's observed request peer address. The capability
does not make dashboard cookie authentication or bill operations work
cross-site. The single-origin setup serves the website and API from the same
backend URL, so the browser's dashboard requests use the first-party session
cookie instead of relying on cross-site cookie behavior.

Capabilities and rate-limit counters are held in backend process memory. A
backend restart invalidates outstanding voice capabilities and resets counters.
The current Render start command runs one Uvicorn worker in one service
instance; do not add workers or scale to multiple instances without moving
capability redemption and rate-limit state to a shared store. Behind a reverse
proxy, verify that Uvicorn is configured to trust only the actual proxy before
relying on forwarded client addresses for per-user rate limits; do not trust
arbitrary client-supplied `X-Forwarded-For` values.

## Local setup (PowerShell)

Build the frontend from the repository root so FastAPI can serve its `dist`
directory:

```powershell
Set-Location E:\BolnaBuddy
npm ci --prefix frontend
npm run build --prefix frontend
.\backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

Set these variables in the same PowerShell session that will run Uvicorn. Use a
local PostgreSQL URL or your own Neon development database URL; do not use the
SQLite file. Generate fresh secrets locally and enter the dashboard password
without echoing it:

```powershell
$secureDatabaseUrl = Read-Host "PostgreSQL connection URL" -AsSecureString
$env:DATABASE_URL = (New-Object System.Net.NetworkCredential("", $secureDatabaseUrl)).Password
$env:BILLBUDDY_API_KEY = [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
$env:BILLBUDDY_SESSION_SECRET = [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
$securePassword = Read-Host "Dashboard password" -AsSecureString
$env:BILLBUDDY_DASHBOARD_PASSWORD = (New-Object System.Net.NetworkCredential("", $securePassword)).Password
$env:BILLBUDDY_FRONTEND_ORIGINS = "http://localhost:8000,http://127.0.0.1:8000,http://localhost:5173,http://127.0.0.1:5173"
$env:BILLBUDDY_COOKIE_SECURE = "false"
$env:BILLBUDDY_COOKIE_SAMESITE = "lax"
Remove-Item Env:BOLNA_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:BOLNA_AGENT_ID -ErrorAction SilentlyContinue
.\backend\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000` in a browser to use the built frontend and API
on one origin. `GET /` continues returning its JSON API message to
non-HTML requests.
The development Vite server can also be run in a second PowerShell window:

```powershell
Set-Location E:\BolnaBuddy\frontend
npm run dev
```

The Vite development server uses the local API at port 8000. The production
build uses same-origin API paths and needs no public API URL build variable.
Dashboard login and bill operations can be tested without Bolna credentials.
For an actual voice call, the backend needs `BOLNA_API_KEY` and the existing
`BOLNA_AGENT_ID`; do not supply these to Vite or the frontend build.

## Manual cloud setup steps (not performed)

1. In Neon, create a project on the free tier and copy its SSL-enabled
   connection string from the **Connect** panel. Keep it private.
2. In Render, update the existing `billbuddy-api` service from this repository's
   `render.yaml`. It remains one Free web service; its multi-stage
   [Dockerfile](./Dockerfile) builds the Vite frontend and packages it with
   FastAPI. No static-site service or custom domain is needed. Confirm the
   selected plan and charges in Render before approving any resource changes.
3. Keep the existing backend service URL and set
   `BILLBUDDY_FRONTEND_ORIGINS` to that exact origin (for example,
   `https://billbuddy-api.onrender.com`, with no trailing slash). The frontend
   and API will both be served from this origin.
4. When Render requests `sync: false` values, enter the Neon URL and generate
   fresh values locally for the backend secrets. Also set `BOLNA_API_KEY` to
   the permanent Bolna key and `BOLNA_AGENT_ID` to the ID of the existing
   BolnaBuddy agent. Do not put either value in frontend environment settings.
   `BILLBUDDY_COOKIE_SECURE=true` and
   `BILLBUDDY_COOKIE_SAMESITE=lax`.
5. If you stop before deploying, save any configured values securely
   and wait for explicit approval. Once approved, the first API startup
   creates an empty `bills` table in Neon; the existing local SQLite database
   is not imported.
6. Verify the existing backend URL's `/health`, dashboard login, and protected
   CRUD before configuring Bolna. In Bolna, use the API URL and send
   `X-BillBuddy-Token` through its server-side secret/header configuration.
   Confirm Bolna's body mapping and test an API bill creation before voice
   agent testing.

## Free-tier caveats

- Render's free backend can sleep while idle, so the first request may be slow.
- Neon free-tier limits and availability are controlled by Neon and can change.
- Database credentials, API keys, dashboard passwords, and session secrets
  must never be committed, logged, or exposed in browser code.
- The production website and API are served from one origin. Browser API calls
  continue using `credentials: "include"` and mutation requests continue to
  require the exact configured `Origin` and session-bound CSRF token. Do not
  use wildcard CORS origins or weaken the cookie flags.
- The browser session authenticates the dashboard, while the API key continues
  to authenticate bill reads and writes for Bolna. `/`, `/health`, and the
  dashboard login/session endpoints remain public.
- `POST /voice/session` uses a short-lived, one-time, voice-only bearer
  capability issued after dashboard-password validation. The frontend sends
  it with `credentials: "omit"`; the endpoint forwards a fixed
  `BOLNA_AGENT_ID` to Bolna's `POST /web-call/freeswitch-session` endpoint.
  Bolna's response contains short-lived SIP credentials for that browser call
  only. The dashboard cookie and CSRF authentication still apply to
  bill-management operations. With the single-origin deployment they do not
  depend on cross-site cookies.
- Voice capabilities and rate-limit counters are process-local. A backend
  restart invalidates outstanding capabilities and resets counters; multiple
  workers or instances require shared state. Behind a reverse proxy, verify
  Uvicorn's trusted-proxy configuration before relying on client-address rate
  limits, and do not trust arbitrary client-supplied `X-Forwarded-For` values.
- The voice integration does not modify the Bolna agent or its bill tools.
  Before enabling calls, verify in Bolna that the configured agent ID is the
  existing BillBuddy agent, its bill tools still use the backend's
  `BILLBUDDY_API_KEY`, and its instructions require explicit confirmation
  before deleting bills or changing payment status.
- Removing the static-site definition from the Blueprint does not delete an
  existing Render static site. It remains separate and is no longer managed
  by this Blueprint; use the backend URL for the single-origin app and retire
  the old static site only after verifying the backend-hosted site.
