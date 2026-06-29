#!/bin/bash
# Daily dashboard PDF snapshot — run by n8n (SSH) at 7:30 PM IST.
# Renders all 5 pages from the dashboard container (bypassing the login proxy),
# merges into one PDF saved under ./snapshots, and uploads to Google Drive.
#
# One-time setup on the VPS:
#   cd /opt/taipy-dashboard
#   git pull && docker compose up -d --build           # ship snapshot mode to the live app
#   docker build -f Dockerfile.snapshot -t dashboard-snapshot .
#   mkdir -p snapshots
#   # Google Drive (reuses the report.py setup): place service-account JSON at
#   #   /opt/taipy-dashboard/gdrive-key.json  and add to .env:  GDRIVE_FOLDER_ID=<folder id>
set -euo pipefail
cd /opt/taipy-dashboard

mkdir -p snapshots
TS=$(TZ=Asia/Kolkata date +%Y%m%d_%H%M)
OUT="/out/dashboard_${TS}_IST.pdf"

# GDRIVE_FOLDER_ID from .env (blank → script just skips the upload, PDF still saved)
GDRIVE_FOLDER_ID="$(grep -E '^GDRIVE_FOLDER_ID=' .env 2>/dev/null | cut -d= -f2- || true)"

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

echo "LOCAL_PDF=/opt/taipy-dashboard/snapshots/dashboard_${TS}_IST.pdf"
