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
import io
import json
import re
import zipfile
from datetime import date
from pathlib import Path

from lxml import etree  # ty: ignore[unresolved-import]  # lxml ships no stubs

from ..lib import compress, eucasenaming, markup, patch
from ..lib.datasets import NAMEDACTS
from ..lib.errors import SkipDocument
from ..lib.eu_structure import doctype
from ..lib.lagrum import (
    EULAGSTIFTNING,
    EURATTSFALL,
    LagrumParser,
    interleave,
    yield_overlaps,
)
from ..lib.util import from_roman
from .correspond import correspondence
from .definitions import build_matcher, extract_definitions, term_refs
from .model import BASE, Block, EurlexDoc, official_short_title, short_label
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
# regions whose inner structure belongs to something other than the act's own
# outline: the verbatim quotation an amending act inserts (text of *another* act)
# and a table (a cell's list is the cell's). A list inside one of these is read as
# part of its enclosing run of text and never lifted out as a point of its own.
ATOMIC = {"QUOT.S", "TBL"}
# a numbered paragraph's marker is carried as the block's `num`, so it is not part
# of the text -- it has to be dropped explicitly for the PARAG that carries no
# ALINEA, where it would otherwise open the paragraph's prose ("1. 1. Kommittén …")
_PARAG_SKIP = SKIP_INLINE | {"NO.PARAG"}
# reading the lead text of a block that also holds nested lists: the lists are
# emitted as their own points (`_sublists`, its exact inverse), so their text must
# not be folded into the lead as well
_LEAD_SKIP = _PARAG_SKIP | {"LIST", "DLIST"}
# ... and the same for a list item, whose own marker (NO.P) is likewise carried
# as `num` rather than as the opening of its text
_ITEM_SKIP = _LEAD_SKIP | {"NO.P"}


# --------------------------------------------------------------------------
# loading the Formex source (single file or the main act of a zip bundle)
# --------------------------------------------------------------------------

RE_ROOT_TAG = re.compile(rb"<(?!\?|!)([A-Za-z][\w.]*)")


def _root_tag(data):
    """The root element name of a Formex member, read off the raw bytes (the
    first tag that is not the XML declaration, a doctype or a comment).

    Every one of the 17 067 zip manifestations has its root tag inside the
    first 4 096 bytes. A member without one is not XML this parser can read, so
    it raises rather than reading as the main act -- which is what an empty
    string did, `"" != "ANNEX"` (rule:fail-fast)."""
    m = RE_ROOT_TAG.search(data[:4096])
    if m is None:
        raise ValueError("Formex member has no root tag in its first 4 096 bytes")
    return m.group(1).decode("ascii", "replace")


def formex_members(path):
    """The raw Formex members of a downloaded manifestation as ``(name, bytes)``
    in document order (main act/judgment first, then annexes) -- a single
    ``.fmx4`` yields one member, a ``.fmx4.zip`` its sorted ``.xml`` members (the
    ``.doc.xml`` wrappers skipped). The byte-level split that `load_formex`
    parses and the patch/editor path reads the main act's source XML from.
    Reads through `compress` (a bare ``.fmx4`` is brotli-compressed on disk; a
    ``.fmx4.zip`` is stored plain, but is checked by content, not suffix).

    Filename order is OJ page order, and in 8 documents (32015R0228 among them)
    an annex is printed on an earlier page than the act itself, so the sort
    alone would lead with the annex -- the whole parse then reads the document
    *as* that annex ("BILAGA VII" for a title, the real act walked as embedded
    content). The first member that is not an ANNEX is promoted to the front;
    everything else keeps page order, so a corrigendum manifestation (CORR
    first, by design) and every already-ordered zip return byte-identically."""
    path = Path(path)
    data = compress.read_bytes(path)
    if zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = sorted(n for n in zf.namelist()
                           if n.endswith(".xml") and not n.endswith(".doc.xml"))
            if not names:
                raise ValueError("%s: zip has no Formex member" % path)
            members = [(m, zf.read(m)) for m in names]
        main = next((i for i, (_, d) in enumerate(members)
                     if _root_tag(d) != "ANNEX"), None)
        # a bundle of annexes and no act is a download that lost its main
        # member; leading with an annex is the failure this promotion exists to
        # prevent, so it raises instead (0 of 17 067 today)
        if main is None:
            raise ValueError("%s: every Formex member is an ANNEX" % path)
        return [members[main]] + members[:main] + members[main + 1:]
    return [(path.name, data)]


def load_formex(path):
    """The Formex roots of a downloaded manifestation, in document order: the
    main act/judgment first, then any annexes."""
    return [etree.fromstring(data, XML_PARSER) for _, data in formex_members(path)]


def formex_intermediate(data):
    """The main act's Formex XML as the text a patch is diffed against: one
    element per line (`lib.markup`), because a Formex manifestation ships as a
    single line -- 45 000 characters at the median, 1.2 MB at the worst -- which
    no useful diff can be cut against."""
    return markup.indent_xml(etree.fromstring(data, XML_PARSER))


