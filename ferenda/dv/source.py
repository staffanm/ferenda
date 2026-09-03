"""The dv source's registration: the courts' published decisions (domar,
referat and notiser).

Identity is the whole problem here -- one decision reaches us as an API
record, a legacy frozen file, or both, and a referat absorbs the verdicts it
reports on -- so the identity index decides what a basefile is and a
full-source parse reconciles the artifact tree to it afterwards.
"""

import functools
import json
import time
from pathlib import Path
from urllib.parse import quote

import requests

from ..lib import casenaming, compress, freshness, layout, util
from ..lib import stage as protocol
from ..lib.datasets import CASENUMBERS, NAMEDCASES
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
    write_artifact,
)
from . import casenumbers, download, identity, legacy, namedcases, paths, render
from .parse import (
    api_member,
    parse_api_record,
    parse_pdf_record,
    record_intermediate,
    to_artifact,
)

HERE = Path(__file__).parent

DOM_DOWNLOADED = layout.DOM_DOWNLOADED            # dv api records (primary)
DV_LEGACY_DOWNLOADED = layout.DV_LEGACY_DOWNLOADED  # legacy raw feed
DV_INDEX = layout.DOM_INDEX
# identity.py is here because parse *reads* it (grupp_map resolves curated
# hanvisningar), like casenaming. DV_INDEX itself is deliberately NOT a parse
# input: it churns on every harvest and would re-stale all ~17k artifacts each
# time, so a reindex that newly resolves a grupp reaches existing artifacts
# only at the next code-staleness or --force pass.
DV_CODE = (HERE / "parse.py", HERE / "model.py",
           HERE / "structure.py", HERE / "identity.py",
           HERE / "legacy.py", HERE.parent / "lib" / "poi.py",
           HERE.parent / "lib" / "poi_worker.py", HERE.parent / "lib" / "pdftext.py",
           HERE.parent / "lib" / "casenaming.py", HERE.parent / "lib" / "lagrum.py",
           HERE.parent / "lib" / "emdref.py", *CITATION_DATA, *CASENUMBER_CODE,
           # the innehåll is normalised through this before a patch is applied
           # to it, so it decides what a patched document parses from
           HERE.parent / "lib" / "markup.py")


@functools.cache
def _dv_grupps():
    """gruppKorrelationsnummer -> canonical case id, for resolving curated
    related-case references whose fritext the citation grammar cannot read.
    Over the whole index (not just API-backed cases): a grupp can name any
    published case."""
    return identity.grupp_map(json.loads(DV_INDEX.read_text()))


def _dv_after_parse():
    """What a full-source dv parse runs once its documents are written: the
    artifact tree is reconciled to the canonical set, pruning verdicts folded
    into a referat (R2), then the case-number snapshot is refreshed from what
    remains."""
    dv_reconcile_artifacts()
    _dv_casenumbers_after_parse()


def dv_reconcile_artifacts():
    """Reconcile the dv *artifact* tree to the current canonical set: delete the
    derived `.json` artifact of any case that is no longer canonical. Two things
    make a standalone artifact stale -- a pre-referat verdict folded into its NJA
    referat (R2), and a record now filtered out (prövningstillstånd / excluded
    typ). Their old `dom/{slug}/{malnr}/{date}.json` would otherwise be re-globbed
    by `relate` and re-catalogued.

    This removes only the derived artifact JSON. The downloaded record and its PDF
    attachment are left untouched -- the folded verdict's PDF stays in the corpus,
    so the referat's "Ursprunglig dom" link keeps working. Full-source parse only
    (the whole canonical set must be present to know what is stale). Returns the
    count removed."""
    valid = {layout.artifact("dv", bf) for bf in paths.cases()}
    removed = 0
    for path in layout.artifacts("dv"):     # logical .json paths, .br-resolved
        if path not in valid:
            compress.unlink(path)           # the artifact JSON only, never the PDF
            removed += 1
    if removed:
        # relate drops the catalog rows for the removed artifacts; the stale
        # generated HTML is cleared on the next generate of the affected pages
        print("dv parse: reconciled %d superseded artifact(s)" % removed, flush=True)
    return removed


