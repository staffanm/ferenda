"""Reusable harvest engine for agency författningssamlingar.

~100 agencies, but only a few *publishing architectures*. An agency is
:class:`Agency` config naming an :class:`Architecture`; the harvest loop
(:func:`harvest`) is architecture-agnostic -- incremental newest-first with a
``.complete`` backfill marker, atomic writes, a politeness delay and a
``Reporter``, all lifted from ``forarbete.download.sync`` and shared.

An architecture is two callables over that shared loop:

  * ``enumerate(session, agency) -> Iterator[DocRef]`` -- *how to list the
    agency's documents*. The variable axis: a full HTML index (FFFS), a
    paginated index, a JSON/AJAX filter endpoint (regeringen.se-style), year
    pages, or a form-POST listing. Each new shape is one new ``enumerate``.
  * ``resolve(session, agency, ref, root) -> record`` -- *item to stored files
    + record*. Mostly shared: :func:`resolve_landing` fetches a landing page,
    classifies the PDFs it hangs (original / konsoliderad / amendment / memo /
    attachment -- :func:`classify_file`, Swedish-convention rules common across
    agencies) and downloads them. A direct-PDF agency would use a thinner
    resolve.

Only the architecture FFFS needs is implemented (``indexed`` enumerate +
``resolve_landing``); the others are named extension points, added when an
agency that needs one is built -- not speculatively (the rewrite's
"don't design the horizontal layer from one source" rule).
"""

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..lib.net import make_session, request
from ..lib.util import Reporter

# the documents are public government records; we present a normal browser UA
# (several agency sites 403 bare clients) and stay polite with delays.
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


@dataclass
class DocRef:
    """One enumerable document: a stable basefile (``fffs/2013:10``), its
    human identifier, and the URL to resolve it (a landing page, or for an
    API-direct agency the file itself). ``extra`` carries an agency-neutral
    payload for :func:`resolve_direct` (an enumeration that already knows the
    file URLs): ``{regulation_url, consolidations:[{url}],
    amendments:[{identifier,url}], title, source_url}``."""
    basefile: str
    identifier: str
    url: str
    title: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class Skip:
    """A non-fatal hole in an enumeration. These agency indexes are flaky and
    badly maintained -- a per-year page 500s, one sitemap of several times out,
    a 'show all' list is briefly down -- so a multi-page enumerator yields this
    instead of a :class:`DocRef` when it cannot fetch one page but can keep
    walking the rest. :func:`harvest` logs it and withholds the ``.complete``
    marker, so the missed page is retried on the next run rather than silently
    lost. (An *expected* empty page -- a year with no regulations -- is not a
    Skip; the enumerator just yields nothing for it.)"""
    reason: str


@dataclass
class Agency:
    """A författningssamling as configuration over the shared engine. ``enumerate``
    and ``resolve`` name the architecture; everything else is per-agency data the
    architecture reads (URLs, the org behind ``dcterms:publisher``, selectors)."""
    fs: str                                # författningssamling code, "fffs"
    name: str                              # "Finansinspektionen"
    publisher: str                         # issuing-org label / uri
    base_url: str                          # scheme://host (for relative hrefs)
    index_url: str                         # the listing entry point
    enumerate: Callable                    # (session, agency) -> Iterator[DocRef]
    resolve: Callable                      # (session, agency, ref, root) -> record
    params: dict = field(default_factory=dict)   # architecture-specific config
    user_agent: str | None = None          # override (a few sites gate on UA)
    headers: dict | None = None            # extra request headers (e.g. Accept-Language)


# --------------------------------------------------------------------------
# shared I/O (same atomic-write contract as the other downloaders)
# --------------------------------------------------------------------------

def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def slug(basefile):
    """Filesystem-safe form of a basefile; the true identifier lives in the
    record JSON, so this only has to be unique and stable."""
    return basefile.replace("/", "-").replace(":", "-").replace(" ", "_")


def record_path(root, fs, basefile):
    return Path(root) / fs / (slug(basefile) + ".json")


def absolute(base_url, href):
    """Absolute URL for a possibly-relative href. Query strings are kept -- some
    document stores need them (a-w2m's ``?id=&res=``); a harmless tracking query
    (SSMFS's ``?searchQuery=``) or an explicit ``:443`` does no damage."""
    return urljoin(base_url + "/" if not base_url.endswith("/") else base_url, href)


