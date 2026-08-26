#!/bin/bash
# Analyse every SOU remiss ärende, most recent first. Resumable: an answer that
# already has a layer is skipped, so stopping and restarting costs nothing but
# the answer in flight. Stop with: pkill -f run-all-sou.sh
S="$(dirname "$0")"
while read -r a; do
  echo "=== $a  $(date '+%m-%d %H:%M:%S')"
  .venv/bin/lagen remisser ai-analyze "$a" || true
done < "$S/all-sou.txt"
echo "### ALL SOU DONE $(date '+%m-%d %H:%M:%S')"
