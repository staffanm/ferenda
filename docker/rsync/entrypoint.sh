#!/bin/sh
# Build /tmp/rsyncd.secrets (mode 0600, owned by the container's runtime uid so
# rsyncd's `strict modes = yes` accepts it) and /tmp/rsyncd.conf from the
# host-mounted /etc/rsyncd.secrets, then exec `rsync --daemon`.
#
# Every `username:password` line in /etc/rsyncd.secrets becomes an allowed user
# in `auth users`, so adding or revoking a credential on prod is an edit to
# ~/wds/ferenda/rsyncd.secrets + `docker compose restart rsync` -- no username
# list in git. When the file is empty (before the first reuser is added),
# `auth users` is set to an unmatchable token (`:none:`) so the daemon stays up
# and refuses every login rather than falling back to anonymous access.
set -eu

umask 077
if [ -f /etc/rsyncd.secrets ]; then
    grep -v '^[[:space:]]*\(#\|$\)' /etc/rsyncd.secrets > /tmp/rsyncd.secrets || true
else
    : > /tmp/rsyncd.secrets
fi

users="$(cut -d: -f1 /tmp/rsyncd.secrets | tr '\n' ' ' | sed 's/ *$//')"
if [ -z "$users" ]; then
    users=":none:"
fi

cat > /tmp/rsyncd.conf <<EOF
port = 1873
use chroot = no
read only = yes
munge symlinks = yes
numeric ids = yes
max connections = 2
lock file = /tmp/rsyncd.lock
log file = /dev/stdout
timeout = 300
strict modes = yes
auth users = $users
secrets file = /tmp/rsyncd.secrets

[artifact]
    path = /data/artifact
    comment = Parsed JSON artifacts (.json.br)

[downloaded]
    path = /data/downloaded
    comment = Raw downloaded source files (.json.br / .html.br)
EOF

exec rsync --daemon --no-detach --config=/tmp/rsyncd.conf
