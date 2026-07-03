"""Tests for the frozen-corpus föreskrift import machinery (REWRITE.md §7g pri 6):
the two harvest-blocked corpora SKVFS (Skatteverket) and SOSFS/HSLF-FS
(Socialstyrelsen).

Hermetic: every case builds a small fake frozen ``entries/`` + ``downloaded/``
tree in a tmp dir and points ``config.LEGACY_ROOT`` at it, so nothing touches the
real legacy trees. Covers the entry-walk mapping (null-basefile and konsolidering
skips, the skvfs/rsfs and sosfs/hslffs splits, the hslffs naming decision), the
record shape (point-at-the-bytes ``legacy`` reference vs. metadata-only), the
precedence rule (future live wins; own-import re-run/force), and end-to-end parse
of an imported record (the frozen PDF resolved under LEGACY_ROOT).
"""

import json
from pathlib import Path

import pytest

from accommodanda import config
from accommodanda.foreskrift import legacy, parse
from accommodanda.foreskrift.agencies import REGISTRY
from accommodanda.foreskrift.legacy import _source_tag, import_corpus
from accommodanda.lib import legacy_import

# a real föreskrift regulation PDF, reused as a stand-in body for the frozen tree
REAL_PDF = (Path(__file__).parent / "files" / "repo" / "nfs" / "downloaded"
            / "konsolidering-nfs-2007" / "10" / "index.pdf")
PDF_STUB = b"%PDF-1.4\n%stub body\n"
LANDING_HTML = "<html><body>rattslig vagledning landing page</body></html>"


def _entry(entries_dir, subdir, n, basefile, *, title="En föreskrift",
           orig_url="https://example.se/x.html"):
    """Materialize one frozen entry JSON at entries/<subdir>/<n>.json."""
    d = entries_dir / subdir
    d.mkdir(parents=True, exist_ok=True)
    (d / ("%s.json" % n)).write_text(json.dumps(
        {"basefile": basefile, "title": title, "orig_url": orig_url}),
        encoding="utf-8")


def _doc(downloaded, subdir, n, *, pdf=None, html=None):
    """Materialize the download dir downloaded/<subdir>/<n>/ with body files."""
    d = downloaded / subdir / str(n)
    d.mkdir(parents=True, exist_ok=True)
    if pdf is not None:
        (d / "index.pdf").write_bytes(pdf)
    if html is not None:
        (d / "index.html").write_text(html, encoding="utf-8")


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    """A tmp LEGACY_ROOT with empty ``<corpus>/{entries,downloaded}/`` trees;
    returns a factory (corpus) -> (source_path, entries, downloaded, out_root)."""
    monkeypatch.setattr(config, "LEGACY_ROOT", tmp_path)

    def make(corpus):
        src = tmp_path / corpus
        entries = src / "entries"
        downloaded = src / "downloaded"
        entries.mkdir(parents=True)
        downloaded.mkdir(parents=True)
        return src, entries, downloaded, tmp_path / "out"
    return make


# --- registry: the frozen-only agencies are registered, no live harvester ----

def test_frozen_agencies_registered_without_harvester():
    for fs in ("skvfs", "rsfs", "sosfs", "hslffs"):
        assert fs in REGISTRY
        assert REGISTRY[fs].enumerate is None and REGISTRY[fs].resolve is None
    # the hslffs naming decision: slug hyphen-stripped, printed designation kept
    assert REGISTRY["hslffs"].designation == "HSLF-FS"
    assert REGISTRY["rsfs"].designation == "RSFS"


# --- precedence -------------------------------------------------------------

def test_should_write_new_slot():
    assert legacy_import.should_write(None, _source_tag("skvfs"), force=False) is True


def test_should_write_live_always_wins():
    live = {"fs": "skvfs", "files": {}}                 # no `source` key
    assert legacy_import.should_write(live, _source_tag("skvfs"), force=False) is False
    # force can't beat live
    assert legacy_import.should_write(live, _source_tag("skvfs"), force=True) is False


def test_should_write_own_import_force_semantics():
    own = {"source": "skvfs-legacy"}
    # plain re-run keeps it; --force rewrites
    assert legacy_import.should_write(own, _source_tag("skvfs"), force=False) is False
    assert legacy_import.should_write(own, _source_tag("skvfs"), force=True) is True


def test_should_write_foreign_source_without_tiebreak_asserts():
    # a single-frozen-source vertical passes no `better` tie-break, so a second
    # frozen corpus at the same slot is a programming error, not a silent skip
    with pytest.raises(AssertionError):
        legacy_import.should_write({"source": "other-legacy"}, _source_tag("skvfs"))


