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
from pathlib import Path

import pytest

from accommodanda.lib.datasets import NAMEDACTS
from accommodanda.lib.datasets import NAMEDLAWS as SFS_NAMEDLAWS
from accommodanda.lib.lagrum import (
    ENKLALAGRUM,
    EULAGSTIFTNING,
    EURATTSFALL,
    FORARBETEN,
    MYNDIGHETSBESLUT,
    RATTSFALL,
    LagrumParser,
    load_abbreviations,
    load_namedacts,
    load_namedlaws,
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
]


@pytest.mark.parametrize("text,uri", EULAGSTIFTNING_CASES)
def test_eulagstiftning_celex(text, uri):
    parser = LagrumParser(NAMEDLAWS, basefile="x", parse_types=[EULAGSTIFTNING])
    assert [r.uri for r in parser.parse_text(text, context={})] == [uri]


# EU acts cited by Swedish short name (load_namedacts), with article anaphora.
# Each tuple is (text, [expected uris]); a parser threads one document so the
# anaphora cases see the act named by the line before them.
GDPR = "https://lagen.nu/ext/celex/32016R0679"
EU_NAMEDACT_SEQUENCE = [
    # explicit name -> article pinpoint, the determiner/adjective absorbed
    ("Enligt artikel 6 i dataskyddsförordningen ska", ["%s#6" % GDPR]),
    ("artikel 6.3 och 6.4 i den allmänna dataskyddsförordningen är",
     []),  # a coordinated article list past the name is left alone (no false pin)
    ("artikel 23.1 i dataskyddsförordningen medger", ["%s#23.1" % GDPR]),
    # anaphora: a bare standalone article and the definite generic noun both
    # pinpoint the act just named
    ("behandlingen är nödvändig enligt artikel 6.1. e). Den", ["%s#6.1" % GDPR]),
    ("artikel 5.1 c i förordningen, som", ["%s#5.1" % GDPR]),
    # a different instrument is never mis-pinned onto the act in focus
    ("artikel 6.1 europakonventionen och", []),
    ("artikel 267 EUF-fördraget för", []),
    ("rätten till privatliv enligt artikel 7 och 8.1 i EU:s rättighetsstadga", []),
]


def test_eu_namedact_articles_and_anaphora():
    parser = LagrumParser(NAMEDLAWS, basefile="dom", parse_types=[EULAGSTIFTNING],
                          named_acts=NAMEDACTS_MAP)
    parser.state = type(parser.state)()       # one threaded document
    for text, want in EU_NAMEDACT_SEQUENCE:
        assert [r.uri for r in parser.parse_text(text, context={})] == want, text


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
