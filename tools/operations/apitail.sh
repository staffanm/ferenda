#!/bin/sh
# Follow REST API calls on prod as they arrive, one readable line per request.
#
# The sibling of mcptail.sh, but reading a different log for a good reason. The
# MCP server logs itself richly (api/mcp.py `_LoggedMCP`), so mcptail reads the
# app. The REST API has no such wrapper: uvicorn's access line carries only
# method, path and status, and its client address is always nginx
# (172.19.0.4). nginx's own log has the caller's real address, user-agent and
# referer, so that is what this reads.
#
# The one thing it cannot show is duration: nginx's default `combined` format
# has no $request_time. Add it to log_format if that matters -- this script
# prints it when the field is present and stays quiet when it is not.
#
# Install: copy to ~/bin/apitail.sh on prod (see tools/prod/keepwarm.sh for why
# it lives outside the CI-managed checkout).
#
# Usage:
#   apitail.sh              follow every /api/ call, live
#   apitail.sh -x           only external callers -- drop our own pages' XHR
#   apitail.sh -e           only failures (4xx/5xx)
#   apitail.sh -n 5000      look this far back before following (default 1000)
#   apitail.sh -p /api/v1/search   only paths starting with this
set -eu
CONTAINER=${APITAIL_CONTAINER:-ferenda-nginx-1}
tail_n=1000
mode=all
prefix=/api/
while [ $# -gt 0 ]; do
    case $1 in
        -x) mode=external ;;
        -e) mode=errors ;;
        -n) shift; tail_n=$1 ;;
        -p) shift; prefix=$1 ;;
        *) echo "usage: $0 [-x|-e] [-n LINES] [-p PATH-PREFIX]" >&2; exit 2 ;;
    esac
    shift
done

docker logs -f --tail "$tail_n" "$CONTAINER" 2>&1 |
python3 -u -c '
import re, sys

# nginx combined: addr - - [time] "METHOD path proto" status bytes "ref" "ua"
LINE = re.compile(
    r"^(?P<ip>\S+) \S+ \S+ \[(?P<time>[^\]]+)\] "
    r"\"(?P<method>[A-Z]+) (?P<path>\S*) [^\"]*\" "
    r"(?P<status>\d{3}) (?P<bytes>\d+) "
    r"\"(?P<ref>[^\"]*)\" \"(?P<ua>[^\"]*)\"(?P<tail>.*)$")
# only if someone adds $request_time to log_format -- printed when present
RT = re.compile(r"\b(\d+\.\d{3})\b")
mode, prefix = sys.argv[1], sys.argv[2]

for raw in sys.stdin:
    m = LINE.search(raw)
    if not m or not m.group("path").startswith(prefix):
        continue
    status = int(m.group("status"))
    # our own pages call the API over XHR; the referer is what tells them from a
    # third-party consumer, the same cut api/analytics.py makes before counting
    own = "ferenda.lagen.nu" in m.group("ref") or "lagen.nu" in m.group("ref")
    if mode == "errors" and status < 400:
        continue
    if mode == "external" and own:
        continue
    rt = RT.search(m.group("tail"))
    ua = m.group("ua")
    ua = "browser" if "Mozilla" in ua and "compatible;" not in ua else ua[:30]
    print("%s %-15s %-30s %s%3d %7sB %s %s" % (
        m.group("time")[12:20], m.group("ip")[:15], ua,
        ("%6ss " % rt.group(1)) if rt else "",
        status, m.group("bytes"),
        "xhr" if own else "   ", m.group("path")[:100]))
' "$mode" "$prefix"
