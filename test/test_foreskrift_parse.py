"""Hermetic (PDF-free) tests for the föreskrift parser: the text-based block
classification, the kapitel/paragraf nesting + SFS anchors, and the best-effort
masthead metadata extraction. The live PDF extraction (``lib.pdftext``) is
exercised against the downloaded corpus during a batch parse, not here."""

import shutil
import sqlite3
from pathlib import Path

from ferenda.lib.pdftext import Para
from ferenda.lib.text import node_text, runs_text
from ferenda.foreskrift import structure
from ferenda.foreskrift import parse as fp
from ferenda.foreskrift.parse import (PARSE_TYPES, classify,
                                           extract_metadata, _iso,
                                           _body_start, _ingress_start,
                                           _dedupe_bemyndigande,
                                           konsoliderad_tom, amendment_uri,
                                           andrar_target,
                                           masthead_amendments, parse_record,
                                           clean_title, title_from_masthead)
from ferenda.foreskrift.model import Block, printed_designation
from ferenda.foreskrift import render as fs_render
from ferenda.foreskrift.render import _andrad_genom, _konsoliderad_banner
from ferenda.lib import catalog
from ferenda.lib.page import Site
from ferenda.lib.lagrum import sfs_parser


# --- classify: text-based markers survive a fontless (scanned) PDF ----------

def test_classify_reads_markers_from_text_not_font():
    paras = [Para("1 kap. Inledande bestämmelser", bold=False),
             Para("1 § Dessa föreskrifter gäller för x.", bold=False),
             Para("2 a § Vid tillämpning av 1 § gäller följande.", bold=False),
             Para("Definitioner", bold=True),
             Para("Ett vanligt stycke som bara är löpande text.", bold=False)]
    assert [(b.kind, b.text, b.num) for b in classify(paras, 1)] == [
        ("kapitel", "1 kap. Inledande bestämmelser", "1"),
        ("paragraf", "1 § Dessa föreskrifter gäller för x.", "1"),
        ("paragraf", "2 a § Vid tillämpning av 1 § gäller följande.", "2a"),
        ("rubrik", "Definitioner", None),
        ("stycke", "Ett vanligt stycke som bara är löpande text.", None)]


def test_classify_does_not_take_a_list_row_as_a_heading():
    # a short bold-less numbered list row must stay a stycke, not a numbered rubrik
    [block] = classify([Para("1. första punkten i en lista", bold=True)], 1)
    assert block.kind == "stycke"


# --- structure.nest: statute-shaped tree + SFS anchors ----------------------

def _b(kind, text, num=None):
    d = {"type": kind, "text": [text]}
    if num:
        d["num"] = num
    return d


def test_nest_builds_statute_shaped_tree_with_anchors():
    tree = structure.nest([
        _b("kapitel", "1 kap. X", "1"),
        _b("paragraf", "1 § a", "1"),
        _b("stycke", "andra stycket av 1 §"),
        _b("paragraf", "2 § b", "2"),
        _b("kapitel", "2 kap. Y", "2"),
        _b("paragraf", "1 § c", "1")])
    assert [n["type"] for n in tree] == ["kapitel", "kapitel"]
    k1 = tree[0]
    assert k1["id"] == "K1" and k1["ordinal"] == "1"
    # a kapitel leads with its title as a rubrik child, then its paragrafer
    assert [c["type"] for c in k1["children"]] == ["rubrik", "paragraf", "paragraf"]
    assert k1["children"][0]["text"] == ["1 kap. X"]
    p1 = k1["children"][1]
    assert p1["id"] == "K1P1" and p1["ordinal"] == "1"
    # the paragraf's body is a stycke child; the "1 §" marker is stripped
    assert p1["children"][0] == {"type": "stycke", "text": ["a"], "page": None}
    assert p1["children"][1]["text"] == ["andra stycket av 1 §"]
    assert tree[1]["children"][1]["id"] == "K2P1"   # § numbering restarts per kap


def test_nest_chapterless_paragraf_anchor_is_bare_p():
    tree = structure.nest([_b("paragraf", "3 § ensam", "3")])
    assert tree[0]["id"] == "P3"
    assert tree[0]["children"][0]["text"] == ["ensam"]   # marker stripped


def test_nest_keeps_a_section_with_no_paragraf_at_all():
    # a short declarative föreskrift / a förteckning: stycken, not one §
    tree = structure.nest([_b("stycke", "Dessa föreskrifter ska fortsätta gälla.")])
    assert tree == [{"type": "stycke", "text": ["Dessa föreskrifter ska fortsätta gälla."]}]


def test_flatten_roundtrips_nesting():
    blocks = [_b("kapitel", "1 kap. X", "1"), _b("paragraf", "1 § a", "1"),
              _b("stycke", "s")]
    flat = structure.flatten(structure.nest(blocks))
    # kapitel head, its title rubrik, the paragraf head, its body stycke, then "s"
    assert [b["type"] for b in flat] == ["kapitel", "rubrik", "paragraf", "stycke", "stycke"]


# --- metadata: best-effort masthead facts -----------------------------------

def test_iso_parses_swedish_dates():
    assert _iso("25", "juni", "2013") == "2013-06-25"
    assert _iso("5", "juli", "2013") == "2013-07-05"
    assert _iso("1", "inte-en-månad", "2013") is None
    assert _iso("1", "maj", None) is None


def test_dedupe_bemyndigande_prefers_paragraf_over_bare_law():
    # the bare 2013:587 is dropped (its #P4 is more precise); result is sorted
    assert _dedupe_bemyndigande({
        "https://lagen.nu/2013:587", "https://lagen.nu/2013:587#P4",
        "https://lagen.nu/2001:100#P5"}) == [
        "https://lagen.nu/2001:100#P5", "https://lagen.nu/2013:587#P4"]


def test_pinpointed_abbreviation_links_bare_mention_does_not():
    # KORTLAGRUM is enabled for föreskrifter (2026-08-15): "32 § LVU" names
    # a provision and links; a bare "enligt LVU" says nothing about the law
    # and stays plain text (its trigger requires the pinpoint).
    parser = sfs_parser("foreskrift", PARSE_TYPES)
    refs = parser.parse_text("Läkarundersökning enligt 32 § LVU ska ske.")
    assert [r.uri for r in refs] == ["https://lagen.nu/1990:52#P32"]
    assert parser.parse_text("Vård enligt LVU skall beredas den unge.") == []


def test_body_start_skips_the_masthead_to_the_first_marker():
    blocks = [Block("rubrik", "Finansinspektionens författningssamling", 1),
              Block("stycke", "beslutade den 25 juni 2013. … föreskriver följande", 1),
              Block("kapitel", "1 kap. Innehåll", 1, num="1"),
              Block("paragraf", "1 § …", 1, num="1")]
    assert _body_start(blocks) == 2          # drop the two masthead blocks


def test_body_start_no_marker_falls_back_to_preamble_verb():
    blocks = [Block("stycke", "Naturvårdsverkets författningssamling", 1),
              Block("stycke", "Med stöd av 1 § kungör Naturvårdsverket följande", 1),
              Block("stycke", "den egentliga förteckningen börjar här", 1)]
    assert _body_start(blocks) == 2          # past the "kungör" preamble verb


