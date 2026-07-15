"""Frozen checks for the manual F5-safe SKVFS browser importer."""

import json
from pathlib import Path

from accommodanda.foreskrift import skvfs
from accommodanda.foreskrift.agencies import REGISTRY
from accommodanda.lib import compress
from accommodanda.lib.util import record_path

FILES = Path(__file__).parent / "files" / "skvfs"


def test_parse_index_uses_leading_identifier_not_amended_reference():
    refs = skvfs.parse_index((FILES / "index.html").read_text())
    assert [ref.basefile for ref in refs] == [
        "skvfs/2026:7", "skvfs/2026:3", "skvfs/2021:19", "rsfs/2001:23",
    ]
    assert refs[0].identifier == "SKVFS 2026:7"
    assert "SKVFS 2025:29" in refs[0].title
    assert refs[1].identifier == "SKVFS 2026:3"
    assert refs[2].url.endswith("/399821.html?date=2021-11-29")
    assert refs[3].fs == "rsfs" and refs[3].identifier == "RSFS 2001:23"


def test_parse_detail_pdf_selects_the_documents_own_pdf():
    ref = skvfs.parse_index((FILES / "index.html").read_text())[0]
    assert skvfs.parse_detail_pdf(
        (FILES / "detail-2026-7.html").read_text(), ref
    ).endswith("/SKVFS%202026_7.pdf")


def test_save_record_writes_live_layout(tmp_path):
    ref = skvfs.parse_index((FILES / "index.html").read_text())[0]
    detail = (FILES / "detail-2026-7.html").read_text()
    pdf_url = skvfs.parse_detail_pdf(detail, ref)
    record = skvfs.save_record(
        tmp_path, REGISTRY["skvfs"], ref, detail, pdf_url, b"%PDF-1.6\nfixture",
    )

    assert "source" not in record
    assert record["files"]["regulation"]["name"] == "skvfs-2026-7-regulation.pdf"
    assert compress.read_bytes(tmp_path / "skvfs" / "skvfs-2026-7-regulation.pdf") \
        == b"%PDF-1.6\nfixture"
    stored = json.loads(compress.read_text(record_path(
        tmp_path, "skvfs", "skvfs/2026:7"
    )))
    assert stored["identifier"] == "SKVFS 2026:7"
    assert compress.read_text(tmp_path / "skvfs" / "skvfs-2026-7.html") == detail
