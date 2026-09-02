"""Downloader for Swedish preparatory works (förarbeten) from regeringen.se.

regeringen.se publishes nine document types under /rattsliga-dokument/. The
visible `?p=N` links are decoration -- the listing is paged by an AJAX endpoint
the page's JS calls, returning a JSON envelope `{"Message": <html>, "TotalCount":
N}` whose Message is the `<ul class="list--block">` of items:

    GET /Filter/GetFilteredItems?lang=sv&filterType=Taxonomy
        &filterByType=FilterablePageBase&rootPageReference=0&displayLimited=True
        &preFilteredCategories=<category-id>&page=<N>

The per-type category id (Proposition=1329, …) is the taxonomy id behind the
`/tx/<id>` links. Types and ids:

    prop  proposition                               1329   Prop. 2025/26:279
    sou   statens-offentliga-utredningar            1331   SOU 2026:34
    ds    departementsserien-och-promemorior        1325   Ds 2026:12
    pm    departementsserien-och-promemorior        1325   Ju2026/01691 (dnr) / title
    dir   kommittedirektiv                          1327   Dir. 2026:45
    fm    forordningsmotiv                           1326   Fm 2025:1
    skr   skrivelse                                 1330   Skr. 2025/26:280
    so    sveriges-internationella-overenskommelser 1332   (titled, no number)
    lr    lagradsremiss                             2085   (titled, no number)

Every listing item carries the document's own identifier and a landing-page
link (`<ul class="list--block"> <li> <div class="sortcompact"> <a>`); the
landing page links the content PDF under `/contentassets/` (or `/globalassets/`).

The **basefile is the document's own identifier** (prop "2025/26:279", sou
"2020:1", …) -- never a regeringen.se URL slug, which is unreliable (the
infomaster reuses and mis-numbers them) -- so the same act from another source
(riksdagen, KB) for older periods reconciles by identity. The two types that
carry no number in the listing are handled explicitly: a **SÖ** keys on the
``SÖ YYYY:NN`` from its landing-page vignette (`resolve_identity`), and an item
under the SÖ index without one is rejected; a **lagrådsremiss** keys on
``<year>/<title-slug>`` (`lr_identity`), since it has only a title. A URL on the
curated ``misleading_urls`` skip-list (dual-published or mislabelled pages) is
never harvested.

Downloaded via `lagen forarbete download [prop|sou|ds|...]`; no doctype = all.
A single document: `lagen forarbete download <doctype> --only <basefile>`.

Stored under `site/data/downloaded/forarbete/<type>/`: one `<slug>.json` record (identifier,
title, date, landing url, downloaded files) + the landing `<slug>.html` + the
content file(s). Incremental by default (newest-first, stop at the first
already-downloaded doc); `--full` re-walks the whole listing, skipping existing.
"""

import hashlib
import itertools
import re
import time
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from ..lib import compress, layout, net
from ..lib.harvest import HarvestWatermark, ItemKey, walk, write_record
from ..lib.net import BROWSER_UA as USER_AGENT
from ..lib.net import make_session
from ..lib.regeringen import (
    BASE,
    TYPES,
    is_misleading,
    landing_vignette,
    listing_items,
    lr_identity,
    pm_identity,
)
from ..lib.util import (
    Reporter,
    basefile_slug,
    document_extension,
    harvest_start,
)

# BASE and the doctype table (TYPES: url segment, taxonomy category id,
# identifier regex) live in lib.regeringen -- shared with the remisser vertical.
FILTER = (BASE + "/Filter/GetFilteredItems?lang=sv&filterType=Taxonomy"
          "&filterByType=FilterablePageBase&rootPageReference=0"
          "&displayLimited=True&preFilteredCategories=%s&page=%d")