def test_ingress_is_kept_as_the_documents_own_opening_words():
    # the preamble states the bemyndigande the föreskrift rests on (18 b §
    # författningssamlingsförordningen). It sits between the masthead's last
    # furniture line and the first §, and used to be dropped with the masthead.
    blocks = [Block("rubrik", "Myndigheten för civilt försvars författningssamling", 1),
              Block("stycke", "Utgivare: Anna Asp ISSN 3119-2394", 1),
              Block("stycke", "Myndighetens föreskrifter om säkerhetsåtgärder;", 1),
              Block("stycke", "beslutade den 15 juni 2026.", 1),
              Block("stycke", "Myndigheten föreskriver följande med stöd av 38 §.", 1),
              Block("paragraf", "1 § …", 1, num="1")]
    assert _ingress_start(blocks, _body_start(blocks)) == 3


# --- konsolideradTom: the one fact that pins a consolidation -----------------

def test_konsoliderad_tom_is_the_most_recent_amendment_not_a_date():
    # FFFS masthead lists incorporated amendments; the last is the data point
    masthead = ("FFFS 2013:10 Konsoliderad elektronisk utgåva Senast uppdaterad: "
                "2026-06-03 Ändringar: FFFS 2014:29, FFFS 2017:7, FFFS 2024:27, FFFS 2026:6")
    assert konsoliderad_tom(masthead, "fffs", "2013", "10") == "https://lagen.nu/fffs/2026:6"


def test_konsoliderad_tom_handles_three_letter_fs_codes():
    # NFS/TFS/BFS have only one letter before 'FS'; the regex must still match them
    masthead = "NFS 2014:29 Denna version innehåller ändringar till och med NFS 2026:5"
    assert konsoliderad_tom(masthead, "nfs", "2014", "29") == "https://lagen.nu/nfs/2026:5"


def test_konsoliderad_tom_none_when_only_the_base_appears():
    assert konsoliderad_tom("FFFS 2013:10 konsoliderad", "fffs", "2013", "10") is None


def test_konsoliderad_tom_folds_designation_to_the_fs_slug():
    # the printed 'ELSÄK-FS' (Ä + hyphen) must match the 'elsakfs' slug
    masthead = "ELSÄK-FS 2012:1 Ändringar: ELSÄK-FS 2017:4, ELSÄK-FS 2018:2"
    assert konsoliderad_tom(masthead, "elsakfs", "2012", "1") == "https://lagen.nu/elsakfs/2018:2"


def test_extract_metadata_lifts_dates_bemyndigande_and_directive():
    text = ("Finansinspektionens föreskrifter; FFFS 2013:10 "
            "Utkom från trycket den 5 juli 2013 beslutade den 25 juni 2013. "
            "Finansinspektionen föreskriver följande med stöd av 4 och 5 §§ "
            "förordningen (2013:587) om förvaltare av alternativa investeringsfonder. "
            "Jfr Europaparlamentets och rådets direktiv 2011/61/EU av den 8 juni 2011. "
            "Denna författning träder i kraft den 22 juli 2013.")
    meta = extract_metadata(text, "", sfs_parser("foreskrift", PARSE_TYPES))
    assert meta["beslutsdatum"] == "2013-06-25"
    assert meta["utkomFranTryck"] == "2013-07-05"
    assert meta["ikrafttradandedatum"] == "2013-07-22"
    assert "https://lagen.nu/2013:587#P4" in meta["bemyndigande"]
    assert "https://lagen.nu/2013:587#P5" in meta["bemyndigande"]
    assert meta["genomfor"] == ["https://lagen.nu/ext/celex/32011L0061"]


# --- ikraftträdande: which of the printed dates is the document's own -------
#
# Every text below is the real wording of the named föreskrift, cut down to the
# masthead and the sentences carrying a date. Taking the first date always (the
# behaviour until 2026-08-08) dated 329 föreskrifter before the day they were
# decided.

def test_ikraft_date_of_an_amendment_is_its_own_not_the_reprinted_base():
    # SJÖFS 2006:39, decided 2006-11-22: it reprints SJÖFS 2005:25, whose
    # provision says 2006-01-01, and states its own last.
    masthead = ("Sjöfartsverkets författningssamling Föreskrifter och allmänna råd "
                "om ändring i Sjöfartsverkets föreskrifter och allmänna råd "
                "(SJÖFS 2005:25) om skyddsanordningar och skyddsåtgärder på fartyg; "
                "beslutade den 22 november 2006.")
    text = (masthead + " Ikraftträdande- och övergångsbestämmelser "
            "1. Denna författning träder i kraft den 1 januari 2006. "
            "________________ "
            "Denna författning träder i kraft den 1 januari 2007. "
            "På Sjöfartsverkets vägnar")
    assert fp.ikrafttradande_date(text, masthead) == "2007-01-01"


def test_ikraft_date_of_a_base_regulation_ignores_appended_amendment_blocks():
    # AFS 1999:4's shape: the grundförfattning's own provision first, the block
    # of an amendment printed after it -- the mirror image of the case above,
    # and why the rule cannot simply be "take the last one".
    masthead = ("Arbetarskyddsstyrelsens föreskrifter om tryckbärande anordningar; "
                "beslutade den 17 juni 1999. Arbetarskyddsstyrelsen föreskriver "
                "följande med stöd av 18 § arbetsmiljöförordningen.")
    text = (masthead + " Ikraftträdande och övergångsbestämmelser "
            "Dessa föreskrifter träder i kraft den 29 november 1999. "
            "__________________ "
            "Dessa föreskrifter träder i kraft den 1 mars 2001.")
    assert fp.ikrafttradande_date(text, masthead) == "1999-11-29"


def test_ikraft_date_skips_a_date_that_is_not_the_documents_own():
    # TSFS 2010:12 opens with a footnote about IMO resolutions that "träder i
    # kraft den 1 juli 2006" -- somebody else's date, four years before this
    # föreskrift was decided.
    masthead = ("Transportstyrelsens föreskrifter och allmänna råd om "
                "navigationssäkerhet och navigationsutrustning; beslutade den "
                "26 januari 2010. Transportstyrelsen föreskriver följande med "
                "stöd av 2 kap. 1 § fartygssäkerhetsförordningen (2003:438).")
    text = (masthead + " Ändringarna antogs av IMO vid MSC:s 77:e session genom "
            "resolutionerna MSC.142(77) och MSC.170(79) och träder i kraft den "
            "1 juli 2006. Ikraftträdande- och övergångsbestämmelser "
            "1. Dessa föreskrifter träder i kraft den 1 april 2010.")
    assert fp.ikrafttradande_date(text, masthead) == "2010-04-01"


def test_ikraft_date_reads_omtryck_and_the_amending_formula_as_an_amendment():
    # SKSFS 2014:3 declares no "om ändring i" in its title -- it is printed as
    # an Omtryck of SKSFS 2011:2 and uses the amending enacting formula.
    masthead = ("Skogsstyrelsens föreskrifter och allmänna råd (SKSFS 2011:2) om "
                "stöd till vissa åtgärder inom skogsbruket; Omtryck beslutade den "
                "4 juni 2014. Skogsstyrelsen föreskriver med stöd av 17 § "
                "förordningen (2010:1879) om stöd till vissa åtgärder inom "
                "skogsbruket, i fråga om Skogsstyrelsens föreskrifter att ...")
    text = (masthead + " ------- Denna författning träder i kraft den 1 maj 2011 . "
            "------- Denna författning träder i kraft den 1 juli 2014 .")
    assert fp.ikrafttradande_date(text, masthead) == "2014-07-01"


