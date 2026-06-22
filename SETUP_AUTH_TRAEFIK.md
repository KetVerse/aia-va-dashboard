# Setup — Microsoft login for the dashboard via Traefik + oauth2-proxy

Goal: put **`https://ops.aiaccountant.com`** in front of the dashboard, gated by
**Microsoft 365 (Entra ID) login**, so only **@karboncard.com** accounts get in.
**No app code changes** — a small `oauth2-proxy` container does the login.

This uses the **Traefik that is already running on the VPS** (the same one serving
`n8n.aiaccountant.com`). Confirmed Traefik facts on this box:

- Runs in **host network mode**, Docker provider, `exposedbydefault=false`.
- Entrypoints **`web` (:80)** and **`websecure` (:443)**, with auto HTTP→HTTPS redirect.
- Cert resolver **`letsencrypt`** (HTTP-01 challenge on port 80).

## How it fits together

```
User → Traefik (443, TLS for ops.aiaccountant.com)
          → oauth2-proxy  (asks Microsoft "are you @karboncard.com?")
              → dashboard:8080   (the Taipy app, unchanged)
```

`oauth2-proxy` runs **inside the dashboard's own compose project**, so it reaches the app
over the compose network as `dashboard:8080`. Traefik (host mode) reaches oauth2-proxy by
its container IP — exactly how it already reaches n8n.

The compose changes are **already committed** in [docker-compose.yml](docker-compose.yml)
(new `oauth2-proxy` service + Traefik labels) and [.env.example](.env.example) (the
`OAUTH2_PROXY_*` keys). You only need to fill the real values and redeploy.

---

## Step 1 — DNS

In Cloudflare DNS, make sure there's an **A record**:

```
ops   →   187.127.173.25
```

⚠️ Set it to **DNS only (grey cloud)**, the same as `n8n` — Traefik's Let's Encrypt
HTTP-01 challenge needs to reach port 80 directly. (You already have this record since
`http://ops.aiaccountant.com:8080` loads.)

---

## Step 2 — Register the app in Microsoft Entra (Azure)

**portal.azure.com → Microsoft Entra ID → App registrations → New registration**

- **Name:** `ops.aiaccountant.com dashboard`
- **Supported account types:** Single tenant
- **Redirect URI** (platform **Web**):
  `https://ops.aiaccountant.com/oauth2/callback`  ← note: points at **your domain**, not Cloudflare
- Click **Register**, then copy:
  - **Application (client) ID**
  - **Directory (tenant) ID**
- **Certificates & secrets → New client secret** → copy the **secret VALUE** now (you can't see it later).
- **API permissions → Add → Microsoft Graph → Delegated** → add `openid`, `email`,
  `profile`, `User.Read` → **Grant admin consent**.

---

## Step 3 — Fill in the secrets on the VPS

The dashboard's files are at **`/docker/taipy-dashboard/`** (n8n's are at `/docker/n8n/`).
Confirm with:

```bash
docker inspect aia-dashboard --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
```

Generate a cookie secret:

```bash
openssl rand -base64 32
```

Edit `/docker/taipy-dashboard/.env` and add (using your real values from Step 2):

```
OAUTH2_PROXY_PROVIDER=oidc
OAUTH2_PROXY_OIDC_ISSUER_URL=https://login.microsoftonline.com/<TENANT_ID>/v2.0
OAUTH2_PROXY_CLIENT_ID=<CLIENT_ID>
OAUTH2_PROXY_CLIENT_SECRET=<CLIENT_SECRET_VALUE>
OAUTH2_PROXY_COOKIE_SECRET=<output of openssl rand -base64 32>
OAUTH2_PROXY_EMAIL_DOMAINS=karboncard.com
OAUTH2_PROXY_REDIRECT_URL=https://ops.aiaccountant.com/oauth2/callback
OAUTH2_PROXY_COOKIE_SECURE=true
OAUTH2_PROXY_WHITELIST_DOMAINS=ops.aiaccountant.com
```

`.env` is gitignored — these secrets stay only on the server.

---

## Step 4 — Deploy

Pull the new compose + start oauth2-proxy:

```bash
cd /docker/taipy-dashboard
git pull            # if this dir is the git checkout; otherwise update the file via Hostinger Docker Manager → Compose
docker compose up -d --build
```

Check both containers are up and watch the proxy start cleanly:

```bash
docker ps | grep -E "aia-dashboard|aia-oauth2-proxy"
docker logs aia-oauth2-proxy --tail 30
```

Traefik will request the cert for `ops.aiaccountant.com` automatically on first hit.

---

## Step 5 — Test

- Open `https://ops.aiaccountant.com` in a private window → it should bounce to
  **Microsoft login** → sign in with a `@karboncard.com` account → dashboard loads.
- Confirm the **padlock** (valid cert) and that **filters/charts work** (that proves the
  WebSocket is passing through Traefik + oauth2-proxy).
- Try a non-`@karboncard.com` account → access denied.
- Logout URL: `https://ops.aiaccountant.com/oauth2/sign_out`.

---

## Step 6 — Lock the back door (do this once login works)

Right now `http://ops.aiaccountant.com:8080` and `http://187.127.173.25:8080` still open
the dashboard **with no login**. To close it: in
[docker-compose.yml](docker-compose.yml) delete the `dashboard` service's `ports:` block
(the `- "8080:8080"`), then `docker compose up -d`. Traefik/oauth2-proxy reach the app over
the internal network, so nothing breaks — the only way in becomes the Microsoft login.

---

## Managing who can log in

- **Whole company:** `OAUTH2_PROXY_EMAIL_DOMAINS=karboncard.com` (current). Anyone with a
  `@karboncard.com` Microsoft account gets in.
- **Specific people only / remove someone:** switch to an allowlist file. Add to the
  `oauth2-proxy` `command:` in compose:
  `--authenticated-emails-file=/emails.txt`, mount a file of one email per line, and edit
  it + `docker restart aia-oauth2-proxy` to add/revoke. Or simply disable the person's
  Microsoft account.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Cert not issued / SSL error | Ensure `ops` DNS is **grey cloud (DNS only)** and port 80 is open; check `docker logs traefik-traefik-1`. |
| Login loops or "email domain" error | Azure didn't return an `email` claim. Add `OAUTH2_PROXY_OIDC_EMAIL_CLAIM=preferred_username` to `.env` and restart. |
| `redirect_uri mismatch` | The Azure redirect URI must be **exactly** `https://ops.aiaccountant.com/oauth2/callback`. |
| Charts/filters dead after login | WebSocket issue — confirm `--proxy-websockets=true` is on oauth2-proxy (it is, in compose). |
| 404 / bad gateway | `docker logs aia-oauth2-proxy`; verify the dashboard container is named/reachable as `dashboard` on the compose network. |
