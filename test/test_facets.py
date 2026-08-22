"""The faceted-navigation data layer (accommodanda/lib/facets.py): per-source
bucket-key extraction and the tree/group scans over a hand-built catalog."""

import json

from accommodanda.lib import catalog, facets
from accommodanda.lib.facets import Row


def row(uri, kind="", label="", title="", display=""):
    return Row(uri, catalog.local(uri), kind, label, title, display)


U = "https://lagen.nu/"


# --------------------------------------------------------------------------
# key extraction
# --------------------------------------------------------------------------

def test_ra_referat_files_under_the_hfd_bucket():
    # Regeringsrätten (RÅ) is the pre-2011 Högsta förvaltningsdomstolen, one
    # court renamed -- its referat share the HFD bucket (R1)
    assert facets._dv_court(row(U + "dom/ra/2009/ref/90")) == "hfd"
    assert facets._dv_court(row(U + "dom/hfd/2011/ref/4")) == "hfd"
    assert facets._dv_year(row(U + "dom/ra/2009/ref/90",
                               label="RÅ 2009 ref. 90")) == "2009"


def test_hd_verdict_without_referat_files_under_nja_with_its_date_year():
    # a not-yet-published HD verdict uses the 'dom/{slug}/{malnr}/{date}' shape;
    # its slug 'hd' must land in the NJA bucket, not övriga, and its year comes
    # from the trailing avgörandedatum (R2)
    v = row(U + "dom/hd/Ö4337-25/2026-07-14")
    assert facets._dv_court(v) == "nja"
    assert facets._dv_year(v) == "2026"
    # an HFD verdict keeps its own bucket but likewise dates from the segment
    assert facets._dv_year(row(U + "dom/hfd/1889-24/2024-05-02")) == "2024"


def test_every_verdict_slug_has_a_bucket():
    # drift guard: every COURT_URI_SLUG the minter can emit must map to a bucket
    from accommodanda.lib.casenaming import COURT_URI_SLUG
    assert set(COURT_URI_SLUG.values()) <= set(facets.VERDICT_BUCKET)


def test_dv_cases_sort_by_referat_number_not_popular_name():
    # within a court+year bucket, order by the NJA number, not the editor's
    # popular name (R3): s. 5 before s. 1021, regardless of the title
    early = row(U + "dom/nja/2019s5", label="NJA 2019 s. 5", title="Zebran")
    late = row(U + "dom/nja/2019s1021", label="NJA 2019 s. 1021",
               title="Apan")
    assert sorted([late, early], key=facets._id_doc_sort) == [early, late]


def test_eu_only_latest_corrected_revision_lists():
    # base + (01) + (02) of the same CELEX collapse to the highest revision (E2)
    base = row(U + "ext/celex/12019W/TXT")
    r1 = row(U + "ext/celex/12019W/TXT(01)")
    r2 = row(U + "ext/celex/12019W/TXT(02)")
    other = row(U + "ext/celex/32016R0679")
    kept = {r.local for r in facets._keep_latest_eu_revision([base, r1, r2, other])}
    assert kept == {"ext/celex/12019W/TXT(02)", "ext/celex/32016R0679"}


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
    # an order files with the judgments; an opinion that surfaces stands on its own
    assert facets._eu_kind(row(U + "x", kind="order")) == "judgment"
    assert facets._eu_kind(row(U + "x", kind="opinion")) == "opinion"


def test_eurlex_treaty_groups_by_family_not_year():
    # a treaty's second facet axis is its family (from the CELEX letter), not the
    # year; other types still bucket by year (E1)
    assert facets._treaty_family("12016M/TXT") == "teu"
    assert facets._treaty_family("12016E/TXT") == "tfeu"
    assert facets._treaty_family("12016P/TXT") == "charter"
    assert facets._treaty_family("12016ME/TXT") == "combined"
    assert facets._treaty_family("11997D/TXT") == "amending"
    assert facets._treaty_family("11994N/TXT") == "accession"
    assert facets._treaty_family("12020W/TXT") == "withdrawal"
    assert facets._eu_second(row(U + "ext/celex/12016M/TXT", kind="treaty")) == "teu"
    assert facets._eu_second(row(U + "ext/celex/32016R0679", kind="regulation")) == "2016"
    # families order by the curated reading order; years newest-first
    assert facets._eu_second_order(["accession", "teu", "charter"]) == [
        "teu", "charter", "accession"]
    assert facets._eu_second_order(["2016", "2019", "2011"]) == ["2019", "2016", "2011"]


