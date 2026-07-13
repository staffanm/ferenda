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


def test_title_canonicalization_is_only_mechanical_typography():
    assert golden_sfs.canon_title("Lag (1828:79 s. 1553);") == \
        "Lag (1828:79 s.1553)"
    # A spelling correction is semantic evidence and must remain visible.
    assert golden_sfs.canon_title("hörande rättegångar") != \
        golden_sfs.canon_title("rörande rättegångar")


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


def test_structure_diffs_without_candidate_always_unexplained():
    problems = ["structure: missing node K2P1",
                "structure/K3P1: text changed:\n  old: 'a'\n  new: 'b'"]
    unexplained, accepted = golden_sfs.adjudicate(problems, GOLDEN)
    assert unexplained == problems
    assert accepted == []


def test_post_freeze_extra_structure_accepted_with_exact_note():
    extra_amendment = "amendments: extra https://lagen.nu/2021:50"
    extra_node = "structure/K2: extra node K2P4"
    new = {"structure": [{"type": "kapitel", "id": "K2", "children": [
        {"type": "paragraf", "id": "K2P4", "children": [
            {"type": "stycke", "id": "K2P4S1",
             "text": "Den nya bestämmelsen. Lag (2021:50).", "children": []}
        ]}
    ]}]}
    unexplained, accepted = golden_sfs.adjudicate(
        [extra_amendment, extra_node], GOLDEN, new)
    assert unexplained == []
    assert [rule for rule, _ in accepted] == ["post-freeze-amendment",
                                              "post-freeze-structure"]


def test_post_freeze_changed_structure_accepted_via_ancestor_note():
    extra_amendment = "amendments: extra https://lagen.nu/2021:50"
    changed = ("structure/K2/K2P4/K2P4S1: text changed:\n"
               "  old: 'Äldre text.'\n"
               "  new: 'Ny text. Lag (2021:50).'")
    new = {"structure": [{"type": "kapitel", "id": "K2", "children": [
        {"type": "paragraf", "id": "K2P4", "children": [
            {"type": "stycke", "id": "K2P4S1",
             "text": "Ny text. Lag (2021:50).", "children": []}
        ]}
    ]}]}
    unexplained, accepted = golden_sfs.adjudicate(
        [extra_amendment, changed], GOLDEN, new)
    assert unexplained == []
    assert [rule for rule, _ in accepted] == ["post-freeze-amendment",
                                              "post-freeze-structure"]


def test_structure_needs_independent_extra_amendment():
    problem = "structure/K2: extra node K2P4"
    new = {"structure": [{"type": "paragraf", "id": "K2P4",
                          "text": "Lag (2021:50).", "children": []}]}
    unexplained, accepted = golden_sfs.adjudicate([problem], GOLDEN, new)
    assert unexplained == [problem]
    assert accepted == []


def test_structure_ordinary_newer_sfs_citation_is_not_amendment_evidence():
    extra_amendment = "amendments: extra https://lagen.nu/2021:50"
    problem = "structure/K2: extra node K2P4"
    new = {"structure": [{"type": "paragraf", "id": "K2P4",
                          "text": "enligt lagen (2021:50) om exempel",
                          "children": []}]}
    unexplained, accepted = golden_sfs.adjudicate(
        [extra_amendment, problem], GOLDEN, new)
    assert unexplained == [problem]
    assert [rule for rule, _ in accepted] == ["post-freeze-amendment"]


def test_structure_missing_and_order_diffs_stay_for_review():
    extra_amendment = "amendments: extra https://lagen.nu/2021:50"
    missing = "structure/K2: missing node K2P3"
    order = "structure/K2: node order differs"
    new = {"structure": [{"type": "kapitel", "id": "K2",
                          "text": "Lag (2021:50).", "children": []}]}
    unexplained, accepted = golden_sfs.adjudicate(
        [extra_amendment, missing, order], GOLDEN, new)
    assert unexplained == [missing, order]
    assert [rule for rule, _ in accepted] == ["post-freeze-amendment"]


