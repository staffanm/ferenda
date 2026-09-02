"""Harvester for ENISA:s rapporter -- the EU:s cybersäkerhetsbyrås guidance on
how the cybersecurity acts are to be met in practice.

**The body's own site is the index, not the EU:s publikationsbyrå.**
``/publications`` lists 587 rapporter over 59 pages; a CELLAR census under
ENISA:s corporate-body URI returns 249 works, so taking the publikationsbyrå
route would lose more than half the corpus. One flat Drupal view, ten rows a
page, no facet worth filtering on -- the only ``<select>`` on the page picks an
audience (Citizens / National-EU authorities / Private Sector), not a document
type.

**The pager does not stop.** ``?page=59`` and every page after it render the
view empty but still answer 200 with the site's "Featured publications" block
above it, whose rows link publication pages too. A walk that reads every
``<h3><a>`` on the page and stops on an empty page reports 745 rows for 587
documents. So the rows are read from inside the listing view
(``div.view-publications-index div.view-content .publications-item``) and the
walk ends on **"this page named nothing new"**.

**There is no number.** ENISA gives its rapporter no running identifier of any
kind -- no series, no year-serial, nothing a citation could name -- so the
identity is the slug of the report's own page
(``/publications/enisa-secure-by-design-and-default-playbook``), which is the
only stable key the body publishes. The slugs are unique in their last segment
across all 587 (some sit under ``/publications/archive/``, ``/info-notes/`` or
``/corporate-documents/``, and the shelf a report sits on is ENISA's to change
while the slug is not), so the last segment is what is taken and the walk
asserts the uniqueness rather than assuming it. Sixteen slugs are not already
slug-shaped -- eight carry capitals (``ENISA_Threat_Landscape``), three carry a
dot (``…-ecsmaf-v2.0``) and four are double-percent-encoded
(``Cyber-Bullying%2520and%2520Online%2520Grooming``, an alias with spaces in
it) -- so `report_slug` decodes and folds them to one form. The request itself
always uses the href verbatim; only the identity is folded.

**What is taken.** ENISA labels each leaf with its own "Publication type", and
this harvest carries the rapporter, not the agency's administration: an
"ENISA Reports" leaf and an untyped one (the old briefings and info notes,
which predate the field) are taken, a "Corporate documents" leaf -- the annual
activity reports, the single programming documents, the stakeholder strategy --
is declined and counted. Each declining type is counted under its own name, so
a type ENISA adds tomorrow shows up as itself rather than as silence.

**Language.** ENISA publishes in English, and the leaf states which versions it
has as one link per language code. 585 of the 587 offer English alone; the SME
guide is the one that exists in all 24 official languages, Swedish included. So
the Swedish PDF is taken where there is one and the English otherwise, with
`sprak` recording which, the way `edpb_download` and `eba_download` do.

**Rate limiting.** The site sits behind CloudFront with a rate rule that answers
429 with an interstitial and ``Retry-After: 0.000`` -- a value urllib3 refuses
to parse, so its own retry raises `InvalidHeader` and kills the walk instead of
riding the throttle out. This harvest therefore mounts its own transport retry
for the host with that header ignored (see `_session`), and paces itself no
faster than `MIN_DELAY`: measured, 100 requests inside five minutes trip the
rule and the block lifts about five minutes later.

Stored per document under ``site/data/downloaded/guidance/enisa/``: an
``enisa-rapporter-<slug>.json`` record and the ``.pdf`` document.
"""

import re
import time
from collections import Counter
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..lib import compress
from ..lib.harvest import paginated, pdf_path, walk_records
from ..lib.net import BROWSER_UA as USER_AGENT
from ..lib.net import fetcher, make_session, request
from ..lib.util import MONTHS_EN, href, normalize_space, record_path
from .issuers import ENISA

BASE = ENISA.base
INDEX = BASE + "/publications"
# the one series this body has: it publishes everything into one undivided
# listing (see `issuers.ENISA`)
SERIE = ENISA.series[0].kod

