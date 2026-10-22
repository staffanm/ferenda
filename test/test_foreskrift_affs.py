"""AFFS's current bootstrap list and its separately loaded repeal archive."""

import json
from collections import Counter
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ferenda.foreskrift import agencies, harvest
from ferenda.lib import compress
from ferenda.lib.harvest import HarvestWatermark, write_record
from ferenda.lib.stage import POLITENESS
from ferenda.lib.util import record_path

FILES = Path(__file__).parent / "files/foreskrift"
ARCHIVE_URL = ("https://arbetsformedlingen.se/appresource/4.306dde6d1690b54594a1efc/"
               "12.351bd4ea198ca9ea3fc952/getNewItems")


@pytest.fixture
def official_register(monkeypatch):
    page = (FILES / "affs-index.html").read_text()
    archived = json.loads((FILES / "affs-annulment.json").read_text())
    events = []

    def request(_session, method, url, **kwargs):
        events.append((method, url, kwargs))
        if method == "GET":
            assert url == agencies.AFFS.index_url
            return SimpleNamespace(text=page)
        assert method == "POST" and url == ARCHIVE_URL
        assert kwargs == {"json": {
            "searchValue": "", "filterList": "annulment", "sortOrder": False}}
        return SimpleNamespace(json=lambda: archived)

    monkeypatch.setattr(agencies, "request", request)
    monkeypatch.setattr(agencies.time, "sleep", lambda delay: events.append(("sleep", delay)))
    return events


def test_affs_discovers_current_and_archived_records_under_their_own_series(official_register):
    refs = list(agencies.affs_enumerate(None, agencies.AFFS))
    assert len(refs) == len({r.basefile for r in refs}) == 83
    assert Counter(r.basefile.split("/")[0] for r in refs) == {"affs": 42, "amsfs": 41}
    # The current upstream list puts 2025:1 ahead of 2025:2. The watermark
    # requires numeric newest-first order, then the archive behind that list.
    assert [r.identifier for r in refs[:2]] == ["AFFS 2025:2", "AFFS 2025:1"]
    assert refs[10].identifier == "AMSFS 1996:7"
    assert refs[11].identifier == "AFFS 2024:3"
    by_id = {r.identifier: r for r in refs}
    assert by_id["AFFS 2024:3"].basefile == "affs/2024:3"
    assert by_id["AFFS 2024:3"].url.endswith("/upphavanden/affs-20243")
    assert by_id["AMSFS 2007:45"].basefile == "amsfs/2007:45"
    assert by_id["AMSFS 2007:45"].fs == "amsfs"
    assert by_id["AMSFS 2007:45"].url.endswith("/upphavanden/amsfs-200745")
    assert by_id["AFFS 2025:1"].title.startswith("Hur en sökande ska vara anmäld")
    assert by_id["AFFS 2024:3"].title == (
        "Upphävande av (AFFS 2019:1) om underlag för beräkning av stödbelopp.")
    assert official_register == [
        ("GET", agencies.AFFS.index_url, {}), ("sleep", POLITENESS),
        ("POST", ARCHIVE_URL, {"json": {
            "searchValue": "", "filterList": "annulment", "sortOrder": False}}),
        ("sleep", POLITENESS)]


def test_affs_reads_endpoint_identifiers_from_the_page(monkeypatch):
    page = (FILES / "affs-index.html").read_text().replace(
        "4.306dde6d1690b54594a1efc", "4.new-page").replace(
        "12_351bd4ea198ca9ea3fc952", "12_new-portlet")
    requested = []

    def request(_session, method, url, **kwargs):
        if method == "GET":
            return SimpleNamespace(text=page)
        requested.append(url)
        return SimpleNamespace(json=lambda: {"data": []})

    monkeypatch.setattr(agencies, "request", request)
    monkeypatch.setattr(agencies.time, "sleep", lambda _delay: None)
    assert len(list(agencies.affs_enumerate(None, agencies.AFFS))) == 11
    assert requested == [
        "https://arbetsformedlingen.se/appresource/4.new-page/12.new-portlet/getNewItems"]


@pytest.mark.parametrize(("full", "deep", "expected"), [
    (False, False, 0), (False, True, 72), (True, False, 83)])
def test_affs_incremental_stops_before_archive_but_sweeps_reach_it(
        tmp_path, monkeypatch, official_register, full, deep, expected):
    current = agencies.affs_enumerate(None, agencies.AFFS)
    for _ in range(11):
        ref = next(current)
        write_record(record_path(tmp_path, ref.fs or "affs", ref.basefile), {
            "identifier": ref.identifier})
    current.close()
    official_register.clear()
    HarvestWatermark(tmp_path / "affs/.watermark.json").save("2026-10-17")
    resolved = []

    def resolve(_session, _agency, ref, root, delay, **kwargs):
        resolved.append(ref)
        write_record(record_path(root, ref.fs or "affs", ref.basefile), {
            "identifier": ref.identifier})
        return True

    monkeypatch.setattr(harvest, "set_deadline", lambda *_args: None)
    agency = replace(agencies.AFFS, resolve=resolve)
    result = harvest._harvest_session(agency, tmp_path, None, full, deep, None,
                                      None, 0, lambda _msg: None)
    assert result[1] == len(resolved) == expected
    assert [event[0] for event in official_register if event[0] != "sleep"] == (
        ["GET", "POST"] if full or deep else ["GET"])
    if full or deep:
        assert compress.read_json(record_path(tmp_path, "amsfs", "amsfs/2007:45")) == {
            "identifier": "AMSFS 2007:45"}
    assert not compress.read_json(tmp_path / "affs/.watermark.json")["dirty"]


def test_affs_first_harvest_reaches_the_archive(tmp_path, official_register):
    resolved = []

    def resolve(_session, _agency, ref, _root, _delay, **kwargs):
        resolved.append(ref.basefile)
        return True

    agency = replace(agencies.AFFS, resolve=resolve)
    result = harvest._harvest_session(agency, tmp_path, None, False, False, None,
                                      None, 0, lambda _msg: None)
    assert result == (83, 83)
    assert "affs/2024:3" in resolved and "amsfs/2007:45" in resolved
