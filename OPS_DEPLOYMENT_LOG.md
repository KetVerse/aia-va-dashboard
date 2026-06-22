# ops.aiaccountant.com — How We Set It Up (Full Log)

Everything we did to take the dashboard from a raw port on the VPS to a
**Microsoft-login-gated HTTPS site** at `https://ops.aiaccountant.com`.

---

## What We Started With

- VPS at `187.127.173.25` (Hostinger KVM, Ubuntu 24.04, Mumbai)
- **Traefik** already running (host network mode, ports 80 + 443, Let's Encrypt certs)
- Traefik was already serving `n8n.aiaccountant.com` — proven pattern to copy
- Dashboard (`aia-dashboard` container) running on port `8080`, **public**, no login
- Dashboard files at `/opt/taipy-dashboard/` on the VPS

---

## Goal

```
Anyone on the internet
    → https://ops.aiaccountant.com  (Traefik, TLS)
        → Microsoft Login (only @karboncard.com / @korefi.ai)
            → Dashboard (Taipy app on port 8080)
```

No changes to the Taipy app itself. A small extra container (`oauth2-proxy`)
sits between Traefik and the app and handles the login.

---

## Step 1 — DNS Record

In **Cloudflare DNS** for `aiaccountant.com`:

| Type | Name | Value | Proxy |
|------|------|-------|-------|
| A | `ops` | `187.127.173.25` | DNS only (grey cloud) ← important |

> Must be grey cloud. Traefik needs to reach port 80 directly for Let's Encrypt
> HTTP-01 challenge to issue the TLS cert.

---

## Step 2 — Azure App Registration

Portal: **portal.azure.com → Microsoft Entra ID → App registrations → New**

Settings used:
- **Name:** `bot-whatsapp-VA` (reused existing app — can also create new)
- **Supported account types:** Single tenant
- **Redirect URI (Web):** `https://ops.aiaccountant.com/oauth2/callback`

After registering, copied:
- **Application (client) ID** → goes into `.env` as `OAUTH2_PROXY_CLIENT_ID`
- **Directory (tenant) ID** → goes into `.env` as part of `OAUTH2_PROXY_OIDC_ISSUER_URL`

Created a **Client Secret**:
- Certificates & secrets → New client secret → copy the **Value** immediately
- Goes into `.env` as `OAUTH2_PROXY_CLIENT_SECRET`

**API Permissions added (Delegated):**
- `openid`
- `email`
- `offline_access`
- `User.Read`

> If "Grant admin consent" is greyed out: you are a guest in that tenant.
> Ask the tenant admin to grant consent, OR create the app in a tenant where
> you are an admin (e.g. karboncard.com or korefi.ai tenant).

---

## Step 3 — Generate Cookie Secret

Run this on the VPS (not inside any container — on the host):

```bash
python3 -c "import secrets; print(secrets.token_hex(16))"
```

This gives a 32-character hex string (exactly 32 bytes) — the only size
oauth2-proxy accepts for its AES cookie cipher.

> Do NOT use `openssl rand -base64 32` — that gives a 44-character base64
> string which is 44 bytes and oauth2-proxy rejects it with "must be 16/24/32 bytes".

---

## Step 4 — Add Secrets to `.env` on VPS

File: `/opt/taipy-dashboard/.env`

```bash
nano /opt/taipy-dashboard/.env
```

Added these lines at the bottom:

```
OAUTH2_PROXY_PROVIDER=oidc
OAUTH2_PROXY_OIDC_ISSUER_URL=https://login.microsoftonline.com/<TENANT_ID>/v2.0
OAUTH2_PROXY_CLIENT_ID=<CLIENT_ID from Azure>
OAUTH2_PROXY_CLIENT_SECRET=<SECRET VALUE from Azure>
OAUTH2_PROXY_COOKIE_SECRET=<output of the python3 command above>
OAUTH2_PROXY_EMAIL_DOMAINS=karboncard.com,korefi.ai
OAUTH2_PROXY_REDIRECT_URL=https://ops.aiaccountant.com/oauth2/callback
OAUTH2_PROXY_COOKIE_SECURE=true
OAUTH2_PROXY_WHITELIST_DOMAINS=ops.aiaccountant.com
```

> `.env` is gitignored — these secrets live only on the server, never in GitHub.

---

## Step 5 — docker-compose.yml Changes

Three things changed in `docker-compose.yml`:

### 5a — Close the back door (port 8080)

Before (anyone could hit the dashboard directly):
```yaml
ports:
  - "8080:8080"
```

After (only local traffic — Traefik/oauth2-proxy can reach it, internet cannot):
```yaml
ports:
  - "127.0.0.1:8080:8080"
```

### 5b — Add the oauth2-proxy service

```yaml
oauth2-proxy:
  image: quay.io/oauth2-proxy/oauth2-proxy:latest
  container_name: aia-oauth2-proxy
  depends_on:
    - dashboard
  env_file:
    - .env
  command:
    - --upstream=http://dashboard:8080
    - --http-address=0.0.0.0:4180
    - --reverse-proxy=true
    - --proxy-websockets=true
    - --skip-provider-button=true
  restart: unless-stopped
  labels:
    - "traefik.enable=true"
    - "traefik.http.routers.ops.rule=Host(`ops.aiaccountant.com`)"
    - "traefik.http.routers.ops.entrypoints=websecure"
    - "traefik.http.routers.ops.tls.certresolver=letsencrypt"
    - "traefik.http.services.ops.loadbalancer.server.port=4180"
```

Key points:
- `--upstream=http://dashboard:8080` — proxies to the dashboard container by name
- `--proxy-websockets=true` — required for Taipy's live filters/charts to work
- Traefik labels — same pattern as n8n, tells Traefik to route `ops.aiaccountant.com` here
- No shared network needed — Traefik is in host network mode and reads Docker socket

### 5c — Network declaration

```yaml
networks:
  default:
    name: taipy-dashboard_default
```

---

## Step 6 — Deploy

On the VPS host terminal (not inside any container):

```bash
cd /opt/taipy-dashboard
git pull
docker compose up --build -d
```

Check both containers are running:
```bash
docker ps | grep -E "aia-dashboard|aia-oauth2-proxy"
```

Watch oauth2-proxy start cleanly:
```bash
docker logs aia-oauth2-proxy --tail 30
```

You should see OIDC discovery logs like:
```
provider_oidc: oidc provider configuration loaded
```

---

## Step 7 — Verify TLS Certificate

Traefik auto-requests the cert on first HTTPS hit. To confirm it was issued:

```bash
docker exec traefik-traefik-1 cat /letsencrypt/acme.json | \
  python3 -c "import sys,json; d=json.load(sys.stdin); \
  [print(c['domain']['main']) for e in d.values() \
   for c in e.get('Certificates',[])]" | grep ops
```

Should print: `ops.aiaccountant.com`

---

## Step 8 — Test Login

1. Open `https://ops.aiaccountant.com` in a private/incognito window
2. Should redirect to Microsoft login page
3. Sign in with `@karboncard.com` or `@korefi.ai` account
4. Dashboard loads with full functionality (filters, charts, WebSocket all working)

If you get an "email domain" error from oauth2-proxy:
```bash
# Add this to .env and restart
OAUTH2_PROXY_OIDC_EMAIL_CLAIM=preferred_username
docker restart aia-oauth2-proxy
```

Logout URL: `https://ops.aiaccountant.com/oauth2/sign_out`

---

## Day-to-Day Operations

### Check who has logged in
```bash
docker logs aia-oauth2-proxy | grep "Authenticated"
```

### Watch logins live
```bash
docker logs aia-oauth2-proxy -f | grep "Authenticated"
```

### Add or remove allowed email domains
Edit `.env` on VPS:
```
OAUTH2_PROXY_EMAIL_DOMAINS=karboncard.com,korefi.ai
```
Then:
```bash
docker restart aia-oauth2-proxy
```
No rebuild needed.

### Deploy a code update
```bash
cd /opt/taipy-dashboard
git pull
docker compose up --build -d
```

### Restart just the app (no code change)
```bash
docker restart aia-dashboard
```

### Restart just the login proxy
```bash
docker restart aia-oauth2-proxy
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Redirect URI mismatch error | Azure app must have exactly `https://ops.aiaccountant.com/oauth2/callback` |
| Cookie secret error in logs | Must be exactly 32 hex chars — use `python3 -c "import secrets; print(secrets.token_hex(16))"` |
| Dashboard loads but filters/charts are dead | WebSocket issue — confirm `--proxy-websockets=true` is in oauth2-proxy command |
| 502 Bad Gateway | Dashboard container not running — `docker ps`, then `docker start aia-dashboard` |
| TLS cert not issued | Ensure DNS is grey cloud (not proxied), port 80 is open, check `docker logs traefik-traefik-1` |
| git pull fails with "local changes" | `git checkout <filename> && git pull` — discards VPS-only edits |
| "Grant admin consent" greyed out | You're a guest in that tenant. Ask tenant admin or use a tenant where you're an admin |

---

## Container Map

| Container | Purpose | Port |
|---|---|---|
| `traefik-traefik-1` | Reverse proxy + TLS | 80, 443 (host) |
| `aia-oauth2-proxy` | Microsoft login gate | 4180 (internal) |
| `aia-dashboard` | Taipy dashboard app | 8080 (localhost only) |

Traffic flow: `Internet → Traefik:443 → oauth2-proxy:4180 → dashboard:8080`
