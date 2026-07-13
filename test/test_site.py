"""Tests for the derived layer: the SQLite catalog (relate) and the static
HTML renderer (generate) -- REWRITE.md §6."""

import hashlib
import json
import re
from datetime import date
from pathlib import Path

import pytest

from accommodanda.lib import catalog, compress, render


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
    assert compress.exists(out / render.doc_relpath(LAW["uri"]))   # page stored precompressed


def test_generate_refuses_output_path_collision(tmp_path):
    # page_relpath flattens every non-alphanumeric character to "_", so two
    # distinct begrepp uris ("Första-hjälpen-tavlor" / "Första_hjälpen-tavlor")
    # collide on one HTML file: last write wins, and under jobs>1 the twin jobs
    # race on the deterministic .tmp name. The planner must refuse such a plan.
    a = tmp_path / "a.json"
    a.write_text(json.dumps({"uri": "https://lagen.nu/begrepp/Första_hjälpen-tavlor",
                             "type": "begrepp", "title": "Första hjälpen-tavlor",
                             "body": []}))
    b = tmp_path / "b.json"
    b.write_text(json.dumps({"uri": "https://lagen.nu/begrepp/Första-hjälpen-tavlor",
                             "type": "begrepp", "title": "Första-hjälpen-tavlor",
                             "body": []}))
    db = str(tmp_path / "catalog.sqlite")
    catalog.rebuild(db, "begrepp", [a, b])
    with pytest.raises(ValueError, match="output path collision"):
        render.generate_site(db, str(tmp_path / "generated"))


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
    # the collapsed query the "Hänvisat till av" panel reads likewise excludes it
    assert catalog.inbound_collapsed(con, "https://lagen.nu/1975:635#P5") == []


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
    # editor.js rides in the single concatenated bundle now, so the page links
    # /script.js (not a per-file /editor.js tag)
    assert law.count("</head>") == 1 and '/script.js' in law
    case = render.render_document(CASE, "dv", site)
    assert 'name="lagen-doc"' not in case      # court decisions are read-only
    assert '/script.js' in case                # but the bundle still loads (editor inert)


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


# a definition paragraf whose point 2 carries an a/b/c sub-list. The NF flattens
# the sub-list into document-order siblings (real 2025:1506 1 kap 2 § shape), with
# the nesting carried only by the ids (P1S1N2Na under P1S1N2) -- render must rebuild
# it into a nested <ol>. Also exercises the gutter §-numeral and the dotted marker.
NESTLAW = {
    "uri": "https://lagen.nu/2010:801",
    "metadata": {"properties": {"dcterms:title": "Testlag (2010:801)"}},
    "structure": [{"type": "paragraf", "id": "P1", "ordinal": "1", "children": [
        {"type": "stycke", "id": "P1S1", "text": ["I denna lag betyder"], "children": [
            {"type": "punkt", "id": "P1S1N1", "ordinal": "1", "text": ["första,"]},
            {"type": "punkt", "id": "P1S1N2", "ordinal": "2", "text": ["andra:"]},
            {"type": "punkt", "id": "P1S1N2Na", "ordinal": "a", "text": ["dellista a,"]},
            {"type": "punkt", "id": "P1S1N2Nb", "ordinal": "b", "text": ["dellista b, och"]},
            {"type": "punkt", "id": "P1S1N2Nc", "ordinal": "c", "text": ["dellista c,"]},
            {"type": "punkt", "id": "P1S1N3", "ordinal": "3", "text": ["tredje."]}]}]}],
}


def test_renders_nested_punkt_sublist(tmp_path):
    db = str(tmp_path / "c.sqlite")
    p = tmp_path / "l.json"
    p.write_text(json.dumps(NESTLAW))
    catalog.rebuild(db, "sfs", [p])
    site = render.Site.from_catalog(catalog.connect(db))
    html = render.render_sfs(NESTLAW, site)
    # outer list plus the nested a/b/c sublist
    assert html.count('class="punkter"') >= 2
    # a/b/c are nested under point 2 (between it and point 3), not flat siblings
    i2, ia, i3 = (html.index('id="P1S1N2"'), html.index('id="P1S1N2Na"'),
                  html.index('id="P1S1N3"'))
    assert i2 < ia < i3
    # the sublist opens inside point 2's <li> (a nested <ol> before its first child)
    assert '<ol class="punkter">' in html[i2:ia]
    # numeric markers carry the source's trailing dot; the lettered sub-item does not
    assert '<span class="num">2.</span>' in html
    assert '<span class="num">a</span>' in html
    # the §-numeral hangs in the gutter; the permalink keeps the pilcrow glyph
    assert '<span class="n">1 §</span>' in html
    assert 'aria-label="Permalänk">¶</a>' in html


