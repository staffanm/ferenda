"""Per-agency download architectures for the författningssamlingar.

~100 agencies, but only a few *publishing architectures*. An agency is
:class:`Agency` config naming an architecture; the actual download loop lives in
:func:`lib.harvest.walk` (architecture-agnostic -- incremental newest-first
behind a ``HarvestWatermark`` gate, atomic writes and a progress ``Reporter``).
:func:`harvest` here just wires one agency's callables onto that shared engine.

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

Three ``enumerate`` shapes are implemented (``indexed``/``paginated``/``json``,
plus bespoke per-agency enumerators) and two ordinary HTTP
``resolve`` shapes (``resolve_landing``, ``resolve_direct`` -- the listing
anchor already *is* the PDF). Browser-protected sources supply the same two
seams but select the Camoufox transport in their ``Agency``
config. New shapes are added only when an agency needs one
(rule:no-speculative-code).
"""

import re
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..lib import compress, datasets
from ..lib.browser import CamoufoxBrowser
from ..lib.errors import UpstreamChanged
from ..lib.harvest import HarvestWatermark, ItemKey, Skip, walk, write_record
from ..lib.net import BROWSER_UA as USER_AGENT
from ..lib.net import (
    is_not_found,
    make_http2_session,
    make_session,
    request,
    set_deadline,
)
from ..lib.util import basefile_slug as slug
from ..lib.util import document_extension, fold_swedish, record_path


@dataclass
class DocRef:
    """One enumerable document: a stable basefile (``fffs/2013:10``), its
    human identifier, and the URL to resolve it (a landing page, or for an
    API-direct agency the file itself). ``extra`` carries an agency-neutral
    payload for :func:`resolve_direct` (an enumeration that already knows the
    file URLs): ``{regulation_url, consolidations:[{url}],
    amendments:[{identifier,url}], title, source_url}``.

    ``fs`` is the författningssamling this one document belongs to, which is
    normally ``agency.fs`` but need not be: an agency that has taken over a
    renamed/disbanded agency's samling lists several författningssamlingar on one
    page (see ``fs_from_designation`` in :func:`ref`), and each document is
    stored and identified under *its own* fs, read from here by the resolvers and
    the downloaded-check. ``None`` means "use ``agency.fs``"."""
    basefile: str
    identifier: str
    url: str
    title: str | None = None
    extra: dict = field(default_factory=dict)
    fs: str | None = None


@dataclass
class Agency:
    """A publisher's författningssamling as configuration over the shared engine.
    ``enumerate`` and ``resolve`` name the architecture; everything else is
    per-agency data the architecture reads (URLs, the org behind
    ``dcterms:publisher``, selectors). ``enumerate``/``resolve`` are None for a
    **closed** författningssamling (SOSFS): a static series with no live
    harvester, its documents already in the corpus; :func:`download.sync` skips
    it as a no-op.

    One entry is one *scope* -- one listing to walk -- not necessarily one
    samling. HSLF-FS is issued into by seven agencies off six sites, so it is
    six entries sharing ``fs="hslffs"`` and differing in ``scope``."""
    fs: str                                # författningssamling code, "fffs"
    name: str                              # "Finansinspektionen"
    publisher: str                         # issuing-org label / uri
    base_url: str                          # scheme://host (for relative hrefs)
    index_url: str                         # the listing entry point
    scope: str | None = None               # the registry key and CLI scope name;
                                           # None = `fs`, the common case where one
                                           # agency owns one samling. Several
                                           # publishers issue into HSLF-FS, each off
                                           # its own site, so they share `fs` and are
                                           # told apart here ("hslffs-sos", "hslffs-ivo")
    enumerate: Callable | None = None      # (session, agency) -> Iterator[DocRef]; None = closed series
    resolve: Callable | None = None        # (session, agency, ref, root) -> record; None = closed series
    params: dict = field(default_factory=dict)   # architecture-specific config
    user_agent: str | None = None          # override (a few sites gate on UA)
    headers: dict | None = None            # extra request headers (e.g. Accept-Language)
    designation: str | None = None         # printed FS prefix ("HSLF-FS") when != fs.upper()
    http2: bool = False                    # use the HTTP/2 client (Cloudflare front that 403s HTTP/1.1: KKVFS)
    browser: bool = False                  # Camoufox instead of an HTTP session (F5: SKVFS/MTFS)
    browser_pace: float = 0.0              # minimum seconds between navigations (rate-limited fronts)


