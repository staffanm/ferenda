"""Pure SKVFS/RSFS catalogue parsing and live-record storage.

Skatteverket's register is behind an F5/Shape JavaScript challenge, so its
``Agency`` selects the detached headful-Chrome transport. This module owns the
source semantics that can be tested without a browser: register HTML to
``DocRef`` objects, a detail page to its official PDF, and those source bytes
to the ordinary föreskrift download layout.
"""

import re
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..lib import compress
from ..lib.util import basefile_slug as slug
from .harvest import DocRef, newest_first, save_single_pdf_record

INDEX_URL = "https://www4.skatteverket.se/rattsligvagledning/115.html?year=Alla"
# The current register has one result per ``ul.rol-result-list > li``.  The
# result's *own* identifier leads its whole text; references in the linked title
# come later (eg. SKVFS 2026:7 amends SKVFS 2025:29), so this must be anchored.
# One live entry prints ``2026_3`` instead of ``2026:3``; both name 2026:3.
RE_IDENTIFIER = re.compile(r"^(SKVFS|RSFS)\s+(\d{4})[:_](\d+)\b", re.IGNORECASE)


def parse_index(html):
    """Parse the live SKVFS/RSFS register into newest-first document refs."""
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select("ul.rol-result-list > li")
    assert items, "Skatteverket register has no ul.rol-result-list document rows"
    refs = []
    seen = {}
    for item in items:
        text = item.get_text(" ", strip=True)
        match = RE_IDENTIFIER.match(text)
        assert match, "Skatteverket register row has no leading FS identifier: %r" % text[:200]
        anchor = item.select_one("p a[href]")
        assert anchor is not None, \
            "Skatteverket register row %r has no linked title" % match.group(0)
        fs = match.group(1).lower()
        number = "%s:%d" % (match.group(2), int(match.group(3)))
        basefile = "%s/%s" % (fs, number)
        title = anchor.get_text(" ", strip=True)
        assert title, "Skatteverket register row %s has an empty title" % basefile
        href = anchor.get("href")
        assert isinstance(href, str)
        docref = DocRef(
            basefile=basefile,
            identifier="%s %s" % (match.group(1).upper(), number),
            url=urljoin(INDEX_URL, href),
            title=title,
            fs=fs,
        )
        if basefile in seen:
            # The register repeats a few identical documents under several
            # subject/detail ids (eg. SKVFS 2021:19 four times). Keep its first
            # occurrence, but fail if the duplicate disagrees on semantics.
            assert seen[basefile].title == title, \
                "duplicate Skatteverket row %s has conflicting titles" % basefile
            continue
        seen[basefile] = docref
        refs.append(docref)
    return newest_first(refs)


def parse_detail_pdf(html, ref):
    """Return the detail page's official PDF URL for exactly ``ref``."""
    matches = []
    for anchor in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        href = anchor.get("href")
        assert isinstance(href, str)
        if anchor.get_text(" ", strip=True).replace("_", ":") == ref.identifier \
                and href.lower().split("?", 1)[0].endswith(".pdf"):
            matches.append(urljoin(ref.url, href))
    assert len(matches) == 1, \
        "%s detail page has %d exact PDF links" % (ref.identifier, len(matches))
    return matches[0]


def enumerate_register(browser, _agency):
    """Enumerate both SKVFS and its closed RSFS predecessor in one browser pass."""
    yield from parse_index(browser.html(INDEX_URL, "SKVFS"))


def save_record(root, agency, ref, detail_html, pdf_url, pdf_data):
    """Store one live regulation in the ordinary föreskrift raw layout."""
    fs = ref.fs
    assert fs in ("skvfs", "rsfs"), "%s has unexpected fs %r" % (ref.basefile, fs)
    record = save_single_pdf_record(
        root, agency, ref, pdf_url, pdf_data, source_url=ref.url,
    )
    compress.write_download(
        Path(root) / fs / (slug(ref.basefile) + ".html"), detail_html,
    )
    return record


def resolve(browser, agency, ref, root, _delay=0.5, *, log=print, rejects=None):
    """Resolve one register ref through detached detail/PDF browser loads."""
    assert rejects is not None, "SKVFS resolver requires the harvest rejection ledger"
    log("  %s: protected detail page" % ref.identifier)
    detail_html = browser.html(ref.url, ref.identifier)
    pdf_url = parse_detail_pdf(detail_html, ref)
    log("  %s: protected PDF" % ref.identifier)
    return save_record(root, agency, ref, detail_html, pdf_url, browser.pdf(pdf_url))
