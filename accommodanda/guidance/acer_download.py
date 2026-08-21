"""Harvester for ACER:s ramriktlinjer, rekommendationer och yttranden.

**The site answers an ordinary HTTP client.** Every listing here is
server-rendered Drupal: one ``curl`` with a browser UA against
``/documents/official-documents/opinions`` returns the same 235 document links
a browser shows, and the recommendations pager is plain ``?page=N`` markup. No
headful Chrome, no JavaScript, no API -- ``/jsonapi`` and a REST endpoint are
both absent and the site's own search (below) does not hold these documents.

**Two page shapes, and they are two different things.**

  * *framework-guidelines* and *opinions* are **hand-built pages**: sections of
    ``section.linktofile-block`` anchors under an ``h2.mmr-title``, with no
    date, no pager and no node behind them. They are not in the site's
    document index either -- ``/documents/search?search_api_fulltext=ACER+
    Opinion+13-2026`` returns zero rows, and so does a search for the demand
    response ramriktlinje -- so the listing page is the only index there is,
    and everything the record does not read off the page has to come off the
    document.
  * *recommendations* is a **card view**: ``.views-row .document`` with a
    ``.title a``, a ``.date`` and a collapsed annex list, nine to a page behind
    a Drupal pager. That one states its dates.

**Identity is ACER's own number, löpnummer first.** The listing states it in
the anchor text ("ACER Opinion 13-2026", "ACER Recommendation 02-2026") and the
document's own cover prints it as "OPINION No 13/2026", so the page-derived
number is *verified against the file* before anything is filed under it
(`cover_numbers`): a hand-typed listing is exactly where a title and an href
come apart. A cover that prints no number at all is counted, not guessed at --
the pre-2017 PDFs open with an ACER feedback wrapper page and some of them are
scans whose OCR renders the word "OPINION" as "OPIMON". That is why the cover
is read two pages deep: on a wrapped document the real cover is page 2.

The ramriktlinjer have **no number**, so their slug is the name's own, and the
name is what ACER cites them by; `issuers.ACER` records why. Their page lists
one section per ämne *on top of* a canonical "Framework Guidelines" section,
and the two overlap: FG-2011-E-002 is listed once as "Framework Guidelines on
CACM for Electricity" and once, from another file, as "Framework Guidelines on
Capacity Allocation and Congestion Management for Electricity". So a candidate
is dropped when its slug or the code its cover prints is already taken, and the
canonical section wins because it comes first in page order.

What is **not** taken: the three decision categories (`issuers.ACER` says why),
every annex (an annex to a rekommendation is filed by ACER as a document of its
own and is not the rekommendationen's text), and, on the ramriktlinjesidan,
the adopting ACER-beslut and an ENTSOG impact assessment that sit beside the
ramriktlinjerna under the same markup.

**One scope, not three.** The three listings are three pages, so splitting
would not duplicate a walk the way the EBA's would -- but they are one host,
and three scopes would put three walks on acer.europa.eu at once
(rule:respect-politeness). The series is a property of the document instead.
Neither hand-built page paginates and the recommendations pager is five pages,
so the corpus is enumerable in seven requests plus one per document: the
EDPB/JK/ARN idiom applies, one walk per run, no watermark.

Stored per document under ``site/data/downloaded/guidance/acer/``: an
``acer-<serie>-<slug>.json`` record and the ``.pdf`` document.
"""

import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..lib import compress
from ..lib.harvest import select_pending, walk_records
from ..lib.net import BROWSER_UA as USER_AGENT
from ..lib.net import make_session, request
from ..lib.pdftext import pdf_first_page_text
from ..lib.util import (
    document_extension,
    english_date,
    href,
    normalize_space,
)
from .issuers import ACER

BASE = ACER.base
OFFICIAL = BASE + "/documents/official-documents"

# the one listing page per series, keyed by our series kod. The path segment is
# the series' own `doctype`, so the registry names the page and this module only
# knows where ACER puts its official documents.
LISTING = {s.kod: "%s/%s" % (OFFICIAL, s.doctype) for s in ACER.series}

