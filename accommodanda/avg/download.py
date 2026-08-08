"""Harvesters for JO, JK, ARN, IMY and KKV decisions from the organs' own sites.

Both sites were redesigned since the legacy downloaders were written, so the
download layer is built fresh against the 2026 sites; what carries over from
the old code is the *domain knowledge* (dnr forms, the JK dotted-ärendetyp
quirk, multi-dnr decisions, decision-as-PDF vs decision-as-page).

**JO** (jo.se, WordPress): the search UI at ``/jo-beslut/sokresultat/`` is
driven by an ``admin-ajax.php`` action, ``get_jo_search_result``, guarded by a
page-embedded nonce (fetch the page, read ``horizon.ajaxNonce``, POST with the
same session). One hit is a complete record: ``diary_number``,
``resolve_date``, ``post_title``, the listing summary (``post_content``), the
deciding ombudsman (``resolve_maker``), the sakområde/lagstiftning taxonomies,
the decision PDF url (``pdf_url``) *and* the site's own flat text extraction of
it (``pdf_text``). ~3,700 decisions back to 1979. Newest-first by default, so
incremental runs stop at the first page with nothing new; the initial backfill
walks the same newest-first listing all the way down, gated by the shared
``HarvestWatermark`` exactly like dv/forarbete.

**JK** (jk.se, Umbraco): the listing at ``/beslut-och-yttranden/`` still
honours the legacy "broken pagination" hack -- ``POST page=9999`` returns every
decision the site carries in one response (~1,400, publications 1998-). The
decision *is* its HTML landing page (no PDF), so per decision we store the
landing page plus a record JSON. Diarienummer come in several raw shapes
(``6098-19-4.4`` dotted old form, ``2024/6800`` new form, ``JK ``-prefixed,
multi-dnr ``;``-separated); :func:`jk_canonical` reduces them to the citation
form that names the document.

**ARN** (arn.se, Optimizely/EPiServer): Allmänna reklamationsnämnden publishes
its vägledande beslut again -- the live source the §7g frozen import (1991-2022)
could only look back from. The old session-bound Digiforms database
(``adokweb.arn.se/referatwebb``) is still dead, but the current site carries a
single static listing page, ``/om-arn/vagledande-beslut/``, whose "De senaste
vägledande besluten" section links every published referat's decision PDF
(2017-, ~140 today) with an ``<h3>{avdelning}, beslut {ISO-datum}</h3>`` heading,
the ARN-curated summary paragraphs, and a link whose text names the diarienummer
(``\\d{4}-\\d{4,}``, first names a joined referat). One page, no pagination, so
the JK idiom applies -- every run walks the whole listing and fetches only what
is new or changed (no watermark gate). Records are written in the same
shape :func:`avg.parse.parse_arn` reads for the frozen corpus (dnr, beslutsdatum,
avdelning, and the summary as the title -- ARN referat have no real title) plus a
live ``source_url``. A harvested record carries no ``source`` marker key, so it
wins over -- and its live PDF overwrites -- any frozen import of the same dnr
(the §7g precedence rule; the other half lives in ``legacy.import_arn``).

**IMY** (imy.se, Optimizely/EPiServer): Integritetsskyddsmyndigheten publishes
its tillsyner at ``/tillsyner/`` -- "ett urval av våra pågående och avslutade
tillsyner från 2018 och framåt", ~130 pages today. The listing is a Vue app over
an ``/api/search/listsearch`` endpoint, but the server still renders the same
hits for ``?page=N`` (``data-pagecount`` says how many), so the plain pages are
the enumeration and no API contract has to be reverse-engineered.

A tillsyn *page* is not a document. It carries a heading, an ingress, the
tillsyn's current step and IMY's own summary of the outcome, and attaches the
decisions themselves as PDFs -- and the diarienummer that names each decision is
printed only inside those PDFs. So the harvester reads them: every attached
document is fetched, its page-1 header parsed for the number, and the documents
are regrouped by it. One page can decide several ärenden (seven "Grannbevakning"
beslut, seven brottsbekämpande myndigheter), one ärende can be published as
several documents (a beslut plus its tillsynsskrivelse, plus an English
translation), and one document can hang off several pages (the
vårdgivar-vägledning off eight) -- the regrouping is what turns all three into
one record per decision. Decisions IMY publishes anonymously print no number at
all (redacted to "DI-2018-XXXX", or an "Avidentifierad version"); those have no
identity to be filed under and are reported, not invented.

Two curated pages re-publish a subset with metadata that exists nowhere else:
``/om-oss/beslut-publikationer-och-remisser/praxisbeslut/`` marks the decisions
IMY considers precedential and adds lagrum, nyckelord, whether the decision was
appealed and whether it has gained legal force; ``…/beslut-om-sanktionsavgift/``
lists every administrative fine with its amount. Both link their entries as
``/link/<guid>.aspx`` redirects; the ``/tillsyner/rss`` feed carries the same
content GUID beside each page url, so one feed request resolves every curated
link instead of one redirect apiece. Neither page names a tillsyn the listing
does not already carry -- they are a metadata overlay, not a second corpus.

One listing walk per run and no watermark (the JK/ARN idiom): every entry is
compared against its stored record and only what is new or changed is written.

**KKV** (konkurrensverket.se, a React front over the agency's own systems):
Konkurrensverkets tillsynsbeslut, joined from **two** of its sources on the
diarienummer.

The **diarium** (``/diarium/sok-i-Konkurrensverkets-diarium/``) supplies the
decisions. Its status filter alone -- "Avslutade ärenden" + "Publicerade
beslut", which AND -- is 10,097 cases, but status says nothing about what kind
of ärende a case *is*: 3,675 of those are remissyttranden and much of the rest
routine korrespondens, neither of which is a förvaltningsbeslut. So the harvest
also applies the agency's own ärendetyp groups (`KKV_CASETYPES`), which narrows
it to the supervisory work: **1,830 cases, 1998-**. Företagskoncentrationer are
deliberately out (2,068 largely one-page clearances that lämnas utan åtgärd).
Unlike the other organs the diarium names the decision itself -- the
diarienummer is a listing field, so nothing has to be read out of a document to
mint the identity.

The curated **ärendelista**
(``/konkurrens/tillsyn-arenden-och-beslut/arendelista/``) supplies what the
diarium has no equivalent of: for 329 cases, Konkurrensverkets own account of
what the case was about, why it was prioritized, what it decided and what the
courts then did with the decision -- sectioned under the page's own headings --
plus the branch, the parties and the kinds of beslut the case produced. A fifth
of those entries name several diarienummer (an ärende that became more than one
case), and the account belongs to each. **138 of the 329 name a case the
narrowed diarium set does not carry**, some from 1993-97, before the diarium
begins; those are stored from the curated account alone.

Transport: the search page is server-rendered, but sending ``X-Requested-With``
gets the bare result JSON instead of a 300 kB page (500 bytes per case, not
4 kB), and the ärendedata and case pages answer ``Accept: application/json`` the
same way. The diarium's paging is cumulative -- ``page=2`` re-sends page 1 -- so
a group is fetched whole with ``take`` rather than paged; the ärendelista's
``page`` is a true offset and is walked normally. The listing is authoritative
for the case, so the per-case ärendedata request -- which is what carries the
*beslutsdatum* -- is made only for a case that is new or has moved.

The decision document comes in three formats behind a parameter that calls all
of them ``pdf``: most are PDF, the pre-2006 ones are the FrontPage-era HTML the
diarium published then (windows-1252, letterhead as a layout table), and two are
Word. A handful of cases publish no document at all.

Stored per decision under ``site/data/downloaded/avg/{org}/``:
``<slug>.json`` record (+ for JO/ARN the decision PDF, for JK the landing HTML).
IMY's and KKV's documents are stored under ``avg/{org}/dok/`` by their own asset
name -- IMY's because one document can belong to several decisions, KKV's
because the diarium's file name carries the format the record has to route on.
"""

import html as htmllib
import json
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from bs4 import BeautifulSoup

from ..lib import compress
from ..lib.harvest import (
    HarvestWatermark,
    ItemKey,
    dispatch_scopes,
    record_unchanged,
    store_record,
    walk,
    write_record,
)
from ..lib.net import BROWSER_UA as USER_AGENT
from ..lib.net import make_http2_session, make_session, request
from ..lib.pdftext import pdf_first_page_text
from ..lib.util import (
    Reporter,
    basefile_slug,
    document_extension,
    normalize_space,
    record_path,
)
from .model import ORGS

RE_ARN_DNR = re.compile(r"\d{4}-\d{4,}")


