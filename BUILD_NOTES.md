# Build Notes — Technical Deep-Dive (for me, the builder)

A candid, technical record of **what I built, how, the clever/hacky bits, what can break,
and how it genuinely differs from Power BI.** This is the "under the hood" companion to
`DASHBOARD_OVERVIEW.md` (which is the layman/IT version).

---

## 1. The stack (what's actually running)

- **Language:** Python 3.12.
- **UI framework:** **Taipy GUI (community/free)** — I describe pages in Taipy's
  Markdown-with-controls syntax (`<|{var}|control|...|>`); Taipy renders a **React SPA**
  and keeps it in sync with my Python variables over a **WebSocket (socket.io)**.
- **Web server:** Taipy runs on **Flask**, served by a **gevent** WSGI server (single
  process, async greenlets). I pass my own Flask app in (`Gui(..., flask=flask_app)`),
  which is how I bolt on custom routes and response hacks.
- **Data crunching:** **Pandas** (+ NumPy).
- **Charts:** **Plotly** (funnels, bars, pies).
- **DB driver:** **psycopg2** to two **Postgres** databases (**Neon** + **Supabase**).
- **Packaging:** **Docker** (single container `aia-dashboard`), **docker-compose** to run it.

**Mental model:** it's a **single-process, in-memory analytics app**. Not a microservice
fleet, not a warehouse query engine. One Python process holds all the data in RAM and
recomputes views on demand.

---

## 2. How the app is wired (the real architecture)

### 2.1 Data lifecycle — load once, recompute per session
- On startup, **`_load_all()`** ([main.py](main.py)) runs ~7 SQL queries and pulls
  **entire tables** into module-level globals: `_RAW_AIA`, `_RAW_VA`, `_RAW_LI`,
  `_RAW_INC` (Neon) and `_RAW_MKT`, `_RAW_UPL`, `_RAW_SYN` (Supabase).
- **`_prep_*()`** functions clean/typecast them into `_AIA`, `_VA`, `_AIA_LI`, etc., and
  build lookup dicts (`_EMAIL_ACCT`, `_ACTIVE_WEEKS`, `_ACCT_DATES`, `_BILLING_END`).
- This raw+prepped data is **shared across all users**. It is **not** re-queried per
  request.
- **`_reload_data()`** rebuilds all of the above; the background thread calls it on the
  refresh schedule.

### 2.2 Reactivity — Taipy `state`
- Each browser session gets its own **`state`** object. Page controls bind to variables
  like `aia_kpi_leads`, `aia_start_date`, `aia_incentive_json`.
- A filter change fires an `on_*_change(state)` callback → I recompute that page's numbers
  from the shared data, **filtered by this session's `state`**, and assign back to
  `state.*`. Taipy diffs and pushes only the changes to that one browser over WebSocket.
- `on_init(state)` runs once per new session and seeds everything via `_refresh_all`.

### 2.3 The custom grid engine (the spicy part) — `grid_server.py`
Taipy's native table couldn't do what I wanted (multi-key sort, sticky Total footer,
per-cell colour heatmaps, per-row colour overrides, in-cell bars). So I built my **own
HTML/JS table** and embed it via an **iframe**:

- `grid_payload_b64(df, ...)` serialises a DataFrame → **base64-encoded JSON**.
- That string is dropped into a **hidden, per-session DOM element** on the Taipy page
  (`<|...|text|mode=raw|>` inside `class=gridholder-<name>`).
- The page embeds `<iframe src="/grid/<name>">`. That iframe (a Flask route in
  `grid_server.py`) is **same-origin**, so its JS reaches into the parent document, reads
  the `gridholder-<name>` element, decodes the JSON, and renders the table. It **polls**
  every second for changes.
- **Why this design:** there is **no server-side per-session data store**. Each browser
  carries its own filtered grid data in its own DOM. Zero cross-session leakage, no
  Redis/session DB. (Documented at the top of `grid_server.py`.)

