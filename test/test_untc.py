"""UN Treaty Collection (MTDSG) scraping, artifact projection, folkrätt wiring.

Runs off a committed synthetic MTDSG fixture (a trimmed Vienna Convention page)
plus small dicts -- no network.
"""

import json
from pathlib import Path

import pytest

from accommodanda.lib import catalog, compress, facets, layout, render
from accommodanda.untc import download, parse
from accommodanda.untc.model import Treaty, load_treaties, treaty_uri

FIXTURES = Path(__file__).parent / "files" / "untc"


def _vclt():
    return parse.parse("XXIII-1", FIXTURES)


# --------------------------------------------------------------------------
# model + curated list
# --------------------------------------------------------------------------

def test_treaty_uri_and_kind():
    assert treaty_uri("XXIII-1") == "https://lagen.nu/ext/untc/XXIII-1"
    assert Treaty("XXIII-1", "23", "Vienna Convention on the Law of Treaties").kind \
        == "treaty"
    assert Treaty("V-5", "5", "Protocol relating to the Status of Refugees").kind \
        == "protocol"


def test_curated_list_is_complete_and_well_formed():
    treaties = load_treaties()
    # the anchors the whole build hangs on
    assert treaties["XXIII-1"]["title"] == "Vienna Convention on the Law of Treaties"
    assert treaties["XXI-6"]["title"] == \
        "United Nations Convention on the Law of the Sea"
    # every curated entry carries the fields the harvest/listing need
    for mtdsg, entry in treaties.items():
        assert entry["mtdsg_no"] == mtdsg
        assert entry["chapter"] and entry["title"] and entry["group"]


# --------------------------------------------------------------------------
# parse: metadata + participation
# --------------------------------------------------------------------------

def test_parse_metadata():
    art = _vclt()
    assert art["uri"] == "https://lagen.nu/ext/untc/XXIII-1"
    assert art["type"] == "internationell-overenskommelse"
    assert art["doctype"] == "treaty"
    assert art["number"] == "XXIII-1"
    assert art["date"] == "1969-05-23"
    md = art["metadata"]
    assert md["conclusionPlace"] == "Vienna"
    assert md["conclusionDate"] == "1969-05-23"
    assert md["entryIntoForce"].startswith("27 January 1980")
    assert md["registration"] == "27 January 1980, No. 18232"
    assert md["depositary"] == "UN Secretary-General"      # not a state -- the UN SG
    assert art["structure"] == []                          # MTDSG carries status, not text
    assert art["source_url"] == (
        "https://treaties.un.org/pages/ViewDetailsIII.aspx"
        "?src=TREATY&mtdsg_no=XXIII-1&chapter=23&clang=_en")


def test_parse_participation_actions_and_footnotes():
    art = _vclt()
    parties = {p["country"]: p for p in art["parties"]}
    # the consent-to-be-bound markers each map to their form
    assert parties["Albania"]["action"] == "accession"        # a
    assert parties["Bosnia and Herzegovina"]["action"] == "succession"   # d
    assert parties["Argentina"]["action"] == "ratification"   # bare date
    assert parties["European Union"]["action"] == "formal confirmation"  # c
    assert parties["Albania"]["actionDate"] == "2001-06-27"
    # a footnote superscript is stripped from the state name; a declaring state
    # (wrapped in <a class="noteIndex">) keeps its name
    assert "Bosnia and Herzegovina" in parties and "3" not in "".join(parties)
    assert "Argentina" in parties
    # signature-only vs bound counts
    assert art["metadata"]["statesParties"] == 4             # Albania/Bosnia/Argentina/EU
    assert art["metadata"]["signatories"] == 2               # Afghanistan/Argentina
    assert parties["Afghanistan"] == {"country": "Afghanistan",
                                      "signature": "1969-05-23"}


def test_tblgrid_anchor_ignores_the_decoy_territorial_table():
    # the page opens its territorial-notification table with a 'Participant'
    # header too; anchoring on the grid's control id keeps that noise out
    countries = {p["country"] for p in _vclt()["parties"]}
    assert "United Kingdom" not in countries


def test_parse_fails_loudly_on_control_drift():
    # the conclusion date is load-bearing; if the control id it lives in drifts,
    # the scrape must reject the page, not ship a dateless artifact
    html = ('<html><body>'
            '<table id="x_tblgrid"><tr><td>Participant</td><td>Signature</td>'
            '<td>Ratification</td></tr>'
            '<tr><td>Sweden</td><td></td><td>5 Dec 1972</td></tr></table>'
            '</body></html>')                     # no rptTreaty_ctl00_tcText
    entry = {"mtdsg_no": "XXIII-1", "chapter": "23", "title": "…"}
    with pytest.raises(ValueError, match="no conclusion date"):
        parse.parse_page(entry, html)


