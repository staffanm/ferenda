"""Downloader for remiss (public referral) ärenden from regeringen.se/remisser/.

The listing at ``/remisser/`` is an AJAX-paged set of ``ul.list--block > li``
items -- the same DOM the forarbete listing uses (see `LISTING`). Each item links
an ärende page ``/remisser/YYYY/MM/<slug>/``, but that slug is *not* the basefile:
a remiss is about one document, so the ärende is keyed on **the document it sends
out** -- ``"<typ>/<identifier>"``: ``sou/2026:14``, ``ds/2026:9``,
``pm/LI2026/01339``, ``lr/2026/<title-slug>``. That makes the join to the
forarbete vertical the basefile itself rather than a lookup.

An ärende page carries the referral's metadata (title, diarienummer, publish/update
dates, deadline), a single "Remissinstanser" PDF listing who was *asked* to
answer, and -- once answers start arriving -- a "Remissvar" ``<ul>`` with one
``<li><a>`` per organisation that has *actually* answered. Only that list is
modelled as instances (`Remiss.svar`); the Remissinstanser PDF is one opaque
document, kept as a url.

An island headed "Dokument(et) som remitteras" (older pages: "Genväg"/"Genvägar")
links the document sent out for consultation, and it settles both questions the
harvest turns on. *Where the link points is the origin test*: a
``/rattsliga-dokument/`` href means regeringen published the document itself
(SOU, Ds, departementspromemoria, lagrådsremiss), while a bare
``/contentassets/``-``/globalassets/`` PDF or an off-site URL means an agency, an
external party or the EU wrote it. Only the first kind is harvested -- an
externally authored document will never enter this corpus, so answers commenting
on it have nothing to attach to (`Remiss.externt_dokument`). *What the link
names is the identity*: `lib.regeringen.TYPES` maps the path segment to a
doctype, whose identifier regex over the link text recovers the basefile -- with
the two numberless types resolved by the rules forarbete itself keys them on
(`lib.regeringen.pm_identity`/`lr_identity`), so one document gets one basefile
from either page.

One download tree, layout.REMISSER_DOWNLOADED:
``downloaded/remisser/<typ>/<id-slug>.json`` (the Remiss record, source of truth)
beside its ``downloaded/remisser/<typ>/<id-slug>/<org-slug>.pdf`` answer PDFs
(each immutable once posted), plus the examined-ärende index
(`layout.REMISSER_SEEN`) the sweep is driven by.

`sync` polls each ärende page through one `_poll` step from two directions: the
listing walk, newest-first over everything the index says still needs fetching,
and a catch-up pass over index entries the (early-stopping) walk never reached.
What "needs fetching" means is the closing date, not novelty -- answers arrive
throughout the remissperiod, so an ärende is re-polled until its deadline plus
GRACE_PERIOD has passed. `sync_one` fetches exactly one ärende URL, bypassing the
listing walk entirely -- the `--only` escape hatch for grabbing one ärende's
remissvar without touching the rest of the (3000-ärende) archive.
"""

import json
import re
import time
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

from ..lib import compress, layout
from ..lib.harvest import write_record
from ..lib.net import BROWSER_UA, make_session, request
from ..lib.regeringen import (
    BASE,
    TYPES,
    listing_items,
    lr_identity,
    pm_identity,
    regeringen_path,
    slug_number,
)
from ..lib.util import Reporter, swedish_date, write_atomic
from .model import Remiss, Remissinstans, org_slug

# the ``?p=N`` links on /remisser/ are decoration -- the server answers every
# value with page one, so walking them yields the newest 20 ärenden forever. The
# listing is paged by the same AJAX endpoint the forarbete listings use
# (`forarbete.download.FILTER`), keyed on the remiss taxonomy category the
# listing page's own filter div carries as `data-categories`; same JSON envelope
# {"Message": <listing html>, "TotalCount": <int>}.
REMISS_CATEGORY = 2099
LISTING = (BASE + "/Filter/GetFilteredItems?lang=sv&filterType=Taxonomy"
           "&filterByType=FilterablePageBase&rootPageReference=0"
           "&displayLimited=True&preFilteredCategories=%d&page=%%d"
           % REMISS_CATEGORY)
GRACE_PERIOD = timedelta(days=21)   # keep re-polling this long past the deadline
# consecutive already-examined ärenden that end an incremental walk -- one full
# listing page, so a single failed ärende leaves a gap the next walk falls into
# rather than a frontier it stops above
STOP_AFTER = 20

HREFPAT = re.compile(r"^/remisser/\d{4}/\d{2}/")
RATTSLIGA_HREF = re.compile(r"^/rattsliga-dokument/")
SEGMENT = re.compile(r"^/rattsliga-dokument/([^/]+)/")
LANDING_YEAR = re.compile(r"^/rattsliga-dokument/[^/]+/(\d{4})/")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
PDF_SIZE = re.compile(r"\s*\(pdf[^)]*\)\s*$", re.IGNORECASE)   # "… (pdf 119 kB)"

# the deadline sentence is free text with two known phrasings; both name the
# date after "den", so match the cue then read the Swedish date out of the block
DEADLINE_CUE = re.compile(r"[Ss]ista dag att svara|senast den", re.IGNORECASE)

