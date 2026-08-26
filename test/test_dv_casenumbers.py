"""The case-number snapshot (ferenda/dv/casenumbers.py): what it keeps, what
it refuses, and whether it rewrote the file -- which is what a full-source dv
parse reports on, since the snapshot is a parse input for five sources."""

import json

from ferenda.dv import casenumbers


def _artifact(uri, court, namn, date, numbers):
    return {"uri": uri, "court": court, "court_namn": namn,
            "avgorandedatum": date, "malnummer": numbers}


def _snapshot(monkeypatch, tmp_path, artifacts):
    """Write `artifacts` as a dv tree `build()` reads, and return its snapshot."""
    paths = []
    for i, art in enumerate(artifacts):
        path = tmp_path / ("a%d.json" % i)
        path.write_text(json.dumps(art), encoding="utf-8")
        paths.append(path)
    monkeypatch.setattr(casenumbers.layout, "artifacts", lambda source: paths)
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


def test_write_reports_whether_the_file_changed(monkeypatch, tmp_path):
    # the snapshot is a parse input (build.CASENUMBER_CODE): new content
    # re-stales five sources' parses, identical content costs nothing, and a
    # full-source dv parse says which of the two happened
    monkeypatch.setattr(casenumbers.layout, "artifacts", lambda source: [])
    path = tmp_path / "casenumbers.json"
    assert casenumbers.write(path)[3] is True          # written for the first time
    written = path.read_text(encoding="utf-8")
    assert casenumbers.write(path)[3] is False         # same tree, same bytes
    assert path.read_text(encoding="utf-8") == written  # and left untouched
