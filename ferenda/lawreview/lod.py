"""The lod (Lov & Data) walk: the journal index page's year links, each
year page's issue cards, each issue page's table of contents, the article's
own page being its document.

Lovdata publishes the journal at lod.lovdata.no. The index page carries a
year navigation (2018 to now) and doubles as the newest year's page, so its
own issue cards come first without a second fetch. Each year page sets one
card per issue. A card of a web-readable issue links the issue page
(``/journal/2022/3``); a card of a print-only issue (the 2018-2021 volumes,
and every volume before the site) links the issue's full PDF instead, so
the walk reads those years and takes nothing off them -- no year floor is
coded, and a volume Lovdata later republishes as pages joins on its own.

An issue page states its number in its H1 ("Innhold nr. 151 3/2022" -- the
running number is the journal's own count since 1984, and the walk does not
keep it), its publication day in the H2 under it ("2022-10-28", the date
every record of the issue carries) and sets its table of contents as one
list: a theme heading ("Leder", "Artikler", "Nytt om personvern", ...)
opens each section, and each entry links the article's own page, its title
in the entry's H3. The theme above an entry is the entry's kind. The
journal prints no page numbers on the web edition, so an article's place in
the issue is the record's sequence number, and the identifier stops at the
issue (`model._id_lod`).

The walk runs newest-first on the harvest watermark's caught-up gate, the
way svjt's does: the issues, once published, receive no new article, so a
caught-up run fetches the index page, reads the newest issue's page behind
it, meets nothing but already-stored articles and stops there -- two
listing fetches, not a re-walk of every year and issue. Only a first run
or a `--full` run walks the whole depth.

The article addresses the site sets carry raw spaces and non-ASCII letters
("/article/2022/10/Nytt om personvern"); the record states the address
percent-encoded, so everything downstream handles one form.
"""

import re
import time
from pathlib import Path
from urllib.parse import quote

from bs4 import BeautifulSoup

from ..lib import harvest, net
from ..lib.harvest import select_pending
from ..lib.util import normalize_hints, record_path
from .journals import LOD

__all__ = ["lod_sync"]

# a year page's address in the index page's year navigation
RE_YEAR_HREF = re.compile(r"lod\.lovdata\.no/journal/(\d{4})$")
# a web-readable issue's address on its year page's card; a print-only
# issue's card links the PDF asset instead and never matches
RE_ISSUE_HREF = re.compile(r"lod\.lovdata\.no/journal/(\d{4})/(\d+)$")
# the issue page's H1 tail ("Innhold nr. 151 3/2022"): number over year,
# the journal's own masthead form
RE_ISSUE_H1 = re.compile(r"(\d+)/(\d{4})\s*$")
# the issue page's own publication day, the H2 under the H1
RE_ISSUE_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})$")
# an entry's article link; nothing else in the table of contents links there
RE_ARTICLE_HREF = re.compile(r"lod\.lovdata\.no/article/")


# An article page is the only kind that sets its running text in
# `section#maincolwidth`, which is what `parse._lod_body` reads -- a
# challenge page, an error page and a listing served in an article's place
# all lack it.
verify_page = harvest.page_verifier('id="maincolwidth"')


def _year_links(html):
    """The index page's year navigation, in the page's own order (newest
    year first)."""
    soup = BeautifulSoup(html, "html.parser")
    years = []
    for a in soup.find_all("a", href=RE_YEAR_HREF):
        href = a.get("href")
        assert isinstance(href, str)
        if href not in years:
            years.append(href)
    if not years:
        raise ValueError("the lod index names no years -- the page moved")
    return years


def _issue_links(html):
    """One year page's web-readable issue addresses, in the page's own order
    (newest issue first). A print-only year's cards link only PDFs, so the
    page contributes nothing -- an expected empty page, not an error."""
    soup = BeautifulSoup(html, "html.parser")
    issues = []
    for a in soup.find_all("a", href=RE_ISSUE_HREF):
        href = a.get("href")
        assert isinstance(href, str)
        if href not in issues:
            issues.append(href)
    return issues


