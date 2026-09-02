"""Harvester for Europeiska försäkrings- och tjänstepensionsmyndighetens
riktlinjer och rekommendationer.

**The index is the document library, and it is nearly clean.** Eiopa keeps one
facet per document type under ``/document-library/<typ>_en`` -- 82 rows under
``guidelines`` and 18 under ``recommendations``, twenty to a page, no browser
and no query grammar. That is the opposite of the EBA, whose "Guidelines" facet
holds only reports *about* guidelines: here the riktlinje itself is on the leaf.
What the facet also holds is everything published beside it, and a leaf carries
up to nineteen files -- the slutrapport on the public consultation, the
compliance table, the technical annexes, the resolution of comments, the press
release. So a leaf is not a document and a file is not a document either.

**The number is on the cover, and only there.** No leaf page prints a number
anywhere, and no file name carries one before 2025. Eiopa writes that number in
almost every spelling a text can take -- ``EIOPA-BoS-14/253``,
``EIOPA BoS 14/253``, ``EIOPA-BoS-20-002``, ``EIOPA-BoS-2021/456``,
``EIOPA-21/260``, ``EIOPA 16/858``, ``Eiopa – 17/651`` -- and poppler renders
the hyphen of the older type-1 covers as an opening parenthesis
(``EIOPA(BoS(14(026``). What survives every spelling is the pair the Board of
Supervisors numbers by: a two-digit year and a löpnummer. `cover_identity`
reads that pair and `issuers` slugs the whole number, so ``EIOPA-BoS-14/253``
and ``EIOPA-BoS-14-253`` are one address.

**A cover is asked what it is, not just what it is numbered.** Eiopa gives the
whole dossier one number: ``EIOPA-BoS-25/660`` is printed on the riktlinje *and*
on the slutrapport about it, so the number cannot tell them apart and the
cover's own lead does -- "GUIDELINES" / "Riktlinjer" against "FINAL REPORT" /
"Errata" / "Compliance table". The leaf's own file titles say the same thing
(`is_document_title`), and they are read first as a filter, so a routine run
reads ~70 covers rather than 214. A title that lies can only cost a document,
never mis-file one, because the cover still has to agree; both refusals are
counted separately.

**A cover printing two numbers is declined.** The consolidated editions print
the number they consolidate beside the number that amended it
(``EIOPA-BoS-14/165`` above ``EIOPA-BoS-22/218``), and nothing on the page says
which of the two the file is filed under. Guessing is what mis-filed fifteen EBA
documents, so these are counted instead.

**Swedish exists by law rather than by favour.** Artikel 16 in förordning (EU)
nr 1094/2010 makes a riktlinje effective only once it is translated into every
official language, and publication of the translations starts the two months in
which Finansinspektionen must state whether it complies; artikel 73 in the same
förordning is what the library's own pages cite for it. The leaf lists the
translations behind an "Other languages" toggle, one anchor per language with
its own ``hreflang``, which is what this reads -- never the file name, which
Eiopa leaves in English for every translation. The newest riktlinjer are still
untranslated and are published here in English with the record saying so.

The identity is read off the **English** manifestation even where the Swedish
one is what gets stored: they are the same file in two languages, the English
one is the original the number was printed on, and several Swedish covers print
no number at all.

Neither facet paginates past its end and the whole corpus is 8 index requests
plus one per leaf, so the EDPB/EBA idiom applies: one walk per run, no
watermark. `known_documents` reads each stored record's identity file back, so
a steady run re-reads no cover.

Stored per document under ``site/data/downloaded/guidance/eiopa/``: an
``eiopa-<serie>-<slug>.json`` record and the ``.pdf`` document.
"""

import re
import time
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from ..lib.harvest import paginated, select_pending, stored_index, walk_records
from ..lib.net import BROWSER_UA as USER_AGENT
from ..lib.net import fetcher, get_text, make_session, request
from ..lib.pdftext import pdf_first_page_text_bytes
from ..lib.util import document_extension, href, normalize_space
from .issuers import EIOPA

BASE = EIOPA.base
LIBRARY = BASE + "/document-library/%s_en"

# a leaf: one publication page. The library links nothing else under
# /publications/, and the leaf is where the files live.
RE_LEAF = re.compile(r'"(/publications/[^"#?]+)"')

