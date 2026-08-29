"""Tests for the `stats` vertical: the artifact scan's measurement rules, the
model's on-disk pruning, and the chart/page projection.

The scan rules are the ones worth locking in -- each of them silently poisons a
whole family of numbers when it regresses, and a wrong number on a statistics
page looks exactly like a right one.
"""

import json
import re
import sqlite3

import pytest

from ferenda.lib import catalog, facets, layout, pathgraph
from ferenda.stats import charts, compute, render, scan
from ferenda.stats.model import Cell, Measure, Point, Report, Row, Tile


def write_artifact(tmp_path, name, art):
    path = tmp_path / name
    path.write_text(json.dumps(art, ensure_ascii=False), encoding="utf-8")
    return path


def law(structure, title="Testlag (2001:1)", uri="https://lagen.nu/2001:1",
        amendments=(), **props):
    return {"uri": uri, "structure": structure, "amendments": list(amendments),
            "metadata": {"properties": {"dcterms:title": title, **props}}}


def scanned_law(paragraf_lengths=(), amendments=(), ikraft="1970-01-01",
                utfardad=None, uri="https://lagen.nu/1970:1", chain=None):
    """A `scan.scan_sfs` result, as the measure builders read it."""
    return {"kind": "law", "uri": uri, "title": "Testlag", "clean_title": "Testlag",
            "ikraft": ikraft, "utfardad": utfardad, "chars": 0, "paragrafer": 0,
            "kapitel": 0, "stycken": 0,
            "paragraf_lengths": list(paragraf_lengths),
            "amendments": list(amendments), "chain": chain}


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
    assert scan._own_text(rad) == "ordförande: den som leder nämnden"

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
    assert [(c, o) for c, _, o in scanned["paragraf_lengths"]] == [(17, "1 §")]


def test_a_paragrafs_beteckning_carries_its_chapter(tmp_path):
    # "62 §" of a chaptered statute names nothing -- the reference has to be
    # "9 kap. 62 §", and the anchor is the only place the chapter survives
    art = law([paragraf("62", [{"type": "stycke", "text": "Stöd lämnas löpande."}],
                        pid="K9P62")])
    scanned = scan.scan_sfs(write_artifact(tmp_path, "t.json", art))
    assert [(a, o) for _, a, o in scanned["paragraf_lengths"]] \
        == [("K9P62", "9 kap. 62 §")]


def test_an_editorial_note_is_not_statute_text(tmp_path):
    # a repealed paragraf's body is the publisher's notice, typed `redaktionell`
    # by sfs/nf.py. Counted as text it wins "kortaste paragrafen" outright and
    # its statute wins "kortaste lagen" -- neither is a fact about the law.
    art = law([
        paragraf("1", [{"type": "stycke", "text": "Denna lag gäller."}]),
        paragraf("2", [{"type": "redaktionell", "sort": "upphavd",
                        "satt_av": "1982:1101",
                        "text": "Har upphävts genom lag (1982:1101)."}]),
    ])
    scanned = scan.scan_sfs(write_artifact(tmp_path, "t.json", art))
    assert [(c, o) for c, _, o in scanned["paragraf_lengths"]] == [(17, "1 §")]
    assert scanned["chars"] == len("Denna lag gäller.")


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


def test_an_amending_act_and_a_runaway_article_are_flagged(tmp_path):
    # CRR2's "Article 1" is 680 000 characters of quoted CRR: an ändringsakt's
    # articles measure the amended act, not their own, so the document is
    # flagged whole. An article whose text contains the OJ running head or the
    # signature block swallowed page furniture (the older tiers' runaway
    # defect) and loses its `clean` flag.
    def act(name, articles):
        return write_artifact(tmp_path, name, {
            "celex": "32019R0876", "doctype": "regulation", "lang": "swe",
            "title": "Testförordning", "date": "2019-05-20",
            "structure": [{"type": "article", "num": str(i + 1),
                           "children": [{"type": "stycke", "text": body}]}
                          for i, body in enumerate(articles)]})

    amending = scan.scan_eurlex(act("amending.json", [
        "Förordning (EU) nr 575/2013 ska ändras på följande sätt: …",
        "Denna förordning träder i kraft…"]))
    assert amending["amending"] is True

    runaway = scan.scan_eurlex(act("runaway.json", [
        "En vanlig artikel om tillsyn.",
        "Detta beslut riktar sig till medlemsstaterna. "
        "Utfärdat i Bryssel den 18 juli 2005. …"]))
    assert runaway["amending"] is False
    assert [(n, clean) for _, n, clean in runaway["lengths"]] \
        == [("1", True), ("2", False)]

    # every stray branch also has a legitimate-prose lookalike that must NOT
    # flag: "i detta sammanhang" contains ANHANG, "intyg utfärdat i en annan
    # medlemsstat" contains "utfärdat i" -- only the signature's full
    # "i <Place> den <day>" shape is the tell
    prose = scan.scan_eurlex(act("prose.json", [
        "Medlemsstaterna ska i detta sammanhang godta ett intyg utfärdat i "
        "en annan medlemsstat, även ett intyg utfärdat i Frankrike."]))
    assert [clean for _, _, clean in prose["lengths"]] == [True]

    # the addressing formula is a decision's final sentence: a long tail
    # after it is swallowed content (31998D0490 carries 205k characters of
    # its own reasoning there, with no furniture for the patterns to see),
    # while the ordinary short closer stays clean
    addressed = scan.scan_eurlex(act("addressed.json", [
        "Detta beslut riktar sig till medlemsstaterna. "
        "Det ska tillämpas från och med den 1 januari 2006.",
        "Detta beslut riktar sig till Franska republiken. " + "x" * 300]))
    assert [clean for _, _, clean in addressed["lengths"]] \
        == [True, False]

    # 31986L0431's text is mojibake ("Utfรคrdat i Bryssel"), so the plain
    # signature pattern misses it -- the Thai codepoint is the tell
    mojibake = scan.scan_eurlex(act("mojibake.json", [
        "Detta direktiv riktar sig till medlemsstaterna. Utfรคrdat "
        "i Bryssel den 24 juni 1986. ANEXO I …"]))
    assert [clean for _, _, clean in mojibake["lengths"]] == [False]

    # the bare OJ phrase is NOT furniture -- nearly every act's
    # entry-into-force article says it legitimately; only the running head's
    # dotted issue date right after the phrase marks a swallowed page
    ikraft = scan.scan_eurlex(act("ikraft.json", [
        "Denna förordning träder i kraft den tjugonde dagen efter det att "
        "den har offentliggjorts i Europeiska unionens officiella tidning."]))
    assert [clean for _, _, clean in ikraft["lengths"]] == [True]
    head = scan.scan_eurlex(act("head.json", [
        "2. När det hänvisas till denna punkt L 400/98 SV Europeiska "
        "unionens officiella tidning 30.12.2006 ska artiklarna…"]))
    assert [clean for _, _, clean in head["lengths"]] == [False]

    # a förordning signs "Utfärdad", a beslut/direktiv "Utfärdat" -- both
    # forms mark a swallowed signature (31987R0678's 32k ikraft article)
    signed = scan.scan_eurlex(act("signed.json", [
        "Denna förordning träder i kraft den tredje dagen efter det att den "
        "har offentliggjorts i Europeiska gemenskapens officiella tidning. "
        "Utfärdad i Bryssel den 26 januari 1987. …"]))
    assert [clean for _, _, clean in signed["lengths"]] == [False]


