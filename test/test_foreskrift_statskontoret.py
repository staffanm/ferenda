from pathlib import Path
from types import SimpleNamespace

from bs4 import BeautifulSoup

from ferenda.foreskrift import harvest, parse, statskontoret
from ferenda.foreskrift.agencies import REGISTRY
from ferenda.lib import compress
from ferenda.lib.harvest import write_record
from ferenda.lib.util import record_path

FILES = Path(__file__).parent / "files" / "foreskrift"


def records():
    html = (FILES / "statskontoret-sections.html").read_text("utf-8")
    sections = [
        {"type": "allmanna_rad" if section.get("class") == ["allmanna-rad"]
         else "foreskrift", "html": str(section)}
        for section in BeautifulSoup(html, "html.parser").select(
            "div.foreskrifter, div.allmanna-rad")
    ]
    return [
        {
            "fs": "esvfa", "basefile": "esvfa/2022:1",
            "identifier": "ESVFA 2022:1", "title": "Äldre föreskrifter",
            "publisher": "Ekonomistyrningsverket", "url": "https://example/old",
            "updated_at": "2026-09-10 10:00:00", "sections": sections,
        },
        {
            "fs": "stkfa", "basefile": "stkfa/2026:1",
            "identifier": "STKFA 2026:1", "title": "Nya föreskrifter",
            "publisher": "Statskontoret", "url": "https://example/new",
            "updated_at": "2026-09-10 11:00:00", "sections": sections,
        },
    ]


def test_dependency_records_map_to_both_series_newest_first(monkeypatch):
    monkeypatch.setattr(statskontoret, "fetch_foreskrifter", records)

    refs = list(statskontoret.enumerate_regulations(None, REGISTRY["stkfa"]))

    assert [ref.basefile for ref in refs] == ["stkfa/2026:1", "esvfa/2022:1"]
    assert refs[0].fs is None
    assert refs[1].fs == "esvfa"
    assert refs[1].extra["publisher"] == "Ekonomistyrningsverket"


def test_resolve_stores_the_html_as_a_consolidation(tmp_path, monkeypatch):
    monkeypatch.setattr(statskontoret, "fetch_foreskrifter", records)
    agency = REGISTRY["stkfa"]
    ref = list(statskontoret.enumerate_regulations(None, agency))[1]

    record = statskontoret.resolve(None, agency, ref, tmp_path, delay=0)

    assert record["fs"] == "esvfa"
    assert record["files"]["regulation"] is None
    [consolidation] = record["files"]["consolidation"]
    assert consolidation["name"] == "esvfa-2022-1-consolidation.html"
    assert compress.exists(tmp_path / "esvfa" / consolidation["name"])
    assert compress.read_json(record_path(tmp_path, "esvfa", "esvfa/2022:1")) \
        == record


def test_changed_page_timestamp_refreshes_a_stored_record(tmp_path, monkeypatch):
    monkeypatch.setattr(statskontoret, "fetch_foreskrifter", records)
    write_record(record_path(tmp_path, "esvfa", "esvfa/2022:1"), {
        "basefile": "esvfa/2022:1", "updated_at": "2026-09-09 10:00:00",
    })
    freshness = {}

    def walk(refs, *, item_key, **_kwargs):
        for ref in refs:
            freshness[ref.basefile] = item_key(ref).is_downloaded
        return SimpleNamespace(seen=len(freshness), new=0, errors=0)

    monkeypatch.setattr(harvest, "walk", walk)

    harvest._harvest_session(REGISTRY["stkfa"], tmp_path, None, False, None,
                             None, 0, print)

    assert freshness["esvfa/2022:1"] is False


def test_typed_html_parses_binding_advisory_list_and_table(tmp_path):
    name = "2022_1-consolidation.html"
    compress.write_download(tmp_path / "esvfa" / name,
                            (FILES / "statskontoret-sections.html").read_text("utf-8"))
    record = {
        "fs": "esvfa", "basefile": "esvfa/2022:1",
        "identifier": "ESVFA 2022:1", "title": "Äldre föreskrifter",
        "publisher": "Ekonomistyrningsverket", "url": "https://example/old",
        "files": {"regulation": None,
                  "consolidation": [{"name": name, "url": "https://example/old"}],
                  "amendment": [], "memo": [], "attachment": []},
    }

    regulation = parse.parse_record(record, str(tmp_path))

    assert regulation.structure == []
    [consolidation] = regulation.consolidations
    assert consolidation.konsolideradTom == "https://lagen.nu/esvfa/2025:1"

    def nodes(items):
        for item in items:
            yield item
            yield from nodes(item.get("children", []))

    parsed = list(nodes(consolidation.structure))
    assert {node["type"] for node in parsed} >= {
        "kapitel", "paragraf", "lista", "punkt", "tabell", "allmanna_rad"
    }
    assert any(node["type"] == "paragraf" and node.get("ordinal") == "2"
               for node in parsed)
    advice = next(node for node in parsed if node["type"] == "allmanna_rad")
    assert advice["text"][0] == "Allmänna råd till 1 kap. 1 § förordningen"
    assert sum(node["type"] == "allmanna_rad" for node in parsed) == 2
    bare_text = next(node for node in parsed
                     if str(node.get("text", [None])[0]).startswith(
                         "En uppgift bör lämnas så snart som möjligt."))
    assert bare_text["text"][1]["uri"] == "https://lagen.nu/esvfa/2025:1"
