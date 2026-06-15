"""Tests for the MediaWiki commentary/concept parsing
(accommodanda.lib.wikitext + accommodanda.wiki.parse)."""

from accommodanda.lib import wikitext
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