def test_post_freeze_explicit_repeal_accepts_exact_missing_node():
    extra_amendment = "amendments: extra https://lagen.nu/2021:50"
    missing = "structure/K2: missing node K2P3"
    new = {
        "structure": [],
        "amendments": [{
            "uri": "https://lagen.nu/2021:50",
            "properties": {
                "rpubl:upphaver": "https://lagen.nu/2018:585#K2P3",
            },
        }],
    }
    unexplained, accepted = golden_sfs.adjudicate(
        [extra_amendment, missing], GOLDEN, new)
    assert unexplained == []
    assert [rule for rule, _ in accepted] == ["post-freeze-amendment",
                                              "post-freeze-structure"]


def test_repeal_at_or_before_horizon_does_not_accept_missing_node():
    missing = "structure/K2: missing node K2P3"
    new = {
        "structure": [],
        "amendments": [{
            "uri": "https://lagen.nu/2020:100",
            "properties": {
                "rpubl:upphaver": ["https://lagen.nu/2018:585#K2P3"],
            },
        }],
    }
    unexplained, accepted = golden_sfs.adjudicate([missing], GOLDEN, new)
    assert unexplained == [missing]
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


# --- celex-correction (§7d): the old engine scrambled sector-3 CELEX year/number ---

CELEX_GOLDEN = {"uri": "https://lagen.nu/2007:1091", "amendments": []}


def test_celex_descramble_inverts_field_order():
    # correct new form 3+year+type+number  ->  old scramble 3+number+type+year
    assert (golden_sfs.celex_descramble("https://lagen.nu/ext/celex/32017R0625")
            == "https://lagen.nu/ext/celex/3625R2017")
    # zero-padded number collapses; pinpoint fragment is preserved
    assert (golden_sfs.celex_descramble("https://lagen.nu/ext/celex/32016R0679#18")
            == "https://lagen.nu/ext/celex/3679R2016#18")
    # non sector-3 (treaty / case law) is out of scope
    assert golden_sfs.celex_descramble(
        "https://lagen.nu/ext/celex/12012E047") is None
    assert golden_sfs.celex_descramble(
        "https://lagen.nu/1962:700#K2P4") is None


def test_celex_correction_mirror_pair_forgiven():
    new = _ref("extra", "K2P4S1", "https://lagen.nu/ext/celex/32017R0625")
    old = _ref("missing", "K2P4S1", "https://lagen.nu/ext/celex/3625R2017")
    unexplained, accepted = golden_sfs.adjudicate([new, old], CELEX_GOLDEN)
    assert unexplained == []
    assert sorted(r for r, _ in accepted) == ["celex-correction", "celex-correction"]


def test_celex_correction_lone_extra_stays():
    # new mints a correct CELEX the golden never had (no scrambled mirror) --
    # a genuine new-pipeline addition, must stay visible
    p = [_ref("extra", "K3P1S1", "https://lagen.nu/ext/celex/32016R0679")]
    unexplained, accepted = golden_sfs.adjudicate(p, CELEX_GOLDEN)
    assert unexplained == p and accepted == []


def test_celex_correction_lone_missing_stays():
    # golden has a CELEX (here well-formed) the new pipeline dropped: a coverage
    # gap, not a scramble it corrected -- stays visible
    p = [_ref("missing", "K3P1S1", "https://lagen.nu/ext/celex/32016R0679")]
    unexplained, accepted = golden_sfs.adjudicate(p, CELEX_GOLDEN)
    assert unexplained == p and accepted == []


def test_celex_correction_requires_same_source():
    # corrected extra and scrambled miss from *different* stycken do not pair
    new = _ref("extra", "K2P4S1", "https://lagen.nu/ext/celex/32017R0625")
    old = _ref("missing", "K9P9S9", "https://lagen.nu/ext/celex/3625R2017")
    unexplained, accepted = golden_sfs.adjudicate([new, old], CELEX_GOLDEN)
    assert sorted(unexplained) == sorted([new, old]) and accepted == []


