"""Detection of graphics/formulas/tables the SFST text database omits.

Two layers: the pure detectors in ``sfs.graphics`` (markers, road-sign codes,
change-note provenance) and their wiring into ``nf.to_normalform`` (a raw
marker stycke becomes a typed ``grafik`` node; a 2007:90 sign-code cell gets a
``grafik`` beside it). Model trees are built directly so the cases stay small
and corpus-independent -- the four real statutes (2002:780, 2005:874,
2017:1272, 2007:90) are covered structurally by these shapes.
"""

import json

import pytest

from accommodanda.lib import annstore, facsimile, render
from accommodanda.lib.render import plain
from accommodanda.sfs import graphics
from accommodanda.sfs.model import (
    Bilaga,
    Forfattning,
    Kapitel,
    Paragraf,
    Rubrik,
    Stycke,
    Tabell,
    Tabellrad,
)
from accommodanda.sfs.nf import to_normalform

BASEFILE = "9999:998"


def grafiks(nf):
    """Every graphic gap in a normal form: block grafik nodes and the grafik
    attached to a road-sign table row."""
    out = []
    def walk(nodes):
        for n in nodes:
            if n.get("type") == "grafik":
                out.append(n)
            if n.get("type") == "rad" and n.get("grafik"):
                out.append(n["grafik"])
            walk(n.get("children", []))
    walk(nf["structure"])
    return out


# ---- pure detectors -------------------------------------------------------

def test_marker_gap_standalone():
    assert graphics.marker_gap("/Formeln är inte med här/") == ("formel", None)
    # the period-inside-slashes variant (2017:1272) and lowercase noun
    assert graphics.marker_gap("/Bilagan är inte med här./") == ("bilaga", None)
    assert graphics.marker_gap("/formeln är inte med här/") == ("formel", None)
    # a trailing change note is absorbed as provenance, not text
    assert graphics.marker_gap(
        "/Formeln är inte med här/ Förordning (2021:734).") == ("formel", "2021:734")
    # real delimiter-free and editorial-tail corpus forms
    assert graphics.marker_gap("Formeln är inte med här.") == ("formel", None)
    assert graphics.marker_gap(
        "Bilaga 2 är inte med här. Bilagan senast ändrad genom lag "
        "(2025:1369).") == ("bilaga", "2025:1369")
    assert graphics.marker_gap(
        "/Bilagan är inte med här. Bilagan senast ändrad genom lag "
        "(2013:1017)./") == ("bilaga", "2013:1017")
    assert graphics.marker_gap("Bilagorna 1, 2 och 3 är inte med här.") == (
        "bilaga", None)
    assert graphics.marker_gap("(Uppställningen är inte med här.)") == (
        "tabell", None)


def test_marker_gap_rejects_embedded_and_placeholder():
    # a marker inside real prose is left in place (no text may be lost)
    assert graphics.marker_gap("Se figuren /Figuren är inte med här/ nedan") is None
    # the empty-body placeholder is not a graphic omission
    assert graphics.marker_gap("(Författningstext saknas)") is None
    assert graphics.marker_gap("En helt vanlig paragraf.") is None


def test_heading_gap_splits_trailing_marker():
    assert graphics.heading_gap("Bilaga 1 /Bilagan är inte med här./") == (
        "Bilaga 1", "bilaga")
    # a bare marker is a marker_gap, not a heading
    assert graphics.heading_gap("/Bilagan är inte med här/") == (
        "/Bilagan är inte med här/", None)
    assert graphics.heading_gap("Övergångsbestämmelser") == (
        "Övergångsbestämmelser", None)


def test_roadsign_code():
    assert graphics.roadsign_code("A1 Varning för farlig kurva") == "A1"
    assert graphics.roadsign_code("C31 Hastighetsbegränsning") == "C31"
    assert graphics.roadsign_code("A13a Varning") == "A13a"
    # chapter 3 (Signalbild) colour names and chapter 1 prose never match
    assert graphics.roadsign_code("Röd") is None
    assert graphics.roadsign_code("Signalbild") is None


def test_changenote_and_governing():
    assert graphics.changenote_sfs("… mm. Förordning (2017:923).") == "2017:923"
    assert graphics.changenote_sfs("ingen ändringsnot här") is None
    # governing note = the last change note among a container's stycken
    kids = [Stycke("1 Balanstalet,BT"), Stycke("/Formeln är inte med här/"),
            Stycke("3 Pensionsskulden. Förordning (2021:734).")]
    assert graphics.governing_sfs(kids) == "2021:734"
    assert graphics.governing_sfs([Stycke("ingen not")]) is None


