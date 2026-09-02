"""Harvesters for Europeiska dataskyddsstyrelsens riktlinjer och
rekommendationer, and for the artikel 29-gruppens vägledningar the EDPB
endorsed.

Two routes, because the documents live in two places.

**The EDPB site** (edpb.europa.eu, Drupal) publishes one page per document
under ``/documents/{guideline,recommendation}/<slug>_<lang>``, in all 24
official languages, and exposes no API -- ``/jsonapi`` is a 404 and the
listing is a paginated view. What it does expose is a **sitemap**
(``/sitemap.xml``, five pages, ``lastmod`` per entry), and the document type
sits in the URL path, so the sitemap is the index this walks: it names every
document page of both series in both languages in five requests.

Each page is read twice, once per language, because the two carry different
things and both are needed:

  * the **English** page states the number. It always does; the Swedish one
    sometimes drops it (the Swedish page for Guidelines 8/2022 is titled
    "Riktlinjer om fastställande av ansvarig tillsynsmyndighet …" with no
    number at all), and the number is this document's identity, so it is taken
    from the page that always states it.
  * the **Swedish** page states the title and links the Swedish PDF, which is
    the text the site publishes. 48 of the 51 documents have one; the rest are
    published in English only, which the record says so that a page showing
    English text can say so too.

The rest of the metadata is read from whichever language is being published:
the adoption date (a ``<time datetime>``, so no date parsing), the version
badge ("Final version", "Version 2.0" -- a riktlinje is adopted, consulted on
and re-adopted, and which version this is has to be stated), the link to the
pre-consultation first version, and the EDPB's own topic tags.

**The Commission newsroom** (ec.europa.eu/newsroom/article29) is where the
artikel 29-gruppens own documents actually live. The EDPB pages that endorse
them are stubs where they exist at all -- see `edpb_data.WP29` for what is wrong
with them -- so this route goes to the newsroom item recorded there, whose page
links the English PDF beside a ZIP of every language version. The Swedish text
is the ``wp<N>…_sv.pdf`` member of that ZIP (never the ``…_annex_sv.pdf`` one,
which is a separate annex document), so the ZIP is fetched, the member
extracted, and only the extracted PDF stored. Not every item carries such a
ZIP, and the ones that do not are published here in English.

Neither route paginates and both corpora are small and fully enumerable, so the
JK/ARN idiom applies: one walk per run, fetching what is new or changed, no
watermark.

Stored per document under ``site/data/downloaded/guidance/edpb/{serie}/``: a
``<slug>.json`` record and the ``<slug>.pdf`` document.
"""

import io
import re
import time
import zipfile
from datetime import date
from functools import partial
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..lib import compress
from ..lib.harvest import pdf_path, select_pending, walk_records
from ..lib.net import BROWSER_UA as USER_AGENT
from ..lib.net import fetcher, make_session, request
from ..lib.util import document_extension, href, normalize_space
from .edpb_data import NEWSROOM, WP29, WP29_DUPLICATE_PAGES
from .issuers import EDPB

# the series this issuer's own index is walked for -- the two open ones, which
# the EDPB site publishes under a document-type path segment. The closed WP29
# series has no such page and comes from the newsroom instead (`wp29_sync`).
HARVESTED = tuple(s.kod for s in EDPB.series if s.doctype)

# "05/2020", and the unpadded "1/2018" the EDPB writes just as often. Narrower
# than `issuers.RE_PAR`, which only has to split an already-known number: this
# one searches free text and a four-digit year is what tells a number from a
# date range.
RE_NUMBER = re.compile(r"\b(\d{1,2})/(\d{4})\b")

SITEMAP = "https://www.edpb.europa.eu/sitemap.xml"
SITEMAP_PAGES = 5
# the EDPB was established by artikel 68 in the allmänna dataskyddsförordningen
# and held its first plenary in May 2018, so no document of its own predates it
EDPB_FOUNDED = 2018
MAX_YEAR = date.today().year + 1
# every document page: /documents/<type>/<slug>_<lang>. The type is the series'
# own `doctype`, so one pattern reads both series out of the sitemap.
RE_DOCUMENT_PAGE = re.compile(
    r"^https://www\.edpb\.europa\.eu/documents/([a-z0-9-]+)/(.+)_([a-z]{2})$")

# the document's own file among the page's attachments: the EDPB names every
# published language version with its language suffix (a "_0" tail is Drupal's
# de-duplication of a re-uploaded file). Summaries, factsheets, annexes,
# consultation reports and the track-changes DOCX sit beside it under names of
# their own, and the document itself is always the first file the page lists --
# so first-in-page-order plus the language suffix identifies it without a
# blocklist of what the others are called.
DOCUMENT_FILE = r"_%s(_\d+)?\.pdf$"


