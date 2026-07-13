"""Run the original legalref test corpus against the new Lark-based
lagrum recognizer (accommodanda.lagrum).

Each test file (windows-1252) holds plaintext input, a blank line, and
the expected output as a <list> serialization of alternating <str> and
<Link uri="..."> elements. Input may hold several paragraphs separated
by "---" lines, optionally prefixed with state directives (BASE:{...},
NOBASE:, RESET:).

The new recognizer reports one span per reference *expression* rather
than the old engine's per-token spans, so the comparison here is the
ordered sequence of link URIs, which both engines agree on.

Only the grammars the new pipeline has ported are driven: SFS (lagrum)
and EGLag (EU legislation). The other directories (Short, Simple,
Regpubl, DV, ECJ, Avg) cover parser types that belong to later stages
of the rewrite.
"""

import ast
import re
import time
from pathlib import Path

import pytest

from accommodanda.lib.datasets import NAMEDACTS
from accommodanda.lib.datasets import NAMEDLAWS as SFS_NAMEDLAWS
from accommodanda.lib.lagrum import (
    ALL_PARSE_TYPES,
    ENKLALAGRUM,
    EULAGSTIFTNING,
    EURATTSFALL,
    FORARBETEN,
    LAGRUM,
    MYNDIGHETSBESLUT,
    RATTSFALL,
    LagrumParser,
    Ref,
    build_trigger,
    interleave,
    load_abbreviations,
    load_namedacts,
    load_namedlaws,
    yield_overlaps,
)
from accommodanda.lib.util import normalize_space

TESTROOT = Path(__file__).parent / "files" / "legalref"
NAMEDLAWS = load_namedlaws(SFS_NAMEDLAWS)
ABBREVIATIONS = load_abbreviations(SFS_NAMEDLAWS)
NAMEDACTS_MAP = load_namedacts(NAMEDACTS)

# Tests the old engine also failed (its driver listed them as broken);
# the expected output in these files is hand-authored desired behavior.
OLD_BROKEN = {
    "sfs-tricky-bokstavslista",
    "sfs-tricky-eller",
    "sfs-tricky-eller-paragrafer-stycke",
    "sfs-tricky-overgangsbestammelse",
    "sfs-tricky-uppdelat-lagnamn",
    "sfs-tricky-vvfs",
}


def expected_uris(want):
    return re.findall(r'<Link uri="([^"]+)"', want)


def run_testfile(path, abbreviations=None, parse_types=None):
    raw = path.read_text(encoding="windows-1252")
    parts = re.split(r"\r?\n\r?\n", raw, maxsplit=1)
    testdata = parts[0]
    want = parts[1].strip() if len(parts) > 1 else ""

    parser = LagrumParser(NAMEDLAWS, basefile="9999:999",
                          abbreviations=abbreviations, parse_types=parse_types)
    got = []
    for para in re.split(r"\r?\n---\r?\n", testdata):
        # like the old driver: only BASE: strips its directive line --
        # RESET:/NOBASE: markers stay in the text and parse as plain words
        context = {"law": "9999:999"}
        if para.startswith("RESET:"):
            parser.state.namedlaws.clear()
        elif para.startswith("NOBASE:"):
            context = {}
        elif para.startswith("BASE:"):
            head, para = para.split("\n", 1)
            context = ast.literal_eval(head.split(":", 1)[1])
        refs = parser.parse_text(normalize_space(para), context=context)
        got.extend(ref.uri for ref in refs)
    return got, expected_uris(want)


def make_params(subdir):
    for path in sorted((TESTROOT / subdir).glob("*.txt")):
        marks = ([pytest.mark.xfail(reason="old engine failed this too",
                                    strict=False)]
                 if path.stem in OLD_BROKEN else [])
        yield pytest.param(path, id=path.stem, marks=marks)


@pytest.mark.parametrize("path", make_params("SFS"))
def test_sfs(path):
    got, want = run_testfile(path)
    assert got == want


