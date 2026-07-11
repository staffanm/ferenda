"""Tests for the SFS old->new paragraf correspondence pass (the non-LLM core:
inventory, old-law detection, FK slicing, and edge validation)."""

import json

from accommodanda.forarbete import kommentar
from accommodanda.sfs import correspond as C

CELEX = "https://lagen.nu/"


def _sfs(uri, structure, amendments=None, title="Lag (2000:1)"):
    return {"uri": uri, "structure": structure,
            "amendments": amendments or [],
            "metadata": {"properties": {"dcterms:title": title}}}


def test_paragraf_index_chaptered_and_flat():
    chaptered = _sfs(CELEX + "2018:585", [
        {"type": "kapitel", "ordinal": "3", "children": [
            {"type": "paragraf", "id": "K3P17", "ordinal": "17"},
            {"type": "paragraf", "id": "K3P18", "ordinal": "18"}]}])
    assert C.paragraf_index(chaptered) == [("K3P17", "3 kap. 17 §"),
                                           ("K3P18", "3 kap. 18 §")]
    flat = _sfs(CELEX + "1996:627", [
        {"type": "paragraf", "id": "P32a", "ordinal": "32 a"}])
    assert C.paragraf_index(flat) == [("P32a", "32 a §")]


def test_paragraf_index_excludes_id_suppressed():
    # an id-suppressed paragraf (temporal/dedup, "id": None) has no anchor;
    # it must not enter the LLM inventory as a mappable target
    art = _sfs(CELEX + "2018:585", [
        {"type": "paragraf", "id": "P1", "ordinal": "1"},
        {"type": "paragraf", "id": None, "ordinal": "2"}])
    assert C.paragraf_index(art) == [("P1", "1 §")]


def test_detect_old_law_from_repeal_clause():
    new = _sfs(CELEX + "2018:585", [], amendments=[{"content": [{"children": [
        {"type": "punkt", "text": [
            "Genom lagen upphävs ",
            {"predicate": "dcterms:references", "uri": CELEX + "1996:627",
             "text": "säkerhetsskyddslagen (1996:627)"}, "."]},
        # a same-clause reference that is NOT the repealed law and a pinpoint that
        # must be ignored (only whole-SFS repeal references count)
        {"type": "punkt", "text": [
            "Hänvisning till ",
            {"predicate": "dcterms:references", "uri": CELEX + "2018:585#P14",
             "text": "14 §"}]}]}]}])
    assert C.detect_old_law(new) == CELEX + "1996:627"


def test_detect_old_law_none_when_no_repeal():
    assert C.detect_old_law(_sfs(CELEX + "2020:9", [])) is None


def test_validate_edges_keeps_valid_drops_hallucinations():
    new_anchors, old_anchors = {"K1P1", "K3P17"}, {"P1", "P32a"}
    fk = ("I paragrafen, som delvis motsvarar 1 § 1996 års säkerhetsskyddslag, "
          "anges lagens syfte. Paragrafen har förts över från 32 a §.")
    raw = [
        # valid correspondence
        {"newParagraf": "K1P1", "oldParagraf": "P1", "relation": "motsvarar",
         "scope": "delvis", "quote": "I paragrafen, som delvis motsvarar 1 § 1996 års säkerhetsskyddslag"},
        # valid transfer, scope null
        {"newParagraf": "K3P17", "oldParagraf": "P32a", "relation": "overfort",
         "scope": None, "quote": "Paragrafen har förts över från 32 a §"},
        # hallucinated new anchor -> dropped
        {"newParagraf": "K9P9", "oldParagraf": "P1", "relation": "motsvarar",
         "scope": "helt", "quote": "I paragrafen, som delvis motsvarar 1 §"},
        # invented quote (not in FK) -> dropped
        {"newParagraf": "K1P1", "oldParagraf": "P1", "relation": "motsvarar",
         "scope": "helt", "quote": "Denna mening finns inte i kommentaren alls."},
        # bad relation vocabulary -> dropped
        {"newParagraf": "K1P1", "oldParagraf": "P1", "relation": "liknar",
         "scope": "helt", "quote": "I paragrafen, som delvis motsvarar 1 §"},
    ]
    edges, rejected = C.validate_edges(raw, new_anchors, old_anchors,
                                       CELEX + "1996:627", fk)
    assert [e["newParagraf"] for e in edges] == ["K1P1", "K3P17"]
    assert edges[0]["oldUri"] == CELEX + "1996:627#P1"
    assert edges[1]["relation"] == "overfort" and edges[1]["scope"] is None
    assert len(rejected) == 3


