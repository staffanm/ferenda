"""Tests for the DV identity indexer (entity resolution across the legacy
Word feed and the new courts' API)."""

import hashlib
import json

from accommodanda.dv.identity import (
    build_index,
    canonical_court,
    legacy_identity,
    norm_malnr,
    norm_referat,
    scan_api,
    scan_legacy,
)
from accommodanda.lib import layout


def test_court_canonicalization():
    assert canonical_court("REG") == "REGR"
    assert canonical_court("MÖD") == "MOD"
    assert canonical_court("HYOD") == "HSV"
    assert canonical_court("HDO") == "HDO"  # unchanged


def test_legacy_malnummer_filename():
    assert legacy_identity("HDO", "T2505-99_1.doc") == (["T2505-99"], [])
    assert legacy_identity("HSV", "B10004-10.docx") == (["B10004-10"], [])
    # attachment-variant suffix dropped, notis number kept distinct
    assert legacy_identity("REG", "1-92_1.doc") == (["1-92"], [])


def test_legacy_ado_is_referat():
    # AD encodes the referat (not the målnummer) in the filename
    assert legacy_identity("ADO", "1993-100_1.doc") == ([], ["AD 1993 nr 100"])


def test_legacy_hdo_notisfall():
    assert legacy_identity("HDO", "2003_not_1.doc") == ([], ["NJA 2003 not 1"])


def test_legacy_admin_notisfall_ordinal_is_not_an_attachment_suffix():
    assert legacy_identity("REG", "1994_not_12.doc") == ([], ["RÅ 1994 not 12"])
    assert legacy_identity("HFD", "2016_not_3.docx") == ([], ["HFD 2016 not 3"])


def test_legacy_non_document():
    assert legacy_identity("HDO", "notes.txt") == (None, None)


def test_normalization_matches_across_spacing():
    assert norm_malnr("B 1057-80") == norm_malnr("B1057-80")
    assert norm_referat("NJA 1981 s. 253") == "NJA1981S253"
    # the two citation forms of one case stay distinct keys
    assert norm_referat("NJA 1981:2") != norm_referat("NJA 1981 s. 2")


def api(uuid, court, mal, ref=(), date="1999-01-01", fingerprint=None):
    return {"store": "domstol", "court": court, "path": uuid + ".json",
            "uuid": uuid, "malnummer": list(mal), "referat": list(ref),
            "avgorandedatum": date, "has_innehall": True, "bilagor": 0,
            "semantic_fingerprint": fingerprint}


def legacy(path, court, mal, ref=()):
    return {"store": "dv", "court": court, "path": path,
            "malnummer": list(mal), "referat": list(ref)}


def test_scan_api_skips_watermark_and_stores_relative_path(tmp_path, monkeypatch):
    # the harvest marker (.watermark.json) shares the downloaded/ tree with the
    # records but is not one; scan_api must skip it, not KeyError on d["domstol"]
    monkeypatch.setattr(layout, "DATA", tmp_path)
    dom = tmp_path / "downloaded" / "dom"
    (dom / "ADO").mkdir(parents=True)
    (dom / "ADO" / "u1.json").write_text(json.dumps({
        "domstol": {"domstolKod": "ADO"}, "id": "u1",
        "malNummerLista": ["A 1-99"], "referatNummerLista": ["AD 1999 nr 1"],
        "avgorandedatum": "1999-01-01", "innehall": "x", "bilagaLista": []}))
    (dom / ".watermark.json").write_text(json.dumps({"last_harvest": "2020-01-01"}))
    records = scan_api(dom)
    assert [r["uuid"] for r in records] == ["u1"]           # watermark skipped
    # paths are stored data_root-relative (portable index), not absolute
    assert records[0]["path"] == "downloaded/dom/ADO/u1.json"