def arn_pdf_path(root, basefile):
    """The materialized decision PDF beside the record ("arn/1992-3657" ->
    ``<root>/arn/arn-1992-3657.pdf``) -- the JO body-file shape."""
    return Path(root) / "arn" / (basefile_slug(basefile) + ".pdf")


def jo_pdf_path(root, basefile):
    """The decision PDF beside a JO record ("jo/2340-2025" ->
    ``<root>/jo/jo-2340-2025.pdf``), shared by the harvester, parse inputs
    and the patch workflow."""
    return Path(root) / "jo" / (basefile_slug(basefile) + ".pdf")


def imy_pdf_path(root, name):
    """One attached IMY document, stored under its own asset name
    ("beslut-tillsyn-polismyndigheten.pdf" ->
    ``<root>/imy/dok/beslut-tillsyn-polismyndigheten.pdf``). Assets live in
    their own subdirectory because a decision's *record* is keyed on the
    diarienummer while its documents are keyed on the asset -- one document can
    belong to several decisions and one decision to several documents, so the
    two namespaces cannot share a stem. `list_basefiles` globs one level, so
    the subdirectory also keeps the assets out of the record enumeration."""
    return Path(root) / "imy" / "dok" / name


def jo_officialreport_path(root):
    """The dnr -> ämbetsberättelse-citation map re-housed beside the JO
    records (dotfile: never a record). parse_jo grafts the citation onto live
    records -- jo.se does not publish it, only the frozen corpus did."""
    return Path(root) / "jo" / ".officialreport.json"


COMPLETE = ".complete"    # marker under the org dir: corpus walked clean once

JO_BASE = "https://www.jo.se"
JO_SEARCH_PAGE = JO_BASE + "/jo-beslut/sokresultat/"
JO_AJAX = JO_BASE + "/wp/wp-admin/admin-ajax.php"
JO_PAGE_SIZE = 50
RE_JO_NONCE = re.compile(r'"ajaxNonce":"([0-9a-f]+)"')
RE_JO_DNR = re.compile(r"\d+-\d{4}")

JK_BASE = "https://www.jk.se"
JK_LIST = JK_BASE + "/beslut-och-yttranden/"
# "Diarienr: 2024/8082 / Beslutsdatum: 20 apr 2026" (the / separator is a span)
RE_JK_OLD = re.compile(r"^(\d+)-(\d{2})-([\d.]+)$")

ARN_BASE = "https://www.arn.se"
ARN_LIST = ARN_BASE + "/om-arn/vagledande-beslut/"
RE_ARN_ISODATE = re.compile(r"\d{4}-\d{2}-\d{2}")

KKV_BASE = "https://www.konkurrensverket.se"
KKV_SEARCH = KKV_BASE + "/diarium/sok-i-Konkurrensverkets-diarium/"
KKV_CASE = KKV_SEARCH + "arendedata/"
KKV_ARENDELISTA = KKV_BASE + "/konkurrens/tillsyn-arenden-och-beslut/arendelista/"
# the site's own status filter, as the two boxes a reader ticks: a case that is
# closed *and* whose decision is published. The two are ANDed
KKV_STATUS = (("statusList", "Avslutat"), ("statusList", "Publicerat"))
# ...and its own ärendetyp groups, which is what makes this a *tillsyn* corpus
# rather than a diarium dump. The status filter says nothing about what kind of
# ärende a case is, so unfiltered it is 10,097 cases of which 3,675 are
# remissyttranden and much of the rest routine korrespondens. These five groups
# are the agency's own curated ranges for its supervisory work; each maps both
# code generations (the pre-2018 "11 Missbruk dominerande ställning" and the
# current "3.2.2 Misstänkt missbruk…" both answer to 46), they do not overlap,
# and together they are 1,830 cases, 1998-. Företagskoncentrationer (49) are
# deliberately *not* here: 2,068 largely one-page clearances that lämnas utan
# åtgärd, closer in character to the remisser than to a tillsynsbeslut.
KKV_CASETYPES = (
    ("38", "38"),      # otillbörliga handelsmetoder i livsmedelskedjan
    ("45", "45"),      # konkurrensbegränsande samarbete
    ("46", "46"),      # missbruk av dominerande ställning
    ("51", "51"),      # konkurrensbegränsande offentlig säljverksamhet
    ("52", "64"),      # upphandlingsskadeavgift + domstolsärenden
)
# a whole group's result set in one response -- the largest is ~700 cases, and
# `kkv_listing` asserts rather than truncating if that ever stops holding
KKV_TAKE = 5000
RE_KKV_DNR = re.compile(r"(\d+/\d{4})")

IMY_BASE = "https://www.imy.se"
IMY_LIST = IMY_BASE + "/tillsyner/"
IMY_RSS = IMY_BASE + "/tillsyner/rss"
IMY_PRAXIS = IMY_BASE + "/om-oss/beslut-publikationer-och-remisser/praxisbeslut/"
IMY_SANKTION = (IMY_BASE
                + "/om-oss/beslut-publikationer-och-remisser/beslut-om-sanktionsavgift/")
# the Vue search component's server-rendered state: how many ?page=N there are
RE_IMY_PAGECOUNT = re.compile(r'data-pagecount="(\d+)"')
# an RSS item pairs the EPiServer content GUID with the page url the curated
# pages reach through /link/<guid>.aspx
RE_IMY_RSS_ITEM = re.compile(
    r"<guid[^>]*>([0-9a-f-]{36})</guid>\s*<link>([^<]+)</link>", re.S)
RE_IMY_GUID_LINK = re.compile(r"^/link/([0-9a-f]{32})\.aspx$")
# the two diarienummer generations an IMY decision prints in its page-1 header:
# the prefixed form IMY and late Datainspektionen use, and the bare pre-2018
# form that only the "Diarienr" column head tells apart from a date
RE_IMY_DNR = re.compile(r"\b(?:DI|IMY)-\d{4}-\d{1,6}\b")
RE_IMY_DNR_OLD = re.compile(r"\bDiarienr\b.{0,120}?\b(\d{3,5}-\d{4})\b")
RE_IMY_ISODATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# the current header labels its date ("Datum: 2026-07-03") and prints the
# counterparty's own dates around it, so the label is what makes the reading
# unambiguous; the pre-2018 header labels nothing and puts the decision date
# first, immediately after the "Diarienr" column head
RE_IMY_DATUM = re.compile(r"Datum:\s*(\d{4}-\d{2}-\d{2})")
# imy.se sets its headings with soft hyphens ("Dataskyddsom\xadbudens roll"):
# a rendering hint that must not reach a stored title or a search index
RE_IMY_SOFT_HYPHEN = re.compile("[­​]")
# the file-size trailer EPiServer appends to every document link's text
RE_IMY_FILEINFO = re.compile(r"\s*\((?:pdf|docx?)[^)]*\)\s*$", re.I)
# the verbiage a prose document link opens with, so what is left is the thing
# the document is *about* ("Läs beslutet mot Hemköp" -> "Hemköp") -- the
# multi-ärende pages have no info-block heading to take a title from
RE_IMY_LINK_LEAD = re.compile(
    r"^(?:Läs\s+|Ta del av\s+)?"
    r"(?:beslutet|beslut|tillsynsbeslutet|tillsynsskrivelsen|tillsynsskrivelse)"
    r"\s+(?:efter\s+\w+\s+|i tillsyn\s+)?(?:mot|för|till)\s+", re.I)
# the praxisbeslut fields, keyed on the stem IMY inflects per box ("Datum för
# beslut"/"beslutet"/"besluten", "Korrigerande åtgärd"/"åtgärder")
IMY_PRAXIS_FIELDS = (("datum för beslut", "beslutsdatum"),
                     ("korrigerande åtgärd", "korrigerandeAtgard"),
                     ("lagrum", "lagrum"),
                     ("nyckelord", "nyckelord"),
                     ("överklagan", "overklagan"),
                     ("vunnit laga kraft", "lagakraft"))


# --------------------------------------------------------------------------
# JO -- WordPress admin-ajax search API
# --------------------------------------------------------------------------

def jo_nonce(session):
    """The ajax nonce baked into the search page (session-bound: keep using the
    same session for the POSTs)."""
    response = request(session, "GET", JO_SEARCH_PAGE, timeout=60)
    match = RE_JO_NONCE.search(response.text)
    assert match, "jo.se search page carries no ajaxNonce -- site changed?"
    return match.group(1)


