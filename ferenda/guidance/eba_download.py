"""Harvester for Europeiska bankmyndighetens riktlinjer och rekommendationer.

**The index is not the publications listing**, which is the trap this harvest
exists to avoid. The EBA's own ``/publications-and-media/publications`` view has
a "Guidelines" facet returning 149 rows, and they are the wrong documents:
final reports and consolidated texts *about* guidelines ("Final Report on
Guidelines on the authorisation of third-country branches", "Consolidated
version of EBA-GL-2015-18"). They carry no EBA/GL number, they link straight to
a PDF with no document page, and harvesting them would file 149 reports under
guideline identities.

The numbered riktlinjer live in the **single rulebook** instead, as a two-level
tree:

  * ``/activities/single-rulebook`` lists the 36 ämnessidor, each at
    ``/regulation-and-policy/<ämne>`` (credit-risk, internal-governance,
    remuneration …). Every EBA page carries the same list in its mega-menu, but
    this one page is taken as the index rather than whichever page happens to be
    at hand.
  * each ämnessida lists its documents as leaves under
    ``/activities/single-rulebook/regulatory-activities/<ämne>/<slug>``. The 36
    together name 289 leaves, of which 127 are riktlinjer and 138 are tekniska
    standarder -- the standarder are adopted as kommissionsförordningar and are
    `eurlex` documents, so they are not taken here.

A leaf page carries everything the record needs. The **number** is the identity
and is read off the page as a whole: the EBA prints it in the path of every
translation it links (``…/Publications/Guidelines/2021/EBA-GL-2021-05 Guidelines
on internal governance/translations/…``) and nowhere as a field of its own, so
the page's distinct ``EBA-GL-ÅÅÅÅ-NN`` tokens are collected and required to
agree. A page naming none is skipped and counted rather than guessed at.

The **Swedish text** exists by law rather than by favour: artikel 16 in
förordning (EU) nr 1093/2010 makes a riktlinje effective only once it is
translated into every official language, and publishing the translations starts
the two months in which Finansinspektionen must state whether it complies. The
page lists one download per language behind a language badge
(``<span class="badge badge--langcode">sv</span>``), which is what this reads --
never the file name, whose language suffix the EBA spells inconsistently
(``…_SV2.pdf`` beside ``…_sv.pdf``). A riktlinje still in consultation has no
translation yet, and is published here in English with the record saying so, the
way `edpb_download` does for an untranslated EDPB riktlinje.

Neither level paginates, so there is no watermark and no depth to stop short of.
The whole tree is 37 index pages, the 289 leaves and the ~130 previous versions
they name -- and at the EBA's own ``Crawl-delay: 10`` that is hours. A published
riktlinje is fixed, so `WALKED` remembers every page read and a steady run reads
the 37 index pages plus whatever is new. ``--force`` looks at all of it again.

Stored under ``site/data/downloaded/guidance/eba/``: an
``eba-<serie>-<slug>.json`` record and the ``.pdf`` document per document, plus
the one ``.walked.json`` memo for the whole source.
"""

import functools
import json
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..lib import compress
from ..lib.harvest import (
    pdf_path,
    select_pending,
    walk_records,
)
from ..lib.net import BROWSER_UA as USER_AGENT
from ..lib.net import make_session, request, set_deadline
from ..lib.pdftext import pdf_first_page_text
from ..lib.util import (
    Reporter,
    document_extension,
    href,
    normalize_space,
    record_path,
    write_atomic,
)
from .issuers import EBA
from .model import vagledning_identifier, vagledning_uri

BASE = EBA.base
# the one page taken as the index of ämnessidor
SINGLE_RULEBOOK = BASE + "/activities/single-rulebook"
RE_TOPIC = re.compile(r'"(/regulation-and-policy/[a-z0-9-]+)"')
RE_LEAF = re.compile(
    r'"(/activities/single-rulebook/regulatory-activities/[^"#?]+)"')

# the EBA's own number, as it prints it in a translation path. The series token
# is what tells a riktlinje from a rekommendation, so it is captured rather than
# matched: GL -> the gl series, REC -> rec.
RE_IDENTITY = re.compile(r"EBA-(GL|REC)-(\d{4})-(\d+)")
# the same number as the document itself prints it, first line of the cover:
# "EBA/GL/2026/01". This is the only place roughly four fifths of the corpus
# states its identity -- see `cover_identity`.
RE_COVER_IDENTITY = re.compile(r"EBA/(GL|REC)/(\d{4})/(\d+)")
SERIE_OF = {"GL": "gl", "REC": "rec"}


