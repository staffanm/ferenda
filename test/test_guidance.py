"""edpb vertical (Europeiska dataskyddsstyrelsens riktlinjer och
rekommendationer): identity, the sitemap index, the document-page reader, the
number rules, the WP29 cover, the numbered-punkt break rule, artifact
projection, the citation grammar and the layout/catalog/facets/render wiring.

Hermetic: the fixtures under ``test/files/edpb/`` are trimmed captures of the
live 2026 pages and recorded `Para` streams over the real PDFs, so the rules are
exercised against what the EDPB actually publishes without network or poppler.
"""

import io
import json
import zipfile
from pathlib import Path

import pytest
from lxml import etree

from accommodanda.guidance import (
    acer_download,
    easa_download,
    edpb_download,
    euipo_download,
    eurlex_download,
)
from accommodanda.guidance import download as guidance_download
from accommodanda.guidance import issuers as guidance_issuers
from accommodanda.guidance import parse as edpb_parse
from accommodanda.guidance import render as guidance_render
from accommodanda.guidance.edpb_data import HBDI, WP29, WP29_BY_SLUG
from accommodanda.guidance.edpb_download import HARVESTED
from accommodanda.guidance.issuers import (
    EASA,
    EDPB,
    EUIPO,
    LOPNUMMER_FORST,
)
from accommodanda.guidance.issuers import number_slug as _number_slug
from accommodanda.guidance.model import (
    Block,
    Fotnot,
    Vagledning,
    vagledning_identifier,
    vagledning_uri,
)
from accommodanda.lib import formex as lib_formex
from accommodanda.lib import lagrum

# the EDPB's series data now lives on its registry entry, and its numbering
# rule takes the component order as an argument (the EBA writes the year first)
KODER = EDPB.koder
REGISTRY = EDPB.series


def number_slug(number):
    return _number_slug(number, LOPNUMMER_FORST)
from accommodanda.lib import catalog, facets, labels, layout, render, tpl
from accommodanda.lib.lagrum import (
    EULAGSTIFTNING,
    VAGLEDNING,
    LagrumParser,
    vagledning_slug,
)
from accommodanda.lib.pdftext import Para, Run, line_from_runs, page_paragraphs

FIXTURES = Path(__file__).parent / "files" / "edpb"


def fixture(name):
    return (FIXTURES / name).read_text("utf-8")


def paras(name):
    return [Para(text=p["text"], bold=p["bold"], size=p["size"])
            for p in json.loads(fixture(name))]


def lines(name):
    """A recorded page stream: ``[(pageno, [Line])]``, every line rebuilt from
    its runs through the constructor `lib.pdftext` itself uses, so its text,
    geometry and style flags describe the same run set the real conversion
    produced -- which is what the punkt column is read off."""
    return [(page["page"],
             [line_from_runs([Run(**run) for run in line["runs"]], line["top"])
              for line in page["lines"]])
            for page in json.loads(fixture(name))]


# --------------------------------------------------------------------------
# identity -- the EDPB's own number names the document
# --------------------------------------------------------------------------

@pytest.mark.parametrize("serie,nummer,uri,identifier", [
    ("riktlinjer", "05/2020", "https://lagen.nu/guidance/edpb/riktlinjer/05-2020",
     "Riktlinjer 05/2020"),
    # the EDPB pads the löpnummer in some years and not others; one document has
    # one address however it was written
    ("riktlinjer", "5/2020", "https://lagen.nu/guidance/edpb/riktlinjer/05-2020",
     "Riktlinjer 5/2020"),
    ("rekommendationer", "01/2019",
     "https://lagen.nu/guidance/edpb/rekommendationer/01-2019", "Rekommendation 01/2019"),
    ("wp", "248", "https://lagen.nu/guidance/edpb/wp/248", "WP 248"),
])
def test_identity(serie, nummer, uri, identifier):
    assert vagledning_uri("edpb", serie, nummer) == uri
    assert vagledning_identifier("edpb", serie, nummer) == identifier


def test_number_slug_normalises_the_padding_only():
    assert number_slug("5/2020") == number_slug("05/2020") == "05-2020"
    assert number_slug("1/2018") == "01-2018"


def test_the_citation_engine_mints_the_same_address_the_document_gets():
    """The one invariant that has to hold across the lib/vertical boundary: the
    URI a citation to "Riktlinjer 05/2020" mints and the URI that document is
    published under are the same string. lagrum cannot import the vertical, so
    the two slug rules are held together here."""
    for nummer in ("05/2020", "5/2020", "1/2018", "10/2020", "02/2025"):
        assert vagledning_slug(nummer) == number_slug(nummer)


def test_registry_is_one_entry_per_series():
    assert len(REGISTRY) == len(KODER) == 3
    assert HARVESTED == ("riktlinjer", "rekommendationer")   # wp is closed
    for series in REGISTRY:
        assert "%s" in series.identifier and series.label


def test_wp29_registry_is_the_closed_endorsed_set():
    """Endorsement 1/2018 endorsed sixteen artikel 29-gruppen documents, and
    all sixteen are carried."""
    assert len(WP29) == len(WP29_BY_SLUG) == 16
    assert sorted(WP29_BY_SLUG) == [
        "242", "243", "244", "248", "250", "251", "253", "254", "256", "257",
        "259", "260", "263", "264", "265", "artikel-30-5"]
    for wp in WP29:
        assert wp.item.isdigit() and wp.page.startswith("https://www.edpb.")


def test_the_two_bcr_forms_name_the_copy_they_are_taken_from():
    """The working party issued the two BCR application forms as Word forms, so
    every PDF of them is a conversion and none is the newsroom's. Those entries
    carry the copy explicitly; every other one is resolved through its newsroom
    item, and must not name a document of its own."""
    forms = [wp for wp in WP29 if wp.document]
    assert [wp.slug for wp in forms] == ["264", "265"]
    for wp in forms:
        assert wp.document.startswith(HBDI) and wp.document.endswith(".pdf")


def test_the_one_endorsed_document_with_no_wp_number_is_named_and_dated_here():
    """The ställningstagande on artikel 30.5 sets no cover: it opens with its
    title in the running text and dates itself nowhere, so the registry carries
    both -- and it is cited by name, having no number to be cited by."""
    wp = WP29_BY_SLUG["artikel-30-5"]
    assert wp.number is None and wp.titel and wp.antagen == "2018-04-19"
    assert vagledning_identifier("edpb", "wp", "artikel-30-5",
                                 wp.citation) == wp.citation
    # every other one is cited by its number, and states its own title
    assert vagledning_identifier("edpb", "wp", "248") == "WP 248"
    assert not any(wp.titel or wp.antagen for wp in WP29 if wp.number)


# --------------------------------------------------------------------------
# the sitemap index
# --------------------------------------------------------------------------

def test_sitemap_groups_document_pages_by_type_slug_and_language():
    pages = edpb_download.sitemap_document_pages([fixture("sitemap-fragment.xml")])
    consent = pages[("guideline", "guidelines-052020-on-consent-under-regulation-2016679")]
    assert set(consent) == {"en", "sv"}
    assert consent["sv"].endswith("_sv")
    # a document published in one language only is grouped just the same
    assert set(pages[("guideline", "data-protection-officer")]) == {"en"}
    # every other document type on the site is carried through as its own key,
    # so a series' harvest can select on it without re-reading the sitemap
    assert ("opinion-of-the-board-art-64", "opinion-212026") in pages
    # and pages that are not documents at all never enter
    assert not any(slug == "registers" for _, slug in pages)


def test_sitemap_with_no_document_pages_is_a_broken_index_not_an_empty_corpus():
    with pytest.raises(AssertionError):
        edpb_download.sitemap_document_pages(["<urlset></urlset>"])


# --------------------------------------------------------------------------
# the document page
# --------------------------------------------------------------------------

def test_page_reads_the_swedish_document_and_its_metadata():
    page = edpb_download.parse_page(
        fixture("guideline-page-sv.html"),
        "https://www.edpb.europa.eu/documents/guideline/x_sv", "sv")
    assert page["titel"].startswith("Riktlinjer 05/2020 om samtycke")
    assert page["antagen"] == "2020-05-04"
    assert page["document"].endswith("_sv.pdf")


def test_page_picks_the_document_in_its_own_language_not_the_summary_beside_it():
    page = edpb_download.parse_page(
        fixture("guideline-page-en.html"),
        "https://www.edpb.europa.eu/documents/guideline/x_en", "en")
    # the page also carries a track-changes DOCX and a consultation report; the
    # document is the first *PDF* named for the page's language
    assert page["document"].endswith("edpb_guidelines_202502_blockchain_v2_en.pdf")
    assert page["version"] == "Final version"
    assert page["konsultation_url"].startswith(
        "https://www.edpb.europa.eu/public-consultations/")
    assert page["amnesord"] == ["Technology", "Basic principles"]


def test_a_wp29_stub_page_publishes_no_document():
    """The endorsed WP29 pages that exist carry no file at all -- which is how
    they are told apart from a real document page, and why `wp29_sync` goes to
    the Commission newsroom instead."""
    page = edpb_download.parse_page(
        fixture("wp29-stub-page-sv.html"),
        "https://www.edpb.europa.eu/documents/guideline/data-protection-officer_sv",
        "sv")
    assert page["document"] is None
    # and its title is one the EDPB has got wrong elsewhere in this set, so
    # nothing downstream may trust it
    assert page["titel"] == "Dataskyddsombud"


# --------------------------------------------------------------------------
# the series number
# --------------------------------------------------------------------------

@pytest.mark.parametrize("titel,filename,expected", [
    # normally the document names itself
    ("Guidelines 05/2020 on consent under Regulation 2016/679",
     "edpb_guidelines_202005_consent_en.pdf", "05/2020"),
    # ... and where it has stopped doing so, the file name still carries it,
    # written either way round
    ("Guidelines on processing of personal data through blockchain technologies",
     "edpb_guidelines_202502_blockchain_v2_en.pdf", "02/2025"),
    ("X", "edpb_guidelines_012021_pdbnotification_adopted_en.pdf", "01/2021"),
    ("X", "edpb_guidelines_201904_dataprotection_by_design_v2.0_sv.pdf", "04/2019"),
    # a six-digit token that is no year in either reading is not a number
    ("X", "edpb_guidelines_991199_whatever_en.pdf", None),
    ("X", "edpb_something_en.pdf", None),
])
def test_series_number(titel, filename, expected):
    assert edpb_download.series_number(
        titel, "https://www.edpb.europa.eu/system/files/" + filename) == expected


# --------------------------------------------------------------------------
# the WP29 language ZIP
# --------------------------------------------------------------------------

def _zip(names):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name in names:
            z.writestr(name, b"%PDF-1.4 " + name.encode())
    return buf.getvalue()


def test_swedish_member_takes_the_document_and_never_its_annex():
    # most run the revision straight on, some space it
    assert edpb_download.swedish_member(
        _zip(["wp243rev01_en.pdf", "wp243rev01_sv.pdf"]), "243")
    assert edpb_download.swedish_member(
        _zip(["wp248 rev.01_sv.pdf"]), "248")
    # and WP259's archive names Swedish by the *country* code instead
    assert edpb_download.swedish_member(
        _zip(["wp259 rev 0.1_DE.pdf", "wp259 rev 0.1_SE.pdf"]), "259")
    # the annex ships in a ZIP of its own whose members are named the same way
    assert edpb_download.swedish_member(
        _zip(["wp242rev01_annex_sv.pdf"]), "242") is None
    # and an archive with no Swedish version at all says so
    assert edpb_download.swedish_member(_zip(["wp243rev01_de.pdf"]), "243") is None


