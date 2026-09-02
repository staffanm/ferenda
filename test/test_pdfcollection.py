"""The browser-owned, stateless multi-document PDF collection."""

import json
import subprocess
import time
from datetime import date

import lxml.html
import pytest
from fastapi.testclient import TestClient

from ferenda import config
from ferenda.api import app as api
from ferenda.api import pdfcollection, pdfjob
from ferenda.lib import compress


def _page(identifier, title, *, preamble=False, amendments=False):
    intro = ('<p class="visa">med beaktande av fördraget</p>'
             '<p class="recital" id="recital-1">(1) Ett skäl.</p>'
             '<p class="preamble">HÄRIGENOM FÖRESKRIVS FÖLJANDE.</p>'
             if preamble else "")
    register = ('<section class="andringar"><h2 id="AN">Ändringar</h2>'
                '<article class="andring" id="L2"><h2>Ändring</h2>'
                '<ul><li><a href="/official.pdf">Tryckt format</a></li></ul>'
                '<p>Övergångsbestämmelse.</p></article></section>'
                if amendments else "")
    return """<!doctype html><html lang="sv"><head><title>{title}</title>
<link rel="stylesheet" href="/style.css"></head><body class="gr-root">
<header class="masthead"></header><div class="gr-body">
<aside class="toc-col"><nav class="toc"><div class="toc-list">
<a href="#top" class="lvl1 toc-top">{identifier}</a>
<a href="#R1" class="lvl1">Första kapitlet</a>
<a href="#R2" class="lvl1">Andra kapitlet</a>
<a href="#R2A" class="lvl2">Ett underavsnitt</a>
<a href="#R3" class="lvl1">Tredje kapitlet</a>
{register_toc}</div></nav></aside><main class="gr-main">
<header class="frontmatter" id="top"><div class="eyebrow">{identifier}</div>
<h1>{title}</h1><dl class="meta"><dt>Titel</dt><dd>{title}</dd></dl></header>
{intro}<h2 class="rubrik" id="R1">Första kapitlet</h2><p>Första texten.</p>
<h2 class="rubrik" id="R2">Andra kapitlet</h2><p>Andra texten.</p>
<h3 class="rubrik" id="R2A">Ett underavsnitt</h3><p>Undertext.</p>
<h2 class="rubrik" id="R3">Tredje kapitlet</h2><p>Tredje texten.</p>
{register}</main><aside class="rail"></aside></div></body></html>""".format(
        identifier=identifier, title=title, intro=intro, register=register,
        register_toc=('<a href="#AN" class="lvl1">Ändringar</a>'
                      if amendments else ""))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    pdfjob._jobs.clear()
    pdfjob._by_key.clear()
    compress.write_text(tmp_path / "generated" / "1998:1.html",
                        _page("SFS 1998:1", "Första lagen", amendments=True))
    compress.write_text(tmp_path / "generated" / "1998:2.html",
                        _page("(EU) 1998/2", "Andra rättsakten", preamble=True))
    return TestClient(api.app)


def _manifest(**changes):
    values = {"title": "Provsamling", "subtitle": "Två dokument",
              "items": [{"path": "/1998:1"},
                        {"path": "/1998:2", "start": "direct"}]}
    values.update(changes)
    return pdfcollection.CollectionManifest(**values)


def test_inspect_reports_options_and_excludes_amendment_outline(client):
    documents = pdfcollection.inspect(["/1998:1", "/1998:2"])
    assert [(doc["label"], doc["title"]) for doc in documents] == [
        ("SFS 1998:1", "Första lagen"),
        ("(EU) 1998/2", "Andra rättsakten")]
    assert documents[0]["amendments"] is True
    assert documents[1]["preamble"] is True
    assert [entry["id"] for entry in documents[0]["outline"]] == [
        "R1", "R2", "R2A", "R3"]


def test_selection_keeps_separate_heading_subtree_and_frontmatter(client):
    text = compress.read_text(pdfcollection._resolved_page("/1998:1"))
    doc = lxml.html.document_fromstring(text, parser=pdfcollection.pdf.PARSER)
    pdfcollection._select_sections(doc, ["R2"])
    assert doc.get_element_by_id("top").findtext("h1") == "Första lagen"
    assert doc.get_element_by_id("R1", None) is None
    assert doc.get_element_by_id("R2") is not None
    assert doc.get_element_by_id("R2A") is not None
    assert doc.get_element_by_id("R3", None) is None
    assert "Andra texten" in doc.text_content()
    assert "Tredje texten" not in doc.text_content()