@pytest.mark.parametrize("path", make_params("EGLag"))
def test_eglag(path):
    got, want = run_testfile(path)
    assert got == want


@pytest.mark.parametrize("path", make_params("Short"))
def test_short(path):
    got, want = run_testfile(path, abbreviations=ABBREVIATIONS)
    assert got == want


@pytest.mark.parametrize("path", make_params("DV"))
def test_rattsfall(path):
    got, want = run_testfile(path, parse_types=[RATTSFALL])
    assert got == want


@pytest.mark.parametrize("path", make_params("Regpubl"))
def test_forarbeten(path):
    got, want = run_testfile(path, parse_types=[FORARBETEN])
    assert got == want


# Multi-page förarbete refs ("s. 445 och 454", "s. 162-165", "s. 400, 505,
# 509 och 511", "a. prop. s. 48, 50") emit one #sid{n} link per page. The
# URI-only test above never exercised their *spans*, so every page link
# defaulted to the whole match window and the links overlapped -- which
# blew up interleave() in the real parse pipeline. These cases assert the
# per-page link boundaries: the first page folds in the leading document
# text, later pages link the bare number (as the golden corpus draws them).
FORARB_PAGE_CASES = [
    ("(jfr prop. 2017/18:232 s. 445 och 454)", None, [
        ("prop. 2017/18:232 s. 445", "https://lagen.nu/prop/2017/18:232#sid445"),
        ("454", "https://lagen.nu/prop/2017/18:232#sid454")]),
    ("prop. 2017/18:105 s. 162-165", None, [
        ("prop. 2017/18:105 s. 162", "https://lagen.nu/prop/2017/18:105#sid162"),
        ("165", "https://lagen.nu/prop/2017/18:105#sid165")]),
    ("prop. 2021/22:136 s. 400, 505, 509 och 511", None, [
        ("prop. 2021/22:136 s. 400", "https://lagen.nu/prop/2021/22:136#sid400"),
        ("505", "https://lagen.nu/prop/2021/22:136#sid505"),
        ("509", "https://lagen.nu/prop/2021/22:136#sid509"),
        ("511", "https://lagen.nu/prop/2021/22:136#sid511")]),
    # "a. prop." (anförd proposition) resolves against the last prop seen
    ("a. prop. s. 48, 50", "https://lagen.nu/prop/2017/18:105", [
        ("a. prop. s. 48", "https://lagen.nu/prop/2017/18:105#sid48"),
        ("50", "https://lagen.nu/prop/2017/18:105#sid50")]),
]


@pytest.mark.parametrize("text,last_prop,links", FORARB_PAGE_CASES,
                         ids=[c[0] for c in FORARB_PAGE_CASES])
def test_forarb_page_spans(text, last_prop, links):
    parser = LagrumParser(NAMEDLAWS, basefile="9999:999",
                          parse_types=[FORARBETEN])
    if last_prop:
        parser.state.last_forarbete = last_prop
    refs = parser.parse_text(text, context={"law": "9999:999"})
    # interleave asserts the spans are disjoint; the link runs it produces
    # pin down each page's exact text boundary
    runs = [(run["text"], run["uri"])
            for run in interleave(text, refs) if isinstance(run, dict)]
    assert runs == links