# --------------------------------------------------------------------------
# file-role classification (shared Swedish-convention rules)
# --------------------------------------------------------------------------

# A regulation's landing page hangs several PDFs; a classifier maps each to its
# role + the file's own (year, lopnummer). The role conventions ("konsoliderad
# version", "Beslutspromemoria", "Bilaga"…) recur across agencies, but *where*
# the signal lives varies -- link text (FFFS, SSMFS), a section heading (KIFS,
# Sitevision), or the PDF filename (NFS) -- so the classifier is the agency's
# (Agency.params["classify"], default text-based). It takes the <a> element so a
# section/href classifier can read the surrounding context, and returns
# ``(role, ars, lop)`` or ``None``. Roles:
#   regulation     the original grundförfattning text
#   consolidation  the konsoliderad version (the in-force text)
#   amendment      an ändringsförfattning's own text (a later FS number)
#   memo           beslutspromemoria / konsekvensutredning (related, not law)
#   attachment     anvisning / blankett / bilaga
RE_KONSOLIDERAD = re.compile(r"konsolider", re.IGNORECASE)
RE_MEMO = re.compile(r"beslutsprom|besluts-?pm|konsekvensutredning", re.IGNORECASE)
RE_ATTACHMENT = re.compile(r"anvisning|blankett|bilaga|mall|vägledning", re.IGNORECASE)
RE_FS_NUMBER = re.compile(r"\b([A-ZÅÄÖ-]+FS)\s*(\d{4}):(\d+)", re.IGNORECASE)
RE_SLUG_NUMBER = re.compile(r"[a-zåäö]+[-_ ]?(\d{4})[-_ ]?(\d{1,3})(?:\D|$)", re.IGNORECASE)


def _own_number(text, fs):
    """The (year, lopnummer) an FS designation in `text` names, or (None, None)."""
    m = RE_FS_NUMBER.search(text)
    if m and m.group(1).upper() == fs.upper():
        return m.group(2), str(int(m.group(3)))
    return None, None


def classify_file(a, fs, base_ars, base_lop):
    """Text-based classifier (FFFS, SSMFS): role + number from the link text."""
    text = a.get_text(" ", strip=True)
    ars, lop = _own_number(text, fs)
    if RE_MEMO.search(text):
        return ("memo", ars, lop)
    if RE_KONSOLIDERAD.search(text):
        return ("consolidation", ars or base_ars, lop or base_lop)
    if RE_ATTACHMENT.search(text):
        return ("attachment", ars, lop)
    if ars is None:
        return None
    return (("regulation" if (ars, lop) == (base_ars, base_lop) else "amendment"), ars, lop)


def classify_section(a, fs, base_ars, base_lop):
    """Heading-based classifier (KIFS, Sitevision): role from the nearest
    preceding <h2>/<h3>, the file's number from the link text."""
    text = a.get_text(" ", strip=True)
    ars, lop = _own_number(text, fs)
    if RE_MEMO.search(text):
        return ("memo", ars, lop)
    head = a.find_previous(["h2", "h3"])
    head = head.get_text(" ", strip=True).lower() if head else ""
    if "konsolider" in head:
        return ("consolidation", ars or base_ars, lop or base_lop)
    if "grundför" in head:
        return ("regulation", base_ars, base_lop)
    if "ändring" in head:
        return ("amendment", ars, lop) if ars else None
    if RE_ATTACHMENT.search(text):
        return ("attachment", ars, lop)
    return None


def classify_href(a, fs, base_ars, base_lop):
    """Filename-based classifier (NFS, ELSÄK-FS, PTSFS): role + number from the
    PDF href slug (``nfs-2014-29.pdf``, ``…-konsoliderad.pdf``, ``andring-…``)."""
    href = a.get("href", "").lower()
    name = href.rsplit("/", 1)[-1]
    m = RE_SLUG_NUMBER.search(name)
    if "konsekvensutred" in href:                 # impact assessment, not the law
        return None
    if "konsolider" in href:
        return ("consolidation", base_ars, base_lop)
    if not m:
        return None
    ars, lop = m.group(1), str(int(m.group(2)))
    if re.search(r"\bandring|\bändring", name):
        return ("amendment", ars, lop)
    return (("regulation" if (ars, lop) == (base_ars, base_lop) else "amendment"), ars, lop)


