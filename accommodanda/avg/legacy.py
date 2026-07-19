"""One-time imports of the frozen ARN and JO corpora into the avg vertical (§7g).

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

**JO** (:func:`import_jo`): the live jo.se harvest covers the frozen corpus
almost completely (measured 2026-07-19: of 3,291 frozen cases, all but five
join a live record on some diarienummer, after normalizing 2-digit years and
two identities the old pipeline garbled from printed dnr *ranges*). The import
therefore does two things: (1) writes the **ämbetsberättelse map**
(``jo/.officialreport.json``, dnr -> "JO 1990/91 s. 70") from every distilled
RDF's ``dcterms:bibliographicCitation`` -- the citation exists *only* in the
frozen corpus (jo.se does not publish it), and ``parse_jo`` grafts it onto
live records too; (2) imports the genuinely missing cases as ``jo-legacy``
records with their frozen PDFs, shaped like live search records so the
ordinary parse path serves them.
"""

import json
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from bs4 import BeautifulSoup

from ..lib import compress, legacy_import, util
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


def jo_pdf_path(root, basefile):
    """The decision PDF beside a JO record ("jo/2340-2025" ->
    ``<root>/jo/jo-2340-2025.pdf``), shared by the live harvester and the
    frozen import. Lives here (not download.py) because download already
    imports the ARN twin from this module."""
    return Path(root) / "jo" / (basefile_slug(basefile) + ".pdf")


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


# --------------------------------------------------------------------------
# JO: the ämbetsberättelse map + the (few) frozen-only cases
# --------------------------------------------------------------------------

JO_SOURCE = "jo-legacy"
RE_JO_DNR = re.compile(r"\d+-\d{4}")

_RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
_RPUBL = "{http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#}"
_DCT = "{http://purl.org/dc/terms/}"


def jo_officialreport_path(root):
    """The dnr -> ämbetsberättelse-citation map the import writes beside the JO
    records (dotfile: never a record). parse_jo grafts the citation onto live
    records too -- jo.se does not publish it, only the frozen corpus does."""
    return Path(root) / "jo" / ".officialreport.json"


def _norm_dnr(dnr):
    """A frozen diarienummer in comparable form: 2-digit years widened
    ("4643-07" -> "4643-2007"; the corpus has no pre-1951 cases)."""
    m = re.fullmatch(r"(\d+)-(\d{2})", dnr)
    if m:
        century = "19" if int(m.group(2)) > 50 else "20"
        return "%s-%s%s" % (m.group(1), century, m.group(2))
    return dnr


def _headnote_meta(casedir):
    """The jo.se-curated (dnr, title) from a frozen case's ``headnote.html``
    search-result row -- "… Diarienummer : 2484-2001 <title>". Authoritative
    where the old pipeline garbled the identity (it read printed dnr *ranges*
    as single dnrs: the frozen 2484-2487 is really 2484-2001). (None, None)
    when the case has no headnote or the row carries no dnr."""
    headnote = casedir / "headnote.html"
    if not headnote.exists():
        return None, None
    text = BeautifulSoup(headnote.read_text("utf-8"),
                         "html.parser").get_text(" ", strip=True)
    m = re.search(r"Diarienummer\s*:?\s*(\d+-\d{2,4})\s*(\S.*?)?(?:\s*Läs mer|$)",
                  text)
    if not m:
        return None, None
    return _norm_dnr(m.group(1)), normalize_space(m.group(2) or "") or None


def _jo_case(rdf_path):
    """One distilled RDF -> (canonical dnr, all dnrs, title, date, citation),
    or None when the file describes no VagledandeMyndighetsavgorande."""
    root = ET.parse(rdf_path).getroot()
    main = root.find(".//%sVagledandeMyndighetsavgorande" % _RPUBL)
    if main is None:
        return None
    uri = main.get(_RDF + "about") or ""
    canonical = uri.rsplit("/", 1)[-1]
    dnrs = {_norm_dnr(e.text.strip().rstrip(","))
            for e in main.findall(_RPUBL + "diarienummer") if e.text}
    title = next((e.text for e in main.findall(_DCT + "title") if e.text), None)
    date = next((e.text for e in (main.findall(_RPUBL + "avgorandedatum")
                                  + main.findall(_DCT + "issued")) if e.text),
                None)
    citation = next((e.text.strip()
                     for e in main.findall(_DCT + "bibliographicCitation")
                     if e.text and e.text.strip()), None)
    return _norm_dnr(canonical), dnrs, normalize_space(title or ""), date, citation