def jo_search(session, nonce, page, page_size=JO_PAGE_SIZE):
    """One page of the decision search (newest first, the UI default order).
    Returns the parsed envelope: search_hits + total_hits/total_pages."""
    return request(session, "POST", JO_AJAX, parse_json=True, timeout=60, data={
        "action": "get_jo_search_result", "_ajax_nonce": nonce,
        "global_search": "0", "sort_order": "", "search_string": "",
        "search_case_number": "", "date_from": "", "date_to": "",
        "hits_per_page": str(page_size), "page": str(page),
        "combine_type": json.dumps({"authorities": "OR", "matter_of_facts": "OR",
                                    "legal_regulations": "OR"}),
        "language": "sv", "advanced_search": "0"})


def jo_dnrs(diary_number):
    """Every diarienummer a hit's diary_number field names (a decision on joined
    complaints carries several); first = canonical."""
    return RE_JO_DNR.findall(diary_number or "")


def jo_record(hit, basefile):
    """The stored record: the hit verbatim minus ``_formatted`` (a duplicate of
    every field with search-highlight markup -- echo noise, doubles the size),
    plus our ``basefile`` (what `list_basefiles` enumerates by)."""
    record = {k: v for k, v in hit.items() if k != "_formatted"}
    record["basefile"] = basefile
    return record


def jo_save(root, hit, session, delay, full=False):
    """Store one hit's record (+ its decision PDF when missing on disk, or
    always under ``full`` -- consistent with jk_save/arn_save's ``--full``
    semantics of refetching an already-downloaded document, not just new
    ones). Returns True if the record is new or changed."""
    dnrs = jo_dnrs(hit.get("diary_number"))
    if not dnrs:
        print("jo: hit %s has no parsable diary_number %r, skipping"
              % (hit.get("id"), hit.get("diary_number")), flush=True)
        return False
    basefile = "jo/" + dnrs[0]
    record = jo_record(hit, basefile)
    path = record_path(root, "jo", basefile)
    changed = not record_unchanged(path, record)
    if changed:
        write_record(path, record)
    pdf_url = record.get("pdf_url")
    pdf = jo_pdf_path(root, basefile)
    if pdf_url and (full or not compress.exists(pdf)):
        response = request(session, "GET", pdf_url, timeout=120)
        if document_extension(response.content) == ".pdf":
            compress.write_download(pdf, response.content)
        else:
            print("jo: %s pdf_url served non-PDF, skipping body file"
                  % basefile, flush=True)
        time.sleep(delay)
    return changed


def jo_sync(root, full=False, only=None, limit=None, delay=0.5, log=print):
    """Download JO decisions onto :func:`lib.harvest.walk`. Newest-first;
    incremental runs stop once a run of already-downloaded decisions (or one
    older than the watermark boundary) shows the corpus is caught up. ``only`` =
    one basefile ("jo/2340-2025"): a targeted search on the case number."""
    session = make_session(USER_AGENT)
    nonce = jo_nonce(session)
    if only:
        dnr = only.split("/", 1)[1]
        envelope = request(session, "POST", JO_AJAX, parse_json=True, timeout=60,
                           data={"action": "get_jo_search_result",
                                 "_ajax_nonce": nonce, "global_search": "0",
                                 "sort_order": "", "search_string": "",
                                 "search_case_number": dnr, "date_from": "",
                                 "date_to": "", "hits_per_page": "10",
                                 "page": "1", "combine_type": "{}",
                                 "language": "sv", "advanced_search": "0"})
        hits = [h for h in envelope["search_hits"] if dnr in jo_dnrs(h.get("diary_number"))]
        assert hits, "jo.se search finds no decision with dnr %s" % dnr
        return 1, int(jo_save(root, hits[0], session, delay, full=full))

    marker = Path(root) / "jo" / COMPLETE
    watermark_path = Path(root) / "jo" / ".watermark.json"

    # Migrate legacy complete marker to watermark
    if marker.exists() and not watermark_path.exists():
        HarvestWatermark(watermark_path).save(date.today().isoformat())

    # JO decisions are dated to the day; a 14-day safety window past the newest
    # resolve_date plus a 20-hit lookahead comfortably covers a bump/reorder.
    watermark = HarvestWatermark(watermark_path, lookahead_limit=20, safety_days=14)

    # a lazy newest-first hit stream over the paged search; the first page also
    # yields total_hits for the progress line
    first = jo_search(session, nonce, 1)

    def hits():
        envelope, page = first, 1
        while True:
            yield from envelope["search_hits"]
            if page >= envelope["total_pages"]:
                return
            page += 1
            time.sleep(delay)
            envelope = jo_search(session, nonce, page)

    def item_key(hit):
        dnrs = jo_dnrs(hit.get("diary_number"))
        if not dnrs:
            return None                       # unparsable diary_number -- not a doc
        basefile = "jo/" + dnrs[0]
        pdf = jo_pdf_path(root, basefile)
        is_downloaded = compress.exists(record_path(root, "jo", basefile)) \
            and (not hit.get("pdf_url") or compress.exists(pdf))
        return ItemKey(basefile=basefile, is_downloaded=is_downloaded,
                       date=hit.get("resolve_date"))

    result = walk(hits(), resolve=lambda hit: jo_save(root, hit, session, delay, full=full),
                  item_key=item_key, watermark=watermark, full=full, limit=limit,
                  scope="jo", count_label="changed", total=first["total_hits"],
                  log=log)
    return result.seen, result.new


# --------------------------------------------------------------------------
# JK -- one-shot listing + per-decision landing pages
# --------------------------------------------------------------------------

def jk_canonical(raw):
    """The canonical diarienummer a raw jk.se ``Diarienr:`` value names -- the
    form a citation uses, which is the form the URI must carry:
    the first of a multi-dnr value ("2024/6800; 2024/7745"), any "JK " prefix
    dropped, and the old form's dotted ärendetyp compacted ("6098-19-4.4" ->
    "6098-19-44" -- jk.se's display quirk; citations write "dnr 6098-19-44")."""
    first = re.split(r"[;,]", raw)[0].strip()
    first = re.sub(r"^JK\s+", "", first)
    m = RE_JK_OLD.match(first)
    if m:
        return "%s-%s-%s" % (m.group(1), m.group(2), m.group(3).replace(".", ""))
    return first


def jk_parse_listing(html_text):
    """The decision entries of a listing response, newest first: {dnr_raw,
    beslutsdatum_raw, url, title} per ``div.date`` + following ``h2 > a``."""
    soup = BeautifulSoup(html_text, "html.parser")
    items = []
    for datediv in soup.select("div.results div.date"):
        text = datediv.get_text(" ", strip=True)
        m = re.search(r"Diarienr:\s*(.+?)\s*/\s*Beslutsdatum:\s*(.+)$", text)
        h2 = datediv.find_next_sibling("h2")
        link = h2.find("a") if h2 else None
        if not (m and link and link.get("href")):
            continue
        items.append({"dnr_raw": m.group(1).strip(),
                      "beslutsdatum_raw": m.group(2).strip(),
                      "url": JK_BASE + str(link["href"]),
                      "title": htmllib.unescape(link.get_text(" ", strip=True))})
    return items


def jk_listing(session):
    """Every decision jk.se carries, in one request -- the site's pagination is
    a POSTed ``page`` field and (still, as in the legacy code's day) a large
    page number returns the whole corpus."""
    response = request(session, "POST", JK_LIST, timeout=120,
                       data={"page": "9999"})
    return jk_parse_listing(response.text)


def jk_html_path(root, basefile):
    return Path(root) / "jk" / (basefile_slug(basefile) + ".html")


def jk_save(root, item, session, delay, full=False):
    """Store one decision: its landing page (the document itself) + record.
    Returns True when fetched (new/refreshed), False when already on disk
    unchanged. ``full`` bypasses the equality check to refetch. The new landing
    is fetched *before* the old is overwritten, so a failed fetch leaves the
    existing good record in place rather than a stub that later crashes parse."""
    basefile = "jk/" + jk_canonical(item["dnr_raw"])
    record = {"basefile": basefile, "org": "jk",
              "diarienummer_raw": item["dnr_raw"],
              "beslutsdatum_raw": item["beslutsdatum_raw"],
              "title": item["title"], "url": item["url"]}
    path = record_path(root, "jk", basefile)
    landing = jk_html_path(root, basefile)
    if not full and record_unchanged(path, record, landing):
        return False
    response = request(session, "GET", item["url"], timeout=60)
    compress.write_download(landing, response.text)
    write_record(path, record)
    time.sleep(delay)
    return True


