"""The regleringshierarki builder (lib.hierarki): the delegation-clause lag
rung, and the per-concept ladder rows. Same fixture idiom as test_norm_chain --
an in-memory catalog with hand-inserted documents/links rows; what is stored,
not how it is shown."""

import json
import re
import sqlite3
from pathlib import Path

import pytest

from ferenda.lib import aihierarki, annstore, catalog, hierarki, page
from ferenda.lib.concepts import term_pattern
from ferenda.lib.util import normalize_fold
from ferenda.wiki import render as wiki_render

LAG = "https://lagen.nu/2003:364"          # fartygssäkerhetslagen
FORORDNING = "https://lagen.nu/2003:438"   # fartygssäkerhetsförordningen
FORESKRIFT = "https://lagen.nu/tsfs/2009:114"
RF = "https://lagen.nu/1974:152"


@pytest.fixture
def con():
    con = sqlite3.connect(":memory:")
    con.executescript(catalog.SCHEMA)
    docs = [(LAG, "sfs", "lag"),
            (FORORDNING, "sfs", "forordning"),
            (FORESKRIFT, "foreskrift", "tsfs"),
            (RF, "sfs", "lag"),
            ("https://lagen.nu/ext/celex/32009L0045", "eurlex", "directive")]
    con.executemany("INSERT INTO documents (uri, source, kind, path) "
                    "VALUES (?,?,?,'x')", docs)
    return con


def link(con, from_uri, from_anchor, predicate, to_uri):
    con.execute("INSERT INTO links (from_uri, from_anchor, predicate, to_uri, "
                "to_root) VALUES (?,?,?,?,?)",
                (from_uri, from_anchor, predicate, to_uri, to_uri.split("#")[0]))


def chain_row(con, lower, lpin, upper, upin, predicate, llvl, ulvl):
    con.execute("INSERT INTO norm_chain VALUES (?,?,?,?,?,?,?)",
                (lower, lpin, upper, upin, predicate, llvl, ulvl))


def _edges(con):
    return sorted(tuple(r) for r in con.execute(
        "SELECT lower_uri, lower_pin, upper_uri, upper_pin "
        "FROM delegation_edge"))


# --------------------------------------------------------------------------
# derive_delegation_edges: the förordning->lag rung from the clause's own links
# --------------------------------------------------------------------------

def test_the_delegation_clause_citation_becomes_the_lag_rung(con):
    """The föreskrift stands on 7 kap. 2 § of the förordning, and that
    provision's own text cites 7 kap. 2 § 1 fartygssäkerhetslagen -- an inline
    link at stycke granularity, which maps under its paragraf pin."""
    chain_row(con, FORESKRIFT, None, FORORDNING, "K7P2",
              "rpubl:bemyndigande", 3, 2)
    link(con, FORORDNING, "K7P2S1", "dcterms:references", LAG + "#K7P2")
    assert hierarki.derive_delegation_edges(con) == (1, 0)
    assert _edges(con) == [(FORORDNING, "K7P2", LAG, "K7P2")]


def test_a_regeringsformen_citation_yields_no_edge(con):
    """A clause resting on 8 kap. RF has no delegating lag above it."""
    chain_row(con, FORESKRIFT, None, FORORDNING, "P18",
              "rpubl:bemyndigande", 3, 2)
    link(con, FORORDNING, "P18", "dcterms:references", RF + "#K8P7")
    assert hierarki.derive_delegation_edges(con) == (0, 0)


def test_only_lag_level_targets_qualify(con):
    """A delegation clause cites its EU basis and sibling förordningar too;
    only a level-1 document is the lag rung."""
    chain_row(con, FORESKRIFT, None, FORORDNING, "P4",
              "rpubl:bemyndigande", 3, 2)
    link(con, FORORDNING, "P4", "dcterms:references",
         "https://lagen.nu/ext/celex/32009L0045#9.2")
    link(con, FORORDNING, "P4S2", "dcterms:references", LAG + "#K7P2")
    assert hierarki.derive_delegation_edges(con) == (1, 0)
    assert _edges(con) == [(FORORDNING, "P4", LAG, "K7P2")]


