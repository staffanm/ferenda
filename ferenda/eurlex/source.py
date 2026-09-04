"""The eurlex source's registration: EU treaties, legislation and case law
from CELLAR, keyed by CELEX.

Three stages -- a per-CELEX download, a parse that presents the latest
consolidated wording, and a versions stage that parses every superseded
consolidation into its own artifact -- plus the verbs that keep the corpus
honest: a citation-ranked backfill, a repeal audit, a bulk-dump import.
"""

import functools
import json
import sys
from pathlib import Path

from lxml import etree  # ty: ignore[unresolved-import]  # lxml ships no stubs

from ..lib import (
    aireport,
    annstore,
    catalog,
    cellar,
    compress,
    eu_structure,
    layout,
    llm,
    markup,
    util,
)
from ..lib import stage as protocol
from ..lib.datasets import NAMEDEUCASES
from ..lib.errors import SkipDocument
from ..lib.formex import formex_intermediate, formex_members
from ..lib.stage import (
    CITATION_DATA,
    POLITENESS,
    Source,
    Stage,
    fanout_key,
    origin,
    patch_input,
    split_fanout_key,
    sum_scope_totals,
    write_artifact,
)
from . import (
    annotate,
    asgit,
    bulk,
    casenames,
    correspond,
    download,
    parse,
    render,
)
from .parse import (
    base_preamble_for,
    content_file,
    parse_consolidation,
    to_artifact,
    version_dirs,
)

HERE = Path(__file__).parent

EURLEX_CODE = (HERE / "parse.py", HERE / "parse_html.py",
               HERE.parent / "lib" / "formex.py",
               HERE / "correspond.py",
               HERE / "parse_pdf.py", HERE / "lang.py",
               HERE / "model.py", HERE / "structure.py",
               # the defined terms and their anchors are stamped into the
               # artifact (`defines`), so this is a parse input like the rest --
               # it had been restaling only by riding along with parse.py edits
               HERE / "definitions.py",
               # the shared anchor grammar the parser stamps ids from
               HERE.parent / "lib" / "eu_structure.py",
               # the notice.ttl readers: the work date that fills in a missing
               # document date, and the repeal date stamped as `expired`
               HERE.parent / "lib" / "cellar.py",
               # the pre-Formex tier reads its body through the shared PDF
               # machinery (parse_pdf imports pdftext.flat_lines), so an edit
               # there changes those artifacts exactly as it does forarbete's
               HERE.parent / "lib" / "pdftext.py",
               # a patched act's Formex/OJ markup is normalised through this
               # before the patch is applied, so it decides what it parses from
               HERE.parent / "lib" / "markup.py",
               HERE.parent / "lib" / "lagrum.py", HERE.parent / "lib" / "emdref.py",
               *CITATION_DATA)


def eurlex_notice(basefile):
    """The tree-notice graph -- the freshness marker for a downloaded CELEX."""
    return layout.eurlex_dir(basefile) / "notice.ttl"


def eurlex_parse_notices(basefile):
    """The notices `parse` reads for one CELEX: its own, plus the base act's
    when this is a '(NN)' revision -- a corrigendum takes its repeal date from
    the act it corrects (`parse.revision_repeal_date`), so repealing the act
    must restale the corrigendum too."""
    base = eu_structure.revision_base(basefile)
    return ([eurlex_notice(basefile)]
            + ([eurlex_notice(base)] if base else []))


def eurlex_content(basefile):
    """The content file parse reads (Formex/HTML/PDF), if any was obtained in a
    wanted language."""
    path, _lang, _route = parse.content_file(layout.eurlex_dir(basefile))
    return [path] if path else []


def eurlex_parse_run(basefile):
    art = parse.parse_dir(layout.eurlex_dir(basefile), basefile)
    if art is None:
        raise SkipDocument("%s: no swe/eng content" % basefile)
    write_artifact("eurlex", basefile, art)


# the per-consolidation recipe is parse_consolidation + to_artifact -- the
# parser's own code, nothing beyond EURLEX_CODE
EURLEX_VERSIONS_CODE = EURLEX_CODE


def eurlex_version_contents(basefile):
    """The content files of every downloaded consolidation of an act -- a new
    version arriving (or a better manifestation replacing one) restales both
    the parse (which presents the latest) and the versions stage."""
    inputs = []
    for _version, vdir in parse.version_dirs(layout.eurlex_dir(basefile)):
        path, _lang, _route = parse.content_file(vdir)
        if path:
            inputs.append(path)
    return inputs


