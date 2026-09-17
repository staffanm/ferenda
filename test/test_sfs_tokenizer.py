"""Direct unit tests for ferenda.sfs.tokenizer.Tokenizer, targeting
tokenizer-internal edge cases that the fixture-driven test_sfs_parse.py
oracle doesn't exercise well (end-of-data lookahead, in-body TOC faking)."""

import json
from pathlib import Path

import brotli

from ferenda.sfs import parse_sfs_source
from ferenda.sfs.assembler import assemble
from ferenda.sfs.model import Paragraf
from ferenda.sfs.nf import to_normalform
from ferenda.sfs.reader import TextReader
from ferenda.sfs.tokenizer import (
    OpenAvdelning,
    OpenKapitel,
    OpenParagraf,
    Tokenizer,
    andrings_datum,
)

BASEFILE = "9999:998"


def _events(text):
    reader = TextReader(text)
    reader.autostrip = True
    return list(Tokenizer(reader, BASEFILE))


def test_trailing_avdelning_heading_is_not_dropped():
    """A avdelning heading with nothing after it must still be emitted: the
    underrubrik lookahead runs past the end of data and used to raise an
    uncaught IOError, which the (former) blanket `except IOError` in
    next_event swallowed as "no more events" -- silently dropping the
    heading."""
    events = _events("FÖRSTA AVDELNINGEN")
    assert events == [OpenAvdelning(ordinal="1", rubrik="FÖRSTA AVDELNINGEN",
                                    underrubrik=None)]


def test_announced_toc_outside_first_section_is_not_chapters():
    """2023:200 (NML): 1 kap. 2 § announces the law's chapter listing, whose
    bare "N kap. Title" lines must not become chapters -- the §1 high-water
    heuristic doesn't reach a TOC in 2 §, so the announcing sentence itself
    ("… är uppdelat enligt följande.") must arm the faking. The real body
    chapters after the listing must all come through."""
    events = _events("""1 kap. Lagens innehåll

1 § I denna lag finns bestämmelser om mervärdesskatt.

2 § Lagens innehåll är uppdelat enligt följande.

1 kap. Lagens innehåll

2 kap. Definitioner och förklaringar

3 kap. Mervärdesskattens tillämpningsområde

Bestämmelser i andra författningar

3 § Ytterligare bestämmelser finns i annan lag.

2 kap. Definitioner och förklaringar

1 § I detta kapitel finns definitioner.

3 kap. Mervärdesskattens tillämpningsområde

1 § Detta kapitel gäller tillämpningsområdet.
""")
    kapitel = [e.ordinal for e in events if isinstance(e, OpenKapitel)]
    assert kapitel == ["1", "2", "3"]


def test_unannounced_chapter_run_is_toc():
    """1984:53 bilaga 2: a run of TOC_RUN+ consecutive chapter-shaped
    paragraphs with nothing between them (here: a listing of *another*
    statute's chapters) is a listing, not real chapters -- a real chapter
    always carries at least a rubrik or a § before the next one. A run
    broken by an upphävt-notice stays real (title-only chapters legitimately
    exist as repeal notices)."""
    events = _events("""1 § Denna förordning gäller reglering av import.

1 kap. Levande djur

2 kap. Kött och andra ätbara djurdelar

3 kap. Fisk samt kräftdjur

2 § Jordbruksverket är licensmyndighet.
""")
    assert not [e for e in events if isinstance(e, OpenKapitel)]
    # the same shapes with a revoked chapter in the run: real chapters
    events = _events("""1 kap. Inledande bestämmelser

1 § Denna lag gäller.

2 § Lagen gäller inte utomlands.

2 kap. Har upphävts genom lag (2005:20).

3 kap. Tillsyn

1 § Tillsyn utövas av myndigheten.
""")
    assert [e.ordinal for e in events if isinstance(e, OpenKapitel)] == ["1", "3"]


def test_dash_toc_announcement_keeps_following_real_chapter():
    """2026:667: the announced listing is dash-form ("- Titel (2 kap.)" --
    never chapter-shaped), and the real "2 kap." heading follows the
    announcing § directly. An announced listing that never opened at
    "1 kap." must not fake it."""
    events = _events("""1 kap. Inledande bestämmelser

1 § Denna förordning gäller ställföreträdare.

3 § Innehållet i förordningen är uppdelat enligt följande.

- Inledande bestämmelser (1 kap.)

- Ställföreträdarens redovisning (2 kap.)

2 kap. Ställföreträdarens redovisning

1 § En förteckning ska innehålla nödvändiga uppgifter.

3 kap. Överförmyndarens skyldigheter

1 § Överförmyndaren ska utöva tillsyn.
""")
    kapitel = [e.ordinal for e in events if isinstance(e, OpenKapitel)]
    assert kapitel == ["1", "2", "3"]


