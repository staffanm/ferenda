"""The crop-review content model (accommodanda/api/graphicsedit.py) and its
carriage through the shared edit cart: approving a crop flips the one flag that
lets it reach the public render, and leaves everything else in the layer alone.
"""

import difflib
import json
import subprocess

import pytest

from accommodanda.api import editcart, editcontent, graphicsedit
from accommodanda.api.auth import Editor
from accommodanda.lib import annstore
from accommodanda.wiki import parse as wiki

EDITOR = Editor("anna", {"name": "Anna Ek", "email": "anna@example.org"})

# one generated entry (awaiting review) and one already signed off, so every
# test sees both sides of `annstore.publishable`
LAYER = {
    "meta": {"status": "generated", "model": "moonshotai/Kimi-K2.6",
             "generated": "2026-08-20", "inputs": {"artifact:sfs/2006:171": "abc"},
             "uri": "https://lagen.nu/2006:171", "through": "2024:519"},
    "g-pending": {"sfs": "2006:1574", "page": 2, "alt": "Tabell",
                  "bbox": [53, 46, 422, 389],
                  "identity": {"sort": "tabell", "anchor": "bidrag i kronor",
                               "code": None, "occurrence": 1}},
    "g-signed": {"sfs": "2006:1574", "page": 3, "alt": "Karta",
                 "bbox": [10, 10, 100, 100], "verified": True,
                 "identity": {"sort": "karta", "anchor": "karta över",
                              "code": None, "occurrence": 1}},
}


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          text=True, capture_output=True).stdout.strip()


@pytest.fixture
def layer(tmp_path, monkeypatch):
    """A git-initialised wiki repo holding one .graphics layer, wired as the
    annotation store, plus an isolated cart."""
    root = tmp_path / "wiki"
    monkeypatch.setattr(annstore, "ROOT", root / "ann")
    p = annstore.path("sfs", "2006:171", ".graphics")
    p.parent.mkdir(parents=True)
    # seeded through the real writer, not a hand-rolled dump: the minimal-diff
    # assertion below is only meaningful against the envelope production emits
    annstore.dump(p, LAYER)
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Seed")
    _git(root, "config", "user.email", "seed@example.org")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "seed")
    monkeypatch.setattr("accommodanda.config.WIKI_ROOT", root)
    monkeypatch.setattr(editcart, "EDITS", tmp_path / "edits")
    return p


PENDING = graphicsedit.Region("2006:171", "g-pending")


def test_queue_lists_only_what_the_site_will_not_show(layer, monkeypatch):
    monkeypatch.setattr(graphicsedit, "_page_count", lambda src: 7)
    pending = graphicsedit.queue()
    assert [e["anchor"] for e in pending] == ["g-pending"]
    entry = pending[0]
    assert entry["sort"] == "tabell" and entry["sfs"] == "2006:1574"
    assert entry["anchor_text"] == "bidrag i kronor"
    assert entry["bbox"] == [53, 46, 422, 389]


def test_derived_layer_needs_no_review(layer, monkeypatch):
    """A mechanically derived layer publishes as it stands, so it never queues."""
    monkeypatch.setattr(graphicsedit, "_page_count", lambda src: 7)
    content = json.loads(layer.read_text())
    content["meta"]["status"] = "derived"
    layer.write_text(json.dumps(content))
    assert graphicsedit.queue() == []


def test_canonical_round_trip():
    text = graphicsedit.canonical({"page": 2, "bbox": [1, 2, 3, 4]})
    assert graphicsedit.parse(text) == {"page": 2, "bbox": [1, 2, 3, 4],
                                        "verified": False}


@pytest.mark.parametrize("payload, why", [
    ('{"page": 0, "bbox": null, "verified": true}', "page must be positive"),
    ('{"page": 1, "bbox": [4, 2, 3, 4], "verified": true}', "x0 < x1 violated"),
    ('{"page": 1, "bbox": null}', "missing verified"),
    ('not json', "unparseable"),
])
def test_parse_rejects_bad_payloads(payload, why):
    with pytest.raises(ValueError):
        graphicsedit.parse(payload)


def test_approving_an_unchanged_bbox_is_a_real_edit(layer):
    """The trap: approval moves no geometry. `verified` lives inside the draft
    payload precisely so the cart sees a difference and carts it."""
    base = graphicsedit.read(PENDING)
    same_geometry = graphicsedit.canonical(
        {"page": 2, "bbox": [53, 46, 422, 389], "verified": True})
    assert same_geometry != base["text"]
    assert editcart.upsert("anna", PENDING, same_geometry) == 1


def test_write_flips_publishable_and_preserves_meta(layer):
    before = json.loads(layer.read_text())
    graphicsedit.write(PENDING, graphicsedit.canonical(
        {"page": 2, "bbox": [53, 46, 422, 389], "verified": True}))
    after = json.loads(layer.read_text())
    assert after["meta"] == before["meta"], "a review must not restamp the envelope"
    assert after["g-signed"] == before["g-signed"], "other entries untouched"
    assert after["g-pending"]["identity"] == before["g-pending"]["identity"]
    assert after["g-pending"]["alt"] == "Tabell"
    assert after["g-pending"]["sfs"] == "2006:1574", "provenance is not editable"
    assert annstore.publishable(after["meta"], after["g-pending"])


