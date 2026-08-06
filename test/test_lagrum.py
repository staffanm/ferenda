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
import json
import re
import time
from pathlib import Path

import pytest

from accommodanda.lib import catalog
from accommodanda.lib.datasets import NAMEDACTS
from accommodanda.lib.datasets import NAMEDLAWS as SFS_NAMEDLAWS
from accommodanda.lib.lagrum import (
    ALL_PARSE_TYPES,
    ENKLALAGRUM,
    EULAGSTIFTNING,
    FORESKRIFT_TRIGGER_SRC,
    FS_DESIGNATIONS,
    EURATTSFALL,
    FORARBETEN,
    LAGRUM,
    MYNDIGHETSBESLUT,
    RATTSFALL,
    LagrumParser,
    Ref,
    build_trigger,
    interleave,
    is_placeholder_sfsid,
    load_abbreviations,
    load_namedacts,
    load_namedlaws,
    with_indefinite_aliases,
    yield_overlaps,
)
from accommodanda.lib.text import runs_text
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


def test_anonymous_law_ref_is_one_pinpointed_link():
    """S2: "1 kap. 18 § lagen (2016:1145) om offentlig upphandling" is one link.

    The old engine split it — a pinpoint link plus a bare link to the act as a
    whole — because it could not tell where the law's name ended and the
    sentence resumed. That is only an argument against extending the link past
    the SFS number, and this does not: the span ends at the closing paren and
    the trailing "om …" stays plain text. The second edge was pure noise, and it
    made every pinpointed citation also count as a whole-act citation.
    """
    parser = LagrumParser(NAMEDLAWS, basefile="9999:999", parse_types=[LAGRUM])

    def refs(text):
        parser.reset()
        return [(r.uri, r.text) for r in parser.parse_text(text, context={})]

    assert refs("organ som avses i 1 kap. 18 § lagen (2016:1145) om offentlig "
                "upphandling,") == [
        ("https://lagen.nu/2016:1145#K1P18", "1 kap. 18 § lagen (2016:1145)")]
    # a named law already combined, and still does
    assert refs("enligt 12 § delgivningslagen (1970:428) gäller") == [
        ("https://lagen.nu/1970:428#P12", "12 § delgivningslagen (1970:428)")]
    # several sections cannot fold into one link, so the law keeps its own --
    # which is why the whole-document panel still filters these (catalog.
    # _SUPERSEDED_BY_PINPOINT), source fix or not
    assert [uri for uri, _ in refs("17-29 och 32 §§ i lagen (2004:575) om x")] == [
        "https://lagen.nu/2004:575#P17", "https://lagen.nu/2004:575#P29",
        "https://lagen.nu/2004:575#P32", "https://lagen.nu/2004:575"]
    # nor can a chapter-only reference: there is no section to pin
    assert [uri for uri, _ in refs("1 kap. lagen (2016:1145) om x")] == [
        "https://lagen.nu/2016:1145#K1", "https://lagen.nu/2016:1145"]


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


# pre-1989 case numbering ("Case 31/87", "mål 45/87"): no court letter existed
# before the Court of First Instance, so the marker word is required (a bare
# "31/87" must not link) and the court defaults to the ECJ
EURATTSFALL_OLD_CASES = [
    ("in Case 31/87, REFERENCE to the Court",
     ["https://lagen.nu/ext/celex/61987CJ0031"]),
    ("se mål 45/87, Dundalk", ["https://lagen.nu/ext/celex/61987CJ0045"]),
    ("delivered on 31/87 items", []),
]


@pytest.mark.parametrize("text,uris", EURATTSFALL_OLD_CASES)
def test_eurattsfall_old_numbering(text, uris):
    parser = LagrumParser(NAMEDLAWS, basefile="x", parse_types=[EURATTSFALL])
    assert [r.uri for r in parser.parse_text(text, context={})] == uris