# the body's own "Publication type", and which of its values are rapporter.
# ``None`` is the untyped leaf -- the briefings and info notes ENISA published
# before it had the field, which are reports in everything but the label.
CARRIED_TYPES = frozenset({"ENISA Reports", None})

# a bound on the pager, so a listing that never repeats itself cannot loop
# forever. 59 pages today; the walk still ends on "nothing new", not on this.
INDEX_PAGES_MAX = 200

# how slowly this harvest may go. CloudFront's rate rule tripped at 0.35 s and
# again at 0.8 s between requests, both times after about a hundred requests,
# which is the shape of a 100-per-five-minutes bucket. Three seconds keeps the
# whole run inside it. `enisa_sync` raises `delay` to this rather than trusting
# the caller's default, because the shared default (0.5 s) is a block.
MIN_DELAY = 3.0

# CloudFront answers an occasional request with an empty 200 rather than the
# page. `lib.net.request` only retries an empty body when it was asked for JSON,
# so an HTML fetch has to notice it here -- and it is worth retrying rather than
# raising, because the next request for the same URL returns the page.
EMPTY_BODY_RETRIES = 3

# "June 26, 2025", the one date form the leaf prints
RE_PUBLISHED = re.compile(r"^([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})$")
# everything a slug may not contain, folded to a single hyphen
RE_NOT_SLUG = re.compile(r"[^a-z0-9]+")


def basefile(nummer):
    """The harvest basefile of one report
    ("enisa/rapporter/enisa-secure-by-design-and-default-playbook")."""
    return "%s/%s/%s" % (ENISA.kod, SERIE, ENISA.serie(SERIE).slug(nummer))


def report_slug(url):
    """The identity of one report: the last segment of its own address, folded
    to one spelling. ``/publications/archive/Measuring%2520Uptake`` ->
    ``measuring-uptake``.

    Decoded **twice** because four of ENISA:s aliases are double-encoded: the
    href reads ``%2520``, which is the encoding of ``%20``, which is the
    encoding of the space the alias actually contains. One decode leaves a
    literal ``%20`` in the slug. The fold to ``[a-z0-9-]`` then makes the
    result safe whatever came out, and the *request* is always made with the
    href verbatim -- only the identity is folded."""
    tail = unquote(unquote(url.rstrip("/").rsplit("/", 1)[-1]))
    slug = RE_NOT_SLUG.sub("-", tail.lower()).strip("-")
    assert slug, "no slug in ENISA publication address %r" % url
    return slug


def published_date(text):
    """ISO form of the date a leaf prints ("June 26, 2025" -> "2025-06-26")."""
    match = RE_PUBLISHED.match(normalize_space(text))
    assert match, "not an ENISA publication date: %r" % text
    return "%s-%02d-%02d" % (match.group(3), MONTHS_EN[match.group(1).lower()],
                             int(match.group(2)))


def listing_leaves(html_text):
    """The publication pages one index page names, as hrefs in page order.

    Read from **inside** the listing view. Every page of the index carries a
    "Featured publications" block above the view whose rows link publication
    pages of their own, and past the last page the view renders empty while that
    block still does not -- so a walk that reads the page's anchors instead of
    the view's sees rows that are not listing rows and never runs out of
    them."""
    view = BeautifulSoup(html_text, "html.parser").select_one(
        "div.view-publications-index div.view-content")
    return [] if view is None else [
        href(anchor).strip()
        for anchor in view.select(".publications-item h3 a[href]")]


