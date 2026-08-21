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
    match = RE_COVER_IDENTITY.search(text or "")
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
        if badge is not None and anchor is not None \
                and href(anchor).lower().split("?")[0].endswith(".pdf"):
            # .pdf only: the compliance table beside a riktlinje is an .xlsx
            # and the track-changes draft a .docx, and both sit behind the
            # same language badge as the document itself
            files.setdefault(normalize_space(badge.get_text()),
                             BASE + href(anchor))
    # every unbadged PDF the page lists, in page order: on the older leaves the
    # document itself is one of several (a consultation paper and a stakeholder
    # response sit beside it), and which is which is not said in the markup --
    # only the covers tell them apart, so all of them are candidates
    plain = [BASE + href(a) for a in soup.select(
        ".document-download__item a[href$='.pdf']")
        if BASE + href(a) not in files.values()]
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
    pending, carries_none, fetched, dead = [], 0, 0, 0
    per_serie = dict.fromkeys(EBA.koder, 0)
    for url in sorted(leaves):
        fields = parse_leaf(_fetch(session, url, delay), url)
        chosen = None
        for sprak, document in fields["candidates"]:
            # the record already names this exact file: no download needed
            if known.get(url, (None, None, None))[2] == document:
                chosen = (*known[url][:2], sprak, document, None)
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
        }, (lambda got=body: got) if body is not None
            else _document_fetcher(session, document)))
    print("eba: %d ämnessidor, %d leaves -> %s, %d carrying no EBA number, "
          "%d covers read, %d dead candidate files"
          % (len(topics), len(leaves),
             ", ".join("%d %s" % (n, kod) for kod, n in per_serie.items()),
             carries_none, fetched, dead))
    return walk_records(
        root, select_pending(pending, only,
                             "the EBA single rulebook carries no document %s"),
        delay=delay, full=full, limit=limit, scope=EBA.kod)


def _document_fetcher(session, url):
    return lambda: request(session, "GET", url, timeout=180).content
