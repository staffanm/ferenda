"""Harvester for BEREC:s riktlinjer -- the guidance the EU:s teleregulatörer
give each other on how the elektroniska kommunikationsregelverket is to be
applied, and which PTS takes utmost account of under artikel 4.4 i förordning
(EU) 2018/1971.

**The site is a document register, not a publications listing**, and that is
what makes this the cheapest walk in the source. BEREC runs a Drupal register
under ``/en/all-documents/<gren>`` (mirrored at ``/en/document-categories/…``,
which serves the same view). Every category page states its own row count and
prints its documents as a four-column table -- ``Document Number``, ``Document
date``, ``Document Title`` linking the leaf, ``Document author`` -- so the
listing already carries the identity, and no facet has to be trusted to mean
what it says: the guidelines category declares 78 rows and the pager yields
exactly 78 distinct leaves in five pages.

``/sitemap.xml`` is useless here: it names the five language home pages and
nothing else.

**The number is BEREC's own and is printed everywhere.** BEREC numbers every
document it produces in one running sequence -- a body prefix, a two-digit year
in parentheses and a serial: ``BoR (22) 81``. That sequence spans every
document type, so BoR (22) 80 is a report, BoR (22) 81 these riktlinjer and BoR
(22) 163 an yttrande. All 78 leaf pages print it as a ``Document number`` field
of their own and all 78 agree with the listing, which is why this harvest does
not have to open the PDF to name the document the way `eba_download` does.

The **document's own cover is still the authority on how the number is
spelled**, and it corrects the register twice: the register writes
``BoR(22)147`` with no spaces where the cover writes ``BoR (22) 147``, and
``BoR (10) 44  Rev 1`` with two where the cover writes ``BoR (10) 44 Rev1``.
`nummer` therefore rewrites the register's text into the spelling the covers
use, and `parse._berec_fields` re-reads the cover and fails the parse if the
number behind a URL ever stops being the one this harvest filed.

**What the guidelines category actually holds**, measured over the 78 rows on
2026-08-21:

  * **43 adopted riktlinjer** -- taken.
  * **18 utkast** ("Draft BEREC Guidelines …", one of them a track-changes
    copy). Not taken: BEREC puts a riktlinje out for public consultation under
    a number of its own and adopts the final text under a later one, so an
    utkast is a superseded pre-adoption text, not guidance. Unlike the EDPB's
    drafts these collide with nothing -- each has its own BoR number and so its
    own address -- so taking them later costs only this predicate.
  * **1 comparison document**, a track-changes diff of the 2020 and the 2022
    open-internet riktlinjer. Filed by BEREC under ``BoR (22) 81``, which is
    *the riktlinje's own number*: taking it would mint a second document at one
    address. Not guidance and not separable, so declined on both grounds.
  * **16 rows with no file at all.** BEREC's register lists them and prints
    "PDF - " with no size and the title unlinked -- the attachment is missing
    upstream. They are the 7 scoping-och-förslagsdokument, the 5 internal
    guidelines on BEREC's own working procedure, and 4 others. Counted, never
    guessed at.

The neighbouring categories were counted rather than assumed and are not this
source's business: 560 reports, 185 yttranden, 96 beslut, 74 arbetsprogram and
2,142 samrådssvar. The two that are arguable -- 12 gemensamma ståndpunkter and
7 metoddokument, both under Regulatory Best Practices beside the riktlinjer --
are left for a decision of their own rather than folded in here.

**English only.** BEREC publishes in English and the leaf pages offer no other
``language_content_entity``; every record says ``sprak: "en"`` so the page can
tell the reader why it is showing English text.

The corpus is small and fully enumerable in five listing pages plus one request
per leaf, so the edpb/eba/rs idiom applies: one walk per run, no watermark.

Stored per document under ``site/data/downloaded/guidance/berec/``: a
``berec-riktlinjer-<slug>.json`` record and the ``.pdf`` document.
"""

import re
import time

from bs4 import BeautifulSoup

