"""Tests for the författningskommentar implements-extractor."""

from accommodanda.forarbete.kommentar import (parse_articles, resolve_directives,
                                              _refparser, extract)

CELEX = "https://lagen.nu/ext/celex/"


def test_parse_articles_dotted_ranged_listed_lettered():
    assert parse_articles("21.1–21.3") == (["21.1", "21.2", "21.3"], ["21"])
    assert parse_articles("3.1 och 3.2") == (["3.1", "3.2"], ["3"])
    assert parse_articles("34.4, 34.5 och 34.7") == (
        ["34.4", "34.5", "34.7"], ["34"])
    assert parse_articles("6.11 och 23.2") == (["6.11", "23.2"], ["6", "23"])
    assert parse_articles("23.4 a") == (["23.4 a"], ["23"])
    assert parse_articles("28") == (["28"], ["28"])


def test_directive_alias_binds_to_subject_not_repealed():
    # the alias "(NIS 2-direktivet)" sits right after the *repealed* 2016/1148,
    # but names the subject directive 2022/2555 -- the first EU citation wins
    blocks = [{"text": [
        "Europaparlamentets och rådets direktiv (EU) 2022/2555 av den 14 "
        "december 2022 om ändring av förordning (EU) nr 910/2014 och om "
        "upphävande av direktiv (EU) 2016/1148 (NIS 2-direktivet)."]}]
    aliases = resolve_directives(blocks, _refparser())
    assert aliases["nis 2-direktivet"] == CELEX + "32022L2555"
    assert aliases["default"] == CELEX + "32022L2555"


def test_extract_implements_from_kommentar():
    # a minimal proposition artifact: the directive definition, then a
    # författningskommentar section with one implements statement
    art = {"body": [
        {"type": "stycke", "text": [
            "Europaparlamentets och rådets direktiv (EU) 2022/2555 "
            "(NIS 2-direktivet) ska genomföras."]},
        {"type": "rubrik", "level": 1, "text": ["15 Författningskommentar"]},
        {"type": "rubrik", "level": 2, "text": ["15.1 Förslaget till cybersäkerhetslag"]},
        {"type": "kapitel", "num": "2", "text": ["2 kap. Verksamhetsutövares skyldigheter"]},
        {"type": "paragraf", "num": "3", "page": 243,
         "text": ["3 § Verksamhetsutövare ska vidta åtgärder."]},
        {"type": "stycke", "page": 243, "text": [
            "Paragrafen genomför artikel 21.1–21.3 i NIS 2-direktivet. "
            "Paragrafen behandlar de säkerhetsåtgärder som ska vidtas."]},
    ]}
    [rec] = extract(art)
    assert rec["predicate"] == "rpubl:genomforDirektiv"
    assert rec["directive"] == CELEX + "32022L2555"
    assert rec["articles"] == ["21"]
    assert rec["pinpoints"] == ["21.1", "21.2", "21.3"]
    assert rec["uris"] == [CELEX + "32022L2555#21"]
    assert rec["partial"] is False
    assert rec["page"] == 243
    assert "cybersäkerhetslag" in rec["law"]
    # the implements edge is tied to the paragraf it comments on
    assert rec["chapter"] == "2" and rec["paragraf"] == "3"


def test_extract_ignores_references_outside_kommentar():
    # a "genomför artikel" sentence in the general motivering is not an
    # författningskommentar implements statement
    art = {"body": [
        {"type": "stycke", "text": [
            "Direktiv (EU) 2022/2555 (NIS 2-direktivet)."]},
        {"type": "stycke", "text": [
            "Paragrafen genomför artikel 5 i NIS 2-direktivet."]},  # no FK heading
    ]}
    assert extract(art) == []
