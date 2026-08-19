"""Harvesters for the seven agencies' rättsliga ställningstaganden.

Six of them publish the document itself as a **PDF** and the metadata around it
on a listing (and, for two agencies, a per-document page). So the harvest is the
same three steps everywhere -- walk the listing, mint the identity from the
agency's own number, fetch the document -- and what differs is only how the
listing is read. None of the seven paginates except Migrationsverket's, so the
JK/ARN idiom applies throughout: one walk per run, fetching what is new or
changed, no watermark.

**IMY** (imy.se, Optimizely/EPiServer): five ställningstaganden, each an info
block naming the title and linking, as a ``/link/<guid>.aspx`` redirect, the
publication page whose *preamble* states the number ("IMYRS 2024:1"). That page
carries IMY's own summary of the statement -- a genuinely editorial abstract,
which no other agency here writes -- followed by boilerplate about the series
and the link to the PDF. The boilerplate is cut at its own heading; the summary
is kept, and the PDF is the body.

**FI** (fi.se): a hand-authored HTML table with the four columns FI maintains by
hand -- Nummer, Titel (linking the PDF), Beslutsdatum, Status. The Status column
is why this is the one listing that keeps *repealed* ställningstaganden visible:
"Ett rättsligt ställningstagande gäller fram till dess att FI upphäver det. Det
tas då bort från webbplatsen, men står kvar i vår förteckning nedan."

**Försäkringskassan** (forsakringskassan.se, SiteVision): the largest series --
108 documents, 2005- -- grouped under a year heading per section, each a file
widget linking one PDF whose link text ends in the number ("(Rättsligt
ställningstagande 2025:01)"). The number is read from the *PDF* rather than
from that text, because the PDF's own ``Serienummer`` field is the document
speaking about itself and the listing has at least one typo (the 2026:01 PDF is
listed as 2026:03). That makes this the one agency whose *identity* comes out of
the document, so the fetch happens here rather than in the walk; the number a
record was filed under is then remembered, and an incremental run costs the one
listing request.

**Kronofogden** (kronofogden.se, SiteVision): a document list grouped under year
headings, each entry linking the PDF and naming the number in a trailing
``span`` ("1/23/VER | pdf | 423 kB"). The numbers run löpnummer/år with a
verksamhets suffix (VER verkställighet, RKF rikskronofogden, TSM konkurstillsyn,
Skusan skuldsanering). The year heading is *not* the number's year -- a 2016
ställningstagande sits under 2018 -- so it is not read as one. Only gällande
ställningstaganden are published, and one entry carries no number at all, which
is reported rather than invented.

**Migrationsverket** (lifos.migrationsverket.se): published through the Lifos
database, harvested through its detailed search filtered on the subject word
"Rättsliga ställningstaganden och kommentarer" -- 104 documents, ten per page,
``page=N`` a true offset. A hit links a document page keyed on a Lifos
``documentSummaryId``; that page carries the title (with the number, RS/028/2021
or RK/003/2026 for the rättsliga kommentarer the same series holds), the
upphovsdatum, the ämnesord and the huvuddokument PDF. Two entries state no
number at all; their PDFs print one, so those are read out of the document the
way Försäkringskassans always are. Migrationsverket revises a
ställningstagande *in place*, keeping the number and raising a version, so the
version is recorded and a revision is an update of the same document rather than
a new one. The site serves only its leaf certificate and leaves the client to
find the intermediate, so the session verifies through an AIA-completed bundle
(`lib.net.mount_aia_chain`).

**Konkurrensverket** (konkurrensverket.se, behind the same HTTP/2-only
Cloudflare front the KKVFS harvest meets): a förteckning table of title and
number. It keeps repealed and superseded entries and states their fate in the
title cell -- "(upphävt 20 oktober 2025)", "(ersätter 2019:1)", "(upphävt genom
2022:2)" -- which is parsed out of the title rather than left in it. A live
entry links a per-document page carrying the publication date, Konkurrensverkets
own ingress and the PDF; a repealed one usually links nothing, and is stored
from the förteckning row alone.

**Skatteverket** (www4.skatteverket.se/rattsligvagledning): the seventh, and
the odd one out on every axis -- 2,614 documents, no PDFs, no series number, and
an F5/Shape JavaScript challenge in front of the lot. Its register and page
semantics live in `skv.py`; what is here is the walk that drives them over the
detached headful-Chrome transport. That transport is serial and owns the
process-global DISPLAY, so this agency is kept off the default sweep and run on
its own schedule by ``lagen rs browser-download``.

Stored per ställningstagande under ``site/data/downloaded/rs/{org}/``: a
``<slug>.json`` record and the document -- ``<slug>.pdf`` for six agencies,
``<slug>.html`` for Skatteverket, whose page *is* the document.
"""

import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..lib import compress
from ..lib.browser import DetachedChrome, IncompleteNavigation, WafRejected
from ..lib.harvest import (
    dispatch_scopes,
    page_path,
    pdf_path,
    select_pending,
    walk_records,
)
from ..lib.net import BROWSER_UA as USER_AGENT
from ..lib.net import make_http2_session, make_session, mount_aia_chain, request
from ..lib.pdftext import pdf_first_page_text
from ..lib.util import (
    Reporter,
    document_extension,
    element_text,
    href,
    normalize_space,
    swedish_date,
)
from . import skv
from .agencies import BROWSER_ORGS, BY_ORG, DEFAULT_ORGS, number_slug

# --------------------------------------------------------------------------
# per-agency constants
# --------------------------------------------------------------------------

