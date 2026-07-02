"""Tests for the markdown commentary/concept parsing
(accommodanda.lib.markdown + accommodanda.wiki.parse), the wikitext->markdown
conversion's losslessness (tools/mediawiki_to_markdown), and the
concept-synthesis that unifies extracted definitions/keywords with the wiki
concepts."""

import json
import sys
from pathlib import Path

import pytest

from accommodanda.lib import catalog, markdown
from accommodanda.wiki import annotate
from accommodanda.wiki import parse as wiki

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import mediawiki_to_markdown as conv  # noqa: E402
import wiki_artifact_diff as diff  # noqa: E402  (legacy reference builders + _norm)


def test_begrepp_uri_ucfirst_and_underscores():
    # MediaWiki upper-cases the first letter, so [foo](begrepp:allmän handling)
    # and the page "Allmän handling" resolve to the same URI
    assert markdown.begrepp_uri("allmän handling") \
        == "https://lagen.nu/begrepp/Allmän_handling"
    assert markdown.begrepp_uri("Ne bis in idem") \
        == "https://lagen.nu/begrepp/Ne_bis_in_idem"


def test_markdown_links_become_concept_runs():
    runs = markdown.to_runs(
        "Se [rättskraft](begrepp:rättskraft) och [res jud.](begrepp:res judicata).")
    assert runs == [
        "Se ",
        {"predicate": "dcterms:references",
         "uri": "https://lagen.nu/begrepp/Rättskraft", "text": "rättskraft"},
        " och ",
        {"predicate": "dcterms:references",
         "uri": "https://lagen.nu/begrepp/Res_judicata", "text": "res jud."},
        "."]


def test_non_link_brackets_stay_literal():
    # a bare [x](y) whose target is not a recognised scheme is left as prose
    # (legal text owns the brackets; the citation engine owns bare refs)
    assert markdown.to_runs("Se punkt [a](nope) här.") == ["Se punkt [a](nope) här."]


def test_markdown_external_links_become_runs():
    # http(s) targets are external link runs; a `)` in the url is %29-escaped
    # in the file and decoded back here
    runs = markdown.to_runs(
        "Se [Avtalslagen 2020](http://www.avtalslagen2020.se/) och "
        "[Spice](http://sv.wikipedia.org/wiki/Spice_(drog%29).")
    links = [r for r in runs if isinstance(r, dict)]
    assert links == [
        {"predicate": "dcterms:references",
         "uri": "http://www.avtalslagen2020.se/", "text": "Avtalslagen 2020"},
        {"predicate": "dcterms:references",
         "uri": "http://sv.wikipedia.org/wiki/Spice_(drog)", "text": "Spice"}]


def test_frontmatter_scalars_and_lists():
    meta, body = markdown.frontmatter(
        "---\ntitle: Foo\nauthor: Bar Baz\n"
        "categories:\n  - Straffrätt\n  - Processrätt\n"
        "aliases: [Foo bar, Quux]\n---\nBrödtext.")
    assert meta == {"title": "Foo", "author": "Bar Baz",
                    "categories": ["Straffrätt", "Processrätt"],
                    "aliases": ["Foo bar", "Quux"]}
    assert body == "Brödtext."


def test_frontmatter_block_list_of_mappings():
    # the Step-4 `guidance:` sources: a block list of `field: value` mappings,
    # continued by deeper-indented fields. A scalar list item whose value is a
    # URL (its `https:` has no following space) must NOT be read as a mapping.
    meta, body = markdown.frontmatter(
        "---\n"
        "annotates: 32023R2854\n"
        "guidance:\n"
        "  - title: Frågor och svar om dataakten\n"
        "    url: https://digital-strategy.ec.europa.eu/faq\n"
        "    pdf: https://ec.europa.eu/doc/108144\n"
        "  - pdf: https://ec.europa.eu/doc/2\n"
        "seealso:\n"
        "  - https://example.org/a\n"
        "---\nBody.")
    assert meta == {
        "annotates": "32023R2854",
        "guidance": [
            {"title": "Frågor och svar om dataakten",
             "url": "https://digital-strategy.ec.europa.eu/faq",
             "pdf": "https://ec.europa.eu/doc/108144"},
            {"pdf": "https://ec.europa.eu/doc/2"}],
        "seealso": ["https://example.org/a"]}
    assert body == "Body."


def test_blocks_split_headings_and_paragraphs():
    assert markdown.blocks("## 1 kap 2 §\n\nFörsta.\nrad två\n\nAndra.") == [
        ("rubrik", 2, "1 kap 2 §"),
        ("stycke", "Första. rad två"),
        ("stycke", "Andra.")]
    # an escaped leading hash is a literal list item / prose, not a heading
    assert markdown.blocks("\\# punkt") == [("stycke", "# punkt")]