def jk_sync(root, full=False, only=None, limit=None, delay=0.5):
    """Download JK decisions. The listing is one request, so every run walks all
    entries and fetches only what is missing or changed (``--full`` refetches
    landings too, by bypassing the record equality check in :func:`jk_save`)."""
    session = make_session(USER_AGENT)
    items = jk_listing(session)
    if only:
        dnr = only.split("/", 1)[1]
        items = [i for i in items if jk_canonical(i["dnr_raw"]) == dnr]
        assert items, "jk.se listing carries no decision with dnr %s" % dnr
    seen = new = 0
    rep = Reporter()
    for item in items:
        new += jk_save(root, item, session, delay, full=full)
        seen += 1
        rep.update(seen, len(items), changed=new)
        if limit and seen >= limit:
            break
    rep.done()
    return seen, new


# --------------------------------------------------------------------------
# ARN -- one static listing page + per-decision PDFs
# --------------------------------------------------------------------------

def arn_dnrs(text):
    """Every diarienummer a listing link's text names ("Referat 2018-06-14;
    2017-07814 (I) och 2017-13660 (II)" -> the two dnr, the embedded date skipped
    -- ``\\d{4}-\\d{4,}`` needs 4+ trailing digits); first names the referat."""
    return RE_ARN_DNR.findall(text or "")


def arn_parse_listing(html_text):
    """The referat entries of arn.se's vägledande-beslut page, in page order
    (newest first): per ``<h3>{avdelning}, beslut {ISO-datum}</h3>`` its summary
    paragraphs and the decision PDF link. Returns {avdelning, beslutsdatum,
    title, url, dnrs} per entry that carries both a date and a PDF link. Pure
    over the HTML so it is testable without network."""
    soup = BeautifulSoup(html_text, "html.parser")
    heading = next((h for h in soup.find_all("h2")
                    if "senaste" in h.get_text().lower()), None)
    assert heading is not None, \
        "arn.se listing has no 'De senaste ...' section -- site changed?"
    # collect element refs first, then mutate (extract the link) -- never during
    # the find_all_next walk
    entries, cur = [], None
    for el in heading.find_all_next():
        if el.name == "h2":
            break                       # the next top-level section ends the list
        if el.name == "h3":
            cur = {"h3": el, "ps": []}
            entries.append(cur)
        elif cur is not None and el.name == "p":
            cur["ps"].append(el)
    items = []
    for e in entries:
        h3 = normalize_space(e["h3"].get_text(" ", strip=True))
        date = RE_ARN_ISODATE.search(h3)
        link = None
        for p in e["ps"]:
            anchor = p.find("a", href=lambda h: h and "pdfer" in h)
            if anchor:
                link = (ARN_BASE + str(anchor["href"]),
                        normalize_space(anchor.get_text(" ", strip=True)))
                anchor.extract()        # so the "Referat NNNN" trailer leaves the title
                break
        if not (date and link and arn_dnrs(link[1])):
            continue
        summary = normalize_space(" ".join(normalize_space(p.get_text(" ", strip=True))
                                     for p in e["ps"]))
        items.append({"avdelning": h3.split(",", 1)[0].strip(),
                      "beslutsdatum": date.group(0), "title": summary,
                      "url": link[0], "dnrs": arn_dnrs(link[1])})
    return items


def arn_listing(session):
    """Every referat arn.se currently lists, in one request (the page carries no
    pagination -- the whole 'vägledande beslut' set is inline)."""
    return arn_parse_listing(request(session, "GET", ARN_LIST, timeout=120).text)


def arn_save(root, item, session, delay, full=False):
    """Store one referat: its record (parse_arn's shape + a live ``source_url``)
    and its decision PDF. Returns True when written (new, changed, or a frozen
    import overwritten -- live always wins), False when already on disk unchanged
    or when the site served a non-PDF body (rejected and logged, like jo_save --
    an error page must never be stored as the decision). The record carries no
    ``source`` marker key, so a frozen-import record of the same dnr never
    compares equal: it is overwritten and its converted PDF is replaced by the
    live one (the §7g precedence rule)."""
    basefile = "arn/" + item["dnrs"][0]
    record = {"basefile": basefile, "org": "arn",
              "diarienummer": item["dnrs"][0],
              "beslutsdatum": item["beslutsdatum"],
              "avdelning": item["avdelning"], "title": item["title"],
              "source_url": item["url"]}
    path = record_path(root, "arn", basefile)
    pdf = arn_pdf_path(root, basefile)
    if not full and record_unchanged(path, record, pdf):
        return False
    response = request(session, "GET", item["url"], timeout=120)
    time.sleep(delay)
    if document_extension(response.content) != ".pdf":
        print("arn: %s: %s served a non-PDF body, skipping"
              % (basefile, item["url"]), flush=True)
        return False
    compress.write_download(pdf, response.content)
    write_record(path, record)
    return True


def arn_sync(root, full=False, only=None, limit=None, delay=0.5):
    """Download ARN referat. The listing is one static page, so every run walks
    all entries and fetches only what is missing or changed (``--full`` refetches
    every PDF and rewrites every record). ``only`` = one basefile
    ("arn/2026-00382"): the matching listing entry."""
    session = make_session(USER_AGENT)
    items = arn_listing(session)
    if only:
        dnr = only.split("/", 1)[1]
        items = [i for i in items if i["dnrs"][0] == dnr]
        assert items, "arn.se listing carries no decision with dnr %s" % dnr
    seen = new = 0
    rep = Reporter()
    for item in items:
        new += arn_save(root, item, session, delay, full=full)
        seen += 1
        rep.update(seen, len(items), changed=new)
        if limit and seen >= limit:
            break
    rep.done()
    return seen, new


# --------------------------------------------------------------------------
# IMY -- listing pages + per-tillsyn landing pages, keyed on the diarienummer
# --------------------------------------------------------------------------

def imy_slug(url):
    """A tillsyn page's own path segment ("…/tillsyner/polismyndigheten-vis/" ->
    "polismyndigheten-vis"). imy.se writes the url both with and without the
    trailing slash (the RSS drops it on four entries), so it is stripped."""
    return url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]


def imy_asset_name(url):
    """The stored file name of an attached document, taken from the asset path
    ("/globalassets/dokument/beslut/2026/beslut-tillsyn-polismyndigheten.pdf" ->
    "beslut-tillsyn-polismyndigheten.pdf"). Every basename in the corpus is
    unique, so this both names the file and folds together the documents that
    several tillsyner share -- the vårdgivar-vägledning hangs off eight pages,
    the 1177-rapport off six, and one asset must be fetched and stored once. A
    ``/link/<guid>.aspx`` document redirect keeps its GUID as the name."""
    name = url.split("?", 1)[0].rsplit("/", 1)[-1]
    return name[:-len(".aspx")] + ".pdf" if name.endswith(".aspx") else name


def imy_text(element):
    """An imy.se element's display text: whitespace-collapsed, and stripped of
    the soft hyphens the CMS sets its headings with. They are line-breaking
    hints, invisible on the page but not in a stored title or a search index --
    "Dataskyddsom\xadbudens roll" must be filed as "Dataskyddsombudens roll"."""
    return RE_IMY_SOFT_HYPHEN.sub(
        "", normalize_space(element.get_text(" ", strip=True)))


def imy_document_title(anchor, text):
    """A document's title. The common "Beslut" card states it as the enclosing
    info-block's heading; the pages that decide several ärenden list their
    decisions as prose links instead, whose text carries EPiServer's file-size
    trailer and opens with the verb rather than the subject, so both are peeled
    off ("Läs beslutet mot Hemköp (pdf, 109 kB)" -> "Hemköp")."""
    block = anchor.find_parent(class_="imy-info-block")
    heading = block.find(class_="imy-info-block__heading") if block else None
    if heading is not None:
        return imy_text(heading)
    return RE_IMY_LINK_LEAD.sub("", RE_IMY_FILEINFO.sub("", text)) or text


def imy_parse_listing(html_text):
    """One ``/tillsyner/?page=N`` page's hits: {slug, url, title, status,
    kategorier} per search-hit anchor, in page order. Pure over the HTML so the
    rules are testable without network. Also returns the total page count the
    listing component declares, so :func:`imy_listing` knows when to stop."""
    soup = BeautifulSoup(html_text, "html.parser")
    pagecount = RE_IMY_PAGECOUNT.search(html_text)
    assert pagecount, "imy.se listing carries no data-pagecount -- site changed?"
    items = []
    for anchor in soup.select("a.imy-search-hit[href]"):
        url = str(anchor["href"]).split("?", 1)[0]
        heading = anchor.find(class_="imy-search-hit__heading")
        assert heading is not None, "imy.se search hit %s has no heading" % url
        # the desktop and mobile detail sections repeat status and categories,
        # so the first occurrence of each is the whole story
        status = anchor.find(class_="imy-search-hit__detail-text")
        items.append({
            "slug": imy_slug(url), "url": url,
            "title": imy_text(heading),
            "status": imy_text(status) if status else None,
            "kategorier": list(dict.fromkeys(
                imy_text(c) for c in
                anchor.select(".imy-search-hit__category-item-link")))})
    return items, int(pagecount.group(1))