IMY_BASE = "https://www.imy.se"
RE_IMY_NUMBER = re.compile(r"IMYRS\s*(\d{4}:\d+)")
# where the page stops being about *this* statement and starts being about the
# series: IMY closes every publication page with the same two blocks
IMY_BOILERPLATE = "om imy:s rättsliga ställningstaganden"

FI_BASE = "https://www.fi.se"
RE_FI_NUMBER = re.compile(r"^\d{4}:\d+$")
# FI's Status column, as its own words map onto the model's two states. It is
# the only listing that states currency as a *field*, so this is the one place a
# remote string decides whether a document reads as the agency's current
# position -- which is why it is mapped rather than passed through, and why an
# unrecognised value stops the harvest instead of defaulting to "gällande".
FI_STATUS = {"gällande": "gällande", "upphävt": "upphävt", "upphävd": "upphävt"}

FK_BASE = "https://www.forsakringskassan.se"
# the listing's own number, kept only to name a PDF whose header carries none
RE_FK_LISTED = re.compile(
    r"\(\s*Rättsligt(?:\s+kompletterande)?\s+ställningstagande\s*\(?\s*"
    r"(\d{4})\s*[:/]\s*(\d{1,3})\s*\)?\s*\)?\s*$", re.I)
RE_FK_SERIENUMMER = re.compile(r"(\d{4}):(\d{1,3})\b")

KFM_BASE = "https://kronofogden.se"
# "1/24", "1/23/VER", "7/16/Skusan" -- löpnummer/år with a verksamhets suffix
RE_KFM_NUMBER = re.compile(r"\b(\d{1,3}/\d{2}(?:/[A-Za-zÅÄÖåäö]+)?)\b")

MIGR_HOST = "lifos.migrationsverket.se"
MIGR_BASE = "https://" + MIGR_HOST
MIGR_SEARCH = MIGR_BASE + "/sokning/detaljerad-sokning.html"
MIGR_SUBJECT = '"Rättsliga ställningstaganden och kommentarer" '
MIGR_DOC = MIGR_BASE + "/dokument"
# "… - RS/028/2021 (version 3.0)" / "RK/003/2026" -- the number Migrationsverket
# gives a ställningstagande (RS) or a rättslig kommentar (RK)
RE_MIGR_NUMBER = re.compile(r"\b(R[SK]/\d{2,3}/\d{4})\b")
RE_MIGR_KOMMENTAR = re.compile(r"^Rättslig\s+kommentar\b", re.I)
RE_MIGR_VERSION = re.compile(r"\(version\s+([\d.]+)\)")
RE_MIGR_SUMMARY_ID = re.compile(r"documentSummaryId=(\d+)")
# the only trace an in-place revision leaves of the text it replaced
RE_MIGR_PREVIOUS = re.compile(
    r"ersätter tidigare version som fastställdes\s+(\d{4}-\d{2}-\d{2})", re.I)
# the heading's own framing, which the record carries as fields instead: the
# document kind it opens with and the "- RS/028/2021 (version 3.0)" it closes on
RE_MIGR_TITLE_LEAD = re.compile(
    r"^Rättslig[ta]?\s+(?:ställningstagande|kommentar)\.?\s*", re.I)
RE_MIGR_TITLE_TAIL = re.compile(
    r"\s*[-–]?\s*R[SK]/\d{2,3}/\d{4}\s*(?:\(version\s+[\d.]+\))?\s*$"
    r"|\s*\(version\s+[\d.]+\)\s*$")   # ... or the version alone, where the
                                       # index states no number (`migr_number`)

KKV_BASE = "https://www.konkurrensverket.se"
RE_KKV_NUMBER = re.compile(r"^\d{4}:\d+$")
# what a förteckning title cell says about a statement's fate, in the agency's
# own words -- parsed out of the title so the listing's heading reads as a title
RE_KKV_UPPHAVD = re.compile(r"\(\s*upphävt\s+(?:genom\s+(\d{4}:\d+)|([^)]+))\)", re.I)
RE_KKV_ERSATTER = re.compile(r"\(\s*ersätter\s+(\d{4}:\d+)\s*\)", re.I)
RE_KKV_PUBLICERAT = re.compile(r"Publicerat\s+(\d{1,2}\s+\w+\s+\d{4})")

# how far after a header label its value can sit once poppler has flattened the
# column layout (see `labelled_value`) -- wide enough for the other two columns'
# labels and values, far short of the title below the block
LABEL_WINDOW = 120


# --------------------------------------------------------------------------
# identity, and the shared record walk
# --------------------------------------------------------------------------

def basefile(org, nummer):
    """The harvest basefile of one ställningstagande ("fk/2025:01")."""
    return "%s/%s" % (org, number_slug(nummer))


def body_path(root, bf):
    """Where one ställningstagande's document is stored: the PDF six agencies
    published it as, or -- for the one that publishes web pages -- the page
    itself. Read off `agencies.REGISTRY` rather than branched on the org, so a
    second page-publishing agency is a flag rather than a code change."""
    return (page_path if BY_ORG[bf.split("/", 1)[0]].page_body
            else pdf_path)(root, bf)


