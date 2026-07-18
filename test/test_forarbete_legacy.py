"""Tests for the frozen-corpus förarbete import machinery (REWRITE.md §7g).

Hermetic: every case builds a small fake frozen tree in a tmp dir and points
``config.LEGACY_ROOT`` at it, so nothing touches the real 410 GB legacy trees.
Covers the precedence rule (live wins; format tier beats source rank; rank breaks
ties; force semantics), the propriksdagen walk (both eras, data-driven body
routing incl. the pdf text-layer probe, junk-dir/shape exclusion, ej-utgiven
skip, live-record skip, idempotency), and the eight entries-driven corpora
(souregeringen multi-part main-first ordering, soukb's 1922 ``fs`` suffix +
textless-probe, propkb's ABBYY-xml/b-series, proptrips' pdf/trips/doc-only/empty
splits, dirtrips' flat html store, dirasp's missing-files skip, the
orig_url-provenance vs live-source_url split, and the SOURCE_RANK/tier gap-window
handoff where a proptrips pdf replaces a propriksdagen html record but an equal
html tier does not). Parse routing of imported records is checked too (text/tml,
skanning2007 and TRIPS html -> page-less blocks; ABBYY xml -> page-anchored
blocks; a metadata-only record still yields an artifact; the re-OCR sidecar wins
over the frozen scan). The probe runs for real: a truncated %PDF stub is a
textless pdf, `_text_pdf()` builds a minimal real pdf whose text layer passes it.
"""

import json
from pathlib import Path

import pytest

from accommodanda import config
from accommodanda.forarbete import legacy, parse
from accommodanda.forarbete.legacy import body_tier, import_propriksdagen, should_write
from accommodanda.lib import layout

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
    """A minimal one-page PDF with a real (visible) text layer long enough to pass
    the import's text-layer probe (>100 non-whitespace chars over the first
    pages)."""
    # several short on-page lines: poppler drops glyphs positioned off the page,
    # so one long line would extract to fewer than the probe's 100 chars
    return _one_page_pdf(b"BT /F1 12 Tf 50 700 Td %s ET" % b" ".join(
        b"(Detta stycke har en riktig textnivara som proben godkanner.) Tj 0 -14 Td"
        for _ in range(4)))


def _scan_pdf():
    """A one-page PDF whose text is drawn in render mode 3 (invisible) -- an
    OCR-behind-image layer like the KB soukb/propkb scans: `pdftotext` reads it,
    the font-aware `pdftohtml -xml` path yields nothing, so parse must fall back to
    the pdftotext OCR extraction."""
    return _one_page_pdf(b"BT /F1 12 Tf 3 Tr 50 700 Td %s ET" % b" ".join(
        rb"(Enligt lagen \(1960:729\) om upphovsratt galler detta.) Tj 0 -14 Td"
        for _ in range(4)))


def _xml(rm, bet, htmlformat="skanning2007", datum="1975-01-01 00:00:00",
         titel="En proposition", dok_id="X1"):
    """A minimal dokumentstatus XML with the fields dokumentstatus_meta reads."""
    return ("<dokumentstatus><dokument>"
            "<dok_id>%s</dok_id><rm>%s</rm><beteckning>%s</beteckning>"
            "<datum>%s</datum><titel>%s</titel><htmlformat>%s</htmlformat>"
            "<dokument_url_html>http://data.riksdagen.se/dokument/%s</dokument_url_html>"
            "</dokument></dokumentstatus>"
            % (dok_id, rm, bet, datum, titel, htmlformat, dok_id)).encode("utf-8")


def _doc(downloaded, rmdir, nr, xml, *, pdf=None, html=None):
    """Materialize one frozen doc dir with index.xml (+ optional body files)."""
    d = downloaded / rmdir / nr
    d.mkdir(parents=True)
    (d / "index.xml").write_bytes(xml)
    if pdf is not None:
        (d / "index.pdf").write_bytes(pdf)
    if html is not None:
        (d / "index.html").write_text(html, encoding="utf-8")
    return d


@pytest.fixture
def legacy_root(tmp_path, monkeypatch):
    """A tmp LEGACY_ROOT with an empty ``propriksdagen/downloaded/`` tree; returns
    (source_path, downloaded, out_root)."""
    monkeypatch.setattr(config, "LEGACY_ROOT", tmp_path)
    downloaded = tmp_path / "propriksdagen" / "downloaded"
    downloaded.mkdir(parents=True)
    return tmp_path / "propriksdagen", downloaded, tmp_path / "out"


# --- precedence primitives -----------------------------------------------

def test_body_tier():
    assert body_tier(["propriksdagen/downloaded/1971/40/index.pdf"]) == 2
    # .doc/.docx/.wpd/.rtf are never emitted in legacy_files (no parse route --
    # see _pick_proptrips), so BODY_FORMATS only lists .pdf; a stray .doc/.wpd
    # would fall to the html-only tier like any other non-pdf body
    assert body_tier(["x/a.doc"]) == 1 and body_tier(["x/a.wpd"]) == 1
    assert body_tier(["x/a.html"]) == 1
    assert body_tier([]) == 0


def _rec(source, files):
    return {"source": source, "legacy_files": files}


