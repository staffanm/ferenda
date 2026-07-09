"""One-time import of the frozen förarbete corpora into the forarbete vertical
(REWRITE.md §7g).

Several förarbete upstreams are dead or historic, so the old downloaders are not
ported -- the corpus is complete and a one-time import materializes it into the
forarbete record layout, after which the ordinary `parse` stage and the whole
derived layer treat each doc like a harvested one (no network). This module holds
the shared precedence machinery plus every förarbete frozen corpus:

  * `propriksdagen` (7,922 props 1971-2017, data.riksdagen.se dokumentstatus) --
    the only *downloaded/*-driven walker (its docs are located by tree shape, not
    by an entry file); routes body by probed htmlformat.
  * the regeringen-era gap-fills `souregeringen`/`dsregeringen`/`dirregeringen`
    (landing HTML + slug-named PDFs) -- one shared entries-driven walker, PDFs
    ordered main-first by the landing page's content links.
  * `soukb` (5,807 SOU scans 1922-1999, KB; 371 GB pointed at in place) and
    `dirasp` (dir PDFs 2006-2019) -- one probed `index.pdf` per doc.
  * `propkb` (19k props 1867-1970, KB) -- ABBYY OCR-XML `index.xml` (the abbyy
    parse route, page-anchored) xor a scan-only `index.pdf`.
  * the TRIPS family `proptrips` (props 1993/94-2022/23) / `dirtrips` (dir
    1987-2016) / `dirasp` (dir 2006-2019, from the retired TRIPS/rkrattsdb site)
    -- a probed `index.pdf` else, for proptrips/dirtrips, the `div.body-text`
    plaintext HTML (the trips parse route). `.doc`/`.docx`/`.wpd` bodies have no
    parse route, so they are not listed (a metadata-only record; a future
    POI/soffice pass can revisit).

The regeringen-era and KB corpora read their authoritative post-sanitization
`basefile` from the entry JSON (never re-derived from the tree path) and locate
the body by the entry's own location in `downloaded/`. The **TRIPS family is the
exception**: its old sanitizer left ~half the entry JSONs null-basefile, so those
three are walked *downloaded-first* with the basefile read from the path (the
`rm`/`year`+`nr` path encodes the identity reliably and agrees with
propriksdagen's XML-minted basefile by construction), the sibling entry supplying
only the `orig_url` provenance -- otherwise ~90% of proptrips would be dropped.
All corpora share one `_write_if_better` precedence core + `_preskip` fast path;
they differ in walk shape and body picking.

**Point at the bytes, don't copy them** (§7g): an import record carries no local
body file. It references the frozen bytes in place through `legacy_files` -- paths
relative to `config.LEGACY_ROOT`, resolved at parse time -- so the 410 GB soukb
tree (a later corpus) is never duplicated. Each record also carries a
`"source": "<corpus>"` tag the precedence rule reads.

**Precedence** (`should_write`): a record written by the live regeringen.se
harvester has no `source` key and always wins -- an import never overwrites it.
Between two frozen corpora a better body-format tier wins (a pdf/doc body beats an
html-only copy, which beats no body); an equal tier is broken by a static source
rank. This encodes the old composite's `get_preferred_instances` behaviour, whose
real rule was "anyone with a PDF beats an html-only copy" -- the format tier is
what later lets proptrips' PDFs beat propriksdagen's html-only records in the
1995/96-2000/01 upstream-PDF-gap window.
"""

import json
import re
import subprocess
from pathlib import Path

from ..lib import compress, legacy_import
from ..lib.util import (
    record_path,
    sniff_extension,
    split_numalpha,
)
from . import download, legacy_formats

# this module's corpus; also its key in the precedence rank
SOURCE = "propriksdagen"

# a frozen record's body-format tier: any of these extensions among its
# legacy_files is a tier-2 (real document) body; an html-only body is tier 1; no
# body is tier 0. The tier is what handles the PDF-gap window -- a later corpus's
# pdf copy outranks propriksdagen's html-only record regardless of source rank.
# Only .pdf is listed: no walker ever emits a .doc/.docx/.wpd/.rtf legacy_files
# entry (those bodies have no parse route, so they are left out of legacy_files
# entirely -- see _pick_proptrips), so a wider set here would be speculative
# (rule:no-speculative-code).
BODY_FORMATS = frozenset({".pdf"})