# a remiss opened as a *sub-ärende* of the ärende its document was written under
# carries the parent diarienummer plus a sequence number ("KN2026/01497–1",
# en-dash on the page, hyphen elsewhere). The promemoria is filed under the
# parent, so the join drops the suffix -- the remiss keeps its own dnr verbatim.
SUBARENDE = re.compile(r"^(.+/\d+)[–-]\d+$")

# the heading of the island naming the remitted document: "Dokument(et) som
# remitteras" on current pages, "Genväg"/"Genvägar" on older ones.
ISLAND_HEADING = ("Dokument", "Genväg")

# Pages whose own markup -- or whose own missing identifier -- defeats that match,
# corrected one page at a time. A
# laxer heading pattern would change how all 3000+ ärenden are read to
# accommodate a handful of infomaster slips, so the fix is curated per document
# -- one line each, with the fault and the answer it yields. Keyed on the
# /remisser/ path (scheme, host and trailing slash stripped), value = the
# remitted document's cross-ref, exactly as `_match_forarbete` would have built
# it had the markup been right.
MARKUP_FIXES = {
    # island heading misspelled "Gevägar" (no n). Its link points at
    # /rattsliga-dokument/departementsserien-och-promemorior/2026/02/
    # elektronisk-overvakning--ett-verktyg-for-socialtjansten-till-skydd-for-barn-och-unga
    "/remisser/2026/02/remiss-utkast-till-lagardsremiss--elektronisk-overvakning-"
    "-ett-verktyg-for-socialtjansten-till-skydd-for-barn-och-unga": {
        "typ": "pm", "basefile": "S2026/00342",
        "slug": "elektronisk-overvakning--ett-verktyg-for-socialtjansten-till-"
                "skydd-for-barn-och-unga"},
    # Miljömålsberedningen's delbetänkande: regeringen published the landing page
    # with neither a number in the link text ("Delbetänkande av
    # Miljömålsberedningen - En klimat- och luftvårdsstrategi för Sverige") nor
    # one in the slug (`en-klimat--och-luftvardsstrategi-for-sverige`). It is
    # SOU 2016:47 -- the ärende's own remissammanställning names it, and the
    # corpus already holds that SOU under exactly this title.
    "/remisser/2016/06/remiss-av-delbetankande-fran-miljomalsberedningen-med-"
    "forslag-om-en-klimat--och-luftvardsstrategi-for-sverige": {
        "typ": "sou", "basefile": "2016:47"},
}


# Ärenden whose remitted document regeringen published with no identifier at all
# -- no series number in the link text, none in the landing slug, and no number
# anywhere in the series for that year. `parse_arende` is right to raise on a
# /rattsliga-dokument/ link it cannot key (a stub basefile would file the ärende
# where no join could find it), but for these there is nothing to key it *to*:
# forarbete's own listing walk skips a numberless item for the same reason, so no
# counterpart document will ever enter the corpus. Recording them here treats the
# ärende as external -- examined once and closed, no answers fetched -- instead of
# failing and being retried on every run forever. One line per url with the
# evidence; an entry is wrong the moment the document turns out to have a number
# (use MARKUP_FIXES then, as the Miljömålsberedningen delbetänkande above does),
# and `parse_arende` raises when it sees that. Note it only sees it while the
# ärende is still being polled: once recorded, the examined-index closes it
# forever, so a later staleness surfaces through `--only`, not the sweep.
UNNUMBERED_DOCUMENTS = frozenset({
    # Förordningsmotiv for "Förordning om miljö- och trafiksäkerhetskrav för
    # myndigheters bilar", landing slug `forordning-om-miljo--och-
    # trafiksakerhetskrav-for-myndigheters-bilar`. The fm series carries no 2019
    # number at all (it runs 2000:2–2001:x, then 2020:1 onwards), and no document
    # in the corpus bears this title.
    "/remisser/2019/12/remiss-av-forslag-till-uppdaterad-forordning-om-miljo--"
    "och-trafiksakerhetskrav-for-myndigheters-bilar-och-bilresor",
})


def _markup_fix(url):
    """The curated cross-ref for a page whose markup defeats the parser, or None."""
    return MARKUP_FIXES.get(regeringen_path(url))


# Answer PDFs regeringen.se lists but cannot deliver. The general handler in
# `_fetch_pending` logs a failed fetch and leaves the answer to the next poll,
# which is what an outage deserves -- a 500 is never read as "this answer does
# not exist". An entry here says something stronger and curated: this url is
# permanently dead, so it is not requested at all, and the ärende stops paying a
# failed round-trip for it on every poll of its remissperiod. One line per url
# with the evidence.
BROKEN_ANSWERS = frozenset({
    # Sportfiskarna's answer on the fiskerikontroll remiss: HTTP 500 with a
    # 750-byte error page on every attempt, through `net.request`'s three
    # retries and across separate runs on different days. The ärende page still
    # lists the answer, so regeringen believes it exists; the bytes do not.
    "https://www.regeringen.se/contentassets/"
    "231e2958e5e04d869d6c1888b797c519/sportfiskarna.pdf",
    # The air-quality directive attached to the KN2025/01294 remiss: HTTP 200,
    # `Content-Length: 0`, `Content-Type: application/pdf` on every attempt
    # (three in a row here, and once per sweep before that) -- a broken upload,
    # not an outage. `_fetch_pending` already refuses to record an empty body as
    # downloaded, so without this the ärende re-requests it on every poll of its
    # remissperiod.
    "https://www.regeringen.se/contentassets/"
    "c930185b992c4b538febc2a7b781e85c/europaparlamentets-och-radets-direktiv-"
    "20242881-av-den-23-oktober-2024-om-luftkvalitet-och-renare-luft-i-europa-"
    "omarbetning.pdf",
})