def test_should_write_live_always_wins():
    live = {"type": "prop"}                              # no `source` key
    cand = _rec("propriksdagen", ["a.pdf"])
    assert should_write(live, cand) is False
    assert should_write(live, cand, force=True) is False   # force can't beat live


def test_should_write_new_slot():
    assert should_write(None, _rec("propriksdagen", [])) is True


def test_should_write_tier_beats_rank():
    # a better body format wins even from a worse-ranked corpus, and vice versa
    html_better_rank = _rec("propriksdagen", ["a.html"])   # rank 1, tier 1
    pdf_worse_rank = _rec("proptrips", ["a.pdf"])           # rank 2, tier 2
    assert should_write(html_better_rank, pdf_worse_rank) is True   # tier 2 > 1
    assert should_write(pdf_worse_rank, html_better_rank) is False  # tier 1 < 2


def test_should_write_rank_breaks_ties():
    existing = _rec("proptrips", ["a.html"])               # rank 2, tier 1
    candidate = _rec("propriksdagen", ["b.html"])          # rank 1, tier 1
    assert should_write(existing, candidate) is True       # rank 1 < 2
    assert should_write(candidate, existing) is False      # rank 2 > 1


def test_should_write_force_semantics_same_source():
    existing = _rec("propriksdagen", ["a.html"])
    candidate = _rec("propriksdagen", ["a.html"])
    assert should_write(existing, candidate) is False      # plain re-run: keep it
    assert should_write(existing, candidate, force=True) is True   # --force rewrites


def test_should_write_unknown_corpus_asserts():
    with pytest.raises(AssertionError, match="unknown frozen corpus"):
        should_write(_rec("mystery", ["a.pdf"]), _rec("propriksdagen", ["b.html"]))


# --- import walk ----------------------------------------------------------

def test_import_pdf_route(legacy_root):
    # a pdf with a real text layer (probe passes) wins over any sibling html
    source, downloaded, out = legacy_root
    _doc(downloaded, "1971", "40", _xml("1971", "40", dok_id="FU031"),
         pdf=_text_pdf(), html="ignored (pdf wins)")
    counts = import_propriksdagen(source, out, log=lambda *_: None)
    assert (counts["imported"], counts["pdf_route"]) == (1, 1)
    rec = json.loads((layout.fa_record_file(out, "prop", "1971-40")).read_text())
    assert rec["basefile"] == "1971:40"
    assert rec["type"] == "prop" and rec["source"] == "propriksdagen"
    assert rec["identifier"] == "Prop. 1971:40"
    assert rec["url"] == "http://data.riksdagen.se/dokument/FU031"
    assert "files" not in rec and "body_format" not in rec
    assert rec["legacy_files"] == ["propriksdagen/downloaded/1971/40/index.pdf"]


def test_import_textless_pdf_falls_to_skanning_html(legacy_root):
    # the skanning2007 era: a textless scan beside riksdagen's OCR Word-export
    # html -- the html is the body; the pdf is NOT listed (tier stays 1, so a
    # later corpus's real pdf/doc copy can still win the basefile)
    source, downloaded, out = legacy_root
    mso = (FIXTURES / "riksdagen_skanning2007.html").read_text(encoding="utf-8")
    _doc(downloaded, "1971", "30", _xml("1971", "30"), pdf=PDF_MAGIC, html=mso)
    counts = import_propriksdagen(source, out, log=lambda *_: None)
    assert (counts["imported"], counts["html_route"]) == (1, 1)
    rec = json.loads((layout.fa_record_file(out, "prop", "1971-30")).read_text())
    assert rec["body_format"] == "skanning2007"
    assert rec["legacy_files"] == ["propriksdagen/downloaded/1971/30/index.html"]
    assert body_tier(rec["legacy_files"]) == 1


def test_import_textless_pdf_falls_to_texttml_html(legacy_root):
    # the 1991-95 text/tml era also stores textless scans as index.pdf; the
    # probe routes those docs to their <br>-plaintext html body
    source, downloaded, out = legacy_root
    body = (FIXTURES / "riksdagen_text_tml.html").read_text(encoding="utf-8")
    _doc(downloaded, "1991-92", "104", _xml("1991/92", "104", htmlformat="text/tml"),
         pdf=PDF_MAGIC, html=body)
    counts = import_propriksdagen(source, out, log=lambda *_: None)
    assert (counts["imported"], counts["html_route"]) == (1, 1)
    rec = json.loads((layout.fa_record_file(out, "prop", "1991-92-104")).read_text())
    assert rec["body_format"] == "text/tml"
    assert rec["legacy_files"] == ["propriksdagen/downloaded/1991-92/104/index.html"]


def test_import_html_ec_is_never_a_body(legacy_root):
    # html-ec html is positioned PDF-rendering junk; with the pdf textless too,
    # the doc imports as metadata-only
    source, downloaded, out = legacy_root
    _doc(downloaded, "1996-97", "1", _xml("1996/97", "1", htmlformat="html-ec"),
         pdf=PDF_MAGIC, html="<div>positioned junk</div>")
    counts = import_propriksdagen(source, out, log=lambda *_: None)
    assert (counts["imported"], counts["metadata_only"]) == (1, 1)
    rec = json.loads((layout.fa_record_file(out, "prop", "1996-97-1")).read_text())
    assert rec["legacy_files"] == [] and "body_format" not in rec


