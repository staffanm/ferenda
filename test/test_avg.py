"""avg vertical (JO + JK myndighetsavgöranden): identity, download parsing,
body classification, artifact projection, layout/catalog wiring.

Hermetic: synthetic fixtures modelled on the live 2026 sites (jo.se WordPress
search hits, jk.se Umbraco landing pages); no network, no poppler."""

import json
from pathlib import Path

import pytest

from accommodanda.avg import download as avg_download
from accommodanda.avg import parse as avg_parse
from accommodanda.avg.model import Beslut, Block, Fotnot, beslut_uri
from accommodanda.lib import catalog, compress, facets, layout
from accommodanda.lib.lagrum import MYNDIGHETSBESLUT, LagrumParser
from accommodanda.lib.pdftext import Para
from accommodanda.lib.util import document_extension, record_path, write_atomic
from accommodanda.lib.lagrum import sfs_parser

ARN_FIXTURES = Path(__file__).parent / "files" / "avg" / "arn"


# --------------------------------------------------------------------------
# identity -- the document URI is what a citation mints, by construction
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,org,dnr", [
    ("se JO:s beslut den 30 juni 2026, dnr 2340-2025", "jo", "2340-2025"),
    ("jfr JO 1995/96 s. 92, dnr 3067-1994", "jo", "3067-1994"),
    ("Justitiekanslerns beslut med dnr 3497-06-40", "jk", "3497-06-40"),
    ("jfr ARN:s änr 1992-3657", "arn", "1992-3657"),
    ("ARN, avgörande 1992-11-12; 1992-3657", "arn", "1992-3657"),
])
def test_uri_matches_citation_grammar(text, org, dnr):
    parser = LagrumParser({}, basefile="avg", parse_types=[MYNDIGHETSBESLUT])
    assert beslut_uri(org, dnr) in [r.uri for r in
                                    parser.parse_text(text, context={})]


def test_jk_canonical():
    # the dotted ärendetyp is jk.se's display quirk; citations write it compact
    assert avg_download.jk_canonical("6098-19-4.4") == "6098-19-44"
    assert avg_download.jk_canonical("2060-19-2.4.1") == "2060-19-241"
    assert avg_download.jk_canonical("3497-06-40") == "3497-06-40"
    # the new-era form passes through; a stray "JK " prefix is dropped
    assert avg_download.jk_canonical("2024/6800") == "2024/6800"
    assert avg_download.jk_canonical("JK 2020/4299") == "2020/4299"
    # multi-dnr: the first names the document
    assert avg_download.jk_canonical("2024/6800; 2024/7745") == "2024/6800"
    # a range or otherwise unparsable form is kept verbatim (never a citation
    # target, but a stable identity)
    assert avg_download.jk_canonical("2019/6642-6643") == "2019/6642-6643"


# --------------------------------------------------------------------------
# JK download -- listing + landing
# --------------------------------------------------------------------------

JK_LISTING = """
<div class="ruling-results container"><div class="results">
  <div class="date">Diarienr: 2025/2328 <span>/</span> Beslutsdatum: 25 jun 2026</div>
  <h2><a href="/beslut-och-yttranden/2026/06/20252328/">Kritik mot Arbetsf&#xF6;rmedlingen</a></h2>
  <br />
  <div class="date">Diarienr: 6098-19-4.4 <span>/</span> Beslutsdatum: 3 maj 2021</div>
  <h2><a href="/beslut-och-yttranden/2021/05/6098194.4/">Ett gammalt beslut</a></h2>
</div></div>"""


def test_jk_parse_listing():
    items = avg_download.jk_parse_listing(JK_LISTING)
    assert [i["dnr_raw"] for i in items] == ["2025/2328", "6098-19-4.4"]
    assert items[0]["title"] == "Kritik mot Arbetsförmedlingen"
    assert items[0]["url"].startswith("https://www.jk.se/beslut-och-yttranden/")
    assert items[0]["beslutsdatum_raw"] == "25 jun 2026"


def test_jk_date():
    assert avg_parse.jk_date("25 jun 2026") == "2026-06-25"
    assert avg_parse.jk_date("3 maj 2021") == "2021-05-03"
    assert avg_parse.jk_date("gårdagen") is None


JK_LANDING = """
<html><body><div class="content col-sm-10">
  <div class="date">Diarienr: 2025/2328 <span>/</span> Beslutsdatum: 25 jun 2026</div>
  <h2>Kritik mot Arbetsförmedlingen för godtyckligt beslutsfattande</h2>
  <div class="actions"><a href="#">Skriv ut</a></div>
  <p><p><strong>Justitiekanslerns beslut</strong></p>
  <p>Justitiekanslern riktar kritik mot Arbetsförmedlingen.</p>
  <p><strong>Ärendet</strong></p>
  <p><em>Bakgrund</em></p>
  <p>Sökanden begärde omprövning enligt 1 kap. 9 § regeringsformen.</p></p>
</div></body></html>"""


def test_jk_body_classification():
    blocks = avg_parse.jk_body(JK_LANDING)
    assert [(b.kind, b.level) for b in blocks] == [
        ("rubrik", 1), ("stycke", 1), ("rubrik", 1), ("rubrik", 2),
        ("stycke", 1)]
    assert blocks[0].text == "Justitiekanslerns beslut"
    assert blocks[3].text == "Bakgrund"
    # the date row, the title h2 and the action toolbar are not body
    assert not any("Diarienr" in b.text or "Skriv ut" in b.text
                   or b.text.startswith("Kritik mot") for b in blocks)


def test_parse_jk_artifact():
    record = {"basefile": "jk/2025/2328", "org": "jk",
              "diarienummer_raw": "2025/2328",
              "beslutsdatum_raw": "25 jun 2026",
              "title": "Kritik mot Arbetsförmedlingen",
              "url": "https://www.jk.se/beslut-och-yttranden/2026/06/20252328/"}
    art = avg_parse.parse_jk(record, JK_LANDING).to_artifact(
        sfs_parser("avg", avg_parse.AVG_PARSE_TYPES))
    assert art["uri"] == "https://lagen.nu/avg/jk/2025/2328"
    assert art["identifier"] == "JK 2025/2328"
    assert art["metadata"]["beslutsdatum"] == "2026-06-25"
    assert art["metadata"]["publisher"] == "Justitiekanslern"
    assert art["source_url"] == record["url"]
    # the RF citation is scanned into an inline run
    runs = [r for b in art["structure"] for r in b["text"] if isinstance(r, dict)]
    assert any(r["uri"] == "https://lagen.nu/1974:152#K1P9" for r in runs)


# --------------------------------------------------------------------------
# JO -- record + PDF classification (pure over the Para stream)
# --------------------------------------------------------------------------

def _p(text, bold=False):
    return Para(text=text, bold=bold)


def test_classify_jo():
    titel = ("Allvarlig kritik mot Kriminalvården, anstalten Hall, för att ha "
             "lyssnat på samtal mellan intagna")
    paras = [
        _p("[P] BESLUT Datum Dnr Sid 1 (8) 2026-06-30 2340-2025"),
        _p("Justitieombudsmannen Katarina Påhlsson"),
        # the PDF sets the title as a sequence of bold lines
        _p("Allvarlig kritik mot Kriminalvården, anstalten Hall, för att ha",
           bold=True),
        _p("lyssnat på samtal mellan intagna", bold=True),
        _p("Beslutet i korthet: Kriminalvårdspersonal har lyssnat på samtal."),
        _p("Anmälan", bold=True),
        _p("I en anmälan till JO förde AA fram klagomål."),
        _p("Sid 2 (8)"),
        _p("Rättslig reglering", bold=True),
        _p("Enligt 2 kap. 6 § regeringsformen gäller skydd mot intrång."),
    ]
    blocks, abstract = avg_parse.classify_jo(paras, titel)
    assert abstract == "Kriminalvårdspersonal har lyssnat på samtal."
    assert [(b.kind, b.text.split()[0]) for b in blocks] == [
        ("rubrik", "Anmälan"), ("stycke", "I"),
        ("rubrik", "Rättslig"), ("stycke", "Enligt")]


def test_parse_jo_pdf_text_fallback(tmp_path):
    record = {"basefile": "jo/2340-2025", "diary_number": "2340-2025",
              "post_title": "Allvarlig kritik mot Kriminalvården",
              "resolve_date": "2026-06-30",
              "resolve_maker": "Justitieombudsmannen Katarina Påhlsson",
              "matter_of_fact_names": ["Avlyssning"],
              "post_content": "<p>Kriminalvårdspersonal har lyssnat.</p>",
              "pdf_text": "[P] Enligt 2 kap. 6 § regeringsformen gäller skydd.",
              "permalink": "https://www.jo.se/besluten/allvarlig-kritik/"}
    # no PDF on disk under tmp_path -> the record's own flat text is the body
    beslut = avg_parse.parse_jo(record, tmp_path)
    assert beslut.uri == "https://lagen.nu/avg/jo/2340-2025"
    assert beslut.identifier == "JO dnr 2340-2025"
    assert beslut.beslutsdatum == "2026-06-30"
    assert beslut.sammanfattning == "Kriminalvårdspersonal har lyssnat."
    assert beslut.nyckelord == ["Avlyssning"]
    assert [b.kind for b in beslut.body] == ["stycke"]
    art = beslut.to_artifact(sfs_parser("avg", avg_parse.AVG_PARSE_TYPES))
    runs = [r for b in art["structure"] for r in b["text"] if isinstance(r, dict)]
    assert any(r["uri"] == "https://lagen.nu/1974:152#K2P6" for r in runs)