# The English citation surface (lang="eng"): CELLAR holds no Swedish text for
# pre-accession case law, so those documents are scanned with the English EU
# terminal block -- same rules and formatters, English words, and the
# parenthesised sub-article convention the old judgments use ("Article 29 (5)").
EULAGSTIFTNING_ENG_CASES = [
    ("Council Directive 71/305/EEC of 26 July 1971 is intended to secure",
     ["https://lagen.nu/ext/celex/31971L0305"]),
    ("Article 29 (5) of Directive 71/305/EEC provides",
     ["https://lagen.nu/ext/celex/31971L0305#29.5"]),
    ("As stated in Article 1(2) of Directive 92/50/EEC.",
     ["https://lagen.nu/ext/celex/31992L0050#1.2"]),
    ("Regulation (EEC) No 2092/91 applies.",
     ["https://lagen.nu/ext/celex/31991R2092"]),
    ("Commission Recommendation 2003/361/EC",
     ["https://lagen.nu/ext/celex/32003H0361"]),
    # a Treaty article refuses to link (no English treaty grammar) -- correct-
    # but-unlinked, never anaphora-pinned onto the last named act
    ("Article 177 of the EEC Treaty by the Raad van State", []),
    # recitals on the English surface, same three shapes as the Swedish one:
    # the act named by number, the recital coordinated with an article that
    # names it, and the definite generic noun referring back
    ("Recital 19 of Directive 71/305/EEC states",
     ["https://lagen.nu/ext/celex/31971L0305#recital-19",
      "https://lagen.nu/ext/celex/31971L0305"]),
    ("Recital 19 and Article 29 (5) of Directive 71/305/EEC provide",
     ["https://lagen.nu/ext/celex/31971L0305#recital-19",
      "https://lagen.nu/ext/celex/31971L0305#29.5"]),
    ("Regulation (EEC) No 2092/91 applies. See recital 4 of the regulation.",
     ["https://lagen.nu/ext/celex/31991R2092",
      "https://lagen.nu/ext/celex/31991R2092#recital-4",
      "https://lagen.nu/ext/celex/31991R2092"]),
    ("The court gave recital 5 no weight.", []),
]


@pytest.mark.parametrize("text,uris", EULAGSTIFTNING_ENG_CASES)
def test_eulagstiftning_english_surface(text, uris):
    parser = LagrumParser({}, basefile="celex",
                          parse_types=[EULAGSTIFTNING], lang="eng")
    assert [r.uri for r in parser.parse_text(text, context={})] == uris


def test_english_anaphora_links_the_directive_and_bare_articles():
    parser = LagrumParser({}, basefile="celex",
                          parse_types=[EULAGSTIFTNING], lang="eng")
    parser.parse_text("Council Directive 71/305/EEC of 26 July 1971 concerns "
                      "public works contracts.", context={})
    got = parser.parse_text("Under Articles 20 and 26 of the directive, "
                            "criteria are laid down.", context={})
    assert [r.uri for r in got] == [
        "https://lagen.nu/ext/celex/31971L0305#20",
        "https://lagen.nu/ext/celex/31971L0305#26"]
    bare = parser.parse_text("Article 29 provides for that examination.",
                             context={})
    assert [r.uri for r in bare] == ["https://lagen.nu/ext/celex/31971L0305#29"]


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
    # the lettered point pins the anaphoric reference too. this expectation used
    # to be "#5.1": the grammar had no letter level, so "c" was left in the text
    # and the citation landed a level short of what it said. deliberately
    # retightened when the letter level was added, not loosened to pass.
    ("artikel 5.1 c i förordningen, som", ["%s#5.1.c" % GDPR]),
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


def _eu_parser():
    parser = LagrumParser(NAMEDLAWS, basefile="dom", parse_types=[EULAGSTIFTNING],
                          named_acts=NAMEDACTS_MAP)
    parser.reset()
    return parser


# dataskyddslagen (2018:218) cites the GDPR as "artikel 6.1 c och e i EU:s
# dataskyddsförordning" -- the two forms that used to defeat the engine at once:
# a genitive determiner with the noun in its indefinite form, and a lettered
# point. The renderer already mints the matching #6.1.c anchor.
EU_LETTERED_POINTS = [
    ("artikel 6.1 c i dataskyddsförordningen", ["%s#6.1.c" % GDPR]),
    # a letter coordination is one link per letter, not one link losing the rest
    ("artikel 6.1 c och e i dataskyddsförordningen",
     ["%s#6.1.c" % GDPR, "%s#6.1.e" % GDPR]),
    ("artikel 5.1 a, b och c i dataskyddsförordningen",
     ["%s#5.1.a" % GDPR, "%s#5.1.b" % GDPR, "%s#5.1.c" % GDPR]),
    ("artikel 9.2 b i dataskyddsförordningen", ["%s#9.2.b" % GDPR]),
]


def test_named_law_with_pinpoint_is_one_link_over_the_whole_phrase():
    # "35 § i förordningen (2014:1101)" is ONE pinpointed link spanning the whole
    # phrase -- not a #P35 link on the section plus a second, unpinpointed link on
    # the SFS number, which is what the engine used to emit. Locked in here
    # because no test covered it: the behaviour was corrected without one.
    parser = LagrumParser(NAMEDLAWS, basefile="x", named_acts=NAMEDACTS_MAP)
    parser.reset()
    text = ("Av 35 § i förordningen (2014:1101) om EU:s direktstöd för "
            "jordbrukare framgår att")
    refs = parser.parse_text(text, context={})
    assert [r.uri for r in refs] == ["https://lagen.nu/2014:1101#P35"]
    assert text[refs[0].start:refs[0].end] == "35 § i förordningen (2014:1101)"


