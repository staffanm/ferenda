"""Tests for EU-act defined-term extraction and in-act interlinking."""

from xml.etree import ElementTree as ET

from ferenda.eurlex.definitions import (
    _term_of,
    extract_definitions,
    inline_definitions,
    term_refs,
)
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


def test_a_term_named_in_passing_is_read_as_a_definition():
    """NIS2 defines "incident" in its definitions article but names
    "betydande incident" only in article 23(1), in a parenthesis closing the
    clause that describes it -- and then uses it 39 times. The parenthesis is
    read as the definition it is, and marked `defines_inline` so an inferred
    definition stays distinguishable from a listed one."""
    body = [Block("article", "Artikel 6 – Definitioner", num="6", anchor="6"),
            Block("point", "incident: en händelse som äventyrar.", num="6"),
            Block("article", "Artikel 23", num="23", anchor="23"),
            Block("paragraph", "Varje medlemsstat ska säkerställa att entiteter "
                  "underrättar sin CSIRT-enhet om alla incidenter som har en "
                  "betydande inverkan på deras tjänster (betydande incident).",
                  num="1"),
            Block("paragraph", "En betydande incident ska rapporteras utan "
                  "dröjsmål.", num="2"),
            Block("paragraph", "Rapporten om en betydande incident ska ange "
                  "orsaken.", num="3")]
    known = extract_definitions(body, "swe")
    assert set(known) == {"incident"}
    inline = inline_definitions(body, "swe", known)
    assert inline == {"betydande incident": "23.1"}
    named = next(b for b in body if b.defines_inline)
    assert (named.defines, named.anchor) == ("betydande incident", "23.1")
    # the listed definition is untouched by the second pass
    assert not any(b.defines_inline for b in body if b.defines == "incident")


def test_inline_definitions_is_swedish_only():
    """`_NOT_A_TERM`'s stopwords, `_TERMISH`'s letter class and
    `_COORDINATION`'s "och"/"eller" test are tuned against the 770-act Swedish
    measurement, and untested on English prose -- a manifestation in another
    language reads no inline definitions rather than guessing at an unmeasured
    precision. The body below is shaped to pass every one of those tests were
    it read at all, so the empty result comes from the language gate, not from
    the shape tests happening to reject English wording."""
    body = [Block("article", "Article 23", num="23", anchor="23"),
            Block("paragraph", "Each Member State shall ensure that entities "
                  "notify their CSIRT of any incident with a significant "
                  "impact on their services (significant incident).", num="1"),
            Block("paragraph", "A significant incident shall be reported "
                  "without delay.", num="2"),
            Block("paragraph", "The report on a significant incident shall "
                  "state the cause.", num="3")]
    assert inline_definitions(body, "eng", {}) == {}


def test_the_naming_parenthesis_is_read_strictly():
    """The parenthesis is a weak signal: over 770 Swedish acts, reading every
    one yields ~19% definitions, the rest being the annotation habit of
    commodity and veterinary regulations. Three conditions carry it to ~75%."""
    def named(text, num="1", uses=3):
        # the act must use the term, so pad the body with plain uses
        body = [Block("article", "Artikel 1", num="1", anchor="1"),
                Block("paragraph", text, num=num)]
        term = text[text.rfind("(") + 1:text.rfind(")")]
        body += [Block("paragraph", "Om %s gäller detta." % term, num=str(i))
                 for i in range(2, 2 + uses)]
        return inline_definitions(body, "swe", {})

    described = ("en tjänst som tillhandahålls av en offentlig myndighet och "
                 "som är av allmänt intresse (viktig tjänst).")
    assert named(described) == {"viktig tjänst": "1.1"}
    # a term the act never uses again is not a term it defined
    assert named(described, uses=0) == {}
    # ... and a parenthesis that annotates one list item, rather than closing a
    # description, is the commodity habit: "örtteer (blommor)"
    assert named("Bromopropylat: vin, russin, bönor, örtteer (viktig tjänst).") \
        == {}
    # the annotation habit: a species name, a qualifier carrying a digit, an
    # act number, a cross-reference, an alias, a clause
    for text in ("långfenad tonfisk (Thunnus alalunga).",
                 "tomater (gula och röda).",
                 "produkter enligt KN-nummer ex29336980 (TARIC-nummer 2933698070).",
                 "förordning (EU) 2016/679.",
                 "villkoren i bilaga I (se bilaga I).",
                 "Open Joint Stock Company Uralchem (nedan kallat Uralchem).",
                 "en taxa (som ska tillämpas vid provtagning)."):
        assert named(text) == {}, text
    # a naming inside a recital is not read: that is where the habit lives
    body = [Block("recital", "en tjänst av allmänt intresse (viktig tjänst).",
                  num="4"),
            Block("paragraph", "Om viktig tjänst gäller detta.", num="1"),
            Block("paragraph", "Viktig tjänst igen.", num="2")]
    assert inline_definitions(body, "swe", {}) == {}