def _formex_roots(path, celex):
    """`load_formex` with the act's patch applied to the *main act's* Formex XML
    (the eurlex intermediate format) before it is parsed. Annexes are not
    separately patchable; the no-patch path stays byte-identical to load_formex."""
    members = formex_members(path)
    if not patch.has_patch("eurlex", celex):
        return [etree.fromstring(data, XML_PARSER) for _, data in members]
    roots = []
    for i, (_name, data) in enumerate(members):
        if i == 0:   # the main act
            data = patch.apply("eurlex", celex,
                               formex_intermediate(data)).encode("utf-8")
        roots.append(etree.fromstring(data, XML_PARSER))
    return roots


# --------------------------------------------------------------------------
# text extraction
# --------------------------------------------------------------------------

def flatten(elem, skip=SKIP_INLINE):
    """The element's mixed text content as one string, recursively: footnote
    subtrees dropped, inline elements spliced in place, block-level children
    space-separated, element tails kept, whitespace normalised. `skip` widens the
    dropped set to the nested lists a caller emits as their own blocks."""
    parts = [elem.text or ""]
    for child in elem:
        if child.tag in skip:
            pass
        elif child.tag in ATOMIC:
            # the widened skip stops at an atomic region -- `_sublists` does not
            # descend into one either, so a list in there is neither emitted as a
            # point nor dropped from the text (an amending act's article 1 would
            # otherwise collide with the lettered points of every article it quotes)
            text = flatten(child)
            parts.append(text if child.tag in INLINE else " %s" % text)
        elif child.tag in INLINE:
            parts.append(flatten(child, skip))
        else:
            parts.append(" %s" % flatten(child, skip))
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


def _is_numbered_list(lst):
    """True for an Arabic-numbered list ("1.", "2.", ...) -- the numbering an
    article's own top-level enumeration uses, as opposed to the lettered/roman
    markers of a nested sub-list. Reads the Formex TYPE, falling back to the first
    item's marker when it is absent."""
    t = lst.get("TYPE", "").lower()
    if t:
        return t == "arab"
    marker = (lst.find("DLIST.ITEM/PREFIX") if lst.tag == "DLIST"
              else lst.find("ITEM/NP/NO.P"))
    return marker is not None and any(c.isdigit()
                                      for c in "".join(marker.itertext()))


def _sublists(holder):
    """The lists nested in a block's body, in document order: at any depth in its
    own prose (Formex wraps them in a P or an ALINEA as readily as it doesn't),
    but never inside another list, whose own emitter picks those up.

    This is the exact inverse of what `_LEAD_SKIP` drops, and the two must stay
    that way: the lead text skips a nested list at any depth, so an emitter that
    only looked one level down would lose the deeper ones outright rather than
    merely leaving them flat."""
    found = []
    for child in holder:
        if child.tag in ("LIST", "DLIST"):
            found.append(child)
        elif child.tag not in SKIP_INLINE and child.tag not in ATOMIC:
            found.extend(_sublists(child))
    return found


def _emit_sublists(holder, blocks, depth):
    """Every list nested in `holder`, as points one level deeper."""
    for sub in _sublists(holder):
        (_emit_dlist if sub.tag == "DLIST" else _emit_list)(
            sub, blocks, "point", depth)


def _marker(text):
    """A list item's structural marker from its raw label ("a)" -> "a"), or None
    when it has none. The marker is kept as the source writes it, a typographic
    bullet ("—") included -- it is what the page hangs in the margin. Whether it
    can also carry a citation anchor is the anchor grammar's call, not the
    parser's (`lib.eu_structure`)."""
    return (text or "").strip("().") or None


def _point_depth(kind, depth):
    """The `depth` a point block carries: its nesting inside another point, so the
    anchor grammar can hang it under its parent (`1.1.f.ii`) and the renderer can
    step its indent in. Only points nest; a paragraph-level enumeration carries
    none, and neither does the first point level."""
    return depth if kind == "point" and depth > 1 else None


def _sub_depth(kind, depth):
    """The depth a nested list's points sit at. `depth` counts *point* nesting,
    not list nesting: a list emitted as the article's own numbered paragraphs
    (GDPR art. 4) is not a point level, so the lettered sub-list hanging off it is
    the first one -- counting it as the second indented those points a step past
    every structurally identical point elsewhere."""
    return depth + 1 if kind == "point" else 1