def test_heading_fragment():
    assert wiki.heading_fragment("21 kap 1 §") == "K21P1"
    assert wiki.heading_fragment("1 kap. 1 c §") == "K1P1c"
    assert wiki.heading_fragment("7 kap 3 § 2 st") == "K7P3S2"
    assert wiki.heading_fragment("25 kap") == "K25"
    assert wiki.heading_fragment("Lagens innehåll") is None
    # a continuously-numbered law is commented with bare `N §` headings; SFS
    # mints those paragrafs as `P{N}` (chapter dropped), so the anchor must too
    assert wiki.heading_fragment("1 §") == "P1"
    assert wiki.heading_fragment("1 a §") == "P1a"
    assert wiki.heading_fragment("3 § 2 st") == "P3S2"
    # EU acts are commented per article -> the dotted anchor render_eurlex mints
    # from the act's structure (PRD Step 3)
    assert wiki.heading_fragment("Artikel 5") == "5"
    assert wiki.heading_fragment("Artikel 13") == "13"
    assert wiki.heading_fragment("Artikel 3.4") == "3.4"
    assert wiki.heading_fragment("Artikel 5.2 a") == "5.2.a"
    assert wiki.heading_fragment("Artikel 5.2.a") == "5.2.a"
    # recitals are commentable too -> the `#recital-N` anchor
    assert wiki.heading_fragment("Skäl 13") == "recital-13"
    assert wiki.heading_fragment("(13)") == "recital-13"


def test_kommentar_bare_paragraf_headings_anchor_per_paragraf(tmp_path):
    # avtalslagen-shape: a `# N kap` heading followed by bare `## N §` headings.
    # Each § must become its own sektion anchored `P{N}` (matching the statute's
    # continuous numbering), not get lumped under the chapter sektion.
    md = ("---\nannotates: 1915:218\n---\n"
          "# 1 kap. Hur man sluter avtal\n\nKapitelintro.\n\n"
          "## 1 §\n\nOm anbud.\n\n## 2 §\n\nOm svar.\n")
    p = tmp_path / "k.md"
    p.write_text(md)
    art = wiki.kommentar_artifact(str(p))
    sektion_ids = [b["id"] for b in art["body"] if b.get("type") == "sektion"]
    assert sektion_ids == ["K1", "P1", "P2"]
    p1 = next(b for b in art["body"] if b.get("id") == "P1")
    assert p1["text"][0]["uri"] == "https://lagen.nu/1915:218#P1"
    assert any("Om anbud." in r for c in p1["children"]
               for r in c["text"] if isinstance(r, str))


KOMMENTAR_MD = """---
annotates: 2009:400
author: Helena Andersson
categories:
  - Lagar inom allmän förvaltningsrätt
---
## 21 kap 1 §

Bestämmelsen är generell. Se [sekretess](begrepp:sekretess) och NJA 1990 s. 510.
"""

BEGREPP_MD = """---
title: Ne bis in idem
categories:
  - Processrätt
aliases:
  - Dubbelbestraffningsförbudet
---
En princip. Se [rättskraft](begrepp:rättskraft).
"""


def test_kommentar_artifact_anchors_to_statute(tmp_path):
    p = tmp_path / "2009:400.md"
    p.write_text(KOMMENTAR_MD)
    art = wiki.kommentar_artifact(str(p))
    assert art["uri"] == "https://lagen.nu/kommentar/2009:400"
    assert art["annotates"] == "https://lagen.nu/2009:400"
    assert art["author"] == "Helena Andersson"
    section = next(b for b in art["body"] if b.get("type") == "sektion")
    assert section["id"] == "K21P1"
    # the heading links to the statute paragraph (the kommentar->paragraph edge)
    assert section["text"][0]["uri"] == "https://lagen.nu/2009:400#K21P1"
    links = [r for c in section["children"] for r in c["text"]
             if isinstance(r, dict)]
    # prose links to the concept and to the cited case
    assert any(l["uri"] == "https://lagen.nu/begrepp/Sekretess" for l in links)
    assert any(l["uri"] == "https://lagen.nu/dom/nja/1990s510" for l in links)


def test_guidance_block_parses_and_is_removed():
    # the `## Externa länkar` bullet list -> typed guidance items; its section is
    # stripped from the body so it is not also emitted as prose (PRD Step 2)
    body = ("Intro.\n\n## Externa länkar\n"
            "- [CRA FAQ](https://digital-strategy.ec.europa.eu/faq) "
            "— Europeiska kommissionen\n"
            "- [Utkast till vägledning](https://ec.europa.eu/draft) — utkast\n"
            "- inte en länk\n\n## Annat\n\nMer text.")
    sections, rest = markdown.guidance_sections(body)
    # a block before any heading is document-level (owner None, PRD Step 2)
    assert sections == [(None, [
        {"label": "CRA FAQ", "href": "https://digital-strategy.ec.europa.eu/faq",
         "note": "Europeiska kommissionen"},
        {"label": "Utkast till vägledning", "href": "https://ec.europa.eu/draft",
         "note": "utkast"}])]
    # the section (heading + bullets) is gone, the surrounding prose kept
    assert "Externa länkar" not in rest and "CRA FAQ" not in rest
    assert "Intro." in rest and "Mer text." in rest
    # a body with no such heading comes back untouched (losslessness)
    assert markdown.guidance_sections("Bara text.") == ([], "Bara text.")