def test_eurlex_treaty_bucket_sorts_newest_first():
    # the current consolidated version tops its family (E1)
    old = row(U + "ext/celex/11992M/TXT", kind="treaty")
    new = row(U + "ext/celex/12016M/TXT", kind="treaty")
    assert sorted([old, new], key=facets._eu_doc_sort) == [new, old]


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
    assert nja["label"] == "Högsta domstolen (NJA)"


def test_documents_naturally_ordered_within_bucket(tmp_path):
    con = _catalog(tmp_path, [
        (U + "dom/nja/2024s10", "dv", "case", "NJA 2024 s. 10", ""),
        (U + "dom/nja/2024s2", "dv", "case", "NJA 2024 s. 2", ""),
        (U + "dom/nja/2024s9", "dv", "case", "NJA 2024 s. 9", ""),
    ])
    bucket = facets.group(con, "dv")[("nja", "2024")]
    assert [r.label for r in bucket] == [
        "NJA 2024 s. 2", "NJA 2024 s. 9", "NJA 2024 s. 10"]


def _dir(tmp_path, name):
    (tmp_path / name).mkdir()
    return tmp_path / name


def test_numbered_series_list_by_number_not_by_subject(tmp_path):
    """A bucket of a numbered series is scanned by its numbers, so it lists by
    the identifier the entry prints, naturally ordered -- :2 before :11.
    Förarbeten, föreskrifter, ställningstaganden, myndighetsavgöranden and EDPB
    guidance listed alphabetically by subject instead, which put Prop.
    2025/26:207 ("Aktivitetskrav…") at the head of the bucket and Prop.
    2025/26:2 nowhere near it."""
    con = _catalog(tmp_path, [
        (U + "prop/2025/26:11", "forarbete", "prop", "Prop. 2025/26:11", "Anonyma vittnen"),
        (U + "prop/2025/26:2", "forarbete", "prop", "Prop. 2025/26:2", "Öppna data"),
        (U + "prop/2025/26:107", "forarbete", "prop", "Prop. 2025/26:107", "Bolag och brott"),
    ])
    assert [r.label for r in facets.group(con, "forarbete")[("prop", "2025")]] \
        == ["Prop. 2025/26:2", "Prop. 2025/26:11", "Prop. 2025/26:107"]

    con = _catalog(_dir(tmp_path, "fs"), [
        (U + "affs/2025:2", "foreskrift", "affs", "AFFS 2025:2", "Statsbidrag"),
        (U + "affs/2025:1", "foreskrift", "affs", "AFFS 2025:1", "Uppgiftsskyldighet"),
    ])
    assert [r.label for r in facets.group(con, "foreskrift")[("AFFS", "2025")]] \
        == ["AFFS 2025:1", "AFFS 2025:2"]

    con = _catalog(_dir(tmp_path, "rs"), [
        (U + "rs/skv/8-2895-2025", "rs", "skv",
         "Skatteverkets ställningstagande dnr 8-2895-2025", "Avdrag för ränta"),
        (U + "rs/skv/8-441-2025", "rs", "skv",
         "Skatteverkets ställningstagande dnr 8-441-2025", "Alkoholskatt"),
    ])
    assert [r.label for r in facets.group(con, "rs")[("skv", "2025")]] \
        == ["Skatteverkets ställningstagande dnr 8-441-2025",
            "Skatteverkets ställningstagande dnr 8-2895-2025"]

    con = _catalog(_dir(tmp_path, "avg"), [
        (U + "avg/arn/1991-5452", "avg", "arn", "ARN 1991-5452", "Övrigt"),
        (U + "avg/arn/1991-4398", "avg", "arn", "ARN 1991-4398", "Резание"),
    ])
    assert [r.label for r in facets.group(con, "avg")[("arn", "1991")]] \
        == ["ARN 1991-4398", "ARN 1991-5452"]

    # an EDPB title carries the digits of the act it interprets, and the subject
    # order sorted on those: "Riktlinjer 3/2018" about förordning (EU) 2016/679
    # sorted on 1679, landing between 2/2019 and 1/2019
    con = _catalog(_dir(tmp_path, "guidance"), [
        (U + "guidance/edpb/riktlinjer/2-2019", "guidance", "riktlinjer",
         "Riktlinjer 2/2019",
         "Riktlinjer 2/2019 om artikel 6.1 b i förordning (EU) 2016/679"),
        (U + "guidance/edpb/riktlinjer/1-2019", "guidance", "riktlinjer",
         "Riktlinjer 1/2019", "Riktlinjer 1/2019 om uppförandekoder"),
    ])
    assert [r.label for r in
            facets.group(con, "guidance")[("edpb", "riktlinjer", "2019")]] \
        == ["Riktlinjer 1/2019", "Riktlinjer 2/2019"]