def test_every_marked_term_states_a_definition():
    """All four of `lib.begrepp`'s modes state one. A brottsrubricering and a
    parenthesised coinage say what the term means without setting the definition
    off from the sentence around it, and cutting at a boundary that is not there
    would lose the definition rather than trim it. The text has one job here --
    telling two definitions of the same term apart (measure 54) -- so the whole
    node is the right unit, and the finer sentence pick belongs to the begrepp
    page (`catalog.definition_sentences`)."""
    assert scan.definition_body_eu(
        "risk", "risk: risk för förlust orsakad av en incident.") \
        == "risk för förlust orsakad av en incident."
    assert scan.definition_body_sfs(
        "konsument", "konsument: en fysisk person som handlar.") \
        == "en fysisk person som handlar."
    # no "term:" head to cut at -- the sentence stays whole
    assert scan.definition_body_sfs(
        "mord", "Den som berövar annan livet, döms för mord till fängelse.") \
        == "Den som berövar annan livet, döms för mord till fängelse."
    assert scan.definition_body_sfs("dödning", "Inteckning får dödas (dödning).") \
        == "Inteckning får dödas (dödning)."


def test_a_definition_that_only_points_elsewhere_is_not_one():
    # "50. personuppgifter: personuppgifter enligt definitionen i artikel 4.1 i
    # förordning (EU) 2016/679" defines nothing -- it hands the term to the
    # dataskyddsförordning. The lead in front of the cue may restate the term
    # ("uppgifter" for "personuppgifter"), but a lead that says something new
    # makes the sentence a definition of its own.
    assert scan.is_cross_reference(
        "personuppgifter",
        "personuppgifter enligt definitionen i artikel 4.1 i förordning (EU) 2016/679.")
    assert scan.is_cross_reference(
        "personuppgifter",
        "uppgifter enligt definitionen i artikel 2 a i förordning (EG) nr 45/2001.")
    assert scan.is_cross_reference(
        "kvalificerat innehav",
        "detsamma som i 1 kap. 5 § lagen om bank- och finansieringsrörelse.")
    assert not scan.is_cross_reference(
        "risk", "risk för förlust eller störning orsakad av en incident.")
    assert not scan.is_cross_reference(
        "område",
        "en yta som anges av gemenskapen och medlemsstaten och som omfattar "
        "en eller flera anläggningar.")
    assert not scan.is_cross_reference(
        "BAT-referensdokument",
        "ett dokument som är resultatet av det informationsutbyte som "
        "anordnats i enlighet med artikel 13 och som upprättats för en "
        "angiven verksamhet.")


def test_a_superseded_wording_does_not_double_an_acts_definitions(tmp_path):
    # PBL 1 kap. 4 § stands in the artifact twice, as the wording expiring
    # 2027-01-01 and the one entering into force the same day. `sfs.nf`
    # suppresses the id of the variant that is not in force, and counting both
    # gives the act twice the definitions it states.
    def definition(term, pid):
        return {"type": "stycke", "id": pid,
                "text": [{"kind": "term", "predicate": "dcterms:subject",
                          "text": term, "uri": "https://lagen.nu/begrepp/X"},
                         ": ett område avsett för ett gemensamt behov,"]}

    art = law([{"type": "kapitel", "id": "K1", "children": [
        {"type": "paragraf", "id": "K1P4", "ordinal": "4",
         "upphor": "2027-01-01",
         "children": [definition("allmän plats", "K1P4S1")]},
        {"type": "paragraf", "id": None, "ordinal": "4",
         "ikrafttrader": "2027-01-01",
         "children": [definition("allmän plats", None)]}]}])
    scanned = scan.scan_sfs(write_artifact(tmp_path, "t.json", art))
    assert [(d["term"], d["place"], d["place_label"])
            for d in scanned["definitions"]] \
        == [("allmän plats", "K1P4", "1 kap. 4 §")]
    # the character and paragraf counts are deliberately untouched: measures 1,
    # 2 and 9 have always counted every variant
    assert scanned["paragrafer"] == 2