# a bound on the pager, so a library facet that never repeats itself cannot
# walk forever. Five pages of guidelines and one of recommendations today
# (twenty rows to a page); the walk still ends on "this page named nothing
# new", not on this.
PAGE_CAP = 60

# Eiopa's own number as its covers print it. The separator between the parts is
# a hyphen, a slash, a space or an en dash -- and, on the type-1 covers of
# 2013-2014, an opening parenthesis, which is what poppler makes of that font's
# hyphen ("EIOPA(BoS(14(026"). The year is two digits on all but two covers,
# which print four ("EIOPA-BoS-2021/456"), and the löpnummer runs to four.
SEP = r"[\s\-‐-―/(]"
RE_COVER_NUMBER = re.compile(
    r"\bEIOPA%s{0,3}(BoS%s{0,3})?(\d{2}|\d{4})%s(\d{1,4})\b" % (SEP, SEP, SEP),
    re.I)

# what a cover leads with when it is *about* a riktlinje rather than being one.
# Not anchored: poppler renders these covers as a single line, and it does not
# begin with the type. A security marking ("EIOPA REGULAR USE"), a watermark
# ("This is only test") and, on the copies Eiopa republishes from a national
# authority, that authority's own classification banner ("Central Bank of
# Ireland - RESTRICTED") all come first. Which of the two patterns wins is
# decided by position instead -- see `cover_kind`.
RE_ABOUT = re.compile(
    r"\b(?:EIOPA\s+)?(?:Joint\s+(?:Committee\s+)?)?"
    r"(?:Final\s+[Rr]eport|Slutrapport|Report\b|Rapport\b|Compliance\s+table"
    r"|One\s+Minute\s+Guide|Errata|Rättelser?|Annex|Bilaga|Impact\s+assessment"
    r"|Resolution\s+of\s+comments|Press\s+release|Overview\s+of\s+replies"
    r"|Feedback\s+statement|Explanatory\s+note|Technical\s+annex"
    r"|Data\s+protection\s+statement|Record\s+of\s+personal\s+data)\b", re.I)
# and what it leads with when it *is* one. Eiopa sets the type above the title,
# in the document's own language.
RE_IS_GUIDANCE = re.compile(
    r"\b(?:(?:Joint|Revised|Preparatory|Draft|Final)\s+)*"
    r"(?:Guidelines?|Recommendations?"
    r"|(?:Gemensamma|Ändrade|Förberedande|Slutliga)?\s*[Rr]iktlinjer"
    r"|Rekommendationer?)\b", re.I)
# how much of a cover is its lead. A character window rather than a line count,
# because poppler gives the whole cover as one line: 400 characters reaches past
# the markings and the type to the title on every cover in the corpus.
COVER_LEAD = 400

# the leaf's own name for a file that is not the guidance: the same populations
# RE_ABOUT names, as Eiopa titles them in the library.
RE_ABOUT_TITLE = re.compile(
    r"^(?:\d[\d.]*[_ ])?(?:EIOPA[-\s][\w./-]+\s*[-_]\s*)?"
    r"(?:Final\s+[Rr]eport|Slutrapport|Compliance\s+table|Overview\s+of\s+replies"
    r"|Resolution\s+of\s+comments|Resolution\s+table|Press\s+release"
    r"|One\s+Minute\s+Guide|Impact\s+assessment|Technical\s+[Aa]nnexe?s?"
    r"|Annexe?s?\b|Bilaga|Privacy\s+statement|Record\s+of\s+personal\s+data"
    r"|Explanatory\s+note|Consolidated|Errata|Amendments|Rättelse"
    r"|\d[\d.]*\s|JC\s|ESA\s|GL\s)", re.I)

# the file's format, as the library prints it beside the download
RE_PDF = re.compile(r"\bPDF\b")


def basefile(serie, nummer):
    """The harvest basefile of one document
    ("eiopa/riktlinjer/eiopa-bos-14-253")."""
    return "%s/%s/%s" % (EIOPA.kod, serie, EIOPA.serie(serie).slug(nummer))


def listing_url(doctype, page):
    """One page of one document-library facet."""
    return "%s?page=%d" % (LIBRARY % doctype, page)


def leaf_pages(listing_html):
    """The publication leaves one library page names, as absolute URLs, in the
    order the page lists them. Pure over the HTML so the index can be tested
    without network."""
    return [BASE + path for path in dict.fromkeys(RE_LEAF.findall(listing_html))]


