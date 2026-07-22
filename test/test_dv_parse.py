"""Tests for the DV decision parser (API innehall path)."""

from datetime import date
from pathlib import Path

from accommodanda.dv.model import Avgorande, Fotnot, Rubrik, Stycke
from accommodanda.dv.parse import (
    _body_lines,
    _inject_numbers,
    case_uri,
    classify_pdf,
    clean_nyckelord,
    decision_date_from_text,
    decision_dates_from_text,
    extract_footrefs,
    parse_api_record,
    parse_body,
    parse_innehall,
    parse_pdf_record,
    to_artifact,
)
from accommodanda.dv.structure import flatten
from accommodanda.lib import catalog
from accommodanda.lib.pdftext import Line, Para, Run, page_paragraphs, pdf_images

FIXTURES = Path(__file__).parent / "files" / "dv"


def test_body_lines_drops_sub_body_size_marginalia():
    # R2: a verdict PDF's header/footer/vertical-doc-id run at a smaller font than
    # the body (19). _body_lines keeps only body-size runs, rebuilding each line --
    # so a doc-id fragment sharing a body line's baseline ("1 3 beslutet") loses the
    # "1 3", and a whole sub-size line (running header) is dropped entirely.
    def line(top, runs):
        return Line(text=" ".join(r.text for r in runs), top=top,
                    bold=False, lead_bold=False, italic=False,
                    size=max(r.size for r in runs), runs=runs)
    lines = [
        line(90, [Run(146, 300, "HÖGSTA DOMSTOLEN BESLUT Ä 8213-25", False, False, 18)]),
        line(200, [Run(213, 800, "Bakgrund till målet.", False, False, 19)]),
        line(1131, [Run(46, 52, "1", False, False, 15),
                    Run(60, 66, "3", False, False, 15),
                    Run(213, 800, "beslutet ska verkställas.", False, False, 19)]),
        line(1150, [Run(46, 52, "D", False, False, 15)]),   # a doc-id fragment alone
    ]
    kept = _body_lines(lines, 19)
    texts = [l.text for l in kept]
    assert texts == ["Bakgrund till målet.", "beslutet ska verkställas."]


def test_inject_numbers_forces_paragraph_breaks():
    # R2: HD prints domskäl numbers as margin bitmaps and packs the paragraphs with
    # no extra vertical gap. _inject_numbers prepends "N. " to the line at each
    # bitmap top and returns those tops, which page_paragraphs must treat as forced
    # breaks -- else the gap heuristic merges the (unspaced) paragraphs and buries
    # the number mid-text.
    def line(top, text):
        return Line(text=text, top=top, bold=False, lead_bold=False,
                    italic=False, size=19,
                    runs=[Run(213, 800, text, False, False, 19)])
    # three consecutive paragraphs, evenly spaced (no paragraph gap the heuristic
    # could see); bitmaps sit on the 1st, 3rd and 5th lines
    lines = [line(100, "första stycket börjar"), line(133, "och fortsätter här"),
             line(166, "andra stycket börjar"), line(199, "med en fortsättning"),
             line(232, "tredje stycket börjar")]
    numbers = [(100, 5), (166, 6), (232, 7)]      # a mid-document run, values 5-7
    injected, breaks = _inject_numbers(lines, numbers)
    assert breaks == {100, 166, 232}
    assert injected[0].text == "5. första stycket börjar"
    paras = page_paragraphs(injected, "", 1, force_break_tops=breaks)
    assert len(paras) == 3                         # split despite no gap
    assert paras[0].text.startswith("5. första stycket börjar och fortsätter")
    assert paras[1].text.startswith("6. andra stycket")
    assert paras[2].text.startswith("7. tredje stycket")


