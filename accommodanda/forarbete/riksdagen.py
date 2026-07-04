"""Downloader for utskottsbetänkanden (committee reports, doktyp=bet) from
data.riksdagen.se -- the third incremental-harvest loop in the codebase
(regeringen `download.py`, `foreskrift/harvest.py` are the other two).

A committee report is the missing prop→law link: an SFS register and a
proposition cite it as "bet. 2025/26:JuU47 s. 12", and the FORARBETEN citation
grammar (`lib/lagrum.py`) already mints those refs as
`https://lagen.nu/bet/<riksmöte>:<beteckning>`. Keying this vertical the same
way (`basefile = "<rm>:<beteckning>"`) makes those citations resolve to a real
catalog document for free.

The listing walk is the dokumentlista JSON feed, newest-first, paged by
`@nasta_sida`:

    GET /dokumentlista/?doktyp=bet&utformat=json&sort=datum&sortorder=desc&sz=200

Each entry carries its own metadata (rm, beteckning, titel, datum, dok_id,
organ) and, when riksdagen has attached the printed report, a `filbilaga` with
the PDF url. The printed page is förarbete's citation anchor ("… s. 12" ->
`#sid12`) and the riksdagen HTML body has no pages, so **bodies are PDF-only**:
we store the filbilaga PDF and nothing else. A document without a filbilaga
(a planned or not-yet-printed betänkande) gets a metadata-only record -- still a
real catalog document at its URI, and still a citation-link target. Such a
record is provisional, not final: once the feed shows a filbilaga for the
entry, the record is re-downloaded and upgraded in place (see `_currency`).

Stored under `site/data/forarbete/bet/`: one `<slug>.json` record (type,
basefile, identifier, title, date, url, files, plus organ and dok_id) and,
when present, the `<slug>.pdf`. Incremental by default: newest-first, stop at
the first *final* document already on disk and current -- a provisional
(planned, filbilaga-less) record never anchors the stop, since its planned
datum can top docs published after the last harvest (see `_currency`).
`--full` re-walks.
`--riksmote` narrows the walk to one riksmöte for dev/manual runs -- a narrowed
walk is a partial view, so it never writes the `.complete` marker (else a later
full backfill would go incremental and silently skip the corpus).

The API caps any one listing at ~50 pages (10k docs at sz=200), far below the
~75k-document corpus, so a single un-narrowed walk can never reach the older
betänkanden. A backfill therefore iterates riksmöte by riksmöte, newest to
oldest -- each riksmöte is ~300-900 docs, well under the cap; see `riksmoten`
for the empirically verified value sequence. The plain incremental run (new
docs land at the top of the un-narrowed listing) keeps the single walk.

The corpus reaches back to 1867, so the older filbilagor are scans; those PDFs
currently parse best-effort through the ordinary live-PDF branch with no OCR
fallback -- an accepted limitation, revisited with the full-crawl decision.
"""

import json
import time
from pathlib import Path
from urllib.parse import quote

import requests

from ..lib.net import HARVESTER_UA as USER_AGENT
from ..lib.net import make_session, request
from ..lib.util import (
    Reporter,
    basefile_slug,
    record_path,
    sync_complete_marker,
    write_atomic,
)
from .download import _has_live_record

API = "https://data.riksdagen.se"
LISTING = (API + "/dokumentlista/?doktyp=bet&utformat=json"
           "&sort=datum&sortorder=desc&sz=200")
TYPE = "bet"
COMPLETE = ".complete"   # marker under bet/: corpus walked clean at least once


def _https(url):
    """A protocol-relative (`//host/…`) or http riksdagen url normalized to
    https -- the feed emits both forms."""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://"):
        return "https://" + url[len("http://"):]
    return url


def descriptor(entry):
    """One dokumentlista entry -> a record descriptor, all fields taken straight
    from the entry. `basefile = "<rm>:<beteckning>"` (e.g. "2025/26:JuU47") and
    `identifier = "Bet. <basefile>"` match the FORARBETEN grammar's bet URIs."""
    basefile = "%s:%s" % (entry["rm"], entry["beteckning"])
    return {"type": TYPE, "basefile": basefile,
            "identifier": "Bet. " + basefile,
            "title": entry["titel"], "date": entry["datum"],
            "url": _https(entry["dokument_url_html"]),
            "organ": entry["organ"], "dok_id": entry["dok_id"],
            "files": []}


def pdf_fil(entry):
    """The PDF entry in a document's `filbilaga`, or None when it has none. The
    filbilaga is null for a betänkande riksdagen has not attached a printed PDF
    to (planned or text-only); every attached file seen for doktyp=bet is a PDF."""
    filbilaga = entry.get("filbilaga")
    if not filbilaga:
        return None
    fil = filbilaga["fil"]
    fil = fil if isinstance(fil, list) else [fil]   # single-file entries come as a dict
    pdfs = [f for f in fil if f["typ"] == "pdf"]
    return pdfs[0] if pdfs else None


