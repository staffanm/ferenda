"""Frozen checks for the F5-safe Tillväxtanalys MTFS browser source."""

import json
from pathlib import Path

import pytest

from ferenda.foreskrift import mtfs
from ferenda.foreskrift.agencies import REGISTRY
from ferenda.lib import compress
from ferenda.lib.errors import UpstreamChanged
from ferenda.lib.util import record_path

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
    assert REGISTRY["mtfs"].browser_pace == 2.0


def test_a_body_that_is_not_a_pdf_writes_nothing(tmp_path):
    """An error page served where the PDF was promised must stop the harvest.
    A raise, not an assert: under `python -O` the check would vanish and the
    HTML would be stored as `<basefile>-regulation.pdf` with an authoritative
    record beside it (rule:errors-drive-retry-use-raise)."""
    ref = mtfs.parse_index((FILES / "index.html").read_text())[0]

    class Browser:
        def pdf(self, url):
            return b"<html><body>503 Service Unavailable</body></html>"

    with pytest.raises(UpstreamChanged, match="is not a PDF"):
        mtfs.resolve(Browser(), REGISTRY["mtfs"], ref, tmp_path,
                     rejects=[], log=lambda *_: None)
    assert not compress.exists(tmp_path / "mtfs" / "mtfs-2023-3-regulation.pdf")
    assert not compress.exists(record_path(tmp_path, "mtfs", "mtfs/2023:3"))
