"""sfs/coverage.py: mechanically reconstructing a missing archived
consolidation from an amendment's own published PDF -- gap detection over
the authoritative andringsforfattningar chain, the plain-replacement triage,
the layout-text reading of the PDF, and the text splice itself."""

from pathlib import Path

import pytest

from ferenda.lib import compress, layout, pdftext
from ferenda.sfs import coverage

# a base consolidation shaped like the beta-API's own JSON, trimmed to just
# what coverage.py reads
BASE_SOURCE = {
    "beteckning": "1998:204",
    "rubrik": "Lag (1998:204) om test",
    "fulltext": {
        "andringInford": "t.o.m. SFS 2020:100",
        "forfattningstext": (
            "1 kap. Inledande bestämmelser\r\n\r\n"
            "1 § Gammal lydelse av första paragrafen.\r\n"
            "Lag (2015:50).\r\n\r\n"
            "2 § Andra paragrafen, opåverkad.\r\n"
            "Lag (2010:20).\r\n\r\n"
            "2 kap. Andra kapitlet\r\n\r\n"
            "1 § Kapitel två, paragraf ett.\r\n"
            "Lag (2010:20).\r\n"
        ),
    },
}


def _write_base(tmp_path, monkeypatch, basefile="1998:204", version="2020:100"):
    monkeypatch.setattr(layout, "SFS_DOWNLOADED", tmp_path / "downloaded")
    path = layout.sfs_archive_version_download(layout.SFS_DOWNLOADED, basefile, version)
    compress.write_json(path, BASE_SOURCE)
    return path


def _line(spec):
    """One `pdftext.Line` from a fixture row: "top left size flags text",
    flags "B" (whole line bold), "b" (leading run bold) or "-"; a further
    run on the same baseline follows a " | " as "left text"."""
    top, left, size, flags, rest = spec.split(None, 4)
    runs = []
    for i, part in enumerate(rest.split(" | ")):
        run_left, text = (int(left), part) if i == 0 else part.split(" ", 1)
        run_left = int(run_left)
        runs.append(pdftext.Run(run_left, run_left + 7 * len(text), text,
                                bold=flags == "B" or (flags == "b" and i == 0),
                                italic=False, size=int(size)))
    return pdftext.Line(" ".join(r.text for r in runs), int(top), bold=flags == "B",
                        lead_bold=flags in ("B", "b"), italic=False, size=int(size),
                        runs=runs, bottom=int(top) + int(size))


def _pages(text):
    """`[(pageno, [Line])]` from a fixture: one row per line, "----" between
    pages."""
    return [(pageno, [_line(row) for row in page.strip().splitlines() if row.strip()])
            for pageno, page in enumerate(text.split("----"), 1)]


def _pdf(monkeypatch, text):
    """Stand in for the PDF: `parse_amendment_pdf` reads it through
    `pdftext.pdf_pages`."""
    monkeypatch.setattr(coverage.pdftext, "pdf_pages", lambda p: _pages(text))


# --- parse_omfattning / is_simple_omfattning ---------------------------------

def _changes(text):
    c = coverage.parse_omfattning(text)
    return (c.replaced, c.added, c.repealed, c.headings_changed, c.headings_added,
            c.headings_removed, c.title, c.unsupported)


def test_parse_omfattning_reads_each_kind_of_change():
    assert _changes("upph. 4 kap. 3 §; ändr. 4 kap. 2 §, rubr. närmast före 4 kap. 2 §; "
                    "ny 5 a §, rubr. närmast före 5 a §") == (
        [("4", "2")], [(None, "5 a")], [("4", "3")], [("4", "2")], [(None, "5 a")], [], False, [])


def test_parse_omfattning_switches_kind_inside_a_clause():
    assert _changes("ändr. författningsrubr., 1-4, nya 12-15 §§, rubr. närmast före 4, 5 §§") == (
        [(None, "1"), (None, "2"), (None, "3"), (None, "4")],
        [(None, "12"), (None, "13"), (None, "14"), (None, "15")], [],
        [], [(None, "4"), (None, "5")], [], True, [])


def test_parse_omfattning_reads_a_heading_removal_and_a_bare_chapter():
    assert _changes("rubr. närmast före 5 § utgår") == ([], [], [], [], [], [(None, "5")], False, [])
    assert _changes("upph. 4 kap.")[7] == ["upph 4 kap."]


def test_is_simple_omfattning_rejects_what_the_module_does_not_write():
    assert coverage.is_simple_omfattning("ändr. 1, 3 §§; ny 1 a §; upph. 2 §")
    assert not coverage.is_simple_omfattning("nuvarande 3 § betecknas 4 §; ändr. den nya 4 §")
    assert not coverage.is_simple_omfattning("ändr. bil.")
    assert not coverage.is_simple_omfattning("ändr. 15 p anvisn. till 22 §")
    assert not coverage.is_simple_omfattning("nya 17 §, rubr. närmast efter 16 §")


# --- effective_date -----------------------------------------------------------

def test_effective_date_reads_the_commencement_sentence():
    assert coverage.effective_date(["Denna lag träder i kraft den 1 juli 2020."], "", "2020:1") == "2020-07-01"
    assert coverage.effective_date([], "1 § ska upphöra att gälla vid utgången av den 7 november 2024.", "2024:1") == "2024-11-08"
    assert coverage.effective_date([], "1 § ska upphöra att gälla vid utgången av 2011.", "2012:1") == "2012-01-01"
    assert coverage.effective_date([], "föreskrivs att 1 § ska ha följande lydelse.", "2020:1") is None


def test_effective_date_refuses_a_future_undetermined_or_unreadable_date():
    with pytest.raises(coverage.NotSimple, match="still in the future"):
        coverage.effective_date(["Denna lag träder i kraft den 1 juli 2099."], "", "2099:1")
    with pytest.raises(coverage.NotSimple, match="Government sets"):
        coverage.effective_date(["Denna lag träder i kraft den dag regeringen bestämmer."], "", "2020:1")
    with pytest.raises(coverage.NotSimple, match="no date this module reads"):
        coverage.effective_date(["Denna lag träder i kraft vid utgången av 2026."], "", "2026:1")


# --- locate_provision --------------------------------------------------------

def test_locate_provision_finds_marker_at_the_exact_line_start():
    text = BASE_SOURCE["fulltext"]["forfattningstext"]
    start, end = coverage.locate_provision(text, "1", "1")
    assert text[start:end].startswith("1 § Gammal lydelse")
    assert text[start:end].rstrip().endswith("Lag (2015:50).")


def test_locate_provision_preserves_a_genuine_blank_line_before_the_marker():
    text = ("1 kap. Rubrik\r\n\r\n1 § Text.\r\nLag (2015:50).\r\n\r\n"
           "2 § Mer text.\r\n")
    start, _end = coverage.locate_provision(text, "1", "2")
    assert text[start:].startswith("2 § Mer text")
    assert text[:start].endswith("\r\n\r\n")


def test_locate_provision_scopes_to_the_named_chapter():
    text = BASE_SOURCE["fulltext"]["forfattningstext"]
    start, end = coverage.locate_provision(text, "2", "1")
    assert "Kapitel två" in text[start:end]


def test_locate_provision_refuses_a_pending_variant():
    text = ("1 § /Upphör att gälla U:2029-01-01/\r\n"
           "Gammal text.\r\nLag (2020:1).\r\n\r\n"
           "1 § /Träder i kraft I:2029-01-01/\r\n"
           "Ny text.\r\nLag (2029:1).\r\n")
    with pytest.raises(coverage.NotSimple, match="pending"):
        coverage.locate_provision(text, None, "1")


def test_locate_provision_refuses_an_unknown_provision():
    with pytest.raises(coverage.NotSimple, match="no 99 § marker"):
        coverage.locate_provision(BASE_SOURCE["fulltext"]["forfattningstext"],
                                  None, "99")


# --- _parse_pinpoints --------------------------------------------------------