def test_celex_old_scrambles_adds_directive_letter_flip():
    # a directive also has the old engine's "defaulted to R" scramble as a variant
    assert golden_sfs.celex_old_scrambles(
        "https://lagen.nu/ext/celex/32018L1808") == {
            "https://lagen.nu/ext/celex/31808L2018",   # plain year/number swap
            "https://lagen.nu/ext/celex/31808R2018"}   # swap + directive->regulation
    # a regulation has only the plain swap (no spurious L variant)
    assert golden_sfs.celex_old_scrambles(
        "https://lagen.nu/ext/celex/32017R0625") == {
            "https://lagen.nu/ext/celex/3625R2017"}


def test_celex_correction_forgives_directive_letter_flip():
    # new mints the correct directive 32018L1808; the old pipeline rendered the
    # bare "direktiv (EU) 2018/1808" as a scrambled *regulation* 31808R2018
    new = _ref("extra", "K2P4S1", "https://lagen.nu/ext/celex/32018L1808")
    old = _ref("missing", "K2P4S1", "https://lagen.nu/ext/celex/31808R2018")
    unexplained, accepted = golden_sfs.adjudicate([new, old], CELEX_GOLDEN)
    assert unexplained == []
    assert sorted(r for r, _ in accepted) == ["celex-correction", "celex-correction"]


# --- golden-chapter-collapse (TOC mis-read as chapter openings) ----------
#
# The old pipeline mis-read a chapter list ("N kap. - Title" lines) as chapter
# openings and dumped the body into one chapter; the new pipeline distributes
# them. The golden's structure shows the collapse (one chapter holds nearly
# every paragraf), which gates the rule.

def _kap(cid, npara):
    return {"type": "kapitel", "id": cid,
            "children": [{"type": "paragraf", "children": []}
                         for _ in range(npara)]}


# K1=1, K2=1, K9=8 -> >=3 chapters, 80% in one chapter -> collapsed
COLLAPSE_GOLDEN = {"uri": "https://lagen.nu/2022:964", "amendments": [],
                   "structure": [_kap("K1", 1), _kap("K2", 1), _kap("K9", 8)]}


def test_collapse_detected_from_structure():
    assert golden_sfs.golden_chapter_collapsed(COLLAPSE_GOLDEN) is True
    # an evenly distributed golden is not collapsed
    even = {"structure": [_kap("K1", 3), _kap("K2", 3), _kap("K3", 4)]}
    assert golden_sfs.golden_chapter_collapsed(even) is False


def test_collapse_self_paragraf_mirror_forgiven():
    # golden read "5 §" from the collapse chapter K9; new distributed it to K3.
    # Same continuous paragraf number, different chapter -> a mirror, forgiven.
    collapsed = _ref("missing", "K9P5S1", "https://lagen.nu/2022:964#K9P3")
    distributed = _ref("extra", "K3P5S1", "https://lagen.nu/2022:964#K3P3")
    unexplained, accepted = golden_sfs.adjudicate(
        [collapsed, distributed], COLLAPSE_GOLDEN)
    assert unexplained == []
    assert {r for r, _ in accepted} == {"golden-chapter-collapse"}
    assert len(accepted) == 2


def test_collapse_external_target_mirror_forgiven():
    # an external-law target is identical on both sides; only the source chapter
    # differs -> still a mirror once the source chapter is stripped
    collapsed = _ref("missing", "K9P5S1", "https://lagen.nu/2005:551#K11P2")
    distributed = _ref("extra", "K3P5S1", "https://lagen.nu/2005:551#K11P2")
    unexplained, accepted = golden_sfs.adjudicate(
        [collapsed, distributed], COLLAPSE_GOLDEN)
    assert unexplained == [] and len(accepted) == 2


def test_collapse_named_chapter_target_mirror_forgiven():
    # a self-reference to a whole chapter (#K3) is not renumbered; both sides
    # agree on it, so the kept target still pairs with the stripped source
    collapsed = _ref("missing", "K9P5S1", "https://lagen.nu/2022:964#K3")
    distributed = _ref("extra", "K2P5S1", "https://lagen.nu/2022:964#K3")
    unexplained, accepted = golden_sfs.adjudicate(
        [collapsed, distributed], COLLAPSE_GOLDEN)
    assert unexplained == [] and len(accepted) == 2


