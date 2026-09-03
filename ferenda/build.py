"""The `lagen` CLI and the registry every source declares itself into.

    lagen <source> <action> [basefile...] [flags]

A *source* (sfs, dv, …) is a small program that registers a few *stages*
(download → parse → relate → generate). Each source's registration is its own
`ferenda/<package>/source.py`, exposing one `SOURCES` tuple; this file imports
them, fills the registry in `lib/stage.py`, and holds the CLI that drives it.
It knows nothing source-specific beyond the handful of *cross-source* actions
below -- the ones that read two sources at once, which no single source may
import (rule:lib-never-imports-vertical).

The engine that decides what to run is `lib/freshness.py`, and the corpus-wide
verbs (relate/index/dump/generate and the composites) are `lib/corpus.py`.

Each stage is a pure function `inputs(basefile) → output(basefile)` with a
recipe; the driver runs a recipe only when its output is stale. Freshness is
content-based, not mtime-based: a stage is fresh when its output exists and
the manifest records the same input hash *and* the same recipe version (a
hash of the stage's own implementation files, so editing the parser re-stales
every doc without a blanket --force). Asking for a downstream action brings
stale upstream stages up to date first (make semantics); `--no-deps` scopes
to just the named stage.

    lagen sfs parse 2018:585        # parse one statute (download must exist)
    lagen sfs parse                 # every stale SFS basefile
    lagen dv parse -j8              # all court decisions, 8 workers
    lagen sfs status                # per-stage fresh/stale/missing counts
    lagen all parse -n              # dry-run: print the plan, do nothing
    lagen all rebuild               # parse→relate→index→dump→generate (offline)
    lagen all all                   # download too, then rebuild — full sync

The parallelisable steps (parse, index) default to all CPU cores; `-j1`
serialises. relate is single-writer (SQLite) and always serial.

(Also runnable as `python -m ferenda.build …`.)
"""

import argparse
import os
import sys
import time
from datetime import date
from pathlib import Path

from . import patchsource
from .api import app as api_app
from .api import edit as api_edit
from .api import errors as api_errors
from .api import graphicsedit as api_graphicsedit
from .api import patch as api_patch
from .avg import source as avg_source
from .coe import source as coe_source
from .dv import source as dv_source
from .eurlex import source as eurlex_source
from .forarbete import jamforelse as fa_jamforelse
from .forarbete import kommentar as fa_kommentar
from .forarbete import source as forarbete_source
from .forarbete import structure as fa_structure
from .foreskrift import source as foreskrift_source
from .guidance import source as guidance_source
from .hudoc import source as hudoc_source
from .icc import source as icc_source
from .icj import source as icj_source
from .icrc import source as icrc_source
from .lawreview import source as lawreview_source
from .lib import (
    aireport,
    annstore,
    catalog,
    compress,
    corpus,
    errorlog,
    freshness,
    layout,
    llm,
    markdown,
    patch,
    runlog,
    util,
)
from .lib import stage as protocol
from .lib.stage import SOURCES
from .remisser import source as remisser_source
from .rs import source as rs_source
from .sfs import asgit as sfs_asgit
from .sfs import correspond as sfs_correspond
from .sfs import render as sfs_render
from .sfs import source as sfs_source
from .site import browse, subdomains
from .site import source as site_source
from .stats import source as stats_source
from .untc import source as untc_source
from .wiki import parse as wiki_parse
from .wiki import source as wiki_source

# Registration: every source declares itself in its own package, and this is
# where the registry is filled. The order is the order `lagen all <verb>` walks
# the corpus in, so it is data, not alphabetical tidiness.
for _module in (sfs_source, dv_source, forarbete_source, eurlex_source,
                hudoc_source, coe_source, icrc_source, untc_source, icc_source,
                icj_source, foreskrift_source, avg_source, guidance_source,
                lawreview_source, rs_source, remisser_source, wiki_source,
                site_source, stats_source):
    for _source in _module.SOURCES:
        # the index step of an unsearched source fingerprints the module that
        # declared `searchable`, its own source.py (lib/corpus.cmd_index)
        _source.registration = (Path(_module.__file__),)
        SOURCES[_source.name] = _source

# every catalogued source registers an `artifacts` lister, and only those do:
# the membership is `layout.CATALOGUED_SOURCES`, because the ops dashboard asks
# the same question and must not import build (build imports the API app, so the
# dependency points the other way -- see lib/runlog.py's docstring). Absent by
# design: remisser/site/stats publish no catalogued documents. Always through
# `layout.artifacts`, the single home that knows what in an artifact dir is a
# document and what is a sidecar (the identity/guidance indexes, sfs's
# `.versions.json` layers and `archive/` consolidations, föreskrift's
# `.grund.json` as-enacted pages) -- hand-globbing is what let the exclusions
# drift: a föreskrift `*/*.json` handed relate 1,650 .grund sidecars as
# documents.
assert {n for n, s in SOURCES.items() if s.artifacts} == set(layout.CATALOGUED_SOURCES), \
    ("the sources registering an artifacts lister must be exactly "
     "layout.CATALOGUED_SOURCES; got %s"
     % sorted({n for n, s in SOURCES.items() if s.artifacts}
              ^ set(layout.CATALOGUED_SOURCES)))


def generate_aggregates(con, *, full):
    """The corpus-wide pages a full generate writes through the REST API in
    process: the faceted browse tree, then the two subdomain projections. They
    are the one part of `generate` that `lib/corpus` cannot write itself --
    `browse` and `subdomains` both import `api`, which lib may not -- so it takes
    them as a callable and this is what it gets. `subdomains` also needs sfs's
    chapter renderer, which it may not import itself (a vertical may not import
    a sibling), so this composes it in.

    `full=False` is `--aggregates-only`, which refreshes just the browse tree.

    The chapter pages (PRD-subdomains.md section 6: hyres.lagen.nu,
    samtyckes.lagen.nu) are the one subdomain kind `write_sub_tree` cannot just
    symlink, since the target is part of a document rather than a document of its
    own; they need the live connection for the rail context (kommentar,
    citations) a chapter page shows. The definite-form subdomain map is generated
    fresh from namedlaws.json/namedacts.json every full-corpus run rather than
    gated by the manifest -- cheap (a few hundred rows, no catalog read), and
    generate's coarse gate fingerprints GENERATE_CODE + the catalog, not those
    two data files, so a same-day edit to either would otherwise go unnoticed
    until something else also changed."""
    browse.generate_all(layout.CATALOG, layout.GENERATED, con)
    if not full:
        return
    subdomains.write_chapter_pages(layout.GENERATED, con,
                                   sfs_render.render_chapter)
    subdomains.write_sub_tree(layout.GENERATED)


