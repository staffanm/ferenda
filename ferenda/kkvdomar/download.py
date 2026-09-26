"""Harvester for Konkurrensverkets domstolsdatabas (information.konkurrensverket.se
/domar/): the courts' decisions in public procurement cases, as Konkurrensverket
collects and publishes them.

The database is one classic ASP search form. A POST to ``domar.asp`` with an
``inInstans`` filter returns ten rows per page, newest beslutsdatum first, and
``showpage=N`` pages through them. A row names the database's own case id, the
målnummer, the court, the ärendemening, the beslutsdatum and the two parties. The
case page (``arende.asp?id=N``) adds the ärendetyp, the outcome ("Avgörande"),
Konkurrensverkets optional kortreferat and the link to the decision PDF under
``/beslut/``. Only the kammarrätter are harvested (``inInstans=2``, 3,835 cases
from 2016 on when this was written); the other instanser use the same form.

Identity is the court's, not the database's: a case is filed under its court, its
first målnummer and its beslutsdatum -- the three facts `lib.casenaming.
verdict_uri` mints the published URI from. Konkurrensverket registers one
decision twice now and then -- 23 of the 3,835 rows name a decision another row
names, some of them a joined case listed once as a range ("1424-1426-21") and
once number by number -- and the court identity files those as one document.
The later registration is the one kept. Of 15 such pairs compared, 12 link
byte-identical PDFs, and in the other three the later one replaces a scan with
the born-digital decision (kst/4829-21, kgg/21-16) or removes a name the first
one printed (kgg/3272-23, ids 41717 and 41810).

The listing sorts by beslutsdatum, but a decision reaches the database 1 to 55
days after it was given (measured over 59 cases; the file name of the PDF carries
the upload time). So the walk keeps walking 60 days past the last harvest before
it trusts that it has caught up. A handful of decisions were uploaded more than a
year late (4 of those 59); a routine run cannot reach those, and a ``--force`` run
walks the whole listing again.

**Decisions an HFD referat supersedes.** Some of these decisions were appealed
to Högsta förvaltningsdomstolen, which then published the case as a referat or
a notis. The dv source holds those, and the kammarrätt decision is then left
out here (`superseded`). The referat does not say which kammarrätt decision it
reviewed -- none of the 40 procurement referat from 2017 on prints its
målnummer -- but HFD's own decision does, on its page 1 ("ÖVERKLAGAT AVGÖRANDE
Kammarrätten i Stockholms dom den 28 oktober 2019 i mål nr 5613-19"), and this
database carries those decisions too (instans 3, 1,139 rows). So the harvest
also walks the HFD listing, storing each row as a record, and reads page 1 of
the ones whose målnummer the dv corpus holds as a referat or a notis
(`datasets.CASENUMBERS`, the case-number snapshot dv writes). When this was
written 41 HFD rows matched, naming 36 kammarrätt decisions, 26 of them in the
listing (the rest are from before 2016). Three referat name nothing: HFD 2023
ref. 7's PDF starts on page 2, and HFD 2018 ref. 14 and ref. 67 have no PDF.
Their kammarrätt decisions stay published.

Stored per decision under ``site/data/downloaded/kkvdomar/{court}/``: a
``<slug>.json`` record (the listing row and the case page's fields) and the
decision ``<slug>.pdf``. A case without a PDF (14 of the 3,835) is stored as a
record only.
"""

import re
import time
from pathlib import Path

from bs4 import BeautifulSoup

from ..lib import compress, datasets
from ..lib.casenaming import COURT_URI_SLUG
from ..lib.errors import UpstreamChanged
from ..lib.harvest import (
    HarvestWatermark,
    ItemKey,
    pdf_path,
    select_one,
    verify_pdf,
    walk,
    write_record,
)
from ..lib.malnummer import COURT_PHRASES
from ..lib.net import HARVESTER_UA as USER_AGENT
from ..lib.net import make_session, request
from ..lib.pdftext import ocr_pdf, pdf_first_page_text
from ..lib.util import href, normalize_space, record_path, swedish_date

BASE = "https://information.konkurrensverket.se"
LISTING = BASE + "/domar/domar.asp"
CASE = BASE + "/domar/arende.asp?id=%s"
# the form's instans values: the kammarrätter, and Högsta förvaltningsdomstolen
# (walked only to find the kammarrätt decisions a referat supersedes)
INSTANS = "2"
HFD_INSTANS = "3"
HFD = "hfd"
# the form's own domstol values, for the --only search on one målnummer
DOMSTOL = {"kst": "24", "kgg": "25", "ksu": "26", "kjo": "27"}
# the database's pages declare ISO-8859-1 but are written in windows-1252: the
# en dash in "måndag – fredag" is a 1252 code point
ENCODING = "cp1252"
PAGE_LIMIT = 1000           # guard: 384 pages when this was written
# the first målnummer of a case, and its year: "3404-3409-22" (a range) is case
# 3404-22, "5067-26 5074-26" is case 5067-26
RE_MALNUMMER = re.compile(r"(\d{1,5})(?:\s?[-–—]\s?\d{1,5})?\s?[-–—]\s?(\d{2})(?!\d)")
# the three listing rows whose målnummer the database misprints, keyed by the
# database's case id, each read off the decision's own page 1
MALNUMMER = {"30246": "10381-18",     # listed as "190508"
             "24165": "961-16",       # listed as "961,16"
             "40895": "3285-22"}      # listed as "3285-222"