def test_collapse_lone_diff_stays_unexplained():
    # a golden link the new pipeline never re-emitted (no distributed mirror) is
    # a genuine resolution difference, not the structural collapse -> visible
    lone = [_ref("missing", "K9P5S1", "https://lagen.nu/2005:551#K11P2")]
    unexplained, accepted = golden_sfs.adjudicate(lone, COLLAPSE_GOLDEN)
    assert unexplained == lone and accepted == []


def test_collapse_inert_on_healthy_golden():
    # an identical mirror pair against a non-collapsed golden is NOT forgiven --
    # the rule only fires when the golden itself is structurally collapsed
    collapsed = _ref("missing", "K9P5S1", "https://lagen.nu/2022:964#K9P3")
    distributed = _ref("extra", "K3P5S1", "https://lagen.nu/2022:964#K3P3")
    unexplained, accepted = golden_sfs.adjudicate(
        [collapsed, distributed], REFS_GOLDEN)
    assert set(unexplained) == {collapsed, distributed} and accepted == []


# --- post-freeze-source-amendment (paragraf rewritten after the freeze) --

def test_paragraf_of_helper():
    assert golden_sfs.paragraf_of("K2P1S13") == "K2P1"
    assert golden_sfs.paragraf_of("K10P18S2") == "K10P18"
    assert golden_sfs.paragraf_of("P5S2") == "P5"
    assert golden_sfs.paragraf_of("K2P1aS3") == "K2P1a"
    assert golden_sfs.paragraf_of("K9") is None       # bare chapter
    assert golden_sfs.paragraf_of("") is None


def test_post_freeze_source_amendment_forgives_whole_paragraf():
    # the paragraf's note bumped to a post-horizon act (2021:50 > 2020:100), so
    # every other reference the new pipeline read from that paragraf -- a
    # renumbered cross-reference here -- is stale on the golden side
    bump = _ref("extra", "K2P1S5", "https://lagen.nu/1962:700#L2021:50")
    renum_new = _ref("extra", "K2P1S2", "https://lagen.nu/1962:700#K9P67")
    renum_old = _ref("missing", "K2P1S2", "https://lagen.nu/1962:700#K9P32")
    unexplained, accepted = golden_sfs.adjudicate(
        [bump, renum_new, renum_old], REFS_GOLDEN)
    assert unexplained == []
    rules = {r for r, _ in accepted}
    # the #L note credits to change-reference-staleness; the others to the new rule
    assert "post-freeze-source-amendment" in rules


def test_post_freeze_source_amendment_only_the_bumped_paragraf():
    # a diff from a *different*, un-bumped paragraf stays visible
    bump = _ref("extra", "K2P1S5", "https://lagen.nu/1962:700#L2021:50")
    other = _ref("extra", "K5P3S1", "https://lagen.nu/1962:700#K9P67")
    unexplained, accepted = golden_sfs.adjudicate([bump, other], REFS_GOLDEN)
    assert other in unexplained
    assert {r for r, _ in accepted} == {"change-reference-staleness"}


def test_post_freeze_source_amendment_needs_post_horizon_bump():
    # the note names a pre-horizon act (2019:9 <= 2020:100): not a post-freeze
    # rewrite, so neither the note nor a sibling reference is forgiven
    prebump = _ref("extra", "K2P1S5", "https://lagen.nu/1962:700#L2019:9")
    sibling = _ref("extra", "K2P1S2", "https://lagen.nu/1962:700#K9P67")
    unexplained, accepted = golden_sfs.adjudicate([prebump, sibling], REFS_GOLDEN)
    assert set(unexplained) == {prebump, sibling} and accepted == []


# --- stycke-pinpoint-drift: same edge, back-link re-anchored to a different
# stycke of the SAME paragraf (the list-continuation parser fix numbers stycken
# correctly; the golden's stale ordinals run off by one) ---

PIN_GOLDEN = {"uri": "https://lagen.nu/1962:700", "amendments": []}


