#!/usr/bin/env python3
"""Daily dashboard PDF snapshot — live-screenshot edition.

Loads each of the 5 dashboard pages in a headless browser with ?snapshot=1
(which forces every grid to full height/width — no scrollbars), prints each as
one tall page, and merges them into a single PDF. Optionally uploads to Google
Drive via a service-account key.

Renders against the dashboard CONTAINER directly (default http://aia-dashboard:8080),
bypassing the Microsoft-login proxy — headless can't do OAuth.

Usage:
    python snapshot_pdf.py [BASE_URL] [OUT_PATH]
        BASE_URL  default http://aia-dashboard:8080  (use http://localhost:8080 to test)
        OUT_PATH  default /tmp/dashboard_<YYYYMMDD_HHMM>_IST.pdf

Env (optional, for Drive upload):
    GDRIVE_FOLDER_ID   target Drive folder
    GDRIVE_KEY_FILE    service-account json (default /opt/taipy-dashboard/gdrive-key.json)
"""
import io, os, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright
from pypdf import PdfReader, PdfWriter

PAGES = [
    ("aia",        "AIA Ops"),
    ("cs",         "CS & Finance"),
    ("marketing",  "Marketing"),
    ("va-ops",     "VA Ops"),
    ("va-finance", "VA Finance"),
]
VIEWPORT_W   = 1680     # design width; PDF widens further if a table needs it
SETTLE_MS    = 9000     # wait for data load + grid expansion after each page loads

_IST = timezone(timedelta(hours=5, minutes=30))

# ── PDF-only row filters ────────────────────────────────────────────────────
# The daily PDF is a review document, so it drops rows nobody acts on: dormant
# customers and deals already parked/churned. These apply to the SNAPSHOT ONLY —
# the live dashboard keeps every row, since it has filter dropdowns for this.
#
# Applied by rewriting the base64 payload in each `.gridholder-<name>` element
# rather than hiding <tr>s: the grid iframe polls that element once a second and
# re-renders whenever it changes, so a DOM edit would be silently reverted while
# a payload edit is picked up as a normal update (and keeps the Total row, the
# row numbering and the frame auto-height all consistent with what's shown).
GRID_FILTERS = {
    # name: (column header, mode, values)   mode: "drop" | "keep"
    "cs_usage": ("Usage Active Days (28d)", "drop", ["0"]),
    "aia_ft":   ("Usage Active Days (28d)", "drop", ["0"]),   # Free Trial Usage & Health
    "vaf_tat":  ("TAT Status",              "drop", ["Parked", "Churned"]),
    "vaf_ar":   ("Due Status",              "drop", ["Churned"]),
}

_FILTER_JS = """
(filters) => {
  const out = [];
  for (const [name, [col, mode, values]] of Object.entries(filters)) {
    const el = document.querySelector('.gridholder-' + name);
    if (!el) continue;               // grid lives on another page — not an error
    const raw = (el.textContent || '').trim();
    if (!raw) { out.push(name + ': empty payload'); continue; }
    let d;
    try { d = JSON.parse(atob(raw)); }
    catch (e) { out.push(name + ': undecodable'); continue; }
    const i = (d.columns || []).indexOf(col);
    if (i < 0) { out.push(name + ': no column "' + col + '"'); continue; }
    const want = values.map(v => String(v).trim().toLowerCase());
    const before = (d.rows || []).length;
    d.rows = (d.rows || []).filter(r => {
      // cells can be plain values or {v: ...} wrappers depending on the column
      let c = r[i];
      if (c && typeof c === 'object') c = ('v' in c) ? c.v : ('t' in c ? c.t : '');
      const s = String(c == null ? '' : c).trim().toLowerCase();
      const hit = want.includes(s) || (want.includes('0') && s !== '' && Number(s) === 0);
      return mode === 'keep' ? hit : !hit;
    });
    // btoa is Latin1-only, and these payloads carry Rs / em-dash / arrows. Python
    // writes them with json.dumps(ensure_ascii=True), i.e. \\uXXXX escapes, so
    // escape the same way on the way back or btoa throws.
    const json = JSON.stringify(d).replace(/[\\u0080-\\uFFFF]/g,
      c => '\\\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'));
    el.textContent = btoa(json);
    out.push(name + ': ' + before + ' -> ' + d.rows.length);
  }
  return out;
}
"""

# The grid iframes widen themselves past their container in snapshot mode, but a
# card that clips overflow keeps document.scrollWidth from growing — so the PDF
# page is sized too narrow and the last column (Status / Due Status) is sheared
# off. Let the cards overflow, then report the widest iframe so the caller can
# size the page to fit it.
_WIDEN_JS = """
() => {
  let need = 0;
  document.querySelectorAll('iframe.grid-frame').forEach(f => {
    let w = f.getBoundingClientRect().width;
    try {
      const d = f.contentDocument;
      if (d) {
        // Measure the TABLE, not body.scrollWidth: .wrap has overflow:auto, so it
        // absorbs the horizontal overflow and the body never reports it. A table
        // needing 1757px inside a 1612px frame leaves body.scrollWidth at 1612
        // while the last column (Status) is scrolled out of sight — invisible in
        // a PDF, which can't scroll.
        const t  = d.querySelector('table');
        const wr = d.querySelector('.wrap');
        if (t)  w = Math.max(w, t.offsetWidth  + 4);
        if (wr) w = Math.max(w, wr.scrollWidth + 4);
        w = Math.max(w, d.body.scrollWidth);
      }
    } catch (e) {}
    w = Math.ceil(w);
    // main.css carries `.grid-frame { width:100% !important }`, so a plain inline
    // width is ignored — which is why the frame never actually widened and the
    // last column stayed cut off. Inline !important is the only thing that wins.
    if (w > f.clientWidth) f.style.setProperty('width', w + 'px', 'important');
    // stop ancestors clipping the widened frame, and stretch the card behind it
    // so the table still sits on its white background instead of hanging out
    let p = f.parentElement;
    for (let n = 0; n < 6 && p; n++, p = p.parentElement) {
      const cs = getComputedStyle(p);
      if (cs.overflowX !== 'visible') p.style.overflowX = 'visible';
      if (cs.overflow  !== 'visible') p.style.overflow  = 'visible';
      if (p.classList && p.classList.contains('chart-card')) {
        p.style.minWidth = (w + 24) + 'px';
        p.style.boxSizing = 'border-box';
      }
    }
    need = Math.max(need, f.getBoundingClientRect().left + w);
  });
  return Math.ceil(need);
}
"""