# An authority-decision citation naming several diarienummer ("dnr X och Y")
# is several separate references sharing a prefix, not one -- each must link
# its own dnr token so the spans stay disjoint. Before this was fixed every
# dnr link defaulted to the whole match window and they overlapped, blowing
# up interleave() in the real sfs/forarbete parse pipeline.
AVG_MULTI_DNR_CASES = [
    ("JO:s beslut den 25 juni 2007, dnr 3940-2006 och 3941-2006", [
        ("3940-2006", "https://lagen.nu/avg/jo/3940-2006"),
        ("3941-2006", "https://lagen.nu/avg/jo/3941-2006")]),
    ("JO 2011/12 s. 471, dnr 6823-2009 och 2196-2010", [
        ("6823-2009", "https://lagen.nu/avg/jo/6823-2009"),
        ("2196-2010", "https://lagen.nu/avg/jo/2196-2010")]),
    ("dnr 1505-80-22 och 2551-80-21", [
        ("1505-80-22", "https://lagen.nu/avg/jk/1505-80-22"),
        ("2551-80-21", "https://lagen.nu/avg/jk/2551-80-21")]),
    # single dnr: still just its own token, leading text stays plain
    ("JO 2013/14 s. 392, dnr 2914-2011", [
        ("2914-2011", "https://lagen.nu/avg/jo/2914-2011")]),
]


@pytest.mark.parametrize("text,links", AVG_MULTI_DNR_CASES,
                         ids=[c[0] for c in AVG_MULTI_DNR_CASES])
def test_avg_multi_dnr_spans(text, links):
    parser = LagrumParser({}, basefile="avg", parse_types=[MYNDIGHETSBESLUT])
    refs = parser.parse_text(text, context={})
    runs = [(run["text"], run["uri"])
            for run in interleave(text, refs) if isinstance(run, dict)]
    assert runs == links


def test_yield_overlaps_term_yields_to_citation():
    # a defined term ("upphovsrättslagen") is often also a named-law reference
    # on the same span; the term-use link must yield so interleave sees no
    # overlap. Disjoint term uses survive.
    cite = Ref(0, 8, "1960:729", "dcterms:references", "https://lagen.nu/1960:729")
    same = Ref(0, 8, "1960:729", "dcterms:subject", "https://lagen.nu/begrepp/X",
               kind="term")
    inside = Ref(2, 6, "60:7", "dcterms:subject", "https://lagen.nu/begrepp/Y",
                 kind="term")
    disjoint = Ref(9, 12, "abc", "dcterms:subject", "https://lagen.nu/begrepp/Z",
                   kind="term")
    assert yield_overlaps([same, inside, disjoint], [cite]) == [disjoint]


# The repo's ECJ fixtures (test/files/legalref/ECJ) are unusable as an
# oracle: the old driver flagged both as broken, they carry no expected
# output, and the files are UTF-8 (U+2011) while the harness reads
# windows-1252. Validate EURATTSFALL against a hand-authored table instead.
EURATTSFALL_CASES = [
    ("In Case C-176/09 the court", "https://lagen.nu/ext/celex/62009CJ0176"),
    ("mål C-197/09 RX-II,", "https://lagen.nu/ext/celex/62009CJ0197"),
    ("By order in Case F-23/07", "https://lagen.nu/ext/celex/62007CW0023"),
    ("i mål T-201/04", "https://lagen.nu/ext/celex/62004CA0201"),
    ("C-176/09", "https://lagen.nu/ext/celex/62009CJ0176"),
    ("Case C‑197/09", "https://lagen.nu/ext/celex/62009CJ0197"),
]


@pytest.mark.parametrize("text,uri", EURATTSFALL_CASES)
def test_eurattsfall(text, uri):
    parser = LagrumParser(NAMEDLAWS, basefile="x", parse_types=[EURATTSFALL])
    assert [r.uri for r in parser.parse_text(text, context={})] == [uri]


