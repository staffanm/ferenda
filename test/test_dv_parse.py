"""Tests for the DV decision parser (API innehall path)."""

from accommodanda.dv.model import Fotnot, Rubrik, Stycke
from accommodanda.dv.parse import (
    case_uri,
    extract_footrefs,
    parse_api_record,
    parse_body,
    parse_innehall,
    to_artifact,
)
from accommodanda.dv.structure import flatten


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
    body = flatten(art["structure"])
    assert [b["type"] for b in body] == ["rubrik", "stycke"]
    # every block's text is an inline-run list (plain runs + link dicts),
    # the same shape SFS emits; this body has no citations -> single runs
    assert all(isinstance(b["text"], list) for b in body)
    assert body[0]["text"] == ["Bakgrund"]


def test_inline_links_in_body():
    html = "<p>Enligt 6 § räntelagen (1975:635) ska ränta utgå.</p>"
    art = to_artifact(parse_api_record({**RECORD, "innehall": html}))
    runs = flatten(art["structure"])[0]["text"]
    links = [r for r in runs if isinstance(r, dict)]
    assert links == [{"predicate": "dcterms:references",
                      "uri": "https://lagen.nu/1975:635#P6",
                      "text": "6 § räntelagen (1975:635)"}]
    assert runs[0] == "Enligt "
    assert runs[-1] == " ska ränta utgå."


# --- footnotes (HD's 2023+ format) --------------------------------------------

def test_html_headings_carry_their_level():
    blocks = parse_innehall("<h1>Svea hovrätt</h1><h2>SKÄL</h2><p>text.</p>")
    assert blocks == [Rubrik("Svea hovrätt", level=1),
                      Rubrik("SKÄL", level=2), Stycke("text.")]


def test_footnote_definitions_are_lifted_out_of_the_body():
    # the <sup>[N]</sup> marker (and the digit it doubles) is stripped; the
    # footnote-def paragraphs leave the block stream
    html = ("<p>Brödtext.[1]</p>"
            "<p><sup>[1]</sup>&nbsp;Förordning (EU) 2016/679.</p>"
            "<p><sup>[2]</sup>2&nbsp;EU:C:2023:145.</p>")
    blocks, footnotes = parse_body(html)
    # the body keeps the raw marker (stripped later, at citation-scan time)
    assert blocks == [Stycke("Brödtext.[1]")]
    assert footnotes == [Fotnot("1", "Förordning (EU) 2016/679."),
                         Fotnot("2", "EU:C:2023:145.")]


def test_extract_footrefs_strips_marker_and_doubled_digit():
    # a clean marker: text kept, marker recorded at its position
    assert extract_footrefs("svar.[10]") == ("svar.", [(5, "10")])
    # the OOXML artifact "C-268/213,[3]" is the case number + a doubled "3"; both
    # the stray digit and the marker come out, leaving the real "C-268/21"
    assert extract_footrefs("mål C-268/213,[3] efter") == (
        "mål C-268/21 efter", [(12, "3")])
    # an unrelated trailing digit is real text, only the bracket is the marker
    assert extract_footrefs("C-268/21.[2]") == ("C-268/21.", [(9, "2")])


def test_footnotes_reach_the_artifact_and_are_citation_scanned():
    html = ("<p>EU-domstolen meddelade dom i mål C-268/213,[3].</p>"
            "<p><sup>[3]</sup>3&nbsp;EU-domstolens dom i mål C-268/21.</p>")
    art = to_artifact(parse_api_record({**RECORD, "innehall": html}))
    # the body ECJ ref is the internal CELEX, not a 3-digit-year external one,
    # and the doubled "3" is gone from the link text ("mål" is the case prefix)
    body_links = [r for r in flatten(art["structure"])[0]["text"]
                  if isinstance(r, dict)]
    ecj = [r for r in body_links
           if r["uri"] == "https://lagen.nu/ext/celex/62021CJ0268"]
    assert ecj and ecj[0]["text"].endswith("C-268/21")
    # the inline marker became a zero-width footnote run
    assert {"predicate": "dcterms:references", "uri": "#fn-3",
            "text": "3", "kind": "footnote"} in body_links
    # the footnote itself is in the artifact, its case number linked
    [fn] = art["footnotes"]
    assert fn["num"] == "3"
    assert any(isinstance(r, dict) and r["uri"].endswith("62021CJ0268")
               for r in fn["text"])


# --- instance structure from the source's <h1> court headings -----------------

def test_h1_court_headings_build_the_instance_tree():
    html = ("<h1>Attunda tingsrätt</h1>"
            "<p>Bolaget väckte vid Attunda tingsrätt talan.</p>"
            "<p>Tingsrätten (rådmannen N.N.) anförde följande.</p><h2>SKÄL</h2>"
            "<p>Skäl.</p>"
            "<h1>Högsta domstolen</h1>"
            "<p>Bolaget överklagade och yrkade att HD skulle ändra.</p>"
            "<p>HD (justitierådet A.B.) anförde följande.</p><p>Domskäl.</p>")
    art = to_artifact(parse_api_record({**RECORD, "innehall": html}))
    tr, hd = art["structure"]
    assert (tr["type"], tr["court"]) == ("instans", "Attunda tingsrätt")
    assert (hd["type"], hd["court"]) == ("instans", "Högsta domstolen")
    # the prose restating the court ("överklagade ... HD") does not duplicate it
    assert [n["type"] for n in art["structure"]] == ["instans", "instans"]
