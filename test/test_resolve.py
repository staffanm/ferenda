"""Hermetic tests for the ⌘K resolver (lib.resolve) and the named-rättsfall row
parser (dv.namedcases.parse_rows). These map a typed query straight to a precise
resource URI -- the thing full-text search can't do, because a nickname/abbr or a
pinpoint appears nowhere in the document text. They run against the committed
curated datasets (sfs/data/namedlaws.json, eurlex/data/namedacts.json,
dv/data/namedcases.json); no network, no OpenSearch, no catalog.
"""

from accommodanda.dv import namedcases
from accommodanda.lib import resolve

# --- SFS: nickname/abbreviation + chapter/§ pinpoint, in ⌘K (law-first) order

def test_sfs_nickname_plus_bare_section():
    # "avtalslagen 36" -- the terse law-first order people actually type
    assert resolve.resolve_sfs("avtalslagen 36") == "https://lagen.nu/1915:218#P36"


def test_sfs_nickname_plus_section_with_marker():
    assert resolve.resolve_sfs("avtalslagen 36 §") == "https://lagen.nu/1915:218#P36"


def test_sfs_abbreviation_colon_pinpoint():
    # "BrB 12:1" -- abbreviation + chapter:section
    assert resolve.resolve_sfs("BrB 12:1") == "https://lagen.nu/1962:700#K12P1"
    assert resolve.resolve_sfs("brb 12:1") == "https://lagen.nu/1962:700#K12P1"


def test_sfs_canonical_citation_order_also_resolves():
    # the Swedish-citation order (pinpoint first) the grammar natively parses
    assert (resolve.resolve_sfs("12 kap. 1 § brottsbalken")
            == "https://lagen.nu/1962:700#K12P1")


def test_sfs_bare_nickname_is_the_law_root():
    assert resolve.resolve_sfs("miljöbalken") == "https://lagen.nu/1998:808"


def test_sfs_unknown_term_does_not_resolve():
    # a plain content word is for full-text, not the resolver
    assert resolve.resolve_sfs("skadestånd") is None


def test_sfs_relative_citation_without_base_does_not_mint_sentinel():
    # "3 § skadestånd" carries a relative pinpoint but no resolvable base
    # law -- it must return None, never a garbage URI under the "query"
    # placeholder basefile (regression: it used to return .../query#P3).
    assert resolve.resolve_sfs("3 § skadestånd") is None
    assert resolve.resolve_sfs("5 §") is None


# --- SFS: bare number-shaped queries (the probes API/MCP clients send) ------

def test_sfs_bare_number_is_the_law_root():
    assert resolve.resolve_sfs("2022:818") == "https://lagen.nu/2022:818"
    assert resolve.resolve_sfs("SFS 2022:818") == "https://lagen.nu/2022:818"
    assert resolve.resolve_sfs("sfs 2022:818") == "https://lagen.nu/2022:818"


def test_sfs_bare_number_plus_pinpoint():
    assert resolve.resolve_sfs("1962:700 3:1") == "https://lagen.nu/1962:700#K3P1"
    assert resolve.resolve_sfs("SFS 1915:218 36 §") == "https://lagen.nu/1915:218#P36"


def test_sfs_page_number_law_slugs_like_the_corpus_basefile():
    # "1904:48 s.1" must mint the corpus basefile slug 1904:48_s.1 -- NOT the
    # legacy COIN template's 1904:48_s._1, which no catalog document ever had
    # (regression: lagrum_uri used to emit _s._1, orphaning every citation to
    # the 54 page-number laws)
    assert resolve.resolve_sfs("1904:48 s.1") == "https://lagen.nu/1904:48_s.1"
    assert resolve.resolve_sfs("1904:48 s. 1 3 §") == "https://lagen.nu/1904:48_s.1#P3"
    assert (resolve.resolve_sfs("lagen (1904:48 s.1)")
            == "https://lagen.nu/1904:48_s.1")
    # a nickname whose dataset id carries the page suffix takes the same slug
    assert resolve.resolve_sfs("lösöresköpslagen") == "https://lagen.nu/1845:50_s.1"


def test_sfs_chapter_colon_section_alone_is_not_a_number():
    # "12:1" is a chapter:section pinpoint with no law -- a 4-digit year is
    # what makes a bare token read as an SFS number
    assert resolve.resolve_sfs("12:1") is None


# --- SFS: each query is independent -- no state leaks between queries -------

def test_sfs_samma_lag_does_not_leak_from_earlier_query():
    # resolving a citation must not leave a "current law" that a *later*,
    # unrelated query's "samma lag" picks up. Two sequential queries are
    # independent; the second must not inherit brottsbalken from the first.
    assert (resolve.resolve_sfs("12 kap. 1 § brottsbalken")
            == "https://lagen.nu/1962:700#K12P1")
    assert resolve.resolve_sfs("5 § samma lag") is None


def test_sfs_learned_alias_does_not_leak_from_earlier_query():
    # "7 § hittepålagen (1999:123)" teaches the parser that "hittepålagen"
    # means 1999:123 -- but only for that query. A later bare "9 § hittepålagen"
    # must not resolve off the leaked alias (it names no known law).
    assert (resolve.resolve_sfs("7 § hittepålagen (1999:123)")
            == "https://lagen.nu/1999:123#P7")
    assert resolve.resolve_sfs("9 § hittepålagen") is None


