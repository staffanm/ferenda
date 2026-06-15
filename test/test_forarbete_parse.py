"""Tests for the förarbete PDF parser's text->blocks logic (PDF-free)."""

from accommodanda.forarbete.parse import (classify, mint_uri, page_paragraphs)


def texts(blocks):
    return [(b.kind, b.text, b.page, b.level) for b in blocks]


def test_mint_uri_matches_citation_form():
    assert mint_uri("prop", "2025/26:161") == "https://lagen.nu/prop/2025/26:161"
    assert mint_uri("sou", "2020:1") == "https://lagen.nu/sou/2020:1"
    assert mint_uri("dir", "2020:1") == "https://lagen.nu/dir/2020:1"


def test_page_paragraphs_strips_header_pagenumber_and_reflows():
    page = ("Prop. 2025/26:161\n"
            "Detta är en mening som bryts mitt i ett ord för-\n"
            "fogar över resten.\n"
            "\n"
            "Ett nytt stycke börjar här.\n"
            "40\n")
    paras = page_paragraphs(page, "Prop. 2025/26:161", 40)
    assert paras == ["Detta är en mening som bryts mitt i ett ord förfogar över resten.",
                     "Ett nytt stycke börjar här."]


def test_page_paragraphs_strips_header_that_bled_into_a_line():
    # plain pdftotext sometimes merges the running header into a body line
    page = "Bland Prop. 2025/26:161 andra kommuner yttrade sig.\n"
    assert page_paragraphs(page, "Prop. 2025/26:161", 31) == \
        ["Bland andra kommuner yttrade sig."]


def test_page_paragraphs_skips_table_of_contents():
    toc = "\n".join("%d Avsnitt ........................ %d" % (i, i + 10)
                    for i in range(1, 8))
    assert page_paragraphs(toc, "Prop. 2025/26:161", 3) == []


def test_classify_headings_and_paragraphs():
    blocks = classify(["1", "Förslag till riksdagsbeslut",
                       "4.3.2 Inledande granskning",
                       "En vanlig brödtext här."], 5)
    assert texts(blocks) == [
        ("rubrik", "1 Förslag till riksdagsbeslut", 5, 1),
        ("rubrik", "4.3.2 Inledande granskning", 5, 3),
        ("stycke", "En vanlig brödtext här.", 5, None)]


def test_classify_drops_lone_number_without_a_title():
    # a stray digit (TOC residue / footnote marker) with no title following
    # must not swallow the next number into a "3 4" heading
    blocks = classify(["3", "4", "Riktig text."], 2)
    assert [b.text for b in blocks] == ["4 Riktig text."]  # "3" dropped, no "3 4"
