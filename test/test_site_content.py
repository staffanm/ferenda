"""Tests for the editorial `site` vertical: markdown -> typed artifacts
(accommodanda.site.parse) and artifacts -> HTML + Atom (accommodanda.site.render),
against a small fixture site/ tree under test/files/sitecontent/."""

import json
import xml.dom.minidom as minidom
from pathlib import Path

import pytest

from accommodanda.lib import compress, markdown
from accommodanda.site import parse, render

FIX = str(Path(__file__).resolve().parent / "files" / "sitecontent")


def test_sfs_and_eurlex_schemes_resolve():
    # the generic source:identifier link rules added to lib.markdown (reusable by
    # any source); symmetric -- content names the source, never its URL shape
    assert markdown.target_uri("sfs:1949:381") == "https://lagen.nu/1949:381"
    assert markdown.target_uri("sfs:1845:50_s.1") == "https://lagen.nu/1845:50_s.1"
    assert markdown.target_uri("eurlex:32016R0679") \
        == "https://lagen.nu/ext/celex/32016R0679"


def test_list_basefiles():
    assert parse.list_basefiles(FIX) == [
        "frontpage", "sitenews", "om/index", "om/lankning"]


def test_frontpage_parse_categories_bold_and_link():
    art = parse.artifact("frontpage", FIX)
    assert art["type"] == "frontpage"
    assert [b["text"] for b in art["blocks"] if b["type"] == "rubrik"] \
        == ["Familjerätt", "Straffrätt"]
    first_list = next(b for b in art["blocks"] if b["type"] == "lista")
    # a `**[Label](sfs:…)**` bullet -> one bold link run resolved to the sfs uri
    assert first_list["items"][0] == [
        {"text": "Föräldrabalk (FB)", "uri": "https://lagen.nu/1949:381",
         "bold": True}]
    # a non-bold bullet stays a plain link run
    assert first_list["items"][1] == [
        {"text": "Sambolag", "uri": "https://lagen.nu/2003:376"}]


def test_about_parse_title_code_and_links():
    art = parse.artifact("om/lankning", FIX)
    assert art["type"] == "om" and art["slug"] == "lankning"
    assert art["title"] == "Länkning"
    code = next(b for b in art["blocks"] if b["type"] == "kod")
    assert code["text"] == "https://lagen.nu/2003:389"
    # not every styled run is a link -- the fixture also carries bold/code runs
    uris = {r.get("uri") for b in art["blocks"] if b["type"] == "stycke"
            for r in b["runs"] if not isinstance(r, str)}
    assert "https://lagen.nu/1960:729" in uris


def test_about_parses_a_gfm_table_with_alignment():
    # the whole reason the site vertical parses with markdown-it rather than a
    # line scanner of its own: a pipe table used to fall through to a paragraph
    # and render as a wall of literal `|`
    art = parse.artifact("om/lankning", FIX)
    table = next(b for b in art["blocks"] if b["type"] == "tabell")
    assert [_flat(c) for c in table["head"]] == ["Sort", "Adress", "Not"]
    assert table["align"] == [None, None, "right"]        # the `|---:|` column
    assert len(table["rows"]) == 2
    # cells carry runs, not text: a code span and a link survive into the cell
    assert table["rows"][0][1] == [
        {"text": "https://lagen.nu/2003:389", "code": True}]
    assert table["rows"][1][1] == [
        {"text": "dom", "uri": "/dom/ad/1993:100"}]


def test_about_parses_ordered_lists_emphasis_mailto_and_rule():
    art = parse.artifact("om/lankning", FIX)
    ordered = next(b for b in art["blocks"]
                   if b["type"] == "lista" and b["ordered"])
    assert [_flat(i) for i in ordered["items"]] == ["hitta lagen",
                                                    "hitta paragrafen"]
    # `*paragrafen*` is emphasis, not two literal asterisks
    assert {"text": "paragrafen", "italic": True} in ordered["items"][1]
    assert any(b["type"] == "avdelare" for b in art["blocks"])    # `---`
    runs = [r for b in art["blocks"] if b["type"] == "stycke"
            for r in b["runs"] if not isinstance(r, str)]
    assert {"text": "mig", "uri": "mailto:staffan.malmgren@gmail.com"} in runs


def test_about_render_emits_table_ordered_list_and_rule():
    html = render.render_about(parse.artifact("om/lankning", FIX))
    assert "<table><thead><tr><th>Sort</th>" in html
    assert '<td style="text-align:right">1</td>' in html
    assert "<ol><li>hitta lagen</li>" in html
    assert "<em>paragrafen</em>" in html
    assert "<hr>" in html
    # a mailto: link is not decorated as an outbound-site link
    assert '<a href="mailto:staffan.malmgren@gmail.com">mig</a>' in html


