"""Tests for the new-law paragraf margin that surfaces case law decided under
the provision's predecessors (render.corresponding_cases_margin): the
correspondence chain is walked transitively (2025:400 -> 2001:453 -> 1980:620),
one section per predecessor provision, headed "Äldre rättsfall för motsvarande
bestämmelse (<the predecessor, linked>)"."""

import sqlite3
from types import SimpleNamespace

from accommodanda.lib import catalog
from accommodanda.lib.render import (
    _inbound_groups,
    _reassigned_before,
    corresponding_cases_margin,
    corresponds_margin,
    renumbered_refs_margin,
)

L = "https://lagen.nu/"


def _site():
    con = sqlite3.connect(":memory:")
    con.executescript(catalog.SCHEMA)
    docs = [
        (L + "2025:400", "sfs", "law", "2025:400", "Socialtjänstlag (2025:400)"),
        (L + "2001:453", "sfs", "law", "2001:453", "Socialtjänstlag (2001:453)"),
        (L + "1980:620", "sfs", "law", "1980:620", "Socialtjänstlag (1980:620)"),
        (L + "dom/ra/1993:11", "dv", "case", "RÅ 1993:11", "Fråga om bistånd"),
        (L + "dom/nja/1993s679", "dv", "case", "NJA 1993 s. 679", "Bistånd"),
        (L + "prop/2000/01:80", "forarbete", "prop", "Prop. 2000/01:80", "Ny SoL"),
    ]
    con.executemany(
        "INSERT INTO documents (uri, source, kind, label, title, path) "
        "VALUES (?,?,?,?,?,'x')", docs)
    links = [
        # a case citing the middle law's provision, one citing the oldest's,
        # and a non-case citer that must not appear
        (L + "dom/ra/1993:11", None, "dcterms:references", L + "2001:453#K4P1"),
        (L + "dom/nja/1993s679", None, "dcterms:references", L + "1980:620#P6"),
        (L + "prop/2000/01:80", None, "dcterms:references", L + "1980:620#P6"),
    ]
    con.executemany(
        "INSERT INTO links (from_uri, from_anchor, predicate, to_uri, to_root) "
        "VALUES (?,?,?,?,?)",
        [(f, a, p, t, t.partition("#")[0]) for f, a, p, t in links])
    catalog.set_correspondence(con, [
        (L + "2025:400#K19P3", L + "2001:453#K4P1", "motsvarar", "helt",
         L + "prop/2024/25:89", None),
        (L + "2001:453#K4P1", L + "1980:620#P6", "motsvarar", "helt",
         L + "prop/2000/01:80", None),
    ])
    known = {L + "2025:400", L + "2001:453", L + "1980:620",
             L + "dom/ra/1993:11", L + "dom/nja/1993s679"}
    return SimpleNamespace(con=con,
                           has=lambda uri: uri.partition("#")[0] in known)


def test_margin_walks_chain_and_links_predecessor_citation():
    html = corresponding_cases_margin(_site(), L + "2025:400#K19P3")
    # one section per predecessor generation, nearest first
    assert html.index("2001:453") < html.index("1980:620")
    assert html.count("Äldre rättsfall för motsvarande bestämmelse") == 2
    # the heading's citation is linked and human-readable
    assert '<a href="/2001:453#K4P1">4 kap. 1 § Socialtjänstlag (2001:453)</a>' in html
    assert '<a href="/1980:620#P6">6 § Socialtjänstlag (1980:620)</a>' in html
    # each generation's own case law, and only case law (the prop is not shown)
    assert "RÅ 1993:11" in html and "NJA 1993 s. 679" in html
    assert "Prop. 2000/01:80" not in html


def test_margin_direct_predecessor_only_for_middle_law():
    # the middle law's own paragraf reaches one generation back
    html = corresponding_cases_margin(_site(), L + "2001:453#K4P1")
    assert html.count("Äldre rättsfall för motsvarande bestämmelse") == 1
    assert "1980:620" in html and "NJA 1993 s. 679" in html


def test_margin_empty_without_correspondence():
    assert corresponding_cases_margin(_site(), L + "2025:400#K1P1") == ""


def _rf_site():
    """RF-style same-law renumbering: SFS 2010:1408 gave 4 kap. 4 § the new
    beteckning 4 kap. 6 § from 2011-01-01. One case cites #K4P4 in 2005 (the
    old meaning, i.e. today's 4:6) and one in 2015 (today's 4:4)."""
    con = sqlite3.connect(":memory:")
    con.executescript(catalog.SCHEMA)
    docs = [
        (L + "1974:152", "sfs", "law", "1974:152",
         "Kungörelse (1974:152) om beslutad ny regeringsform", None),
        (L + "dom/nja/2005s33", "dv", "case", "NJA 2005 s. 33",
         "NJA 2005 s. 33", "2005-03-01"),
        (L + "dom/hfd/2015:79", "dv", "case", "HFD 2015 ref. 79",
         "HFD 2015 ref. 79", "2015-11-01"),
    ]
    con.executemany(
        "INSERT INTO documents (uri, source, kind, label, title, path, date) "
        "VALUES (?,?,?,?,?,'x',?)", docs)
    links = [(L + "dom/nja/2005s33", None, "dcterms:references",
              L + "1974:152#K4P4"),
             (L + "dom/hfd/2015:79", None, "dcterms:references",
              L + "1974:152#K4P4")]
    con.executemany(
        "INSERT INTO links (from_uri, from_anchor, predicate, to_uri, to_root) "
        "VALUES (?,?,?,?,?)",
        [(f, a, p, t, t.partition("#")[0]) for f, a, p, t in links])
    catalog.set_correspondence(con, [
        (L + "1974:152#K4P6", L + "1974:152#K4P4", "betecknas", "helt",
         None, "2011-01-01")])
    return SimpleNamespace(con=con, has=lambda uri: True)