@pytest.mark.parametrize("text,want", EU_LETTERED_POINTS)
def test_eu_lettered_point_pinpoints(text, want):
    assert [r.uri for r in _eu_parser().parse_text(text, context={})] == want


# A stycke (sub-paragraph) pinpoint: "artikel 9.2 andra stycket" anchors #9.2.S2,
# the id the eurlex parser mints for that stycke. Before the production existed
# the whole reference failed to match, so the *article* was lost too and the
# citation degraded to an act-level link -- adding the word made the link worse.
EU_STYCKE_PINPOINTS = [
    ("artikel 9.2 andra stycket i direktiv 2005/85/EG",
     ["https://lagen.nu/ext/celex/32005L0085#9.2.S2"]),
    ("artikel 93.1 andra stycket i direktiv 2014/65/EU",
     ["https://lagen.nu/ext/celex/32014L0065#93.1.S2"]),
    # an article's own stycken, with no numbered paragraph between
    ("artikel 8 andra stycket i direktiv 98/34/EG",
     ["https://lagen.nu/ext/celex/31998L0034#8.S2"]),
    ("artikel 12 första stycket i direktiv 98/34/EG",
     ["https://lagen.nu/ext/celex/31998L0034#12.S1"]),
    # a lettered point is anchored by its paragraph whichever stycke holds it --
    # which is also the only form the acts write ("artikel 11 a") -- so the point
    # takes the fragment and the stycke drops out
    ("artikel 3.1 tredje stycket a i förordning (EG) nr 1272/2008",
     ["https://lagen.nu/ext/celex/32008R1272#3.1.a"]),
    ("artikel 9.2 b i direktiv 2005/85/EG",
     ["https://lagen.nu/ext/celex/32005L0085#9.2.b"]),
    # ... and an article cited with no stycke is untouched
    ("artikel 2.1 i direktiv (EU) 2015/1535",
     ["https://lagen.nu/ext/celex/32015L1535#2.1"]),
]


@pytest.mark.parametrize("text,want", EU_STYCKE_PINPOINTS)
def test_eu_stycke_pinpoints(text, want):
    assert [r.uri for r in _eu_parser().parse_text(text, context={})] == want


def test_eu_stycke_link_spans_the_whole_phrase():
    # the ordinal and "stycket" belong to the link, not to the text around it
    parser = _eu_parser()
    text = "Den ska tillämpas från och med artikel 93.1 andra stycket i direktiv 2014/65/EU."
    refs = parser.parse_text(text, context={})
    assert text[refs[0].start:refs[0].end] == \
        "artikel 93.1 andra stycket i direktiv 2014/65/EU"


@pytest.mark.parametrize("text,want", [
    # a Council-of-Europe treaty fragments its own way (A6P3Lc, not 6.3.c), and
    # the ECHR artifact really does mint A6P3La..A6P3Le -- the letter must reach
    # coe_ids.article_fragment, which has always taken one. Without this the
    # grammar swallows "c" into the link span and still lands on A6P3, so the
    # citation reads as pinpointed while pointing a level short.
    ("artikel 6.3 c i Europakonventionen", ["https://lagen.nu/ext/coe/005#A6P3Lc"]),
    ("artikel 5.1 d i Europakonventionen", ["https://lagen.nu/ext/coe/005#A5P1Ld"]),
    ("artikel 6.1 i Europakonventionen", ["https://lagen.nu/ext/coe/005#A6P1"]),
])
def test_coe_lettered_point_uses_the_treaty_fragment_grammar(text, want):
    assert [r.uri for r in _eu_parser().parse_text(text, context={})] == want


@pytest.mark.parametrize("text,want", [
    # a letter pinpoints with no sub-article in front of it: article 143 of the
    # VAT directive really is a list of points a-l, and articles built that way
    # (Reg. 469/2009 art. 3, the skyddsgrund directive's art. 2) are what the
    # corpus mostly cites this way
    ("artikel 143 b i direktiv 2006/112/EG",
     ["https://lagen.nu/ext/celex/32006L0112#143.b"]),
    ("artikel 143 b och c i direktiv 2006/112/EG",
     ["https://lagen.nu/ext/celex/32006L0112#143.b",
      "https://lagen.nu/ext/celex/32006L0112#143.c"]),
    # the same on the named-act and treaty paths, which is where gating the
    # letter on a sub-article cost the entire citation rather than the pinpoint
    ("artikel 6 c i Europakonventionen", ["https://lagen.nu/ext/coe/005#A6Lc"]),
    ("artikel 3 a i dataskyddsförordningen", ["%s#3.a" % GDPR]),
])
def test_letter_without_a_subarticle_is_read_as_a_point(text, want):
    assert [r.uri for r in _eu_parser().parse_text(text, context={})] == want


