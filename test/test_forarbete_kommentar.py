"""Tests for the författningskommentar implements-extractor."""

from accommodanda.forarbete.kommentar import (
    _refparser,
    article_of,
    extract,
    fm_law,
    parse_articles,
    pinpoints_by_article,
    proposed_name,
    resolve_directives,
    sfs_number,
)
from accommodanda.forarbete.structure import flatten, nest

CELEX = "https://lagen.nu/ext/celex/"


def test_parse_articles_dotted_ranged_listed_lettered():
    assert parse_articles("21.1–21.3") == (["21.1", "21.2", "21.3"], ["21"])
    assert parse_articles("3.1 och 3.2") == (["3.1", "3.2"], ["3"])
    assert parse_articles("34.4, 34.5 och 34.7") == (
        ["34.4", "34.5", "34.7"], ["34"])
    assert parse_articles("6.11 och 23.2") == (["6.11", "23.2"], ["6", "23"])
    assert parse_articles("23.4 a") == (["23.4 a"], ["23"])
    assert parse_articles("28") == (["28"], ["28"])


def test_pinpoints_grouped_by_article():
    assert article_of("26.1 c") == "26"
    assert article_of("2.2 f") == "2"
    assert article_of("28") == "28"
    # a statement spanning articles 2 and 26 -> each gets only its own pinpoints
    assert pinpoints_by_article(["2.1", "2.2 f", "2.5 a", "26.1 c"]) == {
        "2": ["2.1", "2.2 f", "2.5 a"], "26": ["26.1 c"]}


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


def test_default_directive_from_law_level_subject_statement():
    # no parenthetical alias defines the directive; a repealed predecessor is
    # cited more often, but the law-level "lagen genomförs … direktiv X" names the
    # subject -- a bare "direktivet" must resolve to it (2015/2302), not the
    # more-cited repealed directive (90/314/EEG)
    blocks = [
        {"text": ["Genom lagen genomförs delvis Europaparlamentets och rådets "
                  "direktiv (EU) 2015/2302 av den 25 november 2015 om paketresor."]},
        {"text": ["Den tidigare regleringen i rådets direktiv 90/314/EEG om "
                  "paketresor upphävs. Direktiv 90/314/EEG byggde på en annan "
                  "systematik än direktiv 90/314/EEG."]},
    ]
    aliases = resolve_directives(blocks, _refparser())
    assert aliases["default"] == CELEX + "32015L2302"


def test_extract_implements_from_kommentar():
    # a minimal proposition artifact: the directive definition, then a
    # författningskommentar section with one implements statement
    art = {"type": "prop", "structure": nest([
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
    ])}
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
    art = {"type": "prop", "structure": nest([
        {"type": "stycke", "text": [
            "Direktiv (EU) 2022/2555 (NIS 2-direktivet)."]},
        {"type": "stycke", "text": [
            "Paragrafen genomför artikel 5 i NIS 2-direktivet."]},  # no FK heading
    ])}
    assert extract(art) == []


def test_extract_only_from_proposition():
    # the same författningskommentar in a lagrådsremiss yields nothing -- only a
    # proposition is authoritative for implements relations (the proposed
    # structure is still renumbered before enactment)
    body = nest([
        {"type": "stycke", "text": [
            "Europaparlamentets och rådets direktiv (EU) 2022/2555 "
            "(NIS 2-direktivet) ska genomföras."]},
        {"type": "rubrik", "level": 1, "text": ["15 Författningskommentar"]},
        {"type": "rubrik", "level": 2, "text": ["15.1 Förslaget till lag"]},
        {"type": "paragraf", "num": "3", "text": ["3 §"]},
        {"type": "stycke", "text": [
            "Paragrafen genomför artikel 21 i NIS 2-direktivet."]},
    ])
    assert extract({"type": "prop", "structure": body})      # prop: extracted
    assert extract({"type": "lr", "structure": body}) == []  # lagrådsremiss: not
    assert extract({"type": "sou", "structure": body}) == []


def test_alias_binds_across_long_amendment_list():
    # a real förordningsmotiv sentence (Fm 2022:5): the subject directive
    # 2009/147/EG is followed by a long "senast ändrat genom …" amendment list
    # (which itself cites 2002/49/EG first) before the alias "(fågeldirektivet)".
    # The alias must bind to the sentence's *first* directive (2009/147/EG), not
    # the first act of the amendment list -- a fixed char window would misbind.
    blocks = [{"text": [
        "Första punkten motsvarar förbudet i den tidigare 4 § första stycket 1 "
        "och genomför artikel 5 a i Europaparlamentets och rådets direktiv "
        "2009/147/EG av den 30 november 2009 om bevarande av vilda fåglar, "
        "senast ändrat genom Europaparlamentets och rådets förordning (EU) "
        "2019/1010 av den 5 juni 2019 om samordning av rapporteringsskyldigheter, "
        "Europaparlamentets och rådets direktiv 2002/49/EG, 2004/35/EG, "
        "2007/2/EG, 2009/147/EG och 2010/63/EU samt rådets direktiv 86/278/EEG "
        "(fågeldirektivet)."]}]
    aliases = resolve_directives(blocks, _refparser())
    assert aliases["fågeldirektivet"] == CELEX + "32009L0147"


