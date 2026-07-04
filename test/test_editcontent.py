"""The inline editor's content model (accommodanda/api/editcontent.py) and the
`fragment_heading` inverse it relies on: locating and rewriting one markdown
region in a scratch lagen-wiki tree without disturbing anything around it."""

import pytest

from accommodanda.api import editcontent
from accommodanda.wiki import parse as wiki


@pytest.fixture
def wiki_root(tmp_path, monkeypatch):
    """A minimal content repo: one SFS commentary with two sections and a
    document-level guidance block, one concept, one about page."""
    (tmp_path / "commentary" / "sfs" / "1915").mkdir(parents=True)
    (tmp_path / "commentary" / "sfs" / "1915" / "218.md").write_text(
        "---\nannotates: 1915:218\nauthor: Test\n---\n"
        "## 1 §\n\nAvtal sluts genom anbud och accept.\n\n"
        "## Externa länkar\n- [FB](https://example.org) — not\n\n"
        "## 3 §\n\nEn oren accept gäller som avslag.\n", encoding="utf-8")
    (tmp_path / "concept").mkdir()
    (tmp_path / "concept" / "Avtal.md").write_text(
        "---\ntitle: Avtal\n---\nEn överenskommelse mellan parter.\n", encoding="utf-8")
    (tmp_path / "site" / "om").mkdir(parents=True)
    (tmp_path / "site" / "om" / "kontakt.md").write_text(
        "---\ntitle: Kontakt\n---\nHör av dig.\n", encoding="utf-8")
    monkeypatch.setattr("accommodanda.config.WIKI_ROOT", tmp_path)
    wiki.kommentar_index.cache_clear()
    wiki.begrepp_index.cache_clear()
    yield tmp_path
    wiki.kommentar_index.cache_clear()
    wiki.begrepp_index.cache_clear()


# --------------------------------------------------------------------------
# fragment_heading is a left inverse of heading_fragment
# --------------------------------------------------------------------------

@pytest.mark.parametrize("anchor,heading", [
    ("P7", "7 §"), ("K21P1", "21 kap. 1 §"), ("K1P1c", "1 kap. 1 c §"),
    ("K25", "25 kap."), ("5", "Artikel 5"), ("5.2", "Artikel 5.2"),
    ("5.2.a", "Artikel 5.2 a"), ("recital-13", "Skäl 13"),
])
def test_fragment_heading_roundtrips(anchor, heading):
    assert wiki.fragment_heading(anchor) == heading
    assert wiki.heading_fragment(heading) == anchor


def test_fragment_heading_rejects_non_anchorable():
    with pytest.raises(ValueError):
        wiki.fragment_heading("S3")            # a förarbete section, not a host node


# --------------------------------------------------------------------------
# kommentar: read / rewrite one section, preserve the rest byte-for-byte
# --------------------------------------------------------------------------

def test_read_existing_section(wiki_root):
    view = editcontent.read(editcontent.Region("kommentar", "1915:218", "P1"))
    assert view["exists"] and view["markdown"].startswith("## 1 §")
    assert "anbud och accept" in view["markdown"]


def test_read_missing_section_seeds_template(wiki_root):
    view = editcontent.read(editcontent.Region("kommentar", "1915:218", "P2"))
    assert not view["exists"]
    assert view["markdown"] == "## 2 §\n\n"


def test_read_section_includes_its_guidance_block(wiki_root):
    # a `## Externa länkar` block sits under the section it follows, so it is part
    # of that section's editable region (the user sees and keeps/edits it)
    view = editcontent.read(editcontent.Region("kommentar", "1915:218", "P1"))
    assert "## Externa länkar" in view["markdown"]
    assert "## 3 §" not in view["markdown"]         # the next section is a boundary


def test_rewrite_section_preserves_siblings_and_frontmatter(wiki_root):
    # editing 3 § (which has no guidance under it) must leave 1 §, its Externa
    # länkar block and the frontmatter byte-for-byte
    region = editcontent.Region("kommentar", "1915:218", "P3")
    editcontent.write(region, "## 3 §\n\nOmformulerad kommentar med [FB](sfs:1949:381).\n")
    after = (wiki_root / "commentary" / "sfs" / "1915" / "218.md").read_text()
    assert "Omformulerad kommentar" in after and "En oren accept gäller" not in after
    assert "annotates: 1915:218" in after and "author: Test" in after
    assert "## 1 §\n\nAvtal sluts genom anbud och accept." in after
    assert "## Externa länkar\n- [FB](https://example.org) — not" in after
    # and the artifact still parses with both sections anchored, in order
    art = wiki.kommentar_artifact(str(wiki_root / "commentary" / "sfs" / "1915" / "218.md"))
    assert [b["id"] for b in art["body"] if b.get("type") == "sektion"] == ["P1", "P3"]


def test_append_new_section_for_uncommented_node(wiki_root):
    region = editcontent.Region("kommentar", "1915:218", "P2")
    editcontent.write(region, "## 2 §\n\nEtt anbud är bindande.\n")
    art = wiki.kommentar_artifact(str(wiki_root / "commentary" / "sfs" / "1915" / "218.md"))
    assert [b["id"] for b in art["body"] if b.get("type") == "sektion"] == ["P1", "P3", "P2"]