# --------------------------------------------------------------------------
# parsing helpers
# --------------------------------------------------------------------------

def _time_iso(container):
    """The ISO date of the ``<time>`` inside `container`. regeringen.se is
    inconsistent: `datetime` is a clean ISO stamp on some elements ("2026-06-30
    00:00:00") and raw Swedish text on others ("09 april 2026"), so read ISO from
    the attribute when it is one, else parse the Swedish date."""
    if container is None:
        return None
    t = container.find("time")
    if t is None:
        return None
    m = ISO_DATE.match((t.get("datetime") or "").strip())
    return m.group(0) if m else swedish_date(t.get_text(" ", strip=True))


def _section_items(soup, heading):
    """The (href, text) pairs of the anchors under an ``<h2 class="h4">`` whose
    text is `heading` (its following ``<ul>``/``<div>`` sibling)."""
    for h2 in soup.find_all("h2", class_="h4"):
        if h2.get_text(strip=True) == heading:
            container = h2.find_next_sibling(["ul", "div"])
            if container:
                return [(a["href"], a.get_text(" ", strip=True))
                        for a in container.find_all("a", href=True)]
    return []


def _landing_slug(href):
    """The last path segment of a /rattsliga-dokument/ landing href -- what
    forarbete's own listing walk calls the document's `slug`."""
    return href.rstrip("/").rsplit("/", 1)[-1]


def _landing_year(href):
    """The year a /rattsliga-dokument/ landing href is filed under
    (``/rattsliga-dokument/lagradsremiss/2026/06/<slug>/`` -> "2026"). A
    lagrådsremiss is keyed on it, and the remiss page never states the referred
    document's own publication date."""
    hit = LANDING_YEAR.search(href)
    return hit.group(1) if hit else None


def _match_forarbete(href, text, dnr):
    """A remitted-document link -> {"typ", "basefile"} if it names a known
    förarbete type, else None. The href's first path segment *proposes* the type;
    that type's identifier regex, applied to the *link text* (which is free of
    the remiss page's "Remiss av" noise), recovers the canonical basefile.

    Two things the segment alone cannot settle, each handled below where it
    arises: regeringen prints an identifier malformed often enough to matter
    ("SOU 2023 27", no colon), so a numbered series falls back to the number in
    its own landing slug (`lib.regeringen.slug_number`); and it files a document
    under another type's segment now and then, so a numbered type whose rules all
    missed falls back to sweeping every type's regex over the link text. Neither
    fallback fires for a segment this function has no numbered rule for -- those
    still yield None, and `parse_arende` raises.

    The two types regeringen.se publishes *without* a series number are resolved
    by the rules `forarbete` itself keys them on (`lib.regeringen`), so the same
    document gets the same basefile whichever page it was reached from:

      * **departementspromemoria** (`pm`) -- shares its path segment with the Ds
        series but carries no number; these are the modern replacement for a
        numbered Ds. Its basefile is the diarienummer, which the *remiss* page
        supplies (the ärende vignette: regeringen files the promemoria and the
        remiss that sends it out under one ärende) minus any sub-ärende suffix
        (`SUBARENDE`), and failing that the landing page's own slug.
      * **lagrådsremiss** (`lr`) -- keyed ``<year>/<title-slug>``, the year off
        the landing href, the title off the link text. An "Utkast till
        lagrådsremiss: …" draft is a document in its own right and keeps its own
        basefile, exactly as forarbete harvests it.

    The types are tried in `TYPES` order, so a numbered "Ds 2026:9" is claimed by
    `ds` before `pm` sees it.

    A dnr's prefix *case* is left exactly as the page prints it: regeringen.se is
    not self-consistent about it ("JU2026/01595" here against forarbete's
    "Ju2026/01595"), and this is the wrong end to guess from -- the forarbete
    tree's own spelling settles it when the join is made
    (`layout.resolve_basefile`)."""
    m = SEGMENT.match(href)
    if not m:
        return None
    numbered = False        # the segment named a type whose identifier is a regex
    for typ, (segment, _category, idre) in TYPES.items():
        if segment != m.group(1):
            continue
        numbered = bool(idre)
        if idre:
            hit = re.search(idre, text)
            if hit:
                return {"typ": typ, "basefile": hit.group(1)}
            number = slug_number(typ, _landing_slug(href))
            if number:
                return {"typ": typ, "basefile": number}
        elif typ == "pm":
            sub = SUBARENDE.match(dnr or "")
            slug = _landing_slug(href)
            return {"typ": typ,
                    "basefile": pm_identity(sub.group(1) if sub else dnr, slug),
                    "slug": slug}
        elif typ == "lr":
            return {"typ": typ,
                    "basefile": lr_identity(_landing_year(href), text)[0]}
    # A *known* type whose own rules all missed: regeringen files a document under
    # another type's segment now and then -- Ds 2015:51 sits at
    # `/rattsliga-dokument/skrivelse/2015/11/skr.-201551/` -- and the link text
    # still names it correctly, so the segment is the thing to distrust. The same
    # every-type sweep `_title_forarbete` runs, over a cleaner string than the
    # ärende title it reads.
    #
    # Only for a type whose identity *is* a printed identifier. An unknown segment,
    # or a numberless type this function has no rule for (`so`), is not that case
    # and must keep yielding None so `parse_arende` raises: the link text is then
    # some other doctype's title, and an identifier mentioned in passing ("SÖ
    # 1980:72, se Dir. 1979:12") is not the document. That raise is how a new
    # regeringen.se doctype announces itself.
    return _title_forarbete(text, anchored=True) if numbered else None


