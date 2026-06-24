"""Tests for the golden-corpus adjudication overlay (golden_sfs.adjudicate).

The overlay classifies whole families of diffs as new-is-right so they stop
counting as regressions, while leaving every other diff unexplained. These
exercise the two predicates over synthetic golden normal forms + problem
lists -- no corpus needed (the function is pure)."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "golden_sfs", Path(__file__).parent.parent / "tools" / "golden_sfs.py")
golden_sfs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(golden_sfs)

# golden that knew change acts up to 2020:100 -- the freeze horizon
GOLDEN = {"amendments": [{"uri": "https://lagen.nu/2018:585"},
                         {"uri": "https://lagen.nu/2019:9"},
                         {"uri": "https://lagen.nu/2020:100"}]}

IDENT_DIFF = ("metadata.properties.dcterms:identifier changed:\n"
              "  old: 'i lydelse enligt SFS 2020:100'\n"
              "  new: 'i lydelse enligt SFS 2021:50'")


def test_sfs_key_and_horizon():
    assert golden_sfs.sfs_key("amendments: extra https://lagen.nu/2021:50") == (2021, 50)
    assert golden_sfs.sfs_key("no number here") is None
    assert golden_sfs.golden_freeze_horizon(GOLDEN) == (2020, 100)
    assert golden_sfs.golden_freeze_horizon({"amendments": []}) is None


def test_post_freeze_amendment_accepted():
    problems = ["amendments: extra https://lagen.nu/2021:50"]
    unexplained, accepted = golden_sfs.adjudicate(problems, GOLDEN)
    assert unexplained == []
    assert accepted == [("post-freeze-amendment",
                         "amendments: extra https://lagen.nu/2021:50")]


def test_mid_sequence_extra_amendment_not_forgiven():
    # an extra amendment *below* the horizon is not stale-golden -- it is a
    # real divergence (a phantom amendment, or an old-pipeline drop) and must
    # stay unexplained
    problems = ["amendments: extra https://lagen.nu/2019:500"]
    unexplained, accepted = golden_sfs.adjudicate(problems, GOLDEN)
    assert unexplained == problems
    assert accepted == []


def test_structure_diffs_always_unexplained():
    problems = ["structure: missing node K2P1",
                "structure/K3P1: text changed:\n  old: 'a'\n  new: 'b'"]
    unexplained, accepted = golden_sfs.adjudicate(problems, GOLDEN)
    assert unexplained == problems
    assert accepted == []


def test_consolidation_drift_accepted_only_when_stale():
    stale = ["amendments: extra https://lagen.nu/2021:50",
             IDENT_DIFF,
             "metadata.uri: 'https://lagen.nu/2018:585/konsolidering/2020-12-01' "
             "!= 'https://lagen.nu/2018:585/konsolidering/2021-07-01'"]
    unexplained, accepted = golden_sfs.adjudicate(stale, GOLDEN)
    assert unexplained == []
    assert {rule for rule, _ in accepted} == {"post-freeze-amendment",
                                              "stale-consolidation-drift"}

    # the same envelope drift without a post-freeze amendment is NOT forgiven
    unexplained, accepted = golden_sfs.adjudicate([IDENT_DIFF], GOLDEN)
    assert unexplained == [IDENT_DIFF]
    assert accepted == []


def test_non_envelope_metadata_on_stale_doc_stays_unexplained():
    # title-truncation is a separate new-is-right family (not built yet); it
    # must not be swept up by the consolidation-drift rule
    title = "metadata.properties.dcterms:title changed:\n  old: 'A'\n  new: 'A B'"
    problems = ["amendments: extra https://lagen.nu/2021:50", title]
    unexplained, accepted = golden_sfs.adjudicate(problems, GOLDEN)
    assert unexplained == [title]
    assert [rule for rule, _ in accepted] == ["post-freeze-amendment"]


# --- change-reference (ändringshänvisning) staleness --------------------
#
# A paragraf's closing "Lag (NNNN:NN)." names the act that last amended it, as
# an internal link `#L<act>` into the document's own law. The freshly-downloaded
# consolidation has been amended past the golden's freeze horizon, so the same
# stycke now names a later act. golden uri carries the own-law base.

REFS_GOLDEN = {"uri": "https://lagen.nu/1962:700",
               "amendments": [{"uri": "https://lagen.nu/2018:585"},
                              {"uri": "https://lagen.nu/2020:100"}]}  # horizon (2020,100)


def _ref(kind, source, uri):
    return "references: %s %s --dcterms:references--> %s" % (kind, source, uri)


def test_change_ref_helpers():
    assert golden_sfs.reference_diff(
        _ref("extra", "K1P1S1", "https://lagen.nu/1962:700#L2025:1")) == (
        "extra", "K1P1S1", "https://lagen.nu/1962:700#L2025:1")
    # empty source parses to ""
    assert golden_sfs.reference_diff(
        _ref("extra", "", "https://lagen.nu/1962:700#L2025:1"))[1] == ""
    assert golden_sfs.reference_diff("structure: missing K1") is None
    assert golden_sfs.self_change_ref_key(
        "https://lagen.nu/1962:700#L2025:1414", "https://lagen.nu/1962:700") == (2025, 1414)
    # ordinary cross-reference (#K10P5) is not a change-reference
    assert golden_sfs.self_change_ref_key(
        "https://lagen.nu/1962:700#K10P5", "https://lagen.nu/1962:700") is None
    # an #L into a different law is not *this* document's change-reference
    assert golden_sfs.self_change_ref_key(
        "https://lagen.nu/1994:451#L2025:1", "https://lagen.nu/1962:700") is None


def test_change_ref_extra_postfreeze_accepted():
    p = [_ref("extra", "K26P10S2", "https://lagen.nu/1962:700#L2025:1414")]
    unexplained, accepted = golden_sfs.adjudicate(p, REFS_GOLDEN)
    assert unexplained == []
    assert accepted == [("change-reference-staleness", p[0])]


def test_change_ref_empty_source_extra_accepted():
    # a paragraf introduced by the post-freeze amendment: pure extra, no source
    p = [_ref("extra", "", "https://lagen.nu/1962:700#L2025:1414")]
    _, accepted = golden_sfs.adjudicate(p, REFS_GOLDEN)
    assert [rule for rule, _ in accepted] == ["change-reference-staleness"]


def test_change_ref_pre_horizon_extra_not_forgiven():
    # act at/below the freeze horizon is not staleness -- this is the shape a
    # stycke-renumbering diff takes (same old act, different source node)
    p = [_ref("extra", "K13P6S3", "https://lagen.nu/1962:700#L1990:416")]
    unexplained, accepted = golden_sfs.adjudicate(p, REFS_GOLDEN)
    assert unexplained == p and accepted == []


def test_change_ref_missing_forgiven_only_when_same_source_bumped():
    src = "K26P10S2"
    bumped = _ref("extra", src, "https://lagen.nu/1962:700#L2025:1414")
    pre_note = _ref("missing", src, "https://lagen.nu/1962:700#L2019:464")
    unexplained, accepted = golden_sfs.adjudicate([bumped, pre_note], REFS_GOLDEN)
    assert unexplained == []
    assert {rule for rule, _ in accepted} == {"change-reference-staleness"}
    assert len(accepted) == 2


def test_change_ref_missing_without_bump_stays_unexplained():
    # golden has a change-reference the new pipeline dropped, with no post-freeze
    # replacement on the same stycke -- a genuine discrepancy, not staleness
    p = [_ref("missing", "K26P5S2", "https://lagen.nu/1962:700#L2018:1253")]
    unexplained, accepted = golden_sfs.adjudicate(p, REFS_GOLDEN)
    assert unexplained == p and accepted == []


def test_change_ref_missing_bump_is_per_source():
    # a bump on stycke A does not forgive a dropped note on stycke B
    bumped_a = _ref("extra", "K1P1S1", "https://lagen.nu/1962:700#L2025:1414")
    dropped_b = _ref("missing", "K2P2S2", "https://lagen.nu/1962:700#L2019:464")
    unexplained, accepted = golden_sfs.adjudicate([bumped_a, dropped_b], REFS_GOLDEN)
    assert unexplained == [dropped_b]
    assert accepted == [("change-reference-staleness", bumped_a)]


def test_change_ref_external_law_not_forgiven():
    # an #L into another law, even postdating the horizon, is not this
    # document's ändringshänvisning -- a cross-law citation, left to investigate
    p = [_ref("extra", "K1P1S1", "https://lagen.nu/1994:451#L2025:999")]
    unexplained, accepted = golden_sfs.adjudicate(p, REFS_GOLDEN)
    assert unexplained == p and accepted == []


def test_change_ref_ordinary_crossreference_untouched():
    # the "5 §" -> #K10P5 internal cross-reference is a different family
    # (new-is-right but on its own merits, not staleness) -- must stay reported
    p = [_ref("extra", "K10P7S1", "https://lagen.nu/1962:700#K10P5")]
    unexplained, accepted = golden_sfs.adjudicate(p, REFS_GOLDEN)
    assert unexplained == p and accepted == []


# --- balk-basefile-correction (1734 års lag: 1736:0123 1/2) --------------

BALK_GOLDEN = {"uri": "https://lagen.nu/1736:0123_1", "amendments": []}


def test_balk_mirror_pair_forgiven():
    # new mints the corrected full-basefile self-ref; golden has the collapsed
    # form from the same source -> both forgiven as new-is-right
    full = _ref("extra", "K9P1S2", "https://lagen.nu/1736:0123_1#K9P2")
    collapsed = _ref("missing", "K9P1S2", "https://lagen.nu/1736:0123#P2")
    unexplained, accepted = golden_sfs.adjudicate([full, collapsed], BALK_GOLDEN)
    assert unexplained == []
    assert {r for r, _ in accepted} == {"balk-basefile-correction"}
    assert len(accepted) == 2


def test_balk_lone_extra_full_not_forgiven():
    # a full-basefile self-ref with no collapsed counterpart from the same source
    # is an addition, not a correction -> stays visible
    full = [_ref("extra", "K9P1S2", "https://lagen.nu/1736:0123_1#K9P2")]
    unexplained, accepted = golden_sfs.adjudicate(full, BALK_GOLDEN)
    assert unexplained == full and accepted == []


def test_balk_lone_missing_collapsed_not_forgiven():
    # golden has a collapsed self-ref the new pipeline did not replace -> a real
    # drop, stays visible
    collapsed = [_ref("missing", "K9P1S2", "https://lagen.nu/1736:0123#P2")]
    unexplained, accepted = golden_sfs.adjudicate(collapsed, BALK_GOLDEN)
    assert unexplained == collapsed and accepted == []


def test_balk_correlation_is_per_source():
    full = _ref("extra", "K1P1S1", "https://lagen.nu/1736:0123_1#K1P2")
    collapsed = _ref("missing", "K9P9S9", "https://lagen.nu/1736:0123#P2")
    unexplained, accepted = golden_sfs.adjudicate([full, collapsed], BALK_GOLDEN)
    assert set(unexplained) == {full, collapsed} and accepted == []


def test_balk_predicate_inert_on_normal_doc():
    # a non-balk basefile has no collapsed form -> the predicate never fires
    p = [_ref("extra", "K9P1S2", "https://lagen.nu/1962:700#K9P2")]
    unexplained, accepted = golden_sfs.adjudicate(p, REFS_GOLDEN)
    assert unexplained == p and accepted == []


def test_balk_tolerates_trailing_clause():
    # diff lines carry a trailing «clause» (format_ref) -- the predicate must
    # still recover the URI and forgive the pair
    full = _ref("extra", "K9P1S2", "https://lagen.nu/1736:0123_1#K9P2") + "  «2 § …»"
    collapsed = _ref("missing", "K9P1S2", "https://lagen.nu/1736:0123#P2") + "  «2 § …»"
    unexplained, accepted = golden_sfs.adjudicate([full, collapsed], BALK_GOLDEN)
    assert unexplained == [] and len(accepted) == 2


# --- eller-enumeration (single § the old grammar missed) -----------------

ENUM_GOLDEN = {"uri": "https://lagen.nu/1942:740", "amendments": []}


def _refc(kind, source, uri, clause):
    return _ref(kind, source, uri) + "  «%s»" % clause


def test_eller_enum_member_forgiven():
    clause = ("brott som avses i 18 kap. 1, 3, 5 eller 6 § eller 19 kap. "
              "1, 2 eller 13 § brottsbalken")
    p = _refc("extra", "K27P33S3", "https://lagen.nu/1962:700#K18P1", clause)
    unexplained, accepted = golden_sfs.adjudicate([p], ENUM_GOLDEN)
    assert unexplained == []
    assert accepted == [("eller-enumeration", p)]


def test_eller_enum_no_chapter_target():
    # "26 eller 26 a §" -- a paragraf-only target (#P26a), no chapter to check
    clause = "brott som avses i 26 eller 26 a § lagen (2018:558) om företagshemligheter"
    p = _refc("extra", "K27P33S3", "https://lagen.nu/2018:558#P26a", clause)
    _, accepted = golden_sfs.adjudicate([p], ENUM_GOLDEN)
    assert [r for r, _ in accepted] == ["eller-enumeration"]


def test_eller_enum_target_not_in_list_stays():
    clause = "18 kap. 1, 3, 5 eller 6 § brottsbalken"   # 9 is not enumerated
    p = [_refc("extra", "K27P33S3", "https://lagen.nu/1962:700#K18P9", clause)]
    unexplained, accepted = golden_sfs.adjudicate(p, ENUM_GOLDEN)
    assert unexplained == p and accepted == []


def test_och_double_paragraf_not_forgiven():
    # "och … §§" (double §) was parsed by the old grammar -- not a gap
    clause = "enligt 4, 5 och 6 §§ brottsbalken"
    p = [_refc("extra", "K1P1S1", "https://lagen.nu/1962:700#P4", clause)]
    unexplained, accepted = golden_sfs.adjudicate(p, ENUM_GOLDEN)
    assert unexplained == p and accepted == []


def test_eller_enum_wrong_chapter_stays():
    # para 1 is enumerated, but the target chapter (20) is not named in the clause
    clause = "18 kap. 1, 3, 5 eller 6 § brottsbalken"
    p = [_refc("extra", "K1P1S1", "https://lagen.nu/1962:700#K20P1", clause)]
    unexplained, accepted = golden_sfs.adjudicate(p, ENUM_GOLDEN)
    assert unexplained == p and accepted == []


def test_eller_enum_only_extras():
    clause = "18 kap. 1, 3, 5 eller 6 § brottsbalken"
    p = [_refc("missing", "K1P1S1", "https://lagen.nu/1962:700#K18P1", clause)]
    unexplained, accepted = golden_sfs.adjudicate(p, ENUM_GOLDEN)
    assert unexplained == p and accepted == []