def test_guidance_sections_attach_to_their_section_heading():
    # a `## Externa länkar` block under a section heading is tagged with that
    # heading as its owner, so it attaches to that article's rail (PRD Step 3)
    body = ("## Artikel 5\n\nProsa om artikel 5.\n\n## Externa länkar\n"
            "- [Vägledning artikel 5](https://ec.europa.eu/art5)\n\n"
            "## Artikel 6\n\nProsa om artikel 6.\n")
    sections, rest = markdown.guidance_sections(body)
    assert sections == [("Artikel 5", [
        {"label": "Vägledning artikel 5", "href": "https://ec.europa.eu/art5"}])]
    # the article headings + prose stay; only the guidance bullets are removed
    assert "## Artikel 5" in rest and "## Artikel 6" in rest
    assert "art5" not in rest


def test_kommentar_per_article_guidance_attaches_to_section(tmp_path):
    # a per-article `## Externa länkar` block under `## Artikel 5` lands on that
    # section node's `guidance`, not the document-level field (PRD Step 3)
    md = ("---\nannotates: 32024R2847\n---\n"
          "## Externa länkar\n"
          "- [Allmän CRA-FAQ](https://ec.europa.eu/faq) — Kommissionen\n\n"
          "## Artikel 5\n\nArtikel 5 handlar om väsentliga krav.\n\n"
          "## Externa länkar\n"
          "- [Vägledning till artikel 5](https://ec.europa.eu/art5)\n")
    p = tmp_path / "32024R2847.md"
    p.write_text(md)
    art = wiki.kommentar_artifact(str(p))
    # the leading block is still the act-level guidance (Step 2)
    assert art["guidance"] == [
        {"label": "Allmän CRA-FAQ", "href": "https://ec.europa.eu/faq",
         "note": "Kommissionen"}]
    section = next(b for b in art["body"] if b.get("id") == "5")
    assert section["guidance"] == [
        {"label": "Vägledning till artikel 5", "href": "https://ec.europa.eu/art5"}]
    # the heading links the commentary to the EU article (the kommentar->article edge)
    assert section["text"][0]["uri"] == "https://lagen.nu/ext/celex/32024R2847#5"


def test_guidance_under_unanchorable_heading_is_rejected(tmp_path):
    # a `## Externa länkar` block under a heading that is not an anchorable
    # article/paragraph/recital must raise, not silently attach its links to the
    # whole document (a mis-numbered heading would otherwise vanish from every rail)
    md = ("---\nannotates: 32024R2847\n---\n"
          "## Bakgrund\n\nEn inledande text.\n\n"
          "## Externa länkar\n"
          "- [Fel plats](https://ec.europa.eu/x)\n")
    p = tmp_path / "32024R2847.md"
    p.write_text(md)
    with pytest.raises(AssertionError, match="not an anchorable"):
        wiki.kommentar_artifact(str(p))


def test_dangling_anchors_flags_mismatched_sections():
    host = {"structure": [
        {"type": "recital", "num": "12", "text": ["…"]},   # no id; anchor recital-12
        {"type": "article", "id": "5", "num": "5", "text": ["…"],
         "children": [{"type": "point", "id": "5.2", "text": ["…"]}]}]}
    komm = {"body": [
        {"type": "sektion", "id": "5"},            # matches article 5
        {"type": "sektion", "id": "5.2"},          # matches the dotted point anchor
        {"type": "sektion", "id": "5.9"},          # not enumerated, base article 5 -> ok
        {"type": "sektion", "id": "recital-12"},   # matches a numbered recital
        {"type": "sektion", "id": "recital-99"},   # no such recital -> dangling
        {"type": "sektion", "id": "13"}]}          # no article 13 -> dangling
    assert wiki.dangling_anchors(komm, host) == ["recital-99", "13"]


