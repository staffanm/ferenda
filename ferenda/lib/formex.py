"""Read a Formex manifestation -- the structured XML the Publications Office
publishes an EU document as -- into an ordered list of typed blocks.

Formex has several roots: `ACT` (regulations, directives, decisions, treaties,
and the ESRB's own beslut), `GENERAL` (a notice printed in the C series),
`JUDGMENT` and `CONCLUSION` (Court of Justice case law), `REPORT.HEARING` and
`ANNEX`. Each carries a bibliographic header, an optional preamble (recitals +
visas) and a body (enacting terms / judgment contents + ruling). This module
walks the known structure into `Block`s; inline markup (highlights, dates, OJ
references) is flattened to text and footnote NOTEs are dropped from the running
text and re-emitted as `note` blocks.

It lives in lib/ because two sources read Formex: `eurlex`, for the acts and
case law CELLAR holds under their CELEX, and `guidance`, for the ECB's and the
ESRB's soft law, which those bodies publish in EUT rather than on their own
sites (rule:second-use-goes-to-lib). Neither the block vocabulary nor the
walking is source-specific -- each source projects these blocks onto its own
document model.

A `.fmx4.zip` manifestation bundles the main act with its annexes as separate
Formex files; `formex_members` returns them in document order and
`append_annex` embeds each annex into the main document's blocks.
"""

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from lxml import etree  # ty: ignore[unresolved-import]  # lxml ships no stubs

from . import compress, markup, patch
from .util import from_roman


@dataclass
class Cell:
    """One table cell: its text, and the rows/columns it spans in the printed
    table. A cell that spans is written once and the rows it covers omit it --
    Formex and HTML agree on that, so the spans ride to the page unchanged."""
    text: str
    rowspan: int = 1
    colspan: int = 1


@dataclass
class Row:
    cells: list[Cell]
    header: bool = False       # a ROW/CELL the source marks TYPE="HEADER"


@dataclass
class Block:
    kind: str                  # see KINDS below
    text: str
    num: str | None = None     # structural marker: recital "(1)", article "1",
                               # paragraph "2", point "a"
    level: int | None = None   # a heading's division depth (1 = outermost)
    depth: int | None = None   # a point's nesting inside another point (unset =
                               # the first point level, 2 = inside a point, ...),
                               # which hangs its anchor under its parent's
                               # ("1.1.f.ii") and steps its indent in
    label: str | None = None   # a heading's or article's own designation, kept
                               # apart from its title: the sources set them as
                               # separate elements (Formex TI/STI, TI.ART/
                               # STI.ART) and the page hangs the designation in
                               # a gutter beside the title. None where the
                               # heading carries no designation of its own.
    anchor: str | None = None  # citation-target fragment (e.g. article "5")
    defines: str | None = None # a definitions-article point: the term it defines
    quoted: str | None = None  # a `citat` block: the kind it had inside the
                               # quotation -- "recital", "paragraph", "point",
                               # "article", "heading" or "row" -- which is what
                               # its marker's punctuation follows ("(6)", "1.",
                               # "a)") and what the page keys its class off
                               # (`citat-heading`). Kept as the kind rather
                               # than as the finished marker because the
                               # punctuation is presentational and settled by
                               # the renderer (see `_eurlex_marker`).
    rows: list[Row] | None = None  # a `tabell` block: its rows. The block's
                               # `text` is then the table's own caption (its
                               # Formex TITLE), not its contents -- the shape
                               # the artifact's `tabell`/`rad` nodes already
                               # have across the other sources.


# the manifestation is remote-supplied: no DTD/entity expansion (stdlib
# ElementTree would expand nested entities unbounded); comments/PIs removed so
# the element walks see only real elements (ElementTree dropped them, lxml
# keeps them by default)
XML_PARSER = etree.XMLParser(resolve_entities=False, load_dtd=False,
                             no_network=True, remove_comments=True,
                             remove_pis=True)

# the consolidated-act route keeps processing instructions: the per-provision
# provenance of a CONSLEG text rides as `CLG.MDFO`/`CLG.MDFC` PI pairs
# bracketing every amended span, which XML_PARSER's remove_pis discards.
# `cons_provenance` reads them off this tree; the block walk then runs on the
# same tree after `strip_pis` removes them, so the emitters never see a PI
# (flatten would read a PI's pseudo-attributes as body text).
CONS_PARSER = etree.XMLParser(resolve_entities=False, load_dtd=False,
                              no_network=True, remove_comments=True,
                              remove_pis=False)

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


