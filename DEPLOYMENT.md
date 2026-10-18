# BillBuddy Render + Neon preparation

This project is prepared for a Render static frontend, a free Render FastAPI
web service, and a Neon PostgreSQL database. No cloud resources are created by
these instructions. Do not select a paid plan or deploy until approved.

The Render free web service has an ephemeral local filesystem and may sleep
when idle. Bill data is stored in Neon, not on the Render service. The database
schema is created automatically when the API starts. The existing local
`backend/billbuddy.db` is not read, migrated, or modified.

## Environment variables

| Variable | Where | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | Backend only; secret | Neon PostgreSQL connection string. Keep the password private and use Neon's SSL-enabled URL. |
| `BILLBUDDY_API_KEY` | Backend and Bolna secret configuration; secret | Shared API credential for Bolna's protected bill writes, sent as `X-BillBuddy-Token`. Never put it in React or a `VITE_` variable. |
| `BILLBUDDY_DASHBOARD_PASSWORD` | Backend only; secret | Shared dashboard login password. |
| `BILLBUDDY_SESSION_SECRET` | Backend only; secret | Random signing key for the dashboard's HttpOnly session cookie. |
| `BILLBUDDY_FRONTEND_ORIGINS` | Backend | Comma-separated, exact allowed browser origins, without trailing slashes (for example, `https://billbuddy-dashboard.onrender.com`). |
| `BILLBUDDY_COOKIE_SECURE` | Backend | `true` for HTTPS hosting; `false` only for local HTTP development. |
| `BILLBUDDY_COOKIE_SAMESITE` | Backend | `lax` for the default Render `*.onrender.com` hostnames. For a frontend on a different site, `none` requires `BILLBUDDY_COOKIE_SECURE=true`; browser third-party-cookie restrictions can still prevent sessions. |
| `VITE_API_BASE_URL` | Frontend build configuration; public URL, not a secret | HTTPS base URL of the deployed API (for example, `https://billbuddy-api.onrender.com`). Never put a credential here. |

The API key, database URL, dashboard password, and session secret must be
entered only in backend secret configuration. The API URL and frontend origin
are public addresses, not credentials. The browser's localStorage remains a
display cache; it is no longer uploaded to seed PostgreSQL. Starting on Neon
therefore uses an empty database and does not import local SQLite or cached
browser data.

## Local setup (PowerShell)

Install the backend dependencies from the project root:

```powershell
Set-Location E:\BolnaBuddy\backend
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
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
$env:BILLBUDDY_FRONTEND_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"
$env:BILLBUDDY_COOKIE_SECURE = "false"
$env:BILLBUDDY_COOKIE_SAMESITE = "lax"
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

In a second PowerShell window:

```powershell
Set-Location E:\BolnaBuddy\frontend
$env:VITE_API_BASE_URL = "http://localhost:8000"
npm run dev
```

For production, Render injects `VITE_API_BASE_URL` at frontend build time. Set it
to the final HTTPS API URL before the static site build. The frontend refuses
to fall back to HTTP when built for production.

## Manual cloud setup steps (not performed)

1. In Neon, create a project on the free tier and copy its SSL-enabled
   connection string from the **Connect** panel. Keep it private.
2. In Render, create the Blueprint from this repository's `render.yaml`. The
   blueprint describes a Free web service and a static site. Before continuing
   past any resource-creation confirmation, check the selected plan and charges
   shown in Render; stop if anything is paid.
3. When Render requests `sync: false` values, enter the Neon URL and generate
   fresh values locally for the three secrets. The expected hosts from the
   service names are `https://billbuddy-api.onrender.com` and
   `https://billbuddy-dashboard.onrender.com`; use the actual assigned hosts if
   Render assigns different names. Set
   `BILLBUDDY_FRONTEND_ORIGINS` to the exact dashboard origin and
   `VITE_API_BASE_URL` to the HTTPS API base URL. Keep
   `BILLBUDDY_COOKIE_SECURE=true` and `BILLBUDDY_COOKIE_SAMESITE=lax` for
   Render's default `onrender.com` hostnames.
4. If you stop before creating/deploying resources, save these values securely
   and wait for explicit approval. Once approved, the first API startup
   creates an empty `bills` table in Neon; the existing local SQLite database
   is not imported.
5. Verify `https://<api-host>/health`, dashboard login, and protected CRUD
   before configuring Bolna. In Bolna, use the API URL and send
   `X-BillBuddy-Token` through its server-side secret/header configuration.
   Confirm Bolna's body mapping and test an API bill creation before voice
   agent testing.

## Free-tier caveats

- Render's free backend can sleep while idle, so the first request may be slow.
- Neon free-tier limits and availability are controlled by Neon and can change.
- Database credentials, API keys, dashboard passwords, and session secrets
  must never be committed, logged, or exposed in browser code.
- Render's default frontend and API hosts are different origins. CORS therefore
  allows only the configured frontend origin and credentials. Cookie requests
  use `credentials: "include"`; do not use wildcard CORS origins.
- The browser session authenticates the dashboard, while the API key continues
  to authenticate bill reads and writes for Bolna. `/`, `/health`, and the
  dashboard login/session endpoints remain public.
