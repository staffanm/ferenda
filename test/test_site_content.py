"""Tests for the editorial `site` vertical: markdown -> typed artifacts
(ferenda.site.parse) and artifacts -> HTML + Atom (ferenda.site.render),
against a small fixture site/ tree under test/files/sitecontent/."""

import json
import re
import xml.dom.minidom as minidom
from pathlib import Path

import pytest

from ferenda.lib import compress, layout, markdown
from ferenda.lib import render as lib_render
from ferenda.site import parse, render

FIX = str(Path(__file__).resolve().parent / "files" / "sitecontent")


def test_sfs_and_eurlex_schemes_resolve():
    # the generic source:identifier link rules added to lib.markdown (reusable by
    # any source); symmetric -- content names the source, never its URL shape
    assert markdown.target_uri("sfs:1949:381") == "https://lagen.nu/1949:381"
    assert markdown.target_uri("sfs:1845:50_s.1") == "https://lagen.nu/1845:50_s.1"
    assert markdown.target_uri("eurlex:32016R0679") \
        == "https://lagen.nu/celex/32016R0679"


def test_list_basefiles():
    assert parse.list_basefiles(FIX) == [
        "frontpage", "sitenews", "om/index", "om/lankning",
        "subdomain/lagen.nu/jante"]


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


def test_spaced_dashes_convert_in_prose_but_never_in_code():
    # the authored ` -- ` convention renders as a dash (E2); a code span keeps
    # its hyphens -- the om pages document CLI flags
    blocks = parse.blocks("## Rubrik -- med inskott\n\n"
                          "texterna i sig -- de finns publicerade -- på "
                          "andra håll, se `lagen --force`.", "om/x")
    assert blocks[0].text == "Rubrik – med inskott"
    runs = blocks[1].runs
    assert runs[0] == "texterna i sig – de finns publicerade – på andra håll, se "
    assert runs[1] == {"text": "lagen --force", "code": True}


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


def test_an_html_comment_is_dropped_with_everything_it_encloses():
    # markdown-it runs with html:False, which renders a comment as the *text*
    # `<!-- …` rather than dropping it -- the same leak the commentary pages had
    # (U1). Both parsers now strip comments through lib.markdown.strip_comments.
    assert [b.runs for b in parse.blocks(
        "Synlig text.\n\n<!-- en notis\n\nsom sträcker sig över stycken -->\n\nOckså synlig.",
        "om/x")] == [["Synlig text."], ["Också synlig."]]


def test_unmappable_markdown_names_the_basefile():
    # a construct with no block form must say so rather than drop the prose
    with pytest.raises(ValueError, match="om/x: block markdown 'blockquote'"):
        parse.blocks("> citat", "om/x")


def test_an_empty_link_label_names_the_basefile():
    # `[](/a)` resolves its target but has nothing to hang it on, so the link
    # would vanish without trace
    with pytest.raises(ValueError, match="om/x: the link to '/a' has an empty"):
        parse.blocks("se [](/a) här", "om/x")


def test_subdomain_page_relpath_is_explicit_not_a_passthrough():
    assert layout.page_relpath("subdomain/lagen.nu/jante") == \
        "subdomain/lagen.nu/jante.html"


def test_subdomain_record_finds_the_zone_nested_path():
    assert parse.record(FIX, "subdomain/lagen.nu/jante") == (
        Path(FIX) / "site" / "subdomain" / "lagen.nu" / "jante.md")


def test_subdomain_parse_zone_slug_title_and_body():
    art = parse.artifact("subdomain/lagen.nu/jante", FIX)
    assert art["type"] == "subdomain"
    assert (art["zone"], art["slug"], art["title"]) == (
        "lagen.nu", "jante", "Jantelagen")
    assert art["blocks"][0]["type"] == "stycke"
    assert art["blocks"][1] == {"text": "Jantelagens strafflag",
                                "level": 2, "type": "rubrik"}


def test_subdomain_page_without_a_title_names_the_basefile(tmp_path):
    # every site page is titled by its frontmatter, never by a body heading
    # (PRD-subdomains.md) -- jante.md/kamomilla.md originally shipped with a
    # bare `# Title` body heading and no frontmatter, which this rejects
    # rather than silently promoting the heading.
    d = tmp_path / "site" / "subdomain" / "lagen.nu"
    d.mkdir(parents=True)
    (d / "kamomilla.md").write_text("# Kamomillalag\n\n1 § ...\n", encoding="utf-8")
    with pytest.raises(ValueError,
                       match="subdomain/lagen.nu/kamomilla: no frontmatter"):
        parse.artifact("subdomain/lagen.nu/kamomilla", tmp_path)