def imy_listing(session, delay):
    """Every tillsyn imy.se lists, newest first. The listing is a Vue component
    fed by ``/api/search/listsearch``, but the server renders the same hits for
    a plain ``?page=N``, so the enumeration needs no API contract."""
    items, pages = imy_parse_listing(
        request(session, "GET", IMY_LIST, timeout=120).text)
    for page in range(2, pages + 1):
        time.sleep(delay)
        more, _ = imy_parse_listing(
            request(session, "GET", "%s?page=%d" % (IMY_LIST, page),
                    timeout=120).text)
        items.extend(more)
    return items


def imy_guid_map(session):
    """content GUID (dashes stripped) -> tillsyn url, from the ``/tillsyner``
    RSS feed. The curated pages link every entry as a ``/link/<guid>.aspx``
    redirect; the feed pairs the same GUID with the page url, so one request
    resolves the lot instead of one redirect apiece."""
    return {guid.replace("-", ""): url for guid, url in
            RE_IMY_RSS_ITEM.findall(request(session, "GET", IMY_RSS,
                                            timeout=60).text)}


def imy_curated_slugs(anchor, guid_map):
    """The tillsyn slugs a curated-page link names: a direct ``/tillsyner/…``
    href, or a ``/link/<guid>.aspx`` redirect resolved through the feed's GUID
    map. Anything else names none -- a document link, an outbound page, or the
    ``/tillsyner/`` listing itself, which both curated pages link back to."""
    href = str(anchor["href"])
    if href.startswith("/tillsyner/") and imy_slug(href) != "tillsyner":
        return [imy_slug(href)]
    guid = RE_IMY_GUID_LINK.match(href)
    if guid and guid.group(1) in guid_map:
        return [imy_slug(guid_map[guid.group(1)])]
    return []


def imy_praxis_fields(paragraph):
    """One praxis box's ``<strong>``-labelled metadata paragraph -> the fields
    it declares. Each label is matched by its stem because IMY inflects them
    per box ("Datum för beslut"/"beslutet"/"besluten", "Korrigerande
    åtgärd"/"åtgärder"); an unrecognised label means the curated schema grew a
    field, which has to be looked at rather than guessed past."""
    fields = {}
    for strong in paragraph.find_all("strong"):
        label = imy_text(strong).rstrip(":").lower()
        key = next((k for stem, k in IMY_PRAXIS_FIELDS if label.startswith(stem)),
                   None)
        assert key, "imy.se praxisbeslut carries an unknown field %r" % label
        # the value runs from this label to the *next* one -- the fields are one
        # paragraph of "<strong>label:</strong> value<br />" rows
        value = []
        for sibling in strong.next_siblings:
            if sibling.name == "strong":
                break
            if sibling.name != "br":
                value.append(sibling.get_text(" ", strip=True)
                             if sibling.name else str(sibling))
        fields[key] = RE_IMY_SOFT_HYPHEN.sub(
            "", normalize_space("".join(value))).lstrip(": ")
    return fields


def imy_parse_praxis(html_text, guid_map):
    """The praxisbeslut page -> {tillsyn slug: praxis entry}. Each entry is one
    expandable box: the ämne it files under (the ``h3`` above it), IMY's own
    rubrik and summary, and a closing paragraph of labelled fields -- lagrum,
    nyckelord, whether the decision was appealed and whether it has gained legal
    force -- that appear nowhere else on the site. A box may name several
    tillsyner (the six dataskyddsombud granskningar, the four Google Analytics
    decisions), and then every one of them carries it."""
    soup = BeautifulSoup(html_text, "html.parser")
    main = soup.find("div", class_="imy-contentpage__main-content")
    assert main is not None, "imy.se praxisbeslut page has no main content"
    curated, amne = {}, None
    for el in main.find_all(["h3", "div"]):
        if el.name == "h3":
            amne = imy_text(el)
            continue
        if "imy-expandable-box" not in (el.get("class") or []):
            continue
        heading = el.find(class_="imy-expandable-box__heading")
        assert heading is not None, "imy.se praxis box has no heading"
        labelled = [p for p in el.find_all("p") if p.find("strong")]
        entry = {"amne": amne, "rubrik": imy_text(heading),
                 "sammanfattning": normalize_space(" ".join(
                     imy_text(p) for p in el.find_all("p")
                     if p not in labelled))}
        if labelled:
            entry.update(imy_praxis_fields(labelled[-1]))
        for anchor in el.find_all("a", href=True):
            for slug in imy_curated_slugs(anchor, guid_map):
                curated[slug] = entry
    return curated


def imy_parse_sanktion(html_text, guid_map):
    """The sanktionsavgift page -> {tillsyn slug: the fine as IMY states it}.
    Every entry reads "{den granskade}: {beloppet}" ("Spotify: 58 miljoner
    kronor", "Nusvar AB: 35 000 euro") -- and the amount is the one thing the
    tillsyn page itself never says."""
    soup = BeautifulSoup(html_text, "html.parser")
    main = soup.find("div", class_="imy-contentpage__main-content")
    assert main is not None, "imy.se sanktionsavgift page has no main content"
    curated = {}
    for anchor in main.find_all("a", href=True):
        slugs = imy_curated_slugs(anchor, guid_map)
        if not slugs:
            continue
        text = imy_text(anchor).replace("\xa0", " ")
        _who, sep, belopp = text.rpartition(": ")
        assert sep, "imy.se sanktionsavgift entry %r names no amount" % text
        for slug in slugs:
            curated[slug] = belopp
    return curated


def imy_page_metadata(landing_html):
    """What a tillsyn page says about the decisions it publishes: its heading,
    the ingress ("Tillsyn enligt dataskyddsförordningen. Beslut 2026-07-03."),
    the tillsyn's current step, and IMY's own summary of the outcome -- the
    paragraph under the step heading in the status block, which is the closest
    thing to an editor-written abstract these decisions have."""
    soup = BeautifulSoup(landing_html, "html.parser")
    heading = soup.select_one("h1.imy-contentpage__heading")
    assert heading is not None, "imy.se tillsyn page has no h1 -- site changed?"
    ingress = soup.select_one(".imy-contentpage__preamble")
    step = soup.select_one(".imy-status-in-process__visualization-item--current"
                           " .imy-status-in-process__heading")
    summary = soup.select_one(".imy-status-in-process__description")
    return {"titel": imy_text(heading),
            "ingress": imy_text(ingress) if ingress else None,
            "status": imy_text(step) if step else None,
            "sammanfattning": imy_text(summary) if summary else None}


def imy_documents(landing_html):
    """The documents a tillsyn page attaches, in page order: {titel, url, fil,
    sprak}. A document is an anchor to a PDF, or one EPiServer marks
    ``data-type="DOC"``; its title is the enclosing info-block's heading where
    there is one (the common "Beslut" card) and the link's own text otherwise
    (the multi-decision pages list them as prose links). IMY also attaches
    English translations of a few decisions -- flagged, because a translation
    shares its decision's diarienummer and must not be read as its body."""
    soup = BeautifulSoup(landing_html, "html.parser")
    main = soup.find("div", class_="imy-contentpage__main-content")
    if main is None:                  # a tillsyn page with no body yet
        return []
    documents = []
    for anchor in main.find_all("a", href=True):
        href = str(anchor["href"])
        if not (href.lower().endswith(".pdf") or anchor.get("data-type") == "DOC"):
            continue
        text = imy_text(anchor)
        if not href.startswith(("/", "http")):
            print("imy: document link %r has no target, skipping" % text,
                  flush=True)
            continue
        documents.append({
            "titel": imy_document_title(anchor, text),
            "url": IMY_BASE + href if href.startswith("/") else href,
            "fil": imy_asset_name(href),
            "sprak": "en" if text.lower().startswith("in english") else "sv"})
    return documents


