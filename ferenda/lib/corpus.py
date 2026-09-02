"""The corpus-wide verbs: relate, index, dump, generate and the composites.

Everything a build does *across* sources once their documents are parsed --
building the shared catalog from the artifacts on disk, syncing the search
index, writing the bulk dumps, rendering the static site -- plus the status
verbs that report on it and the run instrumentation they share.

The verbs know no source. Every source-specific step a verb needs is a field
the source filled in when it registered (`lib/stage.py`): `artifacts` lists
what to read, `render` renders a page, `extra_pages` adds pages that are not
catalog rows, `write_pages` writes a source whose pages are not catalog rows
at all, `relate_cross` contributes to relate's cross-document block, and
`after[verb]` hangs arbitrary work off a verb. `build.py` composes the
registry and passes it in as each verb's first argument; no verb reads it as a
global (rule:lib-never-imports-vertical).

The pages generated through the REST API in-process (the faceted browse tree,
the subdomain projections) are the one thing a caller must still supply, as
`cmd_generate`'s `aggregates` callable: the API lives a layer above lib/.
"""

import hashlib
import os
import sys
import time
import traceback
from collections import Counter
from datetime import date
from pathlib import Path

from .. import config
from . import (
    annstore,
    catalog,
    compress,
    dump,
    freshness,
    hierarki,
    labels,
    layout,
    pathgraph,
    render,
    runlog,
    search,
    util,
)
from . import stage as protocol

# the ferenda package root -- this module sits one level down, in lib/, so
# every recipe path below resolves to the same file a source's own
# `source.py` names
PKG = Path(__file__).parent.parent

DATA = config.DATA                            # corpus location (config.yml: data_root)
DUMPS = DATA / "dumps"                         # NDJSON bulk exports


# --------------------------------------------------------------------------
# derived layer: relate (catalog) + generate (static site). Corpus-wide verbs,
# not per-document Stages, for two reasons: relate writes shared catalog rows
# (not one output file per basefile), and a doc's generated HTML has a
# *data-dependent* prerequisite set -- its own artifact plus the artifacts of
# exactly the documents that cite it (its inbound set, read from the catalog).
# That set is not expressible in the static
# Stage.inputs(basefile) protocol. For now both rebuild whole; a per-doc
# incremental generate would key off that inbound set.
# --------------------------------------------------------------------------


# relate's per-source extraction (the documents/links it derives per artifact)
# lives wholly in catalog.py; index's unit shape + body extraction in
# search.py + text.py. A change to these re-stales the corresponding step the same
# way a parser edit re-stales parse (recipe-version rule).
# A recipe-version tuple must cover every first-party module whose edit can
# change the stage's output, not just the head module -- else editing an
# imported helper leaves the step "up to date -- skipped" and ships stale
# output until --force. catalog.py's per-artifact extraction lives in
# catalog_rows (the rows and edges one artifact becomes) and imports begrepp
# (alias synthesis), text (run flattening) and markdown (begrepp uris); a
# change to any re-stales relate.
RELATE_CODE = (PKG / "lib" / "catalog.py", PKG / "lib" / "catalog_rows.py",
               PKG / "lib" / "begrepp.py",
               PKG / "lib" / "text.py", PKG / "lib" / "markdown.py",
               PKG / "lib" / "labels.py")
# index reads the catalog rows (source signature, inbound-count ranking) it
# denormalises onto the search units, so a change to catalog.py re-stales it too.
INDEX_CODE = (PKG / "lib" / "search.py", PKG / "lib" / "text.py",
              PKG / "lib" / "catalog.py", PKG / "lib" / "catalog_rows.py",
              # doc_actions stores the case number through malnummer.normalize,
              # so a change to the shape changes what is indexed
              PKG / "lib" / "malnummer.py")
DUMP_CODE = (PKG / "lib" / "dump.py",)


def _swap_catalog(scratch, dest):
    """Atomically replace `dest` with the freshly built `scratch`, durably.

    First quiesce the *old* catalog's write-ahead log: a live `dest` is in WAL
    mode (any incremental relate leaves it so, and the serving layer keeps the
    sidecars present), and a stale `dest-wal` left beside the swapped-in file would
    be silently re-applied by the next reader onto the new base -- a corrupt old/new
    mix (`catalog.quiesce_wal`). Then the swap: the scratch was built with fsync off
    for speed (a crashed rebuild is discarded, not recovered), so this is its one
    durable moment -- fsync the finished file, rename it over `dest` (atomic within
    the directory, which is why scratch and dest must share one), then fsync the
    directory so the rename itself survives a host crash rather than leaving `dest`
    pointing at a half-synced inode."""
    catalog.quiesce_wal(dest)
    fd = os.open(scratch, os.O_RDONLY)
    os.fsync(fd)
    os.close(fd)
    os.replace(scratch, dest)
    dfd = os.open(dest.parent, os.O_RDONLY)
    os.fsync(dfd)
    os.close(dfd)


# the lib code the relate cross-passes run -- their own recipe, apart from
# RELATE_CODE: an edit here re-runs only the __corr__ block (cheap), where an
# entry in RELATE_CODE re-extracts every document of every source. Each
# source's own cross-pass code joins through `Source.cross_code`.
CORR_CODE = (PKG / "lib" / "hierarki.py",)


def _layers(sources):
    """Every side file outside the catalog that a source's pages or
    cross-passes read (`Source.layers`): authored .ann/.corr layers, the
    versions-stage sidecars, the artifacts of a source that catalogues none.
    Sorted, deduplicated, so the fingerprint over them is stable."""
    return sorted({p for s in sources.values() if s.layers for p in s.layers()})


def _corr_watermark(sources):
    """The fingerprint over what the relate cross-passes read: every source's
    layers and the cross-pass code (CORR_CODE + each `Source.cross_code`) --
    the gate for re-running them, shared by cmd_relate and the targeted relate
    check (build._catalog_current_for), so both notice the same layer or code
    edits."""
    return freshness.file_fingerprint(
        _layers(sources) + list(CORR_CODE)
        + [p for s in sources.values() for p in s.cross_code])


# how many dangling anchors relate names individually before the count stands
# alone; a systematic break shows itself in the first few
DANGLING_REPORT = 20
# The sources the anchor audit can answer for -- named by the document a link
# points *at*, not by the one citing it. Both render every provision through
# `page.provision_section`, which anchors on the artifact's own node id, so a
# fragment that names no node really is a dead link. No other source is on this
# list, because every other one mints anchors at render time that no `structure`
# node holds: sfs a change-act anchor per amendment (`1999:1229#L2007:1419`),
# eurlex an article and stycke alias (`32009R1107#29.6`), forarbete a page
# marker (`prop/1975:103#sid355`), coe a sub-paragraph pinpoint
# (`coe/005#A5P1Ld`), and `page.Toc` a generated anchor for any heading with no
# id at all. Asked of every source the audit reports 1 612 832 live links as
# broken. Adding a source here is a claim about its renderer -- read it first.
ANCHOR_EXACT = ("icrc", "untc")