def test_parse_pinpoints_each_with_its_own_kap_and_mark():
    assert coverage._parse_pinpoints("8 a kap. 1 § och 12 kap. 9 §") == [
        ("8 a", "1"), ("12", "9")]


def test_parse_pinpoints_bare_single_provision():
    assert coverage._parse_pinpoints("1 § lagen (1998:204)") == [(None, "1")]


def test_parse_pinpoints_shared_trailing_mark_no_chapter():
    # the dominant real-corpus shape: a comma/"och" run of bare numbers
    # sharing one trailing "§§" (SFS 2026:1248, krigsmateriellagen)
    assert coverage._parse_pinpoints("1, 3, 4, 12, 26, 32 och 33 §§ lagen") == [
        (None, "1"), (None, "3"), (None, "4"), (None, "12"),
        (None, "26"), (None, "32"), (None, "33")]


def test_parse_pinpoints_shared_trailing_mark_one_chapter():
    assert coverage._parse_pinpoints("5 kap. 3, 3 a, 3 b §§") == [
        ("5", "3"), ("5", "3 a"), ("5", "3 b")]


def test_parse_pinpoints_en_dash_range():
    assert coverage._parse_pinpoints("11–14 §§") == [
        (None, "11"), (None, "12"), (None, "13"), (None, "14")]


def test_parse_pinpoints_en_dash_range_with_chapter():
    assert coverage._parse_pinpoints("3 kap. 1–3 §§") == [
        ("3", "1"), ("3", "2"), ("3", "3")]


def test_parse_pinpoints_en_dash_range_mixed_with_a_list():
    assert coverage._parse_pinpoints("1, 3–5 och 8 §§") == [
        (None, "1"), (None, "3"), (None, "4"), (None, "5"), (None, "8")]


def test_parse_pinpoints_spaces_a_lettered_ordinal():
    assert coverage._parse_pinpoints("2a § lagen") == [(None, "2 a")]


# --- parse_amendment_pdf (pdf_pages mocked) ---------------------------------

# the 2018- print, as `pdf_pages` reads it: body size 17, footnotes 14 with
# their labels at 9, superscript references at 11 on the body line's own
# baseline, the running header a run of its own right of the title line,
# each further stycke indented 17 px, the page number alone at the right
_AMENDMENT_PDF_TEXT = """
155 98 29 B Svensk författningssamling
221 98 20 B Lag | 600 SFS 2020:200
246 98 21 B om ändring i lagen (1998:204) om test
247 649 15 - Publicerad
263 649 15 - den 1 juni 2020
284 98 17 - Utfärdad den 1 juni 2020
325 266 11 - 1
325 98 17 - Enligt riksdagens beslut föreskrivs att 1 § ska ha följande
346 98 17 - lydelse.
407 119 11 - 2
407 98 17 b 1 § Ny lydelse av första paragrafen, som är lång nog att brytas på
427 98 17 - nästa rad.
448 115 17 - Ett andra stycke.
488 115 17 - Denna lag träder i kraft den 1 juli 2020.
529 98 17 - På regeringens vägnar
570 98 17 - NAMN NAMNSSON
590 385 17 - Handläggare
1075 98 9 - 1
1075 102 14 - Prop. 2019/20:1.
1092 98 9 - 2
1092 102 14 - Senaste lydelse 2015:50.
1128 794 17 - 1
"""

_ACT = ("1998:204", "Lag (1998:204) om test")


def test_parse_amendment_pdf_extracts_form_pinpoints_blocks_and_footnotes(monkeypatch):
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT)
    amendment = coverage.parse_amendment_pdf(
        Path("/dummy.pdf"), "2020:200", *_ACT)
    assert amendment.form == "Lag"
    assert amendment.named == {(None, "1")}
    assert amendment.effective == "2020-07-01"
    assert amendment.items == [("provision", (None, "1"))]
    # the superscript on the marker's baseline is its footnote digit, the
    # wrapped first line joined, the indented line a stycke of its own
    assert amendment.provisions == {(None, "1"): ("2", [
        "Ny lydelse av första paragrafen, som är lång nog att brytas på nästa rad.",
        "Ett andra stycke."])}
    assert amendment.tail == ["Denna lag träder i kraft den 1 juli 2020."]
    assert amendment.footnotes == {"1": "Prop. 2019/20:1.", "2": "Senaste lydelse 2015:50."}


def test_parse_amendment_pdf_keeps_a_heading_before_a_provision(monkeypatch):
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT.replace(
        "407 119 11 - 2\n", "380 98 17 B Rubrik före paragrafen\n407 119 11 - 2\n"))
    amendment = coverage.parse_amendment_pdf(Path("/dummy.pdf"), "2020:200", *_ACT)
    assert amendment.items == [("heading", "Rubrik före paragrafen"), ("provision", (None, "1"))]


def test_parse_amendment_pdf_reads_a_dels_clause(monkeypatch):
    # "dels att 3 § ska upphöra att gälla, dels att 1 § ska ha följande
    # lydelse": both provisions named, one printed
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT.replace(
        "föreskrivs att 1 § ska ha följande\n346 98 17 - lydelse.",
        "föreskrivs i fråga om lagen (1998:204) dels att 3 § ska upphöra att gälla,\n"
        "346 98 17 - dels att 1 § ska ha följande lydelse."))
    amendment = coverage.parse_amendment_pdf(Path("/dummy.pdf"), "2020:200", *_ACT)
    assert amendment.named == {(None, "1"), (None, "3")}
    assert set(amendment.provisions) == {(None, "1")}


_NO_BODY = ("407 119 11 - 2\n407 98 17 b 1 § Ny lydelse av första paragrafen, som är lång nog att brytas på\n"
            "427 98 17 - nästa rad.\n448 115 17 - Ett andra stycke.\n")


def test_parse_amendment_pdf_reads_a_word_substitution_clause(monkeypatch):
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT.replace(
        "föreskrivs att 1 § ska ha följande\n346 98 17 - lydelse.",
        "föreskrivs att i 1 och 2 §§ lagen (1998:204) om test ordet ”Vägverket” i olika\n"
        "346 98 17 - böjningsformer ska bytas ut mot ”Transportstyrelsen” i motsvarande form.").replace(_NO_BODY, ""))
    amendment = coverage.parse_amendment_pdf(Path("/dummy.pdf"), "2020:200", *_ACT)
    assert amendment.substitutions == [([(None, "1"), (None, "2")], "Vägverket", "Transportstyrelsen", True)]
    assert amendment.provisions == {}


def test_parse_amendment_pdf_reads_a_repeal_only_act(monkeypatch):
    # nothing printed, no commencement sentence: the provision ceases "vid
    # utgången av" a month, and the change is in force the day after
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT.replace(
        "föreskrivs att 1 § ska ha följande\n346 98 17 - lydelse.",
        "föreskrivs att 1 § lagen (1998:204) om test ska upphöra att gälla vid\n"
        "346 98 17 - utgången av april 2020.").replace(_NO_BODY, "").replace(
        "488 115 17 - Denna lag träder i kraft den 1 juli 2020.\n", ""))
    amendment = coverage.parse_amendment_pdf(Path("/dummy.pdf"), "2020:200", *_ACT)
    assert amendment.provisions == {}
    assert amendment.tail == []
    assert amendment.effective == "2020-05-01"


def test_parse_amendment_pdf_expands_a_lettered_range(monkeypatch):
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT.replace("att 1 § ska", "att 1 § och 11 a–11 c §§ ska"))
    amendment = coverage.parse_amendment_pdf(Path("/dummy.pdf"), "2020:200", *_ACT)
    assert amendment.named == {(None, "1"), (None, "11 a"), (None, "11 b"), (None, "11 c")}


def test_parse_amendment_pdf_refuses_a_table_shaped_provision(monkeypatch):
    # two runs on one baseline with a column gap between them
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT.replace(
        "448 115 17 - Ett andra stycke.", "448 115 17 - Kolumn ett | 400 Kolumn två"))
    with pytest.raises(coverage.NotSimple, match="table-shaped"):
        coverage.parse_amendment_pdf(Path("/dummy.pdf"), "2020:200", *_ACT)


