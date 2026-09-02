"""HSLF-FS -- one författningssamling, six publishing sites.

Gemensamma författningssamlingen avseende hälso- och sjukvård, socialtjänst,
läkemedel, folkhälsa m.m. is nobody's own samling: seven agencies issue into it
and each publishes on its own site. :mod:`agencies` therefore registers one
:class:`~harvest.Agency` per *site*, all carrying ``fs="hslffs"`` and their own
``scope`` (the registry key and the CLI scope name), so ``lagen foreskrift
download hslffs-ivo`` walks one publisher's listing while every document any of
them yields is filed under the one samling.

Each of those sites also still lists the closed predecessor samling its agency
took over -- Socialstyrelsen's SOSFS, Folkhälsomyndighetens FoHMFS and FHIFS,
Läkemedelsverkets LVFS, TLV:s TLVFS and LFNFS. A document is filed under the
samling its *printed designation* names, the rule :func:`harvest.ref` applies
under ``fs_from_designation``; ``params["samlingar"]`` is each scope's own map
of the fs codes it may publish into to the designation they are printed as, so
a designation outside it fails the harvest instead of minting a samling that
does not exist.

Two site families:

  * **an index of document pages** (Socialstyrelsen, Folkhälsomyndigheten,
    Läkemedelsverket) -- the index names a page, the page names the file.
    :func:`resolve_page` fetches it and reads the file URL with the scope's own
    ``params["body_url"]``.
  * **an index of files** (IVO, MFoF, TLV) -- one static page whose anchors are
    the documents themselves. :func:`enumerate_files` groups the anchors by the
    number each names and hands the URLs to :func:`harvest.resolve_direct`.

Every enumerator yields one DocRef per printed *number*: an ändringsförfattning
is a föreskrift in its own right, with its own number and its own PDF, which is
how the samling itself publishes it. A konsoliderad version is not a number of
its own -- it attaches to the base act it consolidates.
"""

import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..lib import compress
from ..lib.harvest import paginated, write_record
from ..lib.net import request
from ..lib.util import basefile_slug as slug
from ..lib.util import normalize_hints, record_path
from . import harvest
from .harvest import (
    RE_COLON_NUMBER,
    RE_FS_NUMBER,
    RE_KONSOLIDERAD,
    DocRef,
    absolute,
    direct_docref,
    fetch_pdf,
    newest_first,
)

# The typography the six sites print a designation with, none of which
# `harvest.RE_FS_NUMBER` reads as it stands: zero-width joiners around the
# hyphen and a non-breaking space before the year (Läkemedelsverket), a
# non-breaking hyphen inside the designation (TLV), a space inside it
# ("HSLF- FS 2020:19"), a space after the colon ("HSLF-FS 2024: 21"), a hyphen
# gluing designation to number ("HSLF-FS-2020:23") and, where the text is
# really a file name, underscores for both separators ("HSLF-FS_2025_68").
# `plain` folds every one of them to the printed form, so the shared regex is
# what actually reads these sites.
RE_UNICODE_HYPHEN = re.compile("[\u2010-\u2015\u2212]")
RE_SPLIT_DESIGNATION = re.compile(r"\b([A-ZÅÄÖ][A-ZÅÄÖa-zåäö]*) *- *FS\b")
RE_LOOSE_NUMBER = re.compile(r"(?<=FS)[ _-]?(\d{4}) ?[:_] ?(\d+)")

# The designations these sites misprint, each on one document whose number is
# right: Socialstyrelsen's publication list spells HSLF-FS 2017:25 "HSLS-FS",
# Läkemedelsverkets feed spells HSLF-FS 2017:69 "HFSL-FS", and
# Folkhälsomyndighetens reader spells HSLF-FS 2016:97 "SLF-FS" (its own PDF is
# named hslf-fs-2016-97-andringsforfattning.pdf). Each is filed under the
# samling it belongs to rather than raising or minting one that does not exist.
ALIASES = {"hslsfs": "hslffs", "hfslfs": "hslffs", "slffs": "hslffs"}