def test_alias_lookback_survives_abbreviations():
    # dotted prose abbreviations between the subject directive and its alias
    # ("bl.a. Natura…", "t.ex. Barbastella…" -- terminator + space + uppercase)
    # are not sentence ends: truncating there would leave the lookback window
    # with no directive and the alias would silently fall through to the default
    blocks = [{"text": [
        "Rådets direktiv 92/43/EEG av den 21 maj 1992 om bevarande av "
        "livsmiljöer samt vilda djur och växter gäller bl.a. Natura "
        "2000-områden och skyddar t.ex. Barbastella barbastellus "
        "(art- och habitatdirektivet). En senare mening om annat."]}]
    aliases = resolve_directives(blocks, _refparser())
    assert aliases["art- och habitatdirektivet"] == CELEX + "31992L0043"


def test_subject_vocabulary_is_per_document_type():
    # the default-directive subject vote is per document type: "förordningen …
    # genomförs" is an fm's own statement, but in a prop it talks about some
    # *other* förordning and must not vote on the prop's default
    blocks = [{"text": [
        "Genom förordningen genomförs Europaparlamentets och rådets direktiv "
        "2009/147/EG av den 30 november 2009 om bevarande av vilda fåglar."]}]
    assert resolve_directives(blocks, _refparser(), "fm")["default"] == (
        CELEX + "32009L0147")
    assert "default" not in resolve_directives(blocks, _refparser(), "prop")


def test_fm_law_from_title_rubriks():
    # a förordningsmotiv names its förordning in the leading title rubriks, not a
    # prop-style level-2 heading: an amendment splits "Förordning" + "om ändring
    # i X (NNNN:NN)"; a new förordning is a single "X-förordning" rubrik.
    amend = nest([
        {"type": "rubrik", "level": 3, "text": ["Förordningsmotiv"]},
        {"type": "rubrik", "level": 3, "text": ["Förordning"]},
        {"type": "rubrik", "level": 3,
         "text": ["om ändring i artskyddsförordningen (2007:845)"]},
        {"type": "stycke", "text": ["Utfärdad den 16 juni 2022"]},
        {"type": "rubrik", "level": 3, "text": ["Författningskommentar"]},
    ])
    law = fm_law(flatten(amend))
    assert law == "Förordning om ändring i artskyddsförordningen (2007:845)"
    assert sfs_number(law) == "2007:845"

    new = flatten(nest([
        {"type": "rubrik", "level": 3, "text": ["Förordningsmotiv"]},
        {"type": "rubrik", "level": 3, "text": ["Socialtjänstförordning"]},
        {"type": "stycke", "text": ["Utfärdad den 22 maj 2025"]},
        {"type": "rubrik", "level": 3, "text": ["Författningskommentar"]},
    ]))
    assert fm_law(new) == "Socialtjänstförordning"
    assert sfs_number("Socialtjänstförordning") is None
    assert proposed_name("Socialtjänstförordning") == "Socialtjänstförordning"

    # Fm 2026:1: a long title the layout wraps over two rubriks -- the leading
    # rubriks are rejoined into the full förordning name
    wrapped = flatten(nest([
        {"type": "rubrik", "level": 3, "text": ["Förordningsmotiv"]},
        {"type": "rubrik", "level": 3,
         "text": ["Förordning om omfattning av tiden för lärares och"]},
        {"type": "rubrik", "level": 3,
         "text": ["förskollärares undervisningsuppdrag"]},
        {"type": "stycke", "text": ["Utfärdad den 25 juni 2026"]},
        {"type": "rubrik", "level": 3, "text": ["Författningskommentar"]},
    ]))
    assert fm_law(wrapped) == ("Förordning om omfattning av tiden för lärares "
                               "och förskollärares undervisningsuppdrag")

    # the doc-type label fused into the title itself: stripped by prefix, the
    # remainder is the förordning name (skipping the whole rubrik would leave
    # no law at all)
    fused = flatten(nest([
        {"type": "rubrik", "level": 3, "text": [
            "Förordningsmotiv om förordning om ändring i "
            "artskyddsförordningen (2007:845)"]},
        {"type": "stycke", "text": ["Utfärdad den 16 juni 2022"]},
    ]))
    assert sfs_number(fm_law(fused)) == "2007:845"

    # an in-body rubrik mentioning "förordning" never wins: the scan is bounded
    # to the rubriks before the first non-rubrik block
    body_only = flatten(nest([
        {"type": "rubrik", "level": 3, "text": ["Förordningsmotiv"]},
        {"type": "stycke", "text": ["Utfärdad den 1 juli 2026"]},
        {"type": "rubrik", "level": 3, "text": ["Förordningens tillämpning"]},
    ]))
    assert fm_law(body_only) is None


