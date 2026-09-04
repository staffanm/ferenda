"""lib/compress: the download-side surface (write_download/download_encodings/
glob/list_basefiles) plus the COMPRESS=off escape hatch. The artifact/page-tree
write_bytes/write_text/read_bytes/read_text/exists/stat round-trip is exercised
indirectly by every other suite that persists artifacts; this file targets the
policy surface added for the raw ``downloaded/`` tree."""

import json
from pathlib import Path

from ferenda import config
from ferenda.lib import compress


def test_write_download_large_text_stores_only_br(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "COMPRESS", True)
    path = tmp_path / "doc.html"
    payload = "<html>" + ("x" * 600) + "</html>"
    compress.write_download(path, payload)
    assert not path.exists()                    # no plain sibling
    assert (tmp_path / "doc.html.br").exists()
    assert compress.read_text(path) == payload
    assert compress.read_bytes(path) == payload.encode("utf-8")


def test_write_download_incompressible_stores_plain(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "COMPRESS", True)
    for name, payload in (("body.pdf", b"%PDF-1.4" + b"y" * 600),
                          ("bundle.zip", b"PK" + b"z" * 600)):
        path = tmp_path / name
        compress.write_download(path, payload)
        assert path.exists()
        assert not (tmp_path / (name + ".br")).exists()
        assert compress.read_bytes(path) == payload


def test_write_download_small_payload_stores_plain(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "COMPRESS", True)
    path = tmp_path / "tiny.json"
    payload = json.dumps({"basefile": "x"})
    assert len(payload) < compress.MIN_SIZE
    compress.write_download(path, payload)
    assert path.exists()
    assert not (tmp_path / "tiny.json.br").exists()
    assert compress.read_text(path) == payload


def test_download_encodings_extension_policy_case_insensitive():
    assert compress.download_encodings("a/b.HTML") == compress.DOWNLOAD_ENCODINGS
    assert compress.download_encodings("a/b.html") == compress.DOWNLOAD_ENCODINGS
    assert compress.download_encodings("a/b.PDF") == ()
    assert compress.download_encodings("a/b.pdf") == ()
    assert compress.download_encodings("a/b.Zip") == ()
    assert compress.download_encodings("a/b.json") == compress.DOWNLOAD_ENCODINGS


def test_glob_maps_br_variants_back_to_logical_and_dedupes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "COMPRESS", True)
    (tmp_path / "sub").mkdir()
    compress.write_download(tmp_path / "sub" / "a.json",
                            json.dumps({"basefile": "a"}) + " " * 600)
    # a plain sibling for a second basefile, below the compression floor is not
    # representative here -- write a large-enough payload so it too compresses,
    # then also drop one genuinely plain file to prove dedup doesn't need both
    # variants present for the same name to work
    (tmp_path / "sub" / "b.json").write_text(json.dumps({"basefile": "b"}))
    found = compress.glob(tmp_path, "sub/*.json")
    assert found == {tmp_path / "sub" / "a.json", tmp_path / "sub" / "b.json"}


def test_glob_dedupes_when_plain_and_br_variant_coexist(tmp_path):
    # write_bytes always clears the stale sibling, but glob must still dedupe on
    # its own merits (a caller could hand it any directory) -- pass a directory
    # that name-collides on the logical form the two suffix passes would produce
    logical = tmp_path / "x.json"
    (logical.parent / (logical.name + ".br")).write_bytes(b"br-bytes")
    logical.write_text("plain-bytes")
    found = compress.glob(tmp_path, "*.json")
    assert found == {logical}


# --------------------------------------------------------------------------
# a single-component pattern (the common case: eurlex's per-language content
# lookup, list_basefiles' own "*.json") is answered from one os.scandir
# instead of len(SUFFIXES) + 1 separate Path.glob calls over the same
# directory -- eurlex parse's per-document freshness recheck over 170,000
# basefiles measured over a minute on this (2026-09-03). A multi-level
# pattern (sfs/förarbete's own "*/*.json"/"*/*/*.json" basefile listers)
# gets the same treatment, one os.scandir per level: measured 5-10x faster
# than Path.glob over the same real trees, even before multiplying by
# suffix variants (2026-09-04). Only a "**" pattern (none exists in this
# codebase) still goes through Path.glob.
# --------------------------------------------------------------------------

def _glob_via_pathlib(directory, pattern):
    """The pre-optimization algorithm, kept here only as an equivalence
    reference for the tests below -- not production code."""
    root = Path(directory)
    return {compress.logical(p) for suffix in ("", *compress.SUFFIXES)
            for p in root.glob(pattern + suffix)}


def test_glob_single_level_matches_every_suffix_variant(tmp_path):
    (tmp_path / "a.json").write_text("plain")
    (tmp_path / "b.json.br").write_bytes(b"br")
    (tmp_path / "c.json.gz").write_bytes(b"gz")
    (tmp_path / "d.txt").write_text("not json")
    found = compress.glob(tmp_path, "*.json")
    assert found == {tmp_path / "a.json", tmp_path / "b.json", tmp_path / "c.json"}


def test_glob_single_level_matches_dotfiles_like_pathlib_does(tmp_path):
    # unlike shell globbing (and glob.glob's own default), pathlib.Path.glob
    # does not hide dotfiles from a "*" pattern -- confirmed directly against
    # the stdlib, not assumed -- so this must not add an exclusion of its own;
    # compress.list_basefiles relies on glob() handing dotfiles back, since it
    # filters them back out itself rather than expecting glob() to have done so
    (tmp_path / ".watermark.json").write_text("{}")
    (tmp_path / "real.json").write_text("{}")
    assert compress.glob(tmp_path, "*.json") == {tmp_path / ".watermark.json",
                                                 tmp_path / "real.json"}


