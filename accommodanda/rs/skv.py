"""Skatteverkets rättsliga ställningstaganden: register and page semantics.

Skatteverket is the odd one out among the seven agencies in `agencies.py`, in
three ways that all follow from where it publishes:

  * **Behind the F5/Shape challenge.** rattsligvagledning is served from the same
    front that gates SKVFS, so every navigation runs through the detached
    headful-Chrome transport (`lib.browser`) rather than an HTTP session. That
    is why this agency has its own weekly command instead of riding the ordinary
    ``lagen rs download`` sweep.

  * **The document is a web page.** The other six publish a letterhead PDF; here
    the ställningstagande *is* its page, the way a JK-beslut is (`avg.parse`).
    So the body reader is HTML, not `lib.pdftext`, and the stored document is
    the page itself.

  * **Identity is the diarienummer.** Skatteverket numbers no series. Its own
    cross-references read "Skatteverkets ställningstagande 2026-07-06, dnr
    8-207888-2026", so the dnr is the published designation rather than the
    metadata it is for the other six.

The register is one slow page listing 2,619 entries, of which 2,614 carry a dnr
and can be filed. The whole list is *server-rendered into the page* as the
Sitevision app's initial state, so it is read as JSON rather than scraped off
rendered rows. That payload carries more than the rendered list shows: each
entry's diarienummer, the document's own date, its subject taxonomy ids, and the
validity window. That window's end date is the only place the register states
that a ställningstagande has been withdrawn.

Currency. `latestVersion.endDate` is when a position stopped applying, and 980
of the 2,614 filed documents carry one. A further 273 carry an ``endDate``
*equal to* the start: those are the withdrawal notices themselves ("Ställningstagandet
Verksamhetsöverlåtelse ska inte längre tillämpas"), which Skatteverket publishes
with a single day's validity because their content is a one-time announcement.
Reading a zero-length window as a withdrawal would say the agency withdrew its
own withdrawal notice, so only a window that actually closed later counts.
"""

import json
import re
from datetime import datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from ..lib.util import normalize_space
from .agencies import BY_ORG, number_slug

INDEX_URL = BY_ORG["skv"].listing
BASE = "https://www4.skatteverket.se"
# what `DetachedChrome.html` verifies the completed page by: the register's own
# heading, and the label every ställningstagande page prints its dnr under
INDEX_MARKER = "Ställningstaganden"
PAGE_MARKER = "Dnr:"

# Sitevision serialises each web app's server-rendered state into an inline
# script. The portlet id is regenerated per deploy, so the register's state is
# found by its *shape* (a ``data.pages`` list) rather than by that id.
RE_INITIAL_STATE = re.compile(r"AppRegistry\.registerInitialState\('[^']+',")
# "skatteverket 8-193984-2026" -- the register's own reference id: the issuer,
# then the diarienummer. Skatteverket writes it by hand and the hand varies, so
# what has to match is the dnr shape itself.
#
# The issuer is optional and matched case-insensitively: 189 entries differ only
# in case or in stray whitespace, and one drops the issuer altogether.
#
# The number takes two era shapes -- the 2020- form ("8-492402",
# "8-193984-2026") and the older registry form, an optional avdelning
# (130/131/202), the löpnummer, the year and a unit ("131 297826-13/111",
# "130 2199-04/1152"). Five entries write that form irregularly: a space where
# the slash belongs, an en dash for the year's hyphen, a two-digit unit
# ("131 416292-10/11") and a one-digit year ("130 237238-5/111", 2005).
#
# Five entries name no dnr at all: the four 1997-98 RSV-skrivelser the register
# keys on their date, and one test page. They have no identity to be filed
# under, so `parse_index` reports them rather than inventing one.
RE_REF_DNR = re.compile(
    r"^(?:skatteverket\s+)?("
    r"\d-\d+(?:-\d{4})?"                        # 8-492402, 8-193984-2026
    r"|(?:\d{3}[\s-])?\d+[-–]\d{1,2}(?:[/\s]\d{2,4})?"   # 131 297826-13/111
    r")$", re.IGNORECASE)
