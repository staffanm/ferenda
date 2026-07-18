"""Tests for the DV identity indexer (entity resolution across the legacy
Word feed and the new courts' API)."""

import json

from accommodanda.dv.identity import (
    build_index,
    canonical_court,
    grupp_map,
    legacy_identity,
    norm_malnr,
    norm_referat,
    scan_api,
)
from accommodanda.lib import layout


def test_court_canonicalization():
    assert canonical_court("REG") == "REGR"
    assert canonical_court("MÖD") == "MOD"
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


def test_legacy_non_document():
    assert legacy_identity("HDO", "notes.txt") == (None, None)


def test_normalization_matches_across_spacing():
    assert norm_malnr("B 1057-80") == norm_malnr("B1057-80")
    assert norm_referat("NJA 1981 s. 253") == "NJA1981S253"
    # the two citation forms of one case stay distinct keys
    assert norm_referat("NJA 1981:2") != norm_referat("NJA 1981 s. 2")


def api(uuid, court, mal, ref=()):
    return {"store": "domstol", "court": court, "path": uuid + ".json",
            "uuid": uuid, "malnummer": list(mal), "referat": list(ref),
            "avgorandedatum": "1999-01-01", "has_innehall": True,
            "bilagor": 0}


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
        "gruppKorrelationsnummer": "g-1",
        "malNummerLista": ["A 1-99"], "referatNummerLista": ["AD 1999 nr 1"],
        "avgorandedatum": "1999-01-01", "innehall": "x", "bilagaLista": []}))
    (dom / ".watermark.json").write_text(json.dumps({"last_harvest": "2020-01-01"}))
    records = scan_api(dom)
    assert [r["uuid"] for r in records] == ["u1"]           # watermark skipped
    # paths are stored data_root-relative (portable index), not absolute
    assert records[0]["path"] == "downloaded/dom/ADO/u1.json"
    # the publication-group key is recorded: grupp_map resolves hanvisade
    # publiceringar through it
    assert records[0]["grupp"] == "g-1"


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


def test_genuinely_distinct_cases_not_merged():
    cases = build_index(
        [api("u6", "HDO", ["T 1-99"]), api("u7", "HDO", ["T 2-99"])], [])
    assert len(cases) == 2


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


def test_grupp_map_resolves_only_unambiguous_groups():
    # g1 is claimed by one canonical case -> resolves; g2 by two (a split
    # publication group) -> dropped rather than guessed; a memberless grupp
    # never appears
    cases = [
        {"canonical_id": "NJA 1981 s. 253",
         "members": [{"grupp": "g1"}, {"grupp": None}]},
        {"canonical_id": "AD 1993 nr 100", "members": [{"grupp": "g2"}]},
        {"canonical_id": "AD 1994 nr 1", "members": [{"grupp": "g2"}]},
        {"canonical_id": "NJA 2003 not 5", "members": [{}]},
    ]
    assert grupp_map(cases) == {"g1": "NJA 1981 s. 253"}