def test_parse_amendment_pdf_accepts_the_i_fraga_om_form(monkeypatch):
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT.replace(
        "föreskrivs att 1 § ska ha följande\n346 98 17 - lydelse.",
        "föreskrivs i fråga om lagen (1998:204) att 1 § ska ha\n346 98 17 - följande lydelse."))
    amendment = coverage.parse_amendment_pdf(
        Path("/dummy.pdf"), "2020:200", *_ACT)
    assert amendment.named >= {(None, "1")}


def test_parse_amendment_pdf_refuses_another_acts_amendment(monkeypatch):
    # the mirrored PDF must name this act -- by number, or a balk by name
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT.replace("(1998:204)", "(1998:205)"))
    with pytest.raises(coverage.NotSimple, match="not this act"):
        coverage.parse_amendment_pdf(Path("/dummy.pdf"), "2020:200", *_ACT)


def test_parse_amendment_pdf_tells_footnotes_by_size_not_position(monkeypatch):
    # the footnotes follow the last body line directly (2018:717); a body
    # line that opens with a number and a capitalized word is body text
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT.replace(
        "448 115 17 - Ett andra stycke.\n",
        "448 115 17 - Ett andra stycke som nämner\n"
        "468 98 17 - 2 Transportstyrelsen och fortsätter.\n"
        "489 98 9 - 1\n489 102 14 - Prop. 2019/20:1.\n"
        "506 98 9 - 2\n506 102 14 - Senaste lydelse 2015:50.\n"
        "----\n"
    ).replace(
        "1075 98 9 - 1\n1075 102 14 - Prop. 2019/20:1.\n"
        "1092 98 9 - 2\n1092 102 14 - Senaste lydelse 2015:50.\n", ""))
    amendment = coverage.parse_amendment_pdf(
        Path("/dummy.pdf"), "2020:200", *_ACT)
    assert amendment.provisions[(None, "1")][1][1] == "Ett andra stycke som nämner 2 Transportstyrelsen och fortsätter."
    assert amendment.tail == ["Denna lag träder i kraft den 1 juli 2020."]
    assert amendment.footnotes == {"1": "Prop. 2019/20:1.", "2": "Senaste lydelse 2015:50."}


def test_parse_amendment_pdf_refuses_page_furniture_inside_a_provision(monkeypatch):
    # a footnote set at body size, which the size test cannot catch, must
    # not become statute text
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT.replace(
        "448 115 17 - Ett andra stycke.\n",
        "448 115 17 - Ett andra stycke.\n468 98 17 - 7 Senaste lydelse 2015:50.\n"))
    with pytest.raises(coverage.NotSimple, match="page furniture"):
        coverage.parse_amendment_pdf(Path("/dummy.pdf"), "2020:200", *_ACT)


def test_parse_amendment_pdf_refuses_page_furniture_in_the_tail(monkeypatch):
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT.replace(
        "488 115 17 - Denna lag träder i kraft den 1 juli 2020.\n",
        "488 115 17 - Denna lag träder i kraft den 1 juli 2020.\n"
        "508 98 17 - 9 Senaste lydelse 2015:50.\n").replace(
        "529 98 17 - På", "549 98 17 - På"))
    with pytest.raises(coverage.NotSimple, match="transitional provisions carry"):
        coverage.parse_amendment_pdf(Path("/dummy.pdf"), "2020:200", *_ACT)


def test_parse_amendment_pdf_drops_an_inline_reference_digit(monkeypatch):
    # "(Eric-konsortium)2": a superscript on a body line that is not a
    # marker is a reference into the footnotes, never statute text
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT.replace(
        "448 115 17 - Ett andra stycke.\n",
        "448 115 17 - Ett andra stycke (Eric-konsortium).\n448 330 11 - 2\n"))
    amendment = coverage.parse_amendment_pdf(
        Path("/dummy.pdf"), "2020:200", *_ACT)
    assert amendment.provisions[(None, "1")][1][1] == "Ett andra stycke (Eric-konsortium)."


def test_parse_amendment_pdf_splits_strecksatser_and_rejoins_hyphenation(monkeypatch):
    # strecksats items are set flush at the margin, one per line, and are
    # each a stycke of their own in the consolidated text (2018:753);
    # "till-" + "lämplig" gives back the compound's two l:s, "24–" + "28"
    # keeps the range's dash
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT.replace(
        "448 115 17 - Ett andra stycke.\n",
        "448 115 17 - Ett andra stycke med syften som är till-\n"
        "468 98 17 - lämpliga enligt 24–\n"
        "488 98 17 - 28 kap.:\n"
        "508 98 17 - – medverka till väl underbyggda beslut,\n"
        "528 98 17 - – ge ökade färdigheter.\n").replace(
        "488 115 17 - Denna lag", "568 115 17 - Denna lag").replace(
        "529 98 17 - På", "609 98 17 - På"))
    amendment = coverage.parse_amendment_pdf(
        Path("/dummy.pdf"), "2020:200", *_ACT)
    assert amendment.provisions[(None, "1")][1][1:] == [
        "Ett andra stycke med syften som är tillämpliga enligt 24–28 kap.:",
        "– medverka till väl underbyggda beslut,",
        "– ge ökade färdigheter."]


def test_parse_amendment_pdf_refuses_a_future_effective_date(monkeypatch):
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT.replace("den 1 juli 2020.", "den 1 juli 2099."))
    with pytest.raises(coverage.NotSimple, match="future"):
        coverage.parse_amendment_pdf(Path("/dummy.pdf"), "2020:200", *_ACT)


def test_parse_amendment_pdf_refuses_a_clause_with_no_known_verb(monkeypatch):
    _pdf(monkeypatch, "325 98 17 - föreskrivs att 3 § lagen (1998:204) ska förlängas.\n"
                      "346 98 17 - Denna lag träder i kraft den 1 juli 2020.\n"
                      "380 98 17 - På regeringens vägnar\n")
    with pytest.raises(coverage.NotSimple, match="no change this module reads"):
        coverage.parse_amendment_pdf(Path("/dummy.pdf"), "2020:200", *_ACT)


def test_parse_amendment_pdf_refuses_an_unbounded_body(monkeypatch):
    # no commencement sentence, no signature: nothing says where the last
    # provision's text ends
    _pdf(monkeypatch, "325 98 17 - föreskrivs att 1 § lagen (1998:204) ska ha följande lydelse.\n"
                      "400 98 17 b 1 § Ny lydelse.\n")
    with pytest.raises(coverage.NotSimple, match="bounds"):
        coverage.parse_amendment_pdf(Path("/dummy.pdf"), "2020:200", *_ACT)


def test_parse_amendment_pdf_refuses_an_unsigned_tail(monkeypatch):
    # without the signature block nothing bounds the transitional
    # provisions, and the signatories' names would become one of them
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT.replace("529 98 17 - På regeringens vägnar\n", ""))
    with pytest.raises(coverage.NotSimple, match="signature"):
        coverage.parse_amendment_pdf(Path("/dummy.pdf"), "2020:200", *_ACT)


