"""The PDF export (accommodanda/api/pdf.py, api/pdfjob.py + the /api/v1/pdf
routes): the transform that recasts a generated page for paper, the export
as a background job, and the endpoints driven through FastAPI's TestClient
over a tiny generated tree -- no corpus, no network."""

import subprocess
import threading
import time

import lxml.html
import pytest
from fastapi.testclient import TestClient

from accommodanda import config
from accommodanda.api import app as api
from accommodanda.api import pdf, pdfjob
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
    status = client.post("/api/v1/pdf/jobb", params=params).json()
    deadline = time.monotonic() + timeout
    while not status["klar"] and status["fel"] is None:
        assert time.monotonic() < deadline, "export never finished: %s" % status
        time.sleep(0.05)
        status = client.get("/api/v1/pdf/jobb/%s" % status["id"]).json()
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
    assert client.post("/api/v1/pdf/jobb", params=params).json()["klar"] is True


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
    second = client.post("/api/v1/pdf/jobb", params={"path": "/1998:9999"}).json()
    # the causes are transient, so "Försök igen" must render again rather
    # than hand back the recorded failure
    assert second["id"] != first["id"]


def test_job_status_of_an_unknown_id_is_404(client):
    assert client.get("/api/v1/pdf/jobb/nosuchjob").status_code == 404


def test_job_of_a_missing_page_is_404(client):
    assert client.post("/api/v1/pdf/jobb",
                       params={"path": "/1999:0"}).status_code == 404


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
    first = client.post("/api/v1/pdf/jobb", params=params).json()
    second = client.post("/api/v1/pdf/jobb", params=params).json()
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
    r = client.get("/api/v1/pdf/vanta",
                   params={"path": "/1998:9999", "toc": "1", "kontext": "dv"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    # it carries the options it must start the job with
    assert 'data-path="/1998:9999"' in r.text
    assert 'data-toc="1"' in r.text and 'data-kontext="dv"' in r.text
    # and it names the document by the page's own title, not by its path
    assert "Testlag" in r.text


def test_wait_page_rejects_what_the_export_would_reject(client):
    assert client.get("/api/v1/pdf/vanta",
                      params={"path": "/1999:0"}).status_code == 404
    assert client.get("/api/v1/pdf/vanta",
                      params={"path": "/1998:9999",
                              "kontext": "nonsens"}).status_code == 422


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


def test_kontext_block_is_a_two_cell_row():
    # provision and note are the two cells of one row, so the note stays
    # level with what it annotates. Floating it lets the text flow past,
    # which fills the page and decouples the columns: the GDPR's recitals
    # carry three times the context their text can sit beside, and the
    # backlog never drained -- page 60 had an empty margin, page 100 an
    # empty reading column.
    doc = _doc()
    para = doc.get_element_by_id("P1")
    aside = pdf._kontext_aside(pdf._island(doc)["P1"], frozenset(["dv"]))
    block = pdf._attach(para, [aside])
    assert block.get("class") == "kontextblock"
    (row,) = block.getchildren()
    assert row.get("class") == "kontextrad"
    text, note = row.getchildren()
    assert text.get("class") == "kontextsp" and text.getchildren() == [para]
    assert note.get("class") == "kontextnot" and note.getchildren() == [aside]


def test_every_note_of_one_block_shares_the_margin():
    # a block can carry several markers' notes (SFS marks each stycke of a
    # §); they stack in the one margin cell, in document order
    doc = _doc()
    para = doc.get_element_by_id("P1")
    notes = [pdf._kontext_aside(pdf._island(doc)["P1"], frozenset(["dv"]))
             for _ in range(3)]
    block = pdf._attach(para, notes)
    assert block.find_class("kontextnot")[0].getchildren() == notes


def test_an_annotated_heading_keeps_its_text():
    doc = _doc()
    heading = doc.get_element_by_id("R1")
    aside = pdf._kontext_aside(pdf._island(doc)["P1"], frozenset(["dv"]))
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
                             subresource=_no_subresource),
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