def plain(text):
    """`text` in the form these sites *print* their designations in: the
    invisible break hints and joiners gone (`util.normalize_hints`, which also
    collapses the whitespace), the unicode hyphens folded to ASCII, a designation
    put back together and a number put back into "<år>:<nr>". What
    :data:`harvest.RE_FS_NUMBER` and :data:`harvest.RE_COLON_NUMBER` then read
    is ordinary printed text."""
    text = normalize_hints(RE_UNICODE_HYPHEN.sub("-", text))
    return RE_LOOSE_NUMBER.sub(
        r" \1:\2", RE_SPLIT_DESIGNATION.sub(r"\1-FS", text))


def samling(designation):
    """The samling slug a printed designation names -- `harvest.fs_code`, plus
    the misprints in :data:`ALIASES`."""
    fs = harvest.fs_code(designation)
    return ALIASES.get(fs, fs)


def numbered(text):
    """``(designation, årsutgåva, löpnummer)`` for the first FS number in
    `text`, or None. The *first* one, because a listing row leads with the
    document's own number and names the act it amends after it."""
    m = RE_FS_NUMBER.search(plain(text))
    return (m.group(1), m.group(2), str(int(m.group(3)))) if m else None


def printed(designation, agency):
    """The designation this scope prints the samling `designation` names with
    -- "HSLF-FS" for a document any of the six issue, its own for a closed
    predecessor samling. Raises when the samling is not one the scope publishes
    into: a listing that has started carrying another one is a change to read
    before it is harvested, not a new fs directory to create silently."""
    fs = samling(designation)
    samlingar = agency.params["samlingar"]
    if fs not in samlingar:
        raise ValueError(
            "%s lists %r, which is not one of the samlingar it publishes into "
            "(%s) -- %s" % (agency.scope, designation,
                            ", ".join(sorted(samlingar)), agency.index_url))
    return samlingar[fs]


def docref(agency, designation, arsutgava, lopnummer, url, seen, extra, *,
           title=None):
    """One DocRef for a document the index names by its *page*, under the
    samling `designation` names and deduped on its basefile. ``extra`` is what
    :func:`resolve_page` reads, so it always carries the ``consolidations``
    slot :func:`attach` hangs a konsoliderad version on."""
    identifier = "%s %s:%s" % (printed(designation, agency), arsutgava, lopnummer)
    fs = samling(designation)
    basefile = "%s/%s:%s" % (fs, arsutgava, lopnummer)
    if basefile in seen:
        return None
    seen.add(basefile)
    return DocRef(basefile=basefile, fs=(fs if fs != agency.fs else None),
                  url=url, title=title, identifier=identifier, extra=extra)


def file_docref(agency, designation, arsutgava, lopnummer, url, seen, *, title=None):
    """One DocRef for a document whose index anchor already *is* its file:
    :func:`harvest.direct_docref`'s ``{regulation_url, title, source_url}``
    payload, plus the ``consolidations`` slot :func:`attach` hangs a konsoliderad
    version on."""
    ref = direct_docref(
        agency, samling(designation), arsutgava, lopnummer, url, seen,
        title=title,
        identifier="%s %s:%s" % (printed(designation, agency), arsutgava,
                                 lopnummer))
    if ref is not None:
        ref.extra["consolidations"] = []
    return ref


def attach(agency, refs, konsoliderade):
    """Hang each konsoliderad file on the base act it consolidates. A base the
    index does not also list in its own right stops the harvest: a konsoliderad
    text filed as if it were the act as enacted is the one error this shape can
    make silently."""
    for basefile, url in konsoliderade:
        if basefile not in refs:
            raise ValueError("%s: konsoliderad version %s consolidates %s, which "
                             "%s does not list in its own right"
                             % (agency.scope, url, basefile, agency.index_url))
        refs[basefile].extra["consolidations"].append({"url": url})


# --------------------------------------------------------------------------
# family 1: an index of document pages (Socialstyrelsen, FoHM, Läkemedelsverket)
# --------------------------------------------------------------------------

def css_url(select):
    """The absolute href of the one anchor `select` matches on a page -- either
    the file the page publishes (``body_url``) or the view it forwards to
    (``consolidation_page_url``). Raises naming the page when the anchor is
    gone: a page that stopped linking its document is a site change, not a
    document to record without a body."""
    def read(html, url):
        anchor = BeautifulSoup(html, "html.parser").select_one(select)
        if anchor is None:
            raise ValueError("no %r link on %s" % (select, url))
        href = anchor["href"]
        assert isinstance(href, str)
        return urljoin(url, href)
    return read