def test_a_pinned_citation_beats_the_bare_law(con):
    """"... enligt 7 kap. 2 § 1 fartygssäkerhetslagen (2003:364)" links both
    the paragraf and (in other clauses) the bare law; the paragraf is the
    edge, the bare mention is noise -- the föreskrift parse's own rule."""
    chain_row(con, FORESKRIFT, None, FORORDNING, "P4",
              "rpubl:bemyndigande", 3, 2)
    link(con, FORORDNING, "P4", "dcterms:references", LAG + "#K7P2")
    link(con, FORORDNING, "P4", "dcterms:references", LAG)
    assert hierarki.derive_delegation_edges(con) == (1, 0)
    assert _edges(con) == [(FORORDNING, "P4", LAG, "K7P2")]


def test_an_edge_the_forordning_states_itself_is_not_duplicated(con):
    """132 förordningar carry the bemyndigandeupplysning; where the stated
    edge and the derived one agree exactly, the stated row is the record and
    the derived one is only counted."""
    chain_row(con, FORESKRIFT, None, FORORDNING, "P4",
              "rpubl:bemyndigande", 3, 2)
    chain_row(con, FORORDNING, "P4", LAG, "K7P2",
              "rpubl:bemyndigande", 2, 1)
    link(con, FORORDNING, "P4", "dcterms:references", LAG + "#K7P2")
    assert hierarki.derive_delegation_edges(con) == (0, 1)
    assert _edges(con) == []


def test_an_unpinned_foreskrift_edge_reads_no_clause(con):
    """A föreskrift edge with no provision pin names no clause to read."""
    chain_row(con, FORESKRIFT, None, FORORDNING, None,
              "rpubl:bemyndigande", 3, 2)
    link(con, FORORDNING, "P4", "dcterms:references", LAG + "#K7P2")
    assert hierarki.derive_delegation_edges(con) == (0, 0)


def test_a_neighbouring_provisions_citation_is_not_swept_in(con):
    """P4's clause is read; P41 is a different provision, and P4's LIKE match
    must not leak into it."""
    chain_row(con, FORESKRIFT, None, FORORDNING, "P4",
              "rpubl:bemyndigande", 3, 2)
    link(con, FORORDNING, "P41", "dcterms:references", LAG + "#K7P2")
    link(con, FORORDNING, "P41S1", "dcterms:references", LAG + "#K7P3")
    assert hierarki.derive_delegation_edges(con) == (0, 0)


# --------------------------------------------------------------------------
# rebuild_regleringshierarki: the ladder rows
# --------------------------------------------------------------------------

DIREKTIV = "https://lagen.nu/ext/celex/32022L2555"     # NIS2
CSL = "https://lagen.nu/2025:1506"                     # cybersäkerhetslagen
CSF = "https://lagen.nu/2025:1507"                     # cybersäkerhetsförordningen
MCFFS = "https://lagen.nu/mcffs/2026:8"
CONCEPT = "https://lagen.nu/begrepp/Betydande_incident"


def _write(tmp_path, rel, art):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(art), encoding="utf-8")
    return rel