# --------------------------------------------------------------------------
# identity
# --------------------------------------------------------------------------
#
# Where a record and its PDF go, how ``--only`` narrows the index and the walk
# that stores both are the shared record walk's (`lib.harvest.walk_records`):
# the sitemap and the WP29 registry are enumerated whole on every run, so there
# is no depth to stop short of. What is this source's own is the basefile.

def basefile(serie, nummer):
    """The harvest basefile of one document ("edpb/riktlinjer/05-2020",
    "edpb/wp/248") -- the issuer, then what its URI carries after it, so a
    basefile and an address are the same string."""
    return "%s/%s/%s" % (EDPB.kod, serie, EDPB.serie(serie).slug(nummer))


# --------------------------------------------------------------------------
# the EDPB site: sitemap -> document pages
# --------------------------------------------------------------------------

def sitemap_document_pages(html_texts):
    """Every document page in the sitemap, grouped as ``{(doctype, slug):
    {lang: url}}``. Pure over the sitemap XML so the grouping is testable
    without network."""
    pages = {}
    for text in html_texts:
        for loc in re.findall(r"<loc>([^<]+)</loc>", text):
            match = RE_DOCUMENT_PAGE.match(loc)
            if match:
                doctype, slug, lang = match.groups()
                pages.setdefault((doctype, slug), {})[lang] = loc
    assert pages, "the EDPB sitemap named no document pages at all"
    return pages


def _files(soup, url):
    """The page's attachments as (filename, absolute url), in page order."""
    return [(href(anchor).rsplit("/", 1)[-1], urljoin(url, href(anchor)))
            for item in soup.select(".document-full__files-item")
            for anchor in [item.find("a", href=True)] if anchor is not None]


def parse_page(html_text, url, lang):
    """One EDPB document page -> its fields. Pure over the HTML.

    ``document`` is the page's own published PDF in `lang`, or None where the
    EDPB publishes no file in that language on that page -- which is how a
    document that exists only in English is recognised, and how the WP29 stubs
    (which carry no file at all) are told apart from real document pages."""
    soup = BeautifulSoup(html_text, "html.parser")
    heading = soup.find("h1", class_="document-full__title")
    assert heading is not None, "%s carries no document title" % url
    adopted = soup.select_one(".document-full__date time[datetime]")
    version = soup.select_one(".document-full__version")
    consultation = soup.select_one(".document-full__public-consultation a[href]")
    wanted = re.compile(DOCUMENT_FILE % lang, re.I)
    return {
        "titel": normalize_space(heading.get_text(" ", strip=True)),
        "antagen": adopted["datetime"][:10] if adopted else None,
        "version": normalize_space(version.get_text(" ", strip=True))
        if version else None,
        "konsultation_url": urljoin(url, href(consultation))
        if consultation else None,
        "amnesord": [normalize_space(a.get_text(" ", strip=True)).lstrip("#")
                     for a in soup.select(
                         ".document-full__relevant-topics-list-item-link")],
        "document": next((link for name, link in _files(soup, url)
                          if wanted.search(name)), None),
    }


def series_number(titel, document_url):
    """The document's number in the EDPB's own ``NN/ÅÅÅÅ`` form.

    Normally the title states it and that is the end of it -- the document
    naming itself is the best source there is. The newest guidelines have
    stopped doing so ("Guidelines on processing of personal data through
    blockchain technologies" is 02/2025 and says so nowhere in its title), and
    for those the number is read off the **file name** the EDPB published the
    document under, which carries it as a six-digit token.

    That token is written both ways round across the years
    (``edpb_guidelines_202005_consent`` is 05/2020, ``edpb_guidelines_012021_
    pdbnotification`` is 01/2021), so the halves are told apart by which one is
    a year the EDPB has existed in: it was established by the GDPR in 2018, so
    of the two readings of ``202005`` only 05/2020 has a possible year in it."""
    match = RE_NUMBER.search(titel)
    if match:
        return match.group(0)
    # underscore-delimited, so a word boundary is no help: the run of six digits
    # has to be bounded by non-digits instead
    for token in re.findall(r"(?<!\d)(\d{6})(?!\d)",
                            document_url.rsplit("/", 1)[-1]):
        for serial, year in ((token[:2], token[2:]), (token[4:], token[:4])):
            if EDPB_FOUNDED <= int(year) <= MAX_YEAR:
                return "%s/%s" % (serial, year)
    return None


