"""Tests for the per-paragraf författningskommentar extractor.

Each case locks in a rule derived from the curated prop corpus (the nine
props under site/data): the three FK styles, the span bounds, and the
recovery rules for parse defects the corpus exhibits. See fk.py's module
docstring for which prop motivates which rule.
"""

from accommodanda.forarbete.fk import extract, fk_span, parse_marks
from accommodanda.forarbete.structure import flatten


def prop(*blocks):
    return {"type": "prop", "uri": "https://lagen.nu/prop/2000/01:1",
            "structure": [
                {"type": "rubrik", "level": 1,
                 "text": ["16 Författningskommentar"]},
                *blocks]}


LAW = {"type": "rubrik", "level": 2,
       "text": ["16.1 Förslaget till lag om ändring i testlagen (2000:1)"]}


def test_span_survives_in_fk_chapter_headings_and_stops_at_bilaga():
    # prop 2017/18:89: "1 Kap. …" headings inside the FK are tagged level-1
    # rubriks (a level-bounded span would end after three blocks), and the
    # trailing matter carries the "Bilaga N" marginalia in its text
    art = prop(
        LAW,
        {"type": "rubrik", "level": 1, "text": ["1 Kap. Tillämpningsområde"]},
        {"type": "paragraf", "num": "1", "text": ["1 § Denna lag gäller X."]},
        {"type": "stycke", "text": ["I paragrafen anges tillämpningsområdet."]},
        {"type": "stycke",
         "text": ["Sammanfattning av betänkandet Bilaga 1 (SOU 2000:1)"]},
        {"type": "stycke", "text": ["Detta är bilagetext."]})
    blocks = flatten(art["structure"])
    assert fk_span(blocks) == (0, 5)
    (entry,) = extract(art)
    assert entry["chapter"] == "1"
    assert "bilagetext" not in entry["kommentar"]


def test_span_opens_from_stycke_reflowed_heading():
    # prop 2017/18:269: the FK chapter heading is lost to a stycke and
    # reflowed to "Författningskommentar 18"
    art = {"type": "prop", "structure": [
        {"type": "stycke", "text": ["Författningskommentar 18"]},
        LAW,
        {"type": "paragraf", "num": "1",
         "text": ["1 § I paragrafen anges lagens syfte."]}]}
    assert fk_span(flatten(art["structure"])) == (0, 3)
    (entry,) = extract(art)
    assert entry["kommentar"].startswith("I paragrafen anges")


def test_style_a_lagtext_split_from_commentary():
    # prop 2017/18:89: quoted lagtext follows the marker; commentary starts
    # at the opener formula
    art = prop(
        LAW,
        {"type": "paragraf", "num": "2", "text": ["2 § Med säkerhetsskydd avses skydd."]},
        {"type": "stycke", "text": ["Detta är fortsatt lagtext."]},
        {"type": "stycke", "text": ["I paragrafen definieras säkerhetsskydd."]},
        {"type": "stycke", "text": ["Andra stycket förtydligar detta."]})
    (entry,) = extract(art)
    assert entry["paragrafer"] == ["2"]
    assert entry["lagtext"] == ("Med säkerhetsskydd avses skydd.\n"
                                "Detta är fortsatt lagtext.")
    assert entry["kommentar"] == ("I paragrafen definieras säkerhetsskydd.\n"
                                  "Andra stycket förtydligar detta.")


def test_style_b_bare_marker_needs_no_opener_formula():
    # prop 2018/19:162 "6 §" + "I andra stycket ändras …": under a bare
    # marker there is no lagtext, so everything is commentary whatever
    # formula it opens with
    art = prop(
        LAW,
        {"type": "paragraf", "num": "6", "text": ["6 §"]},
        {"type": "stycke",
         "text": ["I andra stycket ändras paragrafhänvisningen."]})
    (entry,) = extract(art)
    assert entry["lagtext"] == ""
    assert entry["kommentar"] == "I andra stycket ändras paragrafhänvisningen."


