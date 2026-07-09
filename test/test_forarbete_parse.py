"""Tests for the förarbete PDF parser's font-aware blocks logic (PDF-free)."""

from accommodanda.forarbete.model import Block
from accommodanda.forarbete.parse import (
    classify,
    mint_uri,
    rskr_body,
    tag_frontmatter,
)
from accommodanda.forarbete.structure import ingress, nest, signers
from accommodanda.lib.pdftext import Line, Para, line_body_size, page_paragraphs


def test_mint_uri_matches_citation_form():
    assert mint_uri("prop", "2025/26:161") == "https://lagen.nu/prop/2025/26:161"
    assert mint_uri("sou", "2020:1") == "https://lagen.nu/sou/2020:1"
    assert mint_uri("dir", "2020:1") == "https://lagen.nu/dir/2020:1"


def test_classify_recovers_chapter_and_paragraf_markers():
    # a bold "N kap."/"N §" lead (lead_bold) is structure; the same token in
    # regular text is a cross-reference, not a marker
    paras = [Para("1 kap. Inledande bestämmelser", bold=True, lead_bold=True),
             Para("3 § Verksamhetsutövare ska vidta åtgärder.", lead_bold=True),
             Para("Paragrafen genomför artikel 21.")]
    assert [(b.kind, b.num) for b in classify(paras, 243)] == [
        ("kapitel", "1"), ("paragraf", "3"), ("stycke", None)]


def test_classify_cross_reference_is_not_a_marker():
    # "7 §" in regular (non-bold) prose must stay body text
    assert classify([Para("som anges i 7 § ska anmälan göras")], 1)[0].kind \
        == "stycke"


def test_classify_numbered_and_unnumbered_headings():
    paras = [Para("15 Författningskommentar", bold=True, lead_bold=True),
             Para("4.3.2 Inledande granskning", bold=True, lead_bold=True),
             Para("Säkerhetsåtgärder", bold=True, lead_bold=True),
             Para("En vanlig brödtext här.")]
    assert [(b.kind, b.level) for b in classify(paras, 5)] == [
        ("rubrik", 1), ("rubrik", 3), ("rubrik", 3), ("stycke", None)]


def _frontmatter():
    # the page-1 överlämnande run of prop 2020/21:194, as the classifier reads
    # it (nothing on the page is bold, so every block arrives as a stycke)
    return [Block("stycke", "Regeringens proposition 2020/21:194", 1),
            Block("stycke", "Ett starkare skydd för Sveriges säkerhet", 1),
            Block("stycke", "Regeringen överlämnar denna proposition till "
                            "riksdagen.", 1),
            Block("stycke", "Stockholm den 20 maj 2021", 1),
            Block("stycke", "Stefan Löfven", 1),
            Block("stycke", "Mikael Damberg (Justitiedepartementet)", 1),
            Block("stycke", "Propositionens huvudsakliga innehåll", 1),
            Block("stycke", "För att stärka skyddet för Sveriges säkerhet "
                            "föreslår regeringen ändringar.", 1),
            Block("rubrik", "1 Förslag till riksdagsbeslut", 3, level=1)]


def test_tag_frontmatter_signers_and_ingress_heading():
    blocks = tag_frontmatter(_frontmatter())
    assert [b.kind for b in blocks] == [
        "stycke", "stycke", "stycke", "stycke", "signatur", "signatur",
        "rubrik", "stycke", "rubrik"]
    # the promoted heading nests the ingress into its own avsnitt
    assert blocks[6].level == 1


def test_tag_frontmatter_needs_the_handover_sentence():
    # an old riksdagen-format prop without the modern överlämnande: no signer
    # tagging (the heading promotion is independent and still applies)
    blocks = [b for b in _frontmatter()
              if not b.text.startswith("Regeringen överlämnar")]
    assert [b.kind for b in tag_frontmatter(blocks)] == [
        "stycke", "stycke", "stycke", "stycke", "stycke",
        "rubrik", "stycke", "rubrik"]


def test_tag_frontmatter_stops_at_first_rubrik():
    # an ort/datum line in the body proper ("Stockholm den 1 januari 2021"
    # quoted in a section) must not trigger signer tagging
    blocks = [Block("rubrik", "3 Ärendet och dess beredning", 9, level=1),
              Block("stycke", "Stockholm den 1 januari 2021", 9),
              Block("stycke", "Anna Andersson", 9)]
    assert [b.kind for b in tag_frontmatter(_frontmatter() + blocks)][-2:] == \
        ["stycke", "stycke"]


RSKR_MODERN = """
<div class="Section1">
  <h1><span>Riksdagsskrivelse</span><br><span>2025/26:429</span></h1>
  <p class="Mottagare1"><span>Regeringen</span></p>
  <p class="Mottagare2"><span>Utbildningsdepartementet</span></p>
  <p><span>Med överlämnande av utbildningsutskottets betänkande 2025/26:UbU31
  får jag anmäla att riksdagen denna dag bifallit utskottets förslag till
  riksdagsbeslut.</span></p>
  <p class="Stockholm"><span>Stockholm den 17 juni 2026</span></p>
  <p class="AvsTalman"><span>Andreas Norlén</span></p>
  <p class="AvsTjnsteman"><span>Kristina Svartz</span></p>
</div>"""

RSKR_OLD = """<h2>Nr 361</h2>
<p>Tilläggsstat I till riksstaten för budgetåret 1971/72</p>
<p>Till Konungen</p>
<p>Med överlämnande av nämnda betänkande får jag anmäla att</br>riksdagen har
bifallit vad utskottet hemställt.</p>
<p>Stockholm den 17 december 1971</p>
<p>HENRY ALLARD</p>"""