# static source rank breaking an equal-tier tie (lower number wins). Ranks are
# only ever compared *within* one basefile's type (a `record_path` collision), so
# the numbers restart per type family: prop propriksdagen>proptrips>propkb, sou
# souregeringen>soukb, dir dirregeringen>dirasp>dirtrips (the plan order in §7g).
# An unknown source on either side is a programming error -> assert.
SOURCE_RANK = {
    "propriksdagen": 1, "proptrips": 2, "propkb": 3,      # prop family
    "souregeringen": 1, "soukb": 2,                       # sou family
    "dirregeringen": 1, "dirasp": 2, "dirtrips": 3,       # dir family
}

# each frozen corpus -> the förarbete type it feeds (the record subdir + URI
# segment). SOURCE_RANK ties only compare corpora sharing a type, so the type is
# also what keeps the sou/dir/prop rank families from ever meeting.
CORPUS_TYPE = {
    "propriksdagen": "prop", "proptrips": "prop", "propkb": "prop",
    "souregeringen": "sou", "soukb": "sou", "dsregeringen": "ds",
    "dirregeringen": "dir", "dirasp": "dir", "dirtrips": "dir",
}

# the printed identifier prefix per type ("SOU 2020:1", "Ds 1998:69",
# "Dir. 1994:111", "Prop. 1867:23"). These agree with what the live regeringen.se
# harvester records (download.TYPES id regexes) so an imported and a harvested
# identifier for the same act match by construction.
IDENT_PREFIX = {"prop": "Prop.", "sou": "SOU", "ds": "Ds", "dir": "Dir."}

# corpora whose entry `orig_url` still resolves, so it flows to the record's `url`
# (the rendered Källa link -> artifact source_url): regeringen.se landing pages,
# KB's urn.kb.se resolver and weburn.kb.se direct URLs (both host live, spot-
# checked). The retired TRIPS-family corpora (proptrips/dirtrips/dirasp) point at
# dead IPs (193.188.157.x) -- their orig_url is kept as provenance only, never as
# a source_url, since a Källa link to a dead host misleads readers.
LIVE_SOURCE_URL = frozenset({"souregeringen", "dsregeringen", "dirregeringen",
                             "soukb", "propkb"})

# rm-directory name shape: '1971', '1975-76', '1999-2000'. Excludes the two junk
# dirs (`2006-prop-2006-07`, `2017-htgen.nu-prop-2017-18`) -- leftover manual test
# dirs holding duplicate basefiles.
RE_RM_DIR = re.compile(r"\d{4}(?:-\d{2}|-2000)?")


# --------------------------------------------------------------------------
# precedence primitives (shared by every frozen corpus)
# --------------------------------------------------------------------------

def body_tier(legacy_files):
    """The body-format tier of a frozen record's `legacy_files`: 2 for a
    pdf/doc/docx/wpd/rtf body, 1 for an html-only body, 0 for no body."""
    exts = {Path(f).suffix.lower() for f in legacy_files}
    if exts & BODY_FORMATS:
        return 2
    return 1 if exts else 0


def _beats(existing, candidate):
    """Whether `candidate` beats a *different* frozen corpus's `existing` record:
    a higher body-format tier wins, an equal tier broken by the static source
    rank (lower number wins)."""
    assert existing["source"] in SOURCE_RANK, \
        "unknown frozen corpus %r on disk" % existing["source"]
    assert candidate["source"] in SOURCE_RANK, \
        "unknown frozen corpus %r" % candidate["source"]
    et, ct = body_tier(existing["legacy_files"]), body_tier(candidate["legacy_files"])
    if ct != et:
        return ct > et
    return SOURCE_RANK[candidate["source"]] < SOURCE_RANK[existing["source"]]


def should_write(existing, candidate, force=False):
    """Whether a frozen-import `candidate` should be written at its basefile,
    given the record already on disk (`existing`, or None): the shared §7g
    precedence core (`lib.legacy_import.should_write`: new slot -> write, live
    harvest always wins, own prior import only under `force`) with förarbete's
    tie-break between two different frozen corpora (`_beats`: a higher
    body-format tier wins, an equal tier broken by the static source rank)."""
    return legacy_import.should_write(existing, candidate["source"], force,
                                      better=lambda ex: _beats(ex, candidate))


# --------------------------------------------------------------------------
# shared import core (walk / decide / write), used by every corpus below;
# the corpus-independent primitives (rel/read_record/iter_entries/docdir and
# the precedence core) live in lib.legacy_import
# --------------------------------------------------------------------------