def _ladder_con(tmp_path):
    """A real on-disk catalog whose artifacts hold the betydande incident
    shape: the directive and the lag define the term, the förordning is
    silent but its delegation provision names it, the föreskrift restates it
    in a long löptext phrase (PRD §5 rule 3)."""
    arts = {
        DIREKTIV: ("eurlex", "directive", "2022-12-14", "eurlex/nis2.json", {
            "uri": DIREKTIV, "structure": [
                {"type": "artikel", "id": "23.3",
                 "text": ["En incident ska anses vara betydande om ..."]}]}),
        CSL: ("sfs", "lag", "2025-11-20", "sfs/csl.json", {
            "uri": CSL, "structure": [
                {"type": "paragraf", "id": "K2P5",
                 "text": ["Med betydande incident avses en incident som ..."]}],
            "amendments": []}),
        CSF: ("sfs", "forordning", "2025-12-04", "sfs/csf.json", {
            "uri": CSF, "structure": [
                {"type": "paragraf", "id": "P37", "text": [
                    "Myndigheten får meddela föreskrifter om vad som utgör "
                    "en betydande incident enligt 2 kap. 5 § "
                    "cybersäkerhetslagen. Förordning (2026:623)."]}],
            "amendments": [{"properties": {
                "dcterms:identifier": "SFS 2026:623",
                "rpubl:ikrafttradandedatum": "2026-07-01"}}]}),
        MCFFS: ("foreskrift", "mcffs", "2026-05-01", "foreskrift/mcffs.json", {
            "uri": MCFFS, "structure": [
                {"type": "kapitel", "id": "K3", "children": [
                    {"type": "paragraf", "id": "K3P1", "children": [
                        {"type": "stycke", "text": [
                            "Med betydande incident som har orsakat allvarlig "
                            "driftstörning för verksamhetsutövare avses en "
                            "incident där otillgänglighet uppstår."]}]}]}],
            "metadata": {}}),
    }
    con = catalog.connect(tmp_path / "catalog.db", data_root=tmp_path)
    for uri, (source, kind, date, rel, art) in arts.items():
        _write(tmp_path, rel, art)
        con.execute("INSERT INTO documents (uri, source, kind, date, path) "
                    "VALUES (?,?,?,?,?)", (uri, source, kind, date, rel))
    con.executemany(
        "INSERT INTO definitions (concept, from_uri, anchor, term, sentence) "
        "VALUES (?,?,?,?,?)",
        [(CONCEPT, DIREKTIV, "23.3", "betydande incident", "En incident ..."),
         (CONCEPT, CSL, "K2P5", "betydande incident", "Med betydande ...")])
    chain_row(con, CSL, "K2P5", DIREKTIV, "23", "rpubl:genomforDirektiv", 1, 0)
    chain_row(con, MCFFS, None, CSF, "P37", "rpubl:bemyndigande", 3, 2)
    con.execute("INSERT INTO delegation_edge VALUES (?,?,?,?)",
                (CSF, "P37", CSL, "K1P15"))
    con.commit()
    return con


def _hrows(con):
    return {(r[0], r[1]): r for r in con.execute(
        "SELECT concept, doc_uri, anchor, also, level, kind, role, label, "
        "chain_root, via, source, stated, upphavd, via_amended "
        "FROM regleringshierarki")}


def test_the_ladder_spans_all_four_rungs(tmp_path):
    con = _ladder_con(tmp_path)
    stats = hierarki.rebuild_regleringshierarki(con)
    rows = _hrows(con)
    assert stats["ladders"] == 1 and len(rows) == 4
    # every row hangs under the directive root, in rung order
    assert {r[8] for r in rows.values()} == {DIREKTIV}
    assert [rows[k][4] for k in sorted(rows, key=lambda k: rows[k][4])] == \
        [0, 1, 2, 3]


def test_the_defining_rungs_are_definierar_rows(tmp_path):
    con = _ladder_con(tmp_path)
    hierarki.rebuild_regleringshierarki(con)
    rows = _hrows(con)
    assert rows[(CONCEPT, DIREKTIV)][6] == "definierar"
    assert rows[(CONCEPT, DIREKTIV)][9] is None          # the root has no via
    assert rows[(CONCEPT, CSL)][6] == "definierar"
    assert rows[(CONCEPT, CSL)][2] == "K2P5"


def test_the_delegation_provision_is_a_delegerar_row(tmp_path):
    """The förordning never defines the term; its P37 names it while
    delegating, and P37 is the pin the föreskrift stands on."""
    con = _ladder_con(tmp_path)
    hierarki.rebuild_regleringshierarki(con)
    row = _hrows(con)[(CONCEPT, CSF)]
    assert (row[2], row[6], row[10]) == ("P37", "delegerar", "verbatim")


def test_the_long_loptext_phrase_files_under_the_upper_concept(tmp_path):
    """"Med betydande incident som har orsakat allvarlig driftstörning ...
    avses" mints no concept of its own; it aligns against the chain's
    *betydande incident* and keeps the whole phrase as the row's label."""
    con = _ladder_con(tmp_path)
    hierarki.rebuild_regleringshierarki(con)
    row = _hrows(con)[(CONCEPT, MCFFS)]
    assert row[6] == "definierar"
    assert row[7].startswith("betydande incident som har orsakat")
    assert row[2] == "K3P1"


def test_via_walks_to_the_root_and_dates_the_shaken_pin(tmp_path):
    """The föreskrift's path climbs all three edges; CSF's P37 lydelse
    trailer says Förordning (2026:623), in force 2026-07-01 -- after the
    föreskrift's own date, so the edge is dated, not broken (PRD §9.4)."""
    con = _ladder_con(tmp_path)
    hierarki.rebuild_regleringshierarki(con)
    row = _hrows(con)[(CONCEPT, MCFFS)]
    via = json.loads(row[9])
    assert [(e[0], e[2]) for e in via] == [
        (MCFFS, CSF), (CSF, CSL), (CSL, DIREKTIV)]
    assert row[13] == "2026-07-01"