def resolve_page(session, agency, ref, root, delay=0.5, *, log=print, rejects=None):
    """Resolve a document whose index names its *page*: fetch the page, read the
    file URL it publishes through ``params["body_url"]`` and download it.

    ``ref.extra["consolidations"]`` carries the konsoliderad versions the
    enumeration found for this base act, in the form the scope's site publishes
    them (``params["consolidation_form"]``). Where that is a ``"page"``, the
    page *is* the consolidated text: it is stored verbatim as HTML (which
    :func:`parse.parse_consolidation_html` reads) and its own register of
    ändringsförfattningar, read by ``params["consolidation_amendments"]``,
    becomes the record's amendment references -- the only place either agency
    publishes the amendment graph, since both publication lists are flat.
    Folkhälsomyndigheten keeps that text one view further in, so its
    ``params["consolidation_page_url"]`` forwards from the listed page to the
    reader before anything is stored. A ``"file"`` scope links a konsoliderad
    PDF from a page like any other.

    A body whose bytes are not a PDF (a WAF challenge, an error page served 200)
    is rejected by a magic-byte sniff, logged and counted -- never silently
    dropped while the record is still written."""
    p = agency.params
    fs = ref.fs or agency.fs
    landing = request(session, "GET", ref.url).text
    files = {"regulation": None, "consolidation": [], "amendment": [],
             "memo": [], "attachment": []}

    def fetch(url, name):
        return fetch_pdf(session, root, fs, ref, url, name, delay=delay, log=log,
                         rejects=rejects)

    body = p["body_url"](landing, ref.url)
    if fetch(body, "%s-regulation.pdf" % slug(ref.basefile)):
        files["regulation"] = {"name": "%s-regulation.pdf" % slug(ref.basefile),
                               "url": body, "identifier": ref.identifier}
    for i, cons in enumerate(ref.extra["consolidations"]):
        url = cons["url"]
        page = request(session, "GET", url).text
        if p.get("consolidation_page_url"):
            # the listed page carries no text of its own, only a link to the
            # view that holds it (Folkhälsomyndigheten's ?pub= reader)
            url = p["consolidation_page_url"](page, url)
            time.sleep(delay)
            page = request(session, "GET", url).text
        if p["consolidation_form"] == "page":
            name = "%s-consolidation-%d.html" % (slug(ref.basefile), i)
            compress.write_download(Path(root) / fs / name, page)
            for amendment in p["consolidation_amendments"](page, url, agency, ref):
                if amendment not in files["amendment"]:
                    files["amendment"].append(amendment)
        else:
            name = fetch(p["body_url"](page, url),
                         "%s-consolidation-%d.pdf" % (slug(ref.basefile), i))
            if not name:
                continue
        files["consolidation"].append({"name": name, "url": url})
        time.sleep(delay)

    compress.write_download(Path(root) / fs / (slug(ref.basefile) + ".html"), landing)
    record = {"fs": fs, "basefile": ref.basefile, "identifier": ref.identifier,
              "title": ref.title, "publisher": agency.publisher,
              "url": ref.url, "files": files}
    write_record(record_path(root, fs, ref.basefile), record)
    return record


# --------------------------------------------------------------------------
# Socialstyrelsen (also publishing for Rättsmedicinalverket and E-hälsomyndigheten)
# --------------------------------------------------------------------------

# The publication index is a React page that hydrates its whole list from an
# embedded JSON array -- one object per publication, `name` leading with the
# printed designation and `url` naming the publication page.
RE_PUBLICATION_LIST = re.compile(r"SOS\.Components\.PublicationCategoryList,\s*")
# The register a konsoliderad page carries under "Ladda ner": one heading per
# member of the family, naming its role and its printed designation.
RE_ANDRINGSFORFATTNING = re.compile(r"^Ändringsförfattning\b")