def _preskip(existing, source, force, counts):
    """The cheap pre-checks that let a walker skip *building a body at all* (the
    text-layer probe is the expensive part, and matters for the 371 GB soukb
    tree): a live-harvest record (no `source`) always wins, and the corpus's own
    prior import is kept on a plain re-run. Returns True (and tallies the skip)
    when the candidate is a no-op; `force` still rebuilds the corpus's own
    record, so it is not pre-skipped."""
    if existing is None:
        return False
    if "source" not in existing:
        counts["skipped_live"] += 1                       # live harvest always wins
        return True
    if existing["source"] == source and not force:
        counts["skipped_existing"] += 1                   # own record kept on re-run
        return True
    return False


def _write_if_better(recpath, existing, candidate, counts, force):
    """Apply the precedence rule to a fully-built `candidate`: write it (tallying
    `imported`) when it beats what is on disk, else tally the skip. `_preskip`
    normally fires the live/own cases first, but this still handles every case so
    it is correct on its own."""
    if not should_write(existing, candidate, force):
        assert existing is not None       # should_write(None, ...) is always True
        if "source" not in existing:
            counts["skipped_live"] += 1
        elif existing["source"] == candidate["source"]:
            counts["skipped_existing"] += 1
        else:
            counts["skipped_better"] += 1
        return False
    compress.write_download(recpath, json.dumps(candidate, ensure_ascii=False, indent=2))
    counts["imported"] += 1
    return True


# tally keys shared by every corpus's counts dict; a walker adds its own
# route keys (pdf_route/trips_route/…) and `_report` prints those separately.
# `null_stub` is an entries-driven skip; `stray_dir`/`no_docdir` a downloaded-driven
# one (a non-bucket or empty tree dir); `corrupt_entry` a TRIPS sibling entry whose
# provenance was unreadable -- corpora that can't hit one just leave it 0.
_TALLY = ("imported", "skipped_live", "skipped_better", "skipped_existing",
          "null_stub", "no_docdir", "stray_dir", "corrupt_entry")


def _base_counts(**routes):
    """A fresh counts dict: the shared tally keys plus this corpus's route keys."""
    return dict.fromkeys(_TALLY, 0) | dict.fromkeys(routes, 0)


def _report(corpus, counts, log):
    """One import summary line: the imported total broken down by route, then the
    shared skip/skip-shape tallies. Returns the counts dict."""
    routes = ", ".join("%d %s" % (counts[k], k) for k in counts if k not in _TALLY)
    log("%s import-legacy: %d imported (%s); %d null-stub, %d no-docdir, "
        "%d stray-dir, %d corrupt-entry, %d skipped-live, %d skipped-better, "
        "%d re-run-skipped"
        % (corpus, counts["imported"], routes, counts["null_stub"],
           counts["no_docdir"], counts["stray_dir"], counts["corrupt_entry"],
           counts["skipped_live"], counts["skipped_better"],
           counts["skipped_existing"]))
    return counts


# --------------------------------------------------------------------------
# propriksdagen import (downloaded/-driven: docs located by tree shape)
# --------------------------------------------------------------------------


def _iter_docs(downloaded, counts, log):
    """The `downloaded/<rm>/<nr>/` document directories, oldest-first. Non-rm
    junk dirs (shape-checked) are counted and skipped; a subdir is a document
    only when it holds an ``index.xml``."""
    for rmdir in sorted(p for p in downloaded.iterdir() if p.is_dir()):
        if not RE_RM_DIR.fullmatch(rmdir.name):
            counts["junk_dirs"] += 1
            log("propriksdagen: skipping non-rm dir %s" % rmdir.name)
            continue
        for nrdir in sorted((p for p in rmdir.iterdir() if p.is_dir()),
                            key=lambda p: split_numalpha(p.name)):
            if (nrdir / "index.xml").exists():
                yield nrdir


def _pdf_has_text(pdf_path):
    """Whether the PDF carries a usable text layer: >100 non-whitespace chars
    over its first three pages. The htmlformat label is not trusted for this --
    the bytes are probed (11 ms even on a 56 MB scan): the skanning2007 *and*
    text/tml eras' `index.pdf` are textless page scans whose OCR text lives in
    the sibling html, while the html-ec/2000s pdfs are born-digital. A PDF
    poppler cannot read is legacy input rejected to textless (the html/metadata
    route), not a crash."""
    proc = subprocess.run(["pdftotext", "-l", "3", str(pdf_path), "-"],
                          capture_output=True)
    return proc.returncode == 0 and len(b"".join(proc.stdout.split())) > 100