def test_eu_letter_coordination_links_each_letter_on_its_own_span():
    # a coordinated letter list follows the same idiom as a coordinated article
    # list ("artiklarna 101 och 102"): one link per member, each on its own
    # tokens -- a shared phrase span would make every link overlap the others
    text = "artikel 6.1 c och e i dataskyddsförordningen"
    refs = _eu_parser().parse_text(text, context={})
    assert [text[r.start:r.end] for r in refs] == ["c", "e"]
    assert [r.uri for r in refs] == ["%s#6.1.c" % GDPR, "%s#6.1.e" % GDPR]


# A recital is where an act states the reasoning its articles enact, and the
# guidance corpus cites it for exactly that -- "i skäl 108 och artikel 46.1 i
# allmänna dataskyddsförordningen föreskrivs att ...". The eurlex renderer
# already mints the `#recital-N` anchor these land on.
EU_RECITALS = [
    ("skäl 108 i allmänna dataskyddsförordningen",
     ["%s#recital-108" % GDPR, GDPR]),
    # the act named by number links its own phrase too, as it always did
    ("skäl 6 i förordning (EU) 2016/679",
     ["%s#recital-6" % GDPR, GDPR]),
    # a coordination is one link per recital
    ("skälen 108 och 109 i dataskyddsförordningen",
     ["%s#recital-108" % GDPR, "%s#recital-109" % GDPR, GDPR]),
    # Swedish hangs one "i <akt>" off both halves; the recital must take the act
    # the article names rather than go unlinked -- the reported case
    ("skäl 108 och artikel 46.1 i allmänna dataskyddsförordningen",
     ["%s#recital-108" % GDPR, "%s#46.1" % GDPR]),
    ("skäl 108 och artiklarna 46.1 och 46.2 i dataskyddsförordningen",
     ["%s#recital-108" % GDPR, "%s#46.1" % GDPR, "%s#46.2" % GDPR]),
    # a recital that names no act does *not* anaphora-link: these documents
    # number their own paragraphs the same way, and a bare number is likelier
    # to be one of those than a recital of whatever act was last mentioned
    ("Domstolen anförde skäl 5 utan att ange någon rättsakt.", []),
    # ... but the definite generic noun refers back explicitly, and is the
    # commonest recital form in the corpus. Regression: this used to link the
    # whole phrase to the act with no fragment at all, so text reading "skäl
    # 108" landed the reader on the act's front page
    ("Se artikel 5 i dataskyddsförordningen. Se vidare skäl 108 i förordningen.",
     ["%s#5" % GDPR, "%s#recital-108" % GDPR, GDPR]),
    # and "skäl" as the everyday noun is not a citation at all
    ("Det saknades skäl att ändra beslutet.", []),
    ("Av dessa skäl 12 personer överklagade.", []),
]


@pytest.mark.parametrize("text,want", EU_RECITALS)
def test_eu_recital_links(text, want):
    assert [r.uri for r in _eu_parser().parse_text(text, context={})] == want


def test_a_recital_link_spans_its_own_words_only():
    """The recital owns "skäl 108" and no more: the trailing act phrase may be
    shared with a coordinated article, and two links cannot both own it."""
    text = "skäl 108 och artikel 46.1 i allmänna dataskyddsförordningen"
    recital, artikel = _eu_parser().parse_text(text, context={})
    assert text[recital.start:recital.end] == "skäl 108"
    assert text[artikel.start:artikel.end] == \
        "artikel 46.1 i allmänna dataskyddsförordningen"


def test_a_recital_still_leaves_the_act_linked_and_in_focus():
    """Regression: making "skäl N i <akt>" one reference swallowed the act's own
    link and, with it, the anaphora memory every later "artikel N i direktivet"
    depends on -- 69 links across the edpb corpus went with it."""
    parser = _eu_parser()
    text = "Formuleringen är identisk med den i skäl 19 i direktiv 95/46/EG."
    refs = parser.parse_text(text, context={})
    assert [text[r.start:r.end] for r in refs] == ["skäl 19", "direktiv 95/46/EG"]
    # ... and the act is still what a later bare article binds to
    assert [r.uri for r in parser.parse_text("artikel 3.1", context={})] == \
        ["https://lagen.nu/ext/celex/31995L0046#3.1"]


