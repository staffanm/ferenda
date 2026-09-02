"""The sfs source's registration: the Swedish statute book (Svensk
författningssamling), as consolidated by the beta database.

Three stages: a per-act download, the parse that produces the in-force
wording, and a versions stage that parses every archived consolidation into
its own artifact. Beside them sit the acts' own facsimile mirror and the
opt-in `ai-*` passes that place the graphics the consolidated text drops and
author the regleringshierarki rows.

Three more actions -- `ai-correspond`, `table-correspond` and `history-as-git`
-- read a proposition, which is förarbete's job. A source may not import a
sibling (rule:lib-never-imports-vertical), so those live in `build.py` and are
hung on this registration there; the notes below document all of them.
"""

import functools
import json
import sys
import time
from pathlib import Path

from .. import config
from ..lib import (
    aihierarki,
    annstore,
    catalog,
    compress,
    freshness,
    layout,
    llm,
    util,
)
from ..lib import stage as protocol
from ..lib.datasets import NAMEDLAWS
from ..lib.errors import SkipDocument
from ..lib.lagrum import LagrumParser, load_namedlaws
from ..lib.stage import (
    CITATION_DATA,
    POLITENESS,
    Source,
    Stage,
    origin,
    patch_input,
    write_artifact,
)
from . import correspond, download, graphics, load_inputs, pdfmirror, render, versions
from .extract import extract_body
from .nf import to_normalform
from .register import resolve_omfattning

HERE = Path(__file__).parent

SFS_CODE = tuple(HERE / ("%s.py" % m) for m in (
    "__init__", "extract", "reader", "tokenizer", "assembler", "model", "nf",
    "parallelappendix", "register", "bemyndigande",
    "graphics")) + (HERE.parent / "lib" / "lagrum.py", HERE.parent / "lib" / "emdref.py",
                    HERE.parent / "lib" / "begrepp.py", HERE.parent / "lib" / "markdown.py",
                    *CITATION_DATA)


@functools.cache
def _namedlaws():
    return load_namedlaws(NAMEDLAWS)


def sfs_downloaded(basefile):
    return layout.sfs_sfst(basefile)


def sfs_source(basefile):
    """The new beta-API _source JSON (downloaded/{y}/{n}.json), the primary
    form; the legacy SFST/SFSR HTML sit in downloaded/sfst|sfsr/ siblings."""
    return layout.sfs_source(basefile)


def sfs_register(basefile):
    return layout.sfs_sfsr(basefile)


def sfs_inputs(basefile):
    """Freshness inputs: the JSON _source when present (the new beta API), else
    the legacy SFST + SFSR HTML pair -- plus the document's patch file if one
    exists (`patch_input`), so editing a patch re-stales the parse."""
    if compress.exists(sfs_source(basefile)):
        inputs = [sfs_source(basefile)]
    else:
        inputs = [sfs_downloaded(basefile), sfs_register(basefile)]
    return inputs + patch_input("sfs", basefile)


def sfs_download_run(basefile):
    """Fetch one named act's consolidated _source from the beta database,
    archiving any superseded consolidation (the old download_single). New-act
    *discovery* is sfs_harvest (bare `lagen sfs download`), not this."""
    source = download.fetch_one(protocol.session(download), basefile)
    if source is None:
        raise RuntimeError("no published act %s in the beta database" % basefile)
    download.save_document(layout.SFS_DOWNLOADED, source)
    time.sleep(POLITENESS)


