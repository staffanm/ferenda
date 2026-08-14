"""ICJ decision harvesting (the /decisions view), OCR repair, paragraph
splitting and folkrätt wiring.

Runs off a committed stored-record fixture and small strings -- no network and
no PDF binary. The PDF path's two hard parts, telling the Court's paragraph
numbering from every other number and repairing the Reports scans' OCR, are
pure functions and are unit-tested as such.
"""

import json
import re
from pathlib import Path

from bs4 import BeautifulSoup

from accommodanda.icj import download, ocr, parse, treaties
from accommodanda.icj import render as icj_render
from accommodanda.icj.model import (
    Block,
    Decision,
    decision_uri,
    doc_basefile,
    parse_stem,
)
from accommodanda.lib import catalog, facets, layout, render
from accommodanda.lib.page import BANNERS
from accommodanda.lib.pdftext import (
    Line,
    join_across_pages,
    paragraph_texts,
    strip_page_furniture,
)

FIXTURES = Path(__file__).parent / "files" / "icj"
KNOWN = frozenset({"all", "amount", "chamber", "judgment", "may", "from",
                   "time", "military", "convention", "the", "within", "and",
                   "concerning", "iii", "court", "state"})


# --------------------------------------------------------------------------
# identity: the Court's own decision filename is the basefile
# --------------------------------------------------------------------------

def test_stem_grammar_and_uri():
    parts = parse_stem("070-19860627-JUD-01-00")
    assert parts == {"case": "070", "date": "1986-06-27", "kind": "JUD",
                     "part": "01", "sub": "00"}
    assert decision_uri("070-19860627-JUD-01-00") == \
        "https://lagen.nu/ext/icj/070-19860627-JUD-01-00"


def test_the_one_underscored_filename_normalises():
    """875 of the Court's 877 decision files separate with "-"; the 2020 ICAO
    Council judgment uses "_" throughout. Both name the same decision, so both
    have to reach the same basefile or a re-harvest stores it twice."""
    assert doc_basefile("171_20201218_JUD_01-00") == "171-20201218-JUD-01-00"
    assert doc_basefile("171-20201218-JUD-01-00") == "171-20201218-JUD-01-00"


def test_an_unparsable_stem_raises():
    """A stem that does not parse would mint a URI nothing can read back."""
    for bad in ("not-a-stem", "70-19860627-JUD-01-00", ""):
        try:
            doc_basefile(bad)
        except ValueError:
            continue
        raise AssertionError("%r should not parse as a decision stem" % bad)


# --------------------------------------------------------------------------
# scope: what the harvest takes from the Court's 877 decisions
# --------------------------------------------------------------------------

def test_scope_takes_the_court_s_word_on_the_law():
    judgment = {"kind": "judgment", "procedure": "Merits"}
    advisory = {"kind": "advisory opinion", "procedure": None}
    measures = {"kind": "order",
                "procedure": "Request for the indication of provisional measures"}
    modified = {"kind": "order", "procedure": "Request for the modification of "
                                              "the Order of 28 March 2024 "
                                              "indicating provisional measures"}
    for row in (judgment, advisory, measures, modified):
        assert download.in_scope(row), row
    # the ~620 docket orders: bookkeeping, not a reader's document
    for procedure in ("Fixing of time-limits: Memorial and Counter-Memorial",
                      "Extension of time-limit: Counter-Memorial",
                      "Removal from the list"):
        assert not download.in_scope({"kind": "order", "procedure": procedure})


def test_english_copy_is_preferred_and_the_typo_still_counts():
    """One 2022 order is published as "…-enc.pdf" -- a typing slip at the Court
    for "en", and the only copy of that order."""
    base = "/sites/default/files/case-related/192/192-20240126-ord-01-00-%s.pdf"
    assert download._english([base % "fr", base % "en", base % "bi"]) == base % "en"
    assert download._english([base % "fr", base % "bi"]) == base % "bi"
    assert download._english([base % "enc"]) == base % "enc"
    assert download._english([base % "fr"]) is None