# --------------------------------------------------------------------------
# the WP29 cover -- the one place these seven state their identity in Swedish
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name,number,titel,antagen", [
    # the language mark and the number arrive on one line here ...
    ("cover-wp242-sv.json", "242", "Riktlinjer om rätten till dataportabilitet",
     "2017-04-05"),
    # ... on two here, and the adoption dates likewise
    ("cover-wp248-sv.json", "248",
     "Riktlinjer om konsekvensbedömning avseende dataskydd och fastställande av "
     "huruvida behandlingen ”sannolikt leder till en hög risk” i den mening som "
     "avses i förordning 2016/679", "2017-10-04"),
    # ... and this one names the working party again between the two
    ("cover-wp260-sv.json", "260",
     "Riktlinjer om öppenhet enligt förordning (EU) 2016/679", "2018-04-11"),
    # a *working document* is antaget/antagen, not antagna: singular through
    # the adoption line and the revision line both
    ("cover-wp254-sv.json", "254", "Referensram för adekvat skyddsnivå",
     "2018-02-06"),
    # ... and this one runs the working party's name straight into the title
    # rather than setting it on a line of its own
    ("cover-wp259-sv.json", "259",
     "Riktlinjer om samtycke enligt förordning (EU) 2016/679", "2018-04-10"),
])
def test_wp_cover(name, number, titel, antagen):
    cover = edpb_parse.wp_cover(paras(name), WP29_BY_SLUG[number])
    assert cover["titel"] == titel
    # the *last* of the adoption dates: it is the revision the EDPB endorsed
    assert cover["antagen"] == antagen


def test_wp_cover_rejects_a_document_that_is_not_the_one_expected():
    """A wrong newsroom item in the registry must stop the parse, not file
    another document's text under this number."""
    with pytest.raises(AssertionError, match="serves another document"):
        edpb_parse.wp_cover(paras("cover-wp248-sv.json"), WP29_BY_SLUG["250"])


def test_wp_cover_of_the_one_document_that_sets_none_comes_from_the_registry():
    """The ställningstagande on artikel 30.5 has no cover to read, so its title
    and date come from the registry -- but the document still has to be the one
    the registry describes, which it states in its opening prose."""
    cover = edpb_parse.wp_cover(paras("cover-artikel-30-5-en.json"),
                                WP29_BY_SLUG["artikel-30-5"])
    assert cover["titel"].startswith("Position Paper on the derogations")
    assert cover["antagen"] == "2018-04-19"


def test_wp_cover_rejects_an_unnumbered_document_that_is_not_the_one_expected():
    """The identity check is what makes a registry title safe: without a WP
    number to match, any PDF the source served would otherwise be filed under
    that URI carrying the registry's title and date."""
    # ValueError, not AssertionError: this is the one check here whose absence
    # would let the parse succeed with the wrong text, so it must survive -O
    # (rule:errors-drive-retry-use-raise)
    with pytest.raises(ValueError, match="does not open with the title"):
        edpb_parse.wp_cover(paras("cover-wp248-sv.json"),
                            WP29_BY_SLUG["artikel-30-5"])


def test_the_identity_check_is_keyed_on_the_missing_number_not_a_present_title():
    """Regression guard: keying the no-cover branch on `wp.titel` would mean
    that writing a title into any other entry silently turns off the check that
    the document is the one the registry names -- which is the whole reason a
    conversion published by someone other than the issuer is trusted here."""
    numbered = [wp for wp in WP29 if wp.number]
    assert numbered and all(wp.titel is None for wp in numbered)
    assert [wp.slug for wp in WP29 if wp.number is None] == ["artikel-30-5"]


# --------------------------------------------------------------------------
# the numbered punkt
# --------------------------------------------------------------------------

class _Line:
    def __init__(self, text, top, runs=()):
        self.text, self.top, self.runs = text, top, runs


def test_numbered_breaks_follow_the_running_sequence():
    """A number opens a paragraph only when it is the one the document is due,
    so a year, an article number or a list item cannot start one."""
    pages = [(1, [_Line("1. Inledningsvis gäller följande.", 100),
                  _Line("2016. var året då direktivet upphörde", 200),
                  _Line("2. Vidare gäller detta.", 300)]),
             (2, [_Line("4. Detta hoppar över ett nummer", 100),
                  _Line("3. Och detta är det som stod på tur.", 200)])]
    breaks = edpb_parse.numbered_breaks(pages, None)
    assert breaks[1] == {100, 300}
    assert breaks[2] == {200}       # 4 came too early and is not a break


def test_a_document_that_numbers_nothing_yields_no_breaks():
    pages = [(1, [_Line("Riktlinjer om öppenhet", 100),
                  _Line("Denna vägledning gäller.", 200)])]
    assert edpb_parse.numbered_breaks(pages, None) == {1: set()}


def test_the_number_column_is_what_keeps_two_paragraphs_apart():
    """Regression: paragraph 17 of Riktlinjer 05/2020 sits half a line below
    paragraph 16 and arrived glued to the end of it, losing an anchor and the
    boundary the citation scan needs."""
    blocks = edpb_parse.join_continuations(
        [("stycke", "16. I skäl 43 anges det tydligt att …", 0),
         ("stycke", "17. Utan att det påverkar dessa överväganden …", 0)], False)
    assert [b[3] for b in blocks] == ["16", "17"]


def test_a_continuation_rejoins_the_punkt_it_continues():
    blocks = edpb_parse.join_continuations(
        [("stycke", "11. Samtycke innebär varje slag av", 0),
         ("stycke", "frivillig, specifik och informerad viljeyttring.", 0),
         ("rubrik", "3.1 Fritt/frivilligt samtycke", 2),
         ("stycke", "En rubrik avslutar sammanslagningen.", 0)], False)
    assert [b[1] for b in blocks[:1]] == [
        "11. Samtycke innebär varje slag av frivillig, specifik och "
        "informerad viljeyttring."]
    assert blocks[-1][3] is None      # what follows a heading starts something


def test_the_masthead_is_matched_in_title_case_but_only_on_its_own_line():
    """WP 264 sets the running header as "ARTICLE 29 Data Protection Working
    Party" rather than in caps, which left it standing as the document's first
    block — and, behind it, the cover's copy of the title that
    `drop_repeated_title` then never reached.

    The guard rail is the second half. This pattern removes to the end of the
    line, and the group names *itself* in running prose hundreds of times
    across the corpus, so matching the name case-insensitively wherever it
    stands would delete body text wholesale. It is anchored to a line of its
    own, and the Swedish name is left case-sensitive."""
    assert edpb_parse.RE_MASTHEAD.search("ARTICLE 29 Data Protection Working Party")
    assert edpb_parse.RE_MASTHEAD.search("ARTICLE 29 DATA PROTECTION WORKING PARTY")
    # prose naming the group must survive untouched, in either language
    for prose in ("I följande underavsnitt ger artikel 29-arbetsgruppen "
                  "riktlinjer om de kriterier som används i artikel 37.",
                  "The Article 29 Data Protection Working Party Guidelines on "
                  "transparency cover transparency in more detail."):
        assert edpb_parse.RE_MASTHEAD.sub("", prose) == prose


def test_a_section_numbered_document_joins_nothing_and_anchors_nothing():
    """Regression: WP 250 numbered its *sections* "1." and "2." and set plain
    prose under them, so each section number swallowed every paragraph until the
    next -- the document arrived as a single 46,000-character block. Below
    `PUNKT_COVERAGE_MIN` the numbers are section numbers, not punkter."""
    blocks = edpb_parse.join_continuations(
        [("stycke", "1. Anmälan till tillsynsmyndigheten", 0),
         *[("stycke", "Ett stycke som inte är numrerat alls.", 0)
           for _ in range(9)],
         ("stycke", "2. Information till den registrerade", 0)], False)
    assert len(blocks) == 11                    # nothing was joined
    assert all(b[3] is None for b in blocks)    # and nothing anchors a punkt


def test_body_reads_the_guideline_into_numbered_punkter():
    body = edpb_parse.body(paras("paras-riktlinjer-05-2020-sv.json"),
                           "Riktlinjer 05/2020 om samtycke enligt förordning "
                           "(EU) 2016/679", False)
    punkter = [b.punkt for b in body if b.kind == "stycke" and b.punkt]
    assert punkter[:7] == ["1", "2", "3", "4", "5", "6", "7"]
    # the version line, the version history and the adoption date are the
    # record's fields, not the body's text
    assert not any("Versionshistorik" in b.text for b in body)
    assert not any(b.text.startswith("Antagna den") for b in body)
    # the cover's copy of the title goes: the page carries it as the h1
    assert not body[0].text.startswith("Riktlinjer 05/2020 om samtycke")
    # the section headings survive as headings
    assert any(b.kind == "rubrik" and b.text == "1 INLEDNING" for b in body)


# --------------------------------------------------------------------------
# the punkt whose number carries no period, set bare in the margin
# --------------------------------------------------------------------------

BLOCKCHAIN = "Guidelines 02/2025 on processing of personal data through " \
             "blockchain technologies"


def bare_body(name, titel):
    """`parse._paragraphs` and `parse.body` over a recorded page stream."""
    pages = lines(name)
    margin = edpb_parse.punkt_margin(pages)
    breaks = edpb_parse.numbered_breaks(pages, margin)
    return edpb_parse.body(
        [p for pageno, page in pages
         for p in page_paragraphs(page, None, pageno,
                                  force_break_tops=breaks[pageno])],
        titel, margin is not None)


def test_the_number_column_is_read_off_the_lines_that_set_the_number_apart():
    """Riktlinjer 02/2025 hangs its punkt numbers in the margin at x=66 and sets
    the prose at the body's 108."""
    pages = lines("lines-riktlinjer-02-2025-en.json")
    assert edpb_parse.body_column(pages) == 108
    assert edpb_parse.punkt_margin(pages) == 66


def test_a_bare_punkt_number_breaks_the_paragraph_the_period_would_have():
    """Regression: riktlinjer 02/2025 prints "1", not "1.", so `RE_PUNKT` matched
    none of its 137 punkter -- punkter 1-3 arrived as one block with one
    positional id, and a citation to punkt 2 had nothing to land on."""
    body = bare_body("lines-riktlinjer-02-2025-en.json", BLOCKCHAIN)
    punkter = [b.punkt for b in body if b.kind == "stycke" and b.punkt]
    assert punkter == [str(n) for n in range(1, 12)]
    assert [b.text[:24] for b in body if b.punkt in ("1", "2", "3")] == [
        "1 The concept commonly r", "2 Blockchain – or, in a ",
        "3 In practice, blockchai"]
    # poppler emits the wider two-digit number and the prose beside it as one
    # fragment ("10  Finally, the use of …", starting at the margin's 66), which
    # is the second half of the same rule
    assert next(b.text for b in body if b.punkt == "10").startswith(
        "10 Finally, the use of decentralised technologies")