def _sitemap_page(session, n, delay):
    """One page of the sitemap. The index walk sleeps like every other fetch in
    this module (rule:respect-politeness): five pages per series, and both open
    series walk it."""
    text = request(session, "GET", "%s?page=%d" % (SITEMAP, n), timeout=120).text
    time.sleep(delay)
    return text


def _fetch_page(session, url, lang, delay):
    page = parse_page(request(session, "GET", url, timeout=120).text, url, lang)
    time.sleep(delay)
    return page


def edpb_sync(root, serie, full=False, only=None, limit=None, delay=0.5):
    """Harvest one of the two open EDPB series off the sitemap.

    Identity comes from the English page (the only one that always states the
    number), text and title from the Swedish page where the EDPB has published
    one. A page whose slug belongs to an endorsed WP29 document is skipped here
    -- `wp29_sync` owns those, from the newsroom where their text actually is.
    """
    session = make_session(USER_AGENT)
    doctype = EDPB.serie(serie).doctype
    pages = sitemap_document_pages(_sitemap_page(session, n, delay)
                                   for n in range(1, SITEMAP_PAGES + 1))
    wp29_slugs = {wp.page for wp in WP29} | WP29_DUPLICATE_PAGES
    pending = []
    for (kind, slug), langs in sorted(pages.items()):
        if kind != doctype or "https://www.edpb.europa.eu/documents/%s/%s" \
                % (kind, slug) in wp29_slugs:
            continue
        english = _fetch_page(session, langs["en"], "en", delay)
        swedish = (_fetch_page(session, langs["sv"], "sv", delay)
                   if "sv" in langs else None)
        published = swedish if swedish and swedish["document"] else english
        assert published["document"], (
            "%s publishes no PDF in either language" % langs["en"])
        # identity off the *English* page: it always states the number, and the
        # Swedish one sometimes drops it (the Swedish page for Guidelines 8/2022
        # is titled "Riktlinjer om fastställande av ansvarig tillsynsmyndighet…")
        number = series_number(english["titel"], english["document"]
                               or published["document"])
        assert number, ("%s states no series number in its title (%r) or its "
                        "file name -- an unnumbered guidance page is either a "
                        "WP29 stub, which belongs to the wp scope, or a new "
                        "shape this harvest has not seen"
                        % (langs["en"], english["titel"]))
        record = {
            "basefile": basefile(serie, number), "serie": serie,
            "nummer": number,
            "sprak": "sv" if published is swedish else "en",
            "titel": published["titel"],
            "antagen": published["antagen"] or english["antagen"],
            "version": published["version"] or english["version"],
            "konsultation_url": published["konsultation_url"],
            "amnesord": published["amnesord"] or english["amnesord"],
            "source_url": langs.get("sv" if published is swedish else "en"),
            "dokument_url": published["document"],
        }
        pending.append((record, fetcher(session, published["document"],
                                        timeout=180)))
    return walk_records(
        root, select_pending(pending, only,
                             "the EDPB index carries no document %s"),
        delay=delay, full=full, limit=limit, scope=serie)


# --------------------------------------------------------------------------
# the Commission newsroom: the endorsed artikel 29-gruppen documents
# --------------------------------------------------------------------------

def newsroom_documents(html_text, url):
    """The download links a newsroom item page offers, in page order. Both link
    shapes the archive uses over its lifetime (``document.cfm?doc_id=`` and the
    newer ``redirection/document/``) reach the same store, and an item mixes
    them, so both are read and neither is reconstructed from an id: the href on
    the page names the newsroom the file is actually in."""
    soup = BeautifulSoup(html_text, "html.parser")
    links = []
    for anchor in soup.find_all("a", href=True):
        target = urljoin(url, href(anchor))
        if re.search(r"redirection/document/\d+|document\.cfm\?doc_id=\d+",
                     target) and target not in links:
            links.append(target)
    assert links, "newsroom item %s offers no download" % url
    return links


def swedish_member(data, number):
    """The Swedish PDF inside a newsroom language ZIP: ``wp248 rev.01_sv.pdf``,
    ``wp250rev01_sv.pdf``, ``wp259 rev 0.1_SE.pdf``. Never the
    ``…_annex_sv.pdf`` member -- two of them ship their annex as a second ZIP
    whose members are named the same way, and an annex is a document of its
    own, not this one's text. Returns the member's bytes, or None when the
    archive holds no Swedish version.

    Two suffixes, because the archive is not consistent about which code names
    the language: most members carry the language code ``sv``, and WP259's
    carry the *country* code ``SE`` (its ZIP holds 22 members, one per official
    language bar English, and ``_SE`` is the only one that can be the Swedish
    of them). Both are read; nothing else in these archives ends that way."""
    archive = zipfile.ZipFile(io.BytesIO(data))
    # no word boundary after the number: the revision runs straight on in most
    # of them ("wp243rev01_sv.pdf"), and only some space it ("wp248 rev.01_
    # sv.pdf"). What must not follow is another digit.
    name = next((n for n in archive.namelist()
                 if re.match(r"wp\s*%s(?!\d)" % number, n, re.I)
                 and n.lower().endswith(("_sv.pdf", "_se.pdf"))
                 and "annex" not in n.lower()), None)
    return archive.read(name) if name else None


