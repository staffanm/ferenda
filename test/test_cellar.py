"""The CELLAR metadata layer: which predicates survive into a stored notice,
and how the validity pair is read back out of one.

The bug these lock in was a predicate *name*. `META_PREDICATES` asked for
`cdm#start_of_validity` / `cdm#end_of_validity`, which match no triple in the
graph -- CELLAR writes `resource_legal_date_entry-into-force` and
`resource_legal_date_end-of-validity` -- so `KEEP_PREDICATES` dropped every
validity statement and no stored notice ever carried a repeal date. Nothing
noticed, because a filter that keeps nothing looks exactly like a source that
says nothing (rule:lock-in-with-fixture).
"""

import pytest

from ferenda.lib import cellar

CDM = "http://publications.europa.eu/ontology/cdm#"
XSD = "http://www.w3.org/2001/XMLSchema#"

# what `rapper -o ntriples` emits for a CELLAR work notice, trimmed to the
# metadata a stored notice keeps. The bulk unpacker filters exactly these lines
# through KEEP_PREDICATES and writes the survivors as the notice.
NTRIPLES = "\n".join([
    '<http://x> <%sresource_legal_id_celex> "31995L0046" .' % CDM,
    '<http://x> <%sresource_legal_id_sector> "3" .' % CDM,
    '<http://x> <%swork_date_document> "1995-10-24"^^<%sdate> .' % (CDM, XSD),
    '<http://x> <%sresource_legal_in-force> "false"^^<%sboolean> .' % (CDM, XSD),
    '<http://x> <%sresource_legal_date_end-of-validity> "2018-05-24"'
    '^^<%sdate> .' % (CDM, XSD),
    '<http://x> <%sresource_legal_date_entry-into-force> "1995-12-13"'
    '^^<%sdate> .' % (CDM, XSD),
    '<http://x> <%swork_is_about_concept_eurovoc> <http://eurovoc.europa.eu/2828> .'
    % CDM,
    # noise the filter is supposed to drop
    '<http://x> <%sresource_legal_comment_internal> "VIG1O" .' % CDM,
    '<http://x> <%sresource_legal_repertoire> "REP" .' % CDM,
]) + "\n"


@pytest.mark.parametrize("predicate", [
    "resource_legal_in-force",
    "resource_legal_date_end-of-validity",
    "resource_legal_date_entry-into-force",
    "resource_legal_id_celex",
    "work_date_document",
    "work_is_about_concept_eurovoc",
])
def test_the_kept_predicates_survive_the_filter(predicate):
    """Each name is one CELLAR writes on a real work. This drives
    `keep_triples`, not the constant, so a predicate the graph does state but
    the filter drops fails here rather than silently keeping nothing.

    (That a name is the one CELLAR uses is not something a test can settle --
    only the endpoint can. What it does settle is that the filter agrees with
    the name, which is where the original bug sat: `end_of_validity` matched no
    triple, so the filter kept none and nothing said so.)"""
    line = '<http://x> <%s%s> "v" .' % (CDM, predicate)
    assert [t[2] for t in cellar.keep_triples([line])] == [CDM + predicate]


def test_the_filter_keeps_the_validity_pair_and_drops_the_noise():
    """The bulk-unpack path's filter, over the n-triples rapper produces. This
    is the step that lost the repeal for every dump-imported document."""
    kept = [line for line, *_ in cellar.keep_triples(NTRIPLES.splitlines())]
    assert any("resource_legal_in-force" in line for line in kept)
    assert any("resource_legal_date_end-of-validity" in line for line in kept)
    assert not any("comment_internal" in line or "repertoire" in line
                   for line in kept)


