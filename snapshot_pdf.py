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


def render(base_url: str, out_path: Path) -> Path:
    pdfs = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(viewport={"width": VIEWPORT_W, "height": 1200},
                                  device_scale_factor=2)
        page = ctx.new_page()
        page.emulate_media(media="screen")     # keep on-screen styling in the PDF
        for route, name in PAGES:
            url = f"{base_url}/{route}?snapshot=1"
            print(f"[snapshot] {name}: {url}", flush=True)
            page.goto(url, wait_until="load", timeout=60000)
            page.wait_for_timeout(SETTLE_MS)
            dims = page.evaluate(
                "() => ({w: Math.max(document.body.scrollWidth, document.documentElement.scrollWidth),"
                "        h: Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)})")
            w = max(int(dims["w"]) + 8, VIEWPORT_W)
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
