"""avg vertical (JO + JK myndighetsavgöranden): identity, download parsing,
body classification, artifact projection, layout/catalog wiring.

Hermetic: synthetic fixtures modelled on the live 2026 sites (jo.se WordPress
search hits, jk.se Umbraco landing pages); no network, no poppler."""

import json
from pathlib import Path

import pytest

from accommodanda.avg import download as avg_download
from accommodanda.avg import legacy as avg_legacy
from accommodanda.avg import parse as avg_parse
from accommodanda.avg.model import Beslut, Block, beslut_uri
from accommodanda.lib import catalog, compress, facets, layout
from accommodanda.lib.lagrum import MYNDIGHETSBESLUT, LagrumParser
from accommodanda.lib.pdftext import Para
from accommodanda.lib.util import document_extension, record_path, write_atomic

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
        avg_parse._fresh_parser())
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
    art = beslut.to_artifact(avg_parse._fresh_parser())
    runs = [r for b in art["structure"] for r in b["text"] if isinstance(r, dict)]
    assert any(r["uri"] == "https://lagen.nu/1974:152#K2P6" for r in runs)


def test_jo_multi_dnr():
    # a decision on joined complaints carries several dnr; the first names it
    assert avg_download.jo_dnrs("6356-2012 6488-2012") == \
        ["6356-2012", "6488-2012"]
    beslut = Beslut(org="jo", diarienummer=["6356-2012", "6488-2012"],
                    titel="x", body=[Block("stycke", "text")])
    assert beslut.uri == "https://lagen.nu/avg/jo/6356-2012"
    art = beslut.to_artifact(avg_parse._fresh_parser())
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
    uri, source, kind, label, title, path = catalog.avg_document(art, "p.json")
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
# ARN -- the frozen third organ (imported by avg/legacy.py)
# --------------------------------------------------------------------------

def _fragment(dnr):
    return (ARN_FIXTURES / (dnr + ".html")).read_text(encoding="utf-8")


def test_arn_parse_fragment():
    # 1992-3657: metadata cells + a title whose trailing self-citation uses the
    # 4-digit-year date form (with the corpus's stray internal space "11- 12")
    meta = avg_legacy.parse_fragment(_fragment("1992-3657"))
    assert meta["dnr"] == "1992-3657"
    assert meta["beslutsdatum"] == "1992-11-12"
    assert meta["avdelning"] == "Resor"
    assert meta["title"].startswith("Då det på grund av lastbilsstrejk")
    # the "Avgörande 1992-11- 12; 92-3657." self-citation is stripped from the end
    assert meta["title"].endswith("outnyttjade delen av resan.")
    assert "Avgörande" not in meta["title"]


def test_arn_title_sanitization_two_digit_year():
    # 1992-1536's self-citation uses the 2-digit-year form "Avgörande 92-09-21;
    # 92-1536." -- also stripped, anchored to the end
    meta = avg_legacy.parse_fragment(_fragment("1992-1536"))
    assert meta["dnr"] == "1992-1536"
    assert meta["title"].endswith("skadeståndsbeloppets storlek.")
    assert "92-1536" not in meta["title"]


def test_arn_title_sanitization_colon_form():
    # 1991-4398's self-citation uses the colon, space-separated form the old
    # regex missed entirely: "Avgörande: 1991-12-05 91-4398" (no semicolon)
    meta = avg_legacy.parse_fragment(_fragment("1991-4398"))
    assert meta["dnr"] == "1991-4398"
    assert meta["title"] == "Fråga om nyttoavdrag vid hävning av bilköp."


@pytest.mark.parametrize("summary,expected", [
    # the dominant "; " form, 4- and 2-digit years
    ("Text. Avgörande 1992-11-12; 1992-3657.", "Text."),
    ("Text. Avgörande 92-09-21; 92-1536.", "Text."),
    # colon after Avgörande, space-separated dnr
    ("Text. Avgörande: 1991-12-05 91-4398", "Text."),
    # comma separator, parenthesised dnr, stray internal date space
    ("Text. Avgörande 2001-08-28, 2001-0438", "Text."),
    ("Text. Avgörande: 1998-11-02 (98-1207)", "Text."),
    ("Text. Avgörande 1992-11- 12; 92-3657.", "Text."),
    # "Änr" word before the dnr, and a multi-dnr "och" list
    ("Text. Avgörande 1999-05-05; Änr 1999-0677.", "Text."),
    ("Text. Avgörande 2001-08-28; 2000-4837 och 2001-0438", "Text."),
    # reversed order: dnr before the date
    ("Text. Avgörande 1992-3657; 1992-11-12", "Text."),
    # a citation embedded mid-summary (the fragment's summary div swallowed the
    # whole decision body, so the citation is followed by a long tail) is left
    # intact -- stripping it would delete the entire decision text
    ("Fråga om x. Avgörande 1995-09-28; 95-2094 " + "Bakgrunden var att " * 12,
     "Fråga om x. Avgörande 1995-09-28; 95-2094 "
     + ("Bakgrunden var att " * 12).strip()),
])
def test_arn_self_citation_regex(summary, expected):
    assert avg_legacy.RE_SELF_CITE.sub("", summary).strip() == expected