def test_row_reads_the_index_row():
    html = ('<div class="views-row">'
            '<div class="views-field"><div class="field-content">'
            '<a href="/sites/default/files/case-related/192/'
            '192-20240126-ord-01-00-en.pdf"><p>Order of 26 January 2024</p></a>'
            '</div></div>'
            '<div class="views-field"><div class="field-content"></div></div>'
            '<div class="views-field"><div class="field-content">'
            '<a href="/case/192">Application of the Genocide Convention in the '
            'Gaza Strip (South Africa v. Israel)</a></div></div>'
            '<div class="views-field"><div class="field-content">'
            'Request for the Indication of Provisional Measures</div></div>'
            '</div>')
    row = download._row(BeautifulSoup(html, "html.parser").select_one(".views-row"))
    assert row["basefile"] == "192-20240126-ORD-01-00"
    assert row["case"] == "192" and row["date"] == "2024-01-26"
    assert row["kind"] == "order"
    assert row["procedure"] == "Request for the Indication of Provisional Measures"
    assert row["case_name"].startswith("Application of the Genocide Convention")
    assert row["url"].endswith("192-20240126-ord-01-00-en.pdf")


def test_a_drifted_index_shape_raises():
    """`_row` reads the view's columns by position and column 3 is `procedure`,
    which is the whole scope decision for orders. A reordered column leaves
    every row parsing, so only the distribution can see the drift: the corpus
    would silently gain the ~620 time-limit orders or lose the 66
    provisional-measures ones."""
    def rows(judgments=158, advisory=31, orders=66):
        return ([{"kind": "judgment"}] * judgments
                + [{"kind": "advisory opinion"}] * advisory
                + [{"kind": "order"}] * orders)
    download._check_scope(rows())                    # today's shape passes
    for bad, missing in ((rows(judgments=3), "judgments"),
                         (rows(advisory=0), "advisory opinions"),
                         (rows(orders=0), "provisional-measures orders"),
                         (rows(orders=600), "time-limit orders")):
        try:
            download._check_scope(bad)
        except ValueError:
            continue
        raise AssertionError("an index missing its %s must raise" % missing)


# --------------------------------------------------------------------------
# OCR repair of the pre-2002 Reports scans
# --------------------------------------------------------------------------

def test_repair_fixes_the_two_measured_confusions():
    """`l` read as `1` and `m` split into `rn` are 635 of the ~880 repairable
    tokens measured over ten scanned decisions."""
    text, count = ocr.repair("al1 the consequences", KNOWN)
    assert text == "all the consequences" and count == 1
    assert ocr.repair("Judgrnent of the Charnber", KNOWN)[0] == \
        "Judgment of the Chamber"
    # and the other direction: the scanner fuses "rn" into "m"
    assert ocr.repair("conceming the matter", KNOWN)[0] == "concerning the matter"


def test_repair_never_touches_a_number():
    """A token of nothing but digits is the Court's own numbering. The
    ``1``->``l`` and ``1``->``i`` rules read it as a misprint: paragraph "111."
    became "iii.", which ended the paragraph sequence of the 2012 Belgium v.
    Senegal judgment at paragraph 110 and cost every paragraph after it."""
    text, count = ocr.repair("111. The Court considers", KNOWN)
    assert text.startswith("111.") and count == 0
    assert ocr.repair("Article 1 and Article 111", KNOWN)[1] == 0


def test_repair_leaves_a_word_and_an_ambiguous_token_alone():
    # already a word: nothing to repair
    assert ocr.repair("the court and the state", KNOWN)[1] == 0
    # two rules, two different words -> no single reading, so no rewrite.
    # "al1" is "all" under 1->l and "ali" under 1->i; with both in the
    # vocabulary there is nothing to choose between them.
    assert ocr.repair("al1", frozenset({"all", "ali"}))[1] == 0
    assert ocr.repair("al1", frozenset({"all"}))[0] == "all"


# --------------------------------------------------------------------------
# paragraph numbering: the citation anchor
# --------------------------------------------------------------------------

def test_a_glued_run_is_cut_into_numbered_paragraphs():
    """The Reports set a numbered paragraph flush with the one above it, so the
    whole run of reasoning arrives as one block."""
    blocks = parse._classify([
        "Makes the following Order: 1. On 29 December 2023, South Africa filed "
        "an Application. 2. In its Application, South Africa seeks to found "
        "the Court's jurisdiction on Article IX. 3. The Application contained "
        "a Request for provisional measures.",
    ])
    assert [(b.kind, b.number) for b in blocks] == \
        [("stycke", None), ("stycke", "1"), ("stycke", "2"), ("stycke", "3")]
    assert blocks[0].text == "Makes the following Order:"
    assert blocks[1].text.startswith("On 29 December 2023")