def test_selection_prunes_sibling_chapters_inside_a_shared_division():
    doc = lxml.html.document_fromstring("""<html><head><title>Lag</title></head>
<body><nav><div class="toc-list"><a href="#K1">1 kap.</a>
<a href="#K2">2 kap.</a><a href="#K3">3 kap.</a>
<a href="#K3R1">Definitioner</a><a href="#K4">4 kap.</a></div></nav>
<main class="gr-main"><header class="frontmatter"><h1>Lag</h1></header>
<div id="dokument"><section class="avdelning">
<h2 id="K1">1 kap.</h2><p>Första kapitlet.</p>
<h2 id="K2">2 kap.</h2><p>Andra kapitlet.</p>
<h2 id="K3">3 kap.</h2><p>Tredje kapitlet.</p>
<h3 id="K3R1">Definitioner</h3><p>En tabell.</p></section>
<section class="avdelning"><h2 id="K4">4 kap.</h2><p>Fjärde.</p></section>
</div></main></body></html>""")
    pdfcollection._select_sections(doc, ["K3"])
    assert doc.get_element_by_id("K1", None) is None
    assert doc.get_element_by_id("K2", None) is None
    assert doc.get_element_by_id("K3") is not None
    assert doc.get_element_by_id("K3R1") is not None
    assert doc.get_element_by_id("K4", None) is None


def test_selection_keeps_two_nonadjacent_sections(client):
    text = compress.read_text(pdfcollection._resolved_page("/1998:2"))
    doc = lxml.html.document_fromstring(text, parser=pdfcollection.pdf.PARSER)
    pdfcollection._select_sections(doc, ["R1", "R3"])
    assert doc.get_element_by_id("R1") is not None
    assert doc.get_element_by_id("R2", None) is None
    assert doc.get_element_by_id("R3") is not None


def test_assemble_namespaces_documents_and_prints_only_document_toc(client):
    doc = pdfcollection.assemble(_manifest(), date(2026, 8, 20))
    assert doc.find_class("collection-cover")[0].findtext(
        "p[@class='collection-generated']") == \
        "Genererat från lagen.nu 20 augusti 2026"
    assert len(doc.find_class("collection-cover-verso")) == 1
    documents = doc.find_class("collection-document")
    assert documents[0].get_element_by_id("d1-top") is not None
    assert documents[1].get_element_by_id("d2-top") is not None
    assert "collection-first-document" in documents[0].get("class").split()
    assert "start-direct" in documents[1].get("class").split()
    links = doc.find_class("collection-toc")[0].findall("ol/li/a")
    assert [link.get("href") for link in links] == ["#d1-top", "#d2-top"]
    assert [link.text_content() for link in links] == [
        "SFS 1998:1Första lagen", "(EU) 1998/2Andra rättsakten"]
    assert not doc.find_class("collection-toc")[0].findall(".//a[@href='#d1-R1']")


def test_namespaced_sfs_keeps_its_compact_paper_layout_hook(client):
    page = _page("SFS 1998:1", "Första lagen")
    page = page.replace(
        '<h2 class="rubrik" id="R1">',
        '<div id="dokument"><h2 class="rubrik" id="R1">', 1)
    page = page.replace('</main><aside class="rail">',
                        '</div></main><aside class="rail">')
    compress.write_text(config.DATA / "generated" / "1998:1.html", page)
    doc = pdfcollection.assemble(
        _manifest(cover=False, toc=False, columns=2,
                  items=[{"path": "/1998:1", "amendments": False}]),
        date(2026, 8, 20))
    document = doc.get_element_by_id("d1-dokument")
    assert "print-document" in document.get("class").split()


def test_per_document_omissions_apply_before_assembly(client):
    manifest = _manifest(items=[
        {"path": "/1998:1", "amendments": False, "sections": ["R2"]},
        {"path": "/1998:2", "preamble": False},
    ])
    doc = pdfcollection.assemble(manifest, date(2026, 8, 20))
    assert not doc.find_class("andringar")
    assert not any(doc.find_class(name)
                   for name in ("visa", "recital", "preamble"))
    assert doc.get_element_by_id("d1-R2") is not None
    assert doc.get_element_by_id("d1-R1", None) is None


def test_manifest_rejects_duplicates_and_two_column_context(client):
    with pytest.raises(ValueError, match="flera gånger"):
        pdfcollection.validate(_manifest(items=[{"path": "/1998:1"},
                                                {"path": "/1998:1"}]))
    with pytest.raises(ValueError, match="två kolumner"):
        pdfcollection.validate(_manifest(columns=2, context=["dv"]))


def test_manifest_rejects_two_public_aliases_for_the_same_document(client):
    compress.write_text(config.DATA / "generated" / "eurlex" /
                        "32016R0679.html", _page("(EU) 2016/679", "GDPR"))
    with pytest.raises(ValueError, match="flera gånger"):
        pdfcollection.validate(_manifest(items=[
            {"path": "/celex/32016R0679"},
            {"path": "/eurlex/32016R0679"},
        ]))