def test_import_html_route_text_tml(legacy_root):
    source, downloaded, out = legacy_root
    body = (FIXTURES / "riksdagen_text_tml.html").read_text(encoding="utf-8")
    _doc(downloaded, "1995-96", "80", _xml("1995/96", "80", htmlformat="text/tml"),
         html=body)
    counts = import_propriksdagen(source, out, log=lambda *_: None)
    assert (counts["imported"], counts["html_route"]) == (1, 1)
    rec = json.loads((layout.fa_record_file(out, "prop", "1995-96-80")).read_text())
    assert rec["basefile"] == "1995/96:80"
    assert rec["body_format"] == "text/tml"
    assert rec["legacy_files"] == ["propriksdagen/downloaded/1995-96/80/index.html"]


def test_import_metadata_only(legacy_root):
    # a text/tml doc whose only html is a bilaga (not index.html) -> no body route,
    # and a skanning2007 doc with no pdf -> also metadata-only
    source, downloaded, out = legacy_root
    _doc(downloaded, "1975-76", "100", _xml("1975/76", "100"))           # xml only
    _doc(downloaded, "1997-98", "45", _xml("1997/98", "45", htmlformat="text/tml"))
    counts = import_propriksdagen(source, out, log=lambda *_: None)
    assert (counts["imported"], counts["metadata_only"]) == (2, 2)
    rec = json.loads((layout.fa_record_file(out, "prop", "1975-76-100")).read_text())
    assert rec["legacy_files"] == []


def test_import_skips_junk_dirs(legacy_root):
    source, downloaded, out = legacy_root
    _doc(downloaded, "1971", "1", _xml("1971", "1"))
    _doc(downloaded, "2006-prop-2006-07", "1", _xml("2006/07", "1"))
    _doc(downloaded, "2017-htgen.nu-prop-2017-18", "1", _xml("2017/18", "1"))
    counts = import_propriksdagen(source, out, log=lambda *_: None)
    assert counts["imported"] == 1 and counts["junk_dirs"] == 2
    assert not (layout.fa_record_file(out, "prop", "2006-07-1")).exists()


def test_import_accepts_1999_2000_shape(legacy_root):
    source, downloaded, out = legacy_root
    _doc(downloaded, "1999-2000", "1", _xml("1999/2000", "1"))
    counts = import_propriksdagen(source, out, log=lambda *_: None)
    assert counts["imported"] == 1 and counts["junk_dirs"] == 0
    assert (layout.fa_record_file(out, "prop", "1999-2000-1")).exists()


def test_import_ej_utgiven_skipped(legacy_root):
    source, downloaded, out = legacy_root
    ej = (FIXTURES / "riksdagen_ej_utgiven.html").read_text(encoding="utf-8")
    _doc(downloaded, "1994-95", "7", _xml("1994/95", "7", htmlformat="text/tml"),
         html=ej)
    counts = import_propriksdagen(source, out, log=lambda *_: None)
    assert counts["imported"] == 0 and counts["ej_utgiven"] == 1
    assert not (layout.fa_record_file(out, "prop", "1994-95-7")).exists()


def test_import_skips_live_record_unchanged(legacy_root):
    source, downloaded, out = legacy_root
    _doc(downloaded, "1996-97", "115", _xml("1996/97", "115", htmlformat="text/tml"),
         html="Regeringens proposition<br>1996/97:115<br>")
    live = layout.fa_record_file(out, "prop", "1996-97-115")
    live.parent.mkdir(parents=True)
    live.write_text(json.dumps({"type": "prop", "basefile": "1996/97:115",
                                "url": "https://www.regeringen.se/x", "files": []}))
    before = live.read_bytes()
    counts = import_propriksdagen(source, out, force=True, log=lambda *_: None)
    assert counts["skipped_live"] == 1 and counts["imported"] == 0
    assert live.read_bytes() == before                  # not touched, even with force


def test_import_idempotent_rerun_and_force(legacy_root):
    source, downloaded, out = legacy_root
    _doc(downloaded, "1971", "40", _xml("1971", "40"), pdf=_text_pdf())
    import_propriksdagen(source, out, log=lambda *_: None)
    rec = layout.fa_record_file(out, "prop", "1971-40")
    rec.write_text(rec.read_text() + "\n")              # a local edit to detect rewrite
    marked = rec.read_text()
    # plain re-run keeps the record untouched (its mtime, so parse stays fresh)
    counts = import_propriksdagen(source, out, log=lambda *_: None)
    assert counts["imported"] == 0 and counts["skipped_existing"] == 1
    assert rec.read_text() == marked
    # --force re-imports the corpus's own record
    counts = import_propriksdagen(source, out, force=True, log=lambda *_: None)
    assert counts["imported"] == 1 and rec.read_text() != marked


def test_import_limit_caps_run(legacy_root):
    source, downloaded, out = legacy_root
    for nr in ("1", "2", "3"):
        _doc(downloaded, "1971", nr, _xml("1971", nr))
    counts = import_propriksdagen(source, out, limit=2, log=lambda *_: None)
    assert counts["imported"] == 2


