"""Harvester for de AMC och GM Europeiska unionens byrå för luftfartssäkerhet
utfärdar till EU:s luftfartsregler.

**The instrument is not the document**, which is the identity question this
harvest exists to answer. EASA issues its AMC/GM as numbered *annexes to an ED
Decision* -- a decision of the Executive Director, "ED Decision 2026/006/R" --
and one decision carries several: 2026/006/R issues Annex I (AMC & GM to
Regulation (EU) No 1178/2011 -- Issue 1, Amendment 3), Annex II (Part-FCL --
Issue 1, Amendment 15), Annex III (Part-ARA -- Issue 1, Amendment 15) and Annex
IV (Part-ORA -- Issue 1, Amendment 9). Each annex has a page of its own in the
document library and a text of its own. Filing the four under the decision
number would give four documents one address, so the decision is recorded as
`beslut` -- the instrument -- and the identity is the annex.

What a reader cites is the AMC/GM item, "AMC1 CAT.OP.MPA.100", and the item
belongs to a rule annex at a stated version. The annex names itself for exactly
that, on its own cover: Annex IV to ED Decision 2022/005/R prints "'AMC and GM
to Annex IV (Part-CAT) to Commission Regulation (EU) No 965/2012 -- Issue 2,
Amendment 20'". So the identity is that name, read in the two parts it is built
from (`series_number`):

  * the **series** -- whether the annex holds AMC, GM, or both. Not decoration:
    "GM to Part M -- Amendment 4" (2015) and "AMC to Part-M -- Amendment 4"
    (2008) are two documents whose names differ in nothing else, because the
    AMC and the GM to one rule run separate amendment sequences.
  * the **number** -- the rest of the name: the rule annex with its Issue and
    Amendment ("Part-CAT -- Issue 2, Amendment 20"). The amendment belongs to
    the identity rather than to `version`, because these are as-published
    immutables: EASA keeps every amendment, each PDF holds only that
    amendment's own text, and a superseded one is marked "Repealed" rather than
    replaced. 46 of the 513 are so marked.

**The pager repeats content past the end.** The library serves 50 rows a page
under ``?page=N``. Rows shift between pages while a walk runs, because the view
sorts by publication date and EASA inserts into it, so a later page can repeat
an earlier page's rows; and page 12 and every page after it returns the view
shell -- the whole topic index, 320 kB of it -- with *zero* table rows. The walk
therefore stops when a page names no document it has not already seen, and says
which page that was. Measured 2026-08-21: 12 pages of rows, 571 rows, 566
distinct documents, 5 rows repeated across pages.

**A document this source carries is an annex to an ED Decision**, and EASA says
so in two independent places: the leaf page's "Official Publication" mark and
its "Related ED Decision" field. Neither alone is sound. Three real annexes to
ED Decision 2016/012/R (Part-CAT Issue 2 Amendment 7, Part-NCC Amendment 6,
Part-SPO Amendment 6 -- all published the same day) carry no Official
Publication mark, and two real ones (AMC and GM to the rules of the air Issue 1
Amendment 4, Part-CNS Issue 1 Amendment 1) name no ED Decision. Requiring both
loses those five; requiring either loses none, and lets in exactly one page that
is not an annex -- "Part-M", whose file is ``Decision 2011-002-R.pdf``, the
decision itself. That one declines on its name, which says neither AMC nor GM.

Counted apart from it (rule:instrument-failures), because a shape this harvest
has never seen must not hide among the shapes it has: 49 "Consolidated
(unofficial)" texts, which are EASA's own running consolidation of a rule annex
and carry neither mark (they have no ED Decision, no Issue/Amendment of their
own, their names repeat -- six rows are called "Consolidated (unofficial) AMC &
GM to Annex II (Part-ARO)" -- and 42 of them no longer link a file); five rows
whose file is a ``.zip`` bundle of a whole decision's annexes, which are not
separable by address; two rows linking no file; and one name EASA has published
twice, "AMC to Part-66 -- Amendment 10", under ED Decision 2010/011/R and again
under 2011/003/R.

That leaves **508 documents: 457 AMC & GM, 30 GM, 21 AMC**.

EASA publishes in English only -- there is no Swedish AMC/GM to take -- so every
record says ``sprak: "en"`` and the page tells the reader why.

Stored per document under ``site/data/downloaded/guidance/easa/``: an
``easa-<serie>-<nummer>.json`` record and the ``.pdf`` document.
"""

import re
import time

from bs4 import BeautifulSoup

from ..lib.harvest import select_pending, walk_records
from ..lib.net import BROWSER_UA as USER_AGENT
from ..lib.net import make_session, request
from ..lib.util import href, normalize_space
from .issuers import EASA