def test_a_lone_definition_with_no_chain_yields_no_ladder(tmp_path):
    con = _ladder_con(tmp_path)
    lone = "https://lagen.nu/1987:818"
    rel = _write(tmp_path, "sfs/lone.json",
                 {"uri": lone, "structure": []})
    con.execute("INSERT INTO documents (uri, source, kind, date, path) "
                "VALUES (?,?,?,?,?)", (lone, "sfs", "lag", "1987-06-11", rel))
    con.execute("INSERT INTO definitions VALUES (?,?,?,?,?)",
                ("https://lagen.nu/begrepp/Bidragsgrundande_inkomst", lone,
                 "P4", "bidragsgrundande inkomst", "..."))
    con.commit()
    stats = hierarki.rebuild_regleringshierarki(con)
    assert ("https://lagen.nu/begrepp/Bidragsgrundande_inkomst", lone) \
        not in _hrows(con)
    assert stats["defs_off_chain"] == 1


def test_sector_restatements_collapse_onto_one_row(tmp_path):
    """MCFFS 2026:8 restates the definition per sector (O4): one row, the
    first anchor primary, the rest in `also`."""
    con = _ladder_con(tmp_path)
    art = json.loads((tmp_path / "foreskrift/mcffs.json").read_text())
    art["structure"].append(
        {"type": "kapitel", "id": "K4", "children": [
            {"type": "paragraf", "id": "K4P1", "children": [
                {"type": "stycke", "text": [
                    "Med betydande incident som har orsakat allvarlig "
                    "driftstörning för verksamhetsutövare inom sektorn "
                    "energi avses en incident där ..."]}]}]})
    (tmp_path / "foreskrift/mcffs.json").write_text(json.dumps(art))
    hierarki.rebuild_regleringshierarki(con)
    row = _hrows(con)[(CONCEPT, MCFFS)]
    assert row[2] == "K3P1" and json.loads(row[3]) == ["K4P1"]


def test_a_repealed_rung_publishes_dated(tmp_path):
    """Three of the ten worked examples stand on a repealed förordning; the
    rung renders marked upphävd, never dropped (O6). A repealed föreskrift's
    date is the repealing document's ikraftträdande, not its beslutsdatum."""
    con = _ladder_con(tmp_path)
    con.execute("UPDATE documents SET expired = '2015-07-01' WHERE uri = ?",
                (CSF,))
    repealer = "https://lagen.nu/mcffs/2027:1"
    rel = _write(tmp_path, "foreskrift/repealer.json",
                 {"uri": repealer,
                  "metadata": {"ikrafttradandedatum": "2027-03-01"},
                  "structure": []})
    con.execute("INSERT INTO documents (uri, source, kind, date, path) "
                "VALUES (?,?,?,?,?)",
                (repealer, "foreskrift", "mcffs", "2027-01-15", rel))
    link(con, repealer, None, "rpubl:upphaver", MCFFS)
    con.commit()
    hierarki.rebuild_regleringshierarki(con)
    rows = _hrows(con)
    assert rows[(CONCEPT, CSF)][12] == "2015-07-01"
    assert rows[(CONCEPT, MCFFS)][12] == "2027-03-01"
    assert rows[(CONCEPT, CSL)][12] is None


# --------------------------------------------------------------------------
# display: the begreppssida section, the rail lines, the freshness fold
# --------------------------------------------------------------------------

def _site(con):
    return page.Site.from_catalog(con)


def test_the_begreppssida_renders_the_ladder_grouped_and_marked(tmp_path):
    con = _ladder_con(tmp_path)
    con.execute("UPDATE documents SET expired = '2015-07-01' WHERE uri = ?",
                (CSF,))
    con.commit()
    hierarki.rebuild_regleringshierarki(con)
    art = {"uri": CONCEPT, "title": "Betydande incident"}
    html = wiki_render.render(art, _site(con))
    assert '<section class="occurrences regleringshierarki">' in html
    assert 'id="rh-ext-celex-32022L2555"' in html
    assert "EU-direktiv" in html
    assert "upphävd 2015-07-01" in html
    assert "bemyndigandet ändrat 2026-07-01" in html
    # the described branch carries the section too
    art["body"] = [{"type": "stycke", "text": ["En beskrivning."]}]
    assert '<section class="occurrences regleringshierarki">' \
        in wiki_render.render(art, _site(con))