def test_kommentar_guidance_celex_host(tmp_path):
    # an annotation whose `annotates:` is a CELEX resolves to the ext/celex act
    # the eurlex source publishes, and its `## Externa länkar` block -> guidance field
    md = ("---\nannotates: 32024R2847\n---\n"
          "## Externa länkar\n"
          "- [CRA Implementation FAQ](https://digital-strategy.ec.europa.eu/faq) "
          "— Europeiska kommissionen\n")
    p = tmp_path / "32024R2847.md"
    p.write_text(md)
    art = wiki.kommentar_artifact(str(p))
    assert art["annotates"] == "https://lagen.nu/ext/celex/32024R2847"
    assert art["uri"] == "https://lagen.nu/kommentar/32024R2847"
    assert art["guidance"] == [
        {"label": "CRA Implementation FAQ",
         "href": "https://digital-strategy.ec.europa.eu/faq",
         "note": "Europeiska kommissionen"}]
    assert art["body"] == []      # the Externa länkar section is not also prose
    # host_uri keeps the SFS form a top-level lagen.nu page
    assert wiki.host_uri("2009:400") == "https://lagen.nu/2009:400"


def test_begrepp_artifact(tmp_path):
    p = tmp_path / "Ne bis in idem.md"
    p.write_text(BEGREPP_MD)
    art = wiki.begrepp_artifact(str(p))
    assert art["uri"] == "https://lagen.nu/begrepp/Ne_bis_in_idem"
    assert art["title"] == "Ne bis in idem"
    assert art["categories"] == ["Processrätt"]
    # redirects -> alternate uris that resolve to this concept (relate-time)
    assert art["aliases"] == ["https://lagen.nu/begrepp/Dubbelbestraffningsförbudet"]
    links = [r for b in art["body"] for r in b["text"] if isinstance(r, dict)]
    assert links[0]["uri"] == "https://lagen.nu/begrepp/Rättskraft"


def test_conversion_is_lossless(tmp_path):
    # the migration's safety property in miniature (the full corpus check is
    # tools/wiki_artifact_diff.py): wikitext -> markdown -> artifact equals the
    # old wikitext -> artifact, modulo the adjudicated normalisations (_norm).
    # Exercises templates, plain/labelled wikilinks, a citation, a leading-#
    # list line, a category and a byline.
    wt = ("{{mall}} En [[rättskraft]] och [[res judicata|res jud.]]. "
          "Se 2 kap 2 § tryckfrihetsförordningen och NJA 1990 s. 510. "
          "Jfr [http://www.avtalslagen2020.se/ Avtalslagen 2020] och "
          "[http://sv.wikipedia.org/wiki/Spice_(drog) Spice].\n\n"
          "# Numrerad punkt med [[sekretess]]\n\n"
          "[[Kategori:Processrätt]]\n''Huvudförfattare: Foo Bar''")
    old = diff.old_begrepp("Begreppet", wt)
    meta, body = conv.convert_page("Begreppet", wt)
    p = tmp_path / "p.md"
    p.write_text(conv.render_file(meta, body))
    new = wiki.begrepp_artifact(str(p))
    assert diff._norm(old) == diff._norm(new)
    # the leading-# list line survived as prose (not swallowed as a heading)
    assert any(r == "# Numrerad punkt med " for b in new["body"]
               for r in b["text"] if isinstance(r, str))


def test_eurlex_guidance_renders_in_document_rail(tmp_path):
    # end-to-end (PRD Step 2 acceptance): authoring a CELEX-hosted annotation with a
    # `## Externa länkar` block puts its links in the EU act's document-level rail
    # panel (key '') -- shown when no single article is in focus, in place of the
    # empty-rail placeholder
    import re
    from accommodanda.lib import render
    ad = tmp_path / "art"
    ad.mkdir()
    act = ad / "act.json"
    act.write_text(json.dumps({
        "uri": "https://lagen.nu/ext/celex/32024R2847",
        "celex": "32024R2847", "doctype": "regulation",
        "title": "Cyberresiliensförordningen", "shortname": "Cyberresiliensförordningen",
        "abbr": "CRA",
        "structure": [{"type": "article", "id": "1", "num": "1", "text": ["Syfte."]}]}))
    md = ("---\nannotates: 32024R2847\n---\n"
          "## Externa länkar\n"
          "- [CRA Implementation FAQ](https://digital-strategy.ec.europa.eu/faq) "
          "— Europeiska kommissionen\n"
          "- [Utkast till vägledning](https://ec.europa.eu/draft) — utkast\n")
    mp = tmp_path / "g.md"
    mp.write_text(md)
    komm = ad / "komm.json"
    komm.write_text(json.dumps(wiki.kommentar_artifact(str(mp))))

    cat = tmp_path / "catalog.sqlite"
    catalog.rebuild(cat, "eurlex", [act])
    catalog.rebuild(cat, "kommentar", [komm])
    con = catalog.connect(cat)
    site = render.Site.from_catalog(con)

    html = render.render_eurlex(json.loads(act.read_text()), site)
    island = json.loads(
        re.search(r'id="lagen-context">(.*?)</script>', html, re.S).group(1))
    panel = island[""]                          # the document-level rail panel
    assert 'class="rail-sec vagledning"' in panel
    assert "Externa länkar" in panel
    assert 'href="https://digital-strategy.ec.europa.eu/faq"' in panel
    assert 'rel="external"' in panel
    assert "Europeiska kommissionen" in panel
    # not emitted as a top-of-body section any more
    assert "<section" not in html or 'class="vagledning"' not in html
    assert "Officiell vägledning" not in html