def test_eu_single_lettered_point_spans_the_whole_phrase():
    # a lone point keeps the eu_ref span, exactly as a lone article does
    text = "artikel 6.1 c i dataskyddsförordningen"
    (ref,) = _eu_parser().parse_text(text, context={})
    assert text[ref.start:ref.end] == text


@pytest.mark.parametrize("text,want", [
    # the genitive form, which drops the noun's definite suffix
    ("artikel 6.1 i EU:s dataskyddsförordning", ["%s#6.1" % GDPR]),
    ("artikel 6.1 c i EU:s dataskyddsförordning", ["%s#6.1.c" % GDPR]),
    # the registered definite alias keeps working unchanged
    ("artikel 6.1 i dataskyddsförordningen", ["%s#6.1" % GDPR]),
])
def test_eu_genitive_indefinite_act_name(text, want):
    assert [r.uri for r in _eu_parser().parse_text(text, context={})] == want


def test_eu_letter_terminal_never_eats_the_preposition():
    # "i" introduces the act ("artikel 6.1 i dataskyddsförordningen") AND is a
    # possible point letter, and the named-act rule admits the instrument with no
    # preposition at all -- so a letter terminal accepting "i" would read this as
    # point (i) of 6.1 and pin a level too deep. It must stay the preposition.
    assert [r.uri for r in _eu_parser().parse_text(
        "artikel 6.1 i dataskyddsförordningen", context={})] == ["%s#6.1" % GDPR]


def test_with_indefinite_aliases_never_overwrites_hand_edited_data():
    # a derived indefinite form must not displace an alias someone registered by
    # hand for a different act (namedacts.json is curated data)
    got = with_indefinite_aliases({"xdirektivet": "31111L1111",
                                   "xdirektiv": "32222L2222"})
    assert got["xdirektiv"] == "32222L2222"       # explicit entry wins
    assert got["xdirektivet"] == "31111L1111"


def test_with_indefinite_aliases_leaves_acronyms_alone():
    # only the listed noun heads are stripped, so an acronym keeps its final
    # syllable ("gdpr" must not become "gdp")
    got = with_indefinite_aliases({"gdpr": "32016R0679", "nis2": "32022L2555"})
    assert sorted(got) == ["gdpr", "nis2"]


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


def test_interleave_carries_emphasis_into_the_runs():
    """The emphasis a document set survives into the artifact as a third run
    shape: a ``{"text", "style"}`` dict beside the bare strings and the link
    dicts. Unstyled text stays a bare string -- that is the overwhelming
    majority of the corpus, and the artifact's shape for it is unchanged."""
    text = "se bilaga 1 och 3 § nedan"
    refs = [Ref(16, 19, "3 §", "dcterms:references", "https://x/#P3")]
    # "bilaga 1" italic, the rest plain
    assert interleave(text, refs, styles=[(3, 11, "i")]) == [
        "se ",
        {"text": "bilaga 1", "style": "i"},
        " och ",
        {"predicate": "dcterms:references", "uri": "https://x/#P3",
         "text": "3 §"},
        " nedan",
    ]


def test_a_link_takes_emphasis_only_when_it_is_wholly_covered():
    """Half an italic citation cannot become two links without splitting the
    reference, so a partial cover drops the emphasis on the link rather than the
    link itself."""
    text = "enligt 3 § nedan"
    refs = [Ref(7, 10, "3 §", "dcterms:references", "https://x/#P3")]
    assert interleave(text, refs, styles=[(0, 16, "i")]) == [
        {"text": "enligt ", "style": "i"},
        {"predicate": "dcterms:references", "uri": "https://x/#P3",
         "text": "3 §", "style": "i"},
        {"text": " nedan", "style": "i"},
    ]
    # the emphasis stops mid-citation: the link is whole, its style dropped
    assert interleave(text, refs, styles=[(0, 9, "i")]) == [
        {"text": "enligt ", "style": "i"},
        {"predicate": "dcterms:references", "uri": "https://x/#P3",
         "text": "3 §"},
        " nedan",
    ]


def test_styled_runs_are_not_links():
    """The artifact's link graph reads runs by `"uri" in run`, so a styled text
    run -- which has none -- must not reach it. A styled run that counted as a
    link would put an entry with no target in `links`."""
    text = "se bilaga 1"
    runs = interleave(text, [], styles=[(3, 11, "i")])
    out = []
    catalog.collect_links({"id": "P1", "text": runs}, None, None, out)
    assert out == []
    assert runs_text(runs) == text          # the text itself is still readable


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