# --- parse routing of imported records ------------------------------------

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


def test_parse_html_record_page_none_and_citations(legacy_root, monkeypatch):
    source, downloaded, out = legacy_root
    # a text/tml body carrying a heading and an SFS citation
    body = ("Regeringens proposition<br>1995/96:80<br><br>"
            "1 Inledning<br><br>"
            "Enligt lagen (1960:729) om upphovsrätt gäller detta.<br>")
    monkeypatch.setattr(config, "LEGACY_ROOT", downloaded.parent.parent)
    (downloaded / "1995-96" / "80").mkdir(parents=True)
    (downloaded / "1995-96" / "80" / "index.html").write_text(body, encoding="utf-8")
    record = {"type": "prop", "basefile": "1995/96:80",
              "identifier": "Prop. 1995/96:80", "title": "T", "date": "1995-10-05",
              "url": "http://data.riksdagen.se/dokument/GH031",
              "source": "propriksdagen", "body_format": "text/tml",
              "legacy_files": ["propriksdagen/downloaded/1995-96/80/index.html"]}
    art = parse.to_artifact(parse.parse_record(record, str(out)))
    assert art["uri"] == "https://lagen.nu/prop/1995/96:80"
    flat = json.dumps(art["structure"])
    assert "\"page\"" not in flat                        # page-less body omits page
    assert art["structure"], "html body produced no blocks"
    assert any("1960:729" in u for u in _link_uris(art))  # citation scanning intact


def test_parse_skanning2007_record_routes_to_mso_adapter(legacy_root):
    source, downloaded, out = legacy_root
    mso = (FIXTURES / "riksdagen_skanning2007.html").read_text(encoding="utf-8")
    (downloaded / "1971" / "30").mkdir(parents=True)
    (downloaded / "1971" / "30" / "index.html").write_text(mso, encoding="utf-8")
    record = {"type": "prop", "basefile": "1971:30",
              "identifier": "Prop. 1971:30", "title": "T", "date": "1971-12-31",
              "url": "http://data.riksdagen.se/dokument/FU0330",
              "source": "propriksdagen", "body_format": "skanning2007",
              "legacy_files": ["propriksdagen/downloaded/1971/30/index.html"]}
    art = parse.to_artifact(parse.parse_record(record, str(out)))
    assert art["uri"] == "https://lagen.nu/prop/1971:30"
    flat = json.dumps(art["structure"], ensure_ascii=False)
    assert "\"page\"" not in flat                        # page-less body omits page
    assert "departementschefen" in flat                  # U+00AD de-hyphenated
    # the fully-<b> heading became an avsnitt container (the bold signal survives)
    assert any(n.get("type") == "avsnitt" for n in art["structure"])


def test_parse_metadata_only_record_yields_artifact(legacy_root):
    source, downloaded, out = legacy_root
    record = {"type": "prop", "basefile": "1975/76:100",
              "identifier": "Prop. 1975/76:100", "title": "T", "date": None,
              "url": "http://data.riksdagen.se/dokument/X", "source": "propriksdagen",
              "legacy_files": []}
    art = parse.to_artifact(parse.parse_record(record, str(out)))
    assert art["uri"] == "https://lagen.nu/prop/1975/76:100"
    assert art["structure"] == []


# ==========================================================================
# entries-driven corpora (souregeringen/ds/dir-regeringen, soukb, propkb,
# proptrips, dirtrips, dirasp) -- basefile read from the entry, body by its path
# ==========================================================================

def _entry(entries_dir, rel, basefile, title="Titel", orig_url="http://up/1"):
    """A trimmed frozen entry JSON at ``entries/<rel>.json`` (the authoritative
    post-sanitization ``basefile`` + ``orig_url`` provenance the walkers read)."""
    p = entries_dir / (rel + ".json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"basefile": basefile, "title": title,
                             "orig_url": orig_url}), encoding="utf-8")


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    """A tmp LEGACY_ROOT; returns (make, out) where make(corpus) builds an empty
    ``<corpus>/{entries,downloaded}/`` tree and returns (source_path, entries_dir,
    downloaded_dir)."""
    monkeypatch.setattr(config, "LEGACY_ROOT", tmp_path)

    def make(corpus):
        src = tmp_path / corpus
        entries, downloaded = src / "entries", src / "downloaded"
        entries.mkdir(parents=True)
        downloaded.mkdir(parents=True)
        return src, entries, downloaded

    return make, tmp_path / "out"


def _read_rec(out, typ, slug):
    return json.loads(layout.fa_record_file(out, typ, slug).read_text())


# --- souregeringen / dsregeringen / dirregeringen -------------------------

