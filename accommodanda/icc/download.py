"""Harvester for ICC substantive decisions.

Two read-only sources, each doing what it is best at:

  * icc-cpi.int ``/decisions`` -- server-rendered listing, facetable by the
    Rome-Statute type-of-decision. The curated facet ids scope the harvest and
    yield each record's document number (plus a metadata fallback).  Its
    ``/court-record/`` detail pages are Cloudflare-walled, so no text there.
  * the ICC Legal Tools API (legal-tools.org) -- a clean JSON backend
    (``/api/ltdDocs``) that resolves a document number (``externalId``) to the
    decision's metadata and slug, and serves the PDF at ``/doc/<slug>/pdf``.

A record is stored as one JSON (the curated kind + the ICC-listing fallback +
the resolved Legal Tools metadata) plus the decision PDF; a record Legal Tools
cannot resolve is kept metadata-only (no body).
"""

import json
import re
import time
from pathlib import Path

from bs4 import BeautifulSoup

from ..lib import compress
from ..lib.harvest import HarvestWatermark, ItemKey, walk
from ..lib.net import HARVESTER_UA as USER_AGENT
from ..lib.net import make_session, request
from ..lib.util import Reporter, document_extension, normalize_space
from .model import RE_DOC_BASE, doc_basefile, load_types

ICC = "https://www.icc-cpi.int"
DECISIONS = ICC + "/decisions"
LT = "https://www.legal-tools.org"
LT_API = LT + "/api/ltdDocs"
LT_PDF = LT + "/doc/%s/pdf"
PAGE_LIMIT = 60                     # guard: no curated facet has more listing pages
# a Legal Tools translation variant closes with a -t<LANG> segment; the English
# primary is the non-translation record
RE_TRANSLATION = re.compile(r"-t[A-Z]{2,4}\b")


def record_path(root, basefile):
    return Path(root) / (basefile + ".json")


def body_path(root, basefile):
    return Path(root) / (basefile + ".pdf")


def _row(row):
    """One listing row -> its document base number and fallback metadata, or
    None for a row without a court-record link."""
    link = row.find("a", href=re.compile(r"^/court-record/"))
    if link is None:
        return None
    match = RE_DOC_BASE.search(link["href"].upper())
    if not match:
        return None
    chamber = next((normalize_space(el.get_text()) for el in row.find_all(True)
                    if "Chamber" in el.get_text() and len(el.get_text()) < 40), None)
    field = lambda cls: (row.select_one("." + cls).get_text(" ", strip=True)
                         if row.select_one("." + cls) else None)
    return {"base": match.group(0), "title": field("recordTitle"),
            "case_name": field("courtRecordcaseName"), "date": field("datetime"),
            "chamber": chamber}


def enumerate_decisions(session):
    """Every curated substantive decision as {base, kind, ...fallback}, one row
    per document base (the first facet that lists it wins its kind). The facet
    sweep is the discovery phase, shown on one live 'icc index' line."""
    seen = {}
    types = load_types()
    rep = Reporter()
    for facet_no, (facet, entry) in enumerate(types.items(), 1):
        for page in range(PAGE_LIMIT):
            envelope = request(session, "GET", DECISIONS, timeout=120, params={
                "f[0]": "decision_type_of_decision:" + facet, "page": str(page)})
            rows = BeautifulSoup(envelope.text, "html.parser").select(".views-row")
            if not rows:
                break
            for element in rows:
                record = _row(element)
                if record and record["base"] not in seen:
                    record["kind"] = entry["kind"]
                    seen[record["base"]] = record
            if page == PAGE_LIMIT - 1:
                raise ValueError("icc: facet %s exceeds %d listing pages -- raise "
                                 "PAGE_LIMIT" % (facet, PAGE_LIMIT))
            time.sleep(0.2)
        rep.update(facet_no, len(types), scope="icc index", found=len(seen))
    rep.done()
    # an empty harvest means the .views-row markup or the facet ids drifted, not
    # that the ICC issued no substantive decisions -- fail loudly rather than
    # silently wipe the corpus on the next relate
    if not seen:
        raise ValueError("icc: /decisions scrape yielded no rows -- facet ids or "
                         ".views-row markup drifted")
    return list(seen.values())


