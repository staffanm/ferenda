"""Tests for the new-law paragraf margin that surfaces case law decided under
the provision's predecessors (render.corresponding_cases_margin): the
correspondence chain is walked transitively (2025:400 -> 2001:453 -> 1980:620),
one section per predecessor provision, headed "Äldre rättsfall för motsvarande
bestämmelse (<the predecessor, linked>)"."""

import sqlite3
from types import SimpleNamespace

from accommodanda.lib import catalog
from accommodanda.lib.render import corresponding_cases_margin

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
         L + "prop/2024/25:89"),
        (L + "2001:453#K4P1", L + "1980:620#P6", "motsvarar", "helt",
         L + "prop/2000/01:80"),
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