def test_a_filtered_notice_reads_back_as_a_repeal(tmp_path):
    """End to end over the bulk shape: filter the n-triples the way
    `parse_notice` does, store the survivors as the notice, read the repeal out.
    Note the boolean object -- rapper renders CELLAR's flag as
    `"false"^^xsd:boolean`, not as the `"0"` the SPARQL path returns, and
    reading only the digit form made a repealed act read as in force."""
    kept = cellar.Notice(cellar.keep_triples(NTRIPLES.splitlines()))
    (tmp_path / "notice.ttl").write_bytes(kept.ttl())
    assert cellar.notice_repeal_date(tmp_path) == "2018-05-24"
    assert cellar.notice_work_date(tmp_path) == "1995-10-24"
    assert cellar.notice_validity(tmp_path) == ("false", "2018-05-24")


def test_notice_validity_reports_what_the_notice_does_not_state(tmp_path):
    """The stored pair is what a refresh falls back on, so "the notice says
    nothing" has to be distinguishable from "the notice says in force"."""
    (tmp_path / "notice.ttl").write_bytes(
        cellar.notice_ttl("31995L0046", "1995-10-24", []))
    assert cellar.notice_validity(tmp_path) == (None, None)
    assert cellar.notice_repeal_date(tmp_path) is None
    assert cellar.notice_validity(tmp_path / "nonexistent") == (None, None)


@pytest.mark.parametrize("dates,expected", [
    (["2018-05-24"], "2018-05-24"),
    # 31981L0576 carries two; EUR-Lex prints the last
    (["1996-08-05", "2014-10-31"], "2014-10-31"),
    # the placeholder is not a date, whatever it sorts as
    (["2014-10-31", cellar.OPEN_ENDED], "2014-10-31"),
    ([cellar.OPEN_ENDED], None),
    ([""], None),
    ([], None),
])
def test_latest_end_of_validity(dates, expected):
    assert cellar.latest_end_of_validity(dates) == expected


def _bind(**kw):
    return {k: {"value": v} for k, v in kw.items()}


def test_fetch_metadata_folds_the_rows_into_one_validity_pair(monkeypatch):
    """The SPARQL answer is one row per (concept x end-date) combination, so the
    validity pair has to be folded out of several rows -- the mapping the
    download tests all monkeypatch away."""
    monkeypatch.setattr(cellar, "sparql_select", lambda s, q: [
        _bind(celex="31995L0046", wdate="1995-10-24T00:00:00",
              inforce="0", eov="1996-08-05",
              concept="http://eurovoc.europa.eu/2828"),
        _bind(celex="31995L0046", inforce="0", eov="2014-10-31"),
        _bind(celex="31995L0046", inforce="0", eov=cellar.OPEN_ENDED),
        # in force, carrying a past end date: the flag decides, not the date
        _bind(celex="32006L0040", inforce="1", eov="2009-04-28"),
        # answered, but with nothing bound beyond its identity
        _bind(celex="32016R0679"),
    ])
    wdate, eurovoc, validity, answered = cellar.fetch_metadata(
        None, ["31995L0046", "32006L0040", "32016R0679", "31999L0000"])

    assert wdate == {"31995L0046": "1995-10-24"}
    assert eurovoc["31995L0046"] == ["http://eurovoc.europa.eu/2828"]
    assert validity["31995L0046"] == ("0", "2014-10-31")
    assert validity["32006L0040"] == ("1", "2009-04-28")
    # answered, but stating nothing about validity -- absent from `validity` so
    # a rewrite keeps whatever the stored notice already says, rather than
    # overwriting a recorded repeal with (None, None)
    assert "32016R0679" not in validity
    # the CELEX the endpoint bound no row for is absent from `answered`, which
    # is what stops `refresh_metadata` rewriting its notice from an empty answer
    assert answered == {"31995L0046", "32006L0040", "32016R0679"}
    assert "31999L0000" not in validity


def test_fetch_repeals_collects_both_edge_kinds(monkeypatch):
    """A repeal is announced only by the incoming act, and both the express and
    the implied clause count: 32016R0679 repeals 31995L0046 expressly and
    32003R1882 by implication, and both stopped applying."""
    monkeypatch.setattr(cellar, "sparql_select", lambda s, q: [
        _bind(celex="32016R0679", repealed="31995L0046"),
        _bind(celex="32016R0679", repealed="32003R1882"),
        _bind(celex="32016R0679", repealed="31995L0046"),      # duplicated row
    ])
    assert cellar.fetch_repeals(None, ["32016R0679"]) == {
        "32016R0679": ["31995L0046", "32003R1882"]}