def imy_diarienummer(pdf_path):
    """The diarienummer and date an IMY decision prints in its page-1 header,
    as ``(dnr, ISO date)``; ``(None, …)`` where the published PDF carries no
    readable number.

    The download layer reads the PDF because the diarienummer is the document's
    identity and it exists *only* there -- the tillsyn page that links the
    decision never states it -- so the basefile cannot be minted without it.
    Two header generations: IMY and late Datainspektionen print a prefixed
    "IMY-2024-2904"/"DI-2019-3375", pre-2018 Datainspektionen a bare
    "2495-2017" that only its position after the "Diarienr" column head tells
    apart from a date or a form number. The date is read the same two ways: the
    current header labels it ("Datum: 2026-07-03") and prints the counterparty's
    own reference dates beside it, so the label is what disambiguates; the
    pre-2018 header labels nothing and prints the decision date first, so there
    the first date on the page is it. Neither dnr form matches when IMY
    publishes a decision anonymously (the number is redacted to "DI-2018-XXXX" or dropped
    from an "Avidentifierad version") or when the PDF is a scan with no text
    layer; such a decision has no identity to be filed under and the caller
    reports it rather than inventing one."""
    text = pdf_first_page_text(pdf_path)
    prefixed = RE_IMY_DNR.search(text)
    bare = RE_IMY_DNR_OLD.search(text)
    labelled = RE_IMY_DATUM.search(text)
    first = RE_IMY_ISODATE.search(text)
    return (prefixed.group(0) if prefixed else bare.group(1) if bare else None,
            labelled.group(1) if labelled
            else first.group(0) if first else None)


def imy_fetch_document(root, document, session, delay, full=False):
    """Store one attached document, named by its asset (so the eight tillsyner
    that share the vårdgivar-vägledning fetch and keep one copy). Returns the
    stored path, or None when imy.se served something that is not a PDF."""
    path = imy_pdf_path(root, document["fil"])
    if compress.exists(path) and not full:
        return path
    response = request(session, "GET", document["url"], timeout=180)
    time.sleep(delay)
    if document_extension(response.content) != ".pdf":
        print("imy: %s served a non-PDF body, skipping" % document["url"],
              flush=True)
        return None
    compress.write_download(path, response.content)
    return path


def imy_records(pages):
    """The harvested tillsyn pages -> one record per diarienummer, in the order
    the decisions were seen.

    The diarienummer is the identity, and it is neither one-per-page nor
    one-per-document: a page can decide several ärenden (the seven
    "Grannbevakning" beslut, the seven brottsbekämpande myndigheter), one
    ärende can be published as several documents (a beslut plus the
    tillsynsskrivelse that opened it, plus an English translation), and one
    document can hang off several pages (the vårdgivar-vägledning off eight).
    So the documents of every page are regrouped by the number their PDFs
    print: each group becomes one decision, carrying its parts, and every
    tillsyn page it was reached from. A page that decides more than one ärende
    names each record by the document heading too, so listings can tell them
    apart. The page metadata comes from the first page in listing order (newest
    first) that reached the decision.

    Returns (records, orphans) -- the documents whose PDF prints no readable
    number, which no basefile can be minted for."""
    records, orphans = {}, []
    for page in pages:
        dnrs = [d.get("diarienummer") for d in page["dokument"]]
        for document in page["dokument"]:
            dnr = document.get("diarienummer")
            if dnr is None:
                orphans.append((page["slug"], document["titel"]))
                continue
            part = {k: document[k] for k in ("titel", "url", "fil", "sprak")}
            tillsyn = {"slug": page["slug"], "url": page["url"]}
            if dnr in records:
                record = records[dnr]
                # one asset is one part, whatever the linking page called it:
                # two tillsyner link the Expressen Lifestyle decision under
                # different link texts, and reading it twice would double its
                # text in the body and in the search index
                if part["fil"] not in {d["fil"] for d in record["delar"]}:
                    record["delar"].append(part)
                if tillsyn not in record["tillsyner"]:
                    record["tillsyner"].append(tillsyn)
                continue
            titel = page["titel"]
            if len({d for d in dnrs if d}) > 1:
                titel = "%s – %s" % (titel, document["titel"])
            records[dnr] = {
                "basefile": "imy/" + dnr, "org": "imy", "diarienummer": dnr,
                "titel": titel, "beslutsdatum": document.get("beslutsdatum"),
                "ingress": page["ingress"], "status": page["status"],
                "sammanfattning": page["sammanfattning"],
                "kategorier": page["kategorier"],
                "tillsyner": [tillsyn], "delar": [part],
                **page["curated"]}
    return list(records.values()), orphans


def imy_save(root, record, full=False):
    """Write one decision's record when it is new or changed. The PDFs it names
    are already on disk (they are what the diarienummer was read from), so the
    record is the only thing this writes -- and rewriting it unchanged would
    re-stale the parse for nothing."""
    return store_record(record_path(root, "imy", record["basefile"]), record,
                        full=full)


def imy_sync(root, full=False, only=None, limit=None, delay=0.5):
    """Download IMY decisions. The listing is walked whole on every run (13
    pages today) and each tillsyn page's attached documents are fetched, read
    for the diarienummer that names them, and regrouped into one record per
    decision; ``--full`` refetches every document. The two curated pages are
    read first for the praxisbeslut fields and the sanktionsavgift amounts,
    which the tillsyn pages never carry.

    ``only`` = one basefile ("imy/IMY-2024-2904"). A decision names no tillsyn
    page of its own, so the page to refetch is looked up in the stored record:
    a decision the corpus has not seen yet can only be reached by a full run."""
    session = make_session(USER_AGENT)
    guid_map = imy_guid_map(session)
    praxis = imy_parse_praxis(
        request(session, "GET", IMY_PRAXIS, timeout=120).text, guid_map)
    sanktion = imy_parse_sanktion(
        request(session, "GET", IMY_SANKTION, timeout=120).text, guid_map)
    items = imy_listing(session, delay)
    if only:
        path = record_path(root, "imy", only)
        assert compress.exists(path), (
            "imy --only %s: no record on disk, so the tillsyn page that "
            "publishes it is unknown -- run a full `lagen avg download imy` "
            "first" % only)
        slugs = {t["slug"] for t in
                 compress.read_json(path)["tillsyner"]}
        items = [i for i in items if i["slug"] in slugs]
        assert items, "imy.se listing no longer carries %s" % only
    if limit:
        items = items[:limit]

    pages, rep = [], Reporter()
    for seen, item in enumerate(items, 1):
        landing = request(session, "GET", item["url"], timeout=120).text
        time.sleep(delay)
        page = {**item, **imy_page_metadata(landing),
                "dokument": imy_documents(landing), "curated": {}}
        if item["slug"] in praxis:
            page["curated"]["praxis"] = praxis[item["slug"]]
        if item["slug"] in sanktion:
            page["curated"]["sanktionsavgift"] = sanktion[item["slug"]]
        for document in page["dokument"]:
            stored = imy_fetch_document(root, document, session, delay, full=full)
            document["diarienummer"], document["beslutsdatum"] = (
                imy_diarienummer(stored) if stored else (None, None))
        pages.append(page)
        rep.update(seen, len(items), dokument=sum(len(p["dokument"])
                                                  for p in pages))
    rep.done()

    records, orphans = imy_records(pages)
    new = sum(imy_save(root, record, full=full) for record in records)
    if orphans:
        print("imy: %d document(s) print no diarienummer and cannot be filed: %s"
              % (len(orphans), ", ".join("%s/%s" % o for o in orphans)),
              flush=True)
    return len(records), new


# --------------------------------------------------------------------------
# KKV -- the diarium's own search API, sliced by year
# --------------------------------------------------------------------------

def kkv_session():
    """Konkurrensverket sits behind a Cloudflare front that 403s HTTP/1.1 and
    only serves HTTP/2 (the same reason `foreskrift/agencies.py`'s KKVFS sets
    ``http2=True``), so the diarium is harvested over the httpx client."""
    return make_http2_session(USER_AGENT)


def kkv_listing(session, casetype):
    """One ärendetyp group's cases, as the diarium's own search returns them.
    The search page is server-rendered React, but with ``X-Requested-With`` it
    answers with the bare result JSON instead of a 300 kB page -- 500 bytes per
    case rather than 4 kB. The group is the harvest's slice because the
    diarium's paging is *cumulative* (``page=2`` re-sends page 1), so ``take``
    asks for the whole group in one response instead; the assert is the guard
    that a group never silently truncates."""
    envelope = request(session, "GET", KKV_SEARCH, parse_json=True, timeout=120,
                       headers={"X-Requested-With": "XMLHttpRequest"},
                       params=[*KKV_STATUS, ("caseTypeFrom", casetype[0]),
                               ("caseTypeTo", casetype[1]),
                               ("take", str(KKV_TAKE))])
    items = envelope["items"]
    assert len(items) == envelope["pagination"]["total"], (
        "kkv caseType %s-%s: got %d of %d cases -- raise KKV_TAKE"
        % (*casetype, len(items), envelope["pagination"]["total"]))
    return items


