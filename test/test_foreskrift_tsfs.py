"""Transportstyrelsen PDF names with whitespace before the extension."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from ferenda.foreskrift import agencies, harvest

FILES = Path(__file__).parent / "files/foreskrift"


@pytest.mark.parametrize("href", ["/TSFS/TSFS 2010_3 .pdf", "/TSFS/TSFS%202010_3%20.pdf"])
def test_ts_classify_preserves_spaced_pdf_urls(href):
    anchor = BeautifulSoup('<a></a>', "html.parser").a
    assert anchor is not None
    anchor["href"] = href
    assert agencies.ts_classify(anchor, "vvfs", "2003", "22") == ("amendment", "2010", "3")
    assert agencies.ts_classify(anchor, "tsfs", "2010", "3") == ("regulation", "2010", "3")
    assert anchor["href"] == href


def test_ts_resolve_records_spaced_amendment_from_official_family_page(tmp_path, monkeypatch):
    page = (FILES / "tsfs-vvfs-2003-22.html").read_text()
    landing = "https://www.transportstyrelsen.se/details?RuleNumber=2003:22&ruleprefix=VVFS"

    def request(_session, method, url):
        assert (method, url) == ("GET", landing)
        return SimpleNamespace(text=page, content=page.encode())

    monkeypatch.setattr(harvest, "request", request)
    ref = harvest.DocRef(basefile="vvfs/2003:22", fs="vvfs",
                         identifier="VVFS 2003:22", url=landing)
    record = harvest.resolve_landing(None, agencies.TSFS, ref, tmp_path)
    amendments = record["files"]["amendment"]
    assert len(amendments) == 11
    entry = next(item for item in amendments if item["identifier"] == "TSFS 2010:3")
    assert entry["url"] == "https://www.transportstyrelsen.se/TSFS/TSFS 2010_3 .pdf"
    assert record["basefile"] == "vvfs/2003:22"


@pytest.mark.parametrize(("filename", "expected"), [
    ("TSFS 2010_3k .pdf", ("consolidation", "2003", "22")),
    ("TSFS 2010_3_k.pdf", ("consolidation", "2003", "22")),
    ("TSFS 2010_3-notice.pdf", None),
    ("TSFS 2010_3.pdf.exe", None),
    ("bilaga .pdf", None),
])
def test_ts_spaced_names_keep_consolidation_and_companion_classification(filename, expected):
    anchor = BeautifulSoup('<a></a>', "html.parser").a
    assert anchor is not None
    anchor["href"] = "/TSFS/" + filename
    assert agencies.ts_classify(anchor, "vvfs", "2003", "22") == expected