def test_a_notice_round_trips_what_the_act_amends_and_implements(tmp_path):
    """The relation that separates a base act from an act that only maintains
    another one. Both predicates matter: CELLAR tags the 2026 terrorist-list
    regulation `amends` and the 2025 one `implements`, so reading only one of
    them keeps half the ladder in the population."""
    (tmp_path / "notice.ttl").write_bytes(cellar.notice_ttl(
        "32008R0803", "2008-08-08", [], ("1", None),
        {"amends": ["32002R0881"], "implements": ["32001R2580"]}))
    assert cellar.notice_relations(tmp_path) == {
        "amends": ["32002R0881"], "implements": ["32001R2580"]}
    # the other metadata is unaffected by the new triples
    assert cellar.notice_work_date(tmp_path) == "2008-08-08"
    assert cellar.notice_repeal_date(tmp_path) is None


def test_a_notice_without_relations_says_so(tmp_path):
    """A base act carries neither relation, and "carries none" has to be
    distinguishable from "was never asked" -- refresh_metadata falls back on
    the stored relations exactly the way it falls back on the stored date."""
    (tmp_path / "notice.ttl").write_bytes(
        cellar.notice_ttl("32002R0881", "2002-05-27", []))
    assert cellar.notice_relations(tmp_path) == {}
    assert cellar.notice_relations(tmp_path / "nonexistent") == {}


def test_a_treaty_celex_survives_the_relation_round_trip(tmp_path):
    """A treaty CELEX carries a document suffix of its own -- 11992M/TXT,
    11997D/TXTR(01) -- and 1 902 of the eurlex documents we hold are keyed
    that way. `notice_ttl` percent-encodes the target, so a read that did not
    unquote produced `11997D%2FTXTR%2801%29`, and the artifact got a uri no
    document has. Taking the last path segment instead of everything after
    `celex/` reads 12007L/TXT as the CELEX "TXT"."""
    relations = {"amends": ["11997D/TXTR(01)"], "implements": ["12007L/TXT"]}
    (tmp_path / "notice.ttl").write_bytes(
        cellar.notice_ttl("32008R0803", "2008-08-08", [], ("1", None),
                          relations))
    assert cellar.notice_relations(tmp_path) == relations


def test_a_dump_notice_states_the_relation_target_unencoded(tmp_path):
    """The bulk unpacker keeps CELLAR's own object uri, which is not
    percent-encoded. One read has to serve both shapes -- `unquote` is a no-op
    on this one."""
    (tmp_path / "notice.ttl").write_bytes(
        b'<x> <' + CDM.encode() + b'resource_legal_amends_resource_legal> '
        b'<http://publications.europa.eu/resource/celex/12007L/TXT> .\n')
    assert cellar.notice_relations(tmp_path) == {"amends": ["12007L/TXT"]}


def test_the_filter_keeps_the_relations_in_a_dump_notice():
    """A bulk-unpacked notice has to carry the relations too, or an act
    imported from a dump reads as a base act while the same act harvested live
    reads as an amending one."""
    ntriples = NTRIPLES + "\n".join([
        '<http://x> <%sresource_legal_amends_resource_legal> '
        '<http://publications.europa.eu/resource/celex/32002R0881> .' % CDM,
        '<http://x> <%sresource_legal_implements_resource_legal> '
        '<http://publications.europa.eu/resource/celex/32001R2580> .' % CDM,
    ]) + "\n"
    kept = [line for line, *_ in cellar.keep_triples(ntriples.splitlines())]
    assert any("resource_legal_amends_resource_legal" in line for line in kept)
    assert any("resource_legal_implements_resource_legal" in line
               for line in kept)
