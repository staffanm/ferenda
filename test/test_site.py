"""Tests for the derived layer: the SQLite catalog (relate) and the static
HTML renderer (generate) -- REWRITE.md §6."""

import json

from accommodanda.lib import catalog, render


# a minimal SFS-shaped artifact: one paragraph whose stycke cites another law,
# and a DV-shaped artifact: a case citing that paragraph (the inbound edge).
LAW = {
    "uri": "https://lagen.nu/1975:635",
    "metadata": {"properties": {"dcterms:title": "Räntelag (1975:635)"}},
    "structure": [
        {"type": "paragraf", "id": "P6", "ordinal": "6", "children": [
            {"type": "stycke", "id": "P6S1", "beteckning": "6 §", "text": [
                "Ränta beräknas enligt ",
                {"predicate": "dcterms:references", "text": "5 §",
                 "uri": "https://lagen.nu/1975:635#P5"}, "."]},
        ]},
    ],
}
CASE = {
    "uri": "https://lagen.nu/dom/NJA_1994_s_1",
    "court": "HDO", "court_namn": "Högsta domstolen",
    "referat": ["NJA 1994 s. 1"], "malnummer": ["T 1-94"],
    "metadata": {"sammanfattning": "Om dröjsmålsränta."},
    "body": [
        {"type": "stycke", "text": [
            "Bolaget yrkade ränta enligt ",
            {"predicate": "dcterms:references", "text": "6 § räntelagen (1975:635)",
             "uri": "https://lagen.nu/1975:635#P6"}, "."]},
    ],
}


def build_catalog(tmp_path):
    db = str(tmp_path / "catalog.sqlite")
    law = tmp_path / "law.json"
    law.write_text(json.dumps(LAW))
    case = tmp_path / "case.json"
    case.write_text(json.dumps(CASE))
    catalog.rebuild(db, "sfs", [law])
    catalog.rebuild(db, "dv", [case])
    return catalog.connect(db)


# --- catalog --------------------------------------------------------------

def test_collect_links_attributes_to_nearest_id():
    out = []
    catalog.collect_links(LAW["structure"], None, out)
    assert out == [("P6S1", {"predicate": "dcterms:references", "text": "5 §",
                             "uri": "https://lagen.nu/1975:635#P5"})]


def test_rebuild_counts(tmp_path):
    con = build_catalog(tmp_path)
    assert catalog.counts(con) == {"sfs": 1, "dv": 1}


def test_inbound_edge_crosses_sources(tmp_path):
    con = build_catalog(tmp_path)
    rows = catalog.inbound(con, "https://lagen.nu/1975:635#P6")
    # (from_uri, from_anchor, label, title, source) -- the case cites from its
    # body (no node id), so the pinpoint anchor is None
    assert rows == [("https://lagen.nu/dom/NJA_1994_s_1", None,
                     "NJA 1994 s. 1", "NJA 1994 s. 1", "dv")]


def test_human_fragment():
    assert render.human_fragment("K2P16S5") == "2 kap. 16 § 5 st"
    assert render.human_fragment("K8P7S1N1") == "8 kap. 7 § 1 st 1 p"
    assert render.human_fragment("P6") == "6 §"
    assert render.human_fragment("sid39") == "s. 39"
    assert render.human_fragment("") == ""


def test_describe_citer_pinpoints_statutes():
    # statute: full name + pinpoint; case: just the referat (no pinpoint)
    assert render.describe_citer("u", "K2P16S5", "SFS 2010:800",
                                 "Skollag (2010:800)", "sfs") \
        == "Skollag (2010:800) 2 kap. 16 § 5 st"
    assert render.describe_citer("u", None, "NJA 1994 s. 1",
                                 "NJA 1994 s. 1", "dv") == "NJA 1994 s. 1"


def test_inbound_excludes_self_citation(tmp_path):
    # the law's §6 stycke cites its own §5 -> that internal cross-ref must NOT
    # appear as inbound on §5 (it's the law's own outbound link, not external)
    con = build_catalog(tmp_path)
    rows = catalog.inbound(con, "https://lagen.nu/1975:635#P5")
    assert rows == []
    assert catalog.inbound_count(con, "https://lagen.nu/1975:635#P5") == 0