def test_a_draft_statutes_placeholder_number_mints_no_link():
    """L1: a not-yet-numbered statute is written with a zero löpnummer
    ("järnvägstrafiklagen (2018:000)" in the betänkande proposing it, and
    "0000:000" where the year is open too). No real SFS has one, so these are
    never citations to a document -- but each minted a uri, and ~100 000 of
    them piled onto a handful of targets, topping the dangling-target
    want-list the backfill downloads against."""
    parser = LagrumParser(NAMEDLAWS, basefile="9999:999", parse_types=[LAGRUM])

    def uris(text):
        parser.reset()
        return [r.uri for r in parser.parse_text(text, context={})]

    assert uris("den nya järnvägstrafiklagen (2018:000)") == []
    assert uris("lagen (0000:000) om något") == []
    assert uris("enligt 1 § lagen (2022:000) om test") == []
    # a real number is untouched, including one whose löpnummer merely opens
    # with a zero (the 1734 års lag balkar: "1736:0123")
    assert uris("enligt 3 kap. 5 § brottsbalken (1962:700)") == \
        ["https://lagen.nu/1962:700#K3P5"]


def test_placeholder_predicate():
    assert is_placeholder_sfsid("2018:000")
    assert is_placeholder_sfsid("0000:00")
    assert not is_placeholder_sfsid("1962:700")
    assert not is_placeholder_sfsid("1736:0123")


def test_a_named_chapter_stays_sticky_across_a_continued_enumeration():
    """A generic_ref that names a chapter keeps it for the bare sections that
    follow, so a continued enumeration lands in the chapter it continues.

    Säkerhetsskyddsförordningen 6 kap. 2 § cites "4 kap. 7 § första stycket samt
    8 och 9 §§ säkerhetsskyddslagen"; the trailing pair used to mint the
    chapterless `#P8`/`#P9`, which säkerhetsskyddslagen -- chaptered throughout
    -- has no such provisions for, so the citation landed nowhere and 4 kap.
    8-9 §§ never saw the citer in its rail. The joiner used to decide the
    reading: "3 kap. 2 § och 13 §" already resolved 13 § inside chapter 3 while
    ", 13 §" did not."""
    parser = LagrumParser(NAMEDLAWS, basefile="2021:955", parse_types=[LAGRUM])

    def uris(text):
        parser.reset()
        return [r.uri for r in parser.parse_text(text, context={})]

    assert uris("enligt 4 kap. 7 § första stycket samt 8 och 9 §§ "
                "säkerhetsskyddslagen (2018:585)") == [
        "https://lagen.nu/2018:585#K4P7S1",
        "https://lagen.nu/2018:585#K4P8",
        "https://lagen.nu/2018:585#K4P9",
        "https://lagen.nu/2018:585"]
    # comma and "och" now agree, on the reading "och" already had
    assert uris("i 3 kap. 2 §, 13 § brottsbalken") == \
        uris("i 3 kap. 2 § och 13 § brottsbalken") == [
        "https://lagen.nu/1962:700#K3P2",
        "https://lagen.nu/1962:700#K3P13",
        "https://lagen.nu/1962:700"]
    # a later ref naming its own act is not the earlier chapter's continuation
    assert uris("enligt 4 kap. 7 § säkerhetsskyddslagen (2018:585) samt "
                "5 § lagen (1990:52) om vård") == [
        "https://lagen.nu/2018:585#K4P7", "https://lagen.nu/1990:52#P5"]


def test_foreskrift_references_mint_their_series_uri():
    """A myndighetsföreskrift cited by designation and number. The engine had no
    production for these at all, so a förarbete, a dom or a sibling föreskrift
    naming one produced nothing -- the corpus held exactly zero inbound
    references to any of its 12,936 föreskrifter, which read as a fact about
    Swedish legal writing and was a missing rule."""
    parser = LagrumParser(NAMEDLAWS, basefile="prop/2025/26:28",
                          parse_types=ALL_PARSE_TYPES,
                          abbreviations=ABBREVIATIONS)

    def uris(text):
        parser.reset()
        return [r.uri for r in parser.parse_text(text, context={})]

    # the parenthesised form a föreskrift's full name uses, and running text
    assert uris("Säkerhetspolisens föreskrifter (PMFS 2022:1) om säkerhetsskydd") \
        == ["https://lagen.nu/pmfs/2022:1"]
    assert uris("Se vidare MSBFS 2020:7 och FFFS 2013:10.") == [
        "https://lagen.nu/msbfs/2020:7", "https://lagen.nu/fffs/2013:10"]
    # the designation maps through the författningssamling registry, so a series
    # whose slug is not its naive transliteration lands right: ÅFS is
    # Åklagarmyndighetens, not Arbetsmiljöverkets afs
    assert uris("Åklagarmyndighetens föreskrifter (ÅFS 2005:9)") == \
        ["https://lagen.nu/aafs/2005:9"]
    assert uris("Elsäkerhetsverkets föreskrifter (ELSÄK-FS 2008:1)") == \
        ["https://lagen.nu/elsakfs/2008:1"]
    assert uris("Bokföringsnämndens allmänna råd (BFNAR 2012:1)") == \
        ["https://lagen.nu/bfnar/2012:1"]


