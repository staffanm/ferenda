"""Tests for the legacy DV parser (POI Word path).

The POI extraction itself needs a JVM and lives in dv_word; these tests
exercise the format-parsing and model-mapping logic over synthetic
paragraph streams, so they run without Java.
"""

import json
from types import SimpleNamespace

from accommodanda.dv.legacy import (
    _classify,
    _parse_hdo_notis,
    _parse_modern_hfd_notis,
    _parse_old_admin_notis,
    _split_malnummer,
    _split_notis_paras,
    build_avgorande,
    legacy_member,
    parse_head_body,
    write_direct_index,
    write_notis_index,
)
from accommodanda.dv.model import Rubrik, Stycke
from accommodanda.dv.word import Para


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
    assert _split_malnummer("A 52/94, A 54/94") == ["A52/94", "A54/94"]


def test_split_malnummer_single_spaced():
    # "Ö 2475-12" is one identifier, not split on the space
    assert _split_malnummer("Ö 2475-12") == ["Ö2475-12"]


def test_split_malnummer_multiple_with_court_prefix():
    # a court-prefix letter also bundles several målnummer, each of which
    # must keep its own prefix rather than collapsing into one garbage entry
    assert (_split_malnummer("Ö 2475-12 och Ö 2477-12")
            == ["Ö2475-12", "Ö2477-12"])


def test_split_malnummer_multi_letter_court_prefix():
    assert _split_malnummer("PMT 7498-16") == ["PMT7498-16"]
    assert _split_malnummer("PMÖÄ 8867-16") == ["PMÖÄ8867-16"]


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


def test_build_avgorande_splits_legacy_page_and_lopnummer_referat():
    av = build_avgorande({
        "Domstol": "Högsta domstolen",
        "Referat": "NJA 2011 s. 838 (NJA 2011:72)",
    }, [])
    assert av.referat == ["NJA 2011 s. 838", "NJA 2011:72"]


def test_build_avgorande_splits_spaced_legacy_compound_referat():
    av = build_avgorande({
        "Domstol": "Högsta domstolen",
        "Referat": "NJA 2004 s. 137 ( NJA 2004:16)",
    }, [])
    assert av.referat == ["NJA 2004 s. 137", "NJA 2004:16"]


def test_build_avgorande_restores_omitted_ad_series():
    av = build_avgorande({
        "Domstol": "Arbetsdomstolen",
        "Referat": "2016 nr 10",
    }, [])
    assert av.referat == ["AD 2016 nr 10"]


def test_missing_referat_starts_metadata_at_first_label():
    head, _ = parse_head_body([
        P("Patent- och marknadsöverdomstolen", bold=True),
        P(""),
        P("Målnummer:", bold=True),
        P("PMÖÄ 8867-16"),
        P("Avgörandedatum:", bold=True),
        P("2016-10-27"),
    ])
    assert "Referat" not in head
    assert head["Målnummer"] == ["PMÖÄ 8867-16"]
    assert head["Avgörandedatum"] == ["2016-10-27"]


def test_legacy_member_includes_zero_byte_source_record():
    member = {"store": "dv", "path": "downloaded/dv/HDO/2003_not_1.doc"}
    case = {"members": [{"store": "domstol", "path": "api.json"}, member]}
    assert legacy_member(case) is member


def test_split_and_parse_hdo_notis_tracks_month_and_carried_day():
    chunks = _split_notis_paras("HDO", 2009, [
        P("Januari"),
        P("Den 27:e. 1. (Ö 1242-08) S.O. mot Riksåklagaren.", bold=True),
        P("Första avgörandet."),
        P("2. (T 3734-05) M.N. mot SJ AB.", bold=True),
        P("Andra avgörandet."),
    ])
    assert set(chunks) == {1, 2}
    head, body = _parse_hdo_notis(chunks[2][0], 2009)
    assert head["Målnummer"] == ["T 3734-05"]
    assert head["Avgörandedatum"] == ["2009-01-27"]
    assert head["Rubrik"] == ["M.N. mot SJ AB."]
    assert [para.text for para in body] == [
        "M.N. mot SJ AB.", "Andra avgörandet."]


def test_split_hdo_new_nr_heading_form():
    chunks = _split_notis_paras("HDO", 2021, [
        P("Maj"),
        P("Nr 4"),
        P("Den 3:e. (Ö 5935-20) J.B. angående kontaktförbud.", bold=True),
        P("Avgörandet."),
    ])
    head, body = _parse_hdo_notis(chunks[4][0], 2021)
    assert head["Målnummer"] == ["Ö 5935-20"]
    assert head["Avgörandedatum"] == ["2021-05-03"]
    assert [para.text for para in body] == [
        "J.B. angående kontaktförbud.", "Avgörandet."]