def test_a_subject_bucket_still_lists_by_subject(tmp_path):
    """The counterpart: sfs and begrepp file under a subject initial, so the
    subject -- not the SFS number -- is what orders the bucket."""
    con = _catalog(tmp_path, [
        (U + "2008:1302", "sfs", "lag", "2008:1302",
         "Lag (2008:1302) om avtal mellan Sverige och Isle of Man"),
        (U + "1949:105", "sfs", "lag", "1949:105", "Tryckfrihetsförordning (1949:105)"),
        (U + "2018:1197", "sfs", "lag", "2018:1197",
         "Lag (2018:1197) om Förenta nationernas konvention om barnets rättigheter"),
    ])
    buckets = facets.group(con, "sfs")
    assert [r.label for r in buckets[("A",)]] == ["2008:1302"]
    assert [r.label for r in buckets[("F",)]] == ["2018:1197"]


def test_browse_view_attaches_documents_to_a_leaf_above_the_last_level(tmp_path):
    """guidance is the only three-level scheme (Utgivare -> Serie -> År), and
    its År level carries `only_above`, so a small utgivare's *series* bucket is
    a leaf while its rows are still filed under (utgivare, serie, år).

    Looking the leaf up by its own key path found nothing, because that path is
    two elements and `group`'s keys are three. Every guidance browse page
    rendered "Inga dokument." beside a facet rail that counted them correctly,
    and no other source noticed: with two levels a leaf's path is the whole key.
    """
    con = _catalog(tmp_path, [
        (U + "guidance/edpb/riktlinjer/05-2020", "guidance", "riktlinjer",
         "Riktlinjer 05/2020", "Riktlinjer 05/2020 om samtycke"),
        (U + "guidance/edpb/riktlinjer/03-2019", "guidance", "riktlinjer",
         "Riktlinjer 03/2019", "Riktlinjer 03/2019 om videoövervakning"),
        (U + "guidance/edpb/rekommendationer/01-2020", "guidance",
         "rekommendationer", "Rekommendation 01/2020",
         "Rekommendation 01/2020 om överföringar"),
    ])
    view = facets.browse_view(con, "guidance")
    edpb = next(b for b in view["buckets"] if b["key"] == "edpb")
    assert edpb["count"] == 3
    serier = {c["key"]: c for c in edpb["children"]}
    # the series buckets are the leaves: 3 documents is far below the År
    # level's only_above, so no year bucket is built under them
    assert serier["riktlinjer"]["children"] is None
    assert serier["riktlinjer"]["count"] == 2
    assert [d["display"] for d in serier["riktlinjer"]["documents"]] == [
        "Riktlinjer 03/2019 om videoövervakning",
        "Riktlinjer 05/2020 om samtycke"]
    assert len(serier["rekommendationer"]["documents"]) == 1
    # the utgivare above them is not a leaf and carries no documents of its own
    assert "documents" not in edpb


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


def test_kind_labels_name_every_forarbete_type():
    # bet/pm/rskr were missing from the forarbete scheme, so the TYP facet on
    # /forarbete/ and /sok/ printed the raw catalog key (N4)
    kl = facets.kind_labels()
    assert kl["bet"] == "Betänkanden"
    assert kl["pm"] == "Promemorior"
    assert kl["rskr"] == "Riksdagsskrivelser"
    assert kl["prop"] == "Propositioner"


def test_kind_labels_cover_every_kind_axis_key():
    # the flat map the search facets use is derived from SCHEMES, so no bucket a
    # browse page can show is nameless on the search page
    kl = facets.kind_labels()
    for source, levels in facets.SCHEMES.items():
        for level in levels:
            if level.kind_axis:
                for key in level.labels:
                    assert kl.get(key), "%s: %r unnamed" % (source, key)


