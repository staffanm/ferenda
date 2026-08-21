"""The heading gutters: an EU article's designation and a förarbete section's
number move out of the heading text into a gutter of their own, and an all-caps
EU division title is re-set in sentence case.

Every case below is one the corpus actually produces
(accommodanda/eurlex/render.py, accommodanda/forarbete/render.py)."""

from accommodanda.eurlex.render import (
    _article_parts,
    _case_map,
    _division_label,
    _sentence_case,
)
from accommodanda.forarbete.render import _numbered, _outline
from accommodanda.lib.text import runs_text


def _prose(*sentences):
    return [{"type": "recital", "num": str(i), "text": [s]}
            for i, s in enumerate(sentences, 1)]


# ---- the EU article designation ------------------------------------------

def test_article_splits_its_designation_from_its_title():
    block = {"type": "article", "num": "1", "id": "1",
             "text": [{"text": "Artikel 1", "uri": "https://lagen.nu/ext/celex/X#1"},
                      " – Innehåll och tillämpningsområde"]}
    word, num, title = _article_parts(block)
    assert (word, num) == ("Artikel", "1")
    assert runs_text(title) == "Innehåll och tillämpningsområde"


def test_article_with_no_title_keeps_only_its_designation():
    # 128 of 150 sampled acts: the article heading is the designation alone
    block = {"type": "article", "num": "2", "id": "2",
             "text": [{"text": "Artikel 2", "uri": "https://lagen.nu/ext/celex/X#2"}]}
    word, num, title = _article_parts(block)
    assert (word, num, title) == ("Artikel", "2", [])


def test_article_number_split_across_runs():
    # Formex sets "Artikel 6" and the letter as siblings (31979R0929 art. 6b);
    # cutting by run index left "Artikel 6b" in the title beside the gutter
    block = {"type": "article", "num": "6b", "id": "6b",
             "text": [{"text": "Artikel 6", "uri": "https://lagen.nu/ext/celex/X#6b"},
                      "b", " – Rubriken"]}
    word, num, title = _article_parts(block)
    assert (word, num) == ("Artikel", "6b")
    assert runs_text(title) == "Rubriken"


def test_article_heading_in_another_language_still_splits():
    # 32013R0389 art. 65: the lead run is the whole English heading
    block = {"type": "article", "num": "65", "id": "65",
             "text": ["Article 65 – Överföring av utsläppsrätter"]}
    word, num, title = _article_parts(block)
    assert (word, num) == ("Article", "65")
    assert runs_text(title) == "Överföring av utsläppsrätter"


def test_article_that_does_not_split_reports_it():
    # no designation to find -> the caller prints the plain one-line heading
    # rather than printing the designation twice
    block = {"type": "article", "num": "3", "id": "3", "text": ["Något helt annat"]}
    assert _article_parts(block) == (None, None, None)


def test_article_with_no_number_reports_it():
    assert _article_parts({"type": "article", "text": ["Artikel"]}) == (None, None, None)


# ---- the EU division heading ---------------------------------------------

def test_division_heading_splits_and_lowercases():
    blocks = _prose("Dessa allmänna bestämmelser gäller alla.",
                    "De allmänna reglerna och bestämmelser som avses.",
                    "Vidare gäller allmänna bestämmelser i övrigt.")
    label, title = _division_label(["KAPITEL I ALLMÄNNA BESTÄMMELSER"],
                                   _case_map(blocks))
    assert label == "Kapitel I"
    assert runs_text(title) == "Allmänna bestämmelser"


def test_division_title_keeps_a_capital_the_act_uses_consistently():
    blocks = _prose("Uppgifter lämnas till Europeiska centralbanken varje år.",
                    "Rapporten sänds till Europeiska centralbanken.",
                    "Ett organ eller kommissionen får begära uppgifter.",
                    "Beslut fattas av kommissionen efter samråd.")
    label, title = _division_label(["KAPITEL V ORGAN, KOMMISSIONEN OCH EUROPEISKA"],
                                   _case_map(blocks))
    assert label == "Kapitel V"
    assert runs_text(title) == "Organ, kommissionen och Europeiska"