def test_renumbered_refs_margin_splits_by_date():
    site = _rf_site()
    # under the new beteckning: only the pre-renumbering citer, under the
    # user-facing heading, with the old beteckning named
    html = renumbered_refs_margin(site, L + "1974:152#K4P6")
    assert "Hänvisningar till tidigare beteckning 4 kap. 4 §" in html
    assert "före 2011-01-01" in html
    assert "NJA 2005 s. 33" in html and "HFD 2015 ref. 79" not in html
    # the old anchor's own panel keeps only the post-renumbering citer
    cutoff = _reassigned_before(site, L + "1974:152#K4P4")
    assert cutoff == "2011-01-01"
    groups = _inbound_groups(site, L + "1974:152#K4P4", exclude_before=cutoff)
    assert "HFD 2015 ref. 79" in groups and "NJA 2005 s. 33" not in groups
    # a renumbering never feeds the repealed-law margins
    assert corresponds_margin(site, L + "1974:152#K4P4") == ""
    assert corresponding_cases_margin(site, L + "1974:152#K4P6") == ""


def _chain_site():
    """RF 2010:1408's cascade: 12 kap. -> 13 kap. and 13 kap. -> 15 kap. on
    the same date, plus an older renumbering within the label now feeding
    15 kap. (13 kap. 2 § was 13 kap. 1 § until 1995). Citers of each label,
    all pre-2011."""
    con = sqlite3.connect(":memory:")
    con.executescript(catalog.SCHEMA)
    docs = [(L + "1974:152", "sfs", "law", "1974:152", "Regeringsformen", None)]
    for ref, d in (("dom/ra/2005:1", "2005-01-01"),   # cites K13P2 (old 13 kap.)
                   ("dom/ra/2005:2", "2005-01-01"),   # cites K12P2 (old 12 kap.)
                   ("dom/ra/1990:1", "1990-01-01")):  # cites K13P1 (pre-1995 label)
        docs.append((L + ref, "dv", "case", ref.split("/")[-1].upper(),
                     ref.split("/")[-1].upper(), d))
    con.executemany(
        "INSERT INTO documents (uri, source, kind, label, title, path, date) "
        "VALUES (?,?,?,?,?,'x',?)", docs)
    links = [(L + "dom/ra/2005:1", L + "1974:152#K13P2"),
             (L + "dom/ra/2005:2", L + "1974:152#K12P2"),
             (L + "dom/ra/1990:1", L + "1974:152#K13P1")]
    con.executemany(
        "INSERT INTO links (from_uri, from_anchor, predicate, to_uri, to_root) "
        "VALUES (?,NULL,'dcterms:references',?,?)",
        [(f, t, t.partition("#")[0]) for f, t in links])
    catalog.set_correspondence(con, [
        (L + "1974:152#K15P2", L + "1974:152#K13P2", "betecknas", "helt",
         None, "2011-01-01"),
        (L + "1974:152#K13P2", L + "1974:152#K12P2", "betecknas", "helt",
         None, "2011-01-01"),
        (L + "1974:152#K13P2", L + "1974:152#K13P1", "betecknas", "helt",
         None, "1995-01-01"),
    ])
    return SimpleNamespace(con=con, has=lambda uri: True)


def test_renumbered_chain_stays_on_lineage():
    site = _chain_site()
    # 15 kap. 2 §'s lineage: 13 kap. 2 § (until 2011), which was 13 kap. 1 §
    # (until 1995). The same-date 12->13 edge is the label's NEXT occupant's
    # arrival and must not leak 12 kap. citers onto the 15 kap. page.
    html = renumbered_refs_margin(site, L + "1974:152#K15P2")
    assert "2005:1" in html
    assert "1990:1" in html                       # two hops back, pre-1995
    assert "2005:2" not in html                   # 12 kap. lineage, excluded
    assert "tidigare beteckning 13 kap. 2 §" in html
    assert "tidigare beteckning 13 kap. 1 §" in html
    # today's 13 kap. 2 § (the moved 12 kap. 2 §) gets the 12 kap. citer
    html13 = renumbered_refs_margin(site, L + "1974:152#K13P2")
    assert "2005:2" in html13 and "2005:1" not in html13