# the numbered series' number as the listing writes it ("ACER Opinion 13-2026",
# "ACER Recommendation No 02/2026" -- both spellings occur) . Keyed by series
# kod, because the word is what tells a yttrande from a rekommendation and both
# pages otherwise look alike.
RE_NUMBER = {
    "yttranden": re.compile(r"\bOpinion\s+(?:No\s+)?(\d{1,2})[-/](\d{4})\b",
                            re.I),
    "rekommendationer": re.compile(
        r"\bRecommendation\s+(?:No\s+)?(\d{1,2})[-/](\d{4})\b", re.I),
}
# what the cover prints for itself: "OPINION No 13/2026", and, in the older
# layout, "OPINION OF THE AGENCY FOR THE COOPERATION OF ENERGY REGULATORS No
# 04/2016". Matched on the "No NN/ÅÅÅÅ" token alone rather than on the word
# before it, because that word is OCR on the scanned covers ("OPIMON") -- a
# rättsakt cited on the same cover carries three or four digits ("Regulation
# (EC) No 714/2009"), so two digits cannot be one.
RE_COVER_NUMBER = re.compile(r"\bNo\.?\s*(\d{1,2})/(\d{4})\b")
# the ACER wrapper page that opens every pre-2017 PDF, which states the date the
# document was published where the cover behind it states the date it was
# adopted
RE_PUBLISHED = re.compile(r"Publishing date:\s*(\d{2})/(\d{2})/(\d{4})")
# where a cover stops being about this document and starts citing the rättsakter
# it rests on. Every date after this belongs to something else: the recitals
# open "Having regard to Regulation (EU) 2019/942 ... of 5 June 2019". The
# enacting formula above it is deliberately not a marker -- the heading itself
# reads "OPINION No 07/2024 OF THE EUROPEAN UNION AGENCY FOR THE COOPERATION OF
# ENERGY REGULATORS of 29 October 2024", so stopping at the agency's name would
# stop before the document's own date.
RE_RECITALS = re.compile(r"\bHaving\s+regard\s+to\b", re.I)
# how far into the cover a date or a number may be read. The covers state both
# above the recitals; past this the text is the document's own body.
COVER_HEAD = 1200

# a ramriktlinje on its own page, told from the ACER-beslut that adopts it and
# from ENTSOG's impact assessment by naming itself first
RE_FRAMEWORK = re.compile(r"^Framework\s+Guidelines?\b", re.I)
# what a ramriktlinjes slug drops from the front of its name: its own kind, and
# the preposition and article that follow it
RE_FRAMEWORK_LEAD = re.compile(
    r"^Framework\s+Guidelines?\s*(?:on|for)?\s*(?:the\s+)?", re.I)
# an annex listed beside the document it belongs to. ACER files these as
# documents of their own; the record carries one PDF, which is the document.
RE_ANNEX = re.compile(r"^Annexe?s?\b", re.I)

# ACER publishes no translations: every document here is English, and the record
# says so, so the page showing it can say so too.
SPRAK = "en"
# a Drupal pager that repeats past its own end would otherwise walk forever;
# the recommendations view is five pages and this is the guard, not the count
MAX_PAGES = 40


def basefile(serie, nummer):
    """The harvest basefile of one document ("acer/yttranden/13-2026",
    "acer/ramriktlinjer/demand-response")."""
    return "%s/%s/%s" % (ACER.kod, serie, ACER.serie(serie).slug(nummer))


# --------------------------------------------------------------------------
# the listing pages
# --------------------------------------------------------------------------

def linked_documents(html_text, url):
    """The ``(titel, absolute url)`` pairs a hand-built listing names, in page
    order. Pure over the HTML.

    Page order is load-bearing for the ramriktlinjer: the canonical "Framework
    Guidelines" section is the first, and a document listed there as well as
    under its ämne is taken from there."""
    soup = BeautifulSoup(html_text, "html.parser")
    return [(normalize_space(anchor.get_text(" ", strip=True)),
             urljoin(url, href(anchor)))
            for anchor in soup.select("section.linktofile-block a[href]")]


def card_documents(html_text, url):
    """One page of the card view -> ``[(titel, ISO date, url, annexes)]``, in
    page order. Pure over the HTML.

    `annexes` is how many the card lists, counted rather than followed: the
    caller reports what it declined."""
    soup = BeautifulSoup(html_text, "html.parser")
    cards = []
    for row in soup.select(".views-row"):
        anchor = row.select_one(".title a[href]")
        if anchor is None:
            continue
        printed = row.select_one(".date")
        cards.append((
            normalize_space(anchor.get_text(" ", strip=True)),
            _card_date(normalize_space(printed.get_text())) if printed else None,
            urljoin(url, href(anchor)),
            len(row.select(".annex-item a[href]"))))
    return cards


RE_CARD_DATE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")