def _title_forarbete(title, anchored=False):
    """A förarbete cross-ref recovered straight from the ärende title when the page
    carries no "Genvägar" island at all (observed on real pages, e.g. a
    betänkande remiss whose title just names "... (SOU 2026:8)" with no shortcut
    link) -- every type's identifier regex is tried in turn against the title
    text, first match wins.

    `anchored` requires the identifier to *open* the text, for the caller reading a
    link's own label rather than an ärende title. A label names its document
    identifier-first ("Ds 2015:51 Avgiftsfrihet …"); a series mentioned anywhere
    later is a reference, not the identity, and the types are tried in `TYPES`
    order rather than by position, so an unanchored sweep let a passing
    "Tilläggsdirektiv … (SOU 2015:51)" outrank the direktiv it actually names."""
    for typ, (_segment, _category, idre) in TYPES.items():
        if idre:
            hit = (re.match(idre, title) if anchored else re.search(idre, title))
            if hit:
                return {"typ": typ, "basefile": hit.group(1)}
    return None


def _remitterade_lankar(soup):
    """The (href, text) pairs of the island naming the document(s) sent out for
    consultation: headed "Dokument som remitteras"/"Dokumentet som remitteras"
    on current pages, "Genväg"/"Genvägar" on older ones. Empty when the page
    carries no such island at all -- common, and handled by
    `_externt_dokument`."""
    return [(a["href"], a.get_text(" ", strip=True))
            for h2 in soup.find_all("h2", class_="h-underlined")
            if h2.get_text(strip=True).startswith(ISLAND_HEADING)
            for a in h2.parent.find_all("a", href=True)]


def _externt_dokument(links, remitterat):
    """Whether the remitted document was authored outside regeringen -- by an
    agency, an external party or the EU -- rather than published by regeringen
    under one of its own series.

    Two things can testify that regeringen published it: a ``/rattsliga-dokument/``
    link in the island, or -- on the many pages carrying no island at all -- a
    series identifier in the title, which is how `remitterat` gets filled for
    those (`_title_forarbete`). With neither, the document is someone else's,
    attached as a bare ``/contentassets/`` PDF or linked off-site: an agency's
    rapport, framställan or hemställan, a Commission proposal, a letter of
    questions with no document at all. Checked against every island-less page
    among the first 460 ärenden -- all were of exactly those kinds.

    A ``/rattsliga-dokument/`` link therefore means *not* external even when no
    basefile could be derived from it: that combination is a missing identity
    rule, which `parse_arende` raises on rather than passing the ärende over --
    except for the curated `UNNUMBERED_DOCUMENTS`, where the missing identity is
    regeringen's and permanent. `parse_arende` applies that one, not this
    function: the question here is who *wrote* the document, and there the answer
    is still regeringen."""
    if any(RATTSLIGA_HREF.match(href) for href, _ in links):
        return False
    return not remitterat


def _remitterat(links, title, dnr):
    """The förarbete cross-refs from the remitted-document island; when a page
    has none (some ärende pages omit it), fall back to the identifier named in
    the title itself -- the one piece of the referred document's identity every
    remiss page reliably carries."""
    out = []
    for href, text in links:
        ref = _match_forarbete(href, text, dnr)
        if ref and ref not in out:
            out.append(ref)
    if not out:
        ref = _title_forarbete(title)
        if ref:
            out.append(ref)
    return out


def _deadline(soup):
    """The referral deadline (ISO), read from the has-wordExplanation block that
    carries the deadline sentence -- matched by cue so the ingress and any other
    has-wordExplanation block on the page are skipped."""
    for div in soup.find_all(class_="has-wordExplanation"):
        text = div.get_text(" ", strip=True)
        if DEADLINE_CUE.search(text):
            iso = swedish_date(text)
            if iso:
                return iso
    return None


