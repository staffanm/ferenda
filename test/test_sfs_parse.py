"""Run the old SFS parser's hand-authored fixture corpus against the new
structural parser (ferenda).

test/files/sfs/parse/ holds ~110 plaintext → expected-tree pairs, one
formatting feature each (basic, lists, table, temporal, definition,
regression, tricky). Unlike the golden corpus (whole documents derived
from the reference projection's output, so a change-detector), these are
hand-authored: they state the *desired* structure, so they are an oracle.

The expected tree is the old `ferenda.elements` serialization. We map it
to the same normal-form JSON that `ferenda.nf.to_normalform` produces
and reuse the golden comparator's `diff_nodelists`. Only the structural
projection is checked here:

- Inline citation/begrepp links (LinkSubject) fold into their node's text,
  exactly as the golden normalizer folds <a> text -- so a fixture's
  references and definitions do not need to be reproduced for its
  *structure* to match. (Those links carry URIs in the fixtures and are a
  ready oracle for the reference/definition work; compared separately when
  that lands.)
- `beteckning` and rubrik `level` are dropped: the element serialization
  does not encode them.
- Övergångsbestämmelser are dropped on both sides (the new projection
  redistributes them into the amendment register, not the structure).
- Id minting runs with temporal suppression off: these fixtures keep ids
  on expired/not-yet-in-force nodes (they test the parser, not the
  consolidated-view policy).

Fixtures the old parser itself could not handle are marked xfail.
"""

import re
from pathlib import Path

import pytest
from lxml import etree

from ferenda.sfs import parse_sfs  # noqa: F401  (ensures package import)
from ferenda.sfs.extract import sanitize_body
from ferenda.sfs.reader import TextReader
from ferenda.sfs.tokenizer import Tokenizer
from ferenda.sfs.assembler import assemble
from ferenda.sfs.nf import to_normalform
from ferenda.lib import page
from ferenda.lib.lagrum import LagrumParser, load_namedlaws
from ferenda.lib.util import normalize_space

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "golden_sfs", Path(__file__).parent.parent / "tools" / "corpus" / "golden_sfs.py")
golden_sfs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(golden_sfs)

FIXTURES = Path(__file__).parent / "files" / "sfs" / "parse"
BASEFILE = "9999:998"

# Cases the old parser also failed (from integrationSFS.brokentests), kept
# as xfail. Three entries the old driver listed as broken now pass the
# structural oracle under the new parser (regression-10kap-ellagen,
# tricky-lang-rubrik, tricky-lopande-rubriknumrering) and have been
# promoted to ordinary passing tests. The remainder ship without an
# expected tree (empty/missing .xml -- the old driver expected no output)
# or still diverge structurally.
BROKEN = {
    "definition-no-definition", "definition-paranthesis-lista",
    "definition-paranthesis-multiple", "definition-strecksatslista-andrastycke",
    "extra-overgangsbestammelse-med-rubriker",
    "tricky-felformatterad-tabell",
    "tricky-lista-inte-rubrik", "tricky-lista-not-rubriker-2",
    "tricky-okand-aldre-lag",
    "tricky-paragraf-inledande-tomrad", "tricky-tabell-overgangsbest",
    "tricky-tabell-sju-kolumner",
}

INLINE = {"str", "Link", "LinkSubject"}
LISTS = {"NumreradLista", "Strecksatslista", "Bokstavslista", "RomerskLista", "Lista"}


def localname(elem):
    return etree.QName(elem).localname


def inline_leaf(node):
    """Text of an inline leaf (str / Link / LinkSubject), folding any
    nested inline elements. Tails are ignored -- in the element model all
    real text lives inside these leaves, so a tail is only the
    serializer's pretty-print whitespace."""
    parts = [node.text or ""]
    for child in node:
        if localname(child) in INLINE:
            parts.append(inline_leaf(child))
    return "".join(parts)


def inline_text(elem):
    """Concatenate the inline-leaf text directly under `elem`, skipping
    block children (lists, tables) -- mirrors the golden normalizer's
    own_text(skip=...). Inter-element whitespace is serialization noise,
    not content, so it is dropped rather than collapsed to a space."""
    parts = [inline_leaf(child) for child in elem
             if localname(child) in INLINE]
    return normalize_space("".join(parts))


def rubrik_node(text, id=None):
    return {"type": "rubrik", "id": id, "text": normalize_space(text or "")}


