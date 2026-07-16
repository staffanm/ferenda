"""Manifest selection for the bounded legacy-only DV import."""

import json
from types import SimpleNamespace

from accommodanda.dv import import_legacy
from accommodanda.dv.import_legacy import (
    adjudicate_ambiguous,
    bundle_identity,
    legacy_records,
    partition_adjudicated,
    partition_ambiguous,
    placeholder_identities,
    select_all_bundle_members,
    select_bundle_members,
    select_files,
)


def api(uuid, court, malnummer, referat=()):
    return {"store": "domstol", "court": court, "path": uuid + ".json",
            "uuid": uuid, "malnummer": list(malnummer),
            "referat": list(referat), "avgorandedatum": "2001-01-01",
            "has_innehall": True, "bilagor": 0}


def test_selects_only_cases_absent_from_api():
    rows = [("HDO/T1-00.doc", 10), ("HDO/T2-00.doc", 20)]
    selected = select_files([api("u1", "HDO", ["T1-00"])],
                            legacy_records(rows))
    assert [(item["path"], item["size"]) for item in selected] == [
        ("HDO/T2-00.doc", 20)]


def test_selects_one_nonempty_attachment_variant():
    rows = [("REG/1-92.doc", 0), ("REG/1-92_1.doc", 30),
            ("REG/1-92_2.doc", 40)]
    selected = select_files([], legacy_records(rows))
    assert len(selected) == 1
    assert selected[0]["path"] == "REG/1-92_1.doc"


def test_keeps_zero_byte_notis_as_visible_unresolved_input():
    selected = select_files([], legacy_records([("HDO/2003_not_1.doc", 0)]))
    assert selected[0]["canonical_id"] == "NJA 2003 not 1"
    assert selected[0]["size"] == 0


def test_partitions_unlinked_shared_malnummer_as_ambiguous():
    api_records = [api("u1", "HDO", ["T1-00"], ["NJA 2001 s. 1"]),
                   api("u2", "HDO", ["T1-00"], ["NJA 2001 s. 2"])]
    selected = select_files(api_records,
                            legacy_records([("HDO/T1-00.doc", 10),
                                            ("HDO/T2-00.doc", 20)]))
    confirmed, ambiguous = partition_ambiguous(api_records, selected)
    assert [item["path"] for item in confirmed] == ["HDO/T2-00.doc"]
    assert [item["path"] for item in ambiguous] == ["HDO/T1-00.doc"]


def test_adjudicates_ambiguous_case_by_referat_and_date(tmp_path, monkeypatch):
    original = tmp_path / "HDO" / "T1-00.doc"
    original.parent.mkdir()
    original.write_bytes(b"word")
    monkeypatch.setattr(
        import_legacy.legacy_parser, "parse_legacy_file",
        lambda path: SimpleNamespace(
            malnummer=["T 1-00"], referat=["NJA 2001 s. 1"],
            avgorandedatum="2001-01-01", sammanfattning="Summary"))
    rows = adjudicate_ambiguous(
        [api("u1", "HDO", ["T1-00"], ["NJA 2001 s. 1"])],
        [{"path": "HDO/T1-00.doc", "size": 4, "court": "HDO"}],
        tmp_path)
    assert rows[0]["disposition"] == "api-duplicate"
    assert rows[0]["reason"] == "same-referat-and-date"
    assert rows[0]["api_uuid"] == "u1"


