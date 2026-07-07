"""Parse an EU document from its OJ HTML/XHTML manifestation into the EurlexDoc
model, for the (many older) documents with no Formex (fmx4) manifestation.

The OJ HTML/XHTML uses a stable set of CSS classes -- `ti-art`, `sti-art`,
`normal`, `note`, `ti-section-N`, ... (some carry an `oj-` prefix) -- that map
directly onto the Block kinds the Formex parser produces, so html/xhtml artifacts
have the same shape and feed the same `to_artifact` projection. Structural
sub-lists (recitals, points, section headings) render as 2-column tables
`marker | text`, which a left-cell-is-a-marker test separates from data tables.

Older CELLAR HTML (pre-OJ-reformatting) is loosely-formatted text in a `<txt_te>`
wrapper with no semantic classes; there structure is inferred from the text via
the localized vocabulary in `lang` (Article/Artikel, TITLE/AVDELNING, ...).
"""

import re

from bs4 import BeautifulSoup

from ..lib.util import normalize_space
from . import lang as L
from .model import BASE, Block, EurlexDoc, doctype, looks_like_act_title

# CSS classes (language-neutral) whose text is bibliographic, not body
HEADER = {"hd-date", "hd-lg", "hd-ti", "hd-oj", "hd-coll", "hd-modifier", "hd-2"}
TITLE = {"doc-ti", "no-doc-c", "ti-doc"}

# the first preamble line, where the header (and so the title recovery) ends:
# the visa list ("med beaktande av" / "having regard to") or, when there is none,
# the enacting-formula opener ("HAR ANTAGIT …" / "HAS ADOPTED …")
_PREAMBLE_START = re.compile(
    r"^(?:med beaktande av|having regard to|har (?:antagit|beslutat|enats)|"
    r"has adopted|whereas)\b", re.IGNORECASE)

# in the legacy HTML the title line runs straight into the OJ publication
# reference ("… i motorfordon Europeiska gemenskapernas officiella tidning nr L
# 341 …"); the title ends where that reference begins
_OJ_REF = re.compile(r"\s*(?:Europeiska (?:gemenskapernas|unionens) officiella "
                     r"tidning|Official Journal)\b", re.IGNORECASE)


def _role(el):
    """The element's first CSS class, lower-cased and `oj-`-stripped."""
    for cls in el.get("class") or ():
        cls = cls.lower()
        return cls[3:] if cls.startswith("oj-") else cls
    return ""


def _flat(el):
    return normalize_space(el.get_text(" "))


def _heading_level(role):
    match = re.search(r"-(\d+)$", role)
    return int(match.group(1)) if match else 1


RE_EU_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")


def eu_date(text):
    """The EU 'D.M.YYYY' date anywhere in `text` -> ISO ('9.4.1968' ->
    '1968-04-09'), or None. The single definition of the EU date shape for the
    eurlex vertical; parse_pdf searches the OJ header blob with it too."""
    match = RE_EU_DATE.search(text)
    if match:
        day, month, year = match.groups()
        return "%s-%02d-%02d" % (year, int(month), int(day))
    return None


def _oj(text):
    """'L 88/1' -> 'L 88'."""
    match = re.match(r"\s*([A-Z]+)\s*(\d+)", text)
    return "%s %s" % (match.group(1), match.group(2)) if match else (text or None)


def _emit_structural_row(marker, text, blocks, in_body, voc):
    if voc.article.match(marker):
        num = L.article_num(marker)
        blocks.append(Block("article", "%s – %s" % (marker, text) if text else marker,
                            num=num, anchor=num))
    elif voc.heading.match(marker):
        blocks.append(Block("heading", normalize_space("%s %s" % (marker, text)),
                            level=1))
    elif (m := L.RE_RECITAL.match(marker)):
        num = m.group(1)
        blocks.append(Block("recital" if not in_body else "point", text, num=num))
    elif in_body and (m := L.RE_POINT.match(marker)):
        blocks.append(Block("point", text, num=m.group(1)))
    else:                                   # roman/number heading marker
        blocks.append(Block("heading", normalize_space("%s %s" % (marker, text)),
                            level=1))