def _plan_artifact_verb(verb, sources, names, destination):
    """What an artifact-reading corpus verb *would* do to each named source,
    for --dry-run: one line per source in the same "would" wording `report`
    uses for a per-document plan. A plan reads the artifact listers and
    nothing else -- it opens no catalog, no index and no output file."""
    for name in names:
        if sources[name].artifacts is None:
            continue
        print("%s %s: would read %d artifact(s) -> %s"
              % (verb, name, len(sources[name].artifacts()), destination(name)))


def cmd_relate(sources, names, force=None):
    """(Re)build each named source's rows in the shared catalog from its
    artifacts on disk -- documents + the citation edges they carry inline.
    Incremental on artifact content (unchanged artifacts are skipped); editing
    the extraction code (catalog.py) or passing --force re-extracts every
    artifact of the affected source. `force=None` reads the run's --force;
    a targeted generate passes False so its override stays local."""
    if protocol.RUN.dry_run:
        _plan_artifact_verb("relate", sources, names, lambda _name: layout.CATALOG)
        print("relate: would run the cross-document passes (norm chain, "
              "delegation edges, regleringshierarki, concept stubs)")
        return
    force = protocol.RUN.force if force is None else force
    store = freshness.load_fingerprints()
    # a missing catalog invalidates every fingerprint -- the rows it claims are
    # current don't exist, so nothing may be skipped (matches stale_sources())
    catalog_missing = not layout.CATALOG.exists()
    # a full rebuild rewrites every row anyway: a missing catalog, or --force over
    # the whole corpus. Build it in a scratch file opened EXCLUSIVE (holds the lock
    # once instead of per statement -- the round-trip cost that dominates a
    # million-row rebuild, and the local-vs-NFS gap -- and drops the journal +
    # fsync), then swap it in atomically. Live readers keep serving the old catalog
    # untouched until the rename. Under --force the per-source `up_to_date` check
    # already returns False, and a missing catalog forbids skipping, so the scratch
    # is guaranteed to receive every requested source (never a partial catalog).
    published = {name for name, s in sources.items() if s.artifacts}
    full_rebuild = catalog_missing or (force and published <= set(names))
    layout.CATALOG.parent.mkdir(parents=True, exist_ok=True)
    target = layout.CATALOG.with_name(layout.CATALOG.name + ".building") if full_rebuild else layout.CATALOG
    if full_rebuild:
        target.unlink(missing_ok=True)   # discard a scratch left by an aborted rebuild
    dirty = False
    for name in names:
        source = sources[name]
        if source.artifacts is None:
            continue
        paths = source.artifacts()
        wm = freshness.file_fingerprint(paths)
        if not catalog_missing and freshness.up_to_date(store, "relate", name, wm,
                                              RELATE_CODE):
            print("relate %s: up to date (%d artifacts unchanged) -- skipped"
                  % (name, len(paths)))
            freshness._emit_segment("relate", name, 0.0, total=len(paths), ran=0,
                          skipped_fresh=len(paths), status="skipped")
            continue

        def progress(seen, total, changed, current, name=name):
            util.status(seen, total, "relate %s  %d changed  %s"
                        % (name, changed, current), actual=changed)

        recode = freshness.code_changed(store, "relate", name, RELATE_CODE)
        if recode and not force:
            print("relate %s: extraction code changed -- re-extracting all" % name)
        t0 = time.perf_counter()
        docs, edges, changed = catalog.rebuild(
            target, name, paths, progress=progress, force=force or recode,
            data_root=DATA, exclusive=full_rebuild)
        freshness._emit_segment("relate", name, time.perf_counter() - t0, total=docs,
                      ran=changed, status="ok")
        freshness.record_step(store, "relate", name, wm, RELATE_CODE)
        dirty = True
        sys.stderr.write("\n")
        print("relate %s: %d documents, %d links (%d re-extracted this run)"
              % (name, docs, edges, changed))

    # cross-document post-passes (need the whole catalog, so they run last):
    # each source's own `relate_cross` contribution, then the corpus-wide ones
    # -- the norm chain, the delegation edges, the regleringshierarki ladders, a
    # stub begrepp node for every defined term / nyckelord the corpus
    # references. Their inputs are the catalog (changed only if a source was
    # re-related above) and the authored layers (the .corr and .ann files a
    # source reads), so a no-op run skips them too -- gated on a fingerprint
    # over all of them.
    corr_wm = _corr_watermark(sources)
    if dirty or force or not freshness.fingerprint_fresh(store, "relate", "__corr__",
                                               corr_wm):
        t0 = time.perf_counter()
        con = catalog.connect(target, data_root=DATA, exclusive=full_rebuild)
        # each source's own contribution first (pinning a genomför-direktiv
        # statement to the paragraf it transposes, loading the .corr layers,
        # auditing a commentary's anchors): they read and write their own rows
        # and are independent of each other and of the corpus-wide passes below
        counts, warnings = {}, []
        for s in sources.values():
            if s.relate_cross:
                more, lines = s.relate_cross(con)
                counts.update(more)
                warnings += lines
        folded = catalog.canonicalize_concepts(con)
        concepts = catalog.synthesize_concepts(con)
        # the norm hierarchy: which rule derives its authority from which. Needs
        # every source related (a chain crosses EU -> lag -> förordning ->
        # föreskrift), so it runs here rather than per source.
        chain = catalog.rebuild_norm_chain(con)
        # ordering invariant: rebuild_norm_chain DELETEs its table, so the
        # derived delegation edges always re-insert after it; the ladder rows
        # store canonical concept uris, so they build after
        # canonicalize_concepts (above) -- never join its UPDATE loop
        delegated, deleg_dup = hierarki.derive_delegation_edges(con)
        ladder_stats = hierarki.rebuild_regleringshierarki(
            con, curated=hierarki.hierarki_layers())
        # The same question the kommentar anchor audit asks of one commentary
        # and its host act, asked of the whole citation graph: a link whose
        # fragment names no node in the document it points at. Its home is here
        # because relate is what writes the `links` rows -- the graph exists for
        # the first time, and the catalog is already open.
        #
        # Worth the pass: run by hand it found 126 treaty references pointing
        # at an `#A42` on a Hague Convention that anchors its Regulations'
        # articles under `#Annex42`, and every count involved -- links written,
        # documents related -- looked healthy throughout.
        # one pass, not two: the scan reads every anchored link and parses each
        # distinct target artifact, so calling it again only to count them cost
        # the whole walk twice inside the nightly build
        dangling = catalog.dangling_anchors(con, ANCHOR_EXACT)
        # materialize each document's inbound count: the serving layer reads a
        # column instead of counting an index range per request (the ECHR's is
        # 1.4M entries -- tens of seconds cold on prod's disk)
        stamped = catalog.stamp_inbound_counts(con)
        con.commit()
        con.close()
        freshness._emit_segment("relate", "__corr__", time.perf_counter() - t0, status="ok")
        freshness.record_fingerprint(store, "relate", "__corr__", corr_wm)
        dirty = True
        print("relate: %d norm-chain relations" % chain)
        print("relate: %d förordning->lag delegation edges derived from the "
              "title pair and the delegation clauses (%d already stated)"
              % (delegated, deleg_dup))
        print("relate: %d regleringshierarki rows over %d ladders "
              "(verbatim %d, aligned labels %d, genomförande %d; %d chain "
              "documents offer no concept, %d definitions sit off the chain, "
              "%d lone ladders dropped)"
              % (ladder_stats["rows"], ladder_stats["ladders"],
                 ladder_stats["verbatim"], ladder_stats["aligned_labels"],
                 ladder_stats["genomforande"],
                 ladder_stats["chain_docs_no_concept"],
                 ladder_stats["defs_off_chain"],
                 ladder_stats["single_dropped"]))
        # the sources' own counts, in registration order
        for label, value in counts.items():
            print("relate: %d %s" % (value, label))
        print("relate: %d inflected concept variants folded onto canonical begrepp"
              % folded)
        print("relate: %d concept stubs minted from defined terms + nyckelord"
              % concepts)
        print("relate: inbound counts stamped on %d cited documents" % stamped)
        # ... and their warnings, which a hook hands over already worded
        for line in warnings:
            print("relate: %s" % line)
        print("relate: %d link(s) point at an anchor their target does not "
              "have (%s -- the sources whose pages offer exactly their "
              "artifact's anchors)" % (len(dangling), ", ".join(ANCHOR_EXACT)))
        for from_uri, to_uri, count in dangling[:DANGLING_REPORT]:
            print("relate: WARNING %s -> %s (%d) -- the document is held, the "
                  "anchor is not in it" % (from_uri, to_uri, count))
    else:
        print("relate: nothing changed -- cross-document passes skipped")
        freshness._emit_segment("relate", "__corr__", 0.0, status="skipped")
    # publish the freshly built scratch over the live catalog atomically -- only now,
    # after every source + the cross-document passes have landed, so a reader never
    # sees a half-built catalog. (Guarded on existence for the degenerate case where
    # no artifact-backed source was requested and nothing was written.)
    if full_rebuild and target.exists():
        _swap_catalog(target, layout.CATALOG)
    if dirty and layout.CATALOG.exists():
        # the /api/v1/path graph as a sidecar beside the catalog (rsync'd with
        # it): the serving process then loads arrays in under a second instead
        # of scanning 15.6M link rows -- which on prod's ~80-IOPS disk is the
        # difference between a warm start and hours (lib/pathgraph)
        t0 = time.perf_counter()
        n, m = pathgraph.write_sidecar(layout.CATALOG)
        print("relate: path graph sidecar written -- %d documents, %d edges "
              "(%.1fs)" % (n, m, time.perf_counter() - t0))
    if dirty:
        freshness.save_fingerprints(store)
    print("catalog: %s" % layout.CATALOG)


