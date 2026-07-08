"""Tests for the derived layer: the SQLite catalog (relate) and the static
HTML renderer (generate) -- REWRITE.md §6."""

import hashlib
import json
import re
from pathlib import Path

import pytest

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
    "structure": [
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


def test_relate_is_incremental(tmp_path):
    # a second relate over unchanged artifacts re-extracts nothing; a content
    # change re-extracts just that one; a vanished artifact is pruned.
    db = str(tmp_path / "catalog.sqlite")
    law = tmp_path / "law.json"
    law.write_text(json.dumps(LAW))
    case = tmp_path / "case.json"
    case.write_text(json.dumps(CASE))

    docs, _, changed = catalog.rebuild(db, "sfs", [law, case])
    assert (docs, changed) == (2, 2)
    docs, _, changed = catalog.rebuild(db, "sfs", [law, case])
    assert (docs, changed) == (2, 0)              # unchanged -> skipped

    law.write_text(json.dumps({**LAW, "metadata": {"properties":
                   {"dcterms:title": "Räntelag (1975:635), ändrad"}}}))
    _, _, changed = catalog.rebuild(db, "sfs", [law, case])
    assert changed == 1                           # only the edited one

    docs, _, changed = catalog.rebuild(db, "sfs", [case])   # law artifact gone
    con = catalog.connect(db)
    assert (docs, changed) == (1, 0)
    assert catalog.local(LAW["uri"]) not in {
        catalog.local(r[0]) for r in con.execute("SELECT uri FROM documents")}
    # the pruned document's outbound links went with it
    assert con.execute("SELECT COUNT(*) FROM links WHERE from_uri = ?",
                       (LAW["uri"],)).fetchone()[0] == 0


def test_relate_survives_artifact_path_move(tmp_path):
    # a document's identity is its uri, not its on-disk path: when an artifact
    # moves to a new path (a storage-layout change) but keeps its uri, relate must
    # re-index it under the new path and NOT prune it as a "vanished" old path --
    # else the just-written row is deleted and the document silently disappears.
    db = str(tmp_path / "catalog.sqlite")
    old = tmp_path / "flat.json"
    old.write_text(json.dumps(LAW))
    docs, _, _ = catalog.rebuild(db, "sfs", [old])
    assert docs == 1

    new = tmp_path / "nested" / "law.json"       # same uri, different path
    new.parent.mkdir()
    new.write_text(json.dumps(LAW))
    old.unlink()
    docs, _, changed = catalog.rebuild(db, "sfs", [new])
    assert (docs, changed) == (1, 1)             # survived the move, re-indexed once
    con = catalog.connect(db)
    # the stored path is data_root-relative (the catalog's own directory), so it
    # survives the move as the new relative path -- not the absolute one
    assert con.execute("SELECT path FROM documents WHERE uri = ?",
                       (LAW["uri"],)).fetchone()[0] == str(new.relative_to(tmp_path))


def test_catalog_paths_are_data_root_relative_and_portable(tmp_path):
    # the deploy guarantee: a catalog stores artifact paths relative to its own
    # directory (data_root), never absolute, so it can be rsync'd to a host with a
    # different data_root and still resolve every artifact. Build under one root,
    # then "relocate" the whole tree to another dir and confirm the render layer
    # (which resolves stored paths against catalog.data_root) opens the artifacts.
    src = tmp_path / "dev"
    src.mkdir()
    (src / "artifact").mkdir()
    law = src / "artifact" / "law.json"
    law.write_text(json.dumps(LAW))
    db = str(src / "catalog.sqlite")
    catalog.rebuild(db, "sfs", [law])

    con = catalog.connect(db)
    stored = con.execute("SELECT path FROM documents WHERE uri = ?",
                         (LAW["uri"],)).fetchone()[0]
    assert stored == "artifact/law.json"                 # relative, host-independent
    assert catalog.data_root(con) == src                 # derived from the db file

    # relocate the corpus to a different absolute path (simulating the deploy host)
    dst = tmp_path / "prod"
    (src).rename(dst)
    con = catalog.connect(str(dst / "catalog.sqlite"))
    # the render layer resolves the relative path against the new root and renders
    out = dst / "generated"
    total, rendered = render.generate_site(str(dst / "catalog.sqlite"), str(out))
    assert rendered >= 1
    assert (out / render.doc_relpath(LAW["uri"])).exists()


def test_relate_migrates_legacy_absolute_paths(tmp_path):
    # a catalog built before relative paths stored absolute ones; the next relate
    # rewrites them in place to data_root-relative (so an old catalog becomes
    # portable without a full --force rebuild)
    db = str(tmp_path / "catalog.sqlite")
    law = tmp_path / "law.json"
    law.write_text(json.dumps(LAW))
    catalog.rebuild(db, "sfs", [law])
    con = catalog.connect(db)
    con.execute("UPDATE documents SET path = ? WHERE uri = ?",
                (str(law), LAW["uri"]))                  # force a legacy absolute row
    con.commit()
    con.close()

    catalog.rebuild(db, "sfs", [law])                    # a plain incremental relate
    con = catalog.connect(db)
    assert con.execute("SELECT path FROM documents WHERE uri = ?",
                       (LAW["uri"],)).fetchone()[0] == "law.json"


def test_source_content_signature_tracks_catalog(tmp_path):
    # the index watermark: stable while the catalog is unchanged, moves when a
    # document's content changes (so a no-op index can be skipped wholesale)
    db = str(tmp_path / "catalog.sqlite")
    law = tmp_path / "law.json"
    law.write_text(json.dumps(LAW))
    catalog.rebuild(db, "sfs", [law])
    con = catalog.connect(db)
    sig = catalog.source_content_signature(con, "sfs")
    assert catalog.source_content_signature(con, "sfs") == sig      # stable

    law.write_text(json.dumps({**LAW, "metadata": {"properties":
                   {"dcterms:title": "Räntelag (1975:635), ändrad"}}}))
    catalog.rebuild(db, "sfs", [law])
    con = catalog.connect(db)
    assert catalog.source_content_signature(con, "sfs") != sig      # content moved


def test_catalog_signature_tracks_whole_catalog(tmp_path):
    # the generate gate: stable while the whole catalog is unchanged, moves when
    # any source gains/changes a document
    db = str(tmp_path / "catalog.sqlite")
    law = tmp_path / "law.json"
    law.write_text(json.dumps(LAW))
    catalog.rebuild(db, "sfs", [law])
    con = catalog.connect(db)
    sig = catalog.catalog_signature(con)
    assert catalog.catalog_signature(con) == sig
    case = tmp_path / "case.json"
    case.write_text(json.dumps(CASE))
    catalog.rebuild(db, "dv", [case])                # another source's doc appears
    con = catalog.connect(db)
    assert catalog.catalog_signature(con) != sig


def test_relate_skips_read_on_stat_match(tmp_path, monkeypatch):
    # §2.2: an artifact whose (size, mtime) are unchanged since the last relate is
    # not read at all -- the fast path decides "unchanged" from the stat alone.
    db = str(tmp_path / "catalog.sqlite")
    law = tmp_path / "law.json"
    law.write_text(json.dumps(LAW))
    catalog.rebuild(db, "sfs", [law])

    reads = []
    orig = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes",
                        lambda self: reads.append(self) or orig(self))
    _, _, changed = catalog.rebuild(db, "sfs", [law])
    assert changed == 0 and reads == []              # decided by stat, never read