from ..lib.harvest import select_pending, walk_records
from ..lib.net import BROWSER_UA as USER_AGENT
from ..lib.net import make_session, request
from ..lib.util import href, normalize_space
from .issuers import BEREC

BASE = BEREC.base
SERIE = "riktlinjer"
# the register view for the one category this source carries. BEREC serves the
# same view under /en/document-categories/ as well; the listing's own rows link
# the /en/all-documents/ form, so that is the one walked.
LISTING = BASE + "/en/all-documents/berec/regulatory-best-practices/guidelines"
# how many rows the view says it has, printed above the table ("78 Results:").
# Read and reported so a pager that stops early cannot pass for a short corpus.
RE_DECLARED = re.compile(r'results-count container">\s*(\d+)\s+Results')
# an upper bound on the pager, so a view that starts repeating itself cannot
# spin: the walk stops on "no new leaves" (a BEREC page never yields none), and
# this is the guard behind that.
MAX_PAGES = 40

# the register's own number for a document, in every spelling it writes it in:
# "BoR (22) 81", the space-less "BoR(22)147", and a revision qualifier
# ("BoR (10) 44  Rev 1", "BoR (19) 179TC").
RE_NUMMER = re.compile(r"^BoR\s*\(\s*(\d{2})\s*\)\s*(\d{1,4})\s*(.*)$")
# the same number as the document's own cover prints it, which is its first
# line above the title and the date ("BoR (22) 81"). Read at parse time by
# `parse._berec_fields`, which is why it lives here beside the register's form.
RE_COVER_NUMMER = re.compile(r"BoR\s*\(\s*(\d{2})\s*\)\s*(\d{1,4})")

# a row the guidelines category holds that is not an adopted riktlinje. Both
# surfaces are BEREC's own words at the front of its own title, not a reading
# of the subject -- an utkast says "Draft" and the diff says what it is.
RE_UTKAST = re.compile(r"^(?:Track\s+Changes\s+)?Draft\b", re.I)
RE_JAMFORELSE = re.compile(r"\bComparison document\b", re.I)


def nummer(text):
    """BEREC's own number, in the spelling its documents' covers use.

    The register is inconsistent about the spacing and the covers are not, so
    the two register entries that differ are rewritten rather than carried:
    ``BoR(22)147`` becomes ``BoR (22) 147``, which is what that document's own
    first line says. Only the spacing moves -- the year and the serial are the
    register's, and a qualifier ("Rev 1") is kept as written."""
    match = RE_NUMMER.match(normalize_space(text))
    assert match, "not a BEREC document number: %r" % text
    ar, lopnummer, kvalificerare = match.groups()
    return "BoR (%s) %s%s" % (ar, lopnummer,
                              " " + kvalificerare if kvalificerare else "")


def base_number(number):
    """The number without its revision qualifier -- ``BoR (10) 44 Rev 1`` ->
    ``BoR (10) 44``. What a cover can be checked against: a document that
    carries a qualifier prints it in a spelling of its own (the cover of
    BoR (10) 44 Rev 1 reads "BoR (10) 44 Rev1"), while the year and the serial
    are written one way everywhere."""
    match = RE_NUMMER.match(number)
    assert match, "not a BEREC document number: %r" % number
    return "BoR (%s) %s" % match.groups()[:2]


def cover_numbers(cover_text):
    """Every BoR number a document's own cover prints, without qualifiers.

    Narrower than `RE_NUMMER`, which reads a register cell already known to be
    a number: this searches free text, so the parentheses are required and a
    bare "44" in a title cannot pass for a serial."""
    return {"BoR (%s) %s" % pair for pair in RE_COVER_NUMMER.findall(cover_text)}


def basefile(number):
    """The harvest basefile of one document ("berec/riktlinjer/22-81") -- the
    issuer, then what its URI carries after it, so a basefile and an address
    are the same string."""
    return "%s/%s/%s" % (BEREC.kod, SERIE, BEREC.serie(SERIE).slug(number))