def render(base_url: str, out_path: Path) -> Path:
    pdfs = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(viewport={"width": VIEWPORT_W, "height": 1200},
                                  device_scale_factor=2)
        page = ctx.new_page()
        page.emulate_media(media="screen")     # keep on-screen styling in the PDF

        # Warm-up load (discarded): the dashboard is a client-rendered SPA — the whole
        # page, nav bar included, only appears after the JS bundle + websocket +
        # on_init finish. In a freshly-launched Chromium the VERY FIRST navigation
        # can't complete that within SETTLE_MS, so the first real page (AIA Ops) was
        # captured blank every day. This throwaway load absorbs that cold start.
        warm = f"{base_url}/{PAGES[0][0]}?snapshot=1"
        print(f"[snapshot] warm-up: {warm}", flush=True)
        try:
            page.goto(warm, wait_until="load", timeout=60000)
            page.wait_for_selector(".main-nav", timeout=45000)
            page.wait_for_timeout(SETTLE_MS)
        except Exception as ex:
            print(f"[snapshot]   warm-up note: {ex}", flush=True)

        for route, name in PAGES:
            url = f"{base_url}/{route}?snapshot=1"
            print(f"[snapshot] {name}: {url}", flush=True)
            page.goto(url, wait_until="load", timeout=60000)
            # Don't start the settle timer until the SPA shell has actually rendered,
            # so a slow first paint can never be captured as a blank page.
            try:
                page.wait_for_selector(".main-nav", timeout=45000)
            except Exception:
                print(f"[snapshot]   WARN {name}: nav never rendered", flush=True)
            page.wait_for_timeout(SETTLE_MS)
            # PDF-only row filters, then let the iframes re-render off the edited
            # payload (their poll runs on a 1s interval) and re-measure.
            for note in page.evaluate(_FILTER_JS, GRID_FILTERS):
                print(f"[snapshot]   filter {note}", flush=True)
            page.wait_for_timeout(2000)
            need_w = int(page.evaluate(_WIDEN_JS) or 0)
            page.wait_for_timeout(400)
            dims = page.evaluate(
                "() => ({w: Math.max(document.body.scrollWidth, document.documentElement.scrollWidth),"
                "        h: Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)})")
            w = max(int(dims["w"]) + 8, need_w + 24, VIEWPORT_W)
            h = int(dims["h"]) + 40
            print(f"[snapshot]   content {w}x{h}", flush=True)
            pdfs.append(page.pdf(width=f"{w}px", height=f"{h}px", print_background=True,
                                 margin={"top": "0", "bottom": "0", "left": "0", "right": "0"}))
        browser.close()

    writer = PdfWriter()
    for b in pdfs:
        for pg in PdfReader(io.BytesIO(b)).pages:
            writer.add_page(pg)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        writer.write(f)
    print(f"[snapshot] wrote {out_path} ({out_path.stat().st_size//1024} KB, {len(pdfs)} pages)", flush=True)
    return out_path


def upload_to_drive(path: Path) -> str:
    folder = os.environ.get("GDRIVE_FOLDER_ID", "")
    key = Path(os.environ.get("GDRIVE_KEY_FILE", "/opt/taipy-dashboard/gdrive-key.json"))
    if not folder or not key.exists():
        print("[snapshot] Drive upload skipped (GDRIVE_FOLDER_ID or key file missing)", flush=True)
        return ""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    creds = service_account.Credentials.from_service_account_file(
        str(key), scopes=["https://www.googleapis.com/auth/drive.file"])
    svc = build("drive", "v3", credentials=creds)
    meta = {"name": path.name, "parents": [folder]}
    media = MediaFileUpload(str(path), mimetype="application/pdf", resumable=True)
    f = svc.files().create(body=meta, media_body=media,
                           fields="id,webViewLink", supportsAllDrives=True).execute()
    print(f"GDRIVE_URL={f.get('webViewLink','')}", flush=True)
    return f.get("webViewLink", "")


if __name__ == "__main__":
    base = sys.argv[1] if len(sys.argv) > 1 else "http://aia-dashboard:8080"
    if len(sys.argv) > 2:
        out = Path(sys.argv[2])
    else:
        stamp = datetime.now(_IST).strftime("%Y%m%d_%H%M")
        out = Path("/tmp") / f"dashboard_{stamp}_IST.pdf"
    render(base, out)
    print(f"OUTPUT_PATH={out}", flush=True)
    upload_to_drive(out)