def _pick_body(nrdir, meta, log):
    """The body route for one document, as (route, files, body_format) --
    data-driven, never label-trusting:

    * ``pdf_route`` when ``index.pdf`` is present, passes a %PDF magic sniff
      *and* the text-layer probe. A pdf in `legacy_files` means it passed: a
      textless scan is never listed, so it can't shadow its OCR html at parse
      time or inflate the record's body tier against a later corpus's real
      pdf/doc copy;
    * ``html_route`` otherwise, when ``index.html`` holds a known body format:
      ``text/tml`` (``<br>``-plaintext, the 1995/96-2000/01 window) or
      ``skanning2007`` (riksdagen's OCR Word-export html beside a textless
      scan). The format is recorded on the record (`body_format`) so parse
      picks the adapter without re-probing. ``html-ec`` and the odd formats
      are positioned PDF-rendering junk -- never a body;
    * ``ej_utgiven`` when a text/tml body is the never-published sentinel
      (skip the document entirely);
    * ``metadata_only`` otherwise -- a record with no body is still a real
      catalog document at its URI.
    """
    pdf = nrdir / "index.pdf"
    if pdf.exists() and sniff_extension(pdf) == ".pdf" and _pdf_has_text(pdf):
        return "pdf_route", [legacy_import.rel(pdf)], None
    html = nrdir / "index.html"
    fmt = meta["htmlformat"]
    if html.exists() and fmt == "text/tml":
        try:
            legacy_formats.riksdagen_html_paras(html.read_text("utf-8"))
        except ValueError:      # the load-bearing ej-utgiven sentinel -> record a skip
            log("propriksdagen %s: never-published sentinel -- skipping"
                % meta["basefile"])
            return "ej_utgiven", None, None
        return "html_route", [legacy_import.rel(html)], "text/tml"
    if html.exists() and fmt == "skanning2007":
        return "html_route", [legacy_import.rel(html)], "skanning2007"
    return "metadata_only", [], None


def _report_prop(counts, log):
    log("propriksdagen import: %d imported (%d pdf, %d html, %d metadata-only), "
        "%d skipped-live, %d skipped-better, %d re-run-skipped, %d ej-utgiven, "
        "%d junk dirs"
        % (counts["imported"], counts["pdf_route"], counts["html_route"],
           counts["metadata_only"], counts["skipped_live"], counts["skipped_better"],
           counts["skipped_existing"], counts["ej_utgiven"], counts["junk_dirs"]))
    return counts


def import_propriksdagen(source_path, root, limit=None, force=False, log=print):
    """Import the frozen propriksdagen tree at ``source_path`` into the forarbete
    records under ``root``: per document a record ``prop/<slug>.json`` referencing
    its frozen body file(s) in place. The basefile is minted from the XML's
    ``rm``+``beteckning`` (authoritative), so the URI agrees with a FORARBETEN
    citation by construction and the record collides with any live-harvest record
    on the same basefile via the shared ``record_path`` helper.

    Idempotent: a record already on disk from this corpus is left untouched on a
    plain re-run (``force`` rewrites it), and a live or better-format record is
    never overwritten. ``limit`` caps the run (a test slice). Returns the counts
    dict.
    """
    downloaded = Path(source_path) / "downloaded"
    assert downloaded.is_dir(), \
        "%s is not a frozen propriksdagen tree (no downloaded/)" % source_path
    counts = dict(imported=0, skipped_live=0, skipped_better=0, skipped_existing=0,
                  metadata_only=0, html_route=0, pdf_route=0, ej_utgiven=0,
                  junk_dirs=0)
    for nrdir in _iter_docs(downloaded, counts, log):
        if limit is not None and counts["imported"] >= limit:
            break
        meta = legacy_formats.dokumentstatus_meta((nrdir / "index.xml").read_bytes())
        basefile = meta["basefile"]
        recpath = record_path(root, "prop", basefile)
        existing = legacy_import.read_record(recpath)
        if _preskip(existing, SOURCE, force, counts):
            continue
        route, legacy_files, body_format = _pick_body(nrdir, meta, log)
        if route == "ej_utgiven":
            counts["ej_utgiven"] += 1
            continue
        candidate = {"type": "prop", "basefile": basefile,
                     "identifier": meta["identifier"], "title": meta["title"],
                     "date": meta["date"], "orig_url": meta["source_url"],
                     "url": meta["source_url"],  # data.riksdagen.se still resolves
                     "source": SOURCE, "legacy_files": legacy_files} \
            | ({"body_format": body_format} if body_format else {})
        if _write_if_better(recpath, existing, candidate, counts, force):
            counts[route] += 1
    return _report_prop(counts, log)