def fs_code(designation):
    """The samling slug a printed designation names: lowercased and stripped of
    every separator ("HSLF-FS" -> ``hslffs``, "ELSÄK-FS" -> ``elsakfs``,
    "RA-MS" -> ``rams``). The one spelling of that rule -- :func:`ref` mints a
    routed document's fs with it, the HSLF-FS scopes file every document with
    it, and `parse._fs_key` folds a citation's designation with it before the
    registry's own designation->slug rows get their say."""
    return re.sub(r"[^0-9a-zåäö]", "", designation.lower())


# --------------------------------------------------------------------------
# designations: the one reader of a printed FS number
# --------------------------------------------------------------------------

# Characters a publisher's markup and a PDF's text layer put *inside* a
# designation, where every pattern that reads one would otherwise have to know
# about them: a zero-width space or a soft hyphen between the year and the
# lopnummer (Energimyndigheten's listing hides 16 documents that way), a
# non-breaking space in an anchor's text (Kammarkollegiet), an en dash for the
# hyphen (RA-FS in an OCR layer), a space after the colon ("SKVFS 2013: 18",
# "1977: 1"). Removed once, here, rather than spelled again in each pattern.
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"), None)
_SPACES = dict.fromkeys(map(ord, "\u00a0\u2007\u202f"), " ")
_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2212"), "-")
RE_COLON_SPACE = re.compile(r"(?<=\d):[ \t]+(?=\d)")
# a designation whose number is set with a hyphen rather than a colon
# ("HSLF-FS 2023-3"). Anchored on the series suffix, and refused when more
# digits follow, so a date ("HSLF-FS 2023-03-15") is left alone
RE_HYPHEN_NUMBER = re.compile(r"((?:FS|FA|MS|AR|BS)\s*\d{4})-(\d{1,3})(?![\d-])")


def normalise(text):
    """`text` with the typography that hides a designation from a pattern
    removed: invisible characters dropped, the space variants and dash variants
    folded to a plain space and hyphen, and the space after a number's colon
    closed. Applied to the strings a designation is read out of -- a listing
    row, an href, a title, a masthead -- never to a body, whose spacing is the
    document's own."""
    if not text:
        return text
    return RE_HYPHEN_NUMBER.sub(
        r"\1:\2", RE_COLON_SPACE.sub(":", text.translate(_INVISIBLE)
                                     .translate(_SPACES).translate(_DASHES)))


# The vertical's one designation vocabulary: an agency prefix and its series
# suffix. Two patterns read it -- the printed designation and the one a
# document store writes into a filename -- so it is named once. The suffix
# alternation is what a samling's name can end in: "FS" for a
# författningssamling, "FA" for the two föreskrifter-och-allmänna-råd series
# (ESVFA, STKFA), "MS" for RA-MS, "AR" for BFNAR and Naturvårdsverkets pre-1994
# AR, "BS" for LBS. Requiring "FS" left every one of the others unreadable: 53
# RA-MS repeals and every BFNAR amendment link were empty. That alternation has
# grown once already, and a second copy of it would not have grown with it.
# The prefix keeps its printed case rather than being uppercased, because six
# series are spelled mixed ("SiSFS", "FoHMFS", "JvSFS", "AgVFS"); matching only
# all-capitals lost their relations too.
FS_PREFIX = r"[A-ZÅÄÖ][A-ZÅÄÖa-zåäö]{0,9}(?:-| )?(?:FS|FA|MS|AR|BS)"
# the designation as a page prints it, colon and all ("TRMFS 2017:2")
RE_DESIGNATION = re.compile(r"\b(%s)\s*(\d{4}):(\d+)" % FS_PREFIX)
# a designation a document store puts in a *filename*, where the number's colon
# is written as the separator the filesystem allows ("UPPHÄVD_TRMFS 2017_2.pdf").
# Read only when the row's visible text names no designation at all.
RE_FILE_DESIGNATION = re.compile(
    r"(%s)[ _-]*(\d{4})[:._ -](\d{1,3})(?:\D|$)" % FS_PREFIX)

# What tells a listing row that the designation beside it is *another*
# document's: an ändringsförfattning and a repeal both name their target in
# their own title. The first designation after one of these belongs to that
# target; a later one is the document speaking about itself again
# ("Föreskrifter om upphävande av MPRTFS 2019:3 om mediestöd (MPRTFS 2021:1)").
RE_OTHERS_NEXT = re.compile(r"ändring(?:ar)?\s+(?:i|av)\b|upphäv(?:ande\s+av|s|er)\b"
                            r"|ersätter\b", re.IGNORECASE)