def test_subdomain_render_matches_an_about_page_shape():
    html = render.render_subdomain(parse.artifact("subdomain/lagen.nu/jante", FIX))
    assert "<h2>Jantelagens strafflag</h2>" in html
    assert "<title>Jantelagen" in html


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


def test_sitenews_renders_on_the_shared_feed_screen():
    """The news feed is one feed among the sixteen, so its page carries the same
    source selector as every document feed -- in the same left rail, marked on
    itself. Asserting only the article count let the rail be dropped from
    `sitenews_body` with the suite still green."""
    html = render.render_sitenews(parse.artifact("sitenews", FIX))
    assert '<aside class="browse-facets">' in html          # the shared shell
    assert '<a href="/dataset/sitenews/feed/" aria-current="page">' in html
    assert '"/dataset/sfs/feed"' in html                    # …and every other feed
    assert '<a href="/dataset/sitenews/">Alla nyhetsflöden' in html


def test_atom_is_wellformed_and_newest_first():
    art = parse.artifact("sitenews", FIX)
    atom = render.render_atom(art)
    minidom.parseString(atom)                            # raises if malformed
    assert atom.index("Lysator") < atom.index("Ny version lanserad")
    assert "2020-09-17T23:00:00Z" in atom
    assert atom.count("<entry>") == 2


def test_frontpage_render_links_and_masthead():
    html = render.render_frontpage(parse.artifact("frontpage", FIX))
    assert "<h1>lagen<em>.nu</em></h1>" in html
    assert 'href="/1949:381"' in html                    # sfs uri -> bare /id
    assert "<strong>" in html
    assert ">Om</a>" in html and ">Nyheter</a>" in html   # new masthead entries


def test_the_frontpage_wears_the_mark_and_every_page_links_the_icons():
    # the mark is frontpage-only (page(mark=True)) and takes the theme tokens,
    # so it inverts with the page instead of shipping a second dark copy
    html = render.render_frontpage(parse.artifact("frontpage", FIX))
    assert '<svg class="site-mark"' in html
    assert 'stroke="var(--ink)"' in html and 'fill="var(--accent)"' in html
    assert 'href="/favicon.svg"' in html
    # a document page gets the icons but not the mark
    news = render.render_sitenews(parse.artifact("sitenews", FIX))
    assert "site-mark" not in news
    assert 'href="/favicon.svg"' in news


def test_write_assets_emits_the_icons(tmp_path):
    lib_render.write_assets(tmp_path)
    # the SVG is above compress.MIN_SIZE, so with compression on it lands as
    # favicon.svg.br only -- compress.exists knows both spellings. The ICO and
    # the PNG go through encodings=() and are always plain.
    assert compress.exists(tmp_path / "favicon.svg")
    for name in ("favicon.ico", "apple-touch-icon.png"):
        assert (tmp_path / name).exists(), name


def test_the_inline_mark_and_the_favicon_draw_the_same_figure():
    # the mark exists twice: as a template macro wearing the theme tokens and as
    # a standalone favicon with literal colours. Only the colours and the *radii*
    # of the two end nodes may differ (the favicon fills them, because the open
    # ring closes up below 28px), so the three arcs and every node centre must
    # match. page.html holds other glyphs too, hence the filter: the macro's
    # values are compared in the icon's own order.
    lib = Path(lib_render.__file__).parent
    page_html = (lib / "templates" / "page.html").read_text()
    icon_svg = (lib / "assets" / "favicon.svg").read_text()
    for pattern in (r'd="([^"]+)"', r'cx="([^"]+)" cy="([^"]+)"'):
        macro, icon = (re.findall(pattern, t) for t in (page_html, icon_svg))
        assert icon and [v for v in macro if v in icon] == icon, pattern


def test_write_site_emits_expected_paths(tmp_path, monkeypatch):
    artdir = tmp_path / "art"
    artdir.mkdir()
    paths = []
    for bf in ("frontpage", "sitenews", "om/index", "subdomain/lagen.nu/jante"):
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
    assert compress.exists(out / "subdomain" / "lagen.nu" / "jante.html")
    assert compress.exists(out / "dataset" / "sitenews" / "feed" / "index.html")
    assert compress.exists(out / "dataset" / "sitenews" / "feed.atom")
