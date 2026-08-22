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

Neither level paginates and the whole corpus is enumerable in 37 requests plus
one per leaf, so the EDPB/JK/ARN idiom applies: one walk per run, fetching what
is new or changed, no watermark.

Stored per document under ``site/data/downloaded/guidance/eba/``: an
``eba-<serie>-<slug>.json`` record and the ``.pdf`` document.
"""

import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..lib import compress
from ..lib.harvest import (
    select_pending,
    walk_records,
)
from ..lib.net import BROWSER_UA as USER_AGENT
from ..lib.net import make_session, request
from ..lib.pdftext import pdf_first_page_text
from ..lib.util import document_extension, href, normalize_space
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
    fresh download."""
    directory = Path(root) / EBA.kod
    if not directory.exists():
        return {}
    known = {}
    for path in sorted(directory.glob("*.json*")):
        record = compress.read_json(path)
        if record.get("source_url"):
            known[record["source_url"]] = (record["serie"], record["nummer"],
                                           record.get("dokument_url"))
    return known


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
    topics = topic_pages(_fetch(session, SINGLE_RULEBOOK, delay))
    leaves = {url for topic in topics
              for url in leaf_pages(_fetch(session, topic, delay))}
    pending, carries_none, fetched, dead, versions = [], 0, 0, 0, 0
    per_serie = dict.fromkeys(EBA.koder, 0)
    # a queue, not a loop over `leaves`: a leaf names its own previous versions
    # and each of those is a document of its own. Every version page names every
    # other, so `queued` -- which no page is ever added to twice -- is what
    # stops the walk going round in circles.
    queue = sorted(leaves)
    queued = set(queue)
    # {version url: the leaf whose current version supersedes it}
    superseded_by = {}
    while queue:
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
            if other not in queued:
                versions += 1
                queued.add(other)
                queue.append(other)
        fields = parse_leaf(page, url)
        chosen = None
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
                time.sleep(delay)
                continue
            fetched += 1
            time.sleep(delay)
            identity = cover_identity(body)
            if identity:
                chosen = (*identity, sprak, document, body)
                break
        if chosen is None:
            # no candidate's cover names an EBA/GL or EBA/REC number. The leaf is
            # a document type this source does not carry -- a teknisk standard, a
            # report, a consultation paper -- and *not* a guideline whose file
            # went missing, which is what makes this a count rather than a raise
            carries_none += 1
            continue
        serie, nummer, sprak, document, body = chosen
        per_serie[serie] += 1
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
    print("eba: %d ämnessidor, %d leaves + %d previous versions -> %s, "
          "%d carrying no EBA number, %d covers read, %d dead candidate files"
          % (len(topics), len(leaves), versions,
             ", ".join("%d %s" % (n, kod) for kod, n in per_serie.items()),
             carries_none, fetched, dead))
    print("eba: %d superseded by a later version of the same riktlinje"
          " (%d whose successor this run could not name)" % (replaced, unnamed))
    return walk_records(
        root, select_pending(pending, only,
                             "the EBA single rulebook carries no document %s"),
        delay=delay, full=full, limit=limit, scope=EBA.kod)


def _document_fetcher(session, url):
    return lambda: request(session, "GET", url, timeout=180).content