def dv_original_verdicts(basefile):
    """The raw verdict(s) a referat case absorbed (R2): for each folded-in
    no-referat member with a PDF attachment, its målnummer + a `/api/v1/dv-verdict`
    download url the referat page links as "Ursprunglig dom". Empty when the case
    published straight to a referat (nothing was folded in)."""
    out = []
    for m in paths.cases()[basefile]["members"]:
        if m["store"] != "domstol" or m.get("referat") or not m.get("bilagor"):
            continue
        member_path = util.load_relpath(layout.DATA, m["path"])
        assert member_path is not None, "dv member %r has no path" % m
        record = compress.read_json(member_path)
        pdf = next((Path(b["filnamn"]).name for b in record.get("bilagaLista") or []
                    if (b.get("filnamn") or "").lower().endswith(".pdf")), None)
        if pdf:
            out.append({
                "malnummer": [x.strip() for x in record.get("malNummerLista", [])],
                "avgorandedatum": record.get("avgorandedatum"),
                "url": "/api/v1/dv-verdict?court=%s&id=%s&file=%s"
                       % (quote(m["court"], safe=""), quote(m["uuid"], safe=""),
                          quote(pdf, safe=""))})
    return out


def dv_download_run(basefile):
    """Re-fetch one named case's API record (by the uuid the identity index
    already holds) and its attachments. New-case *discovery* is dv_harvest
    (bare `lagen dv download`) + identity reindex -- a case has no uuid to
    fetch until the harvest has seen it, so it can't enter through here."""
    member = api_member(paths.cases()[basefile])
    assert member, ("%s is a legacy-only case: its frozen original is already "
                    "on disk and nothing upstream serves it" % basefile)
    record = download.fetch_record(protocol.session(download), member["uuid"])
    out = paths.record(basefile)
    util.write_atomic(out, json.dumps(
        record, ensure_ascii=False, indent=2).encode())
    download.download_bilagor(protocol.session(download), out.parent.parent, record,
                                 POLITENESS)
    time.sleep(POLITENESS)


def dv_harvest(scopes):
    """Bulk discovery harvest of the courts' publication API -- the only way to
    find cases not yet on disk (paginates the whole corpus). Incremental by
    default; `--force` walks it all oldest-first. Throttled, self-logging.

    Rebuilds the identity index afterwards so new cases are immediately
    visible to parse. The rebuild is a single whole-corpus pass (the index is
    a global union-find, not incrementally updatable) and needs no parsing
    (keys come from raw record fields + legacy filenames), so it runs once at
    the end rather than per page."""
    if protocol.RUN.dry_run:
        print("dv download: would download into %s, then rebuild %s"
              % (DOM_DOWNLOADED, DV_INDEX))
        print("dv download: would refresh named-rättsfall snapshot %s"
              % NAMEDCASES)
        return
    seen, changed = download.sync(DOM_DOWNLOADED, full=protocol.RUN.force)
    print("dv download: %d seen, %d changed" % (seen, changed))
    if changed or not DV_INDEX.exists():
        dv_reindex()
    else:
        print("dv download: no new records, identity index left as is")
    # also refresh the named-rättsfall snapshot: HD updates that list on its own
    # cadence (independent of which cases we just downloaded), so a harvest is the
    # natural moment to re-pull it. A fetch failure lands in the ledger as its
    # own failed segment (the sibling harvest catches' pattern) and is then
    # re-raised: `_run_harvest` turns it into dv's failed download segment and
    # the run exits nonzero, so a snapshot that quietly stopped refreshing is
    # visible instead of a green run over a stale committed file.
    t0 = time.perf_counter()
    try:
        dv_namedcases()
    except requests.exceptions.RequestException:
        freshness._emit_segment("namedcases", "dv", time.perf_counter() - t0,
                      status="errors", errors=1)
        raise
    freshness._emit_segment("namedcases", "dv", time.perf_counter() - t0, status="ok")
    # the case harvest's own numbers, not the two sidecar refreshes' -- those
    # report as their own segments
    return seen, changed


