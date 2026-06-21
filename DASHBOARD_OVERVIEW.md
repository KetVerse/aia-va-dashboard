# AiA + VA Dashboard — How It Works (Plain-English Overview)

A guide to what this project is, how it's built, and how it runs — written so both a
non-technical reader and an IT engineer can follow it.

---

## 1. What is this, in one paragraph?

It's an **internal web dashboard** that shows business metrics for **AiA** and **VA**
(leads, demos, revenue, renewals, churn, marketing spend, customer health, etc.).
Instead of someone manually pulling numbers into Power BI/Excel, this app **reads the
data straight from our databases, does all the calculations, and draws the charts and
tables** in a web page that anyone with the link can open in a browser.

It is written in **Python**. We did **not** hand-build any website HTML/JavaScript —
a Python toolkit called **Taipy** turns our Python code into the actual web page.

---

## 2. The big picture (how the pieces fit)

```
        OUR DATABASES                 OUR APP (one Python program)              THE USER
   ┌────────────────────┐        ┌──────────────────────────────────┐
   │  Neon (Postgres)   │        │  main.py  (the brain)            │
   │  - AiA deals       │──────▶ │   • pulls data                   │
   │  - VA deals        │  SQL   │   • cleans + calculates (Pandas) │      ┌──────────┐
   │  - line items      │        │   • builds charts (Plotly)       │ ───▶ │ Browser  │
   └────────────────────┘        │   • lays out 5 pages (Taipy)     │ web  │ (Chrome/ │
   ┌────────────────────┐        │                                  │ page │  Edge)   │
   │ Supabase (Postgres)│──────▶ │  grid_server.py (fancy tables)   │      └──────────┘
   │  - marketing spend │  SQL   │  main.css       (styling/look)   │
   │  - product usage   │        └──────────────────────────────────┘
   └────────────────────┘                  runs inside DOCKER
                                            on a HOSTINGER VPS (server)
```

Three layers:
1. **Data** — two cloud Postgres databases (Neon and Supabase) hold the raw records.
2. **App** — one Python program reads that data, calculates everything, and serves a website.
3. **User** — opens a browser, sees the dashboard. All the heavy lifting happens on the server.

---

## 3. The technology, explained simply