def _eurlex_main_version(basefile):
    """The consolidation date `parse_dir` chose as the act's own current
    wording, if it has run yet -- read back off the already-built main
    artifact (`parse` always runs before `versions`), not re-derived by
    retrying parses here: that decision (newest-first, first to succeed) is
    already made and recorded in `art["consolidation"]["date"]`. None when
    the act has no archived consolidations, hasn't been parsed yet, or parse
    recorded it as a SkipDocument (an empty placeholder artifact -- ensure()
    writes one for any stage's SkipDocument, unconditionally; e.g. an
    UNCARRIED act, or one with no swe/eng content -- caught directly rather
    than by exception, since an empty file is not even valid JSON)."""
    art_path = layout.artifact("eurlex", basefile)
    if not compress.exists(art_path) or not compress.stat(art_path).st_size:
        return None
    consolidation = compress.read_json(art_path).get("consolidation")
    return consolidation["date"] if consolidation else None


def eurlex_version_list():
    """Every superseded consolidation across the whole corpus, as
    "<basefile>@<version>" dispatch keys -- the versions stage's real,
    parallelizable unit of work (`Stage.list_basefiles`), the eurlex
    counterpart of `sfs.source.sfs_version_list`: a heavily-amended act's
    history spreads across the whole pool instead of one worker parsing it
    alone. The one consolidation `parse_dir` already chose as the act's own
    current wording is excluded -- it is not a lydelse, it *is* the main
    artifact -- known without retrying any parse (`_eurlex_main_version`)."""
    keys = []
    for bf in download.list_basefiles(layout.EURLEX_DOWNLOADED):
        dirs = version_dirs(layout.eurlex_dir(bf))
        if not dirs:
            continue    # most acts (164,443 of 172,047 on dev, 2026-09-03):
                        # no history, so no main artifact to read either
        main = _eurlex_main_version(bf)
        keys.extend(fanout_key(bf, version)
                    for version, _vdir in dirs if version != main)
    return sorted(keys)


def eurlex_version_output(key):
    basefile, version = split_fanout_key(key)
    return layout.eurlex_version_artifact(basefile, version)


def eurlex_version_inputs(key):
    """Freshness inputs of one superseded consolidation: its own content
    file, plus the base act's own (its preamble is spliced into every
    version artifact) and the statute's patch."""
    basefile, version = split_fanout_key(key)
    vdir = dict(version_dirs(layout.eurlex_dir(basefile))).get(version)
    path, _lang, _route = content_file(vdir) if vdir else (None, None, None)
    return (([path] if path else []) + eurlex_content(basefile)
           + patch_input("eurlex", basefile))


def eurlex_version_run(key):
    """Parse one superseded consolidation into its version artifact -- the
    versions stage's per-item dispatch recipe (see `eurlex_version_list`).
    The two exception classes `parse_dir` itself tolerates, for the same
    reason (CELLAR's scanned-era consolidations: a TIFF-in-.xml packet, a
    corrigendum-only member, 13 of 172,043 measured), are the only ones
    caught here -- anything else is a real bug and must surface at the
    driver, not be buried as a skip (rule:no-catch-log-continue)."""
    basefile, version = split_fanout_key(key)
    doc_dir = layout.eurlex_dir(basefile)
    vdir = dict(version_dirs(doc_dir)).get(version)
    if vdir is None:
        raise SkipDocument("%s: consolidation %s no longer present"
                          % (basefile, version))
    preamble = base_preamble_for(doc_dir, basefile)
    try:
        cons = parse_consolidation(vdir, basefile, version, preamble=preamble)
    except (ValueError, etree.XMLSyntaxError) as exc:
        raise SkipDocument("%s: %s" % (type(exc).__name__, exc)) from exc
    if cons is None:
        raise SkipDocument("no Formex manifestation (pre-Formex "
                          "consolidations are PDF-only)")
    compress.write_json(layout.eurlex_version_artifact(basefile, version),
                        to_artifact(cons))