def dv_reindex(args=()):
    """Rebuild the identity index from the records already on disk -- one
    whole-corpus union-find pass, no network and no parsing. Runs automatically
    after a harvest that changed anything, and on demand as `lagen dv reindex`
    (e.g. after revising the entity-resolution rules)."""
    if protocol.RUN.dry_run:
        print("dv reindex: would rebuild %s from %s + %s"
              % (DV_INDEX, DOM_DOWNLOADED, DV_LEGACY_DOWNLOADED))
        return
    _cases, summary, warnings = identity.reindex(
        dvdir=str(DV_LEGACY_DOWNLOADED), domstoldir=str(DOM_DOWNLOADED),
        out=str(DV_INDEX))
    print("dv reindex: %s" % summary)
    for warning in warnings:
        print("  !! %s" % warning)
    paths.cases.cache_clear()
    _dv_grupps.cache_clear()


def dv_namedcases(args=()):
    """Refresh the named-rättsfall snapshot (`lagen dv namedcases`): download
    HD's official list of named precedents and rewrite dv/data/namedcases.json,
    which the ⌘K resolver reads to turn a nickname ("Instagrambilden") into the
    published case URI. Independent of the per-document download/parse chain --
    it's a single small curated dataset, not corpus artifacts."""
    if protocol.RUN.dry_run:
        print("dv namedcases: would download %s -> %s"
              % (namedcases.URL, NAMEDCASES))
        return
    cases = namedcases.harvest()
    resolvable = sum(1 for c in cases if c["uri"])
    print("dv namedcases: %d named cases (%d resolvable) -> %s"
          % (len(cases), resolvable, NAMEDCASES))


def dv_casenumbers(args=()):
    """Refresh the case-number snapshot (`lagen dv casenumbers`): sweep the dv
    artifacts' målnummer and rewrite artifact/dom/casenumbers.json, which the citation
    engine reads to resolve "Högsta domstolens dom 2009-11-03 T 3-08" onto the
    referat it became. Reads artifacts already on disk -- no network, no
    per-document chain. Run it after a parse run that added decisions, or their
    case numbers link nothing."""
    if protocol.RUN.dry_run:
        print("dv casenumbers: would sweep dv artifacts -> %s" % CASENUMBERS)
        return False            # nothing was written, so nothing re-stales
    numbers, courts, refused, changed = casenumbers.write()
    print("dv casenumbers: %d case numbers across %d courts -> %s%s"
          % (numbers, courts, CASENUMBERS,
             "" if changed else " (unchanged)"))
    if refused:
        print("dv casenumbers: %d printed values are not a readable case "
              "number, left out: %s" % (len(refused), ", ".join(
                  sorted(set(refused))[:5]) + (" ..." if len(refused) > 5 else "")))
    if changed:
        # the snapshot is a parse input (CASENUMBER_CODE), so new content
        # re-stales every parse that resolves a case number -- said out loud,
        # because that is hours of reparsing an operator did not ask for by name
        print("dv casenumbers: snapshot changed -- the parse of dv, forarbete, "
              "avg, rs, lawreview and wiki is now stale and re-runs on the "
              "next `lagen <source> parse`")
    return changed


def _dv_casenumbers_after_parse():
    """Refresh the case-number snapshot at the end of a full-source dv parse.

    The snapshot is a view of the whole parsed dv tree, so it belongs to the
    parse that produced the tree rather than to the harvest (`dv namedcases`
    downloads HD's list and rides the harvest instead). It runs after
    `dv_reconcile_artifacts`, so a case number does not survive in it on the
    strength of an artifact that pass just deleted. ~3 s over 23,739 artifacts.

    Full-source parse only. A one-document run leaves the snapshot as it is: it
    is rebuilt from the whole tree either way, and rewriting it there would
    re-stale five sources' parses on the strength of one document."""
    t0 = time.perf_counter()
    changed = dv_casenumbers()
    freshness._emit_segment("casenumbers", "dv", time.perf_counter() - t0,
                  ran=int(changed), status="ok")