def sfs_harvest(scopes):
    """Bulk discovery harvest -- a search_after sweep of the whole corpus, the
    only way to find acts not yet on disk (the old download_new). Incremental
    by default (stops at the first page with nothing new); `--force` walks the
    entire corpus oldest-first. Throttled and self-logging (per page).

    Mirrors the official PDFs of whatever it found, too: the facsimiles are the
    same acts from the same publisher, and leaving them to a separate command
    let the two drift. Always incrementally, `--force` or not -- an act already
    mirrored, or known to have no PDF, costs nothing, so only the first harvest
    pays for the corpus-wide backfill. `--force` here scopes *discovery* (walk
    the whole corpus rather than stop at the first known page); re-fetching
    every facsimile is `mirror-pdf --force`, and asking for one is no way to ask
    for the other."""
    if protocol.RUN.dry_run:
        print("sfs download: would download the corpus into %s"
              % layout.SFS_DOWNLOADED)
        return
    resume_after = json.loads(protocol.RUN.resume_after) if protocol.RUN.resume_after else None
    seen, new, updated, skipped = download.sync(layout.SFS_DOWNLOADED,
                                                    full=protocol.RUN.force,
                                                    resume_after=resume_after)
    print("sfs download: %d seen, %d new, %d updated, %d skipped"
          % (seen, new, updated, skipped))
    pdfmirror.mirror(protocol.session(download), pdfmirror.corpus_beteckningar(sfs_list()),
                     force=False, dry_run=protocol.RUN.dry_run)
    # sfs splits "changed" in two -- an act we did not hold, and a new lydelse of
    # one we did. Both wrote a record, so both count as work done
    return seen, new + updated


def sfs_parse_run(basefile):
    doc, register, sfst_header = load_inputs(
        sfs_source(basefile), sfs_downloaded(basefile),
        sfs_register(basefile), basefile)
    nf = to_normalform(doc, basefile,
                       refparser=LagrumParser(_namedlaws(), basefile),
                       register=register, sfst_header=sfst_header)
    write_artifact("sfs", basefile, nf)


def sfs_intermediate(basefile):
    """SFS's intermediate is the plain consolidated statute text -- straight from
    the beta-API JSON's ``forfattningstext`` when present, else recovered from
    the legacy SFST HTML exactly as the parser does (``sfs.extract.extract_body``)."""
    src = layout.sfs_source(basefile)
    if compress.exists(src):
        text = (compress.read_json(src).get("fulltext") or {}).get("forfattningstext")
        if text is None:
            raise SkipDocument("%s: no forfattningstext to patch" % basefile)
        return text.replace("\r", "")
    return extract_body(layout.sfs_sfst(basefile))


SFS_VERSIONS_CODE = SFS_CODE + (HERE / "versions.py",)


def sfs_versions_inputs(basefile):
    """Freshness inputs of the versions stage: every archived consolidation,
    plus the statute's patch -- the same patch is offered to every archived
    wording (`patch.apply_if_fits`), so editing it re-stales the whole history.
    Archive files are immutable once written, so this set otherwise changes only
    when the downloader supersedes a consolidation (or history is imported)."""
    return ([path for _, path in layout.sfs_version_downloads(basefile)]
            + patch_input("sfs", basefile))


def sfs_versions_sidecar(basefile):
    return layout.sfs_versions_sidecar(basefile)


def sfs_versions_run(basefile):
    """Parse every archived consolidation of one statute into per-version
    artifacts + the sidecar index (see sfs.versions). The sidecar is written
    even when the statute has no archive, marking the stage built."""
    versions.build(basefile,
                           refparser=LagrumParser(_namedlaws(), basefile))


def sfs_list():
    """Every *regular* SFS basefile with a source: the new beta JSON
    (downloaded/{y}/{n}.json) or the legacy SFST HTML (downloaded/sfst/).

    Acts whose year segment is non-numeric -- amendments to government-agency
    regulations carrying a letter prefix, e.g. 'N2026:3' -- are harvested and
    stored but excluded here: they don't belong in the SFS-centric publication
    and will be picked up by the myndfskr (myndighetsföreskrifter) port."""
    return sorted({"%s:%s" % (p.parent.name, p.stem.replace("_", " "))
                   for p in compress.glob(layout.SFS_DOWNLOADED, "*/*.json")
                   if p.parent.name.isdigit() and not p.name.startswith(".")}
                  | {"%s:%s" % (p.parent.name, p.stem.replace("_", " "))
                     for p in compress.glob(layout.SFS_DOWNLOADED / "sfst", "*/*.html")
                     if not p.name.startswith(".")})