def basefile(serie, nummer):
    """The harvest basefile of one document ("eba/gl/2021-05")."""
    return "%s/%s/%s" % (EBA.kod, serie, EBA.serie(serie).slug(nummer))


def topic_pages(index_html):
    """The ämnessidor named by the single-rulebook index, as absolute URLs.
    Pure over the HTML so the index can be tested without network."""
    topics = sorted(set(RE_TOPIC.findall(index_html)))
    assert topics, "the EBA single-rulebook page named no ämnessidor at all"
    return [BASE + path for path in topics]


def leaf_pages(topic_html):
    """The document leaves one ämnessida names, as absolute URLs."""
    return [BASE + path for path in sorted(set(RE_LEAF.findall(topic_html)))]


# where the versions of one document are listed. The EBA does not drop a
# superseded riktlinje from the single rulebook -- it keeps it as a previous
# version of the same leaf, at the same path plus ?version=ÅÅÅÅ, with all its
# translations. Reading only the leaf's current version is what left 82 numbers
# unharvested (`KNOWN-GAPS.md`). The dropdown repeats on every version page and
# names the current one too, so a walk that starts anywhere reaches all of them.
VERSIONS = "section#activity-versions .activity-versions__buttons a[href]"

# the amending wording that tells the number a cover *names* from the number it
# *is*: "RIKTLINJER OM ÄNDRING AV RIKTLINJERNA EBA/GL/2015/12 EBA/GL/2024/10"
# prints the amended number first, and reading the first match filed five
# documents under the number they amend
RE_AMENDS = re.compile(
    # the EBA writes both "om ändring av" and "för ändring av"
    r"(?:(?:om|för)\s+ändring\s+av\s+(?:riktlinjerna|rekommendationerna)"
    r"|amending\s+(?:the\s+)?(?:Guidelines|Recommendations))\s+"
    r"EBA/(?:GL|REC)/\d{4}/\d+", re.I)
# the EBA's own unfilled template, left standing in one published document
RE_TEMPLATE = re.compile(r"EBA/(?:GL|REC)/20XX/XX", re.I)


def version_pages(html_text, url):
    """The other versions of the document a leaf page carries, as absolute URLs.

    Pure over the HTML. The page's own address is excluded, so a caller can
    walk what it gets back without re-walking where it came from."""
    here = url.split("#")[0]
    others = {urljoin(BASE, href(a)).split("#")[0]
              for a in BeautifulSoup(html_text, "html.parser").select(VERSIONS)}
    # a version of *this* document: same path, and at most a ?version= on it.
    # Anything else in the dropdown would be queued and walked as a leaf, and a
    # page with no <h1> kills the run in `parse_leaf`.
    return sorted(other for other in others - {here}
                  if other.split("?")[0] == here.split("?")[0])


def href_identity(url):
    """The (serie, nummer) the EBA prints in a document's own path, or None.

    The number is in the file name of most translations
    (``…/Guidelines on default definition (EBA-GL-2016-07)_SV.pdf``), so asking
    the chosen document's address answers for free what `cover_identity` pays a
    download to learn.

    It must be the file's own name and never the path around it. The EBA files
    a consolidated wording under the *amending* riktlinje's folder, so the whole
    URL names two documents and the folder comes first: the Swedish
    consolidation of EBA/GL/2021/17 lives under
    ``…/Guidelines/2023/EBA-GL-2023-02/Translations consolidated/…/MODIFICATION
    - Consolidated version - GLs AFMs (EBA GL 2021 17)_SV.pdf``. Measured over
    the 80 stored records, matching the whole URL answers 32 of them and gets
    that one wrong; matching the file name answers 13 and gets none wrong. The
    other 19 fall through to the cover, which is where they came from.

    For the same reason it must be the *chosen document's* address and never the
    page as a whole: a version page links the current version's files too.

    The file name is not always the last segment: the older leaves serve a
    document from a path carrying its uuid *after* the name
    ("…/EBA-GL-2015-09 GL on payment commitments - SV.pdf/39b2fd04-…"), so the
    segment that ends the path is a uuid and names nothing."""
    match = RE_IDENTITY.search(_file_name(url))
    return (SERIE_OF[match.group(1)],
            "%s/%s" % (match.group(2), match.group(3))) if match else None


