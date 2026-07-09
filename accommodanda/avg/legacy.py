"""One-time import of the frozen ARN corpus into the avg vertical (§7g).

Allmänna reklamationsnämnden (ARN) published its referat through a Digiforms
web application whose document URLs are session-bound and long dead -- the
corpus (1,027 decisions, 1991-2022) is complete and will never update, so no
downloader is ported. The old pipeline's frozen ``downloaded/YYYY/NNNN/`` tree
is imported once into the avg record layout, after which the ordinary avg
``parse`` stage and the whole derived layer treat each referat like a harvested
decision (no network).

Per case the frozen tree holds a ``fragment.html`` (the search-result row,
carrying the metadata not present in the decision file: Änr = diarienummer,
Avgörande = beslutsdatum, Avdelning = subject, and the free-text summary that
*is* the title -- ARN referat have no real title) plus one decision file
``index.{pdf,doc,wpd,rtf}``. Two known corpus quirks are handled by construction:

- **mislabelled bodies**: five 2001 cases store a Digiforms HTML error page as
  ``index.pdf`` (fails a ``%PDF`` magic check); the sibling ``index.doc`` is the
  real decision. The body file is chosen by **magic-byte sniff**
  (`lib.util.document_extension`), never by extension.
- **empty stubs**: a case with a blank summary *and* a body that yields no text
  (a 296-byte WordPerfect stub) carries no content -- the old pipeline excised
  it (DocumentRemovedError). Detected generically (empty title + empty PDF) and
  skipped, not hardcoded.

The materialized PDF is the imported bytes (a deliberate deviation from §7g's
"point at the bytes": ~80% of the corpus is doc/wpd/rtf that needs conversion
anyway, and the corpus is 96 MB). doc/wpd/rtf are converted with LibreOffice;
native PDFs are copied. The document URI is ``avg/arn/{dnr}`` -- byte-identical
to what ``lagrum.fmt_arn_refs`` mints, so a referat and any citation to it agree.

**Precedence** (:func:`lib.legacy_import.should_write`): each import record
carries the ``source: "arn-legacy"`` marker; a record written by the live
arn.se harvester has no ``source`` key and always wins, even under ``--force``
(the frozen corpus never beats a live copy). The record also keeps
``imported_from`` -- pure provenance naming the frozen file the body came from.
"""

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from bs4 import BeautifulSoup

from ..lib import compress, legacy_import
from ..lib.pdftext import pdf_pages
from ..lib.util import (
    basefile_slug,
    normalize_space,
    record_path,
    sniff_extension,
)

# body-file preference when a case holds more than one valid decision file (the
# five 2001 cases with both a real index.doc and a corrupt index.pdf): a native
# PDF is best, then rtf/docx/doc/wpd in descending fidelity of conversion.
BODY_PREFERENCE = (".pdf", ".rtf", ".docx", ".doc", ".wpd")

# the trailing self-citation the summary carries ("... Avgörande 1992-11-12;
# 92-3657."), stripped to recover the title (ARN referat have no real title, so
# the summary IS the title). The old pipeline's regex only caught the exact
# "DATE; DNR" form; the corpus in fact carries many variants -- a colon after
# "Avgörande", comma/parenthesis separators, an "Änr"/"ärende" word, 2- or
# 4-digit years, stray internal spaces ("1992-11- 12"), reversed DNR-first order,
# and multi-DNR "... och ..." lists. Anchor to the avgörandedatum (present in
# every self-citation) and consume to the end: strip from "Avgörande" through the
# first date-shaped token to end-of-string. The pre-date span forbids a period so
# a stray mid-sentence "Avgörande" can't reach across into the citation, and the
# whole match is bounded so a citation embedded mid-summary (a fragment whose
# summary div swallowed the decision body) is left untouched, not deleted.
RE_SELF_CITE = re.compile(
    r"\s*Avgörande\b[^.]{0,20}?\d{2,4}\s*-\s*\d{1,2}\s*-\s*\d{1,2}.{0,140}$")

# the ARN dnr shape ("YYYY-NNNNN"), shared with the live harvester's listing
# parse (download.arn_dnrs) -- one definition serves both halves of the vertical
RE_ARN_DNR = re.compile(r"\d{4}-\d{4,}")
RE_ISO = re.compile(r"\d{4}-\d{2}-\d{2}$")