# A source whose catalog rows are internal registry rather than reader-facing
# pages declares `searchable=False`: its rows stay in `documents` (relate and
# the source's own verbs read them), the index holds no units for it, and a
# prior index's units are purged. Composed as a field on the source, because a
# search hit that leads nowhere is a property of the source, not of search.
def cmd_index(sources, names, jobs=1):
    """Sync the OpenSearch full-text index for each named source from the catalog
    + artifacts -- a whole-document unit plus one fragment per § node, the
    paragraph-precise search behind the killer feature. Incremental: only new or
    content-changed documents are (re)indexed and vanished ones dropped, so a
    re-run with nothing changed is cheap. Editing the index code (search.py /
    text.py) or passing --force reindexes every document of the affected source.
    `relate` is its prerequisite (run that first). `jobs>1` fans the bulk
    round-trips across threads. Needs a running OpenSearch (OPENSEARCH_URL,
    default http://localhost:9200)."""
    if protocol.RUN.dry_run:
        # named before any connection is opened: a plan must reach neither the
        # cluster nor the catalog
        for name in names:
            if sources[name].artifacts is None:
                continue
            print("index %s: would %s search index '%s' on %s"
                  % (name,
                     "sync the" if sources[name].searchable else "purge its units from the",
                     search.INDEX, config.OPENSEARCH_URL))
        return False
    store = freshness.load_fingerprints()
    dirty = had_errors = False
    # one keep-alive connection per bulk thread, or the pool discards and
    # re-handshakes on every round-trip
    index = search.SearchIndex(pool_maxsize=jobs)
    con = catalog.connect(layout.CATALOG)
    # a dropped index invalidates every fingerprint -- skipping would leave the
    # source's docs unindexed, so nothing may be skipped until it's rebuilt
    index_present = index.exists()
    # the corpus-wide inbound-count map is ~7s to build over a 10M-row link table
    # and identical for every source -- build it once, not once per source
    inbound_counts = catalog.document_inbound_counts(con)
    for name in names:
        source = sources[name]
        if source.artifacts is None:
            continue
        # an unsearched source's step also fingerprints its own registration
        # module (`<package>/source.py`), where `searchable` is declared -- so
        # flipping the flag restales exactly that source's step (the recorded
        # recipe no longer matches), instead of "up to date" leaving stale
        # units indexed indefinitely
        assert source.searchable or source.registration, (
            "%s declares searchable=False but no registration module, so "
            "flipping the flag back would leave stale units indexed" % name)
        code = INDEX_CODE + (() if source.searchable else source.registration)
        wm = catalog.source_content_signature(con, name)
        if index_present and freshness.up_to_date(store, "index", name, wm, code):
            print("index %s: up to date (catalog unchanged) -- skipped" % name)
            freshness._emit_segment("index", name, 0.0, ran=0, status="skipped")
            continue
        if not source.searchable:
            t0 = time.perf_counter()
            index.wait_for_task(index.delete_source_async(name),
                                "purge %s" % name)
            freshness._emit_segment("index", name, time.perf_counter() - t0, total=0,
                          ran=0, status="ok")
            freshness.record_step(store, "index", name, wm, code)
            dirty = True
            print("index %s: not a search source -- stale units purged" % name)
            continue

        def progress(seen, total, current="", name=name):
            util.status(seen, total, "index %s  %s" % (name, current))
        recode = freshness.code_changed(store, "index", name, code)
        if recode and not protocol.RUN.force:
            print("index %s: index code changed -- reindexing all" % name)
        t0 = time.perf_counter()
        docs, indexed, errors, missing, skipped, deleted = index.index_source(
            con, name, progress=progress, jobs=jobs, force=protocol.RUN.force or recode,
            inbound_counts=inbound_counts)
        freshness._emit_segment("index", name, time.perf_counter() - t0, total=docs,
                      ran=indexed, errors=len(errors), skipped_fresh=skipped,
                      status="errors" if errors else "ok")
        freshness.record_step(store, "index", name, wm, code)
        dirty = True
        had_errors |= bool(errors)
        sys.stderr.write("\n")
        print("index %s: %d documents -> %d units indexed, %d up to date, "
              "%d deleted, %d errors"
              % (name, docs, indexed, skipped, deleted, len(errors)))
        if errors:
            _report_index_errors(name, errors)
        if missing:
            print("index %s: %d catalogued artifacts gone from disk, skipped "
                  "(run `lagen %s relate` to prune): %s"
                  % (name, len(missing), name, ", ".join(missing[:5])
                     + (" ..." if len(missing) > 5 else "")))
    con.close()
    if dirty:
        freshness.save_fingerprints(store)
    print("search index '%s' on %s" % (search.INDEX, config.OPENSEARCH_URL))
    return had_errors


INDEX_ERROR_SAMPLES = 3         # distinct reasons shown per failing index run