def test_correspond_end_to_end_monkeypatched(monkeypatch):
    # exercise the whole pipeline with a stubbed LLM (no network): the model
    # returns one good edge and one hallucinated anchor; only the good one is kept
    new = _sfs(CELEX + "2018:585", [
        {"type": "kapitel", "ordinal": "1", "children": [
            {"type": "paragraf", "id": "K1P1", "ordinal": "1"}]}],
        title="Säkerhetsskyddslag (2018:585)")
    old = _sfs(CELEX + "1996:627", [{"type": "paragraf", "id": "P1", "ordinal": "1"}])
    prop = {"uri": CELEX + "prop/2017/18:89", "identifier": "Prop. 2017/18:89",
            "structure": [
                {"type": "rubrik", "level": 1, "text": ["16 Författningskommentar"]},
                {"type": "rubrik", "level": 2,
                 "text": ["16.1 Förslaget till säkerhetsskyddslag"]},
                {"type": "stycke",
                 "text": ["Paragrafen motsvarar 1 § 1996 års säkerhetsskyddslag."]}]}

    def fake_complete(prompt):
        assert "K1P1 = 1 kap. 1 §" in prompt and "P1 = 1 §" in prompt
        return json.dumps({"correspondences": [
            {"newParagraf": "K1P1", "oldParagraf": "P1", "relation": "motsvarar",
             "scope": "helt", "quote": "Paragrafen motsvarar 1 § 1996 års säkerhetsskyddslag"},
            {"newParagraf": "K7P7", "oldParagraf": "P1", "relation": "motsvarar",
             "scope": "helt", "quote": "Paragrafen motsvarar 1 §"}]})

    monkeypatch.setattr(C.llm, "complete", fake_complete)
    # build composes the two verticals: förarbete extracts the FK text, sfs derives
    fk = kommentar.fk_section(prop, "Säkerhetsskyddslag (2018:585)")
    sidecar, stats = C.correspond(new, prop, old, fk)
    assert stats == {"raw": 2, "emitted": 1, "rejected": 1}
    assert sidecar["correspondence"]["edges"] == [{
        "newParagraf": "K1P1", "oldParagraf": "P1",
        "oldUri": CELEX + "1996:627#P1", "relation": "motsvarar",
        "scope": "helt", "quote": "Paragrafen motsvarar 1 § 1996 års säkerhetsskyddslag"}]
    assert sidecar["correspondence"]["newLaw"] == CELEX + "2018:585"
    assert sidecar["correspondence"]["oldLaw"] == CELEX + "1996:627"
    # the catalog rows join the new paragraf anchor onto the new law's uri
    assert C.corr_rows(sidecar) == [
        (CELEX + "2018:585#K1P1", CELEX + "1996:627#P1", "motsvarar", "helt",
         CELEX + "prop/2017/18:89", None)]