def _card_date(printed):
    """"22.07.2026" -> "2026-07-22". The card's own date, which is when ACER
    published the document; a cover that states when it was *adopted* wins over
    it in `_fields`."""
    match = RE_CARD_DATE.match(printed)
    return "%s-%s-%s" % (match.group(3), match.group(2),
                         match.group(1)) if match else None


def has_next_page(html_text):
    """Whether the pager offers a next page. A Drupal pager repeats past its own
    end when a view is filtered oddly, so the walk also stops on a page that
    names no document it has not already seen -- both, because either alone has
    been wrong somewhere in this source."""
    return BeautifulSoup(html_text, "html.parser").select_one(
        'a[rel="next"]') is not None


# --------------------------------------------------------------------------
# identity
# --------------------------------------------------------------------------

def listing_number(serie, titel):
    """The number a listing's anchor text states, in ACER's own ``NN/ÅÅÅÅ``
    form, or None where it states none.

    None is a real outcome and not a failure: the opinions page lists four
    anchors that name no opinion number -- two annexes, one opinion ACER never
    numbered ("ACER Opinion on the European Ten Year Network Development Plan
    2011-2020") and one whose title lost the word "Opinion" -- and the
    recommendations view lists ACER's revised network code on cybersecurity,
    which is the nätföreskriftens text and not a rekommendation."""
    match = RE_NUMBER[serie].search(titel)
    return "%s/%s" % match.groups() if match else None


def framework_slug(titel):
    """A ramriktlinjes URI segment, from the name ACER lists it under:
    "Framework Guidelines on CACM for Electricity" -> "cacm-for-electricity".

    The name loses its own kind and the preposition after it, because every one
    of them carries both and neither tells two apart."""
    stem = RE_FRAMEWORK_LEAD.sub("", titel)
    slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")
    assert slug, "ramriktlinjen %r has no name left to slug" % titel
    return slug


def cover_text(pdf_bytes):
    """The document's own cover as text, two pages deep, or None when the bytes
    are not a PDF at all (an error page served 200 under a ``.pdf`` address --
    counted by the caller, not repaired here).

    Two pages, because ACER wrapped every PDF it published before 2017 in a
    feedback page: on those the real cover is page 2, and page 1 states only the
    publishing date."""
    if document_extension(pdf_bytes) != ".pdf":
        return None
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(pdf_bytes)
        tmp.flush()
        return pdf_first_page_text(Path(tmp.name), pages=2)


def cover_numbers(text):
    """Every ``NN/ÅÅÅÅ`` number the head of a cover prints, as a set.

    A set rather than the first match, for the reason `eba_download` learned:
    an amending document prints the number it amends beside its own. The caller
    requires the filed number to be *among* these, and treats an empty set as
    the cover saying nothing -- which the scanned pre-2014 covers do."""
    return {"%s/%s" % (serial, year)
            for serial, year in RE_COVER_NUMBER.findall(text[:COVER_HEAD])}


def cover_date(text):
    """The date the document states for itself, as ISO, or None.

    Read off the head of the cover only, and never past the recitals: an ACER
    yttrande opens "OPINION No 07/2024 ... of 29 October 2024" and then recites
    "Regulation (EU) 2024/1789 ... of 13 June 2024", so a date taken from the
    whole page is as likely to be a rättsakts date as this document's. Where
    the cover states no date at all the wrapper page's publishing date stands
    in, which is within days of the adoption on every document that states
    both."""
    head = text[:COVER_HEAD]
    stop = RE_RECITALS.search(head)
    adopted = english_date(head[:stop.start()] if stop else head)
    if adopted:
        return adopted
    published = RE_PUBLISHED.search(head)
    return "%s-%s-%s" % (published.group(3), published.group(2),
                         published.group(1)) if published else None


# --------------------------------------------------------------------------
# the walk
# --------------------------------------------------------------------------

def stored_documents(root):
    """``{dokument url: (basefile, nummer, antagen)}`` from the records already
    on disk.

    What keeps a steady run from re-downloading the corpus to re-read covers it
    has read before: a candidate at an address a record already names was
    resolved from these bytes on an earlier run, cover and all, so nothing is
    fetched to say so again. `--full` ignores this and re-reads every cover.
    `eba_download.known_identities` is the same idea for the same reason."""
    directory = Path(root) / ACER.kod
    if not directory.exists():
        return {}
    return {record["dokument_url"]: (record["basefile"], record["nummer"],
                                     record["antagen"])
            for record in (compress.read_json(path)
                           for path in sorted(directory.glob("*.json*")))
            if record.get("dokument_url")}