# --- the entry walk ---------------------------------------------------------

def test_skvfs_split_null_and_pdf_vs_metadata(frozen):
    src, entries, downloaded, out = frozen("skvfs")
    # a skvfs base with a real PDF body
    _entry(entries, "skvfs-2012", 5, "skvfs/2012:5", title="SKV om nåt")
    _doc(downloaded, "skvfs-2012", 5, pdf=PDF_STUB, html=LANDING_HTML)
    # an rsfs predecessor, html-only landing -> metadata-only record
    _entry(entries, "rsfs-1996", 6, "rsfs/1996:6", title="RSV om nåt")
    _doc(downloaded, "rsfs-1996", 6, html=LANDING_HTML)
    # a failed-download stub (null basefile) -> skipped
    _entry(entries, "skvfs-2004", 38, None)
    _doc(downloaded, "skvfs-2004", 38, html=LANDING_HTML)

    counts = import_corpus("skvfs", src, out, log=lambda *_: None)
    assert (counts["imported"], counts["pdf"], counts["metadata_only"],
            counts["null_stub"]) == (2, 1, 1, 1)

    skv = json.loads((out / "skvfs" / "skvfs-2012-5.json").read_text())
    assert skv["fs"] == "skvfs" and skv["basefile"] == "skvfs/2012:5"
    assert skv["identifier"] == "SKVFS 2012:5"
    assert skv["publisher"] == "Skatteverket"
    assert skv["source"] == "skvfs-legacy"
    # point-at-the-bytes: the record references the frozen PDF in place
    assert skv["files"]["regulation"]["legacy"] == "skvfs/downloaded/skvfs-2012/5/index.pdf"

    rsfs = json.loads((out / "rsfs" / "rsfs-1996-6.json").read_text())
    assert rsfs["fs"] == "rsfs" and rsfs["identifier"] == "RSFS 1996:6"
    assert rsfs["files"]["regulation"] is None         # html-only -> metadata only


def test_sosfs_split_konsolidering_skip_and_hslffs_naming(frozen):
    src, entries, downloaded, out = frozen("sosfs")
    _entry(entries, "sosfs-1998", 13, "sosfs/1998:13")
    _doc(downloaded, "sosfs-1998", 13, pdf=PDF_STUB)
    # HSLF-FS: entry basefile is the hyphen-stripped hslffs slug
    _entry(entries, "hslffs-2016", 39, "hslffs/2016:39",
           title="HSLF-FS 2016:39 Socialstyrelsens föreskrifter …")
    _doc(downloaded, "hslffs-2016", 39, pdf=PDF_STUB)
    # a konsolidering (consolidated text): 3-part namespace -> skipped, counted
    _entry(entries, "konsolidering-sosfs-2006", 9, "konsolidering/sosfs/2006:9")
    _doc(downloaded, "konsolidering-sosfs-2006", 9, html=LANDING_HTML)

    counts = import_corpus("sosfs", src, out, log=lambda *_: None)
    assert (counts["imported"], counts["konsolidering"]) == (2, 1)
    assert not (out / "konsolidering").exists()        # never written as a fs

    hslf = json.loads((out / "hslffs" / "hslffs-2016-39.json").read_text())
    assert hslf["fs"] == "hslffs"                       # slug hyphen-stripped
    assert hslf["identifier"] == "HSLF-FS 2016:39"      # printed designation kept
    assert hslf["publisher"] == "Socialstyrelsen"