def test_collection_endpoints_render_job_and_stream_result(client):
    inspected = client.post("/internal-api/v1/pdf/samling/inspektera",
                            json={"paths": ["/1998:1", "/1998:2"]})
    assert inspected.status_code == 200
    started = client.post("/internal-api/v1/pdf/samling/jobb",
                          json=_manifest().model_dump()).json()
    deadline = time.monotonic() + 30
    while not started["klar"]:
        assert started["fel"] is None
        assert time.monotonic() < deadline
        time.sleep(.05)
        started = client.get("/internal-api/v1/pdf/jobb/%s" % started["id"]).json()
    result = client.get("/internal-api/v1/pdf/jobb/%s/resultat" % started["id"])
    assert result.status_code == 200
    assert result.content.startswith(b"%PDF-")
    assert 'filename="Provsamling.pdf"' in result.headers["content-disposition"]


def test_collection_job_is_rejected_when_the_pdf_queue_is_full(
        client, monkeypatch):
    monkeypatch.setattr(pdfjob, "MAX_LIVE_JOBS", 1)
    occupied = pdfjob.Job(id="occupied", key="another.pdf",
                          started=time.monotonic())
    pdfjob._jobs[occupied.id] = occupied
    pdfjob._by_key[occupied.key] = occupied
    response = client.post("/internal-api/v1/pdf/samling/jobb",
                           json=_manifest().model_dump())
    assert response.status_code == 503
    assert response.headers["retry-after"] == "30"


def test_collection_requests_map_missing_pages_and_bad_recipes(client):
    """The 404/422 answers the collection routes give now come from the typed
    export exceptions `api/errors` maps, not from a try/except per route."""
    missing = client.post("/internal-api/v1/pdf/samling/jobb",
                          json=_manifest(items=[{"path": "/1998:0"}]).model_dump())
    assert missing.status_code == 404
    assert missing.json()["detail"] == "no generated page at '/1998:0'"
    assert client.post("/internal-api/v1/pdf/samling/inspektera",
                       json={"paths": ["/1998:0"]}).status_code == 404
    twice = client.post(
        "/internal-api/v1/pdf/samling/jobb",
        json=_manifest(items=[{"path": "/1998:1"},
                              {"path": "/1998:1"}]).model_dump())
    assert twice.status_code == 422
    assert twice.json()["detail"] == "samma dokument får inte förekomma flera gånger"
    assert client.post(
        "/internal-api/v1/pdf/samling/jobb",
        json=_manifest(items=[{"path": "1998:1?x=1"}]).model_dump()
    ).status_code == 422


def test_collection_pages_are_real_reloadable_addresses(client):
    page = client.get("/samling").text
    assert "Min samling" in page
    assert "Direktlänk till denna samling" in page
    assert "Exportera samling" in page and "Importera samling" in page
    assert "recept" not in page.lower()
    assert "data-collection-wait" in client.get(
        "/internal-api/v1/pdf/samling/vanta").text


def test_bookmark_recipe_round_trips_unicode_and_document_options():
    script = r"""
global.window = {};
global.document = {querySelector: function () { return null; }};
global.location = {pathname: '/'};
global.localStorage = {getItem: function () { return null; }};
require('./ferenda/lib/assets/collection.js');
var state = {version: 1, title: 'Arbetsrätt – urval', subtitle: 'År 2026',
  cover: false, toc: true, columns: 2, context: [], items: [
    {path: '/prop/2020/21:22', start: 'recto', amendments: false,
     preamble: true, sections: ['S5', 'S14']},
    {path: '/1976:580', start: 'direct', amendments: true,
     preamble: true, sections: []}
  ]};
var encoded = window.lagenCollection.wire(state);
var decoded = window.lagenCollection.unwire('#' + encoded);
process.stdout.write(JSON.stringify({encoded: encoded,
  manifest: window.lagenCollection.manifest(decoded)}));
"""
    result = subprocess.run(["node", "-e", script], cwd=config.REPO,
                            capture_output=True, check=True)
    answer = json.loads(result.stdout)
    assert answer["encoded"].startswith("j1.")
    assert answer["manifest"] == {
        "version": 1, "title": "Arbetsrätt – urval", "subtitle": "År 2026",
        "cover": False, "toc": True, "columns": 2, "context": [],
        "items": [
            {"path": "/prop/2020/21:22", "start": "recto",
             "amendments": False, "preamble": True,
             "sections": ["S5", "S14"]},
            {"path": "/1976:580", "start": "direct",
             "amendments": True, "preamble": True, "sections": []},
        ],
    }