# --------------------------------------------------------------------------
# Cross-source composition. Three sfs actions and one förarbete reader that
# each need *two* sources at once: deriving an old->new paragraf map or a
# commit's authorship means reading a proposition, which is förarbete's job,
# and `lib/` may not import a source any more than sfs may import förarbete
# (rule:lib-never-imports-vertical). So they live here, in the one file that
# composes across sources, and are hung on sfs's registration as data.
# --------------------------------------------------------------------------


def _forarbete_meta(identifier):
    """A "Prop. 2020/21:194" / "Rskr. 2020/21:387" identifier (the form the
    SFS register cites) -> {title, signers, ingress} off the parsed förarbete
    artifact, or None when that document is not in the corpus. Reading a
    proposition/riksdagsskrivelse is förarbete's job; build composes the two
    verticals, exactly like ai-correspond."""
    typ, _, ident = identifier.partition(" ")
    path = layout.artifact("forarbete",
                           "%s/%s" % (typ.rstrip(".").lower(),
                                      util.basefile_slug(ident)))
    # a placeholder artifact (the budget propositions, prop 1 and prop 100) is
    # no readable document, which is exactly what "not in the corpus" means
    art = compress.read_json(path, default=None, empty=None)
    if art is None:
        return None
    return {"title": art.get("title") or "",
            "signers": fa_structure.signers(art["structure"]),
            "ingress": fa_structure.ingress(art["structure"])}


def sfs_ai_correspond(basefiles):
    """`lagen sfs ai-correspond <new-sfs> <prop-basefile> [<old-sfs>]` -- LLM-derive
    the old->new paragraf correspondence map for a restructured statute from the
    proposition's författningskommentar, validate every edge against both laws'
    paragrafs, and write it as a `.corr` layer in the curated store (lib.annstore).
    The old law is read from the new law's repeal clause unless given. One-shot
    per id, like eurlex ai-annotate; the LLM is never called from
    parse/relate/generate, and a verified layer refuses regeneration sans --force."""
    if not 2 <= len(basefiles) <= 3:
        sys.exit("usage: lagen sfs ai-correspond <new-sfs> <prop-basefile> "
                 "[<old-sfs>]  (e.g. 2018:585 prop/2017-18-89)")
    new_sfs, prop = basefiles[0], basefiles[1]
    llm.start_record()   # one provenance window per layer (lib.annstore stamps meta.run)
    new_art = compress.read_json(layout.artifact("sfs", new_sfs))
    prop_art = compress.read_json(layout.artifact("forarbete", prop))
    old_uri = ("https://lagen.nu/" + basefiles[2] if len(basefiles) == 3
               else sfs_correspond.detect_old_law(new_art))
    if not old_uri:
        # bad input data, not a programming bug (rule:errors-drive-retry-use-raise)
        raise ValueError("%s: could not detect the repealed law from its "
                         "transition clause; pass it as the third argument"
                         % new_sfs)
    old_sfs = old_uri.rsplit("/", 1)[-1]
    old_art = compress.read_json(layout.artifact("sfs", old_sfs))
    out = annstore.path("sfs", new_sfs, ".corr")
    with aireport.Report("sfs", "ai-correspond", 1) as report:
        if protocol.RUN.dry_run:
            report.plan(new_sfs, "map <- %s via %s -> %s" % (old_sfs, prop, out))
            return report
        if report.verified(new_sfs, out):     # pre-LLM-spend; write guards again
            return report
        report.item(new_sfs)
        # reading the proposition's författningskommentar is förarbete's job; build
        # composes the two verticals (sfs.correspond no longer imports forarbete)
        fk = fa_kommentar.fk_section(
            prop_art, new_art["metadata"]["properties"]["dcterms:title"])
        sidecar, stats = sfs_correspond.correspond(new_art, prop_art, old_art, fk)
        annstore.write(out, sidecar,
                       {**annstore.artifact_input("sfs", new_sfs),
                        **annstore.artifact_input("sfs", old_sfs),
                        **annstore.artifact_input("forarbete", prop)}, protocol.RUN.force)
        report.wrote(new_sfs, out, note="%d edges from %d, %d rejected, old law %s"
                     % (stats["emitted"], stats["raw"], stats["rejected"], old_sfs))
    return report