# Two types share category 1325 ("Departementsserien och promemorior"): `ds`
# takes the items numbered `Ds YYYY:N`, `pm` takes the rest (the promemorior
# outside the Ds series). EXCLUDE maps such a sharing type to the sibling whose
# identifier pattern marks the listing items that are *not* its own. (The
# split is this harvester's parsing rule, not site knowledge -- it stays here
# rather than in lib.regeringen.)
EXCLUDE = {"pm": "ds"}

# A promemoria without a Ds number is keyed by its diarienummer -- department
# letters + year + slash + running number (Ju2026/01691, KN2026/01475,
# S2026/01304). Items with neither a Ds number nor a dnr fall back to the slug.
DNR_RE = re.compile(r"\b([A-ZÅÄÖ][a-zA-Zåäö]{0,3}\d{4}/\d{2,6})\b")


# regeringen.se hangs the document download(s) under /contentassets/ or
# /globalassets/. We match the link by *location*, not by suffix: the redesigned
# site serves /contentassets/<hash>/<slug> with no extension at all (the type is
# only in the link text, "… (pdf 2 MB)"), so a suffix filter misses those and the
# document is read from the served bytes instead -- see document_extension.
CONTENT_HREF = re.compile(r"/(?:contentassets|globalassets)/", re.IGNORECASE)

# SÖ (Sveriges internationella överenskommelser) numbering. `SO_OWN` is
# end-anchored: a SÖ title often *cites* other överenskommelser mid-text (e.g.
# "... (SÖ 1974:41) ..., SÖ 1980:72"), and only the trailing one is the
# document's own -- so the best-effort listing-text read takes the last. The
# landing-page vignette (SO_VIGNETTE, full-match) is the authority; see
# resolve_identity.
SO_OWN = re.compile(r"SÖ\s*(\d{4}:\d+)\s*$")
# the vignette is scoped to the SÖ index, so any YYYY:NN in it IS the SÖ number
# -- regeringen.se prints it bare ("1993:80"), prefixed ("Diarienummer: SÖ …"),
# or suffixed ("… m.fl."); all yield the document's own number.
SO_VIGNETTE = re.compile(r"(\d{4}:\d+)")
def resolve_identity(typ, item, landing_html):
    """The authoritative (basefile, identifier) for a document, resolved once its
    landing page is in hand. Only `so` needs the landing (its number lives in the
    page vignette, not reliably in the listing text); every other type was
    settled from the listing. Returns None to REJECT the document -- a listing
    item under the SÖ index whose vignette (and title) carry no real
    ``SÖ YYYY:NN`` (the index also holds pressmeddelanden and the like).

    The vignette is *searched*, not full-matched: regeringen.se prints the number
    in several shapes -- ``SÖ 1980:72``, ``Diarienummer: SÖ 1921:36`` (older
    överenskommelser), ``SÖ 1968:15 m.fl.`` (a multi-treaty publication) -- all of
    which yield the document's own number. When the page has no vignette at all,
    the title's trailing own-number is the fallback."""
    if typ != "so":
        return item["basefile"], item["identifier"]
    vignette = landing_vignette(landing_html) or ""
    match = SO_VIGNETTE.search(vignette) or SO_OWN.search(item.get("title") or "")
    if not match:
        return None
    return match.group(1), "SÖ " + match.group(1)


def fetch(session, url, timeout=60):
    """GET through `lib.net.request` -- exponential backoff, Retry-After, the
    throttle statuses and the harvest deadline -- with one extra retry on
    regeringen.se's habit of 400-ing the first hit.

    That 400 is retried here rather than by adding it to `net.RETRY_STATUS`: a
    400 is a genuine, immediately-fatal client error for every other source in
    the pipeline, and widening the shared set would make all of them burn six
    attempts on a malformed request. The quirk is this one host's, so the
    handling stays with it."""
    try:
        return net.request(session, "GET", url, timeout=timeout)
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code != 400:
            raise
        time.sleep(2)
        return net.request(session, "GET", url, timeout=timeout)


# --------------------------------------------------------------------------
# listing -> document descriptors
# --------------------------------------------------------------------------