def eurlex_versions_rebuild_sidecars():
    """Assemble every act's versions-stage sidecar from the fan-out stage's
    own already-parsed artifacts, re-deriving directly only the small, rare
    set the driver's own SkipDocument handling cannot preserve an error
    message for (a benign skip -- a broken or non-Formex consolidation --
    still gets an empty placeholder artifact from `ensure`, same as any
    other stage, but not the original message; re-parsing it here to recover
    that is rare in practice, 13 of 172,043 measured corpus-wide). A version
    with no artifact at all failed in the fan-out (the driver holds the
    traceback) and is recorded as such, never re-parsed here. Runs once
    per source, after the fan-out's own per-version dispatch
    (`source.after["versions"]`), skipping any act untouched since its own
    sidecar was last built -- the eurlex counterpart of
    `sfs.source.sfs_versions_rebuild_sidecars`."""
    for basefile in download.list_basefiles(layout.EURLEX_DOWNLOADED):
        doc_dir = layout.eurlex_dir(basefile)
        downloads = version_dirs(doc_dir)
        sidecar_path = layout.eurlex_versions_sidecar(basefile)
        if compress.exists(sidecar_path) \
                and compress.stat(sidecar_path).st_mtime_ns >= compress.newest_mtime_ns(
                    # which wording is main is read off the main artifact, so
                    # a re-parse of the act can move it (an act with no
                    # history has nothing to move -- its sidecar is empty
                    # either way)
                    ([layout.artifact("eurlex", basefile)] if downloads else [])
                    + [p for p in (content_file(vdir)[0] for _v, vdir in downloads) if p]
                    + [layout.eurlex_version_artifact(basefile, v)
                       for v, _d in downloads]):
            continue    # nothing under this act changed since
        versions_out, skipped = [], []
        preamble = None
        main = _eurlex_main_version(basefile) if downloads else None
        for version, vdir in downloads:
            if version == main:
                continue
            art_path = layout.eurlex_version_artifact(basefile, version)
            if not compress.exists(art_path):
                # no artifact at all: the fan-out raised on this key (the
                # driver holds the traceback), or the key has not been
                # dispatched yet (a targeted run opened this act's gate).
                # Re-running the parse here, outside the per-document
                # isolation, would end the run -- record what is known
                skipped.append({"version": version, "error": "no version "
                                "artifact -- the versions stage has not "
                                "built this key"})
                continue
            if compress.stat(art_path).st_size:
                art = compress.read_json(art_path)
                versions_out.append({"version": version, "uri": art["uri"]})
                continue
            # an empty placeholder: a benign skip the driver could not keep
            # the message for -- re-derive it directly
            if preamble is None:
                preamble = base_preamble_for(doc_dir, basefile)
            try:
                cons = parse_consolidation(vdir, basefile, version,
                                           preamble=preamble)
            except (ValueError, etree.XMLSyntaxError) as exc:
                skipped.append({"version": version, "error": "%s: %s"
                                % (type(exc).__name__, exc)})
                continue
            if cons is None:
                skipped.append({"version": version, "error": "no Formex "
                                "manifestation (pre-Formex consolidations "
                                "are PDF-only)"})
                continue
            art = to_artifact(cons)
            compress.write_json(layout.eurlex_version_artifact(basefile, version),
                                art)
            versions_out.append({"version": version, "uri": art["uri"]})
        versions_out.sort(key=lambda e: e["version"])
        skipped.sort(key=lambda e: e["version"])
        util.write_json_atomic(sidecar_path,
                              {"versions": versions_out, "skipped": skipped})


def eurlex_download_run(basefile):
    """Fetch one CELEX (tree notice + best content per language) from CELLAR.
    Discovery of new CELEX is eurlex_harvest (bare `lagen eurlex download`)."""
    stored = download.download_document(
        protocol.session(download), layout.EURLEX_DOWNLOADED, basefile,
        download.LANGUAGES, POLITENESS, full=protocol.RUN.force)
    if not stored:
        print("%s: no manifestation in %s" % (
            basefile, "/".join(download.LANGUAGES)), flush=True)