def test_eurlex_per_article_guidance_and_commentary_render_in_article_rail(tmp_path):
    # end-to-end (PRD Step 3 acceptance): a `## Artikel 5` annotation section with
    # prose + a `## Externa länkar` block shows both in article 5's context rail
    import re
    from accommodanda.lib import render
    ad = tmp_path / "art"
    ad.mkdir()
    act = ad / "act.json"
    # a CELEX with no editorial .ann, so article panels are commentary/guidance
    # only -- hermetic, no real recital layer leaking in
    act.write_text(json.dumps({
        "uri": "https://lagen.nu/ext/celex/32024R9999",
        "celex": "32024R9999", "doctype": "regulation",
        "title": "Testförordningen", "shortname": "Testförordningen",
        "abbr": "TF",
        "structure": [
            {"type": "recital", "num": "12", "text": ["Webbplatser omfattas inte."]},
            {"type": "article", "id": "5", "num": "5", "text": ["Väsentliga krav."]},
            {"type": "article", "id": "6", "num": "6", "text": ["Annat."]}]}))
    md = ("---\nannotates: 32024R9999\n---\n"
          "## Artikel 5\n\n"
          "Bestämmelsen anger de väsentliga kraven på produkter med digitala "
          "element.\n\n"
          "## Externa länkar\n"
          "- [Vägledning till artikel 5](https://ec.europa.eu/art5) — Kommissionen\n\n"
          "## Skäl 12\n\n"
          "Skälet klargör att webbplatser i regel inte är produkter.\n")
    mp = tmp_path / "g.md"
    mp.write_text(md)
    komm = ad / "komm.json"
    komm.write_text(json.dumps(wiki.kommentar_artifact(str(mp))))

    cat = tmp_path / "catalog.sqlite"
    catalog.rebuild(cat, "eurlex", [act])
    catalog.rebuild(cat, "kommentar", [komm])
    con = catalog.connect(cat)
    site = render.Site.from_catalog(con)

    html = render.render_eurlex(json.loads(act.read_text()), site)
    island = json.loads(
        re.search(r'id="lagen-context">(.*?)</script>', html, re.S).group(1))
    panel = island["5"]                         # article 5's context rail
    # the commentary prose
    assert 'class="rail-sec rail-komm"' in panel
    assert "väsentliga kraven" in panel
    # and the per-article external links, the same shape as the act-level block
    assert 'class="rail-sec vagledning"' in panel
    assert 'href="https://ec.europa.eu/art5"' in panel
    assert 'rel="external"' in panel
    assert "Kommissionen" in panel
    # a recital is commentable too -- the comment lands in recital 12's rail, and
    # the recital gets its `#recital-12` anchor even with no editorial .ann present
    assert 'class="rail-sec rail-komm"' in island["recital-12"]
    assert "webbplatser i regel inte" in island["recital-12"]
    assert 'id="recital-12"' in html and 'data-rail="recital-12"' in html
    # article 6 has no annotation -> no rail panel of its own
    assert "6" not in island
    # the article node is tagged so the scrollspy drives the rail to it
    assert 'data-rail="5"' in html