def sfs_table_correspond(basefiles):
    """`lagen sfs table-correspond <new-sfs> <prop-basefile> [<old-sfs>[=TAG]
    ...]` -- derive the old->new paragraf correspondence map mechanically from
    the proposition's own jämförelsetabell bilagor (the two-column tables a
    re-enacting prop appends, extracted from the downloaded PDFs -- they often
    sit in a bilaga volume the artifact parse never reads). Same `.corr` layer,
    same payload and store semantics as ai-correspond, no LLM: when a prop
    ships the tables, this route is authoritative and free.

    A prop that replaces *several* laws (SFB prop 2008/09:200, SFL prop
    2010/11:165) takes them all in one run -- the layer is keyed by the new
    law, so the pairs must merge into one sidecar. Such registers tag each
    cell reference with a prop-local shorthand; `=TAG` names an old law's
    ("1990:324=TL") so only its references are read -- explicit, because
    prop-local shorthands don't reliably resolve from any global dataset."""
    if len(basefiles) < 2:
        sys.exit("usage: lagen sfs table-correspond <new-sfs> <prop-basefile> "
                 "[<old-sfs>[=TAG] ...]  (e.g. 2009:400 prop/2008-09-150, or "
                 "2011:1244 prop/2010-11-165 1990:324=TL 1997:483=SBL)")
    new_sfs, prop = basefiles[0], basefiles[1]
    new_art = compress.read_json(layout.artifact("sfs", new_sfs))
    prop_art = compress.read_json(layout.artifact("forarbete", prop))
    olds = []                   # [(old_sfs, tag or None)]
    for arg in basefiles[2:]:
        old_arg, _, tag = arg.partition("=")
        olds.append((old_arg, tag or None))
    if not olds:
        old_uri = sfs_correspond.detect_old_law(new_art)
        if not old_uri:
            # bad input data, not a bug (rule:errors-drive-retry-use-raise)
            raise ValueError("%s: could not detect the repealed law from its "
                             "transition clause; pass it as the third argument"
                             % new_sfs)
        olds = [(old_uri.rsplit("/", 1)[-1], None)]
    out = annstore.path("sfs", new_sfs, ".corr")
    typ, slug = prop.split("/", 1)
    record = compress.read_json(layout.fa_record(prop))
    pdfs = [layout.fa_dir(layout.FA_DOWNLOADED, typ, slug) / f
            for f in record["files"] if f.lower().endswith(".pdf")]
    if protocol.RUN.dry_run:
        print("sfs table-correspond: would map %s <- %s from %d pdf(s) of %s "
              "-> %s" % (new_sfs, "+".join(o for o, _t in olds), len(pdfs),
                         prop, out))
        return
    annstore.guard(out, protocol.RUN.force)
    # reading the proposition's pages is förarbete's job; build composes the
    # two verticals, exactly like ai-correspond
    all_tabs = [t for pdf in pdfs for t in fa_jamforelse.tables(pdf)]
    if not all_tabs:
        # a prop without tables is bad input, not a bug -- and the empty-tabs
        # state must not fall through to an empty layer
        # (rule:errors-drive-retry-use-raise)
        raise ValueError("%s: no jämförelsetabell found in %s" % (new_sfs, prop))
    # the PDFs are the derivation's real input (the tables often sit in a
    # bilaga volume the artifact parse never reads), so their hashes must
    # enter the envelope or `ann status` reports the layer fresh across a
    # re-downloaded bilaga
    inputs = {}
    for pdf in pdfs:
        inputs |= annstore.download_input(pdf.relative_to(layout.DOWNLOADED))
    edges, old_uris = [], []
    for old_sfs, tag in olds:
        old_art = compress.read_json(layout.artifact("sfs", old_sfs))
        tabs = sfs_correspond.relevant_tables(all_tabs, old_sfs, tag=tag)
        try:
            sidecar, stats = sfs_correspond.table_correspond(
                new_art, prop_art, old_art, tabs, tag=tag)
        except ValueError as e:
            # an old law whose section maps nothing §-level (every LBF
            # provision "utgår" in the SFB register) orients no table; in a
            # multi-law run that is one empty pair, not a broken run
            if len(olds) == 1:
                raise
            print("sfs table-correspond %s <- %s: skipped (%s)"
                  % (new_sfs, old_sfs, e))
            continue
        edges += sidecar["correspondence"]["edges"]
        old_uris.append(old_art["uri"])
        inputs |= annstore.artifact_input("sfs", old_sfs)
        print("sfs table-correspond %s <- %s: %d edges from %d rows in %d "
              "table(s) (%d without counterpart, %d rejected, %d table(s) "
              "skipped)" % (new_sfs, old_sfs, stats["emitted"], stats["rows"],
                            len(tabs), stats["none"], stats["rejected"],
                            stats["skipped"]))
    if not edges:
        # a kapitel-level table (vapenlag prop 2025/26:141) yields no
        # paragraf edges -- an empty layer would only mask that
        sys.exit("sfs table-correspond %s: no paragraf edges extractable; "
                 "not writing %s" % (new_sfs, out))
    sidecar = {"correspondence": {
        "newLaw": new_art["uri"],
        "oldLaw": old_uris[0] if len(old_uris) == 1 else old_uris,
        "proposition": prop_art["uri"], "edges": edges}}
    annstore.write(out, sidecar,
                   {**annstore.artifact_input("sfs", new_sfs), **inputs,
                    **annstore.artifact_input("forarbete", prop)}, protocol.RUN.force,
                   model="jamforelsetabell")
    print("sfs table-correspond %s: wrote %d edges (%d old law(s)) to %s"
          % (new_sfs, len(edges), len(old_uris), out))


def sfs_history_as_git(basefiles):
    """`lagen sfs history-as-git <repodir> [basefile...]` -- build or update a
    git repository holding the whole SFS collection as plaintext, one file per
    statute, one commit per amendment event (grouped by proposition), authored
    by the proposition's signers and committed by the riksdagsskrivelse's (see
    sfs.asgit). A re-run appends only a strict extension of the per-transition
    ledger; corrections, backfills and changed attribution require
    --rebuild-history."""
    if not basefiles:
        sys.exit("usage: lagen sfs history-as-git <repodir> [basefile ...]")
    repodir, requested = Path(basefiles[0]), basefiles[1:]
    targets = requested or sfs_source.sfs_list()
    if protocol.RUN.dry_run:
        print("sfs history-as-git: would export %d statute(s) into %s"
              % (len(targets), repodir))
        return
    commits = sfs_asgit.export(
        targets, repodir, forarbete_meta=_forarbete_meta,
        scope=sfs_asgit.scope_id(targets, full=not requested),
        rebuild=protocol.RUN.rebuild_history)
    print("sfs history-as-git: %d commit(s) into %s" % (commits, repodir))


SOURCES["sfs"].actions.update({"ai-correspond": sfs_ai_correspond,
                               "table-correspond": sfs_table_correspond,
                               "history-as-git": sfs_history_as_git})