def test_a_bare_number_out_of_sequence_starts_no_punkt():
    """The running sequence guards the bare form exactly as it guards the dotted
    one: a number the document is not due starts nothing, margin column or not.
    Without it "2016 var året …" would open a punkt."""
    def line(number, text, top):
        return line_from_runs(
            [Run(66, 80, "%s " % number, False, False, 17, "arial"),
             Run(108, 790, text, False, False, 17, "arial")], top)
    pages = [(1, [line(1, "The concept commonly referred to by the term …", 100),
                  line(7, "Member States shall ensure that …", 200),
                  line(2, "Blockchain – or, in a more general manner …", 300)])]
    assert edpb_parse.numbered_breaks(pages, 66) == {1: {100, 300}}


def test_a_bare_number_at_the_body_margin_is_not_a_punkt():
    """The running footer of riktlinjer 02/2025 is "4 | Adopted", set at the body
    margin rather than out in the number column -- and it is the *document's*
    body column that says so, since on the version-history page the commonest
    line start is the table's and leaves the footer looking like a margin
    number."""
    footer = line_from_runs([Run(108, 181, "4 | Adopted ", False, False, 14,
                                "arial")], 1191)
    assert edpb_parse.line_punkt(footer, 66) is None
    assert edpb_parse.line_punkt(footer, 108) == "4"     # in the column it would


def test_a_numbered_table_column_teaches_no_number_column():
    """Riktlinjer 02/2022's annex sets a row number at x=59 with the row's first
    cell at 91, which reads exactly like a margin number -- but its prose does
    not begin at the body column, so the document demonstrates no punkt column
    and its four rows pass for nothing."""
    row = line_from_runs(
        [Run(59, 67, "2", False, False, 17, "arial"),
         Run(91, 210, "Artikel 60.2 – Den", False, False, 17, "arial"),
         Run(291, 322, "Vem", False, False, 17, "arial")], 300)
    body = line_from_runs([Run(106, 700, "Denna vägledning gäller.", False,
                              False, 17, "arial")], 340)
    assert edpb_parse.punkt_margin([(63, [row, body, body])]) is None


def test_a_bilaga_that_numbers_its_own_list_starts_no_second_run_of_punkter():
    """Regression: riktlinjer 04/2020 closes with a nine-item numbered list, and
    read as text those items are punkt 1-9 all over again -- every following
    paragraph joined onto the wrong punkt. A bare number counts only where it
    climbs past the last one."""
    blocks = edpb_parse.join_continuations(
        [("stycke", "47 Den personuppgiftsansvarige måste …", 0),
         ("stycke", "48 Världen befinner sig mitt uppe i …", 0),
         ("stycke", "49 Styrelsen understryker att man inte …", 0),
         ("rubrik", "BILAGA – Vägledande checklista", 2),
         ("stycke", "1 Ansökan om personuppgiftsansvar …", 0),
         ("stycke", "2 Kontaktspårningsappen bör vara …", 0)], True)
    assert [b[3] for b in blocks] == ["47", "48", "49", None, None, None]


# --------------------------------------------------------------------------
# the title, where the EDPB's page states it in the wrong language
# --------------------------------------------------------------------------

def test_an_english_page_title_on_a_swedish_document_is_taken_from_the_cover():
    record = {"sprak": "sv",
              "titel": "Guidelines 4/2019 on Article 25 Data Protection by "
                       "Design and by Default"}
    cover = [Para(text="Riktlinjer 4/2019 om artikel 25", size=27),
             Para(text="Inbyggt dataskydd och dataskydd som standard", size=27),
             Para(text="Version 2.0", size=24),
             Para(text="Antagna den 20 oktober 2020", size=24)]
    assert edpb_parse.titled(record, cover) == (
        "Riktlinjer 4/2019 om artikel 25 Inbyggt dataskydd och dataskydd "
        "som standard")


def test_a_swedish_page_title_is_left_alone():
    """Everywhere else the page's title is the better text -- clean HTML rather
    than PDF extraction, which glues hyphenated line breaks and truncates."""
    record = {"sprak": "sv", "titel": "Riktlinjer 03/2020 om behandling av "
                                      "uppgifter om hälsa"}
    cover = [Para(text="Riktlinjer 03/2020 om behandling av uppgifter om "
                       "hälsa för covid-19utbrott", size=27),
             Para(text="Antagna den 21 april 2020", size=24)]
    assert edpb_parse.titled(record, cover) == record["titel"]


def test_an_english_document_keeps_its_english_title():
    record = {"sprak": "en", "titel": "Guidelines 02/2024 on Article 48 GDPR"}
    assert edpb_parse.titled(record, []) == record["titel"]


# --------------------------------------------------------------------------
# artifact projection
# --------------------------------------------------------------------------

class _NoRefs:
    def parse_text(self, text, context=None):
        return []


def _artifact(**kwargs):
    fields = dict(utgivare="edpb", serie="riktlinjer", nummer="05/2020",
                  titel="Riktlinjer 05/2020 om samtycke", antagen="2020-05-04",
                  body=[Block("rubrik", "1 INLEDNING", 2),
                        Block("stycke", "1. Inledningsvis gäller detta.",
                              punkt="1"),
                        Block("stycke", "Ett stycke utan eget nummer.")])
    return Vagledning(**{**fields, **kwargs}).to_artifact(_NoRefs())


def test_artifact_anchors_a_numbered_punkt_on_its_own_number():
    art = _artifact()
    assert art["uri"] == "https://lagen.nu/guidance/edpb/riktlinjer/05-2020"
    assert art["identifier"] == "Riktlinjer 05/2020"
    assert art["serie"] == "riktlinjer"
    ids = [n.get("id") for n in art["structure"] if n["type"] == "stycke"]
    assert ids == ["punkt1", "S2"]
    assert art["metadata"]["publisher"] == "Europeiska dataskyddsstyrelsen"
    assert art["metadata"]["sprak"] == "sv"


def test_artifact_carries_the_version_and_the_language():
    art = _artifact(version="Version 2.0", sprak="en",
                    konsultation_url="https://www.edpb.europa.eu/x")
    assert art["metadata"]["version"] == "Version 2.0"
    assert art["metadata"]["sprak"] == "en"
    assert art["metadata"]["konsultation"] == "https://www.edpb.europa.eu/x"


def test_a_wp29_artifact_is_published_by_the_working_party():
    art = _artifact(serie="wp", nummer="248", titel="Riktlinjer om …",
                    revision="rev.01")
    assert art["uri"] == "https://lagen.nu/guidance/edpb/wp/248"
    assert art["identifier"] == "WP 248"
    assert art["metadata"]["publisher"] == "Artikel 29-gruppen"
    assert art["metadata"]["revision"] == "rev.01"


# --------------------------------------------------------------------------
# the citation grammar
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def refparser():
    return LagrumParser({}, basefile="guidance",
                        parse_types=[EULAGSTIFTNING, VAGLEDNING])


@pytest.mark.parametrize("text,expected", [
    ("Se riktlinjer 05/2020 om samtycke.",
     ["https://lagen.nu/guidance/edpb/riktlinjer/05-2020"]),
    # the definite and singular forms Swedish prose writes just as often
    ("enligt riktlinjerna 8/2022 punkt 12",
     ["https://lagen.nu/guidance/edpb/riktlinjer/08-2022"]),
    ("riktlinjen 4/2019 om inbyggt dataskydd",
     ["https://lagen.nu/guidance/edpb/riktlinjer/04-2019"]),
    # the EDPB itself alternates singular and plural for its recommendations
    ("Rekommendation 01/2020 om åtgärder",
     ["https://lagen.nu/guidance/edpb/rekommendationer/01-2020"]),
    ("Rekommendationer 02/2020 om garantierna",
     ["https://lagen.nu/guidance/edpb/rekommendationer/02-2020"]),
    # the artikel 29-gruppens own numbering, spaced or not, with or without rev
    ("artikel 29-gruppens riktlinjer om dataskyddsombud (WP 243)",
     ["https://lagen.nu/guidance/edpb/wp/243"]),
    ("se WP248 rev.01 avsnitt III", ["https://lagen.nu/guidance/edpb/wp/248"]),
    # a padded and an unpadded citation to one document are one address
    ("riktlinjer 1/2018 och riktlinjer 01/2018",
     ["https://lagen.nu/guidance/edpb/riktlinjer/01-2018",
      "https://lagen.nu/guidance/edpb/riktlinjer/01-2018"]),
    # "WP29" names the group, not a document
    ("Artikel 29-gruppen (WP29) antog yttrande 15/2011", []),
    # and a bare number pair in prose is not a citation
    ("beslutet meddelades 2019 och 12/2020 saknar sammanhang", []),
])
def test_vagledning_grammar(refparser, text, expected):
    refparser.reset()
    assert [r.uri for r in refparser.parse_text(text, context={})] == expected


def test_a_guidance_reference_the_site_does_not_host_still_parses(refparser):
    """The working party numbered its yttranden in the same series (WP 187, WP
    259) and a guideline's prose is full of them. Those mint a lagen.nu uri like
    any other and render as plain text until the document exists."""
    refparser.reset()
    assert [r.uri for r in refparser.parse_text("WP259 rev.01", context={})] \
        == ["https://lagen.nu/guidance/edpb/wp/259"]


def test_artikel_29_gruppen_is_a_body_not_a_reference_to_artikel_29(refparser):
    """Regression: "artikel 29-gruppen" is named in every data-protection
    document written since 1995, and reading it as a reference sent 13 of one
    guideline's links to artikel 29 in the GDPR -- which repealed the directive
    that established the group and has no such body in it."""
    refparser.reset()
    assert refparser.parse_text("artikel 29-gruppens riktlinjer om samtycke",
                                context={}) == []
    refparser.reset()
    assert refparser.parse_text("artikel 29-arbetsgruppen för uppgiftsskydd",
                                context={}) == []
    # while a real article reference still links
    refparser.reset()
    assert [r.uri for r in refparser.parse_text(
        "artikel 29 i förordning (EU) 2016/679", context={})] \
        == ["https://lagen.nu/ext/celex/32016R0679#29"]


# --------------------------------------------------------------------------
# the corpus-wide wiring
# --------------------------------------------------------------------------

def test_layout_files_a_document_under_its_series():
    assert layout.relpath("guidance", "edpb/riktlinjer/05-2020") == \
        Path("edpb/riktlinjer/05-2020")
    assert layout.relpath("guidance", "edpb/wp/248") == \
        Path("edpb/wp/248")
    assert layout.SOURCE_DIR["guidance"] == "guidance"


def test_catalog_rows_carry_the_series_as_the_kind():
    art = _artifact()
    uri, source, kind, label, title, path = catalog.document_row(
        art, "x.json", "guidance")
    assert (source, kind, label) == ("guidance", "riktlinjer",
                                     "Riktlinjer 05/2020")
    assert title == "Riktlinjer 05/2020 om samtycke"


def test_labels_name_an_english_only_document_as_one():
    swedish = labels.document_labels("guidance", _artifact())
    assert swedish.descriptive_label == "Riktlinjer 05/2020"
    english = labels.document_labels("guidance", _artifact(sprak="en"))
    assert english.descriptive_label == "Riktlinjer 05/2020 (engelsk version)"


