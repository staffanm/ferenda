#!/bin/sh
# Follow MCP tool calls on prod as they arrive, one readable line per call.
#
# The raw log carries two lines per request (start and done, paired by `mcp[N]`)
# and each runs past 300 characters, so a bare `docker logs | grep` is hard to
# read live. This shows the completed calls by default -- the line that carries
# the outcome -- and flags anything that went wrong.
#
# Install: copy to ~/bin/mcptail.sh on prod (see tools/prod/keepwarm.sh for why
# it lives outside the CI-managed checkout).
#
# Usage:
#   mcptail.sh              follow completed calls, live
#   mcptail.sh -a           include the `start` line of each request too
#   mcptail.sh -n 2000      look this far back before following (default 500)
#   mcptail.sh -e           only what went wrong (non-200, JSON-RPC error,
#                           proxy timeout, caller gone)
#
# Two things a bare pipeline gets wrong and this does not: python logging writes
# to stderr, so the 2>&1 is load-bearing, and every stage must flush per line or
# matches sit in a buffer while the screen looks idle.
set -eu
CONTAINER=${MCPTAIL_CONTAINER:-ferenda-accommodanda-1}
tail_n=500
mode=done
while [ $# -gt 0 ]; do
    case $1 in
        -a) mode=all ;;
        -e) mode=errors ;;
        -n) shift; tail_n=$1 ;;
        *) echo "usage: $0 [-a|-e] [-n LINES]" >&2; exit 2 ;;
    esac
    shift
done

# -t so each line carries its RFC3339 stamp; the app's own log format has none
docker logs -f -t --tail "$tail_n" "$CONTAINER" 2>&1 |
python3 -u -c '
import re, sys

# ua can contain spaces ("openai-mcp/1.0.0 (Codex)"), so parse by regex rather
# than by field position -- splitting on whitespace mis-attributes every field
# after it.
LINE = re.compile(
    r"mcp\[(?P<n>\d+)\] (?P<method>\w+) (?P<path>\S+) "
    r"ip=(?P<ip>\S+) ua=(?P<ua>.*?) "
    r"(?P<phase>start|done|raised) (?P<rest>.*)$")
DONE = re.compile(r"status=(?P<status>\S+) (?P<ms>\d+) ms bytes=(?P<bytes>\d+) "
                  r"(?P<flags>[A-Z-]*(?:\([^)]*\)[a-z-]*)?\s*)(?P<what>.*)$")
mode = sys.argv[1]

for raw in sys.stdin:
    m = LINE.search(raw)
    if not m:
        continue
    phase, ua = m.group("phase"), m.group("ua")[:30]
    stamp = raw[11:19] if raw[:2] == "20" else "--:--:--"
    if phase == "raised":
        print("%s %-30s   RAISED  %s" % (stamp, ua, m.group("rest")[:110]))
        continue
    if phase == "start":
        if mode == "all":
            print("%s %-30s        ..  %s" % (stamp, ua, m.group("rest")[:110]))
        continue
    d = DONE.search(m.group("rest"))
    if not d:
        continue
    flags = d.group("flags").strip()
    bad = flags or d.group("status") != "200"
    if mode == "errors" and not bad:
        continue
    print("%s %-30s %6sms  %s%s" % (
        stamp, ua, d.group("ms"),
        ("[%s %s] " % (d.group("status"), flags)) if bad else "",
        d.group("what")[:110]))
' "$mode"