def rebuild_after_commit(changes):
    """Regenerate the static pages an inline-editor commit touched, in dependency
    order: re-parse the changed markdown -> relate the affected wiki source(s) so
    the catalog picks up new/edited commentary edges -> regenerate just the
    touched host/concept pages (and, for editorial edits, the site pages). Reuses
    the exact stage functions the `lagen` CLI runs; a web request mints no run id,
    so the ledger emissions inside them no-op (see `freshness.RUN_ID`). `changes` is the
    list `editcart.commit` returns -- `{"kind": kommentar|begrepp|site|graphics,
    "basefile": …}`; a `graphics` entry is a reviewed `.graphics` crop and needs
    neither, only its host statute's page regenerated. Returns the public URLs of the rebuilt pages.

    Called from `api/edit.py` (the write side of the service). build already
    imports `api.app`; this is the one call back the other way, used only at
    request time, never at import time -- so the mutual reference stays sound."""
    kommentar = [c["basefile"] for c in changes if c["kind"] == "kommentar"]
    begrepp = [c["basefile"] for c in changes if c["kind"] == "begrepp"]
    site = [c["basefile"] for c in changes if c["kind"] == "site"]
    # a reviewed .graphics entry needs no parse and no relate: the layer is read
    # at generate time (`page._graphics_index`), not folded into the artifact,
    # and a crop mints no links. Regenerating the host statute's page is the
    # whole of it.
    graphics = [c["basefile"] for c in changes if c["kind"] == "graphics"]
    for bf in kommentar:
        wiki_source.kommentar_parse_run(bf)
    for bf in begrepp:
        wiki_source.begrepp_parse_run(bf)
    for bf in site:
        site_source.site_parse_run(bf)
    relate = [n for n, present in (("kommentar", kommentar), ("begrepp", begrepp))
              if present]
    if relate:
        corpus.cmd_relate(SOURCES, relate)
    urls = []
    # force=True: these pages are dirty by construction (the request just
    # committed an edit that renders onto them), so the freshness signature --
    # which does not see a kommentar edit to a *host* page -- must not be
    # consulted; the committed edit has to be live when the response returns.
    for bf in kommentar:                 # a commentary rides its host act's page
        host = layout.kommentar_host(bf)
        corpus.cmd_generate(SOURCES, only={str(layout.artifact(host, bf))},
                            source=host, force=True,
                            aggregates=generate_aggregates)
        urls.append(layout.page_url(wiki_parse.host_uri(bf)))
    for bf in begrepp:
        corpus.cmd_generate(SOURCES,
                            only={str(layout.artifact("begrepp", bf))},
                            source="begrepp", force=True,
                            aggregates=generate_aggregates)
        urls.append(layout.page_url(markdown.begrepp_uri(bf)))
    if site:
        # write_site rewrites all editorial pages
        corpus.cmd_generate(SOURCES, source="site",
                            aggregates=generate_aggregates)
        urls += ["/" if bf == "frontpage" else "/" + bf for bf in site]
    for bf in graphics:                  # the crop rides its own statute's page
        corpus.cmd_generate(SOURCES, only={str(layout.artifact("sfs", bf))},
                            source="sfs", force=True,
                            aggregates=generate_aggregates)
        urls.append(layout.page_url(api_graphicsedit.document_uri(bf)))
    return urls


# wire the editor's commit endpoint to the rebuild above (build imports the api
# package, so this is the sound direction to close the loop -- see api/edit.py)
api_edit.set_rebuild(rebuild_after_commit)


def reparse_one(source, basefile):
    """Force-reparse one document, writing its artifact JSON in place -- the
    patch editor's post-save hook, so a just-saved patch is immediately effective
    in the corpus (the artifact is what the API and the next `generate` read).
    Reuses the source's own parse recipe, like `rebuild_after_commit` does for
    the markdown editor."""
    stage = SOURCES[source].stages.get("parse")
    if stage is None:
        raise ValueError("source %r has no parse stage" % source)
    stage.run(basefile)


api_patch.set_reparse(reparse_one)


def cmd_serve(host="127.0.0.1", port=8000):
    # one process serves the whole thing: the static site and the REST API it
    # consumes -- the public API under /api/v1/, the site's own surface (the
    # editors, the export's jobs) under /internal-api/v1/, and the site
    # everything else. Same origin, so the ⌘K palette needs no second port.
    if not layout.GENERATED.exists():
        raise SystemExit("nothing generated yet -- run `lagen all generate` first")
    # show the LAN-reachable host when bound to a wildcard, else localhost
    shown = "localhost" if host in ("127.0.0.1", "localhost") else host
    print("serving site + API at http://%s:%d/  "
          "(API under /api/v1/, docs at /docs, Ctrl-C to stop)" % (shown, port))
    api_app.serve(str(layout.GENERATED), host=host, port=port)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _help(name):
    """Contextual `lagen <source> -h`: the source's actions, harvest scopes and
    any source-specific flags."""
    src = SOURCES[name]
    verbs = ["download"] if src.harvest else []
    verbs += [s for s in src.stages if s not in verbs] + list(src.actions)
    print("usage: lagen %s <action> [ids|scopes] [options]" % name)
    if src.origin:
        print("\nsource:  %s" % src.origin)
    print("actions: %s" % ", ".join(verbs))
    if src.scopes:
        print("\ndownload scopes (narrow the download to sub-corpora):")
        print("  %s" % ", ".join(sorted(src.scopes)))
        print("  e.g. `lagen %s download %s`   (no scope = the whole corpus)"
              % (name, sorted(src.scopes)[0]))
    if src.harvest is not None and "download" in src.stages:
        print("\n`lagen %s download <id>` refetches a single document by id." % name)
    if src.notes:
        print("\n%s" % src.notes)
    print("\nglobal options: `lagen -h`")


FAILURE_SUMMARY_ERRORS_CAP = 10   # detail lines shown per failed step


def _print_failure_summary(run_id):
    """The reason `lagen` is about to exit non-zero, once, at the very end --
    not just the exit code, which is all `lagen all rebuild && sync-up` sees
    when the failure was a step scrolled off screen long before the run's
    last line. Reads `run_detail`'s segments (every verb emits one, with a
    pass/fail status and an error count, uniformly) for *which* steps failed
    this run, and `errors.json`'s per-basefile entries (parse/versions/dump-
    phase stages only -- the per-document ones; index/dump/generate report a
    count but not a message per failure) for *what went wrong* where that
    detail exists.

    A `run_ok=False` with no failed segment at all means the process crashed
    outright (an uncaught exception, not a per-document failure) before
    anything could be tallied -- said explicitly, since an empty step list
    would otherwise read as "nothing failed" beside a non-zero exit code."""
    detail = runlog.run_detail(freshness.RUNS, run_id)
    failed = [s for s in (detail or {}).get("segments", []) if s["status"] == "errors"]
    if not failed:
        util.write("lagen exited with errors, but recorded no failed step -- "
                  "the traceback above is the reason.", err=True)
        return
    util.write("lagen exited with errors -- %d step(s) failed this run:"
              % len(failed), err=True)
    this_run = {k: v for k, v in runlog.read_errors(freshness.ERRORS).items()
               if v["run"] == run_id}
    for seg in failed:
        util.write("  %s %s: %d error(s)" % (seg["step"], seg["source"],
                                             seg["errors"]), err=True)
        prefix = "%s/%s/" % (seg["source"], seg["step"])
        detail_keys = sorted(k for k in this_run if k.startswith(prefix))
        for key in detail_keys[:FAILURE_SUMMARY_ERRORS_CAP]:
            util.write("    %s: %s" % (key[len(prefix):], this_run[key]["error"]),
                      err=True)
        if len(detail_keys) > FAILURE_SUMMARY_ERRORS_CAP:
            util.write("    ... and %d more"
                      % (len(detail_keys) - FAILURE_SUMMARY_ERRORS_CAP), err=True)


