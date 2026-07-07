"""Parse an EU document from Formex (the structured XML manifestation) into the
EurlexDoc model and project it to a JSON artifact.

Formex has two roots we handle: `ACT` (regulations, directives, decisions,
treaties) and `JUDGMENT` (Court of Justice case law). Both carry a
bibliographic header, an optional preamble (recitals + visas) and a body
(enacting terms / judgment contents + ruling). We walk the known structure into
an ordered list of typed blocks; inline markup (highlights, dates, OJ
references) is flattened to text and footnote NOTEs are dropped from the running
text. A `.fmx4.zip` manifestation bundles the main act with its annexes as
separate Formex files; we parse the main act (the lowest-sequence file) and note
the annexes (parsing them is a later step).

Body text is scanned for citations to EU legislation and CJEU case law with the
shared citation engine, the same way SFS/DV/forarbete are, so EU references link
into the rest of the corpus.
"""

import functools
import json
import zipfile
from pathlib import Path

from lxml import etree  # ty: ignore[unresolved-import]  # lxml ships no stubs

from ..lib.datasets import NAMEDACTS
from ..lib.lagrum import EULAGSTIFTNING, EURATTSFALL, LagrumParser, interleave
from ..lib.util import from_roman
from .definitions import build_matcher, extract_definitions, term_refs
from .model import BASE, Block, EurlexDoc, doctype, official_short_title, short_label
from .parse_html import parse_html
from .parse_pdf import parse_pdf
from .structure import nest

LANG_PREFERENCE = ("swe", "eng")

# the manifestation is remote-supplied: no DTD/entity expansion (stdlib
# ElementTree would expand nested entities unbounded); comments/PIs removed so
# the element walks see only real elements (ElementTree dropped them, lxml
# keeps them by default)
XML_PARSER = etree.XMLParser(resolve_entities=False, load_dtd=False,
                             no_network=True, remove_comments=True,
                             remove_pis=True)

# footnote subtrees are dropped from the running text (their content is a note,
# not body prose)
SKIP_INLINE = {"NOTE"}
# text-level elements flattened with no added separator; every other (block)
# child is separated by a space, so adjacent P/TI/STI don't glue together
# ("...2022/2555" + "av den..." -> "...2022/2555 av den...")
INLINE = {"HT", "IE", "FT", "DATE", "QUOT.START", "QUOT.END", "QUOT.S",
          "REF.DOC.OJ", "REF.NP.ECR", "REF.DOC.ECR", "NAME.CASE"}


# --------------------------------------------------------------------------
# loading the Formex source (single file or the main act of a zip bundle)
# --------------------------------------------------------------------------

def load_formex(path):
    """The Formex roots of a downloaded manifestation, in document order: the
    main act/judgment first, then any annexes. A single `.fmx4` yields one
    root; a `.fmx4.zip` bundle yields the main act (lowest-sequence member)
    followed by its annexes (the `.doc.xml` wrappers are skipped)."""
    path = Path(path)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            members = sorted(n for n in zf.namelist()
                             if n.endswith(".xml") and not n.endswith(".doc.xml"))
            assert members, "%s: zip has no Formex member" % path
            return [etree.fromstring(zf.read(m), XML_PARSER) for m in members]
    return [etree.parse(str(path), XML_PARSER).getroot()]


# --------------------------------------------------------------------------
# text extraction
# --------------------------------------------------------------------------

def flatten(elem):
    """The element's mixed text content as one string, recursively: footnote
    subtrees dropped, inline elements spliced in place, block-level children
    space-separated, element tails kept, whitespace normalised."""
    parts = [elem.text or ""]
    for child in elem:
        if child.tag in SKIP_INLINE:
            pass
        elif child.tag in INLINE:
            parts.append(flatten(child))
        else:
            parts.append(" %s" % flatten(child))
        parts.append(child.tail or "")
    return " ".join("".join(parts).split())


