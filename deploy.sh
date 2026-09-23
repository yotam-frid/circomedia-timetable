#!/usr/bin/env bash
# Build the SvelteKit UI once per target and deploy both in parallel:
#   box (Hetzner, adapter-node) → rsync + systemctl restart → https://circomedia.yotamfrid.dev
#   vercel (adapter-vercel)     → vercel deploy --prebuilt --prod  → https://circomedia-timetable.vercel.app
#
# Timetable DATA (site/: feeds, roster, manifest) is a separate concern — push
# it with `python3 publish.py` (rsync). No UI redeploy is needed for data
# changes, and no builder rebuild happens here.
set -euo pipefail
cd "$(dirname "$0")"

echo "~ build box bundle (ADAPTER=node)"
ADAPTER=node pnpm build

echo "~ build vercel bundle"
pnpm build

echo "~ deploy box + vercel in parallel"
(
  set -e
  rsync -az --delete build/ root@91.98.227.5:/srv/timetable-app/build/
  ssh root@91.98.227.5 'systemctl restart timetable'
  echo "box:    https://circomedia.yotamfrid.dev"
) &
BOX=$!

(
  set -e
  vercel deploy --prebuilt --prod --yes
) &
VERCEL=$!

wait $BOX
wait $VERCEL
echo "~ both deployed"