def test_every_registered_series_reaches_the_grammar():
    """The trigger regex decides which text is handed to the parser at all, so a
    designation the grammar terminal accepts but the trigger never fires at is a
    production that exists and silently never runs. Both are built from the
    författningssamling registry; this pins that they stay in step -- spelling
    the trigger by hand ("[A-ZÅÄÖ]…FS") missed LBS and RA-MS, which are not
    FS-shaped."""
    trigger = re.compile(FORESKRIFT_TRIGGER_SRC, re.X)
    missed = [d for d in FS_DESIGNATIONS if not trigger.search("%s 2020:1" % d)]
    assert missed == [], "registered series the trigger never fires at: %s" % missed


def test_an_unregistered_series_mints_nothing():
    """Only a registered författningssamling matches, so an invented or
    mis-transcribed designation stays text rather than minting a dangling uri."""
    parser = LagrumParser(NAMEDLAWS, basefile="prop/2025/26:28",
                          parse_types=ALL_PARSE_TYPES,
                          abbreviations=ABBREVIATIONS)
    assert [r.uri for r in parser.parse_text("en hittepåserie XYZFS 2020:1",
                                             context={})] == []


def test_foreskrift_refs_do_not_disturb_sfs_refs():
    parser = LagrumParser(NAMEDLAWS, basefile="prop/2025/26:28",
                          parse_types=ALL_PARSE_TYPES,
                          abbreviations=ABBREVIATIONS)
    assert [r.uri for r in parser.parse_text(
        "Enligt 3 kap. 5 § brottsbalken (1962:700) gäller annat.",
        context={})] == ["https://lagen.nu/1962:700#K3P5"]


# ---- a law name outlives the act that holds it ------------------------------

_SOL_DATASET = {
    "1980:620": {"label": "socialtjänstlagen", "abbr": "SoL",
                 "until": "2002-01-01"},
    "2001:453": {"label": "socialtjänstlagen", "abbr": "SoL",
                 "from": "2002-01-01", "until": "2025-07-01"},
    "2025:400": {"label": "socialtjänstlagen", "abbr": "SoL",
                 "from": "2025-07-01"},
    "1962:700": {"label": "brottsbalken", "abbr": "BrB"},
}


def _dataset(tmp_path, data=None):
    p = tmp_path / "namedlaws.json"
    p.write_text(json.dumps(data or _SOL_DATASET, ensure_ascii=False),
                 encoding="utf-8")
    return p


def test_a_named_law_resolves_to_the_act_that_bore_the_name_then(tmp_path):
    """Three acts have been socialtjänstlagen. A 2010 decision citing "11 kap.
    1 § socialtjänstlagen" means the 2001 act; resolved against today's, every
    such citation lands on a statute that did not exist when it was written --
    which is how 5 rättsfall and 100+ myndighetsbeslut came to sit in the context
    rail of a law younger than all of them."""
    laws = load_namedlaws(_dataset(tmp_path))
    assert laws.at("socialtjänstlagen", "1995-06-01") == "1980:620"
    assert laws.at("socialtjänstlagen", "2010-05-04") == "2001:453"
    assert laws.at("socialtjänstlagen", "2026-01-01") == "2025:400"


def test_the_boundary_day_belongs_to_the_new_act(tmp_path):
    """`until` is exclusive: an act applies up to but not including the day its
    successor takes over, which is the day the repeal takes effect."""
    laws = load_namedlaws(_dataset(tmp_path))
    assert laws.at("socialtjänstlagen", "2025-06-30") == "2001:453"
    assert laws.at("socialtjänstlagen", "2025-07-01") == "2025:400"


def test_an_undated_lookup_still_answers_for_today(tmp_path):
    """The dating is an enrichment, not a new requirement: a caller with no date
    -- and every name that has only ever meant one act -- behaves as before."""
    laws = load_namedlaws(_dataset(tmp_path))
    assert laws.at("socialtjänstlagen") == "2025:400"
    assert laws["socialtjänstlagen"] == "2025:400"          # plain dict access
    assert laws.at("brottsbalken", "1970-01-01") == "1962:700"
    assert dict(laws) == {"socialtjänstlagen": "2025:400", "brottsbalken": "1962:700"}


def test_an_abbreviation_travels_with_the_name_it_abbreviates(tmp_path):
    """"SoL" meant the 2001 act in 2010 for the same reason the spelled name
    did."""
    abbrevs = load_abbreviations(_dataset(tmp_path))
    assert abbrevs.at("SoL", "2010-05-04") == "2001:453"
    assert abbrevs.at("SoL") == "2025:400"