def main(argv=None):
    # a dependency's warnings.warn (cryptography, lxml, ...) must not tear an
    # active progress line -- see util.write. Installed here, not at import
    # time, so importing ferenda for a test or a one-off script never changes
    # process-wide warning behaviour underneath it.
    util.install_warnings_hook()
    argv = list(sys.argv[1:] if argv is None else argv)
    # contextual help: `lagen <source> [action] -h` -> that source's help
    if "-h" in argv or "--help" in argv:
        leading = next((a for a in argv if not a.startswith("-")), None)
        if leading in SOURCES:
            _help(leading)
            return
    p = argparse.ArgumentParser(prog="lagen", description=(__doc__ or "").split("\n")[0])
    p.add_argument("source", help="source name (%s), 'all', or 'ann' (the "
                   "curated LLM-layer store: `lagen ann status`)"
                   % ", ".join(SOURCES))
    p.add_argument("action",
                   help="download | parse | relate | generate | index | dump "
                        "| rebuild | all | serve | status | errors | patch-show "
                        "| mkpatch "
                        "| a source action (e.g. dv reindex). `errors` prints "
                        "the served site's error ledger, newest first (the "
                        "newest 50, or N with `lagen all errors 200`), or one "
                        "entry in full when given the 8-hex id an error page "
                        "showed (`lagen all errors 3f9a1c07`). `rebuild` runs the "
                        "offline pipeline (parse -> relate -> index -> dump -> "
                        "generate) over already-downloaded data; `all` is "
                        "download followed by rebuild. Every step is incremental, "
                        "so a no-change re-run is cheap")
    p.add_argument("basefiles", nargs="*",
                   help="ids to act on (empty = all stale); for download, names "
                        "download sub-scopes, e.g. 'prop' or 'acts'")
    p.add_argument("-f", "--force", action="store_true",
                   help="rebuild the named stage even if fresh")
    p.add_argument("--no-deps", action="store_true",
                   help="run only the named stage, not its upstream deps")
    p.add_argument("--ignore-code-changes", action="store_true",
                   help="treat outputs as fresh even when the recipe code changed "
                        "(parse/generate, and the extraction/index code behind "
                        "relate/index) -- rebuild only on input-data changes "
                        "(dev convenience; off in production)")
    p.add_argument("--aggregates-only", action="store_true",
                   help="generate: rewrite only the corpus-wide pages (frontpage "
                        "+ browse indexes) from the catalog, skipping the "
                        "per-document render")
    p.add_argument("--assets-only", action="store_true",
                   help="generate: copy only the static chrome (style.css + the "
                        "scripts) -- the minimal refresh after a CSS/JS change, "
                        "no catalog or per-document render")
    p.add_argument("-j", "--jobs", type=int, default=None,
                   help="parallel workers for the parallelisable steps (parse, "
                        "index); default = number of CPU cores, `-j1` to serialise")
    p.add_argument("-n", "--dry-run", action="store_true",
                   help="print the plan, do nothing")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="stream per-step progress to stderr -- the long ai-* "
                        "vision passes (e.g. sfs ai-includegraphics) otherwise "
                        "print nothing until they finish; shows the source PDF, "
                        "page range and elapsed time per vision call")
    p.add_argument("--port", type=int, default=8000,
                   help="port for `serve` -- site + API in one process (default 8000)")
    p.add_argument("--host", default="127.0.0.1", metavar="ADDR",
                   help="interface for `serve` to bind (default 127.0.0.1, "
                        "localhost only; use 0.0.0.0 to expose on the LAN)")
    p.add_argument("--since", type=date.fromisoformat, metavar="YYYY-MM-DD",
                   help="eurlex download: only discover documents dated on/after "
                        "this (overrides the per-sector watermark for this run)")
    p.add_argument("--lang", metavar="CODES",
                   help="eurlex/hudoc download: comma-separated languages "
                        "(defaults: eurlex swe,eng; hudoc ENG)")
    p.add_argument("--source", dest="discovery", choices=("sparql", "soap"),
                   default="sparql",
                   help="eurlex download: discovery backend (default sparql)")
    p.add_argument("--only", metavar="BASEFILE",
                   help="fetch just this one document, bypassing the listing "
                        "walk (hudoc/coe accept an item id/treaty number; "
                        "forarbete/foreskrift/avg/rs need exactly "
                        "one doctype/fs/organ/agency scope; remisser download: "
                        "one case URL)")
    p.add_argument("--riksmote", metavar="YYYY/YY",
                   help="forarbete download bet: narrow the download to one "
                        "riksmöte, e.g. 2025/26 (bet scope only)")
    p.add_argument("--limit", type=int, metavar="N",
                   help="harvest/import at most N documents (a test/backfill slice; "
                        "supported by hudoc/coe and legacy imports)")
    p.add_argument("--obfuscated", action="store_true",
                   help="mkpatch: store the patch ROT18-obfuscated, so a "
                        "redaction of personal data is not plain-text googleable "
                        "in the committed patch")
    p.add_argument("--resume-after", metavar="JSON",
                   help="sfs download: resume a backfill interrupted mid-sweep, "
                       "from the ES search_after cursor printed when it was "
                       "interrupted")
    p.add_argument("--update", action="store_true",
                   help="remisser ai-analyze: re-analyze every ärende already "
                        "analysed whose remissperiod is still open, picking up "
                        "answers that arrived since")
    p.add_argument("--matching", metavar="PREFIX",
                   help="remisser ai-analyze: select every ärende whose basefile "
                        "starts with PREFIX (e.g. 'sou/'), most-recently-updated "
                        "first, instead of naming basefiles")
    p.add_argument("--rebuild-history", action="store_true",
                   help="sfs/eurlex history-as-git: rebuild main from the "
                        "complete current corpus when corrected or backfilled "
                        "history cannot be appended safely")
    p.add_argument("--all", action="store_true", dest="every",
                   help="eurlex refresh-metadata: re-read every downloaded "
                        "document, repealed ones included -- the one-off "
                        "backfill, not the shrinking repeal audit. "
                        "sfs ai-hierarki: every lag whose chain reaches a "
                        "föreskrift")
    args = p.parse_args(argv)

    protocol.RUN.dry_run, protocol.RUN.force, protocol.RUN.no_deps = args.dry_run, args.force, args.no_deps
    protocol.RUN.verbose = args.verbose
    protocol.RUN.ignore_code_changes = args.ignore_code_changes
    protocol.RUN.aggregates_only = args.aggregates_only
    protocol.RUN.assets_only = args.assets_only
    protocol.RUN.since, protocol.RUN.lang, protocol.RUN.source = args.since, args.lang, args.discovery
    protocol.RUN.only = args.only
    protocol.RUN.riksmote = args.riksmote
    protocol.RUN.limit = args.limit
    protocol.RUN.obfuscated = args.obfuscated
    protocol.RUN.resume_after = args.resume_after
    protocol.RUN.rebuild_history = args.rebuild_history
    protocol.RUN.every = args.every
    protocol.RUN.update = args.update
    protocol.RUN.matching = args.matching
    # the parallelisable steps default to all cores; -j1 serialises
    jobs = args.jobs if args.jobs is not None else (os.cpu_count() or 1)
    protocol.RUN.jobs = jobs      # harvests that fan out (foreskrift's per-agency pool) read it

    # A pipeline invocation is wrapped in the run ledger: mint a run id, prune old
    # runs, emit run-start, and (try/finally, so a crash or Ctrl-C still lands it)
    # run-end. serve/status/runs read or serve; --dry-run writes nothing -- none get
    # a run id, and the no-run-id invariant makes every runlog emission below a
    # no-op for them.
    # reset unconditionally so a second in-process main() (e.g. a --dry-run or a
    # non-pipeline verb after a pipeline run) never inherits the prior run's id
    # or error tally
    freshness.start_run()
    if args.action not in ("serve", "status", "runs", "errors") \
            and not protocol.RUN.dry_run:
        run_id = freshness.start_run(os.getpid())
        runlog.prune(freshness.RUNS)
        runlog.emit_run_start(freshness.RUNS, run_id, ["lagen", *argv],
                              os.getpid())
    t0 = time.perf_counter()
    ok = False
    try:
        _dispatch(args, p, jobs)
        ok = True          # only a clean return counts; any exception or a
                           # SystemExit(nonzero) from _dispatch leaves ok False
    finally:
        if freshness.RUN_ID is not None:
            # ok from the success flag, folded with THIS run's error total
            # (RUN_ERRORS) -- not the corpus-wide currently-failing count, which
            # lives in errors.json and the /ops overview
            run_ok = ok and freshness.RUN_ERRORS == 0
            runlog.emit_run_end(freshness.RUNS, freshness.RUN_ID,
                                time.perf_counter() - t0, run_ok, freshness.RUN_ERRORS)
            if not run_ok:
                # `lagen all rebuild` scrolls the actual failure off screen long
                # before the run's last line -- and that line, on its own, only
                # says a step somewhere failed. Print what and where, once, here,
                # so the exit code has a reason attached beside it.
                _print_failure_summary(freshness.RUN_ID)