def _rendered(manifest):
    def no_subresource(path):
        raise AssertionError("unexpected subresource %s" % path)

    return pdfcollection.export(manifest, subresource=no_subresource,
                                generated=date(2026, 8, 20))


def _pages(data):
    info = subprocess.run(["pdfinfo", "-"], input=data, capture_output=True,
                          check=True).stdout.decode()
    return int(next(line.split(":", 1)[1] for line in info.splitlines()
                    if line.startswith("Pages:")))


def test_direct_start_uses_space_left_on_the_same_page(client):
    manifest = _manifest(cover=False, toc=False, items=[
        {"path": "/1998:1", "sections": ["R1"]},
        {"path": "/1998:2", "sections": ["R1"], "start": "direct"},
    ])
    data = _rendered(manifest)
    assert _pages(data) == 1
    text = subprocess.run(["pdftotext", "-layout", "-", "-"], input=data,
                          capture_output=True, check=True).stdout.decode()
    assert "Första lagen" in text and "Andra rättsakten" in text


@pytest.mark.parametrize(("start", "pages"), [("page", 2), ("recto", 3)])
def test_explicit_document_start_modes(client, start, pages):
    manifest = _manifest(cover=False, toc=False, items=[
        {"path": "/1998:1", "sections": ["R1"]},
        {"path": "/1998:2", "sections": ["R1"], "start": start},
    ])
    assert _pages(_rendered(manifest)) == pages


def test_cover_has_blank_reverse_and_toc_precedes_recto_document(client):
    manifest = _manifest(items=[{"path": "/1998:1", "sections": ["R1"]}])
    data = _rendered(manifest)
    assert _pages(data) == 5
    page_two = subprocess.run(
        ["pdftotext", "-layout", "-f", "2", "-l", "2", "-", "-"],
        input=data, capture_output=True, check=True).stdout.decode().strip()
    assert page_two == ""
    page_three = subprocess.run(
        ["pdftotext", "-layout", "-f", "3", "-l", "3", "-", "-"],
        input=data, capture_output=True, check=True).stdout.decode()
    assert "Första lagen" in page_three and "5" in page_three
    page_four = subprocess.run(
        ["pdftotext", "-layout", "-f", "4", "-l", "4", "-", "-"],
        input=data, capture_output=True, check=True).stdout.decode().strip()
    assert page_four == ""


def test_shared_page_header_names_document_at_top(client):
    filler = "".join("<p>Fyllnadstext %d med tillräckligt många ord för raden.</p>" % n
                     for n in range(35))
    compress.write_text(
        config.DATA / "generated" / "1998:1.html",
        _page("SFS 1998:1", "Första lagen", amendments=True).replace(
            "<p>Första texten.</p>", filler))
    data = _rendered(_manifest(cover=False, toc=False, items=[
        {"path": "/1998:1", "sections": ["R1"]},
        {"path": "/1998:2", "sections": ["R1"], "start": "direct"},
    ]))
    text = subprocess.run(["pdftotext", "-layout", "-", "-"], input=data,
                          capture_output=True, check=True).stdout.decode()
    shared = next(page for page in text.split("\f")
                  if "Fyllnadstext" in page and "Andra rättsakten" in page)
    first_line = next(line for line in shared.splitlines() if line.strip())
    assert "Första lagen" in first_line


def test_new_page_header_names_document_that_starts_the_page(client):
    data = _rendered(_manifest(cover=False, toc=False, items=[
        {"path": "/1998:1", "sections": ["R1"]},
        {"path": "/1998:2", "sections": ["R1"], "start": "page"},
    ]))
    text = subprocess.run(
        ["pdftotext", "-layout", "-f", "2", "-l", "2", "-", "-"],
        input=data, capture_output=True, check=True).stdout.decode()
    first_line = next(line for line in text.splitlines() if line.strip())
    assert "Andra rättsakten" in first_line


def test_direct_document_at_a_natural_page_boundary_gets_its_own_header(
        client):
    filler = "".join(
        "<p>Fyllnadstext %d med tillräckligt många ord för raden.</p>" % n
        for n in range(30))
    compress.write_text(
        config.DATA / "generated" / "1998:1.html",
        _page("SFS 1998:1", "Första lagen", amendments=True).replace(
            "<p>Första texten.</p>", filler))
    data = _rendered(_manifest(cover=False, toc=False, columns=2, items=[
        {"path": "/1998:1", "sections": ["R1"]},
        {"path": "/1998:2", "sections": ["R1"], "start": "direct"},
    ]))
    text = subprocess.run(
        ["pdftotext", "-layout", "-f", "2", "-l", "2", "-", "-"],
        input=data, capture_output=True, check=True).stdout.decode()
    first_line = next(line for line in text.splitlines() if line.strip())
    assert "Andra rättsakten" in first_line
