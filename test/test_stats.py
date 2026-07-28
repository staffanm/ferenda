"""Tests for the `stats` vertical: the artifact scan's measurement rules, the
model's on-disk pruning, and the chart/page projection.

The scan rules are the ones worth locking in -- each of them silently poisons a
whole family of numbers when it regresses, and a wrong number on a statistics
page looks exactly like a right one.
"""

import json

from accommodanda.stats import charts, compute, render, scan
from accommodanda.stats.model import Cell, Measure, Point, Report, Row


def write_artifact(tmp_path, name, art):
    path = tmp_path / name
    path.write_text(json.dumps(art, ensure_ascii=False), encoding="utf-8")
    return path


def law(structure, title="Testlag (2001:1)", uri="https://lagen.nu/2001:1",
        amendments=(), **props):
    return {"uri": uri, "structure": structure, "amendments": list(amendments),
            "metadata": {"properties": {"dcterms:title": title, **props}}}


def scanned_law(paragraf_lengths=(), amendments=(), ikraft="1970-01-01",
                utfardad=None, uri="https://lagen.nu/1970:1"):
    """A `scan.scan_sfs` result, as the measure builders read it."""
    return {"kind": "law", "uri": uri, "title": "Testlag", "clean_title": "Testlag",
            "ikraft": ikraft, "utfardad": utfardad, "chars": 0, "paragrafer": 0,
            "kapitel": 0, "stycken": 0,
            "paragraf_lengths": list(paragraf_lengths),
            "amendments": list(amendments)}


def amendment(ikraft=None, ersatter=(), inforsI=(), forarbeten=(), aid="SFS 1999:1"):
    return {"id": aid, "ikraft": ikraft, "utfardad": None, "omfattning": "",
            "forarbeten": list(forarbeten), "inforsI": list(inforsI),
            "ersatter": list(ersatter), "celex": None}


def paragraf(ordinal, children, pid=None):
    return {"type": "paragraf", "id": pid or "P%s" % ordinal, "ordinal": ordinal,
            "children": children}


def test_table_cells_count_as_text(tmp_path):
    # a rad's `cells` are two levels deep -- a list of cells, each of them a run
    # list -- so a naive read of them comes back empty and a definition paragraf
    # whose whole body is a table measures the length of its stem alone
    rad = {"type": "rad", "cells": [["ordförande:"],
                                    ["den som leder ", {"text": "nämnden"}]]}
    assert scan._runs_text(rad) == "ordförande: den som leder nämnden"

    art = law([paragraf("1", [
        {"type": "stycke", "text": "I denna lag betyder"},
        {"type": "tabell", "children": [rad]}])])
    scanned = scan.scan_sfs(write_artifact(tmp_path, "t.json", art))
    assert scanned["paragrafer"] == 1
    # the stem is 19 characters; the table carries the rest of the rule
    assert scanned["paragraf_lengths"][0][0] > 40
    assert scanned["chars"] > 40


def test_provenance_and_renumbering_are_not_rules(tmp_path):
    # the trailing "Lag (2011:590)." names the amendment that last touched the
    # node, and a renumbering stub is a pointer rather than a rule -- counted as
    # text either one wins "shortest paragraf" outright
    art = law([
        paragraf("1", [{"type": "stycke",
                        "text": "Denna lag gäller. Lag (2011:590)."}]),
        paragraf("2", [{"type": "stycke", "text": "Ny beteckning 4 §."}]),
    ])
    scanned = scan.scan_sfs(write_artifact(tmp_path, "t.json", art))
    # the stub contributes no length row at all; the other keeps only its rule
    assert [(c, o) for c, _, o in scanned["paragraf_lengths"]] == [(17, "1")]


def test_clean_title_drops_beteckning_and_temporal_markers():
    assert scan._clean_title("Ellag (1997:857)") == "Ellag"
    assert scan._clean_title(
        "/Rubriken träder i kraft I:2027-01-01/ Lag om skatt") == "Lag om skatt"


def test_a_historical_consolidation_is_a_version_not_a_law(tmp_path):
    # letting one in would list the same statute ten times in a "longest law"
    # ranking -- it is the same law at another moment
    art = law([], uri="https://lagen.nu/1998:808/konsolidering/2020:1")
    scanned = scan.scan_sfs(write_artifact(tmp_path, "t.json", art))
    assert scanned == {"kind": "version", "of": "https://lagen.nu/1998:808"}


def test_an_empty_artifact_reads_as_skipped(tmp_path):
    # a zero-byte artifact is the pipeline's way of recording "no document here"
    path = tmp_path / "empty.json"
    path.write_bytes(b"")
    assert scan.load(path) is None
    assert scan.scan_sfs(path) == {"kind": "skipped"}


def test_restarting_article_numbers_mark_a_multi_instrument_act():
    # an accession act carries the treaty *plus* the act of accession, so its
    # articles do not belong to one act and cannot enter a "longest article" list
    assert scan._restarts(["1", "2", "3"]) is False
    assert scan._restarts(["1", "2", "1", "2"]) is True
    assert scan._restarts(["1", "1a", "2"]) is True     # "1a" reads as 1