def test_scan_legacy_expands_notis_bundle_into_case_identities(tmp_path, monkeypatch):
    monkeypatch.setattr(layout, "DATA", tmp_path)
    dv = tmp_path / "downloaded" / "dv"
    (dv / "HDO").mkdir(parents=True)
    (dv / "HDO" / "2003_not_1.doc").touch()
    bundle = dv / "notis-bundles" / "HDO" / "2003"
    bundle.mkdir(parents=True)
    source = bundle / "HDO_2003_notis_A001--C003.doc"
    source.write_bytes(b"word")
    (dv / "notis-bundles" / "index.json").write_text(json.dumps({
        "version": 1,
        "placeholder_count": 3,
        "bundles": [{
            "path": "HDO/2003/HDO_2003_notis_A001--C003.doc",
            "size": 4,
            "sha256": hashlib.sha256(b"word").hexdigest(),
            "court": "HDO",
            "year": 2003,
            "first": 1,
            "last": 3,
            "ordinals": [1, 2, 3],
        }],
    }))
    records, unrecognized = scan_legacy(dv)
    assert [record["referat"] for record in records] == [
        ["NJA 2003 not 1"], ["NJA 2003 not 2"],
        ["NJA 2003 not 3"], ["NJA 2003 not 1"],
    ]
    assert [record.get("bundle_ordinal") for record in records] == \
        [1, 2, 3, None]
    assert unrecognized == []


def test_scan_legacy_uses_hash_checked_direct_word_identity_index(
        tmp_path, monkeypatch):
    monkeypatch.setattr(layout, "DATA", tmp_path)
    dv = tmp_path / "downloaded/dv"
    source = dv / "HDO/opaque.docx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"word")
    (dv / "legacy-index.json").write_text(json.dumps({
        "version": 1,
        "document_count": 1,
        "documents": [{
            "path": "HDO/opaque.docx",
            "size": 4,
            "sha256": hashlib.sha256(b"word").hexdigest(),
            "court": "HDO",
            "malnummer": ["Ö 2857-09"],
            "referat": ["NJA 2011 s. 838", "NJA 2011:72"],
            "avgorandedatum": "2011-12-07",
        }],
    }))

    records, unrecognized = scan_legacy(dv)

    assert records == [{
        "store": "dv", "court": "HDO",
        "path": "downloaded/dv/HDO/opaque.docx",
        "malnummer": ["Ö 2857-09"],
        "referat": ["NJA 2011 s. 838", "NJA 2011:72"],
        "avgorandedatum": "2011-12-07",
    }]
    assert unrecognized == []


def test_links_on_shared_malnummer():
    # API "B 1057-80" and legacy "B1057-80" are one case
    cases = build_index([api("u1", "HDO", ["B 1057-80"], ["NJA 1981 s. 253"])],
                        [legacy("B1057-80_1.doc", "HDO", ["B1057-80"])])
    assert len(cases) == 1
    assert cases[0]["sources"] == ["domstol", "dv"]
    assert cases[0]["referat"] == ["NJA 1981 s. 253"]


def test_links_on_reconstructed_referat():
    cases = build_index([api("u2", "ADO", ["A 56-20"], ["AD 1993 nr 100"])],
                        [legacy("1993-100.doc", "ADO", [], ["AD 1993 nr 100"])])
    assert len(cases) == 1
    assert cases[0]["sources"] == ["domstol", "dv"]


def test_same_number_different_courts_stay_separate():
    # målnummer keys are court-scoped: no over-linking across courts
    cases = build_index(
        [api("u3", "HSV", ["B 10-93"]), api("u4", "HVS", ["B 10-93"])], [])
    assert len(cases) == 2


def test_attachment_variants_group_into_one_case():
    cases = build_index(
        [api("u5", "HDO", ["T 100-99"])],
        [legacy("T100-99_1.doc", "HDO", ["T100-99"]),
         legacy("T100-99_2.doc", "HDO", ["T100-99"])])
    assert len(cases) == 1
    assert len(cases[0]["members"]) == 3  # one API + two legacy files


def test_same_malnummer_distinct_legacy_referat_stay_separate():
    # One AD proceeding can yield an interim and a final published decision.
    # A shared M must not collapse their distinct referat identities.
    cases = build_index([], [
        legacy("2011-50.docx", "ADO", ["A-205-2010"], ["AD 2011 nr 50"]),
        legacy("2012-19.docx", "ADO", ["A-205-2010"], ["AD 2012 nr 19"]),
    ])
    assert sorted(case["canonical_id"] for case in cases) == [
        "AD 2011 nr 50", "AD 2012 nr 19"]