def classify_single(a, fs, base_ars, base_lop):
    """Trivial classifier for landing pages that hang exactly the base
    regulation's PDF (no per-file type signal); every kept file is the
    regulation. Used where the type axis lives on the index, not the landing
    (STEMFS)."""
    return ("regulation", base_ars, base_lop)


def classify_default_regulation(a, fs, base_ars, base_lop):
    """Like :func:`classify_file`, but a PDF that is plainly *not* a memo /
    attachment / consolidation is taken to BE the base regulation -- even when
    its designation can't be read (a UUID filename) or carries a predecessor
    prefix that differs from the agency's fs (MSBFS hosts old SÄI/SÄIFS texts,
    the number right but the prefix wrong, so :func:`_own_number` rejects it).
    Safe only where a landing hangs the one regulation plus, at most, keyworded
    companions (MSBFS: regulation + 'Konsekvensutredning')."""
    text = a.get_text(" ", strip=True)
    if RE_MEMO.search(text):
        return ("memo", *_own_number(text, fs))
    if RE_KONSOLIDERAD.search(text):
        ars, lop = _own_number(text, fs)
        return ("consolidation", ars or base_ars, lop or base_lop)
    if RE_ATTACHMENT.search(text):
        return ("attachment", *_own_number(text, fs))
    return ("regulation", base_ars, base_lop)


# --------------------------------------------------------------------------
# the shared landing-page resolver (used by every landing-page architecture)
# --------------------------------------------------------------------------

# Roles whose PDF we actually download: the in-force text a reader needs. The
# konsoliderad version folds in every amendment, so the base text is
# regulation + consolidation. Amendments (themselves regulations), beslutsprome-
# morior and attachments are recorded as *references* (identifier + href) -- the
# full amendment graph without the download cost -- and a later pass can fetch an
# amendment body on demand. Overridable per agency (params['download_roles']).
DOWNLOAD_ROLES = frozenset({"regulation", "consolidation"})


def resolve_landing(session, agency, ref, root, delay=0.5):
    """Fetch ``ref``'s landing page, classify the files it hangs, download the
    in-force text (regulation + consolidation) and record the rest as
    references, then write the record JSON + landing HTML. Architecture-generic:
    the only per-agency knobs are the PDF-link selector (``params['pdf_select']``),
    the classification (``params['classify']``) and ``params['download_roles']``.

    Returns the stored record (also written to disk)."""
    fs = agency.fs
    arsutgava, lopnummer = ref.basefile.split("/", 1)[1].split(":")
    landing = request(session, "GET", ref.url).text
    soup = BeautifulSoup(landing, "html.parser")
    classify = agency.params.get("classify", classify_file)
    download_roles = agency.params.get("download_roles", DOWNLOAD_ROLES)

    files = {"regulation": None, "consolidation": [], "amendment": [],
             "memo": [], "attachment": []}
    seen = set()
    for a in soup.select(agency.params.get("pdf_select", 'a[href$=".pdf"]')):
        href = a.get("href")
        if not href or href in seen:
            continue
        seen.add(href)
        result = classify(a, fs, arsutgava, lopnummer)
        if result is None:
            continue
        role, ars, lop = result
        identifier = "%s %s:%s" % (fs.upper(), ars, lop) if ars else None
        # resolve the PDF href against the landing page's own URL (its host may
        # differ from base_url, e.g. STEMFS's a-w2m document store); keep the
        # query string -- some document stores need it (a-w2m's ?id=&res=).
        ref_entry = {"text": a.get_text(" ", strip=True), "identifier": identifier,
                     "url": urljoin(ref.url, href)}
        if role not in download_roles:              # reference only, not fetched
            if identifier is None or not any(e.get("identifier") == identifier
                                             for e in files[role]):
                files[role].append(ref_entry)       # dedup repeated links by identifier
            continue
        data = request(session, "GET", ref_entry["url"]).content
        if data[:4] != b"%PDF":                    # the link wasn't a PDF after all
            continue
        name = "%s-%s.pdf" % (slug(ref.basefile), role) if role == "regulation" \
            else "%s-%s-%s_%s.pdf" % (slug(ref.basefile), role,
                                      ars or "x", lop or len(files[role]))
        write_atomic(Path(root) / fs / name, data)
        entry = dict(ref_entry, name=name)
        if role == "regulation":
            files["regulation"] = entry
        else:
            files[role].append(entry)
        time.sleep(delay)

    write_atomic(Path(root) / fs / (slug(ref.basefile) + ".html"), landing)
    record = {
        "fs": fs, "basefile": ref.basefile, "identifier": ref.identifier,
        "title": ref.title, "publisher": agency.publisher,
        "url": ref.url, "files": files,
    }
    write_atomic(record_path(root, fs, ref.basefile),
                 json.dumps(record, ensure_ascii=False, indent=2))
    return record