def test_a_paragraf_glued_to_the_line_above_still_opens():
    """The source drops the blank line before a paragraf marker, hiding 24
    paragrafer in 23 statutes:
    "… tillämpas. Lag (2004:197).\\n13 e § Kliniska läkemedelsprövningar …".
    The reader splits on the blank line, so 13 e § of Lag (1992:859) om
    läkemedel became the last words of 13 d § instead of a paragraf of its own.
    The provenance marker is what makes the repair safe -- it always ends a
    paragraf.

    Not a `test_sfs_parse` fixture like its sibling nbsp case
    (basic-paragraf-nbsp): the repair runs in `_assemble`, above the tokenizer,
    and that oracle drives the tokenizer directly."""
    doc = parse_sfs_source(
        {"fulltext": {"forfattningstext":
                      "13 d § Kliniska läkemedelsprövningar som inte har "
                      "samband med sjukdom. Lag (2004:197).\n"
                      "13 e § Kliniska läkemedelsprövningar får utföras på "
                      "underåriga. Lag (2004:197).\n\n"
                      "14 § Läkemedel ska förvaras."}},
        BASEFILE)
    got = []

    def walk(node):
        if isinstance(node, Paragraf):
            got.append(node.ordinal)
        for child in getattr(node, "children", None) or []:
            walk(child)

    walk(doc)
    assert got == ["13 d", "13 e", "14"]


def test_rubriken_temporal_authorization_markers():
    """Verify that andrings_datum recognizes Rubriken upphör and träder
    with 'den dag (som) regeringen bestämmer' directives."""
    line1 = "/Rubriken upphör att gälla U:den dag som regeringen bestämmer/ 26 kap. Om fängelse"
    stripped1, upphor1, ikraft1 = andrings_datum(line1)
    assert stripped1 == "26 kap. Om fängelse"
    assert upphor1 == "den dag som regeringen bestämmer"
    assert ikraft1 is None

    line2 = "/Rubriken träder i kraft I:den dag som regeringen bestämmer/ 26 kap. Om den tillämpliga straffskalan"
    stripped2, upphor2, ikraft2 = andrings_datum(line2)
    assert stripped2 == "26 kap. Om den tillämpliga straffskalan"
    assert upphor2 is None
    assert ikraft2 == "den dag som regeringen bestämmer"

    # Paragraf line with erroneous 'Rubriken upphör' marker
    line3 = "1 § /Rubriken upphör att gälla U:den dag som regeringen bestämmer/"
    stripped3, upphor3, ikraft3 = andrings_datum(line3)
    assert stripped3 == "1 §"
    assert upphor3 == "den dag som regeringen bestämmer"
    assert ikraft3 is None


def test_chapter_heading_variant_authorization():
    """Verify chapter heading change with authorization directive correctly
    emits events and mints chapter and paragraph IDs."""
    text = """/Rubriken upphör att gälla U:den dag som regeringen bestämmer/
26 kap. Om fängelse

/Rubriken träder i kraft I:den dag som regeringen bestämmer/
26 kap. Om den tillämpliga straffskalan

1 § /Rubriken upphör att gälla U:den dag som regeringen bestämmer/
Fängelse döms ut på viss tid.

1 § /Träder i kraft I:den dag som regeringen bestämmer/
Fängelse döms ut på viss tid. Lag (2026:1654).

2 § Fängelse får användas.
"""
    events = _events(text)
    assert len([e for e in events if isinstance(e, OpenKapitel)]) == 2
    assert len([e for e in events if isinstance(e, OpenParagraf)]) == 3

    reader = TextReader(text)
    reader.autostrip = True
    doc = assemble(Tokenizer(reader, "1962:700"))
    nf = to_normalform(doc, "1962:700")
    k26 = [n for n in nf["structure"] if n.get("ordinal") == "26" and n.get("id") == "K26"]
    assert len(k26) == 1
    p_ids = [c.get("id") for c in k26[0]["children"] if c.get("type") == "paragraf"]
    assert p_ids == ["K26P1", None, "K26P2"]


def test_sfs_1962_700_kapitel_26_recognized():
    """Verify that 26 kap in 1962:700 (Brottsbalken) is recognized and paragraphs
    receive pinpoint IDs (K26P1, K26P2, K26P2a)."""
    p = Path("site/data/downloaded/sfs/1962/700.json.br")
    if not p.exists():
        return
    with open(p, "rb") as f:
        data = json.loads(brotli.decompress(f.read()).decode("utf-8"))
    txt = data["fulltext"]["forfattningstext"].replace("\r", "")
    reader = TextReader(txt)
    reader.autostrip = True
    doc = assemble(Tokenizer(reader, "1962:700"))
    nf = to_normalform(doc, "1962:700")

    k26_nodes = []

    def find_k26(nodes):
        for n in nodes:
            if n.get("type") == "kapitel" and n.get("ordinal") == "26" and n.get("id") == "K26":
                k26_nodes.append(n)
            find_k26(n.get("children", []))

    find_k26(nf["structure"])
    assert len(k26_nodes) == 1
    p_ids = [c.get("id") for c in k26_nodes[0]["children"] if c.get("type") == "paragraf"]
    assert "K26P1" in p_ids
    assert "K26P2" in p_ids
    assert "K26P2a" in p_ids