def _emit_list(lst, blocks, kind="point", depth=1):
    """LIST -> a block per ITEM (its NO.P marker + its text), recursing into a
    nested sub-list as deeper points. `kind` is this level's block kind: an
    article's own numbered enumeration is paragraph-level, a lettered or nested
    sub-list is points.

    The text is everything the item says in its own right -- reading only its
    first TXT/P instead dropped whatever followed (2022/1636's annex tables sit in
    a second P), and reading it with the plain skip repeated a sub-list that
    Formex wrote inside the item's own TXT (93/104 art. 18.1 b) rather than
    beside it."""
    for item in lst.findall("ITEM"):
        np = item.find("NP")
        holder = np if np is not None else item
        blocks.append(Block(kind, flatten(holder, _ITEM_SKIP),
                            num=_marker(_text(np, "NO.P")) if np is not None else None,
                            depth=_point_depth(kind, depth)))
        _emit_sublists(holder, blocks, _sub_depth(kind, depth))


def _definition_text(term, sep, body):
    """A definition-list entry as one run of text: its defined term, the list's
    separator and the definition ("produkt: alla industriellt framställda
    produkter …"). This is the `term: definition` shape the rest of the pipeline
    already reads -- the definition extractor takes the term from it and the
    renderer emphasises that lead as the act's <dfn> -- so the two Formex ways of
    writing a definitions article (a plain LIST of "term: …" items and this
    explicit DLIST markup) reach them identically. A word separator gets spaces
    on both sides; a punctuation mark attaches to the term."""
    if not term:
        return body
    return "%s%s %s" % (term, sep, body) if sep in ":.," else \
        "%s %s %s" % (term, sep, body)


def _emit_dlist(dlist, blocks, kind="point", depth=1):
    """DLIST (Formex's definition list) -> a block per DLIST.ITEM: its PREFIX as
    the marker, its TERM and DEFINITION joined into one `term: definition` run,
    and any list nested in the definition as deeper points.

    Previously unhandled entirely, so an article written this way -- 2015/1535
    art. 1.1, the whole "produkt"/"tjänst"/"teknisk föreskrift" catalogue -- was
    flattened into a single unreadable paragraph with no points, no anchors and
    no defined terms."""
    sep = dlist.get("SEPARATOR") or ":"
    for item in dlist.findall("DLIST.ITEM"):
        prefix, term = item.find("PREFIX"), item.find("TERM")
        defn = item.find("DEFINITION")
        blocks.append(Block(
            kind,
            _definition_text(flatten(term) if term is not None else "", sep,
                             flatten(defn, _LEAD_SKIP) if defn is not None else ""),
            num=_marker(flatten(prefix)) if prefix is not None else None,
            depth=_point_depth(kind, depth)))
        if defn is not None:
            _emit_sublists(defn, blocks, _sub_depth(kind, depth))


def _stycke_units(alinea):
    """An ALINEA's content split into stycken, each as the run of sibling elements
    that says it.

    Formex writes the further sub-paragraphs of one paragraph two ways: as sibling
    ALINEAs, and -- for about a quarter of the multi-stycke paragraphs -- as
    sibling `P`s inside a single ALINEA. Both are stycken and both must anchor, or
    the citation grammar mints a `#40.2.S2` (2009/1272 art. 40.2) that the page
    does not carry. A new stycke opens at each `P` with prose of its own; what
    follows it -- a list, a table, a `P` that only wraps a list -- belongs to it.

    An ALINEA is returned whole -- the single-stycke reading, unchanged -- when it
    holds at most one such `P`, and also when it carries direct text of its own,
    which belongs to no child run and would be dropped by splitting.

    The children are held in a list and the split keyed on their *index*: lxml
    builds an element proxy on demand and frees it as soon as the last reference
    goes, so `id()` is not a stable identity across two passes over the same
    parent. Keying on it silently mis-split 80 of 152 multi-`P` ALINEAs on the
    production tree while passing green against a stdlib-ElementTree fixture,
    where element identity happens to hold."""
    children = list(alinea)
    opens = {i for i, c in enumerate(children)
             if c.tag == "P" and flatten(c, _LEAD_SKIP)}
    if len(opens) < 2 or (alinea.text or "").strip():
        return [[alinea]]
    units, current = [], []
    for i, child in enumerate(children):
        if i in opens and current:
            units.append(current)
            current = []
        current.append(child)
    return units + [current] if current else units


def _unit_text(unit, skip):
    """The text of one stycke's run of elements, joined the way `flatten` joins
    block-level siblings -- each element's tail included, since the run's text is
    everything between its members too.

    `skip` is applied to the run's own elements as well as (via `flatten`) to
    their children: `flatten` tests an element's *children* against it and not the
    element it is handed, so a `LIST` or a `NO.PARAG` that is itself a member of
    the run would otherwise be read as prose -- the list twice, once here and once
    as the points `_unit_lists` emits."""
    parts = []
    for el in unit:
        if el.tag not in skip:
            parts.append(flatten(el, skip))
        parts.append(" ".join((el.tail or "").split()))
    return " ".join(p for p in parts if p)


def _unit_lists(unit):
    """The lists this stycke owns: a list element in the run itself, plus any
    nested in its prose (`_sublists`)."""
    return [lst for el in unit
            for lst in ([el] if el.tag in ("LIST", "DLIST") else _sublists(el))]