def test_relate_identical_rewrite_reads_once_then_stats(tmp_path):
    # §2.2: rewriting an artifact with byte-identical content bumps its mtime, so
    # the stat differs and it is read once; the content hash still matches, so it
    # is not re-extracted, and the refreshed stat lets the *next* relate skip it.
    db = str(tmp_path / "catalog.sqlite")
    law = tmp_path / "law.json"
    law.write_text(json.dumps(LAW))
    catalog.rebuild(db, "sfs", [law])

    st_before = law.stat().st_mtime_ns
    while law.stat().st_mtime_ns == st_before:       # force a distinct mtime
        law.write_text(json.dumps(LAW))
    _, _, changed = catalog.rebuild(db, "sfs", [law])
    assert changed == 0                              # bytes unchanged -> not re-extracted

    reads = []
    orig = Path.read_bytes
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "read_bytes",
                   lambda self: reads.append(self) or orig(self))
        _, _, changed = catalog.rebuild(db, "sfs", [law])
    assert changed == 0 and reads == []              # stat refreshed -> fast path again


def test_force_relate_reextracts_all(tmp_path):
    db = str(tmp_path / "catalog.sqlite")
    law = tmp_path / "law.json"
    law.write_text(json.dumps(LAW))
    catalog.rebuild(db, "sfs", [law])
    _, _, changed = catalog.rebuild(db, "sfs", [law], force=True)
    assert changed == 1


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
    # a statute is a top-level page at lagen.nu's bare /<sfsid> address (colon kept)
    assert render.doc_relpath("https://lagen.nu/1975:635") == "1975:635.html"
    assert render.href("https://lagen.nu/1975:635#P6") == "/1975:635#P6"
    assert render.doc_relpath("https://lagen.nu/dom/NJA_1994_s_1") \
        == "dom/dom_NJA_1994_s_1.html"
    # external (non-lagen.nu) uris are left absolute
    assert render.href("http://example.org/x") == "http://example.org/x"


def test_render_runs_outbound_link(tmp_path):
    site = render.Site.from_catalog(build_catalog(tmp_path))
    html = render.render_runs(
        ["se ", {"uri": "https://lagen.nu/1975:635#P5", "text": "5 §"}, "."], site)
    assert html == 'se <a href="/1975:635#P5">5 §</a>.'


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


def _island(html):
    """Parse the rail's JSON island ({anchor id -> panel HTML}) out of a page."""
    m = re.search(r'id="lagen-context">(.*?)</script>', html, re.S)
    return json.loads(m.group(1)) if m else {}


def test_law_page_has_inbound_annotation(tmp_path):
    site = render.Site.from_catalog(build_catalog(tmp_path))
    html = render.render_sfs(LAW, site)
    # the citing case is in §6's context-rail panel, linking back to the case
    panel = _island(html)["P6"]
    assert "Hänvisat till av" in panel
    assert 'href="/dom/NJA_1994_s_1"' in panel
    assert "NJA 1994 s. 1" in panel


