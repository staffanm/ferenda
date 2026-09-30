"""The case-number snapshot (ferenda/lib/casenumbers.py): what it keeps, what
it refuses, and whether it rewrote the file -- which is what a full-source dv
or kkvdomar parse reports on, since the snapshot is a parse input for six
sources."""

import json

import pytest

from ferenda.lib import casenumbers, datasets, layout


def _artifact(uri, court, namn, date, numbers):
    return {"uri": uri, "court": court, "court_namn": namn,
            "avgorandedatum": date, "malnummer": numbers}


def _snapshot(monkeypatch, tmp_path, artifacts, kkvdomar=()):
    """Write `artifacts` as a dv tree and `kkvdomar` as a kkvdomar tree (an
    empty string is a SkipDocument placeholder), and return the snapshot
    `build()` reads from the two."""
    trees = {"dv": [], "kkvdomar": []}
    for source, arts in (("dv", artifacts), ("kkvdomar", kkvdomar)):
        for i, art in enumerate(arts):
            path = tmp_path / ("%s%d.json" % (source, i))
            path.write_text(art if isinstance(art, str) else json.dumps(art),
                            encoding="utf-8")
            trees[source].append(path)
    monkeypatch.setattr(casenumbers.layout, "artifacts", trees.__getitem__)
    return casenumbers.build()


def test_the_snapshot_keeps_every_candidate_under_one_spelling(monkeypatch,
                                                               tmp_path):
    snapshot, refused = _snapshot(monkeypatch, tmp_path, [
        _artifact("https://lagen.nu/dom/nja/2009s672", "HDO", "Högsta domstolen",
                  "2009-11-03", ["T 3-08"]),
        # the joined spelling is the same number as the spaced one
        _artifact("https://lagen.nu/dom/nja/2008/not/61", "HDO",
                  "Högsta domstolen", "2008-06-12", ["B732-08"]),
        # one referat collects the cases HD decided together
        _artifact("https://lagen.nu/dom/nja/1992s740", "HDO", "Högsta domstolen",
                  "1992-11-25", ["T 369-91", "T 224-91"]),
        # the same number in another court's series -- both candidates are kept,
        # because only the citation's own court can tell them apart
        _artifact("https://lagen.nu/dom/ad/2012:20", "ADO", "Arbetsdomstolen",
                  "2012-02-22", ["B 53-11"]),
        _artifact("https://lagen.nu/dom/nja/2011s89", "HDO", "Högsta domstolen",
                  "2011-04-19", ["B 53-11"]),
    ])
    assert refused == []
    assert snapshot["numbers"]["T 3-08"] == [
        ["HDO", "2009-11-03", "dom/nja/2009s672"]]
    assert snapshot["numbers"]["B 732-08"] == [
        ["HDO", "2008-06-12", "dom/nja/2008/not/61"]]
    # both of a referat's numbers lead to the one referat
    assert snapshot["numbers"]["T 369-91"] == snapshot["numbers"]["T 224-91"] \
        == [["HDO", "1992-11-25", "dom/nja/1992s740"]]
    assert snapshot["numbers"]["B 53-11"] == [
        ["ADO", "2012-02-22", "dom/ad/2012:20"],
        ["HDO", "2011-04-19", "dom/nja/2011s89"]]
    assert snapshot["courts"]["HDO"] == ["Högsta domstolen"]


def test_a_number_the_matcher_cannot_read_back_is_refused_not_shipped(
        monkeypatch, tmp_path):
    # 268 of the 24,995 printed values are shapes lib/malnummer never produces;
    # as keys they would sit in the snapshot unmatchable by anything
    snapshot, refused = _snapshot(monkeypatch, tmp_path, [
        _artifact("https://lagen.nu/dom/x", "HDO", "Högsta domstolen",
                  "2009-11-03", ["T 3-08", "05-3", "----", "1376–1383-15"]),
    ])
    assert list(snapshot["numbers"]) == ["T 3-08"]
    assert refused == ["05-3", "----", "1376–1383-15"]


def test_a_decision_with_no_recorded_date_stays_sortable(monkeypatch, tmp_path):
    # 19 of the 23,739 artifacts carry a null avgorandedatum; a None in the
    # candidate list crashes the sort the first time it meets a dated sibling
    snapshot, _refused = _snapshot(monkeypatch, tmp_path, [
        _artifact("https://lagen.nu/dom/nja/2022/not/4", "HDO",
                  "Högsta domstolen", None, ["B 1084-22"]),
        _artifact("https://lagen.nu/dom/nja/2022s1", "HDO", "Högsta domstolen",
                  "2022-01-11", ["B 1084-22"]),
    ])
    assert snapshot["numbers"]["B 1084-22"] == [
        ["HDO", "", "dom/nja/2022/not/4"],
        ["HDO", "2022-01-11", "dom/nja/2022s1"]]


def test_the_kammarratt_upphandlingsmal_join_the_snapshot(monkeypatch,
                                                         tmp_path):
    # the kkvdomar decisions cite each other by court and case number, so the
    # snapshot holds them beside dv's; a superseded decision's empty
    # placeholder holds no number
    snapshot, refused = _snapshot(monkeypatch, tmp_path, [
        _artifact("https://lagen.nu/dom/nja/2009s672", "HDO", "Högsta domstolen",
                  "2009-11-03", ["T 3-08"])], kkvdomar=[
        _artifact("https://lagen.nu/dom/kgg/2666-18/2018-12-14", "KGG",
                  "Kammarrätten i Göteborg", "2018-12-14", ["2666-18"]),
        ""])
    assert snapshot["numbers"]["2666-18"] == [
        ["KGG", "2018-12-14", "dom/kgg/2666-18/2018-12-14"]]
    assert snapshot["courts"]["KGG"] == ["Kammarrätten i Göteborg"]
    assert refused == []


def test_write_reports_whether_the_file_changed(monkeypatch, tmp_path):
    # the snapshot is not a recipe input (stage.CASENUMBER_CODE), so nothing
    # re-stales on a change; a full-source dv parse still says whether the
    # file changed, and identical content leaves it untouched
    monkeypatch.setattr(casenumbers.layout, "artifacts", lambda source: [])
    path = tmp_path / "casenumbers.json"
    assert casenumbers.write(path)[3] is True          # written for the first time
    written = path.read_text(encoding="utf-8")
    assert casenumbers.write(path)[3] is False         # same tree, same bytes
    assert path.read_text(encoding="utf-8") == written  # and left untouched


def test_the_snapshot_lives_beside_the_identity_index():
    # datasets spells the one artifact-tree path layout cannot own (the import
    # chain, see datasets.CASENUMBERS); this keeps the two from drifting apart
    assert datasets.CASENUMBERS.parent == layout.DOM_INDEX.parent
    assert datasets.CASENUMBERS.name in layout._NON_ARTIFACT_NAMES


def test_a_missing_snapshot_raises_instead_of_unlinking_every_citation(tmp_path):
    with pytest.raises(AssertionError, match="lagen dv casenumbers"):
        datasets.load_casenumbers(tmp_path / "casenumbers.json")