def _walk(root, records, session, delay, full, limit, scope, fetch=True,
          only=None):
    """Store a listing's records and the documents they name, through the shared
    record walk (`lib.harvest.walk_records`) with **no watermark**: these
    listings are single pages (or, for Lifos, walked whole before this point),
    so there is no depth to stop short of -- every run visits every entry and
    writes what moved. This only says how a ställningstagande's PDF is fetched;
    everything else about the walk is the shared one.

    A record naming no ``dokument_url`` gets no fetch and is stored on its own:
    that is a repealed Konkurrensverket entry, a förteckning row whose document
    the agency has withdrawn, which is a register entry rather than a failure.

    ``fetch=False`` is Försäkringskassans route, where *every* document had to be
    fetched earlier -- the number that names it is printed inside it
    (`self_named_document`) -- and is already on disk. Migrationsverket looks
    like that route but is not: only the two entries whose index row states no
    RS/RK number go through `self_named_document`, so the other ~100 have never
    been fetched when they arrive here and must be fetched like anyone else. The
    shared walk leaves an already-stored PDF untouched, so the two that were
    fetched early are not refetched."""
    def body(record):
        url = record.get("dokument_url")
        if not (fetch and url):
            return None
        return lambda: request(session, "GET", url, timeout=180).content

    return walk_records(
        root, select_pending([(r, body(r)) for r in records], only,
                             "the listing carries no ställningstagande %s"),
        delay=delay, full=full, limit=limit, scope=scope)


# --------------------------------------------------------------------------
# IMY
# --------------------------------------------------------------------------


def imy_parse_listing(html_text):
    """The listing's entries: {titel, nummer, url} per info block. Pure over the
    HTML so the rules are testable without network."""
    soup = BeautifulSoup(html_text, "html.parser")
    main = soup.find("div", class_="imy-contentpage__main-content")
    assert main is not None, "imy.se ställningstagande listing has no main content"
    items = []
    for block in main.find_all("div", class_="imy-info-block"):
        heading = block.find(class_="imy-info-block__heading")
        anchor = block.find("a", href=True)
        assert heading is not None and anchor is not None, \
            "imy.se ställningstagande block has no heading or link"
        number = RE_IMY_NUMBER.search(element_text(anchor))
        assert number, ("imy.se ställningstagande %r names no IMYRS number"
                        % element_text(heading))
        items.append({"titel": element_text(heading), "nummer": number.group(1),
                      "url": urljoin(IMY_BASE, href(anchor))})
    return items


def imy_parse_page(html_text, url):
    """A publication page -> {sammanfattning, dokument_url}. IMY's own summary is
    the run of paragraphs above the "Om IMY:s rättsliga ställningstaganden"
    heading, which opens the boilerplate about the series; the PDF linked below
    it is the document."""
    soup = BeautifulSoup(html_text, "html.parser")
    main = soup.find("div", class_="imy-contentpage__main-content")
    assert main is not None, "imy.se ställningstagande page has no main content"
    summary = []
    for el in main.find_all(["h2", "h3", "p"]):
        if el.name != "p":
            if element_text(el).lower().startswith(IMY_BOILERPLATE):
                break
            continue
        text = element_text(el)
        if text:
            summary.append(text)
    document = next((urljoin(url, href(a)) for a in main.find_all("a", href=True)
                     if href(a).lower().endswith(".pdf")), None)
    return {"sammanfattning": " ".join(summary) or None,
            "dokument_url": document}


def imy_sync(root, full=False, only=None, limit=None, delay=0.5):
    session = make_session(USER_AGENT)
    records = []
    for item in imy_parse_listing(
            request(session, "GET", BY_ORG["imy"].listing, timeout=120).text):
        # the listing links through a /link/<guid>.aspx redirect; requests
        # follows it, and the resolved url is the page a reader is sent to
        response = request(session, "GET", item["url"], timeout=120)
        time.sleep(delay)
        records.append({"basefile": basefile("imy", item["nummer"]), "org": "imy",
                        "nummer": item["nummer"], "titel": item["titel"],
                        "source_url": str(response.url),
                        **imy_parse_page(response.text, str(response.url))})
    return _walk(root, records, session, delay, full, limit, "imy", only=only)


# --------------------------------------------------------------------------
# Finansinspektionen
# --------------------------------------------------------------------------

def fi_parse_listing(html_text):
    """FI's förteckning -> {nummer, titel, beslutsdatum, status, dokument_url}
    per row. The table is hand-authored, so the columns are read positionally
    (Nummer, Titel, Beslutsdatum, Status) and the header row -- the one whose
    first cell is not a number -- is skipped. Pure over the HTML."""
    soup = BeautifulSoup(html_text, "html.parser")
    content = soup.find("div", class_="editor-content")
    assert content is not None, "fi.se ställningstagande page has no editor content"
    items = []
    for row in content.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 4 or not RE_FI_NUMBER.match(
                normalize_space(cells[0].get_text(" ", strip=True))):
            continue
        anchor = cells[1].find("a", href=True)
        items.append({
            "nummer": normalize_space(cells[0].get_text(" ", strip=True)),
            "titel": normalize_space(cells[1].get_text(" ", strip=True)),
            "beslutsdatum": normalize_space(cells[2].get_text(" ", strip=True)),
            "status": fi_status(normalize_space(cells[3].get_text(" ", strip=True))),
            "dokument_url": urljoin(FI_BASE, href(anchor)) if anchor else None})
    assert items, "fi.se förteckning parsed to no rows -- page structure changed?"
    return items


