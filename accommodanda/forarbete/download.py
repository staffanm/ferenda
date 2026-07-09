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
"2020:1", …) -- never a regeringen.se slug -- so the same act from another
source (riksdagen, KB) for older periods reconciles by identity, exactly as the
user requires. The two types regeringen.se publishes without a number (SÖ,
lagrådsremiss) fall back to the landing-page slug as basefile.

Downloaded via `lagen forarbete download [prop|sou|ds|...]`; no doctype = all.
A single document: `lagen forarbete download <doctype> --only <basefile>`.

Stored under `site/data/downloaded/forarbete/<type>/`: one `<slug>.json` record (identifier,
title, date, landing url, downloaded files) + the landing `<slug>.html` + the
content file(s). Incremental by default (newest-first, stop at the first
already-downloaded doc); `--full` re-walks the whole listing, skipping existing.
"""

import json
import re
import time
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from ..lib import compress
from ..lib.harvest import HarvestWatermark
from ..lib.net import BROWSER_UA as USER_AGENT
from ..lib.net import make_session
from ..lib.regeringen import BASE, TYPES, listing_items
from ..lib.util import (
    Reporter,
    basefile_slug,
    document_extension,
    record_path,
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


def fetch(session, url, timeout=60):
    """GET with one retry on regeringen.se's habit of 400-ing the first hit."""
    response = session.get(url, timeout=timeout)
    if response.status_code == 400:
        time.sleep(2)
        response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response


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
            m = DNR_RE.search(text)
            if m:
                basefile = identifier = m.group(1)
                title = text[:m.start()].rstrip(", ").strip() or text
            else:
                basefile, identifier, title = slug, text, text
        else:
            basefile = identifier = slug
            title = text
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


def iter_listing(session, typ, delay):
    """Yield (descriptors, total_count, page_number) per listing page until the
    listing is exhausted.

    Exhaustion keys on the RAW per-page item count, never the type-filtered
    descriptor count: two types share category 1325 (pm/ds), so a page whose
    items all belong to the sibling type filters to zero descriptors while the
    listing continues below it -- reading that as "exhausted" would permanently
    skip everything deeper, --full included. (Same for any page whose items all
    lack the type's identifier in the link text.) A raw-empty page normally IS
    the end; but when the envelope's TotalCount says more items should exist,
    the listing is truncated or broken, and that is an error, not clean
    exhaustion (rule:fail-fast) -- the raise lands inside sync's walk, after
    begin(), so the watermark store stays dirty and the next run re-walks."""
    page = 1
    raw_seen = 0
    while True:
        items, raw, total = listing_page(session, typ, page)
        if raw == 0:
            if total and raw_seen < total:
                raise ValueError(
                    "%s: listing page %d is empty but TotalCount=%d and only "
                    "%d items seen -- truncated or broken listing" %
                    (typ, page, total, raw_seen))
            return
        raw_seen += raw
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


def download_document(session, root, item, delay):
    """Fetch the landing page + its content file(s); store the record JSON,
    the landing HTML, and each file. Returns the stored record."""
    landing = fetch(session, item["url"])
    typ, basefile = item["type"], item["basefile"]
    slug = basefile_slug(basefile)
    files = []
    for href in find_content_links(landing.text):
        url = (BASE + href) if href.startswith("/") else href
        data = fetch(session, url).content
        ext = document_extension(data)
        if ext is None:                    # not a document (image, error page)
            continue
        name = "%s%s%s" % (slug, ("-%d" % len(files) if files else ""), ext)
        compress.write_download(Path(root) / typ / name, data)
        files.append(name)
        time.sleep(delay)
    compress.write_download(Path(root) / typ / (slug + ".html"), landing.text)
    record = {k: item[k] for k in
              ("type", "basefile", "identifier", "title", "date", "url")}
    record["files"] = files
    compress.write_download(record_path(root, typ, basefile),
                            json.dumps(record, ensure_ascii=False, indent=2))
    return record


# --------------------------------------------------------------------------
# download loop
# --------------------------------------------------------------------------

def has_live_record(root, typ, basefile):
    """Whether a *live-harvest* record already exists for this document. A frozen
    import record (§7g -- it carries a `source` key) is treated as absent, for two
    reasons: live always wins, so the downloader must fetch its better copy and
    overwrite the import; and a legacy record must not trip the newest-first
    incremental stop (`done = True`) as if the corpus were already caught up."""
    recpath = record_path(root, typ, basefile)
    return compress.exists(recpath) and "source" not in json.loads(compress.read_text(recpath))


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
    it is found (ignoring the on-disk stop and the watermark). Returns
    {type: (seen, new)}."""
    session = make_session(USER_AGENT)
    totals = {}
    rep = Reporter()
    for typ in (types or list(TYPES)):
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
        # a crashed run leaves {"last_harvest": null, "dirty": true}: still a
        # backfill, so key on the date, not on the file existing
        backfill = full or watermark.last_harvest is None
        seen = new = errors = 0
        done = False
        newest_date = None
        if only is None:
            watermark.begin()
        for items, total, page in iter_listing(session, typ, delay):
            for item in items:
                seen += 1
                if only is not None:
                    if item["basefile"] != only:
                        continue
                    download_document(session, root, item, delay)
                    new, done = 1, True
                    break

                if newest_date is None and item.get("date"):
                    newest_date = item["date"]

                is_downloaded = has_live_record(root, typ, item["basefile"])
                if not backfill:
                    if watermark.should_stop(is_downloaded, item.get("date")):
                        done = True
                        break
                if is_downloaded:
                    continue

                try:
                    download_document(session, root, item, delay)
                    new += 1
                except requests.HTTPError as exc:
                    errors += 1
                    log("  %s %s: %s" % (typ, item["basefile"], exc))
                if limit and new >= limit:
                    done = True
                    break
            rep.update(seen, total, scope=typ, page=page, new=new)
            if done:
                break

        if only is None:
            truncated = bool(limit) and new >= limit
            if not truncated:
                # complete() advances the date even with errors (the
                # date-conclusive stop bounds how deep future runs walk, so a
                # permanently-broken document never forces ever-deeper
                # re-walks), but a per-doc failure or a zero-item walk
                # (indistinguishable from selector rot) leaves the store
                # dirty: the next run walks past the consecutive-hit stop
                # down to the date boundary and retries what was stranded.
                watermark.complete(newest_date,
                                   errors=errors if errors else int(seen == 0),
                                   log=log)
            # a --limit-truncated run just leaves the dirty flag begin() set --
            # the un-fetched backlog below the cap is re-walked next run

        rep.done()
        if errors:
            log("  %s: %d download error(s) -- the store stays dirty, so the "
                "next run re-walks down to the watermark boundary and retries "
                "them (--only <basefile> forces one now)" % (typ, errors))
        totals[typ] = (seen, new)
    return totals