def eurlex_harvest(scopes):
    """Bulk discovery sweep via the CELLAR SPARQL endpoint. `scopes` narrows it
    to the named sectors (treaties/acts/caselaw); empty = all. Incremental by
    default (watermark-bounded, skips CELEX already on disk); --force re-fetches
    everything. --since/--lang/--source tune discovery (see RunOptions)."""
    if protocol.RUN.dry_run:
        print("eurlex download: would download %s into %s"
              % (", ".join(scopes) or "treaties/acts/caselaw",
                 layout.EURLEX_DOWNLOADED))
        return
    languages = tuple(protocol.RUN.lang.split(",")) if protocol.RUN.lang else download.LANGUAGES
    totals = {}
    for sector in (scopes or list(download.SECTORS)):
        seen, stored, skipped = download.sync(
            layout.EURLEX_DOWNLOADED, sector, full=protocol.RUN.force, since=protocol.RUN.since,
            languages=languages, source=protocol.RUN.source)
        print("eurlex %s: %d seen, %d stored, %d skipped"
              % (sector, seen, stored, skipped))
        totals[sector] = (seen, stored)     # `skipped` is seen-minus-stored
    return sum_scope_totals(totals)


def eurlex_unpack(args):
    """`lagen eurlex unpack-bulk <dir-or-zip>` -- import a CELLAR bulk
    legislation dump into the per-CELEX layout, so `parse` then treats the works
    exactly like downloaded documents (no network)."""
    if len(args) != 1:
        sys.exit("usage: lagen eurlex unpack-bulk <bulk-dir-or-zip>")
    if protocol.RUN.dry_run:
        print("eurlex unpack-bulk: would import %s into %s"
              % (args[0], layout.EURLEX_DOWNLOADED))
        return
    bulk.unpack_bulk(args[0], layout.EURLEX_DOWNLOADED)


def eurlex_prune(args=()):
    """`lagen eurlex prune-empty` -- remove harvest dirs left as a bare notice.ttl
    with no Swedish/English document (a pre-accession act never translated). The
    harvest tree is rebuildable, so this only drops dead weight the parser skips."""
    n = download.prune_empty(layout.EURLEX_DOWNLOADED, remove=not protocol.RUN.dry_run)
    print("eurlex prune-empty: %s %d notice-only dir(s) in %s"
          % ("would remove" if protocol.RUN.dry_run else "removed", n,
             layout.EURLEX_DOWNLOADED))


def eurlex_refresh_metadata(args):
    """`lagen eurlex refresh-metadata [<CELEX> ...] [--limit N]` -- re-read the
    CELLAR metadata of downloaded documents and rewrite their notice.ttl. No
    content is refetched.

    This is the *backstop*, not the mechanism. `download` already re-reads the
    acts each newly harvested act says it repeals (`refresh_repeal_targets`),
    which covers 64% of the repeals CELLAR records. The rest end by their own
    terms, named by no incoming act, and only a re-read finds them.

    With no CELEX named it walks every downloaded document whose notice does not
    already record a repeal -- a repeal never lifts, so the audit shrinks each
    time it runs. `--all` widens that to the whole corpus, repealed acts
    included: the audit's skip is right for repeals but wrong for a *new*
    metadata field, and the amends/implements relations measure 56 needs are
    one (a repealed act amends things too). Re-run `parse` and `relate`
    afterwards: the metadata reaches the catalog through the artifact, the way
    every other extracted fact does."""
    celexes = (download.list_basefiles(layout.EURLEX_DOWNLOADED)
               if protocol.RUN.every else list(args) or None)
    if protocol.RUN.dry_run:
        print("eurlex refresh-metadata: would re-read CELLAR metadata for %s "
              "document(s) in %s"
              % (len(celexes) if celexes else "all not-yet-repealed",
                 layout.EURLEX_DOWNLOADED))
        return
    reporter = util.Reporter()
    # the work-list is known before the first query, so the counter can show a
    # real total rather than pacing itself against its own progress
    work = download.refresh_metadata(
        protocol.session(download), layout.EURLEX_DOWNLOADED, celexes, limit=protocol.RUN.limit)
    total = next(work)
    written = repealed = undated = unanswered = 0
    for seen, (_celex, expired, in_force, rewritten) in enumerate(work, 1):
        if not rewritten:
            unanswered += 1     # CELLAR bound no row: the notice is left alone
        else:
            written += 1
            if expired:
                repealed += 1
            elif in_force in cellar.OUT_OF_FORCE:
                undated += 1    # out of force, no end date: it stays listed
        # `actual` paces the ETA, so it counts the notices rewritten -- the work
        # this loop does. Pacing on `repealed` would time the run against an
        # event most documents do not produce (145 of 600 measured, and the
        # audit's premise is that most are not repealed)
        reporter.update(seen, total, scope="eurlex", actual=written,
                        repealed=repealed, undated=undated,
                        unanswered=unanswered)
    reporter.done()
    print("eurlex refresh-metadata: %d notice(s) rewritten, %d repealed with a "
          "date, %d out of force with no end date (these stay listed), "
          "%d unanswered by CELLAR (left untouched)"
          % (written, repealed, undated, unanswered))