def test_rebuild_is_idempotent(tmp_path):
    con = build_catalog(tmp_path)
    # re-relating the same source must not duplicate rows or edges
    catalog.rebuild(str(tmp_path / "catalog.sqlite"), "dv",
                    [tmp_path / "case.json"])
    con2 = catalog.connect(str(tmp_path / "catalog.sqlite"))
    assert catalog.counts(con2) == {"sfs": 1, "dv": 1}
    assert con2.execute("SELECT COUNT(*) FROM links").fetchone()[0] == 2


# --- renderer -------------------------------------------------------------

def test_href_and_relpath():
    assert render.doc_relpath("https://lagen.nu/1975:635") == "sfs/1975_635.html"
    assert render.href("https://lagen.nu/1975:635#P6") == "/sfs/1975_635.html#P6"
    assert render.doc_relpath("https://lagen.nu/dom/NJA_1994_s_1") \
        == "dom/dom_NJA_1994_s_1.html"
    # external (non-lagen.nu) uris are left absolute
    assert render.href("http://example.org/x") == "http://example.org/x"


def test_render_runs_outbound_link(tmp_path):
    site = render.Site.from_catalog(build_catalog(tmp_path))
    html = render.render_runs(
        ["se ", {"uri": "https://lagen.nu/1975:635#P5", "text": "5 §"}, "."], site)
    assert html == 'se <a href="/sfs/1975_635.html#P5">5 §</a>.'


def test_celex_renders_as_external_eurlex_link(tmp_path):
    site = render.Site.from_catalog(build_catalog(tmp_path))
    html = render.render_runs(
        ["enligt ", {"uri": "https://lagen.nu/ext/celex/31995L0046",
                     "text": "direktiv 95/46/EG"}], site)
    assert 'class="ext"' in html
    assert ("href=\"https://eur-lex.europa.eu/legal-content/SV/TXT/"
            "?uri=CELEX:31995L0046\"") in html
    assert "noref" not in html  # external, not a dead internal link


def test_render_runs_gates_absent_target(tmp_path):
    # a citation to a document not in the catalog renders as text, not a 404 link
    site = render.Site.from_catalog(build_catalog(tmp_path))
    html = render.render_runs(
        ["se ", {"uri": "https://lagen.nu/prop/1975:1#P5", "text": "prop. 1975:1"}], site)
    assert "<a " not in html
    assert '<span class="noref"' in html and "prop. 1975:1" in html


def test_law_page_has_inbound_annotation(tmp_path):
    site = render.Site.from_catalog(build_catalog(tmp_path))
    html = render.render_sfs(LAW, site)
    # the citing case appears in the §6 margin, linking back to the case page
    assert "Hänvisat till av" in html
    assert 'href="/dom/dom_NJA_1994_s_1.html"' in html
    assert "NJA 1994 s. 1" in html


def test_case_page_links_into_law(tmp_path):
    site = render.Site.from_catalog(build_catalog(tmp_path))
    html = render.render_dv(CASE, site)
    assert 'href="/sfs/1975_635.html#P6"' in html
    assert "Om dröjsmålsränta." in html  # sammanfattning rendered


PUNKTLAW = {
    "uri": "https://lagen.nu/2010:800",
    "metadata": {"properties": {"dcterms:title": "Skollag (2010:800)"}},
    "structure": [{"type": "paragraf", "id": "P1", "ordinal": "1", "children": [
        {"type": "stycke", "id": "P1S1", "beteckning": "1 §",
         "text": ["Regeringen får meddela"], "children": [
            {"type": "punkt", "id": "P1S1N1", "ordinal": "1",
             "text": ["föreskrifter om verkställighet, och"]},
            {"type": "punkt", "id": "P1S1N2", "ordinal": "2",
             "text": ["andra föreskrifter."]}]}]}],
}


def test_renders_stycke_punkt_children(tmp_path):
    db = str(tmp_path / "c.sqlite")
    p = tmp_path / "l.json"
    p.write_text(json.dumps(PUNKTLAW))
    catalog.rebuild(db, "sfs", [p])
    site = render.Site.from_catalog(catalog.connect(db))
    html = render.render_sfs(PUNKTLAW, site)
    assert "föreskrifter om verkställighet, och" in html  # list item now shown
    assert "andra föreskrifter." in html
    assert 'class="punkter"' in html


def test_snippet_tooltip_on_outbound_link(tmp_path):
    con = build_catalog(tmp_path)
    site = render.Site.from_catalog(con)
    # the case links to 1975:635#P6; hovering shows that paragraph's text
    # (built from its stycke + any list items)
    html = render.render_dv(CASE, site)
    assert 'title="Ränta beräknas enligt 5 §."' in html


