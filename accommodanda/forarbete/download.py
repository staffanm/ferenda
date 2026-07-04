"""Downloader for Swedish preparatory works (förarbeten) from regeringen.se.

regeringen.se publishes eight document types under /rattsliga-dokument/. The
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

Harvested via `lagen forarbete download [prop|sou|ds|...]`; no doctype = all.
A single document: `lagen forarbete download <doctype> --only <basefile>`.

Stored under `site/data/forarbete/<type>/`: one `<slug>.json` record (identifier,
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

from ..lib.net import BROWSER_UA as USER_AGENT
from ..lib.net import make_session
from ..lib.regeringen import BASE, TYPES, listing_items
from ..lib.util import (
    HarvestWatermark,
    Reporter,
    basefile_slug,
    document_extension,
    record_path,
    write_atomic,
)

# BASE and the doctype table (TYPES: url segment, taxonomy category id,
# identifier regex) live in lib.regeringen -- shared with the remisser vertical.
FILTER = (BASE + "/Filter/GetFilteredItems?lang=sv&filterType=Taxonomy"
          "&filterByType=FilterablePageBase&rootPageReference=0"
          "&displayLimited=True&preFilteredCategories=%s&page=%d")

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
    """One listing page -> a descriptor per document, in page order (newest
    first): {type, basefile, identifier, title, date, url, slug}."""
    segment, _, idre = TYPES[typ]
    idpat = re.compile(idre) if idre else None
    hrefpat = re.compile(r"/rattsliga-dokument/%s/\d{4}/\d{2}/" % segment)
    out = []
    for li, href, url, text in listing_items(html, hrefpat):
        slug = href.rstrip("/").rsplit("/", 1)[-1]
        time_el = li.find("time")
        date = time_el.get("datetime") if time_el else None
        if idpat:
            m = idpat.search(text)
            if not m:
                continue  # title without this type's identifier -> not a doc
            basefile, identifier = m.group(1), m.group(0)
            title = text[:m.start()].rstrip(", ").strip() or text
        else:
            basefile = identifier = slug
            title = text
        out.append({"type": typ, "basefile": basefile, "identifier": identifier,
                    "title": title, "date": date, "url": url, "slug": slug})
    return out


def listing_page(session, typ, page):
    """One listing page via the AJAX filter endpoint: returns (items,
    total_count). The endpoint wraps the `ul.list--block` HTML in a JSON
    envelope {"Message": <html>, "TotalCount": N}."""
    category = TYPES[typ][1]
    envelope = fetch(session, FILTER % (category, page)).json()
    return parse_listing(envelope.get("Message", ""), typ), envelope.get("TotalCount")


def iter_listing(session, typ, delay):
    """Yield (descriptors, total_count, page_number) per listing page until one
    is empty."""
    page = 1
    while True:
        items, total = listing_page(session, typ, page)
        if not items:
            return
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
        write_atomic(Path(root) / typ / name, data)
        files.append(name)
        time.sleep(delay)
    write_atomic(Path(root) / typ / (slug + ".html"), landing.text)
    record = {k: item[k] for k in
              ("type", "basefile", "identifier", "title", "date", "url")}
    record["files"] = files
    write_atomic(record_path(root, typ, basefile),
                 json.dumps(record, ensure_ascii=False, indent=2))
    return record


# --------------------------------------------------------------------------
# harvest
# --------------------------------------------------------------------------

def _has_live_record(root, typ, basefile):
    """Whether a *live-harvest* record already exists for this document. A frozen
    import record (§7g -- it carries a `source` key) is treated as absent, for two
    reasons: live always wins, so the downloader must fetch its better copy and
    overwrite the import; and a legacy record must not trip the newest-first
    incremental stop (`done = True`) as if the corpus were already caught up."""
    recpath = record_path(root, typ, basefile)
    return recpath.exists() and "source" not in json.loads(recpath.read_text())


def sync(root, types=None, full=False, limit=None, delay=0.5, log=print,
         only=None):
    """Harvest the named types (default all).

    A type is *backfilled* -- the whole listing walked, downloading whatever is
    missing -- when `--full` is given or the type has never been cleanly walked
    (no watermark yet: a first run, or one interrupted partway). The watermark
    is written only after a successful walk/sync with no download errors, so
    an interrupted or failed run is resumed. Once caught up, later runs go
    *incremental*: newest-first, stopping at the first document already on disk
    that falls past the watermark date boundary or when look-ahead limit is reached.
    `only` (a basefile) downloads just that one document, walking the listing until
    it is found (ignoring the on-disk stop). Returns {type: (seen, new)}."""
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

        watermark = HarvestWatermark(watermark_path, lookahead_limit=20, safety_days=14)
        backfill = full or not watermark_path.exists()
        seen = new = errors = 0
        done = False
        newest_date = None
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

                is_downloaded = _has_live_record(root, typ, item["basefile"])
                if not backfill:
                    if watermark.should_stop(is_downloaded, item.get("date")):
                        done = True
                        break
                elif is_downloaded:
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
        else:
            # Loop completed naturally without early stop
            if not only and errors == 0:
                watermark.save(newest_date)
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("")

        # Incremental stop successfully met
        if done and not only and errors == 0 and not limit:
            watermark.save(newest_date)
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("")

        rep.done()
        totals[typ] = (seen, new)
    return totals
