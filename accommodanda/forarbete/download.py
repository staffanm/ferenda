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
import os
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from ..lib.net import make_session
from ..lib.util import Reporter

BASE = "https://www.regeringen.se"
FILTER = (BASE + "/Filter/GetFilteredItems?lang=sv&filterType=Taxonomy"
          "&filterByType=FilterablePageBase&rootPageReference=0"
          "&displayLimited=True&preFilteredCategories=%s&page=%d")
# regeringen.se 403s non-browser User-Agents, so present a normal browser one
# (the documents are public government records; we stay polite with delays).
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# type -> (url segment, taxonomy category id, identifier regex over the listing
# link text). A None regex marks a type regeringen.se publishes without a
# number; its basefile falls back to the landing-page slug.
TYPES = {
    "prop": ("proposition", 1329, r"Prop\. (\d{4}/\d{2,4}:\d+)"),
    "sou": ("statens-offentliga-utredningar", 1331, r"SOU (\d{4}:\d+)"),
    "ds": ("departementsserien-och-promemorior", 1325, r"Ds (\d{4}:\d+)"),
    "dir": ("kommittedirektiv", 1327, r"Dir\. (\d{4}:\d+)"),
    "fm": ("forordningsmotiv", 1326, r"Fm (\d{4}:\d+)"),
    "skr": ("skrivelse", 1330, r"Skr\. (\d{4}/\d{2,4}:\d+)"),
    "so": ("sveriges-internationella-overenskommelser", 1332, None),
    "lr": ("lagradsremiss", 2085, None),
}

PDF_HREF = re.compile(r"/(?:contentassets|globalassets)/[^\"']+\.(?:pdf|docx?|rtf)",
                      re.IGNORECASE)


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def basefile_slug(basefile):
    """Filesystem-safe form of a basefile; the true identifier lives in the
    record JSON, so this only has to be unique and stable."""
    return basefile.replace("/", "-").replace(":", "-").replace(" ", "_")


def record_path(root, typ, basefile):
    return Path(root) / typ / (basefile_slug(basefile) + ".json")


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
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for li in soup.select("ul.list--block > li"):
        a = li.find("a", href=hrefpat)
        if not a:
            continue
        href = a["href"]
        text = a.get_text(" ", strip=True)
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
        url = (BASE + href) if href.startswith("/") else href
        out.append({"type": typ, "basefile": basefile, "identifier": identifier,
                    "title": title, "date": date,
                    "url": url if url.endswith("/") else url + "/", "slug": slug})
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
    order. regeringen.se hangs them under /contentassets/ or /globalassets/."""
    soup = BeautifulSoup(html, "html.parser")
    seen, out = set(), []
    for a in soup.find_all("a", href=PDF_HREF):
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
    for i, href in enumerate(find_content_links(landing.text)):
        url = (BASE + href) if href.startswith("/") else href
        ext = os.path.splitext(href.split("?")[0])[1] or ".pdf"
        name = "%s%s%s" % (slug, ("-%d" % i if i else ""), ext)
        write_atomic(Path(root) / typ / name, fetch(session, url).content)
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

def sync(root, types=None, full=False, limit=None, delay=0.5, log=print,
         only=None):
    """Harvest the named types (default all).

    A type is *backfilled* -- the whole listing walked, downloading whatever is
    missing -- when `--full` is given or the type has never been cleanly walked
    (no `.complete` marker yet: a first run, or one interrupted partway). The
    marker is written only after a full walk with no download errors, so an
    interrupted or partially-failed initial load is resumed, not mistaken for a
    finished one. Once complete, later runs go *incremental*: newest-first,
    stopping at the first document already on disk. `only` (a basefile)
    downloads just that one document, walking the listing until it is found
    (ignoring the on-disk stop). Returns {type: (seen, new)}."""
    session = make_session(USER_AGENT)
    totals = {}
    rep = Reporter()
    for typ in (types or list(TYPES)):
        marker = Path(root) / typ / ".complete"
        backfill = full or not marker.exists()
        seen = new = errors = 0
        done = False
        for items, total, page in iter_listing(session, typ, delay):
            for item in items:
                seen += 1
                if only is not None:
                    if item["basefile"] != only:
                        continue
                    download_document(session, root, item, delay)
                    new, done = 1, True
                    break
                if record_path(root, typ, item["basefile"]).exists():
                    if not backfill:
                        done = True   # newest-first => everything after is older
                        break
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
            # the listing was exhausted with no early stop -> the whole type
            # was walked. Mark it complete (once clean) so later runs can go
            # incremental instead of re-walking everything.
            if not only and errors == 0:
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("")
        rep.done()
        totals[typ] = (seen, new)
    return totals


def list_basefiles(root, typ):
    return sorted(json.loads(p.read_text())["basefile"]
                  for p in (Path(root) / typ).glob("*.json"))
