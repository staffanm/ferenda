"""kkvdomar: Konkurrensverkets domstolsdatabas -- the listing and case page the
harvest reads, the court identity it files each case under, and the parse from
a decision PDF's paragraphs to the published structure.

The parse fixtures are the per-page paragraphs (`parse.paragraphs`) of four real
decisions, so the tests need neither the PDFs nor OCR:

  * kgg/2857-22/2023-01-04 -- born digital, a dom, bold subheadings, two appeal
    forms behind it
  * kjo/1747-17/2017-07-27 -- a scan, Konkurrensverkets receipt stamp read
    into the party block, the lower court's decision attached as bilaga A
  * kst/2660-24/2024-11-26 -- an appeal form whose "Bilaga B" line is not text,
    only the form's code "KR-09"
  * kst/5074-25/2025-11-03 -- the two-way party labels "KLAGANDE OCH MOTPART"
"""

import json
from pathlib import Path

import pytest

from ferenda.kkvdomar import download, parse
from ferenda.kkvdomar.model import Avgorande, Block
from ferenda.lib import page
from ferenda.lib.errors import SkipDocument
from ferenda.lib.lagrum import ALL_PARSE_TYPES, sfs_parser

FILES = Path(__file__).parent / "files" / "kkvdomar"


def _html(name):
    return (FILES / name).read_bytes().decode(download.ENCODING)


def _paragraphs(name):
    return [[(text, bold) for text, bold in page]
            for page in json.loads((FILES / ("%s.paragraphs.json" % name))
                                   .read_text("utf-8"))]


def _body(name):
    per_page = _paragraphs(name)
    body, doktyp = parse.document_blocks(per_page)
    return body, doktyp, [kind for kind, _l, _p in parse.segments(per_page)]


def _row(**fields):
    return {"id": "1", "malnummer": "6426-25", "domstol": "Kammarrätten i Stockholm",
            "beslutsdatum": "2026-03-12", **fields}


# --------------------------------------------------------------------------
# the harvest
# --------------------------------------------------------------------------

def test_listing_rows():
    rows = download.listing_rows(_html("listing.html"))
    assert len(rows) == 10
    assert rows[0] == {"id": "49275", "malnummer": "1885-26",
                       "domstol": "Kammarrätten i Sundsvall",
                       "arendemening": "Upphandling",
                       "beslutsdatum": "2026-08-31",
                       "sokande": "Åke Werners Åkeri AB",
                       "motpart": "Bräcke kommun"}
    assert download.page_count(_html("listing.html")) == 384


def test_case_page():
    assert download.case_fields(_html("arende-49275.html")) == {
        "instans": "Kammarrätt", "arendetyp": "Överprövning av upphandling",
        "avgorande": "Avskrivning",
        "beslut_url": "https://information.konkurrensverket.se/beslut/"
                      "1_20260914134329_1885-26.pdf"}


@pytest.mark.parametrize("malnummer, domstol, basefile", [
    ("6426-25", "Kammarrätten i Stockholm", "kst/6426-25/2026-03-12"),
    # a joined case is named by its first målnummer
    ("5067-26 5074-26 5077-5119-26", "Kammarrätten i Göteborg",
     "kgg/5067-26/2026-03-12"),
    # a range is one målnummer per case: 3404-22 to 3409-22
    ("3404-3409-22", "Kammarrätten i Jönköping", "kjo/3404-22/2026-03-12"),
    ("5256—5259-19", "Kammarrätten i Sundsvall", "ksu/5256-19/2026-03-12"),
])
def test_basefile_is_the_court_identity(malnummer, domstol, basefile):
    assert download.basefile(_row(malnummer=malnummer, domstol=domstol)) == basefile


def test_a_misprinted_malnummer_is_corrected_from_the_decision():
    html = _html("listing.html").replace(">1885-26<", ">190508<").replace(
        "arende.asp?id=49275", "arende.asp?id=30246")
    row = download.listing_rows(html)[0]
    assert download.basefile(row) == "ksu/10381-18/2026-08-31"


def test_a_row_without_a_malnummer_is_an_upstream_change():
    with pytest.raises(ValueError):
        download.basefile(_row(malnummer="190508", id="99999"))


def test_the_later_registration_replaces_the_earlier(tmp_path):
    row = _row(id="47129")
    download.write_record(download.record_json(tmp_path, download.basefile(row)),
                          {**row, "basefile": download.basefile(row)})
    assert download.is_current(tmp_path, row)
    assert not download.is_current(tmp_path, {**row, "id": "47146"})


