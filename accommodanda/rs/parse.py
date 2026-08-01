"""A harvested rs record + its PDF -> :class:`Stallningstagande` -> JSON artifact.

All six agencies publish a ställningstagande as a letterhead PDF -- a narrow
margin column of labelled fields beside a wide body, structure marked by font
alone -- which is the shape `lib.pdftext.classify_letterhead` reads. So there is
one body reader here, configured per agency by two patterns: the margin column's
own labels and bare values, and the footer masthead, which has to be removed *in
place* because wherever a footer line shares a baseline with a body line the two
arrive glued into one paragraph.

What each agency has to be told, and nothing more, is therefore:

  * ``margin``  -- the labels and values its letterhead sets in the margin
  * ``masthead`` -- the address block it sets in the footer
  * ``header``  -- the page-1 fields the *listing* did not already carry

That last one is deliberately thin. The listing is authoritative for everything
it states -- FI dates its own förteckning, Konkurrensverket its document pages,
Lifos its records -- and nothing the listing states is re-derived from the PDF
(the avg rule). The PDF is read only where the field exists nowhere else: IMY's
and Kronofogdens dates, and four agencies' diarienummer.

The body is citation-scanned with the shared engine, which is the point of the
vertical: a ställningstagande is one long argument about a handful of paragraphs,
so scanning it puts the agency's published reading on the rail of each paragraf
it interprets -- next to the statute, which is where a reader meets it.
"""

import functools
import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from ..lib import compress
from ..lib.datasets import NAMEDLAWS as SFS_NAMEDLAWS
from ..lib.lagrum import (
    ALL_PARSE_TYPES,
    LagrumParser,
    load_abbreviations,
    load_namedlaws,
)
from ..lib.pdftext import (
    classify_letterhead,
    letterhead_footnotes,
    page_paragraphs,
    pages_with_ocr,
    pdf_first_page_text,
)
from ..lib.util import normalize_space, record_path
from .agencies import BY_ORG
from .download import labelled_value, pdf_path
from .model import Block, Fotnot, Stallningstagande

RS_PARSE_TYPES = ALL_PARSE_TYPES

RE_ISODATE = re.compile(r"\d{4}-\d{2}-\d{2}")

# ---- IMY: the margin sets "Diarienummer:"/"Datum:" with their values ---------
RE_IMY_MARGIN = re.compile(
    r"^(?:Diarienummer|Datum|Diarienr)\s*:?$"
    r"|^(?:DI|IMY)-\d{4}-\d+$|^\d{4}-\d{2}-\d{2}$")
RE_IMY_MASTHEAD = re.compile(
    r"\s*(?:Postadress:|Besöksadress:|Webbplats:|E-post:|Telefon:|Fax:"
    r"|Box 8114|104 20 Stockholm|Drottninggatan \d+"
    r"|www\.imy\.se(?:/\S*)?|imy@imy\.se)\s*")
RE_IMY_DNR = re.compile(r"\b(?:DI|IMY)-\d{4}-\d+\b")

# ---- FI: "Rättsligt ställningstagande / <nr> / Datum / <date> / FI dnr / <dnr>"
# ("FI dnr 25-31002" also runs as the page header, so the label and its value
# have to be matched glued as well as apart)
RE_FI_MARGIN = re.compile(
    r"^(?:Rättsligt ställningstagande(?:\s+\d{4}:\d+)?|Datum"
    r"|FI dnr(?:\s+\d{2}-\d{4,6})?)\s*:?$"
    r"|^\d{4}:\d+$|^\d{4}-\d{2}-\d{2}$|^\d{2}-\d{4,6}$")
# the address block, each token one FI prints only there. The agency's bare name
# heads that block but is also all over the prose ("inom Finansinspektionens
# tillsynsområde"), so it is matched only where the address run follows it.
RE_FI_MASTHEAD = re.compile(
    r"\s*(?:Finansinspektionen\s+(?=Box 7821)|Box 7821|103 97 Stockholm"
    r"|Tel \+46|finansinspektionen@fi\.se|www\.fi\.se)\s*")
RE_FI_DNR = re.compile(r"\b\d{2}-\d{4,6}\b")

# ---- Försäkringskassan: a three-column header table above the title ----------
RE_FK_MARGIN = re.compile(
    r"^(?:RÄTTSLIGT STÄLLNINGSTAGANDE|Beslutsdatum|Serienummer|Diarienummer"
    r"|Vår beteckning)\s*:?$"
    r"|^\d{4}:\d{1,3}$|^\d{4}-\d{2}-\d{2}$|^(?:Dnr\s+)?(?:FK\s+)?\d{4}/\d{6}$")
