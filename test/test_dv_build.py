"""DV build-driver source selection across API and legacy representations."""

from accommodanda import build
from accommodanda.lib import layout


def member(store, path):
    return {"store": store, "path": path}


def test_dv_record_prefers_api_representation(tmp_path, monkeypatch):
    case = {"members": [member("dv", "downloaded/dv/HDO/T1-00.doc"),
                        member("domstol", "downloaded/dom/HDO/u1.json")]}
    monkeypatch.setattr(build, "_dv_cases", lambda: {"NJA 2001 s. 1": case})
    monkeypatch.setattr(layout, "DATA", tmp_path)
    assert build.dv_record("NJA 2001 s. 1") == \
        tmp_path / "downloaded/dom/HDO/u1.json"


def test_dv_record_uses_word_for_legacy_only_case(tmp_path, monkeypatch):
    case = {"members": [member("dv", "downloaded/dv/ADO/1993-100.doc")]}
    monkeypatch.setattr(build, "_dv_cases", lambda: {"AD 1993 nr 100": case})
    monkeypatch.setattr(layout, "DATA", tmp_path)
    assert build.dv_record("AD 1993 nr 100") == \
        tmp_path / "downloaded/dv/ADO/1993-100.doc"


def test_dv_parse_dispatches_legacy_through_common_artifact_projection(
        tmp_path, monkeypatch):
    case = {"members": [member("dv", "downloaded/dv/ADO/1993-100.doc")]}
    raw = tmp_path / "downloaded/dv/ADO/1993-100.doc"
    captured = {}
    sentinel = object()
    artifact = {"uri": "https://lagen.nu/dom/ad/1993:100"}
    monkeypatch.setattr(build, "_dv_cases", lambda: {"AD 1993 nr 100": case})
    monkeypatch.setattr(layout, "DATA", tmp_path)
    monkeypatch.setattr(build.dv_legacy, "parse_legacy_file",
                        lambda path, identity: sentinel
                        if (path, identity) == (raw, case) else None)
    monkeypatch.setattr(build, "to_artifact",
                        lambda av, canonical_id, canonical_malnummer: artifact
                        if (av, canonical_id, canonical_malnummer) ==
                        (sentinel, "AD 1993 nr 100", None)
                        else None)
    monkeypatch.setattr(build.casenaming, "case_label", lambda art: "AD 1993 nr 100")
    monkeypatch.setattr(build, "write_artifact",
                        lambda source, basefile, art, source_url=None:
                        captured.update(source=source, basefile=basefile, art=art,
                                        source_url=source_url))

    build.dv_parse_run("AD 1993 nr 100")

    assert captured == {"source": "dv", "basefile": "AD 1993 nr 100",
                        "art": {**artifact, "label": "AD 1993 nr 100"},
                        "source_url": None}


def test_dv_reindex_prunes_only_superseded_document_artifacts(
        tmp_path, monkeypatch):
    monkeypatch.setattr(layout, "ARTIFACT", tmp_path / "artifact")
    monkeypatch.setattr(layout, "DOM_INDEX",
                        tmp_path / "artifact/dom/identity-index.json")
    current = layout.artifact("dv", "NJA 2020 s. 1")
    superseded = layout.artifact("dv", "HDO T 1-20")
    current.parent.mkdir(parents=True)
    current.write_text("current")
    superseded.write_text("superseded")
    layout.DOM_INDEX.write_text("index sidecar")

    pruned = build._dv_prune_artifacts([
        {"canonical_id": "NJA 2020 s. 1"},
    ])

    assert pruned == 1
    assert current.exists()
    assert not superseded.exists()
    assert layout.DOM_INDEX.exists()