def test_jo_multi_dnr():
    # a decision on joined complaints carries several dnr; the first names it
    assert avg_download.jo_dnrs("6356-2012 6488-2012") == \
        ["6356-2012", "6488-2012"]
    beslut = Beslut(org="jo", diarienummer=["6356-2012", "6488-2012"],
                    titel="x", body=[Block("stycke", "text")])
    assert beslut.uri == "https://lagen.nu/avg/jo/6356-2012"
    art = beslut.to_artifact(sfs_parser("avg", avg_parse.AVG_PARSE_TYPES))
    assert art["metadata"]["diarienummer"] == ["6356-2012", "6488-2012"]


# --------------------------------------------------------------------------
# wiring -- layout paths, catalog row, facet keys
# --------------------------------------------------------------------------

def test_layout_paths():
    assert layout.relpath("avg", "jo/2340-2025").as_posix() == "jo/2340-2025"
    # a new-era JK dnr carries a slash; the storage path flattens it
    assert layout.relpath("avg", "jk/2024/8082").as_posix() == "jk/2024-8082"
    assert layout.page_relpath("https://lagen.nu/avg/jo/2340-2025") == \
        "avg/jo_2340-2025.html"
    assert layout.page_url("https://lagen.nu/avg/jk/2024/8082") == \
        "/avg/jk/2024/8082"
    # the static server maps the published URL back to the on-disk file
    assert layout.url_to_relpath("/avg/jk/2024/8082") == "avg/jk_2024_8082.html"


def test_catalog_row():
    art = {"uri": "https://lagen.nu/avg/jo/2340-2025", "org": "jo",
           "identifier": "JO dnr 2340-2025",
           "metadata": {"title": "Allvarlig kritik"}}
    uri, source, kind, label, title, path = catalog.document_row(art, "p.json", "avg")
    assert (source, kind, label, title) == \
        ("avg", "jo", "JO dnr 2340-2025", "Allvarlig kritik")


def test_facet_year():
    class R:
        def __init__(self, local, kind):
            self.local, self.kind = local, kind
    assert facets._avg_year(R("avg/jo/2340-2025", "jo")) == "2025"
    assert facets._avg_year(R("avg/jk/2024/8082", "jk")) == "2024"
    assert facets._avg_year(R("avg/jk/3497-06-40", "jk")) == "2006"
    assert facets._avg_year(R("avg/jk/3541-97-21", "jk")) == "1997"
    assert facets._avg_org(R("avg/jk/2024/8082", "jk")) == "jk"


def test_jo_record_strips_formatted():
    hit = {"id": 1, "diary_number": "2340-2025", "_formatted": {"echo": 1}}
    record = avg_download.jo_record(hit, "jo/2340-2025")
    assert "_formatted" not in record
    assert record["basefile"] == "jo/2340-2025"
    assert record["diary_number"] == "2340-2025"


def test_record_roundtrip(tmp_path):
    # what jk_save writes, avg_list/list_basefiles must enumerate
    record = {"basefile": "jk/2024/8082", "org": "jk",
              "diarienummer_raw": "2024/8082", "beslutsdatum_raw": "20 apr 2026",
              "title": "t", "url": "https://www.jk.se/x/"}
    compress.write_download(record_path(tmp_path, "jk", record["basefile"]),
                            json.dumps(record))
    assert compress.list_basefiles(tmp_path, "jk") == ["jk/2024/8082"]


# --------------------------------------------------------------------------
# ARN -- the frozen third organ (re-housed frozen corpus)
# --------------------------------------------------------------------------

