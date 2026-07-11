"""The faceted-navigation data layer (accommodanda/lib/facets.py): per-source
bucket-key extraction and the tree/group scans over a hand-built catalog."""

from accommodanda.lib import catalog, facets
from accommodanda.lib.facets import Row


def row(uri, kind="", label="", title="", display=""):
    return Row(uri, catalog.local(uri), kind, label, title, display)


U = "https://lagen.nu/"


# --------------------------------------------------------------------------
# key extraction
# --------------------------------------------------------------------------

def test_sfs_initial_files_under_subject_not_designation():
    # the document-type word + SFS number + connector are stripped, so the law
    # files under its subject initial (lagen.nu's "börjar på A")
    assert facets._sfs_initial(row(U + "2008:1302",
        title="Lag (2008:1302) om avtal mellan Sverige och Isle of Man")) == "A"
    assert facets._sfs_initial(row(U + "2009:19",
        title="Förordning (2009:19) om lån till företag")) == "L"
    # an editorial /…/ marker before the title is ignored
    assert facets._sfs_initial(row(U + "2016:1024",
        title="/Rubriken upphör att gälla U:2026-11-20/\nLag (2016:1024) om "
              "fördelning av ansvar")) == "F"


def test_begrepp_initial():
    assert facets._begrepp_initial(row(U + "begrepp/Äganderätt", title="Äganderätt")) == "Ä"
    assert facets._begrepp_initial(row(U + "begrepp/123", title="123-metoden")) == "#"


def test_dv_court_maps_raw_avgoranden_to_their_court():
    assert facets._dv_court(row(U + "dom/nja/2011s357")) == "nja"   # referat
    # a raw avgörande is filed beside its referat: HDO -> Högsta domstolen (nja),
    # MMOD -> MÖD (mod), the kammarrätt codes -> rk; PBR/RHN get their own bucket
    assert facets._dv_court(row(U + "dom/HDO_B_10233_25")) == "nja"
    assert facets._dv_court(row(U + "dom/MMOD_M_14848_24")) == "mod"
    assert facets._dv_court(row(U + "dom/KST_1137_00")) == "rk"
    assert facets._dv_court(row(U + "dom/HVS_B_3108_02")) == "rh"
    assert facets._dv_court(row(U + "dom/PBR_00_126")) == "pbr"
    assert facets._dv_court(row(U + "dom/RHN_169_99")) == "rhn"
    # a genuinely unknown prefix still folds to 'övriga'
    assert facets._dv_court(row(U + "dom/ZZZ_1_25")) == "övriga"


def test_dv_year():
    assert facets._dv_year(row(U + "dom/nja/2011s357", label="NJA 2011 s. 357")) == "2011"
    # HFD target-number labels carry no 4-digit year -- the 2-digit suffix is it
    assert facets._dv_year(row(U + "dom/hfd/1017:25", label="HFD 1017-25")) == "2025"
    # ... even when the målnummer itself looks like a 16xx year ('HFD 1673-25')
    assert facets._dv_year(row(U + "dom/hfd/1673:25", label="HFD 1673-25")) == "2025"
    # raw avgöranden: year read from the uri segment, not the målnummer-laden
    # label. Trailing for most ('HDO B 2043-24' is case 2043, year 24=2024),
    # leading for the year-first courts (MDO, PBR); pivoted so 99 -> 1999.
    assert facets._dv_year(row(U + "dom/HDO_B_2043_24", label="HDO B 2043-24")) == "2024"
    assert facets._dv_year(row(U + "dom/MMOD_1781_26", label="MMOD 1781-26")) == "2026"
    assert facets._dv_year(row(U + "dom/MDO_2000_10", label="MDO 2000-10")) == "2000"
    assert facets._dv_year(row(U + "dom/PBR_00_126", label="PBR 00-126")) == "2000"
    assert facets._dv_year(row(U + "dom/RHN_169_99", label="RHN 169-99")) == "1999"


def test_forarbete_type_and_year():
    assert facets._fa_type(row(U + "prop/2020/21:22")) == "prop"
    assert facets._fa_year(row(U + "prop/2020/21:22")) == "2020"
    assert facets._fa_year(row(U + "sou/1997:157")) == "1997"


def test_eurlex_kind_and_year():
    # the type facet is the catalog's stored doctype, not re-derived from CELEX
    assert facets._catalog_kind(row(U + "ext/celex/32016R0679", kind="regulation")) == "regulation"
    assert facets._eu_year(row(U + "ext/celex/32016R0679")) == "2016"
    assert facets._eu_year(row(U + "ext/celex/61989CJ0074")) == "1989"


def test_foreskrift_series_and_year():
    assert facets._fs_series(row(U + "fffs/2013:10")) == "FFFS"
    assert facets._fs_year(row(U + "fffs/2013:10")) == "2013"


def test_slug_keeps_swedish_letters():
    assert facets._slug("Ö") == "ö"
    assert facets._slug("NJA – Högsta domstolen") == "nja-högsta-domstolen"
    assert facets._slug("#") == "-"


# --------------------------------------------------------------------------
# the scans over a built catalog
# --------------------------------------------------------------------------