BASE = EASA.base
LIBRARY = (BASE + "/en/document-library"
                  "/acceptable-means-of-compliance-and-guidance-material")

# one row of the listing view: the link to the document's own page. The view
# renders the same target twice per row (the title, and a "view" icon), so the
# title anchor is selected rather than every link in the row.
LEAF_SELECTOR = "div.view-main-content table a.easa_node_link"
# how many listing pages a walk may look at before it decides the pager has
# stopped terminating. 12 hold the corpus today; the cap turns an endless pager
# into a failure with a message rather than an unbounded crawl.
PAGE_CAP = 60

# the lead of an annex's own name, which says whether it holds AMC, GM or both:
# "AMC & GM to Part-CAT — …", "AMC and GM to Part 21 — …", "AMC/GM to Part 21 —
# …", "AMC & GM Part-TCO — …" (no preposition at all), "GM on Remote tower
# operations — …". Captured rather than matched: the lead *is* the series.
RE_LEAD = re.compile(
    r"^(?:(AMC)\s*(?:&|and|/)\s*(GM)|(AMC|GM))\b\s*(?:to|for|on)?\s+", re.I)
# a name that holds both without leading with either. Eleven pre-2013 annexes
# are named "Part-145 / AMC Amendment 4 / GM Amendment 1" -- the rule first, and
# the two amendment sequences printed side by side.
RE_BOTH = re.compile(r"\bAMC\b.*\bGM\b", re.I)

SERIE_BOTH = "amc-gm"
# how EASA says an annex's file is a PDF. The download address carries no
# extension -- every file sits behind /en/downloads/<id>/en -- and the link's
# `title`, the upload's own file name, is missing on one row. The site marks the
# type twice beside the link instead, as a MIME type and as this icon class, and
# the icon is the one that is a selector rather than an attribute to narrow:
# 517 of the 522 files are `file-pdf` and five are `file-zip`.
PDF_ICON = ".dfu-file .file-icon.file-pdf"


def basefile(serie, nummer):
    """The harvest basefile of one document
    ("easa/amc-gm/part-cat-issue-2-amendment-20")."""
    return "%s/%s/%s" % (EASA.kod, serie, EASA.serie(serie).slug(nummer))


def leaf_pages(listing_html):
    """The document pages one listing page names, in the order the view shows
    them. Pure over the HTML, so the pager can be tested without network."""
    return [href(a) for a in
            BeautifulSoup(listing_html, "html.parser").select(LEAF_SELECTOR)]


def series_number(titel):
    """``(serie, nummer)`` for an annex's own name, or None where the name is
    not one this source carries.

    ``"AMC & GM to Part-CAT — Issue 2, Amendment 20"`` -> ``("amc-gm",
    "part-cat-issue-2-amendment-20")``. The lead is the series and the rest is
    the number, so the address reproduces the name the document gives itself.

    None where the name says neither AMC nor GM. One page in the library is
    titled just "Part-M" and holds ``Decision 2011-002-R.pdf`` -- the Executive
    Director's decision, not an annex to one. A shape to count, never to file
    under a guessed series."""
    match = RE_LEAD.match(titel)
    serie = (SERIE_BOTH if match.group(1) else match.group(3).lower()) \
        if match else (SERIE_BOTH if RE_BOTH.search(titel) else None)
    if serie is None:
        return None
    rest = titel[match.end():] if match else titel
    return serie, re.sub(r"[^0-9a-z]+", "-", rest.lower()).strip("-")


def parse_leaf(html_text, url):
    """One document page -> its fields. Pure over the HTML.

    ``bilaga`` is "this page is an annex to an ED Decision", read off both the
    marks EASA sets for it, because each is missing on pages the other covers
    -- see the module docstring for the five documents that costs."""
    soup = BeautifulSoup(html_text, "html.parser")
    article = soup.select_one("article.node--type-easa-amcgm")
    assert article is not None, "%s is not an AMC/GM document page" % url
    heading = soup.find("h1")
    assert heading is not None, "%s carries no document title" % url
    published = article.select_one(
        ".field-name-field-easa-official-publication")
    beslut = article.select_one(".field-name-field-easa-related-ed-decision a")
    adopted = article.select_one("time[datetime]")
    files = article.select_one(".field-name-field-easa-amendment-file")
    document = (files.select_one("a[href]") if files is not None else None)
    return {
        "titel": normalize_space(heading.get_text()),
        "bilaga": published is not None or beslut is not None,
        "antagen": adopted["datetime"][:10] if adopted is not None else None,
        # the instrument, as EASA's own field labels it. One decision issues
        # several of these annexes, so this is provenance and not identity
        "beslut": (normalize_space(beslut.get_text())
                   if beslut is not None else None),
        # the rule the AMC/GM attaches to, which is the axis EASA builds its own
        # topic index on ("Part-ARA - Authority Requirements for Aircrew"). One
        # annex covers four rules and names all four
        "amnesord": [normalize_space(a.get_text()) for a in article.select(
            ".field-name-field-easa-acceptable-means a")],
        "dokument_url": BASE + href(document) if document is not None else None,
        "pdf": files is not None and files.select_one(PDF_ICON) is not None,
    }