# and what says the opposite -- an omtryck's row names the base it reprints
# first and its own number last ("TVFS 2015:1 omtryckt genom … TVFS 2019:1")
RE_OWN_NEXT = re.compile(r"omtryck(?:t|et)?\s+(?:genom|av)\b", re.IGNORECASE)
# a filename slug's number ("rgkfs_2015_2.pdf"). Anchored on a year, so an
# opaque id cannot supply one: Integritetsskyddsmyndigheten's links are
# "/link/<uuid>.aspx", whose hex digits minted the document "IMYFS 0008:2".
RE_SLUG_NUMBER = re.compile(r"[a-zåäö]+[-_ ]?((?:19|20)\d{2})[-_ ]?(\d{1,3})(?:\D|$)",
                            re.IGNORECASE)
# a bare number in a row that prints no designation at all
RE_COLON_NUMBER = re.compile(r"\b(\d{4}):(\d+)\b")
# the list 18 c § författningssamlingsförordningen has an agency publish. It is
# a catalogue of the samling, not a document in it, and its filename carries a
# date a slug regex reads as a number (Konsumentverket's "...2021-01.pdf" was
# harvested as the regulation KOVFS 2021:1, hiding the real one).
RE_FORTECKNING_ROW = re.compile(
    r"f[öo]\s?rteckning(?:en|ar)?\s+över\s+(?:gällande\s+)?(?:föreskrifter|författningar)",
    re.IGNORECASE)


def own_designation(text):
    """The designation a listing row gives its *own* document, as a
    ``(printed, year, lopnummer)`` triple -- or None when the row names none of
    its own.

    A row names more than one. Taking the leftmost, which this did until
    2026-09, files a document under another document's number: an amendment
    row's first designation is the base it amends, and a repeal row's is what
    it repeals. Four scopes were filing documents that way (#72, #99, #81,
    #84), and Konkurrensverket needed a per-agency flag to opt out of it.

    So each designation in the row is placed against the words around it. The
    first one after "om ändring i", "upphävande av" or "ersätter" is that
    other document; the first after "omtryckt genom" is this one. What is left
    unclaimed, read left to right, is the row's own."""
    text = normalise(text or "")
    spans = [(m.start(), m.groups()) for m in RE_DESIGNATION.finditer(text)]
    if not spans:
        return None
    others = set()
    for m in RE_OTHERS_NEXT.finditer(text):
        nxt = next((i for i, (at, _g) in enumerate(spans) if at >= m.end()), None)
        if nxt is not None:
            others.add(nxt)
    for m in RE_OWN_NEXT.finditer(text):
        others |= {i for i, (at, _g) in enumerate(spans) if at < m.start()}
    return next((g for i, (_at, g) in enumerate(spans) if i not in others), None)


# printed designation (`fs_code`, so lowercased and stripped of separators, the
# Swedish vowels still in it) -> the samling slug it is registered under. Four
# series carry a vowel their slug transliterates: ÅFS is `aafs` (`afs` is
# Arbetsmiljöverkets), RÅFS `raafs` (`rafs` is Riksarkivets), SJÖFS `sjofs`,
# ELSÄK-FS `elsakfs`. Reading the slug straight off the printed designation
# filed 51 Elsäkerhetsverket documents under an `elsäkfs` no registry knows.
_SERIES_SLUGS = {fs_code(row["designation"]): fs
                 for fs, row in datasets.load_fs_series().items()
                 if row.get("designation")}


def series_slug(designation):
    """The registered samling slug a printed designation names.

    `fs_code` spells the rule -- lowercase, drop every separator -- and this
    puts the registry's own designation->slug rows over it, falling back to the
    transliteration for a series the registry does not know (a predecessor a
    listing still carries, whose documents belong under its own name)."""
    key = fs_code(designation)
    return _SERIES_SLUGS.get(key, fold_swedish(key))


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
# "konsol", not "konsolider": Swedac's filenames abbreviate ("stafs-2022-9-konsol.pdf")
RE_KONSOLIDERAD = re.compile(r"konsol", re.IGNORECASE)
RE_MEMO = re.compile(r"beslutsprom|besluts-?pm|konsekvensutredning", re.IGNORECASE)
RE_ATTACHMENT = re.compile(r"anvisning|blankett|bilaga|mall|vägledning", re.IGNORECASE)
RE_FS_NUMBER = re.compile(r"\b([A-ZÅÄÖ-]+FS)\s*(\d{4}):(\d+)", re.IGNORECASE)


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
    if RE_KONSOLIDERAD.search(head):
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
    if RE_KONSOLIDERAD.search(href):              # incl. Swedac's '-konsol' abbreviation
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


