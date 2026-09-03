"""The förarbete source's registration: preparatory works -- propositioner,
SOU, Ds, utskottsbetänkanden, riksdagsskrivelser and the rest.

Most doctypes come from regeringen.se; `bet` and `rskr` come from
data.riksdagen.se through the shared dokumentlista engine. Several one-time
repair verbs sit beside the harvest, each fetching a body population the
original import never stored.
"""

import functools
import sys
from pathlib import Path

from ..lib import aireport, annstore, compress, layout, llm
from ..lib import stage as protocol
from ..lib.datasets import COE_NAMES, TREATY_NAMES
from ..lib.errors import SkipDocument
from ..lib.pdftext import pdf_intermediate
from ..lib.stage import (
    CASENUMBER_CODE,
    CITATION_DATA,
    POLITENESS,
    Source,
    Stage,
    origin,
    patch_input,
    require_single_scope,
    sum_scope_totals,
    write_artifact,
)
from ..lib.util import harvest_start
from . import (
    aigenomforande,
    download,
    fk,
    genomforande,
    kommentar,
    parse,
    propkb,
    propriksdagen,
    render,
    riksdagen,
    rskr,
    soukb,
)

HERE = Path(__file__).parent

# legacy_formats.py is in FA_CODE because the frozen-import html route reads it at
# parse time (a text/tml body -> paragraphs), so editing it re-stales those docs.
# legacy.py (the import verb) is NOT: it only produces records, which are parse's
# per-doc inputs and already versioned via fa_record's inputs hash.
# every module the parse output actually depends on -- including the shared PDF
# machinery. `lib/pdftext.py` decides the printed page every block carries, and
# `volumes.py` decides which PDFs are read at all, so an edit to either changes
# the artifacts; leaving them out let a page-numbering fix ship without
# re-parsing anything (foreskrift/avg/remisser/coe/icc already list pdftext).
FA_CODE = (HERE / "parse.py", HERE / "model.py",
           HERE / "structure.py", HERE / "kommentar.py",
           HERE / "fk.py", HERE / "volumes.py",
           HERE / "lydelse.py", HERE.parent / "lib" / "tabell.py",
           HERE / "legacy_formats.py",
           HERE / "kbtitles.py",
           HERE.parent / "lib" / "pdftext.py", HERE.parent / "lib" / "lagrum.py",
           HERE.parent / "lib" / "emdref.py", *CITATION_DATA, *CASENUMBER_CODE,
           # the data the citation engine's treaty matching is configured by:
           # a new Swedish treaty name re-stales the parse like a grammar edit
           HERE.parent / "lib" / "treaty_ids.py",
           TREATY_NAMES,
           COE_NAMES)


def fa_record(basefile):
    return layout.fa_record(basefile)


def fa_parse_inputs(basefile):
    """Freshness inputs of the förarbete parse stage: the downloaded record and
    the re-OCR sidecar slot (§7g). The sidecar is listed even while absent, so
    dropping a modern-OCR'd PDF there (which `_legacy_body` then parses instead of
    the frozen scan) re-stales exactly that document's parse."""
    return ([fa_record(basefile), layout.fa_ocr_pdf(*basefile.split("/", 1))]
            + patch_input("forarbete", basefile))


def fa_list():
    """Every harvested record as 'type/slug', read from the year-segmented
    download tree (`<type>/<year>/<slug>.json`); the type is the grandparent dir.
    Per-type dotfiles (`.watermark.json`, `.complete`) sit a level up at
    `<type>/`, so the three-level glob never reaches them."""
    return sorted("%s/%s" % (p.parent.parent.name, p.stem)
                  for p in compress.glob(layout.FA_DOWNLOADED, "*/*/*.json")
                  if not p.name.startswith("."))