def formex_roots(path, source, key):
    """`load_formex` with the document's patch applied to the *main act's*
    Formex XML (the eurlex intermediate format) before it is parsed. Annexes are
    not separately patchable; the no-patch path stays byte-identical to
    load_formex.

    `source`/`key` name the patch: eurlex patches an act by its CELEX, guidance
    a document by its basefile, and the patch store keys on the pair."""
    members = formex_members(path)
    if not patch.has_patch(source, key):
        return [etree.fromstring(data, XML_PARSER) for _, data in members]
    roots = []
    for i, (_name, data) in enumerate(members):
        if i == 0:   # the main act
            data = patch.apply(source, key,
                               formex_intermediate(data)).encode("utf-8")
        roots.append(etree.fromstring(data, XML_PARSER))
    return roots


# --------------------------------------------------------------------------
# text extraction
# --------------------------------------------------------------------------

def flatten(elem, skip=SKIP_INLINE, drop=()):
    """The element's mixed text content as one string, recursively: footnote
    subtrees dropped, inline elements spliced in place, block-level children
    space-separated, element tails kept, whitespace normalised. `skip` widens the
    dropped set to the nested lists a caller emits as their own blocks.

    `drop` leaves out `elem`'s children at those *indices*, for text a caller
    emits elsewhere -- the block quotations `_lifted_quotations` finds. By
    index rather than by element because lxml frees an element proxy as soon as
    the last reference to it goes and builds a new one on the next pass (the
    trap `_stycke_units` documents), and by a parameter here rather than a
    second copy of this walk because the two would then have to be kept in step
    by hand."""
    parts = [elem.text or ""]
    for i, child in enumerate(elem):
        if i in drop or child.tag in skip:
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
    or, failing that, the title ('Artikel 5' -> '5').

    An inserted article's IDENTIFIER writes its letter in capitals ('005A')
    where the act itself prints it lowercase ('Artikel 5a') -- and lowercase is
    how every citation, dotted anchor and `.ann` key writes it, so the printed
    form wins where the two agree letter-for-letter. An identifier the title
    does not corroborate is kept as written."""
    ident = article.get("IDENTIFIER")
    if ident and ident.lstrip("0"):
        num = ident.lstrip("0")
        if not num.isdigit():
            m = re.search(r"(\d+\s?[^\W\d_]{1,2})\s*$",
                          _text(article, "TI.ART"))
            printed = m.group(1).replace(" ", "") if m else ""
            if printed.lower() == num.lower():
                return printed
        return num
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
        # an amending act writes its instructions as list items whose quoted
        # replacement text is a block-level QUOT.S ("5. Följande artiklar ska
        # införas:" + the articles 5a-5f). Lifted as `citat` blocks the same way
        # a judgment's quotations are, instead of folding 28,000 characters of
        # another act into the item's own text
        drop, quotations = _lifted_quotations(holder)
        blocks.append(Block(kind, flatten(holder, _ITEM_SKIP, drop=drop),
                            num=_marker(_text(np, "NO.P")) if np is not None else None,
                            depth=_point_depth(kind, depth)))
        for quotation in quotations:
            parse_quotation(quotation, blocks)
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


def _lifted_unit(unit):
    """A stycke run's own block quotations, split off: `(the run without them,
    the QUOT.S elements)`. The same direct-child / whole-of-a-`P` shapes
    `_lifted_quotations` lifts inside a case-law paragraph, applied to the run
    of sibling elements a stycke is -- an amending act's "Artikel 19 ska
    ersättas med följande:" writes the replacement text as a `P`-wrapped
    QUOT.S beside the lead-in. An element with a tail of its own stays: the
    tail is prose between the run's members, and dropping the element drops it."""
    kept, quotations = [], []
    for el in unit:
        if el.tag == "QUOT.S":
            quotation = el
        elif (el.tag == "P" and len(el) == 1 and el[0].tag == "QUOT.S"
              and not (el.text or "").strip()
              and not (el[0].tail or "").strip()):
            quotation = el[0]
        else:
            kept.append(el)
            continue
        if (el.tail or "").strip():
            kept.append(el)
            continue
        quotations.append(quotation)
    return kept, quotations


def _emit_alinea(unit, num, blocks, stycke=None):
    """One stycke -- a run of sibling elements (`_stycke_units`) -- as a plain
    paragraph, or a lead paragraph followed by a list. A numbered list that is the
    article's own enumeration (not inside a numbered paragraph) is paragraph-level
    -- so its items nest their own points and read as "1." like the official act;
    any other list is points. A block-level quotation in the run (`_lifted_unit`)
    is emitted after it as `citat` blocks that keep the quoted act's structure.

    `stycke` is the ordinal of a *second or later* sub-paragraph of the same
    numbered paragraph; the block is then a `stycke` carrying that ordinal rather
    than the paragraph's own number, which the first sub-paragraph keeps."""
    # `num` stays the *paragraph's* number throughout: it is what decides whether
    # a numbered list is the article's own enumeration (below), and a stycke
    # ordinal must not stand in for it
    kind = "stycke" if stycke else "paragraph"
    block_num = str(stycke) if stycke else num
    unit, quotations = _lifted_unit(unit)
    lists = _unit_lists(unit)
    if not lists:
        text = _unit_text(unit, _PARAG_SKIP)
        # a run that was nothing but its quotation emits no empty block --
        # unless it carries a number, which is a citation target either way
        if text or block_num:
            blocks.append(Block(kind, text, num=block_num))
        for quotation in quotations:
            parse_quotation(quotation, blocks)
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
    for quotation in quotations:
        parse_quotation(quotation, blocks)


def parse_article(article, blocks):
    num = _article_number(article)
    # TI.ART is the article's designation ("Artikel 5"), STI.ART its title --
    # which most articles do not have. Kept apart: the page hangs the
    # designation in a gutter beside the title (see parse_division).
    blocks.append(Block("article", _text(article, "STI.ART"), num=num, anchor=num,
                        label=_text(article, "TI.ART") or None))
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
    # the source keeps the designation and the title apart -- <TI>KAPITEL I</TI>
    # beside <STI>ALLMÄNNA BESTÄMMELSER</STI> -- and the page sets them apart
    # too, so the block keeps them apart rather than flattening the pair into
    # one run. A division with only a TITLE has no designation of its own.
    node = division.find("TITLE")
    designation, title = _text(node, "TI"), _text(node, "STI")
    if not title:
        designation, title = None, _text(division, "TITLE")
    if title:
        blocks.append(Block("heading", title, level=level, label=designation))
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
# CONS.ACT (a consolidated act -- the sector-0 CONSLEG text the Publications
# Office maintains, with every amendment folded in)
# --------------------------------------------------------------------------

def load_cons(path):
    """The Formex roots of a downloaded consolidation, main `CONS.ACT` first,
    with the provenance PIs still in the tree (see CONS_PARSER). Read the
    register and the spans off the first root, then `strip_pis` before the
    block walk. A CONSLEG zip normally holds one member (the annexes ride
    inline as CONS.ANNEX); any further members parse like an act's."""
    roots = [etree.fromstring(data, CONS_PARSER)
             for _, data in formex_members(path)]
    if roots[0].tag != "CONS.ACT":
        raise ValueError("%s: expected a CONS.ACT root, got %s"
                         % (path, roots[0].tag))
    return roots


def strip_pis(root):
    """Remove every processing instruction from the tree, merging tails, so
    the block emitters see only real elements -- `flatten` would otherwise
    read a PI's pseudo-attributes as body text."""
    etree.strip_tags(root, etree.ProcessingInstruction)


def parse_cons_act(root, blocks):
    """A consolidated act -> title + body blocks: descend to `CONS.DOC`, walk
    its (empty) preamble and enacting terms with the plain act walkers, then
    embed each `CONS.ANNEX` -- a TITLE + CONTENTS pair, the exact shape of a
    separate ANNEX file. The caller strips the PIs first."""
    doc = root.find("CONS.DOC")
    if doc is None:
        # a recorded per-document parse failure, not a broken program
        # (rule:errors-drive-retry-use-raise)
        raise ValueError("CONS.ACT has no CONS.DOC")
    title = parse_act(doc, blocks)
    for annex in doc.findall("CONS.ANNEX"):
        append_annex(blocks, annex)
    return title


def cons_metadata(root):
    """(consolidation date, act date, OJ ref) of a consolidated act, each as
    Formex writes it ('20241018'). The consolidation date is INFO.CONSLEG's
    START.DATE -- the day this wording began to apply, the version key CELLAR
    itself uses in the sector-0 CELEX. The act date and the OJ coordinate come
    from the base act's own bibliography in FAM.COMP: the consolidation is the
    same act at another moment, so the document keeps the act's date."""
    info = root.find("INFO.CONSLEG")
    start = info.get("START.DATE") if info is not None else None
    bib = root.find("CONS.DOC/FAM.COMP/BIB.DATA")
    date = oj = None
    if bib is not None:
        node = bib.find("DATE")
        date = node.get("ISO") if node is not None else None
        ref = bib.find(".//DOCUMENT.REF.CONS")
        if ref is not None:
            coll, no = _text(ref, "COLL"), _text(ref, "NO.OJ")
            no = no.lstrip("0") or no
            oj = ("%s %s" % (coll, no)).strip() or None
    return start, date, oj


def _mod_entry(bib):
    """One FAM.COMP act entry -> {celex, date?, title?}."""
    entry = {"celex": _expand_celex(_text(bib, "NO.CELEX"))}
    node = bib.find("DATE")
    if node is not None and node.get("ISO"):
        entry["date"] = node.get("ISO")
    title = bib.find("TITLE")
    if title is not None:
        entry["title"] = flatten(title)
    return entry


def cons_register(root):
    """`FAM.COMP` -> the amendment register, ready-made: the base act, one
    entry per amending act (`GR.MOD.ACT`), and the corrigenda folded in
    (`GR.CORRIG`, kept apart -- a corrigendum corrects the text, it does not
    amend the law)."""
    fam = root.find("CONS.DOC/FAM.COMP")
    if fam is None:
        raise ValueError("CONS.ACT has no FAM.COMP amendment register")
    # a corrigendum hangs off whichever act it corrects -- the base act's sit
    # in the register's own GR.CORRIG, an amending act's inside its MOD.ACT --
    # so they are collected at any depth
    return {"base": _expand_celex(_text(fam.find("BIB.DATA"), "NO.CELEX"))
                    or None,
            "amending": [_mod_entry(mod.find("BIB.DATA"))
                         for mod in fam.findall("GR.MOD.ACT/MOD.ACT")],
            "corrigenda": [_expand_celex(_text(corr.find("BIB.DATA"),
                                               "NO.CELEX"))
                           for corr in fam.iter("CORRIG")]}


# the pseudo-attributes of a CLG.MDFO provenance PI ('ACTION="INSERTED"
# ACTIVE.DOC="32024R1183" ...')
RE_PI_ATTRS = re.compile(r'([\w.]+)="([^"]*)"')

# the older CELEX shape CONSLEG provenance and registers write for a
# sector-3 act: a two-digit year ('306L0138', '306L0112R(02)')
RE_SHORT_CELEX = re.compile(r"^3(\d{2})([A-Z])(\d{3,4})(R\(\d+\))?$")


def _expand_celex(celex):
    """A CONSLEG-written CELEX in the modern form the corpus keys on --
    '306L0138' -> '32006L0138', corrigendum suffix kept. 1952 is sector 3's
    first year, so a two-digit year 52-99 is 19xx and the rest 20xx. Any
    other shape (already modern, or a treaty like '12012J/ACT') passes
    through unchanged."""
    m = RE_SHORT_CELEX.match(celex)
    if not m:
        return celex
    yy, letter, number, rev = m.groups()
    return "3%s%s%s%s%s" % ("19" if int(yy) >= 52 else "20", yy,
                            letter, number.zfill(4), rev or "")


def _pi_attrs(pi):
    return dict(RE_PI_ATTRS.findall(pi.text or ""))


def cons_provenance(root):
    """article number -> what the consolidation says changed it:
    ``{"action": "replaced"|"inserted"|"deleted"|"amended", "by": [celex, ...]}``.

    Every amended span is bracketed by a `CLG.MDFO`/`CLG.MDFC` PI pair whose
    `ACTIVE.DOC` names the amending act (a corrigendum included) -- properly
    nested, so a plain stack pairs them. A span *covering* an article (open at
    the article's start) decides its action: the innermost one, lowercased. An
    article touched only by spans *inside* it (a replaced paragraph, an
    inserted point) is `amended`. `by` lists every act the article's spans
    name, covering spans first.

    `ACTIVE.LOC` is deliberately not read: it is precise to the point of the
    amending act, and one point inserts several articles (`AR:1;PT:5` covers
    5a-5f), so it cannot key anything here."""
    per_article = {}

    def note(num, kind, span):
        entry = per_article.setdefault(num, {"covering": [], "inside": []})
        entry[kind].append(span)

    def walk(el, stack, article):
        for child in el:
            if isinstance(child, etree._ProcessingInstruction):
                if child.target == "CLG.MDFO":
                    stack.append(_pi_attrs(child))
                    if article is not None:
                        note(article, "inside", stack[-1])
                elif child.target == "CLG.MDFC":
                    if not stack:
                        # a close with no open span breaks the pairing this
                        # walk stands on; guessing on would mis-attribute
                        # every later article's provenance, so the document
                        # records a parse failure instead
                        # (rule:errors-drive-retry-use-raise)
                        raise ValueError(
                            "CLG.MDFC with no open CLG.MDFO span (%s)"
                            % (child.text or "").strip())
                    stack.pop()
            elif not isinstance(child.tag, str):
                continue
            elif child.tag == "ARTICLE":
                num = _article_number(child)
                for span in stack:
                    note(num, "covering", span)
                walk(child, stack, num)
            else:
                walk(child, stack, article)

    stack = []
    walk(root, stack, None)
    if stack:
        # the mirror of the stray-close guard: a span that never closes has
        # already marked every article after its open point as covered by it
        raise ValueError("unclosed CLG.MDFO span(s): %s"
                         % ", ".join(s.get("ID", "?") for s in stack))
    out = {}
    for num, spans in per_article.items():
        covering, inside = spans["covering"], spans["inside"]
        action = (covering[-1].get("ACTION", "").lower() if covering
                  else "amended")
        by = []
        for span in covering + inside:
            doc = _expand_celex(span.get("ACTIVE.DOC") or "")
            if doc and doc not in by:
                by.append(doc)
        out[num] = {"action": action, "by": by}
    return out


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
        _case_contents(contents, blocks)
    # the ruling normally sits inside CONTENTS.JUDGMENT, not at the root
    jurisdiction = root.find(".//JURISDICTION")
    if jurisdiction is not None:
        intro = _text(jurisdiction, "INTRO")
        if intro:
            blocks.append(Block("paragraph", intro))
        for np in jurisdiction.findall(".//NP"):
            marker, text = _numbered_text(np)
            blocks.append(Block("ruling", text, num=marker))
    return title


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
        # a TITLE here is the opinion's own (= doc.title), not a section heading
        _case_contents([el for el in contents if el.tag != "TITLE"], blocks)
    return title


def parse_hearing_report(root, blocks):
    """A report for the hearing (Formex ``REPORT.HEARING``) -> body blocks: a
    plain ``CONTENTS`` in the opinion's prose shape. Kept because for the
    oldest ECR cases it is the only body text CELLAR holds -- and its
    "Relevant legislation" section is where the case's act citations live."""
    title = _text(root.find("TITLE"), "TI") or _text(root, "CURR.TITLE")
    contents = root.find("CONTENTS")
    if contents is not None:
        _case_contents(contents, blocks)
    return title


def _numbered_text(elem, drop=()):
    """A case-law numbered paragraph -> (marker, text): its own ``NO.P`` marker,
    which the block carries as `num`, and everything else it says.

    The text is the whole element, not its first ``TXT`` alone. A paragraph that
    introduces a quotation writes the lead-in as ``TXT`` and the quoted act as a
    ``P`` beside it, so reading only the first dropped the quotation -- T-59/11
    lost the whole of the FP6 general conditions it quotes. `_emit_list`
    documents the same trap for a list item.

    The marker comes off the front of that text rather than out of `flatten`'s
    skip set, which works by tag name: dropping ``NO.P`` everywhere would also
    erase the "a)", "b)" of a list the paragraph quotes.

    `drop` names the children whose text is emitted elsewhere -- the block
    quotations `_lifted_quotations` finds."""
    no = elem.find("NO.P")
    marker = flatten(no) if no is not None else ""
    text = flatten(elem, drop=drop)
    if marker and text.startswith(marker):
        text = text[len(marker):].lstrip()
    return marker.strip(". ") or None, text


def _lifted_quotations(np):
    """The quotations a case-law numbered paragraph introduces, as `(indices of
    the children to leave out of the paragraph's own text, the ``QUOT.S``
    elements)`.

    A quotation counts as its own block when it is a direct child of the
    paragraph or the whole of a direct ``P`` child -- 201 732 of the 205 212
    inside a numbered paragraph. The rest sit *in* a sentence (inside the
    lead-in ``TXT``, or beside prose in a ``P``); those are part of what the
    paragraph says and stay in its text."""
    drop, quotations = set(), []
    for i, child in enumerate(np):
        if child.tag == "QUOT.S":
            quotation = child
        elif (child.tag == "P" and len(child) == 1 and child[0].tag == "QUOT.S"
              and not (child.text or "").strip()
              and not (child[0].tail or "").strip()):
            quotation = child[0]
        else:
            continue
        drop.add(i)
        quotations.append(quotation)
    return drop, quotations


def _numbered_paragraph(el, blocks, marker=None):
    """A case-law numbered paragraph -> the judgment's (or opinion's) own block,
    followed by the quotations it introduces. `marker` overrides the element's
    own ``NO.P`` -- ``NP.ECR`` carries its number in an attribute instead.

    The paragraph is emitted even when it says nothing of its own beyond the
    quotation, because its number is what the judgment is cited by."""
    drop, quotations = _lifted_quotations(el)
    own, text = _numbered_text(el, drop)
    blocks.append(Block("paragraph", text, num=marker or own))
    for quotation in quotations:
        parse_quotation(quotation, blocks)


# a quotation's blocks are all retyped to this one kind. The text of *another*
# act reproduced inside a judgment paragraph is not part of the judgment's own
# outline, and a block that kept its act kind would be read as one: `nest` would
# open an article context on a quoted `article` and hang every judgment
# paragraph after it inside that article, and the anchor grammar would mint
# sub-article ids for text this document does not contain.
QUOTATION = "citat"


def _quoted_unit(el, blocks):
    """The children of one element of a quoted act, through the act's own
    emitters -- a quotation holds exactly the shapes an act holds."""
    for child in el:
        tag = child.tag
        if tag == "ARTICLE":
            # the designation and title, then the article's own paragraphs
            # through the quotation's emitter. `parse_article` cannot be reused
            # here: it numbers stycken, and "2."/"3." are ordinals the quoted
            # act never prints -- invented numbering attributed to another
            # document, which is exactly what the retype exists to prevent
            blocks.append(Block("article", _text(child, "STI.ART"),
                                label=_text(child, "TI.ART") or None))
            for parag in child.findall("PARAG"):
                _emit_quoted_alineas(parag.findall("ALINEA") or [parag],
                                     _text(parag, "NO.PARAG").strip(". ")
                                     or None, blocks)
            if not child.findall("PARAG"):
                _emit_quoted_alineas(child.findall("ALINEA"), None, blocks)
        elif tag == "PARAG":
            _emit_quoted_alineas(child.findall("ALINEA") or [child],
                                 _text(child, "NO.PARAG").strip(". ") or None,
                                 blocks)
        elif tag == "CONSID":
            # a quoted recital numbers itself "(6)" inside its own NP, the way
            # `parse_preamble` reads it for an act: the number is the block's
            # marker, not the opening of its text
            np = child.find("NP")
            if np is None:
                _emit_quoted_alineas([child], None, blocks)
            else:
                marker, text = _numbered_text(np)
                blocks.append(Block("recital", text,
                                    num=(marker or "").strip("()") or None))
        elif tag in ("ALINEA", "P", "TXT"):
            _emit_quoted_alineas([child], None, blocks)
        elif tag == "NP":
            marker, text = _numbered_text(child)
            blocks.append(Block("paragraph", text, num=marker))
        elif tag == "LIST":
            _emit_list(child, blocks)
        elif tag == "DLIST":
            _emit_dlist(child, blocks)
        elif tag == "TBL":
            _emit_table(child, blocks)
        elif tag in ("TITLE", "TI", "STI"):
            text = flatten(child)
            if text:
                blocks.append(Block("heading", text, level=1))
        elif tag in ("NOTE", "GR.NOTES", "BIB.INSTANCE"):
            pass
        else:                     # GR.SEQ, DIVISION, GR.CONSID, a nested QUOT.S
            _quoted_unit(child, blocks)


def _emit_quoted_alineas(alineas, marker, blocks):
    """The stycken of one quoted paragraph: the first carries the quoted act's
    own marker ("1."), the rest none. A quotation reproduces the act's
    numbering, and a stycke ordinal is an anchor -- which a quotation, being
    another document's text, has no business minting."""
    for i, unit in enumerate(u for alinea in alineas
                             for u in _stycke_units(alinea)):
        _emit_alinea(unit, marker if i == 0 else None, blocks)


def parse_quotation(quot, blocks):
    """A ``QUOT.S`` -- an act reproduced verbatim inside a case-law numbered
    paragraph -- as `citat` blocks that keep the quoted act's own structure.

    Schrems II (62018CJ0311) quotes three numbered paragraphs of directive
    95/46 article 25 inside its paragraph 4. Reading only the paragraph's first
    ``TXT``, as the parser did, published the lead-in ("I artikel 25 i detta
    direktiv föreskrevs följande:") and nothing after it; folding the quotation
    into the paragraph's own text instead runs all three together and loses the
    act's "1.", "2." and "6.". Its sections 3 to 42 are almost all this shape.

    Every block the act emitters produce is retyped to `QUOTATION`; a point
    keeps its indent step as `depth`, which is all the page needs from its
    kind, and a quoted article folds its designation into its text so no stray
    article number is hung in the margin."""
    inner = []
    _quoted_unit(quot, inner)
    for b in inner:
        if b.kind == TABLE:
            # a quoted act's table: the quotation is another document's text and
            # gets no table markup of its own, so each row is one quoted run
            # with its cells joined, which is what the page has always printed
            blocks.extend(Block(QUOTATION,
                                " | ".join(c.text for c in row.cells),
                                quoted="row")
                          for row in b.rows)
            continue
        blocks.append(Block(
            QUOTATION,
            " ".join(x for x in (b.label, b.text) if x) if b.kind == "article"
            else b.text,
            num=None if b.kind == "article" else b.num,
            level=b.level,
            depth=b.depth or (1 if b.kind == "point" else None),
            quoted=b.kind))


# regions inside a case-law body that are not its running prose: the ruling
# (parse_judgment emits it as `ruling` blocks of its own), an opinion's table of
# contents and its keyword index, and the footnote apparatus (collect_notes
# re-emits it as `note` blocks)
_CASE_SKIP = {"JURISDICTION", "TOC", "INDEX", "NOTE", "GR.NOTES", "BIB.INSTANCE"}


def _case_contents(parent, blocks, level=1):
    """A case-law document's body -- a judgment's ``CONTENTS.JUDGMENT``, an
    opinion's ``CONTENTS.CONCLUSION``, a hearing report's ``CONTENTS`` -- walked
    in **document order**.

    All three share one shape: ``GR.SEQ`` sections, each a ``TITLE`` heading plus
    its contents and carrying its depth in ``LEVEL``, holding the numbered
    paragraphs -- ``NP.ECR`` since ca 2012, plain ``NP`` before that -- and, in
    the oldest ECR judgments, unnumbered ``P`` prose.

    Reading that in three separate passes (every heading, then every NP.ECR, then
    every NP) put the whole body after the *last* heading and dropped the bare
    ``P`` prose altogether. Costa mot E.N.E.L. (61964CJ0006) published five empty
    section headings and no text at all; every modern judgment hung its
    paragraphs under "Rättegångskostnader". One walk fixes both."""
    for el in parent:
        tag = el.tag
        if tag == "TITLE":
            heading = flatten(el)
            if heading:
                blocks.append(Block("heading", heading, level=level))
        elif tag == "GR.SEQ":
            _case_contents(el, blocks, int(el.get("LEVEL") or level))
        elif tag == "NP.ECR":
            # the paragraph number is in the attribute ("NP0012"), not the text
            _numbered_paragraph(
                el, blocks, (el.get("IDENTIFIER") or "").lstrip("NP0") or None)
        elif tag == "NP":
            _numbered_paragraph(el, blocks)
        elif tag in ("P", "QUOT.S"):
            # a QUOT.S here hangs off a *section*, not off a numbered paragraph,
            # so `parse_quotation` never sees it. Measured over 600 random swe
            # case files: 7 of them, none holding a PARAG or an ARTICLE -- there
            # is no act structure in one to keep, so it reads as a run of text
            text = flatten(el)
            if text:
                blocks.append(Block("paragraph", text))
        elif tag == "LIST":
            _emit_list(el, blocks)
        elif tag == "TBL":
            _emit_table(el, blocks)
        elif tag in _CASE_SKIP:
            pass
        else:
            _case_contents(el, blocks, level)   # unknown wrapper: descend


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

# the block kind a TBL becomes. The artifact writes it as the `tabell`/`rad`
# node pair the other sources already produce (sfs, förarbete, föreskrift), so
# one renderer, one markdown projection and one link walk serve them all.
TABLE = "tabell"
# a cell's own block-level children: each one is a line of its own, so a cell
# holding several of them does not read as a single sentence
_CELL_BLOCKS = {"P", "NP", "ALINEA", "TXT", "LIST", "DLIST"}
# the bullet the OJ prints for a Formex dash list, which sets no marker of its own
_DASH = "\u2014 "


def _cell_text(cell):
    """One CELL's text: one line per block-level child, a dash list's items each
    on a line of their own.

    NIS2 bilaga I column 3 lists three kinds of marknadsaktör as three ITEMs of
    one DASH list in a single cell. Flattening the whole cell ran them into one
    sentence -- "Nominerade elmarknadsoperatörer ... Marknadsaktörer ...
    Laddningsoperatörer ..." -- which reads as one entity where the act names
    three. A cell that holds no block-level child (the overwhelming majority:
    212 000 of the 226 000 cells in a 400-act sample) is flattened exactly as
    before."""
    if not any(child.tag in _CELL_BLOCKS for child in cell):
        return flatten(cell)
    lines, inline = [], [cell.text or ""]

    def close():
        lines.append("".join(inline))
        inline.clear()

    for child in cell:
        if child.tag in SKIP_INLINE:
            pass
        elif child.tag in ("LIST", "DLIST"):
            close()
            # an alpha/arab list carries its own NO.P marker in the item text; a
            # DASH list carries none, so the bullet the OJ prints is added here
            bullet = _DASH if (child.get("TYPE") or "").upper() == "DASH" else ""
            lines.extend(bullet + flatten(item) for item in child)
        elif child.tag in _CELL_BLOCKS:
            close()
            lines.append(flatten(child))
        else:
            inline.append(flatten(child))
        inline.append(child.tail or "")
    close()
    return "\n".join(line for line in
                     (" ".join(raw.split()) for raw in lines) if line)


def _emit_table(tbl, blocks):
    """A TBL -> one `tabell` block holding its rows, each cell keeping the
    ROWSPAN/COLSPAN the source sets and the header flag it carries.

    The spans are what make an OJ annex table readable: NIS2 bilaga I writes
    "1. Energi" once with ROWSPAN="17" and the 16 rows it covers omit the cell
    entirely. Read without them, the sector name disappears from every row but the
    first and column 3's entries slide left into column 1.

    An *interior* empty cell is kept as an empty field, because in a
    correspondence table the column a value sits in is what it means: 2004/18's
    jämförelsetabell has a blank spacer column between the three repealed
    directives and the "Ny/Ändrad" column, and dropping it slides the latter
    into the former's place. A trailing empty cell carries no such meaning and
    goes -- unless it spans, where the span is the meaning."""
    rows = []
    for row in tbl.iter("ROW"):
        cells = [Cell(_cell_text(cell), int(cell.get("ROWSPAN") or 1),
                      int(cell.get("COLSPAN") or 1))
                 for cell in row.findall("CELL")]
        while cells and not (cells[-1].text or cells[-1].rowspan > 1
                             or cells[-1].colspan > 1):
            cells.pop()
        if cells:
            rows.append(Row(cells, row.get("TYPE") == "HEADER"
                            or all(c.get("TYPE") == "HEADER"
                                   for c in row.findall("CELL"))))
    if rows:
        blocks.append(Block(TABLE, _text(tbl, "TITLE"), rows=rows))


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


def append_annex(blocks, root):
    """Embed an ANNEX Formex file into `blocks` as a heading + its body, so a
    multi-file manifestation parses to one document."""
    title = _text(root.find("TITLE"), "TI", "P") or _text(root, "TITLE") \
        or "Bilaga"
    blocks.append(Block("heading", title, level=1, anchor=_annex_anchor(title)))
    contents = root.find("CONTENTS")
    if contents is not None:
        walk_content(contents, blocks)


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