def test_render_document_injects_edit_meta(tmp_path):
    # render_document (the dispatcher) grafts the inline-editor <meta> + editor.js
    # onto every page: a statute is commentary-editable (kind=kommentar, ref=the
    # SFS number), a case is not (dv hosts no editable content).
    site = render.Site.from_catalog(build_catalog(tmp_path))
    law = render.render_document(LAW, "sfs", site)
    assert '<meta name="lagen-doc" data-kind="kommentar" data-ref="1975:635"' in law
    # a statute is patchable, so its identity rides on the meta for the
    # "patch source" button beside the commentary one
    assert 'data-source="sfs" data-basefile="1975:635"' in law
    assert law.count("</head>") == 1 and '/editor.js' in law
    case = render.render_document(CASE, "dv", site)
    assert 'name="lagen-doc"' not in case      # court decisions are read-only
    assert '/editor.js' in case                # but the script still loads (inert)


def test_expired_statute_is_marked(tmp_path):
    site = render.Site.from_catalog(build_catalog(tmp_path))
    repealed = {
        "uri": "https://lagen.nu/1975:1385",
        "metadata": {"properties": {
            "dcterms:title": "Aktiebolagslag (1975:1385)",
            "rpubl:upphavandedatum": "2006-01-01",
            "rinfoex:upphavdAv": "https://lagen.nu/2005:552"}},
        "structure": [],
    }
    html = render.render_sfs(repealed, site)
    assert 'class="gr-root expired"' in html       # drives the subdue + watermark
    assert "Upphävd författning" in html
    assert 'href="/2005:552"' in html              # link to the repealing act
    assert "<dt>Upphävd</dt><dd>2006-01-01</dd>" in html
    # a *future* repeal date is still in force -> not marked
    upcoming = json.loads(json.dumps(repealed))
    upcoming["metadata"]["properties"]["rpubl:upphavandedatum"] = "2099-01-01"
    out = render.render_sfs(upcoming, site)
    assert 'class="gr-root expired"' not in out and "expired-banner" not in out


def test_case_page_links_into_law(tmp_path):
    site = render.Site.from_catalog(build_catalog(tmp_path))
    html = render.render_dv(CASE, site)
    assert 'href="/1975:635#P6"' in html
    assert "Om dröjsmålsränta." in html  # sammanfattning rendered


# --- authoritative source url ---------------------------------------------

def test_eurlex_source_url_derives_eli():
    from accommodanda.lib import layout
    # sector-3 regulation/directive/decision -> ELI (number's leading zeros gone)
    assert (layout.source_url("eurlex", "32023R2854")
            == "https://eur-lex.europa.eu/eli/reg/2023/2854/oj")
    assert (layout.source_url("eurlex", "32016L0679")
            == "https://eur-lex.europa.eu/eli/dir/2016/679/oj")
    # a judgment has no ELI -> the stable CELEX legal-content url
    assert (layout.source_url("eurlex", "62019CJ0311")
            == "https://eur-lex.europa.eu/legal-content/SV/TXT/?uri=CELEX:62019CJ0311")
    # sources with no rule derive nothing (their url is downloader-recorded)
    assert layout.source_url("forarbete", "prop/2024/25:1") is None


def test_sfs_source_url_derives_from_basefile():
    from accommodanda.lib import layout
    # the colon in the basefile is percent-encoded into the bet= query param
    assert (layout.source_url("sfs", "2025:1506")
            == "https://beta.rkrattsbaser.gov.se/sfs/item"
               "?bet=2025%3A1506&tab=forfattningstext")


def test_dv_source_url_uses_publication_group():
    from accommodanda.lib import layout
    # keyed by the record's gruppKorrelationsnummer, not derivable from basefile
    assert (layout.dv_source_url("50ca363e-bff6-4048-b68f-9409e72381b2")
            == "https://rattspraxis.etjanst.domstol.se/sok/publicering/"
               "50ca363e-bff6-4048-b68f-9409e72381b2")
    # so the by-basefile rule yields nothing for dv on its own
    assert layout.source_url("dv", "HFD/2023_ref_59") is None


def test_write_artifact_stamps_source_url(tmp_path, monkeypatch):
    from accommodanda.lib import layout
    monkeypatch.setattr(layout, "ARTIFACT", tmp_path)
    from accommodanda import build

    # derived: eurlex gets its ELI even with nothing recorded
    out = layout.artifact("eurlex", "32023R2854")
    out.parent.mkdir(parents=True, exist_ok=True)
    build.write_artifact("eurlex", "32023R2854", {"uri": "x", "celex": "32023R2854"})
    assert (json.loads(out.read_text())["source_url"]
            == "https://eur-lex.europa.eu/eli/reg/2023/2854/oj")

    # recorded: the downloader's landing url travels into the artifact
    out = layout.artifact("forarbete", "prop/2024/25:1")
    out.parent.mkdir(parents=True, exist_ok=True)
    build.write_artifact("forarbete", "prop/2024/25:1", {"uri": "y"},
                         source_url="https://www.regeringen.se/x")
    assert json.loads(out.read_text())["source_url"] == "https://www.regeringen.se/x"