def _emit_table(table, blocks, in_body, voc):
    rows = [cells for cells in
            (tr.find_all(["td", "th"]) for tr in table.find_all("tr")) if cells]
    if rows and all(len(r) == 2 for r in rows):
        markers = [_flat(r[0]) for r in rows]
        if markers and all(voc.is_marker(m) for m in markers):
            for cells in rows:
                _emit_structural_row(_flat(cells[0]), _flat(cells[1]),
                                     blocks, in_body, voc)
            return
    for cells in rows:                       # real data table -> row blocks
        text = " | ".join(c for c in (_flat(c) for c in cells) if c)
        if text:
            blocks.append(Block("row", text))


def parse_html(markup, celex, lang):
    """An OJ HTML/XHTML manifestation (bytes/str/BeautifulSoup) -> EurlexDoc."""
    voc = L.vocab(lang)
    soup = (markup if isinstance(markup, BeautifulSoup)
            else BeautifulSoup(markup, "html.parser"))
    body = soup.find("body") or soup
    doc = EurlexDoc(celex=celex, uri=BASE % celex, doctype=doctype(celex), lang=lang)

    doc.title = normalize_space(" ".join(
        _flat(p) for p in body.find_all(class_=re.compile(r"^(oj-)?(doc-ti|ti-doc)$"))))
    if not doc.title:
        # legacy "Avis juridique important" HTML has no semantic title class -- the
        # title is the class-less header line carrying the act number + date. Scan
        # the header (paragraphs before the preamble opens) and take the first line
        # of that shape, so a recital that merely cites another act isn't mistaken
        # for it.
        for p in body.find_all("p"):
            text = normalize_space(_flat(p))
            if _PREAMBLE_START.match(text):
                break
            if looks_like_act_title(text):
                doc.title = _OJ_REF.split(text, 1)[0].strip()
                break
    hd_date = body.find(class_=re.compile(r"^(oj-)?hd-date$"))
    if hd_date is not None:
        doc.date = eu_date(_flat(hd_date))
    hd_oj = body.find(class_=re.compile(r"^(oj-)?hd-oj$"))
    if hd_oj is not None:
        doc.oj = _oj(_flat(hd_oj))

    in_body = False
    for el in body.find_all(["p", "table"]):
        if el.find_parent("table") is not None:
            continue                         # cell content handled with the table
        if el.name == "table":
            if el.find(class_=re.compile(r"^(oj-)?hd-")) is not None:
                continue                     # the OJ header strip (metadata)
            _emit_table(el, doc.body, in_body, voc)
            continue
        role = _role(el)
        if role in HEADER or role in TITLE:
            continue
        text = _flat(el)
        if not text:
            continue
        # `ti-art` marks an article; with no semantic class (old txt_te HTML) a
        # short line that is itself "Article N" / "Artikel N" is one too -- but a
        # long paragraph merely citing "Article 5 of ..." is not, hence the cap.
        if role == "ti-art" or (not role and voc.article.match(text) and len(text) <= 60):
            num = L.article_num(text)
            doc.body.append(Block("article", text, num=num, anchor=num))
            in_body = True
        elif role == "sti-art":
            if doc.body and doc.body[-1].kind == "article":
                doc.body[-1].text = "%s – %s" % (doc.body[-1].text, text)
            else:
                doc.body.append(Block("heading", text, level=2))
        elif role.startswith("ti-"):
            doc.body.append(Block("heading", text, level=_heading_level(role)))
        elif not role and voc.heading.match(text) and (text.isupper() or len(text) <= 40):
            doc.body.append(Block("heading", text, level=1))
        elif role == "note":
            doc.body.append(Block("note", text))
        elif role == "signatory":
            doc.body.append(Block("signature", text))
        elif in_body:
            doc.body.append(Block("paragraph", text))
        else:
            doc.body.append(Block(voc.preamble_kind(text), text))
            if voc.enacting.search(text):
                in_body = True               # enacting formula ends the preamble
    return doc