def parse_listing(html):
    """One listing page -> a descriptor per ärende, in page order (newest first):
    {slug, title, url}. The slug is the ärende page's own URL segment -- the only
    identity the listing carries. It is *not* the basefile (an ärende is keyed on
    the document it remits, which only the ärende page names), it is what `sync`
    records as "examined" so a later walk can skip the page."""
    return [{"slug": href.rstrip("/").rsplit("/", 1)[-1], "title": text, "url": url}
            for _li, href, url, text in listing_items(html, HREFPAT)]


def parse_arende(html, url):
    """An ärende detail page -> a Remiss (svar empty until answers exist).

    Raises `ValueError` when the page remits a document regeringen published but
    no basefile can be derived from it -- an unrecognised doctype, or one of the
    numberless types with neither a dnr nor a landing slug to fall back on. That
    is site knowledge this harvester is missing, and minting a stub identity for
    it would file the ärende somewhere no join could ever find it, so it fails
    loudly and the sweep records it as a per-ärende failure to be retried once the
    rule is added (rule:errors-drive-retry-use-raise -- a `raise`, not an
    `assert`, since `-O` would strip the check that keeps the tree honest).

    The curated `UNNUMBERED_DOCUMENTS` answer that same question the other way:
    there the missing identifier is regeringen's own and permanent, so the ärende
    is closed as external instead. An entry that turns out to key after all is
    stale, and raises rather than discarding a real ärende."""
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1", id="h1id")
    if h1 is None:
        raise ValueError("no <h1 id='h1id'> on remiss page %s" % url)
    dnr = None
    vignette = h1.find("span", class_="h1-vignette")
    if vignette:
        dnr = vignette.get_text(strip=True).replace("Diarienummer:", "").strip()
        vignette.extract()
    categories = soup.find("div", class_="categories-text")
    dep = categories.find("a") if categories else None
    dates = soup.find("div", class_="date-publ-updated")
    remissinstanser = _section_items(soup, "Remissinstanser:")
    titel = h1.get_text(" ", strip=True)
    links = _remitterade_lankar(soup)
    fix = _markup_fix(url)
    remitterat = [fix] if fix else _remitterat(links, titel, dnr)
    curated_unnumbered = regeringen_path(url) in UNNUMBERED_DOCUMENTS
    if curated_unnumbered and remitterat:
        # the entry says this document has no identifier; it now has one, so the
        # curated line is stale and silently discarding a real ärende is the last
        # thing it should do (rule:errors-drive-retry-use-raise)
        raise ValueError(
            "remiss %s is in UNNUMBERED_DOCUMENTS but now keys to %s/%s -- drop "
            "the entry (or move it to MARKUP_FIXES)"
            % (url, remitterat[0]["typ"], remitterat[0]["basefile"]))
    externt = _externt_dokument(links, remitterat)
    if not externt and not remitterat:
        # a curated entry answers exactly this question: regeringen published the
        # document with no identifier at all, so there is nothing to key it to
        if not curated_unnumbered:
            raise ValueError(
                "remiss %s remits a regeringen.se document but yields no basefile "
                "(links: %s) -- lib.regeringen needs an identity rule for it"
                % (url, [href for href, _ in links] or "none"))
        externt = True
    return Remiss(
        # the referred document *is* the ärende's identity; an external ärende has
        # none (and is never stored -- see `sync`), so it keys off its url slug
        basefile=("%s/%s" % (remitterat[0]["typ"], remitterat[0]["basefile"])
                  if remitterat else url.rstrip("/").rsplit("/", 1)[-1]),
        titel=titel,
        url=url if url.endswith("/") else url + "/",
        dnr=dnr,
        departement=dep.get_text(strip=True) if dep else None,
        publicerad=_time_iso(dates.find("span", class_="published") if dates else None),
        uppdaterad=_time_iso(dates.find("span", class_="updated") if dates else None),
        sista_svarsdag=_deadline(soup),
        remitterat=remitterat,
        externt_dokument=externt,
        remissinstanser_pdf=(BASE + remissinstanser[0][0]) if remissinstanser else None,
        svar=[Remissinstans(organisation=PDF_SIZE.sub("", text).strip(),
                            source_url=BASE + href)
              for href, text in _section_items(soup, "Remissvar:")])


# --------------------------------------------------------------------------
# harvest
# --------------------------------------------------------------------------

def _write_arende(remiss):
    write_record(layout.remisser_arende(remiss.basefile), remiss.to_dict())


def _load_seen():
    """The examined-ärende index: ``{"dirty": bool, "arenden": {url slug ->
    {basefile, until}}}``. Missing file = nothing examined yet, which reads as
    dirty so a first run walks the whole archive."""
    if not layout.REMISSER_SEEN.exists():
        return {"dirty": True, "arenden": {}}
    index = json.loads(layout.REMISSER_SEEN.read_text())
    # an index written before the cases -> arenden rename reads as empty, which
    # would silently re-walk (and re-fetch) the whole archive -- say so instead
    assert "arenden" in index, (
        "%s has no 'arenden' key. An index written before the cases -> arenden "
        "rename needs that key renamed (the entries are unchanged); deleting the "
        "file also works, at the cost of re-examining every ärende page."
        % layout.REMISSER_SEEN)
    return index


def _save_seen(seen):
    layout.REMISSER_SEEN.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(layout.REMISSER_SEEN,
                 json.dumps(seen, ensure_ascii=False, indent=1, sort_keys=True))