# EU legislation CELEX minting. The act-number's year/number order differs by
# act type and flipped for all types in the 2015 reform, so the only robust
# rule is the invariant that a CELEX year is in 1950-2050 (celex_year). The
# year/number swap must not regress the pre-2015 forms.
EULAGSTIFTNING_CASES = [
    # post-2015: "(EU) <year>/<number>", year-first for every act type
    ("Europaparlamentets och rådets direktiv (EU) 2016/1148",
     "https://lagen.nu/ext/celex/32016L1148"),
    ("Europaparlamentets och rådets förordning (EU) 2016/679",
     "https://lagen.nu/ext/celex/32016R0679"),
    # the sequence number can exceed the year range -- only the year is checked
    ("Europaparlamentets och rådets direktiv (EU) 2022/2555",
     "https://lagen.nu/ext/celex/32022L2555"),
    # pre-2015 directive: "<year>/<number>/<coop>" (2- and 4-digit years)
    ("rådets direktiv 85/337/EEG", "https://lagen.nu/ext/celex/31985L0337"),
    ("Europaparlamentets och rådets direktiv 95/46/EG",
     "https://lagen.nu/ext/celex/31995L0046"),
    # pre-2015 regulation: "(coop) nr <number>/<year>", number-first
    ("rådets förordning (EEG) nr 1234/85",
     "https://lagen.nu/ext/celex/31985R1234"),
    # a *bare* act-type word (no "rådets"/"kommissionens" institution) before a
    # parenthesised designation still sets the sector letter -- "direktiv" -> L,
    # "förordning" -> R. Regression for the CRA recital-125 bug, where a bare
    # "direktiv (EU) 2022/2555" minted a regulation (32022R2555) because only the
    # institution-prefixed alternative captured the act type.
    ("ändras genom direktiv (EU) 2022/2555 och",
     "https://lagen.nu/ext/celex/32022L2555"),
    ("som avses i direktiv (EU) 2018/1808",
     "https://lagen.nu/ext/celex/32018L1808"),
    ("enligt förordning (EU) 2022/2554 ska",
     "https://lagen.nu/ext/celex/32022R2554"),
    # absent any act-type word, a parenthesised "(EU) <year>/<number>" still
    # defaults to a regulation -- the correct pre-2015 behaviour (only regulations
    # used the parenthesised form), and the safe default post-2015
    ("i (EU) 2019/1020 anges", "https://lagen.nu/ext/celex/32019R1020"),
    # non-directive/-regulation act types carry their own CELEX sector letter: a
    # recommendation is H (not a directive's L), a decision D
    ("kommissionens rekommendation 2003/361/EG",
     "https://lagen.nu/ext/celex/32003H0361"),
    ("rådets beslut 2010/48/EG", "https://lagen.nu/ext/celex/32010D0048"),
]


@pytest.mark.parametrize("text,uri", EULAGSTIFTNING_CASES)
def test_eulagstiftning_celex(text, uri):
    parser = LagrumParser(NAMEDLAWS, basefile="x", parse_types=[EULAGSTIFTNING])
    assert [r.uri for r in parser.parse_text(text, context={})] == [uri]


# the EU treaties, the Charter and the ECHR cited by name -- linked onto the
# consolidated text (12016E/TXT for TFEU, 12012P/TXT for the Charter, coe/005 for
# the ECHR), the article/sub-article riding as a #-fragment. The "i" is optional.
TREATY_CASES = [
    ("artikel 16.2 i EUF-fördraget", "https://lagen.nu/ext/celex/12016E/TXT#16.2"),
    ("artikel 263 i EUF-fördraget", "https://lagen.nu/ext/celex/12016E/TXT#263"),
    ("artikel 267 FEUF", "https://lagen.nu/ext/celex/12016E/TXT#267"),
    ("artikel 47 i stadgan", "https://lagen.nu/ext/celex/12012P/TXT#47"),
    # a sentence-initial "Artikel" (capitalised) and the Charter's full name
    ("Artikel 8.1 i Europeiska unionens stadga om de grundläggande rättigheterna",
     "https://lagen.nu/ext/celex/12012P/TXT#8.1"),
    # the ECHR (a Council-of-Europe treaty) uses the CoE article grammar its own
    # artifact mints -- "A6", paragraph "A6P1" -- not the EU "#6.1" form
    ("artikel 6 i europakonventionen", "https://lagen.nu/ext/coe/005#A6"),
    ("artikel 6.1 i EKMR", "https://lagen.nu/ext/coe/005#A6P1"),
]