def eurlex_ai_annotate(basefiles):
    """`lagen eurlex ai-annotate <CELEX> ...` -- author the editorial `.ann` layer
    (thematic recital groups + article<->recital links) for the named sector-3
    acts by calling the LLM endpoint. Deliberately one-shot per id: the LLM is
    never called from parse/relate/generate, only from this explicit action."""
    if not basefiles:
        sys.exit("usage: lagen eurlex ai-annotate <CELEX> [<CELEX> ...]")
    with aireport.Report("eurlex", "ai-annotate", len(basefiles)) as report:
        for celex in basefiles:
            llm.start_record()   # one provenance window per layer (lib.annstore stamps meta.run)
            out = annstore.path("eurlex", celex)
            if protocol.RUN.dry_run:
                report.plan(celex, "annotate -> %s" % out)
                continue
            if report.verified(celex, out):
                continue
            report.item(celex)
            report.wrote(celex, annotate.annotate(celex, force=protocol.RUN.force))
    return report


def eurlex_backfill(args):
    """`lagen eurlex backfill [<sector-digit>] [--limit N]` -- download the acts
    the corpus already cites but does not hold, most-cited first.

    The sector-3 stock was imported from a CELLAR bulk dump, and a bulk dump
    ships only the acts *in force*. Every act repealed since is therefore
    missing while being cited from everywhere: 2004/18 alone is referenced 6 979
    times by 790 documents we do hold. Re-running discovery over all of sector 3
    would fetch a hundred thousand acts to reach them; the citation graph names
    exactly the ones that matter, and ranks them
    (`catalog.dangling_targets`) -- the top 500 carry 76% of all dangling
    references. `--limit` bounds a run; re-run to go deeper.

    A CELEX with no Swedish/English manifestation stores nothing (a
    pre-accession act never translated), which is reported, not retried."""
    sector = args[0] if args else "3"
    if not (len(sector) == 1 and sector.isdigit()):
        sys.exit("usage: lagen eurlex backfill [<sector-digit>] [--limit N]  "
                 "(default sector 3, the legislation sector)")
    con = catalog.connect_ro(layout.CATALOG)
    wanted = catalog.dangling_targets(
        con, "%scelex/%s" % (layout.BASE, sector))
    con.close()
    if protocol.RUN.limit:
        wanted = wanted[:protocol.RUN.limit]
    if not wanted:
        print("eurlex backfill: sector %s is complete -- every cited act is in "
              "the corpus" % sector)
        return
    total = sum(n for _uri, n, _docs in wanted)
    print("eurlex backfill: %d cited-but-absent act(s) in sector %s, %d "
          "inbound reference(s)" % (len(wanted), sector, total))
    if protocol.RUN.dry_run:
        for uri, links, docs in wanted[:40]:
            print("  %-18s %7d links from %5d documents"
                  % (uri.rsplit("/", 1)[-1], links, docs))
        return
    session = protocol.session(download)
    stored = empty = 0
    reporter = util.Reporter()
    for seen, (uri, _links, _docs) in enumerate(wanted, 1):
        # the basefile is the bare CELEX; `catalog.local` would keep the whole
        # "celex/…" local path the uri grammar puts in front of it
        if download.download_document(
                session, layout.EURLEX_DOWNLOADED, uri.rsplit("/", 1)[-1],
                download.LANGUAGES, POLITENESS):
            stored += 1
        else:
            empty += 1
        reporter.update(seen, len(wanted), scope="eurlex backfill",
                        stored=stored, no_manifestation=empty)
    reporter.done()
    print("eurlex backfill: %d act(s) downloaded, %d with no swe/eng "
          "manifestation; run `lagen eurlex parse` next" % (stored, empty))


