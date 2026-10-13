#!/usr/bin/env bash
# Build and deploy the SvelteKit UI to the main host:
#   box (Hetzner, adapter-node) → rsync + systemctl restart → https://circomedia.yotamfrid.dev
# Vercel only redirects to the main host and needs no routine UI updates.
# Use --with-vercel only when updating its redirect configuration.
#
# Timetable DATA (site/: feeds, roster, manifest) is a separate concern — push
# it with `python3 publish.py` (rsync). No UI redeploy is needed for data
# changes, and no builder rebuild happens here.
set -euo pipefail
cd "$(dirname "$0")"

WITH_VERCEL=false
case "${1:-}" in
  "") ;;
  --with-vercel) WITH_VERCEL=true ;;
  *) echo "Usage: $0 [--with-vercel]" >&2; exit 2 ;;
esac
if (( $# > 1 )); then
  echo "Usage: $0 [--with-vercel]" >&2
  exit 2
fi

echo "~ build box bundle (ADAPTER=node)"
ADAPTER=node pnpm build

echo "~ deploy box"
rsync -az --delete build/ root@91.98.227.5:/srv/timetable-app/build/
ssh root@91.98.227.5 'systemctl restart timetable'
echo "box: https://circomedia.yotamfrid.dev"

if "$WITH_VERCEL"; then
  echo "~ deploy Vercel redirect"
  vercel deploy --prod --yes
fi
echo "~ deployed"