def test_style_c_marker_inline_in_commentary_stycke():
    # prop 2018/19:163: "1 § Paragrafen reglerar …" as one stycke, never
    # tagged paragraf by the parser
    art = prop(
        LAW,
        {"type": "stycke",
         "text": ["3 § Paragrafen begränsar lagens tillämpningsområde."]})
    (entry,) = extract(art)
    assert entry["paragrafer"] == ["3"]
    assert entry["kommentar"] == "Paragrafen begränsar lagens tillämpningsområde."


def test_combined_marker_names_several_paragrafer():
    # prop 2020/21:194: "9 och 10 §§" heading (tagged a level-1 rubrik)
    art = prop(
        LAW,
        {"type": "rubrik", "level": 1, "text": ["9 och 10 §§"]},
        {"type": "stycke", "text": ["Paragraferna är nya."]})
    (entry,) = extract(art)
    assert entry["paragrafer"] == ["9", "10"]


def test_group_comment_annexes_quoted_run():
    # prop 2017/18:89 3 kap. 5-10 §§: a run of quoted paragrafer closed by
    # one "I paragraferna finns …" comment on the last -- the comment covers
    # the whole run
    art = prop(
        LAW,
        {"type": "kapitel", "num": "3", "text": ["3 kap. Säkerhetsprövning"]},
        {"type": "paragraf", "num": "5", "text": ["5 § En anställning ska placeras i klass."]},
        {"type": "paragraf", "num": "6", "text": ["6 § En anställning i klass 1 kräver Y."]},
        {"type": "paragraf", "num": "7", "text": ["7 § En anställning i klass 2 kräver Z."]},
        {"type": "stycke", "text": ["I paragraferna finns bestämmelser om placering."]})
    (entry,) = extract(art)
    assert entry["chapter"] == "3"
    assert entry["paragrafer"] == ["5", "6", "7"]
    assert entry["kommentar"] == "I paragraferna finns bestämmelser om placering."
    # the quoted-only paragrafs are visible in the debugging view
    assert len(extract(art, include_empty=True)) == 1


def test_group_comment_does_not_cross_a_subject_heading():
    art = prop(
        LAW,
        {"type": "paragraf", "num": "5", "text": ["5 § Lagtext utan kommentar."]},
        {"type": "rubrik", "level": 3, "text": ["Nästa ämne"]},
        {"type": "paragraf", "num": "7", "text": ["7 §"]},
        {"type": "stycke", "text": ["I paragraferna finns bestämmelser om annat."]})
    (entry,) = extract(art)
    assert entry["paragrafer"] == ["7"]      # 5 § settled at the heading


def test_law_level_omnibus_comment_and_stycke_demoted_law_rubrik():
    # prop 2018/19:162: one-stycke per-law comments ("I 4 c § görs en
    # ändring …"), and a law rubrik the parser demoted to a stycke ("9.13 …")
    art = prop(
        {"type": "rubrik", "level": 2,
         "text": ["9.14 Förslaget till lag om ändring i lagen (1966:314) om kontinentalsockeln"]},
        {"type": "stycke", "text": ["I 4 c § görs en ändring med anledning av att beteckningen ändras."]},
        {"type": "stycke", "text": ["9.15 Förslaget till lag om ändring i väglagen (1971:948)"]},
        {"type": "stycke", "text": ["I 76 § görs en ändring med anledning av att beteckningen ändras."]})
    first, second = extract(art)
    assert first["law"].startswith("9.14") and first["paragrafer"] == []
    assert second["law"].startswith("9.15") and second["paragrafer"] == []


def test_single_law_named_in_leading_stycke():
    # prop 2020/21:13: no numbered per-law rubrik; the law is a plain stycke
    # directly under the FK heading
    art = prop(
        {"type": "stycke",
         "text": ["Förslaget till lag om ändring i säkerhetsskyddslagen (2018:585)"]},
        {"type": "rubrik", "level": 1, "text": ["1 kap."]},
        {"type": "stycke", "text": ["1 § Paragrafen, som reglerar lagens tillämpningsområde, är ändrad."]})
    (entry,) = extract(art)
    assert entry["law"] == ("Förslaget till lag om ändring i "
                            "säkerhetsskyddslagen (2018:585)")
    assert entry["chapter"] == "1"