def sos_publications(html, url):
    """Socialstyrelsen's embedded publication list, one dict per publication."""
    m = RE_PUBLICATION_LIST.search(html)
    if not m:
        raise ValueError("no hydrated PublicationCategoryList in %s" % url)
    props, _ = json.JSONDecoder().raw_decode(html, m.end())
    pages = props["publicationPages"]
    if not pages:
        raise ValueError("the publication list in %s is empty" % url)
    return pages


def sos_amendments(page, url, agency, ref):
    """Every ändringsförfattning a Socialstyrelsen konsoliderad page names, as
    the record's ``{identifier, url}`` references.

    The page carries the base act's whole family as a register -- one
    ``fileinformation__heading`` per member ("Ändringsförfattning HSLF-FS
    2021:31") over a link to that document's own publication page. It is the
    only place Socialstyrelsen publishes the amendment graph: the publication
    index is a flat list, so the base act's landing names none of them. That is
    why sosfs/2013:1 sat in the corpus with an empty ``amendments`` list while
    HSLF-FS 2020:61 and 2021:31 were on disk as records of their own."""
    soup = BeautifulSoup(page, "html.parser")
    headings = soup.select("div.fileinformation__heading")
    if not headings:
        raise ValueError("%s: konsoliderad page %s carries no \"Ladda ner\" "
                         "register" % (ref.basefile, url))
    amendments = []
    for heading in headings:
        text = plain(heading.get_text(" ", strip=True))
        if not RE_ANDRINGSFORFATTNING.match(text):
            continue                      # "Grundförfattning …": the base itself
        found = numbered(text)
        if not found:
            raise ValueError("amendment heading %r names no number on %s"
                             % (text, url))
        designation, arsutgava, lopnummer = found
        # the block right after the heading, never a forward search over the
        # whole page: a member whose link is gone would otherwise borrow the
        # next member's URL and file this amendment against that document
        block = heading.find_next_sibling()
        anchor = block.select_one(
            "a.publication-list__items--item--link") if block else None
        if anchor is None:
            raise ValueError("%r on %s links no publication page"
                             % (text, url))
        href = anchor["href"]
        assert isinstance(href, str)
        amendments.append({
            "identifier": "%s %s:%s" % (printed(designation, agency),
                                        arsutgava, lopnummer),
            "url": urljoin(url, href)})
    # a register that names only the grundförfattning (and a rättelse) is a
    # konsoliderad text with no amendment yet -- HSLF-FS 2024:5 -- so an empty
    # list is an answer, not a broken page
    return amendments


def sos_enumerate(session, agency):
    """Socialstyrelsen: one DocRef per publication, plus the konsoliderade
    föreskrifter index attached to the base acts it consolidates.

    The publication index also carries what the samling publishes *about*
    itself and beside itself -- the annual "Register över författningar m.m.",
    the "Förteckning över … gällande författningar", a handful of handböcker,
    målbeskrivningar and meddelandeblad. None of those is a författning and none
    leads with an FS number, so the list is filtered on the number prefix: 33 of
    458 entries were dropped when this was measured (2026-09-02).

    The konsoliderade index prints only ``1999:26``, ``2013:1`` -- a bare number
    with no designation -- so which samling a consolidated base belongs to is
    read by joining it against the publication list. That is conclusive for all
    but the 2015 transition year, where both SOSFS 2015:15 and HSLF-FS 2015:15
    exist; such a number is settled by reading the konsoliderad page's own
    heading ("Senaste version av SOSFS 2015:15 …") rather than guessed."""
    p = agency.params
    seen, refs = set(), {}
    for entry in sos_publications(
            request(session, "GET", agency.index_url).text, agency.index_url):
        m = RE_FS_NUMBER.match(plain(entry["name"]))
        if not m:
            continue                    # not a författning -- see the docstring
        designation, arsutgava, lopnummer = m.group(1), m.group(2), str(int(m.group(3)))
        ref = docref(agency, designation, arsutgava, lopnummer,
                     absolute(agency.base_url, entry["url"]), seen,
                     title=plain(entry["name"]),
                     extra={"consolidations": []})
        if ref:
            refs[ref.basefile] = ref

    for arsutgava, lopnummer, url in sos_konsoliderade(session, agency):
        owners = [fs for fs in p["samlingar"]
                  if "%s/%s:%s" % (fs, arsutgava, lopnummer) in refs]
        if len(owners) != 1:
            # nothing to join on, or the 2015 transition's two samlingar: ask
            # the page which act it consolidates rather than picking one
            designation, arsutgava, lopnummer = sos_konsoliderad_base(session, url)
            owners = [samling(designation)]
        basefile = "%s/%s:%s" % (owners[0], arsutgava, lopnummer)
        if basefile not in refs:
            raise ValueError("konsoliderad page %s consolidates %s, which the "
                             "publication index does not list" % (url, basefile))
        refs[basefile].extra["consolidations"].append({"url": url})
    yield from newest_first(refs.values())