def test_a_quoted_paragraph_number_is_not_the_court_s_own():
    """A judgment block-quotes an ICTY judgment's paragraph 531 in the middle of
    its own paragraph 3. Reading the leading number off the text alone filed
    that quotation as the Court's paragraph 531."""
    blocks = parse._classify([
        "1. The Court begins with the facts.",
        "2. The Chamber found as follows:",
        "531. Turning to the mens rea requisite for the offence of torture, "
        "the Chamber refers to the nature of the beatings.",
        "3. The Court regards those findings as sufficient.",
    ])
    numbers = [b.number for b in blocks if b.number]
    assert numbers == ["1", "2", "3"]
    assert "531" not in numbers
    quoted = [b for b in blocks if b.text.startswith("531.")]
    assert quoted and quoted[0].number is None


def test_a_hole_costs_one_paragraph_not_every_later_one():
    """Walking forward from "the next number I expect" stopped dead at the first
    number the scan lost -- paragraph 74 of 524 in the 2015 Croatia v. Serbia
    judgment. The longest consecutive chain resynchronises instead."""
    blocks = parse._classify([
        "1. First.", "2. Second.",
        "The scan lost the number of the third paragraph.",
        "4. Fourth.", "5. Fifth.", "6. Sixth.",
    ])
    assert [b.number for b in blocks if b.number] == ["1", "2", "4", "5", "6"]


def test_a_section_divider_does_not_end_the_numbering():
    """The Court sets a centred asterisk between sections, and the next
    paragraph opens right after it."""
    blocks = parse._classify([
        "1. The Court recalls the Application.",
        "* 2. In its Application, the DRC made the following claim.",
        "* * * 3. The Court turns to jurisdiction.",
    ])
    assert [b.number for b in blocks if b.number] == ["1", "2", "3"]


def test_the_longest_chain_wins_over_a_restarted_opinion():
    """A separate opinion restarts at 1 and forms its own, shorter chain. Only
    the Court's own reasoning is numbered -- that is the text a citation to
    "paragraph 3" means."""
    blocks = parse._classify([
        "1. One.", "2. Two.", "3. Three.", "4. Four.",
        "SEPARATE OPINION OF JUDGE SIMMA",
        "1. I agree with the Court.", "2. I would add the following.",
    ])
    numbered = [(b.number, b.text) for b in blocks if b.number]
    assert [n for n, _ in numbered] == ["1", "2", "3", "4"]
    assert any(b.kind == "rubrik" and "SIMMA" in b.text for b in blocks)


# --------------------------------------------------------------------------
# the Reports' front matter and typesetting debris
# --------------------------------------------------------------------------

def _lines(*texts):
    return [Line(text=text, top=index * 20, bold=False, lead_bold=False,
                 italic=False, size=10)
            for index, text in enumerate(texts)]


def test_the_bilingual_front_matter_is_dropped():
    """The Court's letterhead over a ``YEAR`` line is where the decision starts.
    Keying on the letterhead alone starts the body at page 1 and keeps the
    French, because the Reports print the same words on their cover."""
    pages = [
        (1, _lines("INTERNATIONAL COURT OF JUSTICE",
                   "COUR INTERNATIONALE DE JUSTICE",
                   "RECUEIL DES ARRÊTS, AVIS CONSULTATIFS ET ORDONNANCES")),
        (2, _lines("Official citation : Corfu Channel, Judgment, "
                   "I.C.J. Reports 1949, p. 4")),
        (3, _lines("INTERNATIONAL COURT OF JUSTICE", "YEAR 1949",
                   "THE CORFU CHANNEL CASE")),
        (4, _lines("1. On 22 May 1947 the Government of the United Kingdom.")),
    ]
    assert [page for page, _ in parse.body_pages(pages)] == [3, 4]