def sfs_renumber_correspond(basefiles):
    """`lagen sfs renumber-correspond <sfs> [...]` -- derive the same-law
    paragraf renumbering map from a statute's own amendment register: every
    "nuvarande … betecknas …" omfattning clause (RF via SFS 2010:1408)
    becomes 'betecknas' edges carrying the amendment's ikrafttradandedatum,
    written as the statute's `.corr` layer. Purely mechanical -- the register
    is already in the parsed artifact -- but stored like the other
    correspondence routes so relate/generate treat all three alike. The layer
    path is shared with table-/ai-correspond, so a law that is *both* a
    re-enactment and renumbered internally cannot yet hold both; the action
    refuses to overwrite an existing layer without --force."""
    if not basefiles:
        sys.exit("usage: lagen sfs renumber-correspond <sfs> [...]  "
                 "(e.g. 1974:152)")
    for sfs in basefiles:
        art = compress.read_json(layout.artifact("sfs", sfs))
        payload, stats = correspond.renumbering_payload(art)
        out = annstore.path("sfs", sfs, ".corr")
        if not payload["correspondence"]["edges"]:
            print("sfs renumber-correspond %s: no renumbering amendments in "
                  "the register; nothing written" % sfs)
            continue
        if protocol.RUN.dry_run:
            print("sfs renumber-correspond: would derive %d edges from %d "
                  "amendment(s) of %s -> %s"
                  % (stats["edges"], stats["amendments"], sfs, out))
            continue
        if out.exists() and not protocol.RUN.force:
            # the shared layer path may hold a table-/ai-correspond layer;
            # clobbering it silently would lose those edges
            raise ValueError("%s exists (another correspondence route wrote "
                             "it); pass --force to replace it" % out)
        annstore.guard(out, protocol.RUN.force)
        annstore.write(out, payload, annstore.artifact_input("sfs", sfs),
                       protocol.RUN.force, model="omfattning")
        print("sfs renumber-correspond %s: %d edges from %d renumbering "
              "amendment(s), wrote %s"
              % (sfs, stats["edges"], stats["amendments"], out))


def sfs_mirror_pdf(basefiles):
    """`lagen sfs mirror-pdf [<sfs> ...]` -- mirror the officially published SFS
    PDFs the consolidated text drops its graphics from, keyed by SFS number,
    into downloaded/sfs/pdf/. With no arguments, mirror the whole corpus (every
    base act + every andringsforfattning across the downloaded registers);
    otherwise just the named acts -- `mirror-pdf 2007:90` fetches that one PDF
    and nothing else. Each act's source follows from its SFS number
    (`pdfmirror.has_facsimile` / `is_online_series`); naming one older than both
    sources is an error. Idempotent -- already-present PDFs are skipped unless
    --force is given. The source for the localization crop.

    A rerun is cheap because both answers are local: an act already mirrored is
    skipped from disk, and one the upstream has already denied from the mirror's
    record of those (`pdfmirror.MirrorState`). Only an act nobody has asked
    about yet costs a request."""
    targets = list(basefiles)
    # A named act older than every facsimile source is a question with no
    # answer, so say so rather than report it as "no published PDF" -- during a
    # corpus sweep those acts are simply the era that predates the mirrors.
    for beteckning in targets:
        if not pdfmirror.has_facsimile(beteckning):
            sys.exit("sfs mirror-pdf: %s predates every published-PDF source -- "
                     "the printed series' mirror begins at %s and acts before it "
                     "exist only on paper"
                     % (beteckning, pdfmirror.RKRATTSDB_FIRST))
    pdfmirror.mirror(protocol.session(download),
                     targets or pdfmirror.corpus_beteckningar(sfs_list()),
                     force=protocol.RUN.force, dry_run=protocol.RUN.dry_run)