def iter_pages(session, url, delay):
    """Yield each dokumentlista page (newest-first), following `@nasta_sida`.
    data.riksdagen.se caps pagination at ~sida 50 yet keeps emitting a
    `@nasta_sida` that points past the cap and re-serves the capped page, so the
    walk also stops once the reported `@sida` no longer advances -- otherwise it
    would loop forever on the last reachable page."""
    seen_sida = 0
    while url:
        page = request(session, "GET", url, parse_json=True)["dokumentlista"]
        sida = int(page["@sida"])
        if sida <= seen_sida:
            return
        seen_sida = sida
        yield page
        nxt = page.get("@nasta_sida")
        url = _https(nxt) if nxt else None
        if url:
            time.sleep(delay)


def _docs(page):
    """The page's document list; the feed collapses a single hit to a lone dict."""
    docs = page.get("dokument") or []
    return docs if isinstance(docs, list) else [docs]


def download_document(session, root, entry, delay):
    """Store one betänkande: the record JSON and, when the entry has a PDF
    filbilaga, that PDF under `root/bet/<slug>.pdf`. A document without a
    filbilaga gets a metadata-only record (files: []). Returns the record."""
    record = descriptor(entry)
    slug = basefile_slug(record["basefile"])
    fil = pdf_fil(entry)
    if fil is not None:
        data = request(session, "GET", fil["url"], timeout=120).content
        # load-bearing validation of untrusted remote bytes (an HTML error page
        # served with 200 must not be stored as the document's PDF forever) --
        # a raise, never an assert (rule:errors-drive-retry-use-raise)
        if data[:4] != b"%PDF":
            raise ValueError("%s: filbilaga %s is not a PDF"
                             % (record["basefile"], fil["url"]))
        name = slug + ".pdf"
        write_atomic(Path(root) / TYPE / name, data)
        record["files"] = [name]
        time.sleep(delay)
    write_atomic(record_path(root, TYPE, record["basefile"]),
                 json.dumps(record, ensure_ascii=False, indent=2))
    return record


def _currency(root, entry, basefile):
    """How current the on-disk record for this entry is: None when it needs a
    (re)fetch, "final" when it has its PDF stored, "provisional" for a current
    metadata-only record. Builds on the shared `_has_live_record` (a frozen
    import never counts), adding the pre-print upgrade cycle: riksdagen lists a
    betänkande as "planerat" (beslutad=0, filbilaga null -- 19 of the newest 200
    feed entries) before the printed PDF is attached, so a routinely harvested
    fresh doc is often metadata-only at first. Such a record is current only
    while the feed still shows no filbilaga; once one appears, the record is
    stale (None) and re-downloading upgrades it in place. Old genuinely PDF-less
    docs (rm=1990/91: 97 of 100 entries have status "saknas" and a null
    filbilaga) stay provisional forever -- their entries never gain a filbilaga
    -- so they never re-download.

    The final/provisional split exists for the incremental stop: only a final
    record may anchor it (see `_walk`). A planned entry carries the datum of its
    *planned* debate, which can post-date documents published after the last
    harvest -- the feed's datum sort then puts those new docs *behind* the
    placeholder, and stopping at it would skip them silently (permanently, if
    the placeholder is withdrawn and never gains a filbilaga)."""
    if not _has_live_record(root, TYPE, basefile):
        return None
    record = json.loads(record_path(root, TYPE, basefile).read_text())
    if record["files"]:
        return "final"
    return "provisional" if pdf_fil(entry) is None else None


# The riksmöte value sequence the API accepts, verified empirically (2026-07)
# by probing `&rm=<value>` and reading `@traffar`:
#   - split-year "YYYY/YY" works from "1975/76" (708 hits) onward; "1971/72"
#     and "1976" give 0 -- the split form starts exactly at 1975/76.
#   - before that, plain calendar years: "1975" (364, the last spring-only
#     session) back to "1867" (335). "1866"/"1865" give 0 -- the corpus starts
#     with the first bicameral riksdag.
#   - extra sessions fold into the plain year: "1914B"/"1958B"/"1919A" give 0
#     while "1914" (639) and "1958" (543) cover them.
# Summing @traffar over the full sequence (161 riksmöten) gives 74772 against
# the un-narrowed total of 74773 -- coverage is complete except at most one
# stray document with an rm value outside any riksmöte filter.
SPLIT_FROM = 1975      # "1975/76" is the first split-year riksmöte
FIRST_RIKSMOTE = 1867  # the oldest riksmöte with bet documents


def riksmoten(newest_year):
    """Every riksmöte value from the riksmöte starting in `newest_year` down to
    1867, newest first: "2026/27" … "1975/76", then "1975" … "1867"."""
    for year in range(newest_year, SPLIT_FROM - 1, -1):
        yield "%d/%02d" % (year, (year + 1) % 100)
    for year in range(SPLIT_FROM, FIRST_RIKSMOTE - 1, -1):
        yield str(year)