def test_glob_single_level_missing_directory_returns_empty(tmp_path):
    assert compress.glob(tmp_path / "does-not-exist", "*.json") == set()


def test_glob_single_level_matches_the_pathlib_reference_on_a_mixed_tree(tmp_path):
    # a directory shaped like eurlex's per-document content dir: several
    # candidate languages and formats, a hidden marker file, and a name that
    # only differs by suffix -- the fast path and the old Path.glob-based
    # algorithm must agree on every pattern a real caller tries
    names = ["swe.html", "swe.pdf", "eng.html.br", "eng.xhtml.gz",
            ".watermark.json", "readme.txt", "swe.htmlx"]
    for name in names:
        (tmp_path / name).write_bytes(b"x")
    for pattern in ("swe.*", "eng.*", "*.json", "*.html", "nomatch.*"):
        assert compress.glob(tmp_path, pattern) == _glob_via_pathlib(tmp_path, pattern), pattern


def test_glob_two_level_matches_sfs_s_own_pattern(tmp_path):
    (tmp_path / "2018").mkdir()
    (tmp_path / "2019").mkdir()
    (tmp_path / "2018" / "585.json").write_text("{}")
    (tmp_path / "2019" / "1.json.br").write_bytes(b"x")
    (tmp_path / "2018" / "readme.txt").write_text("not json")
    found = compress.glob(tmp_path, "*/*.json")
    assert found == {tmp_path / "2018" / "585.json", tmp_path / "2019" / "1.json"}


def test_glob_three_level_matches_forarbete_s_own_pattern(tmp_path):
    (tmp_path / "prop" / "2020").mkdir(parents=True)
    (tmp_path / "prop" / "2020" / "1.json").write_text("{}")
    (tmp_path / "sou" / "2021").mkdir(parents=True)
    (tmp_path / "sou" / "2021" / "5.json").write_text("{}")
    found = compress.glob(tmp_path, "*/*/*.json")
    assert found == {tmp_path / "prop" / "2020" / "1.json",
                     tmp_path / "sou" / "2021" / "5.json"}


def test_glob_multi_level_follows_symlinked_directories(tmp_path):
    # Path.glob's own default (confirmed directly against the stdlib) --
    # replicated by hand since os.scandir's is_dir() needs the same choice
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "x.json").write_text("{}")
    (tmp_path / "linked").symlink_to(tmp_path / "real", target_is_directory=True)
    found = compress.glob(tmp_path, "*/*.json")
    assert found == {tmp_path / "real" / "x.json", tmp_path / "linked" / "x.json"}


def test_glob_multi_level_matches_the_pathlib_reference_on_a_mixed_tree(tmp_path):
    (tmp_path / "a" / "sub").mkdir(parents=True)
    (tmp_path / "a" / "sub" / "x.json").write_text("{}")
    (tmp_path / "a" / "sub" / "y.json.br").write_bytes(b"x")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "z.json").write_text("{}")
    (tmp_path / "b" / ".hidden.json").write_text("{}")
    (tmp_path / "c.json").write_text("{}")     # not two levels deep -- must not match
    for pattern in ("*/*.json", "*/sub/*.json", "nomatch/*.json"):
        assert compress.glob(tmp_path, pattern) == _glob_via_pathlib(tmp_path, pattern), pattern


def test_glob_recursive_pattern_still_falls_back_to_pathlib(tmp_path):
    # no caller in this codebase uses "**" today, but the fallback must still
    # work correctly if one ever does
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "x.json").write_text("{}")
    (tmp_path / "x.json").write_text("{}")
    assert compress.glob(tmp_path, "**/*.json") == _glob_via_pathlib(tmp_path, "**/*.json")


def test_list_basefiles_reads_br_records(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "COMPRESS", True)
    subdir = "sfs"
    (tmp_path / subdir).mkdir()
    compress.write_download(tmp_path / subdir / "2018_585.json",
                            json.dumps({"basefile": "2018:585"}) + " " * 600)
    compress.write_download(tmp_path / subdir / "2019_1.json",
                            json.dumps({"basefile": "2019:1"}))
    assert compress.list_basefiles(tmp_path, subdir) == ["2018:585", "2019:1"]


def test_compress_off_stores_plain(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "COMPRESS", False)
    path = tmp_path / "doc.html"
    payload = "<html>" + ("x" * 600) + "</html>"
    compress.write_download(path, payload)
    assert path.exists()
    assert not (tmp_path / "doc.html.br").exists()
    assert compress.read_text(path) == payload


def test_write_download_leaves_unchanged_bytes_alone(tmp_path, monkeypatch):
    # the poppler conversion cache (lib/pdftext._converted) and the build's
    # freshness watermarks both key on the file's mtime, so a re-download that
    # brings back identical bytes must not touch it
    monkeypatch.setattr(config, "COMPRESS", True)
    path = tmp_path / "prop.pdf"
    assert compress.write_download(path, b"%PDF-1.4 body") is True
    before = compress.stat(path).st_mtime_ns

    assert compress.write_download(path, b"%PDF-1.4 body") is False
    assert compress.stat(path).st_mtime_ns == before

    assert compress.write_download(path, b"%PDF-1.4 revised") is True
    assert compress.read_bytes(path) == b"%PDF-1.4 revised"


def test_write_download_compares_the_logical_bytes_not_the_stored_ones(tmp_path,
                                                                       monkeypatch):
    # a compressible payload is stored as .br: the comparison has to happen on
    # the decompressed content, or every re-download would look like a change
    monkeypatch.setattr(config, "COMPRESS", True)
    path = tmp_path / "landing.html"
    payload = "<html>" + ("x" * 600) + "</html>"
    assert compress.write_download(path, payload) is True
    assert (tmp_path / "landing.html.br").exists()
    assert compress.write_download(path, payload) is False