def parse_listing(html, typ):
    """One listing page -> (descriptors, raw_count): a descriptor per document
    of type `typ`, in page order (newest first) -- {type, basefile, identifier,
    title, date, url, slug} -- plus the RAW number of listing items on the page
    *before* type filtering. The raw count is what tells "listing exhausted"
    apart from "page full of the sibling type's documents" (see iter_listing)."""
    segment, _, idre = TYPES[typ]
    idpat = re.compile(idre) if idre else None
    # a type sharing a category with a sibling (pm/ds) takes the complementary
    # slice: items carrying the sibling's identifier belong to the sibling.
    sibling = EXCLUDE.get(typ)
    excludepat = None
    if sibling:
        sibre = TYPES[sibling][2]
        assert sibre, "EXCLUDE sibling %s must be identifier-numbered" % sibling
        excludepat = re.compile(sibre)
    hrefpat = re.compile(r"/rattsliga-dokument/%s/\d{4}/\d{2}/" % segment)
    out = []
    raw = 0
    for li, href, url, text in listing_items(html, hrefpat):
        raw += 1
        if is_misleading(url):
            continue  # curated skip: dual-published / mislabelled / wrong-number
        slug = href.rstrip("/").rsplit("/", 1)[-1]
        time_el = li.find("time")
        date = time_el.get("datetime") if time_el else None
        if excludepat and excludepat.search(text):
            continue  # carries the sibling type's number -> not ours
        if idpat:
            m = idpat.search(text)
            if not m:
                continue  # title without this type's identifier -> not a doc
            basefile, identifier = m.group(1), m.group(0)
            title = text[:m.start()].rstrip(", ").strip() or text
        elif sibling:
            # pm: a diarienummer keys the record; a promemoria with only a
            # title falls back to the landing-page slug (identifier = title).
            # The rule itself lives in lib.regeringen so remisser resolves the
            # same promemoria to the same basefile from its own page.
            m = DNR_RE.search(text)
            basefile = pm_identity(m.group(1) if m else None, slug)
            if m:
                identifier = m.group(1)
                title = text[:m.start()].rstrip(", ").strip() or text
            else:
                identifier = title = text
        elif typ == "lr":
            # lagrådsremiss: no number, but the title is in the listing text, so
            # the <year>/<title-slug> basefile is settled here.
            title = text
            basefile, identifier = lr_identity(date, text)
        elif typ == "so":
            # SÖ: the number is the landing-page vignette, not reliably in the
            # listing text -- so best-effort here (the trailing own-number, for
            # the incremental skip), authoritative later in resolve_identity.
            m = SO_OWN.search(text)
            basefile = m.group(1) if m else None
            identifier = ("SÖ " + basefile) if basefile else None
            title = SO_OWN.sub("", text).rstrip(", ").strip() or text
        else:
            raise ValueError("couldn't extract basefile: type %r has no "
                             "identifier rule for %r" % (typ, text))
        out.append({"type": typ, "basefile": basefile, "identifier": identifier,
                    "title": title, "date": date, "url": url, "slug": slug})
    return out, raw


def listing_page(session, typ, page):
    """One listing page via the AJAX filter endpoint: returns (items,
    raw_count, total_count). The endpoint wraps the `ul.list--block` HTML in a
    JSON envelope {"Message": <html>, "TotalCount": N}."""
    category = TYPES[typ][1]
    envelope = fetch(session, FILTER % (category, page)).json()
    items, raw = parse_listing(envelope.get("Message", ""), typ)
    return items, raw, envelope.get("TotalCount")