| Tool | What it is | Why we use it / analogy |
|---|---|---|
| **Python** | The programming language everything is written in | The language the whole app "speaks" |
| **Taipy** | A Python framework that turns Python into a web UI | The "page builder" — we describe pages in Python, it produces the website. *(Note: Taipy IS the UI layer — it's not "without UI". It generates the UI for us.)* |
| **Flask** | A lightweight web server that Taipy runs on top of | The "front desk" that answers browser requests |
| **Pandas** | A data-crunching library | Excel-on-steroids inside Python — does the sums, groupings, pivots |
| **Plotly** | A charting library | Draws the interactive graphs (funnels, bars, pies) |
| **Postgres** (Neon & Supabase) | Cloud databases | Where the raw business records live |
| **psycopg2** | The connector between Python and Postgres | The "phone line" the app uses to call the databases |
| **Docker** | Packaging/containers | Puts the app + all its tools in one sealed box so it runs the same anywhere |
| **Hostinger VPS** | A rented Linux server | The always-on computer the app lives on |

---

## 4. What the files actually do

```
taipy-dashboard/
├── main.py            ← THE BRAIN. ~2000 lines. Connects to DBs, calculates
│                        everything, defines the 5 pages, runs the app.
├── pages/             ← The layout of each screen (what goes where).
│   ├── aia_ops.py        AIA Ops Dashboard
│   ├── cs_finance.py     CS & Finance
│   ├── marketing.py      AIA Marketing Tracker
│   ├── va_ops.py         VA Ops Dashboard
│   └── va_finance.py     VA Finance Dashboard
├── grid_server.py     ← Builds the fancy data tables (sortable, colour heatmaps,
│                        totals) that the pages embed.
├── main.css           ← The "paint" — colours, fonts, the nav bar, spacing.
├── requirements.txt   ← The shopping list of Python tools to install.
├── Dockerfile         ← The recipe to build the sealed "box" (container).
├── docker-compose.yml ← The instructions to run that box (port, restart rules).
├── update.sh          ← One-command deploy script for the server.
├── .env               ← SECRET credentials (DB passwords). NEVER shared/committed.
└── .env.example       ← A template of .env with the secrets blanked out.
```

**Key idea:** `main.py` is the brain. The `pages/` files are just "where to put things
on each screen." `grid_server.py` and `main.css` make tables and styling look good.

---

## 5. How the data flows (start to screen)

1. **App starts.** `main.py` connects to **Neon** and **Supabase** and runs SQL queries
   to pull all the raw tables (deals, line items, marketing spend, usage, etc.) into
   the server's memory **once**.
2. **It calculates.** Using Pandas, it turns raw rows into the metrics you see — leads,
   demos, revenue, renewals, cohort matrices, customer-health flags, and so on. (This is
   the logic that used to live in Power BI "DAX" formulas, rewritten in Python.)
3. **It draws.** Plotly builds the charts; `grid_server.py` builds the tables.
4. **It serves.** Taipy/Flask sends all this to your browser as a web page.
5. **You filter.** When you change a date or pick a Deal Owner, the app re-runs the
   relevant calculations **for your session only** and updates the screen instantly —
   no page reload.

**Important nuance — where the calculation happens:** everything is computed on the
**server**, per user session. Your browser just displays the result. So opening it on a
weak laptop is fine; the server does the work.

---

## 6. The auto-refresh (why "Refreshed at" shows a certain time)

- The app loads data from the databases at **startup**, and then **automatically every
  30 minutes on the clock** — at **08:00, 08:30, 09:00 … 18:30, 19:00 IST**
  (08:00 is the first refresh of the day, 19:00 the last; nothing overnight).
- The header shows **"Refreshed at: \<time\> IST"** = the last time it actually pulled
  fresh data from the databases.
- **Refreshing your browser does NOT pull new data** — it just re-displays what the
  server already has. New data only arrives on the schedule above (or on restart).

---

## 7. How it's packaged and run (the build)

This is the part your IT colleague will care about most.

**Build (Docker):** The `Dockerfile` is a recipe. It takes a clean Python 3.12 image,
installs the tools from `requirements.txt`, copies our code in, and sets the start
command. The result is a **container image** — a sealed box containing the app and
everything it needs.

**Run (docker-compose):** `docker-compose.yml` runs that box as a container named
**`aia-dashboard`**, maps the server's **port 8080** to the app, feeds in the secrets
from `.env`, and **auto-restarts** it if it crashes or the server reboots.

**Start command inside the box:**
```
taipy run main.py --port 8080 --host 0.0.0.0 --no-reloader
```
That launches the dashboard listening on port 8080 for any incoming browser.

---

## 8. Where it lives and how we update it

- **Code home:** GitHub — `github.com/KetVerse/aia-va-dashboard` (branch `main`).
- **Running home:** a **Hostinger VPS** (Linux server), currently reachable at
  `http://187.127.173.25:8080`.
- **Deploy flow (how a change goes live):**
  1. We edit code locally and `git push` to GitHub.
  2. On the VPS we run `./update.sh`, which does: `git pull` (get new code) →
     `docker compose up -d --build` (rebuild the box and restart it).
  3. The new version is live; users hard-refresh their browser.

The image is **built on the VPS from source** — we are *not* pulling a prebuilt image
from a registry. GitHub is the single source of truth.

---

## 9. Secrets & security (what must stay private)

- The only secrets are the **two database connection strings** (which include
  passwords). They live **only** in the `.env` file on the server.
- `.env` is **gitignored** — it is never uploaded to GitHub and never leaves the server.
- `.env.example` is a safe template (passwords blanked) so others know what keys are
  needed.

---

## 10. What is NOT there yet — and where your colleague comes in

**Right now the dashboard has NO login.** Anyone who knows the address
(`http://187.127.173.25:8080`) can open it. For ~20+ people across the company that's a
problem, so we want to put a **gate** in front of it.

The plan (no change to the app's code needed):

```
   User ──▶  Cloudflare  ──▶  Microsoft 365 login (Entra ID)
                  │              "Are you a @karboncard.com account?"
                  │                        │ yes
                  ▼                        ▼
        dash.aiaccountant.com  ──▶  our app on the VPS (port 8080)
```

> **Note (important for setup):** the **website address** and the **login email domain
> are intentionally different.**
> - **URL:** `dash.aiaccountant.com` — a subdomain of `aiaccountant.com` (the domain we own).
> - **Allowed to log in:** anyone with a `@karboncard.com` Microsoft 365 account.
>
> This is fine — Cloudflare Access checks the person's *email*, not the website's name.

What we want to set up (this is the help to ask for):
1. **The subdomain `dash.aiaccountant.com`** — pointed through **Cloudflare** (ideally via
   a **Cloudflare Tunnel**, so we don't expose the VPS port to the public internet).
2. **Cloudflare Access** in front, using **Microsoft 365 (Entra ID)** as the login —
   so **only `@karboncard.com` accounts** can open the dashboard, and we can **block
   specific people** when needed.
3. This needs a **one-time Microsoft Entra ID (Azure AD) "app registration" + admin
   consent**, so Cloudflare is allowed to use our Microsoft login. That step needs a
   Microsoft 365 / Entra **admin**.

**Why this approach:** the dashboard's own code stays untouched; Cloudflare handles
HTTPS, the Microsoft login, and the allow/block list. It's free for our user count and
is the standard way to put company SSO in front of an internal app.

---

## 11. Mini-glossary

- **VPS** — a rented always-on Linux computer in a data centre (our server).
- **Container / Docker** — a sealed box holding the app + its tools so it runs identically anywhere.
- **Port 8080** — the "door number" on the server that the app listens on.
- **Postgres** — a type of database; both Neon and Supabase are hosted Postgres.
- **SQL** — the language used to ask a database for data.
- **SSO (Single Sign-On)** — logging in with your existing company account (Microsoft 365) instead of a new password.
- **Entra ID / Azure AD** — Microsoft's identity system that backs Office/Microsoft 365 logins.
- **Cloudflare Access / Tunnel** — a service that sits in front of a website to secure it and add login, without exposing the server directly.
- **`.env`** — a file of secret settings (DB passwords) kept off GitHub and only on the server.
```