def cover_identity(pdf_bytes):
    """The (serie, nummer) a document's own cover prints, or None.

    Most EBA leaf pages state no number anywhere: of the 289 leaves in the
    single rulebook, only 52 print an ``EBA-GL-ÅÅÅÅ-NN`` token, and they are the
    ones whose translations still sit under the old ``document_library`` paths.
    The newer pages serve every file from a UUID directory that carries no
    number at all. The document does state it -- "EBA/GL/2026/01" is the first
    line of the cover, above the date and the title -- so where the page is
    silent the PDF is asked.

    That is why identity costs a download here and not in `edpb_download`, and
    why `eba_sync` caches it per leaf: on a steady run the number is read back
    off the stored record and nothing is fetched."""
    if document_extension(pdf_bytes) != ".pdf":
        # the EBA served something that is not a PDF behind a .pdf address
        # (an error page, most often). Not this harvest's to repair, and not
        # a document either: the caller counts it and moves on.
        return None
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(pdf_bytes)
        tmp.flush()
        text = pdf_first_page_text(Path(tmp.name))
    return cover_number(text or "")


def cover_number(text):
    """The (serie, nummer) a cover states as *its own*, or None. Pure over the
    cover text.

    Not the first number printed. An amending riktlinje's cover names the one it
    amends first ("SLUTRAPPORT OM RIKTLINJER FÖR ÄNDRING AV RIKTLINJERNA
    EBA/GL/2015/12 EBA/GL/2024/10"), and taking the first filed five documents
    under a number that is not theirs. The amended number is the one introduced
    by "om ändring av riktlinjerna" / "amending Guidelines", so it is removed
    before the search; what is left is the document's own.

    The EBA's unfilled template ("EBA/GL/20XX/XX", left standing in the Swedish
    text of EBA/GL/2018/05) states no number and is removed too."""
    stated = RE_TEMPLATE.sub(" ", RE_AMENDS.sub(" ", text))
    match = RE_COVER_IDENTITY.search(stated)
    return (SERIE_OF[match.group(1)],
            "%s/%s" % (match.group(2), match.group(3))) if match else None


def known_identities(root):
    """``{leaf url: (nummer, dokument url)}`` from the records already stored.

    What makes re-reading a cover unnecessary on a steady run: a leaf whose
    address and whose linked document are both unchanged is the document we
    already named, so its number is taken from the record rather than from a
    fresh download.

    ``eba-*.json*`` and not ``*.json*``: `WALKED` sits in the same directory and
    is not a record."""
    directory = Path(root) / EBA.kod
    if not directory.exists():
        return {}
    known = {}
    for path in sorted(directory.glob("eba-*.json*")):
        record = compress.read_json(path)
        if record.get("source_url"):
            known[record["source_url"]] = (record["serie"], record["nummer"],
                                           record.get("dokument_url"))
    return known


# Every leaf and version page this harvest has read, stored beside the records.
#
# **A published riktlinje is fixed.** The EBA gives a revised wording its own
# number and its own leaf and keeps the old one at ``?version=``, so a page that
# has been read has nothing more to say. Re-reading all 289 leaves and their
# ~130 previous versions cost about 90 minutes a run at the host's
# ``Crawl-delay: 10`` and bought nothing. ``--force`` ignores the memo and
# rebuilds it, which is how a page is looked at again.
#
# A page is added only once its verdict is *settled on disk* -- see
# `settled_leaves`. Recording a page as read while its document failed to store
# would strand the document forever, which is the failure `lib.harvest`'s
# watermark exists to prevent and which this source has no watermark to catch.
WALKED = ".walked.json"

# Our adjudication of which candidate files are gone for good -- see the file's
# own `_comment`. A leaf whose only unresolved candidate 404s is deliberately
# left unmemoized (`settled_leaves`), so a file the EBA has removed permanently
# is re-fetched on every run and holds its leaf in the walk forever. Listing it
# here says "we have looked, it is not coming back": no fetch, and the leaf
# settles. Kept as data next to the harvester rather than as a set in the code,
# because it grows by adjudication and every entry is a judgement about one
# publisher's file (rule:curation-is-ours).
DEAD_CANDIDATES = Path(__file__).resolve().parent / "data" / "eba-dead-candidates.json"


