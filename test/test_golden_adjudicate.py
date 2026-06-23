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