def sos_konsoliderade(session, agency):
    """``(årsutgåva, löpnummer, url)`` for every konsoliderad föreskrift
    Socialstyrelsen publishes -- its own index, separate from the publications."""
    url = agency.params["konsoliderade_url"]
    soup = BeautifulSoup(request(session, "GET", url).text, "html.parser")
    rows = soup.select(agency.params["konsoliderade_select"])
    if not rows:
        raise ValueError("no konsoliderade föreskrifter listed on %s" % url)
    for a in rows:
        href = a["href"]
        assert isinstance(href, str)
        if href.rstrip("/").endswith("konsoliderade-foreskrifter"):
            continue                # the index's own entry in the sub-navigation
        text = plain(a.get_text(" ", strip=True))
        m = RE_COLON_NUMBER.match(text)
        if not m:
            raise ValueError("konsoliderad row %r on %s opens with no number "
                             "-- every other row leads with its base act's"
                             % (text[:80], url))
        yield m.group(1), str(int(m.group(2))), absolute(agency.base_url, href)


def sos_konsoliderad_base(session, url):
    """The act a Socialstyrelsen konsoliderad page consolidates, off its own
    heading ("Senaste version av SOSFS 2015:15 …")."""
    heading = BeautifulSoup(request(session, "GET", url).text,
                            "html.parser").select_one("h1")
    found = numbered(heading.get_text(" ", strip=True)) if heading else None
    if not found:
        raise ValueError("konsoliderad page %s names no base act in its heading"
                         % url)
    return found


# --------------------------------------------------------------------------
# Folkhälsomyndigheten
# --------------------------------------------------------------------------

# The publication page's own slug ends in the document's number
# ("…-hslf-fs-2026-30/"); the row title names the act it amends *first* and its
# own number last, so the slug is what the row is read by and the title is what
# corroborates it.
RE_FOHM_SLUG = re.compile(r"[a-zåäö]+(?:-fs|fs)-(\d{4})-(\d+)/?$")
RE_KONSOLIDERAD_ROW = re.compile(r"^Konsoliderad version av\b")
# a row of the reader's printed-PDF register, whichever member it names
RE_FORFATTNING_ROW = re.compile(r"^(?:Grundförfattning|Ändringsförfattning)\b")


def fohm_enumerate(session, agency):
    """Folkhälsomyndigheten: a ``?pn=N`` paged publication list, 25 rows a page,
    each row a publication page that links the document's PDF.

    The list mixes three samlingar -- HSLF-FS and the FoHMFS and FHIFS series
    Folkhälsomyndigheten took over -- so each row is filed under the designation
    its title prints. A row's own number comes from its slug and must be named
    by the title too, which is what tells a row apart from the base act it
    amends; a row whose two disagree stops the harvest rather than filing a
    document under its own base's number. A "Konsoliderad version av …" row is a
    konsoliderad text of an act the list also carries in its own right, so it
    attaches to that act instead of becoming a number of its own."""
    p = agency.params
    rows, _pages = paginated(
        lambda page: request(session, "GET",
                             p["page_url"].format(page=page + 1)).text,
        lambda body: [(a["href"], plain(a.get_text(" ", strip=True)))
                      for a in BeautifulSoup(body, "html.parser").select(
                          p["row_select"])],
        key=lambda row: row[0], cap=p["page_cap"],
        what="Folkhälsomyndighetens publikationslista")
    seen, refs, konsoliderade = set(), {}, []
    for href, title in rows:
        if RE_KONSOLIDERAD_ROW.match(title):
            konsoliderade.append((numbered(title), href))
            continue
        m = RE_FOHM_SLUG.search(href.rstrip("/"))
        if not m:
            raise ValueError("Folkhälsomyndigheten row %s names no number in "
                             "its slug (%r)" % (href, title))
        arsutgava, lopnummer = m.group(1), str(int(m.group(2)))
        designations = [d for d, y, n in RE_FS_NUMBER.findall(plain(title))
                   if (y, str(int(n))) == (arsutgava, lopnummer)]
        if not designations:
            raise ValueError("Folkhälsomyndigheten row %s is slugged %s:%s, "
                             "which its title does not print (%r)"
                             % (href, arsutgava, lopnummer, title))
        ref = docref(agency, designations[0], arsutgava, lopnummer,
                     absolute(agency.base_url, href), seen, title=title,
                     extra={"consolidations": []})
        if ref:
            refs[ref.basefile] = ref
    for found, href in konsoliderade:
        if not found:
            raise ValueError("Folkhälsomyndigheten konsoliderad row %s names "
                             "no base act" % href)
        attach(agency, refs, [("%s/%s:%s" % (samling(found[0]), found[1], found[2]),
                               absolute(agency.base_url, href))])
    yield from newest_first(refs.values())