def test_page_renders_source_link(tmp_path):
    site = render.Site.from_catalog(build_catalog(tmp_path))
    art = dict(LAW, source_url="https://rkrattsbaser.gov.se/sfst?bet=1975:635")
    html = render.render_sfs(art, site)
    assert ('<a class="ext" href="https://rkrattsbaser.gov.se/sfst?bet=1975:635" '
            'rel="external">Källa</a>') in html
    # absent source_url -> no Källa link
    assert "Källa" not in render.render_sfs(LAW, site)


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
    panel = _island(html)["P6"]
    assert 'class="ingroup dv"' in panel
    assert "Rättsfall" in panel           # the source-group heading


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

def test_generate_browse_writes_faceted_pages(tmp_path):
    # the browse pages are generated by consuming the REST API in-process, so this
    # exercises render_aggregates end to end (frontpage + faceted browse)
    con = build_catalog(tmp_path)        # Räntelag (1975:635), one HD case
    out = tmp_path / "site"
    render.render_aggregates(con, out, str(tmp_path / "catalog.sqlite"))
    # the law files under its subject initial 'R' (Räntelag); the source root and
    # the bucket page both list it (root == default bucket, no redirect)
    bucket = (out / "sfs" / "r" / "index.html").read_text()
    assert 'href="/1975:635"' in bucket          # the bare /<sfsid> page address
    assert "som börjar på R" in bucket
    assert 'href="/1975:635"' in (out / "sfs" / "index.html").read_text()
    # every case is listed under its court; the source root resolves directly
    assert 'href="/dom/NJA_1994_s_1"' in (out / "dom" / "index.html").read_text()


def test_generate_site_incremental_reuses_content_hash(tmp_path):
    # §2.1/§2.3: generate skips a page whose stored content_hash and batched
    # dependency digest are both unchanged; a content edit (new content_hash)
    # re-renders exactly that page. The fresh/record protocol mirrors build.py's,
    # reusing the catalog content_hash instead of re-reading the artifact.
    db = str(tmp_path / "catalog.sqlite")
    law = tmp_path / "law.json"
    law.write_text(json.dumps(LAW))
    case = tmp_path / "case.json"
    case.write_text(json.dumps(CASE))
    catalog.rebuild(db, "sfs", [law])
    catalog.rebuild(db, "dv", [case])
    out = tmp_path / "site"

    manifest = {}
    rendered_uris = []

    def signature(chash, dep):
        return hashlib.sha256(((chash or "") + dep).encode()).hexdigest()

    def fresh(uri, out_path, art_path, dep, chash):
        return (uri in manifest and out_path.exists()
                and manifest[uri] == signature(chash, dep))

    def record(uri, art_path, dep, chash):
        manifest[uri] = signature(chash, dep)
        rendered_uris.append(uri)

    total, rendered = render.generate_site(db, out, fresh=fresh, record=record)
    assert total >= 2 and rendered == total          # first run renders every page
    assert LAW["uri"] in rendered_uris and CASE["uri"] in rendered_uris

    rendered_uris.clear()
    _, rendered = render.generate_site(db, out, fresh=fresh, record=record)
    assert rendered == 0 and rendered_uris == []     # nothing changed -> all skipped

    # edit the law's title (new bytes -> new content_hash), re-relate, regenerate:
    # only the law's own page re-renders (its dependency set is unchanged)
    law.write_text(json.dumps({**LAW, "metadata": {"properties":
                   {"dcterms:title": "Räntelag (1975:635), ändrad"}}}))
    catalog.rebuild(db, "sfs", [law])
    rendered_uris.clear()
    _, rendered = render.generate_site(db, out, fresh=fresh, record=record)
    assert rendered_uris == [LAW["uri"]]             # only the edited page

    # a new case citing the law changes the law's inbound set -> its dependency
    # digest moves, so the law's page re-renders though its own bytes are unchanged
    newcase = tmp_path / "newcase.json"
    newcase.write_text(json.dumps({
        "uri": "https://lagen.nu/dom/NJA_2001_s_1", "court": "HDO",
        "referat": ["NJA 2001 s. 1"], "metadata": {},
        "structure": [{"type": "stycke", "text": [
            "se ", {"predicate": "dcterms:references", "text": "6 §",
                    "uri": "https://lagen.nu/1975:635#P6"}, "."]}]}))
    catalog.rebuild(db, "dv", [case, newcase])
    rendered_uris.clear()
    _, rendered = render.generate_site(db, out, fresh=fresh, record=record)
    assert LAW["uri"] in rendered_uris               # cited page re-rendered
    assert "https://lagen.nu/dom/NJA_2001_s_1" in rendered_uris   # the new page


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
        "structure": [{"type": "stycke", "text": [
            "enligt ", {"predicate": "dcterms:references", "text": "räntelagen",
                        "uri": "https://lagen.nu/1975:635"}, "."]}]}))
    catalog.rebuild(db, "sfs", [law])
    catalog.rebuild(db, "dv", [bare])
    site = render.Site.from_catalog(catalog.connect(db))
    html = render.render_sfs(LAW, site)
    assert '<section class="inbound-doc">' in html
    assert "NJA 2000 s. 1" in html


