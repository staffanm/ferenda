#!/usr/bin/env bash
# Nightly full sync on ferenda-vps: `lagen all all` = download (every source)
# then an incremental parse -> relate -> index -> dump -> generate. Run from the
# `ferenda` user's crontab; see docs/deploy-vps.md.
#
#   0 3 * * *  /srv/ferenda/ferenda/tools/vps/nightly.sh
#
# Runs inside the already-running accommodanda container (restart: always keeps
# it up). Logs to a dated file; a failure is loud (set -e, non-zero exit) so
# cron mails it.
set -euo pipefail

PROJECT=/srv/ferenda/ferenda
LOGDIR=/srv/ferenda/logs
mkdir -p "$LOGDIR"
LOG="$LOGDIR/nightly-$(date +%Y%m%d).log"

cd "$PROJECT"
{
  echo "=== nightly lagen all all START $(date -Is) ==="
  docker compose exec -T accommodanda lagen all all
  echo "=== nightly lagen all all DONE  $(date -Is) ==="
} >> "$LOG" 2>&1