# --------------------------------------------------------------------------
# entries-driven corpora (basefile read from the entry, body located by its path)
# --------------------------------------------------------------------------

def _text_pdf(path):
    """Whether `path` is a PDF (magic-sniffed) carrying a usable text layer -- the
    single gate for listing a scan/document PDF as a body. A textless scan (its
    OCR lives elsewhere or nowhere) or a mislabelled asset is rejected, so it can
    neither shadow a real body at parse time nor inflate the record's tier against
    a later corpus's real pdf/doc copy."""
    return path.exists() and sniff_extension(path) == ".pdf" and _pdf_has_text(path)


def _record(corpus, basefile, entry, legacy_files, body_format=None):
    """The import record for an entries-driven corpus. Identity + printed
    identifier come from the type family; no `date` -- the frozen entry JSONs
    carry no authoritative document date (unlike propriksdagen's dokumentstatus
    XML), so the catalog's date is left None rather than fabricated from the
    basefile year. The entry's `orig_url` is always kept as provenance; it also
    becomes the rendered `url` (Källa link -> source_url) only for the corpora
    whose host still resolves (LIVE_SOURCE_URL) -- a TRIPS-family orig_url points
    at a dead IP, so it stays provenance-only."""
    typ = CORPUS_TYPE[corpus]
    orig_url = entry.get("orig_url")
    record = {"type": typ, "basefile": basefile,
              "identifier": "%s %s" % (IDENT_PREFIX[typ], basefile),
              "title": entry.get("title"), "date": None,
              "orig_url": orig_url,
              "url": orig_url if corpus in LIVE_SOURCE_URL else None,
              "source": corpus, "legacy_files": legacy_files}
    if body_format:
        record["body_format"] = body_format
    return record


def _walk_entries(corpus, source_path, limit, force, counts, root, pick, log):
    """The shared entries-driven walk: for each entry (its authoritative basefile,
    null stubs skipped), run the corpus's `pick(entry, docdir) -> (legacy_files,
    body_format, route)` to locate the body, then apply the precedence rule. A
    route of None is a logged skip (an entry whose referenced body dir is absent
    or empty -- the legacy sanitizer's stray dirs). Tallies land in `counts`."""
    entries_dir = Path(source_path) / "entries"
    downloaded = Path(source_path) / "downloaded"
    assert entries_dir.is_dir() and downloaded.is_dir(), \
        "%s is not a frozen %s tree (need entries/ + downloaded/)" % (source_path, corpus)
    typ = CORPUS_TYPE[corpus]
    for entrypath in legacy_import.iter_entries(entries_dir):
        if limit is not None and counts["imported"] >= limit:
            break
        entry = json.loads(entrypath.read_text("utf-8"))
        basefile = entry.get("basefile")
        if basefile is None:                              # a failed-download stub
            counts["null_stub"] += 1
            continue
        recpath = record_path(root, typ, basefile)
        existing = legacy_import.read_record(recpath)
        if _preskip(existing, corpus, force, counts):
            continue
        legacy_files, body_format, route = pick(entry, legacy_import.docdir(downloaded, entrypath,
                                                               entries_dir))
        if route is None:                                 # absent/empty body dir
            counts["no_docdir"] += 1
            continue
        candidate = _record(corpus, basefile, entry, legacy_files, body_format)
        if _write_if_better(recpath, existing, candidate, counts, force):
            counts[route] += 1


# --- souregeringen / dsregeringen / dirregeringen (landing HTML + slug PDFs) ---

def _linkstem(name):
    """A content link's or file's basename with any trailing `.pdf` stripped, so a
    landing-page href (often extensionless on the redesigned site) matches the
    slug-named PDF on disk regardless of a dotted stem (`del-2-kap.-5-8`)."""
    name = name.rstrip("/").rsplit("/", 1)[-1]
    return name[:-4] if name.lower().endswith(".pdf") else name


def _order_pdfs(docdir):
    """The text-probed PDF bodies of a regeringen-era docdir, main part first.
    Multi-part documents (`del-2-kap.-5-8.pdf`, a translation appendix) are
    ordered by the landing page's content-link order -- the main document is
    linked first, exactly as the live downloader's `find_content_links` keeps it
    -- so the first legacy_file is the main part like a harvested record's. PDFs
    not linked from the landing fall to the end (name-sorted); the `.etag`
    siblings and non-PDF assets are ignored."""
    pdfs = [p for p in sorted(docdir.glob("*.pdf")) if _text_pdf(p)]
    if len(pdfs) <= 1:
        return pdfs
    index = docdir / "index.html"
    order = {}
    if index.exists():
        for i, href in enumerate(download.find_content_links(index.read_text("utf-8"))):
            order[_linkstem(href)] = i
    return sorted(pdfs, key=lambda p: (order.get(_linkstem(p.name), len(order)), p.name))


