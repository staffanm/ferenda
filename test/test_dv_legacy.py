"""Tests for the legacy DV parser (POI Word path).

The POI extraction itself needs a JVM and lives in lib/poi; these tests
exercise the format-parsing and model-mapping logic over synthetic
paragraph streams, so they run without Java.
"""

from accommodanda.lib.poi import Para
from accommodanda.dv.legacy import (
    build_avgorande, parse_head_body, _classify, _split_malnummer)
from accommodanda.dv.model import Hanvisning, Rubrik, Stycke


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
    P("Litteratur:", bold=True), P("Ekelöf, Rättegång IV; Fitger, Rättegångsbalken"),
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


def test_split_malnummer_multiple_with_court_prefix():
    # a court-prefix letter also bundles several målnummer, each of which
    # must keep its own prefix rather than collapsing into one garbage entry
    assert (_split_malnummer("Ö 2475-12 och Ö 2477-12")
            == ["Ö2475-12", "Ö2477-12"])


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
    # legacy Rättsfall strings carry no grupp join key -- fritext only
    assert av.related == [Hanvisning("NJA 1985 s. 717"),
                          Hanvisning("NJA 1990 s. 591")]
    # a Litteratur footer line packs several works separated by ";"
    assert av.litteratur == ["Ekelöf, Rättegång IV", "Fitger, Rättegångsbalken"]
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


# ---------------------------------------------------------------------------
# POI text cleanup (pure -- no JVM)

from accommodanda.lib.poi import _clean  # noqa: E402


def test_clean_strips_field_instruction_keeps_result():
    # \x13 instruction \x14 result \x15: only the displayed result is text
    assert _clean("\x13 \x01\x141000-01\x15") == "1000-01"
    assert _clean("Referat: \x13 REF x \x14RÅ 2003 ref. 54\x15") == \
        "Referat: RÅ 2003 ref. 54"


def test_clean_drops_resultless_field_entirely():
    assert _clean("a \x13 PAGEREF foo \x15b") == "a b"


def test_clean_strips_stray_field_markers():
    assert _clean("a\x14b\x15c\x13d") == "abcd"


# ---------------------------------------------------------------------------
# Notisfall (frozen intermediate XML)

from accommodanda.dv.legacy import notis_paras, parse_notis  # noqa: E402

TRIPS_XML = """<body>
<para>R4      M:REGR        Unr:g               Lnr:RÅ2001not122</para>
<para>G:3001  D:3625-2001   A:01-09-04  Avd:2   Reg:1</para>
<para></para>
<para>Ledamöter och föredragande: Se ovan</para>
<para>Uppslagsord: Rättsprövning - detaljplan; Byggnadsmål -
rättsprövning</para>
<para></para>
<para>Lagrum:</para>
<para>1 § lagen (1988:205) om rättsprövning av vissa förvaltningsbeslut</para>
<para></para>
<para>Not 122. Ansökan av X om rättsprövning av beslut ang. detaljplan.</para>
<para>*REGI</para>
</body>"""

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WP_XML = ("""<body xmlns:w="%s">
<w:p><w:r><w:t>R4M:HFDUnr:gLnr:HFD 2012 not 30</w:t></w:r></w:p>
<w:p><w:r><w:t>G:0101D:4799-10A:2012-05-30Avd:2Reg:1</w:t></w:r></w:p>
<w:p><w:r><w:t>Uppslagsord: Förhandsbesked, skatter</w:t></w:r></w:p>
<w:p><w:r><w:t>Not 30. Överklagande av A och B av ett förhandsbesked.</w:t></w:r></w:p>
</body>""" % W)

HDO_LEAD_XML = ("""<body xmlns:w="%s">
<w:p><w:r><w:t>Maj</w:t></w:r></w:p>
<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Den 10:e. 12.</w:t></w:r>
<w:r><w:t> (B 1715-09) I.C. mot Riksåklagaren angående brott.</w:t></w:r></w:p>
<w:p><w:r><w:t>Åklagaren yrkade ansvar enligt gärningsbeskrivningen.</w:t></w:r></w:p>
</body>""" % W)