def _until(remiss):
    """The date past which an ärende needs no further polling: its deadline plus
    GRACE_PERIOD, giving the webmaster time to post stragglers. None when the
    page states no deadline -- then the ärende is polled indefinitely, since
    nothing on the page says the answers have stopped arriving."""
    return ((date.fromisoformat(remiss.sista_svarsdag) + GRACE_PERIOD).isoformat()
            if remiss.sista_svarsdag else None)


def _open_on(until, today):
    """Whether `until` (an ISO date, or None for "no deadline stated") still lies
    ahead of `today`. One comparison, read by both the poll decision and the
    analyse decision -- two copies of it would drift."""
    return until is None or today <= date.fromisoformat(until)


def still_open(remiss, today=None):
    """Whether answers may still arrive for `remiss`: its deadline plus
    GRACE_PERIOD has not passed. An ärende whose page states no deadline counts
    as open indefinitely, since nothing on it says the answers have stopped.

    The same closing date `_needs_poll` decides re-polling by, read off the
    stored record rather than the examined index -- `ai_analyze.updatable` needs
    it per *ärende basefile*, while the index is keyed by URL slug. One
    definition either way: a remiss that is still worth fetching answers for is
    exactly one whose analysis is still worth refreshing."""
    return _open_on(_until(remiss), today or date.today())


def _needs_poll(entry, today):
    """Whether an ärende's page must be fetched again, decided from its index entry
    alone -- no request, no record read.

    Answers accumulate on an ärende page for the whole remissperiod, so "already
    examined" is *not* a reason to skip it: only the closing date is. An entry is
    done when its `until` has passed, or immediately when the ärende remits an
    externally authored document (`basefile` None) -- that one is examined once
    and never again. A slug the index has never seen (`None`) always needs a
    fetch."""
    if entry is None:
        return True
    if entry["basefile"] is None:
        return False
    return _open_on(entry["until"], today)


def _merge(remiss, fresh):
    """Fold a re-fetch into the stored ärende: a recorded answer stays recorded
    even if the fresh HTML momentarily omits it; new answers (by org_slug --
    the same identity `_fetch_pending`/`parse.py`/`build.py` key answers on, so
    two answers from the same organisation are both kept, not deduped away)
    are appended; changed scalar fields are updated. Returns whether anything
    changed."""
    changed = False
    known = {org_slug(inst.source_url) for inst in remiss.svar}
    for inst in fresh.svar:
        slug = org_slug(inst.source_url)
        if slug not in known:
            remiss.svar.append(inst)
            known.add(slug)
            changed = True
    for f in ("titel", "dnr", "departement", "publicerad", "uppdaterad",
              "sista_svarsdag", "remissinstanser_pdf"):
        value = getattr(fresh, f)
        if value is not None and value != getattr(remiss, f):
            setattr(remiss, f, value)
            changed = True
    if fresh.remitterat and fresh.remitterat != remiss.remitterat:
        remiss.remitterat = fresh.remitterat
        changed = True
    return changed


def _check_slugs(remiss):
    """Raise ValueError if two answers share an org_slug: writing both under
    ``downloaded/<typ>/<id-slug>/<org>.pdf`` would silently overwrite one organisation's
    answer with another's and mis-join both basefiles to whichever was written
    last. This must be a `raise`, not an `assert` -- an `assert` here would
    vanish under `python -O`, turning a caught, visible failure back into the
    silent data loss it exists to prevent (rule:errors-drive-retry-use-raise)."""
    slugs = [org_slug(inst.source_url) for inst in remiss.svar]
    if len(slugs) != len(set(slugs)):
        raise ValueError(
            "remiss %s: duplicate org slugs %s -- two answer PDFs would silently "
            "overwrite each other" % (remiss.basefile,
                                      sorted({s for s in slugs if slugs.count(s) > 1})))


def _fetch_pending(session, remiss, delay, log=print):
    """Fetch each answer PDF not yet cached (immutable once posted), flipping its
    `downloaded` flag. Returns the number newly fetched. Raises ValueError on an
    org_slug collision (see `_check_slugs`).

    `downloaded` alone does not settle whether an answer is in hand -- the file
    has to actually be there, with bytes in it. regeringen.se serves some
    attachments as ``200`` with ``Content-Length: 0`` (a broken upload on their
    side, one in the first 1781 answers), and recording that as downloaded hands
    the parse stage a zero-byte "PDF", where pdftohtml exits non-zero and fails
    the whole document. An empty body is therefore never written and never
    marked downloaded: the ärende is simply short one answer, retried on every
    later poll until the publisher fixes it or the remissperiod closes
    (rule:no-catch-log-continue). Re-checking the file on disk is what repairs a
    record written before this guard existed."""
    _check_slugs(remiss)
    fetched = 0
    for inst in remiss.svar:
        path = layout.remisser_answer(remiss.basefile, org_slug(inst.source_url))
        if inst.downloaded and compress.exists(path) and compress.stat(path).st_size:
            continue
        if inst.source_url in BROKEN_ANSWERS:
            continue        # curated dead url -- never requested (see the set)
        # One answer that will not download is that answer's problem, not the
        # sweep's: `sync` walks 3 000+ ärenden and this ran inside no handler,
        # so a single 500 on one attachment aborted a backfill at page 36. Same
        # treatment the org_slug collision above already gets -- log it, leave
        # the answer unfetched, and let the next poll of this still-open ärende
        # retry it (rule:no-catch-log-continue: the recovery is defined, and a
        # genuine outage heals itself). Only the request is guarded; a failure
        # writing what came back is an environment fault and still aborts.
        try:
            data = request(session, "GET", inst.source_url).content
        except requests.RequestException as exc:
            log("  fetch %s: %s: %s (retried on the next poll)"
                % (remiss.basefile, inst.source_url.rsplit("/", 1)[-1], exc))
            inst.downloaded = False
            continue
        if not data:
            log("  fetch %s: %s served 0 bytes (retried on the next poll)"
                % (remiss.basefile, inst.source_url.rsplit("/", 1)[-1]))
            inst.downloaded = False
            continue
        compress.write_download(path, data)
        inst.downloaded = True
        fetched += 1
        time.sleep(delay)
    return fetched


