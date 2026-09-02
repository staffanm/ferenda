"""The remisser source's registration: regeringen.se remiss ärenden and the
referral responses they collect.

The source is never rendered as pages of its own; the parsed answers feed the
sole LLM pass, ai-analyze, whose `.ann` sidecars a later render pass surfaces
on the referred förarbete's rail. Answers arrive only through the bulk harvest
-- there is no per-document download stage (the avg/föreskrift rule).
"""

import functools
import sys
from pathlib import Path

from ..lib import annstore, compress, layout
from ..lib import stage as protocol
from ..lib.pdftext import pdf_intermediate
from ..lib.stage import Source, Stage, record_inputs, write_artifact
from . import ai_analyze, download, model, parse

HERE = Path(__file__).parent

REMISSER_CODE = (HERE / "parse.py", HERE / "model.py",
                 HERE.parent / "lib" / "pdftext.py")


def remisser_list():
    """Every downloaded remiss-answer basefile ("<typ>/<document id>/<org-slug>",
    e.g. "sou/2026:14/kammarkollegiet"), one per `Remissinstans` marked
    downloaded -- the parse stage's targets. Not every `Remiss.svar` entry: an
    instance not yet fetched has no PDF to parse. Case records live one directory
    deep (``<typ>/<id-slug>.json``), which keeps the examined-index out."""
    out = []
    for path in sorted(compress.glob(layout.REMISSER_DOWNLOADED, "*/*.json")):
        remiss = model.Remiss.from_dict(compress.read_json(path))
        out.extend("%s/%s" % (remiss.basefile, model.org_slug(inst.source_url))
                   for inst in remiss.svar if inst.downloaded)
    return out


def remisser_record(basefile):
    return layout.remisser_arende(basefile.rsplit("/", 1)[0])


def remisser_pdf(basefile):
    arende_basefile, org_slug = basefile.rsplit("/", 1)
    return layout.remisser_answer(arende_basefile, org_slug)


remisser_inputs = record_inputs("remisser", remisser_record, remisser_pdf)


def remisser_parse_run(basefile):
    write_artifact("remisser", basefile,
                   parse.parse(
                       basefile, layout.REMISSER_DOWNLOADED).to_dict())


def remisser_harvest(scopes):
    """Bulk harvest: discover new remiss ärenden, re-poll every still-open one for
    newly-arrived answers, and fetch any answer PDF not yet cached. No sub-scopes
    (unlike avg's organs / forarbete's doctypes) -- one homogeneous listing.
    `--only <url>` fetches exactly one ärende by its regeringen.se URL, bypassing
    the listing walk entirely (the archive runs to thousands of pages, so this
    is the escape hatch for "just this one ärende")."""
    if protocol.RUN.dry_run:
        print("remisser download: would download into %s"
              % layout.REMISSER_DOWNLOADED)
        return
    if protocol.RUN.only:
        result = download.sync_one(protocol.RUN.only)
        print("remisser %s: %d svar, %d fetched%s"
              % (result["basefile"], result["svar"], result["fetched"],
                 " (externt dokument -- answers not harvested)"
                 if result["externt"] else ""))
        return result["svar"], result["fetched"]
    summary = download.sync(full=protocol.RUN.force)
    print("remisser: %d new, %d repolled, %d fetched, %d failed; %d still open, "
          "%d externt (skipped)"
          % (summary["new"], summary["repolled"], summary["fetched"],
             summary["failed"], summary["open"], summary["externt"]))
    # a remiss ärende is the document here; `fetched` counts the answer files
    # pulled for those ärenden, which is the work the run did
    return summary["new"] + summary["repolled"], summary["fetched"]