def test_parse_amendment_pdf_reads_the_older_print(monkeypatch):
    # the 1998-2018 print: body 14, footnote labels 8 on their own baseline
    # just above the note (13), the page number a same-size run glued to the
    # last note, the chapter heading a bold line of its own above the
    # marker, a lettered marker bold and alone on its line with the text
    # starting on the next, a printer's colophon page at 8
    text = """
80 85 27 B Svensk författningssamling
175 547 18 B SFS 2005:1
192 85 13 b Lag | 547 Utkom från trycket
211 85 19 b om ändring i föräldrabalken; | 547 den 9 januari 2005
248 85 14 - utfärdad den 8 januari 2005.
282 239 9 - 1 2
283 100 14 - Enligt riksdagens beslut föreskrivs att 4 kap. 7 § och 5 kap. 2 a §
301 85 14 - balken skall ha följande lydelse.
346 85 16 B 4 kap.
372 85 14 b 7 § När det gäller adoptivbarnets ställning upphör all verkan av adop-
389 85 14 - tionen.
430 85 16 B 5 kap.
456 85 14 B 2a§
474 85 14 - Vid olyckor får vävnadsprover användas.
510 100 14 - Denna lag träder i kraft den 1 januari 2005.
545 85 14 - På regeringens vägnar
580 85 14 - THOMAS BODSTRÖM
598 316 14 - Dag Mattsson
906 85 8 - 1
908 93 13 - Prop. 2003/04:131, bet. 2004/05:SoU3.
919 85 8 - 2
921 93 13 - Balken omtryckt 1995:974. | 650 1
----
942 499 8 - Thomson Fakta, tel. 08-587 671 00
951 505 8 - Elanders Gotab, Stockholm 2005
"""
    _pdf(monkeypatch, text)
    amendment = coverage.parse_amendment_pdf(
        Path("/dummy.pdf"), "2005:1", "1949:381", "Föräldrabalk (1949:381)")
    assert amendment.form == "Lag"
    assert amendment.named >= {("4", "7"), ("5", "2 a")}
    assert amendment.provisions == {
        ("4", "7"): ("", ["När det gäller adoptivbarnets ställning upphör all "
                          "verkan av adoptionen."]),
        ("5", "2 a"): ("", ["Vid olyckor får vävnadsprover användas."])}
    assert amendment.tail == ["Denna lag träder i kraft den 1 januari 2005."]
    assert amendment.footnotes == {"1": "Prop. 2003/04:131, bet. 2004/05:SoU3.",
                         "2": "Balken omtryckt 1995:974."}


def test_parse_amendment_pdf_reads_across_a_page_break(monkeypatch):
    # a provision running over a page: the first page's footnotes cut, the
    # second page's running header (a run left of its first body line in
    # the older print, with or without "SFS", the body at that page's own
    # margin) dropped, its page number dropped, the stycke continuing at
    # the page top joined, a marker at a page top starting a block, a
    # hyphenated word rejoined across the break
    text = """
283 100 14 - Enligt riksdagens beslut föreskrivs att 1 och 2 §§ lagen (2004:100)
301 85 14 - om något skall ha följande lydelse.
346 85 14 b 1 § Första stycket, som slutar med ett avstavat ord: säker-
364 85 14 - hets- eller försvarspolitiska skäl.
382 100 14 - Andra stycket som fortsätter på nästa sida med en EU-
906 85 8 - 1
908 93 13 - Prop. 2003/04:131. | 650 1
----
50 38 14 - 2004:764 | 173 förordning och lite mer text.
86 173 14 b 2 § Andra paragrafen.
122 188 14 - Denna lag träder i kraft den 1 januari 2005.
158 173 14 - På regeringens vägnar
921 658 14 - 2
"""
    _pdf(monkeypatch, text)
    amendment = coverage.parse_amendment_pdf(
        Path("/dummy.pdf"), "2004:764", "2004:100", "Lag (2004:100) om något")
    assert amendment.provisions == {
        (None, "1"): ("", [
            "Första stycket, som slutar med ett avstavat ord: säkerhets- eller "
            "försvarspolitiska skäl.",
            "Andra stycket som fortsätter på nästa sida med en EU-förordning "
            "och lite mer text."]),
        (None, "2"): ("", ["Andra paragrafen."])}


def test_parse_amendment_pdf_handles_a_forordning_active_voice(monkeypatch):
    # a lag's enacting clause is passive ("... föreskrivs att ..."); a
    # förordning's is active ("Regeringen föreskriver att ..."), and its
    # trailing marker in the consolidated text says "Förordning", not "Lag"
    text = """
155 98 29 B Svensk författningssamling
221 98 20 B Förordning | 600 SFS 2020:1
246 98 21 B om ändring i kungörelsen (1947:948)
325 98 17 - Regeringen föreskriver att 14 b § kungörelsen (1947:948) ska
346 98 17 - ha följande lydelse.
407 140 11 - 2
407 98 17 b 14 b § Ny lydelse.
448 115 17 - Denna förordning träder i kraft den 1 mars 2020.
489 98 17 - På regeringens vägnar
1075 98 9 - 2
1075 102 14 - Senaste lydelse 2015:50.
"""
    _pdf(monkeypatch, text)
    amendment = coverage.parse_amendment_pdf(
        Path("/dummy.pdf"), "2020:1", "1947:948", "Kungörelse (1947:948) om något")
    assert amendment.form == "Förordning"
    assert amendment.named >= {(None, "14 b")}
    assert amendment.provisions == {(None, "14 b"): ("2", ["Ny lydelse."])}


def test_parse_amendment_pdf_ignores_a_cross_reference_at_a_line_wrap(monkeypatch):
    # a real provision marker is bold; a cross-reference to another law's
    # paragraf that the PDF's own line wrap puts at a line start is not --
    # 2026:9's own 14 b § cites "27 kap.\n33 § andra stycket rättegångsbalken"
    text = """
325 98 17 - föreskrivs att 1 § lagen (2000:2) ska ha följande
346 98 17 - lydelse.
407 98 17 b 1 § Se 27 kap.
427 98 17 - 33 § andra stycket rättegångsbalken för mer information.
468 115 17 - Denna lag träder i kraft den 1 januari 2000.
509 98 17 - På regeringens vägnar
"""
    _pdf(monkeypatch, text)
    amendment = coverage.parse_amendment_pdf(
        Path("/dummy.pdf"), "2000:1", "2000:2", "Lag (2000:2) om test")
    assert amendment.named >= {(None, "1")}
    assert amendment.provisions == {(None, "1"): ("", [
        "Se 27 kap. 33 § andra stycket rättegångsbalken för mer information."])}


# --- apply_amendment ---------------------------------------------------------

_ANDR_1 = coverage.parse_omfattning("ändr. 1 §")


def _apply(omfattning, base_text=None):
    """`apply_amendment` over the mocked PDF and `BASE_SOURCE` (its text
    replaced by `base_text` when given), returning the new source."""
    base = dict(BASE_SOURCE, fulltext=dict(BASE_SOURCE["fulltext"]))
    if base_text is not None:
        base["fulltext"]["forfattningstext"] = base_text
    return coverage.apply_amendment(base, Path("/dummy.pdf"), "2020:200",
                                    coverage.parse_omfattning(omfattning))

def test_apply_amendment_splices_in_the_consolidated_shape(monkeypatch):
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT)
    new_source = coverage.apply_amendment(BASE_SOURCE, Path("/dummy.pdf"), "2020:200", _ANDR_1)
    assert new_source["fulltext"]["forfattningstext"] == (
        "1 kap. Inledande bestämmelser\r\n\r\n"
        "1 § Ny lydelse av första paragrafen, som är lång nog att brytas på "
        "nästa rad.\r\n\r\n"
        "Ett andra stycke.\r\n"
        "Lag (2020:200).\r\n\r\n"
        "2 § Andra paragrafen, opåverkad.\r\n"
        "Lag (2010:20).\r\n\r\n"
        "2 kap. Andra kapitlet\r\n\r\n"
        "1 § Kapitel två, paragraf ett.\r\n"
        "Lag (2010:20).\r\n")
    assert new_source["fulltext"]["andringInford"] == "t.o.m. SFS 2020:200"
    # the base dict itself is never mutated
    assert BASE_SOURCE["fulltext"]["andringInford"] == "t.o.m. SFS 2020:100"