def is_document_title(title):
    """Whether the leaf's own name for a file leaves it a candidate for being
    the guidance itself.

    A cheap filter, not the decision: it is what keeps a run from downloading
    the slutrapport, the compliance table and the seventeen annexes of every
    leaf, and the cover still has to agree before anything is filed. A title
    that lies therefore costs a document rather than mis-filing one, which is
    why it is counted separately from the covers that refuse."""
    return not RE_ABOUT_TITLE.match(normalize_space(title or ""))


def parse_leaf(html_text, url):
    """One publication leaf -> its title, its publication date and its files.

    Each file is ``(titel, en-url, sv-url or None)`` for the PDFs the leaf
    lists. The Swedish url comes from the translation anchor's own ``hreflang``,
    never from the file name: Eiopa names every translation with the English
    file name."""
    soup = BeautifulSoup(html_text, "html.parser")
    heading = soup.find("h1")
    assert heading is not None, "%s carries no document title" % url
    files = []
    for item in soup.select(".ecl-file"):
        anchor = item.select_one("a.ecl-file__download")
        meta = item.select_one(".ecl-file__meta")
        if anchor is None or meta is None \
                or not RE_PDF.search(meta.get_text()):
            # the compliance table beside a riktlinje is an .xls and the
            # technical annexes arrive as a .zip, both listed exactly like the
            # document itself
            continue
        translation = item.select_one(
            "a.ecl-file__translation-download[hreflang='sv']")
        title = item.select_one(".ecl-file__title")
        files.append((normalize_space(title.get_text()) if title else "",
                      BASE + href(anchor),
                      BASE + href(translation) if translation else None))
    return {"titel": normalize_space(heading.get_text()),
            "publicerad": leaf_date(soup), "files": files}


def leaf_date(soup):
    """The leaf's own Publication date, as an ISO date. Eiopa states it as a
    definition term on every leaf ("16 February 2026")."""
    term = soup.find("dt", string=re.compile(r"^\s*Publication date\s*$"))
    assert term is not None, "the leaf states no publication date"
    return datetime.strptime(
        normalize_space(term.find_next("dd").get_text()), "%d %B %Y"
    ).date().isoformat()


def cover_number(text):
    """The one Eiopa number a cover prints, canonicalised, or None.

    None also when the cover prints **two** numbers: the consolidated editions
    set the number they consolidate above the number that amended it, and
    nothing says which of the two the file is filed under."""
    found = {"EIOPA-%s%s/%s" % ("BoS-" if bos else "", ar, lopnummer)
             for bos, ar, lopnummer in RE_COVER_NUMBER.findall(text or "")}
    return found.pop() if len(found) == 1 else None


def cover_kind(text):
    """What a cover says the document is: ``"vagledning"``, ``"om-vagledning"``
    for a document about one, or ``"otypad"`` when its lead says neither.

    **The earlier word wins**, which is the whole of the rule. A slutrapport
    names the riktlinje it reports on, and a riktlinje is published in a series
    whose annexes are named on its cover, so both patterns match most covers
    and neither presence nor absence separates them. The order does: Eiopa sets
    the type above the title, so "Final report on Joint Guidelines to ensure
    consistency..." is a report and "GUIDELINES on ring-fenced funds ... Annex
    I" is a riktlinje, on the same two words in the opposite order."""
    lead = normalize_space(text or "")[:COVER_LEAD]
    about, guidance = RE_ABOUT.search(lead), RE_IS_GUIDANCE.search(lead)
    if about is not None and (guidance is None
                              or about.start() < guidance.start()):
        return "om-vagledning"
    return "vagledning" if guidance is not None else "otypad"


def cover_text(pdf_bytes):
    """The first page of a document, as text, or None when the ``.pdf`` address
    served something that is not a PDF."""
    if document_extension(pdf_bytes) != ".pdf":
        return None
    return pdf_first_page_text_bytes(pdf_bytes)


def known_documents(root):
    """``{identity url: (serie, nummer)}`` from the records already stored.

    What makes a steady run free: the file whose cover named a document is
    recorded beside it, so a candidate we have already read is not read again."""
    directory = Path(root) / EIOPA.kod
    if not directory.exists():
        return {}
    return stored_index(directory, "identitet_url",
                        lambda record: (record["serie"], record["nummer"]))