def resolve_direct(session, agency, ref, root, delay=0.5):
    """Resolve an agency that publishes the document URLs *in the listing* (an
    API-direct source, e.g. Boverket -- no landing page to scrape). Reads
    ``ref.extra`` (the agency-neutral payload an API enumerator fills),
    downloads the in-force text (regulation + consolidation) and records
    amendments as references. The shared resolve for any source whose
    enumeration already carries the file URLs."""
    fs = agency.fs
    extra = ref.extra
    files = {"regulation": None, "consolidation": [], "amendment": [],
             "memo": [], "attachment": []}

    def fetch_pdf(url, name):
        data = request(session, "GET", url).content
        if data[:4] != b"%PDF":
            return None
        write_atomic(Path(root) / fs / name, data)
        time.sleep(delay)
        return name

    if extra.get("regulation_url"):
        name = fetch_pdf(extra["regulation_url"], "%s-regulation.pdf" % slug(ref.basefile))
        if name:
            files["regulation"] = {"name": name, "url": extra["regulation_url"],
                                   "identifier": ref.identifier}
    for i, c in enumerate(extra.get("consolidations", [])):
        if not c.get("url"):
            continue
        name = fetch_pdf(c["url"], "%s-consolidation-%d.pdf" % (slug(ref.basefile), i))
        if name:
            files["consolidation"].append({"name": name, "url": c["url"]})
    files["amendment"] = [{"identifier": a.get("identifier"), "url": a.get("url")}
                          for a in extra.get("amendments", [])]
    record = {
        "fs": fs, "basefile": ref.basefile, "identifier": ref.identifier,
        "title": ref.title or extra.get("title"), "publisher": agency.publisher,
        "url": extra.get("source_url") or ref.url, "files": files,
    }
    write_atomic(record_path(root, fs, ref.basefile),
                 json.dumps(record, ensure_ascii=False, indent=2))
    return record


# --------------------------------------------------------------------------
# reusable enumerators (the variable axis: *how to list an agency's documents*)
# --------------------------------------------------------------------------
# Each yields DocRefs over the shared harvest loop. A new agency picks one and
# supplies its params; a genuinely new site shape is one new enumerator here.

RE_COLON_NUMBER = re.compile(r"(\d{4}):(\d+)")