def declared(listing_html):
    """How many rows the register says this category has. Pure over the HTML."""
    match = RE_DECLARED.search(listing_html)
    assert match, "the BEREC register page states no row count"
    return int(match.group(1))


def listing_rows(listing_html):
    """One page of the register table -> its rows, as
    ``{nummer, datum, titel, url}``. Pure over the HTML.

    Each column is read by the ``headers`` attribute its cell carries rather
    than by position, so a column BEREC adds or moves does not silently shift
    the number into the date."""
    soup = BeautifulSoup(listing_html, "html.parser")
    rows = []
    for row in soup.select("table tbody tr"):
        cell = row.select_one('td[headers="view-field-document-number-table-column"]')
        title = row.select_one('td[headers="view-name-table-column"] a[href]')
        when = row.select_one(
            'td[headers="view-field-document-date-table-column"] time[datetime]')
        if cell is None or title is None:
            continue                        # not a document row of this view
        rows.append({"nummer": nummer(cell.get_text()),
                     "datum": when["datetime"][:10] if when is not None else None,
                     "titel": normalize_space(title.get_text()),
                     "url": BASE + href(title)})
    return rows


def declined_reason(titel):
    """Why this row is not an adopted riktlinje, or None when it is.

    Read off BEREC's own title, which states both cases in its opening words --
    "Draft BEREC Guidelines …" and "… - Comparison document 2022 vs 2020". The
    module docstring records what each population is and what declining it
    costs."""
    if RE_UTKAST.match(titel):
        return "utkast"
    if RE_JAMFORELSE.search(titel):
        return "jämförelsedokument"
    return None


def parse_leaf(leaf_html, url):
    """One register leaf -> ``{nummer, antagen, dokument_url}``. Pure over the
    HTML.

    ``dokument_url`` is the anchor inside the page's ``doc-info`` block, which
    is BEREC's own designation of *the* document: an annex (``.xlsx``), a
    track-changes copy and a corrigendum all sit in the separate
    ``supporting-docs`` block below and are not it. 62 of the 78 leaves carry
    such an anchor and each of the 62 is a PDF; on the other 16 the block holds
    a bare ``<span>`` and a "PDF - " with no size, because BEREC's register has
    lost the file. ``None`` says so, and the caller counts it.

    The number is re-read here rather than carried from the listing, so a leaf
    that has moved under another row cannot be filed under the number the
    listing printed beside its link."""
    soup = BeautifulSoup(leaf_html, "html.parser")
    fields = {normalize_space(title.get_text()).rstrip(":"):
              title.find_next_sibling("span", class_="info-details")
              for title in soup.select(".info-content .info-title")}
    dokumentnummer = fields.get("Document number")
    assert dokumentnummer is not None, \
        "%s carries no Document number field" % url
    anchor = soup.select_one(".doc-info a[href]")
    return {
        "nummer": nummer(dokumentnummer.get_text()),
        "antagen": _iso_date(fields.get("Document date")),
        "dokument_url": BASE + href(anchor) if anchor is not None else None,
    }


# the register prints its dates as "09 June 2022" in the leaf's metadata list
# and as a machine-readable <time datetime> in the listing table. The listing's
# is what a record takes; this reads the leaf's only to date a document whose
# listing row carried no <time> at all.
RE_LEAF_DATE = re.compile(r"^(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})$")
MONTHS_EN = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
             "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
             "november": 11, "december": 12}


def _iso_date(cell):
    if cell is None:
        return None
    match = RE_LEAF_DATE.match(normalize_space(cell.get_text()))
    return "%s-%02d-%02d" % (match.group(3), MONTHS_EN[match.group(2).lower()],
                             int(match.group(1))) if match else None


def _fetch(session, url, delay):
    text = request(session, "GET", url, timeout=120).text
    time.sleep(delay)
    return text


