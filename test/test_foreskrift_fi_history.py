"""FI's historical base regulations remain available outside its current list."""

from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from ferenda.foreskrift import agencies, harvest
from ferenda.lib import compress
from ferenda.lib.harvest import write_record
from ferenda.lib.util import record_path

FILES = Path(__file__).parent / "files" / "foreskrift"
HISTORICAL_URL = "https://www.fi.se/sv/vara-register/fffs/sok-fffs/2001/20015/"
PDF_URL = "https://www.fi.se/contentassets/48819e0fc0ae49868ac486f9e82abeb8/fffs0105.pdf"


def _serve(monkeypatch):
    pages = {
        agencies.FFFS.index_url: (FILES / "fi-current-base-register.html").read_text(),
        agencies.FFFS.params["archive_url"]: (FILES / "fi-repealed-base-register.html").read_text(),
        HISTORICAL_URL: (FILES / "fi-historical-2001-5.html").read_text(),
    }
    asked, pauses = [], []

    def request(_session, _method, url, **_kwargs):
        asked.append(url)
        if url == PDF_URL:
            return SimpleNamespace(content=b"%PDF-1.4 official historical regulation")
        return SimpleNamespace(text=pages[url], content=pages[url].encode())

    monkeypatch.setattr(harvest, "request", request)
    monkeypatch.setattr(harvest.time, "sleep", pauses.append)
    monkeypatch.setattr(harvest, "set_deadline", lambda *_args: None)
    return asked, pauses


def test_fi_archive_query_uses_the_official_search_forms_explicit_flags():
    form = BeautifulSoup((FILES / "fi-repealed-base-register.html").read_text(),
                         "html.parser").find("form", attrs={"name": "searchForm"})
    flags = {box["name"]: "true" if box.has_attr("checked") else "false"
             for box in form.select('input[type="checkbox"]')}
    query = parse_qs(urlsplit(agencies.FFFS.params["archive_url"]).query)
    assert flags == {key: query[key][0] for key in flags}
    assert flags == {"grundforfattning": "true", "andringsforfattning": "false",
                     "active": "false", "canceled": "true"}


def test_fi_enumerates_historical_base_regulations_from_the_official_register(monkeypatch):
    asked, pauses = _serve(monkeypatch)
    refs = list(agencies.fi_enumerate(None, agencies.FFFS))
    assert [ref.identifier for ref in refs] == ["FFFS 2025:13", "FFFS 2005:11", "FFFS 2001:5"]
    assert refs[-1].url == HISTORICAL_URL
    assert asked == [agencies.FFFS.index_url, agencies.FFFS.params["archive_url"]]
    assert pauses[0] >= 0.3


def test_fi_deep_harvest_fills_a_historical_gap_without_refetching_held_documents(tmp_path, monkeypatch):
    asked, _ = _serve(monkeypatch)
    held = []
    for basefile in ["fffs/2025:13", "fffs/2005:11"]:
        path = record_path(tmp_path, "fffs", basefile)
        write_record(path, {"identifier": "FFFS " + basefile.split("/")[1]})
        held.append((path, compress.stat(path).st_mtime_ns))
    harvest.HarvestWatermark(tmp_path / "fffs" / ".watermark.json").save("2026-10-17")

    # The current base already ends an ordinary incremental run. The archive
    # stays lazy until an operator requests a deep walk.
    result = harvest._harvest_session(agencies.FFFS, tmp_path, None, False, False,
                                      None, None, 0, lambda _msg: None)
    assert result[1] == 0
    assert asked == [agencies.FFFS.index_url]
    asked.clear()
    result = harvest._harvest_session(agencies.FFFS, tmp_path, None, False, True,
                                      None, None, 0, lambda _msg: None)
    assert result[1] == 1
    assert asked == [agencies.FFFS.index_url, agencies.FFFS.params["archive_url"],
                     HISTORICAL_URL, PDF_URL]
    record = compress.read_json(record_path(tmp_path, "fffs", "fffs/2001:5"))
    assert record["identifier"] == "FFFS 2001:5"
    assert record["files"]["regulation"]["url"] == PDF_URL
    assert all(compress.stat(path).st_mtime_ns == stamp for path, stamp in held)
    assert not compress.read_json(tmp_path / "fffs" / ".watermark.json")["dirty"]