def test_classify_pdf_drops_boilerplate_and_numbers_paragraphs():
    # R2: (pageno, Para) pairs -> body blocks, tagged with their PDF page; the court
    # header/footer/page markers dropped and HD/HFD numbered reasons kept as ordinal
    # stycken (the number is injected upstream from the margin bitmap)
    paged = [
        (1, Para("Sida 1 (10) PROTOKOLL Aktbilaga 43 Mål nr B 3687-22")),
        (1, Para("PARTER", bold=True)),
        (1, Para("Dok.Id 287315 HÖGSTA DOMSTOLEN Postadress Telefon 08-561 Expeditionstid")),
        (2, Para("HÖGSTA DOMSTOLEN B 3687-22 Sida 2 (10)")),
        (2, Para("Bakgrund", bold=True)),
        (2, Para("1. Under våren 2018 genomförde bolaget en upphandling.")),
        (2, Para("2. Beslutet överklagades.")),
    ]
    blocks = classify_pdf(paged)
    assert Rubrik(text="PARTER", page=1) in blocks    # level 0: heuristic heading
    assert Rubrik(text="Bakgrund", page=2) in blocks
    styckes = {b.ordinal: b for b in blocks if isinstance(b, Stycke)}
    assert styckes["1"].text.startswith("Under våren")   # marker stripped, ordinal kept
    assert styckes["1"].page == 2                        # tagged with its PDF page
    assert styckes["2"].text.startswith("Beslutet")
    # court boilerplate is gone
    assert not any("Sida" in b.text and "(" in b.text for b in blocks)
    assert not any("Dok.Id" in b.text or "Postadress" in b.text for b in blocks)


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


def test_explicit_publishing_court_date_overrides_bad_api_metadata():
    record = {
        **RECORD,
        "domstol": {"domstolKod": "HDO", "domstolNamn": "Högsta domstolen"},
        "referatNummerLista": ["NJA 2018 s. 405"],
        "avgorandedatum": "2016-06-12",
        "innehall": ("<p>Tingsrätten meddelade den 11 juli 2017 beslut.</p>"
                     "<p>HD (justitieråden A och B) meddelade den 12 juni "
                     "2018 följande slutliga beslut.</p>"),
    }
    assert parse_api_record(record).avgorandedatum == "2018-06-12"


def test_text_date_requires_valid_sane_unambiguous_publishing_court_date():
    def find(*texts, referat=("NJA 2018 s. 405",)):
        return decision_date_from_text(
            [Stycke(text) for text in texts], "HDO", "Högsta domstolen",
            referat, today=date(2026, 7, 12))

    # A lower-instance date is not evidence for an HD artifact.
    assert find("Tingsrätten meddelade den 12 juni 2018 beslut.") is None
    # An earlier same-court decision in the procedural history is not enough.
    assert find("HD meddelade den 1 maj 2018 ett tidigare beslut.") is None
    # Calendar validity, future bounds and referat-year proximity are enforced.
    assert find("HD meddelade den 31 februari 2018 följande dom.") is None
    assert find("HD meddelade den 13 juli 2026 följande dom.", referat=()) is None
    assert find("HD meddelade den 12 juni 2015 följande dom.") is None
    assert decision_date_from_text(
        [Stycke("HD meddelade den 12 juni 2000 följande dom.")],
        "HDO", "Högsta domstolen", [], metadata_date="2018-06-12",
        today=date(2026, 7, 12)) is None
    # Two distinct decisions by the publishing court are ambiguous.
    assert find("HD meddelade den 1 maj 2018 följande beslut.",
                "HD meddelade den 12 juni 2018 följande dom.") is None


def test_multiple_published_decisions_keep_every_text_date_and_latest_primary():
    record = {
        **RECORD,
        "domstol": {"domstolKod": "HDO", "domstolNamn": "Högsta domstolen"},
        "referatNummerLista": ["NJA 2001 s. 191"],
        "avgorandedatum": "2001-03-20",
        "innehall": ("<p>HD:s domar meddelades, i målet under I d. 20 mars "
                     "2001 och i målet under II d. 19 april 2001.</p>"),
    }
    decision = parse_api_record(record)
    assert decision.avgorandedatum_lista == ["2001-03-20", "2001-04-19"]
    assert decision.avgorandedatum == "2001-04-19"
    artifact = to_artifact(decision)
    assert artifact["avgorandedatum_lista"] == ["2001-03-20", "2001-04-19"]


def test_historical_hd_footer_is_formal_date_evidence():
    assert decision_dates_from_text(
        [Stycke("HD:s dom meddelades d 18 maj 1998 (mål nr B 618/96).")],
        "HDO", "Högsta domstolen", ["NJA 1998 s. 283"], "1998-05-20",
        today=date(2026, 7, 12)) == ["1998-05-18"]