def test_a_single_stray_capital_is_not_evidence():
    # one cross-reference capitalised "Direktanspråk" mid-sentence and the title
    # came out "Intyg, självrisk, Direktanspråk"
    blocks = _prose("Se avsnittet Direktanspråk nedan.",
                    "Ett direktanspråk får framställas av den skadelidande.",
                    "Varje intyg utfärdas av försäkringsgivaren.")
    _, title = _division_label(["KAPITEL VI INTYG, DIREKTANSPRÅK"], _case_map(blocks))
    assert runs_text(title) == "Intyg, direktanspråk"


def test_heading_without_a_designation_is_left_as_published():
    # 32010R0642: the act writes "AMERIKAS FÖRENTA STATER" nowhere else, so
    # lowercasing it would put words on the page the act never wrote
    runs = ["INTYG GODKÄNT AV AMERIKAS FÖRENTA STATER"]
    assert _division_label(runs, _case_map(_prose("Ett intyg utfärdas."))) == (None, runs)


def test_a_heading_the_source_sets_in_mixed_case_is_untouched():
    runs = ["Europeiska unionens officiella tidning"]
    assert _division_label(runs, ({}, {}, set())) == (None, runs)


def test_a_code_the_act_prints_in_capitals_is_left_alone():
    # 32014R0139 cites itself by "ADR.OR.B"; lowercasing it to "ADR.or.b"
    # rewrites the identifier and a reader searching the page misses it
    blocks = _prose("Kraven i ADR.OR.B.015 ska uppfyllas av ledningen.",
                    "Se ADR.OR.B för närmare bestämmelser om ledning.",
                    "Denna ledning ska dokumenteras.")
    label, title = _division_label(["KAPITEL B LEDNING (ADR.OR.B)"], _case_map(blocks))
    assert label == "Kapitel B"
    assert runs_text(title) == "Ledning (ADR.OR.B)"


def test_division_title_keeps_the_links_inside_it():
    blocks = _prose("Reglerna om data gäller varje företag.",
                    "Ett företag som behandlar data ska anmäla detta.")
    _, title = _division_label(
        ["KAPITEL II DATADELNING MELLAN ",
         {"text": "FÖRETAG", "uri": "https://lagen.nu/ext/celex/X#2.24"}],
        _case_map(blocks))
    assert title[1]["uri"] == "https://lagen.nu/ext/celex/X#2.24"
    assert title[1]["text"] == "företag"


def test_an_acronym_glued_to_an_ordinary_word_splits_at_the_hyphen():
    # "EFHU-GARANTIN" is not an identifier the act prints; it is an acronym and
    # a Swedish noun, and each half decides for itself
    blocks = _prose("Kommissionen förvaltar garantin enligt avtalet.",
                    "Denna garantin och garantifonden redovisas separat.",
                    "Medlen i garantifonden ska placeras säkert.")
    _, title = _division_label(["KAPITEL III EFHU-GARANTIN OCH EFHU-GARANTIFONDEN"],
                               _case_map(blocks))
    assert runs_text(title) == "EFHU-garantin och EFHU-garantifonden"


def test_only_the_token_after_the_designation_is_a_numeral():
    # "I SAMBAND MED" holds a standalone I, and it is the preposition
    blocks = _prose("Villkoren i samband med åtkomst ska vara skäliga.",
                    "Oskäliga villkor i samband med avtal gäller inte.")
    label, title = _division_label(["KAPITEL IV OSKÄLIGA VILLKOR I SAMBAND MED"],
                                   _case_map(blocks))
    assert label == "Kapitel IV"
    assert runs_text(title) == "Oskäliga villkor i samband med"


def test_case_map_ignores_a_table_row():
    # a table's cells are joined with pipes, which made every cell look like a
    # sentence start and put a capital "Artikel" in the map
    blocks = [{"type": "table", "text": ["Artikel | Artikel | Artikel"]},
              {"type": "recital", "num": "1", "text": ["Som avses i artikel 2."]}]
    cap, low, shouted = _case_map(blocks)
    assert "artikel" not in cap
    assert _sentence_case("SOM AVSES I ARTIKEL", (cap, low, shouted))[0] == \
        "Som avses i artikel"