def _emit_alinea(unit, num, blocks, stycke=None):
    """One stycke -- a run of sibling elements (`_stycke_units`) -- as a plain
    paragraph, or a lead paragraph followed by a list. A numbered list that is the
    article's own enumeration (not inside a numbered paragraph) is paragraph-level
    -- so its items nest their own points and read as "1." like the official act;
    any other list is points.

    `stycke` is the ordinal of a *second or later* sub-paragraph of the same
    numbered paragraph; the block is then a `stycke` carrying that ordinal rather
    than the paragraph's own number, which the first sub-paragraph keeps."""
    # `num` stays the *paragraph's* number throughout: it is what decides whether
    # a numbered list is the article's own enumeration (below), and a stycke
    # ordinal must not stand in for it
    kind = "stycke" if stycke else "paragraph"
    block_num = str(stycke) if stycke else num
    lists = _unit_lists(unit)
    if not lists:
        blocks.append(Block(kind, _unit_text(unit, _PARAG_SKIP), num=block_num))
        return
    # everything the block says in its own right -- reading only its direct P
    # children instead dropped whatever else it holds (2022/1636's annex tables
    # are five sixths of that act), and reading it with the plain skip repeated
    # the lists that are emitted as points below
    lead = _unit_text(unit, _LEAD_SKIP)
    # a numbered paragraph is itself a citation target ("artikel 18.1") and the
    # parent its points anchor under, so it is emitted even when it introduces its
    # list with no prose of its own -- dropping it silently reparented the points
    # onto the article ("18.a" for what the act calls 18.1 a)
    if lead or block_num:
        blocks.append(Block(kind, lead, num=block_num))
    for lst in lists:
        list_kind = ("paragraph" if num is None and _is_numbered_list(lst)
                     else "point")
        (_emit_dlist if lst.tag == "DLIST" else _emit_list)(lst, blocks, list_kind)


def parse_article(article, blocks):
    num = _article_number(article)
    title = _text(article, "TI.ART")
    subtitle = _text(article, "STI.ART")
    blocks.append(Block("article", " – ".join(t for t in (title, subtitle) if t),
                        num=num, anchor=num))
    parags = article.findall("PARAG")
    if parags:
        # an article's unnumbered PARAGs are all stycken of the article itself, so
        # their ordinals run across them: restarting per PARAG gave two blocks the
        # same `8.S1`. A numbered paragraph owns its own run and resets it.
        n = 0
        for parag in parags:
            marker = _text(parag, "NO.PARAG").strip(". ") or None
            n = _emit_stycken(parag.findall("ALINEA") or [parag], marker,
                              blocks, n)
    else:
        _emit_stycken(article.findall("ALINEA"), None, blocks, 0)


def _emit_stycken(alineas, num, blocks, start):
    """The stycken (sub-paragraphs) of one numbered paragraph, or of an article
    that has none -- the first as the container's own block, the rest as `stycke`
    blocks numbered after it. Returns the ordinal reached, for the article-level
    run `start` continues.

    Formex writes each stycke as its own ALINEA -- or as a sibling `P` inside one
    (`_stycke_units`) -- and reading only the first (`parag.find("ALINEA")`)
    silently dropped every one after it: 831 stycken across 600 sampled acts,
    2005/85 art. 9.2 losing both derogations from the rule its first stycke
    states. A stycke is a citation target in its own right ("artikel 9.2 andra
    stycket"), anchored `9.2.S2` the way SFS anchors `P2S2`."""
    units = [unit for alinea in alineas for unit in _stycke_units(alinea)]
    for i, unit in enumerate(units):
        # a numbered paragraph's first stycke is the paragraph's own block (it
        # answers to both "artikel 9.2" and "artikel 9.2 första stycket"); an
        # article's stycken are all `stycke` blocks, numbered from 1, so that the
        # unnumbered prose trailing the enacting terms -- a signature block, an
        # annex paragraph -- is not mistaken for one and given its anchor
        ordinal = i + 1 if num else start + i + 1
        _emit_alinea(unit, num, blocks,
                     stycke=None if (num and ordinal == 1) else ordinal)
    return 0 if num else start + len(units)


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


# The EEA-relevance marker is a notice printed under the title, not part of it.
# Formex normally sets it in an element of its own; in five acts it lands in
# STI instead -- 32020R0697, 32020R0699, 32020R0873, 32020R1043 and 32021R0557,
# the COVID batch -- and joining it named 32020R0699 "Rådets förordning (EU)
# 2020/699 av den 25 maj 2020 om tillfälliga åtgärder ... (Text av betydelse
# för EES)" in the catalog, the listings and search. A parenthesis alone does
# not tell the two apart: 32020H1366's "(Migration Preparedness and Crisis
# Blueprint)" is the recommendation's own short name and belongs in the title.
RE_EEA_RELEVANCE = re.compile(
    r"\(Text (av betydelse för EES|with EEA relevance)\)$")


