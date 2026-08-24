"""The njel (Nordic Journal of European Law) walk: the platform's one
archive page, then each issue page's article summaries and the per-article
PDFs they name.

The journal is on an OJS platform, and the platform's archive page is the
whole inventory: one link per issue (the platform sets each link twice, the
second time labelled "… Issue 2024(1)"), the year and number in the label.
An issue page's article summaries carry the title (a subtitle the platform
sets in small type beneath it), the authors and the issue page range the
article spans -- and a PDF link where the platform has set one.

The platform's PDF link is the article's *view* page, an HTML shell around
the PDF; the same address with `view` written `download` streams the PDF
itself, and that is the document the walk stores. A summary that names no
PDF is an article the platform has not published one for: the record keeps
it and states no document, so the parse mines nothing behind it.
"""

import re
import time
from pathlib import Path

from bs4 import BeautifulSoup

from ..lib import harvest, net
from ..lib.harvest import select_pending
from ..lib.util import approximate_date, normalize_space, record_path
from .journals import NJEL

__all__ = ["njel_sync"]

RE_ISSUE_HREF = re.compile(
    r"journals\.lub\.lu\.se/njel/issue/view/([^/]+)/?$")
# The issue card's own series line ("Vol. 2 No. 2 (2019)"): the volume is
# the year since the journal's first volume in 2018, the number the issue
# within its year, and the parenthesis the year.
RE_ISSUE_SERIES = re.compile(r"Vol\.\s*(\d+)\s+No\.\s*(\d+)\s*\((\d{4})\)")


def _njel_issues(session):
    """The journal's whole issue inventory off the one archive page. Each
    issue's card sets the issue's number on its own series line: the card's
    title link can say anything the journal sets it to (the first card says
    "Inaugural Issue"), and the platform's own id for an issue can be a word
    as well as a number (the 2019 special issue's is), so the series line
    is the one statement that covers every card."""
    html = net.request(session, "GET", NJEL.listings[0]).text
    soup = BeautifulSoup(html, "html.parser")
    issues = []
    seen = set()
    for a in soup.find_all("a", class_="title", href=RE_ISSUE_HREF):
        href = a.get("href")
        if not isinstance(href, str):
            continue
        match = RE_ISSUE_HREF.search(href)
        if match is None:
            continue
        issue_id = match.group(1)
        if issue_id in seen:
            continue
        seen.add(issue_id)
        heading = a.find_parent("h2")
        series = (heading.find("div", class_="series")
                  if heading is not None else None)
        if series is None:
            raise ValueError("no njel series line beside %r"
                             % a.get_text(" ", strip=True)[:40])
        line = normalize_space(series.get_text(" ", strip=True))
        m = RE_ISSUE_SERIES.search(line)
        if m is None:
            raise ValueError("no njel year and issue in the series line %r"
                             % line)
        # the journal numbers its volumes by year since its first volume in
        # 2018: a line that states a volume beside a year it does not belong
        # to is a card the page has broken, and the walk says so
        if int(m.group(1)) != int(m.group(3)) - 2017:
            raise ValueError("njel series line %r states a volume beside "
                             "a year it does not belong to" % line)
        issues.append({"id": issue_id, "year": m.group(3),
                       "issue": m.group(2)})
    if not issues:
        raise ValueError("the njel archive names no issues -- the page moved")
    return issues