RE_CASE_ID = re.compile(r"arende\.asp\?id=(\d+)")
RE_PAGES = re.compile(r"Visar sida\s*(?:&nbsp;)?\s*<b>\d+</b> av <b>(\d+)</b>")
RE_ISODATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def court_code(domstol):
    """"Kammarrätten i Stockholm" -> "kst": the court's URI slug, the one
    `lib.casenaming` mints verdict URIs with. A court the citation tables do not
    know is a court this harvest was not built for."""
    codes = COURT_PHRASES.get(normalize_space(domstol).lower())
    if not codes:
        raise UpstreamChanged("kkvdomar: unknown court %r" % domstol)
    return COURT_URI_SLUG[codes[0]]


def basefile(row):
    """court/first målnummer/beslutsdatum -- "kst/6426-25/2026-03-12". A joined
    case lists every målnummer ("5067-26 5074-26 5077-5119-26"); the first one
    names it, the way the court's own page 1 does."""
    malnummer = RE_MALNUMMER.search(row["malnummer"])
    if malnummer is None or not RE_ISODATE.match(row["beslutsdatum"]):
        raise UpstreamChanged("kkvdomar: case %s has no målnummer or date (%r, %r)"
                              % (row["id"], row["malnummer"],
                                 row["beslutsdatum"]))
    return "%s/%s-%s/%s" % (court_code(row["domstol"]), malnummer.group(1),
                            malnummer.group(2), row["beslutsdatum"])


def record_json(root, bf):
    return record_path(root, bf.split("/", 1)[0], bf)


def body_path(root, bf):
    return pdf_path(root, bf)


def list_basefiles(root):
    return sorted(bf for court in sorted(DOMSTOL)
                  for bf in compress.list_basefiles(root, court))


def _text(response):
    return response.content.decode(ENCODING)


def listing_page(session, page, instans=INSTANS, **search):
    """One page of one instans' decisions, as HTML."""
    return _text(request(session, "POST", LISTING, timeout=60, data={
        "inInstans": instans, "inDomstol": "0", "inArendetyp": "0",
        "inDomslut": "0", "showpage": str(page), **search}))


def listing_rows(html):
    """A listing page -> its rows: {id, malnummer, domstol, arendemening,
    beslutsdatum, sokande, motpart}."""
    rows = []
    for link in BeautifulSoup(html, "html.parser").find_all(
            "a", href=RE_CASE_ID):
        tr = link.find_parent("tr")
        if tr is None:
            raise UpstreamChanged("kkvdomar: a case link outside a listing row")
        cells = [normalize_space(td.get_text(" ")) for td in tr.find_all("td")]
        if len(cells) != 6:
            raise UpstreamChanged("kkvdomar: a listing row has %d cells, not 6"
                                  % len(cells))
        case_id = RE_CASE_ID.search(href(link))
        assert case_id, "find_all matched the href on RE_CASE_ID"
        row = {"id": case_id.group(1),
               **dict(zip(("malnummer", "domstol", "arendemening",
                           "beslutsdatum", "sokande", "motpart"), cells,
                          strict=True))}
        row["malnummer"] = MALNUMMER.get(row["id"], row["malnummer"])
        rows.append(row)
    return rows


def page_count(html):
    match = RE_PAGES.search(html)
    if match is None:
        raise UpstreamChanged("kkvdomar: the listing states no page count")
    return int(match.group(1))


def enumerate_rows(session, delay, instans=INSTANS):
    """Every listing row, newest beslutsdatum first, fetched a page at a time so
    that a caught-up walk stops after the first few pages."""
    html = listing_page(session, 1, instans)
    pages = page_count(html)
    if pages > PAGE_LIMIT:
        raise UpstreamChanged("kkvdomar: the listing has %d pages, over the %d "
                              "this harvest expects" % (pages, PAGE_LIMIT))
    for page in range(1, pages + 1):
        if page > 1:
            time.sleep(delay)
            html = listing_page(session, page, instans)
        rows = listing_rows(html)
        if not rows:
            raise UpstreamChanged("kkvdomar: listing page %d parsed to no rows"
                                  % page)
        yield from rows