def _cmd_runs(limit):
    """`lagen all runs [N]`: print the newest N run summaries from the ledger
    (neither a stage nor a source action, so intercepted before the dispatch loop
    and excluded from run-ledger wrapping)."""
    runs = runlog.read_runs(freshness.RUNS)
    if limit:
        runs = runs[:limit]
    if not runs:
        print("no runs recorded yet (%s)" % freshness.RUNS)
        return
    for r in runs:
        secs = "%.1fs" % r["secs"] if r["secs"] is not None else "-"
        print("%s  %-8s %9s  %2d seg  %d err  %s"
              % (r["run"], r["status"], secs, r["segments"], r["errors"],
                 " ".join(r["argv"]) if r["argv"] else
                 "(command line lost -- pruned by a concurrent run)"))


ERRORS_DEFAULT_LIMIT = 50       # `lagen all errors` with no argument


def _cmd_errors(arg=None):
    """`lagen all errors [<id> | <N>]`: the served site's error ledger.

    Bare, it lists the newest `ERRORS_DEFAULT_LIMIT` errors, one line each --
    capped because a bot storm (or a storage fault turning every request into a
    404) can fill both ledger generations, and dumping 16 MB at a terminal
    helps nobody. `<N>` asks for a different count, the way `runs [N]` does;
    `<id>` -- the eight hex characters the error page showed the reader --
    prints that one request in full, traceback included.

    Like `runs`, neither a stage nor a source action, so it is intercepted
    before the dispatch loop and writes no run-ledger entry of its own."""
    # an id is matched on *shape*, not on being non-numeric: ids are
    # secrets.token_hex(4), and about 1 in 43 of those is all digits -- so
    # `arg.isdigit()` would read a reader's quoted "20260731" as a count and
    # silently print 20 million ledger lines instead of their one record
    error_id = arg if arg and errorlog.RE_ID.fullmatch(arg) else None
    limit = None if error_id else (int(arg) if arg else ERRORS_DEFAULT_LIMIT)
    if arg and error_id is None and not arg.isdigit():
        raise ValueError("%r is neither an 8-hex error id nor a count" % arg)
    records = errorlog.entries(api_errors.LEDGER, error_id=error_id, limit=limit)
    if not records:
        print("no error %r in the ledger (%s)" % (error_id, api_errors.LEDGER)
              if error_id
              else "no errors recorded yet (%s)" % api_errors.LEDGER)
        return
    if not error_id:
        for r in records:
            print("%s  %s  %3s  %-4s %s%s"
                  % (r["id"], r["time"], r["status"], r["method"] or "-",
                     r["url"] or "-",
                     "  <- %s" % r["referer"] if r["referer"] else ""))
        return
    for r in records:
        # every record carries every FIELDS key (errorlog.record writes them
        # all, absent ones as None), so index rather than .get -- a missing key
        # would be a schema drift worth crashing on, not worth papering over
        for key in errorlog.FIELDS:
            if r[key] in (None, ""):
                continue
            # the traceback is the only multi-line field; give it its own block
            # rather than crushing it onto a label line
            if key == "traceback":
                print("\ntraceback:\n%s" % r[key].rstrip())
            else:
                print("%-12s %s" % (key + ":", r[key]))


