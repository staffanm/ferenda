"""Regression coverage for the trilingual ECHR appendix in SFS 1994:1219."""

import json
from pathlib import Path

from accommodanda.lib import catalog, render
from accommodanda.sfs import parse_sfs_source
from accommodanda.sfs.model import (
    Bilaga,
    Konventionsartikel,
    Konventionsavdelning,
    Konventionsbilaga,
)
from accommodanda.sfs.nf import to_normalform

FIXTURE = Path(__file__).parent / "files" / "sfs" / "echr-appendix.txt"


def _document():
    text = "1 § Denna konvention ska gälla som lag.\n\nBilaga\n\n" + \
        FIXTURE.read_text()
    return parse_sfs_source({"fulltext": {"forfattningstext": text}}, "1994:1219")


def test_echr_appendix_aligns_languages_instruments_sections_and_articles():
    doc = _document()
    assert doc.children[0].ordinal == "1"
    appendix = next(node for node in doc.children if isinstance(node, Bilaga))
    parallel = appendix.children[0]
    assert isinstance(parallel, Konventionsbilaga)
    assert [(item.nummer, item.protokoll, item.uri) for item in parallel.instruments] == [
        ("005", None, "https://lagen.nu/ext/coe/005"),
        ("009", "1", "https://lagen.nu/ext/coe/009"),
    ]

    convention = parallel.instruments[0]
    assert [(type(node), node.ordinal) for node in convention.children] == [
        (Konventionsartikel, "1"),
        (Konventionsavdelning, "I"),
        (Konventionsartikel, "2"),
    ]
    section = convention.children[1]
    assert section.rubriker == {
        "en": "SECTION I - RIGHTS AND FREEDOMS",
        "fr": "TITRE I - DROITS ET LIBERTÉS",
        "sv": "AVDELNING I - RÄTTIGHETER OCH FRIHETER",
    }
    article = convention.children[2]
    assert article.rubriker["sv"] == "Artikel 2 - Rätt till livet"
    assert article.texter[0].texter["en"] == \
        "Everyone's right to life shall be protected by law."


def test_echr_appendix_artifact_and_rendering_keep_each_paragraph_in_one_row(tmp_path):
    art = to_normalform(_document(), "1994:1219", suppress_temporal=False)
    appendix = art["structure"][1]
    assert appendix["type"] == "bilaga"
    parallel = appendix["children"][1]
    assert parallel["type"] == "konventionsbilaga"
    assert parallel["languages"] == ["en", "fr", "sv"]
    assert parallel["children"][0]["id"] == "B1I005"
    article = parallel["children"][0]["children"][2]
    assert article["id"] == "B1I005A2"
    assert article["uri"] == "https://lagen.nu/ext/coe/005#A2"
    assert [version["language"] for version in article["versions"]] == \
        ["en", "fr", "sv"]
    assert [version["language"] for version in article["paragraphs"][0]["versions"]] \
        == ["en", "fr", "sv"]

    law = tmp_path / "law.json"
    law.write_text(json.dumps(art))
    treaties = []
    for number in ("005", "009"):
        path = tmp_path / (number + ".json")
        path.write_text(json.dumps({
            "uri": "https://lagen.nu/ext/coe/" + number,
            "number": number,
            "identifier": "ETS No. " + number,
            "doctype": "treaty",
            "title": "Treaty " + number,
            "structure": [],
        }))
        treaties.append(path)
    database = tmp_path / "catalog.sqlite"
    catalog.rebuild(database, "sfs", [law])
    catalog.rebuild(database, "coe", treaties)
    site = render.Site.from_catalog(catalog.connect(database))
    html = render.render_sfs(art, site)

    assert 'class="gr-root parallel-appendix"' in html
    assert html.count('class="konvention-article"') == 3
    assert html.count('class="konvention-row konvention-paragraph"') == 5
    assert '<section class="konvention-article" id="B1I005A2">' in html
    assert all('lang="%s"' % language in html for language in ("en", "fr", "sv"))
    assert 'href="/coe/005"' in html
    assert 'href="/coe/005#A2"' in html

    css = render.ASSETS.joinpath("style.css").read_text()
    assert "repeat(3, minmax(0, 1fr))" in css
    assert "min-width: 54rem" not in css
