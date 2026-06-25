"""Tests for the MediaWiki commentary/concept parsing
(accommodanda.lib.wikitext + accommodanda.wiki.parse) and the concept-synthesis
that unifies extracted definitions/keywords with the wiki concepts."""

import json

from accommodanda.lib import catalog, wikitext
from accommodanda.wiki import parse as wiki


def test_begrepp_uri_ucfirst_and_underscores():
    # MediaWiki upper-cases the first letter, so [[allmän handling]] and the
    # page "Allmän handling" resolve to the same URI
    assert wikitext.begrepp_uri("allmän handling") \
        == "https://lagen.nu/begrepp/Allmän_handling"
    assert wikitext.begrepp_uri("Ne bis in idem") \
        == "https://lagen.nu/begrepp/Ne_bis_in_idem"


def test_wikilinks_become_concept_runs():
    runs = wikitext.to_runs("Se [[rättskraft]] och [[res judicata|res jud.]].")
    assert runs == [
        "Se ",
        {"predicate": "dcterms:references",
         "uri": "https://lagen.nu/begrepp/Rättskraft", "text": "rättskraft"},
        " och ",
        {"predicate": "dcterms:references",
         "uri": "https://lagen.nu/begrepp/Res_judicata", "text": "res jud."},
        "."]


def test_blocks_strip_byline_and_category():
    wt = ("''Huvudförfattare Foo Bar''\n\n== 1 kap 2 § ==\n\n"
          "Brödtext här.\n\n[[Kategori:Straffrätt]]")
    assert wikitext.blocks(wt) == [("rubrik", 2, "1 kap 2 §"),
                                   ("stycke", "Brödtext här.")]
    assert wikitext.author(wt) == "Foo Bar"
    assert wikitext.categories(wt) == ["Straffrätt"]


def test_heading_fragment():
    assert wiki.heading_fragment("21 kap 1 §") == "K21P1"
    assert wiki.heading_fragment("1 kap. 1 c §") == "K1P1c"
    assert wiki.heading_fragment("7 kap 3 § 2 st") == "K7P3S2"
    assert wiki.heading_fragment("25 kap") == "K25"
    assert wiki.heading_fragment("Lagens innehåll") is None


KOMMENTAR_XML = """<page xmlns="http://www.mediawiki.org/xml/export-0.10/">
<title>SFS/2009:400</title><ns>0</ns>
<revision><text>''Huvudförfattare Helena Andersson''

== 21 kap 1 § ==

Bestämmelsen är generell. Se [[sekretess]] och NJA 1990 s. 510.

[[Kategori:Lagar inom allmän förvaltningsrätt]]</text></revision></page>"""

BEGREPP_XML = """<page xmlns="http://www.mediawiki.org/xml/export-0.10/">
<title>Ne bis in idem</title><ns>0</ns>
<revision><text>En princip. Se [[rättskraft]].

[[Kategori:Processrätt]]</text></revision></page>"""


def test_kommentar_artifact_anchors_to_statute(tmp_path):
    p = tmp_path / "k.xml"
    p.write_text(KOMMENTAR_XML)
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


def test_begrepp_artifact(tmp_path):
    p = tmp_path / "b.xml"
    p.write_text(BEGREPP_XML)
    art = wiki.begrepp_artifact(str(p))
    assert art["uri"] == "https://lagen.nu/begrepp/Ne_bis_in_idem"
    assert art["title"] == "Ne bis in idem"
    assert art["categories"] == ["Processrätt"]
    links = [r for b in art["body"] for r in b["text"] if isinstance(r, dict)]
    assert links[0]["uri"] == "https://lagen.nu/begrepp/Rättskraft"


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