def fi_status(cell):
    """FI's Status column as the model's own vocabulary.

    A repealed ställningstagande is dropped from fi.se but stays in this
    förteckning, so this cell is the only place its withdrawal is recorded --
    and `render.render_rs` and `labels._rs` both branch on the model's exact
    ``"upphävt"``. FI writes the column by hand, so a value neither of its two
    known words raises: reading it as "gällande" would publish a withdrawn
    statement as the agency's current position, which is the one mistake this
    field exists to prevent."""
    status = FI_STATUS.get(cell.strip().lower())
    if status is None:
        raise ValueError(
            "fi.se förteckning states an unknown Status %r -- map it in "
            "FI_STATUS before a withdrawn ställningstagande can read as "
            "current" % cell)
    return status


def fi_sync(root, full=False, only=None, limit=None, delay=0.5):
    session = make_session(USER_AGENT)
    records = [{"basefile": basefile("fi", item["nummer"]), "org": "fi",
                "source_url": BY_ORG["fi"].listing, **item}
               for item in fi_parse_listing(
                   request(session, "GET", BY_ORG["fi"].listing, timeout=120).text)]
    return _walk(root, records, session, delay, full, limit, "fi", only=only)


# --------------------------------------------------------------------------
# Försäkringskassan
# --------------------------------------------------------------------------

def fk_parse_listing(html_text):
    """Försäkringskassans listing -> {arsgrupp, titel, nummer, dokument_url} per
    entry, in page order. Each year is an ``h2`` heading followed by file widgets
    whose links carry the title and, in a trailing parenthesis, the listing's own
    number. Pure over the HTML."""
    soup = BeautifulSoup(html_text, "html.parser")
    items, year = [], None
    for el in soup.find_all(["h2", "a"]):
        if el.name == "h2":
            text = normalize_space(el.get_text(" ", strip=True))
            if re.fullmatch(r"\d{4}", text):
                year = text
            continue
        if year is None or not href(el).startswith("/download"):
            continue
        # the widget appends its own screen-reader trailer to every link text
        text = normalize_space(el.get_text(" ", strip=True)).replace(
            "Pdf, öppnas i nytt fönster.", "").strip()
        listed = RE_FK_LISTED.search(text)
        assert listed, ("forsakringskassan.se lists %r with no ställningstagande "
                        "number" % text)
        items.append({"arsgrupp": year,
                      "titel": normalize_space(RE_FK_LISTED.sub("", text)),
                      "nummer": "%s:%02d" % (listed.group(1), int(listed.group(2))),
                      "dokument_url": urljoin(FK_BASE, href(el))})
    assert items, "forsakringskassan.se listing parsed to no entries"
    return items


def labelled_value(text, label, pattern, window=LABEL_WINDOW):
    """The first value matching `pattern` within `window` characters after
    `label` in a page's flattened text, or None.

    These header blocks are laid out as two- and three-column tables, and
    poppler flattens a table as either "label label value value" or "label value
    label value" depending on how the columns fall -- so a value is never
    reliably *adjacent* to its label, but it is reliably *after* it and within
    the header. Anchoring on the label and then matching on the value's own
    shape reads both flattenings, and the window keeps the search inside the
    header, where the title's own numbers cannot reach it."""
    at = text.find(label)
    if at < 0:
        return None
    return pattern.search(text, at + len(label), at + len(label) + window)


def fk_serienummer(text):
    """The Serienummer a Försäkringskassan ställningstagande prints on page 1,
    which is what names the document -- the listing's number is the same one
    retyped, and at least once retyped wrong. Normalized to a two-digit
    löpnummer, which is how the series is written everywhere except in the
    handful of PDFs that drop the leading zero ("2020:6" for 2020:06)."""
    match = labelled_value(text, "Serienummer", RE_FK_SERIENUMMER)
    return "%s:%02d" % (match.group(1), int(match.group(2))) if match else None


def stored_numbers(root, org, key):
    """The number each already-harvested record of `org` was filed under, keyed
    on the record field `key`. It is what makes an incremental run cheap for the
    two agencies whose number lives in the *document*: without it every run
    would re-download every PDF just to re-read a number that has not moved."""
    return {record[key]: record["nummer"]
            for path in compress.glob(Path(root) / org, "*.json")
            for record in [compress.read_json(path)]
            if key in record}


def self_named_document(root, org, url, session, delay, extract, listed=None):
    """Fetch a document whose *number* the document itself states, file it under
    that number and return the number -- None where neither the document nor the
    listing names one, or where the agency served something that is not a PDF.

    The PDF is read before it is stored, because the number is what names the
    file: storing it under a provisional name first would leave a file on disk
    that no record claims. Two agencies need this reading. Försäkringskassan
    always does -- its listing retypes the Serienummer, once wrongly, so
    `listed` is what the listing said and the printed number wins over it -- and
    Lifos does for the two documents whose index entry omits the RS/RK number
    their PDF prints, where there is no listed number to fall back to."""
    response = request(session, "GET", url, timeout=180)
    time.sleep(delay)
    if document_extension(response.content) != ".pdf":
        print("rs: %s: %s served a non-PDF body, skipping" % (org, url),
              flush=True)
        return None
    with tempfile.NamedTemporaryFile(suffix=".pdf") as staged:
        staged.write(response.content)
        staged.flush()
        printed = extract(pdf_first_page_text(staged.name))
    if printed and listed and printed != listed:
        print("%s: %s is listed as %s but names itself %s -- filed as the latter"
              % (org, url.rsplit("/", 1)[-1][:60], listed, printed), flush=True)
    nummer = printed or listed
    if nummer:
        compress.write_download(pdf_path(root, basefile(org, nummer)),
                                response.content)
    return nummer