RE_FK_MASTHEAD = re.compile(
    r"\s*(?:Wimi \S+|Försäkringskassan\s+(?=Wimi ))\s*")
RE_FK_DNR = re.compile(r"\b(?:FK\s+)?\d{4}/\d{5,6}\b|\b\d{5,6}-\d{4}\b")

# ---- Kronofogden: a two-column administrative table above the body -----------
RE_KFM_MARGIN = re.compile(
    r"^(?:Kronofogdemyndighetens|ställningstagande|Rättsligt ställningstagande"
    r"|Infoklass:?\s*\d*|Beslutat av|Grundbeslut fattat av|Dokumentägare"
    r"|Beslutsdatum|Gäller från och med|Diarienummer|Ansvarig organisation"
    r"|Nr)\s*:?$"
    r"|^\d{1,3}/\d{2}(?:/[A-Za-zÅÄÖåäö]+)?$|^\d{4}-\d{2}-\d{2}$"
    r"|^KFM\s+[\d-]+$|^Rättsavdelningen$")
RE_KFM_MASTHEAD = re.compile(r"\s*(?:www\.kronofogden\.se|kronofogdemyndigheten"
                             r"@kronofogden\.se)\s*")
RE_KFM_DNR = re.compile(r"\bKFM\s+\d+-\d{4}\b")

# ---- Migrationsverket: a fastställelse block below the title ----------------
# every label in that block is *followed by a colon* ("Beslutsdatum: 2025-02-28
# Gäller för: hela myndigheten"), which is what tells the block apart from body
# prose opening on the same words ("Gäller för utlänningar som har fått
# avslag."). Anchoring on the label alone would delete such a paragraph whole.
# The revision-history table below the block ("Datum för revidering | Version |
# Avsnitt som reviderats") needs no pattern: it is set below the body size in
# every one of the harvested documents, so the size rule already drops it, and a
# label pattern for it would only be another way to eat prose.
RE_MIGR_MARGIN = re.compile(
    r"^(?:Fastställelsebeslut|Beslutsdatum|Gäller för|Gäller från och med"
    r"|Diarienummer)\s*:|^R[SK]/\d{2,3}/\d{4}$")
# the footer sets the agency's name and its domain on one line, and a body line
# sharing that baseline arrives glued to it -- so the pair is removed together,
# never the bare name, which is a word these documents' prose is full of
RE_MIGR_MASTHEAD = re.compile(
    r"\s*(?:Migrationsverket\s+)?www\.migrationsverket\.se\s*")

# ---- Konkurrensverket: the same letterhead its beslut use --------------------
RE_KKV_MARGIN = re.compile(
    r"^(?:STÄLLNINGSTAGANDE|BESLUT|Dnr|Datum)\s*:?\s*(?:\d{4}:\d+)?$"
    r"|^Dnr \d+/\d{4}$|^\d{4}-\d{2}-\d{2}$")
# Konkurrensverkets footer labels its address fields, and the labels are
# ordinary Swedish words a decision's prose opens paragraphs with ("E-post och
# andra elektroniska meddelanden…"). Since a masthead is removed *in place*, a
# bare label would eat that first word -- so each label is bound to the value it
# introduces, which is what makes it a footer field rather than a sentence.
# (avg/parse.py's copy is the unbound version; both describe one agency's
# footer, and folding them together is noted in REWRITE §7l as pending.)
RE_KKV_MASTHEAD = re.compile(
    r"\s*(?:Adress|Besöksadress|Telefon|Fax|E-post|Webbplats)\s*:"
    r"|\s*103 85 Stockholm|\s*118 60 Stockholm|\s*Ringvägen 100"
    r"|\s*Torsgatan 11|\s*08-700 16 00|\s*konkurrensverket@kkv\.se"
    r"|\s*www\.konkurrensverket\.se")
RE_KKV_DNR = re.compile(r"\b\d+/\d{4}\b")


@functools.cache
def _refparser():
    return LagrumParser(load_namedlaws(SFS_NAMEDLAWS), basefile="rs",
                        abbreviations=load_abbreviations(SFS_NAMEDLAWS),
                        parse_types=RS_PARSE_TYPES)


def _fresh_parser():
    """The shared parser with document-lifetime state reset (so one document's
    'samma lag' / learned law names do not bleed into the next)."""
    parser = _refparser()
    parser.reset()
    return parser