# --- EU: short name + optional article -------------------------------------

def test_eu_shortname_plus_article():
    assert (resolve.resolve_eu("GDPR art 32")
            == "https://lagen.nu/ext/celex/32016R0679#32")


def test_eu_swedish_label_and_artikel():
    # a curated Swedish nickname (the act has no naming parenthetical in its
    # title, so the nickname stays in the dataset) + a Swedish "artikel" tail
    assert (resolve.resolve_eu("DORA-förordningen artikel 5")
            == "https://lagen.nu/ext/celex/32022R2554#5")


def test_eu_bare_shortname_is_the_act_root():
    assert resolve.resolve_eu("IPRED") == "https://lagen.nu/ext/celex/32004L0048"


def test_eu_unknown_does_not_resolve():
    assert resolve.resolve_eu("förordningen om något") is None


def test_eu_abbr_and_label_resolve_to_same_act():
    # namedacts.json splits acronym (abbr) from the Swedish short names (label)
    # like namedlaws.json; both must reach the same CELEX. Since d216ecc1 that
    # includes acts whose short title also lives in the official title's
    # parenthesis (GDPR) -- the labels are listed explicitly so the citation
    # engine can link them in running text.
    dora = "https://lagen.nu/ext/celex/32022R2554"
    assert resolve.resolve_eu("DORA") == dora                 # abbr
    assert resolve.resolve_eu("DORA-förordningen") == dora    # label
    assert resolve.resolve_eu("DORA art 5") == dora + "#5"    # abbr + article
    gdpr = "https://lagen.nu/ext/celex/32016R0679"
    assert resolve.resolve_eu("GDPR") == gdpr                 # abbr
    assert resolve.resolve_eu("dataskyddsförordningen") == gdpr  # label


# --- DV: case nickname ------------------------------------------------------

def test_dv_case_nickname():
    assert (resolve.resolve_dv("Instagrambilden")
            == "https://lagen.nu/dom/nja/2020s273")
    assert (resolve.resolve_dv("instagrambilden")
            == "https://lagen.nu/dom/nja/2020s273")


def test_dv_unknown_nickname_does_not_resolve():
    assert resolve.resolve_dv("Inget riktigt rättsfall") is None


# --- the unified entry point -----------------------------------------------

def test_resolve_dispatches_and_tags_source():
    assert resolve.resolve("avtalslagen 36") == [
        {"uri": "https://lagen.nu/1915:218#P36", "source": "sfs"}]
    assert resolve.resolve("GDPR art 32") == [
        {"uri": "https://lagen.nu/ext/celex/32016R0679#32", "source": "eurlex"}]
    assert resolve.resolve("Instagrambilden") == [
        {"uri": "https://lagen.nu/dom/nja/2020s273", "source": "dv"}]


def test_resolve_empty_is_empty():
    assert resolve.resolve("") == []
    assert resolve.resolve("   ") == []


# --- the named-rättsfall PDF row parser (pure, over laid-out text) ----------

# a faithful slice of `pdftotext -layout` output: title, column header, then
# rows -- a plain one, a roman-numeral split, a modern row with a mål-nr column,
# and an unassigned-page ("s. xxx") row that must carry no URI.
SAMPLE = """\
       NAMNGIVNA RÄTTSFALL FRÅN HÖGSTA DOMSTOLEN
                A                                   B                    C
1    NJA             NAMN                                       MÅL NR
2    1874 s. 115     Vägstenarna
1080 2020 s. 273     Instagrambilden
23   1945 s. 440 I   Lustjakten Itaka
1588 2025 s. xxx     Minnesstunderna                                T 5499-24
"""


def test_namedcases_parse_rows_skips_title_and_header():
    rows = namedcases.parse_rows(SAMPLE)
    assert [r["namn"] for r in rows] == [
        "Vägstenarna", "Instagrambilden", "Lustjakten Itaka", "Minnesstunderna"]


def test_namedcases_parse_rows_mints_uri_from_referat():
    rows = {r["namn"]: r for r in namedcases.parse_rows(SAMPLE)}
    assert rows["Instagrambilden"]["referat"] == "NJA 2020 s. 273"
    assert rows["Instagrambilden"]["uri"] == "https://lagen.nu/dom/nja/2020s273"
    # the roman-numeral volume is kept in the referat
    assert rows["Lustjakten Itaka"]["referat"] == "NJA 1945 s. 440 I"


def test_namedcases_parse_rows_reads_malnr_column():
    rows = {r["namn"]: r for r in namedcases.parse_rows(SAMPLE)}
    assert rows["Minnesstunderna"]["malnr"] == "T 5499-24"


def test_namedcases_unassigned_page_has_no_uri():
    # "s. xxx": the NJA page isn't set yet, so there's no canonical URI to mint
    rows = {r["namn"]: r for r in namedcases.parse_rows(SAMPLE)}
    assert rows["Minnesstunderna"]["uri"] is None