def _wp_document(session, item_url, number, delay):
    """One endorsed WP29 document's text: the Swedish version out of the item's
    language ZIP, else the English PDF the item links directly. Returns
    ``(language, source url, fetch)``.

    An item offers between one and three downloads and in no fixed order, and
    what it offers varies: some carry the language ZIP beside the English PDF,
    some the English PDF alone (WP263 and the position paper), and WP257's
    language archive is a **7-Zip** file, which is not a ZIP and holds no
    member this can read -- so that one is published in English like any other
    document the working party never had translated."""
    links = newsroom_documents(
        request(session, "GET", item_url, timeout=120).text, item_url)
    english = None
    for link in links:
        data = request(session, "GET", link, timeout=300).content
        time.sleep(delay)
        if data[:2] == b"PK":
            assert number, (
                "%s serves a language archive, and the document it holds has "
                "no WP number to name a member by -- the archive's members "
                "have to be read before this one can take a version from it"
                % item_url)
            swedish = swedish_member(data, number)
            if swedish:
                return "sv", link, lambda swedish=swedish: swedish
        elif english is None and document_extension(data) == ".pdf":
            english = (link, data)
    assert english, ("newsroom item %s serves neither a Swedish version nor an "
                     "English PDF" % item_url)
    return "en", english[0], lambda: english[1]


def wp29_sync(root, full=False, only=None, limit=None, delay=0.5):
    """Harvest the endorsed artikel 29-gruppen vägledningar.

    The record carries no title or adoption date for the documents that state
    both on their own cover: the EDPB's stub pages state them wrongly (WP250's
    is titled "Dataskyddsombud", which is WP243's subject), and the newsroom
    states them in English only. Both are read off the document's own Swedish
    cover instead -- see `parse.wp_cover` -- which is the same departure `rs`
    makes for Försäkringskassans serienummer, and for the same reason: the
    index is not to be trusted about the document where the document speaks for
    itself. The one document that sets no cover carries both from the registry,
    where they are written down off the EDPB's own page for it.

    A registry entry carrying its own `document` is fetched straight from there
    and the newsroom is not consulted at all: the two BCR application forms were
    published as Word forms, so the only PDFs of them are a tillsynsmyndighets
    conversions (`edpb_data.HBDI`, with what each was verified against recorded
    beside it)."""
    session = make_session(USER_AGENT)
    pending, held = [], 0
    for wp in WP29:
        bf = basefile("wp", wp.slug)
        if only and bf != only:
            continue
        # resolving one of these costs the language ZIP -- 10 to 28 MB, since
        # the archive offers no way to reach a single member -- and the corpus
        # is *closed*: the working party ceased to exist in 2018 and cannot
        # revise what it published. So a document already on disk is not
        # re-resolved, and a routine run costs nothing. `--force` re-verifies.
        if compress.exists(pdf_path(root, bf)) and not full:
            held += 1
            continue
        lang, document_url, fetch = (
            ("en", wp.document, fetcher(session, wp.document, timeout=180))
            if wp.document else
            _wp_document(session, NEWSROOM % wp.item, wp.number, delay))
        pending.append(({
            "basefile": bf, "serie": "wp", "nummer": wp.slug,
            "revision": wp.revision, "sprak": lang,
            "source_url": wp.page, "dokument_url": document_url,
            "newsroom_url": NEWSROOM % wp.item,
        }, fetch))
    assert pending or held or not only, \
        "no endorsed WP29 document is called %s" % only
    seen, new = walk_records(root, pending, delay=delay, full=full, limit=limit,
                             scope="wp")
    return seen + held, new


# --------------------------------------------------------------------------
# the runners, by series
# --------------------------------------------------------------------------

# the two open series come off the EDPB site's own index, one harvest
# parametrized by which; the closed WP29 one comes from the newsroom instead.
# `download.SYNC` prefixes each key with this issuer's kod to make the scope.
SYNC = {**{kod: partial(edpb_sync, serie=kod) for kod in HARVESTED},
        "wp": wp29_sync}