# the `source` precedence marker the import stamps on its records (the frozen
# corpus tag lib.legacy_import.should_write reads; a live arn.se record has none)
SOURCE = "arn-legacy"


def arn_pdf_path(root, basefile):
    """The materialized decision PDF beside the record ("arn/1992-3657" ->
    ``<root>/arn/arn-1992-3657.pdf``) -- the JO body-file shape."""
    return Path(root) / "arn" / (basefile_slug(basefile) + ".pdf")


def parse_fragment(html_text):
    """The metadata from a case's ``fragment.html``: dnr (Änr), beslutsdatum
    (Avgörande, already ISO), avdelning (Avdelning) and the sanitized title (the
    summary paragraph with its trailing self-citation stripped). An empty summary
    -- a case with no real content -- yields an empty title."""
    soup = BeautifulSoup(html_text, "html.parser")

    def value(label):
        cell = soup.find("div", class_="heading3", string=label)
        assert cell is not None, "fragment has no %r label cell" % label
        td = cell.find_parent("td")
        assert td is not None, "fragment %r label cell has no <td>" % label
        val = td.find_next_sibling("td")
        assert val is not None, "fragment %r label has no value cell" % label
        return val.get_text(strip=True)

    # the label/value cells carry `strongstandardtext`; the summary is the only
    # free-text (classless) div with content -- the spacer divs are nbsp-only.
    summary = normalize_space(" ".join(
        d.get_text() for d in soup.find_all("div")
        if not d.get("class") and d.get_text(strip=True)))
    return {"dnr": value("Änr"),
            "beslutsdatum": value("Avgörande"),
            "avdelning": value("Avdelning"),
            "title": RE_SELF_CITE.sub("", summary).strip()}


def pick_body(casedir):
    """The case's decision file, chosen by magic-byte sniff (a mislabelled
    body -- a 2001 error page stored as index.pdf -- is rejected) in
    BODY_PREFERENCE order. Returns (path, extension) or (None, None) when the
    case holds no recognizable document."""
    candidates = {}
    for f in sorted(casedir.glob("index.*")):
        if ext := sniff_extension(f):
            candidates[ext] = f
    for ext in BODY_PREFERENCE:
        if ext in candidates:
            return candidates[ext], ext
    return None, None


def _iter_cases(downloaded):
    """The ``downloaded/YYYY/NNNN/`` case directories, oldest first."""
    for yeardir in sorted(downloaded.iterdir()):
        if yeardir.is_dir() and yeardir.name.isdigit():
            for casedir in sorted(yeardir.iterdir()):
                if casedir.is_dir() and casedir.name.isdigit():
                    yield casedir


def _convert_to_pdf(src, outdir, profile):
    """Convert a doc/wpd/rtf decision file to PDF with headless LibreOffice into
    ``outdir``. An isolated ``UserInstallation`` profile keeps parallel/nested
    soffice instances from fighting over the shared profile lock. The target is
    removed before the run: soffice sometimes exits 0 without producing output,
    and ``outdir`` is shared across every case in the import, so a stale PDF left
    over from a same-stemmed earlier case would otherwise pass the existence
    check below and be imported as the current case's body."""
    out = outdir / (src.stem + ".pdf")
    out.unlink(missing_ok=True)
    subprocess.run(
        ["soffice", "--headless", "-env:UserInstallation=file://%s" % profile,
         "--convert-to", "pdf", "--outdir", str(outdir), str(src)],
        check=True, capture_output=True, timeout=120)
    assert out.exists(), "soffice produced no PDF for %s" % src
    return out


def _pdf_has_any_text(pdf_path):
    """Whether the PDF carries any extractable text at all -- the empty-body test
    for a case whose summary is also blank. (Deliberately laxer than förarbete's
    text-layer probe, which demands >100 chars: here a single word of content
    means the case is not an empty stub.)"""
    return any(line.text.strip()
               for _pageno, lines in pdf_pages(str(pdf_path)) for line in lines)


def _orig_url(source, casedir):
    """The original download URL the old pipeline recorded for this case, from
    its ``entries/YYYY/NNNN.json`` -- kept verbatim on the record as provenance.
    (Not a source link: ARN's Digiforms URLs are session-bound and dead, so the
    artifact carries no source_url; the provenance still must not be lost.)"""
    entry = (Path(source) / "entries" / casedir.parent.name
             / (casedir.name + ".json"))
    return json.loads(entry.read_text()).get("orig_url") if entry.exists() else None