def test_ai_guidance_ann_renders_on_subarticle_and_recital_rails(tmp_path):
    # end-to-end (PRD Step 4 acceptance): the ai-annotate `.ann` sidecar links a
    # FAQ question to *fine-grained* nodes -- two definitions of article 2 and a
    # recital -- not to article 2 as a whole. Each link surfaces in that node's
    # own rail, and the sub-article gets its `2.21` citation anchor even though
    # the act carries no editorial recital layer.
    import re
    from accommodanda.lib import render
    ad = tmp_path / "art"
    ad.mkdir()
    act = ad / "act.json"
    act.write_text(json.dumps({
        "uri": "https://lagen.nu/ext/celex/32024R9998",
        "celex": "32024R9998", "doctype": "regulation",
        "title": "Dataförordningen", "shortname": "Dataförordningen", "abbr": "DF",
        "structure": [
            {"type": "recital", "num": "15", "text": ["Säkerhetsskäl kan begränsa."]},
            {"type": "article", "id": "2", "num": "2", "text": ["Definitioner"],
             "children": [
                {"type": "point", "num": "21", "text": ["datainnehavare: …"]},
                {"type": "point", "num": "22", "text": ["datamottagare: …"]}]},
            {"type": "article", "id": "4", "num": "4", "text": ["Rättigheter"]}]}))
    mp = tmp_path / "g.md"
    mp.write_text("---\nannotates: 32024R9998\n---\n")
    komm = ad / "komm.json"
    komm.write_text(json.dumps(wiki.kommentar_artifact(str(mp))))
    # the AI layer, kept in a .ann sibling of the kommentar artifact
    deflink = {"label": "Frequently Asked Questions – Data Act, question 8",
               "href": "https://ec.europa.eu/doc/108144#page=14",
               "desc": "Who has to share data?", "section": "question 8"}
    reclink = {"label": "Frequently Asked Questions – Data Act, question 9",
               "href": "https://ec.europa.eu/doc/108144#page=15",
               "desc": "What about security concerns?", "section": "question 9"}
    # the .ann keys use the dotted sub-article grammar, matching the node ids render mints
    (ad / "komm.ann").write_text(json.dumps({"guidanceLinks": {
        "2.21": [deflink], "2.22": [deflink], "recital-15": [reclink]}}))

    cat = tmp_path / "catalog.sqlite"
    catalog.rebuild(cat, "eurlex", [act])
    catalog.rebuild(cat, "kommentar", [komm])
    con = catalog.connect(cat)
    site = render.Site.from_catalog(con)

    html = render.render_eurlex(json.loads(act.read_text()), site)
    island = json.loads(
        re.search(r'id="lagen-context">(.*?)</script>', html, re.S).group(1))
    # the definition point 2.21 gets its own (dotted) citation anchor + rail panel
    assert 'id="2.21"' in html and 'data-rail="2.21"' in html
    p21 = island["2.21"]
    assert 'class="rail-sec vagledning"' in p21
    assert 'href="https://ec.europa.eu/doc/108144#page=14"' in p21
    assert "Frequently Asked Questions – Data Act, question 8" in p21  # names source+section
    assert 'class="q">Who has to share data?' in p21           # question as desc
    assert "2.22" in island                                    # the other definition too
    # the recital rides its own rail with the FAQ link
    assert 'class="rail-sec vagledning"' in island["recital-15"]
    assert "What about security concerns?" in island["recital-15"]
    # the link landed on the sub-articles, NOT on article 2 as a whole
    assert "2" not in island


# -- concept synthesis: extracted definitions/keywords + wiki concepts ----------

def test_subject_links_from_nyckelord():
    # a case's nyckelord (metadata, not body) become dcterms:subject concept edges
    art = {"uri": "https://lagen.nu/dom/x",
           "metadata": {"nyckelord": ["Laga förfall", "  ", "Preskription"]}}
    assert catalog.subject_links(art) == [
        (None, {"uri": "https://lagen.nu/begrepp/Laga_förfall",
                "predicate": "dcterms:subject", "text": "Laga förfall"}),
        (None, {"uri": "https://lagen.nu/begrepp/Preskription",
                "predicate": "dcterms:subject", "text": "Preskription"})]


def test_synthesize_concepts_mints_stubs_for_unauthored(tmp_path):
    ad = tmp_path / "art"
    ad.mkdir()
    case = ad / "case.json"
    case.write_text(json.dumps({
        "uri": "https://lagen.nu/dom/nja/2009s796",
        "metadata": {"nyckelord": ["Laga förfall", "Preskription"]},
        "structure": [{"type": "stycke", "text": ["HD prövar frågan."]}]}))
    law = ad / "law.json"
    law.write_text(json.dumps({
        "uri": "https://lagen.nu/1962:700",
        "metadata": {"properties": {"dcterms:title": "Brottsbalk"}},
        "structure": [{"type": "paragraf", "id": "K1P1", "text": [
            "Den som med ",
            {"uri": "https://lagen.nu/begrepp/Uppsåt",
             "predicate": "dcterms:subject", "text": "uppsåt"}, " dödar. Se ",
            # a malformed extracted "term" -> must be filtered out, not stubbed
            {"uri": "https://lagen.nu/begrepp/*/k/_utjämningsbelopp",
             "predicate": "dcterms:subject", "text": "*/k/"}, "."]}]}))
    concept = ad / "concept.json"      # a wiki-authored concept (Preskription)
    concept.write_text(json.dumps({
        "uri": "https://lagen.nu/begrepp/Preskription", "type": "begrepp",
        "title": "Preskription", "body": [{"type": "stycke", "text": ["…"]}]}))
    cat = tmp_path / "catalog.sqlite"
    catalog.rebuild(cat, "dv", [case])
    catalog.rebuild(cat, "sfs", [law])
    catalog.rebuild(cat, "begrepp", [concept])
    con = catalog.connect(cat)

    # Laga förfall (nyckelord) + Uppsåt (sfs defined term) get stubs;
    # Preskription is wiki-authored and the malformed term is filtered out
    assert catalog.synthesize_concepts(con) == 2
    assert catalog.synthesize_concepts(con) == 0          # idempotent

    docs = {r[0]: r[1] for r in con.execute(
        "SELECT uri, path FROM documents WHERE source = 'begrepp'")}
    assert docs["https://lagen.nu/begrepp/Laga_förfall"] == ""   # stub: no artifact
    assert docs["https://lagen.nu/begrepp/Uppsåt"] == ""
    assert docs["https://lagen.nu/begrepp/Preskription"] != ""   # authored
    assert "https://lagen.nu/begrepp/*/k/_utjämningsbelopp" not in docs  # garbage

    # the concept's page now shows what tags / defines it (its inbound)
    assert [r[0] for r in catalog.inbound(
        con, "https://lagen.nu/begrepp/Laga_förfall")] == [
        "https://lagen.nu/dom/nja/2009s796"]
    assert [r[0] for r in catalog.inbound(
        con, "https://lagen.nu/begrepp/Uppsåt")] == ["https://lagen.nu/1962:700"]