def _fetch(session, url, delay):
    text = request(session, "GET", url, timeout=120).text
    time.sleep(delay)
    return text


def walk_library(session, delay, log=print):
    """Every document page the library names, walked page by page until a page
    adds nothing new. Returns ``(pages, how many pages were walked)``.

    The stop condition is "this page named no document I have not already
    seen", never "this page was empty". The view sorts by publication date and
    EASA inserts into it, so rows shift between pages while the walk runs and a
    page can repeat its predecessor's; and past the last page of rows the site
    keeps serving the view shell rather than a 404. Walking to the empty page
    and counting rows reported 571 rows for 566 documents on 2026-08-21."""
    seen: dict[str, None] = {}
    for page in range(PAGE_CAP):
        fresh = [url for url in leaf_pages(
            _fetch(session, "%s?page=%d" % (LIBRARY, page), delay))
            if url not in seen]
        if not fresh:
            log("easa: stopped at page %d, which named no new document" % page)
            return list(seen), page + 1
        seen.update(dict.fromkeys(fresh))
    raise ValueError(
        "the EASA document library still named new documents after %d pages "
        "(%d so far) -- its pager no longer terminates" % (PAGE_CAP, len(seen)))


def easa_sync(root, full=False, only=None, limit=None, delay=0.5):
    """Harvest EASA:s AMC/GM off its document library.

    All three series come out of one walk -- an annex's own name says which it
    belongs to -- so EASA is **one scope**. Three scopes would walk the same 12
    listing pages and 566 document pages three times over, and running them
    concurrently would put three walks on EASA at once
    (rule:respect-politeness).

    Every declined candidate is counted under its own reason
    (rule:instrument-failures). An unofficial consolidation, a bundled ``.zip``,
    a row linking no file, a name that says neither AMC nor GM, and a name EASA
    has already used are five different things; a run that merged them would
    hide the shape it has not seen behind the four it has."""
    session = make_session(USER_AGENT)
    leaves, pages = walk_library(session, delay)
    pending, taken = [], set()
    per_serie = dict.fromkeys(EASA.koder, 0)
    declined = dict.fromkeys(("ej bilaga", "namnlös serie", "utan fil",
                              "inte pdf", "dubblett"), 0)
    for url in leaves:
        fields = parse_leaf(_fetch(session, BASE + url, delay), BASE + url)
        identity = series_number(fields["titel"])
        if not fields["bilaga"]:
            declined["ej bilaga"] += 1
        elif identity is None:
            declined["namnlös serie"] += 1
        elif fields["dokument_url"] is None:
            declined["utan fil"] += 1
        elif not fields["pdf"]:
            declined["inte pdf"] += 1
        elif identity in taken:
            declined["dubblett"] += 1
        else:
            taken.add(identity)
            serie, nummer = identity
            per_serie[serie] += 1
            pending.append(({
                "basefile": basefile(serie, nummer), "utgivare": EASA.kod,
                "serie": serie, "nummer": nummer,
                # EASA issues its AMC/GM in English only: there is no Swedish
                # version to prefer, and the record says so rather than letting
                # a reader take English for an untranslated original
                "sprak": "en", "titel": fields["titel"],
                "antagen": fields["antagen"], "beslut": fields["beslut"],
                "amnesord": fields["amnesord"],
                "source_url": BASE + url,
                "dokument_url": fields["dokument_url"],
            }, _document_fetcher(session, fields["dokument_url"])))
    print("easa: %d listing pages, %d documents -> %s; declined %s"
          % (pages, len(leaves),
             ", ".join("%d %s" % (n, kod) for kod, n in per_serie.items()),
             ", ".join("%d %s" % (n, why) for why, n in declined.items())))
    return walk_records(
        root, select_pending(
            pending, only, "the EASA document library carries no document %s"),
        delay=delay, full=full, limit=limit, scope=EASA.kod)


def _document_fetcher(session, url):
    return lambda: request(session, "GET", url, timeout=180).content