def fa_harvest(scopes):
    """Bulk harvest of preparatory works. Most doctypes come from regeringen.se
    (the old download_new); `bet` (utskottsbetänkanden) and `rskr`
    (riksdagsskrivelser) come from data.riksdagen.se via the shared
    dokumentlista engine. `scopes` narrows to the named doctypes
    (prop/sou/ds/bet/rskr/...); empty = all. `--only BASEFILE` (with exactly
    one regeringen scope) fetches just that one document, walking the listing
    until it is found (regeringen types only -- bet/rskr have no --only).
    `--riksmote YYYY/YY` (with exactly the bet or rskr scope) narrows that
    harvest to one riksmöte -- a dev/manual slice that never advances the
    watermark."""
    require_single_scope("forarbete", scopes, "doctype",
                          "lagen forarbete download prop --only 2025/26:28")
    riksdagen_syncs = {"bet": riksdagen.sync, "rskr": rskr.sync}
    rd_scopes = [s for s in (scopes or riksdagen_syncs) if s in riksdagen_syncs]
    reg_scopes = [s for s in scopes if s not in riksdagen_syncs]
    do_reg = bool(reg_scopes) or not scopes   # empty scopes = all regeringen types
    if protocol.RUN.only and rd_scopes:
        sys.exit("forarbete --only is not supported for bet/rskr "
                 "(data.riksdagen.se); use a full or incremental download")
    if protocol.RUN.riksmote and (do_reg or len(rd_scopes) != 1):
        sys.exit("forarbete --riksmote needs exactly the bet or rskr scope, "
                 "e.g. `lagen forarbete download bet --riksmote 2025/26`")
    if protocol.RUN.dry_run:
        print("forarbete download: would download %s into %s"
              % (protocol.RUN.only or ", ".join(scopes) or "all types",
                 layout.FA_DOWNLOADED))
        return
    # both drivers report {typ: (seen, new)}, so one run's total spans them
    totals = {}
    if do_reg:
        # sync prints each type's own "forarbete <typ>: Starting at ..." banner and
        # closing summary, so every regeringen subtype reads as one block
        totals.update(download.sync(str(layout.FA_DOWNLOADED),
                                       types=reg_scopes or None,
                                       full=protocol.RUN.force, only=protocol.RUN.only))
    for typ in rd_scopes:
        harvest_start("forarbete %s" % typ, riksdagen.API)
        seen, new = riksdagen_syncs[typ](str(layout.FA_DOWNLOADED), full=protocol.RUN.force,
                                         riksmote=protocol.RUN.riksmote)
        print("forarbete %s: %d seen, %d new" % (typ, seen, new))
        totals[typ] = (seen, new)
    return sum_scope_totals(totals)


def fa_parse_run(basefile):
    record = compress.read_json(fa_record(basefile))
    art = parse.to_artifact(parse.parse_record(record, layout.FA_DOWNLOADED))
    # a proposition's författningskommentar states which EU directive article a
    # provision transposes -- attach those genomför relations as a typed section
    # so relate emits the implements edges and the page renders them (§7d).
    implements = kommentar.extract(art)
    if implements:
        art["implements"] = implements
    # the FK's per-paragraf commentary text, the interpretive aid rendered in
    # the statute paragraf's rail. lagtext is dropped: it quotes the statute
    # the SFS vertical already holds, and the artifact records extracted
    # semantics, not duplicated body text. mark=True stamps the commentary
    # blocks in the structure (`fk: <entry-no>` -- the renderer starts a new
    # highlight box when the number changes) so the prop page highlights them.
    kommentarer = [{k: v for k, v in e.items() if k != "lagtext"}
                   for e in fk.extract(art, mark=True)]
    if kommentarer:
        art["kommentarer"] = kommentarer
    # the regeringen.se landing page the downloader recorded -- not derivable by
    # rule, so it travels with the record into the artifact's source_url
    write_artifact("forarbete", basefile, art, source_url=record.get("url"))


def fa_refetch_bodies(args):
    """`lagen forarbete refetch-bodies [<type> ...]` -- second-chance body
    fetch for body-less live-harvest records (default lr + so, the two types
    whose original harvest left large body gaps; see finding 04). Re-reads
    each stored landing's content links and fetches them again; a recovered
    body updates the record, re-staling its parse. `--limit N` caps the run."""
    types = tuple(args) or ("lr", "so")
    unknown = [t for t in types if t not in download.TYPES]
    if unknown:
        sys.exit("unknown förarbete type(s): %s" % ", ".join(unknown))
    if protocol.RUN.dry_run:
        print("forarbete refetch-bodies: would refetch %s bodies under %s"
              % ("/".join(types), layout.FA_DOWNLOADED))
        return
    checked, recovered, errors = download.refetch_bodies(
        layout.FA_DOWNLOADED, types=types, limit=protocol.RUN.limit, delay=POLITENESS)
    print("forarbete refetch-bodies: %d body-less checked, %d recovered, "
          "%d errors" % (checked, recovered, errors))