def flatten_list(elem, out):
    """A list's items in document order; a <str> directly in the list is a
    listtext, words in the list that are no item of it (sfs/nf.py)."""
    for item in elem:
        if localname(item) == "str":
            out.append({"type": "listtext", "text": inline_leaf(item).strip()})
            continue
        if localname(item) != "Listelement":
            continue
        out.append({"type": "punkt", "id": item.get("id"),
                    "ordinal": item.get("ordinal"), "text": inline_text(item)})
        for sub in item:
            if localname(sub) in LISTS:
                flatten_list(sub, out)


def table_node(elem):
    rows = []
    for tr in elem:
        if localname(tr) != "Tabellrad":
            continue
        rows.append({"type": "rad",
                     "cells": [inline_text(c) for c in tr
                               if localname(c) == "Tabellcell"]})
    return {"type": "tabell", "id": None, "children": rows}


def stycke_node(elem):
    node = {"type": "stycke", "id": elem.get("id"), "text": inline_text(elem)}
    children = []
    for child in elem:
        tag = localname(child)
        if tag in LISTS:
            flatten_list(child, children)
        elif tag == "Tabell":
            children.append(table_node(child))
    if children:
        node["children"] = children
    return node


def convert(elem):
    """Element node -> list of NF nodes (some elements expand to several,
    e.g. a Kapitel emits a rubrik child for its @rubrik attribute)."""
    tag = localname(elem)
    if tag in ("Avdelning", "Underavdelning"):
        kids = []
        if elem.get("rubrik"):
            kids.append(rubrik_node(elem.get("rubrik")))
        if elem.get("underrubrik"):
            kids.append(rubrik_node(elem.get("underrubrik")))
        kids += convert_children(elem)
        node = {"type": tag.lower(), "id": elem.get("id"), "children": kids}
        if tag == "Avdelning":
            node["ordinal"] = elem.get("ordinal")
        return [node]
    if tag == "Kapitel":
        kids = ([rubrik_node(elem.get("rubrik"))] if elem.get("rubrik") else [])
        kids += convert_children(elem)
        return [{"type": "kapitel", "id": elem.get("id"),
                 "ordinal": elem.get("ordinal"), "children": kids}]
    if tag == "Bilaga":
        kids = ([rubrik_node(elem.get("rubrik"))] if elem.get("rubrik") else [])
        kids += convert_children(elem)
        return [{"type": "bilaga", "id": elem.get("id"), "children": kids}]
    if tag == "Paragraf":
        return [{"type": "paragraf", "id": elem.get("id"),
                 "ordinal": elem.get("ordinal"),
                 "children": convert_children(elem)}]
    if tag in ("UpphavtKapitel", "UpphavdParagraf"):
        # these carry their text directly as element text, not in <str>
        text = (elem.text or "") + "".join(
            inline_leaf(c) for c in elem if localname(c) in INLINE)
        return [{"type": "upphavd", "text": normalize_space(text)}]
    if tag == "Rubrik":
        return [rubrik_node(elem.text, id=elem.get("id"))]
    if tag == "Stycke":
        return [stycke_node(elem)]
    if tag == "Grafik":
        return [{"type": "grafik", "id": elem.get("id"),
                 "sort": elem.get("sort"), "satt_av": elem.get("satt_av")}]
    if tag in LISTS:
        items = []
        flatten_list(elem, items)
        return [{"type": "lista", "id": None, "children": items}]
    if tag == "Tabell":
        return [table_node(elem)]
    if tag == "Overgangsbestammelser":
        return []  # redistributed into the amendment register downstream
    return []


def convert_children(elem):
    out = []
    for child in elem:
        out.extend(convert(child))
    return out


def fixture_structure(path):
    if not path.read_text().strip():
        return []  # empty fixture: the old driver expected no output
    root = etree.parse(str(path)).getroot()
    return convert_children(root)


def strip(nodes):
    """Reduce to what these fixtures are an oracle for: node type, ordinal,
    text and nesting. Dropped keys:

    - beteckning, level: not encoded in the element serialization.
    - of: the id a listtext's list sits under, which the nesting already shows.
    - id: the fixtures' fragment ids were minted by the old *test* driver,
      whose continuous-§ rule ('K' > 1) differs from the production rule
      ('K' >= 1) that nf.py and the golden corpus use -- so ids here
      conflict with the golden oracle on single-chapter documents.
      Id-minting is validated whole-document against the golden corpus.
    """
    for node in nodes:
        for key in ("beteckning", "level", "id", "key", "of"):
            node.pop(key, None)
        strip(node.get("children", []))
    return nodes