def parse_leaf(html_text, url):
    """One publication page -> its fields. Pure over the HTML.

    `dokument` is the Swedish PDF where ENISA has published one and the English
    one otherwise, taken from the leaf's own per-language links rather than from
    the Download button beside the cover: the button is the default version, and
    for the one report that exists in Swedish the default is English. It is None
    where the leaf offers no PDF at all -- the two controls matrices publish an
    ``.xlsx`` and nothing else -- which the caller counts.

    `typ` is ENISA:s own "Publication type", None on the older leaves that
    carry no such field."""
    soup = BeautifulSoup(html_text, "html.parser")
    article = soup.select_one("article.node--type-publications")
    assert article is not None, "%s is not an ENISA publication page" % url
    heading = soup.select_one("h1")
    assert heading is not None, "%s carries no title" % url
    published = article.select_one(".publish-date .date")
    assert published is not None, "%s states no publication date" % url
    detail = {}
    for item in article.select(".publication-metadata-detail > li"):
        label = item.select_one(".label-detail")
        if label is not None:
            detail[normalize_space(label.get_text())] = item
    typ = detail.get("Publication type")
    topics = detail.get("Topics")
    # one link per language the report exists in, keyed by the code the leaf
    # prints beside it ("EN", "SV"). Absolute already on a few leaves, relative
    # on the rest, so every one is joined onto the base.
    files = {normalize_space(anchor.get_text()).lower():
             urljoin(BASE, href(anchor))
             for anchor in article.select("li.lang a[href]")}
    pdfs = [(sprak, files[sprak]) for sprak in ("sv", "en")
            if sprak in files
            and files[sprak].split("?")[0].lower().endswith(".pdf")]
    return {
        "titel": normalize_space(heading.get_text()),
        "antagen": published_date(published.get_text()),
        "typ": normalize_space(typ.get_text(strip=True).removeprefix(
            "Publication type")) if typ is not None else None,
        "amnesord": [normalize_space(a.get_text())
                     for a in topics.select("ul li a")] if topics else [],
        "sprak": pdfs[0][0] if pdfs else None,
        "dokument": pdfs[0][1] if pdfs else None,
    }


# --------------------------------------------------------------------------
# the walk
# --------------------------------------------------------------------------

# the transport retry this host needs. urllib3's default parses Retry-After,
# and CloudFront's throttle sends "0.000", which it rejects with InvalidHeader
# -- raised out of the retry, so a walk dies on the one thing the retry exists
# to survive. Ignoring the header costs nothing: `lib.net.request`'s own backoff
# already ignores a non-integer Retry-After. The backoff is long on purpose --
# 4+8+16+32+64+128+256 s -- because the measured block lasts about five minutes.
ENISA_RETRY = Retry(total=7, backoff_factor=4.0,
                    status_forcelist=(429, 500, 502, 503, 504),
                    allowed_methods=frozenset({"GET"}),
                    raise_on_status=False,
                    respect_retry_after_header=False)


def _session():
    session = make_session(USER_AGENT)
    session.mount(BASE + "/", HTTPAdapter(max_retries=ENISA_RETRY))
    return session


def _fetch(session, url, delay):
    for _ in range(EMPTY_BODY_RETRIES):
        text = request(session, "GET", url, timeout=120).text
        time.sleep(delay)
        if text:
            return text
    raise ValueError("%s answered %d times with an empty body"
                     % (url, EMPTY_BODY_RETRIES))


def index_leaves(session, delay):
    """Every publication page the index names, in listing order, and the number
    of index pages walked."""
    leaves, pages = paginated(
        lambda page: _fetch(session, "%s?page=%d" % (INDEX, page), delay),
        listing_leaves, cap=INDEX_PAGES_MAX, what="ENISA publications")
    # an index whose first page names nothing is selector rot, not a corpus of
    # none: every other page's emptiness is the walk's own stop signal
    assert leaves, "the ENISA publications index named no publications at all"
    return leaves, pages


def _slugged(leaves):
    """``{nummer: leaf href}`` over the whole index, so a collision is found
    before anything is fetched. The slugs are unique across all 587 today; two
    that ever fold together would file one report over the other, which has to
    stop the run rather than be picked between."""
    filed = {}
    for leaf in leaves:
        nummer = report_slug(leaf)
        assert nummer not in filed, \
            "%s and %s both slug to %r" % (filed.get(nummer), leaf, nummer)
        filed[nummer] = leaf
    return filed


