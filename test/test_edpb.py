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

from accommodanda.edpb import download as edpb_download
from accommodanda.edpb import parse as edpb_parse
from accommodanda.edpb.model import (
    Block,
    Fotnot,
    Vagledning,
    vagledning_identifier,
    vagledning_uri,
)
from accommodanda.edpb.series import (
    HARVESTED,
    HBDI,
    KODER,
    REGISTRY,
    WP29,
    WP29_BY_SLUG,
    number_slug,
)
from accommodanda.lib import catalog, facets, labels, layout, render
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
    ("riktlinjer", "05/2020", "https://lagen.nu/edpb/riktlinjer/05-2020",
     "Riktlinjer 05/2020"),
    # the EDPB pads the löpnummer in some years and not others; one document has
    # one address however it was written
    ("riktlinjer", "5/2020", "https://lagen.nu/edpb/riktlinjer/05-2020",
     "Riktlinjer 5/2020"),
    ("rekommendationer", "01/2019",
     "https://lagen.nu/edpb/rekommendationer/01-2019", "Rekommendation 01/2019"),
    ("wp", "248", "https://lagen.nu/edpb/wp/248", "WP 248"),
])
def test_identity(serie, nummer, uri, identifier):
    assert vagledning_uri(serie, nummer) == uri
    assert vagledning_identifier(serie, nummer) == identifier


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
    assert vagledning_identifier("wp", "artikel-30-5") == wp.citation
    # every other one is cited by its number, and states its own title
    assert vagledning_identifier("wp", "248") == "WP 248"
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
    fields = dict(serie="riktlinjer", nummer="05/2020",
                  titel="Riktlinjer 05/2020 om samtycke", antagen="2020-05-04",
                  body=[Block("rubrik", "1 INLEDNING", 2),
                        Block("stycke", "1. Inledningsvis gäller detta.",
                              punkt="1"),
                        Block("stycke", "Ett stycke utan eget nummer.")])
    return Vagledning(**{**fields, **kwargs}).to_artifact(_NoRefs())


def test_artifact_anchors_a_numbered_punkt_on_its_own_number():
    art = _artifact()
    assert art["uri"] == "https://lagen.nu/edpb/riktlinjer/05-2020"
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
    assert art["uri"] == "https://lagen.nu/edpb/wp/248"
    assert art["identifier"] == "WP 248"
    assert art["metadata"]["publisher"] == "Artikel 29-gruppen"
    assert art["metadata"]["revision"] == "rev.01"


# --------------------------------------------------------------------------
# the citation grammar
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def refparser():
    return LagrumParser({}, basefile="edpb",
                        parse_types=[EULAGSTIFTNING, VAGLEDNING])