def test_outbound_link_carries_no_tooltip(tmp_path):
    con = build_catalog(tmp_path)
    site = render.Site.from_catalog(con)
    # the case links to 1975:635#P6; hover preview is popover.js's job (built
    # client-side from the rendered target page), so the link must be plain --
    # a title attribute would fight the popover with a native tooltip
    html = render.render_dv(CASE, site)
    assert 'href="/1975:635#P6"' in html
    assert 'title="Ränta beräknas enligt 5 §."' not in html


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
    # a chapter's title rubrik is folded into a single chapter heading under the
    # chapter's own id: one TOC entry per chapter (#K1, #K2), plus section rubriks
    assert 'href="#K1"' in html and 'href="#K1R2"' in html and 'href="#K2"' in html
    assert "Tillämpningsområde" in html
    # the merged heading carries the chapter id (the #K1 anchor target) and the
    # full title, with no redundant bare-number "1 kap." heading beside it
    assert ('<h2 id="K1" class="kaprubrik">1 kap. Inledande bestämmelser</h2>'
            in html)
    assert "1 kap.</h2>" not in html          # no bare-number duplicate heading


def test_chapter_heading_flattens_self_referencing_designator(tmp_path):
    # a chapter title's leading "1 kap." is a *reference run* the projection
    # linked to the chapter's own #K1 anchor. Folded into the single chapter
    # heading it must render as plain text, not a live self-link inside its own
    # <h2>. The doc is put in the catalog so site.has() is true -- i.e. an
    # unflattened run genuinely WOULD render as <a href="/2020:1#K1">, so this
    # test fails if _strip_self_ref regresses to a no-op.
    db = str(tmp_path / "catalog.sqlite")
    doc = {"uri": "https://lagen.nu/2020:1",
           "metadata": {"properties": {"dcterms:title": "Testlag (2020:1)"}},
           "structure": [
               {"type": "kapitel", "id": "K1", "ordinal": "1", "children": [
                   {"type": "rubrik", "id": None, "level": 1, "text": [
                       {"predicate": "dcterms:references", "text": "1 kap.",
                        "uri": "https://lagen.nu/2020:1#K1"},
                       " Inledande bestämmelser"]},
                   {"type": "paragraf", "id": "K1P1", "ordinal": "1", "children": [
                       {"type": "stycke", "id": "K1P1S1",
                        "text": ["Tillämpas på allt."]}]}]}]}
    path = tmp_path / "2020-1.json"
    path.write_text(json.dumps(doc))
    catalog.rebuild(db, "sfs", [path])                  # now a hosted document...
    site = render.Site.from_catalog(catalog.connect(db))
    assert site.has("https://lagen.nu/2020:1#K1")       # ...so a run WOULD link
    html = render.render_sfs(doc, site)
    heading = re.search(r'<h2 id="K1" class="kaprubrik">(.*?)</h2>', html).group(1)
    assert heading == "1 kap. Inledande bestämmelser"   # designator de-linked
    assert "<a" not in heading and "#K1" not in heading  # no self-link survived

