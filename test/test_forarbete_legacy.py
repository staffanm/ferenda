"""Parse-route tests for re-housed frozen-corpus förarbete records (§7g).

The frozen corpora were migrated into the downloaded/ store (the scaffolding
importers are torn down); these tests exercise the body routes those records
travel through `parse_record`'s harvested path: the trips/text-tml and
skanning2007 HTML adapters, the ABBYY page-anchored XML route, the
scanned-PDF pdftotext fallback (with the OCR chronology check), the re-OCR
sidecar seam and the metadata-only degenerate.
"""

import json
from pathlib import Path

from ferenda.forarbete import legacy_formats as lf
from ferenda.forarbete import parse
from ferenda.lib import compress, layout

FIXTURES = Path(__file__).parent / "files" / "forarbete-legacy"
PDF_MAGIC = b"%PDF-1.4\n%stub\n"     # magic-valid but textless/unparseable body


def _one_page_pdf(content):
    """A minimal one-page PDF whose content stream is `content` (a text-drawing
    op sequence)."""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    return out + (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
                  % (len(objs) + 1, xref))


def _text_pdf():
    """A minimal one-page PDF with a real (visible) text layer."""
    # several short on-page lines: poppler drops glyphs positioned off the page
    return _one_page_pdf(b"BT /F1 12 Tf 50 700 Td %s ET" % b" ".join(
        b"(Detta stycke har en riktig textnivara som proben godkanner.) Tj 0 -14 Td"
        for _ in range(4)))


def _scan_pdf():
    """A one-page PDF whose text is drawn in render mode 3 (invisible) -- an
    OCR-behind-image layer like the KB soukb/propkb scans: `pdftotext` reads it,
    the font-aware `pdftohtml -xml` path yields nothing, so parse must fall back
    to the pdftotext OCR extraction."""
    return _one_page_pdf(b"BT /F1 12 Tf 3 Tr 50 700 Td %s ET" % b" ".join(
        rb"(Enligt lagen \(1960:729\) om upphovsratt galler detta.) Tj 0 -14 Td"
        for _ in range(4)))


def _link_uris(art):
    """Every inline citation-link uri in an artifact's structure blocks."""
    uris = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "text" and isinstance(v, list):
                    uris.extend(r["uri"] for r in v
                                if isinstance(r, dict) and "uri" in r)
                else:
                    walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)

    walk(art.get("structure", []))
    return uris


def _store(out, record, name=None, data=None):
    """Write one body file beside where `record`'s parse will look for it and
    list it on the record (the harvested/re-housed form)."""
    if name is not None:
        dest = layout.fa_dir(out, record["type"], record["basefile"]) / name
        compress.write_download(dest, data)
        record["files"] = record.get("files", []) + [name]
    else:
        record.setdefault("files", [])
    return record


def test_parse_html_record_page_none_and_citations(tmp_path):
    # a text/tml body carrying a heading and an SFS citation
    body = ("Regeringens proposition<br>1995/96:80<br><br>"
            "1 Inledning<br><br>"
            "Enligt lagen (1960:729) om upphovsrätt gäller detta.<br>")
    record = _store(tmp_path, {
        "type": "prop", "basefile": "1995/96:80",
        "identifier": "Prop. 1995/96:80", "title": "T", "date": "1995-10-05",
        "url": "http://data.riksdagen.se/dokument/GH031",
        "source": "propriksdagen", "body_format": "text/tml"},
        "1995-96-80.html", body)
    art = parse.to_artifact(parse.parse_record(record, str(tmp_path)))
    assert art["uri"] == "https://lagen.nu/prop/1995/96:80"
    flat = json.dumps(art["structure"])
    assert "\"page\"" not in flat                        # page-less body omits page
    assert art["structure"], "html body produced no blocks"
    assert any("1960:729" in u for u in _link_uris(art))  # citation scanning intact