def remisser_ai_analyze(basefiles):
    """`lagen remisser ai-analyze <basefile> ...` -- the sole LLM pass: map one
    remissvar onto the sections of the SOU/Ds it discusses (sentiment + verbatim
    quote per section, plus an overall stance), written as a `.ann` sidecar. A
    basefile is either one answer (`"<typ>/<document id>/<org-slug>"`) or a whole
    ärende (`"<typ>/<document id>"`), which expands to all its answers; the LLM
    is never called from parse/relate/generate.

    `--update` selects every analysed ärende whose remissperiod is still open,
    and `--matching <prefix>` every ärende whose basefile starts with the prefix
    (e.g. "sou/"), most-recently-updated first, instead of naming basefiles.
    The selection and the analysis themselves are `ai_analyze.select` and
    `ai_analyze.analyze_all`; this reads the flags and reports the outcome."""
    if protocol.RUN.update and protocol.RUN.matching:
        sys.exit("--update and --matching both select the ärenden themselves; "
                 "use one")
    if protocol.RUN.update:
        if basefiles:
            sys.exit("--update selects the ärenden itself (every analysed one "
                     "still open); don't also name basefiles")
        basefiles = ai_analyze.updatable()
        print("remisser ai-analyze --update: %d analysed ärende(n) still open"
              % len(basefiles))
    elif protocol.RUN.matching:
        if basefiles:
            sys.exit("--matching selects the ärenden itself; don't also name "
                     "basefiles")
        basefiles = ai_analyze.matching(protocol.RUN.matching)
        print("remisser ai-analyze --matching %s: %d ärende(n), most recent first"
              % (protocol.RUN.matching, len(basefiles)))
    elif not basefiles:
        sys.exit("usage: lagen remisser ai-analyze <basefile> [<basefile> ...]\n"
                 "       lagen remisser ai-analyze --update\n"
                 "       lagen remisser ai-analyze --matching <prefix>")
    if not basefiles:
        return
    targets = ai_analyze.select(basefiles, force=protocol.RUN.force,
                                dry_run=protocol.RUN.dry_run)
    failed = ai_analyze.analyze_all(targets, force=protocol.RUN.force,
                                    dry_run=protocol.RUN.dry_run)
    if failed:
        # sampling is stochastic (llm_temperature 1.0), so re-running genuinely
        # retries rather than reproducing: the usual failure is the model
        # paraphrasing where it was told to quote, and it quotes correctly next
        # time often enough to be worth another pass
        print("remisser ai-analyze: %d of %d failed, no layer written -- re-run "
              "to retry just these: %s" % (len(failed), len(targets),
                                           " ".join(failed)))
        sys.exit(1)

# No per-document download stage (the avg/foreskrift rule): answers arrive only
# through the bulk `remisser_harvest` sweep, so parse runs over whatever is on
# disk; relate/index/dump/generate never touch this source (it publishes nothing).
def remisser_intermediate(basefile):
    """A remissvar's answer PDF as pdftohtml XML."""
    case, org = basefile.split("/", 1)
    return pdf_intermediate(layout.remisser_answer(case, org))


SOURCES: tuple[Source, ...] = (Source("remisser", remisser_list, {
    "parse": Stage("parse", remisser_parse_run,
                   functools.partial(layout.artifact, "remisser"),
                   inputs=remisser_inputs, code=REMISSER_CODE),
},
    harvest=remisser_harvest,
    intermediate=(remisser_intermediate, "pdftohtml XML"),
    # never related: the answers and their ai-analyze .ann layers render onto
    # the referred förarbete's page, so generate's gate must see them here
    layers=lambda: (list(layout.artifacts("remisser"))
                    + sorted(annstore.tree("remisser").rglob("*.ann"))),
    origin="https://www.regeringen.se/remisser/",
    actions={"ai-analyze": remisser_ai_analyze},
    notes="download flag: --only <regeringen.se ärende url> (fetch one ärende + its "
          "answer PDFs, bypassing the listing walk entirely)\n"
          "download sweeps the whole /remisser/ listing (new ärenden, watermarked "
          "so a normal run doesn't re-walk the whole archive) then re-polls every "
          "still-open case for newly-arrived answers; --force ignores the "
          "watermark and re-walks everything\n"
          "only ärenden remitting a document regeringen itself published (SOU, Ds, "
          "departementspromemoria -- one with a /rattsliga-dokument/ landing "
          "page) are harvested; an ärende remitting an agency report, an external "
          "skrivelse or an EU proposal is recorded but its answers are never "
          "fetched\n"
          "ai-analyze --update: re-analyze every ärende already analysed whose "
          "remissperiod (deadline + grace, as for download) has not closed -- "
          "answers arrive throughout the period, so an analysis made early is "
          "missing whatever came after it; already-analysed answers are skipped, "
          "so it costs the LLM only for the new ones. Never part of a rebuild\n"
          "ai-analyze <basefile>: LLM-map one answer onto the referred SOU/Ds's "
          "sections (sentiment + quote per section), written as a .ann sidecar; "
          "<basefile> is one answer (sou/2026-21/domstolsverket) or a whole "
          "ärende (sou/2026-21), which analyzes every answer still lacking a "
          "layer\n"
          "ai-analyze --matching <prefix>: analyze every ärende whose basefile "
          "starts with <prefix> (e.g. sou/), most-recently-updated first, "
          "instead of naming basefiles\n"
          "this source is never related/generated -- it feeds the referred "
          "förarbete's rail, not its own pages"),)