def fetch_pdf(session, root, fs, ref, url, name, *, delay=0.5, log=print,
              rejects=None):
    """Download one document body into ``<root>/<fs>/<name>`` and return that
    name, or None when the bytes are not a PDF.

    Every resolver needs the same three things of a body: fetch it, refuse it
    when a WAF challenge or an error page came back 200 under a document URL
    (a magic-byte sniff -- the served content type is not evidence), and pace
    the next fetch. A refusal is logged and, when the harvest passes its
    ``rejects`` ledger, counted there -- never silently dropped while the record
    is still written, which used to mask the document with zero trace."""
    data = request(session, "GET", url).content
    if document_extension(data) != ".pdf":
        msg = "%s %s: %s served a non-PDF body, skipped (%s)" % (
            fs, ref.basefile, name, url)
        log("  " + msg)
        if rejects is not None:
            rejects.append(msg)
        return None
    compress.write_download(Path(root) / fs / name, data)
    time.sleep(delay)
    return name


def save_single_pdf_record(root, agency, ref, pdf_url, pdf_data, *, source_url=None):
    """Store one direct official PDF and its ordinary föreskrift record.

    Browser-protected direct sources still produce exactly the layout an HTTP
    ``resolve_direct`` source does. Source modules own how they discover and
    fetch the bytes; this shared tail owns the format they write.
    """
    if document_extension(pdf_data) != ".pdf":
        raise UpstreamChanged("%s body is not a PDF" % ref.identifier)
    fs = ref.fs or agency.fs
    name = "%s-regulation.pdf" % slug(ref.basefile)
    compress.write_download(Path(root) / fs / name, pdf_data)
    record = {
        "fs": fs,
        "basefile": ref.basefile,
        "identifier": ref.identifier,
        "title": ref.title,
        "publisher": agency.publisher,
        "url": source_url or ref.url,
        "files": {
            "regulation": {
                "name": name,
                "url": pdf_url,
                "identifier": ref.identifier,
            },
            "consolidation": [],
            "amendment": [],
            "memo": [],
            "attachment": [],
        },
    }
    write_record(record_path(root, fs, ref.basefile), record)
    return record


def resolve_landing(session, agency, ref, root, delay=0.5, *, log=print, rejects=None):
    """Fetch ``ref``'s landing page, classify the files it hangs, download the
    in-force text (regulation + consolidation) and record the rest as
    references, then write the record JSON + landing HTML. Architecture-generic:
    the only per-agency knobs are the PDF-link selector (``params['pdf_select']``),
    the classification (``params['classify']``) and ``params['download_roles']``.

    A link the classifier kept but whose bytes are not a PDF (a WAF challenge or
    error page served 200) is rejected by a magic-byte sniff, logged, and (when
    ``rejects`` is given) counted -- never silently dropped while the record is
    still written, which used to mask the document with zero trace.

    Returns the stored record (also written to disk)."""
    fs = ref.fs or agency.fs     # the document's own samling (see DocRef.fs)
    arsutgava, lopnummer = ref.basefile.split("/", 1)[1].split(":")
    landing = request(session, "GET", ref.url).text
    soup = BeautifulSoup(landing, "html.parser")
    classify = agency.params.get("classify", classify_file)
    download_roles = agency.params.get("download_roles", DOWNLOAD_ROLES)

    files: dict[str, Any] = {"regulation": None, "consolidation": [], "amendment": [],
             "memo": [], "attachment": []}
    seen = set()
    for a in soup.select(agency.params.get("pdf_select", 'a[href$=".pdf"]')):
        href = a.get("href")
        if not href or href in seen:
            continue
        seen.add(href)
        assert isinstance(href, str)
        result = classify(a, fs, arsutgava, lopnummer)
        if result is None:
            continue
        role, ars, lop = result
        # the printed designation when the link names one: a landing page can
        # hang another series' documents (an SLVFS base amended by LIVSFS, MSBFS
        # hosting SÄIFS), and "SLVFS 2016:9" for LIVSFS 2016:9 would mint a
        # document that does not exist
        printed = RE_FS_NUMBER.search(a.get_text(" ", strip=True))
        designation = printed.group(1) if printed else fs.upper()
        identifier = "%s %s:%s" % (designation, ars, lop) if ars else None
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
        name = "%s-%s.pdf" % (slug(ref.basefile), role) if role == "regulation" \
            else "%s-%s-%s_%s.pdf" % (slug(ref.basefile), role,
                                      ars or "x", lop or len(files[role]))
        if not fetch_pdf(session, root, fs, ref, ref_entry["url"], name,
                         delay=delay, log=log, rejects=rejects):
            continue                               # the link wasn't a PDF after all
        entry = dict(ref_entry, name=name)
        if role == "regulation":
            files["regulation"] = entry
        else:
            files[role].append(entry)

    compress.write_download(Path(root) / fs / (slug(ref.basefile) + ".html"), landing)
    record = {
        "fs": fs, "basefile": ref.basefile, "identifier": ref.identifier,
        "title": ref.title, "publisher": agency.publisher,
        "url": ref.url, "files": files,
    }
    write_record(record_path(root, fs, ref.basefile), record)
    return record