def test_souregeringen_multipart_orders_main_first(frozen):
    # a multi-part SOU: the landing page's content-link order (main first) beats
    # the alphabetical order (which would put the appendix first)
    make, out = frozen
    src, entries, downloaded = make("souregeringen")
    _entry(entries, "2020/21", "2020:21",
           orig_url="https://www.regeringen.se/.../sou-202021/")
    d = downloaded / "2020" / "21"
    d.mkdir(parents=True)
    (d / "appendix-eng.pdf").write_bytes(_text_pdf())
    (d / "main-sou-202021.pdf").write_bytes(_text_pdf())
    (d / "index.html").write_text(
        '<a href="/x/contentassets/h/main-sou-202021.pdf">m</a>'
        '<a href="/x/contentassets/h/appendix-eng.pdf">a</a>', encoding="utf-8")
    counts = legacy.import_souregeringen(src, out, log=lambda *_: None)
    assert (counts["imported"], counts["pdf_route"]) == (1, 1)
    rec = _read_rec(out, "sou", "2020-21")
    assert rec["type"] == "sou" and rec["identifier"] == "SOU 2020:21"
    assert rec["source"] == "souregeringen" and rec["date"] is None
    # regeringen.se resolves -> orig_url flows to the rendered url
    assert rec["orig_url"] == "https://www.regeringen.se/.../sou-202021/"
    assert rec["url"] == rec["orig_url"]
    assert [Path(f).name for f in rec["legacy_files"]] == [
        "main-sou-202021.pdf", "appendix-eng.pdf"]


def test_regeringen_metadata_only_when_no_text_pdf(frozen):
    make, out = frozen
    src, entries, downloaded = make("dsregeringen")
    _entry(entries, "1998/69", "1998:69")
    d = downloaded / "1998" / "69"
    d.mkdir(parents=True)
    (d / "index.html").write_text("<html>landing only</html>", encoding="utf-8")
    counts = legacy.import_dsregeringen(src, out, log=lambda *_: None)
    assert (counts["imported"], counts["metadata_only"]) == (1, 1)
    rec = _read_rec(out, "ds", "1998-69")
    assert rec["identifier"] == "Ds 1998:69" and rec["legacy_files"] == []


def test_regeringen_missing_docdir_and_null_stub_skipped(frozen):
    make, out = frozen
    src, entries, downloaded = make("dirregeringen")
    _entry(entries, "2009/34", "2009:34")            # no docdir on disk
    (entries / "0.json").write_text(json.dumps({"basefile": None}))  # failure stub
    counts = legacy.import_dirregeringen(src, out, log=lambda *_: None)
    assert counts["imported"] == 0
    assert counts["no_docdir"] == 1 and counts["null_stub"] == 1


# --- soukb (index.pdf, 1922 fs suffix, text-layer probe) ------------------

def test_soukb_fs_suffix_and_textless_probe(frozen):
    make, out = frozen
    src, entries, downloaded = make("soukb")
    _entry(entries, "1922/10fs", "1922:10fs",        # 1922 första-serien suffix
           orig_url="http://urn.kb.se/resolve?urn=urn:nbn:se:kb:sou-1")
    (downloaded / "1922" / "10fs").mkdir(parents=True)
    (downloaded / "1922" / "10fs" / "index.pdf").write_bytes(_text_pdf())
    _entry(entries, "1935/14", "1935:14")
    (downloaded / "1935" / "14").mkdir(parents=True)
    (downloaded / "1935" / "14" / "index.pdf").write_bytes(PDF_MAGIC)  # textless
    counts = legacy.import_soukb(src, out, log=lambda *_: None)
    assert (counts["pdf_route"], counts["metadata_only"]) == (1, 1)
    rec = _read_rec(out, "sou", "1922-10fs")
    assert rec["basefile"] == "1922:10fs" and rec["identifier"] == "SOU 1922:10fs"
    assert rec["url"] == rec["orig_url"]              # urn.kb.se resolves
    assert rec["legacy_files"] == ["soukb/downloaded/1922/10fs/index.pdf"]
    assert _read_rec(out, "sou", "1935-14")["legacy_files"] == []   # textless


# --- propkb (ABBYY OCR-XML xor a scan-only index.pdf; b-series basefiles) --

def test_propkb_abbyy_xml_and_bseries(frozen):
    make, out = frozen
    src, entries, downloaded = make("propkb")
    _entry(entries, "1867/23", "1867:23",
           orig_url="https://weburn.kb.se/riks/.../prop_1867____23.xml")
    (downloaded / "1867" / "23").mkdir(parents=True)
    (downloaded / "1867" / "23" / "index.xml").write_bytes(
        (FIXTURES / "abbyy_propkb.xml").read_bytes())
    _entry(entries, "1958/b23", "1958:b23")          # b-series verbatim
    (downloaded / "1958" / "b23").mkdir(parents=True)
    (downloaded / "1958" / "b23" / "index.pdf").write_bytes(_text_pdf())
    counts = legacy.import_propkb(src, out, log=lambda *_: None)
    assert (counts["abbyy_route"], counts["pdf_route"]) == (1, 1)
    rec = _read_rec(out, "prop", "1867-23")
    assert rec["identifier"] == "Prop. 1867:23" and rec["body_format"] == "abbyy"
    assert rec["url"] == rec["orig_url"]              # weburn.kb.se resolves
    assert rec["legacy_files"] == ["propkb/downloaded/1867/23/index.xml"]
    assert body_tier(rec["legacy_files"]) == 1        # xml is a tier-1 body
    b = _read_rec(out, "prop", "1958-b23")
    assert b["basefile"] == "1958:b23"
    assert b["legacy_files"] == ["propkb/downloaded/1958/b23/index.pdf"]


# --- proptrips (probed index.pdf else div.body-text; doc/wpd not listed) ---