def sync_one(url, delay=0.5):
    """Fetch exactly one ärende by its regeringen.se URL, bypassing the listing walk
    entirely -- the `--only` escape hatch, so grabbing one already-known ärende's
    remissvar never requires an incremental (let alone full) sweep of the
    archive. Merges onto any existing record for that ärende (like `sync`'s second
    pass) and fetches every answer PDF not yet cached. Returns
    {"basefile", "svar", "fetched", "externt"}.

    The origin gate holds here too: naming an ärende whose document regeringen did
    not write records the ärende but fetches none of its answers. The escape hatch
    exists to reach an ärende the listing walk has not got to yet, not to override
    what belongs in the corpus."""
    session = make_session(BROWSER_UA)
    url = url if url.endswith("/") else url + "/"
    remiss = parse_arende(request(session, "GET", url).text, url)
    existing = layout.remisser_arende(remiss.basefile)
    if not remiss.externt_dokument and compress.exists(existing):
        stored = Remiss.from_dict(compress.read_json(existing))
        _merge(stored, remiss)
        remiss = stored
    fetched = 0
    if not remiss.externt_dokument:
        fetched = _fetch_pending(session, remiss, delay)
        _write_arende(remiss)
    # keep the examined-index in step, so a later listing walk knows this ärende's
    # closing date without re-fetching the page `--only` just read
    index = _load_seen()
    index["arenden"][url.rstrip("/").rsplit("/", 1)[-1]] = (
        {"basefile": None, "until": None} if remiss.externt_dokument
        else {"basefile": remiss.basefile, "until": _until(remiss)})
    _save_seen(index)
    return {"basefile": remiss.basefile, "svar": len(remiss.svar),
            "fetched": fetched, "externt": remiss.externt_dokument}


def _poll(session, slug, url, examined, summary, delay, log):
    """Examine one ärende page and fold everything it says into the corpus: classify
    its origin, merge onto any stored record, fetch every answer PDF not yet
    cached, and update the ärende's index entry with the date it stops needing this.

    The one place an ärende page is turned into corpus state, shared by the listing
    walk and the catch-up pass so "new ärende" and "re-poll" are the same operation
    -- they differ only in whether a record already exists.

    A bad response or a page `parse_arende` can't read (bot-challenge interstitial,
    truncated response, or a remitted document `lib.regeringen` has no identity
    rule for) leaves the index entry untouched, so the ärende is simply examined
    again next run; one such failure must not abort the sweep over every other
    ärende (rule:no-catch-log-continue)."""
    try:
        fresh = parse_arende(request(session, "GET", url).text, url)
    except (requests.RequestException, ValueError) as exc:
        log("  remiss %s: %s (retried next run)" % (slug, exc))
        summary["failed"] += 1
        return
    if fresh.externt_dokument:
        # examined once and closed forever: the document will never enter this
        # corpus, so no answer of its is worth a byte
        examined[slug] = {"basefile": None, "until": None}
        return
    stored = layout.remisser_arende(fresh.basefile)
    remiss = fresh
    if compress.exists(stored):
        remiss = Remiss.from_dict(compress.read_json(stored))
        _merge(remiss, fresh)
        summary["repolled"] += 1
    else:
        summary["new"] += 1
    # a duplicate org_slug is this ärende's own data anomaly -- it must not abort
    # the sweep over every other ärende (rule:no-catch-log-continue: recorded via
    # the log, retried on the next poll). Pre-checked so the catch is exactly as
    # wide as that one failure; any other error out of the fetch loop still
    # aborts loudly.
    try:
        _check_slugs(remiss)
    except ValueError as exc:
        log("  fetch %s: %s (retried next run)" % (remiss.basefile, exc))
    else:
        summary["fetched"] += _fetch_pending(session, remiss, delay, log)
    _write_arende(remiss)
    examined[slug] = {"basefile": remiss.basefile, "until": _until(remiss)}