def already_stored(root, key):
    """Whether this report's record *and* its document are both on disk -- the
    question the index alone can answer, since the slug ENISA files a report
    under is in the listing.

    Asked before the leaf is fetched, which is the whole point: the leaf costs a
    request at `MIN_DELAY`, and reading all 587 of them to learn that nothing
    moved is what made this the slowest harvest in the source -- 4 096 s over
    649 requests for 0 new documents (measured 2026-08-26). A report ENISA
    revises under the same slug is therefore picked up by ``--force``, not by
    the nightly run; that is the same trade `eba_download` makes with its
    ``.walked.json`` and the reason ``--force`` exists."""
    return (compress.exists(record_path(root, ENISA.kod, key))
            and compress.exists(pdf_path(root, key)))


def _pending(session, root, filed, only, full, delay, counts):
    """The listing as a lazy stream of ``(record, body)``, so a capped run stops
    reading leaves where it stops fetching documents.

    Every leaf costs a request and the index has 587 of them, so materialising
    the whole listing would make ``--limit 5`` a 646-request run. Reading it
    lazily is what `walk_records` takes a `total` for. `counts` is filled as the
    stream is read and reported by the caller once the walk has stopped."""
    for nummer, leaf in filed.items():
        key = basefile(nummer)
        if only is not None and key != only:
            continue
        if not full and already_stored(root, key):
            counts["redan hämtade"] += 1
            continue
        url = urljoin(BASE, leaf)
        fields = parse_leaf(_fetch(session, url, delay), url)
        counts["lasta"] += 1
        if fields["typ"] not in CARRIED_TYPES:
            counts["avvisade: %s" % fields["typ"]] += 1
            continue
        if fields["dokument"] is None:
            # the leaf publishes no PDF -- the controls matrices are an .xlsx
            # and nothing else. Not a report this source can carry, and not a
            # broken page either, so it is counted rather than raised on.
            counts["avvisade: utan PDF"] += 1
            continue
        counts["rapporter: %s" % fields["sprak"]] += 1
        yield ({
            "basefile": key, "utgivare": ENISA.kod,
            "serie": SERIE, "nummer": key.rsplit("/", 1)[-1],
            "sprak": fields["sprak"], "titel": fields["titel"],
            "antagen": fields["antagen"], "version": None,
            "konsultation_url": None, "amnesord": fields["amnesord"],
            "source_url": url, "dokument_url": fields["dokument"],
        }, fetcher(session, fields["dokument"], timeout=300))


def enisa_sync(root, full=False, only=None, limit=None, delay=0.5):
    """Harvest ENISA:s rapporter off its own publications index.

    One scope: the body has one series and one listing, and the listing is
    enumerable whole in 59 requests, so the EDPB/EBA idiom applies -- one walk
    per run, fetching what is new or changed, no watermark. A caught-up run
    costs those 59 requests and nothing else: the slug in the listing is the
    identity, so `already_stored` answers for a report without opening its leaf.

    Every leaf that is not taken is counted under the reason it was not, so a
    declined document type and a page shape this harvest has not seen never look
    alike in the output (rule:instrument-failures)."""
    delay = max(delay, MIN_DELAY)
    session = _session()
    leaves, pages = index_leaves(session, delay)
    filed = _slugged(leaves)
    if only is not None and only not in filed:
        # a user-typed --only that names nothing is a typo or a document that
        # has gone; either way the run has nothing to do and says which
        # (rule:errors-drive-retry-use-raise)
        raise ValueError("the ENISA publications index carries no document %s"
                         % only)
    counts = Counter()
    seen, new = walk_records(
        root, _pending(session, root, filed, only, full, delay, counts),
        delay=delay, full=full, limit=limit, scope=ENISA.kod,
        total=1 if only is not None else len(filed))
    print("enisa: %d index pages, %d publications listed, %d leaves read -> %s"
          % (pages, len(filed), counts["lasta"],
             ", ".join("%d %s" % (n, what) for what, n in sorted(counts.items())
                       if what != "lasta") or "nothing"))
    return seen, new