def _text(parent, *tags):
    """Flattened text of the first descendant matching any of `tags`, or ''."""
    for tag in tags:
        node = parent.find(".//" + tag) if parent is not None else None
        if node is not None:
            return flatten(node)
    return ""


# --------------------------------------------------------------------------
# ACT (legislation, treaties)
# --------------------------------------------------------------------------

def _article_number(article):
    """The bare article number for the anchor: from IDENTIFIER ('001' -> '1')
    or, failing that, the title ('Artikel 5' -> '5')."""
    ident = article.get("IDENTIFIER")
    if ident and ident.lstrip("0"):
        return ident.lstrip("0")
    title = _text(article, "TI.ART")
    digits = "".join(c for c in title if c.isdigit())
    return digits or None


def _emit_list(lst, blocks):
    """LIST -> a point block per ITEM (its NO.P marker + text)."""
    for item in lst.findall("ITEM"):
        np = item.find("NP")
        marker = _text(np, "NO.P").strip("().") if np is not None else None
        blocks.append(Block("point", _text(np if np is not None else item, "TXT", "P"),
                            num=marker or None))


def _emit_alinea(content, num, blocks):
    """A PARAG/ALINEA body: a plain paragraph, or a lead paragraph followed by
    a LIST of points."""
    lists = content.findall("LIST")
    if not lists:
        blocks.append(Block("paragraph", flatten(content), num=num))
        return
    lead = " ".join(flatten(p) for p in content.findall("P"))
    if lead.strip():
        blocks.append(Block("paragraph", lead, num=num))
    for lst in lists:
        _emit_list(lst, blocks)


def parse_article(article, blocks):
    num = _article_number(article)
    title = _text(article, "TI.ART")
    subtitle = _text(article, "STI.ART")
    blocks.append(Block("article", " – ".join(t for t in (title, subtitle) if t),
                        num=num, anchor=num))
    parags = article.findall("PARAG")
    if parags:
        for parag in parags:
            marker = _text(parag, "NO.PARAG").strip(". ") or None
            alinea = parag.find("ALINEA")
            _emit_alinea(alinea if alinea is not None else parag, marker, blocks)
    else:
        for alinea in article.findall("ALINEA"):
            _emit_alinea(alinea, None, blocks)


def parse_division(division, level, blocks):
    title = _text(division, "TITLE")
    if title:
        blocks.append(Block("heading", title, level=level))
    for child in division:
        if child.tag == "DIVISION":
            parse_division(child, level + 1, blocks)
        elif child.tag == "ARTICLE":
            parse_article(child, blocks)


def parse_preamble(preamble, blocks):
    for child in preamble:
        if child.tag == "PREAMBLE.INIT" or child.tag == "PREAMBLE.FINAL":
            text = flatten(child)
            if text:
                blocks.append(Block("preamble", text))
        elif child.tag == "GR.VISA":
            for visa in child.findall("VISA"):
                blocks.append(Block("citation", flatten(visa)))
        elif child.tag == "GR.CONSID":
            for consid in child.findall("CONSID"):
                np = consid.find("NP")
                marker = _text(np, "NO.P").strip("()") if np is not None else None
                blocks.append(Block("recital", _text(consid, "TXT") or flatten(consid),
                                    num=marker or None))


def parse_act(root, blocks):
    title = _text(root.find("TITLE"), "TI", "P") or _text(root, "TITLE")
    preamble = root.find("PREAMBLE")
    if preamble is not None:
        parse_preamble(preamble, blocks)
    enacting = root.find("ENACTING.TERMS")
    if enacting is not None:
        for child in enacting:
            if child.tag == "DIVISION":
                parse_division(child, 1, blocks)
            elif child.tag == "ARTICLE":
                parse_article(child, blocks)
    return title