# the case page's labels, and the record key each is stored under. The listing
# already states the rest.
CASE_FIELDS = {"Instans": "instans", "Ärendetyp": "arendetyp",
               "Avgörande": "avgorande", "Kortreferat": "kortreferat"}


def case_fields(html):
    """The case page -> {instans, arendetyp, avgorande, kortreferat?,
    beslut_url?}. The page is a two-column table of "Label:" and value; the
    decision is the one link into ``/beslut/``."""
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for label in soup.find_all("td", class_="formlista2"):
        key = CASE_FIELDS.get(normalize_space(label.get_text()).rstrip(":"))
        value = label.find_next_sibling("td")
        if key and value is not None and normalize_space(value.get_text()):
            out[key] = normalize_space(value.get_text(" "))
    if "instans" not in out:
        raise UpstreamChanged("kkvdomar: the case page states no Instans")
    link = soup.find("a", href=re.compile(r"^/beslut/"))
    if link is not None:
        out["beslut_url"] = BASE + href(link)
    return out


def is_current(root, row):
    """Whether the store already holds this case from this registration or a
    later one. The database's ids rise with each registration, so a higher id
    under the same court identity is Konkurrensverkets replacement."""
    stored = compress.read_json(record_json(root, basefile(row)), default=None)
    return stored is not None and int(stored["id"]) >= int(row["id"])


def resolve(session, root, row, full=False, delay=0.3):
    """Store one case: the record and its PDF. A case already on disk is left
    alone -- a court decision does not change once published -- unless the run
    is ``--force`` or this row is a later registration of it."""
    bf = basefile(row)
    path = record_json(root, bf)
    if is_current(root, row) and not full:
        return False
    fields = case_fields(_text(request(session, "GET", CASE % row["id"],
                                       timeout=60)))
    record = {"basefile": bf, **row, **fields,
              "source_url": CASE % row["id"]}
    if "beslut_url" in record:
        time.sleep(delay)
        # the file name holds spaces for a joined case ("5355-25 5356-25.pdf")
        data = request(session, "GET", record["beslut_url"].replace(" ", "%20"),
                       timeout=180).content
        verify_pdf(data)
        compress.write_download(body_path(root, bf), data)
    write_record(path, record)
    return True


# --------------------------------------------------------------------------
# the kammarrätt decisions an HFD referat supersedes
# --------------------------------------------------------------------------

# the appealed decision as HFD's page 1 names it. OCR has printed "i -mål"
RE_OVERKLAGAT = re.compile(
    r"ÖVERKLAGA(?:T|DE) AVGÖRANDEN?\s+(Kammarrätten i \w+?)s? (?:dom|beslut)\w*"
    r" (?:den )?(\d{1,2} \w+ \d{4}) i\s?-?mål(?:en)? (?:nr )?"
    r"(\d{1,5}(?:\s?[-–—]\s?\d{1,5})?\s?[-–—]\s?\d{2})(?!\d)")
RE_HFD_NUMBER = re.compile(r"(?<![\d-])\d{1,5}-\d{2}(?![\d-])")
# a decision the dv corpus holds without a referat: `casenaming.verdict_uri`'s
# shape, "dom/hfd/6329-25/2026-06-11". A referat or notis is "dom/hfd/2020:24",
# "dom/hfd/2018/not/6".
RE_VERDICT = re.compile(r"^dom/[^/]+/[^/]+/\d{4}-\d{2}-\d{2}$")
SUPERSEDED = ".superseded.json"


def appealed(text):
    """The basefile of the kammarrätt decision an HFD decision's page 1 names
    as the one appealed, or None."""
    match = RE_OVERKLAGAT.search(text)
    if match is None:
        return None
    date = swedish_date(match.group(2))
    number = RE_MALNUMMER.search(match.group(3))
    if date is None or number is None:
        return None
    return "%s/%s-%s/%s" % (court_code(match.group(1)), number.group(1),
                            number.group(2), date)


def published(snapshot, malnummer):
    """The local uri of the HFD referat or notis the dv corpus holds under one
    of these målnummer ("6102-19" -> "dom/hfd/2020:24"), or None."""
    for number in RE_HFD_NUMBER.findall(malnummer):
        for court, _date, local in snapshot["numbers"].get(number, []):
            if court == "HFD" and not RE_VERDICT.match(local):
                return local
    return None


def hfd_basefile(row):
    """"hfd/32325": an HFD record is a lookup row, not a document, so it is
    keyed on the database's own id -- which also spares it the målnummer the
    listing misprints (id 27469 lists "3019")."""
    return "%s/%s" % (HFD, row["id"])


def hfd_key(root, row):
    bf = hfd_basefile(row)
    return ItemKey(bf, compress.exists(record_json(root, bf)), row["beslutsdatum"])


