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

from accommodanda.lib import facets, layout
from accommodanda.stats import charts, compute, render, scan
from accommodanda.stats.model import Cell, Measure, Point, Report, Row, Tile


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