# --------------------------------------------------------------------------
# the page-1 fields the listings do not carry
# --------------------------------------------------------------------------

def _dated(text, label):
    match = labelled_value(text, label, RE_ISODATE)
    return match.group(0) if match else None


def _numbered(text, label, pattern):
    match = labelled_value(text, label, pattern)
    return normalize_space(match.group(0)) if match else None


def imy_header(text):
    return {"beslutsdatum": _dated(text, "Datum"),
            "diarienummer": _numbered(text, "Diarienummer", RE_IMY_DNR)}


def fi_header(text):
    return {"diarienummer": _numbered(text, "FI dnr", RE_FI_DNR)}


def fk_header(text):
    """Försäkringskassan labels the diarienummer two ways across the eras
    ("Diarienummer" now, "Vår beteckning" before ~2020) and writes it in two
    forms ("FK 2026/004799", "52394-2016")."""
    return {"beslutsdatum": _dated(text, "Beslutsdatum"),
            "diarienummer": _numbered(text, "Diarienummer", RE_FK_DNR)
            or _numbered(text, "Vår beteckning", RE_FK_DNR)}


def kfm_header(text):
    """Kronofogden runs two templates -- "Rättsligt ställningstagande" and the
    older "Kronofogdemyndighetens ställningstagande" -- and both state a
    Beslutsdatum, though the older one prints it after the administrative block
    rather than at the head. Both also print a "Gäller från och med" date, which
    is when the statement took effect and not when it was decided; anchoring on
    the label is what keeps the two apart."""
    return {"beslutsdatum": _dated(text, "Beslutsdatum"),
            "diarienummer": _numbered(text, "Diarienummer", RE_KFM_DNR)}


def migr_header(text):
    """The Beslutsdatum the PDF prints, which is the document's own -- Lifos's
    Upphovsdat dates the *record*, and the two part company whenever a
    ställningstagande is revised in place."""
    return {"beslutsdatum": _dated(text, "Beslutsdatum")}


def kkv_header(text):
    return {"diarienummer": _numbered(text, "Dnr", RE_KKV_DNR)}


# the line each agency's template opens with, naming what kind of document this
# is. It is the letterhead's caption rather than the body's first words, and the
# page already says it (the section label above the heading), so a leading block
# matching it goes with the repeated title.
RE_LEAD = re.compile(r"^(?:RÄTTSLIGT STÄLLNINGSTAGANDE|Rättsligt ställningstagande"
                     r"|STÄLLNINGSTAGANDE|Kronofogdemyndighetens ställningstagande"
                     r"|Rättslig kommentar)\s*[\d:/]*$", re.I)


# --------------------------------------------------------------------------
# the per-agency reading, as data
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Reader:
    """How one agency's letterhead is read: the margin column's own labels and
    values, the footer masthead removed in place, the page-1 fields its listing
    does not carry, and whether it marks headings by weight or by size."""
    margin: re.Pattern
    masthead: re.Pattern
    header: Callable
    by_size: bool = False


READERS = {
    "imy": Reader(RE_IMY_MARGIN, RE_IMY_MASTHEAD, imy_header),
    # Finansinspektionen sets no bold anywhere: body 18, headings 24, title 30
    "fi": Reader(RE_FI_MARGIN, RE_FI_MASTHEAD, fi_header, by_size=True),
    "fk": Reader(RE_FK_MARGIN, RE_FK_MASTHEAD, fk_header),
    "kfm": Reader(RE_KFM_MARGIN, RE_KFM_MASTHEAD, kfm_header),
    # Migrationsverket sets its section headings large and regular (body 17,
    # sections 30) and reserves bold for the small print
    "migr": Reader(RE_MIGR_MARGIN, RE_MIGR_MASTHEAD, migr_header, by_size=True),
    "kkv": Reader(RE_KKV_MARGIN, RE_KKV_MASTHEAD, kkv_header),
}


def _fold(text):
    """A heading compared the way a reader would compare it: case- and
    space-insensitive, and free of the punctuation and the number the letterhead
    may set alongside the title."""
    return re.sub(r"[^0-9a-zåäö]+", "", text.lower())


