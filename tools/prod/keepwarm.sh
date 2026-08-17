#!/bin/sh
# Keep the OpenSearch index warm on prod.
#
# The `lagen` index is ~31 GB against a host with ~8 GB of page cache and an
# HDD-class disk (~100 random IOPS). A search after an idle hour reads hundreds
# of scattered blocks and costs 10+ s; the same search a second later costs
# ~100 ms. Nothing keeps the whole index resident -- but a search every 15
# minutes keeps the OpenSearch process off swap and holds the blocks that
# common queries touch.
#
# Install: copy to ~/bin/keepwarm.sh on prod and add to the crontab
#   scp tools/prod/keepwarm.sh staffan@ferenda.lagen.nu:bin/keepwarm.sh
#   */15 * * * * /home/staffan/bin/keepwarm.sh
# It runs from ~/bin rather than from the ~/wds/accommodanda checkout because
# CI fast-forwards that checkout: an untracked file sitting on a path an
# incoming commit adds makes the merge refuse. Re-copy after editing here.
#
# The log is the measurement: one line per probe with the status and the
# round-trip time, so "is search still slow after idle?" is a question the
# file answers. Read the slow tail with
#   awk '$5+0 > 2' ~/keepwarm.log
LOG=${KEEPWARM_LOG:-$HOME/keepwarm.log}
BASE=${KEEPWARM_BASE:-https://ferenda.lagen.nu}

# Four queries, one per quarter-hour slot, so each common term comes round
# every hour instead of one term monopolising the cache. Ordinary reader
# vocabulary -- these are the postings worth holding.
#
# The slot comes off the epoch, not off %H/%M: a zero-padded "08" is an invalid
# octal literal in POSIX shell arithmetic.
set -- skadestånd uppsägning vårdnad preskription
slot=$(( $(date +%s) / 900 % 4 + 1 ))
eval "q=\${$slot}"

# --max-time bounds a cold probe so a stalled one cannot overlap the next; the
# header tells the API's Matomo tracker this is our own probe, not an audience
# (see accommodanda/api/analytics.py `_keep_warm`).
out=$(curl -sS -o /dev/null \
        -w '%{http_code} %{time_total}' \
        --max-time 120 \
        -H 'X-Keep-Warm: 1' \
        -A 'lagen-keepwarm/1' \
        --get --data-urlencode "q=$q" --data 'limit=1' \
        "$BASE/api/v1/search" 2>&1)

printf '%s keepwarm q=%s %s\n' "$(date -Is)" "$q" "$out" >> "$LOG"
