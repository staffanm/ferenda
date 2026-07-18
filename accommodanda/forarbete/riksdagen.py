"""Downloader for utskottsbetänkanden (committee reports, doktyp=bet) from
data.riksdagen.se -- the third incremental-harvest loop in the codebase
(regeringen `download.py`, `foreskrift/harvest.py` are the other two).
The dokumentlista walk, the riksmöte-sliced backfill and the watermark
lifecycle are doctype-agnostic (`harvest`); this module drives them with the
bet specifics (PDF filbilaga bodies, the planned-placeholder upgrade cycle),
and `rskr.py` reuses the same engine for riksdagsskrivelser.

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

Stored under `site/data/downloaded/forarbete/bet/`: one `<slug>.json` record (type,
basefile, identifier, title, date, url, files, plus organ and dok_id) and,
when present, the `<slug>.pdf`. Incremental by default: newest-first, gated by
the shared `HarvestWatermark` (`lib/harvest.py`) exactly like the regeringen and
foreskrift downloaders -- the walk stops on a run of consecutive already-current
*final* documents, or conclusively on one current final document older than the
last clean harvest minus the safety margin. A provisional (planned,
filbilaga-less) record never feeds the gate: its planned datum can top docs
published after the last harvest (see `_currency`), and for the same reason the
saved watermark date is the newest *published* entry's datum, never a planned
one. `--full` re-walks.
`--riksmote` narrows the walk to one riksmöte for dev/manual runs -- a narrowed
walk is a partial view, so it never advances the watermark (else a later
full backfill would go incremental and silently skip the corpus).

The API caps any one listing at ~50 pages (10k docs at sz=200), below the
~25k-document corpus, so a single un-narrowed walk can never reach the older
betänkanden. A backfill therefore iterates riksmöte by riksmöte, newest to
oldest -- each riksmöte is ~300-900 docs, well under the cap; see `riksmoten`
for the empirically verified value sequence. The plain incremental run (new
docs land at the top of the un-narrowed listing) keeps the single walk.

We harvest from riksmöte 1971 -- the first unicameral riksdag -- onward, where
`doktyp=bet` is exactly the committee reports (100% subtyp=bet). Before 1971 the
same doktyp is dominated by utskotts-*utlåtanden* and *memorial* (a bicameral-era
document species with its own identifiers and citation forms, none modelled
here yet); genuine "betänkande"-subtype documents in that era are almost only
Bevillningsutskottet's, so harvesting the old doktyp would mislabel utlåtanden as
"Bet. …" and add link targets nothing resolves. The pre-1971 corpus is therefore
a deliberate non-goal, revisited as its own project (see `FIRST_RIKSMOTE`).

The oldest filbilagor (early-1970s) are scans; those PDFs currently parse
best-effort through the ordinary live-PDF branch with no OCR fallback -- an
accepted limitation, revisited with the full-crawl decision.
"""

import json
import time
from pathlib import Path
from urllib.parse import quote

import requests

from ..lib import compress, layout
from ..lib.harvest import HarvestWatermark
from ..lib.net import HARVESTER_UA as USER_AGENT
from ..lib.net import make_session, request
from ..lib.util import (
    Reporter,
    basefile_slug,
)
from .download import has_live_record

API = "https://data.riksdagen.se"
LISTING = (API + "/dokumentlista/?doktyp=bet&utformat=json"
           "&sort=datum&sortorder=desc&sz=200")
TYPE = "bet"
WATERMARK = ".watermark.json"   # under bet/: HarvestWatermark state


def _https(url):
    """A protocol-relative (`//host/…`) or http riksdagen url normalized to
    https -- the feed emits both forms."""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://"):
        return "https://" + url[len("http://"):]
    return url


def basefile_of(entry):
    """The `<rm>:<beteckning>` basefile off one dokumentlista entry. A missing
    field is a malformed remote feed entry, raised as ValueError -- recorded
    per-document in `_walk` and skipped, where a bare KeyError would escape the
    per-document catch and abort an hours-long backfill
    (rule:errors-drive-retry-use-raise)."""
    if "rm" not in entry or "beteckning" not in entry:
        raise ValueError("malformed dokumentlista entry (missing rm/beteckning)"
                         ", dok_id=%s" % entry.get("dok_id", "?"))
    return "%s:%s" % (entry["rm"], entry["beteckning"])