def iter_listing(session, typ, delay, log=print):
    """Yield (descriptors, total_count, page_number) per listing page until the
    listing is exhausted.

    Exhaustion keys on the RAW per-page item count, never the type-filtered
    descriptor count: two types share category 1325 (pm/ds), so a page whose
    items all belong to the sibling type filters to zero descriptors while the
    listing continues below it -- reading that as "exhausted" would permanently
    skip everything deeper, --full included. (Same for any page whose items all
    lack the type's identifier in the link text.) A raw-empty page normally IS
    the end; but when the envelope's TotalCount says a *page or more* is still
    missing, the listing is truncated or broken, and that is an error, not
    clean exhaustion (rule:fail-fast) -- the raise lands inside sync's walk,
    after begin(), so the watermark store stays dirty and the next run re-walks.

    A shortfall smaller than one page is regeringen.se's own bookkeeping, not a
    broken walk: prop's listing serves 4 349 items under a TotalCount of 4 352
    (fm's, small enough to check exactly, matches to the item). Counting items
    the CMS then declines to serve is upstream's business -- we log the
    discrepancy and take what the listing gave us, because refusing to harvest
    over it would strand every type behind prop in the same run."""
    page = 1
    raw_seen = 0
    page_size = 0
    while True:
        items, raw, total = listing_page(session, typ, page)
        if raw == 0:
            if total and total - raw_seen >= max(page_size, 1):
                raise ValueError(
                    "%s: listing page %d is empty but TotalCount=%d and only "
                    "%d items seen -- truncated or broken listing" %
                    (typ, page, total, raw_seen))
            if total and raw_seen < total:
                log("  %s: listing served %d of the %d items it counts (%d "
                    "counted but not served -- upstream bookkeeping)"
                    % (typ, raw_seen, total, total - raw_seen))
            return
        raw_seen += raw
        page_size = max(page_size, raw)
        yield items, total, page
        page += 1
        time.sleep(delay)


# --------------------------------------------------------------------------
# fetch + store one document
# --------------------------------------------------------------------------

def find_content_links(html):
    """Distinct content-file hrefs (the document PDFs/Word files), in page
    order. regeringen.se hangs them under /contentassets/ or /globalassets/;
    the served bytes (not the href) decide whether each is a document we keep."""
    soup = BeautifulSoup(html, "html.parser")
    seen, out = set(), []
    for a in soup.find_all("a", href=CONTENT_HREF):
        href = a["href"]
        if href not in seen:
            seen.add(href)
            out.append(href)
    return out


DOC_SUFFIXES = (".pdf", ".doc", ".docx", ".rtf", ".wpd")   # document_extension's


def _stored_documents(docdir):
    """``{sha256 of the bytes: stored name}`` for the documents already in
    `docdir` -- what a re-download is matched against. Only document files are
    read; the landing HTML beside them is not one."""
    out = {}
    if not docdir.is_dir():
        return out
    for name in sorted({compress.logical(entry).name for entry in docdir.iterdir()}):
        if name.lower().endswith(DOC_SUFFIXES):
            out.setdefault(
                hashlib.sha256(compress.read_bytes(docdir / name)).hexdigest(), name)
    return out


def _free_name(slug, ext, taken):
    """The next positional document name -- ``<slug>.pdf``, ``<slug>-1.pdf``, …
    -- skipping any already claimed by an unchanged file that kept its name."""
    for i in itertools.count():
        name = "%s%s%s" % (slug, "-%d" % i if i else "", ext)
        if name not in taken:
            return name


def store_documents(session, docdir, slug, hrefs, delay):
    """Fetch every document linked from a landing page into `docdir`, returning
    the stored names in link order. A link whose bytes are not a document we
    recognize (an image, an error page) is skipped -- see `document_extension`.

    Content-addressed against what is already stored: a payload whose bytes are
    on disk keeps the name it already has. The names are positional, so without
    this a landing page that merely reordered its links would renumber every
    file -- and a renamed file is a new file, which costs the document its
    poppler conversion cache and forces a re-parse of text that did not change
    (`lib/compress.write_download` declines the write itself when the bytes
    match, but only if the name still matches too)."""
    by_digest = _stored_documents(docdir)
    names, taken = [], set()
    for href in hrefs:
        data = fetch(session, (BASE + href) if href.startswith("/") else href).content
        time.sleep(delay)
        ext = document_extension(data)
        if ext is None:
            continue
        name = by_digest.get(hashlib.sha256(data).hexdigest())
        if name is None or name in taken:
            name = _free_name(slug, ext, taken)
        compress.write_download(docdir / name, data)
        names.append(name)
        taken.add(name)
    return names