# --- förarbete genomför-direktiv edges (REWRITE.md §7d) -------------------

# a proposition whose författningskommentar transposes a directive article,
# and the EU directive it points at (article 21 is a citation target).
PROP = {
    "uri": "https://lagen.nu/prop/2023/24:1",
    "type": "prop", "identifier": "Prop. 2023/24:1", "title": "Cybersäkerhetslag",
    "structure": [
        {"type": "avsnitt", "id": "sec1", "level": 1, "page": 100,
         "text": ["Författningskommentar"], "children": [
            {"type": "stycke", "page": 100,
             "text": ["Paragrafen genomför artikel 21."]}]},
    ],
    "implements": [{
        "predicate": "rpubl:genomforDirektiv",
        "directive": "https://lagen.nu/ext/celex/32022L2555",
        "articles": ["21"], "pinpoints": ["21.1", "21.2"],
        "uris": ["https://lagen.nu/ext/celex/32022L2555#21"],
        "partial": False, "law": "Cybersäkerhetslag", "chapter": "2",
        "paragraf": "1", "page": 100,
        "sentence": "Paragrafen genomför artikel 21.1-21.2 i NIS 2-direktivet"}],
}
DIRECTIVE = {
    "uri": "https://lagen.nu/ext/celex/32022L2555",
    "celex": "32022L2555", "doctype": "directive", "title": "NIS 2-direktivet",
    "structure": [{"type": "article", "id": "21", "num": "21",
                   "text": ["Riskhanteringsåtgärder"], "children": []}],
}


def build_eu_catalog(tmp_path):
    db = str(tmp_path / "catalog.sqlite")
    prop = tmp_path / "prop.json"
    prop.write_text(json.dumps(PROP))
    direc = tmp_path / "dir.json"
    direc.write_text(json.dumps(DIRECTIVE))
    catalog.rebuild(db, "forarbete", [prop])
    catalog.rebuild(db, "eurlex", [direc])
    return catalog.connect(db)


def test_implements_links_emits_genomfor_edges():
    # one edge per transposed article, anchored to the förarbete page (#sid{N})
    assert catalog.implements_links(PROP) == [
        ("sid100", {"uri": "https://lagen.nu/ext/celex/32022L2555#21",
                    "predicate": "rpubl:genomforDirektiv",
                    "text": "Paragrafen genomför artikel 21.1-21.2 i NIS 2-direktivet"})]


def test_genomfor_edge_is_inbound_on_directive_article(tmp_path):
    # the killer feature for EU law: the directive article shows which Swedish
    # förarbete implements it, pinpointed to the page of the statement
    con = build_eu_catalog(tmp_path)
    assert catalog.inbound(con, "https://lagen.nu/ext/celex/32022L2555#21") == [
        ("https://lagen.nu/prop/2023/24:1", "sid100",
         "Prop. 2023/24:1", "Cybersäkerhetslag", "forarbete")]


def test_prop_page_renders_genomforande_panel(tmp_path):
    site = render.Site.from_catalog(build_eu_catalog(tmp_path))
    html = render.render_forarbete(PROP, site)
    assert "Genomför EU-direktiv" in html
    assert "2 kap. 1 § genomför artikel 21.1, 21.2" in html
    # links to the directive's article on our EU page (we host it)
    assert 'href="/celex/32022L2555#21"' in html


def test_directive_article_shows_implementing_forarbete(tmp_path):
    site = render.Site.from_catalog(build_eu_catalog(tmp_path))
    html = render.render_eurlex(DIRECTIVE, site)
    # article 21's rail panel shows the implementing förarbete
    panel = _island(html)["21"]
    assert "Hänvisat till av" in panel
    assert 'href="/prop/2023/24:1#sid100"' in panel
    assert "Prop. 2023/24:1 s. 100" in panel


def test_genomforande_panel_absent_without_implements(tmp_path):
    site = render.Site.from_catalog(build_eu_catalog(tmp_path))
    html = render.render_forarbete({"uri": "https://lagen.nu/prop/x", "type": "prop",
                                    "identifier": "Prop. X", "body": []}, site)
    assert "Genomför EU-direktiv" not in html


# --- genomför-direktiv pinned to SFS paragrafs (REWRITE.md §7d) -----------

DIR = "https://lagen.nu/ext/celex/32022L2555"


def _impl(law, chapter, paragraf, pinpoint, partial=False):
    return {"predicate": "rpubl:genomforDirektiv", "directive": DIR,
            "articles": ["21"], "pinpoints": [pinpoint], "uris": [DIR + "#21"],
            "partial": partial, "law": law, "chapter": chapter,
            "paragraf": paragraf, "page": 50, "sentence": "genomför artikel 21"}


# one proposition exercising all three resolution paths
PROP_PIN = {
    "uri": "https://lagen.nu/prop/2021/22:9", "type": "prop",
    "identifier": "Prop. 2021/22:9", "date": "2021-10-01",
    "body": [{"type": "rubrik", "level": 1, "page": 50,
              "text": ["Författningskommentar"]}],
    "implements": [
        _impl("5.1 Förslaget till lag om ändring i testlagen (1999:100)",
              None, "3", "21.1"),                    # Case 1: amending (SFS in rubrik)
        _impl("5.2 Förslaget till cybersäkerhetslag", "2", "1", "21.2", True),  # Case 2 unique
        _impl("5.3 Förslaget till konsumentköplag", None, "4", "21.3"),  # Case 2 tie-break
    ],
}