def test_typesetting_debris_is_cut_out_not_used_to_reject_the_block():
    """Both the printer's imposition stamp and the unfilled running-head
    placeholder land *inside* a sentence. Dropping the whole block cost the 2015
    Croatia v. Serbia judgment paragraphs 75 to 524, because the paragraph
    sequence never resumed after the hole."""
    blocks = parse._classify([
        "1. The Court recalls its 2008 Judgment. 6 CIJ1034.indb 3 7/01/14 12:43 "
        "2. In its 2008 Judgment, the Court dismissed the objection.",
        "3. Following the election, running head content of Judge Tladi, "
        "Mr Moseneke ceased to sit.",
    ])
    assert [b.number for b in blocks if b.number] == ["1", "2", "3"]
    assert not any("CIJ1034" in b.text for b in blocks)
    assert not any("running head content" in b.text for b in blocks)
    assert blocks[2].text.startswith("Following the election, of Judge Tladi")


def test_headings_and_their_levels():
    for heading in ("I. GEOGRAPHY", "A. Uti possidetis juris",
                    "OPERATIVE CLAUSE", "SEPARATE OPINION OF JUDGE SIMMA",
                    "DISSENTING OPINION OF JUDGE AD HOC KREĆA"):
        assert parse._is_heading(heading), heading
    for prose in ("The Court considers that the claim is admissible.",
                  "In its Application, Nicaragua asks the Court to adjudge."):
        assert not parse._is_heading(prose), prose
    assert parse._heading_level("I. GEOGRAPHY") == 1
    assert parse._heading_level("A. Uti possidetis juris") == 2
    assert parse._heading_level("1. The 1928 Treaty") == 3


# --------------------------------------------------------------------------
# real extracted pages: the three defects that shipped green against strings
#
# `lines-*.json` freeze what `pages_with_ocr` actually returns for a page range
# of a stored decision -- the OCR as it is, not as the happy-path strings above
# assume. Every unit test before these passed while the corpus carried all
# three defects, which is why they exist (rule:lock-in-with-fixture).
# --------------------------------------------------------------------------

def _pages(name):
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return [(pageno, [Line(**line) for line in lines])
            for pageno, lines in data["pages"]]


def _blocks_from(name):
    """The blocks a fixture's page range yields. `body_pages` is not called: the
    ranges are already inside the decision, and cutting front matter is what
    `test_the_ocr_letterhead_still_finds_the_body` covers."""
    return parse._classify(join_across_pages(paragraph_texts(
        strip_page_furniture(_pages(name)))))


def test_the_ocr_letterhead_still_finds_the_body():
    """The 1948 Corfu Channel judgment's OCR gives "INTERNATIONAL COURT O F
    JUSTICE" -- a space inside "OF" -- on the very page the seam test looks
    for. Requiring single spaces missed it and 52 other documents, which then
    published the Reports' French cover and table of contents as body text."""
    pages = _pages("lines-corfu-preliminary.json")
    body = parse.body_pages(pages, "001-19480325-JUD-01-00")
    assert body[0][0] == 3, "the body starts at the Court's dateline page"
    text = " ".join(l.text for _p, lines in body for l in lines)
    assert "COUR INTERNATIONALE DE JUSTICE" not in text   # the French cover is cut
    assert "RECUEIL" not in text


def test_a_document_without_the_seam_raises():
    """Keeping the whole document instead was not the visible defect its comment
    claimed: nobody saw the French, on a fifth of the corpus."""
    pages = [(1, [Line(text="Something else entirely", top=0, bold=False,
                       lead_bold=False, italic=False, size=10)])]
    try:
        parse.body_pages(pages, "test")
    except ValueError as exc:
        assert "front matter cannot be cut" in str(exc)
    else:
        raise AssertionError("a PDF with no seam must not parse")


def test_the_reports_running_head_never_reaches_the_body():
    """`strip_page_furniture` cannot see it: the Reports alternate the head
    between recto and verso and the OCR perturbs each copy, so the repeated-line
    test never fires. 66 documents kept 742 of these, and in this very judgment
    one sat as a rubrik between the two halves of a split sentence."""
    blocks = _blocks_from("lines-nicaragua-merits.json")
    texts = [b.text for b in blocks]
    assert not any(re.search(r"MILITARY AND PARAMILITARY ACTIVITIES \(JUDGMENT\)",
                             t) for t in texts), \
        [t for t in texts if "JUDGMENT)" in t]