# ---- nf wiring ------------------------------------------------------------

def test_formula_bilaga_gaps_inherit_governing_sfs():
    # 2002:780 shape: each formula is its own stycke; only the last carries the
    # change note, which governs the whole bilaga
    doc = Forfattning(children=[Bilaga(rubrik="Bilaga", children=[
        Stycke("1 Balanstalet,BT"),
        Stycke("/Formeln är inte med här/"),
        Stycke("2 Omsättningstiden,OT"),
        Stycke("/Formeln är inte med här/"),
        Stycke("3 Pensionsskulden,S"),
        Stycke("/Formeln är inte med här/ Förordning (2021:734)."),
    ])])
    gs = grafiks(to_normalform(doc, BASEFILE))
    assert [g["sort"] for g in gs] == ["formel", "formel", "formel"]
    # every gap points at the amendment that set the bilaga, even the first two
    assert {g["satt_av"] for g in gs} == {"2021:734"}
    # identical markers get distinct, collision-free ids (not dedup-collapsed)
    assert [g["id"] for g in gs] == ["G1", "G2", "G3"]


def test_heading_marker_keeps_heading_and_emits_grafik():
    doc = Forfattning(children=[
        Rubrik(text="Bilaga 1 /Bilagan är inte med här./")])
    nf = to_normalform(doc, BASEFILE)
    kinds = [n["type"] for n in nf["structure"]]
    assert kinds == ["rubrik", "grafik"]
    assert plain(nf["structure"][0]["text"]) == "Bilaga 1"
    assert nf["structure"][1]["sort"] == "bilaga"


def test_heading_marker_keeps_explicit_provenance():
    doc = Forfattning(children=[Rubrik(
        text="Bilaga 1 /Bilagan är inte med här. Bilagan senast ändrad "
             "genom lag (2025:1369)./")])
    graphic = grafiks(to_normalform(doc, BASEFILE))[0]
    assert graphic["satt_av"] == "2025:1369"


def test_roadsign_gap_only_for_roadsign_docs():
    doc = Forfattning(children=[Kapitel(ordinal="2", rubrik="2 kap.", children=[
        Paragraf(ordinal="5", children=[Stycke("Vägmärken", children=[
            Tabell(rows=[
                Tabellrad(cells=["Märke", "Närmare föreskrifter"]),
                Tabellrad(cells=["A1 Varning för farlig kurva", "Märket anger…"]),
                Tabellrad(cells=["A2 Varning för farlig nedförslutning", "…"]),
            ])])])])])
    gs = grafiks(to_normalform(doc, "2007:90"))
    assert [(g["sort"], g["code"]) for g in gs] == [
        ("vagmarke", "A1"), ("vagmarke", "A2")]
    # the same tree under any other statute yields no road-sign gaps
    assert grafiks(to_normalform(doc, BASEFILE)) == []


# ---- gap collection from an artifact ---------------------------------------

def test_collect_gaps_carries_anchor_and_bilaga_ordinal():
    doc = Forfattning(children=[Bilaga(rubrik="Bilaga 1", children=[
        Stycke("1 Balanstalet, BT"),
        Stycke("/Formeln är inte med här/ Förordning (2021:734)."),
    ])])
    nf = to_normalform(doc, BASEFILE)
    gaps = graphics.collect_gaps(nf["structure"])
    assert len(gaps) == 1
    g = gaps[0]
    assert g["sort"] == "formel"
    assert g["satt_av"] == "2021:734"
    assert g["bilaga_ordinal"] == 1                 # from the "Bilaga 1" rubrik
    assert g["anchor"] == "1 Balanstalet, BT"       # nearest preceding stycke


def test_collect_gaps_roadsign_anchor_is_the_row():
    doc = Forfattning(children=[Kapitel(ordinal="2", rubrik="2 kap.", children=[
        Paragraf(ordinal="5", children=[Stycke("Vägmärken", children=[
            Tabell(rows=[
                Tabellrad(cells=["Märke", "Beskrivning"]),
                Tabellrad(cells=["A1 Varning för farlig kurva", "Märket anger…"]),
            ])])])])])
    gaps = graphics.collect_gaps(to_normalform(doc, "2007:90")["structure"])
    assert len(gaps) == 1
    assert gaps[0]["code"] == "A1"
    assert "A1 Varning för farlig kurva" in gaps[0]["anchor"]