def _sfs_art(uri, title, ikraft):
    return {"uri": uri, "metadata": {"properties": {
        "dcterms:title": title, "rpubl:ikrafttradandedatum": ikraft}},
        "structure": [
            {"type": "paragraf", "id": "P3", "ordinal": "3", "children": []},
            {"type": "paragraf", "id": "P4", "ordinal": "4", "children": []},
            {"type": "kapitel", "id": "K2", "ordinal": "2", "children": [
                {"type": "paragraf", "id": "K2P1", "ordinal": "1", "children": []}]}]}


SFS_LAWS = [
    _sfs_art("https://lagen.nu/1999:100", "Testlag (1999:100)", "2000-01-01"),
    _sfs_art("https://lagen.nu/2021:500", "Cybersäkerhetslag (2021:500)", "2022-01-01"),
    # konsumentköplag: an older retired law and the new one replacing it
    _sfs_art("https://lagen.nu/1990:932", "Konsumentköplag (1990:932)", "1991-01-01"),
    _sfs_art("https://lagen.nu/2022:260", "Konsumentköplag (2022:260)", "2022-06-01"),
]


def build_pin_catalog(tmp_path):
    from accommodanda.forarbete import genomforande
    db = str(tmp_path / "catalog.sqlite")
    prop = tmp_path / "prop.json"
    prop.write_text(json.dumps(PROP_PIN))
    sfs_paths = []
    for art in SFS_LAWS:
        p = tmp_path / (catalog.local(art["uri"]).replace(":", "_") + ".json")
        p.write_text(json.dumps(art))
        sfs_paths.append(p)
    direc = tmp_path / "dir.json"
    direc.write_text(json.dumps(DIRECTIVE))
    catalog.rebuild(db, "forarbete", [prop])
    catalog.rebuild(db, "sfs", sfs_paths)
    catalog.rebuild(db, "eurlex", [direc])
    con = catalog.connect(db)
    genomforande.resolve(con)
    return con


def test_norm_title_drops_sfs_number():
    assert catalog.norm_title("Lag (2015:671) om alternativ tvistlösning") \
        == catalog.norm_title("lag om alternativ tvistlösning")


def test_genomfor_resolves_all_three_paths(tmp_path):
    con = build_pin_catalog(tmp_path)
    pins = {(s, a): (d, art) for s, a, d, art, *_ in con.execute(
        "SELECT sfs_uri, sfs_anchor, directive, article FROM genomforande")}
    # Case 1: amending -> SFS number straight from the rubrik, fragment P3
    assert ("https://lagen.nu/1999:100", "P3") in pins
    # Case 2 unique: new law matched by title; chaptered fragment K2P1
    assert ("https://lagen.nu/2021:500", "K2P1") in pins
    # Case 2 tie-break: the new konsumentköplag (ikraft after the prop), not the old
    assert ("https://lagen.nu/2022:260", "P4") in pins
    assert not any(s == "https://lagen.nu/1990:932" for s, a in pins)


def test_genomfor_partial_flag_preserved(tmp_path):
    con = build_pin_catalog(tmp_path)
    rows = catalog.genomfor_for(con, "https://lagen.nu/2021:500", "K2P1")
    assert rows and rows[0][5] == 1   # partial


def test_statute_page_shows_genomfor_margin(tmp_path):
    con = build_pin_catalog(tmp_path)
    site = render.Site.from_catalog(con)
    html = render.render_sfs(SFS_LAWS[1], site)   # cybersäkerhetslag 2021:500
    # the genomför-direktiv block rides in the transposing paragraf's rail panel
    rail = "".join(_island(html).values())
    assert "Genomför EU-rätt" in rail
    assert 'href="/celex/32022L2555#21"' in rail
    assert "genomför delvis artikel 21.2" in rail
    assert "Prop. 2021/22:9" in rail              # provenance


def test_directive_article_inbound_shows_statute(tmp_path):
    con = build_pin_catalog(tmp_path)
    inbound = catalog.inbound(con, DIR + "#21")
    sources = {src for _u, _a, _l, _t, src in inbound}
    # the directive article is now cited by both the proposition and the statutes
    assert "forarbete" in sources and "sfs" in sources


# --- EU act editorial layer: recital groups + article<->recital rail ------