def test_a_corrigendum_does_not_republish_an_acts_definitions(tmp_path):
    # 32006R1907R(01) reprints REACH whole; its 44 definitions are the parent
    # act's, counted a second time. The article lengths keep it -- that measure
    # is about how well each manifestation parsed.
    def act(celex):
        return write_artifact(tmp_path, celex + ".json", {
            "celex": celex, "doctype": "regulation", "lang": "swe",
            "title": "Testförordning", "date": "2006-12-30",
            "structure": [{"type": "article", "num": "3", "children": [
                {"type": "paragraph", "id": "3.1", "num": "1",
                 "defines": "ämne",
                 "text": "ämne: ett kemiskt grundämne och dess föreningar."}]}]})

    assert [d["term"] for d in scan.scan_eurlex(act("32006R1907"))["definitions"]] \
        == ["ämne"]
    corrigendum = scan.scan_eurlex(act("32006R1907R(01)"))
    assert corrigendum["definitions"] == []
    assert len(corrigendum["lengths"]) == 1


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


def test_a_row_with_steps_folds_the_whole_chain_out():
    """A "23 steg" row is not checkable from its two ends: the reader cannot
    tell a chain of annual reissues from a chain of substance without seeing
    what stands between them, and those are different facts about the law."""
    html = charts.toplist_html(Measure(
        56, "D", "Längst mellan två författningar", "toplist", unit="steg",
        rows=[Row("PFS 2025:2", 2, "https://lagen.nu/pfs/2025:2",
                  detail="→ PFS 2023:1",
                  steps=[Row("PFS 2025:2", 0, "https://lagen.nu/pfs/2025:2",
                             detail="Pensionsmyndighetens föreskrifter …"),
                         Row("PFS 2024:3", 1, "https://lagen.nu/pfs/2024:3",
                             detail="Pensionsmyndighetens föreskrifter …"),
                         Row("PFS 2023:1", 2, "https://lagen.nu/pfs/2023:1",
                             detail="Pensionsmyndighetens föreskrifter …")])]))
    assert '<details class="viz-steps"><summary>Visa kedjan</summary>' in html
    assert html.count("<li>") == 3                  # every act in the chain
    assert '<a href="/pfs/2024:3">PFS 2024:3</a>' in html   # the middle links
    assert html.count("Pensionsmyndighetens föreskrifter …") == 3
    # the row itself is unchanged: label, detail, bar, value
    assert '<span class="viz-detail">→ PFS 2023:1</span>' in html
    assert '<span class="viz-val">2</span>' in html


def test_a_chain_survives_the_round_trip_through_the_artifact():
    """The steps are nested rows, and the artifact stores them as plain dicts.
    Rebuilding with `Row(**r)` left them dicts and the page raised on the
    first foldout -- so the page is rendered from the artifact here, not from
    the dataclasses."""
    art = Report(generated="2026-08-27", measures=[Measure(
        56, "D", "Längst mellan två författningar", "toplist", unit="steg",
        rows=[Row("PFS 2025:2", 1, "https://lagen.nu/pfs/2025:2",
                  detail="→ PFS 2024:3",
                  steps=[Row("PFS 2025:2", 0, "https://lagen.nu/pfs/2025:2"),
                         Row("PFS 2024:3", 1, "https://lagen.nu/pfs/2024:3",
                             detail="Om förvaltningskostnadsfaktor")])])
    ]).to_artifact()
    assert art["measures"][0]["rows"][0]["steps"][1]["label"] == "PFS 2024:3"
    html = render.render_stats(art)
    assert '<summary>Visa kedjan</summary>' in html
    assert '<a href="/pfs/2024:3">PFS 2024:3</a>' in html
    assert "Om förvaltningskostnadsfaktor" in html


def test_a_row_without_steps_gets_no_foldout():
    """Every other toplist keeps the plain row -- an empty <details> saying
    "Visa kedjan" on 30 measures that have no chain is noise."""
    html = charts.toplist_html(Measure(
        30, "D", "Mest hänvisade dokument", "toplist", unit="hänvisningar",
        rows=[Row("Brottsbalken", 4155, "https://lagen.nu/1962:700")]))
    assert "viz-steps" not in html
    assert "<details" not in html


def test_the_heat_table_flips_its_ink_on_the_dark_steps():
    html = charts.matrix_html(Measure(
        1, "D", "Hänvisningar", "matrix",
        cells=[Cell("SFS", "SFS", 4155603), Cell("SFS", "EU", 578)]))
    # the ramp is logarithmic, so the small cell is still visible rather than
    # rounded to the lightest step's neighbour
    assert charts.HEAT[0] in html and charts.HEAT[-1] in html
    assert html.count("on-dark") == 1


def test_a_flow_group_splits_eurlex_and_merges_the_folkratt_sources():
    # the three EU document families cite each other and behave differently, so
    # they are three nodes; the international-law sources are two kinds of thing
    # between them, treaty text and case law, so they are two
    assert facets.flow_group("eurlex", "treaty") == "EU-fördrag"
    assert facets.flow_group("eurlex", "judgment") == "EU-domar"
    assert facets.flow_group("eurlex", "opinion") == "EU-domar"
    assert facets.flow_group("eurlex", "regulation") == "EU-rättsakter"
    assert facets.flow_group("eurlex", "act") == "EU-rättsakter"
    assert {facets.flow_group(s, "treaty") for s in ("coe", "icrc", "untc")} \
        == {"Konventioner"}
    assert {facets.flow_group(s, "judgment") for s in ("hudoc", "icj", "icc")} \
        == {"Folkrättslig praxis"}
    # an order (CO/TO/FO) is the Court's too -- a hand-written judgment/opinion
    # pair drew it as legislation, which is why the set comes from lib
    assert facets.flow_group("eurlex", "order") == "EU-domar"
    # a source nobody has placed is a hard error: pooling it into an "övrigt"
    # bucket would make the diagram lie about what cites what
    with pytest.raises(AssertionError, match="nysource"):
        facets.flow_group("nysource", "kind")


