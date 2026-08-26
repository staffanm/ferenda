"""The corpus roots a test run sees are empty and disposable, never the
developer's own.

`test/conftest.py` sets `DATA_ROOT` before `ferenda` is imported. This
locks that in: without it the breakage is silent -- the suite still passes, on
the machine that has the corpus, and reports facts about whatever happens to be
on that disk. Five tests were in that state (they named the ärende `sou/2026-14`
and the real tree answered for them), and the whole suite spent 90 % of its
runtime walking 80 200 remisser artifacts no assertion looked at.
"""

from ferenda import config
from ferenda.lib import layout


def test_the_corpus_roots_are_not_the_live_corpus():
    live = config.REPO / "site" / "data"
    for root in (layout.DATA, layout.ARTIFACT, layout.DOWNLOADED,
                 layout.GENERATED, layout.WIKI_ROOT):
        assert not root.is_relative_to(live), (
            "%s points into the live corpus -- test/conftest.py must set "
            "DATA_ROOT before ferenda is imported" % root)


def test_the_artifact_tree_a_test_sees_is_empty():
    # a source with 80 200 artifacts on the developer's disk, and the one whose
    # walk `page.Site.from_catalog` pays for on every unscoped build
    assert layout.artifacts("remisser") == []
    assert layout.artifacts("sfs") == []