def _report_index_errors(name, errors):
    """Print what the cluster actually rejected, grouped by reason.

    The bulk helper hands back the failed items in full; the count alone used to
    be all that survived, so a run that lost 70,909 units left nothing to say
    *why* -- the reason had to be re-derived from cluster stats that a restart had
    already rolled over. Grouped rather than dumped: a rejection is systemic (one
    breaker trip fails a whole chunk), so a handful of distinct reasons covers
    tens of thousands of items, and one id per reason is enough to go look."""
    reasons = {}
    for item in errors:
        # the helper reports one {op_type: {_id, status, error}} per failed item;
        # it is untyped, so the shape is read rather than assumed
        info: dict = next(iter(item.values()), {})
        err = info.get("error") or {}
        key = (info.get("status"),
               err.get("type") if isinstance(err, dict) else str(err)[:60])
        seen, sample = reasons.get(key, (0, None))
        reasons[key] = (seen + 1, sample or info.get("_id"))
    for (status, kind), (count, sample) in sorted(
            reasons.items(), key=lambda kv: -kv[1][0])[:INDEX_ERROR_SAMPLES]:
        print("index %s:   %d x [%s] %s (e.g. %s)"
              % (name, count, status, kind, sample))
    if len(reasons) > INDEX_ERROR_SAMPLES:
        print("index %s:   ... and %d more distinct reasons"
              % (name, len(reasons) - INDEX_ERROR_SAMPLES))


def cmd_dump(sources, names):
    """Write a gzipped NDJSON bulk dump per named source -- every artifact, one
    compact JSON per line, byte-equivalent to the on-disk artifact (the citation
    graph is already inline, so each line is self-contained). The machine-
    readable corpus export that replaces the retired RDF/Fuseki dumps."""
    if protocol.RUN.dry_run:
        _plan_artifact_verb("dump", sources, names,
                            lambda name: DUMPS / ("%s.ndjson.gz" % name))
        return
    DUMPS.mkdir(parents=True, exist_ok=True)
    store = freshness.load_fingerprints()
    dirty = False
    for name in names:
        source = sources[name]
        if source.artifacts is None:
            continue
        out = DUMPS / ("%s.ndjson.gz" % name)
        paths = source.artifacts()
        wm = freshness.file_fingerprint(paths)
        if out.exists() and freshness.up_to_date(store, "dump", name, wm, DUMP_CODE):
            print("dump %s: up to date (%d artifacts unchanged) -- skipped"
                  % (name, len(paths)))
            freshness._emit_segment("dump", name, 0.0, total=len(paths), ran=0,
                          skipped_fresh=len(paths), status="skipped")
            continue

        def progress(seen, total, name=name):
            util.status(seen, total, "dump %s" % name)
        t0 = time.perf_counter()
        lines = dump.dump_source(paths, out, progress=progress)
        freshness._emit_segment("dump", name, time.perf_counter() - t0, total=lines,
                      ran=lines, status="ok")
        freshness.record_step(store, "dump", name, wm, DUMP_CODE)
        dirty = True
        sys.stderr.write("\n")
        print("dump %s: %d documents -> %s" % (name, lines, out))
    if dirty:
        freshness.save_fingerprints(store)


def _harvest_counts(result):
    """`(seen, changed)` from what a harvest returned, or `(None, None)`.

    Every harvest returns that pair -- documents the sweep enumerated upstream,
    and documents it actually wrote -- summing its own scopes first, because
    only the harvest knows whether its numbers are per agency, per sector or per
    doctype. A dry run returns nothing and counts nothing.

    The pair reaches the ledger as a segment's (total, ran), the same measures
    every other step reports: how much there was, and how much did work."""
    if result is None:
        return None, None
    seen, changed = result
    return seen, changed


def _run_harvest(source, scopes):
    """Run one source's bulk discovery harvest, banner and ledger segment
    included; True when it failed. `scopes` narrows the sweep to named
    sub-corpora (forarbete doctypes, eurlex sectors) and is empty for a full
    discovery run. The one home of the harvest call, shared by `cmd_download_all`
    and the single-source dispatch so the two can't drift."""
    name = source.name
    if source.origin and not source.self_banner:
        label = "%s %s" % (name, "/".join(scopes)) if scopes else name
        util.harvest_start("%s download" % label, source.origin)
    t0 = time.perf_counter()
    try:
        # every source computed "N seen, M changed" and printed it; the ledger
        # recorded neither, so the dashboard could not say what a nightly
        # download brought in (all 70 prod segments carried total=null, ran=null)
        seen, changed = _harvest_counts(source.harvest(scopes))
        freshness._emit_segment("download", name, time.perf_counter() - t0,
                      total=seen, ran=changed, status="ok")
    except Exception:  # noqa: BLE001 — per-source resilience point: one source's harvest failure must not abort the remaining sources; printed + nonzero exit at end (rule:no-catch-log-continue)
        traceback.print_exc()
        freshness._emit_segment("download", name, time.perf_counter() - t0,
                      status="errors", errors=1)
        return True
    return False


def cmd_download_all(sources, names, jobs):
    """Upstream discovery + fetch for each named source: the bulk harvest where a
    source has one (sweeping in newly-published documents), else its per-document
    download stage over the ids it already knows. The slow, network-bound head of
    the pipeline -- kept separate from `rebuild` so the offline rebuild stays fast.
    A source derived from another's dump (kommentar/begrepp) has nothing to fetch
    and is skipped."""
    had_errors = False
    for name in names:
        source = sources[name]
        if source.harvest is not None:
            had_errors |= _run_harvest(source, [])       # [] = full discovery
        elif "download" in source.stages:
            basefiles = source.list_basefiles()
            result = freshness.run_action(source, "download", basefiles, jobs)
            report(source, "download", result, len(basefiles), full_source=True)
            had_errors |= bool(result.errors)
        else:
            continue
        run_after(sources, [name], "download")
    return had_errors


def _run_stage_gated(source, step, jobs, store):
    """Run a fingerprint-gated per-document stage (parse/versions) over a whole
    source. Coarse gate: if the stage's inputs + recipe are unchanged, skip the
    per-doc freshness scan (which content-hashes every input) wholesale -- "up to
    date -- skipped"; else run it and, on a clean sweep, record the fingerprint in
    `store` so the next run can skip. Shared by `cmd_all` and the single-source
    dispatch so a direct `lagen <src> parse` gets the same shortcut. Returns
    (had_errors, recorded) -- `recorded` tells the caller to save `store`."""
    pcode = source.stages[step].code
    wm = freshness.stage_fingerprint(source, step)
    if freshness.up_to_date(store, step, source.name, wm, pcode):
        print("%s %s: up to date -- skipped" % (step, source.name))
        # bypasses report(); emit the skipped segment so the run detail still
        # shows the whole pipeline (§2)
        freshness._emit_segment(step, source.name, 0.0, ran=0, status="skipped")
        return False, False
    basefiles = source.list_basefiles()
    result = freshness.run_action(source, step, basefiles, jobs)
    report(source, step, result, len(basefiles), full_source=True)
    # only fingerprint a clean sweep: a failed doc leaves the source un-marked so
    # the next run retries it (and re-surfaces the error) rather than skipping.
    # A dry run does no work at all, so it must not mark the source either --
    # `lagen eurlex parse -n` after a parser edit printed a 64,004-document plan
    # and then recorded the new recipe version, so the real run that followed
    # answered "up to date -- skipped" over the whole stale artifact tree.
    if result.errors or protocol.RUN.dry_run:
        return bool(result.errors), False
    freshness.record_step(store, step, source.name, wm, pcode)
    return False, True


