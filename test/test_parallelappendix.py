"""The parallel-text appendix parser produces *a* valid, aligned parse.

These check that a bi-/tri-lingual convention appendix parses into a
`Konventionsbilaga` whose languages line up — not any particular byte-for-byte
shape. See accommodanda/sfs/parallelappendix.md for scope and known gaps.
"""
import json

import pytest

from accommodanda.lib import catalog, render
from accommodanda.lib.render import LANGUAGE_LABELS
from accommodanda.sfs import parallelappendix as pa
from accommodanda.sfs import parse_sfs_source
from accommodanda.sfs.model import (
    Bilaga,
    Konventionsartikel,
    Konventionsavdelning,
    Konventionsbilaga,
)
from accommodanda.sfs.nf import to_normalform

FIXTURE = "test/files/sfs/echr-appendix.txt"
LAYOUT_FIXTURE = "test/files/sfs/parallelappendix-layout.txt"


def _fixture_text():
    with open(FIXTURE, encoding="utf-8") as fp:
        return "1 § Denna konvention ska gälla som lag.\n\nBilaga\n\n" + fp.read()


def _layout_fixture_text():
    with open(LAYOUT_FIXTURE, encoding="utf-8") as fp:
        return "1 § Överenskommelsen ska gälla som lag.\n\nBilaga\n\n" + fp.read()


def test_fixture_parses_into_an_aligned_parallel_corpus():
    result = pa.parse(_fixture_text())
    assert result is not None
    _, bilaga = result
    assert isinstance(bilaga, Konventionsbilaga)
    assert bilaga.languages == ("en", "fr", "sv")
    assert bilaga.instruments
    for instrument in bilaga.instruments:
        articles = [c for c in instrument.children
                    if isinstance(c, Konventionsartikel)]
        assert articles, "every instrument has at least one article"
        for article in articles:
            # headings and every aligned paragraph carry all three languages
            assert set(article.rubriker) == {"en", "fr", "sv"}
            for stycke in article.texter:
                assert set(stycke.texter) == {"en", "fr", "sv"}
        for division in instrument.children:
            if isinstance(division, Konventionsavdelning):
                assert set(division.rubriker) == {"en", "fr", "sv"}


def test_base_convention_keeps_its_title_and_preamble():
    # The formal title and the "have agreed as follows" recital precede Article 1
    # and must survive parsing, not be discarded with the pre-Article-1 matter.
    _, bilaga = pa.parse(_fixture_text())
    base, protocol = bilaga.instruments
    assert base.protokoll is None
    assert base.rubriker["en"] == \
        "Convention for the Protection of Human Rights and Fundamental Freedoms"
    assert base.rubriker["fr"].startswith(
        "Convention de sauvegarde des Droits de l'Homme")
    assert base.rubriker["sv"].startswith(
        "Europeiska konventionen om skydd för de mänskliga")
    assert base.ingresser, "the base convention's preamble is kept as ingress"
    assert base.ingresser[0].texter["en"] == \
        "The governments signatory hereto have agreed as follows:"
    assert base.ingresser[0].texter["sv"] == \
        "Undertecknade regeringar har kommit överens om följande."
    # the first (unnumbered) protocol is numbered 1; its heading is its title
    assert protocol.protokoll == "1"
    assert protocol.rubriker["en"].startswith("Protocol to the Convention")
    assert protocol.ingresser[0].texter["en"] == \
        "The parties have agreed as follows:"


def test_projection_anchors_instruments_and_resolves_treaty_uris():
    doc = parse_sfs_source(
        {"fulltext": {"forfattningstext": _fixture_text()}}, "1994:1219")
    appendix = next(node for node in doc.children if isinstance(node, Bilaga))
    assert any(isinstance(child, Konventionsbilaga)
               for child in appendix.children)

    artifact = to_normalform(doc, "1994:1219", suppress_temporal=False)
    projected = next(
        child for node in artifact["structure"] if node["type"] == "bilaga"
        for child in node.get("children", [])
        if child["type"] == "konventionsbilaga")
    assert projected["languages"] == ["en", "fr", "sv"]
    # the base convention anchors at the bare bilaga fragment, the first protocol
    # at #B1P1; incorporates.json resolves each to its CoE treaty URI
    base, protocol = projected["children"]
    assert (base["id"], base["uri"]) == ("B1", "https://lagen.nu/ext/coe/005")
    assert (protocol["id"], protocol["uri"]) == \
        ("B1P1", "https://lagen.nu/ext/coe/009")
    article = next(child for child in base["children"]
                   if child["type"] == "konventionsartikel"
                   and child["ordinal"] == "2")
    assert article["id"] == "B1A2"
    assert article["uri"] == "https://lagen.nu/ext/coe/005#A2"


def test_uncurated_statute_gets_anchors_but_no_treaty_uris():
    # A statute absent from incorporates.json still anchors its instruments
    # structurally (#B1, #B1P1), but mints no treaty URIs.
    doc = parse_sfs_source(
        {"fulltext": {"forfattningstext": _fixture_text()}}, "1994:9999")
    artifact = to_normalform(doc, "1994:9999", suppress_temporal=False)
    projected = next(
        child for node in artifact["structure"] if node["type"] == "bilaga"
        for child in node.get("children", [])
        if child["type"] == "konventionsbilaga")
    assert [(inst["id"], inst["uri"]) for inst in projected["children"]] == [
        ("B1", None), ("B1P1", None)]


