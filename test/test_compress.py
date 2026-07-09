"""lib/compress: the download-side surface (write_download/download_encodings/
glob/list_basefiles) plus the COMPRESS=off escape hatch. The artifact/page-tree
write_bytes/write_text/read_bytes/read_text/exists/stat round-trip is exercised
indirectly by every other suite that persists artifacts; this file targets the
policy surface added for the raw ``downloaded/`` tree."""

import json

from accommodanda import config
from accommodanda.lib import compress


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