# Folkhälsomyndighetens publication reader closes with "Tryckta versioner som
# pdf": one row per member of the family, each naming its role and its printed
# designation over a link to that document's own PDF.
RE_ANDRINGSFORFATTNING_ROW = re.compile(r"^Ändringsförfattning\b")


def fohm_amendments(page, url, agency, ref):
    """Every ändringsförfattning Folkhälsomyndighetens publication reader names,
    as the record's ``{identifier, url}`` references.

    The reader's own register is the only place the amendment graph is
    published: the publication list is flat, so neither the base act's page nor
    the konsoliderad row names a single one. A reader that lists only the
    grundförfattning is a konsoliderad text with no amendment yet, which is an
    answer rather than a broken page -- but a page with no register at all is
    not this shape, and says so."""
    reader = BeautifulSoup(page, "html.parser").select_one("div.pubr-reader-body")
    rows = reader.find_all("p") if reader else []
    register = [row for row in rows
                if RE_FORFATTNING_ROW.match(plain(row.get_text(" ", strip=True)))]
    if not register:
        raise ValueError("%s: %s carries no \"Tryckta versioner\" register"
                         % (ref.basefile, url))
    amendments = []
    for row in register:
        text = plain(row.get_text(" ", strip=True))
        if not RE_ANDRINGSFORFATTNING_ROW.match(text):
            continue                       # "Grundförfattning:": the base itself
        found = numbered(text)
        if not found:
            raise ValueError("amendment row %r names no number on %s" % (text, url))
        designation, arsutgava, lopnummer = found
        anchor = row.find("a", href=True)
        if anchor is None:
            raise ValueError("%r on %s links no ändringsförfattning file"
                             % (text, url))
        href = anchor["href"]
        assert isinstance(href, str)
        amendments.append({
            "identifier": "%s %s:%s" % (printed(designation, agency),
                                        arsutgava, lopnummer),
            "url": urljoin(url, href)})
    return amendments


# --------------------------------------------------------------------------
# Läkemedelsverket
# --------------------------------------------------------------------------

# The föreskrifter page is an Angular app whose JSON list endpoint
# (`api/provisionlist`) answers 200 with an empty body to every plain-HTTP
# client -- with and without the interceptor's headers, and with and without the
# EPiStateMarker/ARRAffinity cookies of a preceding page load. The Atom feed the
# same page offers (`showRssFeed`) is served in full to an ordinary request and
# carries the whole register in one call, so that is what is enumerated.
LV_FEED_URL = "https://www.lakemedelsverket.se/api/rss/Rss/?pageId=4741"
# Each document page carries its own server-rendered state on <app-root
# content="…">, and the PDF lives in it rather than in an <a href>.
RE_LV_SLUG = re.compile(r"/(?:hslf-fs-)?(\d{4})-?(\d+)(?:-+\w+)?/?$")