def test_the_rail_prints_one_line_per_concept_at_the_ladder_anchor(tmp_path):
    con = _ladder_con(tmp_path)
    hierarki.rebuild_regleringshierarki(con)
    site = _site(con)
    [section] = page.regleringshierarki_margin(site, CSL, ["K2P5"])
    assert section.key == "regleringshierarki"
    assert "Betydande incident" in section.html
    assert "#rh-ext-celex-32022L2555" in section.html
    # a definition anchored below the panel node still attaches (containment)
    assert page.regleringshierarki_margin(site, CSF, ["P37"])
    # a provision with no row stays silent
    assert page.regleringshierarki_margin(site, CSL, ["K9P9"]) == []


def test_the_fyller_ut_line_sits_on_chapter_panels_only(tmp_path):
    con = _ladder_con(tmp_path)
    hierarki.rebuild_regleringshierarki(con)
    site = _site(con)
    [section] = page.fyller_ut_margin(site, MCFFS, ["K3"])
    assert section.key == "fyller-ut"
    assert "Dessa föreskrifter fyller ut" in section.html
    assert "1 kap. 15 §" in section.html
    assert page.fyller_ut_margin(site, MCFFS, ["K3P1"]) == []
    assert page.fyller_ut_margin(site, CSL, ["K2"]) == []


def test_an_ambiguous_chain_yields_no_fyller_ut_line(tmp_path):
    con = _ladder_con(tmp_path)
    chain_row(con, MCFFS, None, CSF, "P39", "rpubl:bemyndigande", 3, 2)
    con.commit()
    assert MCFFS not in hierarki.fyller_ut_index(con)


def test_a_ladder_row_changes_the_hosts_cross_digest(tmp_path):
    con = _ladder_con(tmp_path)
    hierarki.rebuild_regleringshierarki(con)
    before = page.site_cross_digests(_site(con))
    con.execute("DELETE FROM regleringshierarki WHERE doc_uri = ?", (CSF,))
    con.commit()
    after = page.site_cross_digests(_site(con))
    assert before.get(CSF) != after.get(CSF)          # the provision's page
    assert before.get(CONCEPT) != after.get(CONCEPT)  # the concept's page


# --------------------------------------------------------------------------
# the frozen worked examples (PRD §10): mechanical expectations assert now,
# phase-3 rows are the ai-* pass's target and are skipped here
# --------------------------------------------------------------------------

WORKED = Path(__file__).parent / "files/regleringshierarki/worked-examples.json"


@pytest.mark.parametrize("example", json.loads(
    WORKED.read_text("utf-8"))["examples"], ids=lambda e: e["name"])
def test_worked_example_mechanical_expectations(example):
    con = sqlite3.connect(":memory:")
    con.executescript(catalog.SCHEMA)
    for d in example["documents"]:
        con.execute("INSERT INTO documents (uri, source, kind, date, path) "
                    "VALUES (?,?,?,?,'x')",
                    (d["uri"], d["source"], d["kind"], d["date"]))
    for row in example.get("norm_chain", []):
        con.execute("INSERT INTO norm_chain VALUES (?,?,?,?,?,?,?)", row)
    for l in example.get("links", []):
        link(con, l["from_uri"], l["from_anchor"], l["predicate"], l["to_uri"])
    for g in example.get("genomforande", []):
        con.execute("INSERT INTO genomforande (sfs_uri, sfs_anchor, directive, "
                    "article, prop_uri, pinpoint, partial, sfs_pinpoint) "
                    "VALUES (?,?,?,?,'',?,0,'')",
                    (g["sfs_uri"], g["sfs_anchor"], g["directive"],
                     g["article"], g["pinpoint"]))
    con.commit()
    hierarki.derive_delegation_edges(con)
    assert _edges(con) == sorted(
        tuple(e) for e in example["expected_delegation_edges"])
    for fs, want in example.get("expected_fyller_ut", {}).items():
        got = hierarki.fyller_ut_index(con)[fs]
        assert got["forordning"] == tuple(want["forordning"])
        assert got["lag"] == tuple(want["lag"])
        assert [list(d) for d in got["direktiv"]] == want["direktiv"]
    # the frozen expected_rows are all phase-3 (LLM) targets; the fixture
    # carries no artifact texts, so the mechanical builder cannot run here
    # and its output is asserted in the artifact-backed tests above -- a
    # table assertion at this point would be vacuous (nothing built it)
    assert all(r.get("phase") == 3 for r in example.get("expected_rows", []))


