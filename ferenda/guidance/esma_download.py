"""Harvester for Esmas riktlinjer och rekommendationer.

**Esma runs a register, and the register is the index.** Every row of
``/databases-library/esma-library`` is a table row carrying the document's date,
its number in a ``Reference`` column of its own, its title, a link to the file,
and an expandable panel listing one download per language. The document-type
facet is a term id in the query string, and ``basic_:45`` is "Guidelines &
Recommendations" -- 641 rows over 33 pages of twenty. Nothing needs a browser
and nothing needs a leaf fetch: the row states everything the record holds.

That makes this the opposite of `eba_download`, where the leaf pages state no
number and the PDF cover is the only authority. Here the cover *corroborates*:
123 of the 126 covers read print the number the column gives. The three that do
not are Esmas own inconsistencies -- ESMA70-151-435 prints ESMA70-151-294 in its
footer, which is a different riktlinje, and the library is right -- so the cover
is counted rather than obeyed.

**The listing is one row per language before 2017.** A pre-2017 document is
filed once per translation, each row carrying that language's own title, its own
file and the language appended to the number ("2013/606 SV"). So the 641 rows
are 153 documents, and `library_number` splits that suffix off before grouping.
The Swedish row is where an older riktlinje's Swedish title comes from; the
modern rows title only in English and hang the translations in the panel.

**Three number shapes, and the library prints one of them short.** The modern
"ESMA35-43-3448" and the Joint Committee's "JC/GL/2024/36" appear in the column
exactly as the document prints them. The pre-2017 documents appear as
"2016/1477" where all 34 of their covers print "ESMA/2016/1477", so the prefix
is restored -- the prefixed form is the citation. A Reference in none of the
three shapes is not an Esma number: CESR/09-219 and CESR/04-505b predate Esma,
and six rows carry the literal text "Joint Committee" where the number should
be. Those are declined by number, before anything is fetched.

**The facet types 25 documents as guidelines that are not.** Eight are
slutrapporter, three are vacancy notices, and the rest are an OPINION, a Joint
Consultation Paper, a board decision withdrawing a riktlinje, a NOTE, a blank
compliance-confirmation form, and three .xlsx/.zip files behind a .pdf address.
Only the document itself can tell -- Esma prints the type as the lead of its own
cover ("Riktlinjer", "Guidelines", "Slutrapport", "Final Report") -- so
`cover_kind` reads it there and the candidates are tried in turn until one is a
riktlinje. A slutrapport is declined even where it carries the riktlinje as an
annex, because the punkt numbering a citation names is then the report's.

**Swedish first, from the row that has it.** A candidate is the Swedish row's
own file, else the Swedish entry in the modern panel, else English; `sprak`
records which, and the language comes from the row or the panel badge, never
from the file name.

Neither level paginates past its end and the whole corpus is 33 requests plus
one per document, so the EDPB/EBA idiom applies: one walk per run, no watermark.
On a steady run nothing is fetched -- `known_documents` reads each stored
record's own document URL back, so the cover is re-read only for a document
whose file moved.

Stored per document under ``site/data/downloaded/guidance/esma/``: an
``esma-riktlinjer-<slug>.json`` record and the ``.pdf`` document.
"""

import re
import time
from pathlib import Path

from bs4 import BeautifulSoup

from ..lib.harvest import paginated, select_pending, stored_index, walk_records
from ..lib.net import BROWSER_UA as USER_AGENT
from ..lib.net import fetcher, get_text, make_session, request
from ..lib.pdftext import pdf_first_page_text_bytes
from ..lib.util import document_extension, href, normalize_space
from .issuers import ESMA

SERIE = "riktlinjer"
BASE = ESMA.base
LIBRARY = BASE + "/databases-library/esma-library"

# the library's own document-type facet, as the term id its query string wants
DOCTYPE = ESMA.serie(SERIE).doctype

# the 24 official languages, plus the three the library spells by country
# ("2013/74 CZ", "… EE", "… DK") rather than by language
LANGUAGES = frozenset("BG CS DA DE EL EN ES ET FI FR GA HR HU IT LT LV MT NL "
                      "PL PT RO SK SL SV".split())
LANGUAGE_ALIAS = {"CZ": "CS", "EE": "ET", "DK": "DA"}
# "TC": the track-changes edition Esma publishes beside one riktlinje. Not a
# language and not a document of its own -- it is the same number in an editing
# aid -- so it groups with the riktlinje and is never offered as its text.
TRACK_CHANGES = "TC"
# the variant marker a row appends to the number: "2013/606 SV",
# "JC/GL/2014/01/SV", "2014-1293sv", "ESMA/2016/1452 TC". Anchored on a digit so
# a number ending in letters ("04-505b") cannot lose its last two characters.
RE_VARIANT = re.compile(r"^(.*\d)[ /_-]?([A-Za-z]{2})$")