def test_split_and_parse_old_reg_notis_metadata_and_wrapped_body():
    chunks = _split_notis_paras("REG", 1994, [
        P("R4 M:REGR Unr:g Lnr:RÅ1994not1"),
        P("G:2902 D:5495 1993 A:93 12 29"),
        P("Uppslagsord: Återställande; Ombuds försummelse"),
        P("Lagrum:"),
        P("Ej angivet"),
        P("Not 1. Ansökan om återställande av"),
        P("försutten tid."),
        P("Regeringsrätten avslår ansökningen."),
    ])
    head, body = _parse_old_admin_notis(chunks[1][0], "REG")
    assert head["Målnummer"] == ["5495-1993"]
    assert head["Avgörandedatum"] == ["1993-12-29"]
    assert head["Sökord"] == ["Återställande", "Ombuds försummelse"]
    assert head["Rubrik"] == ["Ansökan om återställande av försutten tid."]
    assert [para.text for para in body] == [
        "Ansökan om återställande av försutten tid.",
        "Regeringsrätten avslår ansökningen.",
    ]


def test_split_old_reg_repairs_whitespace_inside_field_ordinal():
    chunks = _split_notis_paras("REG", 2000, [
        P("R4 M:REGR Lnr:RÅ2000not8 9"),
        P("Not 89. Ansökan. - Text."),
    ])
    assert set(chunks) == {89}


def test_split_and_parse_modern_hfd_notis():
    chunks = _split_notis_paras("HFD", 2016, [
        P("Not 1"),
        P("Högsta förvaltningsdomstolen meddelade den 5 februari 2016 "
          "följande beslut (mål nr 2320-15)."),
        P("Bakgrund", bold=True),
        P("Saken prövades."),
    ])
    head, body = _parse_modern_hfd_notis(chunks[1][0])
    assert head["Målnummer"] == ["2320-15"]
    assert head["Avgörandedatum"] == ["2016-02-05"]
    assert head["Rubrik"] == ["Beslut den 5 februari 2016 i mål 2320-15"]
    assert [para.text for para in body] == ["Bakgrund", "Saken prövades."]


def test_parse_modern_hfd_grouped_notis_with_multiple_decisions():
    chunks = _split_notis_paras("HFD", 2020, [
        P("Not 3"),
        P("I.", bold=True),
        P("Högsta förvaltningsdomstolen meddelade den 5 februari 2020 "
          "följande dom (mål nr 3447-19)."),
        P("Första avgörandet."),
        P("II.", bold=True),
        P("Högsta förvaltningsdomstolen meddelade den 5 februari 2020 "
          "följande dom (mål nr 3477-19)."),
        P("Andra avgörandet."),
    ])
    head, body = _parse_modern_hfd_notis(chunks[3][0])
    assert head["Målnummer"] == ["3447-19", "3477-19"]
    assert head["Avgörandedatum"] == ["2020-02-05"]
    assert head["Rubrik"] == [
        "Dom den 5 februari 2020 i mål 3447-19 och 3477-19"]
    assert [para.text for para in body] == [
        "I.", "Första avgörandet.", "II.", "Andra avgörandet."]
    avgorande = build_avgorande(head, body)
    assert avgorande.malnummer == ["3447-19", "3477-19"]


def test_parse_modern_hfd_heading_without_foljande():
    chunks = _split_notis_paras("HFD", 2020, [
        P("Not 41"),
        P("Högsta förvaltningsdomstolen meddelade den 29 juni 2020 "
          "dom (mål nr 2613-20)."),
        P("Avgörandet."),
    ])
    head, body = _parse_modern_hfd_notis(chunks[41][0])
    assert head["Målnummer"] == ["2613-20"]
    assert head["Avgörandedatum"] == ["2020-06-29"]
    assert [para.text for para in body] == ["Avgörandet."]


def test_notis_index_intersects_word_headings_with_placeholder_ledger(
        tmp_path, monkeypatch):
    bundle = tmp_path / "HDO/2009/HDO_2009_notis_001--002.docx"
    bundle.parent.mkdir(parents=True)
    bundle.write_bytes(b"word")
    monkeypatch.setattr(
        "accommodanda.dv.legacy._read_notis_bundle",
        lambda path: {1: [object()], 2: [object()]})
    indexed, extra = write_notis_index(tmp_path, {("HDO", 2009, 1)})
    payload = json.loads((tmp_path / "index.json").read_text())
    assert (indexed, extra) == (1, 1)
    assert payload["placeholder_count"] == 1
    assert payload["bundles"][0]["ordinals"] == [1]


def test_direct_index_parses_nonempty_per_case_words_only(tmp_path, monkeypatch):
    source = tmp_path / "HDO/opaque.docx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"word")
    (tmp_path / "HDO/zero.doc").touch()
    bundle = tmp_path / "notis-bundles/HDO/2020/bundle.docx"
    bundle.parent.mkdir(parents=True)
    bundle.write_bytes(b"bundle")
    monkeypatch.setattr(
        "accommodanda.dv.legacy.parse_legacy_file",
        lambda path: SimpleNamespace(
            malnummer=["Ö 1-20"], referat=["NJA 2020 s. 1"],
            avgorandedatum="2020-01-01"))

    assert write_direct_index(tmp_path) == 1

    payload = json.loads((tmp_path / "legacy-index.json").read_text())
    assert payload["document_count"] == 1
    assert payload["documents"][0]["path"] == "HDO/opaque.docx"
    assert payload["documents"][0]["referat"] == ["NJA 2020 s. 1"]
