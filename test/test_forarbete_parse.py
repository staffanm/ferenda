"""Tests for the förarbete PDF parser's font-aware blocks logic (PDF-free)."""

from pathlib import Path

import pytest

from accommodanda.forarbete import parse as fa_parse
from accommodanda.forarbete.model import Block
from accommodanda.forarbete.parse import (
    classify,
    mint_uri,
    rskr_body,
    tag_frontmatter,
)
from accommodanda.forarbete.structure import ingress, nest, signers
from accommodanda.lib import compress, layout
from accommodanda.lib.pdftext import Line, Para, line_body_size, page_paragraphs


def _stage(tmp_path, typ, basefile, name, data):
    """Write a body fixture to the year-segmented download slot a förarbete
    record resolves (``<root>/<typ>/<year>/<name>``), so these tests track the
    real layout rule rather than a hand-built flat path."""
    dest = layout.fa_dir(tmp_path, typ, basefile) / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    compress.write_download(dest, data)
    return dest

FIXTURES = Path(__file__).parent / "files" / "forarbete-legacy"


def _harvested_rec(**kw):
    rec = {"type": "prop", "basefile": "1999/2000:1",
           "identifier": "Prop. 1999/2000:1"}
    rec.update(kw)
    return rec


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

    # a born-digital PDF's yield: non-empty, so `_legacy_pdf_body` keeps these
    # blocks instead of reading the stub as a textless scan and falling through
    # to the pdftotext OCR route (which would shell out on a 5-byte fake PDF)
    def fake_parse_pdf(path, identifier, patch_key=None):
        seen["patch_key"] = patch_key
        return [Block("stycke", "Regeringens proposition", 1)]

    monkeypatch.setattr(fa_parse, "parse_pdf", fake_parse_pdf)
    _stage(tmp_path, "sou", "2021:82", "2021-82.pdf", b"%PDF-")
    fa_parse.parse_record({"type": "sou", "basefile": "2021:82",
                           "identifier": "SOU 2021:82",
                           "files": ["2021-82.pdf"]}, tmp_path)
    assert seen["patch_key"] == ("forarbete", "sou/2021-82")


# --- _harvested_body routing (the propkb facsimile design leans on these) ---

def test_harvested_body_routes_xml_to_abbyy(tmp_path):
    """A record whose only body is an ABBYY .xml routes to the page-anchored
    abbyy parser. This is the invariant propkb depends on -- the xml stays the
    body -- so lock the branch against a reorder that would silently re-route
    17k KB props (rule:lock-in-with-fixture)."""
    _stage(tmp_path, "prop", "1999/2000:1", "1999-2000-1.xml",
           (FIXTURES / "abbyy_propkb.xml").read_bytes())
    doc = fa_parse.parse_record(_harvested_rec(files=["1999-2000-1.xml"]), tmp_path)
    assert doc.body                                  # real ABBYY text came through
    assert all(b.page is not None for b in doc.body)  # abbyy route is page-anchored


def test_harvested_body_prefers_pdf_over_xml(tmp_path, monkeypatch):
    """When a record lists both a .pdf and a .xml, the PDF wins. This is exactly
    why propkb keeps its facsimile scan OUT of `files`: listing it would flip an
    ABBYY-bodied doc onto a pdftotext of the scan. Lock the precedence."""
    seen = {}

    def fake_pdf(path, identifier, patch_key=None):
        seen["path"] = str(path)
        return [Block("stycke", "from pdf", 1)], False

    monkeypatch.setattr(fa_parse, "_legacy_pdf_body", fake_pdf)
    _stage(tmp_path, "prop", "1999/2000:1", "1999-2000-1.pdf", b"%PDF-1.4 x")
    _stage(tmp_path, "prop", "1999/2000:1", "1999-2000-1.xml", b"<document/>")
    fa_parse.parse_record(
        _harvested_rec(files=["1999-2000-1.pdf", "1999-2000-1.xml"]), tmp_path)
    assert seen["path"].endswith("prop/1999/1999-2000-1.pdf")   # pdf branch, not xml