def test_historical_court_iso_heading_is_formal_date_evidence():
    body = [
        Stycke("Kammarrätten i Göteborg (1993-02-15, A, B) yttrade."),
        Stycke("Regeringsrätten (1997-01-10, A, B) yttrade: Skälen."),
    ]
    assert decision_dates_from_text(
        body, "REGR", "Regeringsrätten", ["RÅ 1997 ref. 7"], "1994-01-10",
        today=date(2026, 7, 12)) == ["1997-01-10"]
    assert decision_dates_from_text(
        [Stycke("Regeringsrätten (1997-02-31, A, B) yttrade.")],
        "REGR", "Regeringsrätten", ["RÅ 1997 ref. 7"], "1994-01-10",
        today=date(2026, 7, 12)) == []


def test_multiple_hovratt_rulings_are_all_publishing_dates():
    body = [
        Stycke("Hovrätten (A och B) anförde i beslut den 21 november 2012 "
               "följande."),
        Stycke("Hovrätten (A och B) anförde i beslut den 22 november 2012 "
               "i huvudsak följande."),
    ]
    assert decision_dates_from_text(
        body, "HYOD", "Svea hovrätts hyresrättsliga avgöranden",
        ["RH 2013:9"], "2012-11-21", today=date(2026, 7, 12)) == [
            "2012-11-21", "2012-11-22"]


def test_historical_hovratt_combined_date_formats_keep_both_dates():
    cases = [
        ([Stycke("Hovrättens beslut meddelade: den 8 maj 2013 (I); den 14 "
                 "maj 2013 (II).")], "RH 2013:23", "2013-05-08",
         ["2013-05-08", "2013-05-14"]),
        ([Stycke("Svea hovrätt (2002-10-07 och 2002-10-30, A och B) "
                 "meddelade prövningstillstånd.")], "RH 2003:26", "2002-10-07",
         ["2002-10-07", "2002-10-30"]),
    ]
    for body, referat, metadata_date, expected in cases:
        assert decision_dates_from_text(
            body, "HSV", "Svea hovrätt", [referat], metadata_date,
            today=date(2026, 7, 12)) == expected


def test_pmod_formal_release_heading_is_date_evidence():
    assert decision_dates_from_text(
        [Stycke("BESLUT (att meddelas 2022-12-14)")], "PMOD",
        "Patent- och marknadsöverdomstolen", ["PMÖD 2022:9"], "2022-11-14",
        today=date(2026, 7, 12)) == ["2022-12-14"]


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


def test_hand_authored_legacy_decision_skeleton():
    """Oracle-grade cues for split case -> instances -> proposal/ruling/dissent.

    This exercises the parser's own segmenter, not merely the structural
    golden's artifact reducer. The prose is deliberately minimal but follows
    the published editorial formulas each recognizer claims to understand.
    """
    html = ("<p>I</p>"
            "<p>Bolaget väckte talan vid Stockholms tingsrätt.</p>"
            "<p>Tingsrätten (rådmannen A.A.) anförde följande.</p>"
            "<p>Domslut</p><p>Talan avslås.</p>"
            "<p>Bolaget överklagade domen till Högsta domstolen.</p>"
            "<p>Målet avgjordes efter föredragning.</p>"
            "<p>Föredraganden föreslog i betänkande att HD skulle meddela "
            "följande dom.</p>"
            "<p>HD:s avgörande</p>"
            "<p>HD (justitieråden A.A. och B.B.) meddelade följande.</p>"
            "<p>Domskäl</p><p>Skälen här.</p><p>HD:s avgörande</p>"
            "<p>Justitierådet C.C. var skiljaktig och anförde.</p>")
    structure = to_artifact(parse_api_record({**RECORD, "innehall": html}))[
        "structure"]
    [part] = [node for node in structure if node["type"] == "delmal"]
    assert part["ordinal"] == "I"
    district, supreme = [node for node in part["children"]
                         if node["type"] == "instans"]
    assert district["court"] == "Stockholms tingsrätt"
    assert supreme["court"] == "Högsta domstolen"
    assert [node["type"] for node in district["children"]
            if node["type"] in {"dom"}] == ["dom"]
    assert [node["type"] for node in supreme["children"]
            if node["type"] in {"betankande", "dom", "skiljaktig"}] == [
                "betankande", "dom", "skiljaktig"]
    proposal, ruling = [node for node in supreme["children"]
                        if node["type"] in {"betankande", "dom"}]
    assert [node["type"] for node in proposal["children"]
            if node["type"] in {"domskal", "domslut"}] == [
                "domskal", "domslut"]
    assert [node["type"] for node in ruling["children"]
            if node["type"] in {"domskal", "domslut"}] == [
                "domskal", "domslut"]


