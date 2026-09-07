#!/bin/sh
# Publish a push to the content repo: pull it, work out what it re-staled, and
# rebuild only that.
#
# The naive version of this job is `git pull && lagen all rebuild`, and it is
# wrong twice over. It costs a corpus-wide run for a one-word edit to a news
# item; and it collides with the nightly, because `lagen` takes the corpus
# writer lease and a second writer is refused outright (build.py:682).
#
# So: `wiki_targets.py` turns the changed paths into the narrowest correct
# commands -- `lagen site generate sitenews` for a news edit, `lagen sfs
# generate 1962:700` for a patch, and a whole-corpus rebuild only when a path
# is not recognised. A targeted generate is not a shortcut past make
# semantics: `build._prepare_targeted_generate` refreshes that document's
# parse and versions, relates it only if its catalog row actually disagrees,
# and then renders it.
#
# Usage: publish_wiki.sh <attempt> <changed-path> [<changed-path> ...]
#
# Retries hourly through `at` while another writer holds the corpus, giving up
# after MAX_ATTEMPTS -- the same shape as tools/operations/deferred_restart.sh,
# for the same reason.

set -eu

CODE="${CODE:-$HOME/wds/ferenda}"
WIKI="${WIKI:-/mnt/forstor/lagen-wiki}"
LOG="${LOG:-$HOME/publish-wiki.log}"
COMPOSE="docker compose -f $CODE/docker-compose.yml"
MAX_ATTEMPTS=5
RETRY_IN="${RETRY_IN:-1 hour}"

attempt="${1:?usage: publish_wiki.sh <attempt> <changed-path>...}"
shift
[ "$#" -gt 0 ] || { echo "no changed paths given" >&2; exit 2; }

say() {
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [attempt $attempt/$MAX_ATTEMPTS] $*" \
        >> "$LOG"
}

report() {
    say "$1"
    logger -t ferenda-publish-wiki -p daemon.err "$1" 2>/dev/null \
        || say "syslog is not accepting messages either"
}

# The pull happens on every attempt, not only the first: a retry an hour later
# should publish whatever the repo holds by then, not the state it held when
# the push arrived.
say "pulling $WIKI"
git -C "$WIKI" pull --ff-only origin main >>"$LOG" 2>&1 || {
    report "publish-wiki: git pull failed in $WIKI -- the content repo is not
fast-forwardable, so nothing was published. Look at it by hand."
    exit 1
}

# Refuse to start behind another writer rather than letting `lagen` fail per
# command: it would run the first target, be refused on the second, and leave
# the push half published.
set +e
held="$(sh "$CODE/tools/operations/writer_lease.sh" 2>&1)"
rc=$?
set -e
if [ "$rc" = 3 ]; then
    say "corpus busy -- $held"
    if [ "$attempt" -ge "$MAX_ATTEMPTS" ]; then
        report "publish-wiki: gave up after $MAX_ATTEMPTS attempts; the corpus
writer lease has been held for $MAX_ATTEMPTS hours ($held). The content repo is
pulled but nothing was rebuilt from it."
        exit 1
    fi
    # requoted, not "$*": a path can carry a space or a quote, and `at` reads
    # its job through a shell
    quoted=""
    for arg in "$@"; do
        quoted="$quoted '$(printf '%s' "$arg" | sed "s/'/'\\\\''/g")'"
    done
    if ! echo "sh $0 $((attempt + 1))$quoted" | at now + $RETRY_IN 2>>"$LOG"; then
        report "publish-wiki: could not reschedule itself (is atd running?).
The content repo is pulled but nothing was rebuilt from it."
        exit 1
    fi
    say "rescheduled for $RETRY_IN from now"
    exit 0
elif [ "$rc" != 0 ]; then
    # neither free nor held: guessing either way is worse than stopping
    report "publish-wiki: the writer-lease check answered $rc ($held); nothing
was rebuilt."
    exit 1
fi

say "changed: $*"
plan="$($COMPOSE exec -T ferenda /app/.venv/bin/python \
        /app/tools/operations/wiki_targets.py "$@")" || {
    report "publish-wiki: could not work out what the push re-staled; nothing
was rebuilt. Changed paths: $*"
    exit 1
}

if [ -z "$plan" ]; then
    say "nothing any page is built from -- done"
    exit 0
fi

# Through a file, not a pipe: a piped `while read` runs in a subshell, so the
# `exit 1` below would leave the subshell and this script would report success
# for a plan that failed.
steps="$(mktemp)"
trap 'rm -f "$steps"' EXIT INT TERM
printf '%s\n' "$plan" > "$steps"

say "plan:"
while IFS= read -r line; do say "  $line"; done < "$steps"

while IFS= read -r line; do
    say "running: $line"
    if ! $COMPOSE exec -T ferenda sh -c "cd /app && /app/.venv/bin/$line" \
         >>"$LOG" 2>&1; then
        report "publish-wiki: '$line' failed. The content repo is pulled and
earlier commands in the plan did run, so the site may be partly updated."
        exit 1
    fi
done < "$steps"

say "published"