def descriptor(entry):
    """One dokumentlista entry -> a record descriptor, all fields taken straight
    from the entry. `basefile = "<rm>:<beteckning>"` (e.g. "2025/26:JuU47") and
    `identifier = "Bet. <basefile>"` match the FORARBETEN grammar's bet URIs.
    A missing field is a malformed remote feed entry, raised as ValueError --
    recorded per-document in `_walk`, never fatal to the walk
    (rule:errors-drive-retry-use-raise)."""
    basefile = basefile_of(entry)
    missing = [k for k in ("titel", "datum", "dokument_url_html", "organ",
                           "dok_id") if k not in entry]
    if missing:
        raise ValueError("%s: malformed dokumentlista entry, missing %s"
                         % (basefile, ", ".join(missing)))
    return {"type": TYPE, "basefile": basefile,
            "identifier": "Bet. " + basefile,
            "title": entry["titel"], "date": entry["datum"],
            "url": _https(entry["dokument_url_html"]),
            "organ": entry["organ"], "dok_id": entry["dok_id"],
            "files": []}


def pdf_fil(entry):
    """The PDF entry in a document's `filbilaga`, or None when it has none. The
    filbilaga is null for a betänkande riksdagen has not attached a printed PDF
    to (planned or text-only); every attached file seen for doktyp=bet is a PDF.
    A filbilaga whose fil entries lack typ/url is a malformed remote feed
    entry, raised as ValueError -- recorded per-document in `_walk`, never
    fatal to the walk (rule:errors-drive-retry-use-raise)."""
    filbilaga = entry.get("filbilaga")
    if not filbilaga:
        return None
    fil = filbilaga.get("fil")
    if fil is None:
        raise ValueError("malformed filbilaga (no fil), dok_id=%s"
                         % entry.get("dok_id", "?"))
    fil = fil if isinstance(fil, list) else [fil]   # single-file entries come as a dict
    if any("typ" not in f or "url" not in f for f in fil):
        raise ValueError("malformed filbilaga fil entry (missing typ/url), "
                         "dok_id=%s" % entry.get("dok_id", "?"))
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
        compress.write_download(
            layout.fa_dir(root, TYPE, record["basefile"]) / name, data)
        record["files"] = [name]
        time.sleep(delay)
    compress.write_download(layout.fa_record_file(root, TYPE, record["basefile"]),
                            json.dumps(record, ensure_ascii=False, indent=2))
    return record


def _currency(root, basefile, entry):
    """How current the on-disk record for this entry is: None when it needs a
    (re)fetch, "final" when it has its
    PDF stored, "provisional" for a current
    metadata-only record. Builds on the shared `has_live_record` (a frozen
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
    record may feed the watermark gate (see `_walk`). A planned entry carries
    the datum of its *planned* debate, which can post-date documents published
    after the last harvest -- the feed's datum sort then puts those new docs
    *behind* the placeholder, and stopping at it would skip them silently
    (permanently, if the placeholder is withdrawn and never gains a
    filbilaga)."""
    if not has_live_record(root, TYPE, basefile):
        return None
    record = json.loads(compress.read_text(
        layout.fa_record_file(root, TYPE, basefile)))
    if record["files"]:
        return "final"
    return "provisional" if pdf_fil(entry) is None else None


def _published(entry):
    """Only a filbilaga-carrying entry counts as published for the watermark
    date -- a planned entry's future datum would erode the safety margin."""
    return pdf_fil(entry) is not None


# The riksmöte value sequence the API accepts, verified empirically (2026-07)
# by probing `&rm=<value>` and reading `@traffar`:
#   - split-year "YYYY/YY" works from "1975/76" (708 hits) onward; "1971/72"
#     and "1976" give 0 -- the split form starts exactly at 1975/76.
#   - before that, plain calendar years: "1975" (364, the last spring-only
#     session) back to "1971" (681), the first unicameral riksmöte.
# Summing @traffar over this sequence (57 riksmöten, 1971..2026/27) gives 24850,
# every one a genuine subtyp=bet betänkande. The un-narrowed total is 74773 --
# the ~50k difference is the pre-1971 utlåtanden/memorial deliberately excluded
# (see the module docstring and FIRST_RIKSMOTE), not a coverage gap.
SPLIT_FROM = 1975      # "1975/76" is the first split-year riksmöte
FIRST_RIKSMOTE = 1971  # first unicameral riksmöte; doktyp=bet is 100% subtyp=bet
                       # from here on. Pre-1971 doktyp=bet is mostly utlåtanden/
                       # memorial (see module docstring) -- a deliberate non-goal.