### 2.4 Pie cross-filtering (hand-rolled)
- Clicking a pie slice (a Plotly iframe at `/pie/<name>`) writes `"<label>||<counter>"`
  into a **hidden Taipy input** (`class=piebridge-<name>`) on the parent page.
- That input is bound to a Taipy variable → triggers `on_*_channel_click(state)` → I apply
  the channel cross-filter and recompute. The **`||counter`** is a trick so the value
  always changes (so `on_change` fires even when you click the same slice — enables
  click-again-to-clear).

### 2.5 Injected JavaScript (no clean Taipy hook, so I inject)
Taipy community gives no first-class way to add `<head>`/`<script>`, so I use a Flask
**`@flask_app.after_request`** to splice two scripts before `</body>` of the HTML:
- **`_ZOOM_LOCK_SCRIPT`** — freezes the header against browser zoom. It reads
  `devicePixelRatio` vs the load-time baseline, applies inverse CSS `zoom`, and measures
  the real content edge to align the bar. Uses `setProperty(..., 'important')` to beat my
  own `!important` CSS rules.
- **`_PAGE_NAV_SCRIPT`** — keyboard nav: `Alt+PgDn/PgUp` and `Alt+1..5`, by clicking the
  matching `.main-nav` link so Taipy's SPA router handles it (no reload).

### 2.6 Auto-refresh thread
- A daemon thread (`_auto_refresh_loop`) sleeps until the next **:00/:30 clock boundary**,
  and within **08:00–19:00 IST** calls `_reload_data()` then
  **`gui.broadcast_callback(_broadcast_refresh)`** to push fresh numbers to **all connected
  sessions** at once.

---

## 3. Nuances & design decisions worth remembering

- **In-memory, single source of truth in RAM.** Fast filtering (no DB round-trips per
  interaction), but the dataset must fit comfortably in container memory.
- **Shared data, per-session filters.** The heavy frames are global; only the *view* is
  per-session. So a filter change is CPU (Pandas recompute), not I/O.
- **Per-session isolation is via the DOM**, not the server (grids/pies). Elegant and
  stateless, but it hard-depends on **same-origin** iframes.
- **IST is computed with a fixed `timezone(+5:30)` offset**, not the container clock — so
  the schedule is correct regardless of the server's timezone. (Good. Don't "fix" it by
  trusting `TZ`.)
- **`use_reloader=False`** and one container → a deploy is a **full rebuild + restart**;
  in-flight sessions drop and data reloads (a few seconds of downtime).
- **DAX → Python** is explicit and version-controlled (see the mapping in `README.md`):
  `USERELATIONSHIP` → date-range filters, `TREATAS` → merges, `DISTINCTCOUNT` →
  `.nunique()`, cohort/pro-rata revenue & retention matrices → pivot tables with manual
  allocation.
