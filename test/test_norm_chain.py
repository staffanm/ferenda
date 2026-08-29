"""The norm hierarchy: which rule derives its authority from which.

The chain is deliberately narrower than the citation graph. Only the typed
authority relations reach it -- a föreskrift naming the paragraf that empowers
it, a statute naming the directive it transposes, a förordning naming the act it
completes. Ordinary cross-references, a sibling föreskrift amending another, a
förarbete or a dom discussing a föreskrift: real context, not authority.

`norm_chain` is metadata with no reader yet: a first attempt to render it in the
context rail was withdrawn, the display being the hard part rather than the
data. These tests cover what is stored, not how it is shown.
"""

import sqlite3

import pytest

from ferenda.lib import catalog
from ferenda.sfs import bemyndigande


@pytest.fixture
def con():
    con = sqlite3.connect(":memory:")
    con.executescript(catalog.SCHEMA)
    docs = [("https://lagen.nu/celex/32022L2555", "eurlex", "directive"),
            ("https://lagen.nu/2025:1506", "sfs", "lag"),
            ("https://lagen.nu/2025:1507", "sfs", "forordning"),
            ("https://lagen.nu/mcffs/2026:8", "foreskrift", "mcffs"),
            ("https://lagen.nu/prop/2025/26:28", "forarbete", "prop")]
    con.executemany("INSERT INTO documents (uri, source, kind, path) "
                    "VALUES (?,?,?,'x')", docs)
    return con


def link(con, from_uri, from_anchor, predicate, to_uri):
    con.execute("INSERT INTO links (from_uri, from_anchor, predicate, to_uri, "
                "to_root) VALUES (?,?,?,?,?)",
                (from_uri, from_anchor, predicate, to_uri, to_uri.split("#")[0]))


def _rows(con):
    """Every stored row, ordered so a test can name them -- an absent pinpoint
    sorts before a present one rather than blowing the comparison up."""
    return sorted((tuple(r) for r in con.execute(
        "SELECT lower_uri, lower_pin, upper_uri, upper_pin, predicate, "
        "       lower_level, upper_level FROM norm_chain")),
        key=lambda r: tuple(v if v is not None else "" for v in r))


def test_the_chain_spans_every_rung(con):
    link(con, "https://lagen.nu/2025:1506", "K2P9", "rpubl:genomforDirektiv",
         "https://lagen.nu/celex/32022L2555#23")
    link(con, "https://lagen.nu/2025:1507", "P37S1", "rpubl:bemyndigande",
         "https://lagen.nu/2025:1506#K2P14")
    link(con, "https://lagen.nu/mcffs/2026:8", None, "rpubl:bemyndigande",
         "https://lagen.nu/2025:1507")
    assert catalog.rebuild_norm_chain(con) == 3
    # each row records both ends and the rung each sits on, so a reader can
    # walk the chain in either direction without re-deriving the hierarchy
    assert [(r[0], r[2], r[5], r[6]) for r in _rows(con)] == [
        ("https://lagen.nu/2025:1506", "https://lagen.nu/celex/32022L2555",
         1, 0),
        ("https://lagen.nu/2025:1507", "https://lagen.nu/2025:1506", 2, 1),
        ("https://lagen.nu/mcffs/2026:8", "https://lagen.nu/2025:1507", 3, 2)]


def test_plain_references_never_reach_the_chain(con):
    """MCFFS 2026:8 mentions cybersäkerhetslagen fourteen times, of which one is
    the statement of what it implements; a förarbete discussing a föreskrift is
    context, not authority. Admitting either turns the ladder into the citation
    graph it exists to stand apart from."""
    link(con, "https://lagen.nu/mcffs/2026:8", "K1P2", "dcterms:references",
         "https://lagen.nu/2025:1506#K1P2")
    link(con, "https://lagen.nu/prop/2025/26:28", "a3", "dcterms:references",
         "https://lagen.nu/mcffs/2026:8")
    assert catalog.rebuild_norm_chain(con) == 0