def download_document(session, root, item, delay, log=print):
    """Fetch the landing page + its content file(s); store the record JSON,
    the landing HTML, and each file. Returns the stored record, or None when the
    document is rejected on inspection of its landing page (a non-SÖ item under
    the SÖ index) -- nothing is written in that case."""
    landing = fetch(session, item["url"])
    typ = item["type"]
    identity = resolve_identity(typ, item, landing.text)
    if identity is None:
        return None
    basefile, identifier = identity
    slug = basefile_slug(basefile)
    files = store_documents(session, layout.fa_dir(root, typ, basefile), slug,
                            find_content_links(landing.text), delay)
    compress.write_download(layout.fa_dir(root, typ, basefile) / (slug + ".html"),
                            landing.text)
    stored = layout.fa_record_file(root, typ, basefile)
    if not files and compress.exists(stored):
        previous = compress.read_json(stored)
        if previous.get("files"):
            # This landing page links no document, but the document already has
            # a body -- from KB, riksdagen or the Trips import, whose provenance
            # lives in the stored record's `url`/`orig_url`/`body_format`.
            # Writing this record would replace `files` (it is assigned, never
            # merged) with [] and overwrite that provenance, orphaning bytes
            # that stay on disk and demoting the document to metadata-only. It
            # already happened to sou/1995:60 and sou/1999:78. A re-download
            # must be monotonic: never trade a body for no body
            # (rule:fail-fast -- refuse and say so rather than quietly lose it).
            log("  %s/%s: landing links no document but %d file(s) are stored "
                "(%s) -- keeping the stored record"
                % (typ, basefile, len(previous["files"]),
                   previous.get("orig_url") or previous.get("url") or "no url"))
            return previous
    record = {"type": typ, "basefile": basefile, "identifier": identifier,
              "title": item["title"], "date": item["date"], "url": item["url"],
              "files": files}
    write_record(stored, record)
    return record


def has_regeringen_url(record):
    """Whether a stored record's `url` is a regeringen.se landing page -- the
    precondition for `refetch_landings` to fetch it at all. Only a candidate
    filter: whether the landing is actually *missing* on disk is checked by
    `refetch_landings` itself (so a dry-run count is an upper bound, not the
    number an interrupted run still has left).

    The legacy `dsregeringen` import kept the record and the body files but not
    the page they came from -- 1 260 records -- so `volumes.body_pdfs` has no
    link text for them and has to keep every file it cannot positively rule
    out."""
    return bool(record.get("url")) and "regeringen.se" in record["url"]


def word_bodied(record):
    """Whether a record's stored body is a Word file. regeringen.se and
    data.riksdagen.se both served `.doc` for propositions into the late 2000s;
    the same documents are PDFs on regeringen.se today, and the PDF carries the
    font signal the parser needs to recover chapter headings at all (the Word
    route yields none, so prop. 2006/07:128 parsed with no
    författningskommentar)."""
    return any(f.lower().endswith((".doc", ".docx"))
               for f in record.get("files", []))