def _pick_regeringen(_entry, docdir):
    """Body pick for a regeringen-era doc: the text-probed slug PDFs (main first),
    or metadata-only. A missing docdir (an entry the harvest never fetched) is a
    logged skip."""
    if not docdir.is_dir():
        return [], None, None
    pdfs = _order_pdfs(docdir)
    return [legacy_import.rel(p) for p in pdfs], None, ("pdf_route" if pdfs else "metadata_only")


# --- soukb (one probed index.pdf per doc) --------------------------------

def _pick_index_pdf(_entry, docdir):
    """Body pick for soukb: the one `index.pdf` per doc (KB scans with an OCR text
    layer -- probed at import, textless -> metadata-only; a probed pdf is parsed
    via the pdftotext fallback at parse time). A missing docdir is a logged skip.
    (dirasp stores index.pdf too but is walked downloaded-first -> `_pick_dirasp`.)"""
    if not docdir.is_dir():
        return [], None, None
    pdf = docdir / "index.pdf"
    if _text_pdf(pdf):
        return [legacy_import.rel(pdf)], None, "pdf_route"
    return [], None, "metadata_only"


# --- propkb (ABBYY OCR-XML xor a scan-only index.pdf) ---------------------

def _pick_propkb(_entry, docdir):
    """Body pick for propkb: the ABBYY OCR-XML `index.xml` (the abbyy parse route,
    page-anchored so 1867-1970 `#sid{N}` citations resolve) when present, else the
    text-probed scan `index.pdf`, else metadata-only. The two never coexist in a
    doc dir (17,295 xml xor 1,772 pdf)."""
    if not docdir.is_dir():
        return [], None, None
    xml = docdir / "index.xml"
    if xml.exists():
        return [legacy_import.rel(xml)], "abbyy", "abbyy_route"
    pdf = docdir / "index.pdf"
    if _text_pdf(pdf):
        return [legacy_import.rel(pdf)], None, "pdf_route"
    return [], None, "metadata_only"


# --- TRIPS family: proptrips / dirtrips / dirasp (walked downloaded-first) ---
#
# Unlike every other corpus these are walked *downloaded-first*, with the basefile
# read from the path. The retired TRIPS site was scraped from a flaky IP whose
# sanitizer left roughly half the entry JSONs null-basefile (proptrips 465 of
# 4,540, dirtrips 2,684 of 5,095, dirasp 1,442 of 1,826) -- yet those null-entry
# doc dirs hold real bodies (e.g. proptrips 2014/15:40 is a born-digital PDF).
# Walking entries would silently drop ~90% of proptrips. The download path encodes
# the identity reliably instead (`downloaded/1993-94/40/` -> `1993/94:40`, agreeing
# with propriksdagen's rm+beteckning basefile by construction), so it drives the
# walk; the sibling entry -- even a null-basefile one -- still supplies the
# orig_url provenance when present. See the §7g report for this deviation from the
# entries-driven plan.

# a downloaded rm/year bucket: '1993-94', '1999-2000' (proptrips riksmöten) or
# '1987' (dir single years). The `fullmatch` excludes the sanitizer's stray tree
# dirs (`proptrips`, `urls.map`, `mprtfs`, `2006-prop-2006-07`); the bare-year
# empties (`1910/`, `1925/`) match but hold no nr dirs, so mint nothing.
RE_TRIPS_BUCKET = re.compile(r"\d{4}(?:-(?:\d{2}|\d{4}))?")


def _trips_basefile(bucket, nr):
    """'<rm-or-year>:<nr>' from a downloaded path bucket + nr, the first '-' in a
    range bucket restored to '/': '1993-94'+'40' -> '1993/94:40', '1999-2000'+'1'
    -> '1999/2000:1', '1987'+'10' -> '1987:10'."""
    return "%s:%s" % (bucket.replace("-", "/", 1), nr)


