"""The shared artifact text flattener (ferenda/lib/text.py)."""

from ferenda.lib import catalog, text

ART = {
    "uri": "https://lagen.nu/1962:700",
    "structure": [
        {"type": "kapitel", "id": "K1", "text": ["1 kap. Inledande"],
         "children": [
            {"type": "paragraf", "id": "K1P1",
             "text": ["Den som ",
                      {"uri": "https://lagen.nu/1962:700#K3P1", "text": "dödar"},
                      " annan"],
             "children": [{"type": "punkt", "ordinal": "1",
                           "text": ["med uppsåt"]}]},
            {"type": "paragraf", "id": "K1P2", "text": ["Straffet är fängelse."]},
         ]},
        {"type": "tabell", "children": [
            {"type": "rad", "cells": [["Brott"], ["Straff ", {"text": "X"}]]}]},
    ],
    "amendments": [{"content": [{"type": "stycke", "text": ["Ändrat 1990."]}]}],
}


def test_runs_text_plain_and_links():
    assert text.runs_text(["a", {"text": "b"}, "c"]) == "abc"
    assert text.runs_text("bare string") == "bare string"
    assert text.runs_text([{"uri": "x"}]) == ""        # link dict, no text


def test_node_text_includes_descendants_and_cells():
    para = ART["structure"][0]["children"][0]
    assert text.node_text(para) == "Den som dödar annan med uppsåt"
    rad = ART["structure"][1]["children"][0]
    assert text.node_text(rad) == "Brott Straff X"


def test_document_text_spans_structure_and_amendments():
    full = text.document_text(ART)
    assert "1 kap. Inledande" in full
    assert "dödar annan med uppsåt" in full
    assert "Straffet är fängelse." in full
    assert "Brott Straff X" in full
    assert "Ändrat 1990." in full                       # amendment content


def test_fragment_texts_one_per_id_bearing_node():
    frags = dict(text.fragment_texts(ART))
    assert frags["https://lagen.nu/1962:700#K1P1"] == "Den som dödar annan med uppsåt"
    assert frags["https://lagen.nu/1962:700#K1P2"] == "Straffet är fängelse."
    # the kapitel rolls up its children's text
    assert "Inledande" in frags["https://lagen.nu/1962:700#K1"]
    # only id-bearing nodes are fragments (the punkt has no id)
    assert all(u.startswith("https://lagen.nu/1962:700#K") for u in frags)


FORESKRIFT = {
    "uri": "https://lagen.nu/fffs/2013:10",
    "structure": [{"type": "paragraf", "id": "P1",
                   "text": ["Ursprunglig lydelse."]}],
    "consolidations": [
        {"of": "https://lagen.nu/fffs/2013:10",
         "konsolideradTom": "https://lagen.nu/fffs/2014:2",
         "structure": [{"type": "paragraf", "id": "P1",
                        "text": ["Äldre konsoliderad lydelse."]}]},
        {"of": "https://lagen.nu/fffs/2013:10",
         "konsolideradTom": "https://lagen.nu/fffs/2016:13",
         "structure": [{"type": "paragraf", "id": "P1",
                        "text": ["Gällande konsoliderad lydelse."]}]},
    ],
}


def test_anchor_text_reaches_what_the_artifact_stamps_no_id_on():
    """An EU act's articles carry an id; its recitals and sub-articles do not --
    the renderer mints `25.1` and `recital-83` from the block's own type and
    number. A resolved "GDPR (83" pinned the right anchor and showed the act
    with no words under it, because the id lookup finds no recital at all."""
    act = {"uri": "https://lagen.nu/celex/32099R0001", "structure": [
        {"type": "recital", "num": "82", "text": ["Om att styrka efterlevnad."]},
        {"type": "recital", "num": "83", "text": ["Om att upprätthålla "
                                                  "säkerheten."]},
        {"type": "article", "id": "25", "num": "25", "text": ["Inbyggt "
                                                              "dataskydd"],
         "children": [{"type": "paragraph", "num": "1",
                       "text": ["Med beaktande av den senaste utvecklingen."]}]},
    ]}
    assert text.anchor_text(act, "recital-83") == "Om att upprätthålla säkerheten."
    assert text.anchor_text(act, "25.1") == ("Med beaktande av den senaste "
                                             "utvecklingen.")
    # the id lookup still wins where there is an id: an article answers with
    # its whole subtree, not with the heading the anchor walk would find
    assert text.anchor_text(act, "25").startswith("Inbyggt dataskydd")
    assert "senaste utvecklingen" in text.anchor_text(act, "25")
    # an anchor the act does not publish is empty, not a guess
    assert text.anchor_text(act, "recital-99") == ""
    assert text.anchor_text(act, "K1P1") == ""