def sfs_ai_includegraphics(basefiles):
    """`lagen sfs ai-includegraphics <basefile> [...]` -- localize the graphics
    the consolidated text drops (formulas, maps, road signs) to a page + bbox in
    the *provenance-correct* published PDF, via a vision model, into a `.graphics`
    layer (WIKI_ROOT/ann). Detection already typed the gaps at parse (nf grafik
    nodes); this opt-in, per-id pass only places them -- the source PDF is picked
    deterministically (provenance_sfs), never by the model. One vision call per
    source PDF (chunked for a many-page source); a verified layer refuses
    regeneration sans --force, and per-entry `verified` flags survive a rerun.
    Any source PDF not mirrored yet is fetched first, so mirror-pdf need not
    have run. The LLM is never called from parse/relate/generate."""
    if not basefiles:
        sys.exit("usage: lagen sfs ai-includegraphics <basefile> [...]")
    for basefile in basefiles:
        _sfs_includegraphics_one(basefile)


def _sfs_roadsign_index(basefile, register):
    """``(index, sources_read)`` -- the published road-sign geometry for one act,
    and the acts it was read from.

    Mirrors every act whose PDF may reprint a row, then reads each row's page +
    rectangle off those PDFs. An *amending* act the publisher has no facsimile
    for is skipped: it only means one older reprint is unavailable, and any row
    it would have carried still resolves to an earlier printing. The base act's
    own PDF is not optional -- without it most rows have no printing at all, and
    the run would quietly write a layer that drops crops the site is serving
    (rule:fail-fast)."""
    sources = graphics.roadsign_sources(register, basefile)
    for beteckning in pdfmirror.mirror_on_demand(
            protocol.session(download),
            [s for s in sources if not compress.exists(layout.sfs_pdf(s))]):
        freshness.vlog("sfs ai-includegraphics %s: no published PDF for %s -- skipped"
             % (basefile, beteckning))
    read = [s for s in sources if compress.exists(layout.sfs_pdf(s))]
    if basefile not in read:
        raise ValueError("%s: the base act's own published PDF is not "
                         "available, so most road-sign rows have no printing "
                         "to crop" % basefile)
    freshness.vlog("sfs ai-includegraphics %s: reading road-sign rows from %d published "
         "PDF(s)" % (basefile, len(read)))
    return graphics.roadsign_index(
        [(s, layout.sfs_pdf(s)) for s in read], log=freshness.vlog), read


def _sfs_includegraphics_one(basefile):
    art = compress.read_json(layout.artifact("sfs", basefile))
    register = compress.read_json(sfs_source(basefile))
    gaps = graphics.collect_gaps(art["structure"])
    out = annstore.path("sfs", basefile, ".graphics")
    if not gaps:
        print("sfs ai-includegraphics %s: no graphic gaps" % basefile)
        return
    if basefile in graphics.ROADSIGN_DOCS:
        _sfs_roadsigns_one(basefile, art, register, gaps, out)
        return
    # keep verified+provenance-current entries; (re)localize the rest, grouped by
    # source PDF. A verified crop whose bilaga has since been amended (its `sfs`
    # no longer equals the resolved provenance) is re-localized, not kept stale.
    llm.start_record()   # one provenance window per layer (lib.annstore stamps meta.run)
    existing = json.loads(out.read_text()) if out.exists() else {}
    keep, todo = graphics.plan_localization(gaps, existing, register, basefile)
    # Before any vision spend, make sure every source PDF is on disk -- fetching
    # the few this act needs beats making the caller run mirror-pdf by hand and
    # is trivially cheap beside the vision call. One attempt each: if the
    # publisher still has nothing, that is an answer, not a flake to retry.
    missing = pdfmirror.mirror_on_demand(
        protocol.session(download),
        sorted(s for s in todo if not compress.exists(layout.sfs_pdf(s))))
    if missing:
        raise ValueError("%s: source PDF(s) unavailable: %s -- the publisher has "
                         "no facsimile for them, so their graphics cannot be "
                         "localized" % (basefile, ", ".join(missing)))
    todo_n = sum(len(g) for g in todo.values())
    if protocol.RUN.dry_run:
        print("sfs ai-includegraphics %s: %d gap(s); keep %d verified, localize "
              "%d from source PDF(s) %s -> %s"
              % (basefile, len(gaps), len(keep), todo_n, sorted(todo), out))
        return
    if not todo:
        print("sfs ai-includegraphics %s: all %d gap(s) localized and current "
              "(%d verified) -- nothing to do" % (basefile, len(gaps), len(keep)))
        return
    annstore.guard(out, protocol.RUN.force)     # a verified layer refuses, pre-LLM-spend
    freshness.vlog("sfs ai-includegraphics %s: localizing %d gap(s) across %d source PDF(s): %s"
         % (basefile, todo_n, len(todo), ", ".join(sorted(todo))))
    payload = dict(keep)
    for src, group in todo.items():
        payload.update(graphics.localize_group(
            group, layout.sfs_pdf(src), src, log=freshness.vlog))
    # a kept entry's source PDF was consulted too, so drift still tracks it
    _sfs_write_graphics(basefile, art, register, out, payload,
                        list(todo) + [e["sfs"] for e in keep.values()],
                        config.VISION_MODEL)
    print("sfs ai-includegraphics %s: localized %d gap(s) (kept %d verified), "
          "wrote %s" % (basefile, todo_n, len(keep), out))