def test_the_ocr_really_mangles_two_paragraph_numbers():
    """The evidence behind `MAX_NUMBER_GAP`. This judgment's OCR splits
    paragraphs 111 and 112 into "1 1 1." and "1 12.", so nothing downstream can
    read them as numbers and the Court's sequence steps 110 -> 113."""
    text = " ".join(line.text for _p, lines in _pages("lines-nicaragua-merits.json")
                    for line in lines)
    assert re.search(r"\b1 1 1\.", text), "paragraph 111 should be mangled"
    assert re.search(r"\b1 12\.", text), "paragraph 112 should be mangled"
    # and 110 and 113 do come through, which is what makes the step three wide
    assert re.search(r"\b110\.\s", text) and re.search(r"\b113\.\s", text)


def test_a_hole_three_wide_does_not_cost_the_paragraphs_before_it():
    """The corpus consequence of the mangling above. At a maximum gap of two the
    chain broke at 110, the longer tail won, and paragraphs 1-112 of the
    Nicaragua judgment -- 1,930 anchors across the corpus -- shipped with no
    anchor at all."""
    texts = ["%d. Paragraph text." % n for n in range(1, 111)] \
        + ["1 1 1. Mangled.", "1 12. Mangled."] \
        + ["%d. Paragraph text." % n for n in range(113, 290)]
    numbers = sorted(int(re.match(r"(\d+)", texts[i][o:]).group(1))
                     for i, o in parse.paragraph_chain(texts))
    assert numbers[0] == 1 and numbers[-1] == 289
    assert len(numbers) == 287            # every real number, both sides of the hole


def test_a_long_chain_earns_its_late_opening():
    """LaGrand's appearance list swallows its paragraphs 1-6, so its chain opens
    at 7 and runs to 134. An absolute opening veto rejected all 121 anchors. A
    chain that reaches back at least as far as it opens is the Court's own
    numbering whatever it starts at -- while a run of page numbers that opens at
    210 and is 30 long has not accounted for the 209 before it."""
    lagrand = ["Appearances and other prefatory matter."] \
        + ["%d. Paragraph text." % n for n in range(7, 135)]
    numbers = sorted(int(re.match(r"(\d+)", lagrand[i][o:]).group(1))
                     for i, o in parse.paragraph_chain(lagrand))
    assert numbers[0] == 7 and len(numbers) == 128
    # and a short run opening far into the document earns nothing
    pages = ["Body text."] + ["%d. THE COURT considers." % n
                              for n in range(210, 240)]
    assert parse.paragraph_chain(pages) == set()


def test_two_numbers_are_not_a_numbering():
    """`MIN_CHAIN`'s own case. The Asylum fixture above cannot see this floor --
    its stray chain opens at 812, so the opening guard alone kills it. A pair
    that opens at 1 passes that guard, and "1. … 2. …" in a table of contents or
    a quoted submission is not the Court numbering its reasoning."""
    assert parse.paragraph_chain(["1. First item.", "2. Second item.",
                                  "Ordinary prose follows."]) == set()
    assert parse.paragraph_chain(["1. First.", "2. Second.", "3. Third."])


def test_a_decision_that_numbers_nothing_gets_no_anchors():
    """The pre-1960 Reports number no paragraphs. Under "longest chain wins" a
    lone stray number was a chain of one and won by default: the 1950 Asylum
    judgment shipped #P812, #P814 and #P816 -- taken from the page numbers of an
    annex letter list -- as permanent citation targets."""
    blocks = _blocks_from("lines-asylum.json")
    assert blocks, "the page range should still yield text"
    assert [b.number for b in blocks if b.number] == []


# --------------------------------------------------------------------------
# artifact projection + parse metadata
# --------------------------------------------------------------------------

def test_to_artifact_anchors_paragraphs():
    decision = Decision(
        basefile="070-19860627-JUD-01-00", case="070",
        case_name="Military and Paramilitary Activities in and against Nicaragua",
        kind="judgment", title="Judgment of 27 June 1986", date="1986-06-27",
        procedure="Merits",
        body=[Block("rubrik", "I. THE FACTS"),
              Block("stycke", "First.", number="1"),
              Block("stycke", "Second.", number="2"),
              Block("stycke", "A closing line the Court did not number.")])
    art = decision.to_artifact()
    assert [n.get("id") for n in art["structure"]] == [None, "P1", "P2", "S3"]
    assert art["identifier"] == "ICJ 70 (Judgment, 1986-06-27)"
    assert art["doctype"] == "dom"
    assert art["source_url"] == "https://www.icj-cij.org/case/70"


