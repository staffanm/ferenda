"""Document extraction fixtures: original occurrences, context and identities."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ferenda.lib import catalog, citationextract, resolve


@pytest.fixture
def con():
    con = sqlite3.connect(":memory:")
    con.executescript(catalog.SCHEMA)
    yield con
    con.close()


def extract(con, text):
    return citationextract.extract([{"id": "text", "text": text}], con)


@pytest.mark.parametrize("fixture", json.loads(
    (Path(__file__).parent / "files/resolve/extraction.json").read_text()))
def test_document_families(con, fixture):
    occurrences = extract(con, fixture["text"])
    assert [[o["text"], [t["uri"].removeprefix("https://lagen.nu/") for t in o["targets"]]]
            for o in occurrences] == fixture["matches"]
    for occurrence in occurrences:
        location, = occurrence["locations"]
        assert fixture["text"].encode("utf-16-le")[
            location["start"] * 2:location["end"] * 2].decode("utf-16-le") == occurrence["text"]
        assert all(t["source"] == resolve.citation_source(t["uri"]) for t in occurrence["targets"])


def test_context_across_blocks_and_request_reset(con):
    found = citationextract.extract([
        {"id": "p1", "text": "lagen (1915:218)."},
        {"id": "p2", "text": "Enligt 36 § samma lag gäller detta."}], con)
    assert found[-1]["targets"] == [{"uri": "https://lagen.nu/1915:218#P36", "source": "sfs"}]
    assert found[-1]["locations"] == [{"block_id": "p2", "start": 7, "end": 21}]
    assert extract(con, "36 § samma lag") == []


def test_split_pdf_citation_and_utf16_offsets(con):
    found, = citationextract.extract([
        {"id": "page-1", "text": "😀 Se NJA 2013 s."},
        {"id": "page-2", "text": "372, enligt domstolen."}], con)
    assert found["text"] == "NJA 2013 s.\n372"
    assert found["locations"] == [
        {"block_id": "page-1", "start": 6, "end": 17},
        {"block_id": "page-2", "start": 0, "end": 3}]


def test_one_occurrence_can_name_multiple_treaties(con):
    found, = extract(con, "Geneva Conventions")
    assert [t["uri"] for t in found["targets"]] == [
        "https://lagen.nu/icrc/365", "https://lagen.nu/icrc/370",
        "https://lagen.nu/icrc/375", "https://lagen.nu/icrc/380"]


def test_treaty_name_across_pdf_line_break(con):
    found, = extract(con, "Se Rome\nStatute article 6.")
    assert found["text"] == "Rome\nStatute article 6"
    assert found["targets"] == [{"uri": "https://lagen.nu/icrc/585#A6", "source": "icrc"}]


def test_parenthesized_canonical_uri_keeps_balanced_identity(con):
    found = extract(con, "(https://lagen.nu/1915:218#P36); (https://lagen.nu/celex/62018CJ0311(01)).")
    assert [o["text"] for o in found] == [
        "https://lagen.nu/1915:218#P36", "https://lagen.nu/celex/62018CJ0311(01)"]
    assert all(o["targets"][0]["uri"] == o["text"] for o in found)


def test_indexed_aliases_preserve_all_targets(con):
    for uri in ["https://lagen.nu/celex/62018CJ0311", "https://lagen.nu/celex/62018CJ0311(01)"]:
        con.execute("INSERT INTO documents(uri, source, path) VALUES (?, 'eurlex', '')", (uri,))
        con.execute("INSERT INTO citation_alias VALUES ('ecli:eu:c:2020:559', ?)", (uri,))
    found, = extract(con, "ECLI:EU:C:2020:559")
    assert len(found["targets"]) == 2


def test_context_is_isolated_between_threads():
    def run(law):
        con = sqlite3.connect(":memory:")
        con.executescript(catalog.SCHEMA)
        try:
            return extract(con, f"lagen ({law}). 1 § samma lag")[-1]["targets"][0]["uri"]
        finally:
            con.close()
    laws = ["1915:218", "1962:700"] * 5
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(run, laws)) == [f"https://lagen.nu/{law}#P1" for law in laws]


@pytest.mark.parametrize("fails", [False, True])
def test_extraction_releases_cached_text_and_context(con, monkeypatch, fails):
    parser = resolve.citation_parser()
    if fails:
        def fail(_query):
            raise ValueError("private text")
        monkeypatch.setattr(resolve, "resolve", fail)
        with pytest.raises(ValueError, match="private text"):
            extract(con, "12 kap. 1 § avtalslagen. NJA 2013 s. 372. private text")
    else:
        extract(con, "12 kap. 1 § avtalslagen. NJA 2013 s. 372. private text")
    assert parser._scan_text == ""
    assert parser.state.lastlaw is None
    assert parser.state.namedlaws == {}
    for cache in (resolve._parsers, resolve._ecj_parsers):
        assert all(p._scan_text == "" and p.state.lastlaw is None for p in vars(cache).values())