def dv_parse_run(basefile):
    member = paths.member(basefile)
    if member["store"] == "dv":   # legacy-only: frozen Word referat / notis XML
        av = legacy.parse_legacy_file(paths.record(basefile),
                                         paths.cases()[basefile])
        art = to_artifact(av, canonical_id=basefile, grupp_uris=_dv_grupps())
        art["label"] = casenaming.case_label(art)
        write_artifact("dv", basefile, art)
        return
    record = compress.read_json(paths.record(basefile))
    # a not-yet-published HD/HFD verdict has no innehåll HTML -- only the court's
    # own PDF attachment; parse its body from that instead (R2)
    pdf = None if record.get("innehall") else paths.verdict_pdf(basefile, record)
    av = (parse_pdf_record(record, pdf, basefile) if pdf
          else parse_api_record(record, basefile))
    # the case's public publication-search page is keyed by the record's
    # gruppKorrelationsnummer (the publication group), not derivable from basefile
    grupp = record.get("gruppKorrelationsnummer")
    art = to_artifact(av, canonical_id=basefile, grupp_uris=_dv_grupps())
    # stamp the canonical, name-prefixed display title onto the artifact here, so
    # the pure catalog reads it off the artifact without recomputing (the naming
    # grammar itself lives in lib.casenaming, read identically by page + catalog)
    art["label"] = casenaming.case_label(art)
    if pdf:
        # the raw verdict's own PDF, data_root-relative, so the /api/v1/facsimile
        # resolver can rasterize its pages for the inline page-facsimile buttons
        art["facsimile_pdf"] = str(util.store_relpath(pdf, layout.DATA))
    if av.referat:
        art["ursprunglig_dom"] = dv_original_verdicts(basefile)
    write_artifact("dv", basefile, art,
                   source_url=layout.dv_source_url(grupp) if grupp else None)


def dv_intermediate(basefile):
    """DV's intermediate is whichever source its parse reads, and dv has three:
    the whole API record for a case the domstol API publishes -- body *and*
    metadata, so a redaction reaches the målnummer as well as the running text
    (`dv.parse.record_intermediate`); the court's own PDF, as pdftohtml XML, for
    a verdict published before its referat (no innehåll at all); and the frozen
    notis XML for a legacy-only case. A legacy Word referat is read through POI
    and has no editable text form to diff against, the same as avg's two Word
    documents."""
    path = paths.record(basefile)
    if path.suffix.lower() == ".xml":
        return path.read_text()
    if path.suffix.lower() != ".json":
        raise SkipDocument("%s: a legacy Word referat (%s) has no patchable "
                           "intermediate" % (basefile, path.name))
    record = compress.read_json(path)
    if not record.get("innehall"):
        # a verdict published before its referat has no innehåll at all -- parse
        # reads its body from the court's own PDF, so that PDF's pdftohtml XML
        # is what a patch targets (the PDF-bodied sources' intermediate). Some
        # ~290 cases have neither, which parse tolerates (they are metadata-only
        # entries): there is nothing to diff against.
        pdf = paths.verdict_pdf(basefile, record)
        if pdf is None:
            raise SkipDocument("%s: the record carries neither innehåll nor a "
                               "verdict PDF" % basefile)
        return pdf_intermediate(pdf)
    return record_intermediate(record)


SOURCES: tuple[Source, ...] = (Source("dv", lambda: sorted(paths.cases()), {
    "download": Stage("download", dv_download_run, paths.record),
    "parse": Stage("parse", dv_parse_run,
                   functools.partial(layout.artifact, "dv"),
                   inputs=lambda bf: [paths.record(bf)] + patch_input("dv", bf),
                   code=DV_CODE),
}, harvest=dv_harvest, origin=origin(download.API),
   render=render.render,
   intermediate=(dv_intermediate,
                 "API record JSON (opublicerad dom: pdftohtml XML; "
                 "legacy notisfall: intermediate XML)"),
   artifacts=functools.partial(layout.artifacts, "dv"),
   after={"parse": (_dv_after_parse,)},
   actions={"reindex": dv_reindex, "namedcases": dv_namedcases,
            "casenumbers": dv_casenumbers}),)