def _sfs_write_graphics(basefile, art, register, out, payload, sources, model,
                        status=annstore.GENERATED):
    """Write one act's .graphics layer. Every source PDF the pass *read* is an
    input -- not just those a payload entry ended up naming -- so a re-mirrored
    or corrected facsimile shows up as drift even when it printed no row this
    time and would print one after the correction. `through` records how far the
    register had moved when the layer was authored.

    A source PDF that is not on disk is fatal, and says so: it is the file the
    crop endpoint would have to read, so a layer written without it points every
    one of those crops at nothing. On the vision path it can only be a *kept*
    entry's PDF -- the run mirrors the ones it localizes from -- which means an
    earlier sign-off is about to be recorded against a facsimile this host no
    longer has (rule:fail-fast)."""
    inputs = dict(annstore.artifact_input("sfs", basefile))
    for src in sorted(set(sources)):
        pdf = layout.sfs_pdf(src)
        if not compress.exists(pdf):
            raise ValueError(
                "%s: source PDF %s is not mirrored, so the layer cannot record "
                "it as an input -- re-run `lagen sfs mirror-pdf %s`"
                % (basefile, src, src))
        inputs.update(annstore.download_input(
            str(pdf.relative_to(layout.DOWNLOADED))))
    through = graphics.register_latest_amendment(register)
    meta = {"uri": art["uri"]}
    if through:
        meta["through"] = through
    annstore.write(out, payload, inputs, protocol.RUN.force, model=model,
                   meta_extra=meta, status=status)