def test_cell_refs_vocabulary():
    # one provision, stycke qualifiers ignored, scope from a trailing "delvis"
    assert C.cell_refs("1 kap. 8 § första stycket") == [(("1", "8"), "helt")]
    assert C.cell_refs("15 kap. 9 § första stycket 2 delvis") == [
        (("15", "9"), "delvis")]
    # several provisions in one cell, per-segment scope, kap carried forward
    assert C.cell_refs("3 kap. 3 § andra stycket (delvis) och 4 § helt") == [
        (("3", "3"), "delvis"), (("3", "4"), "helt")]
    # enumerations and ranges expand; lettered ordinals normalise
    assert C.cell_refs("2 kap. 1 och 2 §§") == [(("2", "1"), "helt"),
                                                (("2", "2"), "helt")]
    assert C.cell_refs("25 kap. 1–3 §§") == [(("25", "1"), "helt"),
                                             (("25", "2"), "helt"),
                                             (("25", "3"), "helt")]
    # older prop typography sets ranges with an em-dash
    assert C.cell_refs("25 kap. 1—3 §§") == [(("25", "1"), "helt"),
                                             (("25", "2"), "helt"),
                                             (("25", "3"), "helt")]
    assert C.cell_refs("1 kap. 9 a §") == [(("1", "9a"), "helt")]
    # the tables' recurring missing-§ typo still reads as a provision
    assert C.cell_refs("14 kap. 10 femte stycket") == [(("14", "10"), "helt")]
    # explicit no-counterpart cells, incl. the remnant a jfr-cut leaves
    for cell in ("-", "–", "(ny)", "(upphävd)", "", "- (jfr 16 kap. 1 § 4)"):
        assert C.cell_refs(cell) == [], cell
    # text after jfr or another statute's SFS number is not the counterpart
    assert C.cell_refs("1 kap. 1 § andra stycket, jfr även prop. 1979/80:2 "
                       "Del A s. 72-78") == [(("1", "1"), "helt")]
    assert C.cell_refs("(18 § SFS 2009:724)") == []
    # a trailing punkt enumeration ("§ 1–3") is not a second provision
    assert C.cell_refs("6 kap. 7 § 1–3") == [(("6", "7"), "helt")]
    # PBL's style: a bare "ny" cell is a none-marker, and a leading
    # "delvis ny samt" scopes the first reference
    assert C.cell_refs("ny") == []
    assert C.cell_refs("delvis ny samt 1 kap. 3 § första stycket") == [
        (("1", "3"), "delvis")]
    # the SFB register's markers
    assert C.cell_refs("--") == [] and C.cell_refs("Utgår") == []


def test_cell_refs_law_tags():
    # a multi-law register tags each reference with a prop-local shorthand;
    # only untagged references and those owned by the caller's tag are kept
    cell = "1 kap. 1 § TL, 1 kap. 1 och 2 a §§ SBL"
    assert C.cell_refs(cell, tag="TL") == [(("1", "1"), "helt")]
    assert C.cell_refs(cell, tag="SBL") == [(("1", "1"), "helt"),
                                            (("1", "2a"), "helt")]
    assert C.cell_refs(cell) == []
    # a leading tag ("SFBP 2 kap. 4 §") owns the adjacent group
    assert C.cell_refs("SFBP 2 kap. 4 §", tag="SFBP") == [(("2", "4"), "helt")]
    assert C.cell_refs("SFBP 2 kap. 4 §") == []
    # a spelled-out law name is always another law's
    assert C.cell_refs("11 § kassaregisterlagen") == []
    assert C.cell_refs("2 § lagen om deklarationsombud") == []
    # a tag inside a trailing parenthetical owns only its own reference
    cell = "2 §, 38 § första stycket (samt 1 § lagen om vissa avtal)"
    assert C.cell_refs(cell) == [((None, "2"), "helt"),
                                 ((None, "38"), "helt")]