def test_apply_amendment_lists_transitional_provisions_under_the_amendment(monkeypatch):
    # anything beyond the bare commencement sentence goes under the
    # amendment's own number at the end of the Övergångsbestämmelser
    # section -- the government's own consolidation lists exactly those
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT.replace(
        "488 115 17 - Denna lag träder i kraft den 1 juli 2020.\n",
        "488 115 17 - 1. Denna lag träder i kraft den 1 juli 2020.\n"
        "508 115 17 - 2. Äldre föreskrifter gäller fortfarande för ärenden som har inletts\n"
        "528 98 17 - före ikraftträdandet.\n").replace(
        "529 98 17 - På", "569 98 17 - På"))
    base = dict(BASE_SOURCE, fulltext=dict(
        BASE_SOURCE["fulltext"],
        forfattningstext=BASE_SOURCE["fulltext"]["forfattningstext"]
        + "\r\nÖvergångsbestämmelser\r\n\r\n2015:50\r\n\r\n"
          "1. Denna lag träder i kraft den 1 januari 2016.\r\n\r\n"
          "2. Äldre bestämmelser gäller för år 2015.\r\n\r\nBilaga\r\n\r\nTabell.\r\n"))
    text = coverage.apply_amendment(base, Path("/dummy.pdf"), "2020:200", _ANDR_1)["fulltext"]["forfattningstext"]
    assert text.endswith(
        "2. Äldre bestämmelser gäller för år 2015.\r\n\r\n"
        "2020:200\r\n\r\n"
        "1. Denna lag träder i kraft den 1 juli 2020.\r\n\r\n"
        "2. Äldre föreskrifter gäller fortfarande för ärenden som har inletts "
        "före ikraftträdandet.\r\n\r\nBilaga\r\n\r\nTabell.\r\n")


def test_apply_amendment_opens_the_section_when_the_base_has_none(monkeypatch):
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT.replace(
        "488 115 17 - Denna lag träder i kraft den 1 juli 2020.\n",
        "488 115 17 - Denna lag träder i kraft den 1 juli 2020 och tillämpas första\n"
        "508 98 17 - gången för år 2021.\n").replace(
        "529 98 17 - På", "549 98 17 - På"))
    text = coverage.apply_amendment(BASE_SOURCE, Path("/dummy.pdf"), "2020:200", _ANDR_1)["fulltext"]["forfattningstext"]
    assert text.endswith(
        "Lag (2010:20).\r\n\r\nÖvergångsbestämmelser\r\n\r\n2020:200\r\n\r\n"
        "Denna lag träder i kraft den 1 juli 2020 och tillämpas första gången "
        "för år 2021.\r\n")


def test_apply_amendment_keeps_the_heading_after_the_provision(monkeypatch):
    # an unamended provision carries no trailing marker, so its end is the
    # next thing that opens something else -- here the heading before 2 §,
    # which a span running to the next *marker* would have deleted
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT)
    base = dict(BASE_SOURCE, fulltext=dict(
        BASE_SOURCE["fulltext"],
        forfattningstext=(
            "1 § Gammal lydelse.\r\n\r\nEtt andra stycke utan markör.\r\n\r\n"
            "Rubrik före 2 §\r\n\r\n2 § Andra paragrafen.\r\n")))
    text = coverage.apply_amendment(base, Path("/dummy.pdf"), "2020:200", _ANDR_1)["fulltext"]["forfattningstext"]
    assert text == (
        "1 § Ny lydelse av första paragrafen, som är lång nog att brytas på "
        "nästa rad.\r\n\r\nEtt andra stycke.\r\nLag (2020:200).\r\n\r\n"
        "Rubrik före 2 §\r\n\r\n2 § Andra paragrafen.\r\n")


def test_apply_amendment_keeps_a_list_introduction_with_its_provision(monkeypatch):
    # "Underrättelsen skall innehålla upplysning om" ends without a period,
    # like a heading, but is followed by its list, not by a provision -- it
    # is the old provision's own text and goes with the replacement
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT)
    base = dict(BASE_SOURCE, fulltext=dict(
        BASE_SOURCE["fulltext"],
        forfattningstext=(
            "1 § Gammal lydelse.\r\n\r\nUnderrättelsen skall innehålla upplysning om\r\n\r\n"
            "1. kontrolluppgifter,\r\n\r\n2. intäkt av kapital.\r\n\r\n"
            "Rubrik\r\n\r\n2 § Andra paragrafen.\r\n")))
    text = coverage.apply_amendment(base, Path("/dummy.pdf"), "2020:200", _ANDR_1)["fulltext"]["forfattningstext"]
    assert text == (
        "1 § Ny lydelse av första paragrafen, som är lång nog att brytas på "
        "nästa rad.\r\n\r\nEtt andra stycke.\r\nLag (2020:200).\r\n\r\n"
        "Rubrik\r\n\r\n2 § Andra paragrafen.\r\n")


def test_apply_amendment_keeps_same_numbered_provisions_in_two_chapters_apart(monkeypatch):
    # 1 kap. 1 § and 2 kap. 1 § share a paragraf number; each must get its
    # own printed text
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT.replace(
        "att 1 § ska", "att 1 kap. 1 § och 2 kap. 1 § ska").replace(
        "407 119 11 - 2\n"
        "407 98 17 b 1 § Ny lydelse av första paragrafen, som är lång nog att brytas på\n"
        "427 98 17 - nästa rad.\n"
        "448 115 17 - Ett andra stycke.\n",
        "380 98 17 B 1 kap.\n407 98 17 b 1 § Ny ettan.\n"
        "440 98 17 B 2 kap.\n466 98 17 b 1 § Ny tvåan.\n"))
    text = coverage.apply_amendment(
        BASE_SOURCE, Path("/dummy.pdf"), "2020:200",
        coverage.parse_omfattning("ändr. 1 kap. 1 §, 2 kap. 1 §"))["fulltext"]["forfattningstext"]
    assert "1 kap. Inledande bestämmelser\r\n\r\n1 § Ny ettan.\r\nLag (2020:200)." in text
    assert "2 kap. Andra kapitlet\r\n\r\n1 § Ny tvåan.\r\nLag (2020:200)." in text


def test_apply_amendment_refuses_when_footnote_disagrees_with_the_base(monkeypatch):
    # the base's own trailing marker for 1 § is "Lag (2015:50)." -- a PDF
    # claiming a *different* "senaste lydelse" means either the wrong base
    # or the wrong provision, and this must refuse rather than apply anyway
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT.replace("Senaste lydelse 2015:50",
                                                  "Senaste lydelse 2016:60"))
    with pytest.raises(coverage.NotSimple, match="Senaste lydelse"):
        coverage.apply_amendment(BASE_SOURCE, Path("/dummy.pdf"), "2020:200", _ANDR_1)


def test_apply_amendment_refuses_when_register_and_pdf_disagree(monkeypatch):
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT)
    with pytest.raises(coverage.NotSimple, match="register says 1 §, 2 § changed, PDF prints 1 §"):
        _apply("ändr. 1, 2 §§")
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT.replace(
        "407 119 11 - 2\n", "380 98 17 B Rubrik före paragrafen\n407 119 11 - 2\n"))
    with pytest.raises(coverage.NotSimple, match="headings before nothing, PDF prints headings before 1 §"):
        _apply("ändr. 1 §")


def test_apply_amendment_inserts_a_new_provision_with_its_heading(monkeypatch):
    # "1 a §" goes after "1 §", its printed heading before it; the heading
    # that heads 2 § stays where it was
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT.replace(
        "föreskrivs att 1 § ska ha följande\n346 98 17 - lydelse.",
        "föreskrivs att det i lagen (1998:204) ska införas en ny paragraf, 1 a §,\n"
        "346 98 17 - och närmast före 1 a § en ny rubrik av följande lydelse.").replace(
        "407 119 11 - 2\n407 98 17 b 1 § Ny lydelse av första paragrafen, som är lång nog att brytas på\n"
        "427 98 17 - nästa rad.\n",
        "380 98 17 B Ny rubrik\n407 98 17 b 1 a § Ny paragraf.\n"))
    text = _apply("ny 1 a §, rubr. närmast före 1 a §", base_text=(
        "1 § Första.\r\nLag (2015:50).\r\n\r\nRubrik före 2 §\r\n\r\n2 § Andra.\r\n"))["fulltext"]["forfattningstext"]
    assert text == ("1 § Första.\r\nLag (2015:50).\r\n\r\nNy rubrik\r\n\r\n1 a § Ny paragraf.\r\n\r\n"
                    "Ett andra stycke.\r\nLag (2020:200).\r\n\r\nRubrik före 2 §\r\n\r\n2 § Andra.\r\n")


