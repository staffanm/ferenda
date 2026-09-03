"""`aireport.Report`: the shared counter, closing line and ledger segment of the
`ai-*` actions -- counts by outcome, the coverage cell only for a run that
enumerated its own ids, the work so far on record when an item raises."""

import json

import pytest

from ferenda.lib import aireport, annstore, freshness
from ferenda.lib.stage import RUN


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(freshness, "RUNS", tmp_path / "runs.ndjson")
    monkeypatch.setattr(freshness, "STATUS", tmp_path / "status.json")
    monkeypatch.setattr(freshness, "RUN_ID", "r1")
    monkeypatch.setattr(RUN, "force", False)
    return tmp_path


def _segments(ledger):
    return [json.loads(l) for l in (ledger / "runs.ndjson").read_text().splitlines()
            if '"segment"' in l]


def test_closing_line_counts_every_outcome_by_reason(ledger, capsys):
    with aireport.Report("sfs", "ai-includegraphics", 4) as report:
        report.item("2007:90")
        report.wrote("2007:90", ledger / "90.graphics", note="localized 3 gap(s)")
        report.skip("1998:204", "no graphic gaps")
        report.skip("2010:800", "no graphic gaps")
        report.fail("1915:218", ValueError("boom"))
    out, err = capsys.readouterr()
    assert "sfs ai-includegraphics 2007:90: wrote %s (localized 3 gap(s))" % (ledger / "90.graphics") in out
    assert "sfs ai-includegraphics 1915:218: FAILED -- boom" in err
    last = out.strip().splitlines()[-2:]
    assert last[0].startswith("sfs ai-includegraphics: 1 layer(s) written, "
                              "2 skipped (no graphic gaps 2), 1 failed in ")
    assert last[1] == "  failed: 1915:218"
    seg, = _segments(ledger)
    assert (seg["step"], seg["source"], seg["total"], seg["ran"], seg["errors"],
            seg["skipped_fresh"], seg["status"]) == \
        ("ai-includegraphics", "sfs", 4, 1, 1, 2, "errors")
    assert not (ledger / "status.json").exists()      # a targeted run owns no cell


def test_a_component_writing_many_layers_counts_them(capsys):
    with aireport.Report("sfs", "ai-hierarki", 1) as report:
        report.wrote("2018:585", layers=7, note="40 rows")
    out = capsys.readouterr().out
    assert "sfs ai-hierarki 2018:585: wrote 7 layer(s) (40 rows)" in out
    assert "sfs ai-hierarki: 7 layer(s) written over 1 item(s), 0 failed in" in out


def test_corpus_wide_run_records_its_coverage(ledger, capsys):
    with aireport.Report("sfs", "ai-hierarki", 5, corpus_wide=True) as report:
        report.skip("a", "layers present", present=True)
        report.skip("b", "layers present", present=True)
        report.wrote("c", layers=3)
        report.fail("d", RuntimeError("x"))
    assert "-- 3 of 5 carry a layer" in capsys.readouterr().out
    cell = json.loads((ledger / "status.json").read_text())["sfs"]["ai-hierarki"]
    assert (cell["total"], cell["fresh"], cell["missing"], cell["failed"], cell["run"]) == \
        (5, 3, 1, 1, "r1")


def test_a_fault_mid_run_still_records_the_work_done(ledger, capsys):
    with pytest.raises(RuntimeError):
        with aireport.Report("eurlex", "ai-annotate", 3) as report:
            report.wrote("32016R0679", ledger / "a.ann")
            raise RuntimeError("endpoint down")
    assert "eurlex ai-annotate: 1 layer(s) written, 0 failed in" in capsys.readouterr().out
    seg, = _segments(ledger)
    assert (seg["ran"], seg["status"]) == (1, "errors")


def test_a_usage_error_before_any_work_reports_nothing(ledger, capsys):
    with pytest.raises(SystemExit):
        with aireport.Report("eurlex", "ai-annotate"):
            raise SystemExit("usage: ...")
    assert capsys.readouterr().out == ""
    assert not (ledger / "runs.ndjson").exists()


def test_dry_run_counts_what_it_would_do(capsys):
    with aireport.Report("eurlex", "ai-annotate", 2) as report:
        report.plan("32016R0679", "annotate -> x.ann")
        report.plan("32022L2555", "annotate -> y.ann")
    out = capsys.readouterr().out
    assert "eurlex ai-annotate 32016R0679: would annotate -> x.ann" in out
    assert out.strip().endswith("eurlex ai-annotate: would run 2 item(s)")


def test_verified_layer_is_skipped_unless_forced(tmp_path, monkeypatch, capsys):
    layer = tmp_path / "x.ann"
    layer.write_text(json.dumps({"meta": {"status": annstore.VERIFIED}}))
    monkeypatch.setattr(RUN, "force", False)
    with aireport.Report("eurlex", "ai-annotate", 1) as report:
        assert report.verified("x", layer)
    assert "1 skipped (verified, kept 1)" in capsys.readouterr().out
    monkeypatch.setattr(RUN, "force", True)
    with aireport.Report("eurlex", "ai-annotate", 1) as report:
        assert not report.verified("x", layer)
        assert not report.verified("y", tmp_path / "missing.ann")


def test_duration_reads_as_seconds_minutes_or_hours():
    assert aireport._duration(42) == "42 s"
    assert aireport._duration(600) == "10 min"
    assert aireport._duration(7500) == "2h05m"


def test_segment_matches_the_stage_writers_shape(ledger):
    with aireport.Report("remisser", "ai-analyze", 2) as report:
        report.wrote("sou/2026-8/x", ledger / "x.ann")
        report.skip("sou/2026-8/y", "already analysed", present=True)
    seg, = _segments(ledger)
    assert set(seg) >= {"step", "source", "secs", "total", "ran", "errors",
                        "skipped_fresh", "skipdoc", "status", "slowest"}


def test_a_dry_run_that_also_skipped_keeps_its_plan_count(capsys):
    with aireport.Report("sfs", "ai-includegraphics", 2) as report:
        report.skip("1998:204", "no graphic gaps")
        report.plan("2007:90", "localize 3 gap(s)")
    assert "would run 1, 1 skipped (no graphic gaps 1)" in capsys.readouterr().out