def _catalog(tmp_path, rows):
    """rows are (uri, source, kind, label, title[, display]); `display` (the
    reader-facing heading the browse listing shows) defaults to the title -- the
    display_title result for an artifact with no short name/acronym."""
    con = catalog.connect(tmp_path / "cat.sqlite")
    con.executemany(
        "INSERT INTO documents (uri, source, kind, label, title, path, display) "
        "VALUES (?,?,?,?,?,'',?)",
        [(uri, src, kind, label, title, rest[0] if rest else title)
         for (uri, src, kind, label, title, *rest) in rows])
    con.commit()
    return con


def test_group_buckets_and_drops_corrigenda(tmp_path):
    con = _catalog(tmp_path, [
        (U + "ext/celex/32016R0679", "eurlex", "regulation", "32016R0679", "GDPR"),
        (U + "ext/celex/32022R2554", "eurlex", "regulation", "32022R2554", "DORA"),
        (U + "ext/celex/32016L0680", "eurlex", "directive", "32016L0680", "LED"),
        # a corrigendum -- must not appear as its own browse entry
        (U + "ext/celex/32011R0524R(01)", "eurlex", "regulation", "x", "rättelse"),
    ])
    buckets = facets.group(con, "eurlex")
    assert ("regulation", "2016") in buckets
    assert ("regulation", "2022") in buckets
    assert ("directive", "2016") in buckets
    assert all("R(01)" not in catalog.local(r.uri)
               for rows in buckets.values() for r in rows)


def test_tree_orders_buckets_and_picks_default(tmp_path):
    con = _catalog(tmp_path, [
        (U + "dom/nja/2011s357", "dv", "case", "NJA 2011 s. 357", ""),
        (U + "dom/nja/2024s10", "dv", "case", "NJA 2024 s. 10", ""),
        (U + "dom/ad/1993:100", "dv", "case", "AD 1993 nr 100", ""),
        (U + "dom/HDO_B_1_25", "dv", "case", "HDO B 1-25", ""),    # raw HD -> nja
        (U + "dom/ZZZ_1_25", "dv", "case", "ZZZ 1-25", ""),        # unknown -> övriga
    ])
    tree = facets.tree(con, "dv")
    assert tree["levels"] == ["Domstol", "År"]
    # curated court order puts NJA first; 'övriga' (the unknown id) trails
    keys = [b["key"] for b in tree["buckets"]]
    assert keys == ["nja", "ad", "övriga"]
    # NJA holds both referat plus the raw HD avgörande (2025), newest year first;
    # the default lands on the first leaf
    nja = tree["buckets"][0]
    assert nja["count"] == 3
    assert [c["key"] for c in nja["children"]] == ["2025", "2024", "2011"]
    assert tree["default"] == ["nja", "2025"]
    assert nja["label"] == "NJA – Högsta domstolen"


def test_documents_naturally_ordered_within_bucket(tmp_path):
    con = _catalog(tmp_path, [
        (U + "dom/nja/2024s10", "dv", "case", "NJA 2024 s. 10", ""),
        (U + "dom/nja/2024s2", "dv", "case", "NJA 2024 s. 2", ""),
        (U + "dom/nja/2024s9", "dv", "case", "NJA 2024 s. 9", ""),
    ])
    bucket = facets.group(con, "dv")[("nja", "2024")]
    assert [r.label for r in bucket] == [
        "NJA 2024 s. 2", "NJA 2024 s. 9", "NJA 2024 s. 10"]


def test_browse_view_attaches_leaf_documents(tmp_path):
    title = "Förordning (EU) 2016/679 om skydd (allmän dataskyddsförordning)"
    con = _catalog(tmp_path, [
        (U + "ext/celex/32016R0679", "eurlex", "regulation", "32016R0679", title,
         "Dataskyddsförordningen (GDPR)"),      # stored display = short name + acronym
        (U + "ext/celex/32016L0680", "eurlex", "directive", "32016L0680", "LED"),
    ])
    view = facets.browse_view(con, "eurlex")
    reg = next(b for b in view["buckets"] if b["key"] == "regulation")
    leaf = reg["children"][0]                       # the 2016 year bucket
    assert leaf["key"] == "2016" and leaf["children"] is None
    doc = leaf["documents"][0]
    assert doc["uri"] == U + "ext/celex/32016R0679"
    assert doc["url"] == "/celex/32016R0679"     # eurlex's public /celex/ grammar
    # the listing handle is the stored reader-facing heading -- the same display
    # the page and search show (catalog.display_title), not the bare CELEX
    assert doc["display"] == "Dataskyddsförordningen (GDPR)"
    assert doc["display"] != "32016R0679"
    # non-leaf (primary) nodes carry no documents
    assert reg.get("documents") is None


def test_tree_single_level_letters(tmp_path):
    con = _catalog(tmp_path, [
        (U + "1962:700", "sfs", "law", "SFS 1962:700", "Brottsbalk (1962:700)"),
        (U + "2008:1302", "sfs", "law", "SFS 2008:1302",
         "Lag (2008:1302) om avtal"),    # -> 'A' (avtal)
    ])
    tree = facets.tree(con, "sfs")
    assert tree["levels"] == ["Bokstav"]
    assert [b["key"] for b in tree["buckets"]] == ["A", "B"]
    assert all(b["children"] is None for b in tree["buckets"])
    assert tree["default"] == ["A"]
