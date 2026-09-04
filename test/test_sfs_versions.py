"""Historical SFS consolidations (ferenda/sfs/versions.py + the layout
archive rules): enumerating the download archive, recovering version ids from
the three raw generations, and building version artifacts + the sidecar."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ferenda.api import app as app_module
from ferenda.api.app import app
from ferenda.lib import compress, diff, layout
from ferenda.lib.errors import SkipDocument
from ferenda.lib.stage import fanout_key, split_fanout_key
from ferenda.sfs import source, versions

FILES = Path(__file__).parent / "files" / "sfs" / "versions"


# --------------------------------------------------------------------------
# layout: archive path rules + the konsolidering url grammar
# --------------------------------------------------------------------------

def test_version_artifact_paths():
    assert layout.sfs_version_artifact("1998:204", "2003:466") == (
        layout.SFS_ARTIFACT / "archive" / "1998" / "204" / ".versions"
        / "2003" / "466.json")
    # a colon-less id -- never minted in practice, a defensive fallback
    # only -- stays a flat file under .versions/
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
    (root / "11.html~").write_text("editor junk")      # never a version
    found = layout.sfs_version_downloads("1998:204")
    assert found == [("2003:466", root / "2003" / "466.json")]


def test_version_downloads_refuses_a_stray_counter_keyed_file(tmp_path,
                                                              monkeypatch):
    # the legacy counter-keyed form ("11.html", no year to nest under) was
    # retired 2026-09-04 -- every one renamed to its recovered id or deleted
    # as a duplicate. A leftover one is not silently dropped like editor
    # junk: it means this environment's archive was never migrated, and
    # silently ignoring it would drop a real archived consolidation from the
    # statute's version history with no diagnostic trail (rule:fail-fast)
    monkeypatch.setattr(layout, "SFS_DOWNLOADED", tmp_path / "downloaded")
    root = tmp_path / "downloaded" / "archive" / "1998" / "204" / ".versions"
    root.mkdir(parents=True)
    (root / "11.html").write_text("unmigrated legacy archive file")
    with pytest.raises(AssertionError, match="never migrated"):
        layout.sfs_version_downloads("1998:204")


def test_version_downloads_empty_without_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(layout, "SFS_DOWNLOADED", tmp_path / "downloaded")
    assert layout.sfs_version_downloads("1998:204") == []


def test_version_downloads_strips_a_leading_underscore_in_a_year_subdir(
        tmp_path, monkeypatch):
    # a real download artifact (confirmed corpus-wide, 2026-09-04:
    # "_1190.html" under 1991:1472/.versions/2006/, "_915.html" under
    # 1992:1300/.versions/2005/) -- stripped, not turned into a space (which
    # corrupted the version id: "2006: 1190" instead of "2006:1190"). A
    # non-leading underscore is a real supplement letter, see the next test.
    monkeypatch.setattr(layout, "SFS_DOWNLOADED", tmp_path / "downloaded")
    root = tmp_path / "downloaded" / "archive" / "1991" / "1472" / ".versions"
    (root / "2006").mkdir(parents=True)
    (root / "2006" / "_1190.html").write_text("stray leading underscore")
    assert layout.sfs_version_downloads("1991:1472") == \
        [("2006:1190", root / "2006" / "_1190.html")]


def test_version_downloads_keeps_a_supplement_letter_as_a_space(
        tmp_path, monkeypatch):
    # a year subdir's stem is not *always* a plain number: 1971:235_B's own
    # current version is filed at .versions/1971/235_B.html (matching its
    # own year) -- an underscore that isn't leading is a real supplement
    # letter, not a download artifact, so it still becomes a space
    monkeypatch.setattr(layout, "SFS_DOWNLOADED", tmp_path / "downloaded")
    root = (tmp_path / "downloaded" / "archive" / "1971" / "235_B"
            / ".versions" / "1971")
    root.mkdir(parents=True)
    (root / "235_B.html").write_text("own current version")
    assert layout.sfs_version_downloads("1971:235_B") == \
        [("1971:235 B", root / "235_B.html")]


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


def test_header_cutoff_reads_the_sources_own_typing_slips():
    # refusing these left the act with no cutoff at all and its whole version
    # history unkeyed. Measured over every header in the corpus, 2026-09-04:
    # 40 gain a cutoff from the dropped "SFS", one stops mis-reading a
    # five-digit year, none else read differently.
    assert versions.header_cutoff(
        {"Ändring införd": "t.o.m SFS 2018:1409"}) == "2018:1409"   # 1962:627
    assert versions.header_cutoff(
        {"Ändring införd": "t.o.m. SFS 2026.1467"}) == "2026:1467"  # 2007:1175
    assert versions.header_cutoff(
        {"Ändring införd": "t.o.m. SFS1986:1067"}) == "1986:1067"   # 1982:785
    assert versions.header_cutoff(
        {"Ändring införd": "t.o.m. SFS 2003: 466"}) == "2003:466"
    # the "SFS" token dropped entirely
    assert versions.header_cutoff(
        {"Ändring införd": "t.o.m. 2014:1584"}) == "2014:1584"      # 2010:769
    # a yearless cutoff is still unusable -- there is no register to resolve it
    assert versions.header_cutoff({"Ändring införd": "t.o.m. SFS 466"}) is None
    assert versions.header_cutoff({}) is None
    # a five-digit year is a source typo, not a cutoff -- refused rather than
    # minting a version id for a year that cannot exist
    assert versions.header_cutoff(
        {"Ändring införd": "t.o.m. SFS 20120:354"}) is None         # 1991:1128


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
    ordered = sorted(["2010:1969", "2003:466", "1998:204"],
                     key=layout.sfs_version_key)
    assert ordered == ["1998:204", "2003:466", "2010:1969"]


# --------------------------------------------------------------------------
# the versions stage: artifacts + sidecar, dedup, error recording
# --------------------------------------------------------------------------

@pytest.fixture
def archive(tmp_path, monkeypatch):
    """A temporary sfs data root with an archive holding one statute's
    versions: an explicit SFS-keyed file, a second explicit key whose own
    header names that very same cutoff (the same consolidation archived
    twice under different keys -- a duplicate), and a corrupt file. The
    statute's own (current) download is a stub -- sfs_list() needs it to
    find the statute at all."""
    monkeypatch.setattr(layout, "SFS_DOWNLOADED", tmp_path / "downloaded")
    monkeypatch.setattr(layout, "SFS_ARTIFACT", tmp_path / "artifact")
    (tmp_path / "downloaded" / "1998").mkdir(parents=True)
    (tmp_path / "downloaded" / "1998" / "204.json").write_text(
        json.dumps({"basefile": "1998:204"}))
    root = tmp_path / "downloaded" / "archive" / "1998" / "204" / ".versions"
    (root / "2003").mkdir(parents=True)
    (root / "2003" / "466.html").write_bytes(
        (FILES / "sfst-archival.html").read_bytes())
    # filed under a different key, but its own header names cutoff 2003:466
    # too -> the same consolidation fetched twice, a duplicate
    (root / "2003" / "467.html").write_bytes(
        (FILES / "sfst-archival.html").read_bytes())
    (root / "2003" / "998.html").write_bytes(b"<html>not a statute page</html>")
    return tmp_path


def _build(basefile):
    """The versions stage over one statute, serially: every fan-out key of
    it, then the sidecar hook -- what `lagen sfs versions <basefile>` runs.
    Mirrors the driver's own per-key isolation (freshness.py's `stage.run`
    try/except): a key that fails to parse is swallowed here too, exactly as
    the sidecar hook's own direct-parse fallback expects to find its
    predicted path still missing. Returns the sidecar dict."""
    for key in source.sfs_version_list():
        if split_fanout_key(key)[0] == basefile:
            try:
                source.sfs_version_run(key)
            except Exception:  # noqa: BLE001,S110 — emulates the driver's own per-doc resilience point (rule:no-catch-log-continue)
                pass
    source.sfs_versions_rebuild_sidecars()
    return json.loads(layout.sfs_versions_sidecar(basefile).read_text())


def test_build_writes_artifacts_and_sidecar(archive):
    sidecar = _build("1998:204")
    assert [e["version"] for e in sidecar["versions"]] == ["2003:466"]
    assert sidecar["versions"][0]["uri"] == \
        "https://lagen.nu/1998:204/konsolidering/2003:466"
    # the duplicate and the corrupt file are recorded, not retried forever
    skipped = {e["version"]: e for e in sidecar["skipped"]}
    assert skipped["2003:467"]["duplicate_of"] == "2003:466"
    assert "error" in skipped["2003:998"]
    art_path = layout.sfs_version_artifact("1998:204", "2003:466")
    assert compress.exists(art_path)        # stored precompressed (.json.br)
    assert json.loads(compress.read_bytes(art_path))["version"] == "2003:466"
    on_disk = json.loads(layout.sfs_versions_sidecar("1998:204").read_text())
    assert on_disk == sidecar


def test_build_empty_archive_writes_empty_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(layout, "SFS_DOWNLOADED", tmp_path / "downloaded")
    monkeypatch.setattr(layout, "SFS_ARTIFACT", tmp_path / "artifact")
    (tmp_path / "downloaded" / "1998").mkdir(parents=True)
    (tmp_path / "downloaded" / "1998" / "204.json").write_text(
        json.dumps({"basefile": "1998:204"}))
    sidecar = _build("1998:204")
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
    _build("1998:204")

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
    _build("1998:204")

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
    _build("1998:204")
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


# --------------------------------------------------------------------------
# the fan-out dispatch (Stage.list_basefiles): one archived consolidation per
# key instead of one job per statute, so a heavily-amended act's history
# spreads across the whole pool instead of one worker parsing it alone
# (inkomstskattelagen's ~100 versions measured 1,454s single-threaded while
# 31 other workers sat idle, 2026-09-04). The `archive` fixture above and
# `_build` (the two phases run serially) cover the assembled result; these
# cover the pieces.
# --------------------------------------------------------------------------

def test_version_list_includes_every_explicit_key(archive):
    # every archive file carries its own ":" key, duplicate or corrupt alike
    # -- the fan-out dispatches all of them; rebuild_sidecars sorts out which
    # ones actually produced a new version (see test_build_writes_artifacts_
    # and_sidecar)
    assert source.sfs_version_list() == [
        "1998:204@2003:466", "1998:204@2003:467", "1998:204@2003:998"]


def test_version_key_output_and_inputs_shape():
    key = fanout_key("1998:204", "2003:466")
    assert key == "1998:204@2003:466"
    assert split_fanout_key(key) == ("1998:204", "2003:466")
    assert source.sfs_version_output(key) == \
        layout.sfs_version_artifact("1998:204", "2003:466")


def test_version_run_writes_the_predicted_output_path(archive):
    key = "1998:204@2003:466"
    source.sfs_version_run(key)
    out = source.sfs_version_output(key)
    assert compress.exists(out)          # exactly where Stage.output() said
    assert json.loads(compress.read_bytes(out))["version"] == "2003:466"


def test_version_run_raises_on_a_removed_archive(archive):
    # the archive is immutable in practice, but a key whose file vanished
    # between listing and running must fail loudly (a normal SkipDocument,
    # not a silent no-op) -- not swallowed the way the sidecar hook's direct
    # parse swallows a corrupt *file* (a different failure: this one is a
    # missing file, not bad content)
    (archive / "downloaded" / "archive" / "1998" / "204" / ".versions"
     / "2003" / "466.html").unlink()
    with pytest.raises(SkipDocument):
        source.sfs_version_run("1998:204@2003:466")


def test_rebuild_sidecars_skips_a_statute_untouched_since_its_last_build(
        archive, monkeypatch):
    # a bare "after" hook has no per-statute freshness check of its own
    # (ensure() never runs it) -- without an explicit skip, every statute's
    # sidecar would be rewritten on every run this hook fires on, cascading
    # false staleness into anything gated on a sidecar's own mtime (the
    # lydelse pages) even for statutes nothing touched
    _build("1998:204")
    before = layout.sfs_versions_sidecar("1998:204").stat().st_mtime_ns

    def boom(*a, **kw):
        raise AssertionError("rebuilt a sidecar nothing changed under")
    monkeypatch.setattr(source.util, "write_json_atomic", boom)
    source.sfs_versions_rebuild_sidecars()      # must return without calling it
    after = layout.sfs_versions_sidecar("1998:204").stat().st_mtime_ns
    assert after == before


def test_rebuild_sidecars_rebuilds_when_a_new_version_is_archived(
        archive):
    first = _build("1998:204")
    assert [e["version"] for e in first["versions"]] == ["2003:466"]

    later = (FILES / "sfst-archival.html").read_bytes().replace(
        b"2003:466", b"2005:999")
    p = (layout.SFS_DOWNLOADED / "archive" / "1998" / "204" / ".versions"
         / "2005" / "999.html")
    p.parent.mkdir(parents=True)
    p.write_bytes(later)
    source.sfs_version_run("1998:204@2005:999")
    source.sfs_versions_rebuild_sidecars()
    second = json.loads(layout.sfs_versions_sidecar("1998:204").read_text())
    assert sorted(e["version"] for e in second["versions"]) == \
        ["2003:466", "2005:999"]


@pytest.fixture
def mislabeled_archive(tmp_path, monkeypatch):
    """A real, pre-existing archive defect (confirmed directly against the
    downloaded archive, 2026-09-04, not a parsing bug): a version file filed
    under a key equal to the statute's own number, whose header actually
    names a later cutoff. 1936:81's own archive carries this exact pattern --
    "Ändring införd: t.o.m SFS 2020:1028" under the key "1936:81", not
    "2020:1028"."""
    monkeypatch.setattr(layout, "SFS_DOWNLOADED", tmp_path / "downloaded")
    monkeypatch.setattr(layout, "SFS_ARTIFACT", tmp_path / "artifact")
    (tmp_path / "downloaded" / "1998").mkdir(parents=True)
    (tmp_path / "downloaded" / "1998" / "204.json").write_text(
        json.dumps({"basefile": "1998:204"}))
    root = tmp_path / "downloaded" / "archive" / "1998" / "204" / ".versions"
    (root / "1998").mkdir(parents=True)
    (root / "1998" / "204.html").write_bytes(
        (FILES / "sfst-archival.html").read_bytes())
    return tmp_path


def test_version_list_includes_a_mislabeled_explicit_key(mislabeled_archive):
    # the key itself has a ":" (it's the statute's own number), so it
    # dispatches through the fan-out same as any other -- the fan-out never
    # opens the file to check, only rebuild_sidecars discovers its content
    # disagrees with its own key
    assert source.sfs_version_list() == ["1998:204@1998:204"]


def test_version_run_writes_under_the_recovered_identity(mislabeled_archive):
    # the predicted output path (Stage.output(), keyed on "1998:204") is
    # never written -- the file's own header names 2003:466, and writing
    # under the content's real identity is correct even though it isn't
    # where the dispatch key predicted
    source.sfs_version_run("1998:204@1998:204")
    predicted = source.sfs_version_output("1998:204@1998:204")
    assert not compress.exists(predicted)
    recovered = layout.sfs_version_artifact("1998:204", "2003:466")
    assert compress.exists(recovered)


def test_rebuild_sidecars_recovers_a_mislabeled_explicit_key(mislabeled_archive):
    # the predicted artifact never appears for this key (see above) -- the
    # sidecar hook must not silently drop the version because of that: it
    # falls through to the same direct-parse fallback regardless of *why*
    # the predicted path is missing (this is the bug caught live against the
    # real corpus, 2026-09-04 -- fixed by no longer special-casing "which
    # kind of miss")
    source.sfs_version_run("1998:204@1998:204")
    source.sfs_versions_rebuild_sidecars()
    sidecar = json.loads(layout.sfs_versions_sidecar("1998:204").read_text())
    assert sidecar["skipped"] == []
    [entry] = sidecar["versions"]
    assert entry["version"] == "2003:466"
    assert entry["archived_as"] == "1998:204"