def test_parse_skanning2007_record_routes_to_mso_adapter(tmp_path):
    mso = (FIXTURES / "riksdagen_skanning2007.html").read_text(encoding="utf-8")
    record = _store(tmp_path, {
        "type": "prop", "basefile": "1971:30",
        "identifier": "Prop. 1971:30", "title": "T", "date": "1971-12-31",
        "url": "http://data.riksdagen.se/dokument/FU0330",
        "source": "propriksdagen", "body_format": "skanning2007"},
        "1971-30.html", mso)
    art = parse.to_artifact(parse.parse_record(record, str(tmp_path)))
    assert art["uri"] == "https://lagen.nu/prop/1971:30"
    flat = json.dumps(art["structure"], ensure_ascii=False)
    assert "\"page\"" not in flat                        # page-less body omits page
    assert "departementschefen" in flat                  # U+00AD de-hyphenated
    # the fully-<b> heading became an avsnitt container (the bold signal survives)
    assert any(n.get("type") == "avsnitt" for n in art["structure"])


def test_parse_metadata_only_record_yields_artifact(tmp_path):
    record = _store(tmp_path, {
        "type": "prop", "basefile": "1975/76:100",
        "identifier": "Prop. 1975/76:100", "title": "T", "date": None,
        "url": "http://data.riksdagen.se/dokument/X", "source": "propriksdagen"})
    art = parse.to_artifact(parse.parse_record(record, str(tmp_path)))
    assert art["uri"] == "https://lagen.nu/prop/1975/76:100"
    assert art["structure"] == []


def test_parse_abbyy_record_is_page_anchored(tmp_path):
    record = _store(tmp_path, {
        "type": "prop", "basefile": "1867:23",
        "identifier": "Prop. 1867:23", "title": "T", "date": None,
        "url": "https://weburn.kb.se/x", "source": "propkb",
        "body_format": "abbyy"},
        "1867-23.xml", (FIXTURES / "abbyy_propkb.xml").read_bytes())
    art = parse.to_artifact(parse.parse_record(record, str(tmp_path)))
    assert art["uri"] == "https://lagen.nu/prop/1867:23"
    flat = json.dumps(art["structure"], ensure_ascii=False)
    assert '"page": 1' in flat and '"page": 2' in flat   # scan page = #sid anchor
    assert "TABELL SKA HOPPAS" not in flat               # non-Text block skipped


def test_parse_scanned_pdf_falls_back_to_pdftotext(tmp_path):
    # a soukb-style scan: pdftohtml (the font path) reads nothing, so parse falls
    # back to the pdftotext OCR text -- page-anchored, citation-scanned
    record = _store(tmp_path, {
        "type": "sou", "basefile": "1945:1", "identifier": "SOU 1945:1",
        "title": "T", "date": None, "url": "http://urn.kb.se/x",
        "source": "soukb"},
        "1945-1.pdf", _scan_pdf())
    art = parse.to_artifact(parse.parse_record(record, str(tmp_path)))
    assert art["structure"], "scanned pdf fell back to an empty body"
    flat = json.dumps(art["structure"])
    assert '"page": 1' in flat                         # form-feed page = #sid anchor
    # the OCR text WAS citation-scanned -- but a 1945 SOU cannot cite a 1960
    # law, so the chronology check demotes the impossible link to plain text
    # and reports it instead of minting the edge
    assert not any("1960:729" in u for u in _link_uris(art))
    assert {s["uri"] for s in art["suspect_citations"]} \
        == {"https://lagen.nu/1960:729"}


def test_parse_trips_record_is_page_less(tmp_path):
    record = _store(tmp_path, {
        "type": "prop", "basefile": "1993/94:1",
        "identifier": "Prop. 1993/94:1", "title": "T", "date": None,
        "url": None, "source": "proptrips", "body_format": "trips"},
        "1993-94-1.html",
        (FIXTURES / "proptrips_1993-94.html").read_text(encoding="utf-8"))
    art = parse.to_artifact(parse.parse_record(record, str(tmp_path)))
    assert art["structure"]
    assert '"page"' not in json.dumps(art["structure"])   # page-less html body