def test_document_level_reads_preamble(wiki_root):
    # a commentary file whose prose leads before the first `## §` -> that preamble
    # is the document-level (anchor=None) region, the "act as a whole" commentary
    (wiki_root / "commentary" / "sfs" / "1915" / "218.md").write_text(
        "---\nannotates: 1915:218\n---\n"
        "Denna lag reglerar avtal.\n\n## 1 §\n\nAvtal sluts genom anbud.\n",
        encoding="utf-8")
    wiki.kommentar_index.cache_clear()
    view = editcontent.read(editcontent.Region("kommentar", "1915:218", None))
    assert view["exists"] and view["markdown"].strip() == "Denna lag reglerar avtal."
    assert "## 1 §" not in view["markdown"]        # stops at the first section


def test_document_level_absent_seeds_empty(wiki_root):
    # the fixture file starts straight at `## 1 §` -> no preamble yet
    view = editcontent.read(editcontent.Region("kommentar", "1915:218", None))
    assert not view["exists"] and view["markdown"] == "" and view["base_sha"] == ""


def test_write_document_level_preserves_sections(wiki_root):
    region = editcontent.Region("kommentar", "1915:218", None)
    editcontent.write(region, "Introduktion till lagen.\n")
    after = (wiki_root / "commentary" / "sfs" / "1915" / "218.md").read_text()
    assert after.startswith("---\nannotates: 1915:218\nauthor: Test\n---\n"
                            "Introduktion till lagen.\n\n## 1 §")
    # both sections survive, still anchored, and the preamble parses as doc-level
    art = wiki.kommentar_artifact(str(wiki_root / "commentary" / "sfs" / "1915" / "218.md"))
    assert [b["id"] for b in art["body"] if b.get("type") == "sektion"] == ["P1", "P3"]
    assert editcontent.read(region)["markdown"].strip() == "Introduktion till lagen."


def test_document_level_allows_any_prose(wiki_root):
    # unlike a section, the document-level region needs no `## §` heading
    editcontent.write(editcontent.Region("kommentar", "2009:400", None),
                      "En helt ny lagkommentar.\n")
    text = (wiki_root / "commentary" / "sfs" / "2009" / "400.md").read_text()
    assert text == "---\nannotates: 2009:400\n---\nEn helt ny lagkommentar.\n"


def test_write_rejects_wrong_anchor_heading(wiki_root):
    # the section text must open with the heading for the node it annotates
    with pytest.raises(ValueError):
        editcontent.write(editcontent.Region("kommentar", "1915:218", "P1"),
                          "## 9 §\n\nfel rubrik\n")


def test_first_comment_creates_file_with_frontmatter(wiki_root):
    region = editcontent.Region("kommentar", "2009:400", "P1")
    info = editcontent.write(region, "## 1 §\n\nOffentlighetsprincipen.\n")
    assert info["created"]
    text = info["path"].read_text(encoding="utf-8")
    assert text.startswith("---\nannotates: 2009:400\n---\n")
    assert info["path"] == wiki_root / "commentary" / "sfs" / "2009" / "400.md"


# --------------------------------------------------------------------------
# begrepp / site: whole-body edits, frontmatter preserved
# --------------------------------------------------------------------------

def test_begrepp_whole_body(wiki_root):
    region = editcontent.Region("begrepp", "Avtal")
    assert editcontent.read(region)["markdown"].strip() == "En överenskommelse mellan parter."
    editcontent.write(region, "En bindande överenskommelse.\n")
    text = (wiki_root / "concept" / "Avtal.md").read_text(encoding="utf-8")
    assert text == "---\ntitle: Avtal\n---\nEn bindande överenskommelse.\n"


def test_site_whole_body(wiki_root):
    region = editcontent.Region("site", "om/kontakt")
    editcontent.write(region, "Ny kontakttext.\n")
    text = (wiki_root / "site" / "om" / "kontakt.md").read_text(encoding="utf-8")
    assert text == "---\ntitle: Kontakt\n---\nNy kontakttext.\n"


def test_region_validation():
    # a kommentar with no anchor is valid -- it's the document-level region
    assert editcontent.Region("kommentar", "1915:218").anchor is None
    with pytest.raises(ValueError):
        editcontent.Region("begrepp", "Avtal", "P1")          # no anchor allowed
    with pytest.raises(ValueError):
        editcontent.Region("bogus", "x")


@pytest.mark.parametrize("kind,ref,anchor", [
    ("kommentar", "a/../../escape", "P1"),      # eurlex rule yields an absolute path
    ("site", "om/../../../escape", None),        # record() keeps the `..` traversal
])
def test_locate_refuses_paths_outside_wiki_root(wiki_root, kind, ref, anchor):
    with pytest.raises(ValueError):
        editcontent.write(editcontent.Region(kind, ref, anchor), "## 1 §\n\nx\n")
    # nothing was written outside the repo
    assert not (wiki_root.parent / "escape.md").exists()
