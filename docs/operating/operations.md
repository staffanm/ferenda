# Operations

`lib/runlog.py` owns three state files under `DATA/.build/`. The run ledger and
error store are written by `build.py` (run start and end) and `lib/freshness.py` (segments, per-document outcomes) on every *pipeline* `lagen` invocation (a
no-op under `--dry-run`, and for the non-pipeline verbs `serve`/`runs`, which
carry no run id). `status` is the deliberate exception: it too carries no run id
and never touches the ledger, but it writes the authoritative `status.json`
snapshot cell directly (see below).

- `runs.ndjson` — append-only run ledger: one block of events per invocation
  (run-start, one segment per (step, source) executed, run-end).
- `errors.json` — per-document latest-outcome store keyed
  `<source>/<stage>/<basefile>`, set on failure and cleared on success, so a
  "failed" doc is distinguishable from one that was simply never touched.
- `status.json` — rolling per-source × per-stage health snapshot
  (`{total, fresh, stale, missing, failed, empty}` per cell).

`fingerprints.json` (the same directory) holds the coarse per-(step, source)
gates that let relate, index, dump and generate answer "up to date -- skipped"
without re-reading their inputs, and an owed-hook mark
(`<step>/__hooks__/<source>`) for a parse or versions stage whose after-hooks
crashed, so the next run fires them even with nothing stale. Parse and versions
have no coarse gate any more: their one staleness scan decides per document
who gets a worker, and a source with nothing stale skips at the end of that
scan. A dry run never records a gate: `lagen eurlex parse -n` after a parser
edit once printed a 64,004-document plan and then marked the source current,
so the real run that followed skipped the entire stale artifact tree.

```sh
uv run python -m ferenda.build <source> status   # extended: also shows failed/empty, writes the authoritative snapshot cell
uv run python -m ferenda.build all runs [N]       # recent runs from the ledger
uv run python -m ferenda.build all errors [N]     # newest N served-site errors (default 50), newest first
uv run python -m ferenda.build all errors <id>    # one error in full, traceback included (the 8-hex id its error page showed)
uv run python -m ferenda.build ann status         # inventory the curated LLM-layer store (lib/annstore.py): status/date/staleness per .ann/.corr layer
```

## One writer at a time

A pipeline invocation takes the corpus writer lease first — the directory
`DATA/.build/writer.lock`, `lib/writerlock.py`. The read-only verbs (`serve`,
`status`, `runs`, `errors`) and `--dry-run` take none, so watching a build
while it runs is unaffected.

A second writer is refused before it writes anything, naming the first:

```
all relate is already writing this corpus: 20260906T001458Z-lagen-9912
(pid 9912 on lagen, started 2026-09-06 00:14:58). Wait for it, or remove
<data_root>/.build/writer.lock if you know it is gone.
```

Usually there is nothing to do but wait. A lease whose holder was killed is
taken over by the next run on its own — the holder's pid and its start time are
both recorded, so a process that is gone, or a pid the kernel has since reused,
is a lease nobody owns. Removing the directory by hand is for the cases this
machine cannot answer: the holder ran on **another** machine, or in a container
beside this one, and that run is known to be over. Those are judged by age
instead — a run refreshes its lease every five minutes, and one untouched for an
hour counts as gone.

This is what makes the run ledger's single-writer assumption true, and it is
why the fingerprint store may still be rewritten whole. Without it, two runs
interleave `runs.ndjson`, each writes back a fingerprint snapshot that never
saw the other's completed gates, and a full relate has both of them deleting
and writing one scratch catalog. A full rebuild's scratch now carries the run
id (`catalog.sqlite.<run id>.building`), and each run sweeps away scratch files
left by runs that are not its own.

`lib/errorlog.py` owns a separate ledger, `DATA/.build/httperrors.ndjson`, for
the *serving* side rather than the build: one record per 404/500 the running
site answered, keyed by an 8-hex id the error page shows the reader, so "a
page was broken" becomes a url, referer, client and (for a 500) a traceback
someone can act on. It never mixes with `errors.json` — a document can be
missing from the site with no build having failed, which is exactly the case
worth recording here. Written by `api/errors.py`'s exception handlers,
rotated at 8 MB keeping one `.1` generation, read by `lagen all errors`.

`/ops` is an HTML health dashboard mounted on the same FastAPI app as the REST
API (`api/ops.py`) — a system panel (deployed revision baked at image build,
the lagen-wiki repo's push state, OpenSearch index size), a per-source corpus
inventory (documents + artifact size), the per-source × per-stage matrix, a
stale-snapshot banner, failing-doc totals, the last runs, duration-regression
flags, and the catalog delta — with `/ops/runs`, `/ops/runs/{id}` (per-source timing bars +
segments + errors) and `/ops/failures` (drill-down with tracebacks) alongside
it. It's gated by the inline editor's session (`auth.require_editor`) — it
rides the same editor login rather than a separate credential, so any editor
can view it: no session answers 401, and an unset `editor_secret` disables it
entirely (403), exactly as the edit routes do.