def hfd_resolve(root, row):
    """Store one HFD listing row as a record. Nothing is fetched: most HFD rows
    are refusals of prövningstillstånd that dv never publishes, and a row is
    read only once dv holds its referat (`read_appeals`)."""
    bf = hfd_basefile(row)
    if compress.exists(record_json(root, bf)):
        return False
    write_record(record_json(root, bf),
                 {"basefile": bf, **row, "source_url": CASE % row["id"]})
    return True


def read_appeals(session, root, snapshot, delay):
    """Read page 1 of every stored HFD decision that the dv corpus now holds a
    referat or notis of and that has not been read yet; returns how many were
    read. `overklagat` is stored even when page 1 names no decision (HFD 2023
    ref. 7's PDF starts at page 2), so an unreadable PDF is fetched once."""
    read = 0
    for bf in compress.list_basefiles(root, HFD):
        path = record_json(root, bf)
        record = compress.read_json(path)
        if "overklagat" in record or published(snapshot, record["malnummer"]) is None:
            continue
        fields = case_fields(_text(request(session, "GET", record["source_url"],
                                           timeout=60)))
        overklagat = None
        if "beslut_url" in fields:
            time.sleep(delay)
            data = request(session, "GET", fields["beslut_url"].replace(" ", "%20"),
                           timeout=180).content
            verify_pdf(data)
            pdf = body_path(root, bf)
            compress.write_download(pdf, data)
            text = pdf_first_page_text(pdf, pages=2)
            if not text:
                text = pdf_first_page_text(ocr_pdf(pdf, "swe"), pages=2)
            overklagat = appealed(text)
        write_record(path, {**record, **fields, "overklagat": overklagat})
        read += 1
        time.sleep(delay)
    return read


def write_superseded(root, snapshot):
    """{kammarrätt basefile: the HFD referat or notis that supersedes it}, as
    the file parse reads. Rebuilt every run: the snapshot grows as dv publishes
    referat, and a decision appealed long ago becomes superseded then."""
    index = {}
    for bf in compress.list_basefiles(root, HFD):
        record = compress.read_json(record_json(root, bf))
        local = published(snapshot, record["malnummer"])
        if record.get("overklagat") and local:
            index[record["overklagat"]] = "https://lagen.nu/" + local
    write_record(superseded_path(root), dict(sorted(index.items())))
    return index


def superseded_path(root):
    return Path(root) / SUPERSEDED


def superseded(root):
    return compress.read_json(superseded_path(root), default={})


def _only_rows(session, only):
    """The listing rows of one basefile ("kst/6426-25/2026-03-12"), found through
    the form's own målnummer search."""
    court, malnummer, _date = only.split("/")
    if court not in DOMSTOL:
        raise ValueError("kkvdomar: %r names no kammarrätt (%s)"
                         % (only, ", ".join(sorted(DOMSTOL))))
    return listing_rows(listing_page(session, 1, inDomstol=DOMSTOL[court],
                                     InArendeNr=malnummer))


def sync(root, full=False, only=None, limit=None, delay=0.3, log=print):
    root = Path(root)
    session = make_session(USER_AGENT)
    if only:
        rows = [row for row in _only_rows(session, only) if basefile(row) == only]
        # the latest registration, as the walk keeps (see is_current)
        row = select_one(sorted(rows, key=lambda row: -int(row["id"])),
                         basefile, only,
                         "the database lists no kammarrätt decision %s")
        return 1, int(resolve(session, root, row, full=full, delay=delay))

    watermark = HarvestWatermark(root / ".watermark.json",
                                 lookahead_limit=100, safety_days=60)

    def item_key(row):
        return ItemKey(basefile(row), is_current(root, row), row["beslutsdatum"])

    result = walk(enumerate_rows(session, delay),
                  resolve=lambda row: resolve(session, root, row, full=full,
                                              delay=delay),
                  item_key=item_key, watermark=watermark, full=full,
                  limit=limit, scope="kkvdomar", count_label="stored", log=log)
    hfd = walk(enumerate_rows(session, delay, HFD_INSTANS),
               resolve=lambda row: hfd_resolve(root, row),
               item_key=lambda row: hfd_key(root, row),
               watermark=HarvestWatermark(root / ".watermark-hfd.json",
                                          lookahead_limit=100, safety_days=60),
               full=full, limit=limit, scope="kkvdomar hfd",
               count_label="stored", log=log)
    snapshot = datasets.load_casenumbers()
    read = read_appeals(session, root, snapshot, delay)
    index = write_superseded(root, snapshot)
    log("kkvdomar: read %d HFD decisions; %d kammarrätt decisions superseded "
        "by an HFD referat or notis" % (read, len(index)))
    return result.seen + hfd.seen, result.new + hfd.new