def test_gap_key_is_semantic_and_temporal_duplicates_alias():
    doc = Forfattning(children=[
        Bilaga(rubrik="Bilaga 1", children=[
            Stycke("Stockholms innerstad"), Stycke("/Kartan är inte med här/"),
            Stycke("Essingeleden"), Stycke("/Kartan är inte med här/")]),
        Bilaga(rubrik="Bilaga 1", children=[
            Stycke("Stockholms innerstad"), Stycke("/Kartan är inte med här/"),
            Stycke("Essingeleden"), Stycke("/Kartan är inte med här/")]),
    ])
    nf = to_normalform(doc, BASEFILE)
    gaps = graphics.collect_gaps(nf["structure"])
    assert gaps[0]["key"] == gaps[2]["key"]
    assert gaps[1]["key"] == gaps[3]["key"]
    assert gaps[0]["key"] != gaps[1]["key"]
    assert all(g["key"].startswith("g-") for g in gaps)
    assert [g["key"] for g in grafiks(nf)] == [g["key"] for g in gaps]


# ---- provenance ------------------------------------------------------------

# 2004:629's register shape: two map bilagor amended independently and often,
# most touches naming the bilaga explicitly (the wholesale replacements leave no
# in-text change note, so the register is authoritative)
REGISTER_2004_629 = {"andringsforfattningar": [
    {"beteckning": "2005:547", "anteckningar": "ändr. bil. p 1"},
    {"beteckning": "2010:1023",
     "anteckningar": "nuvarande bil. betecknas bil. 1; ändr. 9, 10 §§; ny bil. 2"},
    {"beteckning": "2011:1490", "anteckningar": "ändr. bil. 2"},
    {"beteckning": "2013:1067", "anteckningar": "ändr. bil. 1"},
    {"beteckning": "2016:1007", "anteckningar": "ändr. bil. 2"},
    {"beteckning": "2018:200", "anteckningar": "ändr. bil. 1"},
    {"beteckning": "2020:120", "anteckningar": "ändr. bil. 2"},
    {"beteckning": "2023:395", "anteckningar": "ändr. bil. 1"},
]}


def test_latest_bilaga_amender_picks_the_latest_per_bilaga():
    # the 2004:629 acid test: two independent bilaga histories
    assert graphics.latest_bilaga_amender(REGISTER_2004_629, 1) == "2023:395"
    assert graphics.latest_bilaga_amender(REGISTER_2004_629, 2) == "2020:120"
    assert graphics.latest_bilaga_amender(REGISTER_2004_629, 3) is None


def test_touches_bilaga_does_not_confuse_1_and_10():
    assert graphics._touches_bilaga("ändr. bil. 1", 1)
    assert not graphics._touches_bilaga("ändr. bil. 10", 1)
    assert graphics._touches_bilaga("ändr. bil. 10", 10)
    # a bare 'bil.' matches only the unnumbered (single-bilaga) case
    assert graphics._touches_bilaga("ändr. bil.", None)
    assert not graphics._touches_bilaga("ändr. bil.", 1)
    # mentions that do not publish replacement content are not provenance
    assert not graphics._touches_bilaga("upph. bil. 1", 1)
    assert not graphics._touches_bilaga("nuvarande bil. betecknas bil. 1", 1)


def test_latest_bilaga_amender_ignores_not_yet_effective_change():
    register = {"andringsforfattningar": [
        {"beteckning": "2023:395", "anteckningar": "ändr. bil. 1",
         "ikraftDateTime": "2023-07-01T00:00:00"},
        {"beteckning": "2999:1", "anteckningar": "ändr. bil. 1",
         "ikraftDateTime": "2999-01-01T00:00:00"},
    ]}
    assert graphics.latest_bilaga_amender(register, 1) == "2023:395"


def test_provenance_in_numbered_bilaga_is_register_first():
    # the 2004:629 acid test at the provenance level. The maps carry the sort
    # "karta" (from /Kartan.../), NOT "bilaga" -- register-first must key on the
    # enclosing numbered bilaga, not the marker noun. bilaga 1 with no in-text
    # note -> the register's latest ändr. bil. 1 (2023:395).
    g1 = {"sort": "karta", "bilaga_ordinal": 1, "satt_av": None}
    assert graphics.provenance_sfs(g1, REGISTER_2004_629, "2004:629") == "2023:395"
    # bilaga 2 -> its own independent latest touch
    g2 = {"sort": "karta", "bilaga_ordinal": 2, "satt_av": "2020:120"}
    assert graphics.provenance_sfs(g2, REGISTER_2004_629, "2004:629") == "2020:120"