def test_the_flow_query_groups_across_the_join_and_drops_a_dangling_target():
    # the flow is measured over the catalog, so the grouping has to survive the
    # join: two eurlex kinds on one side become two different nodes, three
    # folkrätt sources become one. A reference whose target the corpus does not
    # hold has no cited group and cannot be drawn -- it is counted by 29's own
    # second query instead, which is why that number is not `links - flows`.
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE documents (uri TEXT, source TEXT, kind TEXT)")
    con.execute("CREATE TABLE links (from_uri TEXT, to_root TEXT)")
    con.executemany("INSERT INTO documents VALUES (?, ?, ?)", [
        ("prop", "forarbete", "prop"), ("sfs", "sfs", "lag"),
        ("reg", "eurlex", "regulation"), ("dom", "eurlex", "judgment"),
        ("echr", "hudoc", "judgment"), ("coe", "coe", "treaty"),
        ("icrc", "icrc", "protocol"),
    ])
    con.executemany("INSERT INTO links VALUES (?, ?)", [
        ("prop", "sfs"), ("prop", "sfs"), ("prop", "reg"),
        ("dom", "reg"), ("echr", "coe"), ("echr", "icrc"),
        ("prop", "borta"),                      # target not in the catalog
    ])
    # largest first, which is the order the drawing and the table both read
    assert [(c.row, c.col, c.value) for c in compute._flows(con)] == [
        ("Förarbeten", "Författningar", 2),
        ("Folkrättslig praxis", "Konventioner", 2),   # coe + icrc are one node
        ("EU-domar", "EU-rättsakter", 1),       # not folded into EU-rättsakter
        ("Förarbeten", "EU-rättsakter", 1),
    ]


def test_a_node_bar_is_the_sum_of_the_ribbons_actually_drawn():
    # 1 000 000 + 200 000 drawn, and a 40-flow that falls under the threshold.
    # The bar has to be the ribbons as drawn, floor included, or the picture
    # contradicts itself where the floor lifts a hairline.
    m = Measure(29, "D", "Flöde", "sankey", unit="hänvisningar", value=1200040,
                display="1 200 040 hänvisningar mellan 300 dokument",
                cells=[Cell("Förarbeten", "Författningar", 1000000),
                       Cell("Förarbeten", "Förarbeten", 200000),
                       Cell("Begrepp", "Begrepp", 40)])
    svg = charts.sankey_svg(m)
    heights = [float(h) for h in re.findall(
        r'class="viz-node"[^>]*height="([\d.]+)"', svg)]
    ribbons = svg.count('class="viz-flow"')
    assert ribbons == 2                      # the 40-flow is under SK_SHARE
    assert "Begrepp" not in svg              # and so is its group, both sides
    # left: one bar over both ribbons; right: one per cited group
    assert heights[0] == pytest.approx(charts.SK_STACK, abs=0.01)
    assert sum(heights[1:]) == pytest.approx(charts.SK_STACK, abs=0.01)


def test_a_flow_too_thin_to_draw_is_still_counted_and_still_listed():
    m = Measure(29, "D", "Flöde", "sankey", unit="hänvisningar", value=1000040,
                display="1 000 040 hänvisningar mellan 300 dokument",
                cells=[Cell("Förarbeten", "Författningar", 1000000),
                       Cell("Förarbeten", "Begrepp", 40)])
    html = charts.figure(m)
    # the citing group's number counts the undrawn flow (1 000 000 + 40), so a
    # node label never states less traffic than the group has...
    assert re.findall(r'class="viz-nodeval"[^>]*>([^<]+)<', html)[0] \
        == charts._fmt(1000040)
    # ...and the table under the figure carries the flow the drawing dropped
    assert "Förarbeten → Begrepp" in html and "Visa som tabell" in html


def test_the_flow_diagram_opens_on_the_number_it_decomposes():
    html = charts.figure(Measure(
        29, "D", "Flöde", "sankey", unit="hänvisningar", value=1200000,
        display="1 200 000 hänvisningar mellan 300 dokument",
        cells=[Cell("Förarbeten", "Författningar", 1000000),
               Cell("Rättsfall", "Författningar", 200000)]))
    assert html.index("viz-hero") < html.index("<svg") < html.index("viz-data")
    # both sides in one node order, so a group can be followed across
    assert html.count("viz-nodelabel") == 3       # 2 citing + 1 cited


def test_a_plotted_measure_carries_its_table_view():
    html = charts.figure(Measure(
        1, "B", "Per år", "series", unit="författningar", xlabel="år",
        points=[Point("2024", 100), Point("2025", 120)]))
    assert "<svg" in html and "Visa som tabell" in html
    assert "<th scope=\"col\">år</th>" in html


def test_a_profile_shows_its_record_holders_instead_of_a_table_view():
    # 100 rows of "plats 1 743 -> 214 tecken" name no thing a reader can look
    # up; the rank profile's named extremes are what its rows are for
    html = charts.figure(Measure(
        1, "A", "Lagars längd", "profile", unit="tecken", xlabel="plats",
        points=[Point("1", 900), Point("2", 20)],
        rows=[Row("Socialförsäkringsbalk", 900, group="Längst")]))
    assert "<svg" in html and "Visa som tabell" not in html
    assert "Socialförsäkringsbalk" in html