def act_metadata(root):
    bib = root.find("BIB.INSTANCE")
    date = oj = None
    if bib is not None:
        node = bib.find(".//DATE")
        date = node.get("ISO") if node is not None else None
        ref = bib.find(".//DOCUMENT.REF")
        if ref is not None:
            coll, no = _text(ref, "COLL"), _text(ref, "NO.OJ")
            oj = ("%s %s" % (coll, no)).strip() or None
    return date, oj


# --------------------------------------------------------------------------
# JUDGMENT (case law)
# --------------------------------------------------------------------------

def parse_judgment(root, blocks):
    title = _text(root.find("TITLE"), "TI") or _text(root, "CURR.TITLE")
    for kw in root.findall(".//INTERMEDIATE//KEYWORD"):
        blocks.append(Block("keyword", flatten(kw)))
    init = root.find("JUDGMENT.INIT")
    if init is not None:
        blocks.append(Block("paragraph", flatten(init)))
    preamble = root.find("PREAMBLE")
    if preamble is not None:
        parse_preamble(preamble, blocks)
    contents = root.find("CONTENTS.JUDGMENT")
    if contents is not None:
        _parse_judgment_contents(contents, blocks)
    jurisdiction = root.find("JURISDICTION")
    if jurisdiction is not None:
        intro = _text(jurisdiction, "INTRO")
        if intro:
            blocks.append(Block("paragraph", intro))
        for np in jurisdiction.findall(".//NP"):
            marker = _text(np, "NO.P").strip(". ") or None
            blocks.append(Block("ruling", _text(np, "TXT", "P"), num=marker))
    return title


def _parse_judgment_contents(contents, blocks):
    for seq in contents.iter("GR.SEQ"):
        title = _text(seq, "TITLE")
        if title:
            blocks.append(Block("heading", title,
                                level=int(seq.get("LEVEL", "1"))))
    for np in contents.findall(".//NP.ECR"):
        marker = (np.get("IDENTIFIER") or "").lstrip("NP0") or None
        blocks.append(Block("paragraph", _text(np, "TXT", "P"), num=marker))


def judgment_metadata(root):
    bib = root.find("BIB.JUDGMENT")
    ecli = None
    if bib is not None:
        node = bib.find("NO.ECLI")
        ecli = node.get("ECLI") if node is not None else None
    date = root.find(".//JUDGMENT.INIT//DATE")
    return (date.get("ISO") if date is not None else None), ecli


# --------------------------------------------------------------------------
# annexes (embedded as part of the single document) and footnotes
# --------------------------------------------------------------------------

def _emit_table(tbl, blocks):
    """A TBL -> one `row` block per ROW (cells joined by ' | '); enough to keep
    the text searchable and citation-scannable without a full table model."""
    for row in tbl.iter("ROW"):
        cells = [flatten(cell) for cell in row.findall("CELL")]
        text = " | ".join(c for c in cells if c)
        if text:
            blocks.append(Block("row", text))


def walk_content(elem, blocks, level=2):
    """Generic Formex body walker for annex CONTENTS (and other free-form
    regions): headings, paragraphs, lists, tables. NOTE footnotes are left for
    collect_notes; bibliographic wrappers are recursed into."""
    for child in elem:
        tag = child.tag
        if tag in ("TITLE", "TI", "STI"):
            text = flatten(child)
            if text:
                blocks.append(Block("heading", text, level=level))
        elif tag in ("P", "ALINEA", "TXT", "NP"):
            text = flatten(child)
            if text:
                blocks.append(Block("paragraph", text))
        elif tag == "LIST":
            _emit_list(child, blocks)
        elif tag == "TBL":
            _emit_table(child, blocks)
        elif tag == "DIVISION":
            parse_division(child, level, blocks)
        elif tag == "ARTICLE":
            parse_article(child, blocks)
        elif tag in ("NOTE", "GR.NOTES", "BIB.INSTANCE"):
            pass                       # footnotes handled separately
        else:
            walk_content(child, blocks, level)   # unknown wrapper: descend