def test_table_correspond_orients_validates_and_dedups():
    new = _sfs(CELEX + "2009:400", [
        {"type": "kapitel", "ordinal": "1", "children": [
            {"type": "paragraf", "id": "K1P1", "ordinal": "1"},
            {"type": "paragraf", "id": "K1P2", "ordinal": "2"}]}])
    old = _sfs(CELEX + "1980:100", [
        {"type": "kapitel", "ordinal": "1", "children": [
            {"type": "paragraf", "id": "K1P1", "ordinal": "1"}]}])
    prop = {"uri": CELEX + "prop/2008/09:150"}
    tabs = [
        # new -> old direction (old law's SFS number in the right header);
        # mostly-resolvable rows, like a real table -- a table resolving far
        # worse than its sibling is skipped as another pair's (SELECT_MARGIN)
        {"bilaga": 7, "header": ("Offentlighets- och", "Sekretesslag (1980:100)"),
         "rows": [
             ("1 kap. 1 § första stycket", "1 kap. 1 § tredje stycket delvis", 513),
             ("1 kap. 2 §", "-", 513),                     # explicit none
             ("1 kap. 1 § andra stycket", "9 kap. 9 §", 513)]  # repealed -> reject
             + [("1 kap. 1 § %d st" % n, "1 kap. 1 §", 513) for n in range(6)]},
        # old -> new direction: the duplicate pair keeps the weaker scope
        {"bilaga": 8, "header": ("Sekretesslag (1980:100)", "Offentlighets- och"),
         "rows": [("1 kap. 1 § tredje stycket", "1 kap. 1 §", 525)]}]
    sidecar, stats = C.table_correspond(new, prop, old, tabs)
    assert stats == {"rows": 10, "none": 1, "rejected": 1, "skipped": 0,
                     "emitted": 1}
    assert sidecar["correspondence"]["edges"] == [{
        "newParagraf": "K1P1", "oldParagraf": "K1P1",
        "oldUri": CELEX + "1980:100#K1P1", "relation": "motsvarar",
        "scope": "delvis",
        "quote": "1 kap. 1 § första stycket — 1 kap. 1 § tredje stycket delvis"}]
    assert sidecar["correspondence"]["proposition"] == CELEX + "prop/2008/09:150"


def test_table_correspond_refuses_unoriented_table():
    new = _sfs(CELEX + "2009:400", [])
    old = _sfs(CELEX + "1980:100", [])
    tabs = [{"bilaga": 7, "header": ("Nya lagen", "Gamla lagen"), "rows": []}]
    try:
        C.table_correspond(new, {"uri": "x"}, old, tabs)
        raise AssertionError("unoriented table must be refused")
    except ValueError as e:
        assert "orient" in str(e)


def test_old_side_scoring_orients_shorthand_headers():
    # NLTS/NLAS style: the header is prop-local shorthand, no SFS number;
    # orientation falls back to which assignment resolves more rows. The new
    # law is chaptered, the old flat -- decisively asymmetric.
    new = _sfs(CELEX + "2022:155", [
        {"type": "kapitel", "ordinal": "2", "children": [
            {"type": "paragraf", "id": "K2P20", "ordinal": "20"}]}])
    old = _sfs(CELEX + "1994:1563", [
        {"type": "paragraf", "id": "P5", "ordinal": "5"}])
    tabs = [{"bilaga": 5, "header": ("Bestämmelse i NLTS", "Bestämmelse i LTS"),
             "text": "Paragrafnyckeln anger ... lagen (1994:1563) om tobaksskatt",
             "rows": [("2 kap. 20 §", "5 §", 747)]}]
    sidecar, stats = C.table_correspond(new, {"uri": "x"}, old, tabs)
    assert stats["emitted"] == 1
    assert sidecar["correspondence"]["edges"][0]["newParagraf"] == "K2P20"
    assert sidecar["correspondence"]["edges"][0]["oldParagraf"] == "P5"


def test_table_correspond_threads_kapitel_heading_rows():
    # kommunallagen style: the left column groups rows under a bare "1 kap."
    # heading and writes the cells kap-less
    new = _sfs(CELEX + "2017:725", [
        {"type": "kapitel", "ordinal": "1", "children": [
            {"type": "paragraf", "id": "K1P1", "ordinal": "1"},
            {"type": "paragraf", "id": "K1P2", "ordinal": "2"}]}])
    old = _sfs(CELEX + "1991:900", [
        {"type": "kapitel", "ordinal": "1", "children": [
            {"type": "paragraf", "id": "K1P1", "ordinal": "1"}]}])
    tabs = [{"bilaga": 7, "header": ("Kommunallagen", "Förslaget till kommunallag"),
             "text": "Jämförelsetabell mellan kommunallagen (1991:900) ...",
             "rows": [("1 kap.", "", 578),
                      ("1 § första stycket", "1 kap. 1 § första meningen", 578),
                      ("1 § andra stycket", "1 kap. 2 §", 578)]}]
    sidecar, stats = C.table_correspond(new, {"uri": "x"}, old, tabs)
    # scoring orients left=old (bare-§ cells resolve there via the heading)
    assert stats["emitted"] == 2 and stats["skipped"] == 0
    assert {(e["newParagraf"], e["oldParagraf"])
            for e in sidecar["correspondence"]["edges"]} == {
        ("K1P1", "K1P1"), ("K1P2", "K1P1")}