def test_the_browse_scheme_is_utgivare_then_series_then_year():
    """Utgivare first, because this source carries several bodies and
    "Riktlinjer" names a different series under each."""
    levels = facets.SCHEMES["guidance"]
    assert [level.name for level in levels] == ["Utgivare", "Serie", "År"]


def test_a_year_selector_appears_only_once_a_body_publishes_over_a_hundred():
    """The avg/rs/föreskrift rule: a by-year axis earns its place when a body's
    own output is too long to read in one list, and under that it is only an
    extra click. The gate is the utgivare's total, not the series' -- what the
    reader is deciding is whether *this body's* output needs splitting."""
    levels = facets.SCHEMES["guidance"]
    assert levels[2].only_above == 100

    def tree(counts):
        return facets._level_nodes(levels, counts, prefix=())

    # 60 EDPB documents: series, then straight to the documents
    small = tree({("edpb", "riktlinjer", "2020"): 37,
                  ("edpb", "wp", "2018"): 23})
    assert [b["key"] for b in small] == ["edpb"]
    assert [c["children"] for c in small[0]["children"]] == [None, None]

    # a body over the threshold keeps its years
    big = tree({("eba", "gl", "2021"): 80, ("eba", "gl", "2022"): 47})
    years = big[0]["children"][0]["children"]
    assert [y["key"] for y in years] == ["2022", "2021"]


def test_edpb_browses_under_the_eu_ratt_masthead_entry():
    """The nav decision: EDPB guidance has no CELEX and is not a rättsakt, so it
    is a source of its own -- but it belongs beside the förordning it interprets,
    the way hudoc sits under folkrätt rather than getting a masthead entry."""
    assert render.BROWSE_DIR["guidance"] == "eurlex/vagledning"
    entry = next(e for e in render.ENV.globals["MAST_NAV"] if e[0] == "EU-rätt")
    assert "Riktlinje" in entry[2] and "Rekommendation" in entry[2]
    assert not any(e[0] in ("Vägledning", "Soft law")
                   for e in render.ENV.globals["MAST_NAV"])


def test_the_eu_selector_names_the_body_each_group_of_documents_comes_from(
        tmp_path):
    """A listing of riktlinjer is the EDPB's, not the union legislator's, and the
    shared EU-rätt selector has to say so: one labelled group per issuing body,
    rather than one flat row of document types in which "Riktlinjer" sat beside
    "Förordningar" with nothing saying who wrote which."""
    db = str(tmp_path / "catalog.sqlite")
    paths = []
    for serie, nummer in (("riktlinjer", "05/2020"),
                          ("rekommendationer", "01/2020"), ("wp", "248")):
        path = tmp_path / ("%s.json" % serie)
        path.write_text(json.dumps(_artifact(serie=serie, nummer=nummer)),
                        "utf-8")
        paths.append(path)
    catalog.rebuild(db, "guidance", paths)
    con = catalog.connect(db)
    groups = render.eurlex_axis(con)
    # eurlex is empty in this catalog, so only the guidance group is offered --
    # the selector is built from what the corpus holds, not from a fixed list
    assert [axis for axis, _entries in groups] == ["EU-organens vägledningar"]
    # and its entries are the issuing bodies, not their series. With one body
    # the series read as a flat list of document types; with two, "Riktlinjer"
    # would appear twice with nothing saying whose.
    assert [label for _key, label, _url, _count in groups[0][1]] == [
        "Europeiska dataskyddsstyrelsen (EDPB)"]


# --------------------------------------------------------------------------
# footnotes -- where these documents keep the citation that identifies a
# guideline they have named in prose
# --------------------------------------------------------------------------

def test_footnotes_are_what_the_block_classifier_dropped():
    """Regression, and the reason the vertical carries notes at all: IMY names
    a vägledning in prose ("Europeiska dataskyddsstyrelsens riktlinjer om
    samtycke") and grounds it with the number in the note below. The block
    classifier drops everything set below the running size, so 43 of the 83
    IMY-beslut that name this guidance carried its number and not one of those
    numbers reached the artifact."""
    # the running size is the *mode* of the stream, so a realistic stream has
    # more body than notes -- which is what tells the two apart at all
    stream = [Para(text="Body prose at the running size.", size=17)] * 4 + [
        Para(text="16 Se yttrande 15/2011 om samtycke (WP 187), s. 8.", size=10),
        Para(text="Riktlinjer 05/2020, punkt 12.", size=10)]
    notes = edpb_parse.footnotes(stream)
    assert [(f.mark, f.text) for f in notes] == [
        ("16", "Se yttrande 15/2011 om samtycke (WP 187), s. 8."),
        ("", "Riktlinjer 05/2020, punkt 12.")]


def test_the_page_furniture_that_shares_the_small_size_is_not_a_footnote():
    stream = [Para(text="Body prose at the running size.", size=17)] * 6 + [
        Para(text="Antagna 5", size=10),          # the running footer
        Para(text="3", size=10),                  # a bare page number
        Para(text="Version 2.0", size=10),        # front matter
        Para(text="En riktig fotnot med tillräcklig längd.", size=10)]
    assert [f.text for f in edpb_parse.footnotes(stream)] == [
        "En riktig fotnot med tillräcklig längd."]


def test_a_footnote_is_citation_scanned_like_any_other_text():
    art = Vagledning(utgivare="edpb", serie="riktlinjer", nummer="05/2020", titel="Riktlinjer 05/2020",
        body=[Block("stycke", "Se riktlinjerna nedan.")],
        fotnoter=[Fotnot("16", "Se WP 248 och riktlinjer 3/2019.")],
    ).to_artifact(LagrumParser({}, basefile="edpb",
                               parse_types=[EULAGSTIFTNING, VAGLEDNING]))
    assert [x["uri"] for x in art["footnotes"][0]["text"]
            if isinstance(x, dict)] == ["https://lagen.nu/guidance/edpb/wp/248",
                                        "https://lagen.nu/guidance/edpb/riktlinjer/03-2019"]


def test_another_authority_numbered_guidance_is_not_the_edpbs(refparser):
    """Regression. "Riktlinjer NN/ÅÅÅÅ" is not the EDPB's form alone -- every
    European authority numbers its guidance that way, and Swedish prose names
    the issuer in front of it. Without a guard, a sentence about Socialstyrelsen
    minted a live link to the EDPB's certification guideline (2/2018 *is* in the
    corpus) and wrote a false edge onto its rail."""
    for text in ("Socialstyrelsens riktlinjer 2/2018 om vård",
                 "EBA:s riktlinjer 4/2017 om intern styrning",
                 "Europarådets rekommendation 1/2019 om barn",
                 "jfr Finansinspektionens rekommendation 01/2020"):
        refparser.reset()
        assert refparser.parse_text(text, context={}) == [], text


@pytest.mark.parametrize("text,expected", [
    ("EDPB:s riktlinjer 05/2020", "https://lagen.nu/guidance/edpb/riktlinjer/05-2020"),
    ("Europeiska dataskyddsstyrelsens riktlinjer 3/2019",
     "https://lagen.nu/guidance/edpb/riktlinjer/03-2019"),
    ("Dataskyddsstyrelsens riktlinjer 07/2020",
     "https://lagen.nu/guidance/edpb/riktlinjer/07-2020"),
    ("Styrelsens riktlinjer 07/2020", "https://lagen.nu/guidance/edpb/riktlinjer/07-2020"),
    ("Artikel 29-gruppens riktlinjer 1/2018",
     "https://lagen.nu/guidance/edpb/riktlinjer/01-2018"),
    # the bare form, which is what the guidance itself and IMY both write once
    # the board has been named
    ("Se riktlinjer 05/2020.", "https://lagen.nu/guidance/edpb/riktlinjer/05-2020"),
    # sentence-initial: the capitalised "I" used to read as another issuer's
    # name because the exemption list was case-sensitive (RE_EDPB_SELF,
    # 2026-08-15 audit R9)
    ("I dataskyddsstyrelsens riktlinjer 05/2020 anges vidare",
     "https://lagen.nu/guidance/edpb/riktlinjer/05-2020"),
])
def test_the_edpbs_own_names_still_link(refparser, text, expected):
    refparser.reset()
    assert [r.uri for r in refparser.parse_text(text, context={})] == [expected]


def test_a_short_note_that_is_nothing_but_a_citation_survives():
    """Regression: the length floor ran before the marker was split off, so
    "1 WP 248." was discarded as page furniture -- the exact class of data the
    footnote reader exists to recover."""
    stream = [Para(text="Löpande text vid brödtextstorlek.", size=17)] * 6 + [
        Para(text="1 WP 248.", size=10),
        Para(text="12 Se ovan.", size=10),
        Para(text="7", size=10)]            # a bare page number still goes
    assert [(f.mark, f.text) for f in edpb_parse.footnotes(stream)] == [
        ("1", "WP 248."), ("12", "Se ovan.")]


def test_drop_repeated_title_needs_a_real_prefix_of_the_title():
    """Regression: a substring test let a bare digit match any title, and the
    loop then went on to eat the block after it. A leading block that folds
    away entirely is cover punctuation and is stepped over -- never content,
    and it is what hides the title echo behind it."""
    titel = "Riktlinjer 05/2020 om samtycke enligt förordning (EU) 2016/679"
    blocks = [("stycke", ".", 0), ("stycke", "1", 0),
              ("stycke", "Den 10 april 2018 antog gruppen sina riktlinjer.", 0)]
    # the "." goes as debris; the bare "1" stops the loop and keeps what follows
    assert edpb_parse.drop_repeated_title(blocks, titel) == blocks[1:]
    # while the cover's real echo of the title still goes
    echoed = [("rubrik", titel, 1), ("stycke", "Brödtext.", 0)]
    assert edpb_parse.drop_repeated_title(echoed, titel) == echoed[1:]


def test_the_issuer_guard_spans_a_multi_word_name(refparser):
    """The genitive may fall on the second word ("Europeiska bankmyndighetens",
    "Europeiska kommissionens"), which a single-token guard let through --
    straight onto Rekommendationer 01/2020, which is in the corpus."""
    for text in ("Europeiska bankmyndighetens riktlinjer 4/2017",
                 "Europeiska kommissionens rekommendation 01/2020"):
        refparser.reset()
        assert refparser.parse_text(text, context={}) == [], text
    # the EDPB's own long name has exactly that shape and must stay exempt
    refparser.reset()
    assert [r.uri for r in refparser.parse_text(
        "Europeiska dataskyddsstyrelsens riktlinjer 3/2019", context={})] \
        == ["https://lagen.nu/guidance/edpb/riktlinjer/03-2019"]


def test_a_title_echo_behind_cover_punctuation_still_goes():
    """Regression on the regression: requiring a prefix made the loop stop at a
    punctuation-only block, so two documents shipped opening with a stray "."
    followed by a verbatim duplicate of their h1."""
    titel = "Riktlinjer 03/2021 om tillämpningen av artikel 65.1 a"
    blocks = [("stycke", ".", 0), ("rubrik", titel, 1), ("stycke", "Brödtext.", 0)]
    assert edpb_parse.drop_repeated_title(blocks, titel) == blocks[2:]