# -- Step 4: AI guidance linker (accommodanda.wiki.annotate) --------------------

_HOST = {"structure": [
    {"type": "recital", "num": "15", "text": ["Skäl om säkerhet och skydd."]},
    {"type": "article", "num": "2", "id": "2", "text": ["Artikel 2 – Definitioner"],
     "children": [
        {"type": "point", "num": "21", "text": ["datainnehavare: en fysisk person …"]},
        {"type": "point", "num": "22", "text": ["datamottagare: en fysisk person …"]}]},
    {"type": "article", "num": "4", "id": "4", "text": ["Artikel 4 – Rättigheter"]}]}


def test_act_map_lists_articles_subarticles_and_recitals():
    # the map is fine-grained: whole articles, sub-articles (definitions points, in
    # the dotted grammar) and recitals, each led by the exact anchor token the
    # model must copy back
    act, anchors = annotate.act_map(_HOST)
    assert anchors == {"recital-15", "2", "2.21", "2.22", "4"}
    assert act.splitlines() == [
        "[recital-15] Skäl om säkerhet och skydd.",
        "[2] Artikel 2 – Definitioner",
        "[2.21] datainnehavare: en fysisk person …",
        "[2.22] datamottagare: en fysisk person …",
        "[4] Artikel 4 – Rättigheter"]


def test_guidance_validate_rejects_hallucinated_target():
    anchors = {"recital-15", "2", "2.21", "2.22", "4"}
    # a question about two definitions links to exactly those sub-articles + a
    # recital -- all valid fine-grained targets
    ok = ('{"links": [{"title": "Who shares", "section": "question 8",'
          ' "targets": ["2.21", "2.22", "recital-15"]}]}')
    assert annotate._validate(ok, anchors) == [
        {"title": "Who shares", "section": "question 8",
         "targets": ["2.21", "2.22", "recital-15"]}]
    # a target not in the act is rejected (fed back to the model on retry) so a
    # hallucinated anchor never reaches the .ann -- ValueError, not assert,
    # because the retry loop load-bears on the raise (-O strips asserts)
    with pytest.raises(ValueError, match="not in the act"):
        annotate._validate(
            '{"links": [{"title": "X", "targets": ["2.99"]}]}', anchors)
    # a link with no targets is rejected too
    with pytest.raises(ValueError, match="no targets"):
        annotate._validate('{"links": [{"title": "X", "targets": []}]}', anchors)


def test_guidance_page_located_by_title_not_model_count():
    # the page is found by matching the (alnum-normalised) title in the page text,
    # so a straight quote matches the PDF's curly one and the model's own page
    # count is never trusted
    text = ("[Sida 1]\nIntro text\n\n"
            "[Sida 2]\n4. Which data are in scope? Several factors…\n\n"
            "[Sida 3]\nMore about users’ rights")
    pages = annotate._pages(text)
    assert annotate._page_of(pages, "Which data are in scope?") == 2
    assert annotate._page_of(pages, "users’ rights") == 3   # curly vs given
    assert annotate._page_of(pages, "nowhere to be found") is None


def test_source_link_label_names_source_and_section_desc_is_question():
    source = {"pdf": "https://ec.europa.eu/doc/108144",
              "title": "Frågor och svar om dataakten"}
    # the link text names the guidance document + its own section reference; the
    # section title (question) follows as `desc`. The model echoed the enumerator
    # into the title -> it must not double the section reference.
    item = annotate._source_link(
        source,
        {"title": "25. Does a data holder have to share data?",
         "section": "question 25"}, 14)
    assert item == {
        "label": "Frågor och svar om dataakten, question 25",
        "href": "https://ec.europa.eu/doc/108144#page=14",
        "desc": "Does a data holder have to share data?",
        "section": "question 25"}
    # no page located -> no #page anchor; no section -> label is just the source
    bare = annotate._source_link(source, {"title": "Bakgrund"}, None)
    assert bare == {"label": "Frågor och svar om dataakten",
                    "href": "https://ec.europa.eu/doc/108144",
                    "desc": "Bakgrund"}