def kkv_cases(session, delay):
    """Every case in the harvested set, deduplicated by diarienummer. The five
    ärendetyp groups do not overlap today, but they are the *site's* ranges and
    nothing guarantees they stay disjoint, so the union is taken by number."""
    cases = {}
    for casetype in KKV_CASETYPES:
        for item in kkv_listing(session, casetype):
            cases.setdefault(item["caseNumber"], item)
        time.sleep(delay)
    return cases


def kkv_arendelista(session, delay):
    """Every case on Konkurrensverkets curated ärendelista ("ett urval av våra
    ärenden och beslut inom konkurrensområdet", 329 today). Nine per page and
    ``page`` is a true offset here -- unlike the diarium's cumulative paging --
    so the walk is one request per page."""
    first = kkv_arendelista_page(session, 1)
    items = list(first["items"])
    for page in range(2, first["pagination"]["pageCount"] + 1):
        time.sleep(delay)
        items.extend(kkv_arendelista_page(session, page)["items"])
    assert len(items) == first["pagination"]["total"], (
        "kkv ärendelista: collected %d of %d cases"
        % (len(items), first["pagination"]["total"]))
    return items


def kkv_arendelista_page(session, page):
    return request(session, "GET", KKV_ARENDELISTA, parse_json=True, timeout=120,
                   headers={"X-Requested-With": "XMLHttpRequest"},
                   params={"page": str(page)})


def kkv_casebox(item):
    """The curated case box as a plain {contentName: [values]} map. The site
    ships it as an ordered list of labelled slots, several of them multi-valued
    -- a case names up to 17 parties and up to four kinds of beslut."""
    return {c["contentName"]: list(c.get("value") or [])
            for c in item.get("caseBoxContents", [])}


def kkv_curated_dnrs(box):
    """The diarienummer a curated case names, canonicalized ("dnr 288/2022" ->
    "288/2022"). A fifth of them name several: an ärende that grew into more
    than one diarium case (a decision and the court proceeding it became), and
    the curated account belongs to every one of them."""
    return [m.group(1) for value in box.get("DiaryNumber", [])
            for m in [RE_KKV_DNR.search(value)] if m]


def kkv_referat(content, url):
    """A curated case page -> the account Konkurrensverket writes about the
    case, which is the thing the diarium has no equivalent of: a named case
    ("Digitala vårdtjänster"), what it was about and why it was prioritized,
    the decision, and what the courts then did with it -- sectioned under the
    page's own headings ("Vad ärendet rör", "Konkurrensverkets beslut",
    "Tingsrätten", "Marknadsdomstolen") -- plus the branch, the parties and the
    kinds of beslut the case produced.

    The narrative also links documents, and they are of two kinds: KKV's own
    (``/globalassets/…``, the decision or a stämningsansökan) and the courts'
    (domstol.se). Only the first are ours to fetch; the rest are recorded as
    the links they are."""
    box = kkv_casebox(content)
    sections, documents, external = [], [], []
    for fragment in content.get("text", {}).get("fragments", []):
        raw = fragment.get("raw") or ""
        if fragment.get("modelType") == "HeadingFragment":
            sections.append({"rubrik": normalize_space(
                BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)),
                "stycken": []})
            continue
        soup = BeautifulSoup(raw, "html.parser")
        for anchor in soup.find_all("a", href=True):
            href = str(anchor["href"])
            (documents if href.startswith("/") else external).append(
                {"titel": normalize_space(anchor.get_text(" ", strip=True)),
                 "url": kkv_absolute(href)})
        if not sections:                  # prose before the first heading
            sections.append({"rubrik": None, "stycken": []})
        sections[-1]["stycken"].extend(
            filter(None, (normalize_space(p.get_text(" ", strip=True))
                          for p in soup.find_all(["p", "li"]))))
    return {"namn": normalize_space(content.get("heading")),
            "ingress": normalize_space(content.get("preamble")) or None,
            "parter": box.get("Purchaser", []),
            "bransch": box.get("Branch", []),
            "arendetyp": box.get("Type", []),
            "beslutstyp": box.get("PartOfProcess", []),
            "artal": (box.get("Year") or [None])[0],
            "status": (box.get("Status") or [None])[0],
            "url": url, "avsnitt": sections,
            "dokument": [d for d in documents
                         if d["url"].lower().endswith((".pdf", ".htm", ".html"))],
            "externa_lankar": external}


def kkv_curated(session, delay, wanted=None):
    """diarienummer -> the curated account of its case, for every case on the
    ärendelista. One request per listing page plus one per case whose account is
    actually needed -- ``wanted`` narrows that second half to the diarienummer a
    single-document run asks for, since the listing already says which case
    names it and the other 328 case pages are then dead weight."""
    curated = {}
    for item in kkv_arendelista(session, delay):
        dnrs = kkv_curated_dnrs(kkv_casebox(item))
        if not dnrs:
            print("kkv: curated case %s names no diarienummer, skipping"
                  % item.get("link"), flush=True)
            continue
        if wanted is not None and not wanted.intersection(dnrs):
            continue
        content = request(session, "GET", kkv_absolute(item["link"]),
                          parse_json=True, timeout=60,
                          headers={"Accept": "application/json"})["content"]
        time.sleep(delay)
        referat = kkv_referat(content, kkv_absolute(item["link"]))
        for dnr in dnrs:
            curated[dnr] = referat
    return curated


def kkv_case(session, case_number):
    """One case's ärendedata record. The listing carries most of it, but not the
    *beslutsdatum* -- the date the decision was actually made, as against the
    date the case was registered -- nor the separate sammanfattning document a
    few cases publish, and both live only here."""
    return request(session, "GET", KKV_CASE, parse_json=True, timeout=60,
                   headers={"Accept": "application/json"},
                   params={"caseNumber": case_number})["content"]


def kkv_asset_name(url):
    """The stored file name of a case's document.

    Two url shapes name one: the diarium serves its documents from a file
    endpoint whose ``pdf=`` parameter is the real name
    ("…/arendedata/file?pdf=26-0558.pdf"), and it is called ``pdf`` whatever the
    format actually is -- two thirds of that corpus is PDF, a third the
    FrontPage-era HTML published before ~2006, two cases Word. The curated
    ärendelista instead links its documents as plain asset paths
    ("/globalassets/…/15-0630-arla-foods-amba.pdf"), where the last segment is
    the name."""
    query = parse_qs(urlsplit(url).query)
    name = unquote(query["pdf"][0] if "pdf" in query
                   else urlsplit(url).path.rsplit("/", 1)[-1])
    # the name comes off a remote url and is joined onto the corpus root, so
    # this is load-bearing validation of untrusted input, not an invariant:
    # under -O an assert would vanish and a name of "../.." would write
    # outside the store (rule:errors-drive-retry-use-raise)
    if not name or "/" in name or name in (".", ".."):
        raise ValueError("kkv: document name %r is not a plain file name" % name)
    return name


def kkv_body_path(root, name):
    """One case's decision document, under its own diarium file name
    ("26-0558.pdf" -> ``<root>/kkv/dok/26-0558.pdf``). The format varies with
    the era, so the name keeps the extension the diarium published it under and
    parse routes on it."""
    return Path(root) / "kkv" / "dok" / name


def kkv_date(raw):
    """An ärendedata date, or None. The diarium writes an unknown date as "-"
    (a case registered before its arrival was recorded), which is not a date and
    must not be stored as one."""
    raw = (raw or "").strip()
    return raw if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw) else None


def kkv_absolute(url):
    """A diarium url as an absolute one. The two halves of the API disagree: the
    listing writes a document link site-relative, the ärendedata record writes
    the same link absolute."""
    return KKV_BASE + url if url.startswith("/") else url