def fa_propkb_scans(args):
    """`lagen forarbete propkb-scans` -- one-time bulk fetch of the KB
    two-chamber proposition scans (1867-1970), the page images behind the
    facsimile view. Adds no documents: the ABBYY OCR text of every propkb prop is
    already downloaded (see forarbete/propkb.py). ~79 GB over ~17k documents, so
    it is its own verb, never part of `harvest`. Writes only PDFs beside the
    records -- no record is touched, so it re-stales no parse. Resumable: a scan
    already on disk is skipped, so a killed run just gets rerun. `--limit N` caps
    the fetch (a test slice)."""
    if args:
        sys.exit("usage: lagen forarbete propkb-scans")
    if protocol.RUN.dry_run:
        print("forarbete propkb-scans: would fetch the missing KB scans into %s"
              % (layout.FA_DOWNLOADED / "prop"))
        return
    seen, fetched = propkb.sync(layout.FA_DOWNLOADED, limit=protocol.RUN.limit)
    print("forarbete propkb-scans: %d seen, %d fetched" % (seen, fetched))


def fa_prop_riksdagen_bodies(args):
    """`lagen forarbete prop-riksdagen-bodies` -- one-time repair of the 1 756
    propositions that carry `files: []` and a data.riksdagen.se url. That url is
    riksdagen's body endpoint; the legacy import took the sibling
    `dokumentstatus` XML (a metadata envelope, not a body) and so wrote no body
    file. Fetches the OCR'd HTML and points each record at it under the existing
    `skanning2007` body_format -- no new parser (see forarbete/propriksdagen.py).

    Its own verb, never part of `harvest`: the records exist, so no listing walk
    can reach them. Resumable -- a record that gained a body drops out of the
    work list, so a killed run is just rerun. `--limit N` caps the fetch."""
    if args:
        sys.exit("usage: lagen forarbete prop-riksdagen-bodies")
    if protocol.RUN.dry_run:
        print("forarbete prop-riksdagen-bodies: would fetch %d missing bodies"
              % len(propriksdagen.pending(layout.FA_DOWNLOADED)))
        return
    seen, fetched, empty = propriksdagen.sync(
        layout.FA_DOWNLOADED, limit=protocol.RUN.limit, delay=POLITENESS)
    print("forarbete prop-riksdagen-bodies: %d seen, %d fetched, %d served empty"
          % (seen, fetched, empty))


def fa_soukb_scans(args):
    """`lagen forarbete soukb-scans` -- one-time bulk re-download of the
    KB-digitised SOUs (1922-1999), the scanned OCR'd PDFs that *are* the body (no
    XML sibling, unlike propkb-scans). Walks `https://sou.kb.se/` as the source of
    truth, forgetting the legacy soukb records, and writes a fresh record per
    basefile pointing at its PDF(s). Hundreds of GB over ~5,800 documents, so it is
    its own verb, never part of `harvest`. Resumable: a PDF already on disk is
    skipped, so a killed run just gets rerun. `--limit N` caps the fetch (a test
    slice)."""
    if args:
        sys.exit("usage: lagen forarbete soukb-scans")
    if protocol.RUN.dry_run:
        print("forarbete soukb-scans: would re-download the KB SOU bodies into %s"
              % (layout.FA_DOWNLOADED / "sou"))
        return
    seen, fetched = soukb.sync(layout.FA_DOWNLOADED, limit=protocol.RUN.limit,
                                  delay=POLITENESS)
    print("forarbete soukb-scans: %d seen, %d fetched" % (seen, fetched))