def test_harvested_body_ocr_sidecar_wins_and_carries_patch_key(tmp_path, monkeypatch):
    """A re-OCR sidecar at `fa_ocr_pdf` is parsed instead of the record's own
    listed body (the prod ocrmypdf upgrade path), and it must carry the
    document's patch key -- keying only the `files` branch would silently unpatch
    every re-OCR'd doc."""
    sidecar = tmp_path / "ocr" / "1999-2000-1.pdf"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_bytes(b"%PDF-1.4 ocr")
    monkeypatch.setattr(fa_parse.layout, "fa_ocr_pdf", lambda typ, bf: sidecar)
    seen = {}

    def fake_pdf(path, identifier, patch_key=None):
        seen["path"], seen["patch_key"] = str(path), patch_key
        return [Block("stycke", "x", 1)], False

    monkeypatch.setattr(fa_parse, "_legacy_pdf_body", fake_pdf)
    _stage(tmp_path, "prop", "1999/2000:1", "1999-2000-1.pdf", b"%PDF-1.4 body")
    fa_parse.parse_record(_harvested_rec(files=["1999-2000-1.pdf"]), tmp_path)
    assert seen["path"] == str(sidecar)              # sidecar parsed, not the pdf
    assert seen["patch_key"] == ("forarbete", "prop/1999-2000-1")


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


# --- OCR chronology sanity check (rewrite-parity finding 05) -----------------

def _ocr_fa(basefile, text, ocr=True):
    return fa_parse.Forarbete(
        type="prop", basefile=basefile, identifier="Prop. " + basefile,
        uri=mint_uri("prop", basefile), title="t", ocr=ocr,
        body=[Block("stycke", text, 12)])


def test_ocr_future_citation_demoted_and_reported():
    # a 1971 prop whose OCR garbled '1934:437' into '1984:437': the impossible
    # link is not minted -- the text stays verbatim -- and the suspect is
    # reported; a chronologically possible citation in the same sentence links
    art = fa_parse.to_artifact(_ocr_fa(
        "1971:10", "Enligt lagen (1984:437) och lagen (1962:700) gäller."))
    runs = [r for b in art["structure"] for r in b["text"]]
    uris = [r["uri"] for r in runs if isinstance(r, dict)]
    assert uris == ["https://lagen.nu/1962:700"]
    assert "lagen (1984:437)" in "".join(
        r if isinstance(r, str) else r["text"] for r in runs)
    assert art["suspect_citations"] == [
        {"text": "1984:437", "uri": "https://lagen.nu/1984:437", "page": 12}]


def test_ocr_chronology_tolerates_the_following_year():
    # a riksmöte document legitimately cites legislation enacted the next
    # calendar year (prop 1975/76 -> SFS 1976); only year + 2 is impossible
    art = fa_parse.to_artifact(_ocr_fa(
        "1975/76:100", "Se lagen (1976:100) och lagen (1977:200)."))
    uris = [r["uri"] for b in art["structure"] for r in b["text"]
            if isinstance(r, dict)]
    assert uris == ["https://lagen.nu/1976:100"]
    assert [s["uri"] for s in art["suspect_citations"]] == [
        "https://lagen.nu/1977:200"]


def test_born_digital_body_is_never_censored():
    # the check gates on the OCR route: a born-digital body keeps every link
    # (a real future citation there is an authoring fact, not a scan error)
    art = fa_parse.to_artifact(_ocr_fa(
        "1971:10", "Enligt lagen (1984:437) gäller.", ocr=False))
    uris = [r["uri"] for b in art["structure"] for r in b["text"]
            if isinstance(r, dict)]
    assert uris == ["https://lagen.nu/1984:437"]
    assert "suspect_citations" not in art


# --- truncated "lag om ändring i" rubriks (rewrite-parity finding 04) --------

def test_dangling_rubrik_joins_next_statute_line():
    # prop 1993/94:38: "12.2 Förslaget till lag om ändring i" + "sekretesslagen"
    body = [Block("rubrik", "12.2 Förslaget till lag om ändring i", 71, level=2),
            Block("stycke", "sekretesslagen", 71),
            Block("stycke", "Genom en ändring i 9 kap. 8 § första stycket "
                  "införs absolut sekretess.", 71)]
    out = fa_parse.join_dangling_rubriks(body)
    assert [b.text for b in out] == [
        "12.2 Förslaget till lag om ändring i sekretesslagen",
        "Genom en ändring i 9 kap. 8 § första stycket införs absolut sekretess."]