def test_kind_shared_by_two_sources_drops_the_parenthetical():
    # 'imy' is both an avg organ and an rs agency, and a search bucket mixes the
    # two corpora -- so neither corpus's abbreviation may be asserted over both
    kl = facets.kind_labels()
    assert kl["imy"] == "Integritetsskyddsmyndigheten"
    assert kl["kkv"] == "Konkurrensverket"


def test_kind_labels_name_the_single_kind_sources():
    # SFS is two kinds, not one: a förordning is subordinate to the lag that
    # delegates to it, and the catalog says so (`labels.sfs_is_statute`) so the
    # norm hierarchy is readable from a document row. The förordning bucket also
    # holds kungörelser and tillkännagivanden, hence "m.m.".
    kl = facets.kind_labels()
    assert kl["lag"] == "Lagar"
    assert kl["forordning"] == "Förordningar m.m."
    assert kl["case"] == "Rättsfall"
    assert kl["kommentar"] == "Lagkommentarer"


def test_foreskrift_series_keep_their_designation():
    assert facets.kind_labels()["aafs"] == "ÅFS"


def test_fold_fs_versions_drops_the_base_and_marks_the_consolidated():
    # a föreskrift with a konsoliderad version is two catalog rows carrying the
    # same beteckning and title, so both listed with nothing to choose between
    # them (B4). The consolidated one lists; the base is offered from its page.
    grouped = {("AFS", "2023"): [
        Row(uri="https://lagen.nu/afs/2023:11", local="afs/2023:11", kind="afs",
            label="AFS 2023:11", title="Arbetsutrustning", display="Arbetsutrustning",
            date="2023-09-15"),
        Row(uri="https://lagen.nu/afs/2023:11/grund", local="afs/2023:11/grund",
            kind="afs", label="AFS 2023:11", title="Arbetsutrustning",
            display="Arbetsutrustning", date="2023-09-15"),
        Row(uri="https://lagen.nu/afs/2023:6", local="afs/2023:6", kind="afs",
            label="AFS 2023:6", title="Enkla tryckkärl", display="Enkla tryckkärl",
            date="2023-05-02"),
    ]}
    refolded, consolidated = facets._fold_fs_versions(grouped)
    listed = [r.uri for r in refolded[("AFS", "2023")]]
    assert listed == ["https://lagen.nu/afs/2023:11",
                      "https://lagen.nu/afs/2023:6"]
    # only the one that *has* a base version is marked
    assert consolidated == {"https://lagen.nu/afs/2023:11"}


def test_fold_fs_versions_leaves_a_bucket_with_no_base_versions_alone():
    grouped = {("AFS", "2020"): [
        Row(uri="https://lagen.nu/afs/2020:1", local="afs/2020:1", kind="afs",
            label="AFS 2020:1", title="T", display="T", date="2020-01-01")]}
    refolded, consolidated = facets._fold_fs_versions(grouped)
    assert len(refolded[("AFS", "2020")]) == 1 and consolidated == set()


def test_an_expired_document_leaves_the_browse(tmp_path):
    """The browse shows what still states law. The rule is the catalog's
    `expired` column and so is general -- it was written for repealed statutes
    and holds unchanged for a withdrawn rättsligt ställningstagande, which no
    longer says how the agency reads the rule."""
    def store(name, uri, status, upphavd=None):
        path = tmp_path / name
        md = {"title": name, "publisher": "Skatteverket", "nummer": name,
              "status": status, "beslutsdatum": "2024-01-01"}
        if upphavd:
            md["upphavd"] = upphavd
        path.write_text(json.dumps({
            "uri": uri, "type": "stallningstagande", "org": "skv",
            "identifier": name, "designation": name,
            "metadata": md, "structure": []}))
        return path

    db = tmp_path / "catalog.sqlite"
    catalog.rebuild(db, "rs", [
        store("gallande", "https://lagen.nu/rs/skv/8-1", "gällande"),
        store("upphavt", "https://lagen.nu/rs/skv/8-2", "upphävt", "2025-06-01"),
        # a withdrawal the agency has announced but dated in the future still
        # states law today, exactly as a not-yet-in-force repeal does
        store("kommande", "https://lagen.nu/rs/skv/8-3", "upphävt", "2099-01-01"),
    ])
    con = catalog.connect(db)
    listed = {r.uri for r in facets._rows(con, "rs")}
    assert listed == {"https://lagen.nu/rs/skv/8-1",
                      "https://lagen.nu/rs/skv/8-3"}, listed
    con.close()
