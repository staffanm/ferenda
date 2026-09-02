"""The föreskrift source's registration: the agency författningssamlingar
(FFFS, …), one per-fs subtree with a PDF body.

One shared harvest engine drives every agency from the data registry in
`agencies.py`, and one shared parser reads every fs (rule:sources-are-programs
-- variation as data). Documents arrive only through the bulk harvest, so
there is no per-document download stage: parse runs over whatever is on disk.
"""

import functools
import json
import sys
from pathlib import Path

from ..lib import annstore, compress, layout, text, util
from ..lib import stage as protocol
from ..lib.errors import SkipDocument
from ..lib.pdftext import pdf_intermediate
from ..lib.stage import (
    CITATION_DATA,
    Source,
    Stage,
    patch_input,
    scoped_harvest,
    write_artifact,
)
from . import download, harvest, parse, render
from .agencies import REGISTRY as FORESKRIFT_AGENCIES

HERE = Path(__file__).parent


def foreskrift_list():
    """Every harvested base regulation as 'fs/year:num' (the artifact subdir
    excluded by the single-level glob)."""
    return sorted(compress.read_json(p)["basefile"]
                  for p in compress.glob(layout.FORESKRIFT_DOWNLOADED, "*/*.json")
                  if not p.name.startswith("."))


def foreskrift_harvest(scopes):
    """Bulk harvest of the agency författningssamlingar (scopes = the registry's
    scope names -- the fs code for a samling one agency owns, 'fffs', and
    'hslffs-<publisher>' for the six sites that publish into HSLF-FS; empty =
    all *non-browser* scopes). The browser-shielded ones (skvfs, mtfs) are
    excluded from the default sweep -- they need the slow, serial DetachedChrome
    transport, so they run on their own schedule via `lagen foreskrift
    browser-download`. Naming one explicitly still harvests it. `--force`
    re-walks and refreshes existing base regulations; `--only fs/year:num` (one
    scope) fetches a single one."""
    if not scopes:
        skipped = download.browser_scopes()
        scopes = download.default_scopes()
        if skipped:
            print("foreskrift download: skipping %d browser-shielded scope%s (%s) "
                  "-- run `lagen foreskrift browser-download` on its own schedule"
                  % (len(skipped), "" if len(skipped) == 1 else "s",
                     ", ".join(skipped)))
    # report=False: sync prints each agency's own summary as it finishes and,
    # with jobs>1, fans the agencies out across a thread pool (each hits a
    # different host)
    return scoped_harvest("foreskrift", download,
                          str(layout.FORESKRIFT_DOWNLOADED), scopes,
                          noun="författningssamling",
                          example="lagen foreskrift download fffs "
                                  "--only fffs/2013:10",
                          label="every non-browser scope", report=False,
                          jobs=protocol.RUN.jobs)


def foreskrift_browser_download(_basefiles):
    """`lagen foreskrift browser-download`: harvest only the browser-shielded
    scopes (skvfs, mtfs), which need the slow headful-Chrome transport and are
    kept off the default parallel sweep. Run sequentially (they share the
    process-global DISPLAY and Playwright's single-thread sync API), on its own,
    less frequent schedule."""
    scopes = download.browser_scopes()
    if protocol.RUN.dry_run:
        print("foreskrift browser-download: would download %s into %s"
              % (", ".join(scopes), layout.FORESKRIFT_DOWNLOADED))
        return
    util.harvest_start("foreskrift browser-download",
                       "the headful-Chrome agency sites (%s)" % ", ".join(scopes))
    download.sync(str(layout.FORESKRIFT_DOWNLOADED), scopes=scopes,
                             full=protocol.RUN.force, only=protocol.RUN.only, jobs=1)