def test_wrapped_lagtext_rubrik_is_not_a_boundary():
    # prop 2017/18:89 5 kap. 5 §: the lagtext wrap "8 kap. 7 § regeringsformen
    # meddela …" is tagged a level-1 rubrik; it must neither become the
    # chapter nor orphan the commentary that follows
    art = prop(
        LAW,
        {"type": "kapitel", "num": "5", "text": ["5 kap. Övriga bestämmelser"]},
        {"type": "paragraf", "num": "5",
         "text": ["5 § Regeringen kan med stöd av"]},
        {"type": "rubrik", "level": 1,
         "text": ["8 kap. 7 § regeringsformen meddela föreskrifter om verkställighet av denna lag."]},
        {"type": "stycke", "text": ["Paragrafen upplyser om verkställighetsföreskrifter."]})
    (entry,) = extract(art)
    assert entry["chapter"] == "5"
    assert entry["kommentar"] == "Paragrafen upplyser om verkställighetsföreskrifter."


def test_footnote_rubrik_is_not_a_boundary():
    # prop 2022/23:87: a lagtext footnote tagged as a rubrik inside an entry
    art = prop(
        LAW,
        {"type": "paragraf", "num": "3", "text": ["3 § Säkerhetsprövningen ska göras."]},
        {"type": "rubrik", "level": 1, "text": ["3 Senaste lydelse 2022:443."]},
        {"type": "stycke", "text": ["Paragrafen ändras så att prövningen utökas."]})
    (entry,) = extract(art)
    assert entry["kommentar"] == "Paragrafen ändras så att prövningen utökas."


def test_embedded_marker_after_merged_heading():
    # prop 2020/21:13: "… lämplighetsprövning 8 § Paragrafen är ny …" -- a
    # subject heading merged into the comment stycke
    art = prop(
        LAW,
        {"type": "stycke",
         "text": ["Särskild säkerhetsskyddsbedömning 8 § Paragrafen är ny och innehåller bemyndiganden."]})
    (entry,) = extract(art)
    assert entry["paragrafer"] == ["8"]
    assert entry["kommentar"] == "Paragrafen är ny och innehåller bemyndiganden."


def test_citation_start_is_not_a_marker():
    # "8 § andra stycket gäller …" cites a provision; only an
    # uppercase/digit continuation after the marker opens an entry
    art = prop(
        LAW,
        {"type": "paragraf", "num": "7", "text": ["7 §"]},
        {"type": "stycke", "text": ["Paragrafen är ny."]},
        {"type": "stycke", "text": ["8 § andra stycket gäller inte här."]})
    (entry,) = extract(art)
    assert entry["paragrafer"] == ["7"]
    assert entry["kommentar"].endswith("8 § andra stycket gäller inte här.")


def test_combined_kap_par_marker_sets_chapter_and_opens_entry():
    # prop 2000/01:48 "17 kap. 21 a §" (bare) and prop 2003/04:24
    # "12 kap. 3 a § Paragrafen har utformats …" (comment inline): one block
    # carries both the chapter and the paragraf
    art = prop(
        LAW,
        {"type": "kapitel", "num": "3", "text": ["3 kap. 18 §"]},
        {"type": "stycke", "text": ["Det andra stycket är nytt och behandlas i avsnitt 5."]},
        {"type": "rubrik", "level": 1,
         "text": ["12 kap. 3 a § Paragrafen har utformats i enlighet med Lagrådets förslag."]})
    first, second = extract(art)
    assert (first["chapter"], first["paragrafer"]) == ("3", ["18"])
    assert first["kommentar"].startswith("Det andra stycket")
    assert (second["chapter"], second["paragrafer"]) == ("12", ["3 a"])
    assert second["kommentar"].startswith("Paragrafen har utformats")


