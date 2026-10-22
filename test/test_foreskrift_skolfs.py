"""SKOLFS repeal decisions in the official statute API."""

import json
from pathlib import Path

from ferenda.foreskrift import agencies

FILES = Path(__file__).parent / "files/foreskrift"


def test_skolfs_discovers_repeal_decisions_without_promoting_ordinary_amendments(monkeypatch):
    data = json.loads((FILES / "skolfs-statute-2026.json").read_text())
    api = agencies.SKOLFS.params["api_url"]
    requested = []

    def request(_session, method, url, **kwargs):
        requested.append((method, url))
        assert kwargs == {"parse_json": True}
        return [2026] if url == api + "/statute/years" else data

    monkeypatch.setattr(agencies, "request", request)
    monkeypatch.setattr(agencies.time, "sleep", lambda _delay: None)
    refs = list(agencies.skolfs_enumerate(None, agencies.SKOLFS))
    by_id = {ref.identifier: ref for ref in refs}
    assert len(refs) == len(by_id) == 17
    repeal = by_id["SKOLFS 2026:70"]
    assert repeal.basefile == "skolfs/2026:70"
    assert repeal.url == api + "/document/ANDRINGSFORFATTNING/2026:70/pdf"
    assert repeal.title.startswith("Beslut om upphävande")
    assert repeal.extra["regulation_url"] == repeal.url
    assert repeal.extra["consolidations"] == repeal.extra["amendments"] == []
    hits = [hit for group in data["searchGroups"] for hit in group["searchHits"]]
    ordinary = {"SKOLFS " + hit["skolfsNumber"] for hit in hits
                if hit["documentType"] == "ANDRINGSFORFATTNING"
                and not agencies.RE_REPEAL_NOTICE.match(hit["statuteTitle"])}
    assert ordinary and ordinary.isdisjoint(by_id)
    attached = {entry["identifier"] for ref in refs for entry in ref.extra["amendments"]}
    base_numbers = {hit["skolfsNumber"] for hit in hits
                    if hit["documentType"] in ("GRUNDFORFATTNING", "ALLMANNA_RAD_OVRIGT")}
    assert attached == {"SKOLFS " + hit["skolfsNumber"] for hit in hits
                        if hit["documentType"] == "ANDRINGSFORFATTNING"
                        and hit["baseSkolfsNumber"] in base_numbers}
    for hit in hits:
        if hit["documentType"] == "SENASTE_LYDELSE":
            base = by_id["SKOLFS " + hit["baseSkolfsNumber"]]
            assert base.extra["consolidations"] == [{
                "url": api + "/document/SENASTE_LYDELSE/" + hit["baseSkolfsNumber"] + "/pdf"}]
    assert requested == [("GET", api + "/statute/years"),
                         ("GET", api + "/statute?year=2026")]