- **`DASHBOARD_PASSWORD` in `.env.example` is dead** — not referenced anywhere in code.
  There is currently **no auth** (that's the Cloudflare/Entra job).

---

## 4. What could go wrong (failure modes & fragility)

| Risk | Why | Mitigation / note |
|---|---|---|
| **DB unreachable at startup** | `_load_all()` falls back to **empty DataFrames** → dashboard shows "no data" until restart | Already hit this once. Consider a visible "data unavailable" banner + retry, instead of silent empty frames |
| **Stale data after deploy/restart** | Data only reloads at startup + on the 08–19 schedule; browser refresh ≠ data refresh | Expected behaviour; a manual "Refresh now" button would help |
| **Memory growth** | All tables live in RAM (×prepped copies + lookups) | ~14.5k AIA rows is fine. Watch container RAM as data grows; no pagination/streaming |
| **Single process / CPU-bound recompute** | gevent is one process; a heavy recompute blocks that greenlet; auto-refresh recomputes for *every* connected session | Fine for ~20 users; won't scale to hundreds without rework (workers/caching) |
| **Injected-JS fragility** | Zoom-lock + nav rely on Taipy's HTML (`#root`, `</body>`), the `after_request` hook, and CSS `zoom` (Chromium/Edge). Firefox/Safari may differ | It's a hack. If a Taipy upgrade changes the bundle/markup, re-verify these scripts |
| **Same-origin iframe dependency** | Grids/pies read the parent DOM | A proxy that rewrites origin/headers could break the bridge. **Cloudflare Tunnel keeps it same-origin → OK** |
| **WebSocket required** | Taipy reactivity is socket.io | Any proxy/CDN in front **must pass WebSocket upgrades** (Cloudflare does; verify Access config) |
| **No auth yet** | Public on `http://IP:8080` | Anyone with the IP sees everything. Close this before wider sharing |
| **No row-level security** | Everyone sees all data once they're in | If some users should see only their slice, that's app logic I'd have to build (Power BI RLS has no equivalent here) |
| **`pandas.read_sql` over psycopg2** | Emits a SQLAlchemy warning | Harmless/noisy. Could switch to SQLAlchemy engine to silence |
| **Owner sync edge case** | AIA/VA Ops share Deal Owner with an "All" fallback | If an owner exists on one side only, the other falls back to All by design |

---

## 5. The "crazy" stuff — how this is genuinely different from Power BI

This is the part that's personally interesting: I didn't just rebuild a report, I changed
the **paradigm**.

1. **The logic is real code, in git.** Every metric is a Python/Pandas function I can
   read, diff, code-review, and unit-test. Power BI's DAX lives inside a binary `.pbix`;
   here a PR shows exactly what a number means and when it changed.

2. **No DAX straitjacket.** DAX is a constrained expression language; filter context is
   famously hard. In Python I have **loops, functions, any library** — so things like
   **pro-rata cohort revenue allocation** and **custom CSM health scoring** are just
   ordinary code instead of black-belt DAX.

3. **Pixel-level UI control.** I own the HTML/CSS/JS. That's why I could build a
   **zoom-locked fixed header**, **keyboard navigation**, and **custom heatmap tables with
   per-row colours and sticky totals** — none of which Power BI's fixed visuals allow.

4. **I hand-built cross-filtering.** Power BI cross-filters visuals automatically; I had
   to engineer the **pie-click → hidden-input → callback** bridge myself. More work, but
   I control exactly what filters what.

5. **No license / gateway / capacity.** No Power BI Pro/Premium, no on-prem data gateway,
   no dataset size caps tied to a SKU. It runs on a cheap VPS and talks to Postgres
   directly. The flip side: **I am the managed service** — uptime, refresh, scaling, and
   security are all on me.

6. **Refresh is a thread I wrote**, clock-aligned to :00/:30 — not Power BI's "8 scheduled
   refreshes a day on Pro." I control cadence and business-hours gating exactly.

7. **Multi-user model is inverted.** Power BI Service handles identity, sharing, and RLS
   for me. Here it's **one shared compute** with per-session filters, and identity is
   being bolted on *in front* (Cloudflare + Entra SSO) rather than baked into the data
   layer. There's **no built-in RLS** — everyone authenticated sees everything.

8. **Statelessness via the browser DOM.** The grid/pie isolation trick (base64 JSON in
   per-session DOM, read by same-origin iframes) is a genuinely unusual pattern — it gives
   multi-user safety with **zero server-side session storage**. Power BI never makes me
   think about this; here it's a deliberate architecture choice.

**Net trade-off:** I traded a **managed, governed, but rigid** platform (Power BI) for a
**fully-controllable, code-first, but self-maintained** one. Maximum flexibility and
ownership; in exchange I carry ops, auth, scaling, and reliability myself.

---

## 6. If I revisit this later — likely next steps

- Put **Cloudflare Access + Entra SSO** in front (planned) — closes the no-auth gap.
- Add a **"data unavailable" banner** + retry instead of silent empty frames.
- Optional **"Refresh now"** button for on-demand DB pulls.
- Consider **RLS-style filtering** if some roles should see only their data.
- Watch **container memory**; add pagination/lazy-load if any table gets large.
- Re-verify the **injected JS** after any Taipy version bump.