def sync(full=False, delay=0.5, log=print):
    """Harvest remiss ärenden into layout.REMISSER_DOWNLOADED (downloaded/remisser):
    each ärende's ``<typ>/<id-slug>.json`` record beside its ``<typ>/<id-slug>/``
    answer-PDF dir.

    What drives the whole sweep is the examined-index (`layout.REMISSER_SEEN`), a
    ärende-url-slug -> ``{basefile, until}`` map. `until` is the date an ärende stops
    needing attention -- its deadline plus GRACE_PERIOD -- because answers keep
    arriving on an ärende page for the whole remissperiod: having examined an ärende
    once is *not* a reason to skip it, only its closing date is. The index also
    holds the ärenden deliberately not stored (an externally authored document, a
    null `basefile`), which are done the moment they are classified. Keying the
    corpus on the remitted document is what makes the index necessary: the
    listing names an ärende by URL slug, and only the ärende page says which document
    it sends out.

    Two passes over the same operation (`_poll`):

      * the **listing walk**, newest-first, polling every ärende the index says
        still needs it. It ends after STOP_AFTER consecutive ärenden that need
        nothing -- not at the first one, so an ärende that failed last run leaves a
        gap this walk falls into rather than hiding behind newer slugs. A run
        with any failure leaves the index `dirty`, and the next run then walks
        the whole archive rather than trusting a run of hits; `full` forces the
        same.
      * the **catch-up pass** over index entries the walk stopped short of: an
        old ärende with a long remissperiod is still open far below the frontier,
        and it is re-polled from the url its record already carries -- no listing
        page needed.

    The index is marked dirty for the run's duration and checkpointed every
    STOP_AFTER ärenden, so a sweep of the archive killed hours in resumes without
    re-fetching one ärende page it already examined -- it only forfeits the
    consecutive-hit stop until a run completes cleanly.

    Returns {"new", "failed", "externt", "repolled", "open", "fetched"}."""
    session = make_session(BROWSER_UA)
    rep = Reporter()
    summary = {"new": 0, "failed": 0, "externt": 0, "repolled": 0, "open": 0,
               "fetched": 0}
    index = _load_seen()
    examined, backfill = index["arenden"], full or index["dirty"]
    today = date.today()
    visited = set()
    # mark the index dirty for the duration: a sweep of the whole archive runs
    # for hours, and an interrupted one must not look like a completed one. The
    # entries written along the way survive either way -- what the dirty flag
    # costs a resumed run is the consecutive-hit stop, not the work already done
    _save_seen({"dirty": True, "arenden": examined})

    seen, total, page_size, page, run, stop = 0, 0, 0, 1, 0, False
    while not stop:
        envelope = request(session, "GET", LISTING % page, parse_json=True)
        items = parse_listing(envelope["Message"])
        total = envelope["TotalCount"]
        page_size = max(page_size, len(items))
        if not items:
            # an empty page is normally the end of the archive -- but a short
            # response mid-walk looks exactly like one, and would silently
            # truncate a --full sweep into a "completed" run (rule:fail-fast)
            if total - seen >= max(page_size, 1):
                raise ValueError(
                    "remisser: listing page %d is empty but TotalCount=%d and "
                    "only %d ärenden were walked -- the listing truncated"
                    % (page, total, seen))
            break
        for item in items:
            seen += 1
            if not _needs_poll(examined.get(item["slug"]), today):
                run += 1
                if not backfill and run >= STOP_AFTER:
                    stop = True
                    break
                continue
            run = 0
            visited.add(item["slug"])
            _poll(session, item["slug"], item["url"], examined, summary, delay, log)
            time.sleep(delay)
        rep.update(seen, None, scope="remisser", page=page, new=summary["new"])
        # checkpoint per listing page: a kill at hour two of a full archive walk
        # keeps every ärende examined so far out of the next run's fetch list
        _save_seen({"dirty": True, "arenden": examined})
        page += 1
        time.sleep(delay)
    rep.done()

    # sorted() snapshots the items, so _poll rewriting entries as it goes is safe
    for done, (slug, entry) in enumerate(sorted(examined.items()), start=1):
        if slug in visited or not _needs_poll(entry, today):
            continue
        record = Remiss.from_dict(json.loads(
            compress.read_text(layout.remisser_arende(entry["basefile"]))))
        _poll(session, slug, record.url, examined, summary, delay, log)
        if done % STOP_AFTER == 0:      # checkpoint at the listing-page cadence
            _save_seen({"dirty": True, "arenden": examined})
        time.sleep(delay)

    summary["externt"] = sum(1 for e in examined.values() if e["basefile"] is None)
    summary["open"] = sum(1 for e in examined.values() if _needs_poll(e, today))
    # a run with failures stays dirty: the next one re-walks the whole archive
    # instead of stopping on a run of hits above the ärende it never got
    _save_seen({"dirty": summary["failed"] != 0, "arenden": examined})
    return summary


def list_basefiles():
    """Every case basefile ("<typ>/<document id>") on disk, sorted -- not answer
    basefiles. Records live one directory deep (``<typ>/<id-slug>.json``), which
    also keeps the examined-index out of the glob."""
    return sorted(compress.read_json(p)["basefile"]
                  for p in compress.glob(layout.REMISSER_DOWNLOADED, "*/*.json"))