def foreskrift_reap(basefiles):
    """`lagen foreskrift reap`: remove harvested records that a later run re-filed
    under another författningssamling.

    An fs reassignment (an agency taking over a renamed agency's samling, so its
    listing is read with ``fs_from_designation``) leaves the pre-reassignment run's
    records behind under the old fs, claiming the same landing pages as the
    correctly-filed ones. They parse and publish like any other document, so the
    same rule shows up twice in a rail -- as "MSBFS 2026:8" beside "MCFFS 2026:8",
    an identifier from a samling that stopped issuing when MSB was renamed at the
    end of 2025.

    Removal is per document and takes the whole chain -- record, bodies, artifact,
    generated page -- because a record left behind re-parses and a page left behind
    keeps serving. Nothing here is recoverable only by deletion: a mistakenly
    reaped document comes back on the next `download --force`. Use `--dry-run` to
    list without removing.

    The scan is always over the whole store, never a scope: a leftover is only
    recognisable *beside* the correctly-filed record that superseded it, and the
    two are by definition in different författningssamlingar, so a scoped walk
    would silently find nothing."""
    if basefiles:
        sys.exit("foreskrift reap takes no scopes -- a leftover is only "
                 "recognisable beside the record in the other samling that "
                 "superseded it, so the scan is always store-wide")
    stale = download.superseded(str(layout.FORESKRIFT_DOWNLOADED))
    if not stale:
        print("foreskrift reap: no superseded records")
        return
    for basefile, (winner, url) in sorted(stale.items()):
        print("foreskrift reap: %s superseded by %s (%s)" % (basefile, winner, url))
        if protocol.RUN.dry_run:
            continue
        for path in download.superseded_files(
                str(layout.FORESKRIFT_DOWNLOADED), basefile):
            compress.unlink(path)
        compress.unlink(layout.artifact("foreskrift", basefile))
        compress.unlink(layout.foreskrift_grund_artifact(basefile))
        compress.unlink(layout.GENERATED / layout.page_relpath(
            "https://lagen.nu/" + basefile))
    print("foreskrift reap: %s %d superseded record(s) -- re-run relate to drop "
          "them from the catalog" % ("would remove" if protocol.RUN.dry_run else "removed",
                                     len(stale)))


# the parser is one shared engine over every fs (its own model/structure plus the
# shared PDF extraction + citation engine), so a change to any of these re-stales
# every föreskrift the recipe-version way -- just like SFS/eurlex parse.
FORESKRIFT_CODE = (HERE / "parse.py",
                   HERE / "model.py",
                   HERE / "structure.py",
                   HERE.parent / "lib" / "pdftext.py", HERE.parent / "lib" / "lagrum.py",
                   HERE.parent / "lib" / "emdref.py", *CITATION_DATA,
                   # the ordförklaringar table is reconstructed from the page
                   # geometry, and the footnotes under a page's rule are scanned
                   # into artifact nodes
                   HERE.parent / "lib" / "tabell.py", HERE.parent / "lib" / "artifact.py",
                   # picks the presented consolidation, which decides whether
                   # the parse run emits a .grund.json sidecar
                   HERE.parent / "lib" / "text.py",
                   # definition detection (term runs) and the concept-URI mint
                   HERE.parent / "lib" / "begrepp.py", HERE.parent / "lib" / "markdown.py")


def foreskrift_record(basefile):
    """The harvested record JSON (``<fs>/<slug>.json``) for one base regulation."""
    fs = basefile.split("/", 1)[0]
    return harvest.record_path(
        layout.FORESKRIFT_DOWNLOADED, fs, basefile)


def foreskrift_inputs(basefile):
    """The record JSON plus every body PDF it references (the regulation and any
    konsoliderad versions); re-downloading any of them re-stales the parse."""
    rec = foreskrift_record(basefile)
    paths = [rec]
    if compress.exists(rec):
        record = compress.read_json(rec)
        fsdir = layout.FORESKRIFT_DOWNLOADED / record["fs"]
        files = record.get("files", {})
        reg = files.get("regulation")
        if reg:
            paths.append(parse.body_path(
                str(layout.FORESKRIFT_DOWNLOADED), record["fs"], reg))
        paths += [fsdir / c["name"] for c in files.get("consolidation", [])
                  if c.get("name")]
    return paths + patch_input("foreskrift", basefile)