def test_apply_amendment_keeps_several_new_provisions_in_order(monkeypatch):
    # "nya 1 a, 1 b §§" both go after 1 §, as one block in ordinal order,
    # the heading before 1 a § first -- as separate edits at one offset the
    # heading block sorted last and came out after 1 b § (2011:318 at
    # 2022:1124, "Marknadskontroll" before 37 a §)
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT.replace(
        "föreskrivs att 1 § ska ha följande\n346 98 17 - lydelse.",
        "föreskrivs att det i lagen (1998:204) ska införas två nya paragrafer, 1 a och 1 b §§,\n"
        "346 98 17 - och närmast före 1 a § en ny rubrik av följande lydelse.").replace(
        "407 119 11 - 2\n407 98 17 b 1 § Ny lydelse av första paragrafen, som är lång nog att brytas på\n"
        "427 98 17 - nästa rad.\n448 115 17 - Ett andra stycke.\n",
        "390 98 17 B Nya rubriken\n407 98 17 b 1 a § Första nya.\n448 98 17 b 1 b § Andra nya.\n"))
    text = _apply("nya 1 a, 1 b §§, rubr. närmast före 1 a §",
                  base_text="1 § Första.\r\n\r\n2 § Andra.\r\n")["fulltext"]["forfattningstext"]
    assert text == ("1 § Första.\r\n\r\nNya rubriken\r\n\r\n1 a § Första nya.\r\nLag (2020:200).\r\n\r\n"
                    "1 b § Andra nya.\r\nLag (2020:200).\r\n\r\n2 § Andra.\r\n")


def test_apply_amendment_refuses_a_new_provision_that_exists(monkeypatch):
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT.replace("föreskrivs att 1 § ska ha följande",
                                                  "föreskrivs att det ska införas en ny paragraf, 1 §, av följande"))
    with pytest.raises(coverage.NotSimple, match="already has 1 §"):
        _apply("ny 1 §", base_text="1 § Första.\r\n\r\n2 § Andra.\r\n")


def test_apply_amendment_writes_the_repeal_note(monkeypatch):
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT.replace(
        "föreskrivs att 1 § ska ha följande\n346 98 17 - lydelse.",
        "föreskrivs i fråga om lagen (1998:204) dels att 2 § ska upphöra att gälla,\n"
        "346 98 17 - dels att 1 § ska ha följande lydelse."))
    text = _apply("upph. 2 §; ändr. 1 §", base_text=(
        "1 § Första.\r\n\r\n2 § Andra.\r\nLag (2010:20).\r\n\r\n3 § Tredje.\r\n"))["fulltext"]["forfattningstext"]
    assert text == ("1 § Ny lydelse av första paragrafen, som är lång nog att brytas på nästa rad.\r\n\r\n"
                    "Ett andra stycke.\r\nLag (2020:200).\r\n\r\n"
                    "2 § Har upphävts genom lag (2020:200).\r\n\r\n3 § Tredje.\r\n")


def test_apply_amendment_replaces_adds_and_removes_headings(monkeypatch):
    base_text = ("Gammal rubrik\r\n\r\n1 § Första.\r\n\r\n2 § Andra.\r\n\r\n"
                 "Rubrik före 3 §\r\n\r\n3 § Tredje.\r\n")
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT.replace(
        "föreskrivs att 1 § ska ha följande\n346 98 17 - lydelse.",
        "föreskrivs i fråga om lagen (1998:204) dels att rubriken närmast före 3 § ska utgå,\n"
        "346 98 17 - dels att rubriken närmast före 2 § ska lyda ”Citerad rubrik”, dels att 1 § och\n"
        "366 98 17 - rubriken närmast före 1 § ska ha följande lydelse.").replace(
        "407 119 11 - 2\n", "390 98 17 B Ny rubrik\n407 119 11 - 2\n"))
    text = _apply("ändr. 1 §, rubr. närmast före 1, 2 §§; rubr. närmast före 3 § utgår",
                  base_text=base_text)["fulltext"]["forfattningstext"]
    assert text == ("Ny rubrik\r\n\r\n1 § Ny lydelse av första paragrafen, som är lång nog att brytas på nästa rad.\r\n\r\n"
                    "Ett andra stycke.\r\nLag (2020:200).\r\n\r\nCiterad rubrik\r\n\r\n2 § Andra.\r\n\r\n3 § Tredje.\r\n")


def test_apply_amendment_takes_the_new_title_from_the_print(monkeypatch):
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT.replace(
        "föreskrivs att 1 § ska ha följande\n346 98 17 - lydelse.",
        "föreskrivs i fråga om lagen (1998:204) dels att rubriken till lagen ska ha\n"
        "346 98 17 - följande lydelse, dels att 1 § ska ha följande lydelse.").replace(
        "407 119 11 - 2\n", "380 98 21 B Lag om nya\n395 98 21 B testfall\n407 119 11 - 2\n"))
    new = _apply("ändr. författningsrubr., 1 §")
    assert new["rubrik"] == "Lag om nya testfall"
    assert "1 § Ny lydelse" in new["fulltext"]["forfattningstext"]


def test_apply_amendment_settles_pending_variants_up_to_its_own_date(monkeypatch):
    # the base carries an earlier amendment's two wordings of 2 §, a pending
    # new 3 §, a pending repeal of 4 §, a pending renumbering of 5 §, a
    # pending heading, and a heading and an 8 § that just cease, all dated
    # before this amendment takes effect; a variant dated after it stays
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT)
    base_text = (
        "1 § Första.\r\n\r\n"
        "2 § /Upphör att gälla U:2020-06-01/\r\nGammal tvåa.\r\nLag (2010:20).\r\n\r\n"
        "2 § /Träder i kraft I:2020-06-01/\r\nNy tvåa.\r\nLag (2019:9).\r\n\r\n"
        "3 § /Träder i kraft I:2020-06-01/\r\nNy trea.\r\nLag (2019:9).\r\n\r\n"
        "4 § /Upphör att gälla U:2020-06-01 genom lag (2019:9)./\r\nGammal fyra.\r\n\r\n"
        "Andra stycket.\r\nLag (2010:20).\r\n\r\n"
        "5 § /Ny beteckning 6 § U:2020-06-01/ Femman.\r\n\r\n"
        "/Rubriken upphör att gälla U:2020-06-01/ Gammal rubrik\r\n\r\n"
        "/Rubriken träder i kraft I:2020-06-01/ Ny rubrik\r\n\r\n"
        "7 § /Upphör att gälla U:2021-01-01/\r\nGammal sjua.\r\n\r\n"
        "7 § /Träder i kraft I:2021-01-01/\r\nNy sjua.\r\n\r\n"
        "/Rubriken upphör att gälla U:2020-06-01/\r\nBorta rubrik\r\n\r\n"
        "8 § /Upphör att gälla U:2020-06-01/\r\nBorta.\r\n\r\nHelt borta.\r\n\r\n"
        "9 § Nian.\r\n")
    text = _apply("ändr. 1 §", base_text=base_text)["fulltext"]["forfattningstext"]
    assert text == (
        "1 § Ny lydelse av första paragrafen, som är lång nog att brytas på nästa rad.\r\n\r\n"
        "Ett andra stycke.\r\nLag (2020:200).\r\n\r\n"
        "2 § Ny tvåa.\r\nLag (2019:9).\r\n\r\n"
        "3 § Ny trea.\r\nLag (2019:9).\r\n\r\n"
        "4 § Har upphävts genom lag (2019:9).\r\n\r\n"
        "6 § Femman.\r\n\r\n"
        "Ny rubrik\r\n\r\n"
        "7 § /Upphör att gälla U:2021-01-01/\r\nGammal sjua.\r\n\r\n"
        "7 § /Träder i kraft I:2021-01-01/\r\nNy sjua.\r\n\r\n"
        "9 § Nian.\r\n")