@functools.cache
def dead_candidates():
    """The candidate urls adjudicated permanently gone, as a set."""
    return frozenset(json.loads(
        DEAD_CANDIDATES.read_text(encoding="utf-8"))["urls"])


def _memo(root, full):
    path = Path(root) / EBA.kod / WALKED
    return {} if full or not path.exists() else json.loads(path.read_text())


def read_walked(root, full=False):
    """The pages already read, empty on a ``--force`` run."""
    return set(_memo(root, full).get("leaves", ()))


def read_covers(root, full=False):
    """``{document url: (serie, nummer) | None}`` -- what a cover download has
    already been paid for, empty on a ``--force`` run.

    The page memo does not cover this. A leaf whose verdict never settles is
    re-walked on every run *by design* (see `settled_leaves`), and re-walking it
    re-downloaded every candidate file to read a cover whose answer had not
    changed: 5 of the 299 leaves are outside the memo today, and they cost this
    run 23 cover downloads at the EBA's ``Crawl-delay: 10`` -- 230 of its 731
    seconds, to learn again that those files name no EBA number.

    Safe to remember for the same reason the page memo is: the EBA gives a
    revised wording its own number and its own file, so a document at a fixed
    url is fixed. ``--force`` reads every cover again."""
    return {url: tuple(v) if v else None
            for url, v in _memo(root, full).get("covers", {}).items()}


def write_walked(root, leaves, covers):
    write_atomic(Path(root) / EBA.kod / WALKED,
                 json.dumps({"leaves": sorted(leaves),
                             "covers": {url: list(v) if v else None
                                        for url, v in sorted(covers.items())}},
                            indent=1))


def settled_leaves(root, final, documented):
    """The pages it is safe to record as read.

    `final` names the pages whose verdict needs no document: the leaf carries no
    EBA number, so there is nothing to store and nothing to retry. `documented`
    maps a page to the basefile it produced, and one of those is settled only
    once that record and its PDF are actually on disk.

    That test is what keeps the memo honest. `walk_records` counts a per-document
    failure and leaves the record unwritten, ``--limit`` stops after N documents
    and ``--only`` drops every other pending record -- and every one of those
    leaves a page read but not harvested. Memoizing it would skip that page on
    every future run.

    The caller applies this only to a walk whose queue drained -- see
    `eba_sync`."""
    return final | {url for url, bf in documented.items()
                    if compress.exists(record_path(root, EBA.kod, bf))
                    and compress.exists(pdf_path(root, bf))}


# what a leaf lists *beside* the riktlinje, named by its own link text. The
# covers cannot tell these apart from the document -- a compliance table's cover
# states the riktlinje's number too, and so does a final report's -- so this is
# the only place the distinction is written down. The module exists to avoid
# filing reports under guideline identities; `ul.RelatedList` reopens that door
# and this is what closes it.
RE_NOT_THE_DOCUMENT = re.compile(
    r"compliance table|efterlevnadstabell|consultation paper|impact assessment"
    r"|public hearing|feedback statement|annex|press release", re.I)


def _file_name(url):
    """The file's own name in a url: the last path segment carrying a suffix,
    which the older leaves follow with the file's uuid."""
    segments = url.split("?")[0].split("/")
    # every caller has already passed the url through `_is_pdf`, so one of the
    # segments carries the suffix
    return next(seg for seg in reversed(segments) if ".pdf" in seg.lower())


def _is_pdf(anchor):
    """Whether an anchor points at a PDF. Not `href$=".pdf"`: the older leaves
    serve a document from a path that carries the file's uuid *after* its name
    ("…/EBA-GL-2015-09 GL on payment commitments - SV.pdf/39b2fd04-…"), which
    ends in no suffix at all."""
    return ".pdf" in href(anchor).lower().split("?")[0]