def eurlex_casenames(args=()):
    """Refresh the named-EU-cases snapshot (`lagen eurlex casenames`): query
    Wikidata for EU cases carrying a CELEX number and rewrite
    eurlex/data/casenames.json, which lib.eucasenaming reads to label a case by
    its usual name ("Schrems II"). Independent of the per-document download/parse
    chain -- one small curated dataset, not corpus artifacts. A parse restamps
    the name onto the artifact, so re-parse the caselaw sector after a refresh."""
    if protocol.RUN.dry_run:
        print("eurlex casenames: would query %s -> %s"
              % (casenames.WDQS, NAMEDEUCASES))
        return
    cases = casenames.harvest()
    print("eurlex casenames: %d named EU cases -> %s"
          % (len(cases), NAMEDEUCASES))


def eurlex_history_as_git(args):
    """`lagen eurlex history-as-git <repodir> [<CELEX> ...]` -- build or
    update a git repository holding every sector-3 R/L act's
    consolidated-version history as plaintext, one file per act, one commit
    per wording CELLAR has published (see eurlex.asgit). A re-run appends
    only the new tail entries of an unchanged corpus; corrected renderings,
    changed artifacts and a repo predating the ledger require
    --rebuild-history."""
    if not args:
        sys.exit("usage: lagen eurlex history-as-git <repodir> [<CELEX> ...]")
    repodir, requested = args[0], args[1:]
    targets = list(requested) or [
        bf for bf in download.list_basefiles(layout.EURLEX_DOWNLOADED)
        if download.RE_PLAIN_ACT.match(bf)]
    if protocol.RUN.dry_run:
        print("eurlex history-as-git: would consider %d act(s) for %s"
              % (len(targets), repodir))
        return
    commits = asgit.export(repodir, targets, rebuild=protocol.RUN.rebuild_history)
    print("eurlex history-as-git: %d commit(s) into %s" % (commits, repodir))


def eurlex_version_pages(sidecars):
    """The EU-act lydelse pages to render, one (uri, source, path, title) row
    per parsed consolidated version -- the eurlex counterpart of
    `sfs_version_pages`, appended to the generate plan the same way."""
    rows = []
    for sc in sidecars:
        if not sc.exists():
            continue
        basefile = layout.eurlex_sidecar_basefile(sc)
        for entry in json.loads(sc.read_text())["versions"]:
            version = entry["version"]
            rows.append((entry["uri"], "eurlex",
                         str(layout.eurlex_version_artifact(basefile, version)),
                         "%s i lydelse per %s" % (basefile, version)))
    return rows


def eurlex_extra_pages(only):
    """The EU-act lydelse rows generate adds to its plan -- the eurlex
    counterpart of `sfs_extra_pages`."""
    if only is not None:
        return eurlex_version_pages(
            [Path(p).with_suffix(".versions.json") for p in only
             if Path(p).is_relative_to(layout.artifact_dir("eurlex"))])
    return eurlex_version_pages(
        sorted(layout.artifact_dir("eurlex").glob("*/*.versions.json")))


def eurlex_relate_cross(con):
    """eurlex's part of relate's cross-document block: load the hand-authored
    `.corr` layers (a recast with no jämförelsetabell of its own, e.g. GDPR
    against 95/46/EC) into `directive_correspondence`, beside the rows
    `correspondence` extracts mechanically at parse time."""
    rows = correspond.hand_rows()
    catalog.add_directive_correspondence(con, rows)
    return ({"hand-authored directive lineage edges loaded from .corr layers":
             len(rows)}, [])


def eurlex_intermediate(basefile):
    """eurlex's intermediate is the main act's Formex XML (or the OJ HTML for the
    older acts that have no Formex manifestation), normalised to one element per
    line -- both manifestations ship as a single line, and `eurlex.parse`
    normalises identically before applying the patch."""
    path, _lang, route = content_file(layout.eurlex_dir(basefile))
    if path is None:
        raise SkipDocument("%s: no content file to patch" % basefile)
    if route == "fmx4":
        return formex_intermediate(formex_members(path)[0][1])
    if route == "html":
        return markup.block_lines(compress.read_bytes(path).decode("utf-8", "replace"))
    raise ValueError("%s: the %s manifestation is not text-patchable "
                     "(PDF-only act)" % (basefile, route))