def drop_front_matter(blocks, titel):
    """Drop the letterhead's caption and the document's own title where the PDF
    opens with them.

    The page already carries both -- the caption as the section label, the title
    as the h1 -- so the PDF's copies would read as the body opening by repeating
    itself. Only *leading* blocks go, so a later heading that happens to echo
    the title is left alone as the real section it is."""
    folded = _fold(titel)
    while blocks and (RE_LEAD.match(blocks[0].text)
                      or (folded and _fold(blocks[0].text).endswith(folded))):
        blocks = blocks[1:]
    return blocks


def body(record, root, patch_key=None):
    """The ställningstagande's text as typed blocks, read from its PDF by the
    shared letterhead rules under the agency's own margin/masthead patterns.
    A document the agency published no PDF for -- a repealed Konkurrensverket
    entry, which keeps its förteckning row and nothing else -- has an empty
    body: the record is then the register entry, which is what it is."""
    path = pdf_path(root, record["basefile"])
    if not compress.exists(path):
        # the other half of the harvest's invariant: a record is written only
        # once its document is on disk (`download._walk`), so an absent PDF here
        # means the record names none -- a repealed Konkurrensverket entry that
        # kept its förteckning row. A record that *does* name one and has no
        # file is a broken store, not a document-less entry, and must not parse
        # to a silently empty artifact under a real identifier.
        assert not record.get("dokument_url"), (
            "%s names a document (%s) that is not on disk at %s -- re-run "
            "`lagen rs download %s`" % (record["basefile"],
                                        record["dokument_url"], path,
                                        record["org"]))
        return []
    reader = READERS[record["org"]]
    # the OCR-aware reader, not the plain one: a handful of these are scans --
    # one with no text layer at all (FKRS 2018:05) and one whose OCR layer
    # poppler renders invisible (Kronofogdens 5/14/TSM) -- which is the same
    # pair of failures the KKV diarium and the remissvar corpora meet
    paras = [p for pageno, lines in pages_with_ocr(path, patch_key)
             for p in page_paragraphs(lines, BY_ORG[record["org"]].name, pageno)]
    return drop_front_matter(
        [Block("stycke", text) if kind == "stycke" else Block("rubrik", text, level)
         for kind, text, level in classify_letterhead(
             paras, reader.margin, reader.masthead, by_size=reader.by_size)],
        record["titel"])


def footnotes(record, root, patch_key=None):
    """The notes the block classifier drops -- see
    `lib.pdftext.letterhead_footnotes`. A ställningstagande grounds the
    references its prose makes down here, so discarding them costs exactly the
    citations that identify what the agency is reading."""
    path = pdf_path(root, record["basefile"])
    if not compress.exists(path):
        return []
    reader = READERS[record["org"]]
    return [Fotnot(mark, text) for mark, text in letterhead_footnotes(
        [p for pageno, lines in pages_with_ocr(path, patch_key)
         for p in page_paragraphs(lines, BY_ORG[record["org"]].name, pageno)],
        reader.margin, reader.masthead)]


def header_fields(record, root):
    """The page-1 fields the listing did not carry. Nothing here overwrites what
    the listing stated: the agency's own index is authoritative for what it
    publishes, and the PDF fills only the gaps it leaves."""
    path = pdf_path(root, record["basefile"])
    if not compress.exists(path):
        return {}
    fields = READERS[record["org"]].header(pdf_first_page_text(path))
    return {k: v for k, v in fields.items() if v and not record.get(k)}


def parse_record(basefile, root):
    """One basefile ("fk/2025:01", "kfm/1-23-VER", "migr/RS-028-2021") ->
    artifact dict, body citation-scanned."""
    org = basefile.split("/", 1)[0]
    record = json.loads(compress.read_text(record_path(root, org, basefile)))
    fields = {**record, **header_fields(record, root)}
    return Stallningstagande(
        org=org, nummer=record["nummer"], titel=record["titel"],
        beslutsdatum=fields.get("beslutsdatum"),
        diarienummer=fields.get("diarienummer"),
        sammanfattning=record.get("sammanfattning"),
        status=record.get("status") or "gällande",
        upphavd=record.get("upphavd"), ersatt_av=record.get("ersatt_av"),
        ersatter=record.get("ersatter"), version=record.get("version"),
        foregaende_version=record.get("foregaende_version"),
        doktyp=record.get("doktyp") or "stallningstagande",
        nyckelord=list(record.get("nyckelord") or []),
        body=body(record, root, ("rs", basefile)),
        fotnoter=footnotes(record, root, ("rs", basefile)),
        source_url=record.get("source_url"),
        document_url=record.get("dokument_url"),
    ).to_artifact(_fresh_parser())