def parse_act_body(elem, blocks):
    """An act's body blocks from `elem`'s children, descending through the
    sequence wrappers Formex nests them in.

    Formex holds the same act in three depths. An `ACT` root keeps the preamble
    and `ENACTING.TERMS` as its own children; a `GENERAL` root (what CELLAR
    serves for an act published across several OJ files) wraps them in
    `CONTENTS`, either directly -- 2004/18's divisions sit there -- or inside a
    further `GR.SEQ` sequence, as the Charter of Fundamental Rights does.
    Descending keeps one walker for all three; reading `ENACTING.TERMS` alone
    left 2004/18 and the Charter with no articles at all. A `TITLE` here always
    restates the document title, so it is not emitted as a heading."""
    for child in elem:
        if child.tag == "DIVISION":
            parse_division(child, 1, blocks)
        elif child.tag == "ARTICLE":
            parse_article(child, blocks)
        elif child.tag == "PREAMBLE":
            parse_preamble(child, blocks)
        elif child.tag == "PREAMBLE.GEN":       # the Charter's "Ingress"
            walk_content(child, blocks)
        elif child.tag in ("ENACTING.TERMS", "GR.SEQ"):
            parse_act_body(child, blocks)


def parse_act(root, blocks):
    node = root.find("TITLE")
    # the OJ's newer act-by-act Formex splits the title: TI holds only the
    # designation ("Rådets förordning (EU) 2025/390"), STI the date and
    # subject ("av den ... om ändring av ..."). The older shape keeps it all
    # in TI's P children, where flatten already joins them.
    subtitle = _text(node, "STI")
    if subtitle and RE_EEA_RELEVANCE.match(subtitle):
        subtitle = None
    title = " ".join(t for t in (_text(node, "TI"), subtitle) if t) \
        or _text(node, "P") or _text(root, "TITLE")
    body = root.find("CONTENTS") if root.tag == "GENERAL" else root
    if body is None:
        # a recorded per-document parse failure, not a broken program: raise so
        # the message survives `python -O` instead of degrading into a TypeError
        # on the walk below (rule:errors-drive-retry-use-raise)
        raise ValueError("GENERAL act %r has no CONTENTS" % title)
    parse_act_body(body, blocks)
    return title


def act_metadata(root):
    bib = root.find("BIB.INSTANCE")
    date = oj = None
    if bib is not None:
        node = bib.find(".//DATE")
        date = node.get("ISO") if node is not None else None
        ref = bib.find(".//DOCUMENT.REF")
        if ref is not None:
            # NO.OJ is zero-padded in Formex ("042"); the citable form -- and
            # the one CELLAR's OJ identifiers carry -- is unpadded ("L 42")
            coll, no = _text(ref, "COLL"), _text(ref, "NO.OJ")
            no = no.lstrip("0") or no
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
    # the ruling normally sits inside CONTENTS.JUDGMENT, not at the root
    jurisdiction = root.find(".//JURISDICTION")
    if jurisdiction is not None:
        intro = _text(jurisdiction, "INTRO")
        if intro:
            blocks.append(Block("paragraph", intro))
        for np in jurisdiction.findall(".//NP"):
            marker = _text(np, "NO.P").strip(". ") or None
            blocks.append(Block("ruling", _text(np, "TXT", "P"), num=marker))
    return title


def _seq_paragraphs(parent, blocks):
    """The free-prose contents shape opinions and hearing reports share:
    opening prose (``P``), numbered paragraphs (``NP`` = NO.P marker + TXT)
    and section groupings (``GR.SEQ`` with a TITLE heading)."""
    for el in parent:
        if el.tag == "NP":
            blocks.append(_np_paragraph(el))
        elif el.tag == "P":
            text = flatten(el)
            if text:
                blocks.append(Block("paragraph", text))
        elif el.tag == "GR.SEQ":
            heading = _text(el, "TITLE")
            if heading:
                blocks.append(Block("heading", heading, level=1))
            _seq_paragraphs(el, blocks)


def parse_opinion(root, blocks):
    """An Advocate General's opinion (Formex ``CONCLUSION``) -> body blocks. Its
    ``CONTENTS.CONCLUSION`` holds the opening prose (``P``, "Mr President, …"), the
    numbered opinion paragraphs (``NP`` = NO.P marker + TXT) and any section
    groupings (``GR.SEQ`` with a TITLE heading) -- the same shape a judgment's
    contents take. Previously unhandled (it fell through to the ACT branch, which
    found no enacting terms), so an opinion rendered as its footnotes alone (E4)."""
    contents = root.find("CONTENTS.CONCLUSION")
    title = _text(contents.find("TITLE") if contents is not None else None, "HT") \
        or _text(root, "CURR.TITLE")
    if contents is not None:
        for el in contents:
            if el.tag != "TITLE":            # the opinion's own title (= doc.title)
                _seq_paragraphs([el], blocks)
    return title