def test_a_letterhead_before_the_title_echo_still_goes():
    """Regression (wp/259): the cover puts the issuer's letterhead and the
    title in one block, so the block is longer than the title and the old
    prefix test kept it -- the page opened with "ARTIKEL 29-GRUPPEN …"
    letterhead debris. The rule shared with rs catches the block that *ends
    with* the title."""
    titel = "Riktlinjer om samtycke enligt förordning (EU) 2016/679"
    blocks = [("rubrik", "ARTIKEL 29-GRUPPEN Artikel 29-gruppen " + titel, 1),
              ("stycke", "Brödtext.", 0)]
    assert edpb_parse.drop_repeated_title(blocks, titel) == blocks[1:]


def test_one_word_furniture_is_not_a_footnote():
    """The lowered floor let bare "Antagna" (the footer whose page number landed
    in another paragraph) and section headings in. A note reads as prose: more
    than one word, or a one-word abbreviation closed with a period."""
    stream = [Para(text="Löpande text vid brödtextstorlek.", size=17)] * 6 + [
        Para(text=t, size=10) for t in
        ("Antagna", "Bakgrund", "Praxis", "Ibid.", "Se skäl 87.")]
    assert [f.text for f in edpb_parse.footnotes(stream)] == ["Ibid.", "Se skäl 87."]


def test_a_citation_predating_the_edpb_is_not_linked_to_it(refparser):
    """The board was established by artikel 68 in dataskyddsförordningen and
    first met in May 2018, so it has issued nothing numbered for an earlier
    year. Without the floor the trigger claimed every "rekommendation nr
    NN/ÅÅÅÅ" ever printed -- a betänkande from 1972 citing "Rekommendationen nr
    12/1966" (an ILO recommendation) linked to edpb/rekommendationer/12-1966."""
    def linked(text):
        return [r.uri for r in refparser.parse_text(text)]

    assert linked("Se riktlinjer 05/2020.") == [
        "https://lagen.nu/guidance/edpb/riktlinjer/05-2020"]
    assert linked("Rekommendationen nr 12/1966 om detta.") == []
    assert linked("rekommendation nr 26/1980 anger vidare") == []


# --------------------------------------------------------------------------
# acer: the two listing shapes, the slug and the cover's word against the page's
# --------------------------------------------------------------------------
#
# The fixtures under ``test/files/acer/`` are trimmed captures of the live 2026
# listings, cut down to the sections that carry a case worth naming.

ACER_FIXTURES = Path(__file__).parent / "files" / "acer"


def acer_fixture(name):
    return (ACER_FIXTURES / name).read_text("utf-8")


def test_acer_framework_page_names_the_ramriktlinjer_and_not_what_sits_beside():
    """The ramriktlinjesidan lists the adopting ACER-beslut and an ENTSOG
    impact assessment under the same markup as the ramriktlinjerna. Only a
    document naming itself a Framework Guideline *first* is one: the beslut's
    title carries the words too, further in ("ACER Decision 02-2011 on the
    Framework Guidelines on ...")."""
    titles = [titel for titel, _ in acer_download.linked_documents(
        acer_fixture("framework-guidelines.html"), "https://www.acer.europa.eu")]
    assert len(titles) == 5
    assert [t for t in titles if acer_download.RE_FRAMEWORK.match(t)] == [
        "Framework Guideline on Demand Response",
        "Framework Guidelines on Capacity Allocation and Congestion "
        "Management for Electricity",
        "Framework Guidelines on Capacity Allocation and Congestion "
        "Management for Electricity"]


@pytest.mark.parametrize("titel,slug", [
    ("Framework Guideline on Demand Response", "demand-response"),
    # the article after the preposition goes too, so "for the joint scenarios"
    # and "on joint scenarios" would not be two documents
    ("Framework Guidelines for the joint scenarios for network development "
     "planning of electricity and gas",
     "joint-scenarios-for-network-development-planning-of-electricity-and-gas"),
    # the file name carries a date stamp and the title does not: the slug is the
    # name's, so it reads as the citation
    ("Framework Guideline on Sector-Specific Rules for Cybersecurity Aspects "
     "of Cross-Border Electricity Flows",
     "sector-specific-rules-for-cybersecurity-aspects-of-cross-border-"
     "electricity-flows"),
])
def test_acer_framework_slug(titel, slug):
    assert acer_download.framework_slug(titel) == slug


def test_acer_one_ramriktlinje_listed_twice_slugs_the_same_way():
    """The canonical section and the ämnessektionen name the same ramriktlinje
    from two different files. The name is the identity, so both reach one
    address and the walk takes the first."""
    slugs = [acer_download.framework_slug(titel)
             for titel, _ in acer_download.linked_documents(
                 acer_fixture("framework-guidelines.html"), "")
             if acer_download.RE_FRAMEWORK.match(titel)]
    assert slugs[1] == slugs[2] == \
        "capacity-allocation-and-congestion-management-for-electricity"


def test_acer_opinions_page_states_a_number_for_all_but_the_annex():
    """The opinions page is one hand-built listing of anchors: an annex sits
    beside the yttrande it belongs to, and one yttrande ACER never numbered
    sits among the numbered ones. Both are outcomes with a count of their own,
    not silent drops."""
    rows = acer_download.linked_documents(acer_fixture("opinions.html"), "")
    annexes = [t for t, _ in rows if acer_download.RE_ANNEX.match(t)]
    numbers = [acer_download.listing_number("yttranden", t) for t, _ in rows
               if not acer_download.RE_ANNEX.match(t)]
    assert annexes == ["Annex I to ACER Opinion 03-2025"]
    assert numbers == ["13/2026", "03/2025", "04/2014", None, "04/2014"]


def test_acer_card_view_reads_its_own_dates_and_counts_its_annexes():
    """The rekommendationsvyn is the other page shape: cards with a date and a
    collapsed annex list. An annex is ACER's own document, not this one's text,
    so it is counted and not followed."""
    cards = acer_download.card_documents(
        acer_fixture("recommendations.html"), "https://www.acer.europa.eu")
    assert [(c[1], c[3]) for c in cards] == [("2026-03-30", 0),
                                             ("2026-03-20", 0),
                                             ("2025-07-29", 1)]
    assert cards[0][2].endswith(
        "/ACER-Recommendation-02-2026-Proposals-to-strengthen-electricity-"
        "market-rules.pdf")
    assert acer_download.listing_number("rekommendationer", cards[0][0]) \
        == "02/2026"


def test_acer_pager_ends_where_the_view_ends():
    assert acer_download.has_next_page(acer_fixture("recommendations.html"))
    assert not acer_download.has_next_page(
        acer_fixture("recommendations-last.html"))


class _AcerCounts:
    """What `filed_number` counts, without the rest of the run."""

    def __init__(self):
        self.silent = 0
        self.renamed = []
        self.conflicts = []


@pytest.mark.parametrize("listed,printed,filed", [
    # the ordinary case: the cover prints what the listing says
    ("13/2026", {"13/2026"}, "13/2026"),
    # a scanned cover prints nothing this can read, so the listing stands
    ("02/2011", set(), "02/2011"),
    # the cover names another document beside itself; the listed number is
    # among them, so nothing changes (the `eba_download` rule)
    ("03/2025", {"03/2025", "01/2024"}, "03/2025"),
    # ACER links ACER-Opinion-04-2015.pdf under the title of 04-2014. The file
    # is the document: it is filed as 04/2015, which is listed nowhere else
    ("04/2014", {"04/2015"}, "04/2015"),
    # several numbers and none of them the listed one: refused, not guessed
    ("04/2014", {"04/2015", "07/2013"}, None),
])
def test_acer_the_cover_settles_the_number(listed, printed, filed):
    counts = _AcerCounts()
    assert acer_download.filed_number(listed, set(printed), "u", counts) == filed


def test_acer_every_way_the_cover_answers_is_counted_apart():
    """rule:instrument-failures: a cover that says nothing, one that overrules
    the listing and one that contradicts it must not look alike in a run."""
    counts = _AcerCounts()
    acer_download.filed_number("02/2011", set(), "silent", counts)
    acer_download.filed_number("04/2014", {"04/2015"}, "renamed", counts)
    acer_download.filed_number("04/2014", {"04/2015", "07/2013"}, "clash",
                               counts)
    assert counts.silent == 1
    assert len(counts.renamed) == 1 and "renamed" in counts.renamed[0]
    assert len(counts.conflicts) == 1 and "clash" in counts.conflicts[0]


def test_acer_cover_numbers_ignore_the_rattsakter_a_cover_cites():
    """Every ACER cover recites the förordning it rests on ("Regulation (EC) No
    714/2009"), and those are numbered too. A löpnummer is two digits, so a
    three- or four-digit one cannot be this document's."""
    assert acer_download.cover_numbers(
        "OPINION No 19/2019 OF THE EUROPEAN UNION AGENCY ... Having regard to "
        "Regulation (EC) No 714/2009 ... and Decision No 1364/2006/EC"
    ) == {"19/2019"}


def test_acer_cover_date_is_the_documents_own_and_not_a_rattsakts():
    """The heading names the agency before it states the date, and the recitals
    state a rättsakts date after it. Only the recitals stop the read."""
    assert acer_download.cover_date(
        "PUBLIC OPINION No 07/2024 OF THE EUROPEAN UNION AGENCY FOR THE "
        "COOPERATION OF ENERGY REGULATORS of 29 October 2024 on the review of "
        "gas and hydrogen national Network Development Plans THE EUROPEAN "
        "UNION AGENCY ..., Having regard to Regulation (EU) 2024/1789 ... of "
        "13 June 2024") == "2024-10-29"
    # a ramriktlinje states a bare date under its title and recites nothing
    assert acer_download.cover_date(
        "Framework Guideline on Demand Response 20 December 2022 European "
        "Union Agency for the Cooperation of Energy Regulators") == "2022-12-20"
    # the pre-2017 wrapper page, whose cover behind it is a scan that states
    # no date pdftotext can read
    assert acer_download.cover_date(
        "Publishing date: 30/05/2012 Document title: We appreciate your "
        "feedback") == "2012-05-30"


def test_acer_identity_reproduces_the_number_acer_prints():
    """"OPINION No 13/2026" -> guidance/acer/yttranden/13-2026, löpnummer
    first. The series segment carries its weight here: ACER restarts every
    series at 01 each year and the sequences are independent, so 01/2013 is a
    yttrande, a rekommendation and a beslut at once."""
    assert vagledning_uri("acer", "yttranden", "13/2026") == \
        "https://lagen.nu/guidance/acer/yttranden/13-2026"
    assert vagledning_uri("acer", "rekommendationer", "2/2025") == \
        "https://lagen.nu/guidance/acer/rekommendationer/02-2025"
    assert vagledning_identifier("acer", "yttranden", "13/2026") == \
        "ACER Opinion No 13/2026"
    # a ramriktlinje has no number, so it is cited by the name ACER lists it
    # under and its address is that name's slug
    assert vagledning_uri("acer", "ramriktlinjer", "demand-response") == \
        "https://lagen.nu/guidance/acer/ramriktlinjer/demand-response"
    assert vagledning_identifier(
        "acer", "ramriktlinjer", "demand-response",
        citation="Framework Guideline on Demand Response") == \
        "Framework Guideline on Demand Response"