# Esmas own number, in the shapes its library prints:
RE_MODERN = re.compile(r"^ESMA[\s/-]?\d", re.I)     # ESMA35-43-3448
RE_JC = re.compile(r"^JC[\s/]", re.I)               # JC/GL/2024/36, JC 2024 34
RE_LEGACY = re.compile(r"^(\d{4})[/-](\d{1,4})$")   # 2016/1477, 2014-1293

# the document type Esma sets as the lead of its own cover. A slutrapport that
# carries riktlinjer is a slutrapport: the report is matched first and wins.
RE_COVER_RAPPORT = re.compile(
    r"slutrapport|final\s+report|^report$|joint\s+consultation\s+paper"
    r"|vacancy\s+notice|^opinion$|decision\s+of\s+the\s+board\s+of\s+supervisors"
    r"|confirmation\s+of\s+compliance|^note$", re.I | re.M)
RE_COVER_RIKTLINJE = re.compile(
    r"riktlinjer|rekommendation|guidelines|recommendations", re.I)
# how much of a cover states the type and the number: the lead block, above the
# title. Reading further would find "riktlinjer" in the running text of every
# slutrapport about one.
COVER_LINES = 12

# a bound on the pager, so a facet that never repeats itself cannot walk
# forever. 33 pages of twenty today; the walk still ends on "this page named
# nothing new", not on this.
PAGE_CAP = 60


def basefile(nummer):
    """The harvest basefile of one document ("esma/riktlinjer/esma35-43-3448")."""
    return "%s/%s/%s" % (ESMA.kod, SERIE, ESMA.serie(SERIE).slug(nummer))


def listing_url(page):
    """One page of the library, filtered to the riktlinjer facet."""
    return "%s?f%%5B0%%5D=basic_%%3A%s&page=%d" % (LIBRARY, DOCTYPE, page)


def library_number(reference):
    """``(nummer, variant)`` for one Reference column, or ``(None, variant)``
    when the column holds no Esma number.

    `nummer` is the number the *document* is cited by, which is the column's
    text for the modern and Joint Committee shapes and the column's text under
    an "ESMA/" prefix for the pre-2017 ones -- the library drops that prefix and
    all 34 of those covers print it. `variant` is the language the row is filed
    under, or ``"TC"`` for the track-changes edition, or None for the row that
    is the document itself."""
    reference = normalize_space(reference)
    variant = None
    match = RE_VARIANT.match(reference)
    if match and match.group(2).upper() in LANGUAGES | {TRACK_CHANGES}:
        reference, variant = match.group(1).rstrip(" /_-"), match.group(2).upper()
    elif match and match.group(2).upper() in LANGUAGE_ALIAS:
        reference = match.group(1).rstrip(" /_-")
        variant = LANGUAGE_ALIAS[match.group(2).upper()]
    if RE_MODERN.match(reference) or RE_JC.match(reference):
        return reference, variant
    legacy = RE_LEGACY.match(reference)
    if legacy:
        return "ESMA/%s/%s" % legacy.groups(), variant
    return None, variant


def library_rows(html_text):
    """One listing page -> its rows, in page order. Pure over the HTML.

    A row is two ``<tr>``: the document's own, and the info panel that follows
    it holding the translations. They are paired by position rather than by the
    button id, because the id is the number lowercased and the numbers carry
    slashes and spaces."""
    soup = BeautifulSoup(html_text, "html.parser")
    rows = []
    for tr in soup.select("table.views-view-table > tbody > tr"):
        reference = tr.select_one("td.views-field-field-document-reference")
        if reference is None:
            # the info panel of the row above it
            assert rows, "an Esma library page opened with an info panel"
            for sub in tr.select("table > tr"):
                cells = sub.find_all("td", recursive=False)
                if len(cells) == 2 and normalize_space(
                        cells[0].get_text()) == "Translated versions":
                    rows[-1]["oversattningar"] = {
                        normalize_space(a.get_text()).upper(): href(a)
                        for a in cells[1].find_all("a", href=True)}
            continue
        title = tr.select_one("td.views-field-title a")
        published = tr.select_one("time[datetime]")
        document = tr.select_one(
            "td.views-field-field-main-document a.download-tag")
        sections = tr.select_one("td.views-field-field-document-section")
        assert title is not None, \
            "an Esma library row named no document: %s" % tr.get_text()[:120]
        rows.append({
            "reference": normalize_space(reference.get_text()),
            "titel": normalize_space(title.get_text()),
            "source_url": BASE + href(title),
            "publicerad": published["datetime"][:10] if published else None,
            "dokument": href(document) if document is not None else None,
            "amnesord": [normalize_space(s) for s in
                         (sections.get_text() if sections is not None
                          else "").split(",") if normalize_space(s)],
            "oversattningar": {},
        })
    return rows


