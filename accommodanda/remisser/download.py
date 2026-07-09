"""Downloader for remiss (public referral) cases from regeringen.se/remisser/.

The listing at ``/remisser/`` is a plain paginated page (``?p=N#result``) of
``ul.list--block > li`` items -- the same DOM the forarbete listing uses. Each
item links a case page ``/remisser/YYYY/MM/<slug>/``; the **basefile is that
slug** (the document's own identifier at the publisher, not a synthetic one).

A case page carries the referral's metadata (title, diarienummer, publish/update
dates, deadline), a single "Remissinstanser" PDF listing who was *asked* to
answer, and -- once answers start arriving -- a "Remissvar" ``<ul>`` with one
``<li><a>`` per organisation that has *actually* answered. Only that list is
modelled as instances (`Remiss.svar`); the Remissinstanser PDF is one opaque
document, kept as a url. A "Genvägar" shortcut links the referred SOU/Ds's own
regeringen.se landing page -- matched against `lib.regeringen.TYPES` to
recover the canonical förarbete basefile, the load-bearing join to that vertical.

One download tree, layout.REMISSER_DOWNLOADED: ``downloaded/remisser/<slug>.json``
(the Remiss record, source of truth) beside its ``downloaded/remisser/<slug>/
<org-slug>.pdf`` answer PDFs (each immutable once posted).

`sync` runs two passes: discover new cases newest-first, stopping at the first
already-known slug (`--full` re-walks the whole listing, downloading only what
is missing), then re-poll every still-open case (deadline unknown, or within
GRACE_PERIOD of it) to pick up newly-arrived answers, and fetch any answer PDF
not yet cached. Any per-case fetch/parse failure -- an HTTP error, or a 200
response whose DOM doesn't match what `parse_case` expects (bot-challenge
interstitial, truncation) -- is recorded as a stub from the listing facts, so
the slug still exists on disk (the incremental stop condition) and pass 2 /
later runs keep re-polling it until it succeeds.
`sync_one` fetches
exactly one already-known case URL, bypassing the listing walk entirely -- the
`--only` escape hatch for grabbing one case's remissvar without touching the
rest of the (multi-thousand-page) archive.
"""

import json
import re
import time
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

from ..lib import compress, layout
from ..lib.net import BROWSER_UA, make_session, request
from ..lib.regeringen import BASE, TYPES, listing_items
from ..lib.util import Reporter, swedish_date
from .model import Remiss, Remissinstans, org_slug

LISTING = BASE + "/remisser/?p=%d#result"
GRACE_PERIOD = timedelta(days=21)   # keep re-polling this long past the deadline

HREFPAT = re.compile(r"^/remisser/\d{4}/\d{2}/")
RATTSLIGA_HREF = re.compile(r"^/rattsliga-dokument/")
SEGMENT = re.compile(r"^/rattsliga-dokument/([^/]+)/")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
PDF_SIZE = re.compile(r"\s*\(pdf[^)]*\)\s*$", re.IGNORECASE)   # "… (pdf 119 kB)"

# the deadline sentence is free text with two known phrasings; both name the
# date after "den", so match the cue then read the Swedish date out of the block
DEADLINE_CUE = re.compile(r"[Ss]ista dag att svara|senast den", re.IGNORECASE)


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


def _match_forarbete(href, text):
    """A Genvägar link -> {"typ", "basefile"} if it names a known förarbete type,
    else None. The href's first path segment picks the type; that type's
    identifier regex, applied to the *link text* (which is free of the remiss
    page's "Remiss av" noise), recovers the canonical basefile."""
    m = SEGMENT.match(href)
    if not m:
        return None
    for typ, (segment, _category, idre) in TYPES.items():
        if segment == m.group(1) and idre:
            hit = re.search(idre, text)
            if hit:
                return {"typ": typ, "basefile": hit.group(1)}
    return None


def _title_forarbete(title):
    """A förarbete cross-ref recovered straight from the case title when the page
    carries no "Genvägar" island at all (observed on real pages, e.g. a
    betänkande remiss whose title just names "... (SOU 2026:8)" with no shortcut
    link) -- every type's identifier regex is tried in turn against the title
    text, first match wins."""
    for typ, (_segment, _category, idre) in TYPES.items():
        if idre:
            hit = re.search(idre, title)
            if hit:
                return {"typ": typ, "basefile": hit.group(1)}
    return None