def parse_hearing_report(root, blocks):
    """A report for the hearing (Formex ``REPORT.HEARING``) -> body blocks: a
    plain ``CONTENTS`` in the opinion's prose shape. Kept because for the
    oldest ECR cases it is the only body text CELLAR holds -- and its
    "Relevant legislation" section is where the case's act citations live."""
    title = _text(root.find("TITLE"), "TI") or _text(root, "CURR.TITLE")
    contents = root.find("CONTENTS")
    if contents is not None:
        _seq_paragraphs(contents, blocks)
    return title


def _np_paragraph(np):
    """A numbered paragraph (``NP``: ``NO.P`` marker + ``TXT``) -> a paragraph
    Block. The shape opinions always use and judgments used until ca 2012,
    when judgments switched to ``NP.ECR`` with an IDENTIFIER attribute."""
    marker = _text(np, "NO.P").strip(". ") or None
    return Block("paragraph", _text(np, "TXT", "P"), num=marker)


def _judgment_nps(el):
    """The judgment body's own ``NP`` paragraphs, in document order. Does not
    descend into NP/NP.ECR (an inner NP is a quoted list item of a cited act,
    not a numbered paragraph of the judgment) nor into JURISDICTION (the
    ruling, which parse_judgment reads separately)."""
    for child in el:
        if child.tag == "NP":
            yield child
        elif child.tag not in ("NP.ECR", "JURISDICTION"):
            yield from _judgment_nps(child)


def _parse_judgment_contents(contents, blocks):
    for seq in contents.iter("GR.SEQ"):
        title = _text(seq, "TITLE")
        if title:
            blocks.append(Block("heading", title,
                                level=int(seq.get("LEVEL", "1"))))
    for np in contents.findall(".//NP.ECR"):
        marker = (np.get("IDENTIFIER") or "").lstrip("NP0") or None
        blocks.append(Block("paragraph", _text(np, "TXT", "P"), num=marker))
    # pre-2012 ECR Formex wraps the same paragraphs in plain NP instead;
    # two thirds of the judgment corpus parsed to nothing without this
    for np in _judgment_nps(contents):
        blocks.append(_np_paragraph(np))


def judgment_metadata(root):
    bib = root.find("BIB.JUDGMENT")
    ecli = None
    if bib is not None:
        node = bib.find("NO.ECLI")
        ecli = node.get("ECLI") if node is not None else None
    # the delivery date sits in the judgment's TITLE ("Domstolens dom ... den
    # 16 juli 2020") or the CURR.TITLE page header. JUDGMENT.INIT's DATEs are
    # the referral/receipt (or a cited treaty's) dates and must not stand in
    # for it -- the golden cross-check caught exactly that. Old ECR Formex has
    # an empty TITLE and no ECLI: the notice work date fills the date in
    # downstream (parse_dir); the ECLI is genuinely absent from the source.
    for holder in (root.find("TITLE"), root.find("CURR.TITLE")):
        node = holder.find(".//DATE") if holder is not None else None
        if node is not None:
            return node.get("ISO"), ecli
    return None, ecli


# --------------------------------------------------------------------------
# annexes (embedded as part of the single document) and footnotes
# --------------------------------------------------------------------------

def _emit_table(tbl, blocks):
    """A TBL -> one `row` block per ROW (cells joined by ' | '); enough to keep
    the text searchable and citation-scannable without a full table model.

    An *interior* empty cell is kept as an empty field, because in a
    correspondence table the column a value sits in is what it means: 2004/18's
    jämförelsetabell has a blank spacer column between the three repealed
    directives and the "Ny/Ändrad" column, and dropping it slides the latter
    into the former's place. Trailing empties carry no such meaning and go."""
    for row in tbl.iter("ROW"):
        cells = [flatten(cell) for cell in row.findall("CELL")]
        while cells and not cells[-1]:
            cells.pop()
        if cells:
            blocks.append(Block("row", " | ".join(cells)))


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
            # a list wrapped in prose keeps its own points (a glossary in an annex
            # reads as entries, not as one run-on paragraph)
            text = flatten(child, _LEAD_SKIP)
            if text:
                blocks.append(Block("paragraph", text))
            _emit_sublists(child, blocks, 1)
        elif tag == "LIST":
            _emit_list(child, blocks)
        elif tag == "DLIST":
            _emit_dlist(child, blocks)
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


# the anchor prefix an annex heading gets, in one place: `annotate` keys the
# ai-annotate prompt's annex cut off it, so a change to the scheme here must not
# silently stop the trimming there (rule:second-use-goes-to-lib)
ANNEX_ANCHOR = "bilaga-"


