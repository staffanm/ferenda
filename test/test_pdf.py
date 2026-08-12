"""The PDF export (accommodanda/api/pdf.py + the /api/v1/pdf route): the
transform that recasts a generated page for paper, and the endpoint driven
through FastAPI's TestClient over a tiny generated tree -- no corpus, no
network."""

import lxml.html
import pytest
from fastapi.testclient import TestClient

from accommodanda import config
from accommodanda.api import app as api
from accommodanda.api import pdf
from accommodanda.lib import compress

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
    compress.write_text(tmp_path / "generated" / "1998:9999.html", PAGE)
    return TestClient(api.app)


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


def test_pdf_subresource_fetch_runs_through_the_app(client, tmp_path):
    # the img URL is answered by the app itself via the in-process client;
    # any 200 body proves that loop (a body WeasyPrint cannot decode as an
    # image is non-fatal by design, like a broken img on screen)
    compress.write_text(tmp_path / "generated" / "1998:9999.html",
                        _with_img("/openapi.json"))
    r = client.get("/api/v1/pdf", params={"path": "/1998:9999"})
    assert r.status_code == 200 and r.content.startswith(b"%PDF-")


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


# -- the paper transform, asserted on structure (no WeasyPrint run) --

def _doc():
    return lxml.html.document_fromstring(PAGE)


def test_print_toc_drops_the_top_self_entry():
    nav = pdf._print_toc(_doc())
    links = [(a.get("href"), a.text) for a in nav.iter("a")]
    assert links == [("#R1", "Inledande bestämmelser")]


def test_kontext_aside_filters_by_kind_and_removes_widgets():
    island = pdf._island(_doc())
    aside = pdf._kontext_aside(island["P1"], frozenset(["dv"]))
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


def test_kontext_aside_without_requested_kinds_is_none():
    island = pdf._island(_doc())
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