def test_term_pattern_inflects_every_word():
    """The attributive adjective agrees with its noun, so a last-word-only
    pattern missed "nationella bedömningsstöd" for the term "nationellt
    bedömningsstöd" -- measured on the golden-ten bench, where it cost all
    five bedömningsstöd rungs."""
    from ferenda.lib.util import normalize_fold
    p = term_pattern("nationellt bedömningsstöd")
    assert p.search(normalize_fold("Nationella bedömningsstöd ska användas"))
    assert p.search(normalize_fold("ett nationellt bedömningsstöd"))
    q = term_pattern("allmän handling")
    assert q.search(normalize_fold("utlämnande av allmänna handlingar"))
    assert not term_pattern("betydande incident").search(
        normalize_fold("en incidentrapport"))


def test_a_specializing_span_mints_its_own_concept():
    """"betydande incident" contains the existing term "incident" but is a
    distinct concept; only a pure inflection variant ("incidenter") folds.
    The .search() dedupe swallowed the D-minted span into *incident* --
    found live 2026-08-28 on the first curated publish."""
    from ferenda.lib.util import normalize_fold
    assert not term_pattern("incident").fullmatch(
        normalize_fold("betydande incident"))
    assert term_pattern("incident").fullmatch(normalize_fold("incidenter"))
    assert term_pattern("betydande incident").fullmatch(
        normalize_fold("betydande incidenten"))


def test_the_normkedja_meta_row_marks_you_are_here(tmp_path):
    """Every document on a chain carries a "Normkedja" metadata row: the
    upward spine with the current document marked, and a downward count
    ("→ 1 föreskrift") -- Staffan 2026-08-28."""
    con = _ladder_con(tmp_path)
    site = _site(con)
    row = page.chain_meta(site, MCFFS)
    assert "here" in row and "→" in row
    assert row.index("32022L2555") < row.index("2025:1506") \
        < row.index("2025:1507")
    top = page.chain_meta(site, DIREKTIV)
    assert "2025:1506" in top      # the child is named, never only counted
    assert page.chain_meta(site, "https://lagen.nu/2003:364") is None \
        or "här" not in str(page.chain_meta(site, "https://lagen.nu/2003:364"))
    assert page.chain_meta(site, "https://lagen.nu/begrepp/X") is None


def test_internal_ladder_links_are_local_paths(tmp_path):
    """The canonical uri is the identifier; an internal href is a local path
    ("/1994:1809#K1P3", never "https://lagen.nu/..."). Reported by Staffan
    off the live Mönstring page 2026-08-28."""
    con = _ladder_con(tmp_path)
    hierarki.rebuild_regleringshierarki(con)
    html = wiki_render.render({"uri": CONCEPT, "title": "Betydande incident"},
                              _site(con))
    section = re.search(r'<section class="occurrences regleringshierarki">'
                        r'.*?</section>', html, re.S).group(0)
    assert 'href="https://lagen.nu/' not in section
    assert 'href="/' in section


# --------------------------------------------------------------------------
# the curated path: write_layers -> hierarki_layers -> the relate merge
# --------------------------------------------------------------------------