def test_adjudicates_verdict_only_case_by_date_and_summary(tmp_path, monkeypatch):
    original = tmp_path / "PMD" / "PMÖÄ1-00.docx"
    original.parent.mkdir()
    original.write_bytes(b"word")
    data = tmp_path / "data"
    record = data / "downloaded" / "dom" / "u1.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"sammanfattning": "Same   summary"}))
    monkeypatch.setattr(import_legacy.layout, "DATA", data)
    monkeypatch.setattr(
        import_legacy.legacy_parser, "parse_legacy_file",
        lambda path: SimpleNamespace(
            malnummer=["PMÖÄ 1-00"], referat=[],
            avgorandedatum="2001-01-01", sammanfattning="Same summary"))
    candidate = api("unused", "PMOD", ["PMÖÄ1-00"], ["PMÖD 2001:1"])
    candidate["path"] = "downloaded/dom/u1.json"
    rows = adjudicate_ambiguous(
        [candidate],
        [{"path": "PMD/PMÖÄ1-00.docx", "size": 4, "court": "PMOD"}],
        tmp_path)
    assert rows[0]["reason"] == "same-malnummer-date-and-summary"


def test_partition_adjudicated_requires_current_remote_size(tmp_path):
    ledger = tmp_path / "ambiguities.json"
    ledger.write_text(json.dumps({
        "version": 1,
        "cases": [{"legacy_path": "HDO/T1-00.doc", "legacy_size": 4,
                   "disposition": "api-duplicate"}],
    }))
    reviewed, unresolved = partition_adjudicated([
        {"path": "HDO/T1-00.doc", "size": 4},
        {"path": "HDO/T2-00.doc", "size": 5},
    ], ledger)
    assert [item["path"] for item in reviewed] == ["HDO/T1-00.doc"]
    assert [item["path"] for item in unresolved] == ["HDO/T2-00.doc"]


def test_bundle_identity_accepts_historical_range_spellings():
    assert bundle_identity("HDO_2007_notis_001--083.doc") == \
        ("HDO", 2007, 1, 83)
    assert bundle_identity("HFD_2019_notis_ 001-043.docx") == \
        ("HFD", 2019, 1, 43)
    assert bundle_identity("HDO_2003_notis_A001--C054.doc") == \
        ("HDO", 2003, 1, 54)
    assert bundle_identity("HDO_2016_notis_009.docx") == \
        ("HDO", 2016, 9, 9)


def test_placeholder_identities_reads_only_zero_byte_case_files(tmp_path):
    court = tmp_path / "HDO"
    court.mkdir()
    (court / "2003_not_1.doc").touch()
    (court / "2003_not_2.doc").write_bytes(b"not a placeholder")
    assert placeholder_identities(tmp_path) == {("HDO", 2003, 1)}


def test_select_bundle_members_keeps_only_needed_ranges_and_newest_snapshot():
    rows = [
        {"archive": "zips/old.zip", "member": "HDO_2010_notis_001--019.docx",
         "size": 10, "zip_mtime": 1},
        {"archive": "zips/new.zip", "member": "HDO_2010_notis_001--019.docx",
         "size": 11, "zip_mtime": 2},
        {"archive": "zips/other.zip", "member": "HDO_2010_notis_020--035.docx",
         "size": 12, "zip_mtime": 3},
        {"archive": "zips/reg.zip", "member": "REG_2000_notis_001--034.doc",
         "size": 13, "zip_mtime": 4},
    ]
    selected, missing = select_bundle_members(
        rows, {("HDO", 2010, 1), ("HDO", 2010, 60)})
    assert [(row["archive"], row["destination"]) for row in selected] == [
        ("zips/new.zip", "HDO/2010/HDO_2010_notis_001--019.docx")]
    assert missing == {("HDO", 2010, 60)}


def test_select_all_bundle_members_deduplicates_zip_snapshots():
    rows = [
        {"archive": "zips/old.zip", "member": "HDO_2010_notis_001--019.docx",
         "size": 10, "zip_mtime": 1},
        {"archive": "zips/new.zip", "member": "HDO_2010_notis_001--019.docx",
         "size": 11, "zip_mtime": 2},
        {"archive": "zips/junk.zip", "member": "readme.doc",
         "size": 12, "zip_mtime": 3},
    ]
    selected = select_all_bundle_members(rows)
    assert [(row["archive"], row["destination"]) for row in selected] == [
        ("zips/new.zip", "HDO/2010/HDO_2010_notis_001--019.docx")]