def test_proptrips_pdf_html_doc_and_empty_dir(frozen):
    make, out = frozen
    src, entries, downloaded = make("proptrips")
    _entry(entries, "2007-08/1", "2007/08:1",
           orig_url="http://193.188.157.111/prop?dok=P")
    (downloaded / "2007-08" / "1").mkdir(parents=True)
    (downloaded / "2007-08" / "1" / "index.pdf").write_bytes(_text_pdf())
    _entry(entries, "1993-94/1", "1993/94:1")
    (downloaded / "1993-94" / "1").mkdir(parents=True)
    (downloaded / "1993-94" / "1" / "index.html").write_text(
        (FIXTURES / "proptrips_1993-94.html").read_text(encoding="utf-8"),
        encoding="utf-8")
    _entry(entries, "1995-96/100", "1995/96:100")    # .wpd only -> no parse route
    (downloaded / "1995-96" / "100").mkdir(parents=True)
    (downloaded / "1995-96" / "100" / "index.wpd").write_bytes(b"\xffWPC junk")
    _entry(entries, "2071-72/1", "2071/72:1")        # legacy sanitizer stray dir
    (downloaded / "2071-72" / "1").mkdir(parents=True)   # empty
    counts = legacy.import_proptrips(src, out, log=lambda *_: None)
    assert counts["pdf_route"] == 1 and counts["trips_route"] == 1
    assert counts["metadata_only"] == 1 and counts["no_docdir"] == 1
    pdf = _read_rec(out, "prop", "2007-08-1")
    assert pdf["orig_url"] == "http://193.188.157.111/prop?dok=P"
    assert pdf["url"] is None                         # dead TRIPS IP: provenance only
    trec = _read_rec(out, "prop", "1993-94-1")
    assert trec["body_format"] == "trips"
    assert trec["legacy_files"] == ["proptrips/downloaded/1993-94/1/index.html"]
    assert _read_rec(out, "prop", "1995-96-100")["legacy_files"] == []   # .wpd not listed
    assert not (layout.fa_record_file(out, "prop", "2071-72-1")).exists()           # empty dir skipped


def test_proptrips_search_shell_page_imports_metadata_only(frozen):
    # regression: like dirtrips, a proptrips index.html can be a search-result
    # shell the crawl saved instead of the document -- no div.body-text, no
    # recoverable body. It must import metadata-only, not a trips-route record
    # whose parse would later fail on the missing div.body-text.
    make, out = frozen
    src, entries, downloaded = make("proptrips")
    _entry(entries, "1994-95/1", "1994/95:1",
           orig_url="http://193.188.157.111/prop?dok=P")
    (downloaded / "1994-95" / "1").mkdir(parents=True)
    (downloaded / "1994-95" / "1" / "index.html").write_text(
        "<!DOCTYPE html><html><body><div class='container'>"
        "Totalt 2 träffar</div></body></html>", encoding="utf-8")
    counts = legacy.import_proptrips(src, out, log=lambda *_: None)
    assert counts["imported"] == 1 and counts["metadata_only"] == 1
    assert counts["trips_route"] == 0
    rec = _read_rec(out, "prop", "1994-95-1")
    assert rec["legacy_files"] == [] and "body_format" not in rec


# --- dirtrips (flat downloaded/<year>/<n>.html) ---------------------------

def test_dirtrips_flat_html_body(frozen):
    make, out = frozen
    src, entries, downloaded = make("dirtrips")
    _entry(entries, "1987/10", "1987:10",
           orig_url="http://193.188.157.111/dir?bet=1987:10")
    (downloaded / "1987").mkdir(parents=True)
    (downloaded / "1987" / "10.html").write_text(
        (FIXTURES / "dirtrips_1987.html").read_text(encoding="utf-8"),
        encoding="utf-8")
    counts = legacy.import_dirtrips(src, out, log=lambda *_: None)
    assert counts["trips_route"] == 1
    rec = _read_rec(out, "dir", "1987-10")
    assert rec["type"] == "dir" and rec["identifier"] == "Dir. 1987:10"
    assert rec["body_format"] == "trips"
    assert rec["url"] is None and rec["orig_url"].startswith("http://193.188")
    assert rec["legacy_files"] == ["dirtrips/downloaded/1987/10.html"]


# --- dirasp (downloaded-first index.pdf; pre-2006 stubs never on disk) ------

def test_dirasp_pdf_downloaded_first(frozen):
    # dirasp is walked downloaded-first: only the on-disk 2006-2019 docs appear;
    # a pre-2006 entry-only stub (no downloaded dir) is simply invisible
    make, out = frozen
    src, entries, downloaded = make("dirasp")
    _entry(entries, "2007/23", "2007:23",
           orig_url="http://193.188.157.100/KOMdoc/07/070023.PDF")
    (downloaded / "2007" / "23").mkdir(parents=True)
    (downloaded / "2007" / "23" / "index.pdf").write_bytes(_text_pdf())
    _entry(entries, "1997/1", "1997:1")              # pre-2006 stub: no downloaded dir
    counts = legacy.import_dirasp(src, out, log=lambda *_: None)
    assert counts["imported"] == 1 and counts["pdf_route"] == 1
    rec = _read_rec(out, "dir", "2007-23")
    assert rec["identifier"] == "Dir. 2007:23" and rec["url"] is None
    assert not layout.fa_record_file(out, "dir", "1997-1").exists()   # entry-only stub not minted