class _Counts:
    """One line per run, one number per outcome (rule:instrument-failures).

    A declined candidate and a page shape this harvest has never seen must not
    look alike in the output, so every reason a candidate does not become a
    document has a counter of its own."""

    def __init__(self):
        self.taken = dict.fromkeys(ACER.koder, 0)
        self.pages = 0
        self.candidates = 0
        self.annexes = 0        # an annex listed beside its document
        self.unnumbered = 0     # the listing names no number for it
        self.not_framework = 0  # a beslut or an impact assessment on the FG page
        self.duplicate = 0      # a second listing of a document already taken
        self.covers = 0         # covers actually read (a download paid for)
        self.silent = 0         # a cover that prints no number of its own
        self.renamed = []       # the cover overruled the listing's number
        self.conflicts = []     # the cover prints several, and not the listed one
        self.not_pdf = 0        # a .pdf address that served something else
        self.unreachable = 0    # a link whose host does not answer at all

    def line(self):
        return ("acer: %d listing pages, %d candidates -> %s; declined %d "
                "annexes, %d unnumbered, %d not a ramriktlinje, %d listed "
                "twice, %d non-PDF bodies, %d unreachable, %d covers "
                "conflicting; %d covers read, %d of them silent, %d renamed "
                "by their cover"
                % (self.pages, self.candidates,
                   ", ".join("%d %s" % (n, kod)
                             for kod, n in self.taken.items()),
                   self.annexes, self.unnumbered, self.not_framework,
                   self.duplicate, self.not_pdf, self.unreachable,
                   len(self.conflicts),
                   self.covers, self.silent, len(self.renamed)))


def _fetch(session, url, delay):
    text = request(session, "GET", url, timeout=120).text
    time.sleep(delay)
    return text


def _document_fetcher(session, url):
    return lambda: request(session, "GET", url, timeout=60).content


def _listing_pages(session, url, delay):
    """Every page of one listing, in order. A page that names no document the
    walk has not seen ends it, whatever the pager claims -- a naive pager walk
    of a Drupal view can report several times the documents there are."""
    pages, seen = [], set()
    for number in range(MAX_PAGES):
        text = _fetch(session, url + ("?page=%d" % number if number else ""),
                      delay)
        fresh = {link for _, link in linked_documents(text, url)} \
            | {card[2] for card in card_documents(text, url)}
        if number and not fresh - seen:
            break
        pages.append(text)
        seen |= fresh
        if not has_next_page(text):
            break
    assert pages and seen, "the ACER listing %s named no document at all" % url
    return pages


def _candidates(session, serie, delay, counts):
    """What one listing offers this series, as ``(nummer, titel, antagen,
    document url)``, in page order. `nummer` is None for a ramriktlinje, whose
    slug comes from its name instead."""
    url = LISTING[serie]
    found = []
    for text in _listing_pages(session, url, delay):
        counts.pages += 1
        # the card view states its own dates; the hand-built pages state none,
        # and those documents are dated from their covers
        rows = [(titel, date, link, annexes)
                for titel, date, link, annexes in card_documents(text, url)] \
            or [(titel, None, link, 0)
                for titel, link in linked_documents(text, url)]
        for titel, date, link, annexes in rows:
            counts.candidates += 1
            counts.annexes += annexes
            if RE_ANNEX.match(titel):
                counts.annexes += 1
                continue
            if serie == "ramriktlinjer":
                if not RE_FRAMEWORK.match(titel):
                    counts.not_framework += 1
                    continue
                found.append((None, titel, date, link))
                continue
            nummer = listing_number(serie, titel)
            if nummer is None:
                counts.unnumbered += 1
                continue
            found.append((nummer, titel, date, link))
    return found


def filed_number(listed, printed, link, counts):
    """Which number a numbered document is filed under, given the one its
    listing states and the ones its own cover prints. None declines it.

    **The cover decides**, because the file is the document and the listing is
    hand-typed HTML. Three outcomes, and each has to be told from the others:

      * the cover prints the listed number among others -> the listing is
        right, and an amending document naming what it amends changes nothing.
      * the cover prints nothing this can read -> the listing stands, counted.
        The scanned pre-2014 covers are this: one of them OCRs its own heading
        as "OPIMON OF THE AGENCY ...".
      * the cover prints exactly one number and it is not the listed one -> the
        listing is wrong about this file, and the cover is followed and said so.
        ACER's opinions page links ``ACER Opinion 04-2015.pdf`` under the title
        "ACER Opinion 04-2014 on the ENTSOG Cost-Benefit Analysis Methodology",
        beside the real 04-2014; yttrande 04/2015 is listed nowhere else, so
        following the title would lose it and following the cover recovers it.

    Anything else -- several numbers, none of them the listed one -- is refused
    and named rather than guessed at."""
    if not printed:
        counts.silent += 1
        return listed
    if listed in printed:
        return listed
    if len(printed) == 1:
        taken = printed.pop()
        counts.renamed.append("%s is listed as %s and its cover prints %s"
                              % (link, listed, taken))
        return taken
    counts.conflicts.append("%s is listed as %s and its cover prints %s"
                            % (link, listed, ", ".join(sorted(printed))))
    return None