def run_after(sources, names, verb):
    """Run each named source's corpus-level `after[verb]` hooks -- the work a
    source hangs off a standard verb (dv reconciles its artifact tree and
    refreshes the case-number snapshot once its parse sweep is through). Called
    with one name from the parse/versions loop, and with the whole run's names
    after each corpus verb."""
    for name in names:
        for hook in sources[name].after.get(verb, ()):
            hook()


def run_phase(sources, names, verb, jobs):
    """Run every stage of the named sources that declares `phase == verb` -- a
    per-document stage that belongs after a corpus verb rather than in the
    leading parse/versions loop (`stats compute` measures the catalog relate has
    just rebuilt). True when one of them errored."""
    had_errors = False
    for name in names:
        source = sources[name]
        for stage_name, stage in source.stages.items():
            if stage.phase != verb:
                continue
            basefiles = source.list_basefiles()
            result = freshness.run_action(source, stage_name, basefiles, jobs)
            report(source, stage_name, result, len(basefiles), full_source=True)
            had_errors |= bool(result.errors)
    return had_errors


def cmd_all(sources, names, jobs, *, whole_corpus, download=False, aggregates):
    """Run the build pipeline for the named sources. The offline core (action
    `rebuild`) is parse -> relate -> index -> dump -> generate; action `all`
    prepends the network-bound download. Each step is independently
    incremental, so a re-run with nothing changed is cheap.

    parse runs over each source's already-downloaded basefiles (bringing only
    missing/stale parses up to date; with `download=False` it discovers nothing
    new, so it makes no network calls). relate/index/dump act on the named
    sources; generate rebuilds the whole corpus when the run targets `all`
    sources, else just the named sources' pages.

    A source hangs its own work off any of these verbs: a per-document stage
    whose `phase` names the verb (run right after it), and an `after[verb]`
    hook (run once per source). Both are fields on the registration; this
    function knows neither what they do nor which source has one."""
    had_errors = False
    if download:
        had_errors = cmd_download_all(sources, names, jobs)
    store = freshness.load_fingerprints()
    for step in ("parse", "versions"):
        for name in names:
            source = sources[name]
            if step not in source.stages:
                continue
            errs, recorded = _run_stage_gated(source, step, jobs, store)
            had_errors |= errs
            # save as soon as a source records, not once the whole loop is
            # through: a kill during a later source's parse used to discard the
            # gate for every source that had already finished cleanly, so the
            # next run re-scanned (and re-hashed the inputs of) all of them for
            # nothing. The artifacts themselves were never at risk -- the
            # per-document manifest checkpoints every SAVE_EVERY docs and
            # flushes in a finally -- but the wasted scan is minutes on a
            # 100k-document source. The store is a handful of keys per source,
            # so writing it per source is free.
            if recorded:
                freshness.save_fingerprints(store)
            run_after(sources, [name], step)
    cmd_relate(sources, names)
    run_after(sources, names, "relate")
    # a bulk item the cluster rejected is a *unit missing from search*, so it
    # belongs in the run's verdict like a failed parse -- one rebuild dropped
    # 1,497 eurlex and 241 förarbete documents from the index and still exited 0
    had_errors |= cmd_index(sources, names, jobs)
    run_after(sources, names, "index")
    cmd_dump(sources, names)
    run_after(sources, names, "dump")
    # a stage that must run after the catalog and the dumps exist, not in the
    # parse loop above -- it reads what relate and dump have just written. The
    # stage says so itself (`phase="dump"`); a run that does not name its source
    # never pays for it (see the stats registration for the worked example).
    had_errors |= run_phase(sources, names, "dump", jobs)
    if whole_corpus:
        cmd_generate(sources, jobs=jobs, aggregates=aggregates)
    else:
        for name in names:
            cmd_generate(sources, source=name, jobs=jobs, aggregates=aggregates)
    run_after(sources, names, "generate")
    return had_errors


def stale_sources(sources):
    """Sources whose artifacts have changed since the catalog was last built
    (make's rule: a prerequisite newer than the target). A missing catalog
    makes every source stale; --force re-relates all."""
    if protocol.RUN.force or not layout.CATALOG.exists():
        return [name for name, s in sources.items() if s.artifacts]
    cutoff = layout.CATALOG.stat().st_mtime
    # artifacts are stored precompressed (.json.br); the listers yield logical
    # .json names, so stat the real backing file (compress.stat) -- a plain
    # p.stat() would raise FileNotFoundError on every compressed tree, exactly
    # as file_fingerprint() already does when fingerprinting these same paths
    return [name for name, s in sources.items() if s.artifacts
            and any(compress.stat(p).st_mtime > cutoff for p in s.artifacts())]


# a page's rendered HTML is a function of the render/query code plus the
# artifacts in its prerequisite set (computed per page from the catalog)
# generate renders the per-document pages (each source's renderer + the lib/page
# kit it walks the artifact with) AND, via the sanctioned in-process API
# inversion, the faceted browse pages -- so facets.py (the bucket rules) and
# api/app.py (the /browse projection) are part of generate's recipe: a facet-rule
# edit must re-stale the browse pages, not leave them "up to date -- skipped".
# page.py's margin builders (the rail's cross-document context) live in
# margins.py, and catalog's row/snippet building in catalog_rows.py: both are
# part of what a page's HTML is a function of, so both must be listed here.
GENERATE_CODE = (PKG / "lib" / "page.py", PKG / "lib" / "margins.py",
                 PKG / "lib" / "catalog.py", PKG / "lib" / "catalog_rows.py",
                 PKG / "lib" / "text.py", PKG / "lib" / "feeds.py",
                 PKG / "lib" / "markdown.py", PKG / "lib" / "layout.py",
                 # page.py builds its Site indexes (and wiki/render.py its
                 # ladders) through hierarki; a top-level lib module the
                 # */render.py glob does not reach
                 PKG / "lib" / "hierarki.py",
                 PKG / "lib" / "history.py", PKG / "lib" / "casenaming.py",
                 PKG / "lib" / "eu_structure.py", PKG / "lib" / "facets.py",
                 PKG / "lib" / "labels.py", PKG / "lib" / "tpl.py",
                 # generate also writes each page's inbound-citation file
                 # (render._write_inbound), so a change to what goes in one or
                 # how it is ordered has to re-stale the pages that carry it
                 PKG / "lib" / "inbound.py",
                 PKG / "api" / "app.py", PKG / "stats" / "charts.py",
                 # every page renderer: each source's own (`Source.render`),
                 # lib's site-assembly one, plus the editorial and statistics
                 # ones. Globbed rather than listed -- a new source's renderer
                 # joins the recipe by existing, not by someone remembering to
                 # add it here. Forgetting one would not fail loudly; it would
                 # silently stop re-staling that source's pages.
                 *sorted(PKG.glob("*/render.py")),
                 # site/browse.py owns the faceted-browse markup and
                 # site/subdomains.py the definite-form subdomain projections.
                 # Neither is named render.py, so the */render.py glob above
                 # does not reach them; both must re-stale generate the way
                 # facets.py and api/app.py do. namedlaws.json/namedacts.json
                 # are deliberately not listed: one combined recipe hash covers
                 # every page, and a 200-row lookup edit must not force a full
                 # corpus re-render (write_sub_tree runs on every full generate
                 # anyway).
                 PKG / "site" / "browse.py",
                 PKG / "site" / "subdomains.py",
                 # the shipped static chrome (incl. the self-hosted fonts):
                 # a stylesheet/script/font edit must re-stale generate
                 # exactly like a renderer edit
                 *sorted(p for p in (PKG / "lib" / "assets").rglob("*")
                         if p.is_file()),
                 # the Jinja templates every page renders through: a template
                 # edit is a renderer edit (rule:artifact-is-truth for pages).
                 # api/templates is deliberately out -- those are the admin/ops
                 # screens, which no generated page renders through, and an edit
                 # to one must not cost a ~300k-page regenerate. PKG is absolute,
                 # so the package-relative first segment is what names the owner.
                 *sorted(p for p in PKG.glob("*/templates/**/*.html")
                         if p.relative_to(PKG).parts[0] != "api"))


