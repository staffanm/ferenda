"""lib/mdtext: the artifact -> markdown transform behind /document?format=md
and the MCP get_document tool. Small constructed artifacts per source shape --
the same node vocabulary docs/api/README.md documents."""

from ferenda.lib import mdtext, text


def test_sfs_shape_reads_as_a_statute():
    art = {
        "uri": "https://lagen.nu/1975:635",
        "metadata": {"properties": {"dcterms:title": "Räntelag (1975:635)"}},
        "structure": [
            {"type": "kapitel", "ordinal": "1", "children": [
                {"type": "rubrik", "level": 1, "text": ["1 kap. Inledning"]},
                {"type": "rubrik", "level": 2, "text": ["Underrubrik"]},
                {"type": "paragraf", "ordinal": "1", "children": [
                    {"type": "stycke", "beteckning": "1 §", "id": "K1P1S1",
                     "text": ["Ränta enligt ",
                              {"predicate": "dcterms:references",
                               "uri": "https://lagen.nu/1975:635#P6",
                               "text": "6 §"},
                              " räntelagen."]},
                    {"type": "stycke", "id": "K1P1S2",
                     "text": ["Andra stycket."],
                     "children": [
                         {"type": "punkt", "ordinal": "1", "text": ["ett,"]},
                         {"type": "punkt", "ordinal": "2", "text": ["två."]}]},
                ]},
            ]},
        ],
        "amendments": [
            {"uri": "https://lagen.nu/1980:100",
             "properties": {"dcterms:identifier": "SFS 1980:100",
                            "rpubl:andrar": "ändr. 1 §",
                            "rpubl:ikrafttradandedatum": "1981-01-01"},
             "forarbeten": ["Prop. 1979/80:1"],
             "content": [{"type": "stycke", "id": "L1980:100S1",
                          "text": ["Denna lag träder i kraft."]}]}],
    }
    md = mdtext.document_markdown(art)
    assert md.startswith("# Räntelag (1975:635)")
    assert "## 1 kap. Inledning" in md
    assert "### Underrubrik" in md
    assert "**1 §** Ränta enligt [6 §](https://lagen.nu/1975:635#P6) räntelagen." in md
    assert "\n\nAndra stycket." in md          # a later stycke has no bold marker
    assert "1. ett," in md and "2. två." in md
    assert "## Ändringar" in md
    assert "### SFS 1980:100" in md
    assert "- Omfattning: ändr. 1 §" in md
    assert "- Ikraftträder: 1981-01-01" in md
    assert "- Förarbeten: Prop. 1979/80:1" in md
    assert "Denna lag träder i kraft." in md


def test_sfs_presented_consolidation_replaces_the_base_structure():
    art = {
        "uri": "https://lagen.nu/1998:204",
        "metadata": {"properties": {"dcterms:title": "Personuppgiftslag"}},
        "structure": [{"type": "paragraf", "children": [
            {"type": "stycke", "beteckning": "1 §", "id": "P1S1",
             "text": ["Som enacted."]}]}],
        "consolidations": [
            {"konsolideradTom": "https://lagen.nu/2003:466",
             "structure": [{"type": "paragraf", "children": [
                 {"type": "stycke", "beteckning": "1 §", "id": "P1S1",
                  "text": ["Som konsoliderad."]}]}]}],
    }
    md = mdtext.document_markdown(art)
    assert "Som konsoliderad." in md and "Som enacted." not in md