@pytest.mark.parametrize("text,expected", [
    ("Se riktlinjer 05/2020 om samtycke.",
     ["https://lagen.nu/edpb/riktlinjer/05-2020"]),
    # the definite and singular forms Swedish prose writes just as often
    ("enligt riktlinjerna 8/2022 punkt 12",
     ["https://lagen.nu/edpb/riktlinjer/08-2022"]),
    ("riktlinjen 4/2019 om inbyggt dataskydd",
     ["https://lagen.nu/edpb/riktlinjer/04-2019"]),
    # the EDPB itself alternates singular and plural for its recommendations
    ("Rekommendation 01/2020 om åtgärder",
     ["https://lagen.nu/edpb/rekommendationer/01-2020"]),
    ("Rekommendationer 02/2020 om garantierna",
     ["https://lagen.nu/edpb/rekommendationer/02-2020"]),
    # the artikel 29-gruppens own numbering, spaced or not, with or without rev
    ("artikel 29-gruppens riktlinjer om dataskyddsombud (WP 243)",
     ["https://lagen.nu/edpb/wp/243"]),
    ("se WP248 rev.01 avsnitt III", ["https://lagen.nu/edpb/wp/248"]),
    # a padded and an unpadded citation to one document are one address
    ("riktlinjer 1/2018 och riktlinjer 01/2018",
     ["https://lagen.nu/edpb/riktlinjer/01-2018",
      "https://lagen.nu/edpb/riktlinjer/01-2018"]),
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
        == ["https://lagen.nu/edpb/wp/259"]


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
    assert layout.relpath("edpb", "riktlinjer/05-2020") == \
        Path("riktlinjer/05-2020")
    assert layout.relpath("edpb", "wp/248") == Path("wp/248")
    assert layout.SOURCE_DIR["edpb"] == "edpb"


def test_catalog_rows_carry_the_series_as_the_kind():
    art = _artifact()
    uri, source, kind, label, title, path = catalog.document_row(art, "x.json", "edpb")
    assert (source, kind, label) == ("edpb", "riktlinjer", "Riktlinjer 05/2020")
    assert title == "Riktlinjer 05/2020 om samtycke"


def test_labels_name_an_english_only_document_as_one():
    swedish = labels.document_labels("edpb", _artifact())
    assert swedish.descriptive_label == "Riktlinjer 05/2020"
    english = labels.document_labels("edpb", _artifact(sprak="en"))
    assert english.descriptive_label == "Riktlinjer 05/2020 (engelsk version)"


def test_the_browse_scheme_is_series_then_year():
    levels = facets.SCHEMES["edpb"]
    assert [level.name for level in levels] == ["Serie", "År"]


def test_edpb_browses_under_the_eu_ratt_masthead_entry():
    """The nav decision: EDPB guidance has no CELEX and is not a rättsakt, so it
    is a source of its own -- but it belongs beside the förordning it interprets,
    the way hudoc sits under folkrätt rather than getting a masthead entry."""
    assert render.BROWSE_DIR["edpb"] == "eurlex/vagledning"
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
    catalog.rebuild(db, "edpb", paths)
    con = catalog.connect(db)
    groups = render.eurlex_axis(con)
    # eurlex is empty in this catalog, so only the EDPB group is offered -- the
    # selector is built from what the corpus holds, not from a fixed list
    assert [axis for axis, _entries in groups] == ["EDPB:s vägledningar"]
    assert [label for _key, label, _url, _count in groups[0][1]] == [
        "Riktlinjer", "Rekommendationer", "Artikel 29-gruppens vägledningar"]


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
    art = Vagledning(
        serie="riktlinjer", nummer="05/2020", titel="Riktlinjer 05/2020",
        body=[Block("stycke", "Se riktlinjerna nedan.")],
        fotnoter=[Fotnot("16", "Se WP 248 och riktlinjer 3/2019.")],
    ).to_artifact(LagrumParser({}, basefile="edpb",
                               parse_types=[EULAGSTIFTNING, VAGLEDNING]))
    assert [x["uri"] for x in art["footnotes"][0]["text"]
            if isinstance(x, dict)] == ["https://lagen.nu/edpb/wp/248",
                                        "https://lagen.nu/edpb/riktlinjer/03-2019"]


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
    ("EDPB:s riktlinjer 05/2020", "https://lagen.nu/edpb/riktlinjer/05-2020"),
    ("Europeiska dataskyddsstyrelsens riktlinjer 3/2019",
     "https://lagen.nu/edpb/riktlinjer/03-2019"),
    ("Dataskyddsstyrelsens riktlinjer 07/2020",
     "https://lagen.nu/edpb/riktlinjer/07-2020"),
    ("Styrelsens riktlinjer 07/2020", "https://lagen.nu/edpb/riktlinjer/07-2020"),
    ("Artikel 29-gruppens riktlinjer 1/2018",
     "https://lagen.nu/edpb/riktlinjer/01-2018"),
    # the bare form, which is what the guidance itself and IMY both write once
    # the board has been named
    ("Se riktlinjer 05/2020.", "https://lagen.nu/edpb/riktlinjer/05-2020"),
    # sentence-initial: the capitalised "I" used to read as another issuer's
    # name because the exemption list was case-sensitive (RE_EDPB_SELF,
    # 2026-08-15 audit R9)
    ("I dataskyddsstyrelsens riktlinjer 05/2020 anges vidare",
     "https://lagen.nu/edpb/riktlinjer/05-2020"),
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
        == ["https://lagen.nu/edpb/riktlinjer/03-2019"]


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