def test_lookup_absorbs_continuous_numbering():
    # 1967 års patentlag style: chaptered text, continuously numbered §§ --
    # the artifact keys kap-less, the table cites with a chapter prefix
    flat = {(None, "42"): "P42", (None, "43"): "P43"}
    assert C._lookup(flat, ("2", "42")) == "P42"
    assert C._lookup(flat, (None, "43")) == "P43"
    # the mirror: a kap-less cell against a chaptered map resolves only when
    # the paragraf ordinal is unambiguous
    chaptered = {("1", "5"): "K1P5", ("2", "9"): "K2P9", ("3", "9"): "K3P9"}
    assert C._lookup(chaptered, (None, "5")) == "K1P5"
    assert C._lookup(chaptered, (None, "9")) is None


def test_table_correspond_skips_unorientable_noise_table():
    new = _sfs(CELEX + "2022:155", [
        {"type": "kapitel", "ordinal": "2", "children": [
            {"type": "paragraf", "id": "K2P20", "ordinal": "20"}]}])
    old = _sfs(CELEX + "1994:1563", [
        {"type": "paragraf", "id": "P5", "ordinal": "5"}])
    noise = {"bilaga": 1, "header": ("13.3", "Förslaget till lag om ändring i"),
             "text": "", "rows": [("Rubriken närmast", "", 12)]}
    real = {"bilaga": 5, "header": ("Bestämmelse i NLTS", "Bestämmelse i LTS"),
            "text": "", "rows": [("2 kap. 20 §", "5 §", 747)]}
    sidecar, stats = C.table_correspond(new, {"uri": "x"}, old, [noise, real])
    assert stats["skipped"] == 1 and stats["emitted"] == 1


# RF's 2010:1408 register entry -- the richest real omfattning: chapter
# moves, per-paragraf moves incl. cross-chapter, upph. and nya lists
RF_OMF = ("upph. 2, 3, 8, 9, 10, 11 kap., 12 kap. 8 §; "
          "nuvarande 12, 13 kap. betecknas 13, 15 kap., "
          "nuvarande 4 kap. 4, 5, 6, 7, 8, 9, 10 §§ betecknas 4 kap. 6, 7, "
          "10, 11, 12, 13, 14 §§, "
          "nuvarande 5 kap. 1, 3, 4, 5, 6, 7 §§, betecknas 5 kap. 3, 4, 5, "
          "6, 7, 8 §§, "
          "nuvarande 6 kap. 9 § betecknas 6 kap. 2 §, "
          "nuvarande 7 kap. 8 § betecknas 6 kap. 10 §, "
          "nuvarande 13 kap. 9, 10, 11, 12, 13 §§ betecknas 15 kap. 15, 9, "
          "10, 11, 12 §§; "
          "ändr. 1 kap. 2, 4, 5, 7, 9 §§; "
          "nya 1 kap. 10 §, 4 kap. 8, 9 §§, 13 kap. 8, 9 §§, "
          "15 kap. 13, 14, 16 §§, rubr. närmast före 4 kap. 8, 9 §§, "
          "nya kap. 2, 3, 8, 9, 10, 11, 12, 14")


def test_parse_betecknas_rf():
    para, kap = C.parse_betecknas(RF_OMF)
    assert kap == [("12", "13"), ("13", "15")]
    assert ("4", "4", "4", "6") in para          # 4 kap. 4 § -> 4 kap. 6 §
    assert ("6", "9", "6", "2") in para          # backwards move
    assert ("7", "8", "6", "10") in para         # cross-chapter move
    assert ("13", "9", "15", "15") in para       # inside a moved chapter
    # the stray comma before "betecknas" (5 kap.) must not break pairing
    assert ("5", "1", "5", "3") in para
    # dotless kap + ranges (RF's 1976:871 style)
    para2, _ = C.parse_betecknas("nuvarande 1 kap 2-8 §§ betecknas 1 kap 3-9 §§")
    assert para2[0] == ("1", "2", "1", "3") and para2[-1] == ("1", "8", "1", "9")