def test_provenance_bilaga_takes_latest_of_register_and_note():
    # a numbered-bilaga gap whose in-text note (2024:9) is NEWER than any
    # register bil. clause (bilaga 1 latest = 2023:395) -> the note wins (max)
    gap = {"sort": "bild", "bilaga_ordinal": 1, "satt_av": "2024:9"}
    assert graphics.provenance_sfs(gap, REGISTER_2004_629, "2004:629") == "2024:9"


def test_register_latest_amendment():
    assert graphics.register_latest_amendment(REGISTER_2004_629) == "2023:395"
    assert graphics.register_latest_amendment({"andringsforfattningar": []}) is None


def test_plan_localization_keeps_verified_current_relocalizes_the_rest():
    gaps = [_gap("G1", "karta", 1),
            _gap("G5", "karta", 2, "2020:120"),
            _gap("G6", "karta", 2)]
    existing = {
        # verified AND provenance-current (bilaga 1 -> 2023:395) -> kept
        "g-g1": {"sfs": "2023:395", "page": 2, "bbox": [1, 2, 3, 4],
                 "identity": gaps[0]["identity"], "verified": True},
        # verified but the wrong source (a stale hand-crop) -> re-localized
        "g-g5": {"sfs": "2019:1", "page": 1,
                 "identity": gaps[1]["identity"], "verified": True},
        # a generated cache entry -> re-localized
        "g-g6": {"sfs": "2020:120", "page": 3,
                 "identity": gaps[2]["identity"]}}
    keep, todo = graphics.plan_localization(
        gaps, existing, REGISTER_2004_629, "2004:629")
    assert set(keep) == {"g-g1"}                        # verified+current identity
    # G5 (provenance-drifted verified) + G6 (generated) re-localized, by source
    assert {s: [g["id"] for g in gg] for s, gg in todo.items()} == {
        "2020:120": ["G5", "G6"]}


def test_plan_localization_new_gaps_and_all_current():
    gaps = [_gap("G1", "formel", None, "2021:734")]
    # nothing localized yet -> all todo
    keep, todo = graphics.plan_localization(gaps, {}, {"andringsforfattningar": []},
                                            "2002:780")
    assert keep == {} and list(todo) == ["2021:734"]
    # already verified & current -> keep, nothing to do
    existing = {"g-g1": {"sfs": "2021:734", "page": 2,
                           "identity": gaps[0]["identity"], "verified": True}}
    keep, todo = graphics.plan_localization(
        gaps, existing, {"andringsforfattningar": []}, "2002:780")
    assert set(keep) == {"g-g1"} and todo == {}

    # the same key cannot carry curation onto a changed semantic identity
    existing["g-g1"]["identity"] = {"different": True}
    keep, todo = graphics.plan_localization(
        gaps, existing, {"andringsforfattningar": []}, "2002:780")
    assert keep == {} and list(todo) == ["2021:734"]


def test_provenance_outside_bilaga_uses_changenote_then_base():
    reg = {"andringsforfattningar": []}
    # a formula in the main text (no enclosing numbered bilaga) -> its change note
    assert graphics.provenance_sfs(
        {"sort": "formel", "bilaga_ordinal": None, "satt_av": "2021:734"},
        reg, "2002:780") == "2021:734"
    # an unamended (original-text) gap crops the base act's own PDF
    assert graphics.provenance_sfs(
        {"sort": "vagmarke", "bilaga_ordinal": None, "satt_av": None},
        reg, "2007:90") == "2007:90"


def test_provenance_unnumbered_bilaga_uses_register():
    reg = {"andringsforfattningar": [
        {"beteckning": "2020:5", "anteckningar": "ändr. bil."}]}
    gap = {"sort": "bild", "bilaga_ordinal": None, "in_bilaga": True,
           "satt_av": None}
    assert graphics.provenance_sfs(gap, reg, "2000:1") == "2020:5"


def _gap(gid, sort, ordinal, satt_av=None):
    identity = {"path": ["bilaga:%s" % ordinal] if ordinal else [],
                "sort": sort, "code": None, "anchor": gid.lower(),
                "occurrence": 1}
    return {"id": gid, "key": "g-" + gid.lower(), "identity": identity,
            "sort": sort, "bilaga_ordinal": ordinal,
            "in_bilaga": ordinal is not None, "satt_av": satt_av}


# ---- vision reply parsing --------------------------------------------------