SOURCES: tuple[Source, ...] = (Source("eurlex", lambda: download.list_basefiles(
    layout.EURLEX_DOWNLOADED), {
    "download": Stage("download", eurlex_download_run, eurlex_notice),
    # the notice is a parse *input*, not just the download stage's output marker:
    # the act's repeal date lives there and nowhere else, and a metadata refresh
    # (`refresh-metadata`, or a repeal a newly downloaded act announces) rewrites
    # the notice while leaving the content file untouched. Without it in the
    # hash, `parse` reads such a document as fresh and the repeal never reaches
    # the artifact -- verified: a notice rewritten to a different end-of-validity
    # left the artifact's `expired` at the old date, with `parse` reporting
    # "skipped (fresh) 1". `depends` alone does not cover this; it recurses into
    # the download stage but does not hash its output.
    "parse": Stage("parse", eurlex_parse_run,
                   functools.partial(layout.artifact, "eurlex"),
                   inputs=lambda bf: eurlex_content(bf)
                   + eurlex_version_contents(bf)
                   + eurlex_parse_notices(bf) + patch_input("eurlex", bf),
                   depends="download", code=EURLEX_CODE),
    # superseded consolidated wordings (CONSLEG) -> per-version artifacts + a
    # sidecar index, feeding the lydelse pages, the compare panel and the diff
    # view -- the eurlex counterpart of the sfs versions stage. Dispatched per
    # consolidation, not per act (list_basefiles), so a heavily-amended act's
    # history spreads across the whole pool instead of one worker parsing it
    # alone; eurlex_versions_rebuild_sidecars (after) assembles each act's
    # sidecar once its own versions are all built.
    "versions": Stage("versions", eurlex_version_run, eurlex_version_output,
                      inputs=eurlex_version_inputs, code=EURLEX_VERSIONS_CODE,
                      list_basefiles=eurlex_version_list),
}, harvest=eurlex_harvest, origin=origin(download.SOAP_ENDPOINT),
   after={"versions": (eurlex_versions_rebuild_sidecars,)},
   render=render.render,
   intermediate=(eurlex_intermediate,
                 "Formex XML (pre-Formex acts: the OJ HTML)"),
   artifacts=functools.partial(layout.artifacts, "eurlex"),
   extra_pages=eurlex_extra_pages, relate_cross=eurlex_relate_cross,
   cross_code=(HERE / "correspond.py",),
   # the ai-annotate .ann layers, the hand-authored .corr lineage layers and
   # the versions-stage sidecars (lydelser)
   layers=lambda: (sorted(annstore.tree("eurlex").rglob("*.ann"))
                   + sorted(annstore.tree("eurlex").glob("*/*.corr"))
                   + sorted(layout.artifact_dir("eurlex").glob("*/*.versions.json"))),
   scopes=frozenset(download.SECTORS),
   actions={"unpack-bulk": eurlex_unpack, "ai-annotate": eurlex_ai_annotate,
            "prune-empty": eurlex_prune, "casenames": eurlex_casenames,
            "backfill": eurlex_backfill,
            "refresh-metadata": eurlex_refresh_metadata,
            "history-as-git": eurlex_history_as_git},
   notes="download flags: --since YYYY-MM-DD, --lang swe,eng, --source sparql|soap\n"
         "unpack-bulk <dir|zip>: import a CELLAR bulk legislation dump\n"
         "prune-empty: remove download dirs with only a notice.ttl (no swe/eng doc)\n"
         "ai-annotate <CELEX>: LLM-author the editorial .ann layer (sector-3 acts)\n"
         "refresh-metadata [<CELEX> ...] [--limit N]: re-read CELLAR metadata into\n"
         "  notice.ttl (validity, work date, eurovoc) without refetching content\n"
         "backfill [<sector>] [--limit N]: download the acts the corpus cites but\n"
         "  does not hold, most-cited first (bulk dumps ship only acts in force)\n"
         "download acts also sweeps every held R/L act's consolidated versions\n"
         "  (CONSLEG) into its .versions/ tree; a per-CELEX download does the same\n"
         "casenames: refresh the named-EU-cases snapshot from Wikidata\n"
         "history-as-git <repodir> [<CELEX> ...]: build/update a git repo of\n"
         "  sector-3 R/L acts' consolidated-version history (append by default,\n"
         "  --rebuild-history to force a full rebuild)"),)