def test_mislabelled_pdf_is_metadata_only(frozen):
    src, entries, downloaded, out = frozen("sosfs")
    # an index.pdf that is in fact HTML (the 70 konsolidering "pdf"s, or a .doc):
    # magic-sniffed and rejected -> metadata-only, not a broken body reference
    _entry(entries, "sosfs-2000", 6, "sosfs/2000:6")
    _doc(downloaded, "sosfs-2000", 6, pdf=b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")  # OLE .doc
    counts = import_corpus("sosfs", src, out, log=lambda *_: None)
    assert (counts["imported"], counts["metadata_only"], counts["pdf"]) == (1, 1, 0)
    rec = json.loads((out / "sosfs" / "sosfs-2000-6.json").read_text())
    assert rec["files"]["regulation"] is None


def test_docdir_located_by_entry_path_not_basefile(frozen):
    """A sanitized entry whose download dir name differs from its basefile fs:
    the body is located by the entry's own path, identity by its basefile."""
    src, entries, downloaded, out = frozen("skvfs")
    # downloaded under skvfs-2004/19, but the authoritative identity is rsfs/2001:23
    _entry(entries, "skvfs-2004", 19, "rsfs/2001:23")
    _doc(downloaded, "skvfs-2004", 19, pdf=PDF_STUB)
    import_corpus("skvfs", src, out, log=lambda *_: None)
    rec = json.loads((out / "rsfs" / "rsfs-2001-23.json").read_text())
    assert rec["files"]["regulation"]["legacy"] == "skvfs/downloaded/skvfs-2004/19/index.pdf"


# --- idempotency / precedence over an existing tree -------------------------

def test_reimport_idempotent_and_respects_live_and_force(frozen):
    src, entries, downloaded, out = frozen("skvfs")
    _entry(entries, "skvfs-2012", 5, "skvfs/2012:5")
    _doc(downloaded, "skvfs-2012", 5, pdf=PDF_STUB)

    assert import_corpus("skvfs", src, out, log=lambda *_: None)["imported"] == 1
    # plain re-run: the own-import record is kept untouched
    again = import_corpus("skvfs", src, out, log=lambda *_: None)
    assert (again["imported"], again["skipped_existing"]) == (0, 1)
    # --force rewrites the corpus's own record
    assert import_corpus("skvfs", src, out, force=True, log=lambda *_: None)["imported"] == 1

    # a future live-harvest record (no `source` key) is never overwritten
    recpath = out / "skvfs" / "skvfs-2012-5.json"
    live = json.loads(recpath.read_text())
    del live["source"]
    recpath.write_text(json.dumps(live))
    guarded = import_corpus("skvfs", src, out, force=True, log=lambda *_: None)
    assert (guarded["imported"], guarded["skipped_live"]) == (0, 1)
    assert "source" not in json.loads(recpath.read_text())


def test_limit_caps_the_run(frozen):
    src, entries, downloaded, out = frozen("skvfs")
    for n in (1, 2, 3):
        _entry(entries, "skvfs-2012", n, "skvfs/2012:%d" % n)
        _doc(downloaded, "skvfs-2012", n, pdf=PDF_STUB)
    assert import_corpus("skvfs", src, out, limit=2, log=lambda *_: None)["imported"] == 2


def test_unknown_corpus_asserts(frozen):
    src, _entries, _downloaded, out = frozen("skvfs")
    with pytest.raises(AssertionError, match="unknown föreskrift legacy corpus"):
        import_corpus("mystery", src, out, log=lambda *_: None)


# --- end-to-end parse of an imported record ---------------------------------

def test_parse_imported_record_resolves_frozen_pdf(frozen):
    """The frozen PDF (referenced in place via `legacy`) is resolved under
    LEGACY_ROOT at parse time and yields a real structure + metadata."""
    src, entries, downloaded, out = frozen("skvfs")
    _entry(entries, "skvfs-2012", 5, "skvfs/2012:5", title="SKV")
    # a real föreskrift PDF as the frozen body
    _doc(downloaded, "skvfs-2012", 5, pdf=REAL_PDF.read_bytes())
    import_corpus("skvfs", src, out, log=lambda *_: None)

    record = json.loads((out / "skvfs" / "skvfs-2012-5.json").read_text())
    reg = parse.parse_record(record, str(out))          # root != LEGACY_ROOT
    art = reg.to_artifact()
    assert art["uri"] == "https://lagen.nu/skvfs/2012:5"
    assert art["identifier"] == "SKVFS 2012:5"
    assert art["structure"], "frozen PDF produced no structure"
    assert any(n.get("type") in ("kapitel", "paragraf") for n in art["structure"])


def test_parse_metadata_only_record_yields_artifact(frozen):
    src, entries, downloaded, out = frozen("skvfs")
    _entry(entries, "rsfs-1996", 6, "rsfs/1996:6", title="RSV")
    _doc(downloaded, "rsfs-1996", 6, html=LANDING_HTML)  # no PDF
    import_corpus("skvfs", src, out, log=lambda *_: None)

    record = json.loads((out / "rsfs" / "rsfs-1996-6.json").read_text())
    art = parse.parse_record(record, str(out)).to_artifact()
    assert art["uri"] == "https://lagen.nu/rsfs/1996:6"
    assert art["structure"] == []                        # metadata-only body
    assert art["metadata"]["title"] == "RSV"