def refetch_landings(root, select, replace_bodies, types=("prop", "ds", "sou"),
                     limit=None, delay=0.5, force=False, log=print):
    """Re-fetch the regeringen.se landing page of every record `select` picks,
    storing it beside the record so the volume rule can read its link texts.

    Incremental: a record whose landing is already stored is passed over, so an
    interrupted run resumes where it stopped. `force` re-reads them.

    With `replace_bodies` the linked documents are downloaded too and become
    the record's `files` -- what turns a Word-bodied record into the PDFs
    regeringen.se serves now. Without it only the landing page is stored: the
    legacy records already hold their bodies, and re-downloading identical
    bytes would only move their mtimes and throw away their conversion cache.

    Returns (checked, updated, errors)."""
    root = Path(root)
    session = make_session(USER_AGENT)
    checked = updated = errors = 0
    rep = Reporter()
    for typ in types:
        recs = sorted(compress.glob(root / typ, "*/*.json"))
        for i, recpath in enumerate(recs):
            record = compress.read_json(recpath)
            if not select(record):
                continue
            if limit and checked >= limit:
                rep.done()
                return checked, updated, errors
            basefile = record["basefile"]
            slug = basefile_slug(basefile)
            docdir = layout.fa_dir(root, typ, basefile)
            landing_path = docdir / (slug + ".html")
            have_landing = compress.exists(landing_path)
            if have_landing and not replace_bodies and not force:
                continue          # the landing-only job already has this one
            checked += 1
            try:
                if have_landing and not force:
                    # already on disk, and current: the Word-bodied records'
                    # stored landings link the PDFs (link text "(pdf 348 kB)",
                    # and the asset serves %PDF) -- the record simply kept a
                    # .doc from the legacy import. So no refetch is needed to
                    # find them.
                    html = compress.read_text(landing_path)
                else:
                    landing = fetch(session, record["url"])
                    time.sleep(delay)
                    compress.write_download(landing_path, landing.text)
                    html = landing.text
                hrefs = find_content_links(html)
                if not replace_bodies:
                    updated += 1
                elif hrefs:
                    stored = store_documents(session, docdir, slug, hrefs, delay)
                    # replace the Word body only when a PDF actually came
                    # back: if the landing yields nothing better, the .doc we
                    # have is still the best body for this document
                    if any(f.lower().endswith(".pdf") for f in stored):
                        updated += 1
                        record["files"] = stored
                        record.pop("body_format", None)
                        write_record(layout.fa_record_file(root, typ, basefile),
                                     record)
            except requests.HTTPError as exc:
                errors += 1
                log("  %s %s: %s" % (typ, record.get("url"), exc))
            rep.update(i + 1, len(recs), scope="landings " + typ,
                       checked=checked, updated=updated, errors=errors)
        rep.done()
    return checked, updated, errors


def refetch_bodies(root, types=("lr", "so"), limit=None, delay=0.5, log=print):
    """Second-chance body fetch for body-less live-harvest records. The harvest
    stored the landing page and record but ended with no document file --
    the content asset served a transient non-document at the time -- so this
    re-reads each stored landing's content links and fetches them again,
    live-refetching the landing where the stored copy carries no links.
    Stored bodies update the record's `files` (re-staling its parse); a
    document whose links still yield nothing is left as it was, and re-tried
    by the next run. Returns (checked, recovered, errors)."""
    root = Path(root)
    session = make_session(USER_AGENT)
    checked = recovered = errors = 0
    rep = Reporter()
    for typ in types:
        recs = sorted(compress.glob(root / typ, "*/*.json"))
        for i, recpath in enumerate(recs):
            record = compress.read_json(recpath)
            if ("legacy_files" in record or "source" in record
                    or record.get("files")):
                continue
            if limit and checked >= limit:
                return checked, recovered, errors
            checked += 1
            basefile = record["basefile"]
            slug = basefile_slug(basefile)
            docdir = layout.fa_dir(root, typ, basefile)
            landing_path = docdir / (slug + ".html")
            try:
                hrefs = (find_content_links(compress.read_text(landing_path))
                         if compress.exists(landing_path) else [])
                if not hrefs:
                    landing = fetch(session, record["url"])
                    compress.write_download(landing_path, landing.text)
                    hrefs = find_content_links(landing.text)
                    time.sleep(delay)
                stored = store_documents(session, docdir, slug, hrefs, delay)
                if stored:
                    recovered += 1
                    record["files"] = stored
                    write_record(layout.fa_record_file(root, typ, basefile), record)
            except requests.HTTPError as exc:
                errors += 1
                log("  %s %s: %s" % (typ, record["url"], exc))
            rep.update(i + 1, len(recs), scope="refetch " + typ,
                       recovered=recovered, errors=errors)
        rep.done()
    return checked, recovered, errors