def generate_fingerprint(sources):
    """The coarse gate for a full-corpus generate: the whole-catalog content
    signature plus the .corr/.ann LLM layers (lib.annstore) and .versions.json
    sidecars that relate doesn't fold into content_hash, plus the set of
    currently-effective repeal dates. Unchanged (with the render code) ⟹ every page is fresh, so the
    ~100k-page freshness scan can be skipped wholesale."""
    con = catalog.connect(layout.CATALOG)
    sig = catalog.catalog_signature(con)
    # a statute's repeal is presented against *today* (page watermark, browse
    # listings), so the day an upphavandedatum passes the gate must reopen even
    # though no file changed -- fold the currently-expired uri set in
    # (rule:respect-source-temporality)
    expired = "\x1f".join(sorted(
        catalog.expired_uris(con, date.today().isoformat())))
    con.close()
    # the side files no catalog row records -- each source's `layers`: the
    # LLM layers in the curated store (lib.annstore, WIKI_ROOT/ann), the
    # versions-stage sidecars, the remiss answers and the site artifacts. A
    # layer that rides another document's rail enters that page's dependency
    # digest per page (page.site_cross_digests); here it reopens the coarse gate
    sides = freshness.file_fingerprint(_layers(sources))
    return hashlib.sha256(
        (sig + "\x1f" + sides + "\x1f" + expired).encode()).hexdigest()


def write_source_pages(sources):
    """Let every source that publishes pages of its own -- not catalog rows --
    write them into the generated tree. Part of a full-corpus run, so a source
    whose artifact does not exist yet writes nothing rather than raising: a
    corpus that has never run the source's own producing action simply has no
    such page. Asking for those pages by name (`lagen stats generate`) takes the
    other path in `cmd_generate` and does raise."""
    for s in sources.values():
        if s.write_pages:
            s.write_pages(layout.GENERATED, whole_corpus=True)