# --------------------------------------------------------------------------
# download identity (no network)
# --------------------------------------------------------------------------

def test_page_path_and_list_basefiles(tmp_path):
    compress.write_download(download.page_path(tmp_path, "XXIII-1"), "<html></html>")
    assert download.list_basefiles(tmp_path) == ["XXIII-1"]


# --------------------------------------------------------------------------
# layout + catalog wiring
# --------------------------------------------------------------------------

def test_untc_layout_round_trips_and_catalog_row():
    uri = "https://lagen.nu/ext/untc/XXIII-1"
    assert layout.page_url(uri) == "/untc/XXIII-1"
    assert layout.page_relpath(uri) == "untc/XXIII_1.html"
    assert str(layout.url_to_relpath("/untc/XXIII-1")) == "untc/XXIII_1.html"
    assert layout.relpath("untc", "XXIII-1") == Path("XXIII-1")
    assert "untc" in facets.sources()
    row = catalog.untc_document(_vclt(), "artifact/untc/XXIII-1.json")
    assert row[:3] == (uri, "untc", "treaty")
    assert row[3] == "Vienna Convention on the Law of Treaties"


# --------------------------------------------------------------------------
# folkrätt landing + treaty page
# --------------------------------------------------------------------------

def _stub(mtdsg, title, date):
    return {"uri": treaty_uri(mtdsg), "number": mtdsg, "doctype": "treaty",
            "type": "internationell-overenskommelse", "identifier": title,
            "title": title, "date": date,
            "metadata": {"statesParties": 0}, "references": [], "structure": [],
            "parties": []}


def test_folkratt_lists_untc_grouped_by_subject(tmp_path):
    vclt = _vclt()                                            # Traktaträtt och havsrätt
    iccpr = _stub("IV-4", "International Covenant on Civil and Political Rights",
                  "1966-12-16")                               # Mänskliga rättigheter
    refugees = _stub("V-2", "Convention relating to the Status of Refugees",
                     "1951-07-28")                            # Flyktingrätt
    paths = []
    for art in (vclt, iccpr, refugees):
        p = tmp_path / (art["number"].replace("-", "_") + ".json")
        p.write_text(json.dumps(art, ensure_ascii=False))
        paths.append(p)
    database = str(tmp_path / "catalog.sqlite")
    catalog.rebuild(database, "untc", paths)
    con = catalog.connect(database)
    html = render.render_folkratt(con)

    assert "Förenta nationerna (FN)" in html
    # the curated subject groups appear in their display order
    assert "Traktaträtt och havsrätt" in html
    assert "Mänskliga rättigheter" in html
    assert "Flyktingrätt" in html
    assert (html.index("Traktaträtt") < html.index("Mänskliga rättigheter")
            < html.index("Flyktingrätt"))
    # the gloss folds the Swedish name, acronym and MTDSG id
    assert "(Wienkonventionen om traktaträtten, VCLT, MTDSG XXIII-1)" in html
    assert 'href="/untc/XXIII-1"' in html
    # the shared Dokumenttyp selector gains an FN-fördrag bucket
    assert "FN-fördrag" in html


def test_render_treaty_page_shows_status_and_participation(tmp_path):
    art = _vclt()
    p = tmp_path / "XXIII-1.json"
    p.write_text(json.dumps(art, ensure_ascii=False))
    database = str(tmp_path / "catalog.sqlite")
    catalog.rebuild(database, "untc", [p])
    con = catalog.connect(database)
    html = render.render_untc(art, render.Site(con, {art["uri"]}))
    assert '<a href="/folkratt/" class="on">Folkrätt</a>' in html   # masthead current
    assert "UN Secretary-General" in html and "Depositarie" in html
    assert "Registrering" in html
    # the participation table renders each state's binding consent in Swedish
    assert "Bindande samtycke" in html
    assert "anslutning" in html                              # accession -> anslutning
    assert "Albania" in html
    # the "Källa" link points at the MTDSG page (& is html-escaped in the href)
    assert "treaties.un.org/pages/ViewDetailsIII.aspx" in html
    assert "mtdsg_no=XXIII-1" in html