# --------------------------------------------------------------------------
# download loop
# --------------------------------------------------------------------------

def has_live_record(root, typ, basefile):
    """Whether a *live-harvest* record already exists for this document. A frozen
    import record (§7g -- it carries a `source` key) is treated as absent, for two
    reasons: live always wins, so the downloader must fetch its better copy and
    overwrite the import; and a legacy record must not trip the newest-first
    incremental stop (`done = True`) as if the corpus were already caught up.

    A **Word-bodied** record is treated as absent for the first of those
    reasons. 260 propositions were imported from data.riksdagen.se with a
    `.doc` body and no `source` key, so they read as live harvests and a
    `--full` walk passed straight over them -- while regeringen.se lists the
    same documents as PDFs. The distinction is not cosmetic: the PDF carries
    the font signal the parser needs to recover chapter headings, and the Word
    route yields none, which is why prop. 2006/07:128 parsed with no
    författningskommentar at all until its PDFs were fetched.

    Note this is only "a live record exists", shared with the riksdagen
    harvesters, where a body-less record is a *modelled* state (a planned
    betänkande has no body yet and must stay provisional). Whether the document
    still needs fetching from regeringen.se is `needs_harvest`."""
    recpath = layout.fa_record_file(root, typ, basefile)
    if not compress.exists(recpath):
        return False
    record = compress.read_json(recpath)
    return "source" not in record and not word_bodied(record)


def needs_harvest(root, typ, basefile):
    """Whether this document still needs fetching from regeringen.se: no live
    record, or one with no body.

    A regeringen landing page links its document, so a stored record with
    ``files: []`` is a download that was *missed*, not a document without a
    body -- 14 038 of 97 213 records carry one, and sampling their stored
    landings shows the great majority do link a document (sou 30/30, dir 30/30,
    fm 30/30, skr 29/30, ds 27/30). Reading them as harvested is what put them
    out of reach: the incremental walk stopped above them and `--full` skipped
    them, so no invocation of this downloader could repair one.

    The handful that genuinely have no body (so/lr/pm, ~31) cost one landing
    fetch per `--full` run and are then rewritten identically --
    `download_document` refuses to replace a stored body with none, so a
    re-fetch can only ever add. This lives here rather than in
    `has_live_record` because it is knowledge about *regeringen.se* pages; the
    riksdagen walk has its own currency rule (`riksdagen._currency`)."""
    if not has_live_record(root, typ, basefile):
        return True
    record = compress.read_json(
        layout.fa_record_file(root, typ, basefile))
    return not record.get("files")


class ListingReporter(Reporter):
    """The forarbete progress line. `harvest.walk` counts the items it has
    walked, while the listing's own envelope states how many there are and
    which page they came from -- so the line takes its total and page from the
    envelope the walk is currently reading (`_listing_items` keeps them
    current), and the ETA survives the walk not knowing the length up front."""

    def __init__(self):
        super().__init__()
        self.total = None
        self.page = None

    def update(self, seen, total, **counts):
        # `total` is what the walk knows (nothing: it is handed a lazy stream);
        # the envelope's count and page number are what the line shows
        super().update(seen, self.total, page=self.page, **counts)


def _listing_items(pages, rep):
    """One listing's pages flattened to the item stream `harvest.walk` walks,
    reporting each page's envelope to `rep` as it goes. The stream is lazy, so
    a listing that dies mid-walk raises *inside* the walk, where it becomes a
    recorded Skip for this doctype instead of ending the run for the eight
    others (`harvest.guarded_enumerate`)."""
    for items, total, page in pages:
        rep.total, rep.page = total, page
        yield from items