def _remitterat(soup, title):
    """The förarbete cross-refs from the "Genvägar"/"Genväg" island(s); when a
    page has none (some case pages omit it), fall back to the identifier named in
    the title itself -- the one piece of the referred document's identity every
    remiss page reliably carries."""
    out = []
    for h2 in soup.find_all("h2", class_="h-underlined"):
        if not h2.get_text(strip=True).startswith("Genväg"):
            continue
        for a in h2.parent.find_all("a", href=RATTSLIGA_HREF):
            ref = _match_forarbete(a["href"], a.get_text(" ", strip=True))
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
    """One listing page -> a descriptor per case, in page order (newest first):
    {basefile, title, url}."""
    return [{"basefile": href.rstrip("/").rsplit("/", 1)[-1],
             "title": text, "url": url}
            for _li, href, url, text in listing_items(html, HREFPAT)]


def parse_case(html, url):
    """A case detail page -> a Remiss (svar empty until answers exist)."""
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
    return Remiss(
        basefile=url.rstrip("/").rsplit("/", 1)[-1],
        titel=titel,
        url=url if url.endswith("/") else url + "/",
        dnr=dnr,
        departement=dep.get_text(strip=True) if dep else None,
        publicerad=_time_iso(dates.find("span", class_="published") if dates else None),
        uppdaterad=_time_iso(dates.find("span", class_="updated") if dates else None),
        sista_svarsdag=_deadline(soup),
        remitterat=_remitterat(soup, titel),
        remissinstanser_pdf=(BASE + remissinstanser[0][0]) if remissinstanser else None,
        svar=[Remissinstans(organisation=PDF_SIZE.sub("", text).strip(),
                            source_url=BASE + href)
              for href, text in _section_items(soup, "Remissvar:")])


# --------------------------------------------------------------------------
# harvest
# --------------------------------------------------------------------------

def _write_case(remiss):
    compress.write_download(layout.remisser_case(remiss.basefile),
                            json.dumps(remiss.to_dict(), ensure_ascii=False, indent=2))


def _is_open(remiss, today):
    """A case is still open (worth re-polling) when its deadline is unknown, or
    today is within GRACE_PERIOD of it."""
    if remiss.sista_svarsdag is None:
        return True
    return today <= date.fromisoformat(remiss.sista_svarsdag) + GRACE_PERIOD