def riksmoten(newest_year):
    """Every riksmöte value from the riksmöte starting in `newest_year` down to
    1971, newest first: "2026/27" … "1975/76", then "1975" … "1971"."""
    for year in range(newest_year, SPLIT_FROM - 1, -1):
        yield "%d/%02d" % (year, (year + 1) % 100)
    for year in range(SPLIT_FROM, FIRST_RIKSMOTE - 1, -1):
        yield str(year)


def newest_riksmote_year(session, listing=LISTING):
    """The starting year of the newest riksmöte with documents in `listing`,
    read from the first un-narrowed page (newest-first; planned betänkanden for
    the coming riksmöte appear here before the calendar would predict them)."""
    page = request(session, "GET", listing, parse_json=True)["dokumentlista"]
    docs = _docs(page)
    if not docs:
        # untrusted remote response -- a raise, never an assert
        # (rule:errors-drive-retry-use-raise)
        raise ValueError("empty dokumentlista for %s" % listing)
    return max(int(entry["rm"][:4]) for entry in docs)


def _walk(session, root, url, *, watermark, delay, log, rep, scope,
          typ=TYPE, fetch=download_document, currency=_currency,
          published=_published):
    """One listing walk (one url, `@nasta_sida`-paged): download every document
    whose record is absent or stale (`_currency` -- a metadata-only record
    whose entry has since gained a filbilaga is re-downloaded and upgraded).
    With a `watermark` (an incremental run) the walk stops when the
    `HarvestWatermark` gate says the corpus is caught up; only a current
    *final* record counts as downloaded for the gate -- a current provisional
    record is skipped but reads as a gap, since its planned-debate datum can
    post-date docs published since the last harvest, which the datum sort puts
    behind it (see `_currency`). `watermark=None` (a backfill or an rm-narrowed
    run) skips current documents but always walks the whole listing. Returns
    (seen, new, errors, newest_pub) -- newest_pub = the datum of the newest
    *published* (filbilaga-carrying) entry seen, the only sound watermark date
    (a planned entry's future datum would erode the safety margin).

    The `typ`/`fetch`/`currency`/`published` knobs are the doctype specifics
    (defaults: bet); `rskr.py` drives the same walk for riksdagsskrivelser."""
    seen = new = errors = 0
    newest_pub = None
    stopped = False
    for page in iter_pages(session, url, delay):
        for entry in _docs(page):
            seen += 1
            try:
                basefile = basefile_of(entry)
                state = currency(root, basefile, entry)
                pub = published(entry)
            except ValueError as exc:
                # a malformed feed entry (missing rm/beteckning or a broken
                # filbilaga) is recorded and skipped; it must not abort the
                # remaining walk (rule:errors-drive-retry-use-raise)
                errors += 1
                log("  %s: %s" % (typ, exc))
                continue
            if watermark is not None and watermark.should_stop(
                    state == "final", entry.get("datum")):
                stopped = True
                break
            if newest_pub is None and pub and entry.get("datum"):
                newest_pub = entry["datum"]   # newest-first => first published wins
            if state:
                continue
            try:
                fetch(session, root, entry, delay)
                new += 1
            except (requests.HTTPError, ValueError) as exc:
                # a counted, logged per-document failure (a 404'd filbilaga,
                # non-PDF bytes, or a descriptor field missing) that keeps the
                # watermark store dirty; it must not abort the remaining
                # ~161-riksmöte walk
                errors += 1
                log("  %s %s: %s" % (typ, basefile, exc))
        rep.update(seen, int(page["@traffar"]), scope=scope,
                   page=int(page["@sida"]), new=new)
        if stopped:
            break
    rep.done()
    return seen, new, errors, newest_pub