def resolve_direct(session, agency, ref, root, delay=0.5, *, log=print, rejects=None):
    """Resolve an agency that publishes the document URLs *in the listing* (an
    API-direct source, e.g. Boverket -- no landing page to scrape). Reads
    ``ref.extra`` (the agency-neutral payload an API enumerator fills),
    downloads the in-force text (regulation + consolidation) and records
    amendments as references. The shared resolve for any source whose
    enumeration already carries the file URLs. A URL whose bytes are not a PDF
    (a WAF/error page) is rejected by a magic-byte sniff, logged and counted
    (via ``rejects``), never silently dropped."""
    fs = ref.fs or agency.fs     # the document's own samling (see DocRef.fs)
    extra = ref.extra
    files: dict[str, Any] = {"regulation": None, "consolidation": [], "amendment": [],
             "memo": [], "attachment": []}

    def fetch(url, name):
        return fetch_pdf(session, root, fs, ref, url, name, delay=delay, log=log,
                         rejects=rejects)

    if extra.get("regulation_url"):
        name = fetch(extra["regulation_url"], "%s-regulation.pdf" % slug(ref.basefile))
        if name:
            files["regulation"] = {"name": name, "url": extra["regulation_url"],
                                   "identifier": ref.identifier}
    for i, c in enumerate(extra.get("consolidations", [])):
        if not c.get("url"):
            continue
        name = fetch(c["url"], "%s-consolidation-%d.pdf" % (slug(ref.basefile), i))
        if name:
            files["consolidation"].append({"name": name, "url": c["url"]})
    files["amendment"] = [{"identifier": a.get("identifier"), "url": a.get("url")}
                          for a in extra.get("amendments", [])]
    record = {
        "fs": fs, "basefile": ref.basefile, "identifier": ref.identifier,
        "title": ref.title or extra.get("title"), "publisher": agency.publisher,
        "url": extra.get("source_url") or ref.url, "files": files,
    }
    write_record(record_path(root, fs, ref.basefile), record)
    return record


# --------------------------------------------------------------------------
# reusable enumerators (the variable axis: *how to list an agency's documents*)
# --------------------------------------------------------------------------
# Each yields DocRefs over the shared harvest loop. A new agency picks one and
# supplies its params; a genuinely new site shape is one new enumerator here.

def ref(agency, ident_text, href, seen, title=None, direct=False):
    """Build a DocRef for a base regulation from a listing row's text and its
    href. When ``direct``, the href *is* the PDF (no landing page) so it goes
    into ``extra`` for :func:`resolve_direct`. Returns None when the row names
    no number, and None for an already-seen base (dedup keeps one DocRef per
    base).

    The number comes from the evidence in this order:

      1. the row's own designation (:func:`own_designation`), which is the
         designation the row's words do not attribute to another document;
      2. for a direct (PDF-href) agency, the filename slug -- the row that
         names only its target has its own number there ("Upphävande av …
         (KKVFS 2015:2)", kkvfs_2021-2.pdf);
      3. a bare "YYYY:N" in the row, for the rows that print no designation.

    A förteckning över gällande föreskrifter is not a document in the samling
    and is dropped before any of that: it is the catalogue, and its filename's
    date reads as a number ("...2021-01.pdf" was harvested as KOVFS 2021:1)."""
    ident_text = normalise(ident_text or "")
    if RE_FORTECKNING_ROW.search(ident_text):
        return None
    own = own_designation(ident_text)
    slugm = RE_SLUG_NUMBER.search(href.rsplit("/", 1)[-1].split("?")[0]) if direct else None
    designation = None
    if slugm and agency.params.get("number_from_slug"):
        arsutgava, lopnummer = slugm.group(1), str(int(slugm.group(2)))
    elif own:
        designation, arsutgava, lopnummer = own[0], own[1], str(int(own[2]))
    elif slugm:
        arsutgava, lopnummer = slugm.group(1), str(int(slugm.group(2)))
    else:
        bare = RE_COLON_NUMBER.search(ident_text)
        if not bare:
            return None
        arsutgava, lopnummer = bare.group(1), str(int(bare.group(2)))
    # Normally every document lands under agency.fs. But a listing mixes
    # författningssamlingar wherever an agency has taken over a renamed or
    # disbanded agency's samling (MCF's "gällande regler" carries new MCFFS
    # *and* still-in-force MSBFS and older SÄIFS), and a predecessor's document
    # filed under the successor's slug is published under a designation no
    # agency ever printed -- "NFS 1987:12", whose own file is snfs1987-12.pdf.
    # So the printed designation decides the samling whenever the row prints
    # one. This was an opt-in flag (``fs_from_designation``) until 2026-09;
    # 139 documents sat under the wrong series because their scope had not set
    # it, and no scope wants the opposite.
    fs = agency.fs
    doc_fs = None      # DocRef.fs override; None == agency.fs (the common case)
    identifier = "%s %s:%s" % (agency.designation or agency.fs.upper(),
                               arsutgava, lopnummer)
    if designation:
        # the printed designation verbatim -- uppercasing would mangle the
        # mixed-case series (SiSFS, SiSUVFS)
        fs = doc_fs = series_slug(designation)
        identifier = "%s %s:%s" % (designation, arsutgava, lopnummer)
    basefile = "%s/%s:%s" % (fs, arsutgava, lopnummer)
    if basefile in seen:
        return None
    seen.add(basefile)
    url = absolute(agency.base_url, href)
    extra = {"regulation_url": url, "title": title, "source_url": agency.index_url} \
        if direct else {}
    return DocRef(basefile=basefile, fs=doc_fs, identifier=identifier,
                  url=url, title=title, extra=extra)