def test_document_extension_magic():
    assert document_extension(b"%PDF-1.4") == ".pdf"
    assert document_extension(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1") == ".doc"
    assert document_extension(b"\xffWPC\x5e\x00\x00\x00") == ".wpd"    # WordPerfect
    assert document_extension(b"{\\rtf1") == ".rtf"
    assert document_extension(b"<!DOCTYPE HTML PUBLIC") is None        # error page


def test_arn_year_facet():
    # ARN 'YYYY-NNNN' orders the year first -- the opposite of JO's 4-4 dnr, so
    # the year facet must key on the organ, not the dnr shape
    class R:
        def __init__(self, local, kind):
            self.local, self.kind = local, kind
    assert facets._avg_year(R("avg/arn/1992-3657", "arn")) == "1992"
    assert facets._avg_org(R("avg/arn/1992-3657", "arn")) == "arn"


def test_classify_arn_and_citation_scan():
    # bold paragraph -> rubrik, else stycke; the body is citation-scanned like
    # the other organs, so a lagrum reference becomes an inline run
    paras = [
        Para(text="Bakgrund", bold=True),
        Para(text="Konsumenten begärde återbetalning.", bold=False),
        Para(text="", bold=False),                       # blank Para dropped
        Para(text="Enligt 2 kap. 6 § regeringsformen gäller skydd.", bold=False),
    ]
    blocks = avg_parse.classify_arn(paras, "1992-3657")
    assert [(b.kind, b.level) for b in blocks] == [
        ("rubrik", 1), ("stycke", 1), ("stycke", 1)]
    beslut = Beslut(org="arn", diarienummer=["1992-3657"],
                    titel="Fråga om återbetalning", beslutsdatum="1992-11-12",
                    nyckelord=["Resor"], body=blocks)
    art = beslut.to_artifact(sfs_parser("avg", avg_parse.AVG_PARSE_TYPES))
    assert art["uri"] == "https://lagen.nu/avg/arn/1992-3657"
    assert art["identifier"] == "ARN 1992-3657"
    assert art["org"] == "arn"
    assert art["metadata"]["publisher"] == "Allmänna reklamationsnämnden"
    assert art["metadata"]["nyckelord"] == ["Resor"]
    runs = [r for b in art["structure"] for r in b["text"] if isinstance(r, dict)]
    assert any(r["uri"] == "https://lagen.nu/1974:152#K2P6" for r in runs)


def test_classify_arn_strips_live_pdf_front_matter():
    # a live arn.se PDF restates the curated summary in bold (mixed with the
    # margin änr/date column) before a "Beslut <date>; <änr>" marker; all of
    # it is front matter -- the body starts after the marker (arn/2017-03049)
    paras = [
        Para(text="Kompensation enligt artikel 7 på grund av inställd", bold=True),
        Para(text="028 flygning. En passagerare har rest från ett EU-land.",
             bold=False),
        Para(text="2017-03049 2018-08-13", bold=False),   # margin änr + date
        Para(text="Beslut 2018-05-22; 2017-03049", bold=True),
        Para(text="AF begärde kompensation med 600 euro.", bold=False),
    ]
    blocks = avg_parse.classify_arn(paras, "2017-03049")
    assert [(b.kind, b.text) for b in blocks] == [
        ("stycke", "AF begärde kompensation med 600 euro.")]


def test_classify_arn_marker_glued_mid_para_and_inline_margin():
    # the extraction can glue margin + marker + body into ONE para
    # (arn/2023-28076), and interleave the margin pair mid-sentence at a
    # column boundary (arn/2024-20746) -- both anchored to the OWN änr, so a
    # citation to another decision's änr is untouched
    paras = [
        Para(text="Summering i fetstil av referatet.", bold=True),
        Para(text="2023-28076 2024-12-28 Beslut 2024-12-28; 2023-28076 "
                  "HS begärde ersättning med 67 000 kr.", bold=True),
        Para(text="Nämnden 2023-28076 2024-12-28 ska därför lägga uppgiften "
                  "till grund för bedömningen.", bold=False),
        Para(text="Jfr ARN:s änr 2020-12345.", bold=False),
    ]
    blocks = avg_parse.classify_arn(paras, "2023-28076")
    assert [(b.kind, b.text) for b in blocks] == [
        ("stycke", "HS begärde ersättning med 67 000 kr."),
        ("stycke", "Nämnden ska därför lägga uppgiften till grund för "
                   "bedömningen."),
        ("stycke", "Jfr ARN:s änr 2020-12345.")]


def test_classify_arn_frozen_body_passes_unchanged():
    # the frozen Digiforms bodies carry no live-PDF noise; a decision that
    # cites a date or another änr must not lose text to the own-änr filters
    paras = [Para(text="Avgörande 1992-11-12; 92-3657.", bold=False),
             Para(text="Nämnden fann att yrkandet skulle bifallas.", bold=False)]
    blocks = avg_parse.classify_arn(paras, "1992-3657")
    assert [b.text for b in blocks] == [
        "Avgörande 1992-11-12; 92-3657.",
        "Nämnden fann att yrkandet skulle bifallas."]


# --------------------------------------------------------------------------
# JO frozen-corpus deltas: the ämbetsberättelse map + missing-case import
# --------------------------------------------------------------------------

JO_RDF = """<?xml version="1.0" encoding="utf-8"?>
<rdf:RDF xmlns:dcterms="http://purl.org/dc/terms/"
  xmlns:rpubl="http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#"
  xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rpubl:VagledandeMyndighetsavgorande rdf:about="https://lagen.nu/avg/jo/%(dnr)s">
    <rpubl:diarienummer>%(dnr)s</rpubl:diarienummer>
    <dcterms:title xml:lang="sv">%(title)s</dcterms:title>
    <rpubl:avgorandedatum>%(date)s</rpubl:avgorandedatum>
    %(citation)s
  </rpubl:VagledandeMyndighetsavgorande>
</rdf:RDF>"""

JO_HEADNOTE = ('<html><body><a href="/JO-beslut/x">Ämbetsberättelse: %s '
               'Beslutsdatum: %s Diarienummer : %s %s</a></body></html>')


def test_parse_jo_grafts_official_report_from_the_map(tmp_path):
    (tmp_path / "jo").mkdir()
    avg_download.jo_officialreport_path(tmp_path).write_text(
        json.dumps({"1672-1987": "JO 1990/91 s. 70"}), "utf-8")
    avg_parse._officialreport_map.cache_clear()
    record = {"diary_number": "1672-1987", "post_title": "Förföljande",
              "resolve_date": "1990-06-28", "pdf_text": "Beslutets text."}
    beslut = avg_parse.parse_jo(record, tmp_path)      # no PDF: text fallback
    assert beslut.official_report == "JO 1990/91 s. 70"
    art = beslut.to_artifact(sfs_parser("avg", avg_parse.AVG_PARSE_TYPES))
    assert art["metadata"]["officialReport"] == "JO 1990/91 s. 70"
    # a dnr the map does not know stays clean
    other = avg_parse.parse_jo({"diary_number": "1-2001",
                                "post_title": "X", "pdf_text": "t"}, tmp_path)
    assert other.official_report is None
    avg_parse._officialreport_map.cache_clear()


def test_arn_catalog_and_uri():
    # the generic avg catalog row + layout path handle arn with no special-casing
    art = {"uri": "https://lagen.nu/avg/arn/1992-3657", "org": "arn",
           "identifier": "ARN 1992-3657",
           "metadata": {"title": "Fråga om återbetalning"}}
    uri, source, kind, label, title, path = catalog.document_row(art, "p.json", "avg")
    assert (source, kind, label) == ("avg", "arn", "ARN 1992-3657")
    assert layout.relpath("avg", "arn/1992-3657").as_posix() == "arn/1992-3657"


# --------------------------------------------------------------------------
# ARN -- the live harvester (arn.se vägledande-beslut listing)
# --------------------------------------------------------------------------

ARN_LISTING = (ARN_FIXTURES / "vagledande-beslut-listing.html").read_text(
    encoding="utf-8")


def test_arn_dnrs():
    # the anchor text carries the dnr; a multi-dnr referat lists several and the
    # first names the document. The embedded beslutsdatum ("2018-06-14") is not a
    # dnr -- \d{4}-\d{4,} needs 4+ trailing digits, so it is skipped.
    assert avg_download.arn_dnrs("Referat 2026-00382") == ["2026-00382"]
    assert avg_download.arn_dnrs(
        "Referat 2018-06-14; 2017-07814 (I) och 2017-13660 (II)") == \
        ["2017-07814", "2017-13660"]
    # zero-padding varies and is preserved verbatim (never normalized)
    assert avg_download.arn_dnrs("Referat 2024-00318") == ["2024-00318"]


def test_arn_parse_listing():
    items = avg_download.arn_parse_listing(ARN_LISTING)
    assert [i["dnrs"][0] for i in items] == [
        "2026-00382", "2025-06866", "2025-00318", "2024-25067", "2017-07814"]
    first = items[0]
    assert first["beslutsdatum"] == "2026-06-16"
    assert first["avdelning"] == "Motor"
    assert first["url"] == ("https://www.arn.se/globalassets/extern/pdfer/"
                            "referat-2026/arendereferat-2026-00382.pdf")
    # the summary is the title (ARN referat have no real title); the "Referat
    # NNNN" link trailer is not part of it
    assert first["title"].startswith("Frågan gällde om ett bilköp")
    assert "Referat 2026-00382" not in first["title"]
    # a summary nested in the site's div wrappers is still collected as the title
    assert items[2]["title"].startswith("ARN har kommit fram till att ett spelbolag")
    # the h3 area survives its "vägledande beslut i utökad sammansättning" quirk
    assert items[4]["avdelning"] == "Bank"
    assert items[4]["dnrs"] == ["2017-07814", "2017-13660"]


def test_parse_arn_source_url_roundtrip(tmp_path, monkeypatch):
    # one parse path, both provenances. Body extraction (poppler) is stubbed so
    # the test stays hermetic; the assertion is on metadata + source_url passthrough.
    monkeypatch.setattr(avg_parse, "pdf_pages", lambda p, patch_key=None: [])
    pdf = avg_download.arn_pdf_path(tmp_path, "arn/2026-00382")
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.7\n")
    live = {"basefile": "arn/2026-00382", "org": "arn",
            "diarienummer": "2026-00382", "beslutsdatum": "2026-06-16",
            "avdelning": "Motor", "title": "Frågan gällde om ett bilköp.",
            "source_url": ("https://www.arn.se/globalassets/extern/pdfer/"
                           "referat-2026/arendereferat-2026-00382.pdf")}
    art = avg_parse.parse_arn(live, tmp_path).to_artifact(sfs_parser("avg", avg_parse.AVG_PARSE_TYPES))
    assert art["uri"] == "https://lagen.nu/avg/arn/2026-00382"
    assert art["identifier"] == "ARN 2026-00382"
    assert art["metadata"]["beslutsdatum"] == "2026-06-16"
    assert art["metadata"]["nyckelord"] == ["Motor"]
    assert art["source_url"] == live["source_url"]
    # a frozen-import record (no source_url) parses through the same path and its
    # artifact carries no Källa link -- the legacy behaviour is unchanged
    frozen = {k: v for k, v in live.items() if k != "source_url"}
    frozen["source"] = "arn-legacy"
    frozen["imported_from"] = "2026/00382/index.pdf"
    art2 = avg_parse.parse_arn(frozen, tmp_path).to_artifact(
        sfs_parser("avg", avg_parse.AVG_PARSE_TYPES))
    assert "source_url" not in art2


def _arn_stub_request(monkeypatch, calls):
    class Resp:
        content = b"%PDF-1.7\nlive referat bytes"
    monkeypatch.setattr(avg_download, "request",
                        lambda session, method, url, **kw: calls.append(url) or Resp())
    return Resp


def test_arn_save_live_wins_over_frozen(tmp_path, monkeypatch):
    # the other half of the §7g precedence rule: legacy.import_arn refuses to
    # overwrite a live record; here the live harvest overwrites a frozen import.
    calls = []
    resp = _arn_stub_request(monkeypatch, calls)
    root, dnr = str(tmp_path), "2020-08372"
    basefile = "arn/" + dnr
    recpath = record_path(root, "arn", basefile)
    pdfpath = avg_download.arn_pdf_path(root, basefile)
    write_atomic(recpath, json.dumps(
        {"basefile": basefile, "org": "arn", "diarienummer": dnr,
         "beslutsdatum": "2020-05-05", "avdelning": "Bank",
         "title": "frozen title", "source": "arn-legacy",
         "imported_from": "2020/08372/index.doc"}))
    write_atomic(pdfpath, b"%PDF-1.4 frozen converted body")
    item = {"dnrs": [dnr], "beslutsdatum": "2020-05-05", "avdelning": "Bank",
            "title": "live summary title",
            "url": "https://www.arn.se/globalassets/extern/pdfer/referat-2021/"
                   "arendereferat-2020-08372.pdf"}
    # a frozen record never compares equal to a live one -> overwritten (live wins)
    assert avg_download.arn_save(root, item, None, 0) is True
    rec = json.loads(recpath.read_text())
    assert "source" not in rec and "imported_from" not in rec
    assert rec["source_url"] == item["url"] and rec["title"] == "live summary title"
    # the converted frozen PDF is replaced by the freshly fetched live one
    assert pdfpath.read_bytes() == resp.content
    assert calls == [item["url"]]
    # a second run over the unchanged live record fetches nothing and reports skip
    calls.clear()
    assert avg_download.arn_save(root, item, None, 0) is False
    assert calls == []


def test_arn_save_rejects_non_pdf(tmp_path, monkeypatch):
    # the magic-sniff now goes through lib.util.document_extension; a WAF/HTML
    # error page is rejected and nothing is written for the referat
    class Resp:
        content = b"<html>error</html>"
    monkeypatch.setattr(avg_download, "request", lambda *a, **kw: Resp())
    item = {"dnrs": ["2020-08372"], "beslutsdatum": "2020-05-05",
            "avdelning": "Bank", "title": "t", "url": "https://www.arn.se/x.pdf"}
    assert avg_download.arn_save(str(tmp_path), item, None, 0) is False
    assert not record_path(tmp_path, "arn", "arn/2020-08372").exists()


# --------------------------------------------------------------------------
# JO / JK -- the --full refresh fixes, ported onto lib.harvest.walk
# --------------------------------------------------------------------------

def test_jo_full_falls_through_to_jo_save(tmp_path, monkeypatch):
    # --full must re-visit an already-downloaded decision so jo_save's change
    # detection runs (the backfill branch used to `continue` before it)
    hit = {"id": 1, "diary_number": "2340-2025", "resolve_date": "2026-06-30",
           "pdf_url": None}
    write_atomic(record_path(tmp_path, "jo", "jo/2340-2025"),
                 json.dumps(avg_download.jo_record(hit, "jo/2340-2025")))
    monkeypatch.setattr(avg_download, "make_session", lambda ua: None)
    monkeypatch.setattr(avg_download, "jo_nonce", lambda session: "nonce")
    monkeypatch.setattr(avg_download, "jo_search",
                        lambda session, nonce, page, **kw: {
                            "search_hits": [hit], "total_hits": 1, "total_pages": 1})
    saved = []
    monkeypatch.setattr(avg_download, "jo_save",
                        lambda root, h, session, delay, full=False:
                        saved.append(h["diary_number"]) or False)
    seen, new = avg_download.jo_sync(str(tmp_path), full=True)
    assert saved == ["2340-2025"]         # the downloaded doc was re-visited


def test_jo_full_refetches_existing_pdf(tmp_path, monkeypatch):
    # --full must refresh an already-downloaded decision PDF (jk/arn/foreskrift
    # semantics), not just records of new decisions
    root = str(tmp_path)
    hit = {"id": 1, "diary_number": "2340-2025", "resolve_date": "2026-06-30",
           "pdf_url": "https://www.jo.se/x.pdf"}
    pdf = avg_download.jo_pdf_path(root, "jo/2340-2025")
    write_atomic(pdf, b"%PDF-1.4 old")

    class Resp:
        content = b"%PDF-1.4 fresh"
    monkeypatch.setattr(avg_download, "request", lambda *a, **kw: Resp())
    assert avg_download.jo_save(root, hit, None, 0) is True
    assert pdf.read_bytes() == b"%PDF-1.4 old"      # incremental: kept
    avg_download.jo_save(root, hit, None, 0, full=True)
    assert pdf.read_bytes() == b"%PDF-1.4 fresh"    # --full: refetched


def test_jk_full_keeps_old_landing_when_refetch_fails(tmp_path, monkeypatch):
    # --full must not pre-delete the stored landing before fetching its
    # replacement: a failed refetch has to leave the existing good record intact
    root = str(tmp_path)
    item = {"dnr_raw": "2024/8082", "beslutsdatum_raw": "20 apr 2026",
            "title": "t", "url": "https://www.jk.se/x/"}
    landing = avg_download.jk_html_path(root, "jk/2024/8082")
    write_atomic(landing, "OLD GOOD HTML")

    def boom(*a, **kw):
        raise RuntimeError("refetch failed")

    monkeypatch.setattr(avg_download, "make_session", lambda ua: None)
    monkeypatch.setattr(avg_download, "jk_listing", lambda session: [item])
    monkeypatch.setattr(avg_download, "request", boom)
    with pytest.raises(RuntimeError):
        avg_download.jk_sync(root, full=True)
    assert landing.read_text() == "OLD GOOD HTML"   # not pre-deleted


def test_jk_date_accepts_iso_passthrough():
    assert avg_parse.jk_date("1999-09-15") == "1999-09-15"
    assert avg_parse.jk_date("20 apr 2026") == "2026-04-20"


def test_jk_body_reads_the_pre_2016_skin():
    html = """<html><body>
      <div class="beslutmetadatacontainer">
        <div class="beslutmetadata">Beslutsdatum 2005-10-06</div>
        <div class="beslutmetadata">Diarienummer 930-03-21</div>
      </div>
      <h1>Justitiekanslerns beslut</h1>
      <p>Justitiekanslern uttalar kritik mot myndigheten.</p>
      <p></p>
      <p>Beslutet expedieras.</p>
    </body></html>"""
    blocks = avg_parse.jk_body(html)
    assert [(b.kind, b.text) for b in blocks] == [
        ("rubrik", "Justitiekanslerns beslut"),
        ("stycke", "Justitiekanslern uttalar kritik mot myndigheten."),
        ("stycke", "Beslutet expedieras.")]


# --------------------------------------------------------------------------
# IMY -- tillsyn pages regrouped into decisions by the diarienummer their PDFs
# print, plus the two curated pages that annotate them
# --------------------------------------------------------------------------

IMY_FIXTURES = Path(__file__).parent / "files" / "avg" / "imy"


def _imy_fixture(name):
    return (IMY_FIXTURES / name).read_text("utf-8")


def _imy_guid_map():
    return {guid.replace("-", ""): url for guid, url in
            avg_download.RE_IMY_RSS_ITEM.findall(
                _imy_fixture("tillsyner-rss.xml"))}


def test_imy_asset_name():
    # the asset path names the stored file -- it is unique across the corpus, so
    # it also folds together the documents several tillsyner link to
    assert avg_download.imy_asset_name(
        "/globalassets/dokument/beslut/2026/beslut-tillsyn-polis.pdf") == \
        "beslut-tillsyn-polis.pdf"
    # a /link/<guid>.aspx document redirect keeps its GUID as the name
    assert avg_download.imy_asset_name("/link/c75ef1b62fc94c24bdf6433ea23264f7.aspx") \
        == "c75ef1b62fc94c24bdf6433ea23264f7.pdf"
    assert avg_download.imy_asset_name("https://www.imy.se/x/y.pdf?v=2") == "y.pdf"


def test_imy_slug_tolerates_the_missing_trailing_slash():
    # the RSS drops it on some entries; the listing and curated pages keep it
    assert avg_download.imy_slug("https://www.imy.se/tillsyner/cdon-ab-cdon.fi/") \
        == "cdon-ab-cdon.fi"
    assert avg_download.imy_slug("https://www.imy.se/tillsyner/cdon-ab-cdon.fi") \
        == "cdon-ab-cdon.fi"


def test_imy_parse_listing():
    items, pages = avg_download.imy_parse_listing(
        _imy_fixture("tillsyner-listing.html"))
    assert pages == 13                       # the component's own page count
    assert [i["slug"] for i in items] == [
        "klarna-checkout", "polismyndigheten-vis", "kustbevakningen"]
    assert items[0]["title"] == "Klarna, checkout-tjänst"
    assert items[0]["status"] == "Beslut"
    # the desktop and mobile detail sections repeat the etiketter; each once
    assert items[0]["kategorier"] == ["Dataskydd", "Dina rättigheter",
                                      "Internet och appar"]


def test_imy_page_metadata_and_documents():
    html = _imy_fixture("tillsyn-polismyndigheten.html")
    meta = avg_download.imy_page_metadata(html)
    assert meta["titel"] == "Polismyndigheten, VIS och gränsförordningen"
    assert meta["ingress"].endswith("Beslut 2026-07-03.")
    assert meta["status"] == "Beslut"        # the *current* step, not the first
    assert meta["sammanfattning"].startswith("IMY konstaterar att Polismyndigheten")
    assert avg_download.imy_documents(html) == [{
        "titel": "Beslut",
        "url": "https://www.imy.se/globalassets/dokument/beslut/2026/"
               "beslut-tillsyn-polismyndigheten.pdf",
        "fil": "beslut-tillsyn-polismyndigheten.pdf", "sprak": "sv"}]


def test_imy_document_title_peels_the_link_verbiage():
    # the multi-ärende pages have no info-block heading to take a title from,
    # so the prose link's own text has to yield the subject
    from bs4 import BeautifulSoup
    def title(html):
        anchor = BeautifulSoup(html, "html.parser").find("a")
        return avg_download.imy_document_title(
            anchor, avg_download.element_text(anchor))
    assert title('<a href="/x.pdf">Läs beslutet mot Hemköp (pdf, 109 kB)</a>') \
        == "Hemköp"
    assert title('<a href="/x.pdf">Beslut i tillsyn mot Försäkringskassan '
                 '(pdf, 89 kB)</a>') == "Försäkringskassan"
    # a link text that is already the subject keeps its whole self
    assert title('<a href="/x.pdf">Ekobrottsmyndigheten (pdf, 532 kB)</a>') \
        == "Ekobrottsmyndigheten"
    # an info-block heading wins over the link text, soft hyphens removed
    assert title('<div class="imy-info-block">'
                 '<h2 class="imy-info-block__heading">Tillsyns\xadskrivelse</h2>'
                 '<a href="/x.pdf">Läs dokumentet (pdf, 1 kB)</a></div>') \
        == "Tillsynsskrivelse"


def test_imy_parse_praxis():
    curated = avg_download.imy_parse_praxis(_imy_fixture("praxisbeslut.html"),
                                            _imy_guid_map())
    # a box reached through a /link/<guid>.aspx redirect, resolved via the feed
    aspudden = curated["utbildningsnamnden-i-stockholms-stad--aspuddens-skola"]
    assert aspudden["amne"] == "Kamerabevakning"
    assert aspudden["rubrik"] == "Kamerabevakning på skola"
    # each labelled field stops at the next label -- they share one paragraph
    assert aspudden["beslutsdatum"] == "2023-10-03"
    assert aspudden["korrigerandeAtgard"] == "Sanktionsavgift på 800 000 kronor"
    assert aspudden["lagrum"].startswith("Artiklarna 6.1 c")
    assert aspudden["overklagan"] == "Nej"
    assert aspudden["lagakraft"] == "Ja"
    # one box can annotate several tillsyner; all four carry it
    tredjeland = {s for s, e in curated.items()
                  if e["amne"] == "Överföring till tredjeland"}
    assert tredjeland == {"tele2-sverige-ab-tele2.se", "cdon-ab-cdon.fi",
                          "coop-sverige-ab-coop.se", "dagens-industri-ab-di.se"}


def test_imy_praxis_rejects_an_unknown_field():
    # the curated schema growing a field must be looked at, not guessed past
    with pytest.raises(AssertionError, match="unknown field"):
        avg_download.imy_parse_praxis(
            '<div class="imy-body imy-contentpage__main-content">'
            '<div class="imy-expandable-box">'
            '<h2 class="imy-expandable-box__heading">R</h2>'
            '<p><strong>Instansordning:</strong> HFD</p></div></div>', {})


def test_imy_parse_sanktion():
    curated = avg_download.imy_parse_sanktion(
        _imy_fixture("sanktionsavgift.html"), _imy_guid_map())
    assert curated["sportadmin-i-skandinavien-ab"] == "6 miljoner kronor"
    assert curated["diskrimineringsombudsmannen-do"] == "100 000 kronor"
    # the page's own "Tillsyner och beslut" back-link is not a tillsyn
    assert "tillsyner" not in curated


def test_imy_diarienummer_reads_both_header_generations(monkeypatch, tmp_path):
    def stub(text):
        monkeypatch.setattr(avg_download, "pdf_first_page_text", lambda p: text)
        return avg_download.imy_diarienummer(tmp_path / "x.pdf")
    # the current header prints the number with its authority prefix
    assert stub("Diarienummer: IMY-2024-2904 Ert diarienummer: A276.737/2024 "
                "Datum: 2026-07-03") == ("IMY-2024-2904", "2026-07-03")
    assert stub("Beslut Diarienr 1 (5) 2020-12-02 DI-2019-3375")[0] == "DI-2019-3375"
    # pre-2018 Datainspektionen printed a bare number; only its position after
    # the "Diarienr" column head tells it from the form number left of it
    assert stub("1540-2016 Beslut Diarienr 1 (27) 2018-05-23 2248-2017") == \
        ("2248-2017", "2018-05-23")
    # the label is what the current header needs: it prints the counterparty's
    # own dated reference first, so the *first* date on the page is not the
    # decision's. Dropping the anchor would silently pick 2024-11-02 here
    assert stub("1(5) Polismyndigheten Diarienummer: IMY-2024-2904 "
                "Ert diarienummer: A276.737/2024-11-02 Datum: 2026-07-03") == \
        ("IMY-2024-2904", "2026-07-03")
    # a decision published anonymously has its number redacted -- no identity
    assert stub("Beslut Diarienr 1 (3) 2019-02-21 DI-2018-XXXX")[0] is None
    assert stub("Beslut - Avidentifierad version 1 (6) 2019-07-03")[0] is None


def test_imy_diarienummer_over_real_headers(monkeypatch, tmp_path):
    # the same two generations, this time over page-1 text extracted from real
    # decisions rather than hand-written strings (rule:lock-in-with-fixture)
    def read(name):
        monkeypatch.setattr(avg_download, "pdf_first_page_text",
                            lambda p: (IMY_FIXTURES / name).read_text("utf-8"))
        return avg_download.imy_diarienummer(tmp_path / "x.pdf")

    # the current header prints the counterparty's own reference beside IMY's
    # and labels the date, so the label is what disambiguates it
    assert read("header-imy.txt") == ("IMY-2024-2904", "2026-07-03")
    # the pre-2018 header labels nothing: bare dnr after the "Diarienr" column
    # head, decision date first
    assert read("header-di.txt") == ("1013-2015", "2017-05-02")


def _imy_page(slug, title, documents, **kw):
    return {"slug": slug, "url": "https://www.imy.se/tillsyner/%s/" % slug,
            "titel": title, "ingress": None, "status": "Beslut",
            "sammanfattning": None, "kategorier": ["Dataskydd"],
            "dokument": documents, "curated": {}, **kw}


def _imy_doc(titel, fil, dnr, datum="2020-01-01", sprak="sv"):
    return {"titel": titel, "url": "https://www.imy.se/globalassets/" + fil,
            "fil": fil, "sprak": sprak, "diarienummer": dnr,
            "beslutsdatum": datum}


def test_imy_records_regroups_documents_by_diarienummer():
    pages = [
        # a page that decides two ärenden: each becomes its own decision, named
        # by the document heading too so the two are distinguishable
        _imy_page("brottsbekampande-myndigheter", "Brottsbekämpande myndigheter",
                  [_imy_doc("Beslut mot Polismyndigheten", "a.pdf", "DI-2018-19918"),
                   _imy_doc("Beslut mot Kriminalvården", "b.pdf", "DI-2018-19919"),
                   _imy_doc("Sammanställning", "c.pdf", None)]),
        # one ärende published as two documents plus an English translation
        _imy_page("spotify", "Spotify AB",
                  [_imy_doc("Beslut", "d.pdf", "DI-2020-10541"),
                   _imy_doc("In English", "e.pdf", "DI-2020-10541", sprak="en")]),
        # a second page linking a decision the first already named: it adds a
        # tillsyn, and the same asset under a different link text is not a part
        _imy_page("kry", "Kry", [_imy_doc("Vägledning", "d.pdf", "DI-2020-10541")]),
    ]
    records, orphans = avg_download.imy_records(pages)
    by_dnr = {r["diarienummer"]: r for r in records}
    assert set(by_dnr) == {"DI-2018-19918", "DI-2018-19919", "DI-2020-10541"}
    assert by_dnr["DI-2018-19918"]["basefile"] == "imy/DI-2018-19918"
    assert by_dnr["DI-2018-19918"]["titel"] == \
        "Brottsbekämpande myndigheter – Beslut mot Polismyndigheten"
    # a page deciding one ärende keeps its own heading as the title
    assert by_dnr["DI-2020-10541"]["titel"] == "Spotify AB"
    assert [d["fil"] for d in by_dnr["DI-2020-10541"]["delar"]] == \
        ["d.pdf", "e.pdf"]
    assert [t["slug"] for t in by_dnr["DI-2020-10541"]["tillsyner"]] == \
        ["spotify", "kry"]
    # the document whose PDF prints no number cannot be filed and is reported
    assert orphans == [("brottsbekampande-myndigheter", "Sammanställning")]


def test_classify_imy():
    # font-driven: a paragraph smaller than the body is a footnote or masthead,
    # a bold one is a heading whose level is the rank of its size, consecutive
    # headings of one level are one heading (a title set across lines)
    paras = [
        Para(text="1(5)", size=12),
        Para(text="Beslut efter tillsyn enligt bl.a. VIS-", bold=True, size=30),
        Para(text="förordningen", bold=True, size=30),
        Para(text="Diarienummer:", bold=True, size=12),
        Para(text="IMY-2024-2904", size=12),
        Para(text="Integritetsskyddsmyndighetens beslut", bold=True, size=24),
        Para(text="IMY konstaterar att myndigheten brustit.", size=14),
        Para(text="Tillämpliga bestämmelser", bold=True, size=18),
        Para(text="Vilka regelverk gäller?", bold=True, size=14),
        Para(text="Av artikel 13 framgår följande.", size=14),
        Para(text="Postadress: 1 Se EU-förordningen. Box 8114", size=11),
    ]
    assert [(b.kind, b.level, b.text) for b in avg_parse.classify_imy(paras)] == [
        ("rubrik", 1, "Beslut efter tillsyn enligt bl.a. VIS-förordningen"),
        ("rubrik", 2, "Integritetsskyddsmyndighetens beslut"),
        ("stycke", 1, "IMY konstaterar att myndigheten brustit."),
        ("rubrik", 3, "Tillämpliga bestämmelser"),
        ("rubrik", 4, "Vilka regelverk gäller?"),
        ("stycke", 1, "Av artikel 13 framgår följande."),
    ]


def test_classify_imy_keeps_the_hyphens_a_broken_heading_ends_on():
    # a heading broken across lines never breaks *at* a hyphen here: a trailing
    # hyphen belongs to the term and closes up, unless it is the suspended
    # hyphen of a coordinated list, which keeps its space
    def heading(*lines):
        return avg_parse.classify_imy(
            [Para(text=t, bold=True, size=30) for t in lines]
            + [Para(text="Brödtext.", size=14)])[0].text
    assert heading("Beslut efter tillsyn enligt bl.a. VIS-",
                   "förordningen") == "Beslut efter tillsyn enligt bl.a. VIS-förordningen"
    assert heading("Tillsyn – Trygg-", "Hansa Försäkring") == \
        "Tillsyn – Trygg-Hansa Försäkring"
    assert heading("Beslut efter tillsyn enligt VIS-, SIS-",
                   "samt dataskyddsförordningen") == \
        "Beslut efter tillsyn enligt VIS-, SIS- samt dataskyddsförordningen"


def test_classify_imy_strips_the_masthead_in_place():
    # the footer is set in the margin column, so wherever a footer line shares a
    # baseline with a body line the two arrive glued -- dropping the paragraph
    # would take the prose with it, so the masthead tokens go and the prose stays
    paras = [
        Para(text="avser gallring i misstankeregistret. www.imy.se", size=14),
        Para(text="Box 8114 Personuppgiftsansvarig är enligt artikel 4.7 den "
                  "104 20 Stockholm som bestämmer ändamålen.", size=14),
        Para(text="Diarienummer: IMY-2024-2904 2(5) Datum: 2026-07-03", size=14),
        Para(text="Page 3 of 11", size=14),
    ]
    assert [b.text for b in avg_parse.classify_imy(paras)] == [
        "avser gallring i misstankeregistret.",
        "Personuppgiftsansvarig är enligt artikel 4.7 den som bestämmer ändamålen.",
    ]


def test_parse_imy_artifact(tmp_path, monkeypatch):
    # poppler is stubbed so the test stays hermetic; the assertions are on the
    # identity, the metadata carried over from the tillsyn page, and the parts
    monkeypatch.setattr(avg_parse, "pdf_pages", lambda p, patch_key=None: [])
    record = {
        "basefile": "imy/IMY-2024-2904", "org": "imy",
        "diarienummer": "IMY-2024-2904",
        "titel": "Polismyndigheten, VIS och gränsförordningen",
        "beslutsdatum": "2026-07-03",
        "ingress": "Tillsyn enligt gränsförordningen. Beslut 2026-07-03.",
        "status": "Beslut", "sammanfattning": "IMY ger en reprimand.",
        "kategorier": ["Dataskydd"],
        "tillsyner": [{"slug": "polismyndigheten-vis",
                       "url": "https://www.imy.se/tillsyner/polismyndigheten-vis/"}],
        "delar": [{"titel": "Beslut", "url": "https://www.imy.se/x/b.pdf",
                   "fil": "b.pdf", "sprak": "sv"}],
        "sanktionsavgift": "6 miljoner kronor",
        "praxis": {"lagrum": "Artikel 13", "overklagan": "Nej", "lagakraft": "Ja"}}
    pdf = avg_download.imy_pdf_path(tmp_path, "b.pdf")
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.7\n")
    art = avg_parse.parse_imy(record, tmp_path).to_artifact(
        sfs_parser("avg", avg_parse.AVG_PARSE_TYPES))
    assert art["uri"] == "https://lagen.nu/avg/imy/IMY-2024-2904"
    assert art["identifier"] == "IMY dnr IMY-2024-2904"
    assert art["org"] == "imy"
    md = art["metadata"]
    assert md["publisher"] == "Integritetsskyddsmyndigheten"
    assert md["beslutsdatum"] == "2026-07-03"
    assert md["nyckelord"] == ["Dataskydd"]
    assert md["sanktionsavgift"] == "6 miljoner kronor"
    assert md["praxis"]["lagakraft"] == "Ja"
    assert md["dokument"] == [{"titel": "Beslut",
                               "url": "https://www.imy.se/x/b.pdf", "sprak": "sv"}]
    assert art["sammanfattning"] == "IMY ger en reprimand."
    # the tillsyn page is the authoritative source a reader is sent back to
    assert art["source_url"] == \
        "https://www.imy.se/tillsyner/polismyndigheten-vis/"


def test_imy_body_skips_the_english_translation(tmp_path, monkeypatch):
    # a translation carries the same dnr as the decision it translates; reading
    # both would ship the same decision twice
    read = []
    monkeypatch.setattr(avg_parse, "pdf_pages",
                        lambda p, patch_key=None: read.append(Path(p).name) or [])
    record = {"diarienummer": "DI-2020-10541", "delar": [
        {"titel": "Beslut", "fil": "sv.pdf", "sprak": "sv"},
        {"titel": "In English", "fil": "en.pdf", "sprak": "en"}]}
    for name in ("sv.pdf", "en.pdf"):
        pdf = avg_download.imy_pdf_path(tmp_path, name)
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(b"%PDF-1.7\n")
    avg_parse.imy_body(record, tmp_path)
    assert read == ["sv.pdf"]


def test_imy_layout_catalog_and_facets():
    uri = "https://lagen.nu/avg/imy/IMY-2024-2904"
    assert layout.relpath("avg", "imy/IMY-2024-2904").as_posix() == \
        "imy/IMY-2024-2904"
    assert layout.page_relpath(uri) == "avg/imy_IMY-2024-2904.html"
    art = {"uri": uri, "org": "imy", "identifier": "IMY dnr IMY-2024-2904",
           "metadata": {"title": "Polismyndigheten, VIS och gränsförordningen"}}
    _uri, source, kind, label, title, _path = catalog.document_row(art, "p.json", "avg")
    assert (source, kind, label) == ("avg", "imy", "IMY dnr IMY-2024-2904")
    assert title == "Polismyndigheten, VIS och gränsförordningen"

    class R:
        local, kind, date = "avg/imy/IMY-2024-2904", "imy", "2026-07-03"
    # an IMY number carries the year the ärende was *opened*, so the year facet
    # keys on the decision date instead
    assert facets._avg_year(R()) == "2026"
    assert facets._avg_org(R()) == "imy"


# --------------------------------------------------------------------------
# KKV -- the diarium: a register rather than a selection, named by its own
# case numbers and published in three document formats
# --------------------------------------------------------------------------

KKV_FIXTURES = Path(__file__).parent / "files" / "avg" / "kkv"


def _kkv_listing_fixture():
    return json.loads((KKV_FIXTURES / "diarium-listing.json").read_text("utf-8"))


def _kkv_case_fixture():
    return json.loads((KKV_FIXTURES / "arendedata.json").read_text("utf-8"))["content"]


def _kkv_curated_fixture():
    return json.loads((KKV_FIXTURES / "arendelista.json").read_text("utf-8"))


def _kkv_casepage_fixture():
    return json.loads((KKV_FIXTURES / "casepage.json").read_text("utf-8"))["content"]


def test_kkv_casetypes_are_the_supervisory_ones():
    # the status filter says nothing about what kind of ärende a case is, so
    # the ärendetyp groups are what makes this a tillsyn corpus and not a
    # diarium dump. Företagskoncentrationer (49) are deliberately excluded, and
    # the ranges must not overlap or the union would double-count
    groups = avg_download.KKV_CASETYPES
    covered = {n for lo, hi in groups for n in range(int(lo), int(hi) + 1)}
    assert 49 not in covered                      # företagskoncentrationer
    assert {38, 45, 46, 51} <= covered            # the tillsyn classes
    assert sum(int(hi) - int(lo) + 1 for lo, hi in groups) == len(covered)


def test_kkv_listing_refuses_a_truncated_group(monkeypatch):
    # the diarium's paging is cumulative, so a group is asked for in one
    # response and completeness is the only check that it arrived whole. It
    # raises rather than asserts: under `python -O` an assert is removed, and
    # the short group would become the harvest's authoritative output
    monkeypatch.setattr(avg_download, "request",
                        lambda *a, **kw: {"items": [{"caseNumber": "1/2020"}],
                                          "pagination": {"total": 2}})
    with pytest.raises(ValueError, match="got 1 of 2 cases"):
        avg_download.kkv_listing(None, ("38", "38"))


def test_kkv_cases_dedupes_across_groups(monkeypatch):
    # the ranges are the *site's*, and nothing guarantees they stay disjoint
    shared = {"caseNumber": "1/2020", "subject": "a"}
    listings = iter([[shared], [shared, {"caseNumber": "2/2020", "subject": "b"}],
                     [], [], []])
    monkeypatch.setattr(avg_download, "kkv_listing",
                        lambda session, casetype: next(listings))
    assert sorted(avg_download.kkv_cases(None, 0)) == ["1/2020", "2/2020"]


def test_kkv_curated_fetches_only_the_case_a_single_run_asks_for(monkeypatch):
    # a --only run needs one account, and the listing already says which case
    # names it -- fetching the other 328 case pages to find out would be 328
    # requests for nothing
    fetched = []

    def fake_request(session, method, url, **kw):
        fetched.append(url)
        return {"content": {"heading": "X", "preamble": "", "caseBoxContents": [],
                            "text": {"fragments": []}}}

    monkeypatch.setattr(avg_download, "kkv_arendelista",
                        lambda session, delay: _kkv_curated_fixture()["items"])
    monkeypatch.setattr(avg_download, "request", fake_request)
    wanted = avg_download.kkv_curated_dnrs(
        avg_download.kkv_casebox(_kkv_curated_fixture()["items"][1]))[0]
    curated = avg_download.kkv_curated(None, 0, wanted={wanted})
    assert len(fetched) == 1                  # one case page, not three
    assert wanted in curated
    # the whole listing still resolves when nothing is narrowed away
    fetched.clear()
    assert len(avg_download.kkv_curated(None, 0)) == 4 and len(fetched) == 3


def test_kkv_curated_dnrs():
    # a fifth of the curated entries name several diarienummer -- an ärende that
    # became more than one case -- and the account belongs to every one of them
    items = _kkv_curated_fixture()["items"]
    named = [avg_download.kkv_curated_dnrs(avg_download.kkv_casebox(i))
             for i in items]
    assert named[0] == ["50/2026"]
    assert len(named[1]) > 1 and all("/" in d and not d.startswith("dnr")
                                     for d in named[1])


def test_kkv_referat():
    referat = avg_download.kkv_referat(_kkv_casepage_fixture(),
                                       "https://www.konkurrensverket.se/x/")
    assert referat["namn"] == "Aktiebolaget Svensk Bilprovning m.fl."
    assert referat["bransch"] == ["Fordon, färdmedel, resande"]
    assert referat["beslutstyp"] == ["Avskrivning"]
    assert len(referat["parter"]) > 1
    # the account is sectioned by the page's own headings, which is what makes
    # it a case history rather than a blob
    assert [a["rubrik"] for a in referat["avsnitt"]] == [
        "Vad ärendet rör", "Varför ärendet prioriterats", "Konkurrensverkets beslut"]
    assert referat["avsnitt"][0]["stycken"][0].startswith("Misstänkt konkurrens")
    # a KKV-hosted document is ours to fetch; a court's is recorded as a link
    assert referat["dokument"][0]["url"].startswith(
        "https://www.konkurrensverket.se/globalassets/")


def test_kkv_referat_splits_kkv_documents_from_court_links():
    content = {"heading": "X", "preamble": "", "caseBoxContents": [],
               "text": {"fragments": [
                   {"modelType": "HeadingFragment", "raw": "Marknadsdomstolen"},
                   {"modelType": "RawFragment",
                    "raw": '<p>Se <a href="/globalassets/x/99-0618.pdf">beslutet</a> '
                           'och <a href="http://avgoranden.domstol.se/Dom02.21.pdf">'
                           'domen</a>.</p>'}]}}
    referat = avg_download.kkv_referat(content, "https://x/")
    assert [d["url"] for d in referat["dokument"]] == \
        ["https://www.konkurrensverket.se/globalassets/x/99-0618.pdf"]
    assert [d["url"] for d in referat["externa_lankar"]] == \
        ["http://avgoranden.domstol.se/Dom02.21.pdf"]


def test_kkv_curated_record_stands_alone():
    # 138 of the 329 curated cases name a case the narrowed diarium set does not
    # carry (some predate the diarium itself), and the account is then the
    # document
    referat = avg_download.kkv_referat(_kkv_casepage_fixture(), "https://x/")
    record = avg_download.kkv_curated_record("756/2025", referat)
    assert record["basefile"] == "kkv/756/2025"
    assert record["titel"] == "Aktiebolaget Svensk Bilprovning m.fl."
    assert record["dokument"]["fil"] == "25-0756.pdf"
    assert record["referat"] is referat


def test_parse_kkv_heads_the_body_with_the_curated_account(tmp_path, monkeypatch):
    # the decision document predates the courts that later reviewed it, so
    # Konkurrensverkets own account -- which carries that history -- heads the
    # body and the decision text follows
    monkeypatch.setattr(avg_parse, "pages_with_ocr", lambda p, patch_key=None: [])
    referat = avg_download.kkv_referat(_kkv_casepage_fixture(), "https://x/")
    record = avg_download.kkv_record(_kkv_listing_fixture()["items"][0],
                                     _kkv_case_fixture(), referat)
    body = avg_download.kkv_body_path(tmp_path, record["dokument"]["fil"])
    body.parent.mkdir(parents=True, exist_ok=True)
    body.write_bytes(b"%PDF-1.7\n")
    art = avg_parse.parse_kkv(record, tmp_path).to_artifact(sfs_parser("avg", avg_parse.AVG_PARSE_TYPES))
    # the curated case name beats the diarium's bureaucratic ärendemening
    assert art["metadata"]["title"] == "Aktiebolaget Svensk Bilprovning m.fl."
    assert art["metadata"]["bransch"] == ["Fordon, färdmedel, resande"]
    assert art["metadata"]["beslutstyp"] == ["Avskrivning"]
    assert art["structure"][0]["type"] == "rubrik"
    assert "".join(r if isinstance(r, str) else r["text"]
                   for r in art["structure"][0]["text"]) == "Vad ärendet rör"
    # the branch joins the ärendetyp as a keyword, which is what browsing needs
    assert "Fordon, färdmedel, resande" in art["metadata"]["nyckelord"]


def test_kkv_asset_name():
    # the diarium's file endpoint calls every format "pdf"; the parameter is the
    # document's real name, extension and all
    name = avg_download.kkv_asset_name
    assert name("/diarium/sok-i-Konkurrensverkets-diarium/arendedata/"
                "file?pdf=26-0558.pdf") == "26-0558.pdf"
    assert name("https://www.konkurrensverket.se/diarium/sok-i-Konkurrensverkets"
                "-diarium/arendedata/file?pdf=98-0974.htm") == "98-0974.htm"
    # a name with a space survives its url-encoding
    assert name("/x/file?pdf=26-0558%20Beslut%20GDPR.pdf") == "26-0558 Beslut GDPR.pdf"
    # a name that would escape the document directory is not one. It comes off
    # a remote url and is joined onto the corpus root, so the guard raises
    # rather than asserts -- under -O an assert would vanish
    with pytest.raises(ValueError, match="plain file name"):
        name("/x/file?pdf=../../etc/passwd")


def test_kkv_date_rejects_the_diariums_placeholder():
    # an unrecorded date is written "-", which is not a date
    assert avg_download.kkv_date("2004-11-26") == "2004-11-26"
    assert avg_download.kkv_date("-") is None
    assert avg_download.kkv_date("") is None
    assert avg_download.kkv_date(None) is None


def test_kkv_record():
    item = _kkv_listing_fixture()["items"][0]
    record = avg_download.kkv_record(item, _kkv_case_fixture())
    assert record["basefile"] == "kkv/558/2026"
    assert record["diarienummer"] == "558/2026"
    assert record["titel"] == "Anmälan om företagskoncentration - bioraffinaderiverksamhet"
    assert record["arendetyp"] == "3.2.3.2 Prövning av företagskoncentration"
    assert record["motpart"] == "HV NEF2 Invest Ascona II AS"
    # the listing timestamps the *registration*; only the ärendedata page dates
    # the decision, which is the whole reason it is fetched
    assert record["registreringsdatum"] == "2026-07-22"
    assert record["beslutsdatum"] == _kkv_case_fixture()["decisionDate"]
    # the listing writes a document link site-relative, the case record absolute
    assert record["dokument"]["url"].startswith("https://www.konkurrensverket.se/")
    assert record["dokument"]["fil"] == "26-0558.pdf"


def test_kkv_record_without_a_document():
    # 31 of the 10,097 published-and-closed cases publish no document; the
    # register entry is still the case
    item = next(i for i in _kkv_listing_fixture()["items"]
                if i["caseNumber"] == "578/2015")
    record = avg_download.kkv_record(item, {})
    assert "dokument" not in record and record["beslutsdatum"] is None
    assert avg_parse.kkv_body(record, "/nonexistent") == ([], None)


def test_kkv_html_text_rejects_an_undeclared_encoding():
    # every diarium HTML decision declares windows-1252 and means it; a document
    # that stopped declaring it has changed and must not be silently mis-decoded
    # into mojibake, so this raises rather than asserts (an assert would vanish
    # under -O and the cp1252 decode below it would run anyway)
    text = avg_parse.kkv_html_text((KKV_FIXTURES / "98-0974.htm").read_bytes())
    assert "Näringsdepartementet" in text
    with pytest.raises(ValueError, match="windows-1252"):
        avg_parse.kkv_html_text(b"<html><head><meta charset=utf-8></head></html>")


def test_kkv_html_text_accepts_a_truthful_us_ascii_declaration():
    # 04-0468 is the one diarium document declaring us-ascii, and it carries
    # zero bytes over 0x7F -- ASCII is a strict subset of cp1252, so it decodes
    # identically either way and there was never anything to mojibake
    assert avg_parse.kkv_html_text(
        b"<html><head><meta charset=us-ascii></head><body>Beslut</body></html>"
    ).endswith("</html>")


def test_kkv_html_text_rejects_a_lying_us_ascii_declaration():
    # a document that declares us-ascii while carrying high bytes is lying about
    # itself, and its real encoding is unknown -- that is exactly what the
    # charset guard exists for, so widening it to us-ascii must not open this
    with pytest.raises(ValueError, match="non-ASCII"):
        avg_parse.kkv_html_text(
            "<html><head><meta charset=us-ascii></head>"
            "<body>N\u00e4ringsdepartementet</body></html>".encode("cp1252"))


def test_kkv_html_lifts_the_diariums_own_abstract():
    # the oldest generation opens with an ÄRENDE:/SAMMANF: table -- the diarium's
    # abstract *about* the letter, so it is the sammanfattning and not body
    blocks, summary = avg_parse.classify_kkv_html(
        avg_parse.kkv_html_text((KKV_FIXTURES / "98-0974.htm").read_bytes()))
    assert summary.startswith("Näringsdepartementet hade en hearing den 12 februari")
    assert not any("SAMMANF" in b.text or "ÄRENDE:" in b.text for b in blocks)
    # the body is the letter's prose, letterhead dropped
    assert blocks[0].text.startswith("Inledningsvis vill Konkurrensverket peka på")
    assert blocks[-1].kind == "stycke"


def test_kkv_html_reads_the_letterhead_as_p_generation():
    # a later generation sets the letterhead as bare <p> lines and has no
    # abstract table; the body still starts at the first real paragraph
    blocks, summary = avg_parse.classify_kkv_html(
        avg_parse.kkv_html_text((KKV_FIXTURES / "02-0968.htm").read_bytes()))
    assert summary is None
    texts = [b.text for b in blocks]
    assert not any(t.startswith("Dnr ") or t == "YTTRANDE" for t in texts)
    assert any(t.startswith("Konkurrensverket har inget att invända") for t in texts)


def test_kkv_html_headings():
    # these letters mark a section heading by nothing at all -- not even bold --
    # so a short unpunctuated digit-free line before a paragraph is one
    html = ("<html><head><meta charset=windows-1252></head><body>"
            "<p>Konkurrensverket har mottagit en anmälan i ärendet och har efter "
            "utredning kommit fram till följande slutsatser.</p>"
            "<p>Saken</p>"
            "<p>Anmälan enligt 37 § konkurrenslagen (1993:20) om företags"
            "koncentration; helikoptrar och tillhörande underhållstjänster.</p>"
            "<p>Ku2000/1259/Me</p>"
            "<p>Konkurrensverket lämnar den anmälda företagskoncentrationen utan "
            "åtgärd enligt 4 kap. 11 § konkurrenslagen.</p>"
            "</body></html>").encode("cp1252")
    blocks, _ = avg_parse.classify_kkv_html(avg_parse.kkv_html_text(html))
    kinds = {b.text: b.kind for b in blocks}
    assert kinds["Saken"] == "rubrik"
    # a ministry reference number is short and unpunctuated too, but it has
    # digits in it -- which is what tells the two apart in these letters
    assert kinds["Ku2000/1259/Me"] == "stycke"


def test_classify_kkv_pdf():
    # the same font-driven reading as IMY: the letterhead and footer are set
    # smaller than the running text, the running header carries a page mark,
    # and a bold paragraph is a heading ranked by its size
    paras = [
        Para(text="BESLUT 2026-07-29 Dnr 558/2026 1 (2)", size=15),
        Para(text="HV NEF2 Invest Ascona II AS Box 1 118 60 Stockholm", size=15),
        Para(text="Anmälan om företagskoncentration – bioraffinaderiverksamhet",
             bold=True, size=20),
        Para(text="Konkurrensverkets beslut", bold=True, size=18),
        Para(text="Konkurrensverket beslutar att lämna förvärvet utan åtgärd.",
             size=17),
        Para(text="Adress 103 85 Stockholm Telefon 08-700 16 00", size=12),
    ]
    assert [(b.kind, b.level, b.text) for b in avg_parse.classify_kkv_pdf(paras)] == [
        ("rubrik", 1, "Anmälan om företagskoncentration – bioraffinaderiverksamhet"),
        ("rubrik", 2, "Konkurrensverkets beslut"),
        ("stycke", 1, "Konkurrensverket beslutar att lämna förvärvet utan åtgärd."),
    ]


def test_parse_kkv_artifact(tmp_path, monkeypatch):
    # poppler is stubbed; the assertions are on the identity and on the register
    # fields, which are the diarium's own and are never re-derived
    monkeypatch.setattr(avg_parse, "pages_with_ocr", lambda p, patch_key=None: [])
    record = avg_download.kkv_record(_kkv_listing_fixture()["items"][0],
                                     _kkv_case_fixture())
    body = avg_download.kkv_body_path(tmp_path, record["dokument"]["fil"])
    body.parent.mkdir(parents=True, exist_ok=True)
    body.write_bytes(b"%PDF-1.7\n")
    art = avg_parse.parse_kkv(record, tmp_path).to_artifact(sfs_parser("avg", avg_parse.AVG_PARSE_TYPES))
    assert art["uri"] == "https://lagen.nu/avg/kkv/558/2026"
    assert art["identifier"] == "KKV dnr 558/2026"
    md = art["metadata"]
    assert md["publisher"] == "Konkurrensverket"
    assert md["arendetyp"] == "3.2.3.2 Prövning av företagskoncentration"
    assert md["motpart"] == "HV NEF2 Invest Ascona II AS"
    # the ärendetyp doubles as the keyword: it is what a reader browses by
    assert md["nyckelord"] == ["3.2.3.2 Prövning av företagskoncentration"]
    assert art["source_url"].endswith("arendedata/?caseNumber=558/2026")


def test_kkv_rejects_an_error_page_served_as_a_decision(tmp_path, monkeypatch):
    # a third of the corpus legitimately is HTML, so HTML cannot simply be
    # refused -- what tells a decision from an error page is that the diarium
    # names an HTML decision ".htm"
    class Resp:
        content = b"<!DOCTYPE html><html><body>Sidan hittades inte</body></html>"
    monkeypatch.setattr(avg_download, "request",
                        lambda session, method, url, **kw: Resp())
    root = str(tmp_path)
    assert avg_download.kkv_fetch_document(
        root, {"url": "https://x/file?pdf=26-0558.pdf", "fil": "26-0558.pdf"},
        None, 0) is None
    assert not compress.exists(avg_download.kkv_body_path(root, "26-0558.pdf"))
    # the same markup under the name the diarium gives its HTML decisions is one
    assert avg_download.kkv_fetch_document(
        root, {"url": "https://x/file?pdf=98-0974.htm", "fil": "98-0974.htm"},
        None, 0) is not None


def test_kkv_drops_a_document_the_diarium_refused_to_serve(tmp_path, monkeypatch):
    # an error page under a decision's name must not leave the record naming a
    # file that is not there: parse would assert on it, and the missing file
    # would keep the freshness check false and rewrite the record every run
    class Resp:
        content = b"<!DOCTYPE html><html><body>Sidan hittades inte</body></html>"
    monkeypatch.setattr(avg_download, "request",
                        lambda session, method, url, **kw: Resp())
    record = avg_download.kkv_record(_kkv_listing_fixture()["items"][0],
                                     _kkv_case_fixture())
    assert "dokument" in record
    path = record_path(tmp_path, "kkv", record["basefile"])
    assert avg_download._kkv_write(str(tmp_path), path, None, record,
                                   None, 0, False)
    assert "dokument" not in record          # dropped, not left dangling
    assert avg_parse.kkv_body(record, str(tmp_path)) == ([], None)


def test_kkv_layout_catalog_and_facets():
    # a KKV number carries a slash, exactly like JK's new-era form, so it rides
    # the storage and page grammar that already handles one
    uri = "https://lagen.nu/avg/kkv/558/2026"
    assert layout.relpath("avg", "kkv/558/2026").as_posix() == "kkv/558-2026"
    assert layout.page_relpath(uri) == "avg/kkv_558_2026.html"
    assert layout.url_to_relpath("/avg/kkv/558/2026") == "avg/kkv_558_2026.html"
    art = {"uri": uri, "org": "kkv", "identifier": "KKV dnr 558/2026",
           "metadata": {"title": "Anmälan om företagskoncentration"}}
    _uri, source, kind, label, _title, _path = catalog.document_row(art, "p.json", "avg")
    assert (source, kind, label) == ("avg", "kkv", "KKV dnr 558/2026")

    class R:
        local, kind, date = "avg/kkv/558/2026", "kkv", "2026-07-29"
    # the case number's year is when the case was *registered*; a long
    # investigation is decided years later, so the facet keys on the decision
    assert facets._avg_year(R()) == "2026"
    assert facets._avg_org(R()) == "kkv"

    class Undated:
        # a curated-only case: its account dates it by a span, not a day, so
        # there is no beslutsdatum -- the case number's year stands in rather
        # than stranding it in "okänt"
        local, kind, date = "avg/kkv/633/1997", "kkv", None
    assert facets._avg_year(Undated()) == "1997"

    class UndatedImy:
        local, kind, date = "avg/imy/DI-2018-1", "imy", None
    assert facets._avg_year(UndatedImy()) == "okänt"


def test_avg_patch_intermediate_routes_the_new_organs(tmp_path, monkeypatch):
    # a patch is authored against the *intermediate the parse reads*, so adding
    # organs to avg means teaching patchsource their document routes -- without
    # this, an imy/kkv basefile fell through to the ARN path
    from accommodanda import patchsource
    from accommodanda.lib import layout
    from accommodanda.lib.errors import SkipDocument
    monkeypatch.setattr(layout, "AVG_DOWNLOADED", tmp_path)
    monkeypatch.setattr(patchsource, "_pdf_xml", lambda p: "<pdf2xml>%s</pdf2xml>" % Path(p).name)

    def store(basefile, record, docs=()):
        compress.write_download(record_path(tmp_path, basefile.split("/")[0], basefile),
                                json.dumps(record, ensure_ascii=False))
        for path, data in docs:
            compress.write_download(path, data)

    # kkv, PDF route
    store("kkv/1/2020", {"diarienummer": "1/2020",
                         "dokument": {"fil": "20-0001.pdf", "url": "u"}},
          [(avg_download.kkv_body_path(tmp_path, "20-0001.pdf"), b"%PDF-1.7\n")])
    assert "20-0001.pdf" in patchsource._avg_intermediate("kkv/1/2020")
    # kkv, the pre-2006 HTML route -- its intermediate is the decoded markup
    store("kkv/2/2003", {"diarienummer": "2/2003",
                         "dokument": {"fil": "03-0002.htm", "url": "u"}},
          [(avg_download.kkv_body_path(tmp_path, "03-0002.htm"),
            '<html><head><meta charset=windows-1252></head><body><p>Beslut</p>'
            '</body></html>'.encode("cp1252"))])
    assert "<p>Beslut</p>" in patchsource._avg_intermediate("kkv/2/2003")
    # a case with no document, and a Word one, are refused with a reason rather
    # than routed to a path that does not exist
    store("kkv/3/2015", {"diarienummer": "3/2015"})
    with pytest.raises(SkipDocument, match="no document"):
        patchsource._avg_intermediate("kkv/3/2015")
    store("kkv/4/2015", {"diarienummer": "4/2015",
                         "dokument": {"fil": "15-0004.docx", "url": "u"}},
          [(avg_download.kkv_body_path(tmp_path, "15-0004.docx"), b"PK\x03\x04zzzz")])
    with pytest.raises(SkipDocument, match="Word"):
        patchsource._avg_intermediate("kkv/4/2015")

    # imy: one Swedish part is patchable; several are not, because parse threads
    # the same patch through every part and it could only apply to one
    store("imy/IMY-1", {"diarienummer": "IMY-1",
                        "delar": [{"fil": "a.pdf", "sprak": "sv"},
                                  {"fil": "b.pdf", "sprak": "en"}]},
          [(avg_download.imy_pdf_path(tmp_path, "a.pdf"), b"%PDF-1.7\n")])
    assert "a.pdf" in patchsource._avg_intermediate("imy/IMY-1")
    store("imy/IMY-2", {"diarienummer": "IMY-2",
                        "delar": [{"fil": "c.pdf", "sprak": "sv"},
                                  {"fil": "d.pdf", "sprak": "sv"}]})
    with pytest.raises(SkipDocument, match="one patch cannot span"):
        patchsource._avg_intermediate("imy/IMY-2")


# --------------------------------------------------------------------------
# footnotes -- the citations the block classifier used to discard
# --------------------------------------------------------------------------

def test_imy_footnotes_carry_the_number_that_identifies_a_named_vagledning():
    """Regression. IMY names a vägledning in prose ("Europeiska
    dataskyddsstyrelsens riktlinjer om samtycke") and grounds it with the number
    in the note below. `classify_letterhead` drops everything set below the
    running size, so 43 of the 83 IMY-beslut naming this guidance carried its
    number and none of those numbers reached the artifact -- which is why the
    IMY→EDPB citation graph was empty."""
    stream = [Para(text="IMY konstaterar följande i ärendet.", size=17)] * 5 + [
        Para(text="12 Europeiska dataskyddsstyrelsens riktlinjer 05/2020 om "
                  "samtycke, punkt 42.", size=9),
        Para(text="Postadress: Box 8114", size=9)]        # the masthead
    notes = avg_parse._footnotes_font_driven(
        stream, avg_parse.RE_IMY_MARGIN, avg_parse.RE_IMY_MASTHEAD)
    assert [(f.mark, f.text) for f in notes] == [
        ("12", "Europeiska dataskyddsstyrelsens riktlinjer 05/2020 om "
               "samtycke, punkt 42.")]


class _NoRefs:
    def parse_text(self, text, context=None):
        return []


def test_a_beslut_without_notes_carries_no_footnotes_key():
    art = Beslut(org="jo", diarienummer=["1-2020"], titel="X",
                 body=[Block("stycke", "Text.")]).to_artifact(_NoRefs())
    assert "footnotes" not in art


def test_footnotes_are_citation_scanned_onto_the_artifact():
    art = Beslut(org="imy", diarienummer=["IMY-2024-1"], titel="X",
                 body=[Block("stycke", "Se nedan.")],
                 fotnoter=[Fotnot("12", "Se riktlinjer 05/2020 och WP 248.")],
                 ).to_artifact(sfs_parser("avg", avg_parse.AVG_PARSE_TYPES))
    assert [x["uri"] for x in art["footnotes"][0]["text"] if isinstance(x, dict)] \
        == ["https://lagen.nu/edpb/riktlinjer/05-2020",
            "https://lagen.nu/edpb/wp/248"]
    assert art["footnotes"][0]["mark"] == "12"
