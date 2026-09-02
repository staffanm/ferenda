"""Harvester for Europeiska datatillsynsmannens riktlinjer och yttranden.

**The site walls its index and serves its documents in the open.** Every Drupal
view on edps.europa.eu answers ``202`` with an empty body and the header
``x-amzn-waf-action: challenge`` -- the listing, the search, even
``/feed/news_en``. The static pages answer 200, and so does every document:
``/system/files/…`` and ``/sites/default/files/publication/…`` hand back a PDF
to a plain GET. So this harvest is split by what each half needs. The 90 index
pages go through `lib.browser.DetachedChrome`, which solves the challenge once
per run and reuses the cookie, exactly as `rs.skv`, `foreskrift.harvest`,
`untc.download` and `icj.download` do. The 442 documents go through
`lib.net`, at one request each and no browser.

**CELLAR is not a second route to these.** The EU:s publikationsbyrå holds 367
works under the EDPS:s corporate-body URI, and a census of what they are settles
it: 111 are full yttranden, all dated 2004-10-22 to 2017-03-21, from the years
when a yttrande was printed whole in EUT C; 119 are *sammanfattningar* whose own
title says where the document is ("Summary of the Opinion … The full text of
this Opinion can be found in English, French and German on the EDPS website");
the remaining 137 are vacancy notices, procedural rules and annual reports.
CELLAR holds nothing the EDPS published after 2023-11-20 and **no riktlinje at
all**. The site holds all of it, the 2004-2017 yttranden included, as the same
EUT offprints -- so the site is the route and `route="site"` is measured, not
assumed.

**The listing is the record.** A teaser row on an ämnessida carries everything
one document needs -- ``article.node--type-edpsweb-publication`` holds the
adoption date in three ``<div>``s, the title and leaf URL, the EDPS:s own topic
tags, and every file it published, one ``<option data-url>`` per language,
grouped under a ``.file-label``. Opening the leaf page adds nothing (checked
against two of them), so the walk is 9 pages of riktlinjer and 80 of yttranden
and no leaf fetches. The pager is walked until two pages in a row name no new
leaf, never until a page comes back empty.

**Identity.** The EDPS numbers its yttranden "Opinion NN/ÅÅÅÅ" from 2020 and
numbered nothing before, and numbers its riktlinjer not at all -- their covers
print a title and a date and nothing else.

  * A yttrande's number is read **off the PDF cover**, because the listing title
    drops it more often than not: the row titled "Digital Services Act" is
    Opinion 1/2021, "European Strategy for Data" is Opinion 3/2020 and "Road
    Safety Package" is Opinion 11/2023, each stated on its own first page. Of
    the 400 rows only 111 name a number in the title, which is why the title is
    a cross-check here and never the source (the EBA lesson: a number attached
    from a page and never checked against the file mis-filed 15 documents).
  * Everything the EDPS did not number takes a slug built from the date it
    published and its own URL segment: ``2018-03-16-guidelines-use-cloud-
    computing-services-european``. The segment is the EDPS:s, not ours; one
    riktlinje has no alias and its segment is the bare node id
    (``2018-01-15-node-4529``).

**Swedish is published for the summary, not always for the document.** 212 of
the 442 rows offer a Swedish file, and 82 of those are the *executive summary*
of an English-only yttrande: ``13-07-18_smart_borders_ex_sum_sv.pdf`` sits
behind the same language badge as ``13-07-18_smart_borders_en.pdf``, and nothing
in the markup says one is two pages and the other forty. The file name does say
so, so a Swedish file is taken only when its name is the English name with the
language token swapped. That check also rejects what the EDPS:s own listing has
mis-linked: the Swedish option under "Visa Information System (VIS)" serves
``05-06-15_pnr_canada_sv.pdf``, which is a different yttrande.

Stored per document under ``site/data/downloaded/guidance/edps/``: an
``edps-<serie>-<slug>.json`` record and the ``.pdf`` document.
"""

import re
import time
from pathlib import Path

from bs4 import BeautifulSoup

from ..lib import browser
from ..lib.harvest import select_pending, stored_index, walk_records
from ..lib.net import BROWSER_UA as USER_AGENT
from ..lib.net import fetcher, make_session
from ..lib.pdftext import pdf_first_page_text_bytes
from ..lib.util import document_extension, english_date, href, normalize_space
from .issuers import EDPS, LOPNUMMER_FORST, number_slug