def _lod_records_from_page(html, issue_url):
    """One issue page's table of contents as records, in the issue's own
    order. The page's H1 states the issue's number and year, cross-checked
    against the address the year page linked; the H2 under it states the
    issue's publication day, which every record carries (it drives the
    harvest watermark, and the article's own page states the same day for
    the parse). The theme heading above an entry is the entry's kind and
    stands for every entry until the next theme. The article's own page
    states its author, so the record states none. An issue page that sets
    no entries is a page the journal did not set (the print-only volumes'
    addresses answer with an empty shell), and the walk says so."""
    m = RE_ISSUE_HREF.search(issue_url)
    assert m is not None, issue_url
    url_issue, url_year = m.group(2), m.group(1)
    soup = BeautifulSoup(html, "html.parser")
    contents = soup.select_one("section#frontcol2")
    if contents is None:
        raise ValueError("lod %s/%s: no contents column -- the template "
                         "moved" % (url_issue, url_year))
    h1 = contents.find("h1")
    if h1 is None:
        raise ValueError("lod %s/%s: no issue number on the page -- the "
                         "template moved" % (url_issue, url_year))
    hm = RE_ISSUE_H1.search(h1.get_text(" ", strip=True))
    if hm is None:
        raise ValueError("lod %s: an unreadable issue number %r"
                         % (issue_url, h1.get_text(" ", strip=True)))
    issue, year = hm.group(1), hm.group(2)
    if (issue, year) != (url_issue, url_year):
        raise ValueError("lod %s/%s: the page states issue %s/%s"
                         % (url_issue, url_year, issue, year))
    h2 = contents.find("h2")
    if h2 is None:
        raise ValueError("lod %s/%s: no publication day on the page -- "
                         "the template moved" % (issue, year))
    dm = RE_ISSUE_DATE.match(h2.get_text(" ", strip=True))
    if dm is None:
        raise ValueError("lod %s/%s: an unreadable publication day %r"
                         % (issue, year, h2.get_text(strip=True)))
    date = dm.group(1)
    records = []
    tema = None
    for el in contents.find_all(["h2", "a"]):
        if el.name == "h2" and "theme" in (el.get("class") or []):
            tema = normalize_hints(el.get_text(" ", strip=True))
            continue
        href = el.get("href")
        if not isinstance(href, str) or not RE_ARTICLE_HREF.search(href):
            continue
        h3 = el.find("h3")
        if h3 is None:
            raise ValueError("lod %s/%s: an entry sets no title"
                             % (issue, year))
        title = normalize_hints(h3.get_text(" ", strip=True))
        if not title:
            raise ValueError("lod %s/%s: an entry sets an empty title"
                             % (issue, year))
        url = quote(href, safe=":/")
        seq = "%02d" % (len(records) + 1)
        records.append({
            "basefile": "lod/%s-%s-%s" % (year, issue, seq),
            "journal": "lod",
            "year": year,
            "issue": issue,
            "seq": seq,
            "date": date,
            "kind": tema,
            "titel": title,
            "fattare": None,          # the article's page states it
            "sammanfattning": None,
            "source_url": url,
            "document_url": url,
        })
    if not records:
        raise ValueError("lod %s/%s: the issue page sets no articles"
                         % (issue, year))
    return records


def lod_sync(root, full=False, only=None, limit=None, delay=0.5):
    """The journal's whole web-readable archive, newest-first on the harvest
    watermark's caught-up gate: the index page's own issue cards, then the
    remaining years' pages behind its year navigation, every issue page
    fetched only as the walk reaches it. A caught-up run reads the index
    page and the newest issue's page and stops there. `--only lod/2022-3-05`
    names its own issue, whose address the site's scheme states outright, so
    that one issue page is the only listing fetched and the walk stores that
    one document, the watermark untouched."""
    session = net.make_session(net.BROWSER_UA)
    if only:
        year, issue = only.split("/", 1)[1].split("-")[:2]
        url = "%s/journal/%s/%s" % (LOD.base, year, issue)
        html = net.request(session, "GET", url).text
        time.sleep(delay)
        pending = []
        for r in _lod_records_from_page(html, url):
            u = r["document_url"]
            pending.append(
                (r, (lambda u=u: net.request(session, "GET", u).text)))
        return harvest.walk_records(
            root, select_pending(pending, only,
                                 "the lod listing carries no article %s"),
            delay=delay, full=full, limit=limit, scope="lod",
            document=harvest.page_path, verify=verify_page)
    watermark = harvest.HarvestWatermark(
        Path(root) / "lod" / ".watermark.json",
        lookahead_limit=3, safety_days=30)

    def items():
        index_html = net.request(session, "GET", LOD.listings[0]).text
        time.sleep(delay)
        walked = set()

        def issue_records(page_html):
            for issue_url in _issue_links(page_html):
                if issue_url in walked:
                    continue
                walked.add(issue_url)
                html = net.request(session, "GET", issue_url).text
                time.sleep(delay)
                yield from _lod_records_from_page(html, issue_url)

        # the index page doubles as the newest year's page: its own issue
        # cards first, then the other years behind the navigation (which
        # names the newest year too -- `walked` keeps its issues from being
        # read twice)
        yield from issue_records(index_html)
        for year_url in _year_links(index_html):
            year_html = net.request(session, "GET", year_url).text
            time.sleep(delay)
            yield from issue_records(year_html)

    def item_key(record):
        return harvest.document_item_key(
            record, record_path(root, "lod", record["basefile"]),
            harvest.page_path(root, record["basefile"]),
            # the issue's own publication day, off its page's H2
            date=record["date"])

    def resolve(record):
        return harvest.resolve_document(
            record, record_path(root, "lod", record["basefile"]),
            harvest.page_path(root, record["basefile"]),
            lambda: net.request(session, "GET",
                                record["document_url"]).text,
            verify_page, full=full, delay=delay)

    result = harvest.walk(
        items(), resolve=resolve, item_key=item_key, watermark=watermark,
        full=full, limit=limit, only=only, scope="lod")
    return result.seen, result.new