def test_dirtrips_search_shell_page_imports_metadata_only(frozen):
    # regression: 5 frozen dirtrips pages (dir 1991:26, 1994:115, 1997:106/112/145)
    # are search-result shells the crawl saved instead of the document -- no
    # div.body-text, no recoverable body. They import metadata-only (legacy_files
    # empty, no body_format) rather than minting a record whose parse can't succeed
    make, out = frozen
    src, entries, downloaded = make("dirtrips")
    _entry(entries, "1991/26", "1991:26",
           orig_url="http://193.188.157.111/dir?bet=1991:26")
    (downloaded / "1991").mkdir(parents=True)
    (downloaded / "1991" / "26.html").write_text(
        "<!DOCTYPE html><html><body><div class='container'>"
        "Totalt 2 träffar</div></body></html>", encoding="utf-8")
    counts = legacy.import_dirtrips(src, out, log=lambda *_: None)
    assert counts["imported"] == 1 and counts["metadata_only"] == 1
    assert counts["trips_route"] == 0
    rec = _read_rec(out, "dir", "1991-26")
    assert rec["legacy_files"] == [] and "body_format" not in rec


def test_dirtrips_corrupt_entry_is_provenance_less_not_fatal(frozen):
    # regression: dirtrips/entries/2006/72.json is corrupt on disk (a doubled
    # tail from an interrupted rewrite) -- a JSON error in a sibling entry means
    # only that its orig_url provenance is unavailable, never an aborted walk
    make, out = frozen
    src, entries, downloaded = make("dirtrips")
    (entries / "2006").mkdir(parents=True)
    (entries / "2006" / "72.json").write_text(
        '{"basefile": null, "orig_url": null}  "summary": null,\n}')
    (downloaded / "2006").mkdir(parents=True)
    (downloaded / "2006" / "72.html").write_text(
        (FIXTURES / "dirtrips_1987.html").read_text(encoding="utf-8"),
        encoding="utf-8")
    counts = legacy.import_dirtrips(src, out, log=lambda *_: None)
    assert counts["imported"] == 1
    assert counts["corrupt_entry"] == 1        # the lost provenance is tallied
    rec = _read_rec(out, "dir", "2006-72")
    assert rec["orig_url"] is None and rec["url"] is None


def test_trips_null_entry_doc_is_recovered_by_path(frozen):
    # the load-bearing deviation: a doc whose sibling entry is null-basefile (the
    # TRIPS sanitizer bug) is still imported, its identity read from the path and
    # its orig_url provenance from the null entry; a stray non-bucket dir is skipped
    make, out = frozen
    src, entries, downloaded = make("proptrips")
    (entries / "2014-15").mkdir(parents=True)
    (entries / "2014-15" / "40.json").write_text(json.dumps(     # null basefile
        {"basefile": None, "title": None,
         "orig_url": "http://193.188.157.111/prop?dok=P&page=141"}))
    (downloaded / "2014-15" / "40").mkdir(parents=True)
    (downloaded / "2014-15" / "40" / "index.pdf").write_bytes(_text_pdf())
    (downloaded / "urls.map").mkdir()                            # sanitizer stray dir
    counts = legacy.import_proptrips(src, out, log=lambda *_: None)
    assert counts["imported"] == 1 and counts["stray_dir"] == 1
    rec = _read_rec(out, "prop", "2014-15-40")
    assert rec["basefile"] == "2014/15:40"           # minted from the path, not entry
    assert rec["identifier"] == "Prop. 2014/15:40"
    assert rec["orig_url"] == "http://193.188.157.111/prop?dok=P&page=141"
    assert rec["url"] is None


# --- SOURCE_RANK / tier interplay at the walk level (gap-window handoff) ----

def _preexisting(out, typ, slug, record):
    p = layout.fa_record_file(out, typ, slug)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(record))


def test_proptrips_pdf_tier_beats_propriksdagen_html(frozen):
    # the 1997/98 upstream-PDF gap: a proptrips pdf (tier 2) replaces an on-disk
    # propriksdagen html-only record (tier 1)
    make, out = frozen
    src, entries, downloaded = make("proptrips")
    _preexisting(out, "prop", "1997-98-45",
                 {"type": "prop", "basefile": "1997/98:45", "source": "propriksdagen",
                  "body_format": "text/tml",
                  "legacy_files": ["propriksdagen/downloaded/1997-98/45/index.html"]})
    _entry(entries, "1997-98/45", "1997/98:45")
    (downloaded / "1997-98" / "45").mkdir(parents=True)
    (downloaded / "1997-98" / "45" / "index.pdf").write_bytes(_text_pdf())
    counts = legacy.import_proptrips(src, out, log=lambda *_: None)
    assert counts["imported"] == 1 and counts["pdf_route"] == 1
    assert _read_rec(out, "prop", "1997-98-45")["source"] == "proptrips"   # replaced