def test_ikraft_date_of_a_consolidated_text_is_the_base_regulations_own():
    # CSNFS 1998:7, decided 1998: the base regulation printed with every later
    # amendment folded in. Its masthead names those amendments, so it reads as
    # an ändringsförfattning -- and taking the last date made a 1998 föreskrift
    # come into force in 2026. The consolidation note has to win.
    masthead = ("Centrala studiestödsnämndens föreskrifter och allmänna råd "
                "(CSNFS 1998:7) om ersättning till deltagare i "
                "teckenspråksutbildning för vissa föräldrar "
                "Grundförfattningen i dess lydelse med införda ändringar "
                "omtryckt CSNFS 2009:3 ändrad CSNFS 2023:8 CSNFS 2025:3")
    text = (masthead + " Till CSNFS 2005:7 Denna författning träder i kraft den "
            "1 juli 2005 och gäller för studier från och med samma datum. "
            "Till CSNFS 2023:8 Denna författning träder i kraft den 1 januari 2024. "
            "Till CSNFS 2025:3 Dessa föreskrifter och allmänna råd träder i kraft "
            "den 1 januari 2026.")
    assert fp.ikrafttradande_date(text, masthead) == "2005-07-01"


def test_ikraft_date_reads_the_declaration_from_the_title_when_the_masthead_is_gone():
    # SJVFS 2015:18's shape, driven through the real wiring: the page opens with
    # a running head that classifies as a rubrik and then goes straight into
    # "1 §", so `_body_start` leaves a masthead of three words and the "om
    # ändring i" declaration never reaches the parser. 259 föreskrifter have a
    # masthead this thin; for 38 of them the harvest title is the only place the
    # declaration survives, and without it they fall back to the first date.
    blocks = [Block("rubrik", "GRUNDLÄGGANDE BESTÄMMELSER", 1),
              Block("paragraf", "1 § Dessa föreskrifter gäller stöd.", 1, num="1"),
              Block("stycke", "Denna författning träder i kraft den 12 mars 2015.", 2),
              Block("stycke", "Denna författning träder i kraft den 11 maj 2015.", 2)]
    masthead = fp._full_text(blocks[:_body_start(blocks)])
    assert masthead == "GRUNDLÄGGANDE BESTÄMMELSER"      # the declaration is not in it
    title = "Föreskrifter om ändring i Statens jordbruksverks föreskrifter (SJVFS 2015:2)"
    text = fp._full_text(blocks)
    assert fp.ikrafttradande_date(text, fp.role_declaration(masthead, None)) == "2015-03-12"
    assert fp.ikrafttradande_date(text, fp.role_declaration(masthead, title)) == "2015-05-11"


def test_ikraft_date_keeps_an_unrecognised_subject_rather_than_losing_the_date():
    # No sentence names a subject the census saw, so the filter must not empty
    # the candidate list and drop the only date the document prints.
    text = "Bestämmelserna i bilaga 1 träder i kraft den 1 januari 2020."
    assert fp.ikrafttradande_date(text, "") == "2020-01-01"


def test_ikraft_date_is_none_when_the_document_states_no_date():
    assert fp.ikrafttradande_date("Dessa föreskrifter gäller tills vidare.", "") is None


def test_extract_metadata_upphaver_from_the_transitional_passive_clause():
    # PMFS 2022:1's shape: the repeal sits in the ikraftträdande provisions as
    # a passive "Genom föreskrifterna upphävs … (PMFS 2019:2)". An earlier
    # bare provision repeal ("5 § upphävs.") names no regulation and must not
    # stop the scan at the first match.
    text = ("Säkerhetspolisens föreskrifter om säkerhetsskydd; "
            "5 § upphävs. "
            "1. Dessa föreskrifter träder i kraft den 1 mars 2022. "
            "2. Genom föreskrifterna upphävs Säkerhetspolisens föreskrifter "
            "om säkerhetsskydd (PMFS 2019:2).")
    meta = extract_metadata(text, "", sfs_parser("foreskrift", PARSE_TYPES))
    assert meta["upphaver"] == ["https://lagen.nu/pmfs/2019:2"]


def test_extract_metadata_upphaver_folds_designation_to_the_fs_slug():
    # 'ÅFS' must mint aafs/… (the registered slug), never a dangling åfs/… --
    # a naive lower() broke the repeal-subduing for every ÅFS/RÅFS document
    text = ("Åklagarmyndighetens föreskrifter om expediering; "
            "Föreskrifterna ersätter Åklagarmyndighetens föreskrifter "
            "(ÅFS 2005:6) om expediering.")
    meta = extract_metadata(text, "", sfs_parser("foreskrift", PARSE_TYPES))
    assert meta["upphaver"] == ["https://lagen.nu/aafs/2005:6"]


def test_printed_designation_names_a_regulation_the_corpus_does_not_hold():
    # a repealed predecessor series nobody harvests still has to be *named* in
    # the Upphäver row; without this the reader was shown the slug
    assert printed_designation("https://lagen.nu/rpsfs/2011:16") == "RPSFS 2011:16"
    assert printed_designation("https://lagen.nu/aafs/2005:6") == "ÅFS 2005:6"
    assert printed_designation("https://lagen.nu/sjofs/2006:39") == "SJÖFS 2006:39"
    # an SFS paragraf is not a regulation designation
    assert printed_designation("https://lagen.nu/1977:1166#P18") is None


def test_andrad_genom_unions_the_register_with_the_inbound_edge():
    # SJÖFS 2005:25's agency never listed its amendments, but SJÖFS 2006:39 says
    # in its own title that it amends it -- so the page said nothing at all
    art = {"amendments": [{"uri": "https://lagen.nu/sjofs/2008:66",
                           "identifier": "SJÖFS 2008:66"}]}
    rows = [("https://lagen.nu/sjofs/2006:39", "SJÖFS 2006:39", "…"),
            ("https://lagen.nu/sjofs/2008:66", "SJÖFS 2008:66", "…")]
    assert _andrad_genom(art, rows) == [
        ("https://lagen.nu/sjofs/2006:39", "SJÖFS 2006:39"),
        ("https://lagen.nu/sjofs/2008:66", "SJÖFS 2008:66")]   # deduped, sorted


def test_konsoliderad_banner_spells_the_cutoff_the_way_the_page_does():
    """The cutoff amendment is normally in the harvest register and the banner
    quotes its printed identifier. Where it is not, the banner has to derive
    one -- and upcasing the URI slug wrote "SJOFS 2006:39" beside the header
    row's "SJÖFS 2006:39" on the same page."""
    banner = _konsoliderad_banner(
        {"uri": "https://lagen.nu/sjofs/2003:12", "amendments": [], "structure": []},
        Site(None, set()), "https://lagen.nu/sjofs/2006:39")
    assert "SJÖFS 2006:39" in banner
    assert "SJOFS" not in banner


