"""Tests for EU-act defined-term extraction and in-act interlinking."""

from xml.etree import ElementTree as ET

from ferenda.eurlex.definitions import (_term_of,
                                             extract_definitions, term_refs)
from ferenda.eurlex.model import Block
from ferenda.eurlex.parse import parse_formex, to_artifact
from ferenda.lib.begrepp import build_matcher
from ferenda.lib.eu_structure import flatten


# a directive with a definitions article (art. 5) and a later article (art. 7)
# that uses two of the defined terms, inflected
DEFN_ACT = """<ACT>
  <BIB.INSTANCE><DATE ISO="20221214">20221214</DATE></BIB.INSTANCE>
  <TITLE><TI><P>Testdirektiv</P></TI></TITLE>
  <ENACTING.TERMS>
    <ARTICLE IDENTIFIER="005">
      <TI.ART>Artikel 5</TI.ART><STI.ART>Definitioner</STI.ART>
      <PARAG IDENTIFIER="005.001"><ALINEA>
        <P>I detta direktiv gäller följande definitioner:</P>
        <LIST TYPE="arab">
          <ITEM><NP><NO.P>1.</NO.P>
            <TXT>incident: en händelse som undergräver säkerheten.</TXT></NP></ITEM>
          <ITEM><NP><NO.P>2.</NO.P>
            <TXT>sårbarhet: en svaghet hos ett system som en incident kan utnyttja.</TXT></NP></ITEM>
        </LIST>
      </ALINEA></PARAG>
    </ARTICLE>
    <ARTICLE IDENTIFIER="007">
      <TI.ART>Artikel 7</TI.ART>
      <PARAG IDENTIFIER="007.001"><NO.PARAG>1.</NO.PARAG>
        <ALINEA>Riktlinjer för hantering av sårbarheter och incidenter.</ALINEA></PARAG>
    </ARTICLE>
  </ENACTING.TERMS>
</ACT>"""


# -- the helper functions -------------------------------------------------

def test_term_of_takes_lead_phrase_before_colon():
    assert _term_of("sårbarhet: en svaghet som kan utnyttjas.", "swe") == "sårbarhet"
    assert _term_of("nätverks- och informationssystem: ...", "swe") \
        == "nätverks- och informationssystem"
    # the definition's body may itself be the sub-list that follows
    assert _term_of("transportabla tryckbärande anordningar:", "swe") \
        == "transportabla tryckbärande anordningar"
    # a definition that goes on to introduce sub-definitions is still a definition:
    # the announcing phrase is only disqualifying in the *head*
    assert _term_of("tjänst: alla tjänster. I denna definition avses med", "swe") \
        == "tjänst"


def test_term_of_rejects_non_definition_points():
    assert _term_of("en löpande mening utan kolon", "swe") is None   # no colon
    assert _term_of(": tom", "swe") is None                          # empty head
    long_head = "x" * 90 + ": def"                                   # head too long
    assert _term_of(long_head, "swe") is None
    # the line announcing the list is a numbered paragraph in some acts
    # (2015/1535 art. 1.1) -- it defines nothing
    assert _term_of("I detta direktiv gäller följande definitioner:", "swe") is None


def test_an_amending_instruction_is_not_a_defined_term():
    """An amending act writes its instructions in exactly a definition's shape,
    and 2014/48/EU article 1 is headed "Definitioner av vissa termer" -- the
    heading of the article it *inserts* -- so every instruction under it read as
    a definition. 2026/1183 art. 1.7 became a 47 kB "definition" of the concept
    "Artiklarna 67-112 ska ersättas med följande"."""
    for head in ("Artikel 6 ska ersättas med följande",
                 "Artiklarna 67–112 ska ersättas med följande",
                 "Följande punkt ska läggas till",
                 "I artikel 20 ska följande punkt läggas till som punkt 5",
                 "Artikel 27 ska ändras på följande sätt"):
        assert _term_of(head + ": den nya texten", "swe") is None, head

    # a term carries "ska" only inside a relative clause, and keeps it: 18 of the
    # corpus's 268 ska-bearing heads are real terms and all 18 read this way
    for term in ("sammanlagt belopp som ska betalas av konsumenten",
                 "kemikalie för vilken exportanmälan ska ske",
                 "garanti som ska infrias på anfordran",
                 "enheter som inte ska undersökas"):
        assert _term_of(term + ": en beskrivning.", "swe") == term