BASE = EDPS.base
# the by-type view of one series; the EDPS's own doctype segment names it
VIEW = BASE + "/data-protection/our-work/our-work-by-type/%s_en"
# a Chrome profile shared across the run, so the WAF challenge is solved once
PROFILE = ".chrome-profile"
# seconds a challenged navigation is left alone before the DOM is read. Measured
# against this site: 7 completes every page, and the two longer waits are what a
# page that came back short is retried with.
SETTLE = (7.0, 20.0, 30.0)

# the EDPS's own number as its cover prints it: "Opinion 11/2023", on the line
# under the date. Some covers set the body's name in front of it ("EDPS Opinion
# 5/2024"), so the word is matched and whatever precedes it is not.
RE_COVER_NUMMER = re.compile(r"\bOpinion\s+(\d{1,3})/(\d{4})\b", re.I)
# the pager's own links, which is how far the view runs
RE_PAGE = re.compile(r"\?page=(\d+)")
# every language and dedupe token a file name carries at its tail: the language
# code the EDPS suffixes with, the numeric suffix Drupal adds when it stores a
# second file under a name it already has, and -- on a translation re-uploaded
# after 2021 -- both, with the English code still in the middle
# ("…_guidelines_en_95_de.pdf"). Removing all of them leaves the document's own
# name, which is what tells a translation from a translated *summary*.
RE_NAME_TAIL = re.compile(r"(?:_(?:[a-z]{2})|_\d+)+\.pdf$", re.I)
# the language suffix on a leaf URL, which is the page's language and not the
# document's
RE_URL_LANG = re.compile(r"_[a-z]{2}$")
# a leaf URL that already leads with the date the row states
RE_URL_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}-")
# a `nummer` that is one of the EDPS's own numbers rather than a date slug --
# what `number_slug` made of "Opinion 11/2023"
RE_SLUGGAT_NUMMER = re.compile(r"^(\d{2})-(\d{4})$")

# how one document of each series is named in running text. The EDPS's own name
# for the series with the body in front of it, because "Yttrande 11/2023" alone
# would not say whose -- the EDPB numbers a Rekommendation 01/2020 too.
KALLAS = {"riktlinjer": "EDPS riktlinjer", "yttranden": "EDPS yttrande"}


def citation(serie, nummer, antagen):
    """How one document is cited: "EDPS yttrande 11/2023" where the EDPS gave it
    a number, "EDPS riktlinjer (2018)" where it gave none.

    A citation to an unnumbered document is the series and the year, because
    that is all the EDPS itself gives it -- its cover prints a title and a date.
    The subject stays where the EDPS put it, in the title beside this."""
    match = RE_SLUGGAT_NUMMER.fullmatch(nummer)
    if match:
        return "%s %s/%s" % (KALLAS[serie], *match.groups())
    assert antagen, "%s/%s is undated and unnumbered both" % (serie, nummer)
    return "%s (%s)" % (KALLAS[serie], antagen[:4])


def basefile(serie, nummer):
    """The harvest basefile of one document ("edps/yttranden/11-2023")."""
    return "%s/%s/%s" % (EDPS.kod, serie, EDPS.serie(serie).slug(nummer))


def last_page(html):
    """How far the view's pager runs. Pure over the listing HTML.

    The number the pager prints, not a number this walks to blindly: the walk
    stops on "no new leaf" as well, because a Drupal view that has been filtered
    can keep answering past its own last page."""
    pages = [int(n) for n in RE_PAGE.findall(html)]
    return max(pages) if pages else 0


def document_key(url):
    """One file's name with every language and dedupe token removed -- what two
    language versions of the *same* document share.

    ``13-07-18_smart_borders_en.pdf`` and ``13-07-18_smart_borders_sv.pdf`` give
    ``13-07-18_smart_borders``; ``13-07-18_smart_borders_ex_sum_sv.pdf``, which
    is the Swedish *sammanfattning* of the English yttrande, gives
    ``13-07-18_smart_borders_ex_sum`` and is therefore not it."""
    return RE_NAME_TAIL.sub("", url.rsplit("/", 1)[-1].lower())


def choose_file(files):
    """``(sprak, url)`` for one file group: the Swedish text where the EDPS has
    published one *of this document*, else the English one, else None.

    Swedish is preferred because it is the text a Swedish reader should meet,
    and checked because the badge lies twice over -- 82 of the 212 Swedish files
    in this corpus are the executive summary of an English-only yttrande, and
    one is a different yttrande outright. `document_key` is what settles it."""
    first = {}
    for lang, url in files:
        first.setdefault(lang, url)
    if "English" not in first:
        return None
    english = first["English"]
    swedish = first.get("Swedish")
    if swedish is not None and document_key(swedish) == document_key(english):
        return ("sv", swedish)
    return ("en", english)