def test_undouble_keeps_the_whole_copy_of_a_title_printed_twice():
    # SJÖFS 2005:25 prints its title once as a page header and again in the
    # ingress, and the masthead scan runs from the first straight into the
    # second, so the page read as a stammer.
    assert fp.undouble(
        "Sjöfartsverkets föreskrifter och allmänna råd om skyddsanordningar och "
        "Sjöfartsverkets föreskrifter och allmänna råd om skyddsanordningar "
        "och skyddsåtgärder på fartyg") == (
        "Sjöfartsverkets föreskrifter och allmänna råd om skyddsanordningar "
        "och skyddsåtgärder på fartyg")


def test_undouble_leaves_a_title_that_merely_repeats_a_phrase():
    # an ändringsförfattning names the regulation it amends, so its own
    # designation phrase recurs -- but the head does not begin what follows it
    title = ("Föreskrifter och allmänna råd om ändring i Sjöfartsverkets "
             "föreskrifter och allmänna råd (SJÖFS 2005:25) om skyddsanordningar")
    assert fp.undouble(title) == title
    # nor is a repeal title a doubling
    repeal = ("Sjöfartsverkets föreskrifter om upphävande av Sjöfartsverkets "
              "föreskrifter (SJÖFS 1990:1) om lotsning")
    assert fp.undouble(repeal) == repeal


# --- titles: harvest link chrome vs the PDF's own rubric (F7) ----------------

def test_clean_title_keeps_a_real_title_and_strips_link_chrome():
    assert clean_title("Totalförsvarets rekryteringsmyndighets föreskrifter "
                       "om totalförsvarsplikt (TRMFS 2017:1) pdf, 281 kB.",
                       "TRMFS 2017:1") == \
        ("Totalförsvarets rekryteringsmyndighets föreskrifter "
         "om totalförsvarsplikt (TRMFS 2017:1)")
    # a leading restatement of the record's own designation goes too
    assert clean_title("SiSUVFS 2024:1 - Statens institutionsstyrelses "
                       "föreskrifter om anvisning av plats",
                       "SiSUVFS 2024:1") == \
        "Statens institutionsstyrelses föreskrifter om anvisning av plats"


def test_clean_title_rejects_chrome_only_titles():
    # observed harvest "titles" that are file chrome or role labels, not titles
    for raw, ident in [("DIFS 2018:1     (pdf, 63 kB)", "DIFS 2018:1"),
                       ("TFS 2004:35 Pdf, 278.1 kB, öppnas i nytt fönster.",
                        "TFS 2004:35"),
                       (".pdf", "MRTVFS 2011:1"),
                       ("2016:4", "SJÖFS 2016:4"),
                       ("KKVFS 2025:1", "KKVFS 2025:1"),
                       ("2023:1, M:11", "PRVFS 2023:1"),
                       ("Grundförfattning (MDFFS 2019:1)", "MDFFS 2019:1"),
                       (None, "EIFS 2011:1")]:
        assert clean_title(raw, ident) is None, raw


def test_title_is_read_from_the_masthead_not_the_body():
    """The title is printed in the masthead, above the operative body. This
    used to search the blocks *past* `_body_start`, where it has already been
    left behind, and so found one only where the body happened to repeat it --
    1,736 föreskrifter were left titled by their own number."""
    blocks = [Block("rubrik", "Åklagarmyndighetens författningssamling", 1),
              Block("rubrik", "Åklagarmyndighetens föreskrifter om åklagarkamrarnas "
                         "lokalisering och verksamhetsområden; beslutade den "
                         "1 december 2006.", 1),
              Block("stycke", "Åklagarmyndigheten föreskriver följande.", 1),
              Block("kapitel", "1 kap. Inledande bestämmelser", 1)]
    assert title_from_masthead(blocks, 3) == \
        ("Åklagarmyndighetens föreskrifter om åklagarkamrarnas "
         "lokalisering och verksamhetsområden")
    assert title_from_masthead([Block("stycke", "1 § Denna föreskrift.", 1)],
                               1) is None


def test_title_survives_the_second_column_landing_inside_it():
    """The masthead is set in two columns, so extraction drops the right-hand
    column's standing text into the middle of the title sentence. Cutting there
    would keep "Skolverkets föreskrifter" and lose the subject; the standing
    text is deleted instead, which rejoins the sentence that was printed."""
    blocks = [Block("rubrik", "Statens skolverks författningssamling ISSN 1102-1950", 1),
              Block("stycke", "Skolverkets föreskrifter Utkom från trycket den 21 "
                         "mars 2012 om betygskatalog för vuxenutbildning; "
                         "beslutade den 8 mars 2012.", 1),
              Block("kapitel", "1 kap. Inledande bestämmelser", 1)]
    assert title_from_masthead(blocks, 2) == \
        "Skolverkets föreskrifter om betygskatalog för vuxenutbildning"


def test_the_amended_regulations_number_stays_in_the_title():
    """An ändringsförfattning names the regulation it amends by number, which is
    the one place a designation belongs in a title -- so a parenthesis is held
    back from the removal that strips the masthead's own FS number."""
    blocks = [Block("rubrik", "Läkemedelsverkets författningssamling ISSN 1101-5225", 1),
              Block("stycke", "Föreskrifter om ändring i Läkemedelsverkets "
                         "föreskrifter (LVFS 1997:13) om förskrivning av vissa "
                         "livsmedel; beslutade den 5 mars 2013.", 1),
              Block("kapitel", "1 kap. Inledande", 1)]
    assert title_from_masthead(blocks, 2) == \
        ("Föreskrifter om ändring i Läkemedelsverkets föreskrifter "
         "(LVFS 1997:13) om förskrivning av vissa livsmedel")


def test_the_utgivare_does_not_become_part_of_the_agency_name():
    """Two adjacent capitalised words are a boundary, not one name: the second
    column puts "Utgivare: Gunilla Hedwall" directly before "Säkerhetspolisens
    föreskrifter om säkerhetsskydd"."""
    blocks = [Block("rubrik", "Polismyndighetens författningssamling", 1),
              Block("stycke", "ISSN 2002-0139 Utgivare: Gunilla Hedwall", 1),
              Block("rubrik", "Säkerhetspolisens föreskrifter om säkerhetsskydd;", 1),
              Block("stycke", "Utkom från trycket beslutade den 31 januari 2022. "
                         "den 4 februari 2022", 1),
              Block("kapitel", "1 kap. Allmänna bestämmelser", 1)]
    assert title_from_masthead(blocks, 4) == \
        "Säkerhetspolisens föreskrifter om säkerhetsskydd"


def test_parse_record_mints_andrar_from_the_pdf_rubric(tmp_path, monkeypatch):
    # a chrome-titled record whose body rubric declares the ändring: the
    # andrar edge is minted from the resolved title, not the discarded chrome
    monkeypatch.setattr(fp, "parse_pdf", lambda *a, **kw: ([], {
        "title": "Föreskrifter om ändring i Konkurrensverkets föreskrifter "
                 "(KKVFS 2021:1) om kartellbekämpning",
        "upphaver": [], "bemyndigande": [], "genomfor": [], "andrar": [],
        "beslutsdatum": None, "utkomFranTryck": None,
        "ikrafttradandedatum": None, "publisher": None}, []))
    record = {"fs": "kkvfs", "basefile": "kkvfs/2025:2",
              "identifier": "KKVFS 2025:2",
              "title": "KKVFS 2025:2 (pdf, 90 kB)",
              "files": {"regulation": {"name": "r.pdf"}}}
    reg = parse_record(record, tmp_path)
    assert reg.title.startswith("Föreskrifter om ändring")
    assert reg.andrar == ["https://lagen.nu/kkvfs/2021:1"]


