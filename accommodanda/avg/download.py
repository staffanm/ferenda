"""Harvesters for JO and JK decisions from the organs' own sites.

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
incremental runs stop at the first page with nothing new; the ``.complete``
marker gates the initial oldest..newest backfill exactly like dv/forarbete.

**JK** (jk.se, Umbraco): the listing at ``/beslut-och-yttranden/`` still
honours the legacy "broken pagination" hack -- ``POST page=9999`` returns every
decision the site carries in one response (~1,400, publications 1998-). The
decision *is* its HTML landing page (no PDF), so per decision we store the
landing page plus a record JSON. Diarienummer come in several raw shapes
(``6098-19-4.4`` dotted old form, ``2024/6800`` new form, ``JK ``-prefixed,
multi-dnr ``;``-separated); :func:`jk_canonical` reduces them to the citation
form that names the document.

Stored per decision under ``site/data/avg/downloaded/{org}/``:
``<slug>.json`` record (+ for JO the decision PDF, for JK the landing HTML).
"""

import html as htmllib
import json
import re
import time
from pathlib import Path

from bs4 import BeautifulSoup

from ..lib.net import BROWSER_UA as USER_AGENT
from ..lib.net import make_session, request
from ..lib.util import Reporter, basefile_slug, record_path, write_atomic

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


def jo_save(root, hit, session, delay):
    """Store one hit's record (+ its decision PDF when missing on disk).
    Returns True if the record is new or changed."""
    dnrs = jo_dnrs(hit.get("diary_number"))
    if not dnrs:
        print("jo: hit %s has no parsable diary_number %r, skipping"
              % (hit.get("id"), hit.get("diary_number")), flush=True)
        return False
    basefile = "jo/" + dnrs[0]
    record = jo_record(hit, basefile)
    path = record_path(root, "jo", basefile)
    changed = not (path.exists() and json.loads(path.read_text()) == record)
    if changed:
        write_atomic(path, json.dumps(record, ensure_ascii=False, indent=2))
    pdf_url = record.get("pdf_url")
    pdf = jo_pdf_path(root, basefile)
    if pdf_url and not pdf.exists():
        response = request(session, "GET", pdf_url, timeout=120)
        if response.content[:4] == b"%PDF":
            write_atomic(pdf, response.content)
        else:
            print("jo: %s pdf_url served non-PDF, skipping body file"
                  % basefile, flush=True)
        time.sleep(delay)
    return changed


def jo_pdf_path(root, basefile):
    return Path(root) / "jo" / (basefile_slug(basefile) + ".pdf")


def jo_sync(root, full=False, only=None, limit=None, delay=0.5):
    """Harvest JO decisions. Newest-first; incremental runs stop at the first
    page with nothing new once a clean full walk has completed (the
    ``.complete`` marker, dv's rule). ``only`` = one basefile ("jo/2340-2025"):
    a targeted search on the case number."""
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
        return 1, int(jo_save(root, hits[0], session, delay))
    marker = Path(root) / "jo" / COMPLETE
    backfill = full or not marker.exists()
    seen = new = 0
    page, exhausted = 1, False
    rep = Reporter()
    while True:
        envelope = jo_search(session, nonce, page)
        hits = envelope["search_hits"]
        if not hits:
            exhausted = True
            break
        page_new = sum(jo_save(root, hit, session, delay) for hit in hits)
        seen += len(hits)
        new += page_new
        rep.update(seen, envelope["total_hits"], page=page, changed=page_new)
        if limit and seen >= limit:
            break
        if page >= envelope["total_pages"]:
            exhausted = True
            break
        if not backfill and page_new == 0:
            break     # newest-first: everything older is already harvested
        page += 1
        time.sleep(delay)
    rep.done()
    if exhausted and not limit:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("")
    return seen, new


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


def jk_save(root, item, session, delay):
    """Store one decision: its landing page (the document itself) + record.
    Returns True when fetched (new), False when already on disk."""
    basefile = "jk/" + jk_canonical(item["dnr_raw"])
    record = {"basefile": basefile, "org": "jk",
              "diarienummer_raw": item["dnr_raw"],
              "beslutsdatum_raw": item["beslutsdatum_raw"],
              "title": item["title"], "url": item["url"]}
    path = record_path(root, "jk", basefile)
    landing = jk_html_path(root, basefile)
    if path.exists() and landing.exists() \
            and json.loads(path.read_text()) == record:
        return False
    response = request(session, "GET", item["url"], timeout=60)
    write_atomic(landing, response.text)
    write_atomic(path, json.dumps(record, ensure_ascii=False, indent=2))
    time.sleep(delay)
    return True


def jk_sync(root, full=False, only=None, limit=None, delay=0.5):
    """Harvest JK decisions. The listing is one request, so every run walks all
    entries and fetches only what is missing or changed (``--full`` refetches
    landings too, by clearing the record match via the raw fields)."""
    session = make_session(USER_AGENT)
    items = jk_listing(session)
    if only:
        dnr = only.split("/", 1)[1]
        items = [i for i in items if jk_canonical(i["dnr_raw"]) == dnr]
        assert items, "jk.se listing carries no decision with dnr %s" % dnr
    seen = new = 0
    rep = Reporter()
    for item in items:
        if full:
            jk_html_path(root, "jk/" + jk_canonical(item["dnr_raw"])) \
                .unlink(missing_ok=True)
        new += jk_save(root, item, session, delay)
        seen += 1
        rep.update(seen, len(items), changed=new)
        if limit and seen >= limit:
            break
    rep.done()
    return seen, new


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def sync(root, scopes=None, full=False, only=None, limit=None, delay=0.5):
    """Harvest the named organs (default both). Returns {org: (seen, new)}."""
    totals = {}
    for org in (scopes or ("jo", "jk")):
        run = {"jo": jo_sync, "jk": jk_sync}[org]
        scoped_only = only if only and only.startswith(org + "/") else None
        totals[org] = run(str(root), full=full, only=scoped_only,
                          limit=limit, delay=delay)
    return totals