def harvest(root, *, typ, listing, fetch, currency, published, watermark,
            full=False, delay=0.5, log=print, riksmote=None):
    """The doctype-agnostic dokumentlista harvest (`sync` below documents the
    lifecycle in bet terms; rskr.py is the second driver).

    Backfilled -- everything missing downloaded -- when `--full` is given or
    the corpus has never been cleanly walked (no watermark date yet: a first
    run, or one crashed partway). Because the API caps any one listing at ~10k
    docs, an un-narrowed backfill walks riksmöte by riksmöte, newest to oldest
    (see `riksmoten`). `riksmote` narrows the run to one riksmöte -- a partial
    view of the corpus, so it NEVER touches the watermark. Returns
    (seen, new)."""
    root = Path(root)
    session = make_session(USER_AGENT)
    rep = Reporter()
    kw = dict(typ=typ, fetch=fetch, currency=currency, published=published,
              delay=delay, log=log, rep=rep)
    if riksmote is not None:
        seen, new, _, _ = _walk(session, root, listing + "&rm=" + quote(riksmote),
                                watermark=None, scope=typ, **kw)
        return seen, new
    # a crashed run leaves {"last_harvest": null, "dirty": true}: still a
    # backfill, so key on the date, not on the file existing
    backfill = full or watermark.last_harvest is None
    watermark.begin()
    if backfill:
        seen = new = errors = 0
        newest_pub = None
        for value in riksmoten(newest_riksmote_year(session, listing)):
            s, n, e, pub = _walk(
                session, root, listing + "&rm=" + quote(value), watermark=None,
                scope="%s %s" % (typ, value), **kw)
            seen, new, errors = seen + s, new + n, errors + e
            newest_pub = newest_pub or pub   # walks run newest riksmöte first
    else:
        seen, new, errors, newest_pub = _walk(
            session, root, listing, watermark=watermark, scope=typ, **kw)
    # complete() advances the date even with errors (bounding how deep future
    # runs walk) but then leaves the store dirty: the next run walks past the
    # consecutive-hit stop down to the date-conclusive boundary and retries
    # the failed documents. A zero-item walk is indistinguishable from feed
    # rot and likewise stays dirty.
    watermark.complete(newest_pub, errors=errors if errors else int(seen == 0),
                       log=log)
    if errors:
        log("  %s: %d download error(s) -- the store stays dirty, so the next "
            "run re-walks down to the watermark boundary and retries them"
            % (typ, errors))
    return seen, new


def sync(root, full=False, delay=0.5, log=print, riksmote=None):
    """Download utskottsbetänkanden (doktyp=bet) into `root/bet/`.

    The walk drives the shared begin/complete watermark lifecycle
    (lib.harvest): the date advances even when some documents failed (the
    date-conclusive stop bounds how deep future runs walk), but errors -- or a
    crash before complete() -- leave the store *dirty*, so the next run
    disables the consecutive-hit stop, walks down to the date boundary, and
    naturally retries whatever was stranded (a partially-failed initial load
    is resumed, not mistaken for finished). Once caught up, later runs go
    incremental: one un-narrowed newest-first walk gated by the shared
    `HarvestWatermark` (new docs land at the top, well within the cap; a
    filbilaga attached to a doc already *past* the stop point surfaces only
    under `--full`, like edits to old regeringen docs). The saved watermark
    date is the newest *published* entry's datum -- a planned entry's future
    datum would erode the gate's safety margin (see `_walk`).
    `riksmote` narrows the run to one riksmöte (e.g. "2025/26", the API's `rm=`
    parameter) for dev/manual runs. Returns (seen, new)."""
    # per-source window (project convention): the dokumentlista datum sort
    # mixes planned-debate placeholders among published docs (see `_currency`),
    # so the gate needs generous slack; 20 consecutive hits / 14 days cover the
    # planning->print lag observed in the feed.
    watermark = HarvestWatermark(Path(root) / TYPE / WATERMARK,
                                 lookahead_limit=20, safety_days=14)
    return harvest(root, typ=TYPE, listing=LISTING, fetch=download_document,
                   currency=_currency, published=_published,
                   watermark=watermark, full=full, delay=delay, log=log,
                   riksmote=riksmote)