def test_parse_record_prefers_pdf_rubric_over_chrome_title(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "parse_pdf", lambda *a, **kw: ([], {
        "title": "Konkurrensverkets föreskrifter om kartellbekämpning",
        "upphaver": [], "bemyndigande": [], "genomfor": [], "andrar": [],
        "beslutsdatum": None, "utkomFranTryck": None,
        "ikrafttradandedatum": None, "publisher": None}, []))
    record = {"fs": "kkvfs", "basefile": "kkvfs/2025:1",
              "identifier": "KKVFS 2025:1", "title": "KKVFS 2025:1",
              "files": {"regulation": {"name": "r.pdf"}}}
    reg = parse_record(record, tmp_path)
    assert reg.title == "Konkurrensverkets föreskrifter om kartellbekämpning"


# --- amendments: minted uris + preserved source urls (review C3) -------------

def test_amendment_uri_minted_from_the_identifiers_own_fs_code():
    # folded to the slug form, mixed-prefix graphs (RPSFS base, PMFS
    # amendments) mint under the amendment's own samling
    assert amendment_uri("ELSÄK-FS 2026:27") == "https://lagen.nu/elsakfs/2026:27"
    assert amendment_uri("PMFS 2020:5") == "https://lagen.nu/pmfs/2020:5"
    # the registry overrides the naive åäö transliteration: ÅFS is aafs (afs is
    # Arbetsmiljöverkets samling), RÅFS is raafs (rafs is Riksarkivets RA-FS)
    assert amendment_uri("ÅFS 2006:3") == "https://lagen.nu/aafs/2006:3"
    assert amendment_uri("RÅFS 1998:1") == "https://lagen.nu/raafs/1998:1"
    assert amendment_uri("FFFS 2014:07") == "https://lagen.nu/fffs/2014:7"
    assert amendment_uri(None) is None          # unreadable link text
    assert amendment_uri("Ändringsregister") is None


def test_parse_record_mints_amendment_uris_and_keeps_source_urls(tmp_path):
    # no regulation PDF in the record -> hermetic; amendments must carry a
    # minted uri (never "") and the agency's own link (previously dropped)
    record = {"fs": "elsakfs", "basefile": "elsakfs/2013:10",
              "identifier": "ELSÄK-FS 2013:10",
              "files": {"amendment": [
                  {"identifier": "ELSÄK-FS 2026:27", "url": "https://ex/a.pdf"},
                  {"identifier": None, "url": "https://ex/b.pdf"}]}}
    reg = parse_record(record, tmp_path)
    known, unreadable = reg.amendments
    assert known.identifier == "ELSÄK-FS 2026:27"
    assert known.uri == "https://lagen.nu/elsakfs/2026:27"
    assert known.url == "https://ex/a.pdf"
    assert unreadable.identifier is None and unreadable.uri is None
    assert unreadable.url == "https://ex/b.pdf"


def test_parse_record_drops_self_upphaver(tmp_path, monkeypatch):
    # LIVSFS 2022:4's upphäver clause restates its own designation; a
    # regulation never replaces itself
    monkeypatch.setattr(fp, "parse_pdf", lambda *a, **kw: ([], {
        "upphaver": ["https://lagen.nu/livsfs/2022:4",
                     "https://lagen.nu/livsfs/2005:20"],
        "bemyndigande": [], "genomfor": [], "andrar": [],
        "beslutsdatum": None, "utkomFranTryck": None,
        "ikrafttradandedatum": None, "publisher": None}, []))
    record = {"fs": "livsfs", "basefile": "livsfs/2022:4",
              "identifier": "LIVSFS 2022:4",
              "files": {"regulation": {"name": "r.pdf"}}}
    reg = parse_record(record, tmp_path)
    assert reg.upphaver == ["https://lagen.nu/livsfs/2005:20"]


def test_parse_record_dedupes_twice_listed_consolidation(tmp_path, monkeypatch):
    # fffs/2015:12's landing page lists the same konsoliderad PDF twice; two
    # identical Consolidations would masquerade as two historical versions.
    # A *distinct* second consolidation (a genuinely archived older one, as on
    # bfs/2007:5) must survive. The agency url rides into the model.
    bodies = {"a.pdf": ([{"id": "P1"}], [], "https://lagen.nu/fffs/2016:13", []),
              "b.pdf": ([{"id": "P1"}], [], "https://lagen.nu/fffs/2016:13", []),
              "c.pdf": ([{"id": "P1", "old": True}], [],
                        "https://lagen.nu/fffs/2014:2", [])}
    monkeypatch.setattr(fp, "parse_consolidation",
                        lambda path, *a: bodies[path.name])
    record = {"fs": "fffs", "basefile": "fffs/2015:12",
              "identifier": "FFFS 2015:12",
              "files": {"consolidation": [
                  {"name": "a.pdf", "url": "https://ex/k.pdf"},
                  {"name": "b.pdf", "url": "https://ex/k.pdf"},
                  {"name": "c.pdf", "url": "https://ex/gammal.pdf"}]}}
    reg = parse_record(record, tmp_path)
    assert len(reg.consolidations) == 2
    assert reg.consolidations[0].url == "https://ex/k.pdf"
    assert reg.consolidations[1].konsolideradTom == "https://lagen.nu/fffs/2014:2"


def test_parse_record_folds_masthead_amendments_into_the_register(tmp_path,
                                                                  monkeypatch):
    # the konsoliderad masthead names the amendments folded in; ones the
    # landing page didn't list join the register (with minted uris), ones it
    # did stay single entries (the landing url wins)
    monkeypatch.setattr(fp, "parse_consolidation", lambda path, *a: (
        [{"id": "P1"}], [], "https://lagen.nu/fffs/2017:7",
        [("FFFS", "2014", "29"), ("FFFS", "2017", "7")]))
    record = {"fs": "fffs", "basefile": "fffs/2013:10",
              "identifier": "FFFS 2013:10",
              "files": {"consolidation": [{"name": "k.pdf", "url": "https://ex/k"}],
                        "amendment": [
                            {"identifier": "FFFS 2014:29", "url": "https://ex/a"}]}}
    reg = parse_record(record, tmp_path)
    assert [(a.identifier, a.uri, a.url) for a in reg.amendments] == [
        ("FFFS 2014:29", "https://lagen.nu/fffs/2014:29", "https://ex/a"),
        ("FFFS 2017:7", "https://lagen.nu/fffs/2017:7", None)]


# --- andrar: the amendment's own title names its target ----------------------