def direct_docref(agency, fs, arsutgava, lopnummer, url, seen, *, identifier=None, title=None):
    """The shared tail of a bespoke direct-PDF enumerator that has parsed a base
    regulation's own (fs, year, lopnummer) itself -- typically off a filename
    slug the generic :func:`ref` can't read. Builds the deduped DocRef with the
    ``{regulation_url, title, source_url}`` extra payload every direct enumerator
    hands :func:`resolve_direct`. ``fs`` is the document's own samling (``==
    agency.fs`` in the common case, a predecessor series when the caller routes
    it); ``DocRef.fs`` is set only when it differs from ``agency.fs``.
    ``identifier`` defaults to ``"<designation> <year>:<lop>"`` from
    ``agency.designation or agency.fs.upper()``; a routed series passes its own.
    Returns ``None`` if this base was already yielded (dedup keeps one per base).
    A *landing* enumerator (no direct PDF href) keeps its own tail -- its DocRef
    carries no extra payload."""
    basefile = "%s/%s:%s" % (fs, arsutgava, lopnummer)
    if basefile in seen:
        return None
    seen.add(basefile)
    return DocRef(
        basefile=basefile, fs=(fs if fs != agency.fs else None),
        identifier=identifier or "%s %s:%s" % (agency.designation or agency.fs.upper(),
                                               arsutgava, lopnummer),
        url=url, title=title,
        extra={"regulation_url": url, "title": title, "source_url": agency.index_url})


def newest_first(refs):
    """Sort DocRefs newest-first by their basefile's ``(year, lopnummer)``, so an
    enumerate that reads oldest-first (a printed förteckning, an unordered
    listing) still hands the incremental walk the newest documents first -- the
    ``HarvestWatermark`` date-boundary stop is only valid on a newest-first
    stream."""
    return sorted(refs, key=lambda r: [int(x) for x in r.basefile.split("/", 1)[1].split(":")],
                  reverse=True)


# What an agency calls the rest of its samling. Thirteen scopes publish their
# repealed regulations on a second page, linked from the listing the harvest
# reads, and never enumerated: 177 documents for MCF, 107 for PRV, 66 for IAF.
# The repeal that ended a regulation lives on that page, so the gap took the
# repeal relations with it.
RE_ARCHIVE_LINK = re.compile(
    r"upphävd|upphäva|upphörd|upphört|tidigare\s+(?:föreskrifter|regler|författningar)"
    r"|äldre\s+(?:föreskrifter|regler|författningar)|historiska\s+föreskrifter", re.I)
#: at most this many archive pages are followed from one listing
ARCHIVE_MAX = 3


def archive_links(soup, agency):
    """The listings this index page links as its own archive of repealed
    regulations, absolute and deduped.

    Followed rather than configured: a scope that has to name the page in its
    params only names it once someone has noticed it is missing, and for
    thirteen scopes nobody had. Bounded to `ARCHIVE_MAX` pages on the agency's
    own host, and read with the scope's own ``link_select`` -- an archive page
    is the same listing with older rows. ``params["no_archive"]`` opts out for
    an agency whose "upphävda" link is prose rather than a listing."""
    if agency.params.get("no_archive"):
        return []
    host = agency.base_url.split("//")[-1].split("/")[0]
    out = []
    for a in soup.find_all("a", href=True):
        if not RE_ARCHIVE_LINK.search(a.get_text(" ", strip=True)):
            continue
        url = absolute(agency.base_url, a["href"])
        if host in url and url not in out:
            out.append(url)
    return out[:ARCHIVE_MAX]