def test_inbound_grouped_by_source(tmp_path):
    con = build_catalog(tmp_path)
    site = render.Site.from_catalog(con)
    html = render.render_sfs(LAW, site)   # §6 is cited by the case
    assert 'class="ingroup dv"' in html
    assert "Rättsfall" in html            # the source-group heading


def test_toc_collects_and_generates_anchors():
    toc = render.Toc()
    assert toc.add("K1", "1 kap.", 1) == "K1"      # existing id reused
    assert toc.add(None, "Bakgrund", 1) == "sec1"  # id-less -> generated, stable
    assert toc.add(None, "  ", 1) == "sec2"        # blank text -> no entry, still counts
    assert toc.entries == [("K1", "1 kap.", 1), ("sec1", "Bakgrund", 1)]


def test_render_toc_skips_short_documents():
    toc = render.Toc()
    toc.add("a", "One", 1)
    toc.add("b", "Two", 1)
    assert render.render_toc(toc) == ""            # < MIN_TOC headings


def test_render_toc_builds_nav_with_levels():
    toc = render.Toc()
    toc.add("K1", "1 kap.", 1)
    toc.add(None, "Rubrik", 2)
    toc.add("K2", "2 kap.", 1)
    html = render.render_toc(toc)
    assert 'class="toc"' in html and "Innehåll" in html
    assert '<a href="#K1" class="lvl1">1 kap.</a>' in html
    assert '<a href="#sec1" class="lvl2">Rubrik</a>' in html


def test_document_page_includes_toc_and_anchors(tmp_path):
    con = build_catalog(tmp_path)  # LAW has only §6 -> no headings -> no toc
    site = render.Site.from_catalog(con)
    headed = {"uri": "https://lagen.nu/2020:1",
              "metadata": {"properties": {"dcterms:title": "Testlag (2020:1)"}},
              "structure": [
                  {"type": "kapitel", "id": "K1", "ordinal": "1", "children": [
                      {"type": "rubrik", "id": "K1R1", "level": 1,
                       "text": ["1 kap. Inledande bestämmelser"]},
                      {"type": "rubrik", "id": "K1R2", "level": 2,
                       "text": ["Tillämpningsområde"]}]},
                  {"type": "kapitel", "id": "K2", "ordinal": "2", "children": [
                      {"type": "rubrik", "id": "K2R1", "level": 1,
                       "text": ["2 kap. Senare bestämmelser"]}]}]}
    html = render.render_sfs(headed, site)
    assert '<nav class="toc">' in html
    # chapters enter the TOC via their title rubrik, not a redundant bare entry
    assert 'href="#K1R1"' in html and 'href="#K1R2"' in html
    assert "1 kap." in html and "Tillämpningsområde" in html
    assert '<h2 id="K1R1"' in html  # body anchor matches the toc link
    con = build_catalog(tmp_path)
    sfs = render.render_browse(con, "sfs")
    assert "1975" in sfs  # law grouped under its year
    assert 'href="/sfs/1975_635.html"' in sfs
    dv = render.render_browse(con, "dv")
    assert 'href="/dom/dom_NJA_1994_s_1.html"' in dv  # every case is listed


def test_group_key_buckets_real_uris():
    assert render._group_key("sfs", "https://lagen.nu/1975:635") == "1975"
    assert render._group_key("dv", "https://lagen.nu/dom/nja/2011s357") == "NJA"
    assert render._group_key("dv", "https://lagen.nu/dom/ad/1993:100") == "AD"


def test_document_level_inbound_for_bare_citation(tmp_path):
    # a case citing the whole law (no #fragment) shows in the law's doc-level
    # panel -- which paragraph annotations never surface
    db = str(tmp_path / "catalog.sqlite")
    law = tmp_path / "law.json"
    law.write_text(json.dumps(LAW))
    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps({
        "uri": "https://lagen.nu/dom/NJA_2000_s_1", "court": "HDO",
        "referat": ["NJA 2000 s. 1"], "metadata": {},
        "body": [{"type": "stycke", "text": [
            "enligt ", {"predicate": "dcterms:references", "text": "räntelagen",
                        "uri": "https://lagen.nu/1975:635"}, "."]}]}))
    catalog.rebuild(db, "sfs", [law])
    catalog.rebuild(db, "dv", [bare])
    site = render.Site.from_catalog(catalog.connect(db))
    html = render.render_sfs(LAW, site)
    assert '<section class="inbound-doc">' in html
    assert "NJA 2000 s. 1" in html