def test_a_relation_between_equals_is_not_authority(con):
    """One föreskrift amending another is a relation between two rules on the
    same rung."""
    con.execute("INSERT INTO documents (uri, source, kind, path) "
                "VALUES ('https://lagen.nu/mcffs/2026:9','foreskrift','mcffs','x')")
    link(con, "https://lagen.nu/mcffs/2026:9", None, "rpubl:andrar",
         "https://lagen.nu/mcffs/2026:8")
    assert catalog.rebuild_norm_chain(con) == 0


def test_a_delegation_records_the_provision_it_authorises(con):
    """A förordning states one delegation per punkt, each naming both the
    empowering provision and the provisions of its own it authorises, so the
    chain is walkable provision-to-provision rather than only document-to-
    document."""
    link(con, "https://lagen.nu/2025:1507", "P4", "rpubl:bemyndigande",
         "https://lagen.nu/2025:1506#K1P8")
    link(con, "https://lagen.nu/2025:1507", "P37S1", "rpubl:bemyndigande",
         "https://lagen.nu/2025:1506#K2P14")
    link(con, "https://lagen.nu/2025:1507", None, "rinfoex:kompletterar",
         "https://lagen.nu/2025:1506")
    catalog.rebuild_norm_chain(con)
    assert [(r[1], r[3]) for r in _rows(con)] == [
        (None, None),                 # the document-level kompletterar
        ("P37S1", "K2P14"),
        ("P4", "K1P8")]


def test_norm_level_is_read_from_catalog_data():
    assert catalog.norm_level("sfs", "lag") == 1
    assert catalog.norm_level("sfs", "forordning") == 2
    assert catalog.norm_level("foreskrift", "pmfs") == 3
    assert catalog.norm_level("eurlex", "directive") == 0
    # a document that is not itself a rule has no rung
    assert catalog.norm_level("forarbete", "prop") is None
    assert catalog.norm_level("dv", "case") is None


# --------------------------------------------------------------------------
# the two ingress formulas that place a förordning under its lag
# --------------------------------------------------------------------------

def _node(text_runs, children=()):
    return {"type": "stycke", "text": text_runs, "children": list(children)}


def _ref(uri, text):
    return {"predicate": "dcterms:references", "uri": uri, "text": text}


def test_bemyndigandeupplysning_is_provision_precise():
    """"Denna förordning är meddelad med stöd av 1. 1 kap. 8 § cybersäkerhets-
    lagen i fråga om 4 §" names both ends, so the chain can be walked provision
    to provision. The punkt is recognised structurally -- references out of the
    document are the delegation, references into it are what it authorises."""
    structure = [_node(["Denna förordning är meddelad med stöd av"], [
        _node(["1. ", _ref("https://lagen.nu/2025:1506#K1P8", "1 kap. 8 §"),
               " i fråga om ", _ref("https://lagen.nu/2025:1507#P4", "4 §")]),
        _node(["2. ", _ref("https://lagen.nu/2025:1506#K2P14", "2 kap. 14 §"),
               " i fråga om ", _ref("https://lagen.nu/2025:1507#P37S1", "37 §")]),
    ])]
    assert bemyndigande.extract(structure, "https://lagen.nu/2025:1507") == {
        "bemyndigande": [
            {"lagrum": "https://lagen.nu/2025:1506#K1P8",
             "provisions": ["https://lagen.nu/2025:1507#P4"]},
            {"lagrum": "https://lagen.nu/2025:1506#K2P14",
             "provisions": ["https://lagen.nu/2025:1507#P37S1"]}],
        "kompletterar": []}


def test_kompletterar_ingress_places_an_older_forordning():
    """Säkerhetsskyddsförordningen states no bemyndigandeupplysning; its ingress
    is all there is, and it is enough to put it under säkerhetsskyddslagen."""
    structure = [_node(["Denna förordning innehåller kompletterande "
                        "bestämmelser till ",
                        _ref("https://lagen.nu/2018:585",
                             "säkerhetsskyddslagen (2018:585)"), "."])]
    out = bemyndigande.extract(structure, "https://lagen.nu/2021:955")
    assert out["kompletterar"] == ["https://lagen.nu/2018:585"]