@pytest.mark.parametrize("text,uri", TREATY_CASES)
def test_treaty_and_charter_articles(text, uri):
    parser = LagrumParser(NAMEDLAWS, basefile="x", parse_types=[EULAGSTIFTNING])
    assert [r.uri for r in parser.parse_text(text, context={})] == [uri]


# a coordinated article list ("101 och 102") links every member; a range
# ("12–15") links its endpoints; each on its own number span
EU_LIST_RANGE_CASES = [
    ("artiklarna 101 och 102 i EUF-fördraget",
     ["https://lagen.nu/ext/celex/12016E/TXT#101",
      "https://lagen.nu/ext/celex/12016E/TXT#102"]),
    ("artiklarna 12, 13 och 14 i stadgan",
     ["https://lagen.nu/ext/celex/12012P/TXT#12",
      "https://lagen.nu/ext/celex/12012P/TXT#13",
      "https://lagen.nu/ext/celex/12012P/TXT#14"]),
    ("artiklarna 12–15 i EUF-fördraget",
     ["https://lagen.nu/ext/celex/12016E/TXT#12",
      "https://lagen.nu/ext/celex/12016E/TXT#15"]),
]


@pytest.mark.parametrize("text,uris", EU_LIST_RANGE_CASES)
def test_eu_article_lists_and_ranges(text, uris):
    parser = LagrumParser(NAMEDLAWS, basefile="x", parse_types=[EULAGSTIFTNING])
    assert [r.uri for r in parser.parse_text(text, context={})] == uris


def test_eu_sarskilt_names_instrument_first():
    # "<instrument>, särskilt artikel N" -- the instrument is named first, the
    # article pins onto it. The link covers just "artikel N", not the instrument.
    parser = LagrumParser(NAMEDLAWS, basefile="x", parse_types=[EULAGSTIFTNING])
    refs = parser.parse_text(
        "med beaktande av fördraget om Europeiska unionens funktionssätt, "
        "särskilt artikel 16,", context={})
    assert [(r.uri, r.text) for r in refs] == [
        ("https://lagen.nu/ext/celex/12016E/TXT#16", "artikel 16")]
    # also after an act cited by number, and with a coordinated list
    assert [r.uri for r in parser.parse_text(
        "direktiv 2000/31/EG, särskilt artikel 5", context={})] == [
        "https://lagen.nu/ext/celex/32000L0031#5"]


def test_gdpr_preamble_reference_patterns():
    # the reported GDPR-preamble gaps, threaded through one EU-document parser
    # (self_eu_act set), in document order -- each links its own instrument, never
    # the GDPR self
    p = LagrumParser({}, basefile="celex", parse_types=[EULAGSTIFTNING])
    p.reset()
    p.state.self_eu_act = "32016R0679"
    T = "https://lagen.nu/ext/celex/12016E/TXT"
    C = "https://lagen.nu/ext/celex/12012P/TXT"

    def uris(text):
        return [r.uri for r in p.parse_text(text, context={})]

    # visa: treaty named first, article after ("särskilt artikel 16") -> TFEU
    assert uris("med beaktande av fördraget om Europeiska unionens funktionssätt, "
                "särskilt artikel 16,") == ["%s#16" % T]
    # a bare self-reference still resolves to the GDPR
    assert uris("påverkar inte tillämpningen av artikel 98") \
        == ["https://lagen.nu/ext/celex/32016R0679#98"]
    # Charter (full name, capitalised) and TFEU sub-article
    assert uris("Artikel 8.1 i Europeiska unionens stadga om de grundläggande "
                "rättigheterna") == ["%s#8.1" % C]
    assert uris("I artikel 16.2 i EUF-fördraget bemyndigas") == ["%s#16.2" % T]
    # a directive named, then a range anaphora back to it
    p.parse_text("Europaparlamentets och rådets direktiv 2000/31/EG", context={})
    assert uris("ansvar i artiklarna 12–15 i det direktivet") == [
        "https://lagen.nu/ext/celex/32000L0031#12",
        "https://lagen.nu/ext/celex/32000L0031#15"]
    # a recommendation keeps its own CELEX sector letter (H, not a directive's L)
    assert uris("artikel 2 i bilagan till kommissionens rekommendation 2003/361/EG") \
        == ["https://lagen.nu/ext/celex/32003H0361"]