def test_the_artifact_drops_a_measure_s_empty_fields():
    # writing all twelve keys on every measure triples the artifact and makes a
    # diff between two builds unreadable -- and the diff is why it is stored
    art = Report("2026-07-28", [Measure(1, "A", "Antal lagar", "scalar", value=3)])
    assert art.to_artifact() == {
        "generated": "2026-07-28",
        "measures": [{"id": 1, "group": "A", "title": "Antal lagar",
                      "kind": "scalar", "value": 3}]}


def test_bars_are_scaled_inside_a_group_not_across_it():
    # 55 313 and 24 on one scale draws the short end as nothing at all
    html = charts.toplist_html(Measure(
        1, "A", "Längst och kortast", "toplist", unit="tecken",
        rows=[Row("Jätten", 50000, group="Längst"),
              Row("Dvärgen", 24, group="Kortast")]))
    assert "--w:100.00%" in html
    assert html.count("--w:100.00%") == 2          # each group tops out at 100
    assert "<th scope=\"rowgroup\" colspan=\"2\">Längst</th>" in html


def test_the_heat_table_flips_its_ink_on_the_dark_steps():
    html = charts.matrix_html(Measure(
        1, "D", "Hänvisningar", "matrix",
        cells=[Cell("SFS", "SFS", 4155603), Cell("SFS", "EU", 578)]))
    # the ramp is logarithmic, so the small cell is still visible rather than
    # rounded to the lightest step's neighbour
    assert charts.HEAT[0] in html and charts.HEAT[-1] in html
    assert html.count("on-dark") == 1


def test_a_plotted_measure_carries_its_table_view():
    html = charts.figure(Measure(
        1, "B", "Per år", "series", unit="författningar", xlabel="år",
        points=[Point("2024", 100), Point("2025", 120)]))
    assert "<svg" in html and "Visa som tabell" in html
    assert "<th scope=\"col\">år</th>" in html


def test_text_age_weights_every_paragraf_by_its_own_amendment():
    # a statute is a mosaic: 20 paragrafer, half of them rewritten in 2020, the
    # rest untouched since the law took effect in 1970 -> mean year 1995
    plens = [(100, "P%d" % i, str(i)) for i in range(1, 21)]
    touched = ["https://lagen.nu/1970:1#P%d" % i for i in range(1, 11)]
    r = scanned_law(plens, [amendment("2020-01-01", ersatter=touched)])
    assert compute.text_age(r) == 1995

    # the newest amendment touching a paragraf wins, not the last one seen
    r = scanned_law(plens, [amendment("2020-01-01", ersatter=touched),
                            amendment("1990-01-01", ersatter=touched)])
    assert compute.text_age(r) == 1995
    # an inserted paragraf dates from its insertion just as a rewritten one does
    assert compute.text_age(
        scanned_law(plens, [amendment("2020-01-01", inforsI=touched)])) == 1995


def test_text_age_declines_to_answer_where_the_register_is_silent():
    plens = [(100, "P%d" % i, str(i)) for i in range(1, 21)]
    dated = amendment("2020-01-01", ersatter=["https://lagen.nu/1970:1#P1"])
    # a dated amendment that does not say what it touched would make every
    # paragraf read as original -- the law must drop out rather than read as old
    assert compute.text_age(scanned_law(plens, [dated, amendment("2021-01-01")])) is None
    # too few paragrafer for a mean to say anything
    assert compute.text_age(scanned_law(plens[:5], [dated])) is None
    # no amendments at all, and no ikraft date to anchor the original text
    assert compute.text_age(scanned_law(plens, [])) is None
    assert compute.text_age(scanned_law(plens, [dated], ikraft=None)) is None


def test_notice_days_is_a_base_statute_measure():
    assert compute.notice_days(
        scanned_law(ikraft="1970-03-02", utfardad="1970-01-01")) == 60
    # a change act carries no utfärdandedatum, so it cannot be measured at all
    assert compute.notice_days(scanned_law(ikraft="1970-03-02")) is None
    # retroactive force is not a notice period
    assert compute.notice_days(
        scanned_law(ikraft="1969-01-01", utfardad="1970-01-01")) is None


def test_bill_lag_joins_amendments_to_the_bills_they_cite():
    propdate = {"Prop. 1998/99:1": "1999-01-01"}
    r = scanned_law(amendments=[
        amendment("1999-03-02", forarbeten=["Prop. 1998/99:1", "Bet. 1998/99:X1"]),
        amendment("1999-03-02", forarbeten=["Prop. 1900/01:9"]),   # no date known
        amendment(None, forarbeten=["Prop. 1998/99:1"]),           # never in force
        amendment("1998-01-01", forarbeten=["Prop. 1998/99:1"]),   # predates the bill
    ])
    road = compute.bill_lag(propdate, [r])
    assert [(d, f) for d, f, _, _ in road] == [(60, "Prop. 1998/99:1")]


def test_the_page_shows_only_the_groups_that_were_measured():
    html = render.render_stats({
        "generated": "2026-07-28",
        "measures": [{"id": 1, "group": "A", "title": "Antal & mått",
                      "kind": "scalar", "value": 42, "note": "Bara gällande rätt."}]})
    assert 'id="gA"' in html and 'id="gB"' not in html    # B measured nothing
    assert "Antal &amp; mått" in html                     # titles are escaped
    assert "Bara gällande rätt." in html                  # the caveat is on the page
    assert "</p>'" not in html