def test_ocr_sidecar_wins_over_stored_scan(tmp_path, monkeypatch):
    # a modern-OCR'd PDF at the sidecar path is parsed instead of the weak scan
    monkeypatch.setattr(layout, "OCR", tmp_path / "ocr")
    record = _store(tmp_path, {
        "type": "sou", "basefile": "1935:14", "identifier": "SOU 1935:14",
        "title": "T", "date": None, "url": "http://urn.kb.se/x",
        "source": "soukb"},
        "1935-14.pdf", PDF_MAGIC)                          # weak scan
    sidecar = layout.fa_ocr_pdf("sou", "1935:14")
    sidecar.parent.mkdir(parents=True)
    sidecar.write_bytes(_text_pdf())
    art = parse.to_artifact(parse.parse_record(record, str(tmp_path)))
    # the sidecar's text layer (not the textless scan) produced the body
    assert "riktig textnivara" in json.dumps(art["structure"], ensure_ascii=False)


# ---- KB SOU front matter ------------------------------------------------------

KB_1997_116 = [                                     # sou/1997:116's opening pages
    "Ur KB:s samlingar\nDigitaliserad år 2015\n",
    "v\nFMs konvention om\nbarnets rättigheter förverkligas\ni Sverige.\n"
    "BARNKOMMITTEN\nSOU 1997:116\n",
    "",
    "Lili\nän\nW Statens offentliga utredningar\nWW 1997:116\nE Socialdepartementet\n"
    "Barnets bästa\ni främsta rummet\nFN:s konvention om barnets\nrättigheter "
    "förverkligas i Sverige\nBarnkommitténs huvudbetänkande\nStockholm 1997\n",
    "SOU och Ds kan köpas från Fritzes kundtjänst. För remissutsändningar\n"
    "av SOU och Ds svarar Fritzes, Offentliga Publikationer.\n"
    "Beställningsadress: Fritzes kundtjänst\n106 47 Stockholm\n",
    "SOU 1997:116\nTill statsrådet Maj-Inger Klingvall\nGenom beslut den 1 "
    "februari 1996 bemyndigade regeringen dåvarande statsrådet Anna Hedborg\n",
    "SOU 1997:116\nBarnen, generalsekreteraren Bodil Långberg\n",
]


def test_kb_front_pages_drops_the_librarys_and_the_printers_pages(monkeypatch):
    # the report starts on pdf page 6 with the letter to the minister, as a
    # modern SOU on regeringen.se does
    monkeypatch.setattr(lf, "pdftotext_text", lambda path: "\f".join(KB_1997_116))
    assert lf.kb_front_pages("x.pdf") == 5


def test_kb_front_pages_stops_at_a_short_volumes_early_start(monkeypatch):
    # sou/1926:2 opens its letter on pdf page 4, after the year's list
    pages = ["National Library\nof Sweden\nDenna bok digitaliserades på Kungl. "
             "biblioteket år 2012\n",
             "STATENS OFFENTLIGA UTREDNINGAR\n1926:2\nJUSTITIEDEPARTEMENTET\n",
             "Statens offentliga utredningar 1926\nK r o n o l o g i s k\n"
             "Utredning rörande lagstiftningen om arbetstiden\n",
             "Till herr statsrådet och chefen för kungl.\njustitiedepartementet.\n"
             "Med stöd av nådigt bemyndigande den 27 november\n",
             "Departementschefens förutsättningar\n"]
    monkeypatch.setattr(lf, "pdftotext_text", lambda path: "\f".join(pages))
    assert lf.kb_front_pages("x.pdf") == 3
    # and a PDF that does not open on KB's banner keeps every page
    monkeypatch.setattr(lf, "pdftotext_text", lambda path: "\f".join(pages[1:]))
    assert lf.kb_front_pages("x.pdf") == 0
