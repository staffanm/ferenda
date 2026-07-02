"""avg vertical (JO + JK myndighetsavgöranden): identity, download parsing,
body classification, artifact projection, layout/catalog wiring.

Hermetic: synthetic fixtures modelled on the live 2026 sites (jo.se WordPress
search hits, jk.se Umbraco landing pages); no network, no poppler."""

import json

import pytest

from accommodanda.avg import download as avg_download
from accommodanda.avg import parse as avg_parse
from accommodanda.avg.model import Beslut, Block, beslut_uri
from accommodanda.lib import catalog, facets, layout
from accommodanda.lib.lagrum import MYNDIGHETSBESLUT, LagrumParser
from accommodanda.lib.pdftext import Para


# --------------------------------------------------------------------------
# identity -- the document URI is what a citation mints, by construction
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,org,dnr", [
    ("se JO:s beslut den 30 juni 2026, dnr 2340-2025", "jo", "2340-2025"),
    ("jfr JO 1995/96 s. 92, dnr 3067-1994", "jo", "3067-1994"),
    ("Justitiekanslerns beslut med dnr 3497-06-40", "jk", "3497-06-40"),
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
    from accommodanda.lib.util import list_basefiles, record_path, write_atomic
    write_atomic(record_path(tmp_path, "jk", record["basefile"]),
                 json.dumps(record))
    assert list_basefiles(tmp_path, "jk") == ["jk/2024/8082"]