def test_acer_basefile_and_listing_come_off_the_registry():
    assert acer_download.basefile("yttranden", "13/2026") == \
        "acer/yttranden/13-2026"
    assert acer_download.LISTING["ramriktlinjer"] == \
        "https://www.acer.europa.eu/documents/official-documents/" \
        "framework-guidelines"


# --------------------------------------------------------------------------
# easa: the AMC/GM annexes to EASA:s ED Decisions
# --------------------------------------------------------------------------

EASA_FIXTURES = Path(__file__).parent / "files" / "easa"


def easa_fixture(name):
    return (EASA_FIXTURES / name).read_text("utf-8")


@pytest.mark.parametrize("titel,serie,nummer", [
    # the ordinary shape, in the four spellings EASA uses for the lead
    ("AMC & GM to Part-CAT — Issue 2, Amendment 20",
     "amc-gm", "part-cat-issue-2-amendment-20"),
    ("AMC and GM to Part 21 — Issue 2, Amendment 18",
     "amc-gm", "part-21-issue-2-amendment-18"),
    ("AMC/GM to Part 21 — Issue 2, Amendment 17",
     "amc-gm", "part-21-issue-2-amendment-17"),
    ("AMC & GM Part-TCO — Initial Issue", "amc-gm", "part-tco-initial-issue"),
    # neither preposition nor "to"
    ("GM on Remote tower operations — Issue 3",
     "gm", "remote-tower-operations-issue-3"),
    # an annex holding one of the two, which is a series of its own
    ("AMC to Part-66 — Amendment 10", "amc", "part-66-amendment-10"),
    ("GM to Annex I (Definitions) — Amendment 5",
     "gm", "annex-i-definitions-amendment-5"),
    # the pre-2013 shape: the rule first, and the two amendment sequences of
    # the AMC and the GM printed side by side
    ("Part-145 / AMC Amendment 4 / GM Amendment 1",
     "amc-gm", "part-145-amc-amendment-4-gm-amendment-1"),
])
def test_easa_series_number_reads_the_annex_name(titel, serie, nummer):
    """The identity is the annex's own name: its lead says which series it
    belongs to, and the rest is the number."""
    assert easa_download.series_number(titel) == (serie, nummer)


def test_easa_amc_and_gm_to_one_rule_are_separate_documents():
    """The lead is not decoration. The AMC and the GM to one rule run separate
    amendment sequences, so "Amendment 4" alone names two documents seven years
    apart -- and dropping the lead from the address would file the 2015 GM over
    the 2008 AMC."""
    amc = easa_download.series_number("AMC to Part-M — Amendment 4")
    gm = easa_download.series_number("GM to Part M — Amendment 4")
    assert amc == ("amc", "part-m-amendment-4")
    assert gm == ("gm", "part-m-amendment-4")
    assert easa_download.basefile(*amc) != easa_download.basefile(*gm)


def test_easa_a_name_that_is_neither_amc_nor_gm_is_declined():
    """One page in the library is titled just "Part-M" and holds Decision
    2011-002-R -- the Executive Director's decision, not an annex to one. It is
    counted, never filed under a guessed series."""
    assert easa_download.series_number("Part-M") is None


def test_easa_uri_and_identifier():
    """The address reproduces the annex's own name, and the citation is that
    name verbatim -- EASA gives its AMC/GM no separate number, so every record
    carries a `citation` and `Series.identifier` is never reached."""
    assert vagledning_uri("easa", "amc-gm", "part-cat-issue-2-amendment-20") \
        == "https://lagen.nu/guidance/easa/amc-gm/part-cat-issue-2-amendment-20"
    assert vagledning_identifier(
        "easa", "amc-gm", "part-cat-issue-2-amendment-20",
        "AMC & GM to Part-CAT — Issue 2, Amendment 20") \
        == "AMC & GM to Part-CAT — Issue 2, Amendment 20"


def test_easa_leaf_pages_reads_the_listing():
    """One listing page names its documents once each, in view order -- the row
    links the same page from the title and from a "view" icon."""
    assert easa_download.leaf_pages(easa_fixture("listing-fragment.html")) == [
        "/en/document-library/acceptable-means-of-compliance-and-guidance-"
        "material/amc-gm-commission-regulation-eu-no-1178-2011-issue-1-"
        "amendment-3",
        "/en/document-library/acceptable-means-of-compliance-and-guidance-"
        "material/amc-gm-part-ara-issue-1-amendment-15",
        "/en/document-library/acceptable-means-of-compliance-and-guidance-"
        "material/amc-gm-part-fcl-issue-1-amendment-15"]


def test_easa_parse_leaf_reads_an_annex():
    """A document page states everything the record needs: the annex's name,
    the instrument that issued it, the rule it attaches to, and the file."""
    fields = easa_download.parse_leaf(easa_fixture("leaf-part-ara.html"), "x")
    assert fields == {
        "titel": "AMC & GM to Part-ARA — Issue 1, Amendment 15",
        "bilaga": True,
        "antagen": "2026-07-15",
        "beslut": "ED Decision 2026/006/R",
        "amnesord": ["Part-ARA - Authority Requirements for Aircrew"],
        "dokument_url": "https://www.easa.europa.eu/en/downloads/143883/en",
        "pdf": True}


def test_easa_parse_leaf_declines_an_unofficial_consolidation():
    """EASA's own running consolidation of a rule annex is not an annex to any
    ED Decision: it carries neither the Official Publication mark nor a Related
    ED Decision, which is what `bilaga` reads."""
    fields = easa_download.parse_leaf(easa_fixture("leaf-consolidated.html"), "x")
    assert fields["titel"].startswith("Consolidated (unofficial)")
    assert fields["bilaga"] is False
    assert fields["beslut"] is None


def _easa_pager(pages, monkeypatch):
    """Serve `pages` (a list of leaf-path lists) under ?page=N, and repeat the
    last one for ever after -- the EASA pager's own behaviour."""
    def fetch(_session, url, _delay):
        n = int(url.rsplit("page=", 1)[1])
        return "".join(
            '<div class="view-main-content"><table><tbody><tr><td>'
            '<a class="easa_node_link" href="%s">x</a></td></tr></tbody>'
            '</table></div>' % path
            for path in pages[min(n, len(pages) - 1)])
    monkeypatch.setattr(easa_download, "_fetch", fetch)


def test_easa_walk_library_stops_on_no_new_document(monkeypatch):
    """The stop rule is "this page named nothing new", never "this page was
    empty". Rows shift between pages while a walk runs, so page 2 here repeats
    one of page 1's -- and past the end EASA keeps serving rows rather than a
    404."""
    _easa_pager([["/a", "/b"], ["/b", "/c"], ["/c"]], monkeypatch)
    pages, walked = easa_download.walk_library(None, 0, log=lambda _m: None)
    assert pages == ["/a", "/b", "/c"]
    assert walked == 3


def test_easa_walk_library_refuses_a_pager_that_never_repeats(monkeypatch):
    """A pager that keeps naming new documents past the cap is a changed site,
    not a large corpus: it stops the harvest with a message rather than
    crawling on."""
    _easa_pager([["/%d" % n] for n in range(easa_download.PAGE_CAP + 1)],
                monkeypatch)
    with pytest.raises(ValueError, match="no longer terminates"):
        easa_download.walk_library(None, 0, log=lambda _m: None)


def _easa_cover(text):
    return [Para(text=text, size=17)]


def test_easa_cover_must_name_the_decision_the_page_named():
    """The page named the instrument and the annex's cover names it too, so
    reading both closes the loop: a file that changes behind its URL fails the
    parse rather than being filed under an identity that is not its."""
    record = {"basefile": "easa/amc-gm/part-cat-issue-2-amendment-20",
              "beslut": "ED Decision 2022/005/R", "titel": "T", "antagen": None}
    cover = _easa_cover(
        "Annex IV to ED Decision 2022/005/R ‘AMC and GM to Annex IV (Part-CAT) "
        "to Commission Regulation (EU) No 965/2012 — Issue 2, Amendment 20’ "
        # the amending annex prints the decision it amends as well, and the
        # filed number is accepted anywhere on the cover, never as the first
        # number found
        "The Annex to Decision 2014/015/R of 24 April 2014 is amended")
    assert edpb_parse._easa_fields("amc-gm", record, cover)["citation"] == "T"
    with pytest.raises(AssertionError, match="its cover names"):
        edpb_parse._easa_fields(
            "amc-gm", record,
            _easa_cover("Annex I to ED Decision 2019/008/R"))


def test_easa_a_cover_naming_only_the_amended_decision_is_read_by_its_name():
    """Some covers name the decision they amend and never their own -- and one
    misspells its own, "201/022/R" where EASA means 2019. What every cover does
    carry is the annex's own name, so that witnesses instead. The name must
    reach TITLE_ECHO_MIN folded characters: a two-letter title is inside almost
    any cover and would witness for a document it has nothing to do with."""
    record = {"basefile": "easa/amc-gm/part-21-issue-2-amendment-13",
              "beslut": "ED Decision 2021/011/R", "antagen": None,
              "titel": "AMC & GM to Part 21 — Issue 2, Amendment 13"}
    cover = _easa_cover(
        "AMC and GM to Part-21 Issue 2, Amendment 13 "
        "The Annex to ED Decision 2012/020/R is amended as follows")
    assert edpb_parse._easa_fields("amc-gm", record, cover)["antagen"] is None
    # the same cover, for a document whose name it does not carry
    with pytest.raises(AssertionError, match="its cover names"):
        edpb_parse._easa_fields(
            "amc-gm", {**record, "titel": "AMC & GM to Part-ATS"}, cover)


def test_easa_an_old_cover_naming_no_decision_is_accepted():
    """The oldest annexes name no decision at all: the cover of AMC & GM to
    Part-MED reads "Initial issue / 15 December 2011" and nothing more. The
    check is on what the cover says, not on it saying something."""
    record = {"basefile": "easa/amc-gm/part-med", "beslut": "ED Decision "
              "2011/015/R", "titel": "AMC & GM to Part-MED", "antagen": None}
    cover = _easa_cover("European Aviation Safety Agency Acceptable Means of "
                        "Compliance and Guidance Material to Part-MED "
                        "Initial issue 15 December 2011")
    assert edpb_parse._easa_fields("amc-gm", record, cover)["titel"] \
        == "AMC & GM to Part-MED"


def test_easa_is_wired_into_the_source():
    """The registry entry, the harvest scope and the facet labels are three
    places one body has to be named, and a body named in one but not the others
    is a page that never renders or a scope that never runs."""
    assert EASA.koder == ("amc-gm", "amc", "gm")
    # no series here is numbered NN/ÅÅÅÅ: the identity is the annex's own name,
    # so the slug is the number verbatim
    assert all(s.order is None for s in EASA.series)
    assert EASA.serie("amc-gm").slug("part-cat-issue-2-amendment-20") \
        == "part-cat-issue-2-amendment-20"
    assert guidance_download.SYNC[EASA.kod] is easa_download.easa_sync
    serie = [level for level in facets.SCHEMES["guidance"]
             if level.name == "Serie"][0]
    assert set(EASA.koder) <= set(serie.labels)
    # the section line an EASA page prints has to be one of the strings the
    # masthead marks EU-rätt current by, or the page renders with no nav entry
    # lit at all
    eu_ratt = next(act for label, _route, act in tpl.MAST_NAV
                   if label == "EU-rätt")
    assert all(guidance_render.SECTION[(EASA.kod, kod)] in eu_ratt
               for kod in EASA.koder)