def cmd_generate(sources, only=None, source=None, jobs=1, force=False, *,
                 aggregates):
    """Render every catalogued document to static HTML, with live outbound
    links and inbound annotations queried from the catalog, plus a frontpage.
    Auto-runs `relate` first for any source whose artifacts are newer than the
    catalog -- relate is generate's upstream dependency.

    Incremental like parse: a page is re-rendered only when its prerequisite
    artifacts (itself + the documents citing it + the documents it cites) or the
    render code changed. `--force` rebuilds all; `--ignore-code-changes` ignores
    the render-code version (rebuild only on data changes).

    `source` restricts the run to one source's pages (`lagen <source> generate`);
    `only`, a set of artifact path strings, restricts it to those documents
    (`lagen <source> generate <id>`). Either scoping leaves the corpus-wide
    aggregate pages as they are and uses the catalog as-is (no auto-relate).

    `--aggregates-only` rewrites just the corpus-wide pages (frontpage + browse
    indexes) from the current catalog, skipping the per-document render -- a
    seconds-long refresh after a frontpage/browse change, not a full rebuild.

    `--assets-only` copies only the static chrome (style.css + the scripts) --
    the minimal refresh after a CSS/JS change, which the HTML links by URL and so
    never has to be re-rendered for. Needs no catalog and no relate.

    `force=True` (the editor's post-commit rebuild) renders the scoped pages
    unconditionally: they are dirty by construction (the request just committed
    an edit onto them), so the freshness check is skipped rather than trusted.

    `aggregates(con, *, full)` writes the corpus-wide pages this module cannot:
    they are rendered through the REST API in-process, which lives a layer above
    `lib/`, so the caller supplies them. `full=False` is the `--aggregates-only`
    subset."""
    if protocol.RUN.dry_run:
        # before the auto-relate, the coarse gate and the catalog connection
        # each of the branches below opens
        if protocol.RUN.assets_only:
            print("generate: would copy the static assets (style.css + "
                  "scripts) -> %s" % layout.GENERATED)
        elif only is not None:
            print("generate: would render %d requested document(s) -> %s"
                  % (len(only), layout.GENERATED))
        elif source is not None:
            print("generate %s: would render the source's pages -> %s"
                  % (source, layout.GENERATED))
        elif protocol.RUN.aggregates_only:
            print("generate: would render the frontpage + browse indexes + "
                  "site pages -> %s" % layout.GENERATED)
        else:
            print("generate: would render every catalogued page, the frontpage "
                  "+ browse indexes, the subdomain projections and the site "
                  "pages -> %s" % layout.GENERATED)
        return
    # segment source: the whole-site run reports under __site__, a scoped
    # per-source render (`lagen <src> generate`) under that source's name
    seg_source = source or "__site__"
    t0 = time.perf_counter()
    if source is not None and sources[source].write_pages is not None:
        # `lagen site generate` / `lagen stats generate`: the source's pages are
        # not catalog rows (editorial chrome, one measurement artifact), so the
        # generic per-document/aggregate paths below have nothing of theirs to
        # render -- its own writer is the whole of it. `whole_corpus=False`
        # tells the writer this run asked for those pages by name, so a missing
        # artifact is an error rather than a page the corpus does not have yet.
        sources[source].write_pages(layout.GENERATED, whole_corpus=False)
        print("generate: rebuilt %s pages -> %s" % (source, layout.GENERATED))
        freshness._emit_segment("generate", source, time.perf_counter() - t0, status="ok")
        return
    if protocol.RUN.assets_only:
        render.write_assets(layout.GENERATED)
        print("generate: copied static assets (style.css + scripts) -> %s" % layout.GENERATED)
        freshness._emit_segment("generate", "__site__", time.perf_counter() - t0, status="ok")
        return
    # a source may own the frontpage (site's curated one), in which case the
    # generic corpus-stats index.html is suppressed
    write_index = not any(s.owns_frontpage() for s in sources.values()
                          if s.owns_frontpage)
    if protocol.RUN.aggregates_only:
        con = catalog.connect(layout.CATALOG)
        render.render_aggregates(con, layout.GENERATED, write_index=write_index)
        aggregates(con, full=False)
        con.close()
        write_source_pages(sources)
        print("generate: rebuilt frontpage + browse indexes + site pages -> %s" % layout.GENERATED)
        freshness._emit_segment("generate", "__site__", time.perf_counter() - t0, status="ok")
        return

    # a full generate auto-relates any stale source first (relate is its upstream
    # dependency); a scoped render skips that corpus-wide scan and uses the catalog
    # as-is -- run `lagen <source> relate` to refresh it
    scoped = only is not None or source is not None
    stale = [] if scoped else stale_sources(sources)
    if stale:
        print("catalog stale for %s -- relating first" % ", ".join(stale))
        cmd_relate(sources, stale)

    # full-corpus generate: a coarse gate over the whole catalog + .corr/.ann
    # layers + render code. All unchanged since the last full generate ⟹ every
    # page is fresh, so skip the per-page scan entirely (the manifest, big, isn't
    # even loaded). A scoped render keeps the per-page path.
    site_wm = None
    if not scoped:
        store = freshness.load_fingerprints()
        site_wm = generate_fingerprint(sources)
        if freshness.up_to_date(store, "generate", "__site__", site_wm, GENERATE_CODE):
            print("generate: up to date -- skipped (%s)" % layout.GENERATED)
            freshness._emit_segment("generate", "__site__", 0.0, ran=0, status="skipped")
            return

    manifest = freshness.load_manifest()
    code_version = freshness.recipe_version(GENERATE_CODE)
    updates = {}
    own_hash = {}                # artifact path -> content hash, memoized per run

    def page_signature(art_path, dep_digest, content_hash):
        # only the page's OWN artifact enters the signature (it changes when the doc
        # is re-parsed); its neighbours enter via dep_digest as a set of
        # relationships, not their contents -- an immutable case re-appearing
        # unchanged must not invalidate every law it cites. The artifact's bytes are
        # NOT re-read here: relate already stored their sha256 as the catalog's
        # `content_hash`, so generate reuses it instead of re-hashing all ~6.3 GB in
        # the single-threaded planning loop (§2.1). Only the page's own LLM layers
        # (lib.annstore, mirrored under WIKI_ROOT/ann) are read from disk (they
        # aren't catalogued), so authoring or editing one re-renders just that
        # page: `.ann` (eurlex ai-annotate) and `.corr` (sfs
        # ai-correspond, the new statute's corresponding-cases margin). Content that
        # renders onto OTHER documents' pages (kommentar prose/.ann, remiss .ann,
        # the old-law side of `.corr`) enters via `dep_digest`, which generate_site
        # folds page.site_cross_digests into per host uri.
        p = str(art_path)
        if p not in own_hash:
            # a synthesized concept stub has no artifact on disk (empty path) and so
            # no sibling layers; an uncatalogued page (sfs historical consolidation)
            # has an artifact but no catalog content_hash, so its bytes are hashed
            # directly. .versions.json is the sfs versions-stage sidecar: an archived
            # consolidation appearing re-renders the statute's page (its version
            # panel lists the new lydelse).
            fp = Path(p) if p else None
            sides = ((annstore.for_artifact(fp), annstore.for_artifact(fp, ".corr"),
                      fp.with_suffix(".versions.json")) if fp else ())
            base = content_hash if content_hash is not None else (
                catalog.content_hash(compress.read_bytes(fp))
                if fp and compress.exists(fp) else "")
            own_hash[p] = hashlib.sha256(base.encode() + b"".join(
                s.read_bytes() if s.exists() else b"" for s in sides)).hexdigest()
        return hashlib.sha256((own_hash[p] + dep_digest).encode()).hexdigest()

    def fresh(uri, out_path, art_path, dep_digest, content_hash):
        # `force` (the editor's dirty-by-construction pages) and --force both
        # bypass the signature check entirely
        if force or protocol.RUN.force or not compress.exists(out_path):  # page stored precompressed
            return False
        entry = manifest.get(freshness.manifest_key("generate", "page", uri))
        return bool(entry) \
            and entry["inputs"] == page_signature(art_path, dep_digest, content_hash) \
            and (protocol.RUN.ignore_code_changes or entry["version"] == code_version)

    def record(uri, art_path, dep_digest, content_hash):
        updates[freshness.manifest_key("generate", "page", uri)] = {
            "inputs": page_signature(art_path, dep_digest, content_hash),
            "version": code_version}

    def progress(done, total, current="", rendered=0):
        util.status(done, total, "generate  %d rendered  %s" % (rendered, current),
                    actual=rendered)

    # the pages a source publishes beside its catalogued documents -- the sfs and
    # EU-act lydelser, the föreskrift /grund text -- ride along whenever the run
    # covers that source: the whole corpus, the source itself, or specific
    # documents of it (`only`, which the source filters its own rows by)
    extra = []
    for name, s in sources.items():
        if s.extra_pages and (only is not None or source in (None, name)):
            extra += s.extra_pages(only)
    # each source's own page renderer, by catalog source key: `generate_site`
    # renders a document through the renderer its source registered. A source
    # with none publishes no page of its own and its rows are dropped.
    renderers = {name: s.render for name, s in sources.items() if s.render}
    total, rendered = render.generate_site(layout.CATALOG, layout.GENERATED, renderers,
                                           progress=progress,
                                           fresh=fresh, record=record, only=only,
                                           source=source, jobs=jobs, extra=extra,
                                           write_index=write_index)
    if not scoped:                       # editorial pages ride a full-corpus run
        # the faceted browse tree and the subdomain projections: generated as a
        # client of the REST API, which lives a layer above lib/, so the caller
        # hands them in
        con = catalog.connect(layout.CATALOG)
        aggregates(con, full=True)
        con.close()
        # ... and each source that publishes pages of its own rather than
        # catalog rows (the editorial chrome, /statistik), which `generate_site`
        # above never reaches. Without this a rebuild recomputed the corpus
        # measurements and then published the previous run's page -- fresh
        # numbers on disk, stale numbers on screen.
        write_source_pages(sources)
    sys.stderr.write("\n")
    if updates:
        manifest.update(updates)
    if not scoped:                       # record the site fingerprint for next time
        freshness.record_step(store, "generate", "__site__", site_wm, GENERATE_CODE)
        freshness.save_fingerprints(store)
    freshness._emit_segment("generate", seg_source, time.perf_counter() - t0, total=total,
                  ran=rendered, status="ok")
    if only is not None and not total:
        print("generate: no catalogued document matched %d requested id(s) -- "
              "parse/relate them first" % len(only))
        return
    print("generate: %d pages (%d rendered, %d fresh)%s -> %s"
          % (total, rendered, total - rendered,
             " [scoped; aggregates untouched]" if scoped else "", layout.GENERATED))
    if not scoped:
        print("serve with: lagen all serve   (then open http://localhost:8000/)")


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------

def status_scan(source, manifest, errors):
    """Structured per-stage status for one source: {total, fresh, stale, missing,
    empty, failed} per stage, `failed` a list of the basefiles with a live
    errors.json entry (so "failed" ≠ "never tried"). `empty` counts zero-byte
    outputs -- the SkipDocument marker (a deliberately empty document), the stat
    already paid by the exists-check. `errors` is the errors.json store keyed
    "<source>/<stage>/<basefile>". Lives here (not lib) with the `status` verb
    that prints it; the freshness rules it reads live in `lib/freshness`."""
    basefiles = source.list_basefiles()
    out = {}
    for name, stage in source.stages.items():
        fresh = stale = missing = empty = 0
        failed = []
        for bf in basefiles:
            if errors.get("%s/%s/%s" % (source.name, name, bf)):
                failed.append(bf)
            output = stage.output(bf)
            if not compress.exists(output):  # the output may be stored precompressed
                missing += 1
            elif compress.stat(output).st_size == 0:
                empty += 1
            elif freshness.is_fresh(manifest, source, stage, bf):
                fresh += 1
            else:
                stale += 1
        out[name] = {"total": len(basefiles), "fresh": fresh, "stale": stale,
                     "missing": missing, "empty": empty, "failed": failed}
    return out