def fold_text(value):
    """Collapse an inline-list text value (str runs + link objects) back to
    a plain string. nf.to_normalform now emits every text node as such a
    list; the structure oracle only cares about the concatenated text, so
    links are folded away here and checked separately by test_sfs_links."""
    if isinstance(value, list):
        return normalize_space("".join(
            part if isinstance(part, str) else part["text"] for part in value))
    return value


def fold(nodes):
    for node in nodes:
        if "text" in node:
            node["text"] = fold_text(node["text"])
        fold(node.get("children", []))
    return nodes


def parse_fixture(path):
    text = sanitize_body(path.read_text(encoding="iso-8859-1"))
    reader = TextReader(text)
    reader.autostrip = True
    doc = assemble(Tokenizer(reader, BASEFILE))
    nf = to_normalform(doc, BASEFILE, suppress_temporal=False)
    return fold(strip(nf["structure"]))


def params():
    for txt in sorted(FIXTURES.glob("*.txt")):
        xml = txt.with_suffix(".xml")
        if not xml.exists():
            continue  # old driver expected empty output; all are in BROKEN
        marks = ([pytest.mark.xfail(reason="old parser failed this too",
                                    strict=False)]
                 if txt.stem in BROKEN else [])
        yield pytest.param(txt, id=txt.stem, marks=marks)


@pytest.mark.parametrize("txt", params())
def test_sfs_parse(txt):
    expected = strip(fixture_structure(txt.with_suffix(".xml")))
    got = parse_fixture(txt)
    problems = []
    golden_sfs.diff_nodelists(expected, got, "structure", problems)
    assert not problems, "\n".join(problems)


# --- reference-link oracle ---------------------------------------------
#
# The fixtures carry the desired inline links as <LinkSubject> leaves. We
# check the *reference* links (predicate dcterms:references) the new parser
# inlines against them, as a whole-document multiset of (covered text,
# target). Definition links (dcterms:subject) are a separate, not-yet-ported
# feature, so they are excluded on both sides. URIs are base-normalized: the
# fixtures were frozen with a localhost res-URI base, Ferenda mints
# lagen.nu URIs.

from ferenda.lib.datasets import NAMEDLAWS as NAMEDLAWS_JSON


def norm_uri(uri):
    for prefix in ("http://localhost:8000/res/", "https://lagen.nu/"):
        if uri.startswith(prefix):
            uri = uri[len(prefix):]
            break
    return uri[4:] if uri.startswith("sfs/") else uri


def collect_runs(runs, out):
    """The citation runs of one inline list. A `kind="term"` run is an in-act
    use of a term the act defines (nf.interlink_definitions), not a citation
    the old reference projection ever emitted -- it carries the same predicate
    but belongs to the definition machinery, and has its own tests."""
    for run in runs if isinstance(runs, list) else []:
        if (isinstance(run, dict) and run["predicate"] == "dcterms:references"
                and run.get("kind") != "term"):
            out.append((run["text"], norm_uri(run["uri"])))


def nf_links(nodes, out, parent_type=None):
    """Collect (text, norm_uri) for every reference link the new parser
    inlines, restricted to the node kinds the *old* pipeline also scanned --
    so this stays an exact-equality oracle against the frozen fixtures.
    Skipped because the reference projection never scanned them (the new one does,
    per the scan-all-text-nodes design): rubrik (headings), upphavd
    (repealed-provision placeholders), and top-level tables (tables that are
    not nested inside a stycke)."""
    for node in nodes:
        ntype = node.get("type")
        if ntype in ("rubrik", "upphavd"):
            continue
        if ntype == "tabell":
            if parent_type != "stycke":
                continue  # the reference projection excludes top-level tables
            for row in node.get("children", []):
                for cell in row.get("cells", []):
                    collect_runs(cell, out)
            continue
        collect_runs(node.get("text"), out)
        nf_links(node.get("children", []), out, ntype)
    return out


def fixture_links(elem, out):
    """Collect (text, norm_uri) reference links from fixture XML, pruning
    Overgangsbestammelser (redistributed out of the structure section)."""
    for child in elem:
        if localname(child) == "Overgangsbestammelser":
            continue
        if localname(child) == "LinkSubject" \
                and child.get("predicate") == "dcterms:references":
            out.append((normalize_space(inline_leaf(child)),
                        norm_uri(child.get("uri"))))
        fixture_links(child, out)
    return out