# a dash Skatteverket sets typographically in some dnr (the register's
# "131 576809–13", and "dnr 8–1740076" in prose where the markup says
# "8-1740076"): the same number, so the printed form settles on the hyphen
RE_DNR_DASH = re.compile("[–—]")
# the JS ``Date.toString()`` the register serialises a document's own date as
RE_JS_DATE = re.compile(r"^\w{3}\s+(\w{3})\s+(\d{2})\s+(\d{4})\b")
MONTHS = {m: i for i, m in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
# the taxonomy path ids inside a "[Label;4.abc, Label;4.abc>Sub;4.def]" subject
# string. Only the ids are read: a label may itself contain the ", " that
# separates two paths ("Energiskatt, koldioxidskatt, svavelskatt;4.703…"), so
# splitting on the separator is ambiguous while the ids are not.
RE_SUBJECT_ID = re.compile(r";(4\.[0-9a-z]+)")
# Skatteverket dates its published versions in UTC; the window it prints on the
# page (and the one a reader means) is the Swedish civil date
STOCKHOLM = ZoneInfo("Europe/Stockholm")

# the page's own metadata box: labelled fields above the body
PAGE_LABELS = ("Områden", "Datum", "Dnr")
# a ställningstagande's headings run h2 (section) to h5, and the CMS leaves 19
# empty h1 behind. Index 1 is the document's own top level, so h1 and h2 share
# it rather than every section being renumbered under an h1 that is always empty.
HEADINGS = ("h1", "h2", "h3", "h4", "h5")
# the last section of a document that has notes, and what stands in each note's
# place in the running text
FOOTNOTE_HEADINGS = ("fotnot", "fotnoter")
RE_FOOTNOTE_MARK = re.compile(r"^(\d{1,2})\s+(?=\S)")
# a typographic rule the editor sets as a paragraph of its own, right above the
# footnotes -- a separator, not a stycke
RE_RULE_ONLY = re.compile(r"^[_\-–—\s]+$")

# what the page states about this position's relation to another one, in
# Skatteverkets own words. Both sentences name the other document with an
# ``a.reference`` carrying its dnr, so the dnr is read off the markup and only
# the sentence has to be recognised.
RE_ERSATTS_AV = re.compile(r"ska inte längre tillämpas", re.IGNORECASE)
RE_ERSATTER = re.compile(r"^Detta ställningstagande ersätter", re.IGNORECASE)


# --------------------------------------------------------------------------
# the register
# --------------------------------------------------------------------------

def index_state(html):
    """The register app's server-rendered state, as the dict Sitevision wrote
    into the page. Located by shape, not by portlet id (regenerated per
    deploy): the one initial state that carries a ``data.pages`` list."""
    for script in BeautifulSoup(html, "html.parser").find_all("script"):
        text = script.string or ""
        if '"pages"' not in text:
            continue
        match = RE_INITIAL_STATE.search(text)
        if match is None:
            continue
        state, _end = json.JSONDecoder().raw_decode(text, match.end())
        if isinstance(state, dict) and "pages" in state.get("data", {}):
            return state
    raise ValueError(
        "the Skatteverket register page carries no reference-overview-list "
        "state -- did the page finish loading, and does Sitevision still "
        "server-render the list?")


def dnr(ref_id):
    """The diarienummer a register entry's reference id names, or None where it
    names none. Nothing is invented: an entry whose id is not a dnr has no
    identity to be filed under, and the harvest reports it."""
    match = RE_REF_DNR.match(RE_DNR_DASH.sub("-", normalize_space(ref_id)))
    return match.group(1) if match else None


def own_date(value):
    """The document's own date, off the JS ``Date.toString()`` the register
    serialises it as ("Tue Jul 07 2026 00:00:00 GMT+0200 (CEST)" ->
    "2026-07-07"). This is the date the page prints as ``Datum:`` -- the
    ställningstagande's, not the day rättslig vägledning published it."""
    match = RE_JS_DATE.match(value or "")
    if match is None:
        raise ValueError(
            "Skatteverket register states an unreadable date %r" % value)
    return "%s-%02d-%s" % (match.group(3), MONTHS[match.group(1)], match.group(2))


def _civil_date(stamp):
    """A UTC publication stamp as the Swedish civil date it names."""
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")) \
        .astimezone(STOCKHOLM).date().isoformat()


def withdrawn(version):
    """The date this position stopped applying, or None while it still does.

    A window that never opened -- ``endDate`` equal to ``startDate`` -- is not a
    withdrawal but a withdrawal *notice*, which Skatteverket publishes with a
    single day's validity (see the module docstring)."""
    # every entry in the register states a start; only the end is optional
    start, end = version["startDate"], version.get("endDate")
    return _civil_date(end) if end and end > start else None


def top_subjects(subject_path, subjects):
    """The top-level områden a register entry is filed under, in page order.

    The entry states its subjects as taxonomy *paths* ("[Inkomstskatt;4.abc,
    Inkomstskatt;4.abc>Näringsverksamhet;4.def]"), and the register's own
    subject table says which id is a top level and what it is called. Reading
    the ids and looking them up is exact, where splitting the path string on its
    separator is not -- a label may contain that separator."""
    out = []
    for subject_id in RE_SUBJECT_ID.findall(subject_path or ""):
        subject = subjects.get(subject_id)
        if subject is None:
            raise ValueError(
                "Skatteverket register names subject %s outside its own "
                "taxonomy" % subject_id)
        if not subject["parent"] and subject["label"] not in out:
            out.append(subject["label"])
    return out


def parse_index(html):
    """The register page -> (records, unidentified).

    Each record is what the register alone knows about one ställningstagande:
    its identity, title, own date, subject områden, currency and page URL. The
    body and what this position replaces live on the page and are read at parse.
    ``unidentified`` are the entries whose reference id is not a diarienummer,
    returned rather than dropped so the harvest says so.

    No sammanfattning is lifted, deliberately. Every page opens on a "1
    Sammanfattning" section, but that section is *body* -- setting it as the
    artifact's summary as well would print Skatteverkets own words twice on the
    page, once above the text and once inside it. IMY's and Konkurrensverkets
    summaries are editorial abstracts written beside the document, which is why
    those are lifted and this is not.

    Pure over the HTML, so the register's rules are testable without a browser.
    """
    state = index_state(html)
    subjects = {s["value"]: s for s in state["settings"]["subjects"]
                if s["value"]}
    pages = state["data"]["pages"]
    if not pages:
        raise ValueError("the Skatteverket register parsed to no entries")
    records, unidentified, seen = [], [], {}
    for page in pages:
        nummer = dnr(page["refId"])
        if nummer is None:
            unidentified.append("%s (%s)" % (normalize_space(page["name"]),
                                             page["refId"] or "no reference id"))
            continue
        basefile = "skv/%s" % number_slug(nummer)
        # the diarienummer is the identity, so two entries claiming one is a
        # collision this store cannot represent -- and, unresolved, would file
        # the later entry over the earlier one with no trace
        if basefile in seen:
            raise ValueError(
                "Skatteverket register names dnr %s twice (%r and %r)"
                % (nummer, seen[basefile], normalize_space(page["name"])))
        seen[basefile] = normalize_space(page["name"])
        upphavd = withdrawn(page["latestVersion"])
        records.append({
            "basefile": basefile, "org": "skv", "nummer": nummer,
            "titel": normalize_space(page["name"]),
            "beslutsdatum": own_date(page["date"]),
            "diarienummer": nummer,
            "status": "upphävt" if upphavd else "gällande",
            "upphavd": upphavd,
            "nyckelord": top_subjects(page["subjects"], subjects),
            "source_url": urljoin(BASE, page["visitorURI"])})
    return records, unidentified


# --------------------------------------------------------------------------
# one ställningstagande's page
# --------------------------------------------------------------------------

def _text(element):
    return normalize_space(element.get_text(" ", strip=True))


def page_metadata(html):
    """The labelled fields the page sets above the body ({Områden, Datum, Dnr}),
    as the page itself prints them."""
    soup = BeautifulSoup(html, "html.parser")
    head = soup.find("div", class_="referenceProperties")
    if head is None:
        raise ValueError("a Skatteverket ställningstagande page has no "
                         "referenceProperties box")
    fields = {}
    for para in head.find_all("p"):
        label = para.find("strong")
        if label is None:
            continue
        name = _text(label).rstrip(":")
        if name in PAGE_LABELS:
            fields[name] = normalize_space(
                _text(para)[len(_text(label)):])
    return fields


def _content(html):
    """The page's body container -- the run of blocks that is the document.

    Skatteverket wraps that run in one anonymous ``div`` inside ``div.body``, in
    every one of the 51 pages sampled across the register. Reading ``div.body``
    itself when that wrapper is absent would hand the block reader a container
    one level too high, and produce a plausible-looking artifact from it, so the
    wrapper is required rather than fallen back from."""
    soup = BeautifulSoup(html, "html.parser")
    body = soup.find("div", class_="body")
    if body is None:
        raise ValueError(
            "a Skatteverket ställningstagande page has no body container")
    inner = body.find("div", recursive=False)
    if inner is None:
        raise ValueError("a Skatteverket ställningstagande page wraps its body "
                         "in no content div")
    return inner


def _rows(table):
    """A table's rows as lists of cell strings. A cell sets its text in one or
    more ``p``; its own flattening is what keeps the cells apart, which reading
    the table as one element would not."""
    return [[_text(cell) for cell in row.find_all(("td", "th"), recursive=False)]
            for row in table.find_all("tr")]


def _blocks(element):
    """One page block element as ``Block``-shaped tuples: (kind, text, level,
    rows, th). A heading keeps its depth, a paragraph and each list item is a
    stycke, and a table keeps its cells.

    Measured over the whole register (1,869 documents harvested), the kinds that
    occur at this level are ``p``, ``h1``-``h5``, ``ul``, ``ol``, ``div.update``,
    ``table`` (206) and ``blockquote`` (47) -- and nothing else. An element of a
    kind this reader does not know is *reported* rather than squashed into one
    run-together stycke, which is how the tables were found: a 51-page sample
    saw only empty layout scaffolds and would have shipped the rest flattened.
    An empty unknown element is still dropped, because a page's leftover layout
    scaffolding carries no text to lose."""
    if element.name in HEADINGS:
        # h2 is the top level Skatteverket writes; the 19 empty h1 the CMS
        # leaves behind are dropped below, and one that ever carries text is a
        # heading above those -- rendered at the same depth rather than
        # renumbering every document's sections under it
        return [("rubrik", _text(element),
                 max(1, HEADINGS.index(element.name)), [], False)]
    if element.name == "p":
        return [("stycke", _text(element), 1, [], False)]
    if element.name in ("ul", "ol"):
        return [("stycke", _text(item), 1, [], False)
                for item in element.find_all("li", recursive=False)]
    if element.name == "table":
        rows = [row for row in _rows(element) if any(cell for cell in row)]
        return [("tabell", "", 1, rows, bool(element.find("th")))] if rows else []
    # a quoted passage -- a skatteavtal article, the OECD commentary on it --
    # set as its own paragraphs. They flatten to stycken, which is what the
    # `p.indented` Skatteverket quotes with elsewhere already does; the corpus
    # has no quotation node to project onto, and inventing one here would be a
    # node type only this source emits.
    if element.name == "blockquote":
        return [("stycke", _text(para), 1, [], False)
                for para in element.find_all("p")]
    # a div.update is Skatteverkets own dated note at the head of the document
    # ("Nytt: 2026-07-06 / Detta ställningstagande ska inte längre tillämpas
    # ..."). It is published text about this position, so it is body: the
    # withdrawal *also* reaches the reader as a banner, off the register's own
    # end date, but the note says things no field carries.
    if element.name == "div" and "update" in (element.get("class") or ()):
        return [("stycke", _text(para), 1, [], False)
                for para in element.find_all("p")]
    text = _text(element)
    if not text:
        return []
    raise ValueError(
        "a Skatteverket page sets body text in a <%s>, which this reader has "
        "no shape for: %r" % (element.name, text[:120]))


def _notes(elements):
    """The notes under a "Fotnot" heading, as (mark, text) pairs.

    Skatteverket numbers them two ways, and which way is a property of the
    section rather than of one note. Either every note prints the marker the
    running text set as a superscript ("1 Bostad med särskild service …"), or
    none does and the numbering is positional -- an ``ol``, or the one document
    whose single note needs no number. Measured over the 71 note sections in the
    register: 70 print every marker, one prints none, and none mixes the two.

    A *mixed* section is therefore not a shape Skatteverket writes, and is far
    more likely to be a note running to a second paragraph -- which positional
    numbering would misnumber. That still stops the document rather than
    guessing."""
    texts = []
    for element in elements:
        if element.name == "ol":
            texts += [_text(item) for item
                      in element.find_all("li", recursive=False) if _text(item)]
            continue
        texts += [block[1] for block in _blocks(element)
                  if block[1] and not RE_RULE_ONLY.match(block[1])]
    marked = [(text, RE_FOOTNOTE_MARK.match(text)) for text in texts]
    unmarked = [text for text, mark in marked if mark is None]
    if len(unmarked) == len(marked):
        return [(str(n), text) for n, text in enumerate(texts, 1)]
    if unmarked:
        raise ValueError(
            "a Skatteverket note section numbers some notes and not others, so "
            "a note may run to a second paragraph: %r"
            % [text[:40] for text in unmarked])
    return [(mark.group(1), text[mark.end():])
            for text, mark in marked if mark is not None]


def page_body(html):
    """The page's prose as ``Block``-shaped tuples plus its footnotes as
    (mark, text) pairs.

    A document that has notes sets them under a "Fotnot" heading. That section
    ends at the next heading of the same depth or shallower -- two documents
    close on a "Tillämpningsinformation" section *after* their notes, and
    reading the notes to the end of the document swallowed it. Dropping the
    notes themselves would cost exactly the references that say what
    Skatteverket is reading."""
    body, notes, notes_depth = [], [], None
    for element in _content(html).find_all(True, recursive=False):
        if element.name in HEADINGS:
            depth = HEADINGS.index(element.name)
            if _text(element).lower() in FOOTNOTE_HEADINGS:
                notes_depth = depth
                continue
            if notes_depth is not None and depth <= notes_depth:
                notes_depth = None          # the notes ended; this is body again
        if notes_depth is not None:
            notes.append(element)
            continue
        for kind, text, level, rows, th in _blocks(element):
            if kind != "tabell" and (not text or RE_RULE_ONLY.match(text)):
                continue
            body.append((kind, text, level, rows, th))
    return body, _notes(notes)


def _referenced_dnr(paragraph):
    """The diarienummer the ``a.reference`` in `paragraph` names, or None.
    Skatteverket marks its own cross-references up with the target's reference
    id, so what a sentence points at is read off the markup rather than out of
    the prose."""
    for anchor in paragraph.select("a.reference[data-reference-id]"):
        nummer = dnr(anchor["data-reference-id"])
        if nummer:
            return nummer
    return None


def page_relations(html):
    """What the page says about this position's place in a chain, as
    ``{ersatt_av, ersatter}`` diarienummer.

    Both facts are sentences Skatteverket writes in fixed words: a withdrawn
    position carries a dated ``div.update`` note saying it "ska inte längre
    tillämpas" and naming what replaced it, and one that superseded an earlier
    position opens a paragraph "Detta ställningstagande ersätter ...". Each
    names its counterpart with a marked-up reference, so only the sentence has
    to be recognised."""
    content = _content(html)
    ersatt_av = next(
        (nummer for note in content.find_all("div", class_="update")
         for para in note.find_all("p")
         if RE_ERSATTS_AV.search(_text(para))
         for nummer in [_referenced_dnr(para)] if nummer), None)
    ersatter = next(
        (nummer for para in content.find_all("p")
         if RE_ERSATTER.match(_text(para))
         for nummer in [_referenced_dnr(para)] if nummer), None)
    return {"ersatt_av": ersatt_av, "ersatter": ersatter}