def test_rskr_body_tags_signers_after_ort_datum():
    blocks = rskr_body(RSKR_MODERN)
    assert [(b.kind, b.text) for b in blocks if b.kind == "signatur"] == [
        ("signatur", "Andreas Norlén"), ("signatur", "Kristina Svartz")]
    # the boilerplate before the ort/datum line stays stycke
    assert blocks[0].kind == "stycke"
    assert blocks[0].text == "Riksdagsskrivelse 2025/26:429"


def test_rskr_body_handles_the_pre_2000s_layout():
    assert [b.text for b in rskr_body(RSKR_OLD) if b.kind == "signatur"] == \
        ["HENRY ALLARD"]


def test_structure_signers_and_ingress():
    # the artifact-side readers the history-as-git export consumes (via
    # build's _forarbete_meta): signatur blocks and the promoted ingress
    # avsnitt, both as plain text
    structure = nest([
        {"type": "stycke", "text": ["Regeringens proposition 2020/21:194"]},
        {"type": "signatur", "text": ["Stefan Löfven"]},
        {"type": "signatur", "text": ["Mikael Damberg (Justitiedepartementet)"]},
        {"type": "rubrik", "level": 1,
         "text": ["Propositionens huvudsakliga innehåll"]},
        {"type": "stycke", "text": ["För att stärka skyddet föreslår ",
                                    {"uri": "x", "text": "regeringen"},
                                    " ändringar."]},
        {"type": "rubrik", "level": 1, "text": ["1 Förslag till riksdagsbeslut"]},
        {"type": "stycke", "text": ["Härigenom föreskrivs."]}])
    assert signers(structure) == ["Stefan Löfven", "Mikael Damberg"]
    assert ingress(structure) == ("För att stärka skyddet föreslår regeringen "
                                  "ändringar.")


def _line(text, top, bold=False, lead_bold=False, italic=False):
    return Line(text, top, bold, lead_bold, italic)


def test_page_paragraphs_strips_header_pagenumber_and_reflows():
    lines = [_line("Prop. 2025/26:161", 40),                 # running header
             _line("en mening som bryts mitt i ett ord för-", 70),
             _line("fogar över resten.", 90),                 # body gap 20
             _line("och fortsätter på nästa rad.", 110),      # body gap 20
             _line("Ett nytt stycke börjar längre ned.", 180),  # big gap -> break
             _line("42", 800)]                                 # page number
    paras = page_paragraphs(lines, "Prop. 2025/26:161", 42)
    assert [p.text for p in paras] == [
        "en mening som bryts mitt i ett ord förfogar över resten. "
        "och fortsätter på nästa rad.",
        "Ett nytt stycke börjar längre ned."]


def test_page_paragraphs_breaks_at_bold_marker():
    # a bold §-marker line starts its own paragraph even without a gap
    lines = [_line("slutet på föregående paragrafs kommentar.", 70),
             _line("3 § Verksamhetsutövare ska vidta åtgärder.", 92, lead_bold=True)]
    paras = page_paragraphs(lines, "Prop. X", 5)
    assert [(p.text, p.lead_bold) for p in paras] == [
        ("slutet på föregående paragrafs kommentar.", False),
        ("3 § Verksamhetsutövare ska vidta åtgärder.", True)]


def test_page_paragraphs_skips_table_of_contents():
    toc = [_line("%d Avsnitt ........................ %d" % (i, i + 10), 50 + i * 20)
           for i in range(1, 8)]
    assert page_paragraphs(toc, "Prop. 2025/26:161", 3) == []


def test_parse_record_patch_key_is_typ_qualified_slug(monkeypatch, tmp_path):
    # the patch key must be the build-style basefile ("sou/2021-82"): the
    # record's own basefile ("2021:82") has no typ segment, which crashed
    # layout.relpath for every SOU (and silently computed a wrong patch path
    # for props, whose riksmöte slash made the split "succeed")
    from accommodanda.forarbete import parse as fa_parse

    seen = {}

    def fake_parse_pdf(path, identifier, patch_key=None):
        seen["patch_key"] = patch_key
        return []

    monkeypatch.setattr(fa_parse, "parse_pdf", fake_parse_pdf)
    (tmp_path / "sou").mkdir()
    (tmp_path / "sou" / "2021-82.pdf").write_bytes(b"%PDF-")
    fa_parse.parse_record({"type": "sou", "basefile": "2021:82",
                           "identifier": "SOU 2021:82",
                           "files": ["2021-82.pdf"]}, tmp_path)
    assert seen["patch_key"] == ("forarbete", "sou/2021-82")


def test_classify_font_size_gates_footnotes_and_fake_headings():
    # prop 2013/14:116: the lagtext provenance footnotes ("1 Senaste lydelse
    # 2008:1266.") and body-sized table rows ("22 år 25 000 …") match the
    # numbered-heading pattern but are not headings; a real (large, unbold)
    # numbered chapter heading is
    paras = [Para("1 Förslag till riksdagsbeslut", size=23),
             Para("22 år 25 000 28 873 27 553 -1 320", size=15),
             Para("1 Senaste lydelse 2008:1266. 2 Senaste lydelse 2008:1266.",
                  size=12),
             Para("En vanlig brödtext här.", size=15)]
    assert [b.kind for b in classify(paras, 4, body=15)] == [
        "rubrik", "stycke", "fotnot", "stycke"]


def test_classify_sizeless_paras_keep_permissive_rules():
    # OCR/legacy routes emit no font sizes; numbered lines stay headings
    assert classify([Para("7 Konsekvensanalys")], 1, body=0)[0].kind == "rubrik"


def test_body_size_is_mode_of_sized_paras():
    assert line_body_size([Para("a", size=15), Para("b", size=15),
                           Para("c", size=23), Para("d")]) == 15
    assert line_body_size([Para("a"), Para("b")]) == 0
