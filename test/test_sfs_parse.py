"""Run the old SFS parser's hand-authored fixture corpus against the new
structural parser (accommodanda).

test/files/sfs/parse/ holds ~110 plaintext → expected-tree pairs, one
formatting feature each (basic, lists, table, temporal, definition,
regression, tricky). Unlike the golden corpus (whole documents derived
from the old pipeline's output, so a change-detector), these are
hand-authored: they state the *desired* structure, so they are an oracle.

The expected tree is the old `ferenda.elements` serialization. We map it
to the same normal-form JSON that `accommodanda.nf.to_normalform` produces
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

from accommodanda.sfs import parse_sfs  # noqa: F401  (ensures package import)
from accommodanda.sfs.extract import sanitize_body
from accommodanda.sfs.reader import TextReader
from accommodanda.sfs.tokenizer import Tokenizer
from accommodanda.sfs.assembler import assemble
from accommodanda.sfs.nf import to_normalform
from accommodanda.lib.lagrum import LagrumParser, load_namedlaws
from accommodanda.lib.util import normalize_space

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "golden_sfs", Path(__file__).parent.parent / "tools" / "golden_sfs.py")
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
LISTS = {"NumreradLista", "Strecksatslista", "Bokstavslista", "Lista"}


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
    for item in elem:
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
    - id: the fixtures' fragment ids were minted by the old *test* driver,
      whose continuous-§ rule ('K' > 1) differs from the production rule
      ('K' >= 1) that nf.py and the golden corpus use -- so ids here
      conflict with the golden oracle on single-chapter documents.
      Id-minting is validated whole-document against the golden corpus.
    """
    for node in nodes:
        for key in ("beteckning", "level", "id", "key"):
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
# fixtures were frozen with a localhost res-URI base, the new pipeline mints
# lagen.nu URIs.

from accommodanda.lib.datasets import NAMEDLAWS as NAMEDLAWS_JSON


def norm_uri(uri):
    for prefix in ("http://localhost:8000/res/", "https://lagen.nu/"):
        if uri.startswith(prefix):
            uri = uri[len(prefix):]
            break
    return uri[4:] if uri.startswith("sfs/") else uri


def collect_runs(runs, out):
    for run in runs if isinstance(runs, list) else []:
        if isinstance(run, dict) and run["predicate"] == "dcterms:references":
            out.append((run["text"], norm_uri(run["uri"])))


def nf_links(nodes, out, parent_type=None):
    """Collect (text, norm_uri) for every reference link the new parser
    inlines, restricted to the node kinds the *old* pipeline also scanned --
    so this stays an exact-equality oracle against the frozen fixtures.
    Skipped because the old pipeline never scanned them (the new one does,
    per the scan-all-text-nodes design): rubrik (headings), upphavd
    (repealed-provision placeholders), and top-level tables (tables that are
    not nested inside a stycke)."""
    for node in nodes:
        ntype = node.get("type")
        if ntype in ("rubrik", "upphavd"):
            continue
        if ntype == "tabell":
            if parent_type != "stycke":
                continue  # old pipeline never linked top-level tables
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