def test_andrar_target_reads_the_first_ref_after_the_andring_phrase():
    uri = "https://lagen.nu/aafs/2006:11"
    assert andrar_target("Åklagarmyndighetens föreskrifter om ändring i "
                         "Åklagarmyndighetens föreskrifter (ÅFS 2005:5) om "
                         "åklagarkamrarnas lokalisering", "aafs", uri) \
        == "https://lagen.nu/aafs/2005:5"
    # chained: "(ÅFS 2006:3) om ändring i (ÅFS 2005:5)" amends 2006:3 directly
    assert andrar_target("föreskrifter om ändring i föreskrifter (ÅFS 2006:3) "
                         "om ändring i föreskrifter (ÅFS 2005:5)", "aafs", uri) \
        == "https://lagen.nu/aafs/2006:3"
    # a mixed-prefix graph mints under the target's own samling
    assert andrar_target("ändring i föreskrifterna (KAMFS 2012:3, TRAFAFS "
                         "2012:3) om uppgifter", "kamfs", uri) \
        == "https://lagen.nu/kamfs/2012:3"
    assert andrar_target("föreskrifter om åklagarväsendet", "aafs", uri) is None


def test_andrar_target_own_series_implied_and_self_excluded():
    # "föreskrifter (2007:12)" drops the designation -- the possessive title
    # implies the record's own fs; an SFS parenthesis must never mint a target
    uri = "https://lagen.nu/aafs/2010:2"
    assert andrar_target("Åklagarmyndighetens föreskrifter om ändring i "
                         "Åklagarmyndighetens föreskrifter (2007:12) om "
                         "internationellt samarbete", "aafs", uri) \
        == "https://lagen.nu/aafs/2007:12"
    assert andrar_target("föreskrifter om ändring som avses i förordningen "
                         "(2001:512) om deponering", "aafs", uri) is None
    # a title restating the record's own designation is never the target
    assert andrar_target("Ändring av FFS 2017:9", "ffs",
                         "https://lagen.nu/ffs/2017:9") is None


# --- konsoliderad HTML (the frozen SOSFS/HSLF-FS konsolidering corpus) -------

KONSOLIDERING_HTML = Path(__file__).parent / "files/foreskrift/konsolidering.html"


def test_parse_consolidation_html_builds_statute_tree_and_cutoff():
    struct, notes, tom, refs = fp.parse_consolidation_html(KONSOLIDERING_HTML,
                                                    sfs_parser("foreskrift", PARSE_TYPES))
    # the cutoff is the numerically latest ref on the "Ändrad:" line, minted
    # under its own samling (HSLF-FS beats SOSFS 2013:6: the series transition)
    assert tom == "https://lagen.nu/hslffs/2017:27"
    assert refs == [("SOSFS", "2013", "6"), ("HSLF-FS", "2017", "27")]
    assert notes == []          # an HTML page has no page-foot rule
    # h2 -> kapitel, h3 -> rubrik, p with "N §" -> paragraf; the h1 page title
    # and the three preamble lines never reach the body
    assert [n["id"] for n in struct] == ["K1", "K2"]
    assert struct[0]["children"][1]["id"] == "K1P1"
    full = " ".join(node_text(n) for n in struct)
    assert "informationssystem" in full
    assert "första punkten i en lista" in full          # li rows stay stycken
    assert "Observera att" not in full
    assert "Senaste version av" not in full
    assert "Meny som aldrig" not in full                 # chrome outside <main>


def test_parse_record_routes_html_consolidation(tmp_path):
    (tmp_path / "sosfs").mkdir()
    shutil.copyfile(KONSOLIDERING_HTML,
                    tmp_path / "sosfs" / "sosfs-2008-1-consolidation-0.html")
    record = {"fs": "sosfs", "basefile": "sosfs/2008:1",
              "identifier": "SOSFS 2008:1",
              "files": {"consolidation": [
                  {"name": "sosfs-2008-1-consolidation-0.html",
                   "url": "https://sos.example/2008-1"}]}}
    reg = parse_record(record, tmp_path)
    [cons] = reg.consolidations
    assert cons.konsolideradTom == "https://lagen.nu/hslffs/2017:27"
    assert cons.url == "https://sos.example/2008-1"
    assert cons.structure                                 # parsed body
    # the Ändrad-line refs join the register, each under its own samling
    assert [(a.identifier, a.uri) for a in reg.amendments] == [
        ("SOSFS 2013:6", "https://lagen.nu/sosfs/2013:6"),
        ("HSLF-FS 2017:27", "https://lagen.nu/hslffs/2017:27")]


def test_masthead_amendments_lists_this_fs_sorted_base_excluded():
    masthead = ("FFFS 2013:10 Konsoliderad Ändringar: FFFS 2017:7, "
                "FFFS 2014:29, NFS 2015:1, FFFS 2013:10")
    assert masthead_amendments(masthead, "fffs", "2013", "10") == [
        ("FFFS", "2014", "29"), ("FFFS", "2017", "7")]


# --------------------------------------------------------------------------
# the bemyndigande ingress clause (18 b § författningssamlingsförordningen)
# --------------------------------------------------------------------------

def _bemyndigande(text):
    """The SFS uris a föreskrift's ingress clause delegates from."""
    clause = fp.stodav_clause(text)
    if clause is None:
        return None
    return sorted(r.uri.replace("https://lagen.nu/", "")
                  for r in sfs_parser("foreskrift", PARSE_TYPES).parse_text(
                      clause, context={})
                  if r.predicate.endswith("references"))


def test_bemyndigande_clause_survives_a_chaptered_delegation():
    """18 b § makes the bemyndigande mandatory, so a missing one is a parser
    failure. The clause used to end at the first `.`, and a delegation almost
    always runs through a chapter -- "7 kap. 7 § fastighetstaxeringslagen" was
    cut at the abbreviation dot to "7 kap", yielding no citation at all. That
    one character left 44% of the corpus with no bemyndigande."""
    assert _bemyndigande(
        "Skatteverket föreskriver med stöd av 7 kap. 7 § fastighetstaxerings"
        "lagen (1979:1152) och 6 kap. 1 § första stycket fastighetstaxerings"
        "förordningen (1993:1199) följande.") == \
        ["1979:1152#K7P7", "1993:1199#K6P1S1"]
    assert _bemyndigande(
        "Säkerhetspolisen föreskriver med stöd av 8 kap. 7 § säkerhetsskydds"
        "förordningen (2021:955) följande.") == ["2021:955#K8P7"]


def test_bemyndigande_clause_stops_before_the_sentence_that_follows():
    """The sentence a clause runs into is usually the first provision, which
    opens with a digit ("… om den officiella statistiken. 1 § Dessa …"), so the
    boundary cannot be "a period followed by a capital" either."""
    assert _bemyndigande(
        "Statistiska centralbyrån föreskriver följande med stöd av 15 § "
        "förordningen (2001:100) om den officiella statistiken. 1 § Dessa "
        "föreskrifter innehåller kompletterande bestämmelser till "
        "kommissionens genomförandeförordning (EU) 2020/1197.") == ["2001:100#P15"]


def test_bemyndigande_clause_excludes_the_foreskrift_an_amendment_amends():
    """Past "att"/"i fråga om" an ändringsförfattning names the föreskrift it
    amends; that is its target, not the delegation it is issued under."""
    assert _bemyndigande(
        "Tullverket föreskriver med stöd av 1 kap. 5 § tullförordningen "
        "(2016:287) i fråga om Tullverkets föreskrifter och allmänna råd "
        "(TFS 2016:2) om en tullordning att 1 kap. 5 § ska ha följande "
        "lydelse.") == ["2016:287#K1P5"]