def test_dangling_rubrik_strips_toc_leader_from_continuation():
    # prop 1992/93:69's innehållsförteckning shape: the statute name carries a
    # dotted leader + page number
    body = [Block("rubrik", "4.1 Förslag till lag om ändring i", 2, level=2),
            Block("stycke",
                  "föreningsbankslagen (1987:620)........................ 26", 2)]
    out = fa_parse.join_dangling_rubriks(body)
    assert [b.text for b in out] == [
        "4.1 Förslag till lag om ändring i föreningsbankslagen (1987:620)"]


def test_dangling_rubrik_joins_across_bilaga_margin_marker():
    # prop 2000/01:129: a "Bilaga 2" margin marker sits between the rubrik and
    # its continuation -- the marker stays in place, the statute name joins
    body = [Block("rubrik", "6 Förslag till lag om ändring i", 110, level=1),
            Block("stycke", "Bilaga 2", 110),
            Block("stycke", "socialförsäkringsregisterlagen (1997:934)", 110)]
    out = fa_parse.join_dangling_rubriks(body)
    assert [b.text for b in out] == [
        "6 Förslag till lag om ändring i socialförsäkringsregisterlagen "
        "(1997:934)",
        "Bilaga 2"]


def test_dangling_rubrik_leaves_an_uppercase_body_stycke_alone():
    # a following real paragraph (uppercase-led) is NOT a continuation
    body = [Block("rubrik", "12.3 Förslaget till lag om ändring i", 70, level=2),
            Block("stycke", "Ändringen i 9 kap. 2 § är föranledd av "
                  "Postverkets bolagisering.", 70)]
    out = fa_parse.join_dangling_rubriks(body)
    assert [b.text for b in out] == [
        "12.3 Förslaget till lag om ändring i",
        "Ändringen i 9 kap. 2 § är föranledd av Postverkets bolagisering."]


def test_dangling_rubrik_joins_a_misclassified_rubrik_continuation():
    # 1979/80:85: the statute name survived classification as its own rubrik;
    # 1963:52 the same in the era's all-caps heading style
    body = [Block("rubrik", "1 Förslag till Lag om ändring i", 3, level=1),
            Block("rubrik", "rättegångsbalken", 3, level=1),
            Block("rubrik", "FÖRSLAGET TILL LAG OM ÄNDRING I", 9, level=1),
            Block("rubrik", "UTSÖKNINGSLAGEN", 9, level=1)]
    out = fa_parse.join_dangling_rubriks(body)
    assert [b.text for b in out] == [
        "1 Förslag till Lag om ändring i rättegångsbalken",
        "FÖRSLAGET TILL LAG OM ÄNDRING I UTSÖKNINGSLAGEN"]


def test_dangling_rubrik_splits_a_glued_continuation():
    # 1993/94:71: reflow glued the statute name onto the next paragraph
    body = [Block("rubrik", "7.2 Förslaget till lag om ändring i", 40, level=2),
            Block("stycke", "trafikskadelagen (1975:1410)14 § Från ett fordons "
                  "trafikförsäkring avräknas.", 40)]
    out = fa_parse.join_dangling_rubriks(body)
    assert [b.text for b in out] == [
        "7.2 Förslaget till lag om ändring i trafikskadelagen (1975:1410)",
        "14 § Från ett fordons trafikförsäkring avräknas."]


# --- printed-page offsets (rewrite-parity finding 04) ------------------------

def test_printed_pageno_reads_folio_and_strips_header():
    lines = [Line("Prop. 2003/04:154 7", 40, False, False, False, 9, []),
             Line("Body text here.", 100, False, False, False, 11, [])]
    assert fa_parse.printed_pageno(lines, "Prop. 2003/04:154") == 7
    assert fa_parse.printed_pageno(lines, None) is None       # no header known


