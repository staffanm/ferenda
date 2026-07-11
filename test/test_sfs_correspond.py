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
         CELEX + "prop/2017/18:89")]


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