def foreskrift_parse_run(basefile):
    """One harvested record -> its JSON artifact: the body structure, the masthead
    metadata, and the bemyndigande/genomför citation edges the model carries.
    When the artifact presents a konsoliderad version *and* the as-enacted base
    text is parsed, the base is re-projected as a ``.grund.json`` sidecar -- the
    uncatalogued ``/grund`` page generate appends to its plan (the föreskrift
    counterpart of the sfs lydelse artifacts); a re-parse that no longer
    presents one removes the sidecar."""
    record = compress.read_json(foreskrift_record(basefile))
    reg = parse.parse_record(record, str(layout.FORESKRIFT_DOWNLOADED))
    art = reg.to_artifact()
    write_artifact("foreskrift", basefile, art)
    sidecar = layout.foreskrift_grund_artifact(basefile)
    if text.presented_consolidation(art) and art.get("structure"):
        grund = dict(art, uri=art["uri"] + "/grund", version="grund",
                     consolidations=[])
        compress.write_text(sidecar,
                            json.dumps(grund, ensure_ascii=False, indent=2,
                                       sort_keys=True),
                            encodings=compress.ARTIFACT_ENCODINGS)
    else:
        # a re-parse that stopped presenting a consolidation retires the whole
        # /grund projection: the sidecar AND the generated page, which would
        # otherwise keep serving as an unrefreshable orphan (generate only
        # plans pages whose sidecar exists). The page's manifest entry may
        # linger, but it is inert without the sidecar, and generate's
        # existence check re-renders the page if the sidecar reappears.
        compress.unlink(sidecar)
        compress.unlink(layout.GENERATED / layout.page_relpath(art["uri"] + "/grund"))


def foreskrift_extra_pages(only):
    """The föreskrift /grund rows generate adds to its plan: the as-enacted base
    text beside a presented consolidation, a page per `.grund.json` sidecar."""
    if only is not None:
        return [row for row in layout.foreskrift_grund_pages()
                if row[2].replace(".grund.json", ".json") in only]
    return layout.foreskrift_grund_pages()


def foreskrift_intermediate(basefile):
    """A föreskrift's base-regulation PDF as pdftohtml XML (konsoliderade versions
    are separate documents, not patched through this key)."""
    record = compress.read_json(foreskrift_record(basefile))
    reg_file = (record.get("files") or {}).get("regulation")
    if not reg_file:
        raise SkipDocument("%s: no base-regulation PDF" % basefile)
    return pdf_intermediate(parse.body_path(layout.FORESKRIFT_DOWNLOADED,
                                            basefile.split("/", 1)[0], reg_file))


SOURCES: tuple[Source, ...] = (Source("foreskrift", foreskrift_list, {
    "parse": Stage("parse", foreskrift_parse_run,
                   functools.partial(layout.artifact, "foreskrift"),
                   inputs=foreskrift_inputs, code=FORESKRIFT_CODE),
},
    render=render.render,
    intermediate=(foreskrift_intermediate, "pdftohtml XML"),
    artifacts=functools.partial(layout.artifacts, "foreskrift"),
    extra_pages=foreskrift_extra_pages,
    # the ai-hierarki layers (regleringshierarki rows on the rail)
    layers=lambda: sorted(annstore.tree("foreskrift").rglob("*.ann")),
    harvest=foreskrift_harvest,
    actions={"browser-download": foreskrift_browser_download,
             "reap": foreskrift_reap},
    # display label only, nothing is ever fetched from a central index: the
    # harvest engine drives each agency's own site from foreskrift/agencies.py
    origin="the %d agency sites in foreskrift/agencies.py"
           % len(FORESKRIFT_AGENCIES),
    scopes=frozenset(FORESKRIFT_AGENCIES),
    notes="download flag: --only fs/year:num (fetch one; needs one scope)\n"
          "scopes are författningssamling codes (fffs, …), plus the six sites "
          "that publish into the shared HSLF-FS samling (hslffs-sos, "
          "hslffs-fohm, hslffs-ivo, hslffs-lv, hslffs-mfof, hslffs-tlv); "
          "empty = all non-browser scopes\n"
          "browser-download: harvest just the headful-Chrome scopes (skvfs, "
          "mtfs), kept off the default sweep for a separate schedule\n"
          "reap: remove records an fs reassignment left behind under the old "
          "författningssamling (--dry-run lists them)"),)