def test_page_offset_constant_majority_wins():
    # SOU 1989:67's shape: printed 1 starts on PDF page 4, with stray bare
    # numbers (a year in a margin) tolerated as a minority
    detections = {pdf: pdf - 3 for pdf in range(4, 90)} | {17: 1920}
    assert fa_parse.page_offset(detections) == -3
    assert fa_parse.page_offset({}) == 0                      # no evidence
    assert fa_parse.page_offset({5: 5, 9: 9}) == 0            # too sparse


def test_page_offset_ambiguous_mapping_raises():
    detections = {pdf: pdf for pdf in range(1, 10)} \
        | {pdf: pdf - 8 for pdf in range(10, 16)}
    with pytest.raises(ValueError, match="ambiguous printed-page mapping"):
        fa_parse.page_offset(detections)


# --- generic tables (rewrite-parity finding 04) ------------------------------

def _row_line(top, cells, bold=False):
    from accommodanda.lib.pdftext import Run
    runs = [Run(left, left + 90, text, bold, False, 11)
            for left, text in cells]
    return Line(" ".join(c[1] for c in cells), top, bold, bold, False, 11, runs)


def test_split_generic_detects_aligned_columns():
    from accommodanda.forarbete import tabell
    lines = [
        Line("En vanlig prosarad utan kolumner.", 90, False, False, False, 11,
             [__import__('accommodanda.lib.pdftext', fromlist=['Run']).Run(
                 100, 400, "En vanlig prosarad utan kolumner.", False, False, 11)]),
        _row_line(120, [(100, "Ålder"), (300, "Belopp")], bold=True),
        _row_line(140, [(100, "22 år"), (300, "25 000")]),
        _row_line(160, [(100, "23 år"), (300, "27 500")]),
        _row_line(180, [(100, "24 år"), (300, "30 000")]),
    ]
    segs = tabell.split_generic(lines)
    assert [s[0] for s in segs] == ["lines", "tabell"]
    kind, th, rows = segs[1]
    assert th is True
    assert rows == [("Ålder", "Belopp"), ("22 år", "25 000"),
                    ("23 år", "27 500"), ("24 år", "30 000")]


def test_split_generic_wrapped_cell_merges_into_previous_row():
    from accommodanda.forarbete import tabell
    lines = [
        _row_line(120, [(100, "Myndighet"), (300, "Anslag")]),
        _row_line(140, [(100, "Riksrevisionen"), (300, "12 000")]),
        _row_line(155, [(300, "varav engångsbelopp 2 000")]),   # wrapped cell
        _row_line(180, [(100, "Domstolsverket"), (300, "8 000")]),
    ]
    [(kind, th, rows)] = tabell.split_generic(lines)
    assert rows == [
        ("Myndighet", "Anslag"),
        ("Riksrevisionen", "12 000 varav engångsbelopp 2 000"),
        ("Domstolsverket", "8 000")]


def test_split_generic_leaves_prose_and_toc_alone():
    from accommodanda.forarbete import tabell
    from accommodanda.lib.pdftext import Run
    prose = [Line("Text %d." % i, 100 + 20*i, False, False, False, 11,
                  [Run(100, 400, "Text %d." % i, False, False, 11)])
             for i in range(5)]
    toc = [_row_line(300 + 20*i, [(100, "4.%d Rubrik....... " % i), (400, "%d" % i)])
           for i in range(4)]
    for l in toc:
        l.text += "......."      # dotted leader marks a TOC line
    segs = tabell.split_generic(prose + toc)
    assert [s[0] for s in segs] == ["lines"]


def test_merge_continued_joins_cross_page_table_and_drops_repeated_header():
    from accommodanda.forarbete import tabell
    a = Block("tabell", "", 14, rows=[("Ålder", "Belopp"), ("22 år", "25 000")],
              th=True)
    b = Block("tabell", "", 15, rows=[("Ålder", "Belopp"), ("23 år", "27 500")],
              th=True)
    c = Block("stycke", "Efterföljande text.", 15)
    merged = tabell.merge_continued([a, b, c])
    assert len(merged) == 2
    assert merged[0].rows == [("Ålder", "Belopp"), ("22 år", "25 000"),
                              ("23 år", "27 500")]