LINK_GAPS = set()

# Adjudicated fixture edit, tricky-nastlade-listor.xml: "5 kap. 5 § tredje
# stycket samt 5 a, 6 a, 6 b och 7 a §§" recorded the trailing four as the
# chapterless #P5a/#P6a/#P6b/#P7a. Mervärdesskattelagen, which the fixture text
# is lifted from, is chaptered throughout and has no top-level paragraf at all,
# so those four targets could never resolve -- the fixture had frozen the
# dropped-chapter bug (a named chapter did not stay sticky across a continued
# enumeration; see test_lagrum's
# test_a_named_chapter_stays_sticky_across_a_continued_enumeration). They now
# expect #K5P5a etc., the chapter the sentence is plainly continuing in.


@pytest.mark.parametrize("txt", params())
def test_sfs_links(txt):
    if txt.stem in LINK_GAPS:
        pytest.xfail("reference link not reproduced by the ported pipeline")
    xml = txt.with_suffix(".xml")
    if not xml.read_text().strip():
        pytest.skip("empty fixture")
    text = sanitize_body(txt.read_text(encoding="iso-8859-1"))
    reader = TextReader(text)
    reader.autostrip = True
    doc = assemble(Tokenizer(reader, BASEFILE))
    refparser = LagrumParser(load_namedlaws(NAMEDLAWS_JSON), BASEFILE)
    nf = to_normalform(doc, BASEFILE, refparser=refparser,
                       suppress_temporal=False)
    # compare as sets: inline links are per-occurrence, but the fixtures
    # (like the old deduped reference list) record each distinct link once,
    # so a ref repeated across temporal variants must not count as a mismatch
    got = set(nf_links(nf["structure"], []))
    expected = set(fixture_links(etree.parse(str(xml)).getroot(), []))
    assert got == expected


# --- text in a list on the page ---------------------------------------------

TAIL_PAGE = """1 § Den som

1. tillverkar sprit,

2. förvärvar sprit som

a) är olovligt tillverkad, eller

b) är smugglad,

och som saknar tillstånd, eller

3. säljer sprit,

döms till böter.
"""


class _NoContext:
    """A rail with no context to show: `page.Rail` reads the catalog."""
    data = {}

    def add(self, node_id, label):
        pass


def render_first_stycke(text, paragraf=0, stycke=0):
    reader = TextReader(text)
    reader.autostrip = True
    doc = assemble(Tokenizer(reader, BASEFILE))
    node = to_normalform(doc, BASEFILE)["structure"][paragraf]["children"][stycke]
    return page.render_node(node, page.Site(None, set()), BASEFILE,
                            page.Toc(), _NoContext())


def test_a_tail_renders_after_its_list_inside_the_holder():
    """The words that end a list's sentence read after the list: inside the
    item that holds a sub-list, after the stycke's own list at top level."""
    html = render_first_stycke(TAIL_PAGE)
    text = re.sub(r"<span class=\"num\">[^<]*</span>", "", html)
    assert re.sub(r"<[^>]+>", " ", text).split() == (
        "Den som tillverkar sprit, förvärvar sprit som är olovligt "
        "tillverkad, eller är smugglad, och som saknar tillstånd, eller "
        "säljer sprit, döms till böter.").split()
    assert "</ol><p>och som saknar tillstånd, eller</p></li>" in html
    assert html.endswith("säljer sprit,</li></ol><p>döms till böter.</p>")


def test_text_between_items_splits_the_list_where_the_source_has_it():
    """Words between two items stay between them; words that open a nested
    list read before its first item, inside the item that holds it."""
    text = (FIXTURES / "regression-text-mellan-listpunkter.txt").read_text(
        encoding="iso-8859-1")
    html = render_first_stycke(text)
    assert ("expansionsfond,</li></ol><p>samt minskas med</p>"
            '<ol class="punkter">') in html
    html = render_first_stycke(text, paragraf=1)
    assert ('i övrigt<p>i Danmark</p><ol class="punkter">') in html


def test_entries_without_markers_render_without_markers():
    """Entries the source prints without markers nest under their item and
    show no marker the source does not print (shortened from 1996:1515
    bilaga, avsnitt 2.2 punkt 3)."""
    text = (FIXTURES / "regression-lista-utan-markorer.txt").read_text(
        encoding="iso-8859-1")
    html = render_first_stycke(text, paragraf=1)
    assert ('lagstiftningsärenden som gäller<ol class="punkter">'
            '<li id="P3S1N2N1">revisorer och revisionsbolag,</li>') in html


