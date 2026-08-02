"""The DV court-instance walk (dv/render._dv_walk): how a stack of court
instances -- each re-using the same fixed headers -- is levelled in the
innehåll panel and typeset in the reading column (D6)."""

import sqlite3

from accommodanda.dv import render as dv_render
from accommodanda.lib import catalog, page

# --- D6: the court-instance walk -------------------------------------------
#
# A DV record is a stack of court instances that each re-use the same fixed
# headers, so the panel used to read DOMSKÄL three times and DOMSLUT three
# times with no way to tell HD's from tingsrättens.

def _instans(court, *children, **kw):
    return {"type": "instans", "court": court, "children": list(children), **kw}


def _rubrik(text, level=1):
    return {"type": "rubrik", "level": level, "text": [text]}


def _walk(nodes):
    # a real Site, not a namespace: a hand-rolled stub goes stale silently every
    # time the render context gains a field, and this lock-in would stop
    # tracking the walk it exists to pin
    con = sqlite3.connect(":memory:")
    con.executescript(catalog.SCHEMA)
    site = page.Site(con, set())
    toc = page.Toc()
    html = dv_render._dv_walk(nodes, site, "https://lagen.nu/dom/x", toc,
                              page.Rail(site, "https://lagen.nu/dom/x"))
    return html, toc.entries


def test_each_instances_sections_nest_under_its_court():
    _, entries = _walk([
        _instans("Hovrätten", _rubrik("DOMSKÄL"), _rubrik("DOMSLUT")),
        _instans("Högsta domstolen", _rubrik("DOMSKÄL"), _rubrik("DOMSLUT"))])
    assert [(t, lvl) for _a, t, lvl in entries] == [
        ("Hovrätten", 1), ("DOMSKÄL", 2), ("DOMSLUT", 2),
        ("Högsta domstolen", 1), ("DOMSKÄL", 2), ("DOMSLUT", 2)]


def test_a_first_instance_adopts_the_heading_above_it():
    # the tingsrätt is often segmented with no court of its own, its name
    # standing as the plain rubrik just above -- rendered as-is that gave a
    # heading and then a section called "Instans"
    html, entries = _walk([_rubrik("Malmö tingsrätt"),
                           _instans(None, _rubrik("DOMSKÄL"))])
    assert [t for _a, t, _l in entries] == ["Malmö tingsrätt", "DOMSKÄL"]
    assert "Instans" not in html
    assert html.count("Malmö tingsrätt") == 1        # not both heading and rubrik


def test_a_rubrik_naming_the_next_instance_is_not_repeated():
    # the source ends an instance by naming the court that heard the appeal
    # next, which then reads as a section of the court below it as well
    html, entries = _walk([
        _instans("Hovrätten", _rubrik("DOMSLUT"), _rubrik("Högsta domstolen")),
        _instans("Högsta domstolen", _rubrik("DOMSKÄL"))])
    assert [t for _a, t, _l in entries] == [
        "Hovrätten", "DOMSLUT", "Högsta domstolen", "DOMSKÄL"]
    assert html.count(">Högsta domstolen<") == 1


def test_only_the_last_instance_is_final():
    html, _ = _walk([_instans("Tingsrätten", _rubrik("DOMSLUT")),
                     _instans("Högsta domstolen", _rubrik("DOMSKÄL"))])
    assert html.count("instans-lower") == 1


def test_a_trailing_delmal_does_not_unmark_the_deciding_court():
    # 36 records end on a delmal rather than an instans; "last node" then
    # marked no instance final and dimmed the whole judgment
    html, _ = _walk([
        _instans("Tingsrätten", _rubrik("DOMSLUT")),
        _instans("Högsta domstolen", _rubrik("DOMSKÄL")),
        {"type": "delmal", "ordinal": "II", "children": [_rubrik("Mål II")]}])
    assert html.count("instans-lower") == 1