def test_ordinary_presentation_is_not_a_judicial_proposal():
    html = ("<p>Domskäl</p>"
            "<p>Migrationsverket (generaldirektören A.A.) beslöt att lämna "
            "ansökan utan bifall.</p>"
            "<p>Föredragningen gjordes av it-direktören D.K. och gällde "
            "informationssäkerhet.</p>"
            "<p>Avgörande för ansvarsfrågan är om uppgiften röjdes.</p>"
            "<p>Domslut</p>")
    structure = to_artifact(parse_api_record({**RECORD, "innehall": html}))[
        "structure"]
    assert not [node for node in structure[0]["children"]
                if node["type"] == "betankande"]
    [ruling] = [node for node in structure[0]["children"]
                if node["type"] == "dom"]
    assert [node["type"] for node in ruling["children"]
            if node["type"] in {"domskal", "domslut"}] == [
                "domskal", "domslut"]


def test_hd_proposal_opens_the_supreme_court_instance():
    html = ("<p>Bolaget väckte talan vid Stockholms tingsrätt.</p>"
            "<p>Tingsrätten (rådmannen A.A.) anförde följande.</p>"
            "<p>Bolaget överklagade till Svea hovrätt.</p>"
            "<p>Hovrätten (hovrättsrådet B.B.) anförde följande.</p>"
            "<p>Målet avgjordes efter föredragning.</p>"
            "<p>Föredraganden föreslog i betänkande att HD skulle meddela "
            "följande dom.</p>"
            "<p>HD (justitierådet C.C.) meddelade följande dom.</p>")
    structure = to_artifact(parse_api_record({**RECORD, "innehall": html}))[
        "structure"]
    assert [node.get("court") for node in structure] == [
        "Stockholms tingsrätt", "Svea hovrätt", "Högsta domstolen"]
    assert [node["type"] for node in structure[-1]["children"]
            if node["type"] in {"betankande", "dom"}] == ["betankande", "dom"]
    assert not [node for node in structure[-2]["children"]
                if node["type"] == "betankande"]


def test_explicit_hd_foredragning_remains_a_proposal_marker():
    html = ("<p>Bolaget överklagade till HD.</p>"
            "<p>HD avgjorde målet efter föredragning.</p>"
            "<p>HD:s avgörande</p>")
    [instance] = to_artifact(parse_api_record({**RECORD, "innehall": html}))[
        "structure"]
    assert instance["court"] == "Högsta domstolen"
    assert [node["type"] for node in instance["children"]
            if node["type"] == "betankande"] == ["betankande"]


def test_appended_lower_court_judgment_opens_a_new_instance():
    html = ("<p>Domskäl</p><p>Arbetsdomstolens skäl.</p><p>Domslut</p>"
            "<h2>BILAGA</h2>"
            "<p>Tingsrättens dom (ordförande rådmannen A.A.)</p>"
            "<h2>DOMSKÄL</h2><p>Tingsrättens skäl.</p><h2>DOMSLUT</h2>")
    structure = to_artifact(parse_api_record({**RECORD, "innehall": html}))[
        "structure"]
    assert [node["type"] for node in structure] == ["instans", "instans"]
    assert all(any(child["type"] == "dom" for child in node["children"])
               for node in structure)
    for instance in structure:
        [ruling] = [child for child in instance["children"]
                    if child["type"] == "dom"]
        assert len([child for child in ruling["children"]
                    if child["type"] == "domslut"]) == 1


# --- curated metadata normalization (lagrum/förarbeten/rättsfall/litteratur) --

def test_curated_lagrum_resolves_with_typed_predicate():
    art = to_artifact(parse_api_record(RECORD))
    [entry] = art["metadata"]["lagrum"]
    assert entry["text"] == "8 kap. 2 § inkomstskattelagen (1999:1229)"
    assert entry["sfsnummer"] == "1999:1229"
    assert entry["runs"] == [{"predicate": "rpubl:lagrum",
                              "uri": "https://lagen.nu/1999:1229#K8P2",
                              "text": "8 kap. 2 § inkomstskattelagen (1999:1229)"}]


def test_curated_lagrum_sfsnummer_backstop():
    # a referens the grammar cannot read still links law-level through the
    # source's own sfsNummer (the authoritative identity beside the string)
    record = {**RECORD, "lagrumLista": [
        {"referens": "Mervärdesskattelag", "sfsNummer": "2023:200"}]}
    [entry] = to_artifact(parse_api_record(record))["metadata"]["lagrum"]
    assert entry["runs"] == [{"predicate": "rpubl:lagrum",
                              "uri": "https://lagen.nu/2023:200",
                              "text": "Mervärdesskattelag"}]


