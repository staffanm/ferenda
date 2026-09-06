#!/bin/sh
# Is a pipeline writing the corpus right now?
#
#   exit 0  free
#   exit 3  a writer holds it (its identity is printed)
#   other   the question could not be answered -- never read this as "free"
#
# Two callers need this and must not answer it differently: the deploy
# (.github/workflows/deploy.yml), which refuses to recreate the container while
# a run is writing, and tools/operations/deferred_restart.sh, which re-asks
# later. It lives here so there is one copy.
#
# Asked INSIDE the container. The lease records the writer's pid and its pid
# namespace, so only a process sharing that namespace can tell a live holder
# from one that died; from the host the pid names nothing and the answer would
# fall back to age alone.

set -eu

cd "${CODE:-$HOME/wds/ferenda}"

# A container that is not up cannot be running a pipeline, and a stopped
# container is exactly the state a restart fixes.
[ -n "$(docker compose ps -q ferenda)" ] || exit 0

exec docker compose exec -T ferenda \
     /app/.venv/bin/python -m ferenda.lib.writerlock