def test_a_record_without_its_pdf_raises():
    """`download.resolve` writes the PDF before the record, so this state cannot
    come from a harvest -- only from a hand-edited or corrupt store. Parsing it
    to an empty structure published a page with six metadata rows, no text and
    no error."""
    try:
        parse.parse("070-19860627-JUD-01-00", FIXTURES)
    except ValueError as exc:
        assert "stored without its PDF" in str(exc)
    else:
        raise AssertionError("a record with no PDF must not parse")


def test_record_metadata_reaches_the_artifact():
    """The metadata half, read off the stored index row rather than the PDF."""
    record = json.loads((FIXTURES / "070-19860627-JUD-01-00.json").read_text())
    art = Decision(
        basefile=record["basefile"], case=record["case"],
        case_name=record["case_name"], kind=record["kind"],
        title=record["title"], date=record["date"],
        procedure=record["procedure"], pdf_url=record["url"]).to_artifact()
    assert art["uri"] == "https://lagen.nu/ext/icj/070-19860627-JUD-01-00"
    assert art["type"] == "avgorande" and art["court"] == "icj"
    assert art["title"].startswith("Military and Paramilitary Activities")
    assert art["avgorandedatum"] == "1986-06-27"
    md = art["metadata"]
    assert md["publisher"] == "International Court of Justice"
    assert md["caseNumber"] == "70"
    assert md["procedure"] == "Merits"
    assert md["ocrRepairs"] == 0
    assert md["pdfUrl"].endswith("070-19860627-JUD-01-00-EN.pdf")


# --------------------------------------------------------------------------
# the treaty references: why the source is here
# --------------------------------------------------------------------------

def test_a_decision_cites_the_treaties_it_applies():
    """The corpus already held these instruments and nothing cited them: untc's
    14 treaties had zero inbound links before this source existed."""
    refs = treaties.references(
        "The Court recalls Article II of the Convention on the Prevention and "
        "Punishment of the Crime of Genocide and Article 31 of the Vienna "
        "Convention on the Law of Treaties. See also UNCLOS.")
    assert [r["uri"] for r in refs] == [
        "https://lagen.nu/ext/untc/I-1021",     # Genocide Convention
        "https://lagen.nu/ext/untc/I-18232",    # VCLT
        "https://lagen.nu/ext/untc/I-31363"]    # UNCLOS
    assert all(r["predicate"] == "dcterms:references" for r in refs)
    assert refs[2]["text"] == "UNCLOS"


def test_the_court_s_own_short_form_counts():
    """The Court names an instrument in full once and by its short form after
    that, so matching the title alone would miss most of a judgment."""
    refs = treaties.references("Israel is bound by the Genocide Convention.")
    assert [r["uri"] for r in refs] == ["https://lagen.nu/ext/untc/I-1021"]


def test_the_bare_word_convention_cites_nothing():
    """"the Convention" names whichever instrument the decision is about. A
    short form has to carry the instrument's own subject word, or every treaty
    would be cited by every decision."""
    assert treaties.references("The Court considers that the Convention "
                               "applies to the present dispute.") == []
    assert treaties.references("The Protocol entered into force in 1967.") == []


def test_one_reference_per_instrument():
    """These are document-level relations, not the literal spans a body walk
    collects -- and the Court names the instrument it is deciding under on
    nearly every page."""
    refs = treaties.references(
        "the Genocide Convention " * 40 + " and the Genocide Convention again")
    assert len(refs) == 1


def test_references_reach_the_artifact():
    decision = Decision(
        basefile="192-20240126-ORD-01-00", case="192",
        case_name="Application of the Genocide Convention in the Gaza Strip",
        kind="order", title="Order of 26 January 2024", date="2024-01-26",
        references=treaties.references("Article II of the Genocide Convention"),
        body=[Block("stycke", "Text.", number="1")])
    art = decision.to_artifact()
    assert [r["uri"] for r in art["references"]] == \
        ["https://lagen.nu/ext/untc/I-1021"]
    # and the catalog's generic reference contract picks them up as links
    assert [run["uri"] for _anchor, _page, run in catalog.artifact_links(art)] == \
        ["https://lagen.nu/ext/untc/I-1021"]


# --------------------------------------------------------------------------
# layout + catalog wiring
# --------------------------------------------------------------------------