def test_uri_is_the_verdict_uri():
    art = Avgorande(court="KST", domstol="Kammarrätten i Stockholm",
                    malnummer="3404-22", malnummer_lista="3404-3409-22",
                    avgorandedatum="2022-12-08", instans="Kammarrätt")
    assert art.uri == "https://lagen.nu/dom/kst/3404-22/2022-12-08"
    assert art.identifier == "Kammarrätten i Stockholm mål nr 3404-22 m.fl."


# --------------------------------------------------------------------------
# the parse
# --------------------------------------------------------------------------

def test_born_digital_decision():
    body, doktyp, kinds = _body("kgg-2857-22-2023-01-04")
    assert doktyp == "dom"
    assert kinds == ["decision", "appeal", "appeal"]
    headings = [(b.level, b.text) for b in body if b.kind == "rubrik"]
    assert headings == [
        (1, "KLAGANDE"), (1, "MOTPART"), (1, "ÖVERKLAGAT AVGÖRANDE"),
        (1, "SAKEN"), (1, "KAMMARRÄTTENS AVGÖRANDE"), (1, "YRKANDEN M.M."),
        (1, "SKÄLEN FÖR KAMMARRÄTTENS AVGÖRANDE"), (2, "Vad målet gäller"),
        (2, "Kammarrättens bedömning")]
    texts = [b.text for b in body]
    # the letterhead, the running headers, the footer and the rules are gone
    assert not any("Sida" in t or "Dok.Id" in t or "Postadress" in t
                   or "___" in t for t in texts)
    assert texts[1] == "Treano Bygg AB, 556530-6197 Ombud: Advokaterna och"
    # a paragraph a page break split is one paragraph again (page 1 ends on
    # "trots att bolaget", page 2 opens on "har offererat lägst anbud")
    assert any("trots att bolaget har offererat lägst anbud." in t for t in texts)
    # the appeal forms are not part of the decision
    assert not any("Den som vill överklaga" in t for t in texts)
    assert parse.domslut(body).startswith(
        "Kammarrätten beslutar att upphandlingen inte får avslutas")


def test_scanned_decision_keeps_the_lower_courts_decision():
    body, doktyp, kinds = _body("kjo-1747-17-2017-07-27")
    assert doktyp == "dom"
    assert kinds == ["decision", "bilaga", "appeal", "appeal"]
    texts = [b.text for b in body]
    # Konkurrensverkets receipt stamp is read out of the party block
    assert texts[:4] == ["KLAGANDE", "Guntorps Herrgård AB, 556250-6344 2017-07-31",
                         "Guntorpsgatan 2 — RN 387 36 Borgholm", "MOTPART"]
    bilaga = texts.index("Bilaga")
    assert [(b.level, b.text) for b in body[bilaga + 1:bilaga + 3]] == [
        (2, "SÖKANDE"), (1, "Guntorps Herrgård AB, 556250-6344 Guntorpsgatan 2 "
                           "387 36 Borgholm")]
    # the lower court's own "HUR MAN ÖVERKLAGAR" section stays: it is the
    # last page of its decision, not the start of an appeal form
    assert "Information om hur man överklagar finns i bilaga 1 (DV 3109 D)." in texts
    assert not any(t.startswith("Om Ni vill överklaga") for t in texts)


def test_appeal_form_known_by_its_code_alone():
    body, _doktyp, kinds = _body("kst-2660-24-2024-11-26")
    # one form over two pages: its second page ("Sid 2 av 2") continues it
    assert kinds == ["decision", "appeal"]
    assert body[-1].text == "Johan Stigenberg föredragande jurist"


def test_two_way_party_labels():
    body, _doktyp, _kinds = _body("kst-5074-25-2025-11-03")
    assert [b.text for b in body[:4]] == [
        "KLAGANDE OCH MOTPART", "Västerås stad", "MOTPART OCH KLAGANDE",
        "Atos Storkök AB, 559091-8651 Ombud:"]


@pytest.mark.parametrize("text", [
    "KAMMARRÄTTEN I BESLUT Sida 2", "GÖTEBORG", "Mål nr 1137-26 m.fl.",
    "KAMMARRÄTTEN BESLUT Sida 2 I JÖNKÖPING Mål nr 2750-21 Avdelning 1:3",
    "FÖRVALTNINGSRÄTTEN BESLUT 3370-17 I LINKÖPING",
    "KAMMARRÄTTEN I BESLUT Sida 2 GÖTEBORG Mål nr 5256—-5259-19"])