def candidates(rows):
    """``[(sprak, url)]`` for one number's rows, best first.

    Swedish before English, and within each the row Esma filed *as* that
    language before the panel entry beside another language's row -- the
    pre-2017 Swedish row is a different file from the English one, and it is the
    riktlinje where the English row is the slutrapport that carried it
    (ESMA/2013/606 is exactly that). The track-changes edition is never a
    candidate: it is an editing aid, not the text.

    Only ``.pdf`` links are offered. A compliance table beside a riktlinje is an
    .xlsx and a translation bundle is a .zip, and both hang off the same panel
    as the document."""
    def pdfs(*urls):
        return [u for u in urls
                if u and u.lower().split("?")[0].endswith(".pdf")]

    by_variant = {row["variant"]: row for row in rows}
    ordered = []
    for sprak, code in (("sv", "SV"), ("en", "EN")):
        # every Swedish candidate before any English one: a modern document is
        # one row whose own file is English and whose Swedish text hangs in the
        # panel, and taking the row's file first would serve all of them in
        # English
        for row in ([by_variant[code]] if code in by_variant else []) \
                + ([by_variant[None]] if sprak == "en" and None in by_variant
                   else []):
            ordered += [(sprak, url) for url in pdfs(row["dokument"])]
        ordered += [(sprak, url) for row in rows
                    if row["variant"] != TRACK_CHANGES
                    for url in pdfs(row["oversattningar"].get(code))]
    seen, unique = set(), []
    for sprak, url in ordered:
        if url not in seen:
            seen.add(url)
            unique.append((sprak, BASE + url))
    return unique


def describing_rows(rows, sprak):
    """``(served, original)`` -- which of one number's rows describes the text
    we serve, and which describes the document itself.

    They differ for the pre-2017 documents, where each translation is a library
    row of its own. The *served* row carries the title in the language we store
    (the Swedish row is where an older riktlinje's Swedish title comes from);
    the *original* row carries the adoption date and Esmas own ämnesord, and a
    translation's row carries neither -- it is dated the day the translation
    appeared, weeks after the riktlinje was adopted."""
    by_variant = {row["variant"]: row for row in rows}
    served = by_variant.get("SV" if sprak == "sv" else "EN") \
        or by_variant.get(None) or rows[0]
    return served, by_variant.get(None) or served


def cover_kind(pdf_bytes):
    """What a document's own cover says it is: ``"riktlinje"``, ``"rapport"``,
    ``"otypad"``, or ``"icke-pdf"`` when the ``.pdf`` address served something
    else.

    Esma sets the type as the lead of the cover, above the title -- "Riktlinjer"
    / "Guidelines" for a riktlinje, "Slutrapport" / "Final Report" / "OPINION" /
    "VACANCY NOTICE" for the 22 documents the facet types as guidelines that are
    not. Only the lead block is read: every slutrapport *about* a riktlinje says
    "riktlinjer" further down its first page."""
    if document_extension(pdf_bytes) != ".pdf":
        return "icke-pdf"
    return cover_type(_first_page(pdf_bytes))


def cover_type(cover_text):
    """The same reading over the cover's text, so the rule can be tested against
    the covers this corpus actually sets without a PDF in the loop."""
    lead = "\n".join([line.strip() for line in cover_text.splitlines()
                      if line.strip()][:COVER_LINES])
    if RE_COVER_RAPPORT.search(lead):
        return "rapport"
    return "riktlinje" if RE_COVER_RIKTLINJE.search(lead) else "otypad"


def cover_states_number(pdf_bytes, nummer):
    """Whether the cover prints the number the library filed the document under.

    Counted, never asserted: Esma prints a *different* riktlinje's number in the
    footer of ESMA70-151-435 and writes "JC/GL 2024 88" where the library says
    "JC 2024 88". The library's Reference column is the register and stays the
    identity; this says how often the document agrees, so a drift in either
    shows up in the run's own output."""
    return cover_names(_first_page(pdf_bytes), nummer)


def cover_names(cover_text, nummer):
    """Whether a cover's text prints `nummer`, however Esma spaced it. The
    separators are free because the same number is set "ESMA35-43-3448" on one
    cover and "ESMA35\u201343\u20133006" with en-dashes on another, and
    "ESMA/2016/1477" where the library writes "2016/1477"."""
    return bool(re.search(r"[^A-Za-z0-9]*".join(
        re.escape(part) for part in re.split(r"[^A-Za-z0-9]+", nummer) if part),
        re.sub(r"\s+", " ", cover_text), re.I))


def _first_page(pdf_bytes):
    return pdf_first_page_text_bytes(pdf_bytes) or ""