HDO_NOLEAD_XML = ("""<body xmlns:w="%s">
<w:p><w:r><w:t>Mars</w:t></w:r></w:p>
<w:p><w:r><w:t>I ett mål angående grovt narkotikabrott hade hovrätten häktat.</w:t></w:r></w:p>
<w:p><w:r><w:t>HD fann att skäl för häktning saknades och hävde beslutet.</w:t></w:r></w:p>
</body>""" % W)


def test_notis_paras_both_flavors():
    assert notis_paras(TRIPS_XML)[0].text.startswith("R4")
    wp = notis_paras(HDO_LEAD_XML)
    assert wp[0].text == "Maj"
    assert wp[1].bold and wp[1].text.startswith("Den 10:e. 12.")


def test_notis_trips_header_and_sections():
    av = parse_notis(TRIPS_XML, "REG", "2001_not_122.xml")
    assert av.referat == ["RÅ 2001 not 122"]
    assert av.malnummer == ["3625-2001"]
    assert av.avgorandedatum == "2001-09-04"
    assert av.nyckelord == ["Rättsprövning - detaljplan",
                            "Byggnadsmål - rättsprövning"]
    assert [l.referens for l in av.lagrum] == [
        "1 § lagen (1988:205) om rättsprövning av vissa förvaltningsbeslut"]
    assert len(av.body) == 1 and av.body[0].text.startswith("Not 122.")


def test_notis_concatenated_trips_header():
    av = parse_notis(WP_XML, "HFD", "2012_not_30.xml")
    assert av.referat == ["HFD 2012 not 30"]
    assert av.malnummer == ["4799-10"]
    assert av.avgorandedatum == "2012-05-30"
    assert av.body[0].text.startswith("Not 30.")


def test_notis_hdo_month_lead():
    av = parse_notis(HDO_LEAD_XML, "HDO", "2009_not_12.xml")
    assert av.referat == ["NJA 2009 not 12"]
    # date assembled from the month heading, the "Den 10:e" lead and the
    # filename year; målnummer from the lead's parenthesis
    assert av.avgorandedatum == "2009-05-10"
    assert av.malnummer == ["B 1715-09"]
    assert av.body[0].text.startswith("(B 1715-09) I.C. mot")


def test_notis_hdo_without_lead_keeps_whole_body():
    av = parse_notis(HDO_NOLEAD_XML, "HDO", "2008_not_20.xml")
    assert av.avgorandedatum is None and av.malnummer == []
    assert [b.text[:10] for b in av.body] == ["I ett mål ", "HD fann at"]


def test_notis_index_identity_wins():
    case = {"referat": ["NJA 2008 not 20"], "malnummer": ["Ö 528-08"],
            "avgorandedatum": "2008-03-13", "courts": ["HDO"]}
    av = parse_notis(HDO_NOLEAD_XML, "HDO", "2008_not_20.xml", case)
    assert av.malnummer == ["Ö 528-08"]
    assert av.avgorandedatum == "2008-03-13"


def test_notis_sammanfattning_from_oracle_rubrik():
    case = {"referat": ["NJA 2008 not 20"], "courts": ["HDO"],
            "referatrubrik": "M.J. m.fl. mot riksåklagaren angående häktning."}
    av = parse_notis(HDO_NOLEAD_XML, "HDO", "2008_not_20.xml", case)
    assert av.sammanfattning == "M.J. m.fl. mot riksåklagaren angående häktning."


def test_word_referat_falls_back_to_oracle_rubrik():
    # a legacy Word head without its own Rubrik row takes the frozen oracle's
    head, body = parse_head_body([P("Regeringsrätten", bold=True),
                                  P("RÅ 1994 ref. 1"),
                                  P("REFERAT", bold=True), P("Text.")])
    av = build_avgorande(head, body, {"referatrubrik": "Oracle-rubriken."})
    assert av.sammanfattning == "Oracle-rubriken."
