"""Concept normalization -- the corpus-aware Swedish noun de-inflector that
collapses inflected surface forms onto one canonical begrepp (lib/concepts.py)."""

import pytest

from accommodanda.lib import concepts


@pytest.fixture(autouse=True)
def _fresh():
    # isolate each test from the on-disk override file + wiki registry
    concepts._OVERRIDES = {"alias": {}, "distinct": []}
    concepts._WIKI = set()
    yield
    concepts._OVERRIDES = None
    concepts._WIKI = None


def canon(forms):
    return {c: v for c, v in concepts.cluster(forms).items()}


def test_generic_inflection_collapses_to_base():
    assert canon({"Borgenär", "Borgenären", "Borgenärerna"}) == {
        "Borgenär": ["Borgenär", "Borgenären", "Borgenärerna"]}
    assert canon({"Andel", "Andelar"}) == {"Andel": ["Andel", "Andelar"]}
    assert canon({"Merkostnad", "Merkostnaden", "Merkostnader"}) == {
        "Merkostnad": ["Merkostnad", "Merkostnaden", "Merkostnader"]}


def test_are_definite_plural_collapses_to_the_are_base():
    # the -are agent noun's definite singular/plural reduce to the -are base
    assert canon({"Näringsidkare", "Näringsidkaren", "Näringsidkarna"}) == {
        "Näringsidkare": ["Näringsidkare", "Näringsidkaren", "Näringsidkarna"]}


def test_bare_are_agent_noun_stays_distinct_from_its_base():
    # the crux: a bare -are is a base (agent noun), NOT an inflection -- so a
    # judge is not a verdict, an entrepreneur is not a company
    assert canon({"Domare", "Dom"}) == {"Domare": ["Domare"], "Dom": ["Dom"]}
    assert canon({"Företag", "Företaget", "Företagare"}) == {
        "Företag": ["Företag", "Företaget"], "Företagare": ["Företagare"]}


def test_merge_only_onto_an_observed_base():
    # an ambiguous -arna form merges only when its candidate base is real:
    # 'Bilarna' alone has no observed base, so it stays put
    assert canon({"Bilarna"}) == {"Bilarna": ["Bilarna"]}
    assert canon({"Bil", "Bilarna"}) == {"Bil": ["Bil", "Bilarna"]}


def test_casing_and_whitespace_are_folded():
    assert canon({"Annonssida på Internet", "Annonssida på internet"}) == {
        "Annonssida på Internet": ["Annonssida på Internet",
                                   "Annonssida på internet"]}


def test_multiword_head_inflection_on_the_last_word():
    assert canon({"Ansvar för marknadsföring", "Ansvar för marknadsföringen"}) == {
        "Ansvar för marknadsföring": ["Ansvar för marknadsföring",
                                      "Ansvar för marknadsföringen"]}


def test_wiki_form_wins_canonical_selection():
    concepts.register_wiki({"Näringsidkare"})
    # even though "näringsidkare" (lower) sorts first, the wiki display form wins
    g = concepts.cluster({"näringsidkare", "Näringsidkarna"})
    assert g == {"Näringsidkare": ["Näringsidkarna", "näringsidkare"]}


def test_alias_override_forces_a_merge():
    concepts._OVERRIDES = {"alias": {"ab": "Aktiebolag"}, "distinct": []}
    g = concepts.cluster({"Aktiebolag", "AB"})
    assert g == {"Aktiebolag": ["AB", "Aktiebolag"]}


def test_keep_distinct_blocks_a_wrong_merge():
    concepts._OVERRIDES = {"alias": {},
                           "distinct": [{"talan", "talerätt"}]}
    # were they to share a key, keep_distinct splits them back apart
    g = concepts.cluster({"Talan", "Talerätt"})
    assert set(g) == {"Talan", "Talerätt"}