def test_a_hard_wrap_inside_a_link_stays_one_run():
    # the author's wrap column is typography, not content: the softbreak used to
    # become its own styled run, so a wrapped link rendered as three <a>s -- the
    # middle one a link-decorated, arrow-suffixed space (live on /om/api)
    art = parse.artifact("om/lankning", FIX)
    runs = [r for b in art["blocks"] if b["type"] == "stycke" for r in b["runs"]]
    assert {"text": "bryts över två rader",
            "uri": "https://example.org/a"} in runs
    assert {"text": "fet text", "bold": True} in runs
    html = render.render_about(art)
    body = html[html.find("<main"):html.find("</main>")]
    assert '<a class="ext" href="https://example.org/a" rel="external">' \
        'bryts över två rader</a>' in body
    # no run-split elements in the body (the masthead's adjacent nav anchors
    # are why this is scoped to <main>)
    assert "</a><a" not in body and "</strong><strong" not in body


def test_a_heading_keeps_its_code_span():
    # `_text` kept only `text` tokens, so a code span in a heading vanished
    # outright and left a double space -- the silent loss this parser exists to
    # prevent
    art = parse.artifact("om/lankning", FIX)
    assert "Ankaret #K6P18 i en rubrik" in [
        b["text"] for b in art["blocks"] if b["type"] == "rubrik"]


def test_unmappable_markdown_names_the_basefile():
    # a construct with no block form must say so rather than drop the prose
    with pytest.raises(ValueError, match="om/x: block markdown 'blockquote'"):
        parse.blocks("> citat", "om/x")


def test_an_empty_link_label_names_the_basefile():
    # `[](/a)` resolves its target but has nothing to hang it on, so the link
    # would vanish without trace
    with pytest.raises(ValueError, match="om/x: the link to '/a' has an empty"):
        parse.blocks("se [](/a) här", "om/x")


def _flat(runs):
    return "".join(r if isinstance(r, str) else r["text"] for r in runs)


def test_about_site_relative_and_begrepp_links():
    art = parse.artifact("om/index", FIX)
    runs = [r for b in art["blocks"] if b["type"] == "stycke"
            for r in b["runs"] if not isinstance(r, str)]
    uris = {r["uri"] for r in runs}
    assert "/om/lankning" in uris                       # site-relative cross-link
    assert "https://lagen.nu/begrepp/Anbud" in uris     # begrepp: reused from lib


def test_sitenews_parse_preserves_file_order():
    art = parse.artifact("sitenews", FIX)
    assert [it["published"][:10] for it in art["items"]] \
        == ["2018-09-11", "2020-09-17"]
    assert art["items"][0]["id"] == "n2018-09-11-10-39-00"
    # the second item's bullet list is captured as a lista block
    assert any(b["type"] == "lista" for b in art["items"][1]["blocks"])


def test_sitenews_render_is_newest_first():
    art = parse.artifact("sitenews", FIX)
    html = render.render_sitenews(art)
    assert html.count("<article") == 2
    assert html.index("Lysator") < html.index("Ny version lanserad")
    assert ('rel="alternate" type="application/atom+xml" '
            'href="/dataset/sitenews/feed.atom"') in html


def test_atom_is_wellformed_and_newest_first():
    art = parse.artifact("sitenews", FIX)
    atom = render.render_atom(art)
    minidom.parseString(atom)                            # raises if malformed
    assert atom.index("Lysator") < atom.index("Ny version lanserad")
    assert "2020-09-17T23:00:00Z" in atom
    assert atom.count("<entry>") == 2


def test_frontpage_render_links_and_masthead():
    html = render.render_frontpage(parse.artifact("frontpage", FIX))
    assert 'href="/1949:381"' in html                    # sfs uri -> bare /id
    assert "<strong>" in html
    assert ">Om</a>" in html and ">Nyheter</a>" in html   # new masthead entries


def test_write_site_emits_expected_paths(tmp_path, monkeypatch):
    artdir = tmp_path / "art"
    artdir.mkdir()
    paths = []
    for bf in ("frontpage", "sitenews", "om/index"):
        p = artdir / (bf.replace("/", "_") + ".json")
        p.write_text(json.dumps(parse.artifact(bf, FIX)))
        paths.append(p)
    monkeypatch.setattr(render.layout, "artifacts", lambda source: paths)
    out = tmp_path / "out"
    out.mkdir()
    render.write_site(out)
    # pages are written precompressed (.html.br + .gz); compress.exists resolves
    # the logical path to whichever variant is on disk
    assert compress.exists(out / "index.html")
    assert compress.exists(out / "om" / "index.html")
    assert compress.exists(out / "dataset" / "sitenews" / "feed" / "index.html")
    assert compress.exists(out / "dataset" / "sitenews" / "feed.atom")
