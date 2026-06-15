"""Tests for the legacy DV parser (POI Word path).

The POI extraction itself needs a JVM and lives in dv_word; these tests
exercise the format-parsing and model-mapping logic over synthetic
paragraph streams, so they run without Java.
"""

from accommodanda.dv.word import Para
from accommodanda.dv.legacy import (
    build_avgorande, parse_head_body, _classify, _split_malnummer)
from accommodanda.dv.model import Rubrik, Stycke


def P(text, bold=False, in_table=True):
    return Para(text, bold, in_table)


# A minimal but representative referat stream: bold court, plain referat,
# bold "Label:" / value pairs, the REFERAT marker, body, bold footer.
SAMPLE = [
    P("Arbetsdomstolen", bold=True),
    P("AD 1993 nr 100"),
    P(""),
    P("Domsnummer:", bold=True), P("1993-100"),
    P("Målnummer:", bold=True), P("A-31-1992;A-125-1992"),
    P("Avgörandedatum:", bold=True), P("1993-05-26"),
    P("Rubrik:", bold=True), P("Enligt en bestämmelse i arbetstidslagen."),
    P("Lagrum:", bold=True), P("14 § arbetstidslagen (1982:673)"),
    P("Rättsfall:", bold=True), P("NJA 1985 s. 717"), P("NJA 1990 s. 591"),
    P("REFERAT", bold=True),
    P("Elektriska Arbetsgivareföreningen", bold=True),
    P("mot"),
    P("4 Kap ARBETSTID", bold=True),
    P("1. Den ordinarie veckoarbetstiden är 40 timmar."),
    P("Sökord:", bold=True), P("Kollektivavtal; Veckovila"),
]


def test_header_two_cell():
    head, _ = parse_head_body(SAMPLE)
    assert head["Domstol"] == "Arbetsdomstolen"
    assert head["Referat"] == "AD 1993 nr 100"


def test_header_pipe_form():
    head, _ = parse_head_body([P("Högsta Domstolen | NJA 2001 s. 191", bold=True),
                               P("REFERAT", bold=True), P("Text.")])
    assert head["Domstol"] == "Högsta Domstolen"
    assert head["Referat"] == "NJA 2001 s. 191"


def test_metadata_label_value_pairs():
    head, _ = parse_head_body(SAMPLE)
    assert head["Domsnummer"] == ["1993-100"]
    assert head["Avgörandedatum"] == ["1993-05-26"]
    assert head["Lagrum"] == ["14 § arbetstidslagen (1982:673)"]


def test_metadata_multivalue():
    head, _ = parse_head_body(SAMPLE)
    assert head["Rättsfall"] == ["NJA 1985 s. 717", "NJA 1990 s. 591"]


def test_referat_marker_starts_body():
    _, body = parse_head_body(SAMPLE)
    # body is everything after REFERAT up to the Sökord footer
    assert body[0].text == "Elektriska Arbetsgivareföreningen"
    assert all(p.text != "Sökord:" for p in body)
    assert all(p.text != "1993-100" for p in body)


def test_footer_sokord_not_in_body():
    head, _ = parse_head_body(SAMPLE)
    assert head["Sökord"] == ["Kollektivavtal; Veckovila"]


def test_split_malnummer_list():
    assert _split_malnummer("A-31-1992;A-125-1992") == ["A-31-1992", "A-125-1992"]
    assert _split_malnummer("A 52/94, A 54/94") == ["A", "52/94", "A", "54/94"]


def test_split_malnummer_single_spaced():
    # "Ö 2475-12" is one identifier, not split on the space
    assert _split_malnummer("Ö 2475-12") == ["Ö2475-12"]


def test_classify_numbered_paragraph():
    block = _classify(P("1. Den ordinarie veckoarbetstiden är 40 timmar."))
    assert block == Stycke("Den ordinarie veckoarbetstiden är 40 timmar.", ordinal="1")


def test_classify_bold_short_is_heading():
    assert _classify(P("4 Kap ARBETSTID", bold=True)) == Rubrik("4 Kap ARBETSTID")


def test_classify_allcaps_heading():
    assert _classify(P("DOMSLUT")) == Rubrik("DOMSLUT")


def test_classify_plain_is_stycke():
    block = _classify(P("Domstolen finner att talan skall bifallas i sin helhet."))
    assert isinstance(block, Stycke) and block.ordinal is None


def test_build_avgorande_maps_fields():
    head, body = parse_head_body(SAMPLE)
    av = build_avgorande(head, body)
    assert av.court_namn == "Arbetsdomstolen"
    assert av.referat == ["AD 1993 nr 100"]
    assert av.avgorandedatum == "1993-05-26"
    assert av.malnummer == ["A-31-1992", "A-125-1992"]
    assert av.lagrum[0].referens == "14 § arbetstidslagen (1982:673)"
    assert av.related == ["NJA 1985 s. 717", "NJA 1990 s. 591"]
    assert av.nyckelord == ["Kollektivavtal", "Veckovila"]
    assert av.sammanfattning == "Enligt en bestämmelse i arbetstidslagen."


def test_build_avgorande_prefers_index_identity():
    # the identity index supplies canonical referat/court/málnummer
    head, body = parse_head_body(SAMPLE)
    case = {"courts": ["ADO"], "referat": ["AD 1993 nr 100 (canonical)"],
            "malnummer": ["A-31-1992"], "avgorandedatum": "1993-05-26"}
    av = build_avgorande(head, body, case)
    assert av.court == "ADO"
    assert av.referat == ["AD 1993 nr 100 (canonical)"]
    assert av.malnummer == ["A-31-1992"]