def _file_group(block):
    """The (language name, absolute url) pairs of one ``.file-label`` group,
    PDFs only. A group can carry an .xlsx checklist or a .docx draft behind the
    same badge as the document, and one row links a .docx and nothing else."""
    return [(normalize_space(option.get_text()), BASE + option["data-url"])
            for option in block.select("option[data-url]")
            if option.get("data-extension") == "pdf"]


def listing_rows(html):
    """The document rows one listing page names. Pure over the HTML.

    ``files`` is a *list of groups* in the page's own order, not one flat list:
    a row that publishes a document in parts lists each part under its own
    ``.file-label`` ("Part I: Records and threshold assessment"), and a row that
    publishes annexes lists those the same way. The first group is the document
    -- checked against the six riktlinjer that carry more than one."""
    rows = []
    for article in BeautifulSoup(html, "html.parser").select(
            "article.node--type-edpsweb-publication"):
        title = article.select_one("h3.node__title a")
        assert title is not None, "an EDPS listing row carries no title link"
        date = article.select_one(".edpsweb-publication-date")
        assert date is not None, "%s is listed with no date" % href(title)
        rows.append({
            "titel": normalize_space(title.get_text()),
            "url": BASE + href(title),
            "antagen": english_date(" ".join(normalize_space(part.get_text())
                                             for part in date.select("div"))),
            "files": [group for group in
                      (_file_group(block) for block in article.select(
                          ".field--name-field-edpb-files > div"))
                      if group],
            "amnesord": [normalize_space(item.get_text()) for item in
                         article.select(".field--name-field-edpsweb-subjects "
                                        ".field__item a")],
        })
    return rows


def date_slug(url, antagen):
    """The address of a document the EDPS gave no number: the date it published
    and its own URL segment.

    The segment is taken as the EDPS wrote it, minus the page-language suffix it
    ends in and minus a date it already leads with -- the newer leaves are
    ``2025-11-11-guidance-risk-management-…`` and the older ones plain
    ``web-services``, and both come out dated once."""
    segment = RE_URL_LANG.sub("", url.rstrip("/").rsplit("/", 1)[-1])
    assert antagen, "%s is listed with no date to file it under" % url
    return "%s-%s" % (antagen, RE_URL_DATE.sub("", segment))


def printed_nummer(text):
    """The number an EDPS text states ("11/2023"), or None. Pure, so the same
    reading serves a PDF cover here and the parse's own re-read of it.

    Searched for rather than matched against the whole line: a cover sets the
    date above the number and a listing title runs the number into the sentence
    ("EDPS Opinion 20/2026 on the Proposal for ...")."""
    match = RE_COVER_NUMMER.search(text or "")
    return "%s/%s" % match.groups() if match else None


def nummer_slug(nummer):
    """The `nummer` a document the EDPS numbered is filed under: "11/2023" ->
    "11-2023", padded and ordered exactly as the EDPB's are, so the address
    still reproduces the citation. A document the EDPS did not number is filed
    under `date_slug` instead -- the two shapes cannot collide, one starting
    with a two-digit löpnummer and the other with a four-digit year."""
    return number_slug(nummer, LOPNUMMER_FORST)


def cover_nummer(pdf_bytes):
    """The number a yttrande's own cover prints ("11/2023"), or None.

    None is a real answer here, and two different ones: an EDPS yttrande from
    before 2020 has no number to print, and a scanned one prints its number as
    an image `pdftotext` cannot read. `edps_sync` tells them apart by asking the
    listing title afterwards, and counts each."""
    if document_extension(pdf_bytes) != ".pdf":
        # served something that is not a PDF behind a .pdf address. Not a
        # document, and not this harvest's to repair: the caller counts it.
        return None
    return printed_nummer(pdf_first_page_text_bytes(pdf_bytes))


def known_identities(root):
    """``{leaf url: (serie, nummer, sprak, dokument url)}`` from the records
    already stored.

    What keeps a steady run from re-reading 400 covers: a row whose leaf address
    and whose chosen file are both unchanged is the document we already named."""
    directory = Path(root) / EDPS.kod
    if not directory.exists():
        return {}
    return stored_index(directory, "source_url",
                        lambda record: (record["serie"], record["nummer"],
                                        record["sprak"],
                                        record.get("dokument_url")))


def _navigate(chrome, url):
    """One challenged page, retried with a longer settle. A page that never
    completes raises: an index page silently missing is a slice of the corpus
    silently missing."""
    for settle in SETTLE:
        try:
            return chrome.html(url, marker="EDPS", settle=settle)
        except browser.IncompleteNavigation:
            continue
    raise RuntimeError("%s never completed behind the WAF challenge" % url)