def _njel_records_from_page(html, issue_url, year, issue):
    """One issue page's article summaries as records, in the issue's own
    order. The title is the summary's heading with the platform's small-type
    subtitle out of it; the page is the first of the issue page range the
    summary states; the document is the summary's PDF link, the platform's
    view address written as the download that streams the PDF. A summary
    without a PDF link names an article with no document, and it is kept as
    one."""
    soup = BeautifulSoup(html, "html.parser")
    records = []
    for block in soup.select("div.article-summary.media"):
        heading = block.select_one("h3")
        if heading is None:
            raise ValueError("njel %s(%s): a summary sets no title"
                             % (year, issue))
        for small in heading.find_all("small"):
            small.decompose()
        title = normalize_space(heading.get_text(" ", strip=True))
        if not title:
            raise ValueError("njel %s(%s): a summary names no title"
                             % (year, issue))
        authors = block.select_one("div.authors")
        fattare = normalize_space(authors.get_text(" ", strip=True)) \
            if authors is not None else None
        pages = block.select_one("p.pages")
        page = None
        if pages is not None:
            # The range's start is the article's opening page, and the
            # older issues number their front matter in Roman numerals
            # ("III-V"): the token keeps the numeral the journal writes
            # it, the rest of the range off.
            raw = pages.get_text(" ", strip=True)
            page = re.split(r"[-–—]", raw, maxsplit=1)[0].strip()
            if not re.fullmatch(r"\d+|[IVXLCDM]+", page):
                raise ValueError(
                    "njel %s(%s): an unreadable page range %r"
                    % (year, issue, raw))
        galley = block.select_one("a.galley-link")
        document_url = None
        if galley is not None:
            href = galley.get("href")
            assert isinstance(href, str)
            if "/article/view/" not in href:
                raise ValueError("njel %s(%s): an unexpected PDF link %r"
                                 % (year, issue, href))
            document_url = href.replace("/article/view/",
                                        "/article/download/")
        seq = "%02d" % (len(records) + 1)
        records.append({
            "basefile": "njel/%s-%s-%s" % (year, issue, seq),
            "journal": "njel",
            "year": year,
            "issue": issue,
            "seq": seq,
            "titel": title,
            "fattare": fattare,
            "sammanfattning": None,
            "sida": page,
            "source_url": issue_url,
            "document_url": document_url,
        })
    if not records:
        raise ValueError("njel %s(%s): the issue page sets no summaries"
                         % (year, issue))
    return records


def njel_sync(root, full=False, only=None, limit=None, delay=0.5):
    """The journal's whole archive: the one archive page, then the issue
    pages newest-first and every PDF the issue names. The host asks for a
    sixty-second crawl delay, so a watermark on the issue's year is what
    keeps a caught-up run to one archive read and one newest-issue-page
    read instead of re-reading the whole archive. `--only
    njel/2024-1-01` names its own issue, which is then the only issue page
    fetched and the walk stores that one document."""
    session = net.make_session(net.BROWSER_UA)
    issues = _njel_issues(session)
    if only:
        year, issue, _seq = only.split("/", 1)[1].split("-")
        issues = [i for i in issues
                  if (i["year"], i["issue"]) == (year, issue)]
        pending = []
        for i in issues:
            url = "%s/issue/view/%s" % (NJEL.base, i["id"])
            html = net.request(session, "GET", url).text
            time.sleep(delay)
            records = _njel_records_from_page(html, url, i["year"],
                                              i["issue"])
            for r in records:
                u = r["document_url"]
                pending.append((r,
                                (lambda u=u:
                                 net.request(session, "GET", u).content)
                                if u else None))
        return harvest.walk_records(
            root, select_pending(pending, only,
                                 "the njel archive carries no article %s"),
            delay=delay, full=full, limit=limit, scope="njel")
    watermark = harvest.HarvestWatermark(
        Path(root) / "njel" / ".watermark.json",
        lookahead_limit=3, safety_days=30)
    # newest issue first: the journal publishes its articles in issues, so a
    # caught-up run proves its newest issue is complete and stops, and the
    # archive behind it is never re-read
    issues.sort(key=lambda i: (i["year"], i["issue"]), reverse=True)

    def items():
        for issue in issues:
            url = "%s/issue/view/%s" % (NJEL.base, issue["id"])
            html = net.request(session, "GET", url).text
            time.sleep(delay)
            yield from _njel_records_from_page(
                html, url, issue["year"], issue["issue"])

    def item_key(record):
        return harvest.document_item_key(
            record, record_path(root, "njel", record["basefile"]),
            *([harvest.pdf_path(root, record["basefile"])]
              if record["document_url"] else []),
            # the platform states the year, not the day: the year's middle
            date=approximate_date(record["year"]))

    def resolve(record):
        return harvest.resolve_document(
            record, record_path(root, "njel", record["basefile"]),
            harvest.pdf_path(root, record["basefile"]),
            ((lambda: net.request(session, "GET",
                                  record["document_url"]).content)
             if record["document_url"] else None),
            harvest.verify_pdf, full=full, delay=delay)

    result = harvest.walk(
        items(), resolve=resolve, item_key=item_key, watermark=watermark,
        full=full, limit=limit, only=only, scope="njel")
    return result.seen, result.new