def test_ordinary_statute_is_not_parsed_as_parallel():
    # No Bilaga -> not a parallel corpus -> parse() declines, pipeline flat-parses
    assert pa.parse("1 § En helt vanlig paragraf utan bilaga.\n") is None


def test_layout_noise_is_normalised_without_losing_alignment():
    result = pa.parse(_layout_fixture_text())
    assert result is not None
    _, bilaga = result
    assert bilaga.languages == ("en", "sv", "tr")
    assert set(bilaga.languages) <= LANGUAGE_LABELS.keys()
    assert len(bilaga.instruments) == 1

    children = bilaga.instruments[0].children
    articles = [child for child in children
                if isinstance(child, Konventionsartikel)]
    assert [article.ordinal for article in articles] == ["1", "2", "3", "4"]
    assert articles[0].rubriker["en"].endswith("Article I")
    assert articles[1].rubriker == {
        "en": "Article II",
        "sv": "Artikel II",
        "tr": "Madde 2",
    }
    assert "Article 4 of the separate" in " ".join(
        paragraph.texter["en"] for paragraph in articles[1].texter)
    first_nested = next(
        paragraph.texter for paragraph in articles[1].texter
        if paragraph.texter["en"].startswith("(i)"))
    assert first_nested == {
        "en": "(i) the national authority; and",
        "sv": "1) den statliga myndigheten, och",
        "tr": "1) ulusal makam ve",
    }
    second_nested = next(
        paragraph.texter for paragraph in articles[1].texter
        if paragraph.texter["en"].startswith("(ii)"))
    assert second_nested == {
        "en": "(ii) the municipal authority.",
        "sv": "2) den kommunala myndigheten.",
        "tr": "2) belediye makamı.",
    }
    assert articles[2].rubriker["en"] == "Article III – Taxes covered"

    divisions = [child for child in children
                 if isinstance(child, Konventionsavdelning)]
    assert divisions[0].rubriker == {
        "en": "PART I GENERAL RULES",
        "sv": "AVDELNING I ALLMÄNNA REGLER",
        "tr": "BÖLÜM I GENEL KURALLAR",
    }
    assert divisions[1].rubriker == {
        "en": "PART II SPECIAL RULES",
        "sv": "",
        "tr": "BÖLÜM II ÖZEL KURALLAR",
    }
    assert LANGUAGE_LABELS["de"] == "Deutsch"
    assert pa.ordinal("XXXIV") == "34"


def test_article_mismatch_remains_a_hard_failure():
    broken = _layout_fixture_text().replace("Madde 4\n", "Madde 5\n", 1)
    with pytest.raises(pa.AppendixMisaligned,
                       match="article sequence differs"):
        pa.parse(broken)


def test_render_lays_the_appendix_out_in_one_column_per_language(tmp_path):
    doc = parse_sfs_source(
        {"fulltext": {"forfattningstext": _fixture_text()}}, "1994:1219")
    art = to_normalform(doc, "1994:1219", suppress_temporal=False)
    law = tmp_path / "law.json"
    law.write_text(json.dumps(art))
    treaties = []
    for number in ("005", "009"):
        path = tmp_path / (number + ".json")
        path.write_text(json.dumps({
            "uri": "https://lagen.nu/ext/coe/" + number,
            "number": number, "identifier": "ETS No. " + number,
            "doctype": "treaty", "title": "Treaty " + number, "structure": []}))
        treaties.append(path)
    database = tmp_path / "catalog.sqlite"
    catalog.rebuild(database, "sfs", [law])
    catalog.rebuild(database, "coe", treaties)
    site = render.Site.from_catalog(catalog.connect(database))
    html = render.render_sfs(art, site)

    # one grid column per detected language, and every language labelled
    assert 'class="konventionsbilaga" style="--n-languages: 3"' in html
    assert all('lang="%s"' % language in html for language in ("en", "fr", "sv"))
    for label in ("English", "Français", "Svenska"):
        assert label in html
    # the resolved treaty identity renders citable /coe links on the appendix
    assert 'href="/coe/005"' in html
    assert 'href="/coe/005#A2"' in html
    # each article renders as its own aligned section
    projected = next(
        child for node in art["structure"] if node["type"] == "bilaga"
        for child in node.get("children", [])
        if child["type"] == "konventionsbilaga")
    articles = [child for instrument in projected["children"]
                for child in instrument["children"]
                if child["type"] == "konventionsartikel"]
    assert articles
    assert html.count('class="konvention-article"') == len(articles)

    # the grid column count is driven by the inline --n-languages variable, so
    # the same rule serves the 2- and 3-language appendices without a min width
    css = render.ASSETS.joinpath("style.css").read_text()
    assert "repeat(var(--n-languages, 3), minmax(0, 1fr))" in css
    assert "min-width: 54rem" not in css