def _merge(remiss, fresh):
    """Fold a re-fetch into the stored case: a recorded answer stays recorded
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
    ``downloaded/<case>/<slug>.pdf`` would silently overwrite one organisation's
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


def _fetch_pending(session, remiss, delay):
    """Fetch each answer PDF not yet cached (immutable once posted), flipping its
    `downloaded` flag. Returns the number newly fetched. Raises ValueError on an
    org_slug collision (see `_check_slugs`)."""
    _check_slugs(remiss)
    fetched = 0
    for inst in remiss.svar:
        if inst.downloaded:
            continue
        data = request(session, "GET", inst.source_url).content
        compress.write_download(layout.remisser_answer(remiss.basefile,
                                                       org_slug(inst.source_url)), data)
        inst.downloaded = True
        fetched += 1
        time.sleep(delay)
    return fetched


def sync_one(url, delay=0.5):
    """Fetch exactly one case by its regeringen.se URL, bypassing the listing walk
    entirely -- the `--only` escape hatch, so grabbing one already-known case's
    remissvar never requires an incremental (let alone full) sweep of the
    archive. Merges onto any existing record for that case (like `sync`'s second
    pass) and fetches every answer PDF not yet cached. Returns
    {"basefile", "svar", "fetched"}."""
    session = make_session(BROWSER_UA)
    url = url if url.endswith("/") else url + "/"
    remiss = parse_case(request(session, "GET", url).text, url)
    existing = layout.remisser_case(remiss.basefile)
    if compress.exists(existing):
        stored = Remiss.from_dict(json.loads(compress.read_text(existing)))
        _merge(stored, remiss)
        remiss = stored
    fetched = _fetch_pending(session, remiss, delay)
    _write_case(remiss)
    return {"basefile": remiss.basefile, "svar": len(remiss.svar), "fetched": fetched}


def sync(full=False, delay=0.5, log=print):
    """Harvest remiss cases into layout.REMISSER_DOWNLOADED (downloaded/remisser):
    each case's ``<slug>.json`` record beside its ``<slug>/`` answer-PDF dir.

    Pass 1 discovers new cases newest-first, stopping at the first slug already on
    disk (`full` re-walks the whole listing, downloading only what is missing).
    A case page that fails to fetch (404/500/connection error) or fails to parse
    (unexpected DOM -- a bot-challenge interstitial, a truncated response) is
    written as a *stub* record from the listing facts: the on-disk slug is the
    incremental stop condition, so newer slugs written in the same walk would
    otherwise hide the failed case from every later incremental run; as a stub
    (no deadline) it stays "open" and pass 2 / later runs re-poll it until a
    fetch succeeds.
    Pass 2 re-polls every still-open case (deadline unknown or within
    GRACE_PERIOD) to merge newly-arrived answers, and fetches any answer PDF not
    yet cached (across all cases, so a case that was already closed when first
    seen still gets its PDFs). A case whose answers collide on org_slug logs
    the collision and skips its PDF fetch this run (any merged scalar/svar
    updates are still written) -- an anomaly in that one case's data, not
    grounds to abort the sweep over every other case. Returns {"new", "failed",
    "repolled", "closed", "fetched"}."""
    cases = layout.REMISSER_DOWNLOADED
    session = make_session(BROWSER_UA)
    rep = Reporter()
    summary = {"new": 0, "failed": 0, "repolled": 0, "closed": 0, "fetched": 0}

    seen, page, stop = 0, 1, False
    while not stop:
        items = parse_listing(request(session, "GET", LISTING % page).text)
        if not items:
            break
        for item in items:
            seen += 1
            if compress.exists(layout.remisser_case(item["basefile"])):
                if not full:
                    stop = True
                    break
                continue
            # a bad response (HTTP error) or a malformed page (parse_case's
            # ValueError on unexpected DOM) must not abort the walk -- one
            # failed case would otherwise silently swallow every case behind
            # it once newer slugs land on disk (rule:no-catch-log-continue:
            # recorded as a stub below, re-polled every later run)
            try:
                remiss = parse_case(
                    request(session, "GET", item["url"]).text, item["url"])
            except (requests.RequestException, ValueError) as exc:
                log("  remiss %s: %s (stub written, re-polled next run)"
                    % (item["basefile"], exc))
                remiss = Remiss(basefile=item["basefile"], titel=item["title"],
                                url=item["url"])
                summary["failed"] += 1
            else:
                summary["new"] += 1
            _write_case(remiss)
            time.sleep(delay)
        rep.update(seen, None, scope="remisser", page=page, new=summary["new"])
        page += 1
        time.sleep(delay)
    rep.done()

    today = date.today()
    for path in sorted(compress.glob(cases, "*.json")):
        remiss = Remiss.from_dict(json.loads(compress.read_text(path)))
        changed = False
        if _is_open(remiss, today):
            try:
                fresh = parse_case(
                    request(session, "GET", remiss.url).text, remiss.url)
            except (requests.RequestException, ValueError) as exc:
                log("  repoll %s: %s" % (remiss.basefile, exc))
                continue
            summary["repolled"] += 1
            changed = _merge(remiss, fresh)
        else:
            summary["closed"] += 1
        # a duplicate org_slug is this case's own data anomaly -- it must not
        # abort the whole sweep over every other case still to be repolled
        # (rule:no-catch-log-continue: recorded via the log, retried next run).
        # Pre-checked here so the catch is exactly as wide as that one failure;
        # any other error out of the fetch loop still aborts loudly.
        try:
            _check_slugs(remiss)
        except ValueError as exc:
            log("  fetch %s: %s (retried next run)" % (remiss.basefile, exc))
            fetched = 0
        else:
            fetched = _fetch_pending(session, remiss, delay)
        summary["fetched"] += fetched
        if changed or fetched:
            _write_case(remiss)
    return summary


def list_basefiles():
    """Every case basefile (slug) on disk, sorted -- not instance basefiles."""
    return sorted(json.loads(compress.read_text(p))["basefile"]
                  for p in compress.glob(layout.REMISSER_DOWNLOADED, "*.json"))
