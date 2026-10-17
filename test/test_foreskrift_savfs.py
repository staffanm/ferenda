"""Arbetsgivarverket still publishes six regulations from its SAVFS predecessor."""

from pathlib import Path
from types import SimpleNamespace

from ferenda.foreskrift import harvest
from ferenda.foreskrift.agencies import REGISTRY
from ferenda.foreskrift.model import printed_designation, regulation_uri
from ferenda.lib import compress, facets, layout
from ferenda.lib.lagrum import FORESKRIFT, sfs_parser
from ferenda.lib.pdftext import pdf_first_page_text

FILES = Path(__file__).parent / "files/foreskrift"


def _listed(monkeypatch):
    agency = REGISTRY["agvfs"]
    page = (FILES / "agvfs-index-page2.html").read_text()
    monkeypatch.setattr(harvest, "request", lambda _s, _m, url, **_kw:
                        SimpleNamespace(text=page if "page=2" in url else ""))
    monkeypatch.setattr(harvest.time, "sleep", lambda _delay: None)
    assert agency.enumerate is not None
    return list(agency.enumerate(None, agency))


def test_agvfs_discovers_savfs_without_refiling_agvfs(monkeypatch):
    refs = _listed(monkeypatch)
    assert {r.basefile for r in refs} == {
        "agvfs/2002:3", "agvfs/2001:1", "agvfs/2000:2",
        "savfs/1990:3", "savfs/1984:11", "savfs/1984:6",
        "savfs/1983:11", "savfs/1983:5", "savfs/1979:4"}
    # AgVFS 2000:2 cites SAVFS 1990:3 in its title. The cited base is not
    # the amendment's own designation, and the actual SAVFS remains separate.
    [own] = [r for r in refs if r.basefile == "agvfs/2000:2"]
    assert own.identifier == "AgVFS 2000:2"
    assert all(r.fs == "savfs" and r.identifier.startswith("SAVFS ")
               for r in refs if r.basefile.startswith("savfs/"))
    # SAVFS 1986:11 announces a series split; it is not one of the regulations.
    assert not any(r.basefile.endswith("1986:11") for r in refs)


def test_savfs_landing_stores_the_verified_pdf_under_savfs(monkeypatch, tmp_path):
    refs = _listed(monkeypatch)
    [ref] = [r for r in refs if r.basefile == "savfs/1990:3"]
    landing = (FILES / "savfs-1990-3.html").read_text()
    pdf = (FILES / "savfs-1990-3.pdf").read_bytes()
    requested = []

    def request(_session, _method, url, **_kwargs):
        requested.append(url)
        return SimpleNamespace(text=landing, content=(pdf if url.endswith(".pdf")
                                                     else landing.encode()))

    monkeypatch.setattr(harvest, "request", request)
    agency = REGISTRY["agvfs"]
    assert agency.resolve is not None
    record = agency.resolve(None, agency, ref, tmp_path, delay=0)
    assert record["fs"] == "savfs" and record["basefile"] == "savfs/1990:3"
    assert record["identifier"] == record["files"]["regulation"]["identifier"] == "SAVFS 1990:3"
    assert record["files"]["regulation"]["url"].endswith(
        "savfs-1990-3a1-forordning-om-utbetalning-av-personskadeersattning.pdf")
    assert len(requested) == 2
    assert compress.read_bytes(tmp_path / "savfs" / record["files"]["regulation"]["name"]) == pdf
    assert not (tmp_path / "agvfs").exists()


def test_savfs_routes_and_cites_its_own_document_but_browses_under_agvfs():
    uri = regulation_uri("savfs", "1990", "3")
    assert uri == "https://lagen.nu/savfs/1990:3"
    assert printed_designation(uri) == "SAVFS 1990:3"
    assert layout.page_relpath(uri) == "savfs/1990_3.html"
    assert layout.page_uri(layout.page_url(uri)) == uri
    assert layout.url_to_relpath("/savfs/1990:3") == "savfs/1990_3.html"
    assert facets.fs_live_series("savfs") == "agvfs"
    assert ("savfs", facets.fs_series_info("savfs")) in facets.fs_predecessors("agvfs")
    [ref] = sfs_parser("foreskrift", [FORESKRIFT]).parse_text("SAVFS 1983:11")
    assert ref.uri == "https://lagen.nu/savfs/1983:11"


def test_official_savfs_pdf_prints_the_listed_designation():
    # Official 1990:3 PDF fetched 2026-10-17 from the frozen landing page.
    assert "SAVFS 1990:3" in pdf_first_page_text(FILES / "savfs-1990-3.pdf")