# --------------------------------------------------------------------------
# EUIPO: the coordinate that stands in for a number, and which PDFs a volume
# is carried as
# --------------------------------------------------------------------------

def test_euipo_unit_nummer_reads_the_scope_codes():
    """The identity is EUIPO's own language-free scope codes, so one document
    keeps one address when the Swedish translation of an edition lands."""
    assert euipo_download.unit_nummer("PARTB", "SECTION4") == "part-b-section-4"
    assert euipo_download.unit_nummer("PARTM", "") == "part-m"
    # a leading zero is a real avsnitt here: Del C opens with Avsnitt 0
    assert euipo_download.unit_nummer("PARTC", "SECTION0") == "part-c-section-0"
    with pytest.raises(AssertionError):
        euipo_download.unit_nummer("PARTEXA RCD", "")


def test_euipo_cover_scope_reads_the_printed_coordinate():
    """A cover states its del once and its avsnitt as one of the numbers the
    opening prints -- a del-level PDF carries the covers of every avsnitt in
    it, so the avsnitt half is a set."""
    assert euipo_download.cover_scope(
        "GUIDELINES FOR EXAMINATION OF EUROPEAN UNION TRADE MARKS "
        "EUROPEAN UNION INTELLECTUAL PROPERTY OFFICE (EUIPO) Part C "
        "Opposition Section 3 Unauthorised filing by agents") == ("C", {"3"})
    # the Swedish volume prints the same coordinate in Swedish
    assert euipo_download.cover_scope(
        "RIKTLINJER FÖR PRÖVNING AV EU-VARUMÄRKEN Del B Prövning Avsnitt 4 "
        "Absoluta registreringshinder") == ("B", {"4"})
    # a del-level cover prints no avsnitt of its own
    assert euipo_download.cover_scope(
        "GUIDELINES FOR EXAMINATION Part A General rules") == ("A", set())
    # and one that carries its avsnitt's covers prints several
    assert euipo_download.cover_scope(
        "Part A General rules Section 1 Means of communication "
        "Part A General rules Section 2 General principles") \
        == ("A", {"1", "2"})


def test_euipo_plan_units_takes_the_smallest_pdf_that_overlaps_nothing():
    """Three shapes, one rule: the smallest PDF EUIPO publishes that no other
    taken PDF contains."""
    whole = euipo_download.WHOLE_VOLUME
    # a del every one of whose avsnitt publishes its own PDF: avsnitt by avsnitt
    units, declined = euipo_download.plan_units([
        ("PARTB", "Part B Examination", "2005000000", "/b",
         [("SECTION1", "Section 1 Proceedings", "2000120000", "/b1"),
          ("SECTION2", "Section 2 Formalities", "2000130000", "/b2")])])
    assert [u[0] for u in units] == ["part-b-section-1", "part-b-section-2"]
    assert declined == []
    # a del one of whose avsnitt publishes none -- Del A, whose Avsnitt 10
    # Bevis exists only inside the del's own PDF. Taking both would carry the
    # other avsnitt twice.
    units, declined = euipo_download.plan_units([
        ("PARTA", "Part A General rules", "2004000000", "/a",
         [("SECTION1", "Section 1 Means of communication", "2000030000", "/a1"),
          ("SECTION10", "Section 10 Evidence", "2004000000", "/a10")])])
    assert [u[0] for u in units] == ["part-a"]
    # a del that publishes no PDF at all, and the front matter that never does
    units, declined = euipo_download.plan_units([
        ("", "1 Inledning", whole, "/i", []),
        ("PARTOTHER", "Redaktionell not", whole, "/n", []),
        ("PARTEXA RCD", "Prövning av ansökningar", whole, "/e", [])])
    assert units == []
    assert declined == [("inledande sidor", "1 Inledning"),
                        ("inledande sidor", "Redaktionell not"),
                        ("delar utan egen PDF", "Prövning av ansökningar")]


def test_euipo_pick_publication_takes_the_current_edition_in_swedish():
    """The current edition, Swedish where that edition has a Swedish text.
    Two editions can be unflagged at once -- formgivningsriktlinjerna carry
    2023 and 2026 today -- and the older one is superseded whatever the flag
    says."""
    def pub(pid, sprak, force, obsolete=None, family="Design Guidelines"):
        return {"Id": pid, "Language": sprak, "ProductFamily": [family],
                "EntryIntoForce": force + "T00:00:00",
                "IsPubObsolete": obsolete}
    publications = [pub("1", "sv", "2023-03-31"), pub("2", "en", "2023-03-31"),
                    pub("3", "sv", "2026-07-01"), pub("4", "en", "2026-07-01"),
                    pub("5", "sv", "2022-03-31", "Yes"),
                    pub("6", "en", "2026-07-01", family="Trade mark Guidelines")]
    assert euipo_download.pick_publication(
        publications, "Design Guidelines")["Id"] == "3"
    # the trade mark edition in force has no Swedish text yet, so English
    assert euipo_download.pick_publication(
        publications, "Trade mark Guidelines")["Id"] == "6"
    with pytest.raises(AssertionError):
        euipo_download.pick_publication(publications, "Craft GI Guidelines")


def test_euipo_unit_title_names_the_volume_the_del_and_the_avsnitt():
    assert euipo_download.unit_title(
        "Trade mark guidelines", "Part C Opposition",
        "Section 0 Introduction") \
        == "Trade mark guidelines, Part C Opposition, Section 0 Introduction"
    assert euipo_download.unit_title(
        "Riktlinjer för formgivningar", None, None) \
        == "Riktlinjer för formgivningar"


def _euipo_cover(text):
    """One EUIPO cover as the Para stream `parse._euipo_fields` reads."""
    return [Para(text=text, size=30)]


def test_euipo_cover_check_reads_del_and_avsnitt_apart():
    """`parse._euipo_fields` proves the stored file is the del it is filed
    under, and -- for an avsnitt document -- that the cover prints that
    avsnitt among its numbers."""
    record = {"basefile": "euipo/varumarke/part-c-section-3",
              "nummer": "part-c-section-3", "titel": "…", "antagen": None,
              "citation": "…"}
    assert edpb_parse._euipo_fields(
        "varumarke", record,
        _euipo_cover("GUIDELINES FOR EXAMINATION Part C Opposition Section 3 "
                  "Unauthorised filing"))["citation"] == "…"
    with pytest.raises(AssertionError):
        edpb_parse._euipo_fields(
            "varumarke", record,
            _euipo_cover("GUIDELINES FOR EXAMINATION Part D Cancellation "
                      "Section 3 Substantive provisions"))
    # a whole-volume document's nummer is no coordinate, and its cover lists
    # every del, so no check is made
    volume = {"basefile": "euipo/gi/all-parts", "nummer": "all-parts",
              "titel": "…", "antagen": None, "citation": "…"}
    assert edpb_parse._euipo_fields(
        "gi", volume,
        _euipo_cover("RIKTLINJER FÖR GRANSKNING Del A Geografiska beteckningar "
                  "Del B Immaterialrättsmyndighetens organisation"))["titel"] \
        == "…"


def test_euipo_is_wired_into_the_source():
    """The registry entry, the harvest scope, the facet labels and the section
    line are four places one body has to be named."""
    assert EUIPO.koder == ("varumarke", "formgivning", "gi")
    # no series here is numbered NN/ÅÅÅÅ: the identity is a coordinate, so the
    # slug is the number verbatim
    assert all(s.order is None for s in EUIPO.series)
    assert EUIPO.serie("varumarke").slug("part-b-section-4") \
        == "part-b-section-4"
    assert guidance_download.SYNC[EUIPO.kod] is euipo_download.euipo_sync
    serie = [level for level in facets.SCHEMES["guidance"]
             if level.name == "Serie"][0]
    assert set(EUIPO.koder) <= set(serie.labels)
    eu_ratt = next(act for label, _route, act in tpl.MAST_NAV
                   if label == "EU-rätt")
    assert all(guidance_render.SECTION[(EUIPO.kod, kod)] in eu_ratt
               for kod in EUIPO.koder)
    # EUIPO's template marks its headings bold and reprints a running head on
    # every page; both are read off the registry, not branched on in the parse
    assert EUIPO.feta_rubriker and EUIPO.upprepat_sidhuvud
    assert not EDPB.feta_rubriker and not EDPB.upprepat_sidhuvud


def test_eurlex_amending_act_is_filed_under_its_own_number():
    """An amending act names the act it amends first and prints its own number
    in the trailing parenthesis. Reading the first match filed nine ESRB
    documents under the amended act's number, overwriting that act's own text."""
    body = eurlex_download.BODIES["esrb"]
    titel = ("Europeiska systemrisknämndens beslut av den 20 mars 2020 om "
             "ändring av beslut ESRB/2011/1 om arbetsordningen för Europeiska "
             "systemrisknämnden (ESRB/2020/3) 2020/C 140/04")
    assert eurlex_download.series_number(body, {}, titel) == "2020/3"


def test_eurlex_stated_number_beats_the_printed_one():
    """CELLAR's own prefix/year/sequence predicates are the body's structured
    statement of the number, so a title that names another document cannot
    displace them."""
    body = eurlex_download.BODIES["esrb"]
    row = {"pfx": {"value": "ESRB"}, "yr": {"value": "2019"},
           "nr": {"value": "3"}}
    assert eurlex_download.series_number(
        body, row, "... om ändring av rekommendation ESRB/2016/14 ...") == "2019/3"


# --------------------------------------------------------------------------
# route A: the ECB and the ESRB, read from the manifestation CELLAR serves
# --------------------------------------------------------------------------

def _formex(xml):
    return etree.fromstring(xml.encode("utf-8"), lib_formex.XML_PARSER)


def test_general_root_carries_its_text_in_contents():
    """An ECB-yttrande is printed in the C series and comes as a `GENERAL`
    root, whose text sits in CONTENTS. `parse_act` walks straight past it and
    returned zero blocks for all 224 of them."""
    root = _formex(
        '<GENERAL><TITLE><TI><P>Opinion of the European Central Bank</P></TI>'
        '</TITLE><CONTENTS><NP><NO.P>1.</NO.P><TXT>The ECB received a request.'
        '</TXT></NP><GR.SEQ><TITLE><TI><P>General considerations</P></TI>'
        '</TITLE><NP><NO.P>2.</NO.P><TXT>The proposal is welcome.</TXT></NP>'
        '</GR.SEQ></CONTENTS></GENERAL>')
    raw = []
    edpb_parse._formex_main(root, raw)
    assert [(b.kind, b.text) for b in raw] == [
        ("paragraph", "1. The ECB received a request."),
        ("heading", "General considerations"),
        ("paragraph", "2. The proposal is welcome."),
    ]


