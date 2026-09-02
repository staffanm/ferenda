"""The search-engine and link-preview head every page carries (page.html via
`page_context`): the title tag leads with the identifier, a document page names
its one public address, the description is the catalog snippet -- and the
sitemap the full generate writes lists exactly the documents that have a page."""

from ferenda.lib import catalog, compress, render
from ferenda.lib.page import description_text, head_title, page_context
from ferenda.lib.tpl import ENV


def _head(**kwargs):
    ctx = page_context("Hovrättens dagboksblad", "Rättsfall", "", body="body",
                       **kwargs)
    html = ENV.get_template("page.html").render(ctx)
    return html[:html.index("</head>")]


def test_title_tag_leads_with_the_identifier_the_h1_keeps_the_name():
    assert head_title("Hovrättens dagboksblad", "NJA 2015 s. 180") == \
        "NJA 2015 s. 180 – Hovrättens dagboksblad | lagen.nu"
    assert head_title("Skollagen", "SFS 2010:800") == \
        "SFS 2010:800 – Skollagen | lagen.nu"


def test_title_tag_does_not_repeat_an_identifier_the_name_carries():
    assert head_title("HFD 2012 ref. 21", "HFD 2012 ref. 21") == \
        "HFD 2012 ref. 21 | lagen.nu"
    assert head_title("Avtal") == "Avtal | lagen.nu"


def test_frontpage_is_titled_by_its_mark_not_by_its_title_string():
    ctx = page_context("lagen.nu", "Start", "", body="", mark=True)
    assert ctx["head_title"] == "lagen.nu"
    ctx = page_context("lagen.nu", "Begrepp", "", body="")
    assert ctx["head_title"] == "lagen.nu | lagen.nu"


def test_description_is_the_snippet_cut_on_a_word_boundary():
    assert description_text("  1 §  Denna lag\n gäller ") == "1 § Denna lag gäller"
    long = " ".join(["ord"] * 200)
    cut = description_text(long)
    assert len(cut) <= 301 and cut.endswith("ord…")
    assert description_text("") is None


def test_document_page_head_names_its_public_address_and_opening_words():
    head = _head(doc_uri="https://lagen.nu/dom/nja/2015s180#P3",
                 short_id="NJA 2015 s. 180", description="Ett dagboksblad.")
    assert "<title>NJA 2015 s. 180 – Hovrättens dagboksblad | lagen.nu</title>" in head
    assert '<link rel="canonical" href="https://lagen.nu/dom/nja/2015s180">' in head
    assert '<meta property="og:url" content="https://lagen.nu/dom/nja/2015s180">' in head
    assert '<meta name="description" content="Ett dagboksblad.">' in head
    assert '<meta property="og:description" content="Ett dagboksblad.">' in head
    assert '<meta property="og:type" content="article">' in head
    assert '<meta property="og:title" content="NJA 2015 s. 180 – Hovrättens dagboksblad | lagen.nu">' in head
    assert '<meta property="og:image" content="https://lagen.nu/og-image.png">' in head


def test_solo_page_head_has_no_canonical_and_is_a_website():
    head = _head(solo=True)
    assert 'rel="canonical"' not in head and "og:url" not in head
    assert 'name="description"' not in head
    assert '<meta property="og:type" content="website">' in head


def _catalog(tmp_path, rows):
    con = catalog.connect(tmp_path / "catalog.sqlite")
    con.executemany(
        "INSERT INTO documents (uri, source, path, art_mtime_ns) VALUES (?,?,?,?)",
        rows)
    con.commit()
    return con


def test_sitemap_lists_the_documents_with_a_page_escaped_and_dated(tmp_path):
    con = _catalog(tmp_path, [
        ("https://lagen.nu/1998:204", "sfs", "sfs/1998/204.json", 1_700_000_000 * 10**9),
        ("https://lagen.nu/dom/HDO_Ö2857_08", "dv", "dv/x.json", None),
        ("https://lagen.nu/svjt/1952s453", "lawreview", "lr/x.json", 1),
    ])
    out = tmp_path / "generated"
    assert render.write_sitemaps(con, out, {"sfs", "dv"}) == 2
    index = compress.read_text(out / "sitemap.xml")
    assert "<loc>https://lagen.nu/sitemap-1.xml</loc>" in index
    assert "sitemap-2.xml" not in index
    urls = compress.read_text(out / "sitemap-1.xml")
    assert ("<url><loc>https://lagen.nu/1998:204</loc>"
            "<lastmod>2023-11-14</lastmod></url>") in urls
    assert "<url><loc>https://lagen.nu/dom/HDO_%C3%962857_08</loc></url>" in urls
    assert "svjt" not in urls


def test_sitemap_removes_files_a_smaller_corpus_no_longer_fills(tmp_path):
    con = _catalog(tmp_path, [("https://lagen.nu/1998:204", "sfs", "p", None)])
    out = tmp_path / "generated"
    out.mkdir()
    compress.write_text(out / "sitemap-2.xml", "<urlset/>")
    render.write_sitemaps(con, out, {"sfs"})
    assert not list(out.glob("sitemap-2.xml*"))
    assert compress.exists(out / "sitemap-1.xml")


def test_sitemap_splits_at_the_protocol_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "SITEMAP_URLS", 2)
    con = _catalog(tmp_path, [("https://lagen.nu/%d:1" % y, "sfs", "p", None)
                              for y in range(1990, 1995)])
    out = tmp_path / "generated"
    render.write_sitemaps(con, out, {"sfs"})
    assert compress.read_text(out / "sitemap.xml").count("<sitemap>") == 3
    assert compress.read_text(out / "sitemap-3.xml").count("<url>") == 1


def test_catalog_snippet_falls_back_to_description(tmp_path):
    con = _catalog(tmp_path, [("https://lagen.nu/a", "dv", "p", None)])
    con.execute("UPDATE documents SET description = 'Beslut.' WHERE uri = 'https://lagen.nu/a'")
    assert catalog.snippet(con, "https://lagen.nu/a") == "Beslut."
    assert catalog.snippet(con, "https://lagen.nu/none") is None