def import_jo(source, root, force=False, log=print):
    """Import the frozen JO corpus's *deltas* at ``source`` into the avg records
    under ``root``: the ämbetsberättelse map for every case that has one, plus a
    ``jo-legacy`` record + PDF for each case no live jo.se record covers (five,
    measured 2026-07-19 -- the live harvest carries everything else). Join is on
    *any* diarienummer, 2-digit years normalized. Idempotent; ``force`` rewrites
    the import's own records. Returns (mapped, imported, skipped)."""
    distilled = Path(source) / "distilled"
    downloaded = Path(source) / "downloaded"
    assert distilled.is_dir() and downloaded.is_dir(), \
        "%s is not a frozen JO tree (need distilled/ + downloaded/)" % source

    live = set()          # every dnr a *live-harvested* jo record carries
                          # (multi-dnr cases list several, split on ;/,)
    for recpath in compress.glob(Path(root) / "jo", "*.json"):
        if recpath.name.startswith("."):
            continue
        rec = json.loads(compress.read_text(recpath))
        if rec.get("source") == JO_SOURCE:
            continue      # import's own records answer to should_write, not
                          # coverage -- else a re-run could never refresh them
        live |= {d.strip() for d in re.split(r"[;,]", rec.get("diary_number") or "")
                 if d.strip()}

    mapped = imported = skipped = 0
    report = {}
    for rdf_path in sorted(distilled.glob("*/*.rdf")):
        case = _jo_case(rdf_path)
        if case is None:
            continue
        canonical, dnrs, title, date, citation = case
        casedir = downloaded / rdf_path.parent.name / rdf_path.stem
        hd_dnr, hd_title = _headnote_meta(casedir)
        if hd_dnr:
            dnrs = dnrs | {hd_dnr}    # jo.se's own identity joins + gets mapped
            canonical = hd_dnr        # ...and names any imported record's uri
        title = hd_title or title     # prefer the jo.se-curated referat rubrik
        if citation:
            for dnr in dnrs | {canonical}:
                report[dnr] = citation
            mapped += 1
        if dnrs & live or canonical in live:
            continue
        if not RE_JO_DNR.fullmatch(canonical):
            log("jo %s: canonical dnr unusable -- skipping import" % canonical)
            skipped += 1
            continue
        basefile = "jo/" + canonical
        recpath = record_path(root, "jo", basefile)
        if not legacy_import.should_write(legacy_import.read_record(recpath),
                                          JO_SOURCE, force):
            skipped += 1
            continue
        pdf = (downloaded / rdf_path.parent.name / rdf_path.stem / "index.pdf")
        if not (pdf.exists() and sniff_extension(pdf) == ".pdf"):
            log("jo %s: no frozen decision PDF -- skipping import" % canonical)
            skipped += 1
            continue
        record = {"basefile": basefile,
                  "diary_number": "; ".join(
                      [canonical] + sorted(dnrs - {canonical})),
                  "post_title": title, "resolve_date": date,
                  "source": JO_SOURCE,
                  "imported_from": "%s/%s/index.pdf"
                                   % (rdf_path.parent.name, rdf_path.stem)}
        compress.write_download(jo_pdf_path(root, basefile), pdf.read_bytes())
        compress.write_download(recpath,
                                json.dumps(record, ensure_ascii=False, indent=2))
        imported += 1
        log("jo import: %s (%s)" % (canonical, title[:60]))
    # a parse input (avg_inputs lists it): atomic, so a crash never leaves a
    # truncated map that every subsequent JO parse would choke on
    util.write_atomic(jo_officialreport_path(root),
                      json.dumps(dict(sorted(report.items())),
                                 ensure_ascii=False, indent=0))
    log("jo import: %d citations mapped, %d cases imported, %d skipped"
        % (mapped, imported, skipped))
    return mapped, imported, skipped