def test_listed_items_rf():
    assert C._listed_items(RF_OMF, "upph.") == {
        ("2", None), ("3", None), ("8", None), ("9", None), ("10", None),
        ("11", None), ("12", "8")}
    nya = C._listed_items(RF_OMF, "nya")
    assert ("15", "13") in nya and ("15", "16") in nya and ("1", "10") in nya
    # whole new chapters after the rubr. run must survive the rubr. cut
    assert ("2", None) in nya and ("14", None) in nya
    # rubrik items themselves carry no anchors and must not leak in
    assert ("4", "8") in nya           # from the "nya … 4 kap. 8, 9 §§" list


def test_renumbering_payload_expands_chapter_moves():
    art = {"uri": CELEX + "1974:152",
           "structure": [
               {"type": "kapitel", "ordinal": "15", "children": [
                   {"type": "paragraf", "id": "K15P%d" % n, "ordinal": str(n)}
                   for n in range(1, 18)]}],
           "amendments": [
               {"properties": {
                   "dcterms:identifier": "SFS 2010:1408",
                   "rpubl:ikrafttradandedatum": "2011-01-01",
                   "rpubl:andrar": RF_OMF}},
               # a LATER amendment adds 15 kap. 17 § (and renumbers something
               # else, so it enters the renumbering parse): today's inventory
               # holds it, but no edge may claim old 13 kap. contained it
               {"properties": {
                   "dcterms:identifier": "SFS 2018:1903",
                   "rpubl:ikrafttradandedatum": "2019-01-01",
                   "rpubl:andrar": "nuvarande 15 kap. 15 § betecknas "
                                   "15 kap. 15 a §; nya 15 kap. 17 §"}}],
           "metadata": {"properties": {}}}
    payload, stats = C.renumbering_payload(art)
    edges = {(e["oldParagraf"], e["newParagraf"]): e
             for e in payload["correspondence"]["edges"]}
    # explicit paragraf move, with the amendment's date
    e = edges[("K4P4", "K4P6")]
    assert e["relation"] == "betecknas" and e["ikrafttrader"] == "2011-01-01"
    assert e["oldUri"] == CELEX + "1974:152#K4P4"
    # chapter move expands per current paragraf of the new chapter, explicit
    # moves and the amendment's own "nya" provisions excluded
    assert ("K13P1", "K15P1") in edges and ("K13P8", "K15P8") in edges
    assert ("K13P9", "K15P15") in edges          # explicit override
    assert ("K13P13", "K15P13") not in edges     # 15:13 is nytt
    assert ("K13P16", "K15P16") not in edges     # 15:16 is nytt
    # 15:17 entered via the LATER amendment's nya list: in today's inventory,
    # but the 2011 chapter move must not mint a backdated edge for it
    assert ("K13P17", "K15P17") not in edges
    # ... while the later amendment's own explicit move is a real edge
    assert edges[("K15P15", "K15P15a")]["ikrafttrader"] == "2019-01-01"
    assert payload["correspondence"]["newLaw"] == \
        payload["correspondence"]["oldLaw"] == CELEX + "1974:152"


def test_relevant_tables_filters_by_old_sfs_citation():
    tob = {"bilaga": 5, "header": ("Bestämmelse i NLTS", "Bestämmelse i LTS"),
           "text": "... lagen (1994:1563) om tobaksskatt ...", "rows": [("x", "y", 1)]}
    alk = {"bilaga": 7, "header": ("Bestämmelse i NLAS", "Bestämmelse i LAS"),
           "text": "... lagen (1994:1564) om alkoholskatt ...", "rows": [("x", "y", 1)]}
    assert C.relevant_tables([tob, alk], "1994:1563") == [tob]
    # nothing cites the number -> all tables pass through (scoring guards)
    anon = {"bilaga": 8, "header": None, "text": "Jämförelsetabell",
            "rows": [("x", "y", 1)]}
    assert C.relevant_tables([anon], "2003:113") == [anon]