def test_apply_amendment_refuses_a_provision_still_pending(monkeypatch):
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT)
    with pytest.raises(coverage.NotSimple, match="still carries a pending"):
        _apply("ändr. 1 §", base_text=(
            "1 § /Upphör att gälla U:2021-01-01/\r\nGammal.\r\n\r\n"
            "1 § /Träder i kraft I:2021-01-01/\r\nNy.\r\n"))


def test_apply_amendment_substitutes_words(monkeypatch):
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT.replace(
        "föreskrivs att 1 § ska ha följande\n346 98 17 - lydelse.",
        "föreskrivs att i 1 och 2 §§ lagen (1998:204) om test ordet ”länsrätt” i olika\n"
        "346 98 17 - böjningsformer ska bytas ut mot ”förvaltningsrätt” i motsvarande form.").replace(_NO_BODY, ""))
    text = _apply("ändr. 1, 2 §§", base_text=(
        "1 § Länsrätten prövar. Hos länsrättens kansli. Lag (2015:50).\r\n\r\n"
        "2 § Talan förs i länsrätt.\r\n\r\n3 § Länsrätten berörs inte.\r\n"))["fulltext"]["forfattningstext"]
    assert text == (
        "1 § Förvaltningsrätten prövar. Hos förvaltningsrättens kansli.\r\nLag (2020:200).\r\n\r\n"
        "2 § Talan förs i förvaltningsrätt.\r\nLag (2020:200).\r\n\r\n3 § Länsrätten berörs inte.\r\n")


def test_apply_amendment_refuses_a_substitution_with_nothing_to_replace(monkeypatch):
    _pdf(monkeypatch, _AMENDMENT_PDF_TEXT.replace(
        "föreskrivs att 1 § ska ha följande\n346 98 17 - lydelse.",
        "föreskrivs att i 1 § lagen (1998:204) om test ordet ”Vägverket” ska bytas ut\n"
        "346 98 17 - mot ”Transportstyrelsen”.").replace(_NO_BODY, ""))
    with pytest.raises(coverage.NotSimple, match="no \"Vägverket\" to replace"):
        _apply("ändr. 1 §", base_text="1 § Vägverkets beslut.\r\n")


# --- pending_gaps -------------------------------------------------------------

def _write_current(tmp_path, monkeypatch, basefile, andringsforfattningar,
                   cutoff):
    monkeypatch.setattr(layout, "SFS_DOWNLOADED", tmp_path / "downloaded")
    source = {
        "beteckning": basefile,
        "fulltext": {"andringInford": "t.o.m. SFS %s" % cutoff,
                    "forfattningstext": "current text"},
        "andringsforfattningar": andringsforfattningar,
    }
    compress.write_json(layout.sfs_source(basefile), source)
    return source


def test_pending_gaps_finds_the_first_link_of_each_broken_run(tmp_path, monkeypatch):
    basefile = "1998:204"
    chain = [{"beteckning": v} for v in
            ("2001:1", "2002:1", "2003:1", "2004:1", "2005:1")]
    _write_current(tmp_path, monkeypatch, basefile, chain, "2005:1")
    # only 2001:1 is archived; 2005:1 is current -- 2002:1..2004:1 are gaps,
    # but only the first (2002:1, right after the covered 2001:1) is
    # attemptable until it is itself resolved
    p1 = layout.sfs_archive_version_download(layout.SFS_DOWNLOADED, basefile, "2001:1")
    compress.write_json(p1, {"beteckning": basefile})
    gaps = coverage.pending_gaps(basefile)
    assert [(g[0], g[2]) for g in gaps] == [("2001:1", "2002:1")]


def test_pending_gaps_resets_at_a_later_independently_covered_link(
        tmp_path, monkeypatch):
    basefile = "1998:204"
    chain = [{"beteckning": v} for v in
            ("2001:1", "2002:1", "2003:1", "2004:1", "2005:1")]
    _write_current(tmp_path, monkeypatch, basefile, chain, "2005:1")
    for v in ("2001:1", "2003:1"):
        compress.write_json(
            layout.sfs_archive_version_download(layout.SFS_DOWNLOADED, basefile, v),
            {"beteckning": basefile})
    gaps = coverage.pending_gaps(basefile)
    # 2002:1 is a gap after 2001:1 (attemptable); 2003:1 is independently
    # covered, so 2004:1 is a fresh, separately attemptable gap after it
    assert [(g[0], g[2]) for g in gaps] == [("2001:1", "2002:1"),
                                            ("2003:1", "2004:1")]


def test_pending_gaps_empty_when_fully_covered(tmp_path, monkeypatch):
    basefile = "1998:204"
    chain = [{"beteckning": "2001:1"}]
    _write_current(tmp_path, monkeypatch, basefile, chain, "2001:1")
    assert coverage.pending_gaps(basefile) == []


def test_pending_gaps_skips_a_whole_act_repeal_link(tmp_path, monkeypatch):
    # a bare "upph." link never gets its own consolidated text at all --
    # the government's own system doesn't mint one for a whole-act repeal
    # -- so it is never a gap, and the walk continues past it exactly as
    # if it weren't in the chain: 2003:1 is a gap after the covered
    # 2001:1, skipping straight past the 2002:1 repeal link in between
    basefile = "1998:204"
    chain = [{"beteckning": "2001:1"},
            {"beteckning": "2002:1", "anteckningar": "upph."},
            {"beteckning": "2003:1"},
            {"beteckning": "2004:1"}]
    _write_current(tmp_path, monkeypatch, basefile, chain, "2004:1")
    compress.write_json(
        layout.sfs_archive_version_download(layout.SFS_DOWNLOADED, basefile, "2001:1"),
        {"beteckning": basefile})
    gaps = coverage.pending_gaps(basefile)
    assert [(g[0], g[2]) for g in gaps] == [("2001:1", "2003:1")]


# --- cover_gap (orchestration) ------------------------------------------------

def _stub_pdf(tmp_path, monkeypatch, version="2020:200"):
    monkeypatch.setattr(layout, "sfs_pdf",
                        lambda v: tmp_path / "pdf" / (v.replace(":", "-") + ".pdf"))
    pdf_path = layout.sfs_pdf(version)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"stub")
    monkeypatch.setattr(coverage.pdftext, "pdf_pages",
                        lambda p: _pages(_AMENDMENT_PDF_TEXT))


def test_cover_gap_skips_when_no_pdf_mirrored(tmp_path, monkeypatch):
    monkeypatch.setattr(layout, "SFS_DOWNLOADED", tmp_path / "downloaded")
    base_path = _write_base(tmp_path, monkeypatch)
    status, detail = coverage.cover_gap("1998:204", "2020:100", base_path,
                                        "2020:200")
    assert status == "skipped"
    assert "mirror-pdf" in detail


def test_cover_gap_skips_an_html_base_that_wont_parse(tmp_path, monkeypatch):
    monkeypatch.setattr(layout, "SFS_DOWNLOADED", tmp_path / "downloaded")
    html_base = tmp_path / "downloaded" / "archive" / "1998" / "204" / \
        ".versions" / "2020" / "100.html"
    html_base.parent.mkdir(parents=True)
    html_base.write_text("<!DOCTYPE html><html></html>")
    _stub_pdf(tmp_path, monkeypatch)
    status, detail = coverage.cover_gap("1998:204", "2020:100", html_base,
                                        "2020:200")
    assert status == "skipped"
    assert "doesn't parse" in detail