def append_annex(doc, root):
    """Embed an ANNEX Formex file into the document as a heading + its body, so
    a multi-file manifestation parses to one document."""
    title = _text(root.find("TITLE"), "TI", "P") or _text(root, "TITLE") \
        or "Bilaga"
    doc.body.append(Block("heading", title, level=1, anchor=_annex_anchor(title)))
    contents = root.find("CONTENTS")
    if contents is not None:
        walk_content(contents, doc.body)


def _annex_anchor(title):
    """Anchor for an annex heading from its number ('BILAGA III' -> 'bilaga-3',
    'ANNEX II' -> 'bilaga-2'); roman or arabic."""
    token = title.split()[-1] if title else ""
    if token.isdigit():
        return "bilaga-" + token
    try:
        return "bilaga-%d" % from_roman(token)
    except (KeyError, ValueError):
        return None


def collect_notes(root, blocks):
    """Append the root's footnotes as `note` blocks (their prose is scanned for
    citations like any other block -- the mechanical path to the act references
    that live in the footnote apparatus, since REF.DOC.OJ carries only an OJ
    coordinate, not a CELEX)."""
    for i, note in enumerate(root.iter("NOTE"), 1):
        text = flatten(note)
        if text:
            blocks.append(Block("note", text, num=str(i)))


# --------------------------------------------------------------------------
# top level
# --------------------------------------------------------------------------

def parse_formex(root, celex, lang):
    """A Formex root element -> EurlexDoc."""
    doc = EurlexDoc(celex=celex, uri=BASE % celex, doctype=doctype(celex),
                    lang=lang)
    if root.tag == "JUDGMENT":
        doc.date, doc.ecli = judgment_metadata(root)
        doc.title = parse_judgment(root, doc.body)
    elif root.tag == "ANNEX":
        # some older acts expose only an annex as their Formex manifestation;
        # render it rather than an empty page (a fuller manifestation, if any,
        # is a download-selection question)
        doc.date, doc.oj = act_metadata(root)
        doc.title = _text(root.find("TITLE"), "TI", "P") or _text(root, "TITLE")
        contents = root.find("CONTENTS")
        if contents is not None:
            walk_content(contents, doc.body)
    else:                                   # ACT (legislation, treaties)
        doc.date, doc.oj = act_metadata(root)
        doc.title = parse_act(root, doc.body)
    return doc


def parse_document(roots, celex, lang):
    """All Formex parts of a manifestation -> one EurlexDoc: the main
    act/judgment with its footnotes, then each annex embedded in order."""
    doc = parse_formex(roots[0], celex, lang)
    collect_notes(roots[0], doc.body)
    for root in roots[1:]:
        if root.tag == "ANNEX":
            append_annex(doc, root)
        else:
            walk_content(root, doc.body, level=1)
        collect_notes(root, doc.body)
    return doc


@functools.cache
def _refparser():
    """Citation scanner for EU body text: EU legislation + CJEU case law. No
    SFS vocabulary (EU references are absolute CELEX/case numbers)."""
    return LagrumParser({}, basefile="celex",
                        parse_types=[EULAGSTIFTNING, EURATTSFALL])


@functools.cache
def _namedacts():
    """The hand-edited EU named-act dataset, CELEX -> {label?, abbr?} (each a str
    or a list). Source of the established short name and the citing acronym we
    stamp onto the artifact for the document page heading."""
    return json.loads(NAMEDACTS.read_text(encoding="utf-8"))


def _first(value):
    """The dataset stores `label`/`abbr` as a str or a list (the namedacts
    convention); the page heading wants a single value -- the first when a list."""
    return value[0] if isinstance(value, list) else value