def _sfs_roadsigns_one(basefile, art, register, gaps, out):
    """The road-sign path: no vision call, no register-note provenance.

    A road-sign statute prints no omission marker and carries no per-row change
    note -- the designator cell *is* the dropped sign, 326 of them in 2007:90 --
    so both questions are answered by the published PDFs themselves. Their text
    layer names each row by the same designator, which gives the crop rectangle
    (the ink between this row's caption and the next), and the act that prints
    a row last is the one whose graphic is in force."""
    # the whole act routes here, so every one of its gaps must be a road sign.
    # A marker gap appearing in one (a future `/Bilagan är inte med här/`) has
    # no designator to look up and belongs on the vision path -- fail rather
    # than attribute it to the base act by default (rule:fail-fast)
    codeless = [g["id"] for g in gaps if not g.get("code")]
    if codeless:
        raise ValueError("%s: gap(s) %s carry no road-sign designator -- a "
                         "road-sign act has gained a marker gap, which this "
                         "route cannot place" % (basefile, ", ".join(codeless)))
    index, sources = _sfs_roadsign_index(basefile, register)
    existing = json.loads(out.read_text()) if out.exists() else {}
    keep, todo = graphics.plan_localization(
        gaps, existing, register, basefile,
        provenance=lambda gap: index[gap["code"]]["sfs"]
        if gap["code"] in index else basefile)
    todo_gaps = [gap for group in todo.values() for gap in group]
    if protocol.RUN.dry_run:
        print("sfs ai-includegraphics %s: %d road-sign gap(s); keep %d "
              "verified, place %d from %d published PDF(s) -> %s"
              % (basefile, len(gaps), len(keep), len(todo_gaps),
                 len(todo), out))
        return
    if not todo:
        print("sfs ai-includegraphics %s: all %d road-sign gap(s) placed and "
              "current (%d verified) -- nothing to do"
              % (basefile, len(gaps), len(keep)))
        return
    annstore.guard(out, protocol.RUN.force)
    placed, unprinted = graphics.localize_roadsigns(todo_gaps, index)
    if not placed:
        raise ValueError("%s: not one of %d road-sign gap(s) is printed in any "
                         "of the %d published PDF(s) read -- writing the layer "
                         "would drop every crop the site is serving"
                         % (basefile, len(todo_gaps), len(sources)))
    if unprinted:
        print("sfs ai-includegraphics %s: no published PDF draws %s -- left as "
              "placeholder(s)" % (basefile, ", ".join(unprinted)))
    _sfs_write_graphics(basefile, art, register, out, dict(keep) | placed,
                        sources, "roadsign", status=annstore.DERIVED)
    print("sfs ai-includegraphics %s: placed %d road-sign gap(s) (kept %d "
          "verified), wrote %s" % (basefile, len(placed), len(keep), out))


