"""The source registrations must agree with each other.

`build.SOURCES` says what the driver can run; each entry's `artifacts` field
says whose artifacts relate/index/dump read, and its `render` field who renders
a document page. The three are hand-maintained over the same set of sources.
Nothing fails loudly when a new source declares one and forgets the others: it
simply never reaches the catalog, or crashes at generate time on a corpus run
hours in. These tests are that missing failure, with the principled exceptions
written down as data rather than left to be rediscovered.

The second half locks in what an `artifacts` lister may return: documents only.
Hand-globbed listers had drifted from `layout.artifacts` -- the föreskrift one
matched 1,650 `.grund.json` as-enacted sidecars, and any lister recursing into
`artifact/sfs/archive/` would have handed relate 31,213 superseded
consolidations as if each were a separate act.
"""

import pytest

from ferenda import build
from ferenda.lib import facets, layout

# Sources that publish no catalogued documents, so they register no `artifacts`
# lister and are never related/indexed/dumped:
#   remisser -- its parsed answers feed the referred förarbete's rail via .ann
#               sidecars; it renders no pages of its own (remisser/source.py)
#   site     -- lagen.nu's editorial chrome (frontpage, /om, sitenews): parsed and
#               generated, but carrying no citation graph
#   stats    -- one corpus-measurement artifact, rendered to /statistik
NO_DOCUMENTS = {"remisser", "site", "stats"}

# An artifact source with no renderer of its own: a kommentar is an annotation
# rendered into its host statute's rail, not a page (lib/render.render_document,
# which drops kommentar rows rather than looking one up). A lawreview
# article is mined for the citations it makes and surfaces as an external-linked
# rail row on the documents it cites; the site republishes nothing of it.
NO_RENDERER = {"kommentar", "lawreview"}


# the sources whose artifacts relate/index/dump read, and the sources that
# render a document page -- the two registration fields these tests check
PUBLISHED = sorted(n for n, s in build.SOURCES.items() if s.artifacts)
RENDERED = sorted(n for n, s in build.SOURCES.items() if s.render)


def test_the_exception_sets_name_real_sources():
    # a renamed/removed source must not leave a stale exception behind, quietly
    # excusing the successor from every check below
    assert NO_DOCUMENTS <= set(build.SOURCES)
    assert NO_RENDERER <= set(PUBLISHED)


@pytest.mark.parametrize("name", sorted(build.SOURCES))
def test_a_source_that_publishes_documents_has_an_artifacts_lister(name):
    assert (name in PUBLISHED) == (name not in NO_DOCUMENTS)


@pytest.mark.parametrize("name", sorted(build.SOURCES))
def test_a_source_with_artifacts_has_a_page_renderer(name):
    assert (name in RENDERED) == (name in PUBLISHED and name not in NO_RENDERER)


@pytest.mark.parametrize("name", sorted(build.SOURCES))
def test_every_source_has_an_on_disk_home(name):
    # an artifacts lister resolves through layout.artifact_dir, so a source
    # missing from SOURCE_DIR is a KeyError at relate time, not a registration
    # error
    assert name in layout.SOURCE_DIR


@pytest.mark.parametrize("name", PUBLISHED)
def test_an_artifacts_lister_returns_documents_only(name, tmp_path, monkeypatch):
    """Sidecars and the archive subtree are not documents. Checked against a
    synthetic artifact tree rather than the corpus: the rule is a path rule, so
    four files per source prove it in milliseconds where a corpus walk would
    read ~250,000."""
    monkeypatch.setattr(layout, "ARTIFACT", tmp_path)
    root = layout.artifact_dir(name)
    (root / "sub").mkdir(parents=True)
    document = root / "sub" / "doc.json"
    document.write_text("{}")
    # the per-document sidecars: extra *pages* beside a document (an sfs
    # historical wording index, a föreskrift's as-enacted projection)
    (root / "sub" / "doc.versions.json").write_text("[]")
    (root / "sub" / "doc.grund.json").write_text("{}")
    # the index sidecars, excluded by basename wherever ARTIFACT is rooted
    (root / layout.DOM_INDEX.name).write_text("[]")
    (root / layout.GUIDANCE_INDEX.name).write_text("{}")
    # one superseded consolidation, in the archive layout sfs actually writes
    archive = root / "archive" / "2003" / "466" / ".versions" / "2003"
    archive.mkdir(parents=True)
    (archive / "466.json").write_text("{}")

    assert build.SOURCES[name].artifacts() == [document]


def test_ungenerated_schemes_are_declared_in_facets_not_in_browse():
    """Which facet schemes become browse pages is facets' own declaration.
    UNGENERATED must name real schemes (a typo would silently re-enable a
    page tree), and every other scheme must be browsable -- browse.py derives
    its whole skip set from this, so the API and the generated site can no
    longer disagree about which sources have browse pages."""
    assert facets.UNGENERATED <= set(facets.SCHEMES)
    assert set(facets.browsable()) == set(facets.SCHEMES) - facets.UNGENERATED
    # the folkrätt landing lists these in full; that is why they are here
    assert facets.UNGENERATED == {"coe", "icrc", "untc", "icc", "icj"}