def test_bemyndigande_verb_boundary_keeps_the_delegating_act():
    """The preamble verbs are word-anchored: unanchored, "kungör" matched
    inside "kungörelsen" and dropped the act the delegation comes from."""
    assert _bemyndigande(
        "Med stöd av 13 § kungörelsen (1958:272) om tjänstekort meddelar "
        "rikspolisstyrelsen följande.") == ["1958:272#P13"]


def test_no_bemyndigande_clause_is_not_an_empty_one():
    assert _bemyndigande("Riksdagsdirektören föreskriver följande.") is None


# --- the page shapes the prose reflow used to lose ---------------------------

def test_a_bullet_list_glued_into_one_stycke_becomes_a_lista():
    # poppler sets the bullet as its own run, so the character survives the
    # reflow that folds the items into one paragraph -- 7 688 blocks of the
    # corpus carried at least one, all read as running text
    [lead, lista] = fp._split_bullets(Block(
        "stycke", "Ledningens utbildning bör omfatta • ledningens roll, "
                  "• riskhantering, samt • interna regler.", 3))
    assert (lead.kind, lead.text) == ("stycke", "Ledningens utbildning bör omfatta")
    assert lista.kind == "lista"
    assert [c.text for c in lista.children] == [
        "ledningens roll,", "riskhantering, samt", "interna regler."]


def test_a_stycke_without_a_bullet_is_left_alone():
    block = Block("stycke", "Verksamhetsutövaren ska bedriva arbetet.", 4)
    assert fp._split_bullets(block) == [block]


def test_allmanna_rad_are_grouped_under_the_paragraf_they_explain():
    # a råd runs from its heading to the next structural marker: it is advisory
    # text, and left flat it read as further stycken of the binding paragraf
    blocks = fp._group_allmanna_rad([
        Block("paragraf", "1 § Utbildningen ska ge ledningen kunskap.", 3, num="1"),
        Block("stycke", "Allmänna råd", 3),
        Block("stycke", "Ledningens utbildning bör omfatta terminologi.", 3),
        Block("kapitel", "3 kap. Organisatoriska säkerhetsåtgärder", 4, num="3"),
        Block("paragraf", "1 § Verksamhetsutövaren ska bedriva arbetet.", 4, num="1"),
    ])
    assert [b.kind for b in blocks] == \
        ["paragraf", "allmanna_rad", "kapitel", "paragraf"]
    assert [c.text for c in blocks[1].children] == \
        ["Ledningens utbildning bör omfatta terminologi."]


def test_the_rad_heading_keeps_the_provision_it_names():
    # "Allmänna råd till 3 §" is the heading's own words and names what the råd
    # explains, so it becomes the section's label rather than being discarded
    blocks = fp._group_allmanna_rad([
        Block("paragraf", "3 § Den intagne ska underrättas.", 2, num="3"),
        Block("stycke", "Allmänna råd till 3 §", 2),
        Block("stycke", "Underrättelsen bör lämnas skriftligen.", 2),
    ])
    assert blocks[1].kind == "allmanna_rad"
    assert blocks[1].text == "Allmänna råd till 3 §"


def test_a_rad_heading_with_nothing_under_it_stays_a_heading():
    # the page broke under the heading: keep the text rather than drop it
    [para, head] = fp._group_allmanna_rad([
        Block("paragraf", "1 § …", 1, num="1"),
        Block("stycke", "Allmänna råd", 1),
    ])
    assert (head.kind, head.text) == ("rubrik", "Allmänna råd")


def test_rubriker_are_ranked_by_size_under_the_chapter_heading():
    # every heading of a chaptered föreskrift used to come out level 1, so the
    # table of contents read flat -- chapter and subheading side by side
    blocks = [Block("rubrik", "Myndighetens författningssamling", 1, size=24),
              Block("kapitel", "1 kap. Inledande bestämmelser", 1, num="1", size=24),
              Block("rubrik", "Tillämpningsområde", 1, size=22),
              Block("paragraf", "1 § …", 1, num="1", size=18),
              Block("rubrik", "Undantag", 1, size=20)]
    fp._rank_rubriker(blocks, _body_start(blocks))
    # the masthead heading is outside the body and stays unranked; inside it the
    # 22-point rubrik is level 2 and the 20-point one level 3
    assert [b.level for b in blocks] == [None, None, 2, None, 3]


def test_a_stray_bold_glyph_is_not_a_heading():
    # MCFFS 2026:11 sets a bold 8-point "." on page 12, which reached the table
    # of contents as a heading named "."
    assert classify([Para(".", bold=True)], 12)[0].kind == "stycke"


# --- end to end over a real PDF (MCFFS 2026:11) ------------------------------

MCFFS_PDF = Path(__file__).parent / "files/foreskrift/mcffs-2026-11.pdf"


def test_mcffs_2026_11_reads_every_page_shape():
    """One document that prints all six shapes the reflow used to flatten: an
    ingress, a footnote under a page-foot rule, an ordförklaringar table over a
    page break, nested headings, allmänna råd and bullet lists."""
    struct, meta, notes = fp.parse_pdf(MCFFS_PDF, "MCFFS 2026:11",
                                       sfs_parser("foreskrift", PARSE_TYPES))
    nodes = list(structure.flatten(struct))

    # the ingress opens the document, past the masthead's title sentence
    assert struct[0]["type"] == "ingress"
    assert node_text(struct[0]["children"][0]) == "beslutade den 15 juni 2026."
    assert "med stöd av 38 § p. 5" in node_text(struct[0]["children"][1])

    # the "Jfr … direktiv" note sits under the rule, not inside 1 kap. 2 §
    assert [n["mark"] for n in notes] == ["1", "2"]
    assert "2022/2555" in node_text(notes[0])
    # …and its text no longer trails 1 kap. 2 § as a stycke of the provision.
    # The rule before the ikraftträdande clause (page 27) is body text and stays.
    assert not any("2022/2555" in node_text(n) for n in nodes)

    # the chapter heading is level 1 and its subheadings level 2
    kap1 = struct[1]
    assert kap1["id"] == "K1" and kap1["children"][0]["level"] == 1
    assert [n["level"] for n in kap1["children"] if n["type"] == "rubrik"] == \
        [1, 2, 2]

    # the ordförklaringar table is one table across the page break, its repeated
    # "Begrepp / Betydelse" header dropped
    [tabell] = [n for n in nodes if n["type"] == "tabell"]
    rows = tabell["children"]
    assert len(rows) == 14 and rows[0].get("th") is True
    assert runs_text(rows[0]["cells"][0]).strip() == "Begrepp"
    # a term that wraps in the left column keeps its definition beside it
    assert runs_text(rows[2]["cells"][0]).strip() == \
        "information i behov av utökat skydd"

    # the advisory text under 2 kap. 1 § is a råd, and its bullets a list
    [rad] = [n for n in structure.flatten(struct[2]["children"])
             if n["type"] == "allmanna_rad"]
    assert node_text(rad).startswith("Allmänna råd")
    [lista] = [n for n in rad["children"] if n["type"] == "lista"]
    assert len(lista["children"]) == 4
    assert node_text(lista["children"][0]).startswith("ledningens roll")

    # and none of it moved the metadata the masthead carries
    assert meta["beslutsdatum"] == "2026-06-15"
    assert meta["ikrafttradandedatum"] == "2026-10-01"
    assert meta["bemyndigande"] == ["https://lagen.nu/2025:1507"]


