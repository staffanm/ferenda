"""Mirror the officially published SFS PDFs the consolidated text is missing.

The consolidated SFST text is text-only, so graphics/formulas/maps are dropped;
the published SFS carries them. This mirrors those PDFs locally, keyed by SFS
number, so the localization pass (``sfs graphics``) can crop the in-force
graphic from the exact amending act's PDF. Which source holds an act follows
from its SFS number alone -- see ``RKRATTSDB_FIRST`` / ``SVENSK_FIRST`` for the
two boundaries, neither of which is a date:

- **1998:306 - 2018:159 -> rkrattsdb.gov.se SFSdoc**: a directly derivable URL,
  ``/SFSdoc/{YY}/{YY}{NNNN}.PDF`` (2007:90 -> ``/07/070090.PDF``). A closed
  series -- nothing will ever be added to it -- so a 404 is a permanent answer,
  recorded once (``MirrorState.absent``) and never asked again.
- **2018:160 - -> svenskforfattningssamling.se**: the predictable per-doc HTML
  page ``/doc/{year}{nr}.html`` (2021:734 -> ``/doc/2021734.html``) carries a
  single PDF link; fetch the page, take the href. No month can be derived from
  the SFS number and the publisher's month folder need not match the
  rkrattsbaser date, so the doc page is the one stable handle -- and, being the
  only handle, its own answer is final: no page, or a page with no link, means
  the act has no published PDF (recorded in ``MirrorState.absent``).
- **before 1998:306**: print only. Nothing to fetch, and nothing to record.

The worklist is the corpus itself: every base act and every
andringsforfattning already recorded in the downloaded registers, deduped -- no
blind enumeration.

What keeps a rerun cheap is only ever local: an act already mirrored is skipped
from disk, and one the upstream has denied is skipped from ``absent``. Each act
is therefore asked about at most once. The cost of that bargain is that a
negative is permanent -- if the publisher posts a PDF it previously lacked, only
``--full`` will find it.
"""

import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..lib import compress, layout
from ..lib.net import is_not_found, request
from ..lib.util import write_atomic

RKRATTSDB = "https://rkrattsdb.gov.se/SFSdoc/%s/%s%s.PDF"
SVENSK = "https://svenskforfattningssamling.se/"
SVENSK_DOC = SVENSK + "doc/%s%s.html"
# The two facsimile sources' exact first acts. Neither boundary follows from a
# year or a date, so both are stated as the SFS numbers they are:
#  * the printed-series mirror simply starts at 1998:306 -- 1998:1-305 404 there
#    (confirmed against the mirror: 305 consecutive misses, then an unbroken run)
#    and exist on paper only, as does everything before them;
#  * the authentic online series starts at 2018:160, the first act published
#    after the 1 April 2018 switch. 2018:1-159 predate it and sit in the printed
#    series, which is why the switch cannot be read off the year.
# They meet: 2018:159 is the printed series' last act, 2018:160 the online
# series' first, so every act from 1998:306 on has exactly one source.
RKRATTSDB_FIRST = "1998:306"
SVENSK_FIRST = "2018:160"
# Seconds to wait after asking a source for something, per source, measured
# against each one's own patience.
#  * rkrattsdb allows a hard 120 requests, then 403s the whole host for ~30s,
#    forever, in lockstep. Pacing under that quota is *free*: hammering it lands
#    120 fetches per ~67s cycle (~1.8/s) once the penalty is counted, so ~2/s of
#    traffic it never refuses finishes no slower and asks nothing of the
#    publisher. 0.55 rather than the 0.5 the quota literally allows, so a slow
#    response cannot drift the rate over the line.
#  * svenskforfattningssamling refused nothing at ~3/s across 13k fetches.
RKRATTSDB_DELAY = 0.55
SVENSK_DELAY = 0.3


def _parts(beteckning):
    year, nr = beteckning.split(":", 1)
    return int(year), year, nr.strip()


def source_delay(beteckning):
    """How long to wait after asking this act's source for something. Which
    source it is decides: the two have very different patience, and only the
    act's number says which one will be asked."""
    return SVENSK_DELAY if is_online_series(beteckning) else RKRATTSDB_DELAY


def has_facsimile(beteckning):
    """Whether any source holds this act's published PDF at all. Acts before
    ``RKRATTSDB_FIRST`` exist in print only -- nobody has scanned them, so
    asking for one is a question with no answer rather than a miss."""
    return _sort_key(beteckning) >= _sort_key(RKRATTSDB_FIRST)


def is_online_series(beteckning):
    """Whether this act belongs to the authentic online series (``SVENSK_FIRST``
    onwards) rather than the printed series' mirror. Implies
    :func:`has_facsimile`, since the two ranges meet."""
    return _sort_key(beteckning) >= _sort_key(SVENSK_FIRST)