def to_artifact(doc):
    """Project to the artifact JSON: metadata + body blocks whose text is an
    inline-run list (plain runs + {predicate,uri,text} citation links). Defined
    terms are extracted first (anchoring the definition points), then every block
    is scanned both for citations and for in-act uses of those terms."""
    parser = _refparser()
    parser.reset()                          # fresh per-document state
    matcher, index = build_matcher(extract_definitions(doc.body, doc.lang),
                                   doc.lang)
    body = []
    for b in doc.body:
        cites = parser.parse_text(b.text, context={})
        # term-use links yield to a citation wherever the spans overlap (a
        # citation is the stronger, cross-document link)
        uses = [u for u in term_refs(b.text, matcher, index, doc.uri, b.anchor)
                if not any(u.start < c.end and c.start < u.end for c in cites)]
        block = {"type": b.kind, "text": interleave(b.text, cites + uses)}
        for key in ("num", "level"):
            if getattr(b, key) is not None:
                block[key] = getattr(b, key)
        # the citation anchor is the artifact `id` -- the key the catalog
        # registers fragments under and the renderer emits as the element id, so
        # a citation to `<celex>#<article>` (or `#<article>.<point>` for a
        # definition) resolves to this block
        if b.anchor is not None:
            block["id"] = b.anchor
        if b.defines is not None:
            block["defines"] = b.defines
        body.append(block)
    art = {"uri": doc.uri, "celex": doc.celex, "doctype": doc.doctype,
           "lang": doc.lang, "title": doc.title, "date": doc.date,
           "structure": nest(body)}
    # a short, distinctive human handle derived from the official title (the
    # browse index / search shows it instead of the bare CELEX). Acts (legislation
    # /treaties) only -- a judgment's "title" is the case name, already short.
    if doc.doctype != "judgment":
        label = short_label(doc.title)
        if label:
            art["label"] = label
        # the document page heading: the established short name + citing acronym.
        # The short name is the curated `label` from the named-act dataset (rare),
        # else the act's own trailing-parenthesis short title; the acronym (`abbr`)
        # is only shown when the dataset carries one. Both absent -> the page falls
        # back to the full official title (which always sits in the metadata list).
        entry = _namedacts().get(doc.celex) or {}
        shortname = _first(entry.get("label")) or official_short_title(doc.title)
        if shortname:
            art["shortname"] = shortname
        abbr = _first(entry.get("abbr"))
        if abbr:
            art["abbr"] = abbr
    if doc.ecli:
        art["ecli"] = doc.ecli
    if doc.oj:
        art["oj"] = doc.oj
    return art


# format precedence -> parser route: (filename token, route). fmx4 (richest) >
# xhtml > html > pdf (last resort). xhtml is checked before html since "html" is
# a substring of "xhtml".
_TIERS = (("fmx4.zip", "fmx4"), ("fmx4", "fmx4"), ("xhtml", "html"),
          ("html", "html"), ("pdf", "pdf"))


def _route(path):
    """(rank, parser-route) for a content file by format precedence, or None."""
    for rank, (token, route) in enumerate(_TIERS):
        if token in path.name:
            return rank, route
    return None


def content_file(doc_dir, languages=LANG_PREFERENCE):
    """The best content file in a document dir as (path, lang, route), preferring
    language (swe then eng) then format (fmx4 > xhtml > html > pdf). The download
    already kept only the best format per language; this picks across what landed.
    (None, None, None) if the dir has no content file."""
    for lang in languages:
        ranked = sorted((rank, route, cand)
                        for cand in doc_dir.glob(lang + ".*")
                        if (r := _route(cand)) for rank, route in (r,))
        if ranked:
            _, route, path = ranked[0]
            return path, lang, route
    return None, None, None


def parse_content(path, route, celex, lang):
    """Dispatch a content file to its format's parser -> EurlexDoc."""
    if route == "fmx4":
        return parse_document(load_formex(path), celex, lang)
    if route == "html":
        return parse_html(path.read_bytes(), celex, lang)
    if route == "pdf":
        return parse_pdf(path, celex, lang)
    raise ValueError("no parser for route %r" % route)