def test_curated_related_grammar_grupp_and_retention():
    record = {**RECORD, "hanvisadePubliceringarLista": [
        # the citation grammar reads the fritext
        {"fritext": "NJA 2015 s. 141", "gruppKorrelationsnummer": "g-nja"},
        # unreadable fritext + grupp join -> whole string links to the case
        {"fritext": "Se det opublicerade avgörandet",
         "gruppKorrelationsnummer": "g-odd"},
        # unreadable fritext, no grupp -> retained as plain text, never erased
        {"fritext": "SvJT 1955 rf s. 76"},
    ]}
    art = to_artifact(parse_api_record(record),
                      grupp_uris={"g-odd": "RH 1991:78",
                                  "g-nja": "NJA 2015 s. 141"})
    grammar, grupp, kept = art["metadata"]["related"]
    assert grammar["runs"] == [{"predicate": "rpubl:rattsfallshanvisning",
                                "uri": "https://lagen.nu/dom/nja/2015s141",
                                "text": "NJA 2015 s. 141"}]
    assert grammar["grupp"] == "g-nja"
    # grammar and grupp join agree -> no conflict recorded
    assert "grupp_konflikt" not in grammar
    assert grupp["runs"] == [{"predicate": "rpubl:rattsfallshanvisning",
                              "uri": "https://lagen.nu/dom/rh/1991:78",
                              "text": "Se det opublicerade avgörandet"}]
    assert kept == {"text": "SvJT 1955 rf s. 76",
                    "runs": ["SvJT 1955 rf s. 76"]}


def test_curated_related_conflict_between_grammar_and_grupp_is_recorded():
    # the editor's string resolves to one case, the publication-group join to
    # another: the grammar's link stands, but the disagreement is recorded so
    # the acceptance pass can list exactly the edges that may be wrong
    record = {**RECORD, "hanvisadePubliceringarLista": [
        {"fritext": "NJA 2015 s. 141", "gruppKorrelationsnummer": "g-x"}]}
    art = to_artifact(parse_api_record(record),
                      grupp_uris={"g-x": "RH 1991:78"})
    [entry] = art["metadata"]["related"]
    assert entry["runs"][0]["uri"] == "https://lagen.nu/dom/nja/2015s141"
    assert entry["grupp_konflikt"] == "https://lagen.nu/dom/rh/1991:78"


def test_curated_forarbeten_resolve_and_junk_is_dropped():
    record = {**RECORD, "forarbeteLista": ["Prop. 2019/20:9 s. 70", "-", " "]}
    [entry] = to_artifact(parse_api_record(record))["metadata"]["forarbeten"]
    assert entry["text"] == "Prop. 2019/20:9 s. 70"
    [link] = [r for r in entry["runs"] if isinstance(r, dict)]
    assert link["predicate"] == "rpubl:forarbete"
    assert link["uri"].startswith("https://lagen.nu/prop/2019/20:9")


def test_litteratur_joined_and_kept_as_text():
    record = {**RECORD, "litteraturLista": [
        {"forfattare": "Ekelöf m.fl. Rättegång IV", "titel": "2009, s. 42 ff."}]}
    [entry] = to_artifact(parse_api_record(record))["metadata"]["litteratur"]
    assert entry == {"text": "Ekelöf m.fl. Rättegång IV, 2009, s. 42 ff.",
                     "runs": ["Ekelöf m.fl. Rättegång IV, 2009, s. 42 ff."]}


def test_europarattsliga_labels_kept_as_labels_not_relations():
    # the field carries coarse topic labels ("EU-rätt", "Mänskliga
    # rättigheter"), never citations -- kept as metadata beside rattsomrade,
    # but no relation edge is minted from them
    record = {**RECORD, "europarattsligaAvgorandenLista": ["EU-rätt "]}
    av = parse_api_record(record)
    assert av.europarattslig == ["EU-rätt"]     # stripped
    assert av.related == []
    art = to_artifact(av)
    assert art["metadata"]["europarattslig"] == ["EU-rätt"]
    assert all(run["predicate"] != "rpubl:rattsfallshanvisning"
               for _, run in catalog.curated_links(art))