# a base archived under either legacy HTML generation reads the same way a
# JSON one does, once its plain text is out -- this is the utf-8 rättsdatabaser-
# successor page shape (extract.extract_body's "search-results-content >
# body-text" branch); the paragraf/marker formatting inside body-text is the
# same "\n"-joined convention as BASE_SOURCE's own text, just without "\r"
_HTML_BASE_PAGE = (
    "<!DOCTYPE html><html><body>"
    # parse_sfst_header reads every result-inner-box *without* a nested
    # body-text div: box[0] the "SFS-nummer · ... · Visa register" line,
    # box[1] the bare Rubrik text, box[2:] "Key: value" header fields --
    # the real page's own layout (confirmed against a fetched legacy page)
    "<div class=\"result-inner-box bold\">SFS-nummer · 1998:204 · "
    "<a>Visa register</a></div>"
    "<div class=\"result-inner-box\">En testlag</div>"
    "<div class=\"result-inner-box\">Departement: Justitiedepartementet</div>"
    "<div class=\"search-results-content\"><div class=\"body-text\">"
    "1 kap. Inledande bestämmelser\n\n"
    "1 § Gammal lydelse av första paragrafen.\n"
    "Lag (2015:50).\n\n"
    "2 § Andra paragrafen, opåverkad.\n"
    "Lag (2010:20).\n\n"
    "2 kap. Andra kapitlet\n\n"
    "1 § Kapitel två, paragraf ett.\n"
    "Lag (2010:20).\n"
    "</div></div>"
    "</body></html>"
)


def test_cover_gap_reads_and_reconstructs_from_an_html_base(tmp_path, monkeypatch):
    monkeypatch.setattr(layout, "SFS_DOWNLOADED", tmp_path / "downloaded")
    _write_current(tmp_path, monkeypatch, "1998:204",
                   [{"beteckning": "2020:200", "anteckningar": "ändr. 1 §"}],
                   "2020:200")
    html_base = layout.sfs_archive_version_download(
        layout.SFS_DOWNLOADED, "1998:204", "2020:100").with_suffix(".html")
    compress.write_bytes(html_base, _HTML_BASE_PAGE.encode("utf-8"))
    _stub_pdf(tmp_path, monkeypatch)
    status, dest = coverage.cover_gap("1998:204", "2020:100", html_base, "2020:200")
    assert status == "wrote"
    written = compress.read_json(Path(dest))
    text = written["fulltext"]["forfattningstext"]
    assert "1 § Ny lydelse av första paragrafen" in text
    assert "Gammal lydelse" not in text
    assert "Andra paragrafen, opåverkad." in text        # untouched, carried over
    assert "Kapitel två, paragraf ett." in text
    assert written["beteckning"] == "1998:204"
    assert written["rubrik"] == "En testlag"


# the latin-1 rättsdatabaser archival page: header lines above an <hr>
# inside a <pre>, the statute text below it
_LATIN1_BASE_PAGE = (
    "<html><body><pre>SFS nr: 1998:204\nRubrik:<b> En testlag</b>\n"
    "Ändring införd:<b> t.o.m. SFS 2020:100</b>\n<hr>\n"
    "1 kap. Inledande bestämmelser\n\n"
    "1 § Gammal lydelse av första paragrafen.\n"
    "Lag (2015:50).\n\n"
    "2 § Andra paragrafen, opåverkad.\n"
    "Lag (2010:20).\n"
    "</pre></body></html>"
).encode("latin-1")


def test_cover_gap_reads_a_latin1_archival_base(tmp_path, monkeypatch):
    monkeypatch.setattr(layout, "SFS_DOWNLOADED", tmp_path / "downloaded")
    _write_current(tmp_path, monkeypatch, "1998:204",
                   [{"beteckning": "2020:200", "anteckningar": "ändr. 1 §"}],
                   "2020:200")
    html_base = layout.sfs_archive_version_download(
        layout.SFS_DOWNLOADED, "1998:204", "2020:100").with_suffix(".html")
    compress.write_bytes(html_base, _LATIN1_BASE_PAGE)
    _stub_pdf(tmp_path, monkeypatch)
    status, dest = coverage.cover_gap("1998:204", "2020:100", html_base, "2020:200")
    assert status == "wrote"
    written = compress.read_json(Path(dest))
    assert written["rubrik"] == "En testlag"
    assert written["fulltext"]["forfattningstext"].lstrip().startswith(
        "1 kap. Inledande bestämmelser\r\n\r\n1 § Ny lydelse av första paragrafen")
    assert "Andra paragrafen, opåverkad." in written["fulltext"]["forfattningstext"]


def test_cover_gap_writes_a_marked_reconstruction(tmp_path, monkeypatch):
    _write_current(tmp_path, monkeypatch, "1998:204",
                   [{"beteckning": "2020:100", "anteckningar": "ändr. 2 §"},
                    {"beteckning": "2020:200", "anteckningar": "ändr. 1 §"},
                    {"beteckning": "2021:5", "anteckningar": "ändr. 2 §"}],
                   "2021:5")
    base_path = _write_base(tmp_path, monkeypatch)
    _stub_pdf(tmp_path, monkeypatch)
    status, dest = coverage.cover_gap("1998:204", "2020:100", base_path, "2020:200")
    assert status == "wrote"
    written = compress.read_json(Path(dest))
    assert written[coverage.RECONSTRUCTED_KEY]["base"] == "2020:100"
    assert written[coverage.RECONSTRUCTED_KEY]["amendment"] == "2020:200"
    assert "cover-consolidation-gap 1998:204" in \
        written[coverage.RECONSTRUCTED_KEY]["command"]
    # the text itself is unmarked: the parser and the history export read
    # it as any other archived consolidation
    assert written["fulltext"]["forfattningstext"].startswith("1 kap. Inledande")
    # the register is the live document's chain, cut at the target
    assert [a["beteckning"] for a in written["andringsforfattningar"]] == [
        "2020:100", "2020:200"]


def test_cover_gap_reports_not_simple_without_writing(tmp_path, monkeypatch):
    _write_current(tmp_path, monkeypatch, "1998:204",
                   [{"beteckning": "2020:200", "anteckningar": "ändr. 1 §, bil."}],
                   "2020:200")
    base_path = _write_base(tmp_path, monkeypatch)
    _stub_pdf(tmp_path, monkeypatch)
    status, detail = coverage.cover_gap("1998:204", "2020:100", base_path, "2020:200")
    assert status == "not_simple"
    assert "names bil." in detail
    dest = layout.sfs_archive_version_download(
        layout.SFS_DOWNLOADED, "1998:204", "2020:200")
    assert not compress.exists(dest)


def test_cover_gap_dry_run_writes_nothing(tmp_path, monkeypatch):
    _write_current(tmp_path, monkeypatch, "1998:204",
                   [{"beteckning": "2020:200", "anteckningar": "ändr. 1 §"}],
                   "2020:200")
    base_path = _write_base(tmp_path, monkeypatch)
    _stub_pdf(tmp_path, monkeypatch)
    status, _detail = coverage.cover_gap("1998:204", "2020:100", base_path,
                                         "2020:200", dry_run=True)
    assert status == "wrote"
    dest = layout.sfs_archive_version_download(
        layout.SFS_DOWNLOADED, "1998:204", "2020:200")
    assert not compress.exists(dest)


def test_cover_gap_refuses_a_whole_act_repeal_via_the_ordinary_gate(
        tmp_path, monkeypatch):
    # cover_gap itself carries no special case for a whole-act repeal -- a
    # bare "upph." omfattning names no provision to write, the same refusal
    # path an empty Omfattning takes
    _write_current(tmp_path, monkeypatch, "1998:204",
                   [{"beteckning": "2020:200", "anteckningar": "upph."}],
                   "2020:200")
    base_path = _write_base(tmp_path, monkeypatch)
    _stub_pdf(tmp_path, monkeypatch)
    status, detail = coverage.cover_gap("1998:204", "2020:100", base_path, "2020:200")
    assert status == "not_simple"
    assert "names no change to write" in detail
    dest = layout.sfs_archive_version_download(
        layout.SFS_DOWNLOADED, "1998:204", "2020:200")
    assert not compress.exists(dest)