def test_comment_verb_continuation_is_a_marker_not_a_citation():
    # prop 2003/04:24 "21 § ändras på det sättet …": lowercase after the
    # marker is normally a citation, but a comment verb makes it the comment
    # itself (kept whole -- the marker is its subject)
    art = prop(
        LAW,
        {"type": "stycke",
         "text": ["21 § ändras på det sättet att mervärdesskatt inte längre skall ingå."]},
        {"type": "stycke", "text": ["8 § andra stycket gäller inte här."]})
    (entry,) = extract(art)
    assert entry["paragrafer"] == ["21"]
    assert entry["kommentar"].startswith("21 § ändras på det sättet")


def test_fk_heading_survives_merged_page_marginalia():
    # prop 1996/97:74: the heading arrives as "7 Författningskommentar
    # Prop. 1997:74" (page-header marginalia fused in)
    art = {"type": "prop", "structure": [
        {"type": "rubrik", "level": 1,
         "text": ["7 Författningskommentar Prop. 1997:74"]},
        LAW,
        {"type": "stycke", "text": ["8 § I paragrafen har införts en bestämmelse."]}]}
    (entry,) = extract(art)
    assert entry["paragrafer"] == ["8"]


def test_forslaget_har_behandlats_opener():
    # prop 2004/05:159 (style A) / 2011/12:172 (style C): commentary opens
    # "Förslaget har behandlats i avsnitt(en) …"
    art = prop(
        LAW,
        {"type": "stycke", "text": ["1 § Ett kommunalt aktiebolag får ingå avtal."]},
        {"type": "stycke", "text": ["Förslaget har behandlats i avsnitten 5.1 och 5.2."]})
    (entry,) = extract(art)
    assert entry["lagtext"] == "Ett kommunalt aktiebolag får ingå avtal."
    assert entry["kommentar"] == "Förslaget har behandlats i avsnitten 5.1 och 5.2."


def test_trailing_protocol_merged_into_stycke_ends_span():
    # prop 2002/03:131: the closing protocol arrives as a stycke prefixed
    # with the department name, not at line start
    art = prop(
        LAW,
        {"type": "stycke", "text": ["3 § Paragrafen är ny."]},
        {"type": "stycke",
         "text": ["Socialdepartementet Utdrag ur protokoll vid regeringssammanträde den 28 maj 2003"]},
        {"type": "stycke", "text": ["5 § Paragrafen är inte alls ny."]})
    (entry,) = extract(art)
    assert entry["paragrafer"] == ["3"]


def test_mark_stamps_commentary_blocks_in_artifact_structure():
    # mark=True flags the commentary blocks (not the quoted lagtext) in the
    # artifact's own structure -- the prop page's highlight reads the flag
    lag = {"type": "paragraf", "num": "2", "text": ["2 § Med säkerhetsskydd avses skydd."]}
    lag2 = {"type": "stycke", "text": ["Detta är fortsatt lagtext."]}
    kom = {"type": "stycke", "text": ["I paragrafen definieras säkerhetsskydd."]}
    kom2 = {"type": "stycke", "text": ["Andra stycket förtydligar detta."]}
    art = prop(LAW, lag, lag2, kom, kom2)
    extract(art, mark=True)
    assert kom.get("fk") == kom2.get("fk") == 1     # same entry, same box
    assert "fk" not in lag and "fk" not in lag2
    # a bare marker block joins its commentary run; a second entry gets its
    # own number, so the renderer draws separate boxes
    bare = {"type": "paragraf", "num": "6", "text": ["6 §"]}
    komb = {"type": "stycke", "text": ["Paragrafen är ny."]}
    bare2 = {"type": "paragraf", "num": "7", "text": ["7 §"]}
    komb2 = {"type": "stycke", "text": ["Paragrafen är ännu nyare."]}
    art = prop(LAW, bare, komb, bare2, komb2)
    extract(art, mark=True)
    assert bare.get("fk") == komb.get("fk") == 1
    assert bare2.get("fk") == komb2.get("fk") == 2


def test_parse_marks_ranges_and_letters():
    assert parse_marks("9 och 10") == ["9", "10"]
    assert parse_marks("5–7") == ["5", "6", "7"]
    assert parse_marks("1, 2 och 4") == ["1", "2", "4"]
    assert parse_marks("18 a och 19") == ["18 a", "19"]


def test_non_prop_yields_nothing():
    assert extract({"type": "sou", "structure": []}) == []