def test_canonicalize_folds_inflected_variant_to_wiki_base(tmp_path):
    from accommodanda.lib import render
    ad = tmp_path / "art"
    ad.mkdir()
    # an SFS law whose defined term is the inflected form "Näringsidkarna"
    law = ad / "law.json"
    law.write_text(json.dumps({
        "uri": "https://lagen.nu/2018:1217",
        "metadata": {"properties": {"dcterms:title": "Paketreselag"}},
        "structure": [{"type": "paragraf", "id": "K1P5", "text": [
            "Med ", {"uri": "https://lagen.nu/begrepp/Näringsidkarna",
                     "predicate": "dcterms:subject", "text": "näringsidkarna"},
            " avses …"]}]}))
    # the wiki concept page, in base form
    concept = ad / "c.json"
    concept.write_text(json.dumps({
        "uri": "https://lagen.nu/begrepp/Näringsidkare", "type": "begrepp",
        "title": "Näringsidkare", "body": [{"type": "stycke", "text": ["…"]}]}))
    cat = tmp_path / "catalog.sqlite"
    catalog.rebuild(cat, "sfs", [law])
    catalog.rebuild(cat, "begrepp", [concept])
    con = catalog.connect(cat)

    assert catalog.canonicalize_concepts(con) == 1          # one variant folded
    assert catalog.concept_aliases(con) == {
        "https://lagen.nu/begrepp/Näringsidkarna":
        "https://lagen.nu/begrepp/Näringsidkare"}
    # the link target is remapped, so the base concept's inbound shows the law
    assert [r[0] for r in catalog.inbound(
        con, "https://lagen.nu/begrepp/Näringsidkare")] == [
        "https://lagen.nu/2018:1217"]
    # render: the law's inflected term link resolves to the canonical page (live),
    # while still showing the inflected surface text
    site = render.Site.from_catalog(con)
    html = render.render_runs([{"uri": "https://lagen.nu/begrepp/Näringsidkarna",
                                "predicate": "dcterms:subject",
                                "text": "näringsidkarna"}], site)
    assert 'class="noref"' not in html          # resolved -> live, not muted
    assert "näringsidkarna</a>" in html         # original surface text shown
    assert "Näringsidkare" in html              # links to the canonical page


def test_redirect_alias_resolves_old_name_to_concept(tmp_path):
    # a begrepp's `aliases` (old MediaWiki redirect names) fold onto it at
    # canonicalize: a link to the old name resolves to the concept and its
    # inbound attaches there (O3 -- redirects preserved as aliases)
    ad = tmp_path / "art"
    ad.mkdir()
    concept = ad / "c.json"
    concept.write_text(json.dumps({
        "uri": "https://lagen.nu/begrepp/Uppsåt", "type": "begrepp",
        "title": "Uppsåt", "aliases": ["https://lagen.nu/begrepp/Dolus"],
        "body": [{"type": "stycke", "text": ["…"]}]}))
    law = ad / "law.json"
    law.write_text(json.dumps({
        "uri": "https://lagen.nu/1962:700",
        "metadata": {"properties": {"dcterms:title": "Brottsbalk"}},
        "structure": [{"type": "paragraf", "id": "K1P2", "text": [
            "Med ", {"uri": "https://lagen.nu/begrepp/Dolus",
                     "predicate": "dcterms:subject", "text": "dolus"},
            " avses uppsåt."]}]}))
    cat = tmp_path / "catalog.sqlite"
    catalog.rebuild(cat, "begrepp", [concept])
    catalog.rebuild(cat, "sfs", [law])
    con = catalog.connect(cat)

    catalog.canonicalize_concepts(con)
    assert catalog.concept_aliases(con)["https://lagen.nu/begrepp/Dolus"] \
        == "https://lagen.nu/begrepp/Uppsåt"
    assert [r[0] for r in catalog.inbound(
        con, "https://lagen.nu/begrepp/Uppsåt")] == ["https://lagen.nu/1962:700"]


def test_definition_links_promote_swedish_eu_terms_to_begrepp():
    swe = {"lang": "swe", "structure": [
        {"type": "article", "id": "2", "children": [
            {"type": "point", "id": "2.a", "defines": "ränta", "text": ["…"]},
            {"type": "point", "id": "2.b", "defines": "royalties", "text": ["…"]}]}]}
    assert catalog.definition_links(swe) == [
        ("2.a", {"uri": "https://lagen.nu/begrepp/Ränta",
                 "predicate": "dcterms:subject", "text": "ränta"}),
        ("2.b", {"uri": "https://lagen.nu/begrepp/Royalties",
                 "predicate": "dcterms:subject", "text": "royalties"})]
    # the English manifestation's terms are not Swedish concepts -> excluded
    eng = {"lang": "eng", "structure": [
        {"type": "point", "id": "2.a", "defines": "interest", "text": ["…"]}]}
    assert catalog.definition_links(eng) == []
