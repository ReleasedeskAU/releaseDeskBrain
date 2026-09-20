#!/usr/bin/env bash
# Recreate api_server in one step (BN-378).
#
# Nginx no longer needs a reload to pick up a new api_server IP — the overlay
# template re-resolves via Docker DNS (127.0.0.11). Reload here is
# belt-and-suspenders only. Raw `compose up --force-recreate --no-deps
# api_server` is also safe after the template is live.
#
# Run from anywhere. Overlay must be last in the compose file list.
set -euo pipefail

overlay_dir="$(cd "$(dirname "$0")" && pwd)"
compose_dir="${overlay_dir}/../docker_compose"
compose=(
  docker compose
  -f docker-compose.yml
  -f docker-compose.resources.yml
  -f ../releasedesk-overlay/docker-compose.releasedesk.yml
)

cd "${compose_dir}"
"${compose[@]}" up -d --force-recreate --wait --wait-timeout 180 --no-deps api_server

# Harmless if nginx is down or the name differs; resolver is the real fix.
if docker exec onyx-nginx-1 nginx -s reload >/dev/null 2>&1; then
  echo "api_server recreated; nginx reload sent"
else
  echo "api_server recreated; nginx reload skipped (resolver still applies)"
fi
