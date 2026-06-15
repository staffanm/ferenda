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
    dest = d.Path("/data/sfs")
    assert d.source_path(dest, "2018:585") == dest / "source/2018/585.json"
    assert d.archive_path(dest, "2018:585", "2020:1007") == \
        dest / "source/archive/2018/585/2020_1007.json"


def test_beteckning_with_space_in_number(tmp_path):
    d.save_document(tmp_path, src("1976:725 s.1", None, "x"))
    assert (tmp_path / "source/1976/725_s.1.json").exists()
    assert d.list_basefiles(tmp_path) == ["1976:725 s.1"]


def test_first_download_is_new(tmp_path):
    assert d.save_document(tmp_path, src("2018:585", None, "v1")) == "new"


def test_identical_redownload_is_unchanged(tmp_path):
    s = src("2018:585", "t.o.m. SFS 2020:1007", "v1")
    d.save_document(tmp_path, s)
    assert d.save_document(tmp_path, s) == "unchanged"
    assert not (tmp_path / "archive").exists()


def test_version_bump_archives_old_consolidation(tmp_path):
    d.save_document(tmp_path, src("2018:585", "t.o.m. SFS 2020:1007", "v1"))
    assert d.save_document(
        tmp_path, src("2018:585", "t.o.m. SFS 2025:1472", "v2")) == "updated"
    archived = tmp_path / "source/archive/2018/585/2020_1007.json"
    assert json.loads(archived.read_text())["fulltext"]["forfattningstext"] == "v1"
    current = tmp_path / "source/2018/585.json"
    assert json.loads(current.read_text())["fulltext"]["forfattningstext"] == "v2"


def test_same_version_correction_does_not_archive(tmp_path):
    d.save_document(tmp_path, src("2018:585", "t.o.m. SFS 2025:1472", "v2"))
    assert d.save_document(
        tmp_path, src("2018:585", "t.o.m. SFS 2025:1472", "v2-fixed")) == "updated"
    assert not (tmp_path / "source/archive").exists()
    current = tmp_path / "source/2018/585.json"
    assert json.loads(current.read_text())["fulltext"]["forfattningstext"] == "v2-fixed"


def test_first_amendment_archives_base_under_its_beteckning(tmp_path):
    d.save_document(tmp_path, src("2018:585", None, "base"))
    d.save_document(tmp_path, src("2018:585", "t.o.m. SFS 2020:1007", "amended"))
    archived = tmp_path / "source/archive/2018/585/2018_585.json"
    assert json.loads(archived.read_text())["fulltext"]["forfattningstext"] == "base"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