def _ref(agency, ident_text, href, seen, title=None, direct=False):
    """Build a DocRef for a base regulation from a designation string ('NFS
    2026:6' / 'SSMFS 2018:1 …') and an href. The (year, lopnummer) come from the
    first ``YYYY:N`` in the text. When ``direct``, the href *is* the PDF (no
    landing) so it goes into ``extra`` for :func:`resolve_direct`. Returns None
    on no number or an already-seen base (dedup keeps one DocRef per base)."""
    # Find the regulation's own number, most reliable signal first:
    #  1. an FS-prefixed designation in the text ("RGKFS 2015:2") -- skips an SFS
    #     reference in a title ("med stöd av förordning (2006:1097)");
    #  2. for a direct (PDF-href) agency, the filename slug ("rgkfs_2015_2.pdf")
    #     -- when the title carries no designation at all;
    #  3. a bare "YYYY:N" in the text, as a last resort.
    fsm = RE_FS_NUMBER.search(ident_text)
    slugm = RE_SLUG_NUMBER.search(href.rsplit("/", 1)[-1].split("?")[0]) if direct else None
    if fsm:
        arsutgava, lopnummer = fsm.group(2), str(int(fsm.group(3)))
    elif slugm:
        arsutgava, lopnummer = slugm.group(1), str(int(slugm.group(2)))
    else:
        bare = RE_COLON_NUMBER.search(ident_text)
        if not bare:
            return None
        arsutgava, lopnummer = bare.group(1), str(int(bare.group(2)))
    basefile = "%s/%s:%s" % (agency.fs, arsutgava, lopnummer)
    if basefile in seen:
        return None
    seen.add(basefile)
    url = absolute(agency.base_url, href)
    extra = {"regulation_url": url, "title": title, "source_url": agency.index_url} \
        if direct else {}
    return DocRef(basefile=basefile,
                  identifier="%s %s:%s" % (agency.fs.upper(), arsutgava, lopnummer),
                  url=url, title=title, extra=extra)


def indexed_enumerate(session, agency):
    """HTML index page(s) list every (base) regulation. params: ``link_select``
    (CSS selector for the anchors); ``index_urls`` (a list, for a per-year index;
    defaults to the single ``index_url``); ``direct`` (the anchor href is the PDF
    itself, not a landing page); ``skip_re`` (drop anchors whose text matches,
    e.g. companion 'Beslutspromemoria' PDFs); ``optional_pages`` (a per-year index
    where a year with no regulations simply 404s -- skip it rather than abort)."""
    p = agency.params
    direct = p.get("direct", False)
    skip = re.compile(p["skip_re"]) if p.get("skip_re") else None
    method = "POST" if p.get("post_data") else "GET"      # some "show all" lists POST
    seen = set()
    multi = len(p.get("index_urls", [agency.index_url])) > 1
    for url in p.get("index_urls", [agency.index_url]):
        try:
            response = request(session, method, url, data=p.get("post_data"))
        except requests.exceptions.HTTPError as exc:
            if p.get("optional_pages") and getattr(exc.response, "status_code", None) == 404:
                continue                       # a year with no regulations -- no page
            if multi:                          # one bad page in a per-year index
                yield Skip("%s: %r" % (url, exc))
                continue
            raise                              # the sole index page -- a real break
        except requests.exceptions.RequestException as exc:
            if multi:
                yield Skip("%s: %r" % (url, exc))
                continue
            raise
        soup = BeautifulSoup(response.text, "html.parser")
        for a in soup.select(p["link_select"]):
            text = a.get_text(" ", strip=True)
            if skip and skip.search(text):
                continue
            docref = _ref(agency, text, a.get("href", ""), seen,
                          title=text if direct else None, direct=direct)
            if docref:
                yield docref
        time.sleep(0.3)


def paginated_enumerate(session, agency):
    """The index is paged HTML at ``page_url.format(page=N)`` (newest-first);
    walk until a page yields no rows. params: ``page_url``, ``row_select``."""
    seen = set()
    page = 1
    while True:
        try:
            response = request(session, "GET", agency.params["page_url"].format(page=page))
        except requests.exceptions.RequestException as exc:
            yield Skip("page %d: %r" % (page, exc))   # cannot trust paging past it
            return
        soup = BeautifulSoup(response.text, "html.parser")
        rows = soup.select(agency.params["row_select"])
        if not rows:
            return
        for a in rows:
            docref = _ref(agency, a.get_text(" ", strip=True), a.get("href", ""), seen)
            if docref:
                yield docref
        page += 1
        time.sleep(0.5)


def json_enumerate(session, agency):
    """The index is a JSON search API (the whole corpus in one call). params:
    ``api_url``, ``unwrap`` (an outer wrapper key, e.g. 'searchModel'),
    ``results_key`` (default 'results'), ``id_field``, ``url_field``,
    ``title_field``, ``direct`` (``url_field`` is the PDF itself, not a landing)."""
    p = agency.params
    direct = p.get("direct", False)
    data = request(session, "GET", p["api_url"], parse_json=True)
    if p.get("unwrap"):
        data = data[p["unwrap"]]
    seen = set()
    for r in data.get(p.get("results_key", "results"), []):
        docref = _ref(agency, str(r[p["id_field"]]), r[p["url_field"]], seen,
                      title=r.get(p.get("title_field", "heading")), direct=direct)
        if docref:
            yield docref