def test_running_header(text):
    assert parse._drop_header([(text, False), ("Brödtext.", False)]) == [
        ("Brödtext.", False)]


def test_a_body_paragraph_opening_on_a_number_is_not_a_header():
    page = [("2015 ref. 55). Förvaltningsrätten borde således ha hämtat in "
             "handlingarna.", False)]
    assert parse._drop_header(page) == page


def test_the_body_is_citation_scanned():
    art = Avgorande(
        court="KSU", domstol="Kammarrätten i Sundsvall", malnummer="1885-26",
        malnummer_lista="1885-26", avgorandedatum="2026-08-31",
        instans="Kammarrätt",
        body=[Block("stycke", "Enligt 20 kap. 6 § andra stycket lagen (2016:1145) "
                              "om offentlig upphandling får en upphandling inte "
                              "överprövas.")],
    ).to_artifact(sfs_parser("kkvdomar", ALL_PARSE_TYPES, written="2026-08-31"))
    links = [run for run in art["structure"][0]["text"] if isinstance(run, dict)]
    assert links[0]["uri"] == "https://lagen.nu/2016:1145#K20P6S2"


# --------------------------------------------------------------------------
# decisions an HFD referat supersedes
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text, basefile", [
    ("1 (2) HÖGSTA FÖRVALTNINGSDOMSTOLENS BESLUT Mål nr 4486-26 KLAGANDE "
     "Professional Management Lenefors & Svensson AB, 556534-1186 ÖVERKLAGAT "
     "AVGÖRANDE Kammarrätten i Stockholms beslut den 25 juni 2026 i mål nr "
     "3738-26 SAKEN Offentlig upphandling", "kst/3738-26/2026-06-25"),
    # OCR's "i -mål"
    ("ÖVERKLAGAT AVGÖRANDE Kammarrätten i Stockholms dom den 22 januari 2015 i "
     "-mål nr 6806-14 SAKEN Offentlig upphandling", "kst/6806-14/2015-01-22"),
    ("ÖVERKLAGAT AVGÖRANDE Kammarrätten i Göteborgs dom den 6 mars 2020 i mål "
     "nr 946-20 SAKEN", "kgg/946-20/2020-03-06"),
    # a page 1 that is not there (HFD 2023 ref. 7 starts at page 2)
    ("2 (8) HÖGSTA FÖRVALTNINGSDOMSTOLEN DOM Mål nr 766-22 BAKGRUND", None),
])
def test_the_appealed_decision_is_read_off_hfds_page_1(text, basefile):
    assert download.appealed(text) == basefile


SNAPSHOT = {"numbers": {
    "6102-19": [["HFD", "2020-05-08", "dom/hfd/2020:24"]],
    "5644-19": [["HFD", "2020-02-07", "dom/hfd/2020/not/5"]],
    # a decision dv holds without a referat supersedes nothing
    "6329-25": [["HFD", "2026-06-11", "dom/hfd/6329-25/2026-06-11"]],
    # another court's case under the same number
    "1234-19": [["ADO", "2019-01-01", "dom/ad/2019:1"]]}}


@pytest.mark.parametrize("malnummer, local", [
    ("6102-19", "dom/hfd/2020:24"), ("5644-19", "dom/hfd/2020/not/5"),
    ("766-22 RÄTTELSE 6102-19", "dom/hfd/2020:24"),
    ("6329-25", None), ("1234-19", None), ("3019", None)])
def test_only_a_referat_or_notis_supersedes(malnummer, local):
    assert download.published(SNAPSHOT, malnummer) == local


def test_a_superseded_decision_is_not_parsed(tmp_path):
    for hfd_id, malnummer, overklagat in [
            ("32325", "6102-19", "kst/5613-19/2019-10-28"),
            ("99999", "6329-25", "kst/1-25/2025-01-01")]:
        download.write_record(
            download.record_json(tmp_path, "hfd/%s" % hfd_id),
            {"basefile": "hfd/%s" % hfd_id, "id": hfd_id,
             "malnummer": malnummer, "overklagat": overklagat})
    assert download.write_superseded(tmp_path, SNAPSHOT) == {
        "kst/5613-19/2019-10-28": "https://lagen.nu/dom/hfd/2020:24"}
    with pytest.raises(SkipDocument):
        parse.parse("kst/5613-19/2019-10-28", tmp_path)


def test_the_rail_names_the_group_by_its_court():
    assert ("kkvdomar", "Kammarrättsdomar") in page.INBOUND_GROUPS
