"""Tests for the editorial `site` vertical: markdown -> typed artifacts
(accommodanda.site.parse) and artifacts -> HTML + Atom (accommodanda.site.render),
against a small fixture site/ tree under test/files/sitecontent/."""

import json
import xml.dom.minidom as minidom
from pathlib import Path

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
    art = parse.artifact(FIX, "frontpage")
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
    art = parse.artifact(FIX, "om/lankning")
    assert art["type"] == "om" and art["slug"] == "lankning"
    assert art["title"] == "Länkning"
    code = next(b for b in art["blocks"] if b["type"] == "kod")
    assert code["text"] == "https://lagen.nu/2003:389"
    uris = {r["uri"] for b in art["blocks"] if b["type"] == "stycke"
            for r in b["runs"] if not isinstance(r, str)}
    assert "https://lagen.nu/1960:729" in uris


def test_about_site_relative_and_begrepp_links():
    art = parse.artifact(FIX, "om/index")
    runs = [r for b in art["blocks"] if b["type"] == "stycke"
            for r in b["runs"] if not isinstance(r, str)]
    uris = {r["uri"] for r in runs}
    assert "/om/lankning" in uris                       # site-relative cross-link
    assert "https://lagen.nu/begrepp/Anbud" in uris     # begrepp: reused from lib


def test_sitenews_parse_preserves_file_order():
    art = parse.artifact(FIX, "sitenews")
    assert [it["published"][:10] for it in art["items"]] \
        == ["2018-09-11", "2020-09-17"]
    assert art["items"][0]["id"] == "n2018-09-11-10-39-00"
    # the second item's bullet list is captured as a lista block
    assert any(b["type"] == "lista" for b in art["items"][1]["blocks"])


def test_sitenews_render_is_newest_first():
    art = parse.artifact(FIX, "sitenews")
    html = render.render_sitenews(art)
    assert html.count("<article") == 2
    assert html.index("Lysator") < html.index("Ny version lanserad")
    assert ('rel="alternate" type="application/atom+xml" '
            'href="/dataset/sitenews/feed.atom"') in html


def test_atom_is_wellformed_and_newest_first():
    art = parse.artifact(FIX, "sitenews")
    atom = render.render_atom(art)
    minidom.parseString(atom)                            # raises if malformed
    assert atom.index("Lysator") < atom.index("Ny version lanserad")
    assert "2020-09-17T23:00:00Z" in atom
    assert atom.count("<entry>") == 2


def test_frontpage_render_links_and_masthead():
    html = render.render_frontpage(parse.artifact(FIX, "frontpage"))
    assert 'href="/1949:381"' in html                    # sfs uri -> bare /id
    assert "<strong>" in html
    assert ">Om</a>" in html and ">Nyheter</a>" in html   # new masthead entries


def test_write_site_emits_expected_paths(tmp_path, monkeypatch):
    artdir = tmp_path / "art"
    artdir.mkdir()
    paths = []
    for bf in ("frontpage", "sitenews", "om/index"):
        p = artdir / (bf.replace("/", "_") + ".json")
        p.write_text(json.dumps(parse.artifact(FIX, bf)))
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