def _svensk_pdf_url(session, year, nr):
    """The PDF link on one act's doc page, or None when the online series has no
    PDF for it -- either no doc page at all, or one carrying no link. The doc
    page is the only view we have of that series, so its answer is the answer;
    there is no second opinion for a 404 to disagree with."""
    doc = SVENSK_DOC % (year, nr)
    try:
        page = request(session, "GET", doc).text
    except requests.HTTPError as exc:
        if is_not_found(exc):
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
    """The published-PDF URL for one SFS number, or None when the act's own
    source turns out not to hold one. The printed series' URL is derivable; the
    online series' is not (its month folder follows publication, not the SFS
    number), so that one costs a doc-page fetch."""
    _yint, year, nr = _parts(beteckning)
    if is_online_series(beteckning):
        return _svensk_pdf_url(session, year, nr)
    if has_facsimile(beteckning):
        return RKRATTSDB % (year[2:], year[2:], nr.zfill(4))
    return None


class MirrorState:
    """The acts the upstream was asked about and definitively had no PDF for --
    a printed-series 404, or a doc page missing or carrying no PDF link.
    Persisted as ``absent`` in ``downloaded/sfs/pdf/.mirror.json``.

    This is what tells "no published PDF" apart from "not fetched yet", which
    the filesystem alone cannot: both look like a missing file. Without it every
    such act cost a request on every run. It is small -- the printed series has
    no known gaps -- and it is deliberately only ever written from an answer the
    upstream actually gave, never from an inference."""

    def __init__(self, root):
        self.path = Path(root) / ".mirror.json"
        self.absent: set[str] = set()
        if self.path.exists():
            self.absent = set(json.loads(self.path.read_text())["absent"])

    def save(self):
        write_atomic(self.path, json.dumps(
            {"absent": sorted(self.absent, key=_sort_key)}, indent=1))

    def record_absent(self, beteckning):
        """Remember that the upstream definitively has no PDF for `beteckning`,
        so a rerun does not ask again. Written through at once rather than at
        the end of the run: negatives are rare, and a crashed run must not lose
        them and re-ask the whole printed series."""
        self.absent.add(beteckning)
        self.save()

    def record_mirrored(self, beteckning):
        """Note that `beteckning` is now on disk. Clears any recorded negative:
        a `--full` rerun that finds a PDF the upstream had previously denied
        must not leave the store asserting both at once. A no-op (and no write)
        in the overwhelmingly common case, so a backfill of tens of thousands of
        PDFs does not rewrite the store once per fetch."""
        if beteckning in self.absent:
            self.absent.discard(beteckning)
            self.save()


def fetch_one(session, state, beteckning, force=False, delay=None):
    """Mirror one SFS PDF under ``downloaded/sfs/pdf/``. Returns the path on a
    fresh fetch, None when already present (idempotent), when `state` already
    knows the upstream has no PDF for it, or when the upstream answers that it
    has none -- recorded in `state`, so a later run does not ask again.

    Paces the source it asked at that source's own rate (`source_delay`);
    `delay` overrides it, and 0 disables it. The wait is only ever taken on the
    paths that actually asked something: a corpus-wide run walks tens of
    thousands of targets it answers from disk, from `state` or from the act's
    own number, and pausing on those would add hours without sparing either
    publisher a single request."""
    out = layout.sfs_pdf(beteckning)
    if not force and (compress.exists(out) or beteckning in state.absent):
        return None
    if not has_facsimile(beteckning):
        return None
    fetched = _fetch_upstream(session, state, beteckning, out)
    time.sleep(source_delay(beteckning) if delay is None else delay)
    return fetched


def _fetch_upstream(session, state, beteckning, out):
    """Ask the upstream for one act's PDF and store it. Every path here costs at
    least one request -- `fetch_one` has already ruled out everything answerable
    from local state."""
    url = pdf_url(session, beteckning)
    if url is None:
        # fetch_one has ruled out everything answerable locally, so this is the
        # online series itself saying no: no doc page, or one with no PDF link.
        state.record_absent(beteckning)
        return None
    try:
        data = request(session, "GET", url).content
    except requests.HTTPError as exc:
        # The printed-series URL is derived from the SFS number, not discovered,
        # so a 404 is the mirror saying it holds no facsimile for this act --
        # a permanent answer about a closed series, not a broken run. Anything
        # else is a real failure and must not be mistaken for "no PDF".
        if not is_not_found(exc):
            raise
        state.record_absent(beteckning)
        return None
    if not data.startswith(b"%PDF-"):
        raise ValueError("%s returned non-PDF content for %s" % (url, beteckning))
    compress.write_download(out, data)
    state.record_mirrored(beteckning)
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
