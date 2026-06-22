#!/bin/bash
# Incrementally parse eurlex docs as `unpack-bulk` writes them, then relate +
# generate once the unpack settles. Safe to interrupt/re-run: parse is
# content-hash incremental, so finished docs are skipped.
cd /home/staffan/repos/ferenda || exit 1
prev=-1
while true; do
  n=$(find site/data/eurlex -mindepth 3 -name notice.ttl 2>/dev/null | wc -l)
  echo "[$(date +%H:%M:%S)] notices on disk: $n -- parsing (incremental, -j4)..."
  uv run python -m accommodanda.build eurlex parse -j4
  if [ "$n" -eq "$prev" ]; then
    echo "[$(date +%H:%M:%S)] notice count stable at $n -- unpack appears done"
    break
  fi
  prev=$n
  sleep 180
done
echo "[$(date +%H:%M:%S)] final parse pass..."
uv run python -m accommodanda.build eurlex parse -j4
echo "[$(date +%H:%M:%S)] relate + generate site..."
uv run python -m accommodanda.build eurlex generate
echo "=== pipeline DONE $(date) ==="
