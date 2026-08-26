"""Tillväxtanalys MTFS catalogue parsing and protected PDF storage.

The Sitevision register is behind the same F5/Shape challenge as SKVFS, but its
source shape is simpler: each ``MTFS YYYY:N`` heading is immediately followed
by the official PDF link. The ``Agency`` selects detached headful Chrome while
this module owns those testable source semantics.
"""

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .harvest import DocRef, newest_first, save_single_pdf_record

INDEX_URL = (
    "https://www.tillvaxtanalys.se/statistik/"
    "tillvaxtanalysforeskrifter.125740.html"
)
RE_IDENTIFIER = re.compile(r"^MTFS\s+(\d{4}):(\d+)$", re.IGNORECASE)


def parse_index(html):
    """Return every current and repealed MTFS PDF, newest first."""
    soup = BeautifulSoup(html, "html.parser")
    refs = []
    seen = set()
    for heading in soup.find_all("h3"):
        match = RE_IDENTIFIER.fullmatch(heading.get_text(" ", strip=True))
        if not match:
            continue
        row = heading.find_next_sibling()
        assert row is not None and row.name == "p", \
            "%s is not followed by its PDF paragraph" % heading.get_text(" ", strip=True)
        anchor = row.find("a", href=True)
        assert anchor is not None, \
            "%s has no PDF link" % heading.get_text(" ", strip=True)
        assistive = anchor.select_one("span.env-assistive-text")
        if assistive is not None:
            assistive.decompose()
        title = anchor.get_text(" ", strip=True)
        assert title, "%s has an empty title" % heading.get_text(" ", strip=True)
        year, number = match.group(1), str(int(match.group(2)))
        basefile = "mtfs/%s:%s" % (year, number)
        assert basefile not in seen, "duplicate Tillväxtanalys row %s" % basefile
        seen.add(basefile)
        href = anchor.get("href")
        assert isinstance(href, str)
        pdf_url = urljoin(INDEX_URL, href)
        assert pdf_url.lower().split("?", 1)[0].endswith(".pdf"), \
            "%s link is not a PDF URL" % basefile
        refs.append(DocRef(
            basefile=basefile,
            identifier="MTFS %s:%s" % (year, number),
            url=pdf_url,
            title=title,
            fs="mtfs",
            extra={"regulation_url": pdf_url, "source_url": INDEX_URL},
        ))
    assert refs, "Tillväxtanalys register has no MTFS headings"
    return newest_first(refs)


def enumerate_register(browser, _agency):
    """Load and parse the protected one-page MTFS register."""
    yield from parse_index(browser.html(INDEX_URL, "Tillväxtanalys föreskrifter"))


def resolve(browser, agency, ref, root, _delay=0.5, *, log=print, rejects=None):
    """Fetch and store one direct official PDF through detached Chrome."""
    assert rejects is not None, "MTFS resolver requires the harvest rejection ledger"
    log("  %s: protected PDF" % ref.identifier)
    return save_single_pdf_record(
        root, agency, ref, ref.url, browser.pdf(ref.url), source_url=INDEX_URL,
    )