def test_arn_empty_summary():
    # 1993-3084 carries a blank summary paragraph -> an empty title (the parse
    # importer pairs this with an empty body to detect the excised stub)
    meta = avg_legacy.parse_fragment(_fragment("1993-3084"))
    assert meta["dnr"] == "1993-3084"
    assert meta["beslutsdatum"] == "1993-11-11"
    assert meta["title"] == ""


def test_document_extension_magic():
    assert document_extension(b"%PDF-1.4") == ".pdf"
    assert document_extension(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1") == ".doc"
    assert document_extension(b"\xffWPC\x5e\x00\x00\x00") == ".wpd"    # WordPerfect
    assert document_extension(b"{\\rtf1") == ".rtf"
    assert document_extension(b"<!DOCTYPE HTML PUBLIC") is None        # error page


def test_arn_pick_body_prefers_valid_doc_over_corrupt_pdf(tmp_path):
    # the five 2001 cases store a Digiforms HTML error page as index.pdf; the real
    # decision is the sibling index.doc. Selection sniffs magic bytes, so the
    # mislabelled .pdf is rejected and the valid .doc chosen.
    (tmp_path / "index.pdf").write_bytes(b"<!DOCTYPE HTML PUBLIC \"-//W3C//\">")
    (tmp_path / "index.doc").write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest")
    chosen, ext = avg_legacy.pick_body(tmp_path)
    assert (chosen.name, ext) == ("index.doc", ".doc")


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
    art = beslut.to_artifact(avg_parse._fresh_parser())
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


def _jo_frozen_case(source, year, num, dnr, title, date, citation=None,
                    headnote_dnr=None, headnote_title=None):
    d = source / "distilled" / year
    d.mkdir(parents=True, exist_ok=True)
    cit = ("<dcterms:bibliographicCitation>%s</dcterms:bibliographicCitation>"
           % citation if citation else "")
    (d / (num + ".rdf")).write_text(JO_RDF % dict(
        dnr=dnr, title=title, date=date, citation=cit), "utf-8")
    case = source / "downloaded" / year / num
    case.mkdir(parents=True, exist_ok=True)
    (case / "index.pdf").write_bytes(b"%PDF-1.4 frozen body")
    if headnote_dnr:
        (case / "headnote.html").write_text(JO_HEADNOTE % (
            citation or "-", date, headnote_dnr, headnote_title or ""), "utf-8")


def test_import_jo_maps_citations_and_imports_only_missing(tmp_path):
    source, root = tmp_path / "frozen", tmp_path / "root"
    (root / "jo").mkdir(parents=True)
    # covered by live: a live record carries the dnr -> map only, no import
    write_atomic(root / "jo" / "jo-1672-1987.json", json.dumps(
        {"diary_number": "1672-1987", "post_title": "Live"}).encode())
    _jo_frozen_case(source, "1987", "1672", "1672-1987", "Förföljande",
                    "1990-06-28", citation="JO 1990/91 s. 70")
    # garbled identity: the RDF says 2484-2487 but the headnote knows the
    # true dnr, which a live record covers -> mapped onto it, not imported
    write_atomic(root / "jo" / "jo-2484-2001.json", json.dumps(
        {"diary_number": "2484-2001", "post_title": "Live"}).encode())
    _jo_frozen_case(source, "2487", "2484", "2484-2487", "Initiativet",
                    "2002-02-28", citation="JO 2002/03 s. 357",
                    headnote_dnr="2484-2001", headnote_title="Handläggning")
    # genuinely missing -> imported, named by the headnote identity/title
    _jo_frozen_case(source, "2000", "2450", "2450-2000", "rdf-titel",
                    "2002-02-14", headnote_dnr="2450-2000",
                    headnote_title="Kritik mot en överförmyndare")
    mapped, imported, skipped = avg_legacy.import_jo(source, root,
                                                     log=lambda *a: None)
    assert (mapped, imported, skipped) == (2, 1, 0)
    report = json.loads(
        avg_legacy.jo_officialreport_path(root).read_text("utf-8"))
    assert report["1672-1987"] == "JO 1990/91 s. 70"
    assert report["2484-2001"] == "JO 2002/03 s. 357"   # the live identity
    rec = json.loads((root / "jo" / "jo-2450-2000.json").read_text())
    assert rec["source"] == "jo-legacy"
    assert rec["diary_number"] == "2450-2000"
    assert rec["post_title"] == "Kritik mot en överförmyndare"
    assert (root / "jo" / "jo-2450-2000.pdf").read_bytes().startswith(b"%PDF")
    # idempotent: a re-run imports nothing new and never touches live records
    assert avg_legacy.import_jo(source, root, log=lambda *a: None) == (2, 0, 1)


def test_parse_jo_grafts_official_report_from_the_map(tmp_path):
    (tmp_path / "jo").mkdir()
    avg_legacy.jo_officialreport_path(tmp_path).write_text(
        json.dumps({"1672-1987": "JO 1990/91 s. 70"}), "utf-8")
    avg_parse._officialreport_map.cache_clear()
    record = {"diary_number": "1672-1987", "post_title": "Förföljande",
              "resolve_date": "1990-06-28", "pdf_text": "Beslutets text."}
    beslut = avg_parse.parse_jo(record, tmp_path)      # no PDF: text fallback
    assert beslut.official_report == "JO 1990/91 s. 70"
    art = beslut.to_artifact(avg_parse._fresh_parser())
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
    uri, source, kind, label, title, path = catalog.avg_document(art, "p.json")
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
    pdf = avg_legacy.arn_pdf_path(tmp_path, "arn/2026-00382")
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.7\n")
    live = {"basefile": "arn/2026-00382", "org": "arn",
            "diarienummer": "2026-00382", "beslutsdatum": "2026-06-16",
            "avdelning": "Motor", "title": "Frågan gällde om ett bilköp.",
            "source_url": ("https://www.arn.se/globalassets/extern/pdfer/"
                           "referat-2026/arendereferat-2026-00382.pdf")}
    art = avg_parse.parse_arn(live, tmp_path).to_artifact(avg_parse._fresh_parser())
    assert art["uri"] == "https://lagen.nu/avg/arn/2026-00382"
    assert art["identifier"] == "ARN 2026-00382"
    assert art["metadata"]["beslutsdatum"] == "2026-06-16"
    assert art["metadata"]["nyckelord"] == ["Motor"]
    assert art["source_url"] == live["source_url"]
    # a frozen-import record (no source_url) parses through the same path and its
    # artifact carries no Källa link -- the legacy behaviour is unchanged
    frozen = {k: v for k, v in live.items() if k != "source_url"}
    frozen["source"] = avg_legacy.SOURCE
    frozen["imported_from"] = "2026/00382/index.pdf"
    art2 = avg_parse.parse_arn(frozen, tmp_path).to_artifact(
        avg_parse._fresh_parser())
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
    pdfpath = avg_legacy.arn_pdf_path(root, basefile)
    write_atomic(recpath, json.dumps(
        {"basefile": basefile, "org": "arn", "diarienummer": dnr,
         "beslutsdatum": "2020-05-05", "avdelning": "Bank",
         "title": "frozen title", "source": avg_legacy.SOURCE,
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


# --- jk-legacy import (the legacy-corpus sweep) ------------------------------

JK_RDF = """<?xml version="1.0" encoding="utf-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:dcterms="http://purl.org/dc/terms/"
         xmlns:rpubl="http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#">
  <rpubl:VagledandeMyndighetsavgorande rdf:about="https://lagen.nu/avg/jk/859-97-21">
    <dcterms:title xml:lang="sv">Klagomål mot en lantmäterimyndighet</dcterms:title>
    <rpubl:beslutsdatum>1999-09-15</rpubl:beslutsdatum>
  </rpubl:VagledandeMyndighetsavgorande>
</rdf:RDF>"""


def _jk_frozen(tmp_path):
    src = tmp_path / "frozen"
    for sub in ("entries/1997", "downloaded/1997", "distilled/1997"):
        (src / sub).mkdir(parents=True)
    (src / "entries/1997/859-97-21.json").write_text(json.dumps(
        {"basefile": "859-97-21", "orig_url": "https://www.jk.se/old/859-97-21"}))
    (src / "downloaded/1997/859-97-21.html").write_text("<html>beslut</html>")
    (src / "distilled/1997/859-97-21.rdf").write_text(JK_RDF, encoding="utf-8")
    return src


def test_import_jk_imports_only_uncovered_and_is_idempotent(tmp_path):
    src = _jk_frozen(tmp_path)
    root = tmp_path / "avg"
    (root / "jk").mkdir(parents=True)
    assert avg_legacy.import_jk(src, root, log=lambda *a: None) == (1, 0)
    rec = json.loads(compress.read_text(root / "jk" / "jk-859-97-21.json"))
    assert rec["source"] == "jk-legacy"
    assert rec["title"] == "Klagomål mot en lantmäterimyndighet"
    assert rec["beslutsdatum_raw"] == "1999-09-15"
    assert compress.exists(root / "jk" / "jk-859-97-21.html")
    # second run: its own record does not count as live, but should_write skips
    assert avg_legacy.import_jk(src, root, log=lambda *a: None) == (0, 1)
    # a live record covering the dnr in the dotted display form blocks import
    compress.write_download(root / "jk" / "jk-859-97-21.json", json.dumps(
        {"basefile": "jk/859-97-2.1", "org": "jk",
         "diarienummer_raw": "859-97-2.1"}))
    assert avg_legacy.import_jk(src, root, log=lambda *a: None) == (0, 0)


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
