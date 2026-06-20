#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────
# Update the AiA + VA dashboard on the VPS.
# Run on the HOST shell (root@srv1701205), NOT inside the container:
#     cd /opt/taipy-dashboard && ./update.sh
# It pulls the latest code from GitHub and rebuilds/restarts the container.
# Your .env (credentials) is gitignored and never touched.
# ─────────────────────────────────────────────────────────────────────────
set -e
cd /opt/taipy-dashboard

echo ">>> [1/3] Pulling latest code from GitHub..."
git pull

echo ">>> [2/3] Rebuilding and restarting the container..."
docker compose up -d --build

echo ">>> [3/3] Verifying the new code is live..."
sleep 3
docker exec aia-dashboard grep -c "_auto_refresh_loop" main.py >/dev/null \
  && echo "    OK - container is running the updated code." \
  || echo "    WARNING - could not verify; check 'docker logs aia-dashboard'."

echo ""
echo ">>> Done. Open http://187.127.173.25:8080 and hard-refresh (Ctrl+Shift+R)."
