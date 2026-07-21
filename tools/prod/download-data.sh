#!/usr/bin/env bash
# Pull the live corpus DOWN from the prod host into this dev checkout -- the
# inverse of `sync-data` (the dev->prod push; currently an untracked script in
# ~/.bin on the main dev box). Run it ON the dev box.
#
# Prod host is ferenda.lagen.nu (= lagen.nu = 130.236.254.142, LiU/Lysator; NOT
# the retired Hetzner `ferenda-vps`). Login is ssh as `staffan` (no root); reads
# only, so no ownership fixup is needed here. The corpus (data_root) lives on the
# root_squashed NFS at /mnt/forstor/accommodanda and the catalog on the fast local
# disk at /mnt/data/accommodanda. NB: verify these paths + the on-host tree shape
# against the deploy config in ~/wds/ferenda -- the merged-host layout is newer
# than this script.
#
# Why: rebuilding the corpus from changed parser/render code is far faster on the
# dev box than on the prod host (which can't re-parse ~100K förarbeten within a
# deploy's time budget). So the code-change workflow is:
#
#   tools/prod/download-data.sh     # pull the current corpus down to dev
#   lagen all rebuild              # reparse/regenerate with the new code (fast here)
#   sync-data                      # push the rebuilt corpus back up to prod
#
# No --delete: this only ever adds/updates files in the dev tree, so a stray
# path can never wipe local work. Pass extra rsync flags as arguments (e.g.
# --dry-run) -- they are forwarded to both transfers.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
DEST="$REPO/site/data"
REMOTE=staffan@lagen.nu
REMOTE_DATA=/mnt/forstor/accommodanda     # data_root (downloaded/, artifact/, generated/, …)
REMOTE_CATALOG=/mnt/data/accommodanda     # catalog.sqlite on the fast local disk

mkdir -p "$DEST"

# the whole data_root tree from the NFS mount
rsync -aH --partial --info=progress2 "$@" \
  "$REMOTE:$REMOTE_DATA/" "$DEST/"
# the catalog from the separate fast disk (dev keeps it inside site/data)
rsync -aH --partial --info=progress2 "$@" \
  "$REMOTE:$REMOTE_CATALOG/catalog.sqlite" "$DEST/catalog.sqlite"

echo "download-data: pulled $REMOTE corpus into $DEST"