def cmd_status(source):
    """Full, authoritative recompute of `source`'s per-stage health, printed and
    written to status.json as the exact snapshot cells (the CLI-only exact writer;
    the cheap per-segment writer in report() covers full-source pipeline runs)."""
    manifest = freshness.load_manifest()
    scan = status_scan(source, manifest, runlog.read_errors(freshness.ERRORS))
    total = next(iter(scan.values()))["total"] if scan else 0
    print("%s: %d basefiles" % (source.name, total))
    for name, st in scan.items():
        print("  %-10s %6d fresh  %6d stale  %6d missing  %6d failed  %6d empty"
              % (name, st["fresh"], st["stale"], st["missing"],
                 len(st["failed"]), st["empty"]))
        runlog.update_status_cell(freshness.STATUS, source.name, name, {
            "total": st["total"], "fresh": st["fresh"], "stale": st["stale"],
            "missing": st["missing"], "failed": len(st["failed"]),
            "empty": st["empty"], "run": freshness.RUN_ID})


def cmd_status_document(source, basefile):
    """`lagen status <source> <basefile>` -- the per-document troubleshooting view:
    each stage's freshness (fresh/stale/missing/empty, and whether its last run is
    recorded as failed) for this one basefile, plus, from the parsed artifact, its
    identity (uri, source_url) and the four reader-facing name forms (lib.labels)
    so a label surprise on the page can be diagnosed without opening the artifact."""
    manifest = freshness.load_manifest()
    errors = runlog.read_errors(freshness.ERRORS)
    print("%s %s" % (source.name, basefile))
    for name, stage in source.stages.items():
        output = stage.output(basefile)
        if not compress.exists(output):
            state = "missing"
        elif compress.stat(output).st_size == 0:
            state = "empty"
        elif freshness.is_fresh(manifest, source, stage, basefile):
            state = "fresh"
        else:
            state = "stale"
        failed = "  (last run FAILED)" \
            if errors.get("%s/%s/%s" % (source.name, name, basefile)) else ""
        print("  %-10s %-8s%s" % (name, state, failed))
    art_path = layout.artifact(source.name, basefile)
    if not compress.exists(art_path):
        print("  (no artifact on disk -- not parsed yet)")
        return
    art = compress.read_json(art_path)
    lb = labels.document_labels(source.name, art)
    print("  uri             %s" % art.get("uri"))
    print("  source_url      %s" % (art.get("source_url") or "-"))
    print("  short_id        %s" % lb.short_id)
    print("  short_title     %s" % (lb.short_title or "-"))
    print("  descriptive     %s" % lb.descriptive_label)
    print("  official_title  %s" % lb.official_title)


def cmd_ann_status():
    """`lagen ann status` -- inventory the curated LLM-layer store (lib.annstore):
    every `.ann`/`.corr`/`.graphics` layer with its status, model, authoring date
    and staleness. Stale = the recorded input hashes no longer match the
    artifacts on disk: a *generated* or *derived* layer can simply be re-run; a
    *verified* one has hand curation authored against drifted data and needs
    human re-review -- it is never regenerated mechanically (--force overrides)."""
    rows = 0
    counts = Counter()
    for p in annstore.entries():
        meta = annstore.read_meta(p)     # the store's status policy, one home
        drift = annstore.drifted(meta.get("inputs", {}))
        counts[meta["status"]] += 1
        if drift:
            counts["stale"] += 1
        rows += 1
        print("%-9s %-10s %s%s"
              % (meta["status"], meta.get("generated", "-"),
                 p.relative_to(annstore.ROOT),
                 "  STALE: %s" % ", ".join(drift) if drift else ""))
    print("ann status: %d layer(s) in %s -- %s, %d stale"
          % (rows, annstore.ROOT,
             ", ".join("%d %s" % (counts[st], st) for st in annstore.STATUSES),
             counts["stale"]))


def report(source, action, result, requested, full_source):
    """Print one action's outcome and fold it into the run instrumentation:
    emit the (action, source) segment, apply the per-doc outcomes to errors.json
    and -- only when the run covered the whole source (`full_source`, no explicit
    basefile args) -- write the cheap status.json cell. All emissions are no-ops
    without a run id (--dry-run, non-pipeline verbs)."""
    verb = "would run" if protocol.RUN.dry_run else "ran"
    # planned already contains every errored basefile (ensure() plans before it
    # runs), so subtract the *union* -- subtracting both sets double-counted
    # errored docs and undercounted "skipped (fresh)" (negative on error-heavy runs)
    skipped = requested - len({bf for _, bf in result.planned}
                              | {bf for _, bf, _, _ in result.errors})
    print("%s %s (%d basefiles): %s %d, skipped (fresh) %d, errors %d" % (
        source.name, action, requested, verb, len(result.planned),
        skipped, len(result.errors)))
    for stage, bf, msg, _tb in result.errors[:20]:
        print("  ERROR %s %s: %s" % (stage, bf, msg))
    secs = sum(s for _, _, s in result.timings)
    slowest = sorted(((bf, s) for _, bf, s in result.timings),
                     key=lambda x: x[1], reverse=True)
    freshness._emit_segment(action, source.name, secs, total=requested,
                  ran=len(result.done), errors=len(result.errors),
                  skipped_fresh=skipped, skipdoc=len(result.skips),
                  status="errors" if result.errors else "ok", slowest=slowest)
    # clear stale errors for docs (re)built this run AND for docs skipped as
    # fresh -- both mean the doc now has a valid artifact and is not failing
    freshness._apply_outcomes(source.name, result.errors, result.done + result.fresh)
    # a full-source run proves the current basefile set is complete, so error
    # entries for basefiles it no longer lists are orphans (a doc left the corpus,
    # or an enumerator bug once emitted it) -- drop them, since they are never
    # re-run and fresh-skip healing can't reach them
    if full_source:
        freshness._reconcile_orphans(source.name, source.list_basefiles())
    if freshness.RUN_ID is not None:
        # scope the failing count to THIS source -- a `lagen dv parse` must not
        # report another source's errors (the store holds every source's)
        prefix = source.name + "/"
        failing = sum(1 for k in runlog.read_errors(freshness.ERRORS)
                      if k.startswith(prefix))
        print("%d docs failing in %s" % (failing, source.name))
    # cheap cell: a full-source run proves the source -- everything planned+done
    # is now fresh, nothing missing (§1c). A targeted run must NOT touch the cell.
    if full_source:
        freshness._update_status_cell(source.name, action, {
            "total": requested, "fresh": requested - len(result.errors),
            "stale": 0, "missing": 0, "failed": len(result.errors),
            "empty": len(result.skips), "run": freshness.RUN_ID})