def test_extract_definitions_anchors_points_and_maps_terms():
    body = [Block("article", "Artikel 5 – Definitioner", num="5", anchor="5"),
            Block("paragraph", "I detta direktiv gäller följande definitioner:"),
            Block("point", "incident: en händelse.", num="1"),
            Block("point", "sårbarhet: en svaghet.", num="2"),
            Block("article", "Artikel 7", num="7", anchor="7"),
            Block("point", "annat: inte en definition.", num="3")]
    terms = extract_definitions(body, "swe")
    assert terms == {"incident": "5.1", "sårbarhet": "5.2"}
    # the definition points are mutated; the point after article 7 is untouched
    assert (body[2].anchor, body[2].defines) == ("5.1", "incident")
    assert (body[3].anchor, body[3].defines) == ("5.2", "sårbarhet")
    assert body[5].anchor is None and body[5].defines is None


def test_term_refs_are_suffix_tolerant_and_skip_self():
    matcher, index = build_matcher({"sårbarhet": "5.2", "incident": "5.1"}, "swe")
    refs = term_refs("hantering av sårbarheter och incidenter.",
                     matcher, index, "https://lagen.nu/celex/X", None)
    found = {(r.text, r.uri.rsplit("#", 1)[1]) for r in refs}
    assert found == {("sårbarheter", "5.2"), ("incidenter", "5.1")}
    assert all(r.kind == "term" for r in refs)
    # inside the definition of "sårbarhet" (anchor 5.2) its own term is skipped,
    # but a different defined term it mentions is still linked
    self_refs = term_refs("sårbarhet: en svaghet som en incident utnyttjar.",
                          matcher, index, "https://lagen.nu/celex/X", "5.2")
    assert [r.text for r in self_refs] == ["incident"]


def test_build_matcher_prefers_longer_term():
    # "cybersäkerhet" is nested in "storskalig cybersäkerhetsincident" -- the
    # longer phrase must win where it occurs verbatim
    matcher, index = build_matcher(
        {"cybersäkerhet": "6.3", "storskalig cybersäkerhetsincident": "6.7"},
        "swe")
    refs = term_refs("en storskalig cybersäkerhetsincident inträffade.",
                     matcher, index, "https://lagen.nu/celex/X", None)
    assert [(r.text, r.uri.rsplit("#", 1)[1]) for r in refs] \
        == [("storskalig cybersäkerhetsincident", "6.7")]


# -- end to end through to_artifact --------------------------------------

def _runs(block):
    return block["text"]


def test_to_artifact_definitions_and_uses():
    art = to_artifact(parse_formex(ET.fromstring(DEFN_ACT), "32022L2555", "swe"))
    blocks = flatten(art["structure"])
    by_id = {b.get("id"): b for b in blocks if b.get("id")}

    # the definition points are anchored <article>.<point> and tagged
    assert by_id["5.1"]["defines"] == "incident"
    assert by_id["5.2"]["defines"] == "sårbarhet"

    # article 7's paragraph links its inflected uses to the definition points
    # (selected by content -- the definitions article's own entries are now
    # paragraphs too, so a bare num=="1" lookup would hit "incident" first)
    para = next(b for b in blocks
                if b["type"] == "paragraph" and b.get("num") == "1"
                and "Riktlinjer" in _runs(b)[0])
    links = [r for r in _runs(para) if isinstance(r, dict)]
    assert {(r["text"], r["uri"]) for r in links if r.get("kind") == "term"} == {
        ("sårbarheter", "https://lagen.nu/celex/32022L2555#5.2"),
        ("incidenter", "https://lagen.nu/celex/32022L2555#5.1")}

    # the "sårbarhet" definition links the "incident" it mentions but not itself
    defn = by_id["5.2"]
    terms_in_defn = [r["text"] for r in _runs(defn)
                     if isinstance(r, dict) and r.get("kind") == "term"]
    assert "incident" in terms_in_defn
    assert "sårbarhet" not in terms_in_defn