# a small regulation: two grouped recitals, an article with a numbered paragraph
# and a lettered point, plus the .ann editorial layer linking them both ways
ACT = {
    "uri": "https://lagen.nu/ext/celex/32099R0001", "celex": "32099R0001",
    "doctype": "regulation", "title": "Testförordning",
    "structure": [
        {"type": "recital", "num": "1", "text": ["Bakgrund."]},
        {"type": "recital", "num": "2", "text": ["Syfte."]},
        {"type": "article", "id": "4", "num": "4", "text": ["Artikel 4 – Skyldigheter"],
         "children": [
            {"type": "paragraph", "num": "1",
             "text": ["Datahållaren ska göra data tillgänglig."], "children": [
                {"type": "point", "num": "a", "text": ["på ett säkert sätt."]}]}]},
    ],
}
LAYER = {
    "recitalGroups": [
        {"id": "rg", "label": "Bakgrund och syfte", "range": [1, 2],
         "articleRefs": ["4"]}],
    # deliberately the legacy parenthesised sub-article form, to prove the loader
    # normalises it to the canonical dotted grammar ("4(1)(a)" -> "4.1.a")
    "articleToRecitals": {"4": [1, 2], "4(1)": [1], "4(1)(a)": [2]},
}


def _render_act(monkeypatch, tmp_path):
    monkeypatch.setattr(render, "_load_editorial",
                        lambda celex: render.Editorial(LAYER))
    db = str(tmp_path / "catalog.sqlite")
    act = tmp_path / "act.json"
    act.write_text(json.dumps(ACT))
    catalog.rebuild(db, "eurlex", [act])
    return render.render_eurlex(ACT, render.Site.from_catalog(catalog.connect(db)))


def test_editorial_indexes_both_directions():
    ed = render.Editorial(LAYER)
    assert ed.group_start[1]["label"] == "Bakgrund och syfte"
    assert ed.group_of[2]["id"] == "rg"
    # recital 2 is cited by article 4 (whole) and sub-point 4.1.a -> article {4}
    assert ed.recital_articles[2] == ["4"]
    assert ed.recitals_for("4.1.a") == [2]      # legacy "4(1)(a)" normalised on load


def test_subarticle_key_grammar():
    from accommodanda.lib.eu_structure import subarticle_key
    assert subarticle_key("paragraph", "1", "4", None) == "4.1"
    assert subarticle_key("point", "a", "4", "1") == "4.1.a"
    assert subarticle_key("point", "a", "4", None) == "4.a"
    assert subarticle_key("paragraph", "1", None, None) is None


def test_act_page_renders_recital_group_heading(monkeypatch, tmp_path):
    html = _render_act(monkeypatch, tmp_path)
    # a compact, subdued single-line editorial label (not a prominent heading)
    assert '<p id="rg" class="recital-group">' in html
    assert "Skäl 1–2:" in html and "<b>Bakgrund och syfte</b>" in html
    assert "(jfr art " in html and 'href="#4"' in html
    # recitals become anchorable targets and drive the rail
    assert '<p id="recital-1" class="recital" data-rail="recital-1">' in html


def test_act_rail_links_articles_and_subarticles_to_recitals(monkeypatch, tmp_path):
    html = _render_act(monkeypatch, tmp_path)
    island = _island(html)
    # article -> its recitals (forward)
    assert 'href="#recital-1"' in island["4"] and 'href="#recital-2"' in island["4"]
    # the sub-article paragraph and point each link to their own recitals
    # (dotted node ids -- the "4.1"/"4.1.a" grammar the renderer mints)
    assert island["4.1"] and 'href="#recital-1"' in island["4.1"]
    assert island["4.1.a"] and 'href="#recital-2"' in island["4.1.a"]
    # recital -> back to the articles it underpins, plus its thematic group
    assert 'href="#4"' in island["recital-1"]
    assert "Bakgrund och syfte" in island["recital-1"]


def test_act_toc_has_preamble_section_with_group_titles(monkeypatch, tmp_path):
    html = _render_act(monkeypatch, tmp_path)
    nav = re.search(r'<nav class="toc">.*?</nav>', html, re.S)
    assert nav, "act page should have a TOC"
    nav = nav.group(0)
    # a "Preambel" parent listing the group titles (linked to each group anchor)
    assert ">Preambel</a>" in nav
    assert ">Bakgrund och syfte</a>" in nav and 'href="#rg"' in nav
    # just the titles -- no recital numbers, no "jfr art" article refs in the TOC
    assert "Skäl" not in nav and "jfr art" not in nav


def test_act_without_annotation_has_no_recital_groups(tmp_path):
    # default _load_editorial: no .ann sidecar for this synthetic celex -> plain page
    db = str(tmp_path / "catalog.sqlite")
    act = tmp_path / "act.json"
    act.write_text(json.dumps(ACT))
    catalog.rebuild(db, "eurlex", [act])
    html = render.render_eurlex(ACT, render.Site.from_catalog(catalog.connect(db)))
    # the editorial grouping (group headings + the article<->recital back-links) is
    # absent without a .ann
    assert "recital-group" not in html
    assert "Tematisk grupp" not in html and "Relevanta skäl" not in html
    # but a numbered recital still gets its stable `#recital-N` citation anchor, so
    # it can be cited and commented on even with no editorial layer
    assert 'id="recital-1"' in html


