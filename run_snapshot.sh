#!/bin/bash
# Daily dashboard PDF snapshot — run by n8n (SSH) at 7:30 PM IST.
# Renders all 5 pages from the dashboard container (bypassing the login proxy),
# merges into one PDF saved under ./snapshots, and uploads to Google Drive.
#
# One-time setup on the VPS:
#   cd /opt/taipy-dashboard
#   git pull && docker compose up -d --build           # ship snapshot mode to the live app
#   mkdir -p snapshots
#   # Google Drive (reuses the report.py setup): place service-account JSON at
#   #   /opt/taipy-dashboard/gdrive-key.json  and add to .env:  GDRIVE_FOLDER_ID=<folder id>
# The dashboard-snapshot renderer image is (re)built automatically below if it's
# ever missing (e.g. after a docker prune) — no manual `docker build` step needed.
set -euo pipefail
cd /opt/taipy-dashboard

mkdir -p snapshots

# Wait until the dashboard web server is actually listening. It reloads on deploy
# and spends ~30-60s loading data before the server comes up; the renderer would
# otherwise hit ERR_CONNECTION_REFUSED. (Host maps 127.0.0.1:8080 -> aia-dashboard.)
echo "[run_snapshot] waiting for dashboard on :8080 ..."
for i in $(seq 1 60); do
  if (exec 3<>/dev/tcp/127.0.0.1/8080) 2>/dev/null; then exec 3<&- 3>&-; echo "[run_snapshot] dashboard ready"; break; fi
  sleep 3
done

TS=$(TZ=Asia/Kolkata date +%Y%m%d_%H%M)
OUT="/out/dashboard_${TS}_IST.pdf"

# GDRIVE_FOLDER_ID from .env (blank → script just skips the upload, PDF still saved)
GDRIVE_FOLDER_ID="$(grep -E '^GDRIVE_FOLDER_ID=' .env 2>/dev/null | cut -d= -f2- || true)"

# Self-healing: the renderer image isn't in any registry (built locally only), so
# a docker prune / VPS restart can silently delete it — without this check, `docker
# run` below would then try to PULL "dashboard-snapshot" from Docker Hub and fail
# with "pull access denied ... repository does not exist".
if ! docker image inspect dashboard-snapshot >/dev/null 2>&1; then
  echo "[run_snapshot] dashboard-snapshot image missing, rebuilding..."
  docker build -f Dockerfile.snapshot -t dashboard-snapshot .
fi

KEY_MOUNT=()
if [ -f /opt/taipy-dashboard/gdrive-key.json ]; then
  KEY_MOUNT=(-v /opt/taipy-dashboard/gdrive-key.json:/key.json:ro -e GDRIVE_KEY_FILE=/key.json)
fi

docker run --rm \
  --network taipy-dashboard_default \
  -v /opt/taipy-dashboard/snapshots:/out \
  -e GDRIVE_FOLDER_ID="${GDRIVE_FOLDER_ID}" \
  "${KEY_MOUNT[@]}" \
  dashboard-snapshot http://aia-dashboard:8080 "${OUT}"

# Export each PDF page as a JPEG next to the PDF (<name>_page-1.jpg .. _page-5.jpg).
# Best-effort ONLY: it must never break the PDF -> Drive flow or the LOCAL_PDF stdout
# line the n8n workflow parses, so every step here is guarded with `|| ...` and the
# script's exit code is unchanged. poppler-utils is self-installed if missing.
PDF="/opt/taipy-dashboard/snapshots/dashboard_${TS}_IST.pdf"
if ! command -v pdftoppm >/dev/null 2>&1; then
  echo "[run_snapshot] installing poppler-utils for page-image export ..."
  { apt-get update -qq && apt-get install -y -qq poppler-utils; } || echo "[run_snapshot] poppler-utils install failed (skipping page images)" >&2
fi
if command -v pdftoppm >/dev/null 2>&1; then
  pdftoppm -jpeg -jpegopt quality=82 -r 96 "$PDF" "${PDF%.pdf}_page" \
    || echo "[run_snapshot] page-image export failed (PDF still produced)" >&2
fi

echo "LOCAL_PDF=/opt/taipy-dashboard/snapshots/dashboard_${TS}_IST.pdf"