def sync(root, types=None, full=False, limit=None, delay=0.5, log=print,
         only=None):
    """Download the named types (default all).

    A type is *backfilled* -- the whole listing walked, downloading whatever is
    missing -- when `--full` is given or the type has never been cleanly walked
    (no watermark date yet: a first run, or one crashed partway). The walk
    drives the shared begin/complete watermark lifecycle (lib.harvest): the
    watermark date advances even when some documents failed to download (one
    persistently-broken document must not force ever-deeper re-walks -- the
    date-conclusive stop bounds the depth), but errors leave the store *dirty*,
    so the next run disables the consecutive-hit stop, walks down to the
    date-conclusive boundary, and naturally retries the failures. A crashed or
    `--limit`-truncated run likewise stays dirty and is re-walked. Once caught
    up, later runs go *incremental*: newest-first, stopping at the first
    document already on disk that falls past the watermark date boundary or
    when the look-ahead limit is reached.
    `only` (a basefile) downloads just that one document, walking the listing until
    it is found (ignoring the on-disk stop and the watermark); an `only` no
    listing walked carries is a typo or a document that has gone, and the run
    says so rather than report itself clean. Returns {type: (seen, new)}."""
    session = make_session(USER_AGENT)
    totals = {}
    rep = ListingReporter()
    walked = list(types or TYPES)
    for typ in walked:
        harvest_start("forarbete %s" % typ,
                      "%s/rattsliga-dokument/%s/" % (BASE, TYPES[typ][0]))
        marker = Path(root) / typ / ".complete"
        watermark_path = Path(root) / typ / ".watermark.json"

        # Migrate legacy complete marker to watermark
        if marker.exists() and not watermark_path.exists():
            initial_watermark = HarvestWatermark(watermark_path)
            initial_watermark.save(date.today().isoformat())

        # per-source window (project convention): regeringen.se listings are
        # strictly newest-first by publication date but occasionally resurface
        # an edited item near the top; 20 consecutive hits / 14 days of slack
        # absorb those bumps without deep re-walks.
        watermark = HarvestWatermark(watermark_path, lookahead_limit=20, safety_days=14)

        def item_key(item, typ=typ):
            # `so` items whose SÖ number isn't in the listing text carry
            # basefile None (the landing settles it); they can't match an
            # on-disk record, so they're never skipped -- the landing check in
            # download_document dedups/rejects them instead.
            return ItemKey(item["basefile"],
                           item["basefile"] is not None
                           and not needs_harvest(root, typ, item["basefile"]),
                           date=item.get("date"))

        # `--full` here means "walk the whole listing again", never "fetch it
        # all again": a regeringen document is fixed once published, and
        # re-resolving would refetch every landing page and file of 30 000
        # documents to store bytes already on disk (harvest.walk's `deep`).
        result = walk(_listing_items(iter_listing(session, typ, delay, log=log),
                                     rep),
                      resolve=lambda item: download_document(session, root, item,
                                                             delay, log),
                      item_key=item_key, watermark=watermark, deep=full,
                      only=only, limit=limit, scope=typ, log=log, reporter=rep)

        if result.errors:
            log("  %s: %d download error(s) -- the store stays dirty, so the "
                "next run re-walks down to the watermark boundary and retries "
                "them (--only <basefile> forces one now)" % (typ, result.errors))
        # summary right after this type's own start line + progress, so each
        # subtype reads as one self-contained block (not all summaries at the end)
        log("forarbete %s: %d seen, %d new" % (typ, result.seen, result.new))
        totals[typ] = (result.seen, result.new)
    if only is not None and not any(new for _, new in totals.values()):
        # user-typed --only: load-bearing validation raises, never asserts --
        # without this the run walks every listing to the bottom and reports a
        # clean "0 new", which reads as "nothing to do", not as a typo
        raise ValueError("--only %s names no document in the forarbete "
                         "listings walked (%s)" % (only, ", ".join(walked)))
    return totals