def _related_translations(soup):
    """``{language code: url}`` from a leaf's language menu, where it has one.

    The EBA's older leaves are built the other way round: the riktlinje is not
    in the download list at all -- that list holds the consultation paper, the
    stakeholder response and the hearing slides -- and the document itself sits
    in `ul.RelatedList` with its translations in a `.RelatedTranslations`
    dropdown beside it. 43 of the 209 leaves this harvest declined for "carrying
    no EBA number" are that shape, and every one sampled carried a number.

    The language is the link text's own two-letter prefix ("sv svenska"), the
    way the newer markup carries it in a badge -- and never the file name, whose
    language suffix the EBA spells inconsistently."""
    menu = soup.select_one(".RelatedTranslations")
    if menu is None:
        return {}
    out = {}
    for anchor in menu.select("a[href]"):
        code = normalize_space(anchor.get_text()).split(" ")[0].lower()
        if len(code) == 2 and _is_pdf(anchor):
            out.setdefault(code, urljoin(BASE, href(anchor)))
    return out


def parse_leaf(html_text, url):
    """One leaf page -> its fields. Pure over the HTML.

    ``document`` is the Swedish PDF where the EBA has published one, else the
    English one; ``sprak`` says which, so a page showing English text can say
    why. The language comes from the badge beside each download, never from the
    file name."""
    soup = BeautifulSoup(html_text, "html.parser")
    heading = soup.find("h1")
    assert heading is not None, "%s carries no document title" % url
    files = {}
    for item in soup.select(".document-download__item"):
        badge = item.select_one(".badge--langcode")
        anchor = item.find("a", href=True)
        if badge is not None and anchor is not None and _is_pdf(anchor):
            # .pdf only: the compliance table beside a riktlinje is an .xlsx
            # and the track-changes draft a .docx, and both sit behind the
            # same language badge as the document itself
            files.setdefault(normalize_space(badge.get_text()),
                             urljoin(BASE, href(anchor)))
    # the older leaves say it a second way (`_related_translations`), and there
    # the language menu is the only place the document appears at all
    for code, document in _related_translations(soup).items():
        files.setdefault(code, document)
    # every unbadged PDF the page lists, in page order: on the older leaves the
    # document itself is one of several (a consultation paper and a stakeholder
    # response sit beside it), and which is which is not said in the markup --
    # only the covers tell them apart, so all of them are candidates
    beside = [a for a in soup.select("ul.RelatedList a[href]")
              if RE_NOT_THE_DOCUMENT.search(a.get_text(" ", strip=True))]
    plain = [urljoin(BASE, href(a))
             for a in soup.select(".document-download__item a[href]")
             + [a for a in soup.select("ul.RelatedList a[href]")
                if a not in beside]
             if _is_pdf(a) and urljoin(BASE, href(a)) not in files.values()]
    adopted = soup.select_one("time[datetime]")
    status = soup.select_one(".field--name-field-status")
    return {
        "titel": normalize_space(heading.get_text()),
        # Swedish first: it is the text the site publishes where the EBA has
        # issued one. `sprak` is decided per candidate by `eba_sync`, once the
        # covers have said which candidate is the document at all.
        "candidates": ([("sv", files["sv"])] if "sv" in files else [])
        + ([("en", files["en"])] if "en" in files else [])
        + [("en", url) for url in plain],
        # what the list held beside the document, so a leaf whose riktlinje is
        # rejected by name leaves a trace rather than vanishing into
        # `carries_none` (rule:instrument-failures)
        "beside": [normalize_space(a.get_text()) for a in beside],
        "antagen": (adopted["datetime"][:10] if adopted is not None else None),
        "status": normalize_space(status.get_text()) if status is not None
        else None,
    }


def _fetch(session, url, delay):
    text = request(session, "GET", url, timeout=120).text
    time.sleep(delay)
    return text


# What the single-rulebook walk may cost before it is stuck rather than slow.
# The EBA's robots.txt asks 10 seconds between requests and its pages take about
# two more, so a request costs ~12 s (measured 2026-08-22). A steady run reads
# the 37 index pages and whatever `WALKED` has not seen, so it is minutes; only
# a first or `--force` run pays the whole 289 leaves, their ~130 previous
# versions and ~235 cover reads, which comes to about 2.3 hours.
#
# The budget is well above that. It exists to end a run that has stopped making
# progress, not to cap a legitimate backfill.
WALK_BUDGET = 4 * 3600.0

