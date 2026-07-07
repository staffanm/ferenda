"""Unit tests for the SFS downloader's version/archiving logic -- the part
that has no counterpart in the DV downloader. Network-free: drives
save_document with synthetic _source objects.
"""

import json

import pytest

from accommodanda.sfs import download as d


def src(beteckning, andring, body, **extra):
    return {"beteckning": beteckning, "grundforfattningId": 1,
            "fulltext": {"andringInford": andring, "forfattningstext": body},
            **extra}


def test_version_id_from_andring_infall():
    assert d.version_id(src("2018:585", "t.o.m. SFS 2025:1472", "x")) == "2025:1472"


def test_version_id_strips_internal_space():
    assert d.version_id(src("2018:585", "t.o.m. SFS 2025: 1472", "x")) == "2025:1472"


def test_version_id_unamended_act_is_its_own_version():
    assert d.version_id(src("2018:585", None, "x")) == "2018:585"


def test_paths():
    dest = d.Path("/data/downloaded/sfs")
    assert d.source_path(dest, "2018:585") == dest / "2018/585.json"
    # archived versions live in the download dir's own archive/ subtree, in the
    # old site's per-document .versions/{vyear}/{vnr} layout (.json for .html)
    assert d.archive_path(dest, "2018:585", "2020:1007") == \
        dest / "archive/2018/585/.versions/2020/1007.json"


def test_beteckning_with_space_in_number(tmp_path):
    # the space maps to "_" on disk; build.sfs_list maps it back when
    # enumerating basefiles for the driver
    dl = tmp_path / "downloaded"
    d.save_document(dl, src("1976:725 s.1", None, "x"))
    assert (dl / "1976/725_s.1.json").exists()


def test_first_download_is_new(tmp_path):
    assert d.save_document(tmp_path / "downloaded", src("2018:585", None, "v1")) == "new"


def test_identical_redownload_is_unchanged(tmp_path):
    dl = tmp_path / "downloaded"
    s = src("2018:585", "t.o.m. SFS 2020:1007", "v1")
    d.save_document(dl, s)
    assert d.save_document(dl, s) == "unchanged"
    assert not (dl / "archive").exists()


def test_version_bump_archives_old_consolidation(tmp_path):
    dl = tmp_path / "downloaded"
    d.save_document(dl, src("2018:585", "t.o.m. SFS 2020:1007", "v1"))
    assert d.save_document(
        dl, src("2018:585", "t.o.m. SFS 2025:1472", "v2")) == "updated"
    archived = dl / "archive/2018/585/.versions/2020/1007.json"
    assert json.loads(archived.read_text())["fulltext"]["forfattningstext"] == "v1"
    current = dl / "2018/585.json"
    assert json.loads(current.read_text())["fulltext"]["forfattningstext"] == "v2"


def test_same_version_correction_does_not_archive(tmp_path):
    dl = tmp_path / "downloaded"
    d.save_document(dl, src("2018:585", "t.o.m. SFS 2025:1472", "v2"))
    assert d.save_document(
        dl, src("2018:585", "t.o.m. SFS 2025:1472", "v2-fixed")) == "updated"
    assert not (dl / "archive").exists()
    current = dl / "2018/585.json"
    assert json.loads(current.read_text())["fulltext"]["forfattningstext"] == "v2-fixed"


def test_first_amendment_archives_base_under_its_beteckning(tmp_path):
    dl = tmp_path / "downloaded"
    d.save_document(dl, src("2018:585", None, "base"))
    d.save_document(dl, src("2018:585", "t.o.m. SFS 2020:1007", "amended"))
    archived = dl / "archive/2018/585/.versions/2018/585.json"
    assert json.loads(archived.read_text())["fulltext"]["forfattningstext"] == "base"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