def test_curated_links_reach_the_catalog_graph():
    record = {**RECORD, "hanvisadePubliceringarLista": [
        {"fritext": "NJA 2015 s. 141"}]}
    art = to_artifact(parse_api_record(record))
    links = catalog.curated_links(art)
    # unanchored typed edges: the curated lagrum + the related case
    assert (None, {"predicate": "rpubl:lagrum",
                   "uri": "https://lagen.nu/1999:1229#K8P2",
                   "text": "8 kap. 2 § inkomstskattelagen (1999:1229)"}) in links
    assert (None, {"predicate": "rpubl:rattsfallshanvisning",
                   "uri": "https://lagen.nu/dom/nja/2015s141",
                   "text": "NJA 2015 s. 141"}) in links
    assert len(links) == 2


def test_to_artifact_non_referat_case_gets_verdict_uri():
    # a raw verdict (no referat) publishes at the old COIN scheme when court,
    # målnummer and date are all known; a fact-less stray keeps the slug URI
    av = Avgorande(court="HDO", court_namn="Högsta domstolen",
                   malnummer=["Ö 528-08"], avgorandedatum="2008-03-13",
                   body=[Stycke("Beslutet.")])
    assert to_artifact(av)["uri"] == "https://lagen.nu/dom/hd/Ö528-08/2008-03-13"
    dateless = Avgorande(court="HDO", court_namn="Högsta domstolen",
                         malnummer=["Ö 528-08"], body=[Stycke("Beslutet.")])
    assert to_artifact(dateless)["uri"] == "https://lagen.nu/dom/HDO_Ö_528_08"


def test_clean_nyckelord_strips_register_junk():
    # trailing continuation dash, list separators, leading continuation dash,
    # pure-punctuation entries; terminal periods are kept (m.m.)
    assert clean_nyckelord(["Allmän handling -", "Avskrivning;", "Avtalsbrott,",
                            "- Återställande av försutten tid - avslag",
                            "--", "  ", "Personlig integritet m.m."]) == [
        "Allmän handling", "Avskrivning", "Avtalsbrott",
        "Återställande av försutten tid - avslag",
        "Personlig integritet m.m."]


# --------------------------------------------------------------------------
# raw-verdict PDF pipeline (R2) -- golden fixture through real pdftohtml
# --------------------------------------------------------------------------

# test/files/dv/verdict_numbered.pdf is a hand-built minimal verdict (see the
# adjacent make_verdict_numbered_pdf.py): a running header at a sub-body font,
# a DOMSKÄL heading, and three domskäl paragraphs each preceded by a tiny
# left-margin *image* -- the paragraph-number bitmap HD prints instead of
# selectable text. It exercises pdf_images/_paragraph_numbers/parse_pdf_record
# against a real poppler run, which the synthetic-Line unit tests cannot.

def _pdf_record():
    return {"domstol": {"domstolKod": "HDO", "domstolNamn": "Högsta domstolen"},
            "malNummerLista": ["Ä 1-24"], "referatNummerLista": [],
            "avgorandedatum": "2024-01-01"}


def test_pdf_images_finds_the_margin_number_bitmaps():
    # the recovery hinges on exactly the small left-margin images being detected:
    # three of them, each passing _paragraph_numbers' size/left filter
    imgs = pdf_images(str(FIXTURES / "verdict_numbered.pdf"))
    margin = [(page, top, left, w, h) for page, top, left, w, h in imgs
              if 0 < h < 30 and 0 < w < 100 and left < 260]
    assert len(margin) == 3
    assert all(page == 1 for page, *_ in margin)


def test_parse_pdf_record_recovers_contiguous_domskal_numbers():
    av = parse_pdf_record(_pdf_record(), FIXTURES / "verdict_numbered.pdf")
    assert isinstance(av, Avgorande)
    numbered = [b for b in av.body if isinstance(b, Stycke) and b.ordinal]
    # the three margin bitmaps become contiguous ordinals 1, 2, 3 on their lines
    assert [b.ordinal for b in numbered] == ["1", "2", "3"]
    assert numbered[0].text.startswith("Fragan")
    # every recovered block is page-tagged for the facsimile link
    assert all(b.page == 1 for b in numbered)
    # the DOMSKÄL heading survives as an unnumbered rubrik
    assert any(isinstance(b, Rubrik) and b.text == "DOMSKAL" for b in av.body)
    # the sub-body running header is dropped as marginalia, never a body line
    body_text = " ".join(b.text for b in av.body)
    assert "Mal nr" not in body_text