# --------------------------------------------------------------------------
# JK: the frozen-only decisions (jk.se's archive thins out before ~2000)
# --------------------------------------------------------------------------

JK_SOURCE = "jk-legacy"


def _jk_norm(dnr):
    """The dot/space-insensitive diarienummer normal form the frozen/live join
    runs on: the frozen ids write the avdelning undotted ('859-97-21'), live
    jk.se writes it dotted ('859-97-2.1')."""
    return re.sub(r"[.\s,]+", "", (dnr or "").lower())


def _jk_case(rdf_path):
    """One frozen-JK distilled RDF -> (title, ISO beslutsdatum), best-effort."""
    if not rdf_path.exists():
        return None, None
    main = ET.parse(rdf_path).getroot().find(
        ".//%sVagledandeMyndighetsavgorande" % _RPUBL)
    if main is None:
        return None, None
    title = next((e.text for e in main.findall(_DCT + "title") if e.text), None)
    date = next((e.text for e in (main.findall(_RPUBL + "beslutsdatum")
                                  + main.findall(_RPUBL + "avgorandedatum")
                                  + main.findall(_DCT + "issued")) if e.text),
                None)
    return normalize_space(title or "") or None, date


def import_jk(source, root, force=False, log=print):
    """Import the frozen JK corpus's not-live decisions at ``source`` into the
    avg records under ``root``: a ``jk-legacy`` record + the frozen jk.se
    landing page (the same markup the live harvest stores, so `parse_jk` reads
    it unchanged) for each decision no live record covers. The join is
    dot-insensitive over every diarienummer a live record names (37 genuinely
    absent, measured 2026-07-19 -- almost all 1997-1999). Idempotent; ``force``
    rewrites the import's own records. Returns (imported, skipped)."""
    source = Path(source)
    assert (source / "entries").is_dir() and (source / "downloaded").is_dir(), \
        "%s is not a frozen JK tree (need entries/ + downloaded/)" % source

    live = set()
    for recpath in compress.glob(Path(root) / "jk", "*.json"):
        if recpath.name.startswith("."):
            continue
        rec = json.loads(compress.read_text(recpath))
        if rec.get("source") == JK_SOURCE:
            continue
        for part in re.split(r"\s+och\s+|[;,]", rec.get("diarienummer_raw", "")):
            if part.strip():
                live.add(_jk_norm(part))

    imported = skipped = 0
    for entry_path in sorted(source.glob("entries/*/*.json")):
        entry = json.loads(entry_path.read_text())
        dnr = entry.get("basefile")
        if not dnr or _jk_norm(dnr) in live:
            continue
        html = source / "downloaded" / entry_path.parent.name / (dnr + ".html")
        if not html.exists():
            log("jk %s: no frozen landing page -- skipping import" % dnr)
            skipped += 1
            continue
        basefile = "jk/" + dnr
        recpath = record_path(root, "jk", basefile)
        if not legacy_import.should_write(legacy_import.read_record(recpath),
                                          JK_SOURCE, force):
            skipped += 1
            continue
        title, date = _jk_case(
            source / "distilled" / entry_path.parent.name / (dnr + ".rdf"))
        record = {"basefile": basefile, "org": "jk", "diarienummer_raw": dnr,
                  "beslutsdatum_raw": date, "title": title or dnr,
                  "url": entry.get("orig_url"), "source": JK_SOURCE}
        compress.write_download(
            Path(root) / "jk" / (basefile_slug(basefile) + ".html"),
            html.read_bytes())
        compress.write_download(recpath,
                                json.dumps(record, ensure_ascii=False, indent=2))
        imported += 1
        log("jk import: %s (%s)" % (dnr, (title or "")[:60]))
    log("jk import: %d imported, %d skipped" % (imported, skipped))
    return imported, skipped