def _annex_anchor(title):
    """Anchor for an annex heading from its number ('BILAGA III' -> 'bilaga-3',
    'ANNEX II' -> 'bilaga-2'); roman or arabic."""
    token = title.split()[-1] if title else ""
    if token.isdigit():
        return ANNEX_ANCHOR + token
    try:
        return ANNEX_ANCHOR + "%d" % from_roman(token)
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
    elif root.tag == "CONCLUSION":          # an Advocate General's opinion (E4)
        doc.date, doc.ecli = judgment_metadata(root)
        doc.title = parse_opinion(root, doc.body)
    elif root.tag == "REPORT.HEARING":
        # for the oldest cases (Beentjes) the report for the hearing is the
        # only text CELLAR holds; its "Relevant legislation" section carries
        # the act citations the rail joins on, so it stands in for the
        # judgment body rather than rendering an empty page
        doc.date, doc.ecli = judgment_metadata(root)
        doc.title = parse_hearing_report(root, doc.body)
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
def _refparser(lang="swe"):
    """Citation scanner for EU body text: EU legislation + CJEU case law. No
    SFS vocabulary (EU references are absolute CELEX/case numbers). `lang`
    "eng" loads the English citation surface -- pre-accession case law exists
    in no Swedish version, so those documents are parsed from their English
    manifestation ("Article 29 (5) of Directive 71/305/EEC")."""
    return LagrumParser({}, basefile="celex",
                        parse_types=[EULAGSTIFTNING, EURATTSFALL], lang=lang)


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


def _isodate(value):
    """Formex DATE@ISO is compact ('20200716'); the artifact carries the dashed
    ISO form (what the page shows and what CELLAR's work date uses)."""
    if value and re.fullmatch(r"\d{8}", value):
        return "%s-%s-%s" % (value[:4], value[4:6], value[6:8])
    return value


def to_artifact(doc):
    """Project to the artifact JSON: metadata + body blocks whose text is an
    inline-run list (plain runs + {predicate,uri,text} citation links). Defined
    terms are extracted first (anchoring the definition points), then every block
    is scanned both for citations and for in-act uses of those terms."""
    parser = _refparser("eng" if doc.lang == "eng" else "swe")
    parser.reset()                          # fresh per-document state
    # a legislative act's own body cites its own articles by a bare "artikel N";
    # tell the parser its identity so those self-refer to it rather than
    # anaphora-pinning onto an external act a recital named (a judgment has no
    # such self-act -- its bare articles do refer to the act under discussion)
    if doc.doctype != "judgment":
        parser.state.self_eu_act = doc.celex
    matcher, index = build_matcher(extract_definitions(doc.body, doc.lang),
                                   doc.lang)
    body = []
    for b in doc.body:
        cites = parser.parse_text(b.text, context={})
        # term-use links yield to a citation wherever the spans overlap (a
        # citation is the stronger, cross-document link)
        uses = yield_overlaps(
            term_refs(b.text, matcher, index, doc.uri, b.anchor), cites)
        block = {"type": b.kind, "text": interleave(b.text, cites + uses)}
        for key in ("num", "level", "depth"):
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
           "lang": doc.lang, "title": doc.title, "date": _isodate(doc.date),
           "structure": nest(body)}
    # a short, distinctive human handle shown instead of the bare CELEX (the page
    # heading, the browse index / search, an inbound-citation label). The two
    # document families derive it differently:
    if doc.doctype == "judgment":
        # a case: its Formex "title" is "Domstolens dom (...) den ..." -- no use
        # as a name. The heading is the case's usual name / case number
        # ("Schrems II", "C-176/09"); an inbound citation adds the case number
        # ("C-311/18 (Schrems II)"). Stamped from lib.eucasenaming so the pure
        # catalog + renderer read them off the artifact without recomputing.
        art["shortname"] = eucasenaming.case_name(doc.celex)
        art["label"] = eucasenaming.case_citation(doc.celex)
    else:
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
    """(rank, parser-route) for a content file by format precedence, or None.

    Matches the exact trailing suffix (e.g. ".fmx4", ".fmx4.zip"), not a bare
    substring: a stale `swe.fmx4.tmp` left behind by a hard-killed download
    (write_atomic's temp file, orphaned when the process dies before the
    rename) must not be mistaken for a real `.fmx4` content file."""
    for rank, (token, route) in enumerate(_TIERS):
        if path.name.endswith("." + token):
            return rank, route
    return None


def content_file(doc_dir, languages=LANG_PREFERENCE):
    """The best content file in a document dir as (path, lang, route), preferring
    language (swe then eng) then format (fmx4 > xhtml > html > pdf). The download
    already kept only the best format per language; this picks across what landed.
    (None, None, None) if the dir has no content file."""
    for lang in languages:
        ranked = sorted((rank, route, cand)
                        for cand in compress.glob(doc_dir, lang + ".*")
                        if (r := _route(cand)) for rank, route in (r,))
        if ranked:
            _, route, path = ranked[0]
            return path, lang, route
    return None, None, None