def walk_view(chrome, doctype, delay):
    """Every row one series' view names, deduplicated by leaf URL.

    Stops on two consecutive pages naming no new leaf rather than on an empty
    page: a Drupal pager repeats its last rows past the end (EASA's library
    reports 2,006 rows for 584 documents to a walk that trusts it)."""
    view = VIEW % doctype
    html = _navigate(chrome, view)
    limit, page, quiet, seen, rows = last_page(html), 0, 0, set(), []
    while page <= limit and quiet < 2:
        if page:
            time.sleep(delay)
            html = _navigate(chrome, "%s?page=%d" % (view, page))
        fresh = [row for row in listing_rows(html) if row["url"] not in seen]
        seen.update(row["url"] for row in fresh)
        rows += fresh
        quiet = quiet + 1 if not fresh else 0
        page += 1
    return rows


def edps_sync(root, full=False, only=None, limit=None, delay=0.5):
    """Harvest the EDPS's riktlinjer and yttranden off its two by-type views.

    **One scope for both series**, the way the EBA's two come off one walk. They
    do not share pages -- a riktlinje and a yttrande are listed under different
    doctypes -- but they share a host, a WAF challenge and a Chrome profile, and
    two scopes would run two browsers at edps.europa.eu at once and solve the
    challenge twice (rule:respect-politeness).

    Every count a run prints is one outcome, and the declines are named apart
    (rule:instrument-failures): a row publishing no PDF at all, a row whose file
    could not be named, a cover that states a number its listing contradicts.
    """
    session = make_session(USER_AGENT)
    known = known_identities(root)
    pending, counts = [], dict.fromkeys(
        ("rows", "no pdf", "swedish", "english", "cover number",
         "number from title only", "cover disagrees with title", "unnumbered",
         "covers read"), 0)
    with browser.DetachedChrome(Path(root) / EDPS.kod / PROFILE,
                                settle=SETTLE[0]) as chrome:
        rows = [(serie, row) for serie in EDPS.koder
                for row in walk_view(chrome, EDPS.serie(serie).doctype, delay)]
    for serie, row in rows:
        counts["rows"] += 1
        chosen = choose_file(row["files"][0]) if row["files"] else None
        if chosen is None:
            # the EDPS published this row as a .docx, or published no file with
            # it at all. Not a document this source can carry, and not a fetch
            # that broke -- which is why it is counted here and not raised.
            counts["no pdf"] += 1
            continue
        sprak, document = chosen
        counts["swedish" if sprak == "sv" else "english"] += 1
        body = None
        if known.get(row["url"], (None, None, None, None))[3] == document:
            nummer = known[row["url"]][1]
        else:
            body = fetcher(session, document, timeout=180)()
            counts["covers read"] += 1
            time.sleep(delay)
            nummer = _nummer(row, cover_nummer(body), counts)
        pending.append(({
            "basefile": basefile(serie, nummer), "utgivare": EDPS.kod,
            "serie": serie, "nummer": nummer,
            "sprak": sprak, "titel": row["titel"],
            "antagen": row["antagen"], "version": None,
            "konsultation_url": None, "amnesord": row["amnesord"],
            "source_url": row["url"], "dokument_url": document,
        }, (lambda got=body: got) if body is not None
            else fetcher(session, document, timeout=180)))
    print("edps: %s" % ", ".join("%d %s" % (n, name)
                                 for name, n in counts.items()))
    return walk_records(
        root, select_pending(pending, only,
                             "the EDPS listings carry no document %s"),
        delay=delay, full=full, limit=limit, scope=EDPS.kod)


def _nummer(row, printed, counts):
    """The identity of one row: the number the EDPS gave the document, slugged,
    or a date-and-URL slug where it gave none.

    The cover is the source and the listing title is the check, because the
    title is silent about the number in two rows out of three. Where the cover
    is silent too the title is taken instead, and counted apart -- that is the
    scanned yttrande, whose number is set as an image, and it must not be
    recorded as a yttrande the EDPS never numbered."""
    named = printed_nummer(row["titel"])
    if printed is None and named is None:
        counts["unnumbered"] += 1
        return date_slug(row["url"], row["antagen"])
    if printed is None:
        counts["number from title only"] += 1
        return nummer_slug(named)
    if named is not None and named != printed:
        # the cover wins: it is the document, the title is a listing about it.
        # Counted so a run says how often the two disagree rather than picking
        # silently -- one of them is wrong and neither is this harvest's to fix.
        counts["cover disagrees with title"] += 1
    counts["cover number"] += 1
    return nummer_slug(printed)