def walk_listing(session, delay):
    """Every row of the guidelines category, and the count the register
    declares for it, as ``(rows, declared)``.

    The pager stops on **no new leaf**, never on an empty page: a Drupal view
    that has run past its last row serves the last page again rather than an
    empty one, and a walk that stopped on emptiness would loop (measured on
    another agency's library: 2,006 rows reported for 584 documents)."""
    rows, seen, total = [], set(), None
    for page in range(MAX_PAGES):
        html_text = _fetch(session, "%s?page=%d" % (LISTING, page), delay)
        if total is None:
            total = declared(html_text)
        fresh = [row for row in listing_rows(html_text) if row["url"] not in seen]
        if not fresh:
            return rows, total
        seen.update(row["url"] for row in fresh)
        rows.extend(fresh)
    raise ValueError("the BEREC guidelines pager ran past %d pages" % MAX_PAGES)


def _document_fetcher(session, url):
    return lambda: request(session, "GET", url, timeout=180).content


def berec_sync(root, full=False, only=None, limit=None, delay=0.5):
    """Harvest BEREC:s adopted riktlinjer off its document register.

    BEREC is **one scope**: it runs one numbering sequence across every
    document type and this source carries one category out of it, so there is
    nothing to fan out and one walk per run puts one walk on the host
    (rule:respect-politeness).

    Every declined row is counted under its own reason, because the four
    reasons mean four different things and must not look alike in a run's
    output (rule:instrument-failures): an utkast and a comparison document are
    documents this source does not carry, a row with no file is BEREC's
    register having lost one, and a number that collides with one already filed
    would be a new register shape this harvest has not seen."""
    session = make_session(USER_AGENT)
    rows, total = walk_listing(session, delay)
    if len(rows) != total:
        # `raise`, not `assert` (rule:errors-drive-retry-use-raise): stripped
        # under -O this would let a pager that stopped early pass for a short
        # corpus, and the run would report a complete harvest of half of it
        raise ValueError(
            "the BEREC register declares %d guidelines and its pager yields %d"
            % (total, len(rows)))
    pending, declined, no_file, collisions = [], {}, 0, 0
    filed = {}
    for row in rows:
        reason = declined_reason(row["titel"])
        if reason:
            declined[reason] = declined.get(reason, 0) + 1
            continue
        leaf = parse_leaf(_fetch(session, row["url"], delay), row["url"])
        if leaf["nummer"] != row["nummer"]:
            # `raise`, not `assert`: this is the check whose absence would let
            # the harvest *succeed* with the document filed under a number that
            # is not its own -- the listing's row and the leaf it links having
            # come apart is exactly the case that mis-filed 15 EBA documents
            raise ValueError(
                "%s is listed as %s and states %s on its own page"
                % (row["url"], row["nummer"], leaf["nummer"]))
        if leaf["dokument_url"] is None:
            # BEREC's register lists the document and has lost the file behind
            # it -- the title is unlinked and the size blank on its own page.
            # Not a shape this harvest failed to read, and not a document
            # either: nothing to store, so it is counted and left.
            no_file += 1
            continue
        bf = basefile(leaf["nummer"])
        if bf in filed:
            # two register rows under one number. BEREC does this once, for the
            # comparison document declined above; a second occurrence is a new
            # register shape and must not overwrite a document already filed.
            collisions += 1
            continue
        filed[bf] = row["url"]
        pending.append(({
            "basefile": bf, "utgivare": BEREC.kod, "serie": SERIE,
            "nummer": leaf["nummer"], "sprak": "en", "titel": row["titel"],
            "antagen": row["datum"] or leaf["antagen"], "version": None,
            "konsultation_url": None, "amnesord": [],
            "source_url": row["url"], "dokument_url": leaf["dokument_url"],
        }, _document_fetcher(session, leaf["dokument_url"])))
    print("berec: %d rows declared, %d walked -> %d riktlinjer; declined %s, "
          "%d rows whose register entry has no file, %d number collisions"
          % (total, len(rows), len(pending),
             ", ".join("%d %s" % (n, reason)
                       for reason, n in sorted(declined.items())) or "none",
             no_file, collisions))
    return walk_records(
        root, select_pending(pending, only,
                             "the BEREC register carries no guideline %s"),
        delay=delay, full=full, limit=limit, scope=BEREC.kod)
