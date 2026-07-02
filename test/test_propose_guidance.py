"""Offline tests for the pure parsing helpers of the Track-B guidance proposer
(accommodanda.wiki.guidance_discover, wired as `lagen kommentar propose-guidance`).
The scraping itself is network-bound and exercised by hand; what is worth locking
is the CELEX derivation (ELI mapping + the consolidated-text exclusion), the
YAML-scalar quoting, and the link collection that shape the draft frontmatter."""

import lxml.html

from accommodanda.wiki import guidance_discover as pg


def test_celex_from_explicit_uri():
    assert pg.celex_from_href(
        "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32024R2847"
    ) == "32024R2847"
    assert pg.celex_from_href("...?uri=CELEX:32023R2854") == "32023R2854"


def test_celex_from_eli_maps_sector3_letter():
    assert pg.celex_from_href("https://eur-lex.europa.eu/eli/reg/2023/2854") \
        == "32023R2854"
    assert pg.celex_from_href("https://eur-lex.europa.eu/eli/dir/2018/2001") \
        == "32018L2001"


def test_celex_ignores_consolidated_text():
    # a leading-0 CELEX is a consolidated version, not the base act -- dropped so
    # the cross-check keys on the real act, not a stray consolidated link
    assert pg.celex_from_href("...?uri=CELEX%3A01996L0009-20190606") is None
    assert pg.celex_from_href("https://example.com/not-eurlex") is None


def test_scalar_quotes_only_when_needed():
    assert pg.yaml_scalar("Guidance on vehicle data, accompanying the Data Act") \
        == "Guidance on vehicle data, accompanying the Data Act"
    assert pg.yaml_scalar("Data Act — Factsheet") == "Data Act — Factsheet"
    # a colon would break `title: value`, so it must be quoted
    assert pg.yaml_scalar("FAQ: the Data Act") == '"FAQ: the Data Act"'


def test_library_items_are_absolute_deduped_and_ordered():
    html = """<a href="/en/library/faq-data-act">FAQ</a>
              <a href="https://digital-strategy.ec.europa.eu/en/library/faq-data-act#x">dup</a>
              <a href="/en/library/data-act-factsheet?foo=1">fs</a>
              <a href="/en/policies/data-act">not a library item</a>"""
    tree = lxml.html.fromstring(html)
    assert pg.library_items(tree, "https://digital-strategy.ec.europa.eu/en/policies/data-act") == [
        "https://digital-strategy.ec.europa.eu/en/library/faq-data-act",
        "https://digital-strategy.ec.europa.eu/en/library/data-act-factsheet",
    ]


def test_hub_urls_filters_subpages_and_normalizes_scheme():
    locs = [
        "http://digital-strategy.ec.europa.eu/en/policies/data-act",   # hub (http)
        "https://digital-strategy.ec.europa.eu/en/policies/data-act",  # dup (https)
        "https://digital-strategy.ec.europa.eu/en/policies/ai/sub",    # sub-page
        "https://digital-strategy.ec.europa.eu/en/library/faq",        # not a hub
        "https://digital-strategy.ec.europa.eu/en/policies/cyber-resilience-act",
    ]
    pattern = pg.GUIDANCE_SITES[0][1]
    assert pg.hub_urls(locs, pattern) == [
        "https://digital-strategy.ec.europa.eu/en/policies/data-act",
        "https://digital-strategy.ec.europa.eu/en/policies/cyber-resilience-act",
    ]


def test_index_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(pg, "INDEX_PATH", tmp_path / "guidance-index.json")
    assert pg.pages_for("32023R2854") == []          # no index yet
    pg.write_index({"32023R2854": ["https://x/en/policies/data-act"]})
    assert pg.pages_for("32023R2854") == ["https://x/en/policies/data-act"]
    assert pg.pages_for("39999R9999") == []          # indexed, but not this act


def test_direct_docs_uses_anchor_text_and_policy_url():
    html = """<a href="https://ec.europa.eu/newsroom/dae/redirection/document/122331">
                 FAQ - Cyber Resilience Act</a>
              <a href="/en/library/cyber-resilience-act">landing, not a doc</a>"""
    tree = lxml.html.fromstring(html)
    policy = "https://digital-strategy.ec.europa.eu/en/policies/cyber-resilience-act"
    assert pg.direct_docs(tree, policy) == [
        ("FAQ - Cyber Resilience Act", policy,
         "https://ec.europa.eu/newsroom/dae/redirection/document/122331"),
    ]