def lv_body_url(html, url):
    """The PDF a Läkemedelsverket document page publishes, out of the Angular
    state it is server-rendered with (``informationBlock.printedVersion``)."""
    root = BeautifulSoup(html, "html.parser").select_one("app-root[content]")
    if root is None:
        raise ValueError("no server-rendered app-root state on %s" % url)
    state = root["content"]
    assert isinstance(state, str)
    printed_version = json.loads(state)["informationBlock"]["printedVersion"]
    if not printed_version["value"]:
        raise ValueError("%s publishes no printed_version version" % url)
    return urljoin(url, printed_version["value"][0]["url"])


def lv_enumerate(session, agency):
    """Läkemedelsverket: the whole register as one Atom feed, one entry per
    document, newest first.

    An entry's number is printed in its title, either in full ("Föreskrifter
    (HSLF-FS 2026:25) om ändring i …") or, for one entry, as a bare number; the
    URL slug carries it too and settles the bare case. The feed's own
    ``category`` says whether an entry is the konsoliderad text of an act it
    also carries, in which case it attaches to that act."""
    # the feed is served as `text/xml` with no charset, which requests reads as
    # ISO-8859-1; the bytes carry their own XML declaration, so hand those over
    feed = BeautifulSoup(request(session, "GET",
                                 agency.params["feed_url"]).content, "xml")
    entries = feed.find_all("entry")
    if not entries:
        raise ValueError("no entries in the feed at %s" % agency.params["feed_url"])
    seen, refs, konsoliderade = set(), {}, []
    for entry in entries:
        link = entry.find("link")
        if entry.title is None or link is None:
            raise ValueError("an entry in the feed at %s carries no title or "
                             "link" % agency.params["feed_url"])
        title = plain(entry.title.get_text())
        href = link["href"]
        assert isinstance(href, str)
        found = numbered(title) or lv_slug_number(href, title)
        konsoliderad = any(RE_KONSOLIDERAD.search(str(c.get("term") or ""))
                           for c in entry.find_all("category"))
        if konsoliderad:
            konsoliderade.append((found, href))
            continue
        ref = docref(agency, found[0], found[1], found[2],
                     href, seen, title=title, extra={"consolidations": []})
        if ref:
            refs[ref.basefile] = ref
    attach(agency, refs,
           [("%s/%s:%s" % (samling(found[0]), found[1], found[2]), href)
            for found, href in konsoliderade])
    yield from newest_first(refs.values())


def lv_slug_number(href, title):
    """``(designation, årsutgåva, löpnummer)`` off a Läkemedelsverket document
    slug, for the one feed entry whose title is a bare number. The samling is
    not in the slug, so the entry is filed under the samling in force for its
    årsutgåva -- LVFS was closed and replaced by HSLF-FS on 1 July 2015."""
    m = RE_LV_SLUG.search(href.rstrip("/"))
    if not m:
        raise ValueError("Läkemedelsverket entry %r (%s) names no number"
                         % (title, href))
    return ("HSLF-FS" if int(m.group(1)) >= 2015 else "LVFS",
            m.group(1), str(int(m.group(2))))


# --------------------------------------------------------------------------
# family 2: an index of files (IVO, MFoF, TLV)
# --------------------------------------------------------------------------

def enumerate_files(session, agency):
    """One static index page whose anchors *are* the documents: group them by
    the number each names and hand the URLs to :func:`harvest.resolve_direct`.

    params: ``link_select`` (CSS for the document anchors); ``unit`` (an
    enclosing tag whose text names the number, when the anchor's own does not --
    MFoF splits one title across two anchors).

    An anchor naming no number is not a document: these pages also carry the
    agency's own förteckning över gällande författningar."""
    p = agency.params
    soup = BeautifulSoup(request(session, "GET", agency.index_url).text,
                         "html.parser")
    seen, refs, konsoliderade = set(), {}, []
    for a in soup.select(p["link_select"]):
        unit = a.find_parent(p["unit"]) if p.get("unit") else a
        if unit is None:
            raise ValueError("%s: %s hangs outside the %r row its number is "
                             "read from (%s)"
                             % (agency.scope, a.get("href"), p["unit"],
                                agency.index_url))
        text = plain(unit.get_text(" ", strip=True))
        url = absolute(agency.base_url, a["href"])
        found = numbered(text) or bare_number(text, agency)
        if not found:
            continue
        if RE_KONSOLIDERAD.search(text + " " + url):
            konsoliderade.append(("%s/%s:%s" % (samling(found[0]), found[1],
                                                found[2]), url))
            continue
        ref = file_docref(agency, found[0], found[1], found[2], url, seen,
                          title=text)
        if ref:
            refs[ref.basefile] = ref
    attach(agency, refs, konsoliderade)
    yield from newest_first(refs.values())