def indexed_enumerate(session, agency):
    """HTML index page(s) list every (base) regulation. params: ``link_select``
    (CSS selector for the anchors); ``index_urls`` (a list, for a per-year index;
    defaults to the single ``index_url``); ``direct`` (the anchor href is the PDF
    itself, not a landing page); ``skip_re`` (drop anchors whose text matches,
    e.g. companion 'Beslutspromemoria' PDFs); ``optional_pages`` (a per-year index
    where a year with no regulations simply 404s -- skip it rather than abort);
    ``no_archive`` (do not follow the archive of repealed regulations the
    listing links, :func:`archive_links`).

    The listing an agency calls "gällande föreskrifter" is not the samling: the
    regulations it has repealed sit on a second page, and so do the documents
    that repealed them. Those pages are followed from the index rather than
    listed per agency, because a scope only names one after someone notices it
    is missing."""
    p = agency.params
    direct = p.get("direct", False)
    skip = re.compile(p["skip_re"]) if p.get("skip_re") else None
    method = "POST" if p.get("post_data") else "GET"      # some "show all" lists POST
    seen = set()
    queue = list(p.get("index_urls", [agency.index_url]))
    multi = len(queue) > 1
    archives_from = len(queue)      # pages past this point are followed archives
    for url in queue:
        try:
            response = request(session, method, url, data=p.get("post_data"))
        except requests.exceptions.HTTPError as exc:
            if p.get("optional_pages") and is_not_found(exc):
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
        if len(queue) == archives_from:        # from the listing itself, once
            queue += [u for u in archive_links(soup, agency) if u not in queue]
            multi = multi or len(queue) > archives_from
        for a in soup.select(p["link_select"]):
            text = a.get_text(" ", strip=True)
            if skip and skip.search(text):
                continue
            docref = ref(agency, text, a.get("href", ""), seen,
                          title=text if direct else None, direct=direct)
            if docref:
                yield docref
        time.sleep(0.3)


def paginated_enumerate(session, agency):
    """The index is paged HTML at ``page_url.format(page=N)`` (newest-first);
    walk until a page yields no rows. params: ``page_url``, ``row_select``,
    ``no_archive`` (:func:`archive_links`).

    The archive of repealed regulations the first page links is walked the same
    way, page by page: MCF's "gällande regler" is 10 documents and its
    "upphävda regler" 177."""
    seen = set()
    queue = [agency.params["page_url"]]
    followed = False
    for base in queue:                       # grows once, from the first page
        page = 1
        while True:
            try:
                response = request(session, "GET", base.format(page=page))
            except requests.exceptions.RequestException as exc:
                yield Skip("page %d: %r" % (page, exc))   # cannot trust paging past it
                break
            soup = BeautifulSoup(response.text, "html.parser")
            if not followed:
                followed = True
                queue += [u + ("&page={page}" if "?" in u else "?page={page}")
                          for u in archive_links(soup, agency)]
            rows = soup.select(agency.params["row_select"])
            if not rows:
                break
            for a in rows:
                docref = ref(agency, a.get_text(" ", strip=True), a.get("href", ""), seen)
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
        docref = ref(agency, str(r[p["id_field"]]), r[p["url_field"]], seen,
                      title=r.get(p.get("title_field", "heading")), direct=direct)
        if docref:
            yield docref


# --------------------------------------------------------------------------
# per-agency wiring onto the shared download engine (lib.harvest.walk)
# --------------------------------------------------------------------------

def harvest(agency, root, full=False, only=None, limit=None, delay=0.5, log=print,
            reporter=None):
    """Download one agency onto :func:`lib.harvest.walk`.

    `reporter` overrides the live progress sink: the sequential path lets
    :func:`walk` build its own live one-line Reporter, but the parallel harvest
    passes a NullReporter (per-agency live lines would collide) and shows a single
    aggregate line for the whole pool instead.

    Backfill (walk the whole index, download what is missing) on ``--full`` or
    when the agency has never cleanly completed (no watermark yet -- a first or
    interrupted run). Once caught up, later runs go incremental: enumeration is
    newest-first, so the walk stops at the first document already on disk that
    falls past the watermark's date boundary (or after a run of consecutive
    already-downloaded items). ``only`` (a basefile) fetches just that one.
    Returns ``(seen, new)``."""
    if agency.browser:
        assert not agency.http2 and agency.headers is None and agency.user_agent is None, \
            "%s browser transport cannot also configure an HTTP session" % agency.fs
        with CamoufoxBrowser(Path(root) / agency.fs / ".browser-profile",
                             pace=agency.browser_pace) as session:
            return _harvest_session(agency, root, session, full, only, limit, delay,
                                    log, reporter)
    session = (make_http2_session if agency.http2 else make_session)(
        agency.user_agent or USER_AGENT)
    if agency.headers:
        session.headers.update(agency.headers)
    return _harvest_session(agency, root, session, full, only, limit, delay, log,
                            reporter)