def fa_refetch_landings(args):
    """`lagen forarbete refetch-landings [landings|word]` -- re-fetch documents'
    regeringen.se landing pages so the volume rule has their link texts.

    Two populations need it, and they need different things:

    * `landings` (default) -- the 1 260 legacy `dsregeringen` records, which
      kept their body files but not the page they came from. Only the landing
      is fetched: their bodies are already on disk, and re-downloading identical
      bytes would move their mtimes and discard their conversion cache.
    * `word` -- the records whose body is still a Word file (438 propositions).
      regeringen.se serves those as PDF today, and the PDF carries the font
      signal the parser needs: prop. 2006/07:128 read from `.doc` produced no
      författningskommentar at all, and from PDF produces 29. Here the linked
      documents are downloaded and become the record's files.

    A Word-bodied record whose `url` is data.riksdagen.se has no regeringen.se
    landing to fetch and is left for the listing walk (`download <typ> --only`).
    """
    which = args[0] if args else "landings"
    if which not in ("landings", "word"):
        sys.exit("usage: lagen forarbete refetch-landings [landings|word]")
    word = which == "word"
    select = ((lambda r: download.word_bodied(r) and download.has_regeringen_url(r))
              if word else
              (lambda r: r.get("source") and download.has_regeringen_url(r)))
    if protocol.RUN.dry_run:
        n = sum(1 for typ in ("prop", "ds", "sou")
                for p in compress.glob(layout.FA_DOWNLOADED / typ, "*/*.json")
                if not p.name.startswith(".")
                and select(compress.read_json(p)))
        print("forarbete refetch-landings %s: up to %d landing page(s) to "
              "fetch%s (already-stored ones are passed over by the real run)"
              % (which, n, " and their documents" if word else ""))
        return
    checked, updated, errors = download.refetch_landings(
        layout.FA_DOWNLOADED, select, replace_bodies=word,
        limit=protocol.RUN.limit, delay=POLITENESS, force=protocol.RUN.force)
    print("forarbete refetch-landings %s: %d checked, %d updated, %d errors"
          % (which, checked, updated, errors))


def fa_ai_genomforande(basefiles):
    """`lagen forarbete ai-genomforande <prop-basefile> [<CELEX> ...]` -- LLM-author
    the directive->paragraf transposition map for the EU directive(s) a proposition
    transposes, out of its författningskommentar, and write it as a `.ann` layer in
    the curated store (lib.annstore). A richer superset of the artifact's mechanical
    `implements` that `genomforande.resolve` prefers at relate time. With no CELEX,
    the directives are detected from the prop's own `implements` (every directive it
    mechanically names). One-shot per prop, like eurlex ai-annotate; the LLM is never
    called from parse/relate/generate, and a verified layer refuses regeneration
    without --force."""
    if not basefiles:
        sys.exit("usage: lagen forarbete ai-genomforande <prop-basefile> "
                 "[<CELEX> ...]  (e.g. prop/2025-26-28 32022L2555; the CELEX "
                 "defaults to the directives the prop's implements names)")
    prop, celexes = basefiles[0], basefiles[1:]
    llm.start_record()   # one provenance window per layer (lib.annstore stamps meta.run)
    prop_art = compress.read_json(layout.artifact("forarbete", prop))
    if not celexes:
        celexes = aigenomforande.detect_directives(prop_art)
    # a directive whose eurlex artifact is absent cannot be validated against;
    # drop it with a warning rather than fail the whole prop (a repealed directive
    # not held in the corpus -- rule:errors-drive-retry-use-raise applies to bad
    # program state, not to a legitimately-missing optional target)
    present = [c for c in celexes if compress.exists(layout.artifact("eurlex", c))]
    for c in celexes:
        if c not in present:
            print("forarbete ai-genomforande %s: skipping directive %s "
                  "(no parsed eurlex artifact)" % (prop, c), file=sys.stderr)
    if not present:
        sys.exit("forarbete ai-genomforande %s: no directive to map "
                 "(none named/detected, or none parsed in eurlex)" % prop)
    out = annstore.path("forarbete", prop, ".ann")
    with aireport.Report("forarbete", "ai-genomforande", 1) as report:
        if protocol.RUN.dry_run:
            report.plan(prop, "map %s onto it -> %s" % (",".join(present), out))
            return report
        if report.verified(prop, out):       # pre-LLM-spend; write guards again
            return report

        def progress(i, n, label):
            # the shared counter, redrawn per batch: one is minutes on a local
            # endpoint, and a silent terminal reads as a hang
            report.item(prop, "batch %d/%d %s" % (i + 1, n, label))

        report.item(prop)
        payload, stats = aigenomforande.annotate(prop_art, present, progress)
        annstore.write(out, payload,
                       {**annstore.artifact_input("forarbete", prop),
                        **{k: v for c in present
                           for k, v in annstore.artifact_input("eurlex", c).items()}},
                       protocol.RUN.force)
        report.wrote(prop, out, note="<- %s: %d edges over %d paragrafer, %d batch, "
                     "%d+%d tokens, %d direktivartiklar täckta"
                     % (",".join(present), stats["edges"], stats["mapped_paragrafer"],
                        stats["batches"], llm.USAGE["prompt_tokens"],
                        llm.USAGE["completion_tokens"], stats["articles_covered"]))
    return report