def test_notis_suffix_is_an_ordinal_not_an_attachment_variant():
    cases = build_index([], [
        legacy("2003_not_1.doc", "HDO", [], ["NJA 2003 not 1"]),
        legacy("2003_not_2.doc", "HDO", [], ["NJA 2003 not 2"]),
    ])
    assert sorted(case["canonical_id"] for case in cases) == [
        "NJA 2003 not 1", "NJA 2003 not 2"]


def test_genuinely_distinct_cases_not_merged():
    cases = build_index(
        [api("u6", "HDO", ["T 1-99"]), api("u7", "HDO", ["T 2-99"])], [])
    assert len(cases) == 2


def test_reused_nonreferat_malnummer_is_date_qualified():
    cases = build_index([
        api("u6a", "HDO", ["B 1-24"], date="2025-01-01"),
        api("u6b", "HDO", ["B 1-24"], date="2025-06-01"),
    ], [])
    assert sorted(case["canonical_id"] for case in cases) == [
        "HDO B 1-24 2025-01-01", "HDO B 1-24 2025-06-01"]


def test_exact_api_republication_is_one_legal_case():
    cases = build_index([
        api("u6c", "MMOD", ["F 1-24"], fingerprint="same"),
        api("u6d", "MMOD", ["F 1-24"], fingerprint="same"),
    ], [])
    assert len(cases) == 1
    assert len(cases[0]["members"]) == 2


def test_same_malnummer_distinct_api_referat_stay_separate():
    # AD can publish two distinct decisions under one case number. M alone must
    # not fuse authoritative API records (the live example is A 112-92:
    # AD 1993 nr 22 and AD 1994 nr 13).
    cases = build_index(
        [api("u8", "ADO", ["A 112-92"], ["AD 1993 nr 22"]),
         api("u9", "ADO", ["A 112-92"], ["AD 1994 nr 13"])], [])
    assert sorted(c["canonical_id"] for c in cases) == [
        "AD 1993 nr 22", "AD 1994 nr 13"]


def test_ambiguous_malnummer_does_not_guess_legacy_api_pair():
    # A legacy filename with no referat key cannot choose between two API
    # decisions sharing its M. Keep all three components instead of attaching
    # the old body to an arbitrary published identifier.
    cases = build_index(
        [api("u10", "HDO", ["T 1-99"], ["NJA 2000 s. 1"]),
         api("u11", "HDO", ["T 1-99"], ["NJA 2001 s. 2"])],
        [legacy("T1-99.doc", "HDO", ["T1-99"])])
    assert len(cases) == 3


def test_malnummer_does_not_bridge_conflicting_referat_components():
    # One API publication can contain two case numbers even though the old feed
    # correctly publishes a separate referat for one of them (RH 2016:61/62).
    # The shared M must not pull that distinct referat into the API component.
    cases = build_index(
        [api("u10b", "HSV", ["ÖH 6975-13", "ÖH 1730-15"], ["RH 2016:62"])],
        [legacy("ÖH6975-13.docx", "HSV", ["ÖH6975-13"], ["RH 2016:61"]),
         legacy("ÖH1730-15.docx", "HSV", ["ÖH1730-15"], ["RH 2016:62"])])
    assert sorted(case["canonical_id"] for case in cases) == [
        "RH 2016:61", "RH 2016:62"]
    assert next(case for case in cases if case["canonical_id"] == "RH 2016:62")[
        "sources"] == ["domstol", "dv"]


def test_shared_nja_lopnummer_does_not_merge_distinct_page_referat():
    # NJA 2016:31 is shared by two published decisions. The page form is the
    # canonical old lagen.nu identity and must keep them separate.
    cases = build_index([
        api("u12", "HDO", ["Ö 1121-15"],
            ["NJA 2016 s. 341", "NJA 2016:31"]),
        api("u13", "HDO", ["T 6237-14"],
            ["NJA 2016 s. 346", "NJA 2016:31"]),
    ], [])
    assert sorted(c["canonical_id"] for c in cases) == [
        "NJA 2016 s. 341", "NJA 2016 s. 346"]


def test_legacy_only_case_is_kept():
    cases = build_index([], [legacy("2003_not_5.doc", "HDO", [],
                                    ["NJA 2003 not 5"])])
    assert len(cases) == 1
    assert cases[0]["sources"] == ["dv"]
    assert cases[0]["canonical_id"] == "NJA 2003 not 5"