def test_the_rad_heading_is_citation_scanned_and_reaches_the_document_text():
    """The råd's heading is held under `text`, not a key of its own: `lib.text`
    collects `text` and nothing else, and the whole document's plain text is
    what feeds the search index — held as a `label` it left 687 words of a
    371-document sample unsearchable. Under `text` it is also scanned, so
    "Allmänna råd till 2 kap. 1 § … häkteslagen (2010:611)" links the provision
    it explains."""
    parser = sfs_parser("foreskrift", PARSE_TYPES)
    [rad] = fp._structure([Block("allmanna_rad",
                                 "Allmänna råd till 2 kap. 1 § andra stycket "
                                 "häkteslagen (2010:611)", 2,
                                 children=[Block("stycke", "Bör lämnas.", 2)])],
                          parser)
    assert node_text(rad).startswith("Allmänna råd till 2 kap. 1 §")
    assert [r["uri"] for r in rad["text"] if isinstance(r, dict)] == \
        ["https://lagen.nu/2010:611#K2P1S2"]


SKVFS_PDF = Path(__file__).parent / "files/foreskrift/skvfs-2006-32.pdf"


def test_a_page_foot_note_still_reaches_the_metadata_scan():
    """The notes are read for metadata with the body, because a föreskrift
    prints metadata *as* a page-foot note: SKVFS 2006:32 sets its own
    ikraftträdande clause under the rule, in the small type its template uses,
    and KIFS 2017:7 grounds the directive it transposes in a "Jfr …" note.
    Measured over 1 500 regulations, the notes changed metadata on exactly those
    two shapes and never replaced a value the body had stated."""
    struct, meta, notes = fp.parse_pdf(SKVFS_PDF, "SKVFS 2006:32",
                                       sfs_parser("foreskrift", PARSE_TYPES))
    assert notes and "träder i kraft den 1 januari 2007" in node_text(notes[0])
    assert meta["ikrafttradandedatum"] == "2007-01-01"
    # and the note is not left in the body as a stycke of the last provision
    assert not any("tillämpas för beskattningsåret 2007" in node_text(n)
                   for n in structure.flatten(struct))


def test_a_rad_stops_at_the_documents_closing_matter():
    """A råd explains the § above it; it cannot reach past the operative body
    into the ikraftträdande clause and the signature. Left running, TFS 2009:2,
    KVFS 2021:2 and RPSFS 2011:12 each set binding text inside the advisory box,
    under a label saying it is not binding — the inverse of what the section is
    for. ~180 regulations print a råd in that position."""
    for closer in ("Denna författning träder i kraft den 1 juni 2009.",
                   "___________",
                   "Dessa föreskrifter och allmänna råd träder i kraft "
                   "den 1 maj 2021."):
        blocks = fp._group_allmanna_rad([
            Block("paragraf", "36 § En ansökan ska göras till Tullverket.", 9,
                  num="36"),
            Block("stycke", "Allmänna råd", 9),
            Block("stycke", "Ansökan bör prövas med stor noggrannhet.", 9),
            Block("stycke", closer, 9),
            Block("stycke", "TULLVERKET KARIN STARRIN", 9),
        ])
        assert [b.kind for b in blocks] == \
            ["paragraf", "allmanna_rad", "stycke", "stycke"], closer
        assert [c.text for c in blocks[1].children] == \
            ["Ansökan bör prövas med stor noggrannhet."], closer


def test_a_rad_ending_inside_the_closing_block_is_cut_not_moved():
    """The reflow glues a råd's last paragraph to the clause that follows it.
    KVFS 2021:2 prints the råd's closing sentence and "___________ Dessa
    föreskrifter … träder i kraft den 1 maj 2021." as one paragraph; moving the
    whole block out ended the råd mid-sentence, and IAFFS 2025:5 — whose råd is
    that one paragraph — lost its råd entirely."""
    blocks = fp._group_allmanna_rad([
        Block("paragraf", "4 § En intagen får meddelas särskilda villkor.", 3,
              num="4"),
        Block("stycke", "Allmänna råd", 3),
        Block("stycke", "Ett sådant behov föreligger normalt inte. "
                        "___________ Dessa föreskrifter och allmänna råd "
                        "träder i kraft den 1 maj 2021.", 3),
    ])
    assert [b.kind for b in blocks] == ["paragraf", "allmanna_rad", "stycke"]
    assert [c.text for c in blocks[1].children] == \
        ["Ett sådant behov föreligger normalt inte."]
    assert blocks[2].text == ("___________ Dessa föreskrifter och allmänna råd "
                              "träder i kraft den 1 maj 2021.")


def test_a_rad_keeps_an_ikrafttradande_it_only_quotes():
    # the closer tests for *this* document's entry into force, the same way
    # `ikrafttradande_date` does — a råd discussing another act's stays a råd
    blocks = fp._group_allmanna_rad([
        Block("paragraf", "3 § Tillstånd får ges.", 4, num="3"),
        Block("stycke", "Allmänna råd", 4),
        Block("stycke", "Bestämmelsen bör läsas mot den ändring som "
                        "träder i kraft den 1 juli 2020.", 4),
    ])
    assert [b.kind for b in blocks] == ["paragraf", "allmanna_rad"]
    assert len(blocks[1].children) == 1


def test_a_symbol_font_bullet_splits_like_an_ordinary_one():
    # SKSFS 2014:7 sets its bullets in Symbol (U+F0B7) and prints not one
    # U+2022; a guard naming only U+2022 left all 90 of its items glued
    [lead, lista] = fp._split_bullets(Block(
        "stycke", "Utbildningen bör omfatta  första punkten, "
                  " andra punkten.", 2))
    assert lead.text == "Utbildningen bör omfatta"
    assert [c.text for c in lista.children] == ["första punkten,", "andra punkten."]


def test_a_presented_consolidation_prints_its_own_notes_not_the_bases():
    """A konsoliderad version is a different document from the base regulation,
    so listing the base's page-foot notes under it would print numbered notes
    about a text the reader is not looking at. `presented_consolidation` picks
    the body; the notes have to follow the same pick."""
    art = {
        "uri": "https://lagen.nu/fffs/2013:10", "identifier": "FFFS 2013:10",
        "type": "foreskrift", "metadata": {},
        "structure": [{"type": "stycke", "text": ["Ursprunglig lydelse."]}],
        "footnotes": [{"mark": "1", "text": ["Basregelns egen not."]}],
        "consolidations": [{
            "of": "https://lagen.nu/fffs/2013:10", "konsolideradTom": None,
            "structure": [{"type": "stycke", "text": ["Konsoliderad lydelse."]}],
            "footnotes": [{"mark": "1", "text": ["Konsolideringens egen not."]}],
        }],
        "amendments": [],
    }
    con = sqlite3.connect(":memory:")
    con.executescript(catalog.SCHEMA)          # an empty but real catalog
    html = fs_render.render(art, Site(con, set()))
    assert "Konsolideringens egen not." in html
    assert "Basregelns egen not." not in html