def fa_relate_cross(con):
    """Förarbete's part of relate's cross-document block: pin each
    genomför-direktiv statement to the SFS paragraf it transposes, and each
    författningskommentar entry to the paragraf it comments on. Both need the
    whole catalog, so they run there rather than per document."""
    return ({"genomför-direktiv relations pinned to SFS paragrafs":
                 genomforande.resolve(con,
                                         genomforande.genomforande_layers()),
             "författningskommentar entries pinned to SFS paragrafs":
                 fk.resolve(con)}, [])


def fa_intermediate(basefile):
    """A förarbete's live-harvest body PDF as pdftohtml XML (the same first PDF
    parse reads). Frozen legacy-import bodies carry non-XML formats and are not
    patched at source level."""
    record = compress.read_json(layout.fa_record(basefile))
    if "legacy_files" in record:
        raise ValueError("%s: frozen legacy-import body is not text-patchable "
                         "at source level" % basefile)
    pdfs = [f for f in record.get("files", []) if f.lower().endswith(".pdf")]
    if not pdfs:
        raise SkipDocument("%s: no body PDF" % basefile)
    return pdf_intermediate(layout.fa_dir(layout.FA_DOWNLOADED, record["type"],
                                          record["basefile"]) / pdfs[0])


SOURCES: tuple[Source, ...] = (Source("forarbete", fa_list, {
    "parse": Stage("parse", fa_parse_run,
                   functools.partial(layout.artifact, "forarbete"),
                   inputs=fa_parse_inputs, code=FA_CODE),
}, harvest=fa_harvest, origin=origin(download.BASE), self_banner=True,
   render=render.render,
   intermediate=(fa_intermediate, "pdftohtml XML"),
   artifacts=functools.partial(layout.artifacts, "forarbete"),
   relate_cross=fa_relate_cross,
   cross_code=(HERE / "genomforande.py", HERE / "fk.py"),
   # the genomförande/fk .ann layers the cross-passes pin
   layers=lambda: sorted(annstore.tree("forarbete").rglob("*.ann")),
   scopes=frozenset(download.TYPES) | {"bet", "rskr"},
   actions={"propkb-scans": fa_propkb_scans,
            "prop-riksdagen-bodies": fa_prop_riksdagen_bodies,
            "soukb-scans": fa_soukb_scans,
            "refetch-bodies": fa_refetch_bodies,
            "refetch-landings": fa_refetch_landings,
            "ai-genomforande": fa_ai_genomforande},
   notes="refetch-landings [landings|word]: re-fetch regeringen.se landing pages\n"
         "  so the volume rule has their link texts; `word` also replaces a\n"
         "  Word body with the PDFs regeringen.se serves today\n"
         "ai-genomforande <prop-basefile> [<CELEX> ...]: LLM-author the "
         "directive->paragraf transposition map from the prop's "
         "författningskommentar (a .ann layer relate prefers over the mechanical "
         "implements); the CELEX(es) default to the directives the prop names\n"
         "download flag: --only BASEFILE (fetch one document; needs one "
         "regeringen scope)\n"
         "download flag: --riksmote YYYY/YY (narrow the bet or rskr download "
         "to one riksmöte; needs that single scope, never advances the "
         "watermark)\n"
         "propkb-scans: one-time ~79 GB fetch of the KB proposition page-image "
         "scans for the facsimile view (--limit N caps it; adds no documents, "
         "re-stales no parse)\n"
         "prop-riksdagen-bodies: one-time fetch of the 1756 proposition bodies "
         "riksdagen serves but the legacy import never stored\n"
         "soukb-scans: one-time hundreds-of-GB re-download of the KB SOU bodies "
         "(1922-1999) from sou.kb.se as the source of truth (--limit N caps it; "
         "the scanned PDF is the body)"),)