def test_whole_page_removes_the_bbox(layer):
    graphicsedit.write(PENDING, graphicsedit.canonical(
        {"page": 4, "bbox": None, "verified": True}))
    entry = json.loads(layer.read_text())["g-pending"]
    assert "bbox" not in entry and entry["page"] == 4


def test_unapproving_removes_the_flag(layer):
    signed = graphicsedit.Region("2006:171", "g-signed")
    graphicsedit.write(signed, graphicsedit.canonical(
        {"page": 3, "bbox": [10, 10, 100, 100], "verified": False}))
    entry = json.loads(layer.read_text())["g-signed"]
    assert "verified" not in entry
    assert not annstore.publishable({"status": "generated"}, entry)


def test_commit_carries_a_graphics_draft(layer):
    editcart.upsert("anna", PENDING, graphicsedit.canonical(
        {"page": 2, "bbox": [60, 50, 400, 380], "verified": True}))
    result = editcart.commit(EDITOR, "sfs: granskad grafik")
    assert result["changes"] == [{"kind": "graphics", "basefile": "2006:171"}]
    entry = json.loads(layer.read_text())["g-pending"]
    assert entry["bbox"] == [60, 50, 400, 380] and entry["verified"] is True
    assert editcart.cart("anna") == [], "a checked-out cart is empty"
    author = _git(layer.parents[3], "log", "-1", "--format=%an <%ae>")
    assert author == "Anna Ek <anna@example.org>"


def test_a_re_run_under_a_draft_is_a_conflict(layer):
    editcart.upsert("anna", PENDING, graphicsedit.canonical(
        {"page": 2, "bbox": [60, 50, 400, 380], "verified": True}))
    content = json.loads(layer.read_text())      # the vision pass re-runs
    content["g-pending"]["bbox"] = [1, 1, 200, 200]
    layer.write_text(json.dumps(content))
    with pytest.raises(editcart.Conflict) as exc:
        editcart.commit(EDITOR, "sfs: granskad grafik")
    assert exc.value.keys == ["graphics:2006:171#g-pending"]
    assert json.loads(layer.read_text())["g-pending"]["bbox"] == [1, 1, 200, 200], \
        "a conflict writes nothing"


def test_document_uri_comes_off_the_layer(layer):
    assert graphicsedit.document_uri("2006:171") == "https://lagen.nu/2006:171"


def test_approval_leaves_the_geometry_lines_alone(layer):
    """The git diff is the review (lib/annstore), so approving a crop that
    needed no change must not rewrite `[53, 46, …]` as `[53.0, 46.0, …]`
    because a float made the round trip through the browser and pydantic.

    Appending a JSON key also puts a comma on the line before it, so the
    smallest possible diff is that comma plus the approval -- what must NOT
    appear is any coordinate.
    """
    before = layer.read_text()
    graphicsedit.write(PENDING, graphicsedit.canonical(
        {"page": 2, "bbox": [53.0, 46.0, 422.0, 389.0], "verified": True}))
    changed = _diff(before, layer.read_text())
    assert '+    "verified": true' in changed
    assert not [l for l in changed if any(c in l for c in ("53", "46", "422", "389"))], \
        changed


def test_a_fractional_coordinate_survives(layer):
    graphicsedit.write(PENDING, graphicsedit.canonical(
        {"page": 2, "bbox": [53.5, 46, 422, 389], "verified": True}))
    assert json.loads(layer.read_text())["g-pending"]["bbox"][0] == 53.5


def _diff(before, after):
    return [l for l in difflib.unified_diff(before.splitlines(),
                                            after.splitlines(), lineterm="", n=0)
            if not l.startswith(("+++", "---", "@@"))]


def test_a_mixed_cart_commits_both_kinds(layer, tmp_path, monkeypatch):
    """The case the whole `_region_of`/`_read`/`_write` dispatch exists for:
    one commit carrying a markdown region and a graphics entry together."""
    root = tmp_path / "wiki"
    md = root / "commentary" / "sfs" / "2006" / "171.md"
    md.parent.mkdir(parents=True)
    md.write_text("---\nannotates: 2006:171\n---\n## 1 §\n\nUrsprunglig.\n",
                  encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "seed commentary")
    wiki.kommentar_index.cache_clear()
    wiki.begrepp_index.cache_clear()

    editcart.upsert("anna", editcontent.Region("kommentar", "2006:171", "P1"),
                    "## 1 §\n\nNy text.\n")
    editcart.upsert("anna", PENDING, graphicsedit.canonical(
        {"page": 2, "bbox": [53, 46, 422, 389], "verified": True}))
    result = editcart.commit(EDITOR, "sfs: kommentar och granskad grafik")

    assert sorted(c["kind"] for c in result["changes"]) == ["graphics", "kommentar"]
    assert json.loads(layer.read_text())["g-pending"]["verified"] is True
    assert "Ny text." in md.read_text(encoding="utf-8")
    wiki.kommentar_index.cache_clear()