def newest_riksmote_year(session):
    """The starting year of the newest riksmöte with bet documents, read from
    the first un-narrowed listing page (newest-first; planned betänkanden for
    the coming riksmöte appear here before the calendar would predict them)."""
    page = request(session, "GET", LISTING, parse_json=True)["dokumentlista"]
    docs = _docs(page)
    if not docs:
        # untrusted remote response -- a raise, never an assert
        # (rule:errors-drive-retry-use-raise)
        raise ValueError("empty dokumentlista for %s" % LISTING)
    return max(int(entry["rm"][:4]) for entry in docs)


def _walk(session, root, url, *, backfill, limit, delay, log, rep, scope):
    """One listing walk (one url, `@nasta_sida`-paged): download every document
    whose record is absent or stale (`_currency` -- a metadata-only record
    whose entry has since gained a filbilaga is re-downloaded and upgraded).
    Incremental (not backfill) stops at the first current *final* on-disk
    record (newest-first => everything after is older); a current provisional
    record is skipped but never anchors the stop -- its planned-debate datum
    can post-date docs published since the last harvest, which the datum sort
    puts behind it (see `_currency`). A backfill skips current documents but
    keeps walking. Returns (seen, new, errors, truncated) -- truncated = the
    walk ended early (limit hit or incremental stop), so the listing was NOT
    exhausted."""
    seen = new = errors = 0
    truncated = False
    for page in iter_pages(session, url, delay):
        for entry in _docs(page):
            seen += 1
            basefile = "%s:%s" % (entry["rm"], entry["beteckning"])
            currency = _currency(root, entry, basefile)
            if currency:
                if not backfill and currency == "final":
                    truncated = True
                    break
                continue
            try:
                download_document(session, root, entry, delay)
                new += 1
            except (requests.HTTPError, ValueError) as exc:
                # a counted, logged per-document failure (a 404'd filbilaga or
                # non-PDF bytes) that gates the `.complete` marker; it must not
                # abort the remaining ~161-riksmöte walk
                errors += 1
                log("  bet %s: %s" % (basefile, exc))
            if limit and new >= limit:
                truncated = True
                break
        rep.update(seen, int(page["@traffar"]), scope=scope,
                   page=int(page["@sida"]), new=new)
        if truncated:
            break
    rep.done()
    return seen, new, errors, truncated


def sync(root, full=False, limit=None, delay=0.5, log=print, riksmote=None):
    """Harvest utskottsbetänkanden (doktyp=bet) into `root/bet/`.

    Backfilled -- everything missing downloaded -- when `--full` is given or the
    corpus has never been cleanly walked (no `.complete` marker: a first run, or
    one interrupted partway). Because the API caps any one listing at ~10k docs,
    an un-narrowed backfill walks riksmöte by riksmöte, newest to oldest (see
    `riksmoten`); the marker is written only after ALL riksmöte walks finish
    with zero errors, so an interrupted or partially-failed initial load is
    resumed, not mistaken for finished. Once complete, later runs go
    incremental: one un-narrowed newest-first walk stopping at the first
    *final* document already on disk and current (new docs land at the top,
    well within the cap; a filbilaga attached to a doc already *behind* the
    stop point surfaces only under `--full`, like edits to old regeringen
    docs).
    `riksmote` narrows the run to one riksmöte (e.g. "2025/26", the API's `rm=`
    parameter) for dev/manual runs; a narrowed run is a partial view of the
    corpus and therefore NEVER writes `.complete`. Returns (seen, new)."""
    root = Path(root)
    session = make_session(USER_AGENT)
    marker = root / TYPE / COMPLETE
    backfill = full or not marker.exists()
    rep = Reporter()
    if backfill and riksmote is None:
        # marker invariant: entering a backfill invalidates any earlier "cleanly
        # walked" claim -- the marker is dropped up front and rewritten only on a
        # clean finish, so an interrupted, limit-truncated or partially-failed
        # walk (including a --full re-walk over an already-complete corpus)
        # leaves the next run backfilling instead of hiding the gaps behind the
        # incremental stop.
        marker.unlink(missing_ok=True)
        seen = new = errors = 0
        exhausted = True
        for value in riksmoten(newest_riksmote_year(session)):
            s, n, e, truncated = _walk(
                session, root, LISTING + "&rm=" + quote(value), backfill=True,
                limit=(limit - new if limit else None), delay=delay, log=log,
                rep=rep, scope="%s %s" % (TYPE, value))
            seen, new, errors = seen + s, new + n, errors + e
            if truncated:
                exhausted = False   # limit hit mid-corpus -> not a full walk
                break
        sync_complete_marker(marker, exhausted=exhausted, errors=errors)
        return seen, new
    url = LISTING + ("&rm=" + quote(riksmote) if riksmote else "")
    seen, new, errors, _ = _walk(session, root, url, backfill=backfill,
                                 limit=limit, delay=delay, log=log, rep=rep,
                                 scope=TYPE)
    # an incremental or rm-narrowed run never earns the marker; any error
    # drops it (see sync_complete_marker for the no-gaps invariant)
    sync_complete_marker(marker, exhausted=False, errors=errors)
    return seen, new