def test_parse_localization_converts_pixels_to_points():
    # px -> raw PDF points at DPI 150: v * 72 / 150
    out = graphics.parse_localization(
        '{"G1": {"page": 2, "bbox": [150, 300, 600, 900], "alt": "BT"}}',
        {"G1", "G2"}, "2021:734")
    assert out["G1"] == {"sfs": "2021:734", "page": 2,
                         "bbox": [72, 144, 288, 432], "alt": "BT"}


def test_parse_localization_whole_page_when_no_bbox():
    out = graphics.parse_localization(
        '{"G1": {"page": 3}}', {"G1"}, "2021:734")
    assert out["G1"] == {"sfs": "2021:734", "page": 3, "alt": ""}
    assert "bbox" not in out["G1"]


def test_parse_localization_omitted_gap_is_absent():
    # the model saw G1 but not G2 (not on these pages) -> G2 simply missing
    out = graphics.parse_localization(
        '{"G1": {"page": 1, "bbox": [0, 0, 10, 10]}}', {"G1", "G2"}, "x")
    assert set(out) == {"G1"}


def test_parse_localization_rejects_bad_shapes():
    with pytest.raises(ValueError, match="unknown gap id"):
        graphics.parse_localization('{"G9": {"page": 1}}', {"G1"}, "x")
    with pytest.raises(ValueError, match="page must be"):
        graphics.parse_localization('{"G1": {"page": 0}}', {"G1"}, "x")
    with pytest.raises(ValueError, match="bbox must be"):
        graphics.parse_localization(
            '{"G1": {"page": 1, "bbox": [1, 2, 3]}}', {"G1"}, "x")
    with pytest.raises(ValueError, match="not among shown pages"):
        graphics.parse_localization('{"G1": {"page": 7}}', {"G1"}, "x",
                                    pages=[1, 2])
    with pytest.raises(ValueError, match="positive ordered bounds"):
        graphics.parse_localization(
            '{"G1": {"page": 1, "bbox": [20, 2, 3, 4]}}', {"G1"}, "x")
    with pytest.raises(ValueError, match="exceeds image"):
        graphics.parse_localization(
            '{"G1": {"page": 1, "bbox": [1, 2, 300, 400]}}', {"G1"}, "x",
            image_size=(200, 300))
    with pytest.raises(ValueError, match="entry must be"):
        graphics.parse_localization('{"G1": 7}', {"G1"}, "x")


def _fake_png(w, h):
    # only the IHDR width/height (bytes 16:24) matter to facsimile.png_size
    return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR"
            + w.to_bytes(4, "big") + h.to_bytes(4, "big"))


def test_localize_group_chunks_pages_and_merges(monkeypatch):
    class _P:
        def __init__(self, page):
            self.page = page

        def read_bytes(self):
            return _fake_png(1240, 1754)

    monkeypatch.setattr(facsimile, "page_count", lambda pdf: 8)
    monkeypatch.setattr(facsimile, "cached_page",
                        lambda source, src, pdf, p: _P(p))
    calls = []

    def fake_author(prompt, validate, model=None, images=(), max_tokens=None):
        calls.append((prompt, len(images)))
        # first chunk (pages 1-6) locates G1; second chunk (7-8) locates G2
        reply = ('{"G1": {"page": 2, "bbox": [150, 300, 600, 900]}}'
                 if len(calls) == 1 else '{"G2": {"page": 7}}')
        return validate(reply)

    gaps = [{"id": "G1", "key": "g-one", "identity": {"n": 1},
             "sort": "formel", "anchor": "1 Balanstalet"},
            {"id": "G2", "key": "g-two", "identity": {"n": 2},
             "sort": "formel", "anchor": "2 Omsättningstiden"}]
    progress = []
    out = graphics.localize_group(gaps, "/x.pdf", "2021:734", author=fake_author,
                                  log=progress.append)
    assert len(calls) == 2                       # 8 pages / 6 per call = 2 chunks
    assert calls[0][1] == 6 and calls[1][1] == 2  # images per chunk
    assert out["g-one"]["bbox"] == [72, 144, 288, 432]
    assert out["g-one"]["identity"] == {"n": 1}
    assert out["g-two"] == {"sfs": "2021:734", "page": 7, "alt": "",
                             "identity": {"n": 2}}
    # -v progress: a vision-call line names src + page range *before* each call,
    # so a hang/timeout is attributable rather than silent
    assert any("pages 1-6 -- vision call" in m for m in progress)
    assert any("pages 7-8 -- vision call" in m for m in progress)


