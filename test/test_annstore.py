"""The curated LLM-layer store (lib.annstore): path grammar, the
generated/verified envelope, the verified-refuses-regeneration guard, and
input-drift (staleness) detection."""

import json

import pytest

from accommodanda.lib import annstore, compress, layout


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(layout, "ARTIFACT", tmp_path / "artifact")
    monkeypatch.setattr(annstore, "ROOT", tmp_path / "ann")
    return tmp_path


def test_path_mirrors_the_artifact_tree(store):
    # same (source, basefile) identity, same relpath grammar -- only the root
    # and the suffix differ, so a migration is a plain move
    assert annstore.path("eurlex", "32023R2854") == \
        annstore.ROOT / "eurlex" / "2023" / "32023R2854.ann"
    assert annstore.path("sfs", "2018:585", ".corr") == \
        annstore.ROOT / "sfs" / "2018" / "585.corr"
    assert annstore.for_artifact(layout.artifact("remisser", "case/org")) == \
        annstore.ROOT / "remisser" / "case" / "org.ann"


def test_for_artifact_rejects_foreign_paths(store):
    # a path outside the artifact tree has no mirrored layer; mapping it
    # anywhere would be a silent wrong answer
    with pytest.raises(ValueError):
        annstore.for_artifact(store / "elsewhere" / "doc.json")


def test_write_stamps_a_generated_envelope(store):
    p = annstore.write(annstore.path("eurlex", "32099R0001"),
                       {"editorialLayer": {"x": 1}}, {"artifact:eurlex/e": "ab"})
    env = json.loads(p.read_text())
    assert env["meta"]["status"] == "generated"
    assert env["meta"]["inputs"] == {"artifact:eurlex/e": "ab"}
    assert env["meta"]["model"] and env["meta"]["generated"]
    assert env["editorialLayer"] == {"x": 1}       # payload keys stay top-level
    assert annstore.status(p) == annstore.GENERATED


def test_generated_regenerates_verified_refuses(store):
    p = annstore.path("eurlex", "32099R0001")
    annstore.write(p, {"editorialLayer": 1}, {})
    annstore.write(p, {"editorialLayer": 2}, {})   # generated = a cache: fine
    assert json.loads(p.read_text())["editorialLayer"] == 2

    # a human verifies (flips the field by hand): now it is curation
    env = json.loads(p.read_text())
    env["meta"]["status"] = "verified"
    p.write_text(json.dumps(env))
    with pytest.raises(ValueError, match="verified"):
        annstore.guard(p)
    with pytest.raises(ValueError, match="verified"):
        annstore.write(p, {"editorialLayer": 3}, {})
    assert json.loads(p.read_text())["editorialLayer"] == 2   # untouched
    annstore.write(p, {"editorialLayer": 3}, {}, force=True)  # explicit override
    assert annstore.status(p) == annstore.GENERATED


def test_unknown_status_raises_rather_than_disarming_the_guard(store):
    # status is hand-edited data: a typo ("verifed", "Verified") must surface,
    # not fall through to freely-regenerable and clobber the curation
    p = annstore.path("eurlex", "32099R0001")
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"meta": {"status": "verifed", "inputs": {}},
                             "editorialLayer": {}}))
    with pytest.raises(ValueError, match="unknown meta.status"):
        annstore.status(p)
    with pytest.raises(ValueError, match="unknown meta.status"):
        annstore.guard(p)


def test_meta_less_file_counts_as_verified(store):
    # unknown provenance (a pre-envelope/migrated file) must never be silently
    # clobbered -- treat it as curated
    p = annstore.path("sfs", "2018:585", ".corr")
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"correspondence": {}}))
    assert annstore.status(p) == annstore.VERIFIED
    with pytest.raises(ValueError, match="verified"):
        annstore.guard(p)


def test_drifted_flags_changed_and_vanished_inputs(store):
    art = layout.artifact("eurlex", "32099R0001")
    art.parent.mkdir(parents=True)
    compress.write_bytes(art, b'{"v": 1}')
    inputs = annstore.artifact_input("eurlex", "32099R0001")
    assert annstore.drifted(inputs) == []              # authored against current
    compress.write_bytes(art, b'{"v": 2}')             # the act is re-parsed
    assert annstore.drifted(inputs) == ["artifact:eurlex/32099R0001"]
    art.unlink()                                       # ... or vanishes outright
    assert annstore.drifted(inputs) == ["artifact:eurlex/32099R0001"]


def test_entries_lists_every_layer(store):
    a = annstore.write(annstore.path("eurlex", "32099R0001"), {"l": 1}, {})
    c = annstore.write(annstore.path("sfs", "2018:585", ".corr"), {"c": 1}, {})
    assert annstore.entries() == sorted([a, c])