def _resolve(session, stored, serie, listed, titel, link, counts):
    """One candidate against its own file -> ``(basefile, nummer, antagen,
    body)``, or None when the file declines to be a document of this series.

    A ramriktlinje has no number to check, so its file is asked for nothing but
    its date; a numbered document's identity is settled here by
    :func:`filed_number`."""
    if link in stored:
        # resolved from these bytes on an earlier run: the identity and the
        # date it settled on are the record's, and nothing is fetched
        return (*stored[link], _document_fetcher(session, link))
    try:
        body = _document_fetcher(session, link)()
    except requests.RequestException:
        # ACER has moved its files to www.acer.europa.eu and retired the old
        # documents.acer.europa.eu, but two of the 314 links on its own listing
        # pages still name the retired host, which now accepts no connection at
        # all. That is a dead link upstream, not a document this walk failed to
        # fetch: the cause is known and the recovery is to count it and go on,
        # which is what makes this a catch (rule:no-catch-log-continue). It is
        # also why it must not block -- one unanswered host held up every other
        # ACER document behind it.
        counts.unreachable += 1
        return None
    text = cover_text(body)
    if text is None:
        # a .pdf address that served an error page. Not this harvest's to
        # repair and not a document either: counted, and the walk goes on
        counts.not_pdf += 1
        return None
    counts.covers += 1
    nummer = listed if listed is None else filed_number(listed,
                                                        cover_numbers(text),
                                                        link, counts)
    if listed is not None and nummer is None:
        return None
    slug = framework_slug(titel) if nummer is None else nummer
    return (basefile(serie, slug), nummer or slug, cover_date(text),
            lambda got=body: got)


def acer_sync(root, full=False, only=None, limit=None, delay=0.5):
    """Harvest ACER:s three published guidance series off their listing pages.

    All three come out of one walk (see the module docstring): three listings
    on one host, walked one after another, with the series a property of the
    document rather than of the run.

    A document listed twice is taken once, and which listing wins is page
    order: the ramriktlinjesidan repeats its canonical section per ämne, and
    the opinions page lists a corrigendum and an annex-bundle under the number
    of the opinion they belong to. For the numbered series the duplicate is not
    known until the cover has spoken, which is why the check comes after the
    fetch -- seven files a run, against the mis-filing it prevents."""
    session = make_session(USER_AGENT)
    # `--full` re-reads every cover as well as re-fetching every document: a
    # verification is only worth what the run is willing to repeat
    stored = {} if full else stored_documents(root)
    counts = _Counts()
    pending, taken = [], set()
    for serie in ACER.koder:
        for listed, titel, date, link in _candidates(session, serie, delay,
                                                     counts):
            # a ramriktlinjes identity is its name, known before the fetch, so
            # its repeats cost nothing
            if listed is None and basefile(serie, framework_slug(titel)) in taken:
                counts.duplicate += 1
                continue
            resolved = _resolve(session, stored, serie, listed, titel, link,
                                counts)
            time.sleep(delay)
            if resolved is None:
                continue
            bf, nummer, antagen, body = resolved
            if bf in taken:
                counts.duplicate += 1
                continue
            taken.add(bf)
            counts.taken[serie] += 1
            pending.append(({
                "basefile": bf, "utgivare": ACER.kod, "serie": serie,
                "nummer": nummer,
                # a ramriktlinje has no number to be cited by, so it is cited
                # by the name ACER lists it under -- the WP29/EASA rule
                "citation": titel if listed is None else None,
                "sprak": SPRAK, "titel": titel,
                "antagen": antagen or date, "version": None,
                "konsultation_url": None, "amnesord": [],
                "source_url": LISTING[serie], "dokument_url": link,
            }, body))
    print(counts.line())
    for line in counts.renamed + counts.conflicts:
        print("acer: %s" % line)
    return walk_records(
        root, select_pending(pending, only,
                             "the ACER listings carry no document %s"),
        delay=delay, full=full, limit=limit, scope=ACER.kod)