def test_eu_range_anaphora_to_named_directive():
    # "artiklarna 12–15 i det direktivet" pins onto the directive just named
    # (the definite generic noun now resolves in an EU document too)
    parser = LagrumParser({}, basefile="celex", parse_types=[EULAGSTIFTNING])
    parser.reset()
    parser.parse_text("Europaparlamentets och rådets direktiv 2000/31/EG",
                      context={})
    got = [r.uri for r in parser.parse_text(
        "ansvar i artiklarna 12–15 i det direktivet", context={})]
    assert got == ["https://lagen.nu/ext/celex/32000L0031#12",
                   "https://lagen.nu/ext/celex/32000L0031#15"]


# EU acts cited by Swedish short name (load_namedacts), with article anaphora.
# Each tuple is (text, [expected uris]); a parser threads one document so the
# anaphora cases see the act named by the line before them.
GDPR = "https://lagen.nu/ext/celex/32016R0679"
EU_NAMEDACT_SEQUENCE = [
    # explicit name -> article pinpoint, the determiner/adjective absorbed
    ("Enligt artikel 6 i dataskyddsförordningen ska", ["%s#6" % GDPR]),
    # a coordinated article list before the name links each member to the act
    ("artikel 6.3 och 6.4 i den allmänna dataskyddsförordningen är",
     ["%s#6.3" % GDPR, "%s#6.4" % GDPR]),
    ("artikel 23.1 i dataskyddsförordningen medger", ["%s#23.1" % GDPR]),
    # anaphora: a bare standalone article and the definite generic noun both
    # pinpoint the act just named
    ("behandlingen är nödvändig enligt artikel 6.1. e). Den", ["%s#6.1" % GDPR]),
    ("artikel 5.1 c i förordningen, som", ["%s#5.1" % GDPR]),
    # a treaty / the Charter / the ECHR links onto its OWN consolidated text --
    # never mis-pinned onto the act in focus (the "i" before the instrument is
    # optional). The ECHR is a Council-of-Europe treaty (coe/005), the others CELEX.
    ("artikel 6.1 europakonventionen och", ["https://lagen.nu/ext/coe/005#A6P1"]),
    ("artikel 267 EUF-fördraget för",
     ["https://lagen.nu/ext/celex/12016E/TXT#267"]),
    # a coordinated list before the Charter (indefinite/determiner-led name) links
    # each member onto the Charter's consolidated text
    ("rätten till privatliv enligt artikel 7 och 8.1 i EU:s rättighetsstadga",
     ["https://lagen.nu/ext/celex/12012P/TXT#7",
      "https://lagen.nu/ext/celex/12012P/TXT#8.1"]),
]


def test_eu_namedact_articles_and_anaphora():
    parser = LagrumParser(NAMEDLAWS, basefile="dom", parse_types=[EULAGSTIFTNING],
                          named_acts=NAMEDACTS_MAP)
    parser.state = type(parser.state)()       # one threaded document
    for text, want in EU_NAMEDACT_SEQUENCE:
        assert [r.uri for r in parser.parse_text(text, context={})] == want, text