def test_eurlex_shape_reads_as_an_eu_act():
    art = {
        "uri": "https://lagen.nu/celex/32016R0679",
        "title": "Europaparlamentets och rådets förordning (EU) 2016/679",
        "structure": [
            {"type": "citation", "text": ["med beaktande av fördraget,"]},
            {"type": "recital", "num": "1", "text": ["Skyddet är en rättighet."]},
            {"type": "heading", "level": 1, "label": "KAPITEL I",
             "text": ["Allmänna bestämmelser"], "children": [
                 {"type": "article", "num": "2", "id": "2",
                  "label": "Artikel 2", "text": ["Tillämpningsområde"],
                  "children": [
                      {"type": "paragraph", "num": "2",
                       "text": ["Förordningen gäller inte behandling som"],
                       "children": [
                           {"type": "point", "num": "a",
                            "text": ["rör unionsrätten,"]},
                           {"type": "point", "num": "i", "depth": 2,
                            "text": ["en nästlad punkt."]}]},
                      {"type": "stycke", "num": "2",
                       "text": ["Ett onumrerat stycke."]}]}]},
            {"type": "note", "num": "1", "text": ["EUT C 229, s. 90."]},
        ],
    }
    md = mdtext.document_markdown(art)
    assert md.startswith("# Europaparlamentets och rådets förordning (EU) 2016/679")
    assert "(1) Skyddet är en rättighet." in md
    assert "## Kapitel I – Allmänna bestämmelser" in md    # the label de-shouted
    assert "### Artikel 2 – Tillämpningsområde" in md
    assert "2. Förordningen gäller inte behandling som" in md
    assert "- a) rör unionsrätten," in md
    assert "  - i) en nästlad punkt." in md                # depth 2 indents
    assert "\n\nEtt onumrerat stycke." in md               # stycke num not printed
    assert "(1) EUT C 229, s. 90." in md


def test_forarbete_shape_reads_as_a_proposition():
    art = {
        "uri": "https://lagen.nu/prop/2017/18:199",
        "identifier": "Prop. 2017/18:199",
        "title": "En stärkt minoritetspolitik",
        "structure": [
            {"type": "avsnitt", "level": 1, "num": "2", "id": "sec2",
             "text": ["2 Lagtext"], "children": [
                 {"type": "ruta", "page": 10,
                  "text": ["Härigenom föreskrivs att 1 § ska ha följande lydelse."]},
                 {"type": "kapitel", "num": "8", "text": ["8 kap."]},
                 {"type": "paragraf", "num": "1", "text": ["1 §"]},
                 {"type": "stycke", "page": 10, "text": ["Paragraftexten."]},
                 {"type": "fotnot", "page": 10,
                  "text": ["1 Senaste lydelse 2010:865."]},
                 {"type": "tabell", "text": [], "children": [
                     {"type": "rad", "th": True,
                      "cells": [["Nuvarande lydelse"], ["Föreslagen lydelse"]]},
                     {"type": "rad",
                      "cells": [["gammal | text"], ["ny text"]]}]},
             ]},
        ],
    }
    md = mdtext.document_markdown(art)
    assert md.startswith("# Prop. 2017/18:199: En stärkt minoritetspolitik")
    assert "## 2 Lagtext" in md
    assert "> Härigenom föreskrivs" in md
    assert "**8 kap.**" in md and "**1 §**" in md
    assert "*1 Senaste lydelse 2010:865.*" in md
    assert "| Nuvarande lydelse | Föreslagen lydelse |" in md
    assert "| --- | --- |" in md
    assert "| gammal \\| text | ny text |" in md      # a pipe in a cell escapes


def test_a_spanning_cell_is_written_out_into_the_rows_it_covers():
    """A pipe table has no rowspan. NIS2 bilaga I writes "1. Energi" once with
    ROWSPAN="17"; printed as-is, the 16 rows it covers put their third-column
    cell in column 1, under the sector name it is not."""
    md = mdtext.node_markdown({"type": "tabell", "text": [], "children": [
        {"type": "rad", "th": True,
         "cells": [["Sektor"], ["Delsektor"], ["Typ av entitet"]]},
        {"type": "rad", "rowspan": [2, 2, 1],
         "cells": [["1. Energi"], ["a) Elektricitet"], ["Elföretag"]]},
        {"type": "rad", "cells": [["Producenter"]]},
        {"type": "rad", "cells": [["2. Transporter"], ["a) Luft"], ["Flygplatser"]]},
    ]})
    assert md.split("\n") == [
        "| Sektor | Delsektor | Typ av entitet |",
        "| --- | --- | --- |",
        "| 1. Energi | a) Elektricitet | Elföretag |",
        "|  |  | Producenter |",
        "| 2. Transporter | a) Luft | Flygplatser |"]


