"""The ft (Förvaltningsrättslig tidskrift) walk: the platform's archive page,
then each issue's open-access cards and the per-article PDFs they name.

The platform moved hosts as the walk's own upstream: the journal issued at
ft.nu serves its archive at forvaltningsrattslig.org, and the archive page's
year filter is the whole backlist (1938 and all), so nothing here is kept by
hand.

The platform lists an issue's articles as cards, but the cards of its
subscription articles name only the platform's paywalled download. The cards
that carry the "Open Access" badge are the ones that name a public PDF, and
the walk records those: an issue that sets no open-access card contributes
no records, which is the ordinary state of its pre-2025 issues (the journal
went open access in 2025 -- see `KNOWN-GAPS.md`). An issue page that sets no
issue number at all is not an issue page, and the walk says so.

Each issue page's own H1 states its number and year ("Nummer 2026 1"), and
the record takes both off that page, not off the archive line that linked
it. The article's opening page is not in the listing: the PDF's first leaf
prints the issue's running table of contents, and the article's own line
there ends in the page (`parse._ft_start_page` reads it).
"""

import re
import time
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..lib import harvest, net
from ..lib.harvest import select_pending
from ..lib.util import approximate_date, normalize_space, record_path
from .journals import FT

__all__ = ["ft_sync"]

# The archive page's year filter, one input group per year: the addon names
# the year, the button group beside it its issues.
RE_YEAR = re.compile(r"(\d{4})")

# The issue page's H1 ("Nummer 2026 1"). The page the archive line linked is
# not a guarantee of the number it names, so the record reads both off the
# page that serves the article.
RE_NUMMER = re.compile(r"Nummer\s+(\d{4})\s+(\d+)\b")


def _ft_issues(session):
    """The archive's whole issue inventory: every year the filter names, every
    issue button beside it, the (year, issue, url) triple the archive page
    itself states."""
    html = net.request(session, "GET", FT.listings[0]).text
    soup = BeautifulSoup(html, "html.parser")
    issues = []
    for div in soup.select("div.input-group"):
        addon = div.select_one("span.input-group-addon")
        if addon is None:
            continue
        m = RE_YEAR.search(addon.get_text())
        if m is None:
            continue
        year = m.group(1)
        for a in div.select("div.btn-group a"):
            issue = a.get_text(strip=True)
            if not issue.isdigit():
                continue
            href = a.get("href")
            assert isinstance(href, str), \
                "an ft issue button is not a link"
            issues.append({"year": year, "issue": issue,
                           "url": urljoin(FT.base, href)})
    if not issues:
        raise ValueError("the ft archive names no issues -- the page moved")
    return issues


def _ft_records_from_page(html, issue_url):
    """One issue page's open-access cards as records, in the issue's own
    order, each naming its own PDF as its document. The subscription cards
    name no public PDF and contribute nothing; an issue that sets no
    open-access card is an ordinary pre-2025 issue, and it contributes
    nothing without saying anything. A page that sets no issue number, or an
    open-access card that names no title, is a page the journal did not set,
    and the walk says so."""
    soup = BeautifulSoup(html, "html.parser")
    h1 = next((h for h in soup.find_all("h1")
               if h.get_text(strip=True).startswith("Nummer")), None)
    if h1 is None:
        raise ValueError("ft %s: no issue number on the page -- "
                         "the template moved" % issue_url)
    m = RE_NUMMER.match(h1.get_text(strip=True))
    if m is None:
        raise ValueError("ft %s: an unreadable issue number %r"
                         % (issue_url, h1.get_text(strip=True)))
    year, issue = m.group(1), m.group(2)
    records = []
    for li in soup.select("li.list-group-item"):
        link = li.select_one("a[href*='downloadopenaccess']")
        if link is None:
            continue             # a subscription card: no public PDF
        href = link.get("href")
        assert isinstance(href, str), "an ft open-access link is not a link"
        title_el = li.find("b")
        title = normalize_space(title_el.get_text(" ", strip=True)) \
            if title_el is not None else ""
        if not title:
            raise ValueError("ft %s %s: an open-access card names no title"
                             % (year, issue))
        fattare = None
        for p in li.find_all("p"):
            # the card's abstract can name a link of its own, and it is
            # not the author: the card's author is its first other link
            if "abstract" in (p.get("class") or []):
                continue
            a = p.find("a")
            if a is not None:
                fattare = normalize_space(a.get_text(" ", strip=True)) or None
                break
        seq = "%02d" % (len(records) + 1)
        records.append({
            "basefile": "ft/%s-%s-%s" % (year, issue, seq),
            "journal": "ft",
            "year": year,
            "issue": issue,
            "seq": seq,
            "titel": title,
            "fattare": fattare,
            "sammanfattning": None,
            "source_url": issue_url,
            "document_url": urljoin(FT.base, href),
        })
    return records


def ft_sync(root, full=False, only=None, limit=None, delay=0.5):
    """The journal's whole archive: the one archive page, then the issue
    pages newest-first and every open-access PDF the issue names. A
    watermark on the issue's year stops a caught-up run once its newest
    issues are on disk in full, and never re-fetches an issue page whose
    articles are all stored (the pre-2025 issues set no open-access cards
    at all, and they sit behind the stop). `--only ft/2026-1-01` names its
    own issue, which is then the only issue page fetched and the walk
    stores that one document."""
    session = net.make_session(net.BROWSER_UA)
    issues = _ft_issues(session)
    if only:
        year, issue, _seq = only.split("/", 1)[1].split("-")
        issues = [i for i in issues
                  if (i["year"], i["issue"]) == (year, issue)]
        pending = []
        for one in issues:
            html = net.request(session, "GET", one["url"]).text
            time.sleep(delay)
            for r in _ft_records_from_page(html, one["url"]):
                pending.append(
                    (r, (lambda u=r["document_url"]:
                         net.request(session, "GET", u).content)))
        return harvest.walk_records(
            root, select_pending(pending, only,
                                 "the ft archive carries no article %s"),
            delay=delay, full=full, limit=limit, scope="ft")
    watermark = harvest.HarvestWatermark(
        Path(root) / "ft" / ".watermark.json",
        lookahead_limit=3, safety_days=30)
    # newest issue first: the journal publishes its open access in issues,
    # so a caught-up run proves its newest issues are complete and stops,
    # and the archive behind it is never re-read
    issues.sort(key=lambda i: (int(i["year"]), int(i["issue"])), reverse=True)

    def items():
        for issue in issues:
            html = net.request(session, "GET", issue["url"]).text
            time.sleep(delay)
            yield from _ft_records_from_page(html, issue["url"])

    def item_key(record):
        return harvest.document_item_key(
            record, record_path(root, "ft", record["basefile"]),
            harvest.pdf_path(root, record["basefile"]),
            # the platform states the year, not the day: the year's middle
            date=approximate_date(record["year"]))

    def resolve(record):
        return harvest.resolve_document(
            record, record_path(root, "ft", record["basefile"]),
            harvest.pdf_path(root, record["basefile"]),
            lambda: net.request(session, "GET",
                                record["document_url"]).content,
            harvest.verify_pdf, full=full, delay=delay)

    result = harvest.walk(
        items(), resolve=resolve, item_key=item_key, watermark=watermark,
        full=full, limit=limit, only=only, scope="ft")
    return result.seen, result.new