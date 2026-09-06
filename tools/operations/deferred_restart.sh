#!/bin/sh
# Finish a deploy that was held back because a pipeline was writing.
#
# `docker compose exec` dies with its container, so recreating the container
# under a running pipeline truncates that run: no error, no traceback, a partly
# written corpus and a nightly that simply stops. On 2026-09-06 a deploy killed
# a 24-minute production run four scopes into `avg download`, before parse,
# relate, index or generate had run at all.
#
# So the deploy builds the image, asks the corpus writer lease whether anything
# is writing (tools/operations/writer_lease.sh -- the same copy both callers
# use), and if something is it schedules this instead of restarting. The image
# is already built and tagged, so the wait costs only the delay.
#
# This runs the WHOLE deploy tail, not just the restart: recreate, wait for
# health, smoke-test through nginx, publish the browser chrome. Skipping the
# last one is not a smaller version of a deploy -- on 2026-08-18 an image-only
# release shipped new API routes to browsers still running the old
# generated/script.js.
#
# Usage: deferred_restart.sh <attempt> <previous-image-id>
# Gives up after MAX_ATTEMPTS rather than rescheduling forever: a lease still
# held five hours later is a stuck pipeline, and a person should look at it.

set -eu

CODE="${CODE:-$HOME/wds/ferenda}"
LOG="${LOG:-$HOME/deferred-restart.log}"
MAX_ATTEMPTS=5
RETRY_IN="${RETRY_IN:-1 hour}"

attempt="${1:?usage: deferred_restart.sh <attempt> <previous-image-id>}"
# Passed in, NOT read from /tmp/ferenda-previous-image. That path is one
# global slot: a deploy pushed during the hours this waits overwrites it, and
# a rollback would then restore an image chosen by a different deploy
# generation, discarding a release that succeeded in between. The id this run
# should put back is fixed when this run is scheduled.
previous="${2:?usage: deferred_restart.sh <attempt> <previous-image-id>}"

say() {
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [attempt $attempt/$MAX_ATTEMPTS] $*" \
        >> "$LOG"
}

# Every exit that leaves production on the old image goes to syslog as well as
# to this file. `at` mails its output to a local mailbox nobody reads, and the
# GitHub run that scheduled this went green hours ago with only a warning
# annotation -- so without this the end state is "new image built, old code
# serving, deploy reported successful, no trace anywhere an operator looks".
#
# syslog rather than the app's own ledger on purpose: `.build/errors.json` is
# the *build's* record, one entry per basefile, and a deploy failure is not a
# document that failed to parse. Surfacing it on /ops would be better than
# `journalctl -t ferenda-deploy`, and needs a decision about which ledger owns
# operator-facing deploy state.
report() {
    say "$1"
    logger -t ferenda-deploy -p daemon.err "$1" 2>/dev/null \
        || say "syslog is not accepting messages either"
}

cd "$CODE"

set +e
held="$(sh "$CODE/tools/operations/writer_lease.sh" 2>&1)"
rc=$?
set -e
case "$rc" in
    0) : ;;                       # free -- carry on
    3)
        say "still held -- $held"
        if [ "$attempt" -ge "$MAX_ATTEMPTS" ]; then
            report "deferred restart gave up after $MAX_ATTEMPTS attempts: the
corpus writer lease has been held for $MAX_ATTEMPTS hours ($held). Production
is still serving the PREVIOUS image. Finish it by hand:
  cd $CODE && docker compose --profile prod up -d ferenda"
            exit 1
        fi
        if ! echo "sh $0 $((attempt + 1)) $previous" \
             | at now + $RETRY_IN 2>>"$LOG"; then
            report "deferred restart could not reschedule itself (is atd
running?). Production is still serving the PREVIOUS image and nothing will
retry. Finish it by hand once the pipeline ends:
  cd $CODE && docker compose --profile prod up -d ferenda"
            exit 1
        fi
        say "rescheduled for $RETRY_IN from now"
        exit 0
        ;;
    *)
        # Not "free" and not "held": the check could not answer, and guessing
        # either way is worse than stopping (rule:fail-fast).
        report "deferred restart stopped: the writer-lease check answered $rc,
which is neither free nor held ($held). Production is still serving the
PREVIOUS image."
        exit 1
        ;;
esac

say "corpus is free -- recreating"
docker compose --profile prod up -d ferenda >>"$LOG" 2>&1

# `up -d` returning says the container started, nothing more.
n=0
healthy=no
while [ "$n" -lt 60 ]; do
    status="$(docker compose ps -q ferenda \
              | xargs -r docker inspect -f '{{.State.Health.Status}}' 2>/dev/null \
              || true)"
    case "$status" in
        healthy) healthy=yes; break ;;
        unhealthy) break ;;
    esac
    n=$((n + 1))
    sleep 5
done

if [ "$healthy" = yes ]; then
    # The same three the deploy smoke-tests, through nginx, the way a reader
    # arrives. Header checks are the vhost's and a container restart cannot
    # change them, so they are not repeated here.
    ok=yes
    for path in /healthz /api/v1/sources /1962:700; do
        code="$(curl -sS -o /dev/null -w '%{http_code}' -m 30 \
                --resolve lagen.nu:443:127.0.0.1 "https://lagen.nu$path" || true)"
        say "GET $path -> $code"
        [ "$code" = 200 ] || ok=no
    done
    if [ "$ok" = yes ]; then
        # style.css + the script bundle, or browsers keep the old ones against
        # the new API (see the header comment)
        docker compose exec -T ferenda lagen all generate --assets-only \
            >>"$LOG" 2>&1
        say "healthy, smoke-tested, chrome published -- deferred deploy complete"
        exit 0
    fi
    say "smoke test failed"
fi

say "rolling back to $previous"
docker tag "$previous" lagen-ferenda:current
docker compose --profile prod up -d ferenda >>"$LOG" 2>&1
report "deferred restart rolled back: the new image did not come up healthy, or
failed its smoke test. Production is serving $previous."
exit 1