def test_the_log_rank_profile_spends_its_columns_on_the_head():
    points = compute._rank_profile(list(range(5000, 0, -1)), k=40, log=True)
    ranks = [int(p.x.replace(" ", " ").replace(" ", "")) for p in points]
    # the head is sampled rank by rank (there is no rank 1.4 to sample), the
    # tail in steps of hundreds -- and both ends are still real members
    assert ranks[:4] == [1, 2, 3, 4]
    assert ranks[-1] == 5000 and points[0].y == 5000
    assert ranks[len(ranks) // 2] < 200          # half the columns cover 4 % of the corpus
    # the even sampling it replaces reaches rank 100 only in its second column
    assert compute._rank_profile(list(range(5000, 0, -1)), k=40)[1].x != "2"


def test_a_scalar_with_several_numbers_answers_as_a_row_of_tiles():
    html = charts.figure(Measure(
        9, "A", "I siffror", "scalar", unit="tecken", value=520,
        display="520 tecken · 86 ord",
        tiles=[Tile("520", "tecken"), Tile("86", "ord")]))
    assert html.count("viz-tile-val") == 2 and "viz-hero" not in html
    assert ">tecken<" in html and ">86<" in html


def test_two_definitions_are_the_same_when_their_text_is():
    # NIS2 art. 6.9 and CER-direktivet art. 2.6 both define "risk" and differ by
    # three words, so they are two definitions; the same wording with a full
    # stop added is still one.
    nis2 = ("risk för förlust eller störning orsakad av en incident, som ska "
            "uttryckas som en kombination av omfattningen av förlusten eller "
            "störningen och sannolikheten för att en sådan incident inträffar.")
    cer = nis2.replace("en sådan incident inträffar", "incidenten inträffar")
    assert compute._definition_key(nis2) != compute._definition_key(cer)
    assert compute._definition_key(nis2) == compute._definition_key(
        "Risk för förlust eller  störning orsakad av en incident, som ska "
        "uttryckas som en kombination av omfattningen av förlusten eller "
        "störningen och sannolikheten för att en sådan incident inträffar")


def test_an_act_states_its_definitions_somewhere_citable():
    # CRR states 188 definitions in 8 articles, so the row names the first one
    # in reading order and counts the rest -- and links to that article, not to
    # the act's first page. An act that gathers them in one just says which.
    def defs(*places):
        return [{"place": a, "place_label": t} for a, t in places]

    assert compute._definition_place(
        defs(("3", "artikel 3"), ("3", "artikel 3"))) == ("3", "artikel 3")
    # reading order, not sorted: article 4 comes before 142, and "142" < "4"
    assert compute._definition_place(
        defs(("4", "artikel 4"), ("142", "artikel 142"), ("192", "artikel 192"))) \
        == ("4", "artikel 4 och 2 andra")
    # the citation is not the anchor: 82/714/EEG's "Artikel 1.01" anchors as
    # "1-001", because the dot separates anchor segments
    assert compute._definition_place(defs(("1-001", "artikel 1.01"))) \
        == ("1-001", "artikel 1.01")
    assert compute._definition_place(defs((None, ""))) == (None, None)


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
    # stats.html is 1:1 with the page: it names every measure explicitly and
    # owns each one's title/lede/note; the artifact supplies only the numbers,
    # by id. A subset artifact renders only the measures it carries, and a
    # group with none measured disappears whole -- heading and nav entry alike.
    html = render.render_stats({
        "generated": "2026-07-28",
        "measures": [{"id": 21, "group": "C", "title": "Artefaktens egen titel",
                      "kind": "scalar", "value": 42}]})
    assert 'id="gC"' in html and 'id="gA"' not in html    # A measured nothing
    assert 'href="#gC"' in html and 'href="#gA"' not in html   # nav follows suit
    assert 'id="m21"' in html and 'id="m22"' not in html  # only measured ids
    # the template's prose renders, not the artifact's presentation stamps
    assert "Äldsta lagar som fortfarande gäller" in html
    assert "Artefaktens egen titel" not in html
    assert "</p>'" not in html


def test_the_eu_title_measure_renders_beside_its_swedish_twin():
    # measure 6 is measure 5 asked of the EU acts, placed right after it.
    # Both are rank profiles -- each column a real title's own length at its
    # rank -- whose named extremes render as a plain list: the columns did
    # the comparing, so the rows carry no bars. Measure 6's lede is computed
    # -- the artifact's numbers, never frozen prose.
    html = render.render_stats({
        "generated": "2026-08-14",
        "measures": [
            {"id": 5, "group": "A", "title": "", "kind": "profile",
             "unit": "tecken", "xlabel": "plats i längdordning",
             "points": [{"x": "1", "y": 385}, {"x": "2 663", "y": 63},
                        {"x": "5 326", "y": 5}],
             "rows": [{"label": "Kungörelse om tillämpning av …", "value": 385,
                       "group": "Längst"},
                      {"label": "Ellag", "value": 5, "group": "Kortast"}]},
            {"id": 6, "group": "A", "title": "", "kind": "profile",
             "unit": "tecken", "xlabel": "plats i längdordning",
             "lede": "Medianen bland 28 227 akter är 231 tecken.",
             "points": [{"x": "1", "y": 1361}, {"x": "14 114", "y": 231},
                        {"x": "28 227", "y": 56}],
             "rows": [{"label": "Kommissionens genomförandeförordning …",
                       "value": 1361, "group": "Längst",
                       "uri": "https://lagen.nu/ext/celex/32020R0421"},
                      {"label": "Rådets direktiv 75/442/EEG … om avfall",
                       "value": 56, "group": "Kortast",
                       "uri": "https://lagen.nu/ext/celex/31975L0442"}]}]})
    assert html.index('id="m5"') < html.index('id="m6"')
    assert "Rubriklängd i svenska författningar" in html
    assert "Rubriklängd i EU-rätten" in html
    assert "Medianen bland 28 227 akter är 231 tecken." in html
    # the extremes list is plain -- titles and values, no second bar chart
    assert "om avfall" in html and "Ellag" in html
    assert 'class="viz-bar"' not in html
    # both profiles drew their columns, and the extremes' unit column says tecken
    assert html.count('class="viz-col"') == 6
    assert ">tecken</th>" in html


def test_the_snapshot_path_is_keyed_on_the_report_date():
    # one file per day: a second compute the same day settles on that day's
    # figure rather than accumulating a run-per-file series
    p = layout.stats_snapshot("2026-07-28")
    assert p.name == "statistik-2026-07-28.json"
    assert p.parent.name == "archive"
    # it sits beside the live artifact, not on top of it
    assert p.parent.parent == layout.artifact("stats", "statistik").parent
    assert p != layout.artifact("stats", "statistik")


def test_a_future_repeal_is_still_in_force():
    # Ellag (1997:857) is repealed as of 2027-01-01 and is law until then. A bare
    # `expired IS NULL` read it -- and 16 other live statutes, Konsumentkreditlagen
    # among them -- as already gone, dropping them out of every gällande-rätt
    # measure including "de kortaste lagarna". Same rule the search layer applies
    # at query time (search.REPEALED_IN_FORCE).
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE documents (uri TEXT, expired TEXT)")
    con.executemany("INSERT INTO documents VALUES (?, ?)", [
        ("never", None),                 # never repealed
        ("future", "2099-01-01"),        # repeal not yet in force -> still law
        ("past", "2001-01-01"),          # repeal taken effect -> gone
    ])
    live = {u for (u,) in con.execute(
        "SELECT uri FROM documents WHERE " + compute.in_force())}
    dead = {u for (u,) in con.execute(
        "SELECT uri FROM documents WHERE " + compute.repealed())}
    assert live == {"never", "future"}
    assert dead == {"past"}
    # the two are complements: every row lands in exactly one
    assert not live & dead
    assert len(live | dead) == 3


def test_a_chain_of_inserted_paragrafer_is_measured_by_its_reach(tmp_path):
    # 52 a §, 52 b § … 52 u §: a new rule between 52 § and 53 § never renumbers
    # what follows, so the lettering is how much law one place in the numbering
    # has taken on. The measure is how far it reaches, not how many survive --
    # a repealed paragraf is removed from the consolidated text, and the
    # amendment register is the only record that it was ever there.
    art = law([paragraf("8", [{"type": "stycke", "text": "Avgift tas ut."}],
                        pid="K7P8"),
               paragraf("8 a", [{"type": "stycke", "text": "Avgiften betalas."}],
                        pid="K7P8a"),
               # 8 b is gone: SFS 2022:1302 repealed it
               paragraf("8 c", [{"type": "stycke", "text": "Avgiften återbetalas."}],
                        pid="K7P8c")],
              amendments=[{"properties": {
                  "dcterms:identifier": "SFS 2022:1302",
                  "rpubl:upphaver": ["https://lagen.nu/2001:1#K7P8b"]}}])
    chain = scan.scan_sfs(write_artifact(tmp_path, "t.json", art))["chain"]
    assert chain == {"anchor": "K7P8", "bas": "K7P8", "nummer": "8", "span": 3,
                     "sista": "c", "upphavda": 1, "oforklarat": 0}


def test_a_gap_the_register_does_not_explain_is_counted_not_absorbed(tmp_path):
    # 4 kap. 28 d § of Lag (2000:562) stands in the source text without its §
    # sign, so it never reads as a paragraf. The register introduces it and
    # repeals nothing, so the gap is no upphävd paragraf -- and it is counted
    # rather than absorbed, so the row cannot read as a complete account.
    art = law([paragraf("28", [{"type": "stycke", "text": "Ansökan görs."}],
                        pid="K4P28"),
               paragraf("28 c", [{"type": "stycke", "text": "Hemlig dataavläsning."}],
                        pid="K4P28c"),
               # 28 d § is the one the source text loses; 28 e § stands after it
               paragraf("28 e", [{"type": "stycke", "text": "Tekniskt bistånd."}],
                        pid="K4P28e")],
              amendments=[{"properties": {
                  "dcterms:identifier": "SFS 2020:65",
                  "rpubl:upphaver": ["https://lagen.nu/2001:1#K4P28a",
                                     "https://lagen.nu/2001:1#K4P28b"]}}])
    chain = scan.scan_sfs(write_artifact(tmp_path, "t.json", art))["chain"]
    assert (chain["span"], chain["upphavda"], chain["oforklarat"]) == (5, 2, 1)
    assert compute._chain_detail(chain) == (
        "4 kap. 28 § – 28 e §, varav 2 numera upphävda och 1 saknas i texten")


def test_a_chain_is_named_the_way_a_reader_would_write_it():
    # "7 kap. 8 § – 8 v §": the chapter is said once
    chaptered = {"bas": "K7P8", "nummer": "8", "span": 22,
                 "sista": "v", "upphavda": 1, "oforklarat": 0}
    assert compute._chain_detail(chaptered) \
        == "7 kap. 8 § – 8 v §, varav 1 numera upphävd"
    # an unchaptered act, and a chain nothing has been taken out of
    intact = {"bas": "P52", "nummer": "52", "span": 21,
              "sista": "u", "upphavda": 0, "oforklarat": 0}
    assert compute._chain_detail(intact) == "52 § – 52 u §"
    # plural
    assert compute._chain_detail({**chaptered, "upphavda": 3}).endswith(
        "varav 3 numera upphävda")


def test_the_chain_key_is_the_anchors_own_place(tmp_path):
    # Upphovsrättslagen has chapter nodes but anchors its paragrafer "P52" …
    # "P52u" with no K segment, because the chapters were inserted around a run
    # that was already numbered straight through. The anchor is where that is
    # written down, so the 52-series is one chain of 3 and not three of 1.
    art = law([paragraf("52", [{"type": "stycke", "text": "Avtal om överlåtelse."}],
                        pid="P52"),
               paragraf("52 a", [{"type": "stycke", "text": "Avtalslicens."}],
                        pid="P52a"),
               paragraf("52 b", [{"type": "stycke", "text": "Skälig ersättning."}],
                        pid="P52b")])
    chain = scan.scan_sfs(write_artifact(tmp_path, "t.json", art))["chain"]
    assert (chain["anchor"], chain["span"]) == ("P52", 2)
    assert compute._chain_detail(chain) == "52 § – 52 b §"


def test_a_restarting_act_keeps_its_chapters_chains_apart(tmp_path):
    # most chaptered acts restart the numbering in each chapter, and their
    # anchors say so: 3 kap. 8 a § is "K3P8a" and 7 kap. 8 a § is "K7P8a", two
    # different paragrafer that must never merge into one chain
    art = law([paragraf("8", [{"type": "stycke", "text": "Ansökan."}], pid="K3P8"),
               paragraf("8 a", [{"type": "stycke", "text": "Avgift."}], pid="K3P8a"),
               paragraf("8", [{"type": "stycke", "text": "Tillsyn."}], pid="K7P8"),
               paragraf("8 a", [{"type": "stycke", "text": "Föreläggande."}],
                        pid="K7P8a"),
               paragraf("8 b", [{"type": "stycke", "text": "Vite."}], pid="K7P8b")])
    chain = scan.scan_sfs(write_artifact(tmp_path, "t.json", art))["chain"]
    assert (chain["anchor"], chain["span"]) == ("K7P8", 2)


def test_a_chain_under_an_avdelning_is_counted(tmp_path):
    # Taxeringslagen (1956:623) anchors its paragrafer under an avdelning
    # ("A2P116t"), and its chain of 20 is the longest any repealed act reached.
    # An anchor grammar that knew only kapitel dropped 413 paragrafer in 21 acts.
    art = law([paragraf("116", [{"type": "stycke", "text": "Besvär."}],
                        pid="A2P116"),
               paragraf("116 a", [{"type": "stycke", "text": "Prövningstillstånd."}],
                        pid="A2P116a")])
    chain = scan.scan_sfs(write_artifact(tmp_path, "t.json", art))["chain"]
    assert (chain["anchor"], chain["span"]) == ("A2P116", 1)
    # the avdelning is no part of how the paragraf is cited
    assert compute._chain_detail(chain) == "116 § – 116 a §"


def test_a_chain_whose_base_paragraf_is_gone_still_names_its_number(tmp_path):
    # 32 § of Förordning (2004:1205) was repealed away, so the chain's first
    # surviving member is 32 b §. The row still reads "32 § – 32 w §" -- the
    # number is where the chain hangs -- while the link goes to a paragraf that
    # is actually on the page.
    art = law([paragraf("32 b", [{"type": "stycke", "text": "Tilldelning."}],
                        pid="P32b"),
               paragraf("32 c", [{"type": "stycke", "text": "Överlåtelse."}],
                        pid="P32c")],
              amendments=[{"properties": {
                  "dcterms:identifier": "SFS 2009:1305",
                  "rpubl:upphaver": ["https://lagen.nu/2001:1#P32",
                                     "https://lagen.nu/2001:1#P32a"]}}])
    chain = scan.scan_sfs(write_artifact(tmp_path, "t.json", art))["chain"]
    assert (chain["anchor"], chain["bas"]) == ("P32b", "P32")
    assert compute._chain_detail(chain) == "32 § – 32 c §, varav 1 numera upphävd"


def test_a_repealed_acts_chain_does_not_count_its_repealed_paragrafer():
    # "varav 1 numera upphävd" tells a reader nothing about an act that is
    # itself repealed -- every paragraf went with it. The span alone is what
    # says how far the chain reached.
    chain = {"bas": "P32", "nummer": "32", "span": 23,
             "sista": "w", "upphavda": 1, "oforklarat": 0}
    assert compute._chain_span(chain) == "32 § – 32 w §"
    assert compute._chain_detail(chain) == "32 § – 32 w §, varav 1 numera upphävd"


def test_a_lede_links_the_document_it_names():
    # the repealed record is named in the lede but not in the list, so the lede
    # is the only place a reader can reach it from. The lede stays plain text in
    # the artifact; the anchor is put in at render time.
    linked = render._linked(
        "rekordet Förordning (2004:1205) om handel med utsläppsrätter, 32 §.",
        {"Förordning (2004:1205) om handel med utsläppsrätter":
         "https://lagen.nu/2004:1205#P32b"})
    assert linked == ('rekordet <a href="https://lagen.nu/2004:1205#P32b">'
                      "Förordning (2004:1205) om handel med utsläppsrätter</a>,"
                      " 32 §.")
    # the text and the uri are both escaped, so a title carrying markup cannot
    # inject any
    assert render._linked("a <b>t</b> c", {"<b>t</b>": "/x?a=1&b=2"}) \
        == 'a <a href="/x?a=1&amp;b=2">&lt;b&gt;t&lt;/b&gt;</a> c'


def _legislation_catalog(tmp_path):
    """Four acts in the legislation groups: a base act (a), a föreskrift chain
    b -> c, and an amending act (x) whose rpubl:andrar keeps it out of the
    population. a cites x, x cites b -- so the longest chain runs a -> x -> b
    -> c only while the amending act is counted."""
    path = tmp_path / "catalog.sqlite"
    con = catalog.connect(path)
    docs = {"https://lagen.nu/a": ("sfs", "lag"),
            "https://lagen.nu/xfs/1": ("foreskrift", "xfs"),
            "https://lagen.nu/xfs/2": ("foreskrift", "xfs"),
            "https://lagen.nu/xfs/3": ("foreskrift", "xfs")}
    for uri, (source, kind) in docs.items():
        con.execute("INSERT INTO documents (uri, source, kind, label, title, "
                    "short_id, path) VALUES (?, ?, ?, 'L', ?, ?, '')",
                    (uri, source, kind, "Om %s" % uri.rsplit("/", 1)[-1],
                     uri.replace("https://lagen.nu/", "").upper()))
    refs = [("https://lagen.nu/a", "https://lagen.nu/xfs/1"),
            ("https://lagen.nu/xfs/1", "https://lagen.nu/xfs/2"),
            ("https://lagen.nu/xfs/2", "https://lagen.nu/xfs/3")]
    for f, t in refs:
        con.execute("INSERT INTO links (from_uri, predicate, to_uri, to_root) "
                    "VALUES (?, 'dcterms:references', ?, ?)", (f, t, t))
    # xfs/1 amends xfs/2: it maintains another föreskrift rather than stating
    # a rule of its own
    con.execute("INSERT INTO links (from_uri, predicate, to_uri, to_root) "
                "VALUES ('https://lagen.nu/xfs/1', 'rpubl:andrar', "
                "'https://lagen.nu/xfs/2', 'https://lagen.nu/xfs/2')")
    con.commit()
    con.close()
    return path


def test_base_acts_exclude_what_only_maintains_another_act(tmp_path):
    """The filter measure 56 rests on. Leaving an amending act in does not
    lengthen the answer a little -- it is the whole ladder, and in the real
    corpus the difference is 71 references against 23."""
    path = _legislation_catalog(tmp_path)
    graph = pathgraph.load(path)
    con = catalog.connect_ro(str(path))
    base = compute._base_acts(con, graph)
    assert "https://lagen.nu/xfs/1" not in base          # it amends xfs/2
    assert set(base) == {"https://lagen.nu/a", "https://lagen.nu/xfs/2",
                         "https://lagen.nu/xfs/3"}
    # the chain through the amending act goes with it: a reaches nothing
    assert pathgraph.longest_shortest(graph, base) == [
        ["https://lagen.nu/xfs/2", "https://lagen.nu/xfs/3"]]
    # with it, the same graph answers a three-step chain
    assert pathgraph.longest_shortest(graph, graph.uris, k=1) == [
        ["https://lagen.nu/a", "https://lagen.nu/xfs/1",
         "https://lagen.nu/xfs/2", "https://lagen.nu/xfs/3"]]
    con.close()


def test_base_acts_refuses_a_catalog_that_cannot_filter_the_eu_acts(tmp_path):
    """A corpus whose eurlex acts have never been told which of them only
    amend another is not the population measure 56 claims: the sweep answers
    with the al-Qaida sanctions ladder at 71 steps under a lede saying
    amending acts do not count, and a wrong published statistic reads exactly
    like a right one. The relations reach the catalog through
    `lagen eurlex refresh-metadata`, then parse and relate."""
    path = tmp_path / "eu.sqlite"
    con = catalog.connect(path)
    acts = ["https://lagen.nu/ext/celex/32002R0881",
            "https://lagen.nu/ext/celex/32008R0803"]
    for uri in acts:
        con.execute("INSERT INTO documents (uri, source, kind, label, title, "
                    "short_id, path) VALUES (?, 'eurlex', 'regulation', 'L', "
                    "'T', ?, '')", (uri, uri.rsplit("/", 1)[-1]))
    con.execute("INSERT INTO links (from_uri, predicate, to_uri, to_root) "
                "VALUES (?, 'dcterms:references', ?, ?)",
                (acts[1], acts[0], acts[0]))
    con.commit()
    con.close()
    graph = pathgraph.load(path)
    ro = catalog.connect_ro(str(path))
    with pytest.raises(AssertionError, match="refresh-metadata"):
        compute._base_acts(ro, graph)

    # one eurlex act publishing the relation is what says the sweep has run
    rw = catalog.connect(path)
    rw.execute("INSERT INTO links (from_uri, predicate, to_uri, to_root) "
               "VALUES (?, 'rpubl:andrar', ?, ?)",
               (acts[1], acts[0], acts[0]))
    rw.commit()
    rw.close()
    assert compute._base_acts(catalog.connect_ro(str(path)), graph) == [acts[0]]
    ro.close()


def test_group_d_assembles_measure_56_with_its_chains(tmp_path):
    """The measure end to end: population, sweep, the `WHERE uri IN (…)` label
    lookup, the rows and their folded-out steps. Every piece has its own test;
    nothing drove the assembly, so the query built from
    `",".join("?" * sum(len(c) for c in chains))` never ran in the suite."""
    path = _legislation_catalog(tmp_path)
    graph = pathgraph.load(path)
    con = catalog.connect_ro(str(path))
    measure = next(m for m in compute._group_d(con, {"graph": graph})
                   if m.id == 56)
    con.close()
    assert measure.kind == "toplist" and measure.unit == "steg"
    # one chain survives the base-act filter: xfs/2 -> xfs/3
    assert [(r.label, r.value, r.detail) for r in measure.rows] == [
        ("XFS/2", 1, "→ XFS/3")]
    # the foldout names every act in the chain, labelled off short_id and
    # carrying the title -- what tells a run of reissues from a run of substance
    assert [(t.label, t.uri, t.detail) for t in measure.rows[0].steps] == [
        ("XFS/2", "https://lagen.nu/xfs/2", "Om 2"),
        ("XFS/3", "https://lagen.nu/xfs/3", "Om 3")]
    # the lede counts the population it actually walked
    assert "3 grundförfattningar" in measure.lede


def test_base_acts_only_covers_the_legislation_groups(tmp_path):
    """A verdict and a förarbete are not legislation. Both cite the act here,
    so both are nodes in the graph -- the population still holds only the act,
    or a chain between two statutes could route through a judgment."""
    path = tmp_path / "mixed.sqlite"
    con = catalog.connect(path)
    for uri, source, kind in (("https://lagen.nu/a", "sfs", "lag"),
                              ("https://lagen.nu/dom/x/1", "dv", "case"),
                              ("https://lagen.nu/sou/1", "forarbete", "sou")):
        con.execute("INSERT INTO documents (uri, source, kind, label, title, "
                    "path) VALUES (?, ?, ?, 'L', 'T', '')", (uri, source, kind))
    for citing in ("https://lagen.nu/dom/x/1", "https://lagen.nu/sou/1"):
        con.execute("INSERT INTO links (from_uri, predicate, to_uri, to_root) "
                    "VALUES (?, 'dcterms:references', 'https://lagen.nu/a', "
                    "'https://lagen.nu/a')", (citing,))
    con.commit()
    con.close()
    graph = pathgraph.load(path)
    assert len(graph.uris) == 3                 # all three are in the graph
    ro = catalog.connect_ro(str(path))
    assert compute._base_acts(ro, graph) == ["https://lagen.nu/a"]
    ro.close()