# The session deadline is armed this much *after* the walk's own budget, so the
# graceful stop wins the race. Both bound the same run: the loop check breaks
# between pages and stores what it named, while `lib.net.request` raises
# `BudgetExceeded` from inside a fetch -- which is not a `requests.HTTPError`,
# so the candidate loop does not catch it and the run aborts before
# `walk_records` with every pending record lost. Armed at the same instant the
# two are a coin flip. The deadline is still what bounds one blocked fetch.
DEADLINE_GRACE = 300.0

# The version dropdown is the one thing that makes the queue grow while it is
# being walked, and it grows by whatever the EBA links there. A leaf has a
# handful of previous versions; 289 leaves named about 130 between them. A walk
# that has queued more than this per leaf is following something that is not a
# version list, and every page it reads costs ten seconds.
MAX_QUEUED_PER_LEAF = 3


def eba_sync(root, full=False, only=None, limit=None, delay=0.5):
    """Harvest the EBA's numbered guidance off the single-rulebook tree.

    Both series come out of one walk -- a leaf page states which it belongs to
    in its own number -- so the EBA is **one scope**, not one per series. Two
    scopes would walk the same 37 index pages and 289 leaves twice, and running
    them concurrently would put both walks on the EBA at once
    (rule:respect-politeness).

    `unnumbered` counts what the walk declined, because a leaf naming no
    EBA-GL/REC number is either a document type this source does not carry (the
    138 tekniska standarder, which are adopted as kommissionsförordningar and
    belong to `eurlex`) or a page shape this harvest has not seen -- and the two
    must not look alike in a run's output (rule:instrument-failures)."""
    session = make_session(USER_AGENT)
    known = known_identities(root)
    walked = read_walked(root, full)
    # a leaf whose verdict needs no document (it carries no EBA number), and
    # {leaf url: basefile} for one that produced a record. `settled_leaves` turns
    # the two into what the memo may record -- not before `walk_records` has run.
    final, documented = set(), {}
    # {document url: the (serie, nummer) its cover states, or None}. A version
    # page links the current version's files too, so within one run the same
    # document is offered on more than one page and its cover is read once --
    # and across runs the memo keeps a leaf that can never settle from
    # re-downloading its candidates every night (see `read_covers`).
    covers = read_covers(root, full)
    memoized = len(covers)
    try:
        started = time.monotonic()
        # the deadline bounds one blocked fetch -- `lib.net.request` caps both its
        # timeout and its backoff sleeps by it -- while the budget check in the
        # queue below stops the walk cleanly between pages. `DEADLINE_GRACE` is
        # what lets the graceful stop go first. Cleared before `walk_records`,
        # whose cost is bounded by the list it is handed.
        set_deadline(session, started + WALK_BUDGET + DEADLINE_GRACE)
        rep = Reporter()
        topics = topic_pages(_fetch(session, SINGLE_RULEBOOK, delay))
        leaves = set()
        # reported page by page: 36 ämnessidor at the EBA's Crawl-delay is six
        # minutes, and a first run's walk below is hours. A run that prints nothing
        # until it ends cannot be told from one that has stopped.
        for read, topic in enumerate(topics, 1):
            leaves.update(leaf_pages(_fetch(session, topic, delay)))
            rep.update(read, len(topics), scope="eba ämnessidor", leaves=len(leaves))
        rep.done()
        pending, carries_none, fetched, dead, versions = [], 0, 0, 0, 0
        gone = 0                     # candidates skipped by the dead list
        per_serie = dict.fromkeys(EBA.koder, 0)
        # a queue, not a loop over `leaves`: a leaf names its own previous versions
        # and each of those is a document of its own. Every version page names every
        # other, so `queued` -- which no page is ever added to twice -- is what
        # stops the walk going round in circles.
        queue = sorted(leaves - walked)
        queued = set(queue)
        skipped = len(leaves) - len(queue)
        # {version url: the leaf whose current version supersedes it}
        superseded_by = {}
        truncated = drained = False
        while queue:
            if time.monotonic() - started > WALK_BUDGET:
                # not slow any more: stuck. Store what the walk did name; the
                # memo is left alone, so the next run walks the whole tree again
                # (see the `finally` for why an early stop memoizes nothing).
                truncated = True
                rep.done()
                print("eba: sanity trip -- the single-rulebook walk is still "
                      "running after %.0f minutes, %d of %d pages read; stopping "
                      "and storing what it named, and leaving the memo unchanged "
                      "so the next run re-walks"
                      % (WALK_BUDGET / 60, len(queued) - len(queue), len(queued)),
                      flush=True)
                break
            url = queue.pop(0)
            page = _fetch(session, url, delay)
            for other in version_pages(page, url):
                # the current version is the one at the bare path; everything with
                # a ?version= is a wording the EBA has since replaced
                if "?version=" in other:
                    superseded_by[other] = url.split("?")[0]
                # against `queued`, not `seen`: every version page names every
                # other, so a page already waiting in the queue would be added
                # again -- and counted again -- once per sibling that names it
                if other not in queued and other not in walked:
                    versions += 1
                    queued.add(other)
                    queue.append(other)
            if len(queued) > len(leaves) * MAX_QUEUED_PER_LEAF:
                # remote data, so a raise and not an assert: `-O` must not remove the
                # one guard on a dropdown that has started naming pages without end
                # (rule:errors-drive-retry-use-raise)
                raise ValueError(
                    "the version dropdowns have queued %d pages for %d leaves -- "
                    "that is not a version list, and every page costs ten seconds"
                    % (len(queued), len(leaves)))
            fields = parse_leaf(page, url)
            chosen, vanished = None, False
            for sprak, document in fields["candidates"]:
                # the record already names this exact file: no download needed
                if known.get(url, (None, None, None))[2] == document:
                    chosen = (*known[url][:2], sprak, document, None)
                    break
                # the number is in most translations' own file name, which answers
                # for free what a cover download pays for -- and answers it for the
                # *chosen* document, where a page-wide match on a version page
                # would read a superseded document as its own successor
                named = href_identity(document)
                if named is not None:
                    chosen = (*named, sprak, document, None)
                    break
                if document in dead_candidates():
                    # adjudicated gone: not fetched, and -- unlike a live 404 --
                    # it does not set `vanished`, so this leaf can finally settle
                    gone += 1
                    continue
                if document in covers:
                    # already read on another page of this run: a version page
                    # links the current version's files too
                    read_before = covers[document]
                    if read_before is None:
                        continue
                    chosen = (*read_before, sprak, document, None)
                    break
                try:
                    body = _document_fetcher(session, document)()
                except requests.HTTPError:
                    # a candidate that is not there is not the document -- the older
                    # leaves link consultation responses that the EBA has since
                    # removed, and the next candidate is the one to try. A 404 on
                    # the *chosen* document still raises, in `walk_records`
                    # (rule:no-catch-log-continue: the cause is known and the
                    # recovery is defined, not a log-and-hope)
                    dead += 1
                    vanished = True
                    # named, not just counted: a 404 that keeps coming back is
                    # what `DEAD_CANDIDATES` is for, and a run that prints only
                    # the count leaves nothing to put in the list
                    print("eba: dead candidate %s" % document, flush=True)
                    time.sleep(delay)
                    continue
                fetched += 1
                time.sleep(delay)
                identity = cover_identity(body)
                covers[document] = identity
                if identity:
                    chosen = (*identity, sprak, document, body)
                    break
            if chosen is None:
                # no candidate's cover names an EBA/GL or EBA/REC number. The leaf is
                # a document type this source does not carry -- a teknisk standard, a
                # report, a consultation paper -- and *not* a guideline whose file
                # went missing, which is what makes this a count rather than a raise
                carries_none += 1
                # ... unless a candidate's file was not there at all. That leaf may
                # well be a riktlinje whose PDF the EBA served badly this once, and
                # memoizing it would drop it from the corpus for good.
                if not vanished:
                    final.add(url)
            else:
                serie, nummer, sprak, document, body = chosen
                per_serie[serie] += 1
                documented[url] = basefile(serie, nummer)
                pending.append(({
                    "basefile": basefile(serie, nummer), "utgivare": EBA.kod,
                    "serie": serie, "nummer": nummer,
                    "sprak": sprak, "titel": fields["titel"],
                    "antagen": fields["antagen"], "version": fields["status"],
                    "konsultation_url": None, "amnesord": [],
                    "source_url": url, "dokument_url": document,
                    "ersatt_av": superseded_by.get(url),
                    "ersatt_av_identifier": None, "ersatt_av_url": None,
                }, (lambda got=body: got) if body is not None
                    else _document_fetcher(session, document)))
            # a first run is hours of requests and prints nothing of its own until
            # it ends. `queued` grows as the version dropdowns are read, so the
            # total rises while the counter runs.
            rep.update(len(queued) - len(queue), len(queued), scope=EBA.kod,
                       numrerade=sum(per_serie.values()), onumrerade=carries_none,
                       omslag=fetched)
        # the queue is empty, so every page the tree names has been read -- and
        # with it every version dropdown. That is the condition the memo needs;
        # see the `finally` below.
        drained = not truncated
        # a superseded version names its successor by *url* while the walk runs,
        # because the successor's own number is not known until its page is read.
        # Now that every page has been, the urls resolve to addresses.
        named = {record["source_url"]:
                 (vagledning_uri(EBA.kod, record["serie"], record["nummer"]),
                  vagledning_identifier(EBA.kod, record["serie"],
                                        record["nummer"], None))
                 for record, _fetch_document in pending}
        replaced = unnamed = 0
        for record, _fetch_document in pending:
            if not record["ersatt_av"]:
                continue
            # what the EBA links as the current version of this leaf, kept whatever
            # became of it: the body files this wording as a *previous* version, so
            # it is superseded even where the successor named no number or its file
            # 404'd. Dropping the relation there would publish a wording the body
            # itself retired as current law.
            record["ersatt_av_url"] = record["ersatt_av"]
            successor = named.get(record["ersatt_av"])
            unnamed += successor is None
            record["ersatt_av"], record["ersatt_av_identifier"] = \
                successor or (None, None)
            replaced += 1
        rep.done()
        # the enumeration's budget is spent; `walk_records` fetches from a list that
        # is already fixed, so it is bounded by its own length rather than by a clock
        set_deadline(session, None)
        print("eba: %d ämnessidor, %d of the %d leaves were already read; this run "
              "walked %d pages -> %s, %d carrying no EBA number, %d previous "
              "versions found, %d covers read, %d dead candidate files, "
              "%d skipped as gone for good%s"
              % (len(topics), skipped, len(leaves), len(queued) - len(queue),
                 ", ".join("%d %s" % (n, kod) for kod, n in per_serie.items()),
                 carries_none, versions, fetched, dead, gone,
                 " (walk truncated: %d pages unread)" % len(queue) if truncated
                 else ""))
        print("eba: %d superseded by a later version of the same riktlinje"
              " (%d whose successor this run could not name)" % (replaced, unnamed))
        print("eba: %d cover verdicts remembered from earlier runs, %d added"
              % (memoized, len(covers) - memoized), flush=True)
        return walk_records(
            root, select_pending(pending, only,
                                 "the EBA single rulebook carries no document %s"),
            delay=delay, full=full, limit=limit, scope=EBA.kod)
    finally:
        # Only a walk whose queue drained may add to the memo, and only after
        # `walk_records` has stored the documents.
        #
        # A page is not settled by having been read. A leaf's version dropdown is
        # the *only* route to its previous versions, and the queue is FIFO -- so
        # a walk that stops early has read leaves whose version pages are still
        # in `queue`. Memoizing those leaves would skip them next run, never
        # re-read their dropdowns, and strand the versions for good. Previous
        # versions are what the dropdown walk was added to recover: 82 numbers
        # (`KNOWN-GAPS.md`).
        #
        # So a truncated or aborted run leaves the memo exactly as it found it
        # and re-walks next time. That is the right trade: the walk stops early
        # only on the 4-hour budget or the queue guard, both of which mean
        # something is wrong. `settled_leaves` then keeps out any page whose
        # document did not store. Never pruned: a leaf the EBA takes down stops
        # being named, and an entry no walk consults costs nothing.
        if drained:
            walked |= settled_leaves(root, final, documented)
            write_walked(root, walked, covers)
            print("eba: %s remembers %d pages; --force looks at them again"
                  % (WALKED, len(walked)), flush=True)
        else:
            print("eba: the walk did not finish, so %s is unchanged -- a page it "
                  "read may have named a version page it never reached"
                  % WALKED, flush=True)


def _document_fetcher(session, url):
    return lambda: request(session, "GET", url, timeout=180).content