def fk_sync(root, full=False, only=None, limit=None, delay=0.5):
    """Harvest Försäkringskassans series. A ställningstagande not yet on disk is
    fetched and filed under the Serienummer its PDF prints -- the document naming
    itself beats the listing retyping it -- and one already harvested keeps the
    number it was filed under, so the run costs one request rather than 108."""
    session = make_session(USER_AGENT)
    items = fk_parse_listing(
        request(session, "GET", BY_ORG["fk"].listing, timeout=120).text)
    if limit:
        items = items[:limit]
    known = {} if full else stored_numbers(root, "fk", "dokument_url")
    records, rep = [], Reporter()
    for seen, item in enumerate(items, 1):
        nummer = known.get(item["dokument_url"]) or self_named_document(
            root, "fk", item["dokument_url"], session, delay, fk_serienummer,
            listed=item["nummer"])
        if nummer:
            records.append({"basefile": basefile("fk", nummer), "org": "fk",
                            "nummer": nummer, "titel": item["titel"],
                            "arsgrupp": item["arsgrupp"],
                            "source_url": BY_ORG["fk"].listing,
                            "dokument_url": item["dokument_url"]})
        rep.update(seen, len(items), scope="fk")
    rep.done()
    # the PDFs are already stored under their resolved names, so only the
    # records remain to be written
    return _walk(root, records, session, delay, full, None, "fk", fetch=False,
                 only=only)


# --------------------------------------------------------------------------
# Kronofogden
# --------------------------------------------------------------------------

def kfm_parse_listing(html_text):
    """Kronofogdens document list -> ({arsgrupp, titel, nummer, dokument_url}
    entries, unnumbered titles). An entry is an ``li`` whose link carries the
    title and, in a trailing ``span``, the number. One entry today names no
    number at all; it has no identity to be filed under and is returned
    separately for the caller to report. Pure over the HTML."""
    soup = BeautifulSoup(html_text, "html.parser")
    listing = soup.find("ul", class_="iw-kfm-document-list")
    assert listing is not None, "kronofogden.se page has no iw-kfm-document-list"
    items, unnumbered, year = [], [], None
    for el in listing.find_all(["p", "li"]):
        if el.name == "p":
            text = normalize_space(el.get_text(" ", strip=True))
            if re.fullmatch(r"\d{4}", text):
                year = text
            continue
        anchor = el.find("a", href=True)
        assert anchor is not None, "kronofogden.se list entry has no link"
        description = anchor.find("span")
        detail = normalize_space(description.get_text(" ", strip=True)) \
            if description else ""
        if description:
            description.extract()      # so the trailer leaves the title
        titel = normalize_space(anchor.get_text(" ", strip=True))
        number = RE_KFM_NUMBER.search(detail)
        if not number:
            unnumbered.append(titel)
            continue
        items.append({"arsgrupp": year, "titel": titel,
                      "nummer": number.group(1),
                      "dokument_url": urljoin(KFM_BASE, href(anchor))})
    assert items, "kronofogden.se listing parsed to no entries"
    return items, unnumbered


def kfm_sync(root, full=False, only=None, limit=None, delay=0.5):
    """Harvest Kronofogdens ställningstaganden. The listing carries the number
    and the title; the beslutsdatum and the diarienummer live only in the PDF's
    own header table and are read at parse, the identity not depending on them."""
    session = make_session(USER_AGENT)
    items, unnumbered = kfm_parse_listing(
        request(session, "GET", BY_ORG["kfm"].listing, timeout=120).text)
    if unnumbered:
        print("kfm: %d ställningstagande(n) name no number and cannot be filed: %s"
              % (len(unnumbered), ", ".join(unnumbered)), flush=True)
    records = [{"basefile": basefile("kfm", item["nummer"]), "org": "kfm",
                "nummer": item["nummer"], "titel": item["titel"],
                "arsgrupp": item["arsgrupp"],
                "source_url": BY_ORG["kfm"].listing,
                "dokument_url": item["dokument_url"]} for item in items]
    return _walk(root, records, session, delay, full, limit, "kfm", only=only)


# --------------------------------------------------------------------------
# Migrationsverket (Lifos)
# --------------------------------------------------------------------------

def migr_session():
    """A session that verifies lifos.migrationsverket.se against an
    AIA-completed trust bundle: the site sends only its leaf certificate, so the
    intermediate has to be fetched from the pointer the leaf itself carries
    (`lib.net.mount_aia_chain`)."""
    session = make_session(USER_AGENT)
    mount_aia_chain(session, MIGR_BASE + "/", MIGR_HOST)
    return session


def migr_parse_results(html_text):
    """One search page's hits: the Lifos documentSummaryId of each result, in
    page order and deduplicated (every hit is linked twice, by its title and by
    its thumbnail). Pure over the HTML."""
    soup = BeautifulSoup(html_text, "html.parser")
    ids = []
    for anchor in soup.find_all("a", href=True):
        match = RE_MIGR_SUMMARY_ID.search(href(anchor))
        if match and match.group(1) not in ids:
            ids.append(match.group(1))
    return ids


def _migr_metadata(soup):
    """The Dokumentinformation block as a {label: value} map. Lifos sets it as
    a run of paired label/value divs rather than a table, so the pairing is
    positional -- but the classes naming the two columns are its own and stable."""
    block = soup.find(id="metadataDisplayInformation")
    assert block is not None, "lifos document page has no Dokumentinformation"
    labels = block.find_all("div", class_="metadataDisplayLeftColumn")
    values = block.find_all("div", class_="metadataDisplayRightColumn")
    return {normalize_space(label.get_text(" ", strip=True)).rstrip(":"):
            normalize_space(value.get_text(" ", strip=True))
            for label, value in zip(labels, values, strict=True)}


