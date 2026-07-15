"""Frozen checks for the F5-safe Tillväxtanalys MTFS browser source."""

import json
from pathlib import Path

from accommodanda.foreskrift import mtfs
from accommodanda.foreskrift.agencies import REGISTRY
from accommodanda.lib import compress
from accommodanda.lib.util import record_path

FILES = Path(__file__).parent / "files" / "mtfs"


def test_parse_index_pairs_heading_with_direct_pdf_and_strips_file_metadata():
    refs = mtfs.parse_index((FILES / "index.html").read_text())
    assert [ref.basefile for ref in refs] == [
        "mtfs/2023:3", "mtfs/2018:2", "mtfs/2016:3",
    ]
    assert refs[0].identifier == "MTFS 2023:3"
    assert refs[0].url.endswith("/MTFS%202023_3%20koncerner.pdf")
    assert refs[0].title == "Föreskrift om statistik om svenska koncerner"
    assert "2009:2" in refs[-1].title


def test_resolve_fetches_browser_pdf_and_writes_direct_layout(tmp_path):
    ref = mtfs.parse_index((FILES / "index.html").read_text())[0]

    class Browser:
        def pdf(self, url):
            assert url == ref.url
            return b"%PDF-1.7\nfixture"

    record = mtfs.resolve(
        Browser(), REGISTRY["mtfs"], ref, tmp_path, rejects=[], log=lambda *_: None,
    )

    assert record["url"] == mtfs.INDEX_URL
    assert record["publisher"].startswith("Myndigheten för tillväxtpolitiska")
    assert compress.read_bytes(tmp_path / "mtfs" / "mtfs-2023-3-regulation.pdf") \
        == b"%PDF-1.7\nfixture"
    stored = json.loads(compress.read_text(record_path(
        tmp_path, "mtfs", "mtfs/2023:3",
    )))
    assert stored["files"]["regulation"]["url"] == ref.url


def test_mtfs_alone_joins_skvfs_on_browser_transport():
    browser_fs = {fs for fs, agency in REGISTRY.items() if agency.browser}
    assert browser_fs == {"skvfs", "mtfs"}
    assert REGISTRY["mtfs"].browser_settle == 20.0