def sitemap_enumerate(session, agency):
    """The index is XML sitemap(s) of landing-page URLs. params: ``sitemaps``
    (list of sitemap URLs), ``loc_filter`` (substring a doc <loc> must contain),
    ``id_from_loc`` (regex with year+lopnummer groups over the <loc>). Used where
    the site has no scrapable list but a complete sitemap (STAFS)."""
    p = agency.params
    idre = re.compile(p["id_from_loc"])
    seen = set()
    for sm in p["sitemaps"]:
        try:
            xml = request(session, "GET", sm).text
        except requests.exceptions.RequestException as exc:
            yield Skip("%s: %r" % (sm, exc))    # one sitemap of several is down
            continue
        for loc in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", xml):
            if p.get("loc_filter") and p["loc_filter"] not in loc.lower():
                continue
            m = idre.search(loc)
            if not m:
                continue
            docref = _ref(agency, "%s:%s" % (m.group(1), m.group(2)), loc, seen)
            if docref:
                yield docref


# --------------------------------------------------------------------------
# the harvest loop (architecture-agnostic; generalized forarbete.sync)
# --------------------------------------------------------------------------

def _guarded_enumerate(agency, session, log):
    """Iterate an agency's enumerator so that an exception escaping it (a
    single-call API or index page that died outright -- the listing endpoint is
    down, returns malformed JSON, 403s) ends the walk with a :class:`Skip`
    instead of aborting the whole run. Multi-page enumerators yield their own
    :class:`Skip` for individual bad pages and keep going; this catches whatever
    they let through. Either way the agency is left incomplete and retried."""
    walk = iter(agency.enumerate(session, agency))
    while True:
        try:
            yield next(walk)
        except StopIteration:
            return
        except Exception as exc:           # the index endpoint itself failed
            yield Skip("enumeration aborted: %r" % exc)
            return


def harvest(agency, root, full=False, only=None, limit=None, delay=0.5, log=print):
    """Harvest one agency.

    Backfill (walk the whole index, download what is missing) on ``--full`` or
    when the agency has never cleanly completed (no ``.complete`` marker -- a
    first or interrupted run). Once complete, later runs go incremental:
    enumeration is newest-first, so we stop at the first document already on
    disk. ``only`` (a basefile) fetches just that one. Returns ``(seen, new)``."""
    session = make_session(agency.user_agent or USER_AGENT)
    if agency.headers:
        session.headers.update(agency.headers)
    marker = Path(root) / agency.fs / ".complete"
    backfill = full or not marker.exists()
    seen = new = errors = 0
    done = False
    rep = Reporter()
    walk = _guarded_enumerate(agency, session, log)
    for ref in walk:
        if isinstance(ref, Skip):          # a page we could not fetch; logged below
            errors += 1
            log("  %s enumerate: %s" % (agency.fs, ref.reason))
            continue
        seen += 1
        if only is not None:
            if ref.basefile != only:
                continue
            agency.resolve(session, agency, ref, root, delay)
            new, done = 1, True
            break
        if record_path(root, agency.fs, ref.basefile).exists():
            if full:
                pass                   # re-resolve: refresh new amendments/consolidations
            elif backfill:
                continue               # resume an interrupted first walk; keep what's there
            else:
                done = True            # incremental: newest-first => the rest is older
                break
        try:
            agency.resolve(session, agency, ref, root, delay)
            new += 1
        except Exception as exc:       # one bad doc must not abort the walk
            errors += 1
            log("  %s %s: %s" % (agency.fs, ref.basefile, exc))
        rep.update(seen, None, scope=agency.fs, new=new)
        if limit and new >= limit:
            done = True
            break
        time.sleep(delay)
    if not done and not only and errors == 0:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("")          # clean full walk -> later runs incremental
    rep.done()
    return seen, new


def list_basefiles(root, fs):
    return sorted(json.loads(p.read_text())["basefile"]
                  for p in (Path(root) / fs).glob("*.json"))