def test_proptrips_html_tier_does_not_beat_propriksdagen_html(frozen):
    # an equal-tier (html) proptrips candidate loses to propriksdagen on rank
    make, out = frozen
    src, entries, downloaded = make("proptrips")
    _preexisting(out, "prop", "1995-96-100",
                 {"type": "prop", "basefile": "1995/96:100", "source": "propriksdagen",
                  "body_format": "text/tml",
                  "legacy_files": ["propriksdagen/downloaded/1995-96/100/index.html"]})
    _entry(entries, "1995-96/100", "1995/96:100")
    (downloaded / "1995-96" / "100").mkdir(parents=True)
    (downloaded / "1995-96" / "100" / "index.html").write_text(
        "<div class='body-text'>text</div>", encoding="utf-8")
    counts = legacy.import_proptrips(src, out, log=lambda *_: None)
    assert counts["imported"] == 0 and counts["skipped_better"] == 1
    assert _read_rec(out, "prop", "1995-96-100")["source"] == "propriksdagen"   # kept


# --- parse routing of the abbyy + trips bodies ----------------------------

def test_parse_abbyy_record_is_page_anchored(frozen):
    make, out = frozen
    src, entries, downloaded = make("propkb")
    (downloaded / "1867" / "23").mkdir(parents=True)
    (downloaded / "1867" / "23" / "index.xml").write_bytes(
        (FIXTURES / "abbyy_propkb.xml").read_bytes())
    record = {"type": "prop", "basefile": "1867:23",
              "identifier": "Prop. 1867:23", "title": "T", "date": None,
              "url": "https://weburn.kb.se/x", "source": "propkb",
              "body_format": "abbyy",
              "legacy_files": ["propkb/downloaded/1867/23/index.xml"]}
    art = parse.to_artifact(parse.parse_record(record, str(out)))
    assert art["uri"] == "https://lagen.nu/prop/1867:23"
    flat = json.dumps(art["structure"], ensure_ascii=False)
    assert '"page": 1' in flat and '"page": 2' in flat   # scan page = #sid anchor
    assert "TABELL SKA HOPPAS" not in flat               # non-Text block skipped


def test_parse_scanned_pdf_falls_back_to_pdftotext(frozen):
    # a soukb-style scan: pdftohtml (the font path) reads nothing, so parse falls
    # back to the pdftotext OCR text -- page-anchored, citation-scanned
    make, out = frozen
    src, entries, downloaded = make("soukb")
    (downloaded / "1945" / "1").mkdir(parents=True)
    (downloaded / "1945" / "1" / "index.pdf").write_bytes(_scan_pdf())
    record = {"type": "sou", "basefile": "1945:1", "identifier": "SOU 1945:1",
              "title": "T", "date": None, "url": "http://urn.kb.se/x",
              "source": "soukb",
              "legacy_files": ["soukb/downloaded/1945/1/index.pdf"]}
    art = parse.to_artifact(parse.parse_record(record, str(out)))
    assert art["structure"], "scanned pdf fell back to an empty body"
    flat = json.dumps(art["structure"])
    assert '"page": 1' in flat                         # form-feed page = #sid anchor
    assert any("1960:729" in u for u in _link_uris(art))   # OCR text was scanned


def test_parse_trips_record_is_page_less(frozen):
    make, out = frozen
    src, entries, downloaded = make("proptrips")
    (downloaded / "1993-94" / "1").mkdir(parents=True)
    (downloaded / "1993-94" / "1" / "index.html").write_text(
        (FIXTURES / "proptrips_1993-94.html").read_text(encoding="utf-8"),
        encoding="utf-8")
    record = {"type": "prop", "basefile": "1993/94:1",
              "identifier": "Prop. 1993/94:1", "title": "T", "date": None,
              "url": None, "source": "proptrips", "body_format": "trips",
              "legacy_files": ["proptrips/downloaded/1993-94/1/index.html"]}
    art = parse.to_artifact(parse.parse_record(record, str(out)))
    assert art["structure"]
    assert '"page"' not in json.dumps(art["structure"])   # page-less html body


# --- the re-OCR sidecar seam ----------------------------------------------

def test_ocr_sidecar_wins_over_legacy_pdf(frozen, monkeypatch):
    # a modern-OCR'd PDF at the sidecar path is parsed instead of the frozen scan
    make, out = frozen
    src, entries, downloaded = make("soukb")
    monkeypatch.setattr(layout, "OCR", out.parent / "ocr")
    (downloaded / "1935" / "14").mkdir(parents=True)
    (downloaded / "1935" / "14" / "index.pdf").write_bytes(PDF_MAGIC)  # weak scan
    sidecar = layout.fa_ocr_pdf("sou", "1935:14")
    sidecar.parent.mkdir(parents=True)
    sidecar.write_bytes(_text_pdf())
    record = {"type": "sou", "basefile": "1935:14", "identifier": "SOU 1935:14",
              "title": "T", "date": None, "url": "http://urn.kb.se/x",
              "source": "soukb",
              "legacy_files": ["soukb/downloaded/1935/14/index.pdf"]}
    art = parse.to_artifact(parse.parse_record(record, str(out)))
    # the sidecar's text layer (not the textless scan) produced the body
    assert "riktig textnivara" in json.dumps(art["structure"], ensure_ascii=False)
