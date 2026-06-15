"""Tests for the DV decision parser (API innehall path)."""

from accommodanda.dv.parse import (case_uri, parse_innehall, parse_api_record,
                                   to_artifact)
from accommodanda.dv.model import Rubrik, Stycke


def test_case_uri_mints_old_rinfo_scheme():
    # referat cases mint via the RATTSFALL parser -> identical to citations
    assert case_uri("AD 1993 nr 100") == "https://lagen.nu/dom/ad/1993:100"
    assert case_uri("NJA 1994 s. 12") == "https://lagen.nu/dom/nja/1994s12"
    assert case_uri("RÅ 2009 ref. 5") == "https://lagen.nu/dom/ra/2009:5"
    assert case_uri("MÖD 2008:8") == "https://lagen.nu/dom/mod/2008:8"
    assert case_uri("NJA 2003 not 1") == "https://lagen.nu/dom/nja/2003/not/1"


def test_case_uri_falls_back_for_non_referat():
    # a non-referat id RATTSFALL can't parse keeps a stable slug URI
    assert case_uri("HSV B3689-08") == "https://lagen.nu/dom/HSV_B3689_08"


def kinds(blocks):
    return [(type(b).__name__, b.text) for b in blocks]


def test_allcaps_heading():
    blocks = parse_innehall("<p>ÖVERKLAGAT AVGÖRANDE</p>")
    assert blocks == [Rubrik("ÖVERKLAGAT AVGÖRANDE")]


def test_known_label_heading_titlecase():
    assert parse_innehall("<p>Skälen för avgörandet</p>") == [
        Rubrik("Skälen för avgörandet")]
    assert parse_innehall("<p>Bakgrund</p>") == [Rubrik("Bakgrund")]


def test_ordinary_paragraph_is_stycke():
    html = ("<p>Den som uppsåtligen berövar annan livet döms för mord "
            "till fängelse i lägst tio år.</p>")
    blocks = parse_innehall(html)
    assert len(blocks) == 1 and isinstance(blocks[0], Stycke)
    assert blocks[0].ordinal is None


def test_numbered_paragraph():
    blocks = parse_innehall("<p>12.&nbsp;&nbsp;&nbsp;Frågan i målet är "
                            "om beskattning ska ske.</p>")
    assert blocks == [Stycke("Frågan i målet är om beskattning ska ske.",
                             ordinal="12")]


def test_br_becomes_newline_and_blocks_heading():
    # a multi-line party block keeps its line breaks and is not a heading
    blocks = parse_innehall("<p>KLAGANDE<br>Skatteverket<br>171 94 Solna</p>")
    assert len(blocks) == 1 and isinstance(blocks[0], Stycke)
    assert blocks[0].text == "KLAGANDE\nSkatteverket\n171 94 Solna"


def test_entities_and_nbsp():
    blocks = parse_innehall("<p>Bolaget&nbsp;AB &amp; Co överklagade.</p>")
    assert blocks[0].text == "Bolaget AB & Co överklagade."


def test_separator_dropped():
    assert parse_innehall("<p>______________</p>") == []
    assert parse_innehall("<p>&nbsp;</p>") == []


def test_document_order_preserved():
    html = "<p>BAKGRUND</p><p>Något hände.</p><p>DOMSLUT</p><p>Talan bifalls.</p>"
    assert kinds(parse_innehall(html)) == [
        ("Rubrik", "BAKGRUND"), ("Stycke", "Något hände."),
        ("Rubrik", "DOMSLUT"), ("Stycke", "Talan bifalls.")]


RECORD = {
    "domstol": {"domstolKod": "HFD", "domstolNamn": "Högsta förvaltningsdomstolen"},
    "malNummerLista": ["1880-16"],
    "referatNummerLista": ["HFD 2016 ref. 69"],
    "avgorandedatum": "2016-10-14",
    "publiceringsform": "REFERAT",
    "typ": "VAGLEDANDE_MEN_EJ_PREJUDICERANDE",
    "rattsomradeLista": ["Skatt"],
    "nyckelordLista": [" Gåva", "Generationsskifte"],
    "lagrumLista": [{"referens": "8 kap. 2 § inkomstskattelagen (1999:1229)",
                     "sfsNummer": "1999:1229"}],
    "forarbeteLista": [],
    "sammanfattning": "  En förmögenhetsöverföring ... ",
    "hanvisadePubliceringarLista": [],
    "europarattsligaAvgorandenLista": [],
    "innehall": "<p>Bakgrund</p><p>L.H.S. och C.S. äger aktierna.</p>",
}


def test_api_record_metadata_mapping():
    av = parse_api_record(RECORD)
    assert av.court == "HFD"
    assert av.malnummer == ["1880-16"]
    assert av.referat == ["HFD 2016 ref. 69"]
    assert av.nyckelord == ["Gåva", "Generationsskifte"]  # stripped
    assert av.lagrum[0].sfsnummer == "1999:1229"
    assert av.sammanfattning == "En förmögenhetsöverföring ..."  # stripped
    assert kinds(av.body) == [("Rubrik", "Bakgrund"),
                              ("Stycke", "L.H.S. och C.S. äger aktierna.")]


def test_artifact_shape():
    art = to_artifact(parse_api_record(RECORD))
    # the document URI is minted via the RATTSFALL citation parser, so it is
    # byte-identical to what a citation "HFD 2016 ref. 69" produces (the old
    # dom/{serie}/{year}:{nr} scheme), not an ad-hoc slug
    assert art["uri"] == "https://lagen.nu/dom/hfd/2016:69"
    assert art["metadata"]["lagrum"][0]["sfsnummer"] == "1999:1229"
    assert "references" not in art  # citations live inline in the body now
    assert [b["type"] for b in art["body"]] == ["rubrik", "stycke"]
    # every block's text is an inline-run list (plain runs + link dicts),
    # the same shape SFS emits; this body has no citations -> single runs
    assert all(isinstance(b["text"], list) for b in art["body"])
    assert art["body"][0]["text"] == ["Bakgrund"]


def test_inline_links_in_body():
    html = "<p>Enligt 6 § räntelagen (1975:635) ska ränta utgå.</p>"
    art = to_artifact(parse_api_record({**RECORD, "innehall": html}))
    runs = art["body"][0]["text"]
    links = [r for r in runs if isinstance(r, dict)]
    assert links == [{"predicate": "dcterms:references",
                      "uri": "https://lagen.nu/1975:635#P6",
                      "text": "6 § räntelagen (1975:635)"}]
    assert runs[0] == "Enligt "
    assert runs[-1] == " ska ränta utgå."