def _trips_entry(entries_dir, bucket, nr, counts):
    """The sibling entry JSON for a path-derived TRIPS doc (often null-basefile),
    or {} when absent. Only its `orig_url`/`title` provenance is read -- identity
    comes from the path, so a null basefile here is harmless. One frozen stub is
    corrupt on disk (dirtrips/entries/2006/72.json, a doubled tail from an
    interrupted rewrite): a JSON error just means this provenance is unavailable,
    which is exactly the absent-entry case -- not a reason to abort the walk,
    but it is tallied (`corrupt_entry`) so a lost provenance never goes
    unreported."""
    p = entries_dir / bucket / (nr + ".json")
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text("utf-8"))
    except json.JSONDecodeError:
        counts["corrupt_entry"] += 1
        return {}


def _pick_proptrips(docdir):
    """Body pick for a proptrips doc dir: a text-probed `index.pdf` (pdf route,
    tier 2 -- the 1995/96+ born-digital copies that beat propriksdagen's html-only
    records in the upstream-PDF gap), else the `index.html` TRIPS plaintext (trips
    route, tier 1) when it actually carries a `div.body-text` (some frozen pages
    are search-result shells the crawl saved instead of the document, like
    dirtrips' 5 known shells -- no recoverable body), else metadata-only.
    `.doc`/`.docx`/`.wpd` have no parse route so are not listed. An empty dir (a
    legacy sanitizer stray) is a no-doc skip."""
    if not any(docdir.iterdir()):
        return None
    pdf = docdir / "index.pdf"
    if _text_pdf(pdf):
        return [legacy_import.rel(pdf)], None, "pdf_route"
    html = docdir / "index.html"
    if html.exists() and "body-text" in html.read_text("utf-8"):
        return [legacy_import.rel(html)], "trips", "trips_route"
    return [], None, "metadata_only"      # only a doc/docx/wpd body, or a shell page


def _pick_dirasp(docdir):
    """Body pick for a dirasp doc dir: the text-probed `index.pdf` (the KOM PDFs,
    2006-2019), else metadata-only. An empty dir is a no-doc skip."""
    if not any(docdir.iterdir()):
        return None
    pdf = docdir / "index.pdf"
    if _text_pdf(pdf):
        return [legacy_import.rel(pdf)], None, "pdf_route"
    return [], None, "metadata_only"


def _walk_trips_dirs(corpus, source_path, root, pick, counts, limit, force, log):
    """Downloaded-first walk for a per-doc-dir TRIPS corpus (proptrips, dirasp):
    each `downloaded/<bucket>/<nr>/` holding a body. basefile from the path,
    provenance from the sibling entry. Stray non-bucket dirs and empty bucket/nr
    dirs (the sanitizer's leftovers) mint nothing and are tallied."""
    entries_dir = Path(source_path) / "entries"
    downloaded = Path(source_path) / "downloaded"
    assert downloaded.is_dir(), \
        "%s is not a frozen %s tree (no downloaded/)" % (source_path, corpus)
    typ = CORPUS_TYPE[corpus]
    for bucket in sorted(p for p in downloaded.iterdir() if p.is_dir()):
        if not RE_TRIPS_BUCKET.fullmatch(bucket.name):
            counts["stray_dir"] += 1
            continue
        for nrdir in sorted((p for p in bucket.iterdir() if p.is_dir()),
                            key=lambda p: split_numalpha(p.name)):
            if limit is not None and counts["imported"] >= limit:
                return
            basefile = _trips_basefile(bucket.name, nrdir.name)
            recpath = record_path(root, typ, basefile)
            existing = legacy_import.read_record(recpath)
            if _preskip(existing, corpus, force, counts):
                continue
            picked = pick(nrdir)
            if picked is None:                            # empty stray dir
                counts["no_docdir"] += 1
                continue
            legacy_files, body_format, route = picked
            entry = _trips_entry(entries_dir, bucket.name, nrdir.name, counts)
            candidate = _record(corpus, basefile, entry, legacy_files, body_format)
            if _write_if_better(recpath, existing, candidate, counts, force):
                counts[route] += 1


# --------------------------------------------------------------------------
# per-corpus entry points + dispatch
# --------------------------------------------------------------------------

def _import_entries(corpus, source_path, root, pick, routes, limit, force, log):
    """Run the shared entries-driven walk for `corpus` with its body `pick` and
    the route keys it can emit, then report. Returns the counts dict."""
    counts = _base_counts(**dict.fromkeys(routes, 0))
    _walk_entries(corpus, source_path, limit, force, counts, root, pick, log)
    return _report(corpus, counts, log)


def import_souregeringen(source_path, root, limit=None, force=False, log=print):
    return _import_entries("souregeringen", source_path, root, _pick_regeringen,
                           ("pdf_route", "metadata_only"), limit, force, log)