def test_regeringsformen_alone_yields_no_parent_act():
    """A förordning issued under the government's own residual power (8 kap. 7 §
    RF) has no delegating lag above it -- a true fact, and not an edge."""
    structure = [_node(["Denna förordning är meddelad med stöd av ",
                        _ref("https://lagen.nu/1974:152#K8P7",
                             "8 kap. 7 § regeringsformen"), "."])]
    assert bemyndigande.extract(structure, "https://lagen.nu/1986:172") == {
        "bemyndigande": [], "kompletterar": []}


def test_a_lag_states_neither_formula():
    structure = [_node(["Denna lag gäller för den som bedriver verksamhet."])]
    assert bemyndigande.extract(structure, "https://lagen.nu/2018:585") == {
        "bemyndigande": [], "kompletterar": []}


def test_a_lag_that_complements_an_eu_act_carries_the_edge():
    """Dataskyddslagen 1 kap. 1 § opens "Denna lag kompletterar
    Europaparlamentets och rådets förordning (EU) 2016/679" -- the GDPR shape,
    an EU förordning detailed by Swedish law. The förordning-only alternation
    left 0 lagar with the edge, hiding the whole family from the chain."""
    gdpr = "https://lagen.nu/celex/32016R0679"
    verb = [_node(["Denna lag kompletterar ",
                   _ref(gdpr, "Europaparlamentets och rådets förordning (EU) "
                              "2016/679"), "."])]
    assert bemyndigande.extract(verb, "https://lagen.nu/2018:218") == {
        "bemyndigande": [], "kompletterar": [gdpr]}
    ingress = [_node(["Denna lag innehåller kompletterande bestämmelser "
                      "till ", _ref(gdpr, "EU:s dataskyddsförordning"), "."])]
    assert bemyndigande.extract(ingress, "https://lagen.nu/2018:218") == {
        "bemyndigande": [], "kompletterar": [gdpr]}


def test_lag_to_eu_regulation_kompletterar_reaches_the_chain(con):
    """The lag->EU rung: kompletterar at levels 1 -> 0, the same walk the
    bemyndigande edge makes at 3 -> 2."""
    con.execute("INSERT INTO documents (uri, source, kind, path) VALUES "
                "('https://lagen.nu/celex/32016R0679','eurlex',"
                "'regulation','x')")
    con.execute("INSERT INTO documents (uri, source, kind, path) VALUES "
                "('https://lagen.nu/2018:218','sfs','lag','x')")
    link(con, "https://lagen.nu/2018:218", None, "rinfoex:kompletterar",
         "https://lagen.nu/celex/32016R0679")
    assert catalog.rebuild_norm_chain(con) == 1
    assert [(r[0], r[2], r[5], r[6]) for r in _rows(con)] == [
        ("https://lagen.nu/2018:218", "https://lagen.nu/celex/32016R0679",
         1, 0)]


def test_a_reference_embedded_in_the_cited_acts_title_is_not_complemented():
    """The GDPR's full title itself cites direktiv 95/46/EG ("... och om
    upphävande av direktiv 95/46/EG"). That reference is part of the cited
    act's name, and must not become a second kompletterar edge."""
    gdpr = "https://lagen.nu/celex/32016R0679"
    old = "https://lagen.nu/celex/31995L0046"
    structure = [_node([
        "Denna lag kompletterar ",
        _ref(gdpr, "Europaparlamentets och rådets förordning (EU) 2016/679"),
        " om skydd för fysiska personer och om upphävande av direktiv ",
        _ref(old, "95/46/EG"),
        " (allmän dataskyddsförordning)."])]
    assert bemyndigande.extract(structure, "https://lagen.nu/2018:218") == {
        "bemyndigande": [], "kompletterar": [gdpr]}