def test_curated_layers_round_trip_into_the_table(tmp_path, monkeypatch):
    """The LLM output path end to end: rows written as .ann layers, read
    back by the glob, merged by the rebuild -- landing with source 'llm',
    winning over the mechanical row on the same provision, and leaving the
    other mechanical rows alone. This path shipped its one live bug
    unfixtured (the mint swallow, 2026-08-28); never again."""
    con = _ladder_con(tmp_path)
    monkeypatch.setattr(annstore, "ROOT", tmp_path / "ann")
    rows = [
        # overrides the mechanical definierar row at MCFFS K3P1
        (MCFFS, "K3P1", "betydande incident", "detaljerar", "en mätbar gräns"),
        # a fresh provision no mechanical pass filed
        (CSF, "P18", "betydande incident", "alagger", None),
    ]
    written = aihierarki.write_layers(con, rows, all_docs=[MCFFS, CSF, CSL])
    assert written == 3          # incl. CSL's empty done-marker layer
    layers = hierarki.hierarki_layers()
    # the empty done-marker layer reads back too, carrying zero rows
    assert set(layers) == {MCFFS, CSF, CSL}
    assert len(layers[MCFFS]) == 1 and layers[CSL] == []
    stats = hierarki.rebuild_regleringshierarki(con, curated=layers)
    assert stats["curated_rows"] == 2
    got = {(r[0], r[1]): r for r in con.execute(
        "SELECT doc_uri, anchor, role, source, label FROM regleringshierarki "
        "WHERE concept = ?", (CONCEPT,))}
    assert got[(MCFFS, "K3P1")][2:] == ("detaljerar", "llm", "en mätbar gräns")
    assert got[(CSF, "P18")][2:4] == ("alagger", "llm")
    # the mechanical rows elsewhere survive untouched
    assert got[(CSL, "K2P5")][3] == "verbatim"


def test_task_validators_ground_every_output(monkeypatch):
    """The A and C validators: a span that is not a verbatim substring of
    its own clause is discarded and counted; a role outside the fixed set
    is discarded; 'ålägger'/'vet ej' normalize to the ascii forms."""
    replies = iter([
        '{"K1": ["säkerhetsåtgärder", "påhittat ämne"], "K2": "not-a-list"}',
        '{"R1": "ålägger", "R2": "vet ej", "R3": "kanske"}'])
    monkeypatch.setattr(aihierarki.llm, "author",
                        lambda prompt, validate, **kw: validate(next(replies)))
    stats = aihierarki.new_stats()
    spans = aihierarki.run_a(
        [("d1", "P1", "föreskrifter om säkerhetsåtgärder"),
         ("d1", "P2", "en annan bestämmelse")], batched=True, stats=stats)
    assert spans == {("d1", "P1"): ["säkerhetsåtgärder"], ("d1", "P2"): []}
    # two counted misses: the non-substring span AND the malformed
    # not-a-list answer for K2 -- a shape failure is never a silent "no
    # subjects"
    assert stats["a_discarded"] == 2
    roles = aihierarki.run_c(
        [("d1", "P1", "x", "text"), ("d1", "P2", "x", "text"),
         ("d1", "P3", "x", "text")], batched=True, stats=stats)
    assert roles == {("d1", "P1", "x"): "alagger", ("d1", "P2", "x"): "namner"}
    assert stats["c_discarded"] == 1


def test_b_and_d_validators_ground_every_output(monkeypatch):
    """B1: the index must be offered and the phrase a substring; B2: the id
    must be in the outline; D: spans substring-checked, a non-list reply
    raises so `llm.author` retries."""
    replies = iter([
        '{"T1": {"val": 1, "fras": "gamma"}, "T2": {"val": 3}, '
        '"T3": {"val": 9, "fras": "x"}}',
        '{"alfa": {"id": "P2", "fras": "alfa"}, "beta": {"id": "P9"}}',
        '{"amnen": ["mönstring", "påhittat"]}'])
    monkeypatch.setattr(aihierarki.llm, "author",
                        lambda prompt, validate, **kw: validate(next(replies)))
    stats = aihierarki.new_stats()
    aligned = aihierarki.run_b1(
        [("d", "P1", "gamma-frasen", "text med gamma i"),
         ("d", "P2", "delta", "text"), ("d", "P3", "x", "text")],
        ["alfa", "beta"], batched=True, stats=stats)
    assert aligned == {("d", "P1", "gamma-frasen"): "alfa",
                       ("d", "P2", "delta"): None}
    assert stats["b1_discarded"] == 1          # the out-of-range index
    probed = aihierarki.run_b2("d", "Doc", [("P1", "…"), ("P2", "…")],
                               ["alfa", "beta"], batched=True, stats=stats)
    assert probed == {"alfa": "P2"}
    assert stats["b2_discarded"] == 1          # the id outside the outline
    spans = aihierarki.run_d("kedjans texter om mönstring", stats)
    assert spans == ["mönstring"]
    assert stats["d_discarded"] == 1           # the non-substring span
    monkeypatch.setattr(aihierarki.llm, "author",
                        lambda prompt, validate, **kw: validate('{"amnen": 7}'))
    with pytest.raises(ValueError):
        aihierarki.run_d("text", aihierarki.new_stats())