def kkv_record(item, case, referat=None):
    """The stored record: the diarium's own fields under Swedish names, plus the
    curated account of the case where the ärendelista has one. All three parts
    are authoritative for different things -- the listing names the case, the
    ärendedata page dates the decision, the ärendelista says what the case was
    about and how it ended -- so none of them is re-derived from the others or
    from the document."""
    document = item.get("decisionLink")
    summary = case.get("summaryLink")
    record = {"basefile": "kkv/" + item["caseNumber"], "org": "kkv",
              "diarienummer": item["caseNumber"],
              "titel": normalize_space(item["subject"]),
              "arendetyp": normalize_space(item.get("type")) or None,
              "status": item.get("status"),
              "avdelning": normalize_space(case.get("department")) or None,
              "motpart": normalize_space(item.get("counterpart")) or None,
              "ankomstdatum": kkv_date(case.get("arrivalDate")),
              "registreringsdatum": kkv_date(
                  (item.get("registrationDate") or "").split("T")[0]),
              "beslutsdatum": kkv_date(case.get("decisionDate")),
              "url": KKV_BASE + item["url"]}
    if document:
        record["dokument"] = {"url": kkv_absolute(document),
                              "fil": kkv_asset_name(document)}
    if summary:
        record["sammanfattning_dokument"] = {"url": kkv_absolute(summary),
                                             "fil": kkv_asset_name(summary)}
    if referat:
        record["referat"] = referat
    return record


def kkv_curated_record(dnr, referat):
    """The record for a case the ärendelista carries but the diarium's
    published-and-closed set does not -- most of the 413, including the ones
    from 1993-97 that predate the diarium itself. The curated account is then
    the whole document: it names the case and links whatever Konkurrensverket
    published of it.

    It carries no *beslutsdatum*. The account dates a case by årtal, and an
    årtal is a span ("2025-2026", "2022-"), not the day a decision was made;
    deriving one from the other would be inventing the precision. The span is
    stored as what it is, and the year facet falls back to the diarienummer's
    own year (`facets._avg_year`)."""
    document = next((d for d in referat["dokument"]), None)
    record = {"basefile": "kkv/" + dnr, "org": "kkv", "diarienummer": dnr,
              "titel": referat["namn"],
              "arendetyp": (referat["arendetyp"] or [None])[0],
              "status": referat["status"], "avdelning": None,
              "motpart": ", ".join(referat["parter"]) or None,
              "ankomstdatum": None, "registreringsdatum": None,
              "beslutsdatum": None, "artal": referat["artal"],
              "url": referat["url"], "referat": referat}
    if document:
        record["dokument"] = {"url": document["url"],
                              "fil": kkv_asset_name(document["url"])}
    return record


def kkv_save(root, item, referat, session, delay, full=False):
    """Store one case: its record and the document(s) it publishes. Returns True
    when written. The ärendedata page is fetched only when the listing entry (or
    the curated account) has moved, or nothing is stored yet: the listing
    carries every field that identifies and dates the *case*, so an unchanged
    entry means an unchanged case, and an incremental run costs five requests
    for the whole diarium side rather than one per case."""
    basefile = "kkv/" + item["caseNumber"]
    path = record_path(root, "kkv", basefile)
    stored = compress.read_json(path, default=None)
    if stored and not full \
            and _kkv_settled(stored) == _kkv_settled(kkv_record(item, {}, referat)) \
            and _kkv_bodies_on_disk(root, stored):
        return False
    record = kkv_record(item, kkv_case(session, item["caseNumber"]), referat)
    time.sleep(delay)
    return _kkv_write(root, path, stored, record, session, delay, full)


def kkv_save_curated(root, dnr, referat, session, delay, full=False):
    """Store a case the ärendelista carries and the diarium's set does not."""
    path = record_path(root, "kkv", "kkv/" + dnr)
    stored = compress.read_json(path, default=None)
    record = kkv_curated_record(dnr, referat)
    return _kkv_write(root, path, stored, record, session, delay, full)


def _kkv_write(root, path, stored, record, session, delay, full):
    """Fetch the record's documents and write it if anything changed.

    A document the diarium refused to serve (an error page under a decision's
    name) is dropped from the record rather than left naming a file that is not
    there. Otherwise the record would assert a document parse cannot find, and
    the missing file would keep `_kkv_bodies_on_disk` false, rewriting the
    record and re-staling its parse on every run."""
    for key in ("dokument", "sammanfattning_dokument"):
        if key in record and kkv_fetch_document(
                root, record[key], session, delay, full=full) is None:
            del record[key]
    if stored == record and _kkv_bodies_on_disk(root, record) and not full:
        return False
    write_record(path, record)
    return True


def _kkv_settled(record):
    """The part of a record the cheap sources -- the diarium listing and the
    curated account -- are authoritative for. Equal here means the case has not
    moved, and the per-case ärendedata request can be skipped."""
    return {k: record.get(k) for k in
            ("diarienummer", "titel", "arendetyp", "status", "motpart",
             "registreringsdatum", "url", "dokument", "referat")}


def _kkv_bodies_on_disk(root, record):
    return all(compress.exists(kkv_body_path(root, record[key]["fil"]))
               for key in ("dokument", "sammanfattning_dokument")
               if key in record)


def kkv_fetch_document(root, document, session, delay, full=False):
    """Store one case document, keeping the format the diarium published it in.

    HTML needs a rule the other sources do not: `document_extension` refuses it
    (everywhere else an HTML body *is* the error page), but a third of this
    corpus legitimately is HTML. What tells the two apart is the diarium's own
    file name -- a decision published as HTML is named ``.htm``, so HTML under
    any other name is the error page and is rejected."""
    path = kkv_body_path(root, document["fil"])
    if compress.exists(path) and not full:
        return path
    response = request(session, "GET", document["url"], timeout=180)
    time.sleep(delay)
    if document_extension(response.content) is None \
            and not _kkv_is_published_html(document["fil"], response.content):
        print("kkv: %s served neither a document nor its promised HTML, skipping"
              % document["url"], flush=True)
        return None
    compress.write_download(path, response.content)
    return path


def _kkv_is_published_html(name, data):
    """Whether the bytes are an HTML decision the diarium *says* it publishes as
    HTML -- markup under an ``.htm`` name. HTML under a ``.pdf`` name is an
    error page, however well-formed."""
    return (name.lower().endswith((".htm", ".html"))
            and (b"<html" in data[:1024].lower()
                 or b"<!doctype html" in data[:1024].lower()))


def kkv_sync(root, full=False, only=None, limit=None, delay=0.5):
    """Download Konkurrensverkets tillsynsbeslut.

    Two sources, joined on the diarienummer. The **diarium** supplies the
    decisions: its published-and-closed cases narrowed to the agency's own five
    supervisory ärendetyp groups (`KKV_CASETYPES`), 1,830 cases since 1998, each
    with the document it was published as. The curated **ärendelista** supplies
    what the diarium cannot -- Konkurrensverkets own account of a case, its
    branch, the parties, and what the courts did with the decision afterwards --
    for the 329 cases it covers -- 413 diarienummer, since a fifth of them name
    several -- of which most name a case the narrowed diarium set does not carry
    (some from 1993-97, before the diarium begins; the rest the hand-picked
    företagsförvärv the bulk exclusion drops), and those are stored from the
    curated account alone.

    ``only`` = one basefile ("kkv/558/2026")."""
    session = kkv_session()
    if only:
        case_number = only.split("/", 1)[1]
        curated = kkv_curated(session, delay, wanted={case_number})
        item = kkv_cases(session, delay).get(case_number)
        if item is None:
            assert case_number in curated, \
                "kkv: %s is neither a narrowed diarium case nor on the ärendelista" % only
            return 1, int(kkv_save_curated(root, case_number, curated[case_number],
                                           session, delay, full=full))
        return 1, int(kkv_save(root, item, curated.get(case_number), session,
                               delay, full=full))

    curated = kkv_curated(session, delay)
    cases = kkv_cases(session, delay)
    seen = new = 0
    rep = Reporter()
    for item in cases.values():
        new += kkv_save(root, item, curated.get(item["caseNumber"]), session,
                        delay, full=full)
        seen += 1
        rep.update(seen, len(cases), scope="diarium", changed=new)
        if limit and seen >= limit:
            rep.done()
            return seen, new
    rep.done()

    # the curated cases the narrowed diarium set does not carry
    extra = {d: r for d, r in curated.items() if d not in cases}
    for done, (dnr, referat) in enumerate(extra.items(), 1):
        new += kkv_save_curated(root, dnr, referat, session, delay, full=full)
        seen += 1
        rep.update(done, len(extra), scope="ärendelista", changed=new)
        if limit and seen >= limit:
            break
    rep.done()
    return seen, new


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

SYNC = {"jo": jo_sync, "jk": jk_sync, "arn": arn_sync, "imy": imy_sync,
        "kkv": kkv_sync}


def sync(root, scopes=None, full=False, only=None, limit=None, delay=0.5):
    """Download the named organs (default all five). Returns {org: (seen, new)}."""
    return dispatch_scopes(root, scopes, SYNC, ORGS, full=full, only=only,
                           limit=limit, delay=delay)
