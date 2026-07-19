"""Hermetic (PDF-free) tests for the föreskrift parser: the text-based block
classification, the kapitel/paragraf nesting + SFS anchors, and the best-effort
masthead metadata extraction. The live PDF extraction (``lib.pdftext``) is
exercised against the downloaded corpus during a batch parse, not here."""

import shutil
from pathlib import Path

from accommodanda.lib.pdftext import Para
from accommodanda.lib.text import node_text
from accommodanda.foreskrift import structure
from accommodanda.foreskrift import parse as fp
from accommodanda.foreskrift.parse import (classify, extract_metadata, _iso,
                                           _body_start, _dedupe_bemyndigande,
                                           konsoliderad_tom, _fresh_parser,
                                           amendment_uri, andrar_target,
                                           masthead_amendments, parse_record)


# --- classify: text-based markers survive a fontless (scanned) PDF ----------

def test_classify_reads_markers_from_text_not_font():
    paras = [Para("1 kap. Inledande bestämmelser", bold=False),
             Para("1 § Dessa föreskrifter gäller för x.", bold=False),
             Para("2 a § Vid tillämpning av 1 § gäller följande.", bold=False),
             Para("Definitioner", bold=True),
             Para("Ett vanligt stycke som bara är löpande text.", bold=False)]
    assert classify(paras) == [
        ("kapitel", "1 kap. Inledande bestämmelser", "1"),
        ("paragraf", "1 § Dessa föreskrifter gäller för x.", "1"),
        ("paragraf", "2 a § Vid tillämpning av 1 § gäller följande.", "2a"),
        ("rubrik", "Definitioner", None),
        ("stycke", "Ett vanligt stycke som bara är löpande text.", None)]


def test_classify_does_not_take_a_list_row_as_a_heading():
    # a short bold-less numbered list row must stay a stycke, not a numbered rubrik
    [(kind, _t, _n)] = classify([Para("1. första punkten i en lista", bold=True)])
    assert kind == "stycke"


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


def test_body_start_skips_the_masthead_to_the_first_marker():
    blocks = [("rubrik", "Finansinspektionens författningssamling", 1, None),
              ("stycke", "beslutade den 25 juni 2013. … föreskriver följande", 1, None),
              ("kapitel", "1 kap. Innehåll", 1, "1"),
              ("paragraf", "1 § …", 1, "1")]
    assert _body_start(blocks) == 2          # drop the two masthead blocks


def test_body_start_no_marker_falls_back_to_preamble_verb():
    blocks = [("stycke", "Naturvårdsverkets författningssamling", 1, None),
              ("stycke", "Med stöd av 1 § kungör Naturvårdsverket följande", 1, None),
              ("stycke", "den egentliga förteckningen börjar här", 1, None)]
    assert _body_start(blocks) == 2          # past the "kungör" preamble verb


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
    meta = extract_metadata(text, _fresh_parser())
    assert meta["beslutsdatum"] == "2013-06-25"
    assert meta["utkomFranTryck"] == "2013-07-05"
    assert meta["ikrafttradandedatum"] == "2013-07-22"
    assert "https://lagen.nu/2013:587#P4" in meta["bemyndigande"]
    assert "https://lagen.nu/2013:587#P5" in meta["bemyndigande"]
    assert meta["genomfor"] == ["https://lagen.nu/ext/celex/32011L0061"]


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
    meta = extract_metadata(text, _fresh_parser())
    assert meta["upphaver"] == ["https://lagen.nu/pmfs/2019:2"]


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
        "ikrafttradandedatum": None, "publisher": None}))
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
    bodies = {"a.pdf": ([{"id": "P1"}], "https://lagen.nu/fffs/2016:13", []),
              "b.pdf": ([{"id": "P1"}], "https://lagen.nu/fffs/2016:13", []),
              "c.pdf": ([{"id": "P1", "old": True}],
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
        [{"id": "P1"}], "https://lagen.nu/fffs/2017:7",
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
    struct, tom, refs = fp.parse_consolidation_html(KONSOLIDERING_HTML,
                                                    _fresh_parser())
    # the cutoff is the numerically latest ref on the "Ändrad:" line, minted
    # under its own samling (HSLF-FS beats SOSFS 2013:6: the series transition)
    assert tom == "https://lagen.nu/hslffs/2017:27"
    assert refs == [("SOSFS", "2013", "6"), ("HSLF-FS", "2017", "27")]
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
