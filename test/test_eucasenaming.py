"""Tests for lib.eucasenaming -- the EU case number derived from a CELEX and the
name/citation a reader sees (the EU mirror of lib.casenaming)."""

import json

import pytest

from accommodanda.lib import eucasenaming


def test_case_number_court_of_justice():
    # 6·YYYY·C(ourt of Justice)·J(udgment)·serial -> "C-311/18"
    assert eucasenaming.case_number("62018CJ0311") == "C-311/18"


def test_case_number_general_court_and_tribunal():
    assert eucasenaming.case_number("62004TJ0201") == "T-201/04"
    assert eucasenaming.case_number("62005FJ0001") == "F-1/05"


def test_case_number_strips_serial_zeros_and_keeps_two_digit_year():
    assert eucasenaming.case_number("62009CJ0176") == "C-176/09"
    assert eucasenaming.case_number("61998CJ0007") == "C-7/98"


def test_case_number_opinion_shares_the_judgment_case_number():
    # an AG opinion (kind letter C) has the same case number as its judgment
    assert eucasenaming.case_number("62018CC0311") == "C-311/18"


def test_case_number_falls_back_to_celex_for_unknown_shape():
    # a joined-case / non-modern id we don't format shows its bare CELEX rather
    # than an empty name
    assert eucasenaming.case_number("6199?CJ") == "6199?CJ"


def test_named_case_reads_the_snapshot(tmp_path, monkeypatch):
    snapshot = tmp_path / "casenames.json"
    snapshot.write_text(json.dumps(
        {"cases": [{"celex": "62018CJ0311", "name": "Schrems II"}]}))
    monkeypatch.setattr(eucasenaming, "NAMEDEUCASES", snapshot)
    eucasenaming._names.cache_clear()

    assert eucasenaming.given_name("62018CJ0311") == "Schrems II"
    # page heading = the usual name; inbound label = "Number (Name)"
    assert eucasenaming.case_name("62018CJ0311") == "Schrems II"
    assert eucasenaming.case_citation("62018CJ0311") == "C-311/18 (Schrems II)"


def test_unnamed_case_falls_back_to_case_number(tmp_path, monkeypatch):
    snapshot = tmp_path / "casenames.json"
    snapshot.write_text(json.dumps({"cases": []}))
    monkeypatch.setattr(eucasenaming, "NAMEDEUCASES", snapshot)
    eucasenaming._names.cache_clear()

    assert eucasenaming.given_name("62009CJ0176") is None
    # heading and citation are both the bare case number when no name is curated
    assert eucasenaming.case_name("62009CJ0176") == "C-176/09"
    assert eucasenaming.case_citation("62009CJ0176") == "C-176/09"


def test_missing_snapshot_is_a_hard_error(tmp_path, monkeypatch):
    monkeypatch.setattr(eucasenaming, "NAMEDEUCASES", tmp_path / "absent.json")
    eucasenaming._names.cache_clear()
    # the committed snapshot is a hard invariant: absent -> a broken checkout.
    # The assert raises AssertionError normally; under `python -O` the assert is
    # stripped and the missing file surfaces as FileNotFoundError at read_text --
    # either way it is a hard error, never a silent empty map.
    with pytest.raises((AssertionError, FileNotFoundError)):
        eucasenaming.given_name("62018CJ0311")


@pytest.fixture(autouse=True)
def _clear_cache():
    # the committed snapshot is the default; don't let a test's monkeypatched map
    # leak into the next
    yield
    eucasenaming._names.cache_clear()