def cmd_patch_show(args, p):
    """`lagen <source> patch-show <basefile>` -- print a document's intermediate
    source text (the format its patch targets: plain text for sfs, innehåll HTML
    for dv, Formex XML for eurlex), with any existing patch already applied, to
    stdout. Redirect it to a file, hand-edit that file, then feed it back to
    `mkpatch` to author a minimal patch."""
    if not patchsource.is_patchable(args.source):
        p.error("source %r has no patchable intermediate (patchable: %s)"
                % (args.source, ", ".join(patchsource.patchable_sources())))
    if len(args.basefiles) != 1:
        p.error("patch-show needs exactly one basefile")
    basefile = args.basefiles[0]
    text, label = patchsource.current(args.source, basefile)
    sys.stderr.write("# %s %s -- intermediate format: %s%s\n"
                     % (args.source, basefile, label,
                        " (patch applied)" if patch.has_patch(args.source, basefile)
                        else ""))
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


def cmd_mkpatch(args, p):
    """`lagen <source> mkpatch <basefile> <edited-file> [description]` -- author a
    patch from a hand-edited copy of the intermediate text. Diffs the pristine
    intermediate against `<edited-file>` and writes the minimal unified diff to
    the document's patch location (`patches/<source>/…`). `--obfuscated` stores it
    obfuscated (redactions of personal data). An edited file identical to the
    pristine text removes any existing patch."""
    if not patchsource.is_patchable(args.source):
        p.error("source %r has no patchable intermediate (patchable: %s)"
                % (args.source, ", ".join(patchsource.patchable_sources())))
    if not 2 <= len(args.basefiles) <= 3:
        p.error("mkpatch needs: <basefile> <edited-file> [description]")
    basefile, edited_path = args.basefiles[0], args.basefiles[1]
    description = args.basefiles[2] if len(args.basefiles) == 3 else ""
    pristine, label = patchsource.intermediate(args.source, basefile)
    edited = Path(edited_path).read_text(encoding="utf-8")
    if protocol.RUN.dry_run:
        print(patch.make_patch_text(pristine, edited, description)
              or "mkpatch: no differences; nothing to write")
        return
    path = patch.create_patch(args.source, basefile, pristine, edited,
                              description=description,
                              obfuscated=protocol.RUN.obfuscated)
    if path is None:
        print("mkpatch %s %s: no differences; removed any existing patch"
              % (args.source, basefile))
    else:
        print("mkpatch %s %s: wrote %s patch %s (%s intermediate)"
              % (args.source, basefile, "obfuscated" if protocol.RUN.obfuscated else "plain",
                 path, label))


def _catalog_current_for(name, basefiles):
    """The targeted make-check behind `lagen <source> generate <ids>`: are the
    requested documents' catalog rows current? The row's `content_hash` is
    relate's receipt for the artifact's exact bytes, so re-hashing the named
    artifacts and comparing settles it from the catalog alone -- no whole-source
    stat pass over 60k+ files (cmd_relate's per-source gate). Any mismatch --
    missing catalog, unparsed artifact, missing row, changed bytes, changed
    relate code, edited cross-pass layers -- returns False and the caller falls
    back to the full (incremental) per-source relate. The cross-pass check
    matters because a page's freshness signature folds its own .corr/.ann bytes
    in: without it, authoring a layer and then rendering the page targeted
    would re-render from the catalog that never loaded the layer -- and stamp
    the wrong page fresh."""
    store = freshness.load_fingerprints()
    if not layout.CATALOG.exists():
        return False
    if freshness.code_changed(store, "relate", name, corpus.RELATE_CODE):
        return False           # rows exist but were extracted by older code
    if not freshness.fingerprint_fresh(store, "relate", "__corr__",
                                       corpus._corr_watermark(protocol.SOURCES)):
        return False           # an authored layer awaits the cross-passes
    con = catalog.connect(layout.CATALOG)
    root = catalog.data_root(con)
    try:
        for bf in basefiles:
            art = layout.artifact(name, bf)
            if not compress.exists(art):
                return False
            row = con.execute("SELECT content_hash FROM documents WHERE path = ?",
                              (str(art.relative_to(root)),)).fetchone()
            if row is None or row[0] != catalog.content_hash(
                    compress.read_bytes(art)):
                return False
    finally:
        con.close()
    return True


def _prepare_targeted_generate(source, basefiles, jobs):
    """Bring the artifacts behind a document-scoped generate up to date.

    ``generate`` is implemented outside the per-document Stage graph because its
    freshness also depends on catalog relationships.  That must not make
    ``lagen sfs generate 1994:1219`` an exception to the driver's make semantics:
    refresh the requested documents' parse/versions stages, then relate the
    resulting artifacts before rendering them.  The relate check is targeted
    (`_catalog_current_for`): the full per-source relate runs only when a
    requested document's catalog row disagrees with its artifact, or an
    authored cross-pass layer changed.
    ``--force`` belongs to the named generate action, not these prerequisites,
    so upstream stages remain freshness-checked: each of them is *passed*
    `force=False` rather than reading it off the run options, so the override
    cannot leak into anything else the run goes on to do.
    """
    if protocol.RUN.no_deps:
        return False
    had_errors = False
    for stage in ("parse", "versions"):
        if stage not in source.stages:
            continue
        result = freshness.run_action(source, stage, basefiles, jobs, force=False)
        corpus.report(source, stage, result, len(basefiles), full_source=False)
        had_errors |= bool(result.errors)
    if not had_errors and not protocol.RUN.dry_run and source.artifacts \
            and not _catalog_current_for(source.name, basefiles):
        # a --force meant for one page's generate must not re-extract every
        # artifact of its source
        corpus.cmd_relate(SOURCES, [source.name], force=False)
    return had_errors