def bare_number(text, agency):
    """``(designation, årsutgåva, löpnummer)`` for an index row that prints its
    number without a designation in front of it, filed under the scope's own
    samling. MFoF interleaves the download chrome into the row, so "(HSLF-FS
    pdf, 233.2 kB, öppnas i nytt fönster. 2022:25)" carries the number but no
    designation the number can be read off."""
    m = RE_COLON_NUMBER.search(plain(text))
    if not m or not agency.params.get("bare_numbers"):
        return None
    return (agency.params["samlingar"][agency.fs], m.group(1), str(int(m.group(2))))


# TLV publishes one accordion panel per base act, its files under three
# headings. Every file in a panel prints the same shape of link text ("HSLF-FS
# 2017:29 pdf, 394 kB."), so what a file *is* comes from the heading above it.
RE_GRUNDFORESKRIFT = re.compile(r"^Grundföreskrift")


def tlv_enumerate(session, agency):
    """TLV: one accordion panel per base act, holding its grundföreskrift, its
    konsoliderad version and every ändringsförfattning of it.

    A konsoliderad file belongs to the panel it sits in, not to the number its
    own link text prints: TLV's oldest panel labels the konsoliderad text of
    LFNFS 2003:1 "TLVFS 2003:2", a number the samling never issued on its own.
    Reading the panel's grundföreskrift instead files that text against the act
    it actually consolidates. Every other file in a panel is a föreskrift in its
    own right -- an ändringsförfattning has its own number and its own PDF."""
    p = agency.params
    panels = BeautifulSoup(request(session, "GET", agency.index_url).text,
                           "html.parser").select(p["panel_select"])
    if not panels:
        raise ValueError("no föreskrift panels on %s" % agency.index_url)
    seen, refs = set(), {}
    for panel in panels:
        heading = panel.find("h2")
        if heading is None:
            raise ValueError("%s: a panel on %s names no base act"
                             % (agency.scope, agency.index_url))
        panel_title = plain(heading.get_text(" ", strip=True))
        base, konsoliderade, role = None, [], None
        # in panel order, so each file's role is the heading above it *here* --
        # a forward search from the anchor reads across the panel boundary and
        # would give the first file of a panel the last role of the one before
        for el in panel.select("h3, %s" % p["link_select"]):
            if el.name == "h3":
                role = plain(el.get_text(" ", strip=True))
                continue
            url = absolute(agency.base_url, el["href"])
            if role is None:
                raise ValueError("%s: %s sits under no role heading in %r (%s)"
                                 % (agency.scope, url, panel_title,
                                    agency.index_url))
            if RE_KONSOLIDERAD.match(role):
                konsoliderade.append(url)
                continue
            found = numbered(el.get_text(" ", strip=True))
            if not found:
                raise ValueError("%s: %s names no number under %r in %r"
                                 % (agency.scope, url, role, panel_title))
            grund = bool(RE_GRUNDFORESKRIFT.match(role))
            # the panel heading is the base act's title; every other file in the
            # panel is a different document, so it keeps its own link text (which
            # `parse.clean_title` reads as chrome and replaces with the PDF's
            # own rubric)
            title = panel_title if grund else plain(el.get_text(" ", strip=True))
            ref = file_docref(agency, found[0], found[1], found[2], url,
                              seen, title=title)
            if grund:
                base = "%s/%s:%s" % (samling(found[0]), found[1], found[2])
            if ref:
                refs[ref.basefile] = ref
        if konsoliderade and base is None:
            raise ValueError("%s: panel %r carries a konsoliderad version but "
                             "no grundföreskrift (%s)"
                             % (agency.scope, panel_title, agency.index_url))
        attach(agency, refs, [(base, url) for url in konsoliderade])
    yield from newest_first(refs.values())