def migr_parse_document(html_text):
    """A Lifos document page -> {titel, nummer, version, foregaende_version,
    beslutsdatum, dokumentnr, nyckelord, dokument_url}. The DocumentHeader
    carries the title with the number and the version in it; the
    Dokumentinformation block beside it the upphovsdatum and the Lifos document
    number, and the summary block the date the version this one replaces was
    fastställd -- Migrationsverket revises a ställningstagande in place, so that
    is the only trace the earlier text leaves.

    ``nummer`` is None for the two documents whose index entry omits the RS/RK
    number their PDF prints (`migr_sync` then reads it out of the document, the
    Försäkringskassan route). Pure over the HTML."""
    soup = BeautifulSoup(html_text, "html.parser")
    heading = soup.find("div", class_="DocumentHeader")
    assert heading is not None, "lifos document page has no DocumentHeader"
    heading_text = normalize_space(heading.get_text(" ", strip=True))
    number = RE_MIGR_NUMBER.search(heading_text)
    version = RE_MIGR_VERSION.search(heading_text)
    # the heading states the document kind, the subject and the number in one
    # line ("Rättsligt ställningstagande. Konfliktbedömning - RS/001/2025
    # (version 2.0)"); the identity and the version are fields of their own, so
    # what stays as the title is the subject between them
    titel = normalize_space(RE_MIGR_TITLE_TAIL.sub(
        "", RE_MIGR_TITLE_LEAD.sub("", heading_text)))
    metadata = _migr_metadata(soup)
    summary = soup.find(id="documentViewerSummary")
    previous = RE_MIGR_PREVIOUS.search(
        normalize_space(summary.get_text(" ", strip=True)) if summary else "")
    keywords = soup.find(id="metadataDisplaySubjectword")
    document = next((urljoin(MIGR_DOC, href(a)) for a in soup.find_all("a", href=True)
                     if "documentAttachmentId=" in href(a)), None)
    return {"titel": titel, "nummer": number.group(1) if number else None,
            # a rättslig kommentar is Migrationsverkets reading of one named
            # avgörande, published in the same numbered series; the heading says
            # which this is even where it omits the number
            "doktyp": "kommentar" if RE_MIGR_KOMMENTAR.match(heading_text)
            else "stallningstagande",
            "version": version.group(1) if version else None,
            "foregaende_version": previous.group(1) if previous else None,
            "beslutsdatum": metadata.get("Upphovsdat") or None,
            "dokumentnr": metadata.get("Dokumentnr") or None,
            # the ämnesord minus the one the harvest itself filtered on -- it
            # says which Lifos collection the document is in, not what it is about
            "nyckelord": [k.strip() for k in
                          normalize_space(keywords.get_text(" ", strip=True)).split(",")
                          if k.strip() and k.strip() != MIGR_SUBJECT.strip('" ')]
            if keywords else [],
            "dokument_url": document}


def migr_search(session, page):
    return request(session, "GET", MIGR_SEARCH, timeout=120,
                   params={"fullTextSearchType": "allWords",
                           "subjectWords": MIGR_SUBJECT,
                           "dateFieldName": "disabled", "page": str(page)}).text


def migr_listing(session, delay):
    """Every documentSummaryId the filtered search returns. ``page`` is a true
    offset and the last page is the one that repeats no new id, which is the
    only stop signal that does not depend on parsing the hit count out of the
    result banner."""
    ids, page = [], 1
    while True:
        found = migr_parse_results(migr_search(session, page))
        fresh = [i for i in found if i not in ids]
        if not fresh:
            return ids
        ids.extend(fresh)
        page += 1
        time.sleep(delay)


def migr_number(text):
    """The RS/RK number a Migrationsverket document prints beside its title,
    for the two whose Lifos index entry omits it."""
    match = RE_MIGR_NUMBER.search(text)
    return match.group(1) if match else None


def migr_current(records):
    """One record per number, the current version of it.

    Migrationsverket revises a ställningstagande *in place* -- the number stays
    and the version rises -- but Lifos keeps the superseded entry in its index
    beside the new one, so four numbers arrive twice today (RS/021/2020 as
    version 2.0 from 2023 and 4.0 from 2026, and three like it). One number is
    one document, so the later beslutsdatum wins; the search is relevance-
    ordered, so taking whichever came last would pick between them at random."""
    current = {}
    for record in records:
        held = current.get(record["nummer"])
        if held is None or (record["beslutsdatum"] or "") > (held["beslutsdatum"] or ""):
            current[record["nummer"]] = record
    return list(current.values())