# sanity trip: a routine incremental agency harvest finishes in minutes; one
# still running past this is upstream pathology (a hung register API, a WAF
# tarpit), not work worth waiting on -- stop, leave the store dirty, move on
INCREMENTAL_BUDGET = 300.0


def item_key(agency, root, ref):
    """What :func:`lib.harvest.walk` needs to place one enumerated ref: its
    basefile, whether the store already holds it, and its date."""
    # basefile is always "<fs>/<year>:<lopnummer>" (built by `ref`, off a regex
    # that only ever captures a 4-digit year) -- the year anchors the date
    # watermark; a shape that violates this is this module's own bug, not a data
    # quirk to route around
    year = ref.basefile.split("/", 1)[1].split(":")[0]
    if not (len(year) == 4 and year.isdigit()):
        raise UpstreamChanged("%s: basefile year %r is not a 4-digit year"
                              % (ref.basefile, year))
    # the record lives under the document's own fs, which is agency.fs unless the
    # row named a different samling (see DocRef.fs)
    stored = record_path(root, ref.fs or agency.fs, ref.basefile)
    downloaded = compress.exists(stored)
    if downloaded and ref.extra.get("updated_at"):
        # a listing that states when the document last changed: "on disk" is then
        # "on disk *and current*", which is what `ItemKey.is_downloaded` means
        # (`lib.harvest`). Statskontoret publishes its regulations as a website,
        # so a consolidation moves on without taking a new number, and the page's
        # own `last-modified` is the only thing that says it did.
        downloaded = (compress.read_json(stored).get("updated_at")
                      == ref.extra["updated_at"])
    return ItemKey(basefile=ref.basefile, is_downloaded=downloaded,
                   date=f"{year}-12-31")


def _harvest_session(agency, root, session, full, only, limit, delay, log,
                     reporter=None):
    """Run the shared walk over an already-selected HTTP or browser transport."""
    # Records are filed by samling, but a *walk* is one publisher's listing, so
    # the watermark is per scope: six agencies issue into HSLF-FS, and one
    # publisher catching up says nothing about the other five. The unsuffixed
    # name is the one-agency case's existing file -- renaming it would restage
    # 66 agencies as first-run backfills.
    scope = agency.scope or agency.fs
    suffix = "" if scope == agency.fs else "-" + scope
    marker = Path(root) / agency.fs / ".complete"
    watermark_path = Path(root) / agency.fs / (".watermark%s.json" % suffix)

    # Migrate legacy complete marker to watermark
    if marker.exists() and not watermark_path.exists():
        HarvestWatermark(watermark_path).save(date.today().isoformat())

    # föreskrifter carry only a year (no day), so the item date is the year end;
    # a 14-day safety window past the newest FS plus a 20-item lookahead is
    # ample -- an agency issues at most a few dozen numbers a year.
    watermark = HarvestWatermark(watermark_path, lookahead_limit=20, safety_days=14)
    rejects: list[str] = []

    # arm the sanity trip on incremental runs only (walk exempts backfills
    # itself, but the session deadline must not cut a legitimate first/--full
    # harvest short); the deadline bounds a single blocked fetch, the walk
    # budget stops the loop cleanly between items
    if only is None and not full and watermark.last_harvest is not None:
        set_deadline(session, time.monotonic() + INCREMENTAL_BUDGET)

    def resolve(ref):
        return agency.resolve(session, agency, ref, root, delay,
                              log=log, rejects=rejects)

    result = walk(agency.enumerate(session, agency), resolve=resolve,
                  item_key=lambda ref: item_key(agency, root, ref),
                  watermark=watermark, full=full, only=only,
                  limit=limit, budget=INCREMENTAL_BUDGET, scope=scope,
                  log=log, reporter=reporter)

    if rejects:
        log("  %s: %d file(s) served a non-PDF body and were skipped"
            % (scope, len(rejects)))
    if result.errors:
        log("  %s: %d download error(s) -- the store stays dirty, so the next "
            "run walks past the already-downloaded backlog and retries them"
            % (scope, result.errors))
    return result.seen, result.new
