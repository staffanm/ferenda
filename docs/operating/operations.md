# Operations

`lib/runlog.py` owns three state files under `DATA/.build/`. The run ledger and
error store are written by `build.py` on every *pipeline* `lagen` invocation (a
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
gates that let a whole stage answer "up to date -- skipped" without the per-
document freshness scan. A dry run never records one: `lagen eurlex parse -n`
after a parser edit printed a 64,004-document plan and then marked the source
current, so the real run that followed skipped the entire stale artifact tree.

```sh
uv run python -m ferenda.build <source> status   # extended: also shows failed/empty, writes the authoritative snapshot cell
uv run python -m ferenda.build all runs [N]       # recent runs from the ledger
uv run python -m ferenda.build all errors [N]     # newest N served-site errors (default 50), newest first
uv run python -m ferenda.build all errors <id>    # one error in full, traceback included (the 8-hex id its error page showed)
uv run python -m ferenda.build ann status         # inventory the curated LLM-layer store (lib/annstore.py): status/date/staleness per .ann/.corr layer
```

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