# ---- the förarbete section number ----------------------------------------

def _fa(*rows):
    """`(level, text)` rows as a nested avsnitt structure."""
    return [{"type": "avsnitt", "level": lvl, "text": text if isinstance(text, list)
             else [text]} for lvl, text in rows]


def test_dotted_section_number_moves_to_the_gutter():
    doc = _fa((1, "2 Lagtext"), (2, "2.1 Förslag till lag"))
    num, title = _numbered(doc[1], 2, _outline(doc))
    assert num == "2.1"
    assert runs_text(title) == "Förslag till lag"


def test_a_number_in_its_own_styled_run_is_found():
    # the common artifact shape; reading only a leading plain string missed
    # half of every document's numbered headings
    doc = _fa((1, [{"style": "b", "text": "3"}, " Ärendet och dess beredning"]),
              (2, "3.1 Bakgrund"))
    num, title = _numbered(doc[0], 1, _outline(doc))
    assert num == "3"
    assert runs_text(title) == "Ärendet och dess beredning"


def test_a_top_level_number_with_no_subsections_still_counts():
    # prop. 2020/21:43 numbers "2 Lagtext" with a 2.1 under it and "1 Förslag
    # till riksdagsbeslut" with nothing; both are sections on the same page
    doc = _fa((1, "1 Förslag till riksdagsbeslut"), (1, "2 Lagtext"),
              (2, "2.1 Förslag till lag"))
    assert _numbered(doc[0], 1, _outline(doc))[0] == "1"


def test_a_running_head_is_not_a_section_number():
    # a scanned proposition prints its own identity on every page
    doc = _fa((1, "4 Ärendet"), (2, "4.1 Bakgrund"),
              (1, "38 Kungl. Maj:ts proposition nr 287."),
              (1, "50 Kungl. Maj:ts proposition nr 287."))
    outline = _outline(doc)
    assert _numbered(doc[2], 1, outline)[0] is None
    assert _numbered(doc[0], 1, outline)[0] == "4"


def test_a_number_above_the_documents_own_ceiling_is_a_table_cell():
    doc = _fa((1, "4 Ärendet"), (2, "4.1 Bakgrund"), (1, "1562 Investeringar"))
    assert _numbered(doc[2], 1, _outline(doc))[0] is None


def test_a_document_with_no_decimal_numbering_gets_no_gutter():
    doc = _fa((1, "20 Kungl. Maj:ts Nåd. Proposition Nr 286."),
              (1, "24 Spanien"))
    outline = _outline(doc)
    assert _numbered(doc[0], 1, outline)[0] is None
    assert _numbered(doc[1], 1, outline)[0] is None


def test_ocr_debris_is_not_a_numbered_section():
    doc = _fa((1, "4 Ärendet"), (2, "4.1 Bakgrund"), (1, "3 J-"))
    assert _numbered(doc[2], 1, _outline(doc))[0] is None


def test_a_lowercase_continuation_line_is_not_a_heading():
    doc = _fa((1, "4 Ärendet"), (2, "4.1 Bakgrund"),
              (1, "6 kap. 10 § första stycket får, trots"))
    assert _numbered(doc[2], 1, _outline(doc))[0] is None


def test_a_reprinted_official_journal_date_is_not_a_section_number():
    # a förarbete that reprints an EU act carries its running head:
    # "27.6.2013 SV Europeiska unionens officiella tidning L 176/431"
    doc = _fa((1, "4 Ärendet"), (2, "4.1 Bakgrund"),
              (2, "27.6.2013 SV Europeiska unionens officiella tidning L 176/431"))
    assert _numbered(doc[2], 2, _outline(doc))[0] is None


def test_the_number_must_sit_on_its_documents_own_outline_level():
    doc = _fa((1, "4 Ärendet"), (2, "4.1 Bakgrund"), (3, "8 Pensionssystemet"))
    assert _numbered(doc[2], 3, _outline(doc))[0] is None