def test_genomfor_pinpoints_split_per_article(tmp_path):
    # a single statement transposing two articles (2 and 26): each genomforande
    # row shows only its own article's pinpoints, not the whole statement's
    from accommodanda.forarbete import genomforande
    db = str(tmp_path / "catalog.sqlite")
    prop = tmp_path / "prop.json"
    prop.write_text(json.dumps({
        "uri": "https://lagen.nu/prop/2025/26:28", "type": "prop",
        "identifier": "Prop. 2025/26:28", "date": "2025-10-14",
        "body": [{"type": "rubrik", "level": 1, "page": 1,
                  "text": ["Författningskommentar"]}],
        "implements": [{
            "predicate": "rpubl:genomforDirektiv", "directive": DIR,
            "articles": ["2", "26"], "pinpoints": ["2.1", "2.2 f", "26.1 c"],
            "uris": [DIR + "#2", DIR + "#26"], "partial": True,
            "law": "15.1 Förslaget till cybersäkerhetslag",
            "chapter": "1", "paragraf": "3", "page": 1, "sentence": "genomför"}]}))
    sfs = tmp_path / "sfs.json"
    sfs.write_text(json.dumps(_sfs_art("https://lagen.nu/2025:1506",
                                       "Cybersäkerhetslag (2025:1506)", "2026-01-15")))
    direc = tmp_path / "dir.json"
    direc.write_text(json.dumps(DIRECTIVE))
    catalog.rebuild(db, "forarbete", [prop])
    catalog.rebuild(db, "sfs", [sfs])
    catalog.rebuild(db, "eurlex", [direc])
    con = catalog.connect(db)
    genomforande.resolve(con)
    pins = {a: pin for _d, a, _pu, _pl, pin, _pa
            in catalog.genomfor_for(con, "https://lagen.nu/2025:1506", "K1P3")}
    assert pins == {"2": "2.1, 2.2 f", "26": "26.1 c"}


def test_commentary_shows_in_paragraph_rail_not_as_page(tmp_path):
    # wiki SFS commentary is an annotation -- its prose shows in the statute
    # paragraph's context rail, side-by-side; there is no kommentar page
    ad = tmp_path / "art"
    ad.mkdir()
    law = ad / "law.json"
    law.write_text(json.dumps({
        "uri": "https://lagen.nu/1962:700",
        "metadata": {"properties": {"dcterms:title": "Brottsbalk"}},
        "structure": [{"type": "paragraf", "id": "K3P1", "ordinal": "1",
                       "text": ["Den som berövar annan livet döms för mord."]}]}))
    komm = ad / "komm.json"
    komm.write_text(json.dumps({
        "uri": "https://lagen.nu/kommentar/1962:700", "type": "kommentar",
        "annotates": "https://lagen.nu/1962:700", "author": "Foo Bar",
        "body": [{"type": "sektion", "id": "K3P1", "heading": "3 kap. 1 §",
                  "text": ["3 kap. 1 §"],
                  "children": [{"text": ["Bestämmelsen kräver uppsåt."]}]}]}))
    db = str(tmp_path / "catalog.sqlite")
    catalog.rebuild(db, "sfs", [law])
    catalog.rebuild(db, "kommentar", [komm])
    con = catalog.connect(db)
    site = render.Site.from_catalog(con)
    assert site.commentary[("https://lagen.nu/1962:700", "K3P1")]  # indexed

    html = render.render_sfs(json.loads(law.read_text()), site)
    island = re.search(r'id="lagen-context">(.*?)</script>', html, re.S).group(1)
    assert "rail-komm" in island                       # rail section emitted
    assert "Bestämmelsen kräver uppsåt." in island     # the prose, side-by-side
    assert "Foo Bar" in island                         # author byline

    # the statute's inbound never lists commentary as a citing document
    assert catalog.inbound(con, "https://lagen.nu/1962:700#K3P1") == []


def test_law_level_commentary_is_the_rail_default_panel(tmp_path):
    # commentary before the first section heading is about the statute as a whole;
    # it becomes the rail's default panel (key '') -- shown when no paragraph is
    # in focus, e.g. at the top of the document
    ad = tmp_path / "art"
    ad.mkdir()
    law = ad / "law.json"
    law.write_text(json.dumps({
        "uri": "https://lagen.nu/1915:218",
        "metadata": {"properties": {"dcterms:title": "Avtalslag"}},
        "structure": [{"type": "paragraf", "id": "P1", "ordinal": "1",
                       "text": ["Anbud om slutande av avtal …"]}]}))
    komm = ad / "komm.json"
    komm.write_text(json.dumps({
        "uri": "https://lagen.nu/kommentar/1915:218", "type": "kommentar",
        "annotates": "https://lagen.nu/1915:218", "author": "Ellinor",
        "body": [{"type": "stycke", "text": ["Lagen reglerar avtalsslutande."]},
                 {"type": "sektion", "id": "P1", "heading": "1 §", "text": ["1 §"],
                  "children": [{"text": ["Om anbud och accept."]}]}]}))
    db = str(tmp_path / "catalog.sqlite")
    catalog.rebuild(db, "sfs", [law])
    catalog.rebuild(db, "kommentar", [komm])
    con = catalog.connect(db)
    site = render.Site.from_catalog(con)
    assert ("https://lagen.nu/1915:218", None) in site.commentary  # preamble indexed

    island = _island(render.render_sfs(json.loads(law.read_text()), site))
    assert "Lagen reglerar avtalsslutande." in island[""]   # law-level default panel
    assert "Om dokumentet" in island[""]
    assert "Om anbud och accept." in island["P1"]            # per-paragraph still works