def test_corr_root_keeps_the_passage_each_correction_names():
    """A rättelse says which passage it corrects in a DESCRIPTION that
    `walk_content` does not reach; without it the correction reads as two
    unattached quotations."""
    root = _formex(
        '<CORR><TITLE><TI><P>Rättelse</P></TI></TITLE><CONTENTS.CORR>'
        '<CORRECTION><DESCRIPTION>Sidan 2, skäl 4</DESCRIPTION>'
        '<OLD.CORR><P>I stället för:</P></OLD.CORR>'
        '<NEW.CORR><P>ska det stå:</P></NEW.CORR></CORRECTION>'
        '</CONTENTS.CORR></CORR>')
    raw = []
    edpb_parse._formex_main(root, raw)
    assert raw[0].kind == "heading"
    assert raw[0].text == "Sidan 2, skäl 4"


def test_formex_article_heading_carries_its_designation():
    """The guidance block holds one string where Formex sets the designation
    and the title apart, so the heading has to name the article itself."""
    blocks, noter = edpb_parse._from_formex_blocks([
        lib_formex.Block("article", "Ändringar", num="1"),
        lib_formex.Block("note", "EUT L 331, 15.12.2010, s. 1.", num="1"),
    ], "sv")
    assert [(b.kind, b.text) for b in blocks] == [("rubrik", "Artikel 1 Ändringar")]
    assert [(n.mark, n.text) for n in noter] == [
        ("1", "EUT L 331, 15.12.2010, s. 1.")]


def test_html_body_drops_the_title_block_above_the_first_punkt():
    """EUR-Lex serves the oldest yttranden as a flat run of <p> whose first
    paragraphs reprint the title, the date and the CON number -- all of which
    the record already carries as fields."""
    html = ("<html><body><p>Opinion of the European Central Bank</p>"
            "<p>of 13 September 2001</p><p>(CON/2001/25)</p>"
            "<p>1. On 21 May 2001 the ECB received a request.</p>"
            "<p>2. The ECB's competence is based on Article 105(4).</p>"
            "</body></html>")
    blocks = edpb_parse._html_paragraph_blocks(html)
    assert [(b.punkt, b.text[:20]) for b in blocks] == [
        ("1", "1. On 21 May 2001 th"),
        ("2", "2. The ECB's compete"),
    ]


def test_ecb_classification_mark_is_front_matter():
    """The ECB prints it at the top of every page of a yttrande. The first
    page's line carries the language code and the rest do not, so the recurrence
    test that removes the others leaves this one standing."""
    for line in ("EN ECB-PUBLIC", "ECB-PUBLIC", "SV ECB-PUBLIC"):
        assert edpb_parse.RE_FRONT_MATTER.match(line), line
    assert not edpb_parse.RE_FRONT_MATTER.match(
        "ECB-PUBLIC is not what this paragraph says")


# --------------------------------------------------------------------------
# the EBA's Swedish title, read off the document's own cover
# --------------------------------------------------------------------------
# The covers below are the recorded paragraph streams of the real PDFs, so the
# tests assert against what the extraction actually yields -- "LGDskattning"
# without its hyphen, the consolidation glyph, the unfilled template.

def _eba_cover(*lines):
    return [Para(text=line, size=17) for line in lines]


def test_eba_cover_title_is_read_past_the_shouted_running_head():
    """The cover sets the title twice: once shouted as a running head and once
    in sentence case. Only the second is the document's name."""
    assert edpb_parse.eba_cover_title(_eba_cover(
        "RIKTLINJER FÖR PD-SKATTNING, LGD-SKATTNING OCH HANTERING AV FALLERADE"
        " EXPONERINGAR",
        "EBA/GL/2017/16 23/04/2018",
        "Riktlinjer för PD-skattning, LGDskattning och hantering av fallerade"
        " exponeringar",
    )) == ("Riktlinjer för PD-skattning, LGDskattning och hantering av "
           "fallerade exponeringar")


def test_eba_cover_title_joins_a_title_split_over_two_paragraphs():
    """The EBA sets a long title as its lead word alone over the rest."""
    assert edpb_parse.eba_cover_title(_eba_cover(
        "EBA/GL/2024/06 6 juni 2024",
        "Riktlinjer",
        "för minimiinnehållet i styrningsarrangemangen för utgivare av "
        "tillgångsanknutna token",
    )) == ("Riktlinjer för minimiinnehållet i styrningsarrangemangen för "
           "utgivare av tillgångsanknutna token")


def test_eba_cover_title_keeps_the_number_an_amending_title_names():
    """An amending riktlinje names the riktlinje it amends by number inside its
    own title, so the furniture comes off the ends and never the middle."""
    assert edpb_parse.eba_cover_title(_eba_cover(
        "SLUTRAPPORT OM RIKTLINJER OM ÄNDRING AV RIKTLINJERNA FÖR BEDÖMNING AV"
        " SEKRETESSORDNINGARS LIKVÄRDIGHET EBA/GL/2025/05 4 november 2025",
        "Riktlinjer",
        "om ändring av riktlinjerna EBA/GL/2022/04 för bedömning av "
        "sekretessordningars likvärdighet",
    )) == ("Riktlinjer om ändring av riktlinjerna EBA/GL/2022/04 för bedömning"
           " av sekretessordningars likvärdighet")


def test_eba_cover_title_skips_an_unfilled_template():
    """eba/gl/2018-05 ships with the EBA's own placeholder still in it; the
    real title stands further down the same cover."""
    assert edpb_parse.eba_cover_title(_eba_cover(
        "Riktlinjer",
        "EBA/GL/20XX/XX DD månad ÅÅÅÅ Riktlinjernas nummer ska anges av COMMS",
        "Riktlinjer om ändring av riktlinjerna EBA/GL/2018/05",
        "För rapportering av statistiska uppgifter om bedrägeri enligt andra "
        "betaltjänstdirektivet",
    )) == ("Riktlinjer om ändring av riktlinjerna EBA/GL/2018/05 För "
           "rapportering av statistiska uppgifter om bedrägeri enligt andra "
           "betaltjänstdirektivet")


def test_eba_cover_title_does_not_double_a_repeated_lead_word():
    """eba/gl/2017-05 sets the lead word twice, once alone and once opening the
    title itself."""
    assert edpb_parse.eba_cover_title(_eba_cover(
        "RIKTLINJER OM IKT-RISKBEDÖMNING ENLIGT ÖUP EBA/GL/2017/05 11/09/2017",
        "Riktlinjer",
        "Riktlinjer om IKT-riskbedömning inom ramen för översyns- och "
        "utvärderingsprocessen (ÖUP)",
    )) == ("Riktlinjer om IKT-riskbedömning inom ramen för översyns- och "
           "utvärderingsprocessen (ÖUP)")


def test_eba_cover_title_drops_a_consolidation_marker():
    """A consolidated cover prints EUR-Lex's own change markers before the
    title, set in a symbol font: a private-use glyph and the marker's letter
    code. eba/gl/2021-17 opens "\uf0daO Riktlinjer", and dropping the glyph
    alone left the bare "O" standing at the head of the title."""
    assert edpb_parse.eba_cover_title(_eba_cover(
        "EBA:S RIKTLINJER FÖR AVGRÄNSNING OCH RAPPORTERING AV TILLGÄNGLIGA "
        "FINANSIELLA MEDEL I INSÄTTNINGSGARANTISYSTEM",
        "EBA/GL/2021/17 (konsoliderad version) 17 december 2021",
        "\uf0daO Riktlinjer",
        "för avgränsning och rapportering av tillgängliga finansiella medel i "
        "insättningsgarantisystem",
    )) == ("Riktlinjer för avgränsning och rapportering av tillgängliga "
           "finansiella medel i insättningsgarantisystem")


# --------------------------------------------------------------------------
# which of the two titles an EBA artifact carries
# --------------------------------------------------------------------------

def test_eba_english_document_keeps_its_record_title():
    """Eight of the 80 are English throughout; the record's title is already in
    their own language and the cover adds nothing."""
    record = {"basefile": "eba/gl/2018-09", "sprak": "en", "nummer": "2018/09",
              "titel": "Guidelines on the STS criteria for non-ABCP "
                       "securitisation"}
    assert edpb_parse.eba_titel(record, _eba_cover("Final Report")) == \
        record["titel"]


def test_eba_refuses_a_cover_title_naming_this_documents_own_number():
    """Nothing amends itself. The EBA files an amending riktlinje behind the
    amended riktlinje's leaf page, so the PDF at eba/gl/2018-10 is
    EBA/GL/2022/13 and its cover title names EBA/GL/2018/10 as the act it
    amends. Taking that title would leave the artifact saying it is the
    amendment while its own identifier says it is the amended act. Five
    documents are in this state."""
    record = {"basefile": "eba/gl/2018-10", "sprak": "sv", "nummer": "2018/10",
              "titel": "Guidelines on disclosure of non-performing and "
                       "forborne exposures"}
    cover = _eba_cover(
        "RIKTLINJER OM ÄNDRING AV RIKTLINJERNA EBA/GL/2018/10",
        "EBA Public",
        "EBA/GL/2022/13 12 oktober 2022",
        "Riktlinjer",
        "om ändring av riktlinjerna EBA/GL/2018/10 om offentliggörande av "
        "nödlidande exponeringar och exponeringar med anstånd")
    assert edpb_parse.eba_titel(record, cover) == record["titel"]


def test_eba_keeps_an_amending_title_that_names_another_document():
    """The same shape is the *right* title when the number it amends is not
    this document's own: eba/gl/2025-05 really does amend EBA/GL/2022/04."""
    record = {"basefile": "eba/gl/2025-05", "sprak": "sv", "nummer": "2025/05",
              "titel": "Guidelines amending the guidelines on equivalence of "
                       "confidentiality regimes"}
    cover = _eba_cover(
        "Riktlinjer",
        "om ändring av riktlinjerna EBA/GL/2022/04 för bedömning av "
        "sekretessordningars likvärdighet")
    assert edpb_parse.eba_titel(record, cover) == (
        "Riktlinjer om ändring av riktlinjerna EBA/GL/2022/04 för bedömning av"
        " sekretessordningars likvärdighet")


def test_eba_swedish_cover_with_no_title_is_a_parser_change():
    """72 of the 72 Swedish documents state a title on their cover. None means
    the EBA changed its template, which is a parser change and not something to
    ship an English title over in silence (rule:fail-fast)."""
    record = {"basefile": "eba/gl/2099-01", "sprak": "sv", "nummer": "2099/01",
              "titel": "Guidelines on something"}
    with pytest.raises(AssertionError, match="no Swedish title"):
        edpb_parse.eba_titel(record, _eba_cover("EBA/GL/2099/01 1 januari 2099"))


def test_own_number_slug_is_shared_by_the_minter_and_the_citation_engine():
    """The address a citation resolves to cannot drift from the address the
    page is published under, so both sides call one implementation."""
    assert guidance_issuers.own_number_slug is lagrum.own_number_slug
    for number in ("ESMA35-43-3448", "ESMA/2016/1477", "JC/GL/2024/36",
                   "BoR (11) 67", "BoR (10) 44 Rev 1"):
        assert guidance_issuers.own_number_slug(number) == \
            lagrum.own_number_slug(number)