def test_pinpoint_drift_mirror_pair_forgiven():
    # golden read the closing change-ref from stycke 3 (it split a continuation
    # into its own stycke); the new pipeline folds the continuation, so the same
    # edge is read from stycke 2 -- same paragraf, same target
    old = _ref("missing", "K13P5cS3", "https://lagen.nu/1962:700#L2019:1162")
    new = _ref("extra", "K13P5cS2", "https://lagen.nu/1962:700#L2019:1162")
    unexplained, accepted = golden_sfs.adjudicate([old, new], PIN_GOLDEN)
    assert unexplained == []
    assert {r for r, _ in accepted} == {"stycke-pinpoint-drift"}
    assert len(accepted) == 2


def test_pinpoint_drift_lone_extra_not_forgiven():
    new = [_ref("extra", "K13P5cS2", "https://lagen.nu/1962:700#K9P1")]
    unexplained, accepted = golden_sfs.adjudicate(new, PIN_GOLDEN)
    assert unexplained == new and accepted == []


def test_pinpoint_drift_different_paragraf_not_forgiven():
    # same target but from a *different paragraf* -- a genuinely different source,
    # not a re-anchored stycke
    old = _ref("missing", "K13P5cS3", "https://lagen.nu/1962:700#K9P1")
    new = _ref("extra", "K13P6S1", "https://lagen.nu/1962:700#K9P1")
    unexplained, accepted = golden_sfs.adjudicate([old, new], PIN_GOLDEN)
    assert set(unexplained) == {old, new} and accepted == []


def test_pinpoint_drift_bilaga_out_of_scope():
    # a bilaga source (not paragraf-rooted) is out of scope -- bilaga S# offsets
    # are structure-staleness, a different family
    old = _ref("missing", "B1S102", "https://lagen.nu/ext/celex/32012R0648")
    new = _ref("extra", "B1S179", "https://lagen.nu/ext/celex/32012R0648")
    unexplained, accepted = golden_sfs.adjudicate([old, new], PIN_GOLDEN)
    assert set(unexplained) == {old, new} and accepted == []


# --- brottsrubricering-begrepp: a crime-name concept the new pipeline extracts
# from a "döms för X till böter/fängelse" clause that the old pipeline missed ---

def _beg(kind, name, clause):
    line = "begrepp: %s https://lagen.nu/begrepp/%s" % (kind, name)
    return line + ("  «%s»" % clause if clause else "")


def test_brottsrubricering_begrepp_forgiven():
    p = _beg("extra", "Kapning",
             "Den som bemäktigar sig ett fartyg döms för kapning till fängelse "
             "i högst fyra år.")
    unexplained, accepted = golden_sfs.adjudicate([p], PIN_GOLDEN)
    assert unexplained == []
    assert accepted == [("brottsrubricering-begrepp", p)]


def test_brottsrubricering_begrepp_alt_form_forgiven():
    p = _beg("extra", "Mord", "För mord döms till fängelse på livstid.")
    _, accepted = golden_sfs.adjudicate([p], PIN_GOLDEN)
    assert [r for r, _ in accepted] == ["brottsrubricering-begrepp"]


def test_non_brottsrubricering_begrepp_stays():
    # an ordinary defined term (not an offence clause) is not blanket-forgiven
    p = [_beg("extra", "Konsument",
              "I denna lag avses med konsument en fysisk person.")]
    unexplained, accepted = golden_sfs.adjudicate(p, PIN_GOLDEN)
    assert unexplained == p and accepted == []


def test_brottsrubricering_begrepp_missing_not_forgiven():
    # the predicate only forgives an `extra`; a term the new pipeline dropped
    # (begrepp: missing) is a real regression and stays visible
    p = [_beg("missing", "Kapning",
              "Den som bemäktigar sig ett fartyg döms för kapning till fängelse.")]
    unexplained, accepted = golden_sfs.adjudicate(p, PIN_GOLDEN)
    assert unexplained == p and accepted == []


# --- grafik-node-replaces-marker: a graphic the SFST text drops, recovered as a
# typed grafik node where the old pipeline carried the omission marker as text --

GRAFIK_GOLDEN = {"uri": "https://lagen.nu/2002:780", "amendments": [],
                 "structure": [{"type": "bilaga", "id": "B1", "children": [
                     {"type": "stycke", "id": "B1S1", "text": "1 Balanstalet, BT"},
                     {"type": "stycke", "id": "B1S2",
                      "text": "/Formeln är inte med här/ Förordning (2021:734)."},
                 ]}]}
