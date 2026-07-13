#!/usr/bin/env bash
# Pull the live corpus DOWN from ferenda-vps into this dev checkout -- the
# inverse of `sync-data` (the dev->prod push; currently an untracked script in
# ~/.bin on the main dev box). Run it ON the dev box.
#
# Why: rebuilding the corpus from changed parser/render code is far faster on the
# dev box than on the small prod host (which can't re-parse ~100K förarbeten
# within a deploy's time budget). So the code-change workflow is:
#
#   tools/vps/download-data.sh     # pull the current corpus down to dev
#   lagen all rebuild              # reparse/regenerate with the new code (fast here)
#   sync-data                      # push the rebuilt corpus back up to prod
#
# Mirrors the VPS two-disk split (see docs/deploy-vps.md): the artifact tree +
# catalog.sqlite + generated/ live on the fixed disk, downloaded/ on a mounted
# volume. Reads over ssh as root@ferenda-vps (the box's login user); no writes
# to prod, so no ownership fixup is needed on this side.
#
# No --delete: this only ever adds/updates files in the dev tree, so a stray
# path can never wipe local work. Pass extra rsync flags as arguments (e.g.
# --dry-run) -- they are forwarded to both transfers.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
DEST="$REPO/site/data"
REMOTE=ferenda-vps
REMOTE_DATA=/srv/ferenda/site/data
REMOTE_DOWNLOADED=/mnt/HC_Volume_106236756/downloaded

mkdir -p "$DEST/downloaded"

# everything except downloaded/ (artifact/, catalog.sqlite, generated/, ocr/, …)
rsync -aH --partial --info=progress2 --exclude='/downloaded' "$@" \
  "$REMOTE:$REMOTE_DATA/" "$DEST/"
# downloaded/ from the mounted volume
rsync -aH --partial --info=progress2 "$@" \
  "$REMOTE:$REMOTE_DOWNLOADED/" "$DEST/downloaded/"

echo "download-data: pulled $REMOTE corpus into $DEST"
