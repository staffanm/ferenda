"""Hermetic tests for the ⌘K resolver (lib.resolve) and the named-rättsfall row
parser (dv.namedcases.parse_rows). These map a typed query straight to a precise
resource URI -- the thing full-text search can't do, because a nickname/abbr or a
pinpoint appears nowhere in the document text. They run against the committed
curated datasets (sfs/data/namedlaws.json, eurlex/data/namedacts.json,
dv/data/namedcases.json); no network, no OpenSearch, no catalog.
"""

from accommodanda.lib import resolve
from accommodanda.dv import namedcases


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
    # namedacts.json splits acronym (abbr) from a genuinely-informal name (label)
    # like namedlaws.json; both must reach the same CELEX
    dora = "https://lagen.nu/ext/celex/32022R2554"
    assert resolve.resolve_eu("DORA") == dora                 # abbr
    assert resolve.resolve_eu("DORA-förordningen") == dora    # label
    assert resolve.resolve_eu("DORA art 5") == dora + "#5"    # abbr + article


def test_eu_extractable_label_pruned_abbr_still_resolves():
    # an act whose Swedish short title lives in the official title's parenthesis
    # carries only its acronym here (the name is extracted from the title later)
    assert resolve.resolve_eu("GDPR") == "https://lagen.nu/ext/celex/32016R0679"
    assert resolve.resolve_eu("dataskyddsförordningen") is None


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