def test_icj_layout_round_trips_and_catalog_row():
    uri = "https://lagen.nu/ext/icj/070-19860627-JUD-01-00"
    assert layout.page_url(uri) == "/icj/070-19860627-JUD-01-00"
    assert layout.page_relpath(uri) == "icj/070_19860627_JUD_01_00.html"
    assert str(layout.url_to_relpath("/icj/070-19860627-JUD-01-00")) == \
        "icj/070_19860627_JUD_01_00.html"
    assert "icj" in facets.sources()
    art = Decision(basefile="070-19860627-JUD-01-00", case="070",
                   case_name="Military and Paramilitary Activities in and "
                             "against Nicaragua",
                   kind="judgment", title="Judgment of 27 June 1986",
                   date="1986-06-27").to_artifact()
    row = catalog.icj_document(art, "artifact/icj/070-19860627-JUD-01-00.json")
    assert row[:3] == (uri, "icj", "dom")
    assert row[3] == "ICJ 70 (Judgment, 1986-06-27)"


# --------------------------------------------------------------------------
# the OCR banner: said only where the text really was read off a scan
# --------------------------------------------------------------------------

def test_the_scan_banner_follows_the_repair_count():
    """A nonzero repair count is the evidence that this text was read off the
    printed Reports, not a guess from the decision's date -- the July 2004 Wall
    opinion is a scan and the December 2004 judgment in the same volume is
    typeset."""
    banner = str(BANNERS.icj_ocr_banner(
        "https://www.icj-cij.org/sites/default/files/case-related/70/"
        "070-19860627-JUD-01-00-EN.pdf"))
    assert "Inläst text" in banner
    assert "I.C.J. Reports" in banner
    assert "den tryckta versionen är den officiella" in banner
    assert "070-19860627-JUD-01-00-EN.pdf" in banner
    # and the renderer asks for it where the repair count says the text is a
    # scan. Not at *any* repair: 27 of the 117 typeset decisions repair 1 to 8
    # words (a real typo, a ligature the text layer carries), and telling their
    # readers the text is machine-read would be false.
    assert icj_render.scan_banner({"ocrRepairs": 434, "pdfUrl": None})
    assert icj_render.scan_banner({"ocrRepairs": 19, "pdfUrl": None})
    assert not icj_render.scan_banner({"ocrRepairs": 2, "pdfUrl": None})
    assert not icj_render.scan_banner({"ocrRepairs": 0, "pdfUrl": None})


# --------------------------------------------------------------------------
# folkrätt landing
# --------------------------------------------------------------------------

def _stub(basefile, case, name, kind, date):
    return {"uri": decision_uri(basefile), "docnumber": basefile, "doctype": kind,
            "type": "avgorande", "court": "icj",
            "identifier": "ICJ %s (%s, %s)" % (case, kind, date),
            "title": name, "avgorandedatum": date,
            "metadata": {"caseNumber": case, "decisionType": kind},
            "references": [], "structure": []}


def test_folkratt_lists_icj_grouped_by_decision_kind(tmp_path):
    arts = [
        _stub("070-19860627-JUD-01-00", "70",
              "Military and Paramilitary Activities in and against Nicaragua",
              "dom", "1986-06-27"),
        _stub("131-20040709-ADV-01-00", "131",
              "Legal Consequences of the Construction of a Wall",
              "rådgivande yttrande", "2004-07-09"),
        _stub("192-20240126-ORD-01-00", "192",
              "Application of the Genocide Convention in the Gaza Strip",
              "beslut", "2024-01-26"),
    ]
    paths = []
    for art in arts:
        path = tmp_path / (art["docnumber"] + ".json")
        path.write_text(json.dumps(art, ensure_ascii=False))
        paths.append(path)
    database = str(tmp_path / "catalog.sqlite")
    catalog.rebuild(database, "icj", paths)
    html = render.render_folkratt(catalog.connect(database))

    assert "Internationella domstolen (ICJ)" in html
    assert "Rådgivande yttranden" in html
    assert "Interimistiska beslut" in html
    assert html.index("Domar") < html.index("Rådgivande yttranden")
    assert 'href="/icj/070-19860627-JUD-01-00"' in html
    assert "ICJ-avgöranden" in html                   # the shared Dokumenttyp bucket