def test_presented_consolidation_is_latest_parsed():
    cons = text.presented_consolidation(FORESKRIFT)
    assert cons["konsolideradTom"] == "https://lagen.nu/fffs/2016:13"
    # a consolidation without parsed structure never presents (an image-only
    # scan or a cover-sheet PDF) -- the base text stays the reading text
    unparsed = {**FORESKRIFT,
                "consolidations": [{"konsolideradTom": None, "structure": []}]}
    assert text.presented_consolidation(unparsed) is None
    assert text.presented_consolidation(ART) is None    # no consolidations key


def test_presented_consolidation_replaces_base_text_and_fragments():
    # the presented consolidation IS the document text; the base structure is
    # excluded (same §§ mint the same fragment ids -- walking both would
    # double every anchor and index superseded text beside its replacement)
    assert text.document_text(FORESKRIFT) == "Gällande konsoliderad lydelse."
    assert text.fragment_texts(FORESKRIFT) == [
        ("https://lagen.nu/fffs/2013:10#P1", "Gällande konsoliderad lydelse.")]
    # without any parsed consolidation the base structure carries the text
    base_only = {k: v for k, v in FORESKRIFT.items() if k != "consolidations"}
    assert text.document_text(base_only) == "Ursprunglig lydelse."


def test_dv_body_section():
    art = {"uri": "https://lagen.nu/dom/nja/2009s796",
           "body": [{"type": "rubrik", "id": "r1", "text": ["Domskäl"]},
                    {"type": "stycke", "text": ["HD finner ", {"text": "att"}]}]}
    assert text.document_text(art) == "Domskäl HD finner att"
    assert text.fragment_texts(art) == [
        ("https://lagen.nu/dom/nja/2009s796#r1", "Domskäl")]


# --------------------------------------------------------------------------
# footnotes are presented body text
# --------------------------------------------------------------------------

FOOTNOTED = {
    "uri": "https://lagen.nu/avg/imy/IMY-2024-1",
    "structure": [{"type": "stycke", "id": "S1",
                   "text": ["IMY hänvisar till styrelsens riktlinjer."]}],
    "footnotes": [{"mark": "12",
                   "text": ["Se ",
                            {"uri": "https://lagen.nu/edpb/riktlinjer/05-2020",
                             "text": "riktlinjer 05/2020"},
                            ", punkt 42."]}],
}


def test_footnotes_are_walked_as_presented_body():
    """`BODY_SECTIONS` is "what the reader sees, the index stores and the link
    walk reads" -- and notes are presented at the foot of the page. Leaving
    them out cost every citation a document keeps in its notes: for an
    IMY-beslut that is the one *identifying* the vägledning its prose names,
    and for a court decision the whole apparatus DV has printed as endnotes
    since 2023."""
    assert "footnotes" in text.BODY_SECTIONS
    assert FOOTNOTED["footnotes"] in text.body_sections(FOOTNOTED)


def test_a_footnote_citation_reaches_the_link_graph():
    uris = [run["uri"] for _anchor, _page, run in catalog.artifact_links(FOOTNOTED)]
    assert "https://lagen.nu/edpb/riktlinjer/05-2020" in uris


def test_a_footnote_reaches_the_indexed_document_text():
    assert "punkt 42" in text.document_text(FOOTNOTED)


def test_provision_heading_reads_the_types_that_print_one():
    art = {"uri": "https://lagen.nu/coe/005", "structure": [
        {"type": "artikel", "id": "A6", "ordinal": "6",
         "text": ["Article 6 – Right to a fair trial"],
         "children": [{"type": "stycke", "id": "A6S1",
                       "text": ["In the determination of his civil rights …"]}]},
        {"type": "paragraf", "id": "K4P5", "text": ["Den som hotar någon annan …"]}]}
    # an article prints a heading; the pinned hit is named by it
    assert text.provision_heading(art, "A6") == "Article 6 – Right to a fair trial"
    # a paragraf prints none, and a stycke's own text is body text
    assert text.provision_heading(art, "K4P5") == ""
    assert text.provision_heading(art, "A6S1") == ""
    assert text.provision_heading(art, "A9") == ""
