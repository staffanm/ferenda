"""The PDF export (ferenda/api/pdf.py, api/pdfjob.py + the /api/v1/pdf
routes): the transform that recasts a generated page for paper, the export
as a background job, and the endpoints driven through FastAPI's TestClient
over a tiny generated tree -- no corpus, no network."""

import json
import subprocess
import threading
import time

import lxml.html
import pytest
import weasyprint
from fastapi.testclient import TestClient

from ferenda import build, config
from ferenda.api import app as api
from ferenda.api import facsimiles, pdf, pdfjob
from ferenda.lib import catalog, compress, render
from ferenda.lib import page as page_layout
from ferenda.lib.catalog import BASE

# a minimal but structurally faithful generated page: chrome, TOC column,
# frontmatter, one § carrying a rail marker, and the context island with a
# folded section (details.rail-sec + its capped details.more) and a flat one
PAGE = """<!doctype html>
<html lang="sv"><head><title>Testlag</title>
<link rel="stylesheet" href="/style.css"></head><body class="gr-root">
<header class="masthead"><a href="/">lagen.nu</a></header>
<div class="gr-body">
<aside class="toc-col"><nav class="toc"><div class="toc-list">
<a href="#top" class="lvl1 toc-top">SFS 1998:9999</a>
<a href="#R1" class="lvl2">Inledande bestämmelser</a>
</div></nav></aside>
<main class="gr-main">
<header class="frontmatter" id="top"><div class="eyebrow">SFS 1998:9999</div>
<h1>Testlag</h1></header>
<h2 class="rubrik" id="R1">Inledande bestämmelser</h2>
<section class="paragraf" id="P1" data-rail="P1">
<div class="paragraf-gutter"><span class="n">1 §</span></div>
<div class="paragraf-body"><p>En paragraf.</p></div></section>
</main>
<aside class="rail" id="rail"></aside>
</div>
<script type="application/json" id="lagen-context">{"P1":
"<div class=\\"rail-h\\">Kontext f\\u00f6r <b>1 \\u00a7</b></div>\
<details class=\\"rail-sec dv\\" data-sec=\\"dv\\" data-label=\\"R\\u00e4ttsfall\\" data-n=\\"3\\" open>\
<summary><span class=\\"rail-sec-h\\">R\\u00e4ttsfall</span></summary>\
<ul><li>NJA 2020 s. 1</li><li>NJA 2021 s. 2</li></ul>\
<details class=\\"more\\"><summary>+1 fler</summary><ul><li>NJA 2022 s. 3</li></ul></details>\
</details>\
<div class=\\"rail-sec rail-sec-flat begrepp\\" data-sec=\\"begrepp\\" data-label=\\"Begrepp\\" data-n=\\"1\\">\
<span class=\\"rail-sec-h\\">Begrepp</span><ul><li>Avtal</li></ul></div>"}
</script>
<script src="/script.js" defer></script></body></html>"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    # the job registry is process-global and its key is the page's *content*,
    # so without this a second test of the same PAGE would join the first
    # test's job -- whose result sits in a tmp_path that is already gone
    pdfjob._jobs.clear()
    pdfjob._by_key.clear()
    compress.write_text(tmp_path / "generated" / "1998:9999.html", PAGE)
    return TestClient(api.app)


def _finished(client, params, timeout=60):
    """Start the export as a job and poll it to the end, as the waiting
    screen does. Returns the final status."""
    status = client.post("/internal-api/v1/pdf/jobb", params=params).json()
    deadline = time.monotonic() + timeout
    while not status["klar"] and status["fel"] is None:
        assert time.monotonic() < deadline, "export never finished: %s" % status
        time.sleep(0.05)
        status = client.get("/internal-api/v1/pdf/jobb/%s" % status["id"]).json()
    return status


def test_pdf_inline_with_toc_and_kontext(client):
    r = client.get("/api/v1/pdf", params={
        "path": "/1998:9999", "toc": "1", "kontext": "alla"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF-")
    assert r.headers["content-disposition"] == 'inline; filename="1998-9999.pdf"'


def test_pdf_download_disposition(client):
    r = client.get("/api/v1/pdf", params={"path": "/1998:9999", "download": "1"})
    assert r.headers["content-disposition"] == \
        'attachment; filename="1998-9999.pdf"'


def test_pdf_unknown_kind_is_422(client):
    r = client.get("/api/v1/pdf", params={"path": "/1998:9999",
                                          "kontext": "dv,nonsens"})
    assert r.status_code == 422
    assert "nonsens" in r.json()["detail"]


def test_pdf_missing_page_is_404(client):
    assert client.get("/api/v1/pdf",
                      params={"path": "/1999:0"}).status_code == 404


def _with_img(src):
    return PAGE.replace("<p>En paragraf.</p>",
                        '<p>En paragraf.</p><img src="%s" alt="">' % src)


# a real 1x1 PNG -- WeasyPrint has to decode it, so a stand-in byte string
# would not prove the image reached the page
PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415408d763f8cfc0000003010100c9fe92ef0000000049454e"
    "44ae426082")


def test_pdf_subresource_reads_the_facsimile_off_disk(client, tmp_path,
                                                      monkeypatch):
    """The export resolves a facsimile URL to a file and reads it -- no HTTP.

    It used to answer its own subresource fetches through an in-process
    `TestClient(app)`, which cost a full ASGI round trip per image (2.34 ms
    against 0.11 ms; 0.8 s of it on 2007:90's 325 road signs) and stranded the
    export's own routes in `app.py`, the only module that can name `app`.
    `facsimiles.subresource` parses the URL and reads the cached PNG instead.

    `facsimile_path` is stubbed because rendering it from a real downloaded
    PDF is `test_facsimile.py`'s subject, not this one; what is asserted here
    is the parsing and the read, which is the code that changed."""
    png = tmp_path / "sid1.png"
    png.write_bytes(PNG_1X1)
    seen = []
    monkeypatch.setattr(facsimiles, "facsimile_path",
                        lambda local, sid, bbox=None:
                        (seen.append((local, sid, bbox)), png)[1])
    compress.write_text(
        tmp_path / "generated" / "1998:9999.html",
        _with_img("/api/v1/facsimile?uri=https%3A%2F%2Flagen.nu%2F1998%3A9999"
                  "&amp;sid=7"))
    r = client.get("/api/v1/pdf", params={"path": "/1998:9999"})
    assert r.status_code == 200 and r.content.startswith(b"%PDF-")
    # the uri came back decoded to its catalog-local form, and sid as an int
    assert seen == [("1998:9999", 7, None)]


def test_pdf_subresource_outside_the_served_paths_is_503(client, tmp_path):
    """The dispatcher serves two path families and refuses everything else.

    Narrower than the old in-process client, which answered any route the app
    had. That is the point: a renderer that starts emitting a subresource the
    export cannot resolve now fails loudly here instead of printing a page with
    a hole in it."""
    compress.write_text(tmp_path / "generated" / "1998:9999.html",
                        _with_img("/openapi.json"))
    r = client.get("/api/v1/pdf", params={"path": "/1998:9999"})
    assert r.status_code == 503
    assert not list((tmp_path / "cache" / "pdfexport").glob("*.pdf"))


def test_pdf_subresource_failure_is_503_and_never_cached(client, tmp_path):
    # /api/v1/facsimile 404s in this fixture: the degraded PDF must be
    # refused, and above all never cached -- a cached miss would outlive
    # the transient failure until the page itself changed
    compress.write_text(tmp_path / "generated" / "1998:9999.html",
                        _with_img("/api/v1/facsimile?uri=x&sid=1"))
    r = client.get("/api/v1/pdf", params={"path": "/1998:9999"})
    assert r.status_code == 503
    assert not list((tmp_path / "cache" / "pdfexport").glob("*.pdf"))


def test_pdf_external_subresource_is_503_and_never_cached(client, tmp_path):
    # an external URL would break the "pages load no third-party resource"
    # invariant: surfaced, not silently dropped from the PDF
    compress.write_text(tmp_path / "generated" / "1998:9999.html",
                        _with_img("https://example.com/x.png"))
    r = client.get("/api/v1/pdf", params={"path": "/1998:9999"})
    assert r.status_code == 503
    assert not list((tmp_path / "cache" / "pdfexport").glob("*.pdf"))


def test_pdf_data_uri_image_renders(client, tmp_path):
    compress.write_text(tmp_path / "generated" / "1998:9999.html",
                        _with_img("data:image/gif;base64,R0lGODlhAQABAAAAACw="))
    r = client.get("/api/v1/pdf", params={"path": "/1998:9999"})
    assert r.status_code == 200 and r.content.startswith(b"%PDF-")


def test_a_data_uri_keeps_its_content_type_and_both_encodings_decode():
    """`_data_uri` replaced weasyprint's deprecated default_url_fetcher. Only the
    base64 form is exercised by the render test above, and only the media type
    was carried at first -- a `charset` dropped here makes WeasyPrint read the
    payload as latin-1."""
    b64 = pdf._data_uri("data:image/gif;base64,R0lGODlhAQABAAAAACw=")
    assert b64.headers["content-type"] == "image/gif"
    text = pdf._data_uri("data:text/plain;charset=utf-8,h%C3%A4r")
    assert text.headers["content-type"] == 'text/plain; charset="utf-8"'
    assert text._file_obj.read() == "här".encode()
    bare = pdf._data_uri("data:,plain")
    assert bare.headers["content-type"] == 'text/plain; charset="US-ASCII"'


def test_pdf_result_is_cached_per_option_set(client, tmp_path):
    params = {"path": "/1998:9999", "toc": "1"}
    assert client.get("/api/v1/pdf", params=params).status_code == 200
    cache = tmp_path / "cache" / "pdfexport"
    (entry,) = cache.glob("*.pdf")
    # prove the second request reads the cache: plant known bytes there
    entry.write_bytes(b"%PDF-cached")
    assert client.get("/api/v1/pdf", params=params).content == b"%PDF-cached"
    # a different option set is a different key, rendered fresh
    r = client.get("/api/v1/pdf", params={"path": "/1998:9999"})
    assert r.content.startswith(b"%PDF-") and r.content != b"%PDF-cached"
    assert len(list(cache.glob("*.pdf"))) == 2


def test_pdf_cache_separates_column_and_amendment_options(client):
    page = pdf.generated_page("/1998:9999")
    entries = {
        pdf.cache_entry(page, toc=False, kinds=frozenset(),
                        amendments=amendments, columns=columns).name
        for amendments in (False, True)
        for columns in (1, 2)
    }
    assert len(entries) == 4


def test_pdf_cache_key_is_content_based(client, tmp_path):
    params = {"path": "/1998:9999"}
    client.get("/api/v1/pdf", params=params)
    (entry,) = (tmp_path / "cache" / "pdfexport").glob("*.pdf")
    entry.write_bytes(b"%PDF-cached")
    # rewriting the identical page (new mtime, same bytes -- what an rsync
    # deploy or a no-op regenerate produces) still hits the entry
    compress.write_text(tmp_path / "generated" / "1998:9999.html", PAGE)
    assert client.get("/api/v1/pdf", params=params).content == b"%PDF-cached"
    # changed content -- a source update or a patch-file edit flowing
    # through regenerate -- makes the planted entry unreachable
    compress.write_text(tmp_path / "generated" / "1998:9999.html",
                        PAGE.replace("En paragraf.", "En ändrad paragraf."))
    assert client.get("/api/v1/pdf", params=params).content != b"%PDF-cached"


# -- the export as a background job (api/pdfjob.py) --

def test_job_renders_and_fills_the_cache(client, tmp_path):
    params = {"path": "/1998:9999", "toc": "1", "kontext": "alla"}
    status = _finished(client, params)
    assert status["fel"] is None and status["andel"] == 1.0
    # "done" means the bytes are there: the plain endpoint now answers from
    # the cache without laying anything out again
    (entry,) = (tmp_path / "cache" / "pdfexport").glob("*.pdf")
    entry.write_bytes(b"%PDF-cached")
    assert client.get("/api/v1/pdf", params=params).content == b"%PDF-cached"


def test_job_for_a_cached_export_starts_nothing(client, monkeypatch):
    params = {"path": "/1998:9999", "toc": "1"}
    assert _finished(client, params)["fel"] is None
    monkeypatch.setattr(pdf, "render_pdf", _never_called)
    assert client.post("/internal-api/v1/pdf/jobb", params=params).json()["klar"] is True


def _never_called(*args, **kwargs):
    raise AssertionError("rendered again instead of reading the cache")


def test_job_carries_a_render_failure_to_the_poller(client, tmp_path):
    # the same degraded-subresource case the plain endpoint answers 503 for:
    # on a worker thread it must reach the reader as a job failure, not
    # vanish and leave the screen waiting for ever
    compress.write_text(tmp_path / "generated" / "1998:9999.html",
                        _with_img("/api/v1/facsimile?uri=x&sid=1"))
    status = _finished(client, {"path": "/1998:9999"})
    assert "SubresourceUnavailable" in status["fel"]
    assert not list((tmp_path / "cache" / "pdfexport").glob("*.pdf"))


def test_a_failed_export_is_retried_not_rejoined(client, tmp_path):
    compress.write_text(tmp_path / "generated" / "1998:9999.html",
                        _with_img("/api/v1/facsimile?uri=x&sid=1"))
    first = _finished(client, {"path": "/1998:9999"})
    second = client.post("/internal-api/v1/pdf/jobb", params={"path": "/1998:9999"}).json()
    # the causes are transient, so "Försök igen" must render again rather
    # than hand back the recorded failure
    assert second["id"] != first["id"]


def test_job_status_of_an_unknown_id_is_404(client):
    assert client.get("/internal-api/v1/pdf/jobb/nosuchjob").status_code == 404


def test_job_of_a_missing_page_is_404(client):
    assert client.post("/internal-api/v1/pdf/jobb",
                       params={"path": "/1999:0"}).status_code == 404


def test_new_unique_job_is_rejected_when_the_queue_is_full(
        client, monkeypatch):
    monkeypatch.setattr(pdfjob, "MAX_LIVE_JOBS", 1)
    occupied = pdfjob.Job(id="occupied", key="another.pdf",
                          started=time.monotonic())
    pdfjob._jobs[occupied.id] = occupied
    pdfjob._by_key[occupied.key] = occupied
    response = client.post("/internal-api/v1/pdf/jobb",
                           params={"path": "/1998:9999"})
    assert response.status_code == 503
    assert response.headers["retry-after"] == "30"
    assert "PDF-kön är full" in response.json()["detail"]


def test_identical_exports_share_one_render(client, monkeypatch):
    # two readers asking for the same export at the same moment: the second
    # must wait on the first render, not start a second one beside it
    renders = []
    real = pdf.render_pdf

    def slow(html_text, **kw):
        renders.append(1)
        time.sleep(0.4)
        return real(html_text, **kw)

    monkeypatch.setattr(pdf, "render_pdf", slow)
    params = {"path": "/1998:9999", "toc": "1"}
    first = client.post("/internal-api/v1/pdf/jobb", params=params).json()
    second = client.post("/internal-api/v1/pdf/jobb", params=params).json()
    assert second["id"] == first["id"]        # joined, not started again
    assert _finished(client, params)["fel"] is None
    assert len(renders) == 1


def test_the_render_lock_holds_for_direct_requests_too(client, monkeypatch):
    # /api/v1/pdf has no job to join it to, so the lock inside export() is
    # what keeps two concurrent misses from rendering the same PDF twice
    renders = []
    real = pdf.render_pdf

    def slow(html_text, **kw):
        renders.append(1)
        time.sleep(0.4)
        return real(html_text, **kw)

    monkeypatch.setattr(pdf, "render_pdf", slow)
    got = []
    threads = [threading.Thread(
        target=lambda: got.append(client.get(
            "/api/v1/pdf", params={"path": "/1998:9999"}).content))
        for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert len(renders) == 1
    assert all(body.startswith(b"%PDF-") for body in got)
    assert got[0] == got[1] == got[2]


def test_wait_page_is_a_real_page_for_the_export(client):
    r = client.get("/internal-api/v1/pdf/vanta",
                   params={"path": "/1998:9999", "toc": "1", "kontext": "dv"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    # it carries the options it must start the job with
    assert 'data-path="/1998:9999"' in r.text
    assert 'data-toc="1"' in r.text and 'data-kontext="dv"' in r.text
    assert 'data-andringar="1"' in r.text and 'data-kolumner="1"' in r.text
    # and it names the document by the page's own title, not by its path
    assert "Testlag" in r.text


def test_wait_page_carries_the_compact_options_and_drops_the_context(client):
    # two columns print no context, so the screen must not start a job for a
    # kind the export would ignore
    r = client.get("/internal-api/v1/pdf/vanta",
                   params={"path": "/1998:9999", "toc": "1", "kontext": "dv",
                           "andringar": "0", "kolumner": "2"})
    assert r.status_code == 200
    assert 'data-kontext=""' in r.text
    assert 'data-andringar="0"' in r.text and 'data-kolumner="2"' in r.text


def test_wait_page_rejects_what_the_export_would_reject(client):
    assert client.get("/internal-api/v1/pdf/vanta",
                      params={"path": "/1999:0"}).status_code == 404
    assert client.get("/internal-api/v1/pdf/vanta",
                      params={"path": "/1998:9999",
                              "kontext": "nonsens"}).status_code == 422
    assert client.get("/internal-api/v1/pdf/vanta",
                      params={"path": "/1998:9999",
                              "kolumner": "3"}).status_code == 422


def test_progress_never_walks_backwards_or_reaches_full_early():
    job = pdfjob.Job(id="x", key="k", started=time.monotonic())
    job.plan(100)
    seen = []
    for message in ("Step 1 - Fetching and parsing HTML - HTML string",
                    "Step 3 - Applying CSS",
                    "Step 5 - Creating layout - Page 40",
                    "Step 5 - Creating layout - Page 90",
                    "Step 5 - Creating layout - Repagination #1",
                    "Step 6 - Creating PDF",
                    "Step 7 - Adding PDF metadata"):
        job.note(message)
        seen.append(job.status())
    assert [round(s["andel"], 3) for s in seen] == sorted(
        round(s["andel"], 3) for s in seen)
    assert max(s["andel"] for s in seen) <= 0.99   # 100 % only means "ready"
    # the estimate is replaced by the true count once pass one is over
    assert seen[3]["sidor"] == 100 and not seen[3]["exakt"]
    assert seen[4]["sidor"] == 90 and seen[4]["exakt"]
    job.finished = time.monotonic()
    assert job.status()["andel"] == 1.0


def test_estimate_pages_is_close_on_the_fixture_page():
    doc = lxml.html.document_fromstring(PAGE)
    assert pdf.estimate_pages(doc) == 1


# -- the paper transform, asserted on structure (no WeasyPrint run) --

def _doc():
    return lxml.html.document_fromstring(PAGE)


def test_print_toc_drops_the_top_self_entry():
    nav = pdf._print_toc(_doc(), frozenset())
    links = [(a.get("href"), a.text) for a in nav.iter("a")]
    assert links == [("#R1", "Inledande bestämmelser")]


def test_kontext_aside_filters_by_kind_and_removes_widgets():
    island = pdf.island(_doc())
    aside = pdf._kontext_aside(island["P1"], frozenset(["dv"]))
    assert aside.tag == "div"       # the semantic <aside> wraps this panel
    # the flat begrepp section was not requested and is gone
    assert not aside.find_class("begrepp")
    # the fold became a plain block with an h4 label + count
    sec = aside.find_class("rail-sec")[0]
    assert sec.tag == "div" and not sec.findall("summary")
    assert sec.findtext("h4") == "Rättsfall"
    assert sec.find("h4/span").text == "(3)"
    # the capped tail stays capped: a plain "+1 fler" line, no third case
    assert aside.find_class("print-more")[0].text == "+1 fler"
    assert "NJA 2022" not in lxml.html.tostring(aside, encoding="unicode")


def test_kontext_block_is_an_article_aside_pair():
    # Both flows occupy one grid row, so the note stays level with what it
    # annotates and a later article must wait for the longer flow.
    doc = _doc()
    para = doc.get_element_by_id("P1")
    aside = pdf._kontext_aside(pdf.island(doc)["P1"], frozenset(["dv"]))
    block = pdf._attach(para, [aside])
    assert block.tag == "section"
    assert {"kontextblock", "paragrafblock"} <= set(block.get("class").split())
    text, note = block.getchildren()
    assert text.tag == "article" and text.get("class") == "kontextsp"
    assert text.getchildren() == [para]
    assert note.tag == "aside" and note.get("class") == "kontextnot"
    assert note.getchildren() == [aside]


def test_document_context_starts_beside_the_document_text_after_the_toc():
    panel = (
        '<div class=\\"rail-sec rail-sec-flat dv\\" data-sec=\\"dv\\" '
        'data-label=\\"Om dokumentet\\" data-n=\\"1\\">'
        '<span class=\\"rail-sec-h\\">Om dokumentet</span>'
        '<ul><li>Dokumentnot</li></ul></div>')
    page = PAGE.replace(
        '<h2 class="rubrik" id="R1">',
        '<p id="body-start">Dokumentets första text.</p>'
        '<h2 class="rubrik" id="R1">').replace(
            '{"P1":', '{"":"%s","P1":' % panel)
    doc = pdf.paper_document(page, toc=True, kinds=frozenset(["dv"]),
                              amendments=True, columns=1)
    front = doc.find_class("frontmatter")[0]
    toc = front.getnext()
    block = toc.getnext()
    assert "print-toc" in (toc.get("class") or "").split()
    assert "kontextblock" in (block.get("class") or "").split()
    assert block.find_class("kontextsp")[0][0].get("id") == "body-start"
    note = block.find_class("kontextnot")[0]
    assert "Om dokumentet" in note.text_content()


def test_sfs_stycken_and_points_become_independent_context_rows():
    page = PAGE.replace(
        '<h2 class="rubrik" id="R1">Inledande bestämmelser</h2>\n'
        '<section class="paragraf" id="P1" data-rail="P1">\n'
        '<div class="paragraf-gutter"><span class="n">1 §</span></div>\n'
        '<div class="paragraf-body"><p>En paragraf.</p></div></section>',
        '<div id="dokument"><section class="kapitel">'
        '<h2 class="rubrik" id="R1">Inledande bestämmelser</h2>'
        '<section class="paragraf" id="P1" data-rail="P1">'
        '<div class="paragraf-gutter"><span class="n">1 §</span></div>'
        '<div class="paragraf-body"><p id="P1S1">Första stycket.</p>'
        '<p id="P1S2" data-rail="P1S2">Andra stycket.</p>'
        '<ol class="punkter"><li id="P1S2N1"><span class="num">1.</span>Ett</li>'
        '<li id="P1S2N2" data-rail="P1S2N2"><span class="num">2.</span>Två</li>'
        '</ol></div></section></section></div>')
    source = lxml.html.document_fromstring(page)
    island = source.get_element_by_id("lagen-context")
    panels = json.loads(island.text)
    panels.update({marker:
                   '<div class="rail-sec rail-sec-flat dv" data-sec="dv" '
                   'data-label="Rättsfall" data-n="1"><ul><li>%s</li></ul></div>'
                   % label
                   for marker, label in (("P1S2", "Andra"),
                                         ("P1S2N2", "Punkt två"))})
    island.text = json.dumps(panels)
    page = lxml.html.tostring(source, encoding="unicode")
    doc = pdf.paper_document(page, toc=False, kinds=frozenset(["dv"]),
                              amendments=True, columns=1)
    blocks = doc.find_class("kontextblock")
    assert len(blocks) == 3
    assert [block.find_class("kontextsp")[0][0].get("id")
            for block in blocks] == ["P1", None, None]
    assert doc.get_element_by_id("P1S1").getparent().getparent().get("id") == "P1"
    for marker in ("P1S2", "P1S2N2"):
        row = doc.get_element_by_id(marker)
        while "kontextsp" not in (row.get("class") or "").split():
            row = row.getparent()
        provision = row[0]
        assert "paragraf-fortsatt" in provision.get("class").split()
        assert not provision.find_class("paragraf-gutter")[0].text_content()
    assert "kontextrot" in doc.get_element_by_id("dokument").get("class").split()


def test_sfs_chapter_context_gets_a_row_before_its_first_provision():
    old = (
        '<h2 class="rubrik" id="R1">Inledande bestämmelser</h2>\n'
        '<section class="paragraf" id="P1" data-rail="P1">\n'
        '<div class="paragraf-gutter"><span class="n">1 §</span></div>\n'
        '<div class="paragraf-body"><p>En paragraf.</p></div></section>')
    new = (
        '<div id="dokument"><section class="kapitel" data-rail="K2">'
        '<h2 class="kaprubrik" id="K2">2 kap. Lagens tillämpningsområde</h2>'
        + old[old.index('<section class="paragraf"'):] +
        '</section></div>')
    page = PAGE.replace(old, new).replace(
        '{"P1":',
        '{"K2":"<div class=\\"rail-sec rail-sec-flat dv\\" '
        'data-sec=\\"dv\\" data-label=\\"Om kapitlet\\" data-n=\\"1\\">'
        '<ul><li>En lång kapitelnote</li></ul></div>","P1":')
    doc = pdf.paper_document(page, toc=False, kinds=frozenset(["dv"]),
                              amendments=True, columns=1)
    chapter = doc.find_class("kapitel")[0]
    chapter_row, provision_row = chapter.getchildren()[:2]
    assert "kaprubrikblock" in chapter_row.get("class").split()
    assert chapter_row.find_class("kontextsp")[0][0].get("id") == "K2"
    assert "paragrafblock" in provision_row.get("class").split()
    assert chapter_row.getparent() is provision_row.getparent()


def _page_with_sfs_register():
    old = (
        '<h2 class="rubrik" id="R1">Inledande bestämmelser</h2>\n'
        '<section class="paragraf" id="P1" data-rail="P1">\n'
        '<div class="paragraf-gutter"><span class="n">1 §</span></div>\n'
        '<div class="paragraf-body"><p>En paragraf.</p></div></section>')
    register = (
        '<div id="dokument">%s</div>'
        '<section class="andringar" id="L">'
        '<h2 class="kaprubrik">Ändringar och övergångsbestämmelser</h2>'
        '<div class="andring"><h2>Ändring, SFS 1999:1</h2>'
        '<ul><li><a href="/official.pdf">Tryckt format</a></li></ul>'
        '<h3>Övergångsbestämmelse</h3>'
        '<section class="overgangsbestammelse"><ul class="punkter">'
        '<li>Denna lag träder i kraft.</li></ul></section>'
        '<dl class="meta"><dt>Ikraftträder</dt><dd>1999-01-01</dd></dl>'
        '</div></section>') % old
    return PAGE.replace(old, register).replace(
        '<a href="#R1" class="lvl2">Inledande bestämmelser</a>',
        '<a href="#R1" class="lvl2">Inledande bestämmelser</a>'
        '<a href="#L" class="lvl2">Ändringar</a>')


def test_sfs_amendments_keep_legal_text_but_drop_screen_links():
    doc = pdf.paper_document(_page_with_sfs_register(), toc=True,
                              kinds=frozenset(), amendments=True, columns=1)
    register = doc.find_class("print-andringar")[0]
    post = register.find_class("andring")[0]
    assert not post.xpath("./ul")
    assert post.xpath("./section/ul/li")[0].text_content() == \
        "Denna lag träder i kraft."
    assert post.find_class("meta")[0].text_content() == \
        "Ikraftträder1999-01-01"


def test_sfs_amendments_can_be_omitted_from_the_body_and_toc():
    # only the register's own entry goes with it. A TOC entry whose target is
    # missing for any other reason stays: that is a renderer bug, and a short
    # TOC would hide it.
    page = _page_with_sfs_register().replace(
        '<a href="#L" class="lvl2">',
        '<a href="#SPOKE" class="lvl2">Spökavsnitt</a><a href="#L" class="lvl2">')
    doc = pdf.paper_document(page, toc=True, kinds=frozenset(),
                              amendments=False, columns=1)
    assert not doc.find_class("andringar")
    hrefs = [a.get("href") for a in doc.find_class("print-toc")[0].iter("a")]
    assert "#L" not in hrefs and "#SPOKE" in hrefs


def test_two_column_mode_keeps_the_title_wide_and_uses_two_text_columns():
    old = (
        '<h2 class="rubrik" id="R1">Inledande bestämmelser</h2>\n'
        '<section class="paragraf" id="P1" data-rail="P1">\n'
        '<div class="paragraf-gutter"><span class="n">1 §</span></div>\n'
        '<div class="paragraf-body"><p>En paragraf.</p></div></section>')
    prose = '<h2 class="rubrik" id="R1">Inledande bestämmelser</h2>' + \
        "".join('<p id="line-%d">En paragraf med lagom mycket text.</p>' % i
                for i in range(180))
    doc = pdf.paper_document(PAGE.replace(old, prose), toc=False,
                              kinds=frozenset(["dv"]), amendments=True,
                              columns=2)
    assert "pdf-two-columns" in doc.get("class").split()
    assert "pdf-two-columns" in doc.find("body").get("class").split()
    assert not doc.find_class("print-kontext")
    front = doc.find_class("frontmatter")[0]
    assert front.getnext().get("class") == "print-columns"

    failures = []
    document = weasyprint.HTML(
        string=pdf._paper_html(doc), base_url=BASE,
        url_fetcher=pdf._fetcher(_no_subresource, failures)).render()
    assert not failures
    mm = 25.4 / 96
    lines = []
    title = None
    for box in document.pages[0]._page_box.descendants():
        element = getattr(box, "element", None)
        element_id = element.get("id") if element is not None else None
        if type(box).__name__ == "BlockBox" and element_id == "top":
            title = box
        if (type(box).__name__ == "BlockBox" and element_id
                and element_id.startswith("line-")):
            lines.append(box)
    assert title is not None and title.border_width() * mm > 175
    positions = {round(box.position_x * mm) for box in lines}
    assert positions == {12, 109}
    assert {round(box.border_width() * mm) for box in lines} == {89}


def test_two_column_sfs_table_breaks_between_columns_after_its_opening_text():
    old = (
        '<h2 class="rubrik" id="R1">Inledande bestämmelser</h2>\n'
        '<section class="paragraf" id="P1" data-rail="P1">\n'
        '<div class="paragraf-gutter"><span class="n">1 §</span></div>\n'
        '<div class="paragraf-body"><p>En paragraf.</p></div></section>')
    rows = "".join(
        '<tr id="long-row-%d"><td>Begrepp %d</td><td>En beskrivning som '
        'tar tillräckligt mycket plats för att tabellen ska fortsätta.</td></tr>'
        % (i, i) for i in range(45))
    provision = (
        '<div id="dokument"><section class="paragraf" id="table-provision">'
        '<div class="paragraf-gutter"><span class="n">1 §</span></div>'
        '<div class="paragraf-body"><p id="table-opening">I denna lag används '
        'följande begrepp.</p><table><tr><td>Begrepp</td><td>Betydelse</td></tr>'
        + rows + '</table></div></section></div>')
    doc = pdf.paper_document(PAGE.replace(old, provision), toc=False,
                              kinds=frozenset(), amendments=True, columns=2)
    assert "print-document" in doc.get_element_by_id(
        "dokument").get("class").split()
    start = doc.find_class("paragraf-start")[0]
    assert "paragraf-gutter" in start[0].get("class").split()
    assert start[1].get("id") == "table-opening"

    failures = []
    document = weasyprint.HTML(
        string=pdf._paper_html(doc), base_url=BASE,
        url_fetcher=pdf._fetcher(_no_subresource, failures)).render()
    assert not failures
    mm = 25.4 / 96
    first_page_rows = []
    for box in document.pages[0]._page_box.descendants():
        element = getattr(box, "element", None)
        element_id = element.get("id") if element is not None else None
        if (type(box).__name__ == "TableRowBox" and element_id
                and element_id.startswith("long-row-")):
            first_page_rows.append(box)
    assert len({round(box.position_x * mm) for box in first_page_rows}) == 2


def test_sfs_tables_use_italic_headers_without_row_rules():
    old = (
        '<h2 class="rubrik" id="R1">Inledande bestämmelser</h2>\n'
        '<section class="paragraf" id="P1" data-rail="P1">\n'
        '<div class="paragraf-gutter"><span class="n">1 §</span></div>\n'
        '<div class="paragraf-body"><p>En paragraf.</p></div></section>')
    table = (
        '<div id="dokument"><section class="paragraf" id="table-provision">'
        '<div class="paragraf-gutter"><span class="n">1 §</span></div>'
        '<div class="paragraf-body"><p>Följande begrepp används.</p>'
        '<table><tr><td id="table-head">Begrepp</td>'
        '<td>Betydelse</td></tr><tr><td id="table-body">Sekretess</td>'
        '<td>Ett förbud att röja en uppgift.</td></tr></table>'
        '</div></section></div>')
    doc = pdf.paper_document(PAGE.replace(old, table), toc=False,
                              kinds=frozenset(), amendments=True, columns=1)
    failures = []
    document = weasyprint.HTML(
        string=pdf._paper_html(doc), base_url=BASE,
        url_fetcher=pdf._fetcher(_no_subresource, failures)).render()
    assert not failures
    cells = {}
    provisions = []
    for box in document.pages[0]._page_box.descendants():
        element = getattr(box, "element", None)
        element_id = element.get("id") if element is not None else None
        if type(box).__name__ == "TableCellBox" and element_id:
            cells[element_id] = box
        if (type(box).__name__ in ("BlockBox", "GridBox")
                and element_id == "table-provision"):
            provisions.append(box)
    assert provisions and all(box.style["break_inside"] == "auto"
                              for box in provisions)
    assert cells["table-head"].style["font_style"] == "italic"
    assert cells["table-body"].style["font_style"] == "normal"
    for cell in cells.values():
        assert cell.border_top_width == cell.border_bottom_width == 0


def test_article_aside_fragments_mirror_across_pages():
    paragraphs = "".join(
        "<p>Textflöde %d med några ord för en naturlig radbrytning.</p>" % i
        for i in range(60))
    html = (
        '<!doctype html><html lang="sv"><head>'
        '<link rel="stylesheet" href="/style.css"></head>'
        '<body><div class="gr-body"><main class="gr-main">'
        '<section class="kontextblock">'
        '<article class="kontextsp">%s</article>'
        '<aside class="kontextnot">%s</aside>'
        '</section><article class="after">Efterföljande artikel.</article>'
        '</main></div></body></html>' % (paragraphs, paragraphs))
    failures = []
    document = weasyprint.HTML(
        string=html, base_url=BASE,
        url_fetcher=pdf._fetcher(_no_subresource, failures)).render()
    assert not failures
    mm = 25.4 / 96
    expected = (
        {"kontextsp": (28, 117), "kontextnot": (145, 55)},
        {"kontextsp": (65, 117), "kontextnot": (10, 55)},
    )
    for page, page_expected in zip(document.pages[:2], expected, strict=True):
        fragments = {}
        for box in page._page_box.descendants():
            element = getattr(box, "element", None)
            classes = ((element.get("class") or "").split()
                       if element is not None else [])
            for cls in page_expected.keys() & classes:
                if type(box).__name__ == "BlockBox":
                    fragments[cls] = (
                        (box.position_x + box.margin_left) * mm,
                        box.border_width() * mm)
        assert fragments.keys() == page_expected.keys()
        for cls, (x, width) in fragments.items():
            expected_x, expected_width = page_expected[cls]
            assert x == pytest.approx(expected_x)
            assert width == pytest.approx(expected_width)
    note_fragments = []
    after = None
    for page_number, page in enumerate(document.pages):
        for box in page._page_box.descendants():
            element = getattr(box, "element", None)
            classes = ((element.get("class") or "").split()
                       if element is not None else [])
            if type(box).__name__ != "BlockBox":
                continue
            if ("kontextnot" in classes
                    and box.border_width() * mm == pytest.approx(55)):
                note_fragments.append(
                    (page_number, (box.position_y + box.border_height()) * mm))
            if "after" in classes:
                after = (page_number, box.position_y * mm)
    assert after is not None and note_fragments
    last_page, last_bottom = note_fragments[-1]
    assert after[0] > last_page or (after[0] == last_page
                                    and after[1] >= last_bottom)


def test_weasy_table_rows_mirror_after_linear_layout():
    # The API-specific table layout avoids WeasyPrint's superlinear grid
    # pagination on large SFS files. It first lays both cells in recto order;
    # the paper-space pass moves note-cell fragments on verso pages.
    paragraphs = "".join(
        "<p>Textflöde %d med några ord för en naturlig radbrytning.</p>" % i
        for i in range(60))
    html = (
        '<!doctype html><html lang="sv"><head>'
        '<link rel="stylesheet" href="/style.css"></head>'
        '<body class="pdf-weasy"><div class="gr-body"><main class="gr-main">'
        '<section class="kontextblock">'
        '<article class="kontextsp">%s</article>'
        '<aside class="kontextnot"><div class="print-kontext">%s</div></aside>'
        '</section><article class="after">Efterföljande artikel.</article>'
        '</main></div></body></html>' % (paragraphs, paragraphs))
    failures = []
    document = weasyprint.HTML(
        string=html, base_url=BASE,
        url_fetcher=pdf._fetcher(_no_subresource, failures)).render()
    pdf._mirror_margin_notes(document)
    assert not failures
    mm = 25.4 / 96
    expected = (
        {"kontextsp": (28, 117), "kontextnot": (145, 55)},
        {"kontextsp": (65, 117), "kontextnot": (10, 55)},
    )
    for page_number, (page, page_expected) in enumerate(
            zip(document.pages[:2], expected, strict=True), 1):
        fragments = {}
        dividers = []
        for box in page._page_box.descendants():
            element = getattr(box, "element", None)
            classes = ((element.get("class") or "").split()
                       if element is not None else [])
            for cls in page_expected.keys() & classes:
                if type(box).__name__ == "TableCellBox":
                    fragments[cls] = (box.position_x * mm,
                                      box.border_width() * mm)
            if type(box).__name__ == "BlockBox" and "print-kontext" in classes:
                dividers.append(box)
        assert fragments.keys() == page_expected.keys()
        for cls, (x, width) in fragments.items():
            expected_x, expected_width = page_expected[cls]
            assert x == pytest.approx(expected_x)
            assert width == pytest.approx(expected_width)
        assert dividers
        divider = dividers[0]
        if page_number == 1:
            assert divider.border_left_width > 0
            assert divider.border_right_width == 0
            assert divider.padding_left > 0 and divider.padding_right == 0
        else:
            assert divider.border_left_width == 0
            assert divider.border_right_width > 0
            assert divider.padding_left == 0 and divider.padding_right > 0


def test_paper_stylesheet_removes_known_weasyprint_warning_causes(caplog):
    assert "font-weight: 400 600" not in pdf.stylesheet()
    caplog.set_level("INFO")
    failures = []
    weasyprint.HTML(
        string='<link rel="stylesheet" href="/style.css"><p>Text</p>',
        base_url=BASE,
        url_fetcher=pdf._fetcher(_no_subresource, failures)).render()
    assert not failures
    assert not [record for record in caplog.records
                if record.name in ("weasyprint", "fontTools.ttLib.woff2")]


def test_print_context_references_and_indented_points_keep_their_edges():
    html = (
        '<html><head><link rel="stylesheet" href="/style.css"></head>'
        '<body><div class="gr-body"><main class="gr-main">'
        '<div class="print-kontext"><div class="rail-sec">'
        '<h4 id="label">Förarbeten</h4><ul>'
        '<li id="one">Prop. 2020/21:1</li>'
        '<li id="two">SOU 2020:2</li></ul></div>'
        '<div class="rail-sec kommentar"><p id="comment">Kommentar</p></div>'
        '</div>'
        '<p id="point" class="point hang">a) En punkt med text.</p>'
        '<p><a class="ext">Extern hänvisning</a></p>'
        '</main></div></body></html>')
    failures = []
    document = weasyprint.HTML(
        string=html, base_url=BASE,
        url_fetcher=pdf._fetcher(_no_subresource, failures)).render()
    assert not failures
    boxes = {}
    texts = []
    for box in document.pages[0]._page_box.descendants():
        element = getattr(box, "element", None)
        element_id = element.get("id") if element is not None else None
        if element_id in ("label", "one", "two", "comment", "point") \
                and type(box).__name__ == "BlockBox":
            boxes[element_id] = box
        if type(box).__name__ == "TextBox":
            texts.append(box.text)
    assert boxes["label"].position_y < boxes["one"].position_y \
        < boxes["two"].position_y
    assert boxes["comment"].style["font_family"] == \
        boxes["one"].style["font_family"]
    assert boxes["comment"].style["font_size"] == boxes["one"].style["font_size"]
    mm = 25.4 / 96
    point_right = (boxes["point"].position_x + boxes["point"].margin_left
                   + boxes["point"].border_width()) * mm
    assert point_right == pytest.approx(145)
    assert "↗" not in "".join(texts)


def test_every_note_of_one_block_shares_the_margin():
    # a block can carry several markers' notes (SFS marks each stycke of a
    # §); they stack in the one margin cell, in document order
    doc = _doc()
    para = doc.get_element_by_id("P1")
    notes = [pdf._kontext_aside(pdf.island(doc)["P1"], frozenset(["dv"]))
             for _ in range(3)]
    block = pdf._attach(para, notes)
    assert block.find_class("kontextnot")[0].getchildren() == notes


def test_the_permalink_pilcrow_does_not_reach_paper():
    # `a.pilcrow` alone loses to `.paragraf-gutter .pilcrow`, which sets
    # `display: block` on screen. The print block therefore hid nothing, and
    # every § printed a stray glyph under its number.
    page = PAGE.replace(
        '<span class="n">1 §</span></div>',
        '<span class="n">1 §</span>'
        '<a class="pilcrow" href="#P1" aria-label="Permalänk">¶</a></div>')
    out = subprocess.run(
        ["pdftotext", "-", "-"],
        input=pdf.render_pdf(page, toc=False, kinds=frozenset(),
                             subresource=_no_subresource,
                             amendments=True, columns=1),
        capture_output=True, check=True).stdout.decode()
    assert "1 §" in out
    assert "¶" not in out


def test_an_annotated_heading_keeps_its_text():
    doc = _doc()
    heading = doc.get_element_by_id("R1")
    aside = pdf._kontext_aside(pdf.island(doc)["P1"], frozenset(["dv"]))
    block = pdf._attach(heading, [aside])
    # the break falls after the block now, out of reach of the heading's own
    # break-after: avoid
    assert "rubrikblock" in block.get("class").split()


def test_the_printed_page_carries_a_running_head_and_its_number():
    # a regression guard with teeth: the running head and the page number
    # live in the @page *corner* boxes, and a rewrite of the @page rule once
    # dropped every one of them. Nothing short of rendering catches that --
    # the strings are still set, they are simply read by nobody.
    long = PAGE.replace("<p>En paragraf.</p>",
                        "".join("<p>%s</p>" % ("Text i paragrafen. " * 40)
                                for _ in range(12)))
    out = subprocess.run(
        ["pdftotext", "-layout", "-f", "3", "-l", "3", "-", "-"],
        input=pdf.render_pdf(long, toc=False, kinds=frozenset(),
                             subresource=_no_subresource,
                             amendments=True, columns=1),
        capture_output=True, check=True).stdout.decode()
    lines = [line for line in out.splitlines() if line.strip()]
    head, foot = lines[0], lines[-1]
    assert "SFS 1998:9999" in head          # the eyebrow, on the fold
    assert "Testlag" in head                # the document, in the middle
    # where you are, on the outer edge. The division half is what this page
    # can show; the § half of the same string comes from
    # `.paragraf-gutter .n`, which needs a statute with more than one § to
    # exercise and which a one-§ fixture never opens a page on.
    assert "Inledande bestämmelser" in head
    assert foot.strip() == "3"              # the page number, alone


def _no_subresource(path_qs):
    raise AssertionError("the fixture page loads no subresource: %s" % path_qs)


def test_short_citer_keeps_the_identifier_and_the_pinpoint():
    # print cuts a citation to what a reader looks it up by. Getting this
    # wrong ships a citation nobody can find, and it is pure text surgery,
    # so it is pinned line by line.
    assert pdf._short_citer(
        "Prop. 2015/16:170: En uppdaterad fondlagstiftning (UCITS V), "
        "avsnitt 8.4") == "Prop. 2015/16:170, avsnitt 8.4"
    assert pdf._short_citer("SOU 2023:24: Etablering för fler, s. 269") == \
        "SOU 2023:24, s. 269"
    # no pinpoint to keep: the identifier alone
    assert pdf._short_citer(
        "Prop. 2024/25:19: Långsiktig reglering av vissa forskningsdatabaser"
    ) == "Prop. 2024/25:19"
    # the run-on forms, which carry no separator at all
    assert pdf._short_citer(
        "(EU) 2016/1629 Tekniska krav för fartyg i inlandssjöfart"
    ) == "(EU) 2016/1629"
    assert pdf._short_citer("IMYRS 2021:1 Innebörden av begreppet") == \
        "IMYRS 2021:1"
    assert pdf._short_citer("B-140522-2026 Förutsättningar för att") == \
        "B-140522-2026"
    # already short, or carrying no identifier to cut back to: kept whole
    for whole in ("NJA 2020 s. 1", "HFD 2019 ref. 9", "WP 256", "C-258/23",
                  "Förslag till avgörande av generaladvokat Dean Spielmann"):
        assert pdf._short_citer(whole) == whole


def test_cap_section_folds_what_print_drops_into_the_disclosure():
    # the cap only collapses -- the count has to cover the items print hides
    # as well as the ones the screen already did, or the line lies
    section = lxml.html.fromstring(
        '<div class="rail-sec"><ul>%s</ul>'
        '<details class="more"><summary>+250 till</summary>'
        '<ul>%s</ul></details></div>'
        % ("".join("<li>NJA 2020 s. %d</li>" % i for i in range(9)),
           "".join("<li>NJA 2021 s. %d</li>" % i for i in range(250))))
    pdf._cap_section(section, 5)
    assert len(section.findall("ul/li")) == 5
    # 250 already hidden + the 4 this cut: and in the rail's own wording
    assert section.find_class("print-more")[0].text == "+254 till"


def test_cap_section_leaves_a_short_list_alone():
    section = lxml.html.fromstring(
        '<div class="rail-sec"><ul><li>NJA 2020 s. 1</li></ul></div>')
    pdf._cap_section(section, 5)
    assert len(section.findall("ul/li")) == 1
    assert not section.find_class("print-more")


def test_running_labels_shorten_a_division_heading():
    doc = lxml.html.document_fromstring(
        '<div><h2 class="kaprubrik">4 kap. Om brott mot frihet och frid</h2>'
        '<h2 class="rubrik">KAPITEL X Delegerade akter och genomförandeakter</h2>'
        '<h2 class="rubrik">Inledande bestämmelser</h2>'
        '<h2 class="rubrik">En rubrik som är alldeles för lång för en kolumntitel'
        '</h2></div>')
    pdf._running_labels(doc)
    assert [h.get("data-kort") for h in doc.iter("h2")] == [
        "4 kap. · ", "KAPITEL X · ", "Inledande bestämmelser · ", ""]


def test_kontext_aside_without_requested_kinds_is_none():
    island = pdf.island(_doc())
    assert pdf._kontext_aside(island["P1"], frozenset(["kommentar"])) is None


def test_parse_kinds():
    assert pdf.parse_kinds("") == frozenset()
    assert "dv" in pdf.parse_kinds("alla")
    assert pdf.parse_kinds("dv, kommentar") == frozenset({"dv", "kommentar"})
    with pytest.raises(ValueError, match="nonsens"):
        pdf.parse_kinds("nonsens")


def test_filename_for():
    assert pdf.filename_for("/1998:204") == "1998-204.pdf"
    assert pdf.filename_for("/prop/2020/21:22") == "prop-2020-21-22.pdf"


def test_paper_transform_holds_on_a_really_rendered_sfs_page(tmp_path):
    """The paper transform asserts the SFS page's own structure -- a
    `.paragraf-gutter` and a `.paragraf-body` that opens with a paragraph.
    That markup belongs to `sfs/templates/sfs.html`, so a hand-written page
    cannot prove the contract still holds. Render one for real instead."""
    law = tmp_path / "law.json"
    law.write_text(json.dumps({
        "uri": "https://lagen.nu/1998:9998",
        "metadata": {"properties": {"dcterms:title": "Provlag (1998:9998)"}},
        "structure": [
            {"type": "paragraf", "id": "P1", "ordinal": "1", "children": [
                {"type": "stycke", "id": "P1S1", "beteckning": "1 §",
                 "text": ["Denna lag gäller"], "children": [
                     {"type": "punkt", "id": "P1S1N1", "ordinal": "1",
                      "text": ["på prov, och"]},
                     {"type": "punkt", "id": "P1S1N2", "ordinal": "2",
                      "text": ["i andra hand."]},
                 ]},
                {"type": "stycke", "id": "P1S2",
                 "text": ["Ett andra stycke."]},
            ]},
        ],
    }), encoding="utf-8")
    # a case citing the § gives the page its rail marker and context island,
    # which is what puts the provision through `_split_sfs_provisions`
    case = tmp_path / "case.json"
    case.write_text(json.dumps({
        "uri": "https://lagen.nu/dom/NJA_1994_s_1",
        "court": "HDO", "court_namn": "Högsta domstolen",
        "referat": ["NJA 1994 s. 1"], "malnummer": ["T 1-94"],
        "metadata": {"sammanfattning": "Om provlagen."},
        "structure": [{"type": "stycke", "text": [
            "Enligt ",
            {"predicate": "dcterms:references", "text": "1 § 2 provlagen",
             "uri": "https://lagen.nu/1998:9998#P1S1N2"}, "."]}],
    }), encoding="utf-8")
    db = str(tmp_path / "catalog.sqlite")
    catalog.rebuild(db, "sfs", [law])
    catalog.rebuild(db, "dv", [case])
    out = tmp_path / "generated"
    render.generate_site(db, out,
                         {name: src.render
                          for name, src in build.SOURCES.items() if src.render},
                         source="sfs",
                         write_index=False)
    page = compress.read_text(
        out / page_layout.doc_relpath("https://lagen.nu/1998:9998"))
    assert 'id="lagen-context"' in page and "data-rail" in page

    doc = pdf.paper_document(page, toc=True, kinds=frozenset(["dv"]),
                              amendments=True, columns=1)
    assert doc.find_class("kontextblock")
    provision = doc.find_class("paragraf")[0]
    assert provision.find_class("paragraf-gutter")
    assert provision.find_class("paragraf-body")[0][0].tag == "p"
    # the case cites the § second point, so the list really is split -- and
    # the point keeps the explicit number the split relies on
    fragment = doc.find_class("paragraf-fortsatt")[0]
    point = fragment.find_class("paragraf-body")[0][0][0]
    assert point.get("id") == "P1S1N2"
    assert point.find_class("num")[0].text_content() == "2."
    # and the compact layout, whose asserts read the same three facts
    compact = pdf.paper_document(page, toc=False, kinds=frozenset(),
                                  amendments=True, columns=2)
    start = compact.find_class("paragraf-start")[0]
    assert "paragraf-gutter" in start[0].get("class").split()