def sfs_ai_hierarki(basefiles):
    """`lagen sfs ai-hierarki <lag-basefile> ...` -- run the regleringshierarki
    LLM passes (A subject span, D chain subject, B alignment/probe, C role;
    lib.aihierarki) over each lag's chain component, batched, and write the
    rows as `.ann` layers in the curated store -- what relate merges into the
    `regleringshierarki` table with source 'llm'. Seeded per lag because the
    component is the unit of work: the lag, its förordningar (stated and
    delegation-derived), their gällande föreskrifter and any EU rung. One-shot
    like the other ai-* commands: the LLM is never called from
    parse/relate/generate, and a verified layer refuses regeneration without
    --force."""
    con = catalog.connect(layout.CATALOG)
    if not basefiles and protocol.RUN.every:
        # every gällande lag whose component reaches a föreskrift, plus the
        # EU-pair tier (an EU rung above, nothing below) -- 524 of 1,768
        # lagar, measured 2026-08-29
        basefiles = aihierarki.candidate_lagar(con)
        print("%d candidate lagar (chains reaching a föreskrift, "
              "or an EU rung above)" % len(basefiles))
    if not basefiles:
        sys.exit("usage: lagen sfs ai-hierarki <lag-basefile> ... "
                 "(e.g. 2018:585), or --all for every lag whose chain "
                 "reaches a föreskrift -- one component per lag")
    llm.start_record()
    eligible = aihierarki.layer_sources(con)
    t0 = time.perf_counter()
    paid = 0            # components actually run (skips are instant)
    for i, bf in enumerate(basefiles, 1):
        docs, clauses = aihierarki.component(con, catalog.BASE + bf)
        if protocol.RUN.dry_run:
            print("would run %s: %d documents, %d pinned delegation clauses"
                  % (bf, len(docs), len(clauses)))
            continue
        # resume: a component whose documents all carry a hierarki layer is
        # done -- a restarted corpus run (this one runs for weeks) must never
        # re-pay finished components. --force re-runs them.
        if not protocol.RUN.force and all(d not in eligible
                                 or aihierarki.layer_path(eligible[d],
                                                          d).exists()
                                 for d in docs):
            util.status(i, len(basefiles),
                        "sfs ai-hierarki %s: layers present, skipped" % bf,
                        actual=paid or None)
            continue
        ncalls = [0]

        def progress(task, i=i, bf=bf, ncalls=ncalls, paid=paid):
            # the standard overwriting status line (util.status: \r + erase,
            # width-clipped, right-aligned ETA paced on run components): a
            # 70-document component takes hours before its completion line,
            # and a silent terminal reads as a hang
            ncalls[0] += 1
            util.status(i, len(basefiles),
                        "sfs ai-hierarki %s  call %d (task %s), %dk+%dk tok"
                        % (bf, ncalls[0], task,
                           llm.USAGE["prompt_tokens"] // 1000,
                           llm.USAGE["completion_tokens"] // 1000),
                        actual=paid + 1)

        rows, stats = aihierarki.run_component(con, docs, clauses,
                                               batched=True,
                                               progress=progress)
        util.progress_break()
        written = aihierarki.write_layers(con, rows, force=protocol.RUN.force,
                                          all_docs=docs)
        paid += 1
        # rough ETA: elapsed over the components actually run, extrapolated
        # to what remains -- component sizes vary widely, so read it as an
        # order of magnitude, not a promise
        remaining = (time.perf_counter() - t0) / paid * (len(basefiles) - i)
        tasks = ("a", "b1", "b2", "c", "d")
        print("(%d/%d) %s: %d rows over %d documents -> %d layers "
              "(%d calls, %d discarded, %d+%d tokens)  ETA %dh%02dm"
              % (i, len(basefiles), bf, len(rows), len(docs), written,
                 sum(stats[t + "_calls"] for t in tasks),
                 sum(stats[t + "_discarded"] for t in tasks),
                 llm.USAGE["prompt_tokens"], llm.USAGE["completion_tokens"],
                 remaining // 3600, remaining % 3600 // 60),
              flush=True)
    con.close()


def sfs_version_pages(sidecars):
    """The historical-consolidation ("lydelse") pages to render, one (uri,
    source, path, title) row per parsed version, read from the given
    versions-stage sidecars. They are not catalog rows -- versions carry no
    citations or search entries -- so generate appends them to the plan as
    extra pages."""
    rows = []
    for sc in sidecars:
        if not sc.exists():
            continue
        basefile = layout.sfs_sidecar_basefile(sc)
        for entry in json.loads(sc.read_text())["versions"]:
            version = entry["version"]
            rows.append((entry["uri"], "sfs",
                         str(layout.sfs_version_artifact(basefile, version)),
                         "SFS %s i lydelse enligt SFS %s" % (basefile, version)))
    return rows


def sfs_extra_pages(only):
    """The sfs lydelse rows generate adds to its plan. `only` (a set of artifact
    path strings) restricts them to the named statutes, whose sidecars sit next
    to their artifacts; None asks for the whole source."""
    if only is not None:
        return sfs_version_pages([Path(p).with_suffix(".versions.json")
                                  for p in only
                                  if Path(p).is_relative_to(layout.SFS_ARTIFACT)])
    return sfs_version_pages(
        sorted(layout.SFS_ARTIFACT.glob("*/*.versions.json")))


def sfs_relate_cross(con):
    """SFS's part of relate's cross-document block: summarize each proposition's
    Omfattning magnitude across the laws it amends, and load the authored
    old->new paragraf correspondences (the `.corr` layers) into the catalog."""
    omfattning_rows = resolve_omfattning(con)
    corr = [row for path in annstore.tree("sfs").glob("*/*.corr")
            for row in correspond.corr_rows(json.loads(path.read_text()))]
    catalog.set_correspondence(con, corr)
    return ({"propositions' Omfattning magnitude summarized across the laws "
             "they amend": omfattning_rows,
             "old->new paragraf correspondences loaded from .corr layers":
                 len(corr)}, [])


def sfs_layers():
    """The side files SFS pages and cross-passes read: the `.corr`
    correspondence layers, the ai-hierarki/ai-correspond `.ann` layers and the
    versions-stage sidecars (a new historical consolidation re-renders the
    statute's version panel)."""
    return (sorted(annstore.tree("sfs").glob("*/*.corr"))
            + sorted(annstore.tree("sfs").rglob("*.ann"))
            + sorted(layout.SFS_ARTIFACT.glob("*/*.versions.json")))


SOURCES: tuple[Source, ...] = (Source("sfs", sfs_list, {
    # download has no input files (the input is the remote DB) and its output
    # is valid regardless of the fetcher's version, so inputs/code stay empty:
    # an act on disk is "fresh" until --force re-fetches it.
    "download": Stage("download", sfs_download_run, sfs_source),
    "parse": Stage("parse", sfs_parse_run,
                   functools.partial(layout.artifact, "sfs"),
                   inputs=sfs_inputs, code=SFS_CODE),
    # historical consolidations: parse the download archive (superseded
    # versions, incl. two decades of legacy HTML snapshots) into per-version
    # artifacts + a sidecar index, feeding the lydelse pages and the diff view
    "versions": Stage("versions", sfs_versions_run, sfs_versions_sidecar,
                      inputs=sfs_versions_inputs, code=SFS_VERSIONS_CODE),
}, harvest=sfs_harvest, origin=origin(download.ENDPOINT),
   render=render.render,
   intermediate=(sfs_intermediate, "plain text"),
   artifacts=functools.partial(layout.artifacts, "sfs"),
   extra_pages=sfs_extra_pages, relate_cross=sfs_relate_cross,
   cross_code=(HERE / "register.py", HERE / "correspond.py"), layers=sfs_layers,
   # ai-correspond, table-correspond and history-as-git are added here by
   # build.py: each reads a proposition, which is förarbete's job
   actions={"ai-hierarki": sfs_ai_hierarki,
            "renumber-correspond": sfs_renumber_correspond,
            "mirror-pdf": sfs_mirror_pdf,
            "ai-includegraphics": sfs_ai_includegraphics},
   notes="ai-correspond <new-sfs> <prop> [<old-sfs>]: LLM-derive the old->new "
         "paragraf correspondence map into a .corr layer (WIKI_ROOT/ann)\n"
         "ai-hierarki <lag> [...]: LLM-author the regleringshierarki rows for "
         "the lag's whole chain component (förordningar, föreskrifter, EU "
         "rung) into .ann layers; relate merges them as source 'llm'. "
         "--all = every lag whose chain reaches a föreskrift\n"
         "table-correspond <new-sfs> <prop> [<old-sfs>[=TAG] ...]: the same "
         ".corr layer read mechanically from the prop's jämförelsetabell/"
         "paragrafnyckel tables; several old laws merge into one layer, =TAG "
         "names an old law's prop-local shorthand in a multi-law register\n"
         "renumber-correspond <sfs> [...]: same-law renumbering map from the "
         "register's 'nuvarande … betecknas …' omfattning clauses\n"
         "history-as-git <repodir> [basefile ...]: build/update a git repo of "
         "the SFS collection, one commit per amendment event; --rebuild-history "
         "rewrites it from the current complete corpus\n"
         "mirror-pdf [<sfs> ...]: mirror the officially published SFS PDFs "
         "(graphics/maps the consolidated text drops) into downloaded/sfs/pdf/; "
         "named act(s) mirror just those, no args = whole corpus (and runs as "
         "part of `download`). 2018:160 and later come from "
         "svenskforfattningssamling.se, 1998:306-2018:159 from rkrattsdb.gov.se, "
         "and a named act before 1998:306 is an error -- it exists only in "
         "print. --force re-fetches existing and re-asks about acts an upstream "
         "once said it had no PDF for\n"
         "ai-includegraphics <basefile> [...]: vision-localize the dropped "
         "graphics to page+bbox in the provenance-correct published PDF into a "
         ".graphics layer (mirroring any source PDF it still needs); per-entry "
         "verified flags survive reruns, --force overrides a verified layer"),)