def import_dsregeringen(source_path, root, limit=None, force=False, log=print):
    return _import_entries("dsregeringen", source_path, root, _pick_regeringen,
                           ("pdf_route", "metadata_only"), limit, force, log)


def import_dirregeringen(source_path, root, limit=None, force=False, log=print):
    return _import_entries("dirregeringen", source_path, root, _pick_regeringen,
                           ("pdf_route", "metadata_only"), limit, force, log)


def import_soukb(source_path, root, limit=None, force=False, log=print):
    return _import_entries("soukb", source_path, root, _pick_index_pdf,
                           ("pdf_route", "metadata_only"), limit, force, log)


def import_propkb(source_path, root, limit=None, force=False, log=print):
    return _import_entries("propkb", source_path, root, _pick_propkb,
                           ("abbyy_route", "pdf_route", "metadata_only"),
                           limit, force, log)


def import_proptrips(source_path, root, limit=None, force=False, log=print):
    counts = _base_counts(pdf_route=0, trips_route=0, metadata_only=0)
    _walk_trips_dirs("proptrips", source_path, root, _pick_proptrips, counts,
                     limit, force, log)
    return _report("proptrips", counts, log)


def import_dirasp(source_path, root, limit=None, force=False, log=print):
    counts = _base_counts(pdf_route=0, metadata_only=0)
    _walk_trips_dirs("dirasp", source_path, root, _pick_dirasp, counts,
                     limit, force, log)
    return _report("dirasp", counts, log)


def import_dirtrips(source_path, root, limit=None, force=False, log=print):
    """dirtrips is downloaded-first too (its TRIPS entries are ~half null-basefile):
    a flat `downloaded/<year>/<n>.html` per doc (storage_policy=file). The basefile
    comes from the path, provenance from the sibling entry; the html is the
    `div.body-text` TRIPS plaintext (trips route). A handful of frozen pages
    (5: dir 1991:26, 1994:115, 1997:106/112/145) are search-result shells the
    crawl saved instead of the document -- no `div.body-text`, no recoverable
    body -- so those import metadata-only."""
    entries_dir = Path(source_path) / "entries"
    downloaded = Path(source_path) / "downloaded"
    assert downloaded.is_dir(), \
        "%s is not a frozen dirtrips tree (no downloaded/)" % source_path
    counts = _base_counts(trips_route=0, metadata_only=0)
    for bucket in sorted(p for p in downloaded.iterdir() if p.is_dir()):
        if not RE_TRIPS_BUCKET.fullmatch(bucket.name):
            counts["stray_dir"] += 1
            continue
        for html in sorted((p for p in bucket.iterdir() if p.suffix == ".html"),
                           key=lambda p: split_numalpha(p.stem)):
            if limit is not None and counts["imported"] >= limit:
                return _report("dirtrips", counts, log)
            basefile = _trips_basefile(bucket.name, html.stem)
            recpath = record_path(root, "dir", basefile)
            existing = legacy_import.read_record(recpath)
            if _preskip(existing, "dirtrips", force, counts):
                continue
            entry = _trips_entry(entries_dir, bucket.name, html.stem, counts)
            if "body-text" in html.read_text("utf-8"):
                candidate = _record("dirtrips", basefile, entry,
                                    [legacy_import.rel(html)], "trips")
                route = "trips_route"
            else:
                candidate = _record("dirtrips", basefile, entry, [])
                route = "metadata_only"
            if _write_if_better(recpath, existing, candidate, counts, force):
                counts[route] += 1
    return _report("dirtrips", counts, log)


# corpus -> its walker. `import_corpus` is the single build.py entry point; the
# per-corpus functions stay public for the tests that drive one directly.
IMPORTERS = {
    "propriksdagen": import_propriksdagen,
    "souregeringen": import_souregeringen,
    "dsregeringen": import_dsregeringen,
    "dirregeringen": import_dirregeringen,
    "soukb": import_soukb,
    "dirasp": import_dirasp,
    "propkb": import_propkb,
    "proptrips": import_proptrips,
    "dirtrips": import_dirtrips,
}


def import_corpus(corpus, source_path, root, limit=None, force=False, log=print):
    """Import the frozen `corpus` tree at `source_path` into the forarbete records
    under `root`, dispatching to its walker. Idempotent, points at the frozen
    bytes in place, never overwrites a live-harvest or better-format record."""
    assert corpus in IMPORTERS, \
        "unknown förarbete legacy corpus %r (have: %s)" \
        % (corpus, ", ".join(sorted(IMPORTERS)))
    return IMPORTERS[corpus](source_path, root, limit=limit, force=force, log=log)