def test_group_labels_render_above_their_own_items():
    """Each group label reads above the items that restart their count under
    it, and every item gets an id of its own (shortened from 1983:55 2 §)."""
    text = (FIXTURES / "regression-lista-gruppetiketter.txt").read_text(
        encoding="iso-8859-1")
    html = render_first_stycke(text, stycke=1)
    assert ('</ol></li><li id="P2S2N2">i fråga om anskaffning av inventarier'
            '<ol class="punkter"><li id="P2S2N2N1">') in html


def test_entries_without_markers_nest_in_an_overgangsbestammelse():
    """A numbered övergångsbestämmelse holds its list with no stycke around
    it; the entries after "Genom förordningen upphävs" still nest under that
    punkt, and the next punkt stays in the same list (shortened from 1983:412,
    övergångsbestämmelser 1988:681). The fixture comparison skips
    övergångsbestämmelser, so this checks the tree itself."""
    reader = TextReader((FIXTURES / "regression-lista-utan-markorer.txt")
                        .read_text(encoding="iso-8859-1"))
    reader.autostrip = True
    ob = assemble(Tokenizer(reader, BASEFILE)).children[-1].children[0]
    [lista] = ob.children
    assert [i.ordinal for i in lista.children] == ["1", "2", "3"]
    [entries] = lista.children[1].children
    assert entries.kind == "omarkerad"
    assert [e.text for e in entries.children] == [
        "förordningen om statsbidrag till driftkostnader för vissa "
        "lanthushållsskolor,",
        "förordningen om statsbidrag till vissa privatskolor."]


def fixture_paragrafer(name):
    """The paragrafer of a fixture's tree, for checks of the nesting that the
    fixture comparison flattens away."""
    reader = TextReader((FIXTURES / name).read_text(encoding="iso-8859-1"))
    reader.autostrip = True
    return assemble(Tokenizer(reader, BASEFILE)).children


def test_a_list_nests_under_an_item_that_does_not_end_its_sentence():
    """A list of a kind that does not nest by default nests under an item
    that ends with ":" or without punctuation (1995:1554 1 kap. 3 §, 1958:522
    artikel 7), and stands beside the list when the item ends its sentence
    (shortened from those acts)."""
    p1, p2, p3, _ = fixture_paragrafer("regression-lista-oavslutad-punkt.txt")
    [dashes] = p1.children[0].children[0].children[0].children
    [letters] = dashes.children[1].children
    assert (dashes.kind, letters.kind) == ("strecksats", "bokstav")
    assert [lst.kind for lst in p2.children[0].children] == [
        "strecksats", "bokstav"]
    [numbers] = p3.children[0].children[0].children[1].children
    assert numbers.kind == "numrerad"


def test_an_item_printed_with_its_first_subitem_holds_no_text():
    """"2. (a) Om inte annat följer av (b), …" is item 2, and (a) is the first
    item of the list under it (1973:1199 artikel 4); "(i)" after "(a)" is a
    roman item under it."""
    _, _, p3, p4 = fixture_paragrafer("regression-parentesmarkorer.txt")
    item = p3.children[0].children[0].children[1]
    assert item.text == ""
    [letters] = item.children
    assert [i.ordinal for i in letters.children] == ["a", "b"]
    [romans] = p4.children[0].children[0].children[0].children
    assert (romans.kind, [i.ordinal for i in romans.children]) == (
        "romersk", ["i", "ii"])


def test_an_item_printed_with_two_levels_of_subitems_keeps_them_in_order():
    """"1. (a) (i) …" splits twice: item 1 and item (a) hold no text, and the
    text belongs to item (i) of the list under (a)."""
    reader = TextReader("1 § Skatt tas ut på\n\n1. (a) (i) inkomster i "
                        "Sverige,\n\n(ii) inkomster utomlands,\n\n(b) annat.\n")
    reader.autostrip = True
    [paragraf] = assemble(Tokenizer(reader, BASEFILE)).children
    [lista] = paragraf.children[0].children
    [item] = lista.children
    assert item.text == ""
    [letters] = item.children
    assert [(i.ordinal, i.text) for i in letters.children] == [
        ("a", ""), ("b", "annat.")]
    [romans] = letters.children[0].children
    assert [(i.ordinal, i.text) for i in romans.children] == [
        ("i", "inkomster i Sverige,"), ("ii", "inkomster utomlands,")]