def _dispatch(args, p, jobs):
    """Route one parsed invocation to its command. Split out of main() so main
    can wrap the whole dispatch in a single run-start/run-end try/finally."""
    if args.source == "ann":
        # the curated LLM-layer store is not a source; `lagen ann status` is its
        # one verb (writes happen only through the per-source ai-* actions)
        if args.action != "status":
            p.error("unknown ann action %r (have: status)" % args.action)
        corpus.cmd_ann_status()
        return
    if args.action == "patch-show":
        cmd_patch_show(args, p)
        return
    if args.action == "mkpatch":
        cmd_mkpatch(args, p)
        return
    # generate is corpus-wide by default, but `lagen <source> generate <id> ...`
    # targets just those documents (and leaves the aggregate pages alone)
    if args.action == "generate":
        # `all generate` = the whole corpus (+ aggregates); `<source> generate` =
        # that source's pages (incl. synthesized stubs, which have no artifact
        # file); `<source> generate <ids>` = just those docs. A document-scoped
        # run refreshes its parse/versions prerequisites and catalog rows unless
        # --no-deps was given, but still leaves corpus-wide aggregate pages alone.
        if args.source == "all":
            if args.basefiles:
                p.error("`all generate <ids>` needs a specific source, e.g. "
                        "`lagen eurlex generate 32022L2555`")
            corpus.cmd_generate(SOURCES, jobs=jobs,
                                aggregates=generate_aggregates)
        elif args.source not in SOURCES:
            p.error("unknown source %r (have: %s)"
                    % (args.source, ", ".join(SOURCES)))
        elif args.basefiles:
            if _prepare_targeted_generate(SOURCES[args.source], args.basefiles,
                                          jobs):
                sys.exit(1)
            corpus.cmd_generate(SOURCES,
                                only={str(layout.artifact(args.source, bf))
                                      for bf in args.basefiles}, jobs=jobs,
                                aggregates=generate_aggregates)
        else:
            corpus.cmd_generate(SOURCES, source=args.source, jobs=jobs,
                                aggregates=generate_aggregates)
        return
    if args.action == "serve":
        cmd_serve(args.host, args.port)
        return
    if args.action == "runs":
        _cmd_runs(int(args.basefiles[0]) if args.basefiles else None)
        return
    if args.action == "errors":
        _cmd_errors(args.basefiles[0] if args.basefiles else None)
        return

    names = list(SOURCES) if args.source == "all" else [args.source]
    if any(n not in SOURCES for n in names):
        p.error("unknown source %r (have: %s)" % (args.source, ", ".join(SOURCES)))

    if args.action in ("rebuild", "all"):
        had_errors = corpus.cmd_all(SOURCES, names, jobs,
                                    whole_corpus=args.source == "all",
                                    download=args.action == "all",
                                    aggregates=generate_aggregates)
        if had_errors:
            sys.exit(1)
        return
    if args.action == "relate":
        corpus.cmd_relate(SOURCES, names)
        corpus.run_after(SOURCES, names, "relate")
        return
    if args.action == "index":
        had_errors = corpus.cmd_index(SOURCES, names, jobs)
        corpus.run_after(SOURCES, names, "index")
        if had_errors:
            sys.exit(1)
        return
    if args.action == "dump":
        corpus.cmd_dump(SOURCES, names)
        corpus.run_after(SOURCES, names, "dump")
        return

    had_errors = False
    for name in names:
        source = SOURCES[name]
        if args.action == "status":
            if args.basefiles:
                for basefile in args.basefiles:
                    corpus.cmd_status_document(source, basefile)
            else:
                corpus.cmd_status(source)
            continue
        if args.action == "download":
            scopes = args.basefiles
            if source.harvest is not None and (
                    not scopes or all(s in source.scopes for s in scopes)):
                # bulk discovery, optionally narrowed to named sub-scopes
                # (forarbete doctypes / eurlex sectors). The per-doc stage only
                # refetches known ids; new docs come only from the bulk sweep,
                # so this must NOT fall back to list_basefiles().
                had_errors |= corpus._run_harvest(source, scopes)
                corpus.run_after(SOURCES, [name], "download")
                continue
            if scopes and source.scopes and "download" not in source.stages:
                bad = [s for s in scopes if s not in source.scopes]
                p.error("unknown %s scope(s): %s (have: %s)"
                        % (name, ", ".join(bad), ", ".join(sorted(source.scopes))))
            if not scopes and source.harvest is None:
                if args.source == "all" and "download" not in source.stages:
                    continue
                p.error("source %r has no bulk harvest" % name)
            # scopes are document ids -> fall through to the per-doc download stage
        if args.action in source.actions:
            t0 = time.perf_counter()
            report = source.actions[args.action](args.basefiles)
            if not isinstance(report, aireport.Report):
                # an ai-* action reports itself (counts, status.json); the rest
                # get the bare "it ran" segment
                freshness._emit_segment(args.action, name, time.perf_counter() - t0, status="ok")
            continue
        if args.action not in source.stages:
            # an "all" sweep visits every source; one that lacks the action
            # simply has nothing to do (stats has only compute, two sources
            # have browser-download) -- same shortcut the download branch
            # takes above. A *named* source keeps the hard error.
            if args.source == "all":
                continue
            p.error("source %r has no action %r (have: %s)"
                    % (name, args.action,
                       ", ".join([*source.stages, *source.actions])))
        # a full-source run of a fingerprint-gated per-doc stage gets the same
        # coarse "up to date -- skipped" shortcut cmd_all uses, so a direct
        # `lagen sfs parse` with nothing changed skips the per-doc scan too
        if not args.basefiles and args.action in ("parse", "versions"):
            store = freshness.load_fingerprints()
            errs, recorded = corpus._run_stage_gated(source, args.action, jobs,
                                                     store)
            if recorded:
                freshness.save_fingerprints(store)
            corpus.run_after(SOURCES, [name], args.action)
            had_errors |= errs
            continue
        basefiles = args.basefiles or source.list_basefiles()
        result = freshness.run_action(source, args.action, basefiles, jobs)
        corpus.report(source, args.action, result, len(basefiles),
                      full_source=not args.basefiles)
        had_errors |= bool(result.errors)
    if had_errors:                 # report every source first, then signal failure
        sys.exit(1)


if __name__ == "__main__":
    main()