def test_localize_group_refuses_partial_result(monkeypatch):
    class _P:
        def read_bytes(self):
            return _fake_png(100, 100)

    monkeypatch.setattr(facsimile, "page_count", lambda pdf: 1)
    monkeypatch.setattr(facsimile, "cached_page", lambda *args: _P())
    gap = {"id": "G1", "key": "g-one", "identity": {"n": 1},
           "sort": "formel", "anchor": "Balanstalet"}
    with pytest.raises(ValueError, match="did not locate gap.*G1"):
        graphics.localize_group(
            [gap], "/x.pdf", "2021:734",
            author=lambda prompt, validate, **kwargs: validate("{}"))


# ---- render: figure/img when localized, placeholder otherwise ---------------

DOC_URI = "https://lagen.nu/2002:780"


def _site(graphics_index):
    return render.Site(None, set(), graphics=graphics_index)


def test_render_grafik_placeholder_when_unlocalized():
    node = {"type": "grafik", "id": "G1", "sort": "formel", "satt_av": "2021:734"}
    html = render.render_grafik(node, _site({}), DOC_URI)
    assert 'class="grafik-saknas"' in html and 'data-grafik="G1"' in html
    assert "Formel saknas" in html and "SFS 2021:734" in html
    assert "<img" not in html                       # no crop until localized


def test_render_grafik_figure_when_localized():
    node = {"type": "grafik", "id": "G1", "key": "g-formula",
            "sort": "formel", "satt_av": "2021:734"}
    entry = {"sfs": "2021:734", "page": 2, "bbox": [72, 64, 523, 210],
             "alt": "Formel för balanstalet"}
    html = render.render_grafik(
        node, _site({(DOC_URI, "g-formula"): entry}), DOC_URI)
    assert '<figure class="grafik" data-grafik="g-formula">' in html
    # crop url = uri + node + a cache-buster derived from the bbox; & escaped
    assert "/api/v1/sfs-graphic?uri=https%3A%2F%2Flagen.nu%2F2002%3A780" in html
    assert "node=g-formula" in html and "&amp;v=" in html
    assert 'alt="Formel för balanstalet"' in html
    # attribution names the *source* (provenance) SFS, linked
    assert '<figcaption>Formel ur <a href="/2021:734">SFS 2021:734</a>' in html


def test_render_roadsign_cell_localized_and_not():
    site = _site({(DOC_URI, "g-a1"): {"sfs": "2007:90", "page": 12,
                                      "bbox": [10, 10, 40, 40], "alt": "Vägmärke A1"}})
    row = {"type": "rad", "cells": [["A1 Varning"], ["Beskrivning"]],
           "grafik": {"id": "G5", "key": "g-a1", "sort": "vagmarke",
                       "code": "A1"}}
    toc, rail = render.Toc(), render.Rail(site, DOC_URI)
    html = render.render_node(row, site, DOC_URI, toc, rail)
    assert '<td class="grafik" data-grafik="g-a1"><img' in html
    assert 'alt="Vägmärke A1"' in html
    # the same row with no layer entry falls back to the honest gap cell
    bare = render.render_node(row, _site({}), DOC_URI, render.Toc(),
                              render.Rail(_site({}), DOC_URI))
    assert '<td class="grafik-saknas" data-grafik="g-a1">[A1]</td>' in bare


def test_graphic_cache_buster_covers_source_page_and_bbox():
    base = {"sfs": "2021:734", "page": 2, "bbox": [1, 2, 3, 4]}
    urls = {
        render._grafik_crop(entry, DOC_URI, "g-one", "alt")
        for entry in (base, {**base, "sfs": "2022:1"},
                      {**base, "page": 3}, {**base, "bbox": [1, 2, 3, 5]})}
    assert len(urls) == 4


def test_graphics_index_keys_by_uri_and_stable_gap_key(tmp_path, monkeypatch):
    monkeypatch.setattr(annstore, "ROOT", tmp_path / "ann")
    p = annstore.path("sfs", "2002:780", ".graphics")
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({
        "meta": {"status": "generated", "uri": DOC_URI},
        "g-one": {"sfs": "2021:734", "page": 2, "bbox": [1, 2, 3, 4],
                  "verified": True},
        "g-two": {"sfs": "2021:734", "page": 3}}))
    idx = render._graphics_index()
    assert set(idx) == {(DOC_URI, "g-one")}  # meta + unverified draft skipped
    assert idx[(DOC_URI, "g-one")]["sfs"] == "2021:734"