def migr_sync(root, full=False, only=None, limit=None, delay=0.5):
    """Harvest Migrationsverkets series through Lifos. Two of the 104 index
    entries state no RS/RK number; their PDFs print one, so those are read out
    of the document (the Försäkringskassan route) and the number a record was
    filed under is remembered so a later run does not refetch them. A document
    that names no number anywhere has no identity to be filed under and is
    reported rather than invented. Four numbers arrive twice, an entry and the
    revision that replaced it, and `migr_current` keeps the later."""
    session = migr_session()
    ids = migr_listing(session, delay)
    if limit:
        ids = ids[:limit]
    known = {} if full else stored_numbers(root, "migr", "dokumentnr")
    records, orphans, rep = [], [], Reporter()
    for seen, summary_id in enumerate(ids, 1):
        page = request(session, "GET", MIGR_DOC, timeout=120,
                       params={"documentSummaryId": summary_id}).text
        time.sleep(delay)
        document = migr_parse_document(page)
        nummer = (document["nummer"] or known.get(summary_id)
                  or (self_named_document(root, "migr", document["dokument_url"],
                                          session, delay, migr_number)
                      if document["dokument_url"] else None))
        if nummer:
            records.append({**document, "nummer": nummer,
                            "basefile": basefile("migr", nummer), "org": "migr",
                            "source_url": "%s?documentSummaryId=%s"
                            % (MIGR_DOC, summary_id)})
        else:
            orphans.append("%s (%s)" % (document["titel"], summary_id))
        rep.update(seen, len(ids), scope="migr")
    rep.done()
    if orphans:
        print("migr: %d document(s) name no RS/RK number and cannot be filed: %s"
              % (len(orphans), ", ".join(orphans)), flush=True)
    return _walk(root, migr_current(records), session, delay, full, None,
                 "migr", only=only)


# --------------------------------------------------------------------------
# Konkurrensverket
# --------------------------------------------------------------------------

def kkv_parse_listing(html_text):
    """Konkurrensverkets förteckning -> {nummer, titel, status, upphavd,
    ersatt_av, ersatter, url} per row. The title cell states a statement's fate
    in the agency's own parenthetical -- "(upphävt 20 oktober 2025)", "(upphävt
    genom 2022:2)", "(ersätter 2019:1)" -- which is lifted out of the title into
    fields of its own. A live row links its document page; a repealed one usually
    links nothing. Pure over the HTML."""
    soup = BeautifulSoup(html_text, "html.parser")
    items = []
    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        number_cell = cells[-1]
        number = normalize_space(number_cell.get_text(" ", strip=True))
        if not RE_KKV_NUMBER.match(number):
            continue
        # the fate parentheticals sit in the title cell -- both the repeal
        # ("(upphävt 20 oktober 2025)", "(upphävt genom 2022:2)") and the
        # supersession ("(ersätter 2019:1)") -- so they are lifted out of the
        # title and the title itself is what is left
        titel = normalize_space(cells[0].get_text(" ", strip=True))
        upphavd = RE_KKV_UPPHAVD.search(titel)
        ersatter = RE_KKV_ERSATTER.search(titel)
        anchor = number_cell.find("a", href=True)
        items.append({
            "nummer": number,
            "titel": normalize_space(RE_KKV_ERSATTER.sub(
                "", RE_KKV_UPPHAVD.sub("", titel))),
            "status": "upphävt" if upphavd else "gällande",
            "upphavd": normalize_space(upphavd.group(2)) if upphavd
            and upphavd.group(2) else None,
            "ersatt_av": upphavd.group(1) if upphavd else None,
            "ersatter": ersatter.group(1) if ersatter else None,
            "url": urljoin(KKV_BASE, href(anchor)) if anchor else None})
    assert items, "konkurrensverket.se förteckning parsed to no rows"
    return items


def kkv_parse_page(html_text, url):
    """A Konkurrensverket document page -> {beslutsdatum, sammanfattning,
    dokument_url}: the publication date, the agency's own ingress and the PDF the
    page links.

    The page is a React build whose class names are generated per deploy, so
    nothing here is selected by class: the date is the "Publicerat 28 maj 2025"
    line, the ingress is the paragraph set wholly in ``<strong>`` (the lead
    every one of these pages opens with -- the same signal `avg.parse` reads a
    JK section heading by), and the document is the one PDF under the agency's
    own ``/globalassets/`` asset path. Pure over the HTML."""
    soup = BeautifulSoup(html_text, "html.parser")
    # the reading scope, narrowest first: the page's <main> where it marks one,
    # else its body -- and the document itself when neither is present, which is
    # a fragment rather than a page
    main = soup.find("main") or soup.body or soup
    publicerat = RE_KKV_PUBLICERAT.search(
        normalize_space(main.get_text(" ", strip=True)))
    document = next((urljoin(url, href(a)) for a in main.find_all("a", href=True)
                     if href(a).lower().endswith(".pdf")
                     and "/globalassets/" in href(a)), None)
    ingress = next((normalize_space(p.get_text(" ", strip=True))
                    for p in main.find_all("p")
                    for strong in [p.find("strong")]
                    if strong is not None
                    and normalize_space(strong.get_text(" ", strip=True))
                    == normalize_space(p.get_text(" ", strip=True))), None)
    return {"beslutsdatum": swedish_date(publicerat.group(1)) if publicerat
            else None,
            "sammanfattning": ingress, "dokument_url": document}


def kkv_sync(root, full=False, only=None, limit=None, delay=0.5):
    """Harvest Konkurrensverkets förteckning. The agency sits behind the same
    HTTP/2-only Cloudflare front the KKVFS föreskrift harvest meets, so the
    session is the httpx one."""
    session = make_http2_session(USER_AGENT)
    records = []
    for item in kkv_parse_listing(
            request(session, "GET", BY_ORG["kkv"].listing, timeout=120).text):
        page = {}
        if item["url"]:
            page = kkv_parse_page(
                request(session, "GET", item["url"], timeout=120).text,
                item["url"])
            time.sleep(delay)
        records.append({"basefile": basefile("kkv", item["nummer"]), "org": "kkv",
                        "source_url": item["url"] or BY_ORG["kkv"].listing,
                        **{k: v for k, v in item.items() if k != "url"}, **page})
    return _walk(root, records, session, delay, full, limit, "kkv", only=only)