def test_the_parser_dates_a_bare_law_name_by_the_documents_own_date(tmp_path):
    laws = load_namedlaws(_dataset(tmp_path))
    text = "Enligt 11 kap. 1 § socialtjänstlagen ska nämnden inleda utredning."

    def cited(written):
        p = LagrumParser(laws, "dom/test", written=written,
                                parse_types=[LAGRUM])
        return [n.uri for n in p.parse_text(text, context={})
                if not isinstance(n, str)]

    assert cited("2010-05-04") == ["https://lagen.nu/2001:453#K11P1"]
    assert cited(None) == ["https://lagen.nu/2025:400#K11P1"]


def test_a_law_the_document_names_itself_outranks_the_dated_table(tmp_path):
    """"lagen (2001:453) om ..." is this document saying which act it means --
    better evidence than any table, whatever date the document carries."""
    laws = load_namedlaws(_dataset(tmp_path))
    p = LagrumParser(laws, "dom/test", written="2026-01-01",
                            parse_types=[LAGRUM])
    p.parse_text("Enligt socialtjänstlagen (2001:453) gäller följande.",
                 context={})
    refs = [n.uri for n in p.parse_text("Se 11 kap. 1 § socialtjänstlagen.",
                                        context={}) if not isinstance(n, str)]
    assert refs == ["https://lagen.nu/2001:453#K11P1"]


def test_a_date_before_every_recorded_window_mints_no_link(tmp_path):
    """The name meant some earlier act then and the table does not know which.
    Answering with today's act is the very thing this exists to stop; answering
    with the oldest on record asserts a succession nobody established."""
    data = {"1990:100": {"label": "tullagen", "from": "1988-01-01",
                         "until": "2000-01-01"},
            "2000:1": {"label": "tullagen", "from": "2000-01-01"}}
    laws = load_namedlaws(_dataset(tmp_path, data))
    assert laws.at("tullagen", "1980-01-01") is None       # before the chain
    assert laws.at("tullagen", "1995-01-01") == "1990:100"


def test_the_oldest_act_on_record_is_open_at_the_start(tmp_path):
    """A chain whose oldest row has no `from` is open-ended backwards -- that is
    what "as far as we know it was always this act" looks like, and it is the
    normal shape, since a repealed act's own start is often unrecorded."""
    laws = load_namedlaws(_dataset(tmp_path))              # 1980:620 has no from
    assert laws.at("socialtjänstlagen", "1975-01-01") == "1980:620"


def test_two_acts_cannot_both_still_carry_a_name(tmp_path):
    """Both open-ended means the dataset says two acts hold the name today; the
    flat mapping would silently take the earlier of them."""
    data = {"1990:100": {"label": "tullagen"}, "2000:1": {"label": "tullagen"}}
    with pytest.raises(AssertionError, match="only one act can hold a name"):
        load_namedlaws(_dataset(tmp_path, data))


def test_at_requires_a_real_day(tmp_path):
    """`lib.util.approximate_date` is what turns a partial date into one; a bare
    year truncated and compared lexicographically would land in whichever span
    happened to sort right."""
    laws = load_namedlaws(_dataset(tmp_path))
    with pytest.raises(AssertionError, match="must be an ISO day"):
        laws.at("socialtjänstlagen", "2010")


def test_a_renamed_successor_leaves_the_old_name_on_the_repealed_act(tmp_path):
    """Where the replacement renamed the concept, no act carries the old name
    today, so a citation to it can only mean the repealed one -- there is nothing
    ambiguous to resolve and nothing to move. firmalagen (1974:156) was replaced
    by "Lag (2018:1653) om företagsnamn"; "1 § firmalagen" written in 2026 still
    means 1974:156."""
    data = {"1974:156": {"label": "firmalagen"}}
    laws = load_namedlaws(_dataset(tmp_path, data))
    assert laws.at("firmalagen", "2026-01-01") == "1974:156"
    assert laws.at("firmalagen") == "1974:156"


def test_a_same_name_successor_takes_the_name_over(tmp_path):
    """The ambiguous case, and the only one worth moving: two acts have been
    polisdatalagen, so the date decides."""
    data = {"1998:622": {"label": "polisdatalagen", "until": "2012-03-01"},
            "2010:361": {"label": "polisdatalagen", "from": "2012-03-01"}}
    laws = load_namedlaws(_dataset(tmp_path, data))
    assert laws.at("polisdatalagen", "2008-01-01") == "1998:622"
    assert laws.at("polisdatalagen", "2026-01-01") == "2010:361"