def import_arn(source, root, limit=None, force=False, log=print):
    """Import the frozen ARN tree at ``source`` into the avg records under
    ``root``: per case a record ``arn/<slug>.json`` + its decision PDF
    ``arn/<slug>.pdf``. Idempotent (a case with both already on disk is skipped
    unless ``force``); ``limit`` caps the run for a test slice. Returns
    (imported, skipped, empty)."""
    assert shutil.which("soffice"), \
        "LibreOffice (soffice) not found -- needed to convert ARN doc/wpd/rtf"
    downloaded = Path(source) / "downloaded"
    assert downloaded.is_dir(), \
        "%s is not a frozen ARN tree (no downloaded/)" % source
    imported = skipped = empty = 0
    profile = tempfile.mkdtemp(prefix="arn-soffice-")
    try:
        with tempfile.TemporaryDirectory(prefix="arn-convert-") as tmp:
            tmpdir = Path(tmp)
            for casedir in _iter_cases(downloaded):
                if limit is not None and imported >= limit:
                    break
                dnr = "%s-%s" % (casedir.parent.name, casedir.name)
                assert RE_ARN_DNR.fullmatch(dnr), "case %s is not an ARN dnr" % casedir
                basefile = "arn/" + dnr
                recpath = record_path(root, "arn", basefile)
                pdfpath = arn_pdf_path(root, basefile)
                # the shared §7g precedence rule via the `source` marker: a live
                # arn.se record (no marker) always wins, even under --force; the
                # import's own record is rewritten under --force or when its
                # materialized PDF is missing
                existing = legacy_import.read_record(recpath)
                if not legacy_import.should_write(existing, SOURCE,
                                                  force or not pdfpath.exists()):
                    skipped += 1
                    continue

                meta = parse_fragment((casedir / "fragment.html").read_text("utf-8"))
                if meta["dnr"] != dnr:
                    raise ValueError(
                        "fragment Änr %s != dir-derived dnr %s" % (meta["dnr"], dnr))
                if not RE_ISO.match(meta["beslutsdatum"]):
                    raise ValueError(
                        "arn %s beslutsdatum %r is not ISO" % (dnr, meta["beslutsdatum"]))

                chosen, ext = pick_body(casedir)
                if chosen is None:
                    log("arn %s: no recognizable decision file -- skipping" % dnr)
                    empty += 1
                    continue
                # the materialized PDF is content-stable (frozen source), so an
                # existing one is reused even under --force -- a record-only rerun
                # (e.g. adding a provenance field) must not redo ~830 conversions
                if pdfpath.exists():
                    pdf = pdfpath
                elif ext == ".pdf":
                    pdf = chosen
                else:
                    pdf = _convert_to_pdf(chosen, tmpdir, profile)

                # a case with neither a title nor any body text is an empty stub
                # (the legacy DocumentRemovedError case) -- detected generically,
                # not by id.
                if not meta["title"] and not _pdf_has_any_text(pdf):
                    log("arn %s: blank summary and empty body -- skipping (%s)"
                        % (dnr, chosen.name))
                    empty += 1
                    continue

                imported_from = "%s/%s/%s" % (casedir.parent.name, casedir.name,
                                              chosen.name)
                record = {"basefile": basefile, "org": "arn", "diarienummer": dnr,
                          "beslutsdatum": meta["beslutsdatum"],
                          "avdelning": meta["avdelning"], "title": meta["title"],
                          "source": SOURCE, "imported_from": imported_from}
                orig_url = _orig_url(source, casedir)
                if orig_url:
                    record["orig_url"] = orig_url
                if pdf != pdfpath:
                    compress.write_download(pdfpath, pdf.read_bytes())
                compress.write_download(recpath, json.dumps(record, ensure_ascii=False, indent=2))
                imported += 1
    finally:
        # ignore_errors: soffice may still hold the profile dir open briefly
        # after being killed by the `timeout=` in `_convert_to_pdf`; the
        # profile is scratch space, never worth failing the import over.
        shutil.rmtree(profile, ignore_errors=True)
    log("arn import: %d imported, %d skipped, %d empty" % (imported, skipped, empty))
    return imported, skipped, empty