def parse_content(path, route, celex, lang):
    """Dispatch a content file to its format's parser -> EurlexDoc."""
    if route == "fmx4":
        return parse_document(_formex_roots(path, celex), celex, lang)
    if route == "html":
        data = compress.read_bytes(path)
        if patch.has_patch("eurlex", celex):
            data = patch.apply("eurlex", celex, markup.block_lines(
                data.decode("utf-8", "replace"))).encode("utf-8")
        return parse_html(data, celex, lang)
    if route == "pdf":
        return parse_pdf(path, celex, lang)
    raise ValueError("no parser for route %r" % route)


# the work date line in a stored notice.ttl, in both its shapes: the live
# path's synthesized n-triples ('<...cdm#work_date_document> "2016-04-27"^^...')
# and the bulk unpacker's turtle subset ('j.0:work_date_document "1982-03-31"^^...')
RE_NOTICE_WDATE = re.compile(r'work_date_document>?\s+"(\d{4}-\d{2}-\d{2})')


def notice_work_date(doc_dir):
    """The CELLAR work date kept in the document dir's notice.ttl, or None.
    The authoritative document date for a manifestation that carries none of
    its own (old ECR judgment Formex has an empty TITLE; pre-2004 OJ html has
    no bibliographic markup)."""
    path = Path(doc_dir) / "notice.ttl"
    if not compress.exists(path):
        return None
    m = RE_NOTICE_WDATE.search(compress.read_bytes(path).decode("utf-8", "replace"))
    return m.group(1) if m else None


# a corrigendum CELEX: the parent act's number + 'R(NN)'
RE_CORRIGENDUM = re.compile(r"R\(\d+\)$")


def _plausible_date(value):
    """A Formex DATE@ISO can be garbled at digitisation (61981CJ0025 carries
    '19820231' -- the 31st of February); an impossible calendar date cannot be
    the document's, so it yields to the notice work date."""
    try:
        date.fromisoformat(_isodate(value))
        return True
    except ValueError:
        return False


# Acts this corpus deliberately does not carry, CELEX -> why. `parse_dir` raises
# `SkipDocument` for them, and the driver writes the empty artifact that marks a
# document built-and-not-to-be-retried: the catalog then drops its row
# (`catalog`: an artifact with no content is not a document) and the index its
# units. It does **not** remove an already-rendered page -- nothing in the driver
# reaps `generated/`, so uncarrying an act that was previously carried means
# unlinking its html by hand, here and on prod.
#
# The bar is *the document cannot be served*, not "it is awkward": an act that
# merely parses badly is a bug to fix, and one that is genuinely repealed or empty
# already has its own path. Each entry states the measurement that meets that bar,
# in terms that stay true -- a fact about one build does not -- and enough for a
# later reader to retest the claim rather than inherit it. The downloaded Formex
# stays on disk, so the retest is always: drop the entry, `lagen eurlex parse
# <celex>`, reindex, and look for the failure named below.
UNCARRIED = {
    "32018R0688":
        "annex I is a 6,000-page table of the EBA's reference portfolios: a 97 MB "
        "Formex file that parses to 50,493,892 characters and renders to a 53 M "
        "character page. OpenSearch's JSON parser refuses a string field past "
        "50,000,000 -- bracketed against a live cluster, 50 MB accepted and 52 MB "
        "rejected with mapper_parsing_exception -- so the whole-document unit and "
        "its bilaga-1 fragment can never be indexed, and the page is past what a "
        "reader can be served in any case. Nothing in the corpus cites it (0 rows "
        "in `links`), so dropping it dangles no reference",
}


def parse_dir(doc_dir, celex):
    """A document dir -> artifact dict: the best content file parsed, the
    notice work date filling in a missing or impossible document date. None
    when the dir has no swe/eng content -- the parse pipeline's single entry
    point per CELEX.

    A corrigendum's Formex bibliography carries the *corrected act's* date, not
    its own; its notice work date (the correcting OJ's publication) is the
    document's actual date, so it wins there.

    An `UNCARRIED` act raises SkipDocument before anything is opened: its source
    is on disk and will never be servable, so the driver's empty-artifact marker
    is the honest outcome -- the alternative is a per-document failure on every
    build, forever, which only teaches the operator to ignore a red exit."""
    if celex in UNCARRIED:
        raise SkipDocument("%s: %s" % (celex, UNCARRIED[celex]))
    path, lang, route = content_file(doc_dir)
    if path is None:
        return None
    doc = parse_content(path, route, celex, lang)
    if (doc.date is None or not _plausible_date(doc.date)
            or RE_CORRIGENDUM.search(celex)):
        doc.date = notice_work_date(doc_dir) or doc.date
    art = to_artifact(doc)
    # the act's own jämförelsetabell, read *after* to_artifact because the
    # header's "Direktiv 2004/18/EG" is identified by the citation link minted
    # there. Empty for all but ~2% of sector-3 acts and every judgment, so this
    # costs nothing on the rest (correspond.correspondence).
    edges, _stats = correspondence(art)
    if edges:
        art["correspondence"] = edges
    return art