def known_documents(root):
    """``{document url: (nummer, sprak)}`` from the records already stored.

    What makes a steady run free: a candidate that is already the stored file
    for its number is the document we named, so its cover is not read again."""
    directory = Path(root) / ESMA.kod
    if not directory.exists():
        return {}
    return stored_index(directory, "dokument_url",
                        lambda record: (record["nummer"], record["sprak"]))


def walk_library(session, delay):
    """Every row of the riktlinjer facet, and the number of pages it took.

    Stops on a page that names no row this walk has not seen, never on an empty
    one: a Drupal pager answers past its own end, and a walk that trusts the
    page count reports a corpus several times its real size."""
    return paginated(
        lambda page: get_text(session, listing_url(page), delay), library_rows,
        lambda row: (row["reference"], row["source_url"]),
        cap=PAGE_CAP, what="Esma library riktlinjer facet")


def group_by_number(rows):
    """``{nummer: [row]}`` and the rows whose Reference is no Esma number.

    The pre-2017 documents are one row per language and the modern ones one row
    with a panel, so this is what turns 641 rows into 153 documents."""
    documents, unnumbered = {}, []
    for row in rows:
        nummer, variant = library_number(row["reference"])
        if nummer is None:
            unnumbered.append(row)
            continue
        documents.setdefault(nummer, []).append({**row, "variant": variant})
    return documents, unnumbered


def esma_sync(root, full=False, only=None, limit=None, delay=0.5):
    """Harvest Esmas riktlinjer och rekommendationer off its own library.

    One scope, not one per series: Esma runs one numbered series here and one
    walk of one facet yields it whole.

    Every declined candidate is counted under its own reason, because the
    reasons mean different things (rule:instrument-failures). `otypad` and
    `icke-pdf` are page shapes this harvest has not seen or a link Esma
    mislabelled; `rapport` is a document type this source does not carry; `utan
    nummer` is a row the library itself left unnumbered. A count that moves is
    a change upstream, and one that stays is the corpus."""
    session = make_session(USER_AGENT)
    known = known_documents(root)
    rows, pages = walk_library(session, delay)
    documents, unnumbered = group_by_number(rows)
    pending, fetched, agreeing = [], 0, 0
    declined = dict.fromkeys(("utan nummer", "utan pdf-länk", "icke-pdf",
                              "rapport", "otypad"), 0)
    declined["utan nummer"] = len(unnumbered)
    per_sprak = {"sv": 0, "en": 0}
    for nummer in sorted(documents):
        chosen, verdicts = None, []
        for sprak, url in candidates(documents[nummer]):
            if known.get(url) == (nummer, sprak):
                chosen = (sprak, url, None)
                break
            body = request(session, "GET", url, timeout=180).content
            fetched += 1
            time.sleep(delay)
            kind = cover_kind(body)
            verdicts.append(kind)
            if kind == "riktlinje":
                agreeing += cover_states_number(body, nummer)
                chosen = (sprak, url, body)
                break
        if chosen is None:
            # no candidate's cover names a riktlinje. Reported under the *last*
            # verdict rather than the first: the reasons are ordered, and a
            # document whose Swedish file is a slutrapport and whose English one
            # is too is a slutrapport, not a shape we failed to read
            declined[verdicts[-1] if verdicts else "utan pdf-länk"] += 1
            continue
        sprak, url, body = chosen
        per_sprak[sprak] += 1
        served, original = describing_rows(documents[nummer], sprak)
        pending.append(({
            "basefile": basefile(nummer), "utgivare": ESMA.kod,
            "serie": SERIE, "nummer": nummer,
            "sprak": sprak, "titel": served["titel"],
            # the library's own Date column *on the original row*, which is the
            # adoption date: a translation is filed weeks later and dated the
            # day it appeared, so ESMA/2016/1477 is adopted 2016-10-20 and its
            # Swedish row says 2016-11-10
            "antagen": original["publicerad"], "version": None,
            "konsultation_url": None, "amnesord": original["amnesord"],
            "source_url": served["source_url"], "dokument_url": url,
        }, (lambda got=body: got) if body is not None
            else fetcher(session, url, timeout=180)))
    print("esma: %d listing pages, %d rows -> %d numbered documents, "
          "%d taken (%d sv, %d en), %d covers read of which %d print the "
          "library's number, declined: %s"
          % (pages, len(rows), len(documents), len(pending),
             per_sprak["sv"], per_sprak["en"], fetched, agreeing,
             ", ".join("%d %s" % (n, reason)
                       for reason, n in declined.items())))
    return walk_records(
        root, select_pending(pending, only,
                             "the Esma library carries no document %s"),
        delay=delay, full=full, limit=limit, scope=ESMA.kod)
