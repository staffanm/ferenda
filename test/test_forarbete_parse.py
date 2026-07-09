"""Tests for the förarbete PDF parser's font-aware blocks logic (PDF-free)."""

from accommodanda.forarbete.parse import classify, mint_uri
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