def test_eu_self_act_bare_article():
    # inside an EU act's own body (self_eu_act set), a bare "artikel N" self-refers
    # to that act -- it must not anaphora-pin onto an external act a recital named
    # earlier (the GDPR art 2(3) "artikel 98" -> förordning (EG) nr 45/2001 bug)
    parser = LagrumParser({}, basefile="celex",
                          parse_types=[EULAGSTIFTNING, EURATTSFALL])
    parser.reset()
    parser.state.self_eu_act = "32016R0679"
    assert [r.uri for r in parser.parse_text(
        "i enlighet med artikel 28.2 i förordning (EG) nr 45/2001", context={})] \
        == ["https://lagen.nu/ext/celex/32001R0045#28.2"]  # explicit external ref
    assert [r.uri for r in parser.parse_text(
        "påverkar inte tillämpningen av artikel 98", context={})] \
        == ["%s#98" % GDPR]  # bare article self-refers, despite the recital above


def test_eu_namedact_off_without_acts():
    # the grammar extension is gated on supplied acts -- a parser with none
    # behaves exactly as before (a bare nickname does not link)
    parser = LagrumParser(NAMEDLAWS, basefile="dom", parse_types=[EULAGSTIFTNING])
    assert parser.parse_text("artikel 6 i dataskyddsförordningen",
                             context={}) == []


@pytest.mark.parametrize("path", make_params("Avg"))
def test_myndighetsbeslut(path):
    got, want = run_testfile(path, parse_types=[MYNDIGHETSBESLUT])
    assert got == want


@pytest.mark.parametrize("path", make_params("Simple"))
def test_enklalagrum(path):
    got, want = run_testfile(path, parse_types=[ENKLALAGRUM])
    assert got == want


def test_lagrum_trigger_bounded_on_pathological_enumeration():
    # A long flattened digit/comma enumeration with no closing " §" used to
    # make the LAGRUM trigger's unbounded list-continuation quantifier
    # backtrack quadratically (O(n^2)+): ~6s at 24KB of "12, " repeats.
    # The quantifier is now bounded ({0,50}), so this stays linear and fast
    # even though the input never matches.
    trigger = build_trigger([LAGRUM])
    pathological = "12, " * 6000  # 24 KB, previously ~6s
    start = time.time()
    trigger.search(pathological)
    assert time.time() - start < 1.0


def test_interleave_disjoint_refs():
    text = "se 3 § och 5 § nedan"
    refs = [Ref(3, 6, "3 §", "dcterms:references", "https://x/#P3"),
            Ref(11, 14, "5 §", "dcterms:references", "https://x/#P5")]
    assert interleave(text, refs) == [
        "se ",
        {"predicate": "dcterms:references", "uri": "https://x/#P3",
         "text": "3 §"},
        " och ",
        {"predicate": "dcterms:references", "uri": "https://x/#P5",
         "text": "5 §"},
        " nedan",
    ]


def test_interleave_rejects_overlapping_refs():
    # Every producer guarantees disjoint spans (parse_text consumes matched
    # spans; call sites merging two ref lists filter overlaps first), so an
    # overlap reaching interleave is an upstream bug. It used to be silently
    # dropped, losing a link; now it fails fast.
    text = "3 kap. 5 §"
    refs = [Ref(0, 10, "3 kap. 5 §", "dcterms:references", "https://x/#K3P5"),
            Ref(7, 10, "5 §", "dcterms:references", "https://x/#P5")]
    with pytest.raises(AssertionError, match="overlapping ref spans"):
        interleave(text, refs)


def test_parser_reset_clears_document_state():
    # reset() gives the per-document state a clean slate without paying for
    # parser reconstruction (grammar compilation is the expensive part).
    parser = LagrumParser(NAMEDLAWS, basefile="9999:999",
                          parse_types=ALL_PARSE_TYPES,
                          abbreviations=ABBREVIATIONS)
    # give the parser a "samma lag" focus and a learned in-document alias
    parser.parse_text("enligt 5 § lagen (1994:953) om åligganden",
                      context={})
    assert parser.state.lastlaw == "1994:953"
    parser.state.namedlaws["testlagen"] = "1999:175"
    parser.reset()
    assert not parser.state.namedlaws and parser.state.lastlaw is None