def walk_library(session, doctype, delay):
    """Every leaf one facet names, in listing order, and how many library pages
    that took. Stops on a page that names no leaf this walk has not already
    seen -- never on an empty page, which is how a pager that repeats past its
    end runs a walk forever."""
    return paginated(
        lambda page: get_text(session, listing_url(doctype, page), delay),
        leaf_pages, cap=PAGE_CAP, what="Eiopa document library")


def eiopa_sync(root, full=False, only=None, limit=None, delay=0.5):
    """Harvest Eiopas riktlinjer och rekommendationer off its document library.

    One scope, not one per series: the two facets are two pages of one site and
    one leaf can appear in both, so walking them together is what lets the
    second facet's copy be recognised rather than filed twice. Running them
    concurrently would also put two walks on Eiopa at once
    (rule:respect-politeness).

    Every declined candidate is counted under its own reason, because the
    reasons mean different things (rule:instrument-failures). `titel` is a file
    the leaf itself names as something other than the guidance -- the
    slutrapport, the annexes -- and never reaches a download; `om-vagledning` is
    a document about a riktlinje; `otypad` is a cover shape this harvest has not
    seen; `utan nummer` is a cover that prints no Eiopa number, or two;
    `nummerkrock` is the pair below."""
    session = make_session(USER_AGENT)
    known = known_documents(root)
    found, fetched, pages, both = [], 0, 0, 0
    declined = dict.fromkeys(
        ("titel", "om-vagledning", "otypad", "utan nummer", "icke-pdf",
         "nummerkrock"), 0)
    leaves = {}
    for serie in EIOPA.koder:
        listed, walked = walk_library(session, EIOPA.serie(serie).doctype, delay)
        pages += walked
        for url in listed:
            # a leaf both facets name stays with the first: Eiopa files its
            # predecessor CEIOPS' pre-application guidance under guidelines and
            # recommendations alike
            both += url in leaves
            leaves.setdefault(url, serie)
    for url, listed_under in leaves.items():
        leaf = parse_leaf(get_text(session, url, delay), url)
        for titel, identity_url, swedish in leaf["files"]:
            if not is_document_title(titel):
                declined["titel"] += 1
                continue
            body = None
            if identity_url in known:
                serie, nummer = known[identity_url]
            else:
                body = request(session, "GET", identity_url, timeout=180).content
                fetched += 1
                time.sleep(delay)
                text = cover_text(body)
                if text is None:
                    declined["icke-pdf"] += 1
                    continue
                kind = cover_kind(text)
                if kind != "vagledning":
                    declined[kind] += 1
                    continue
                serie, nummer = listed_under, cover_number(text)
                if nummer is None:
                    declined["utan nummer"] += 1
                    continue
            found.append((serie, nummer, {
                "basefile": basefile(serie, nummer), "utgivare": EIOPA.kod,
                "serie": serie, "nummer": nummer,
                "sprak": "sv" if swedish else "en",
                "titel": titel or leaf["titel"],
                "antagen": leaf["publicerad"], "version": None,
                "konsultation_url": None, "amnesord": [],
                "source_url": url, "dokument_url": swedish or identity_url,
                "identitet_url": identity_url,
            }, None if swedish else body))
    taken = [(record, (lambda got=body: got) if body is not None
              else fetcher(session, record["dokument_url"], timeout=180))
             for serie, nummer, record, body in found
             if sum(1 for other in found if other[:2] == (serie, nummer)) == 1]
    declined["nummerkrock"] = len(found) - len(taken)
    per_serie = {kod: sum(1 for record, _ in taken if record["serie"] == kod)
                 for kod in EIOPA.koder}
    print("eiopa: %d listing pages, %d leaves (%d named by both facets) -> %s, "
          "%d sv / %d en, %d covers read, declined: %s"
          % (pages, len(leaves), both,
             ", ".join("%d %s" % (n, kod) for kod, n in per_serie.items()),
             sum(1 for record, _ in taken if record["sprak"] == "sv"),
             sum(1 for record, _ in taken if record["sprak"] == "en"), fetched,
             ", ".join("%d %s" % (n, reason)
                       for reason, n in declined.items())))
    return walk_records(
        root, select_pending(
            taken, only, "the Eiopa document library carries no document %s"),
        delay=delay, full=full, limit=limit, scope=EIOPA.kod)
