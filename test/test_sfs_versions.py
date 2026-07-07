"""Historical SFS consolidations (accommodanda/sfs/versions.py + the layout
archive rules): enumerating the download archive, recovering version ids from
the three raw generations, and building version artifacts + the sidecar."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from accommodanda.api import app as app_module
from accommodanda.api.app import app
from accommodanda.lib import compress, diff, layout
from accommodanda.sfs import versions

FILES = Path(__file__).parent / "files" / "sfs" / "versions"


# --------------------------------------------------------------------------
# layout: archive path rules + the konsolidering url grammar
# --------------------------------------------------------------------------

def test_version_artifact_paths():
    assert layout.sfs_version_artifact("1998:204", "2003:466") == (
        layout.SFS_ARTIFACT / "archive" / "1998" / "204" / ".versions"
        / "2003" / "466.json")
    # a legacy counter id stays a flat file under .versions/
    assert layout.sfs_version_artifact("1998:204", "11") == (
        layout.SFS_ARTIFACT / "archive" / "1998" / "204" / ".versions"
        / "11.json")


def test_versions_sidecar_is_artifact_sibling():
    sidecar = layout.sfs_versions_sidecar("1998:204")
    assert sidecar == layout.artifact("sfs", "1998:204").with_suffix(
        ".versions.json")


def test_konsolidering_url_roundtrip():
    uri = "https://lagen.nu/1998:204/konsolidering/2003:466"
    rel = layout.page_relpath(uri)
    assert rel == "1998:204/konsolidering/2003:466.html"
    assert layout.page_url(uri) == "/1998:204/konsolidering/2003:466"
    assert layout.url_to_relpath("/1998:204/konsolidering/2003:466") == rel


def test_version_downloads_enumeration_and_json_preference(tmp_path,
                                                           monkeypatch):
    monkeypatch.setattr(layout, "SFS_DOWNLOADED", tmp_path / "downloaded")
    root = tmp_path / "downloaded" / "archive" / "1998" / "204" / ".versions"
    (root / "2003").mkdir(parents=True)
    (root / "2003" / "466.html").write_text("older html")
    (root / "2003" / "466.json").write_text("{}")      # same version, json wins
    (root / "11.html").write_text("counter-keyed")
    (root / "11.html~").write_text("editor junk")      # never a version
    found = layout.sfs_version_downloads("1998:204")
    assert found == [("11", root / "11.html"),
                     ("2003:466", root / "2003" / "466.json")]


def test_version_downloads_empty_without_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(layout, "SFS_DOWNLOADED", tmp_path / "downloaded")
    assert layout.sfs_version_downloads("1998:204") == []


# --------------------------------------------------------------------------
# header parsing + version-id recovery, one fixture per raw generation
# --------------------------------------------------------------------------

def test_archival_header():
    header = versions.archival_header(FILES / "sfst-archival.html")
    assert header["SFS nr"] == "1998:204"
    assert header["Rubrik"] == "Personuppgiftslag (1998:204)"
    assert header["Utfärdad"] == "1998-04-29"
    assert header["Ändring införd"] == "t.o.m. SFS 2003:466"
    assert versions.header_cutoff(header) == "2003:466"


def test_archival_header_wrapped_key_and_value():
    # the Rubrik wraps over several lines (continuations fold into the value)
    # and "Departement/myndighet" wraps its *key* -- the colon line wins
    header = versions.archival_header(FILES / "sfst-wrapped.html")
    assert header["Rubrik"] == (
        "Förordning (1987:85) om underrättelse till Brottsförebyggande "
        "rådet om domar i mål om ansvar för brottsligt förfarande "
        "med narkotika")
    assert header["myndighet"] == "Justitiedepartementet KRIM"
    assert versions.header_cutoff(header) == "2000:1270"


def test_parse_version_archival_sfst():
    recovered, art = versions.parse_version(
        "1998:204", "2003:466", FILES / "sfst-archival.html")
    assert recovered == "2003:466"
    assert art["uri"] == "https://lagen.nu/1998:204/konsolidering/2003:466"
    assert art["version"] == "2003:466"
    props = art["metadata"]["properties"]
    assert props["dcterms:identifier"] == \
        "SFS 1998:204 i lydelse enligt SFS 2003:466"
    assert props["dcterms:title"] == "Personuppgiftslag (1998:204)"
    assert props["rpubl:utfardandedatum"] == "1998-04-29"
    assert art["structure"]                            # the body parsed


def test_parse_version_recovers_counter_id():
    # a legacy counter-keyed archive file ("3.html", utf-8 rättsdatabaser
    # format) names its real cutoff in the header -- the id is recovered
    recovered, art = versions.parse_version(
        "2003:1067", "3", FILES / "rkrattsbaser-counter.html")
    assert recovered == "2017:531"
    assert art["uri"] == "https://lagen.nu/2003:1067/konsolidering/2017:531"
    assert art["structure"]


def test_parse_version_keeps_key_without_cutoff(tmp_path):
    # no "Ändring införd" in the header (an archived base act): the archive
    # key -- even a bare counter -- stays the version id
    raw = (FILES / "sfst-wrapped.html").read_bytes()
    stripped = tmp_path / "7.html"
    stripped.write_bytes(b"\n".join(
        line for line in raw.split(b"\n") if b"ndring inf" not in line))
    recovered, art = versions.parse_version("1987:85", "7", stripped)
    assert recovered == "7"
    assert art["uri"] == "https://lagen.nu/1987:85/konsolidering/7"


def test_version_sort_key():
    ordered = sorted(["2010:1969", "11", "2003:466", "1998:204"],
                     key=layout.sfs_version_key)
    assert ordered == ["11", "1998:204", "2003:466", "2010:1969"]


# --------------------------------------------------------------------------
# build(): artifacts + sidecar, dedup, error recording
# --------------------------------------------------------------------------

@pytest.fixture
def archive(tmp_path, monkeypatch):
    """A temporary sfs data root with an archive holding one statute's
    versions: an explicit SFS-keyed file and a counter-keyed duplicate of a
    different cutoff, plus a corrupt file."""
    monkeypatch.setattr(layout, "SFS_DOWNLOADED", tmp_path / "downloaded")
    monkeypatch.setattr(layout, "SFS_ARTIFACT", tmp_path / "artifact")
    root = tmp_path / "downloaded" / "archive" / "1998" / "204" / ".versions"
    (root / "2003").mkdir(parents=True)
    (root / "2003" / "466.html").write_bytes(
        (FILES / "sfst-archival.html").read_bytes())
    # a counter-keyed copy of the very same consolidation -> duplicate
    (root / "12.html").write_bytes((FILES / "sfst-archival.html").read_bytes())
    (root / "13.html").write_bytes(b"<html>not a statute page</html>")
    return tmp_path


def test_build_writes_artifacts_and_sidecar(archive):
    sidecar = versions.build("1998:204")
    assert [e["version"] for e in sidecar["versions"]] == ["2003:466"]
    assert sidecar["versions"][0]["uri"] == \
        "https://lagen.nu/1998:204/konsolidering/2003:466"
    # the duplicate and the corrupt file are recorded, not retried forever
    skipped = {e["version"]: e for e in sidecar["skipped"]}
    assert skipped["12"]["duplicate_of"] == "2003:466"
    assert "error" in skipped["13"]
    art_path = layout.sfs_version_artifact("1998:204", "2003:466")
    assert compress.exists(art_path)        # stored precompressed (.json.br)
    assert json.loads(compress.read_bytes(art_path))["version"] == "2003:466"
    on_disk = json.loads(layout.sfs_versions_sidecar("1998:204").read_text())
    assert on_disk == sidecar


def test_build_empty_archive_writes_empty_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(layout, "SFS_DOWNLOADED", tmp_path / "downloaded")
    monkeypatch.setattr(layout, "SFS_ARTIFACT", tmp_path / "artifact")
    sidecar = versions.build("1998:204")
    assert sidecar == {"versions": [], "skipped": []}
    assert layout.sfs_versions_sidecar("1998:204").exists()


# --------------------------------------------------------------------------
# the diff endpoint: chronological normalization + the explanatory note
# --------------------------------------------------------------------------

def test_diff_endpoint_normalizes_direction(archive):
    # a second, later consolidation of the same statute (same text, its
    # cutoff rewritten), so from/to can be passed both ways
    later = (FILES / "sfst-archival.html").read_bytes().replace(
        b"2003:466", b"2005:999")
    p = (layout.SFS_DOWNLOADED / "archive" / "1998" / "204" / ".versions"
         / "2005" / "999.html")
    p.parent.mkdir(parents=True)
    p.write_bytes(later)
    versions.build("1998:204")

    client = TestClient(app)
    reversed_args = client.get("/api/v1/document/diff", params={
        "uri": "https://lagen.nu/1998:204", "from": "2005:999",
        "to": "2003:466"})
    assert reversed_args.status_code == 200
    # direction is always older -> newer, whatever the argument order,
    # and the note names both endpoints
    assert ("Ändringar från lydelsen enligt SFS 2003:466 till lydelsen "
            "enligt SFS 2005:999") in reversed_args.text
    forward = client.get("/api/v1/document/diff", params={
        "uri": "https://lagen.nu/1998:204", "from": "2003:466",
        "to": "2005:999"})
    assert forward.text == reversed_args.text


def test_diff_endpoint_rejects_dotdot_version(archive):
    # a version id must be as strictly validated as basefile: no ".." segment,
    # even though it otherwise shapes like a valid "one colon, no slash" id
    client = TestClient(app)
    resp = client.get("/api/v1/document/diff", params={
        "uri": "https://lagen.nu/1998:204", "from": "..:..", "to": "2003:466"})
    assert resp.status_code == 400


def test_diff_endpoint_caches_computed_diff(archive, monkeypatch):
    # two archived consolidations are immutable, so a repeat request for the
    # same (basefile, from, to) triple must not recompute the diff
    later = (FILES / "sfst-archival.html").read_bytes().replace(
        b"2003:466", b"2005:999")
    p = (layout.SFS_DOWNLOADED / "archive" / "1998" / "204" / ".versions"
         / "2005" / "999.html")
    p.parent.mkdir(parents=True)
    p.write_bytes(later)
    versions.build("1998:204")

    app_module._diff_cache.clear()
    calls = []
    real_diff_html = diff.diff_html
    def counting_diff_html(*args, **kwargs):
        calls.append(1)
        return real_diff_html(*args, **kwargs)
    monkeypatch.setattr(diff, "diff_html", counting_diff_html)

    client = TestClient(app)
    params = {"uri": "https://lagen.nu/1998:204", "from": "2003:466",
             "to": "2005:999"}
    first = client.get("/api/v1/document/diff", params=params)
    second = client.get("/api/v1/document/diff", params=params)
    assert first.status_code == second.status_code == 200
    assert first.text == second.text
    assert len(calls) == 1


def test_diff_endpoint_does_not_cache_current_consolidation(archive, monkeypatch):
    # `to` defaults to the current (mutable) consolidation -- that pair must
    # never be served from the cache. Seed a "current" artifact (a copy of the
    # archived one is fine -- diff.diff_html only cares about its shape).
    versions.build("1998:204")
    current_path = layout.artifact("sfs", "1998:204")
    current_path.parent.mkdir(parents=True, exist_ok=True)
    compress.write_bytes(
        current_path,
        compress.read_bytes(layout.sfs_version_artifact("1998:204", "2003:466")))

    app_module._diff_cache.clear()
    calls = []
    real_diff_html = diff.diff_html
    def counting_diff_html(*args, **kwargs):
        calls.append(1)
        return real_diff_html(*args, **kwargs)
    monkeypatch.setattr(diff, "diff_html", counting_diff_html)

    client = TestClient(app)
    params = {"uri": "https://lagen.nu/1998:204", "from": "2003:466"}
    client.get("/api/v1/document/diff", params=params)
    client.get("/api/v1/document/diff", params=params)
    assert len(calls) == 2
    assert ("1998:204", "2003:466", None) not in app_module._diff_cache