def test_extract_from_forordningsmotiv():
    # Fm 2022:5 (ändring i artskyddsförordningen, transposes the birds directive):
    # the författningskommentar rubrik comes out at level 3, its förordning named
    # in the title rubriks, and "Andra/Fjärde punkten genomför artikel 5 b/d i
    # fågeldirektivet" fire as implements statements keyed to 4 §.
    art = {"type": "fm", "structure": nest([
        {"type": "rubrik", "level": 3, "text": ["Förordningsmotiv"]},
        {"type": "rubrik", "level": 3, "text": ["Förordning"]},
        {"type": "rubrik", "level": 3,
         "text": ["om ändring i artskyddsförordningen (2007:845)"]},
        {"type": "stycke", "page": 1, "text": ["Utfärdad den 16 juni 2022"]},
        {"type": "rubrik", "level": 3, "text": ["Fridlysning av fåglar"]},
        {"type": "paragraf", "num": "4", "page": 1, "text": ["4 § Det är förbjudet…"]},
        {"type": "rubrik", "level": 3, "text": ["Författningskommentar"]},
        {"type": "paragraf", "num": "4", "page": 3, "text": ["4 § Det är förbjudet…"]},
        {"type": "stycke", "page": 3, "text": [
            "Paragrafen, som är ny, innehåller bestämmelser om fridlysning av "
            "vilda fåglar. Första punkten motsvarar förbudet i den tidigare 4 § "
            "första stycket 1 och genomför artikel 5 a i Europaparlamentets och "
            "rådets direktiv 2009/147/EG av den 30 november 2009 om bevarande av "
            "vilda fåglar, senast ändrat genom Europaparlamentets och rådets "
            "förordning (EU) 2019/1010 av den 5 juni 2019, Europaparlamentets och "
            "rådets direktiv 2002/49/EG, 2004/35/EG, 2007/2/EG, 2009/147/EG och "
            "2010/63/EU samt rådets direktiv 86/278/EEG (fågeldirektivet). Andra "
            "punkten genomför artikel 5 b i fågeldirektivet. Fjärde punkten "
            "genomför artikel 5 d i fågeldirektivet."]},
    ])}
    rows = extract(art)
    assert len(rows) == 2
    assert all(r["directive"] == CELEX + "32009L0147" for r in rows)
    assert all(r["articles"] == ["5"] and r["paragraf"] == "4" for r in rows)
    assert [r["pinpoints"] for r in rows] == [["5 b"], ["5 d"]]
    assert sfs_number(rows[0]["law"]) == "2007:845"


def test_extract_forordningsmotiv_editorial_change_yields_nothing():
    # Fm 2025:1 (ny socialtjänstförordning): the commentary is purely editorial
    # ("Ändringarna är endast språkliga") with no genomför statement -- zero rows
    # is correct-by-content, not a pattern gap.
    art = {"type": "fm", "structure": nest([
        {"type": "rubrik", "level": 3, "text": ["Förordningsmotiv"]},
        {"type": "rubrik", "level": 3, "text": ["Socialtjänstförordning"]},
        {"type": "stycke", "page": 1, "text": ["Utfärdad den 22 maj 2025"]},
        {"type": "rubrik", "level": 3, "text": ["Författningskommentar"]},
        {"type": "kapitel", "num": "2", "page": 2, "text": ["2 kap. Boendeformer"]},
        {"type": "rubrik", "level": 3, "text": ["Bemanning"]},
        {"type": "paragraf", "num": "2", "page": 2, "text": ["2 §"]},
        {"type": "stycke", "page": 2, "text": [
            "Paragrafen motsvarar hittillsvarande 2 kap. 3 § "
            "socialtjänstförordningen (2001:937). Ändringarna är endast "
            "språkliga."]},
    ])}
    assert extract(art) == []


def test_directive_alias_ignores_co_cited_regulation():
    # a "(… i direktivet)" parenthetical following a *regulation* citation must
    # not bind the regulation as a directive -- a "genomför" statement can never
    # target a regulation, so a non-directive resolution is rejected
    blocks = [{"text": [
        "förordning (EG) nr 1107/2006 av den 5 juli 2006 om rättigheter i "
        "samband med flygresor (jfr artikel 13.8 i direktivet)."]}]
    aliases = resolve_directives(blocks, _refparser())
    assert CELEX + "32006R1107" not in aliases.values()