# the new pipeline: B1S2 became a grafik node G1
GRAFIK_NEW = {"structure": [{"type": "bilaga", "id": "B1", "children": [
    {"type": "stycke", "id": "B1S1", "text": "1 Balanstalet, BT"},
    {"type": "grafik", "id": "G1", "sort": "formel", "satt_av": "2021:734"},
]}]}


def test_is_marker_only():
    assert golden_sfs.is_marker_only("/Formeln är inte med här/")
    assert golden_sfs.is_marker_only("/Bilagan är inte med här./")   # period variant
    assert golden_sfs.is_marker_only(
        "/Formeln är inte med här/ Förordning (2021:734).")          # + change note
    assert golden_sfs.is_marker_only("Formeln är inte med här.")     # no slashes
    assert golden_sfs.is_marker_only(
        "Bilaga 2 är inte med här. Bilagan senast ändrad genom lag (2025:1369).")
    # a marker embedded in real prose is NOT marker-only -- a real drop
    assert not golden_sfs.is_marker_only("Se figuren /Figuren är inte med här/ nedan")
    assert not golden_sfs.is_marker_only("En vanlig paragraf.")


def test_grafik_extra_node_and_missing_marker_forgiven():
    extra = "structure/B1: extra node G1"
    missing = "structure/B1: missing node B1S2"
    unexplained, accepted = golden_sfs.adjudicate(
        [extra, missing], GRAFIK_GOLDEN, GRAFIK_NEW)
    assert unexplained == []
    assert {r for r, _ in accepted} == {"grafik-node-replaces-marker"}
    assert len(accepted) == 2


def test_grafik_missing_real_prose_stays():
    # a golden stycke that held real text (not just a marker) going missing is a
    # genuine regression, never swept up by the grafik family
    golden = {"uri": "https://lagen.nu/x", "amendments": [], "structure": [
        {"type": "stycke", "id": "P1S1", "text": "En riktig bestämmelse."}]}
    p = ["structure: missing node P1S1"]
    unexplained, accepted = golden_sfs.adjudicate(p, golden)
    assert unexplained == p and accepted == []


def test_grafik_extra_non_grafik_node_stays():
    # an extra node that is NOT a grafik (a real phantom paragraf) stays visible
    new = {"structure": [{"type": "paragraf", "id": "P9", "text": "x"}]}
    p = ["structure: extra node P9"]
    unexplained, accepted = golden_sfs.adjudicate(p, GRAFIK_GOLDEN, new)
    assert unexplained == p and accepted == []


def test_grafik_heading_marker_stripped_forgiven():
    # a bilaga heading that trailed a marker loses only the marker
    changed = ("structure/B2: text changed:\n"
               "  old: 'Bilaga 1 /Bilagan är inte med här./'\n"
               "  new: 'Bilaga 1'")
    extra = "structure/B2: extra node G1"
    unexplained, accepted = golden_sfs.adjudicate(
        [extra, changed], GRAFIK_GOLDEN, GRAFIK_NEW)
    assert unexplained == []
    assert [r for r, _ in accepted] == ["grafik-node-replaces-marker"] * 2


def test_grafik_unpaired_missing_or_extra_stays():
    missing = "structure/B1: missing node B1S2"
    extra = "structure/B1: extra node G1"
    assert golden_sfs.adjudicate([missing], GRAFIK_GOLDEN, GRAFIK_NEW)[0] == [missing]
    assert golden_sfs.adjudicate([extra], GRAFIK_GOLDEN, GRAFIK_NEW)[0] == [extra]


def test_grafik_heading_real_text_change_stays():
    # a heading change that is not merely a stripped marker is a real diff
    changed = ("structure/B2: text changed:\n"
               "  old: 'Bilaga 1 om avgifter'\n  new: 'Bilaga 1 om kostnader'")
    unexplained, accepted = golden_sfs.adjudicate([changed], GRAFIK_GOLDEN)
    assert unexplained == [changed] and accepted == []