# --------------------------------------------------------------------------
# Skatteverket (rättslig vägledning, behind the F5/Shape challenge)
# --------------------------------------------------------------------------

# The register renders 2,619 rows and is slow even for a real browser, so it is
# given minutes. A document page is done in a few seconds -- but the *pace*, not
# the page, is what the settle has to respect here: measured against the live
# site, some 30 navigations at 5-second spacing trip the front's rate defence,
# after which every navigation is rejected for a good while whatever profile
# asks. So a document waits far longer than it needs to, which is affordable
# precisely because this agency runs on a weekly schedule of its own: 20 seconds
# apiece is roughly 15 hours for the whole register once, and a few minutes for
# what a week adds.
SKV_INDEX_SETTLE = 180.0
SKV_PAGE_SETTLE = 20.0
# Once the front starts rejecting, it keeps rejecting: knocking through the
# remaining thousands of documents would be both useless and rude. The run stops
# and says so, and because a stored record is only ever written with its page,
# the next run resumes at exactly the document this one stopped on.
SKV_BLOCK_LIMIT = 5


def until_blocked(pending, blocked, limit=SKV_BLOCK_LIMIT, log=print):
    """The pending entries, up to the point `blocked()` says the site has
    stopped answering -- the walk then simply runs out of entries.

    `blocked` reports how many navigations in a row the front has refused or
    left unfinished. A
    run of them means it has closed, and knocking through the thousands of
    documents below would be both useless and rude. Nothing is stranded by
    stopping: a record is only ever stored once its page is, so the next run
    resumes at exactly this document."""
    for entry in pending:
        if blocked() >= limit:
            log("skv: %d navigations rejected in a row -- Skatteverkets front "
                "has closed for now; stopping. Re-run `lagen rs "
                "browser-download` later and it resumes here." % blocked())
            return
        yield entry


def skv_verify(html_text):
    """Reject a page that is not a ställningstagande. `browser.html` has already
    ruled out a WAF rejection and an unfinished challenge; what is left to check
    is that this page carries the document -- so a store that reports a record
    written always has the text behind it."""
    if "referenceProperties" not in html_text or 'class="body' not in html_text:
        raise ValueError("served no ställningstagande page; record left unwritten")


def skv_sync(root, full=False, only=None, limit=None, delay=0.5):
    """Harvest Skatteverkets ställningstaganden through detached headful Chrome.

    One register navigation gives every document's identity, title, date,
    områden and currency (`skv.parse_index`); each document then costs one more
    navigation for the page that *is* its text. The register is walked whole
    every run and the shared record walk fetches only what moved, so a weekly
    run costs the register plus the handful of documents that moved -- but a
    first run is 2,614 paced navigations, some fifteen hours, which is why this
    agency has a command of its own. ``--limit N`` slices that backfill into
    runs; a resumed run skips whatever is already stored.

    `delay` is ignored: the browser's settle already paces every navigation, and
    sleeping on top of it would only make a long backfill longer."""
    profile = Path(root) / "skv" / ".browser-profile"
    blocked = 0
    with DetachedChrome(profile, settle=SKV_PAGE_SETTLE) as browser:
        records, unidentified = skv.parse_index(
            browser.html(skv.INDEX_URL, skv.INDEX_MARKER,
                         settle=SKV_INDEX_SETTLE))
        if unidentified:
            print("skv: %d register entr%s name no diarienummer and cannot be "
                  "filed: %s" % (len(unidentified),
                                 "y" if len(unidentified) == 1 else "ies",
                                 "; ".join(unidentified)), flush=True)

        def fetch(url):
            nonlocal blocked
            try:
                page = browser.html(url, skv.PAGE_MARKER)
            except (WafRejected, IncompleteNavigation):
                # counted, then re-raised: the walk still records this document
                # as failed and leaves it unstored, and the count is what tells
                # `until_blocked` the front has closed (rule:no-catch-log-continue
                # -- this handler fixes nothing, it only measures).
                # Both shapes count. A rejection is the front saying no; a run
                # of navigations that never complete is the same front holding
                # the challenge open, and a stop that watched only for the first
                # would spend fifteen hours failing every document on the second.
                blocked += 1
                raise
            blocked = 0
            return page

        pending = select_pending(
            [(r, (lambda url=r["source_url"]: fetch(url))) for r in records],
            only, "the register carries no ställningstagande %s")
        return walk_records(
            root, until_blocked(pending, lambda: blocked), delay=0, full=full,
            limit=limit, scope="skv", total=len(pending), document=page_path,
            verify=skv_verify, refetch_when_changed=True)


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

SYNC = {"imy": imy_sync, "fi": fi_sync, "fk": fk_sync, "kfm": kfm_sync,
        "migr": migr_sync, "kkv": kkv_sync, "skv": skv_sync}


def sync(root, scopes=None, full=False, only=None, limit=None, delay=0.5, jobs=1):
    """Download the named agencies' ställningstaganden. With no scopes named,
    the six ordinary HTTP agencies -- Skatteverket needs the serial headful
    browser and runs on its own schedule (`agencies.BROWSER_ORGS`), though
    naming it explicitly still harvests it. Returns {org: (seen, new)}."""
    return dispatch_scopes(root, scopes, SYNC, DEFAULT_ORGS, full=full,
                           only=only, limit=limit, delay=delay, jobs=jobs,
                           serial=BROWSER_ORGS, label="rs download")