def test_a_colspan_widens_the_row_it_sits_in():
    md = mdtext.node_markdown({"type": "tabell", "text": [], "children": [
        {"type": "rad", "colspan": [2, 1], "cells": [["Båda"], ["Tredje"]]},
        {"type": "rad", "cells": [["a"], ["b"], ["c"]]},
    ]})
    assert md.split("\n")[-2:] == ["| Båda |  | Tredje |", "| a | b | c |"]


def test_generic_shape_numbered_stycken_and_footnotes():
    # the dv/hudoc shape: rubrik levels, ordinal-numbered stycken, footnotes
    art = {
        "uri": "https://lagen.nu/dom/nja/2015s899",
        "label": "NJA 2015 s. 899",
        "metadata": {"sammanfattning": "Fråga om ansvar."},
        "structure": [
            {"type": "domskal", "children": [
                {"type": "rubrik", "level": 1, "text": ["Domskäl"]},
                {"type": "stycke", "ordinal": "1", "text": ["Första punkten."]},
                {"type": "stycke", "text": ["Onumrerad."]}]},
        ],
        "footnotes": [{"num": "1", "text": ["En slutnot."]}],
    }
    md = mdtext.document_markdown(art)
    assert md.startswith("# NJA 2015 s. 899")
    assert "Fråga om ansvar." in md          # the abstract rides along
    assert "## Domskäl" in md
    assert "1. Första punkten." in md
    assert "## Noter" in md and "(1) En slutnot." in md


def test_unknown_node_type_degrades_to_its_text_and_children():
    art = {"uri": "https://example/x",
           "structure": [{"type": "framtida", "text": ["Egen prosa."],
                          "children": [{"type": "stycke", "text": ["Barnet."]}]}]}
    md = mdtext.document_markdown(art)
    assert "Egen prosa." in md and "Barnet." in md


def test_node_markdown_renders_one_subtree():
    # the MCP pinpoint read: text.fragment_node picks the subtree,
    # node_markdown renders it
    art = {"uri": "https://lagen.nu/1962:700",
           "structure": [
               {"type": "paragraf", "id": "K3P1", "children": [
                   {"type": "stycke", "beteckning": "1 §", "id": "K3P1S1",
                    "text": ["Den som berövar annan livet döms för mord."]}]},
               {"type": "paragraf", "id": "K3P2", "children": [
                   {"type": "stycke", "beteckning": "2 §", "id": "K3P2S1",
                    "text": ["Dråp."]}]}]}
    md = mdtext.node_markdown(text.fragment_node(art, "K3P1"))
    assert md == "**1 §** Den som berövar annan livet döms för mord."
    assert text.fragment_node(art, "P999") is None


def test_document_markdown_title_falls_back_to_the_callers():
    # a kommentar artifact names itself no way at all -- the caller's catalog
    # title is the only name available
    art = {"uri": "https://lagen.nu/1915:218/kommentar",
           "structure": [{"type": "stycke", "text": ["Kommentaren."]}]}
    md = mdtext.document_markdown(art, title="Kommentar till avtalslagen")
    assert md.startswith("# Kommentar till avtalslagen")
    # the artifact's own name still wins over the fallback
    art["title"] = "Egen titel"
    assert mdtext.document_markdown(art, title="Katalogtitel") \
        .startswith("# Egen titel")


def test_inline_runs_collapse_whitespace_and_escape_link_labels():
    assert mdtext._inline(["rad\nbruten  text"]) == "rad bruten text"
    assert mdtext._inline([{"uri": "https://x", "text": "a [b]"}]) \
        == "[a \\[b\\]](https://x)"