def _english_primary(matches, base):
    """The English primary among a base number's Legal Tools variants: drop the
    -t<LANG> translations, then prefer the exact base, then the shortest
    externalId (bare over -Red over -Red-Corr)."""
    english = [m for m in matches if not RE_TRANSLATION.search(m.get("externalId", ""))]
    english = sorted(english or matches,
                     key=lambda m: (m.get("externalId") != base, len(m.get("externalId", ""))))
    return english[0] if english else None


def resolve_lt(session, base):
    """The English primary Legal Tools record for a document base number, or None
    when Legal Tools does not hold it."""
    filt = json.dumps({"where": {"externalId": {"like": base}}, "limit": 50})
    matches = request(session, "GET", LT_API, parse_json=True, timeout=120,
                      params={"filter": filt})
    return _english_primary(matches, base) if matches else None


def fetch_pdf(session, slug):
    response = request(session, "GET", LT_PDF % slug, timeout=180)
    if document_extension(response.content) != ".pdf":
        raise ValueError("Legal Tools doc %s is not a PDF" % slug)
    return response.content


def _stored_record(record, lt):
    """The stored JSON: the curated kind, the ICC-listing fallback, and the
    resolved Legal Tools metadata (None when unresolved)."""
    return {"base": record["base"], "kind": record["kind"],
            "icc": {"title": record.get("title"), "case_name": record.get("case_name"),
                    "date": record.get("date"), "chamber": record.get("chamber")},
            "lt": lt}


def list_basefiles(root):
    return sorted(path.stem for path in compress.glob(root, "*.json")
                  if not path.name.startswith("."))


def resolve(session, root, record, full=False, delay=0.3):
    """Store one decision, keyed by its base document number (variant qualifiers
    like -Red name only which PDF Legal Tools served). Re-resolves and re-fetches
    only when new or forced."""
    basefile = doc_basefile(record["base"])
    path = record_path(root, basefile)
    body = body_path(root, basefile)
    stored = json.loads(compress.read_text(path)) if compress.exists(path) else None
    if not full and stored is not None and (stored.get("lt") is None
                                            or compress.exists(body)):
        return False
    lt = resolve_lt(session, record["base"])
    if lt and lt.get("slug"):
        compress.write_download(body, fetch_pdf(session, lt["slug"]))
        time.sleep(delay)
    compress.write_download(path, json.dumps(_stored_record(record, lt),
                                             ensure_ascii=False, indent=2))
    return True


def sync(root, full=False, only=None, limit=None, delay=0.3, log=print):
    root = Path(root)
    session = make_session(USER_AGENT)
    records = enumerate_decisions(session)
    if only:
        record = next((r for r in records if r["base"] == only.upper()), None)
        if record is None:
            raise ValueError("ICC lists no curated decision %s" % only)
        return 1, int(resolve(session, root, record, full=full, delay=delay))

    records.sort(key=lambda r: r.get("date") or "", reverse=True)
    watermark = HarvestWatermark(root / ".watermark.json",
                                 lookahead_limit=30, safety_days=30)

    def item_key(record):
        # keyed by the base document number, so a stored record is found cheaply
        return ItemKey(record["base"],
                       compress.exists(record_path(root, doc_basefile(record["base"]))),
                       _iso(record.get("date")))

    result = walk(records, resolve=lambda r: resolve(session, root, r, full=full,
                                                     delay=delay),
                  item_key=item_key, watermark=watermark, full=full, limit=limit,
                  scope="icc", count_label="stored", total=len(records), log=log)
    return result.seen, result.new


_MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], 1)}


def _iso(value):
    """The ICC listing date ('4 February 2021') as an ISO date, for the watermark."""
    match = re.match(r"(\d{1,2})\s+([A-Z][a-z]+)\s+(\d{4})", value or "")
    return ("%04d-%02d-%02d" % (int(match.group(3)), _MONTHS[match.group(2)],
                                int(match.group(1)))) if match else None
