"""Mirror the officially published SFS PDFs the consolidated text is missing.

The consolidated SFST text is text-only, so graphics/formulas/maps are dropped;
the published SFS carries them. This mirrors those PDFs locally, keyed by SFS
number, so the localization pass (``sfs graphics``) can crop the in-force
graphic from the exact amending act's PDF. Two eras, both addressable from the
SFS number alone:

- **1998-2017 -> rkrattsdb.gov.se SFSdoc**: a directly derivable URL,
  ``/SFSdoc/{YY}/{YY}{NNNN}.PDF`` (2007:90 -> ``/07/070090.PDF``).
- **2018- -> svenskforfattningssamling.se**: the predictable per-doc HTML page
  ``/doc/{year}{nr}.html`` (2021:734 -> ``/doc/2021734.html``) carries a single
  PDF link; fetch the page, take the href. No month can be derived from the SFS
  number and the publisher's month folder need not match the rkrattsbaser date,
  so the doc page is the one stable handle.

The worklist is the corpus itself: every base act and every
andringsforfattning already recorded in the downloaded registers, deduped -- no
blind enumeration.
"""

import json
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..lib import compress, layout
from ..lib.net import request

RKRATTSDB = "https://rkrattsdb.gov.se/SFSdoc/%s/%s%s.PDF"
SVENSK_DOC = "https://svenskforfattningssamling.se/doc/%s%s.html"
# the svensk era begins 2018 (SFS moved online 2018-04); rkrattsdb SFSdoc goes
# back to 1998. Earlier acts have no published-PDF facsimile online.
SVENSK_FROM = 2018
RKRATTSDB_FROM = 1998

def _parts(beteckning):
    year, nr = beteckning.split(":", 1)
    return int(year), year, nr.strip()


def _svensk_pdf_url(session, year, nr):
    doc = SVENSK_DOC % (year, nr)
    try:
        page = request(session, "GET", doc).text
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise
    link = BeautifulSoup(page, "html.parser").find(
        "a", href=lambda href: bool(href and href.lower().endswith(".pdf")))
    if not link:
        return None
    href = link.get("href")
    assert isinstance(href, str), "%s: PDF link has non-string href" % doc
    return urljoin(doc, href)


def pdf_url(session, beteckning):
    """The published-PDF URL for one SFS number, or None when none is
    reachable (pre-1998, or a post-2018 doc page carrying no PDF link)."""
    yint, year, nr = _parts(beteckning)
    if yint > SVENSK_FROM:
        return _svensk_pdf_url(session, year, nr)
    if yint == SVENSK_FROM:
        # The authentic online series began on 1 April 2018. SFS numbers do not
        # encode the publication date, so try the new collection and fall back
        # to the printed-series mirror for early-2018 acts.
        return (_svensk_pdf_url(session, year, nr)
                or RKRATTSDB % (year[2:], year[2:], nr.zfill(4)))
    if yint >= RKRATTSDB_FROM:
        return RKRATTSDB % (year[2:], year[2:], nr.zfill(4))
    return None


def fetch_one(session, beteckning, force=False):
    """Mirror one SFS PDF under ``downloaded/sfs/pdf/``. Returns the path on a
    fresh fetch, None when already present (idempotent) or when the act has no
    published PDF."""
    out = layout.sfs_pdf(beteckning)
    if compress.exists(out) and not force:
        return None
    url = pdf_url(session, beteckning)
    if url is None:
        return None
    data = request(session, "GET", url).content
    if not data.startswith(b"%PDF-"):
        raise ValueError("%s returned non-PDF content for %s" % (url, beteckning))
    compress.write_download(out, data)
    return out


def corpus_beteckningar(bases):
    """Every SFS number to mirror: each base act plus every andringsforfattning
    in its downloaded register, deduped and sorted oldest-first."""
    seen = set()
    for bf in bases:
        seen.add(bf)
        src = json.loads(compress.read_bytes(layout.sfs_source(bf)))
        for act in src.get("andringsforfattningar") or []:
            if act.get("beteckning"):
                seen.add(act["beteckning"])
    return sorted(seen, key=_sort_key)


def _sort_key(beteckning):
    year, nr = beteckning.split(":", 1)
    digits = re.match(r"\d+", nr)
    return (int(year), int(digits.group()) if digits else 0, nr)