def test_generate_browse_writes_faceted_pages(tmp_path):
    # the browse pages are generated by consuming the REST API in-process, so this
    # exercises render_aggregates end to end (frontpage + faceted browse)
    con = build_catalog(tmp_path)        # Räntelag (1975:635), one HD case
    out = tmp_path / "site"
    render.render_aggregates(con, out, str(tmp_path / "catalog.sqlite"))
    # the law files under its subject initial 'R' (Räntelag); the source root and
    # the bucket page both list it (root == default bucket, no redirect)
    bucket = compress.read_text(out / "sfs" / "r" / "index.html")   # pages precompressed
    assert 'href="/1975:635"' in bucket          # the bare /<sfsid> page address
    assert "som börjar på R" in bucket
    assert ('type="application/atom+xml" '
            'href="/dataset/sfs/feed.atom"') in bucket
    assert 'href="/1975:635"' in compress.read_text(out / "sfs" / "index.html")
    # every case is listed under its court; the source root resolves directly
    assert 'href="/dom/NJA_1994_s_1"' in compress.read_text(out / "dom" / "index.html")
    # The complete search shell and its client are generated with the aggregate
    # chrome; the quick palette links to it and exposes the Right Arrow shortcut.
    search_page = compress.read_text(out / "sok" / "index.html")
    assert 'class="search-page"' in search_page
    # all client JS ships in one concatenated bundle, not as per-file scripts
    bundle = compress.read_text(out / render.SCRIPT_BUNDLE)
    assert not compress.exists(out / "fullsearch.js")   # folded into the bundle
    assert not compress.exists(out / "search.js")
    # the bundle carries the full-search client (fullsearch.js) ...
    assert "facetGroup('source', 'Källa'" in bundle
    assert "renderPagination" in bundle and "data.next_cursor" in bundle
    assert "api.delete('page'); api.delete('offset')" in bundle
    # ... and the ⌘K palette (search.js)
    assert "Avgränsa " in bundle and "e.key === 'ArrowRight'" in bundle
    assert 'class="search-refine" href="/sok/" hidden></a><input' in bundle
    assert "refine.hidden = true;" in bundle
    # fullsearch.js's own ordering guard (seq bumped before the empty-q return, so
    # an empty query still invalidates a pending request) -- asserted against the
    # source file, where "if (!q)" is unambiguously fullsearch's, not the bundle
    fullsearch = (render.ASSETS / "fullsearch.js").read_text(encoding="utf-8")
    assert fullsearch.index("var mine = ++seq") < fullsearch.index("if (!q)")
    # The legacy all-feeds directory and repository aliases are restored.
    feed_index = compress.read_text(out / "dataset" / "sitenews" / "index.html")
    assert "/dataset/sfs/feed.atom?rdf_type=type%2Flag" in feed_index
    assert "/dataset/dv/feed.atom" in feed_index
    assert compress.exists(out / "dataset" / "sfs" / "feed.atom")
    assert compress.exists(out / "dataset" / "forarbeten" / "feed.atom")
    assert compress.exists(out / "dataset" / "myndprax" / "feed.atom")


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
        return (uri in manifest and compress.exists(out_path)   # page precompressed
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


# the fresh/record protocol the incremental tests share: signature = own
# content_hash + the dependency digest generate_site hands over (which folds in
# the cross-document layers and the repeal status)
def _incremental_harness(manifest, rendered_uris):
    def signature(chash, dep):
        return hashlib.sha256(((chash or "") + dep).encode()).hexdigest()

    def fresh(uri, out_path, art_path, dep, chash):
        return (uri in manifest and compress.exists(out_path)
                and manifest[uri] == signature(chash, dep))

    def record(uri, art_path, dep, chash):
        manifest[uri] = signature(chash, dep)
        rendered_uris.append(uri)
    return fresh, record


KOMMENTAR = {
    # the uri grammar wiki/parse mints: BASE + "kommentar/" + basefile --
    # _kommentar_indexes recovers the identity from it (rule:fail-fast)
    "uri": "https://lagen.nu/kommentar/1975:635",
    "annotates": "https://lagen.nu/1975:635",
    "author": "testförfattare",
    "body": [{"type": "sektion", "id": "P6", "children": [
        {"type": "stycke", "text": ["Kommentar till 6 §."]}]}],
}


def test_kommentar_edit_rerenders_host_page(tmp_path):
    # kommentar prose renders onto the HOST act's page (the rail), not a page of
    # its own -- so editing it must invalidate the host page's freshness even
    # though the host's own artifact bytes and link sets are unchanged. This is
    # the site_cross_digests fold into the dependency digest; before it, only
    # --force shipped a commentary edit.
    db = str(tmp_path / "catalog.sqlite")
    law = tmp_path / "law.json"
    law.write_text(json.dumps(LAW))
    komm = tmp_path / "komm.json"
    komm.write_text(json.dumps(KOMMENTAR))
    catalog.rebuild(db, "sfs", [law])
    catalog.rebuild(db, "kommentar", [komm])
    out = tmp_path / "site"
    manifest, rendered_uris = {}, []
    fresh, record = _incremental_harness(manifest, rendered_uris)

    render.generate_site(db, out, fresh=fresh, record=record)
    assert LAW["uri"] in rendered_uris
    page = compress.read_text(out / render.doc_relpath(LAW["uri"]))
    assert "Kommentar till 6 §." in page             # prose reached the rail

    rendered_uris.clear()
    _, rendered = render.generate_site(db, out, fresh=fresh, record=record)
    assert rendered == 0                             # unchanged -> all fresh

    # edit the commentary prose, re-relate the kommentar source, regenerate:
    # the host act's page must re-render and carry the new prose
    komm.write_text(json.dumps({**KOMMENTAR, "body": [
        {"type": "sektion", "id": "P6", "children": [
            {"type": "stycke", "text": ["Omskriven kommentar."]}]}]}))
    catalog.rebuild(db, "kommentar", [komm])
    rendered_uris.clear()
    render.generate_site(db, out, fresh=fresh, record=record)
    assert rendered_uris == [LAW["uri"]]
    page = compress.read_text(out / render.doc_relpath(LAW["uri"]))
    assert "Omskriven kommentar." in page


def test_generate_site_only_composes_with_source(tmp_path):
    # the editor's post-commit rebuild passes BOTH source= and only= (one host
    # page within a source). They must compose to exactly that page -- source
    # overriding only made every editor checkout scan (and potentially render)
    # the whole source inside the web request.
    db = str(tmp_path / "catalog.sqlite")
    law = tmp_path / "law.json"
    law.write_text(json.dumps(LAW))
    law2 = tmp_path / "law2.json"
    law2.write_text(json.dumps({
        "uri": "https://lagen.nu/1976:100",
        "metadata": {"properties": {"dcterms:title": "Annan lag (1976:100)"}},
        "structure": [{"type": "paragraf", "id": "P1", "ordinal": "1",
                       "children": [{"type": "stycke", "id": "P1S1",
                                     "text": ["Text."]}]}]}))
    catalog.rebuild(db, "sfs", [law, law2])
    out = tmp_path / "site"

    total, rendered = render.generate_site(
        db, out, only={str(law)}, source="sfs")
    assert (total, rendered) == (1, 1)               # exactly the named page

    # a scope naming a document outside the source renders nothing
    total, rendered = render.generate_site(
        db, out, only={str(law)}, source="dv")
    assert total == 0


def test_repeal_date_passing_rerenders_page(tmp_path, monkeypatch):
    # a statute's repeal status is evaluated against *today* -- when its
    # upphavandedatum passes, the page must go stale by itself (no artifact
    # changed), or the site keeps presenting a repealed statute as in force
    db = str(tmp_path / "catalog.sqlite")
    law = tmp_path / "law.json"
    law.write_text(json.dumps({
        **LAW, "metadata": {"properties": {
            "dcterms:title": "Räntelag (1975:635)",
            "rpubl:upphavandedatum": "2099-01-01"}}}))
    catalog.rebuild(db, "sfs", [law])
    out = tmp_path / "site"
    manifest, rendered_uris = {}, []
    fresh, record = _incremental_harness(manifest, rendered_uris)

    render.generate_site(db, out, fresh=fresh, record=record)
    assert LAW["uri"] in rendered_uris
    rendered_uris.clear()
    _, rendered = render.generate_site(db, out, fresh=fresh, record=record)
    assert rendered == 0                             # repeal not yet in force

    class _after_repeal(date):
        @classmethod
        def today(cls):
            return date(2099, 1, 2)

    monkeypatch.setattr(render, "date", _after_repeal)
    rendered_uris.clear()
    render.generate_site(db, out, fresh=fresh, record=record)
    assert LAW["uri"] in rendered_uris               # status flipped -> re-rendered
    page = compress.read_text(out / render.doc_relpath(LAW["uri"]))
    assert "pphävd" in page                          # page now marked upphävd


def test_inbound_excludes_kommentar_annotation(tmp_path):
    # a kommentar citing the paragraf is a rail annotation, not a citing page, so
    # it is excluded from the inbound panel set -- both the per-pinpoint `inbound`
    # and the collapsed `inbound_collapsed` the "Hänvisat till av" panel renders
    db = str(tmp_path / "catalog.sqlite")
    law = tmp_path / "law.json"
    law.write_text(json.dumps(LAW))
    case = tmp_path / "case.json"
    case.write_text(json.dumps(CASE))
    komm = tmp_path / "komm.json"
    komm.write_text(json.dumps({
        **KOMMENTAR, "body": [{"type": "sektion", "id": "P6", "children": [
            {"type": "stycke", "text": [
                "se ", {"predicate": "dcterms:references", "text": "6 §",
                        "uri": "https://lagen.nu/1975:635#P6"}, "."]}]}]}))
    catalog.rebuild(db, "sfs", [law])
    catalog.rebuild(db, "dv", [case])
    catalog.rebuild(db, "kommentar", [komm])
    con = catalog.connect(db)
    rows = catalog.inbound(con, "https://lagen.nu/1975:635#P6")
    assert [r[4] for r in rows] == ["dv"]            # the case, not the kommentar
    collapsed = catalog.inbound_collapsed(con, "https://lagen.nu/1975:635#P6")
    assert [r[3] for r in collapsed] == ["dv"]       # one doc line, kommentar excluded


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


# --- collapsed inbound panel + own-förarbeten section --------------------

def test_forarbete_pinpoint_maps_anchor_to_human_form():
    assert render.forarbete_pinpoint("a14.3") == "avsnitt 14.3"
    assert render.forarbete_pinpoint("a1-17") == "avsnitt 1"   # clash suffix dropped
    assert render.forarbete_pinpoint("sid39") == "s. 39"
    assert render.forarbete_pinpoint("sec7") == ""             # generated, no number


def test_citer_name_prefers_full_title_by_kind():
    # a förarbete carries its full title on its number; a lagrådsremiss by title
    # alone; an sfs/dv citer already stores a human title
    assert render.citer_name("forarbete", "prop", "Prop. 2025/26:116",
                             "En ny funktion") == "Prop. 2025/26:116: En ny funktion"
    assert render.citer_name("forarbete", "lr", "Lr 1", "En ny lag") \
        == "Lagrådsremiss: En ny lag"
    assert render.citer_name("sfs", "law", "SFS 2010:800",
                             "Skollag (2010:800)") == "Skollag (2010:800)"


def test_citer_line_collapses_pinpoints_and_caps_at_five():
    # one line per document: full-title name + up to five avsnitt (shared category
    # word written once, each number its own link), then " m.fl." beyond five
    row = ("https://lagen.nu/prop/2025/26:123", "Prop. 2025/26:123",
           "Explosiva varor", "forarbete", "prop", "2025-01-01",
           "a18.4.1,a15.2,a16.7,a2,a9,a11")     # six avsnitt, out of order
    li = render._citer_line(row)
    assert 'href="/prop/2025/26:123">Prop. 2025/26:123: Explosiva varor</a>' in li
    assert "avsnitt <a" in li                             # category word once
    assert li.index("2</a>") < li.index("9</a>") < li.index("15.2</a>")  # natural sort
    assert li.endswith(" m.fl.</li>")                     # >5 -> m.fl.
    assert 'href="/prop/2025/26:123#a15.2">15.2</a>' in li


FORARB_LAW = {
    "uri": "https://lagen.nu/2020:100",
    "metadata": {"properties": {"dcterms:title": "Testlag (2020:100)"}},
    "amendments": [
        {"forarbeten": ["Prop. 2019/20:5", "SOU 2018:9", "Bet. 2019/20:XX1"]},
        {"forarbeten": ["Prop. 2021/22:7"]},
    ],
    "structure": [{"type": "paragraf", "id": "P1", "ordinal": "1", "children": [
        {"type": "stycke", "id": "P1S1", "text": ["Text."]}]}],
}


def _citer(uri, ident, title, dt, anchors):
    """A förarbete artifact citing the whole Testlag from each of `anchors`."""
    return {"uri": uri, "type": "prop" if "prop" in uri else "sou",
            "identifier": ident, "title": title, "date": dt,
            "structure": [{"type": "avsnitt", "id": a, "children": [
                {"type": "stycke", "text": [
                    "se ", {"predicate": "dcterms:references", "text": "lagen",
                            "uri": "https://lagen.nu/2020:100"}, "."]}]}
                for a in anchors]}


def build_forarb_catalog(tmp_path):
    db = str(tmp_path / "catalog.sqlite")
    files = {
        "law": FORARB_LAW,
        # own preparatory works (in the register): a prop + a SOU, both citing the law
        "propown": _citer("https://lagen.nu/prop/2019/20:5", "Prop. 2019/20:5",
                          "Ursprungspropositionen", "2020-02-01", ["a5.2", "a5.3"]),
        "souown": _citer("https://lagen.nu/sou/2018:9", "SOU 2018:9",
                         "Utredningen", "2018-06-01", ["a3.1"]),
        # a later, unrelated prop citing the law -- stays in the panel
        "propother": _citer("https://lagen.nu/prop/2023/24:9", "Prop. 2023/24:9",
                            "Senare proposition", "2024-03-01", ["a1.1"]),
    }
    for name, art in files.items():
        (tmp_path / (name + ".json")).write_text(json.dumps(art))
    catalog.rebuild(db, "sfs", [tmp_path / "law.json"])
    catalog.rebuild(db, "forarbete", [tmp_path / f for f in
                                      ("propown.json", "souown.json", "propother.json")])
    return catalog.connect(db)


def test_inbound_collapsed_aggregates_pinpoints_and_excludes(tmp_path):
    con = build_forarb_catalog(tmp_path)
    uri = "https://lagen.nu/2020:100"
    rows = catalog.inbound_collapsed(con, uri)
    # one row per citing document (the two-avsnitt prop is a single row)
    by_uri = {r[0]: r for r in rows}
    assert set(anchor for anchor in by_uri
               ["https://lagen.nu/prop/2019/20:5"][6].split(",")) == {"a5.2", "a5.3"}
    # excluding the law's own förarbeten drops them, keeps the unrelated prop
    kept = catalog.inbound_collapsed(con, uri, exclude_from={
        "https://lagen.nu/prop/2019/20:5", "https://lagen.nu/sou/2018:9"})
    assert [r[0] for r in kept] == ["https://lagen.nu/prop/2023/24:9"]


def test_forarbeten_section_lists_own_works_and_excludes_from_panel(tmp_path):
    site = render.Site.from_catalog(build_forarb_catalog(tmp_path))
    section, own = render.forarbeten_section(site, FORARB_LAW)
    # hosted own works link under their full-title label; the unhosted Bet. shows bare
    assert "Prop. 2019/20:5: Ursprungspropositionen" in section
    assert "SOU 2018:9: Utredningen" in section
    assert "Bet. 2019/20:XX1" in section
    assert own == {"https://lagen.nu/prop/2019/20:5", "https://lagen.nu/sou/2018:9"}
    # the citation panel below excludes them; only the unrelated prop remains
    panel = render.document_inbound(site, "https://lagen.nu/2020:100", own)
    assert "Prop. 2023/24:9: Senare proposition" in panel
    assert "Prop. 2019/20:5" not in panel and "SOU 2018:9" not in panel


def test_forarbeten_section_top_billed_above_citation_panel(tmp_path):
    site = render.Site.from_catalog(build_forarb_catalog(tmp_path))
    html = render.render_sfs(FORARB_LAW, site)
    assert html.index('<section class="forarbeten">') \
        < html.index('<section class="inbound-doc">')


def test_inbound_panel_overflow_is_expandable(tmp_path, monkeypatch):
    # citers past PANEL_CAP go behind a <details> "+N fler" disclosure (no JS)
    monkeypatch.setattr(render, "PANEL_CAP", 1)
    site = render.Site.from_catalog(build_forarb_catalog(tmp_path))
    panel = render.document_inbound(site, "https://lagen.nu/2020:100")
    # three förarbete citers, one shown, the other two disclosed
    assert '<details class="more"><summary>+2 fler</summary>' in panel


def test_forarbete_inbound_sorted_by_kind_then_date(tmp_path):
    # in the citation panel förarbeten order prop→sou, each block oldest-first:
    # the document_date column (populated at relate) drives the chronology
    site = render.Site.from_catalog(build_forarb_catalog(tmp_path))
    panel = render.document_inbound(site, "https://lagen.nu/2020:100")
    order = [panel.index("Prop. 2019/20:5"),   # prop, 2020-02
             panel.index("Prop. 2023/24:9"),   # prop, 2024-03 (later)
             panel.index("SOU 2018:9")]         # sou after every prop, despite 2018
    assert order == sorted(order)


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
    # the collapsed citer line carries the förarbete's full-title label and links
    # the page pinpoint ("s. 100") to its own anchor
    assert "Prop. 2023/24:1: Cybersäkerhetslag" in panel
    assert 'href="/prop/2023/24:1#sid100">100</a>' in panel


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
    # the per-paragraf FK commentary layer (forarbete.fk), resolved by the
    # same law paths: an amending-law entry, a combined-designator entry on a
    # chaptered new law, and a law-level entry (no designators -> anchor '')
    "kommentarer": [
        {"law": "5.1 Förslaget till lag om ändring i testlagen (1999:100)",
         "chapter": None, "paragrafer": ["3"], "page": 51,
         "kommentar": "Paragrafen ändras så att kraven skärps."},
        {"law": "5.2 Förslaget till cybersäkerhetslag",
         "chapter": "2", "paragrafer": ["1"], "page": 52,
         "kommentar": "Paragrafen är ny och genomför direktivet."},
        {"law": "5.1 Förslaget till lag om ändring i testlagen (1999:100)",
         "chapter": None, "paragrafer": [], "page": 53,
         "kommentar": "De ändringar som föreslås i lagen är följdändringar."},
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
    from accommodanda.forarbete import fk, genomforande
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
    fk.resolve(con)
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


# --- per-paragraf författningskommentar pinned to SFS paragrafs -----------

def test_fk_resolves_to_statute_anchors(tmp_path):
    con = build_pin_catalog(tmp_path)
    rows = {(s, a): text for s, a, _pu, _pl, _pd, _pg, text in
            catalog.fk_kommentar_all(con)}
    # amending law: SFS number from the rubrik, flat fragment
    assert rows[("https://lagen.nu/1999:100", "P3")].startswith(
        "Paragrafen ändras")
    # new law by title, chaptered fragment
    assert rows[("https://lagen.nu/2021:500", "K2P1")].startswith(
        "Paragrafen är ny")
    # a law-level entry (no designators) lands on anchor '' (the document rail)
    assert rows[("https://lagen.nu/1999:100", "")].startswith("De ändringar")
    # no FK edge in links: a prop's own FK is display, not a citation
    assert not con.execute("SELECT 1 FROM links WHERE predicate LIKE '%fk%'"
                           ).fetchall()


def test_statute_paragraf_rail_shows_fk_commentary(tmp_path):
    con = build_pin_catalog(tmp_path)
    site = render.Site.from_catalog(con)
    html = render.render_sfs(SFS_LAWS[1], site)   # cybersäkerhetslag 2021:500
    panel = _island(html)["K2P1"]
    assert "Författningskommentar" in panel
    assert "Paragrafen är ny och genomför direktivet." in panel
    # provenance links the prop's FK page pinpoint
    assert 'href="/prop/2021/22:9#sid52">Prop. 2021/22:9</a>' in panel


def test_law_level_fk_lands_on_document_rail(tmp_path):
    con = build_pin_catalog(tmp_path)
    site = render.Site.from_catalog(con)
    html = render.render_sfs(SFS_LAWS[0], site)   # testlagen 1999:100
    panel = _island(html)[""]
    assert "De ändringar som föreslås i lagen är följdändringar." in panel


def test_prop_page_highlights_fk_commentary_blocks(tmp_path):
    # blocks the FK extractor stamped `fk` render inside one fk-komm box per
    # run; quoted lagtext and ordinary prose stay outside
    con = build_pin_catalog(tmp_path)
    site = render.Site.from_catalog(con)
    art = {"uri": "https://lagen.nu/prop/2021/22:9", "type": "prop",
           "identifier": "Prop. 2021/22:9", "structure": [
               {"type": "rubrik", "level": 1, "text": ["16 Författningskommentar"]},
               {"type": "paragraf", "num": "1", "text": ["1 § Lagtext här."]},
               {"type": "stycke", "text": ["Paragrafen är ny."], "fk": 1},
               {"type": "stycke", "text": ["Övervägandena finns i avsnitt 5."],
                "fk": 1},
               {"type": "stycke", "text": ["Paragrafen ändras."], "fk": 2},
               {"type": "stycke", "text": ["2 § Mera lagtext."]}]}
    html = render.render_forarbete(art, site)
    # one box per entry: the two fk=1 blocks share a box, fk=2 gets its own
    assert html.count('<div class="fk-komm">') == 2
    boxed = html.split('<div class="fk-komm">')[1].split("</div>")[0]
    assert "Paragrafen är ny." in boxed
    assert "Övervägandena finns i avsnitt 5." in boxed
    assert "Paragrafen ändras." not in boxed
    assert "Mera lagtext" not in boxed


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
    assert '<p id="recital-1" class="recital hang" data-rail="recital-1">' in html


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
    # every numbered sub-article is addressable (a stable id + a permalink marker)
    # even with nothing to show in the rail -- but without context it does NOT ride
    # the rail, so ubiquitous ids don't litter the margin with markers
    assert '<p id="4.1.a"' in html and '<a class="num" href="#4.1.a">a)</a>' in html
    assert 'id="4.1.a" class="point hang"' in html          # no data-rail attribute


def test_act_structural_markers_and_hanging_indent(monkeypatch, tmp_path):
    # the artifact stores bare structural tokens ("1", "a"); the renderer supplies
    # the presentational punctuation and the .hang class that hangs the marker in
    # the left margin. Every numbered sub-article is addressable, and its marker
    # doubles as the permalink: a recital "(1)", a numbered paragraph "1.", a
    # lettered point "a)"
    html = _render_act(monkeypatch, tmp_path)
    assert '<p id="recital-1" class="recital hang"' in html
    assert '<a class="num" href="#recital-1">(1)</a>' in html   # recital
    assert '<p id="4.1" class="paragraph hang"' in html
    assert '<a class="num" href="#4.1">1.</a>' in html          # numbered paragraph
    assert '<p id="4.1.a" class="point hang"' in html           # every point has an id
    assert '<a class="num" href="#4.1.a">a)</a>' in html        # lettered point


def test_defined_term_links_to_begrepp_page(tmp_path):
    # a definition's lead term links to the corpus begrepp page for that concept,
    # folding the inflected form onto its canonical page via the alias graph
    # ("personuppgifter" -> /begrepp/Personuppgift); a term with no page stays a
    # plain <dfn>
    db = str(tmp_path / "catalog.sqlite")
    concept = tmp_path / "c.json"
    concept.write_text(json.dumps({
        "uri": "https://lagen.nu/begrepp/Personuppgift", "type": "begrepp",
        "title": "Personuppgift", "body": [],
        "aliases": ["https://lagen.nu/begrepp/Personuppgifter"]}))
    catalog.rebuild(db, "begrepp", [concept])
    act = {
        "uri": "https://lagen.nu/ext/celex/32099R0002", "celex": "32099R0002",
        "doctype": "regulation", "title": "T",
        "structure": [
            {"type": "article", "id": "4", "num": "4", "text": ["Artikel 4"],
             "children": [
                {"type": "paragraph", "num": "1", "id": "4.1",
                 "defines": "personuppgifter",
                 "text": ["personuppgifter: varje upplysning."]},
                {"type": "paragraph", "num": "2", "id": "4.2",
                 "defines": "kverkställighet",
                 "text": ["kverkställighet: något som saknar begreppssida."]}]}]}
    actp = tmp_path / "a.json"
    actp.write_text(json.dumps(act))
    catalog.rebuild(db, "eurlex", [actp])
    con = catalog.connect(db)
    catalog.canonicalize_concepts(con)     # folds the alias graph into concept_alias
    html = render.render_eurlex(act, render.Site.from_catalog(con))
    assert ('<a href="/begrepp/Personuppgift"><dfn>personuppgifter</dfn></a>'
            in html)
    assert "<dfn>kverkställighet</dfn>" in html               # no page -> no link
    assert '<a href="/begrepp/Kverkställighet"' not in html


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
