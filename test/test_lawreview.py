"""lawreview vertical (tidskriftsartiklar: the nine journals of
`journals.py`): the listing readers off the journals' own pages (issue-page
templates, archive pages and cross-year listings alike), the issue number
rules, and the parse's mining contract (the whole text survives to the
citation scanner, nothing is re-typeset).

Hermetic: the fixtures under ``test/files/lawreview/`` are trimmed captures
of the live listing, issue and article pages, and the PDF parses are three
minted two-leaf documents: ``jp-sartryck-sample.pdf`` (a cover leaf and the
article's first leaf with its "sida 37" footer, reused as a generic body
where the journal reads its page off the record), ``ft-oa-sample.pdf``
(the first leaf prints the issue's running table of contents, the article's
line ending in a leader whose page the conversion sets on the next line)
and ``siplr-sample.pdf`` (an article whose first leaf prints its
``– 5 –`` footer, the page's running head set after it).
The jp host's WAF ride-out is tested where the rule now lives:
`test/test_net.py`.
"""

import json
import shutil
import threading
from pathlib import Path

import pytest
import requests

from ferenda.build import SOURCE_RENDERERS, UNSEARCHED
from ferenda.lawreview import download, euar, ft, lod, njel, nmt, parse, siplr, urt
from ferenda.lawreview.journals import BY_KOD, JOURNALS
from ferenda.lawreview.model import Artikel
from ferenda.lib import compress, facets, feeds, harvest, page, render

FILES = Path(__file__).parent / "files" / "lawreview"


def _read(name):
    return (FILES / name).read_text(encoding="utf-8")


class _FakeResponse:
    def __init__(self, html):
        self.text, self.content = html, html.encode("utf-8")


FAKE_PDF = b"%PDF-1.4\n%%EOF"


class _FakePdf:
    def __init__(self, body=FAKE_PDF):
        self.content, self.text = body, ""


def _serve(module, monkeypatch, html):
    """One listing page as the only page the walk's session gets."""
    monkeypatch.setattr(module.net, "make_session", lambda ua: object())
    monkeypatch.setattr(module.net, "request",
                        lambda session, method, url: _FakeResponse(html))


# --------------------------------------------------------------------------
# the svjt listing reader
# --------------------------------------------------------------------------

class TestSvjtRecords:
    def test_records_off_a_year_page(self):
        records = download._svjt_records_from_page(
            _read("svjt-arkiv-2026.html"), "2026")
        assert [r["basefile"] for r in records] == ["svjt/2026-104",
                                                    "svjt/2026-200"]
        first, second = records
        assert first["titel"] == "Bortfall av hemfesteringsrätt"
        # the card set the name in capitals; the record states it title-cased
        assert first["fattare"] == "Anna Smith-Olofsson"
        assert first["sammanfattning"].startswith("En studie av")
        assert first["source_url"] == "https://svjt.se/svjt/2026/104"
        assert first["document_url"].endswith("SvJT2026hft1s104Bortfall.pdf")
        # a name the card set in its own case is kept as it stands
        assert second["fattare"] == "Jacob Lindqvist"
        assert second["document_url"] is None
        assert second["sammanfattning"] is None

    def test_a_card_with_no_title_refuses_the_year(self):
        with pytest.raises(ValueError, match="names no title"):
            download._svjt_records_from_page(
                _read("svjt-arkiv-2026-broken.html"), "2026")

    def test_a_card_naming_another_year_is_taken_off_its_own_link(self):
        # the 1916 archive page hosts one promoted card that names a 1941
        # article no other page lists: the record takes its year and page
        # off the card's own link, not off the page the card sits on
        html = """<div class="article-grid-item"><article>
          <a href="/svjt/1941/230">
            <div class="article-summary">
              <h2 class="text-card-title">EN ARTIKEL FR\u00c5N 1941</h2>
              <p class="text-card-body"></p>
            </div></a>
          <div class="article-meta">
            <span class="author">\u00c5KE MALMSTR\u00d6M</span>
          </div>
        </article></div>"""
        records = download._svjt_records_from_page(html, "1916")
        assert [r["basefile"] for r in records] == ["svjt/1941-230"]
        record = records[0]
        assert record["year"] == "1941"
        assert record["issue"] == "230"
        assert record["source_url"] == "https://svjt.se/svjt/1941/230"
        assert record["fattare"] == "\u00c5ke Malmstr\u00f6m"


# --------------------------------------------------------------------------
# the svjt harvest watermark: a caught-up run stops short of the depth
# --------------------------------------------------------------------------

def _year_page(year, pagenum, title):
    """One svjt year page as its archive sets it: a single article card whose
    link names the article's own page."""
    return (
        '<html><body><div class="article-grid"><div class="article-grid-item">'
        '<article><div class="article-content">'
        '<h2 class="text-card-title"><a href="/svjt/%s/%s">%s</a></h2>'
        '<div class="article-meta"><span class="author">TEST AUTHOR</span></div>'
        '</div></article></div></div></body></html>'
        % (year, pagenum, title))


class TestSvjtWatermark:
    ARCHIVE = ('<html><body><select>'
               '<option value="2026">2026</option>'
               '<option value="2025">2025</option>'
               '<option value="2024">2024</option>'
               '</select></body></html>')
    YEARS = {
        "2026": _year_page("2026", "104", "Nyaste artikeln"),
        "2025": _year_page("2025", "50", "Föregående artikel"),
        "2024": _year_page("2024", "5", "Gammal artikel"),
    }
    ARTICLE = ('<html><body><div class="body"><p>Minad text.</p>'
               '</div></body></html>')

    def _fake_request(self, fetched):
        def request(session, method, url, **kwargs):
            fetched.append(url)
            if url.endswith("/arkiv"):
                return _FakeResponse(self.ARCHIVE)
            for year, year_html in self.YEARS.items():
                if url.endswith("/arkiv/" + year):
                    return _FakeResponse(year_html)
            return _FakeResponse(self.ARTICLE)
        return request

    def test_a_caught_up_run_stops_short_of_the_depth(self, monkeypatch,
                                                      tmp_path):
        monkeypatch.setattr(download.net, "make_session", lambda ua: object())
        fetched = []
        monkeypatch.setattr(download.net, "request", self._fake_request(fetched))

        # first run: empty store, so backfill walks the whole depth (all three
        # years) and stores every article; the watermark completes clean
        seen, new = download.svjt_sync(tmp_path, delay=0.0)
        assert (seen, new) == (3, 3)
        assert any(u.endswith("/arkiv/2024") for u in fetched)
        wm = json.loads((tmp_path / "svjt" / ".watermark.json").read_text())
        assert wm["dirty"] is False and wm["last_harvest"] is not None

        # second run, caught up: the newest year is all already stored, so the
        # walk reads the newest year page, hits the year whose date sits past
        # the safety boundary and stops there -- it never reads the oldest
        # year page and stores nothing
        fetched.clear()
        seen, new = download.svjt_sync(tmp_path, delay=0.0)
        assert (seen, new) == (2, 0)
        assert any(u.endswith("/arkiv/2026") for u in fetched)
        assert not any(u.endswith("/arkiv/2024") for u in fetched)


# --------------------------------------------------------------------------
# the jp issue rules and listing reader
# --------------------------------------------------------------------------

class TestJpIssueCode:
    @pytest.mark.parametrize("slug, label, expected", [
        # the plain slugs carry the number and year
        ("nummer-012026", "Häfte 1/2026", ("01", "2026")),
        ("nummer-022013", "Häfte 2/2013", ("02", "2013")),
        # WordPress renamed a colliding slug; the digits still lead
        ("nummer-012013-2", "Häfte 1/2013", ("01", "2013")),
        # the two jubileumsnummer slugs carry no number: the year comes
        # off the label that states it
        ("nummer-jubileum", "Jubileumsnummer 2014", ("J", "2014")),
        ("jubileumsnummer-2019", "Jubileumsnummer 2019", ("J", "2019")),
    ])
    def test_issue_code(self, slug, label, expected):
        assert download._jp_issue_code(slug, label) == expected

    def test_a_slug_with_no_number_and_no_year_refuses(self):
        with pytest.raises(ValueError, match="no jp issue code"):
            download._jp_issue_code("nummer-01", "Häfte 1")


class TestJpRecords:
    def test_records_off_an_issue_page(self):
        records = download._jp_records_from_page(
            _read("jp-nummer-012026.html"), "nummer-012026",
            "Häfte 1/2026")
        # the table-of-contents block names no PDF, so it is not an
        # article and takes no sequence number
        assert [r["basefile"] for r in records] == [
            "jp/2026-01-01", "jp/2026-01-02", "jp/2026-01-03"]
        inledning, artikel, sista = records
        # the Inledning section's one article is the editors' own
        assert inledning["kind"] == "inledning"
        assert inledning["titel"] == "Chefredaktörerna har ordet"
        assert inledning["fattare"] == "Max Granath & Joen Marklund"
        assert inledning["sammanfattning"] is None
        assert artikel["kind"] is None
        assert artikel["titel"] == "En artikel om hemfesteringsrätt"
        assert artikel["fattare"] == "Alice Andersson"
        assert artikel["sammanfattning"].startswith("Artikeln behandlar")
        assert artikel["document_url"].endswith("artikel1.pdf")
        assert sista["fattare"] is None
        assert sista["sammanfattning"] is None

    def test_an_issue_page_with_no_articles_refuses(self):
        # a challenged or template-less page serves a wrapper with no article
        with pytest.raises(ValueError, match="no article names a PDF"):
            download._jp_records_from_page(
                "<html><body><div class='entry-content-wrapper'></div>"
                "</body></html>", "nummer-012026", "Häfte 1/2026")

    def test_records_off_an_old_template_issue_page(self):
        # through ~2016 the journal set the issue page in another template:
        # the blocks sit in column wrappers, and the title, the abstract and
        # the italic author share one paragraph of line-broken runs
        records = download._jp_records_from_page(
            _read("jp-nummer-012016.html"), "nummer-012016", "Häfte 1/2016")
        assert [r["basefile"] for r in records] == [
            "jp/2016-01-%02d" % i for i in range(1, 8)]
        inledning, big_data = records[0], records[1]
        assert inledning["kind"] == "inledning"
        assert inledning["titel"] == "Chefredaktören har ordet"
        assert inledning["fattare"] == "Ludvig Berglönn"
        # the one paragraph held title and author only, so no abstract
        assert inledning["sammanfattning"] is None
        assert big_data["titel"] == "Big Data"
        assert big_data["fattare"] == "Eirik Jungar"
        assert big_data["sammanfattning"].startswith(
            "Data protection is not a new phenomenon.")


class TestJpSync:
    MENU = ("<html><body>"
            '<a href="https://juridiskpublikation.se/tidskriften/nummer-022026/">'
            "Nummer 2/2026</a>"
            '<a href="https://juridiskpublikation.se/tidskriften/nummer-012026/">'
            "Nummer 1/2026</a>"
            "</body></html>")
    # one article: the title is the PDF link, the author the italic mark
    ISSUE = ("<html><body>"
             '<div class="entry-content-wrapper">'
             '<section class="av_textblock_section">'
             '<a href="https://x/artikel1.pdf">'
             "<strong>En artikel</strong></a>"
             "<p><em>En Fattare</em></p>"
             "</section></div></body></html>")

    def _request(self, monkeypatch):
        def request(session, method, url):
            if url.endswith(".pdf"):
                return _FakePdf()
            if "nummer-" in url:
                # the first issue serves no page: no entry content wrapper
                if "nummer-022026" in url:
                    return _FakeResponse("<html><body></body></html>")
                return _FakeResponse(self.ISSUE)
            return _FakeResponse(self.MENU)
        monkeypatch.setattr(download.net, "make_session", lambda ua: object())
        monkeypatch.setattr(download.net, "request", request)

    def test_a_failing_issue_page_becomes_a_skip(self, monkeypatch, tmp_path,
                                                 capsys):
        # a challenged or template-less issue page must not stop the sweep:
        # it rides into the walk as a Skip, the run logs it and goes on, and
        # the store stays dirty until a run walks clean
        self._request(monkeypatch)
        seen, new = download.jp_sync(tmp_path, delay=0)
        out = capsys.readouterr().out
        assert (seen, new) == (1, 1)
        # the broken page is logged as a skip, and the walk stored the live
        # issue's article behind it
        assert "served no article list" in out
        assert "nummer-022026" in out
        assert (tmp_path / "jp" / "jp-2026-01-01.json").is_file()
        # a run with a skip is not clean: the store stays dirty
        mark = json.loads((tmp_path / "jp" / ".watermark.json")
                          .read_text(encoding="utf-8"))
        assert mark["dirty"] is True

    def test_an_unknown_slug_fails_the_run(self, monkeypatch, tmp_path):
        # a slug shape the registry does not hold is a code gap, not an
        # issue to skip: the run fails loud before any page is fetched
        monkeypatch.setattr(download.net, "make_session", lambda ua: object())
        monkeypatch.setattr(download, "_jp_issues",
                            lambda s: [("specialnummer-x", "Specialnummer")])
        with pytest.raises(ValueError, match="no jp issue code"):
            download.jp_sync(tmp_path)

    # the newest issue sets three articles, enough for the walk's stop
    ISSUE_LATEST = ("<html><body>"
                    '<div class="entry-content-wrapper">'
                    + "".join(
                        '<section class="av_textblock_section">'
                        '<a href="https://x/l%d.pdf"><strong>L%d</strong></a>'
                        "</section>" % (i, i)
                        for i in (1, 2, 3))
                    + "</div></body></html>")

    def _live_request(self, monkeypatch, made):
        def request(session, method, url):
            made.append(url)
            if url.endswith(".pdf"):
                return _FakePdf()
            if "nummer-022026" in url:
                return _FakeResponse(self.ISSUE_LATEST)
            if "nummer-012026" in url:
                return _FakeResponse(self.ISSUE)
            return _FakeResponse(self.MENU)
        monkeypatch.setattr(download.net, "make_session", lambda ua: object())
        monkeypatch.setattr(download.net, "request", request)

    def test_a_caught_up_run_reads_only_the_newest_issue(self, monkeypatch,
                                                          tmp_path):
        # a caught-up run reads the menu, the newest issue page, and
        # nothing else: the backlist and its PDFs are never re-read
        made = []
        self._live_request(monkeypatch, made)
        seen, new = download.jp_sync(tmp_path, delay=0)
        assert (seen, new) == (4, 4)
        made.clear()
        seen, new = download.jp_sync(tmp_path, delay=0)
        assert (seen, new) == (3, 0)
        assert made == [download.JP.listings[0],
                        "https://juridiskpublikation.se/tidskriften/nummer-022026/"]
        mark = json.loads((tmp_path / "jp" / ".watermark.json")
                          .read_text(encoding="utf-8"))
        assert mark["dirty"] is False
        assert mark["last_harvest"]


# --------------------------------------------------------------------------
# the ft listing readers
# --------------------------------------------------------------------------

class TestFtIssues:
    def test_the_archive_names_every_year_and_its_issues(self, monkeypatch):
        _serve(ft, monkeypatch, _read("ft-journals.html"))
        issues = ft._ft_issues(object())
        assert [(i["year"], i["issue"]) for i in issues] == [
            ("2026", "1"), ("2026", "2"), ("2026", "3"),
            ("2025", "1"), ("2025", "2"), ("2025", "3"), ("2025", "4"),
            ("1938", "1"), ("1938", "2"), ("1938", "3"), ("1938", "4"),
            ("1938", "5"), ("1938", "6")]
        # the newest and the oldest issues the archive states, and the
        # page's plain search box names no year, so it sets no issue
        assert issues[0]["url"].endswith("/Journal/2364")
        assert issues[-1]["url"].endswith("/Journal/466")


class TestFtRecords:
    ISSUE_URL = "https://www.forvaltningsrattslig.org/Journals/Journal/2364"

    def test_the_open_access_cards_are_the_records(self):
        records = ft._ft_records_from_page(_read("ft-issue-2026-1.html"),
                                           self.ISSUE_URL)
        assert [r["basefile"] for r in records] == [
            "ft/2026-1-0%d" % i for i in range(1, 5)]
        first, last = records[0], records[-1]
        assert first["titel"] == ("Om tillståndsprövning av "
                                  "välgörenhetsapotek – möjligheter och "
                                  "problem i omvandlingen av svensk välfärd")
        assert first["fattare"] == "Nick Dimitrievski"
        assert first["sammanfattning"] is None
        assert first["document_url"].startswith(
            "https://www.forvaltningsrattslig.org/Articles/downloadopenaccess")
        assert first["source_url"] == self.ISSUE_URL
        # the page sets ten cards: four open-access, two subscription
        # cards and four section heads. only the open-access cards name a
        # public pdf, and only they become records
        assert last["fattare"] == "Jaan Paju"

    def test_an_issue_with_no_open_access_sets_no_records(self):
        # the ordinary pre-2025 state: the issue sets cards, none of them
        # open access. the walk keeps none and says nothing
        assert ft._ft_records_from_page(_read("ft-issue-no-oa.html"),
                                        self.ISSUE_URL) == []

    def test_a_page_with_no_issue_number_refuses(self):
        with pytest.raises(ValueError, match="no issue number"):
            ft._ft_records_from_page(
                "<html><body><h1>ft</h1>"
                '<ul class="list-group"><li class="list-group-item">'
                '<a href="/Articles/downloadopenaccess/x.pdf"></a>'
                "<b>en artikel</b></li></ul></body></html>", self.ISSUE_URL)


def _ft_card(title, pdf):
    """One open-access card: the title in its bold run, its abstract in the
    marked paragraph, its PDF in the platform's own download link."""
    return ('<li class="list-group-item"><b>%s</b>'
            '<p class="abstract">En kort text</p>'
            '<a href="%s">Open Access</a></li>' % (title, pdf))


class TestFtSync:
    ARCHIVE = ("<html><body>"
               '<div class="input-group">'
               '<span class="input-group-addon">2026</span>'
               '<div class="btn-group">'
               '<a href="/Journal/31">1</a>'
               '<a href="/Journal/33">3</a>'
               "</div></div>"
               '<div class="input-group">'
               '<span class="input-group-addon">2025</span>'
               '<div class="btn-group"><a href="/Journal/21">1</a></div>'
               "</div></body></html>")
    # the newest issue sets three open-access cards, enough for the walk's
    # stop; the older issues set one each
    ISSUE_LATEST = ("<html><body><h1>Nummer 2026 3</h1>"
                    + _ft_card("En artikel",
                               "https://www.forvaltningsrattslig.org/"
                               "Articles/downloadopenaccess/331.pdf")
                    + _ft_card("En annan",
                               "https://www.forvaltningsrattslig.org/"
                               "Articles/downloadopenaccess/332.pdf")
                    + _ft_card("En tredje",
                               "https://www.forvaltningsrattslig.org/"
                               "Articles/downloadopenaccess/333.pdf")
                    + "</body></html>")
    ISSUE_A = ("<html><body><h1>Nummer 2026 1</h1>"
               + _ft_card("En artikel",
                          "https://www.forvaltningsrattslig.org/"
                          "Articles/downloadopenaccess/311.pdf")
               + "</body></html>")
    ISSUE_B = ("<html><body><h1>Nummer 2025 1</h1>"
               + _ft_card("En gammal",
                          "https://www.forvaltningsrattslig.org/"
                          "Articles/downloadopenaccess/211.pdf")
               + "</body></html>")

    def _request(self, monkeypatch, made):
        def request(session, method, url):
            made.append(url)
            if "downloadopenaccess" in url:
                return _FakePdf()
            if url == ft.FT.listings[0]:
                return _FakeResponse(self.ARCHIVE)
            if url.endswith("/Journal/33"):
                return _FakeResponse(self.ISSUE_LATEST)
            if url.endswith("/Journal/31"):
                return _FakeResponse(self.ISSUE_A)
            if url.endswith("/Journal/21"):
                return _FakeResponse(self.ISSUE_B)
            raise AssertionError("an unstubbed ft request: %s" % url)
        monkeypatch.setattr(ft.net, "make_session", lambda ua: object())
        monkeypatch.setattr(ft.net, "request", request)

    def test_a_caught_up_run_reads_only_the_newest_issue(self, monkeypatch,
                                                          tmp_path):
        # a caught-up run reads the archive page, the newest issue page,
        # and nothing else: the backlist is never re-read
        made = []
        self._request(monkeypatch, made)
        seen, new = ft.ft_sync(tmp_path, delay=0)
        assert (seen, new) == (5, 5)
        made.clear()
        seen, new = ft.ft_sync(tmp_path, delay=0)
        assert (seen, new) == (3, 0)
        assert made == [ft.FT.listings[0],
                        "https://www.forvaltningsrattslig.org/Journal/33"]
        mark = json.loads((tmp_path / "ft" / ".watermark.json")
                          .read_text(encoding="utf-8"))
        assert mark["dirty"] is False
        assert mark["last_harvest"]


# --------------------------------------------------------------------------
# the nmt listing readers
# --------------------------------------------------------------------------

class TestNmtIssueCode:
    @pytest.mark.parametrize("label, expected", [
        # the three hands the archive has set its labels in
        ("NMT 2025:2", ("2025", "2")),
        ("NMT2024:2", ("2024", "2")),
        ("NMT 2024:Special issue: Rights of Nature, National Interest, "
         "and Representation", ("2024", "s")),
        ("NMT 2020:2", ("2020", "2")),
        ("NMT 2019-1.pdf", ("2019", "1")),
        ("NMT 2017:1", ("2017", "1")),
        # a label that carries only the year is the year's one issue
        ("NMT 2009.pdf", ("2009", "1")),
    ])
    def test_issue_code(self, label, expected):
        assert nmt._nmt_issue_code(label) == expected

    def test_a_caption_line_is_not_an_issue_label(self):
        with pytest.raises(ValueError, match="no nmt issue code"):
            nmt._nmt_issue_code(
                "Special issue dedicated to Bertil Bengtsson, 90 years "
                "in May 2016")


class TestNmtIssues:
    LISTING = "https://nordiskmiljoratt.se/earlier-issues.html"

    def test_the_listing_hands_and_the_print_only_lines(self):
        issues = nmt._nmt_issues_from_page(_read("nmt-earlier.html"),
                                           self.LISTING)
        assert [(i["year"], i["issue"]) for i in issues] == [
            ("2025", "2"), ("2024", "2"), ("2024", "s"),
            ("2020", "2"), ("2019", "1"), ("2017", "1")]
        assert [len(i["articles"]) for i in issues] == [5, 5, 3, 6, 5, 3]
        # the newest hand: the author, the bold title, the leader, and the
        # page in the link's own text
        first = issues[0]["articles"][0]
        assert first["fattare"] == "David Langlet"
        assert first["titel"] == "Introduction"
        assert first["sida"] == "5"
        assert first["document_url"].endswith(
            "NMT2025nr2_startsidor_inkl_introduction.pdf")
        assert first["source_url"] == self.LISTING
        # a title the journal sets across two lines states its page on the
        # continuation line, and that line's link is the article's pdf
        continued = issues[1]["articles"][4]
        assert continued["titel"].startswith(
            "The role of the voluntary carbon market")
        assert "systems thinking" in continued["titel"]
        assert continued["sida"] == "61"
        assert continued["document_url"] is not None
        # a line that names its page and no link is a print-only article
        print_only = issues[1]["articles"][0]
        assert print_only["sida"] == "5"
        assert print_only["document_url"] is None
        # the label the journal sets as a bare link still names its issue
        assert (issues[4]["year"], issues[4]["issue"]) == ("2019", "1")
        # the journal's oldest hands set no page on some lines: the record
        # keeps no page, and the identifier takes the line's place instead
        assert [a["sida"] for a in issues[5]["articles"]] == [None] * 3

    def test_the_latest_issue_listing(self):
        issues = nmt._nmt_issues_from_page(
            _read("nmt-latest.html"),
            "https://nordiskmiljoratt.se/latest-issue.html")
        assert [(i["year"], i["issue"]) for i in issues] == [("2026", "1")]
        assert issues[0]["articles"][0]["titel"] == "Introduction"

    def test_a_page_with_no_issue_labels_refuses(self):
        with pytest.raises(ValueError, match="names no issues"):
            nmt._nmt_issues_from_page(
                "<html><body><p>inga tidskrifter</p></body></html>",
                self.LISTING)

    def test_an_article_line_before_any_issue_label_refuses(self):
        with pytest.raises(ValueError, match="precedes any issue label"):
            nmt._nmt_issues_from_page(
                '<html><body><p class="mobile-undersized-upper">'
                "En Fattare; En inledning … 5</p></body></html>",
                self.LISTING)


# --------------------------------------------------------------------------
# the njel listing readers
# --------------------------------------------------------------------------

class TestNjelIssues:
    def test_the_archive_names_every_issue(self, monkeypatch):
        _serve(njel, monkeypatch, _read("njel-archive.html"))
        issues = njel._njel_issues(object())
        assert [(i["id"], i["year"], i["issue"]) for i in issues] == [
            ("3526", "2024", "1"),
            ("3719", "2026", "2"),
            ("2717", "2018", "1"),
            ("NJEL2019%282%29", "2019", "2"),
        ]
        # the platform's own id for an issue can be a word, and the card's
        # series line is what states the issue's number and year: the card's
        # title link can say anything. the platform's Current Issue and
        # Archives links address no /issue/view/ page, so the walk's href
        # rule tells them off

    def test_a_series_line_beside_a_volume_that_does_not_fit_its_year_refuses(
            self, monkeypatch):
        # the platform nests the card's series line inside the heading, the
        # shape the archive page sets on every card
        _serve(njel, monkeypatch,
               "<html><body><div class='media-body'>"
               "<h2 class='media-heading'>"
               "<a class='title' "
               'href="https://journals.lub.lu.se/njel/issue/view/1234">'
               "Ett nummer</a>"
               "<div class='series lead'>Vol. 5 No. 1 (2018)</div>"
               "</h2></div></body></html>")
        with pytest.raises(ValueError, match="volume beside"):
            njel._njel_issues(object())


class TestNjelRecords:
    ISSUE_URL = "https://journals.lub.lu.se/njel/issue/view/3719"

    def test_the_summaries_are_the_records(self):
        records = njel._njel_records_from_page(
            _read("njel-issue-3719.html"), self.ISSUE_URL, "2026", "2")
        assert [r["basefile"] for r in records] == [
            "njel/2026-2-0%d" % i for i in range(1, 4)]
        first, galleyless, last = records
        # the title is the summary's heading, the small-type subtitle out
        assert first["titel"] == ("Selling (EU) Citizenship or Exercising "
                                  "Sovereignty?")
        assert first["fattare"] == "Alina Tryfonidou"
        assert first["sida"] == "1"
        # the platform's pdf link is the article's view page: the walk
        # stores the download that streams the pdf itself
        assert first["document_url"] == (
            "https://journals.lub.lu.se/njel/article/download/29082/25242")
        # a summary that names no pdf is an article without a document, kept
        # as one
        assert galleyless["document_url"] is None
        assert galleyless["sida"] == "132"
        assert last["fattare"] == "Elena Basheska, Dimitry V. Kochenov"
        assert last["sida"] == "36"

    def test_an_issue_page_with_no_summaries_refuses(self):
        with pytest.raises(ValueError, match="sets no summaries"):
            njel._njel_records_from_page(
                "<html><body>"
                "<div class='article-summary'></div></body></html>",
                self.ISSUE_URL, "2026", "2")


# --------------------------------------------------------------------------
# the siplr listing readers
# --------------------------------------------------------------------------

class TestSiplrIssues:
    def test_the_issues_page_names_every_issue(self, monkeypatch):
        _serve(siplr, monkeypatch, _read("siplr-issues.html"))
        assert siplr._siplr_issues(object()) == [
            "https://stockholmiplawreview.com/issue-1-2018/",
            "https://stockholmiplawreview.com/issue-1-2024/",
            "https://stockholmiplawreview.com/issue-2-2025/",
        ]

    def test_issue_code_off_the_address(self):
        assert siplr._siplr_issue_code(
            "https://stockholmiplawreview.com/issue-2-2025/") == ("2", "2025")


class TestSiplrRecords:
    ISSUE_URL = "https://stockholmiplawreview.com/issue-2-2025/"

    def test_headings_and_pdfs_paired_in_the_page_order(self):
        records = siplr._siplr_records_from_page(
            _read("siplr-issue-2-2025.html"), self.ISSUE_URL, "2025", "2")
        assert [r["basefile"] for r in records] == [
            "siplr/2025-2-0%d" % i for i in range(1, 4)]
        first = records[0]
        assert first["titel"] == "Trade secrets – today and in the future"
        assert first["fattare"] == "Christina Wainikka"
        assert first["document_url"].endswith("SIPLR2025_nr2_2_Wainikka.pdf")
        # the page sets a fourth pdf, the issue's own combined file: it is
        # not an article, and the pairing survives it
        assert all("By" not in r["titel"] for r in records)

    def test_a_single_line_heading_with_no_author_line_is_its_title(self):
        # the 2024 #1 interview: no "By", no name line -- the heading is
        # the article's title, and the record's author stays unset
        records = siplr._siplr_records_from_page(
            "<html><body><h3>The impact of AI in the patent world: "
            "<em>An interview with Martin Müller</em></h3>"
            '<a href="https://x/y.pdf">y.pdf</a></body></html>',
            self.ISSUE_URL, "2024", "1")
        assert records[0]["titel"] == ("The impact of AI in the patent "
                                       "world: An interview with Martin "
                                       "Müller")
        assert records[0]["fattare"] is None

    def test_a_bare_name_line_at_the_heading_tail_is_the_author(self):
        # the 2023 #1 hand: the author's name line, set with no "By" before
        # it, at the heading's tail
        records = siplr._siplr_records_from_page(
            "<html><body><h3>Incorporating Cultural Heritage in the "
            "Proposal<br/><br/>Leila Magnini, LL.M.</h3>"
            '<a href="https://x/y.pdf">y.pdf</a></body></html>',
            self.ISSUE_URL, "2023", "1")
        assert records[0]["titel"] == \
            "Incorporating Cultural Heritage in the Proposal"
        assert records[0]["fattare"] == "Leila Magnini, LL.M."

    def test_files_that_are_not_articles_are_set_aside(self):
        # the hands the journal has used across its years, the combined-
        # issue and cover files, the 2023 #1 whole-issue hand, the one
        # pleading file its 2022 #2 issue hosts, and the file another
        # issue's page set, its name stamping that issue's year
        html = ("<html><body>"
                "<h3>En artikel By En Fattare</h3>"
                "<h3>En annan By En Annan</h3>"
                '<a href="https://x/SIPLR2024nr2__TITELSIDOR.pdf">x</a>'
                '<a href="https://x/SIPLR2024nr2_inlagaomslag.pdf">x</a>'
                '<a href="https://x/Hela_nr2.pdf">x</a>'
                '<a href="https://x/Online-4.1_IP_nr-2_2022_A4.pdf">x</a>'
                '<a href="https://x/IP_nr-2_2018_A4.pdf">x</a>'
                '<a href="https://x/Issue-2-1.pdf">x</a>'
                '<a href="https://x/SIPLR2024nr2_www.pdf">x</a>'
                '<a href="https://x/SIPLR2023nr1_HELA.pdf">x</a>'
                '<a href="https://x/1-0-github_complaint.pdf">x</a>'
                '<a href="https://x/Kathy-Bowrey_IP_nr-2_2022_A4.pdf">x</a>'
                '<a href="https://x/1_En-Fattare.pdf">x</a>'
                '<a href="https://x/2_En-Annan.pdf">x</a>'
                "</body></html>")
        records = siplr._siplr_records_from_page(html, self.ISSUE_URL,
                                                "2024", "2")
        assert [r["document_url"] for r in records] == [
            "https://x/1_En-Fattare.pdf", "https://x/2_En-Annan.pdf"]

    def test_a_pdf_beside_no_heading_refuse(self):
        # an article PDF the page sets beside no heading is a file no
        # record names, and a page the journal did not set
        html = ("<html><body>"
                "<h3>En artikel By En Fattare</h3>"
                "<h3>En annan By En Annan</h3>"
                '<a href="https://x/a.pdf">a.pdf</a>'
                '<a href="https://x/b.pdf">b.pdf</a>'
                '<a href="https://x/c.pdf">c.pdf</a>'
                "</body></html>")
        with pytest.raises(ValueError, match="3 article PDFs beside 2"):
            siplr._siplr_records_from_page(html, self.ISSUE_URL, "2025", "2")

    def test_a_heading_beside_no_pdf_names_a_print_only_record(self):
        # the 2024 #2 issue lists one article beside no PDF: the archive's
        # own state, and the record it names is a print-only one. The files
        # carry the journal's newer surname hand, which is what lets the
        # reader prove the unpaired heading is the right one
        html = ("<html><body>"
                "<h3>En artikel By En Fattare</h3>"
                "<h3>En annan By En Annan</h3>"
                "<h3>En tredje By En Tredje</h3>"
                '<a href="https://x/SIPLR2024_nr2_1_Fattare.pdf">a</a>'
                '<a href="https://x/SIPLR2024_nr2_2_Annan.pdf">b</a>'
                "</body></html>")
        records = siplr._siplr_records_from_page(html, self.ISSUE_URL,
                                                "2024", "2")
        assert [r["basefile"] for r in records] == [
            "siplr/2024-2-0%d" % i for i in range(1, 4)]
        assert records[2]["document_url"] is None

    def test_a_file_naming_a_later_heading_is_filed_under_it(self):
        # the 2019 #1 page sets eight print-only headings before its one
        # PDF, whose name carries the third heading's title slug: page
        # order alone filed that PDF under the first article, and the
        # file's own name outranks the order
        html = ("<html><body>"
                "<h3>Perspectives on patents By En Fattare</h3>"
                "<h3>CRISPR systems By En Annan</h3>"
                "<h3>Being equitable about equivalents By En Tredje</h3>"
                '<a href="https://x/Juni_Online_IP_nr-1_2019_A4_'
                'Being-equitable-about.pdf">a</a>'
                "</body></html>")
        records = siplr._siplr_records_from_page(html, self.ISSUE_URL,
                                                 "2019", "1")
        assert records[0]["document_url"] is None
        assert records[1]["document_url"] is None
        assert records[2]["document_url"].endswith(
            "Being-equitable-about.pdf")

    def test_a_nameless_file_with_disagreeing_counts_refuses(self):
        # a file whose name names no heading can only be placed by blind
        # page order, and with a heading unpaired that order is a guess:
        # refuse, so the issue surfaces as an error rather than storing a
        # possible mispair
        html = ("<html><body>"
                "<h3>En artikel By En Fattare</h3>"
                "<h3>En annan By En Annan</h3>"
                '<a href="https://x/a.pdf">a</a>'
                "</body></html>")
        with pytest.raises(ValueError, match="cannot place"):
            siplr._siplr_records_from_page(html, self.ISSUE_URL, "2024", "2")

    def test_a_page_linking_one_file_twice_pairs_it_once(self):
        # the 2018 #2 page links one article's file twice: the duplicate is
        # one PDF, and the heading past it stays print-only rather than
        # taking the same file as its own
        html = ("<html><body>"
                "<h3>Parody in European copyright law By Ana-Maria Barbu</h3>"
                "<h3>En annan artikel By Harsh Mahaseth</h3>"
                '<a href="https://x/parody-in-european-copyright-law_ip'
                '_nr-2_2018.pdf">a</a>'
                '<a href="https://x/parody-in-european-copyright-law_ip'
                '_nr-2_2018.pdf">b</a>'
                "</body></html>")
        records = siplr._siplr_records_from_page(html, self.ISSUE_URL,
                                                 "2018", "2")
        assert records[0]["document_url"].endswith("2018.pdf")
        assert records[1]["document_url"] is None


# --------------------------------------------------------------------------
# the urt listing readers
# --------------------------------------------------------------------------

class TestUrtEntries:
    def test_the_listing_names_every_open_access_article(self, monkeypatch):
        _serve(urt, monkeypatch, _read("urt-oa.html"))
        entries = urt._urt_entries(object())
        assert [(e["year"], e["issue"], e["sida"]) for e in entries] == [
            ("2026", "1", "147"),
            ("2025", "3-4", "121"),
            ("2024", "1", "1"),
            ("2023", None, "1"),
            ("2022", None, "35"),
            ("2020", None, "101"),
            ("2014", None, "40"),
        ]
        # the "UrT" token and the "no" are set on some entries and dropped
        # on the rest, and "s." where "p." is newer: the year and the page
        # are on every entry, and they are what the record takes
        assert entries[0]["author"] == "Line Rakner"
        # a title that names a year of its own does not read as the
        # citation: the rightmost match is the entry's own
        assert (entries[2]["year"], entries[2]["issue"]) == ("2024", "1")

    def test_an_entry_with_no_citation_refuses(self, monkeypatch):
        _serve(urt, monkeypatch,
               "<html><body><h2>2024</h2>"
               "<p>En Fattare "
               '<a href="https://urt.cc/reportage/en-artikel/">en artikel</a>'
               "</p></body></html>")
        with pytest.raises(ValueError, match="sets no citation"):
            urt._urt_entries(object())


class TestUrtArticlePage:
    def test_the_page_states_its_article(self, monkeypatch):
        _serve(urt, monkeypatch, _read("urt-article.html"))
        page = urt._urt_article_page(object(), "https://urt.cc/reportage/x/")
        assert page["title"].startswith("The Role of Multilateral "
                                        "Negotiations")
        assert (page["issue"], page["sida"], page["year"]) == ("1", "1",
                                                                "2026")
        assert page["fattare"] == "Line Rakner"
        assert page["pdf"].endswith(".pdf")

    def test_the_older_pages_set_the_meta_line_in_their_own_hand(self,
                                                                 monkeypatch):
        # the 2014 and earlier pages set the label's mark with its colon off
        # it, and a print-only article's page sets its meta line beside no
        # PDF link: the record it names is a print-only one
        _serve(urt, monkeypatch,
               "<html><body><h1>en artikel</h1>"
               "<p><strong>Volym</strong> : no 1</p>"
               "<p><strong>Sida</strong> : s. 1</p>"
               "<p><strong>År</strong> : 2020</p>"
               "</body></html>")
        page = urt._urt_article_page(object(), "https://urt.cc/reportage/x/")
        assert (page["issue"], page["sida"], page["year"]) == ("1", "1",
                                                                "2020")
        assert page["pdf"] is None

    def test_the_oldest_pages_set_the_labels_plain_or_bolded(self,
                                                             monkeypatch):
        # the 2016 to 2020 hands: the labels set as plain text or as a
        # bold mark with its colon off it, and the article's author beside
        # its bold line
        _serve(urt, monkeypatch,
               "<html><body><h1>en artikel</h1>"
               "<p>Volym: no 1<br />Sida: s. 1<br />År: 2017</p>"
               "<p><b>En Fattare</b></p>"
               '</body></html>')
        page = urt._urt_article_page(object(), "https://urt.cc/reportage/x/")
        assert (page["issue"], page["sida"], page["year"]) == ("1", "1",
                                                                "2017")
        assert page["fattare"] == "En Fattare"

    def test_a_combined_issue_states_its_two_halves(self, monkeypatch):
        # the 2025 #3-4 pages state the issue's two halves in one line,
        # the same hand the listing's own citation states
        _serve(urt, monkeypatch,
               "<html><body><h1>en artikel</h1>"
               "<p><strong>Volym:</strong> 3-4<br />"
               "<strong>Sida:</strong> 121<br />"
               "<strong>År:</strong> 2025</p>"
               '</body></html>')
        page = urt._urt_article_page(object(), "https://urt.cc/reportage/x/")
        assert page["issue"] == "3-4"

    def test_a_label_word_in_running_text_is_not_a_label(self,
                                                         monkeypatch):
        # the site's own footer link sets the label's words in the middle
        # of its own word, and the page's meta line states its values
        _serve(urt, monkeypatch,
               "<html><body><h1>en artikel</h1>"
               '<a href="https://x/">Hemsida från Sentro</a>'
               "<p><strong>Volym:</strong> 1<br />"
               "<strong>Sida:</strong> 1<br />"
               "<strong>År:</strong> 2026</p>"
               '</body></html>')
        page = urt._urt_article_page(object(), "https://urt.cc/reportage/x/")
        assert (page["issue"], page["sida"], page["year"]) == ("1", "1",
                                                                "2026")

    def test_a_page_with_no_meta_line_refuses(self, monkeypatch):
        _serve(urt, monkeypatch,
               "<html><body><h1>en artikel</h1>"
               '<a href="https://urt.cc/x.pdf">x.pdf</a></body></html>')
        with pytest.raises(ValueError, match="states no issue, page or year"):
            urt._urt_article_page(object(), "https://urt.cc/reportage/x/")


class TestUrtSync:
    LISTING = ("<html><body>"
               "<h2>2026</h2>"
               '<p>En Fjärde<br/>'
               '<a href="https://urt.cc/reportage/a7/">En sista (UrT 2026 no 1 p. 7)</a></p>'
               '<p>En Tredje<br/>'
               '<a href="https://urt.cc/reportage/a5/">En artikel (UrT 2026 no 1 p. 5)</a></p>'
               '<p>En Annan<br/>'
               '<a href="https://urt.cc/reportage/a3/">En annan (UrT 2026 no 1 p. 3)</a></p>'
               "<h2>2025</h2>"
               '<p>En Furst<br/>'
               '<a href="https://urt.cc/reportage/b2/">En gammal (UrT 2025 no 2 p. 2)</a></p>'
               "</body></html>")

    @staticmethod
    def _page(title, volym, sida, ar, pdf, fattare="En Fattare"):
        return ("<html><body><h1>%s</h1>"
                '<p><strong>Volym:</strong> no %s</p>'
                '<p><strong>Sida:</strong> s. %s</p>'
                '<p><strong>År:</strong> %s</p>'
                '<p><strong>%s</strong></p>'
                '<a href="%s">PDF</a></body></html>'
                % (title, volym, sida, ar, fattare, pdf))

    def _request(self, monkeypatch, made):
        pages = {
            "https://urt.cc/reportage/a7/": self._page(
                "En sista", "1", "7", "2026",
                "https://urt.cc/x/7.pdf"),
            "https://urt.cc/reportage/a5/": self._page(
                "En artikel", "1", "5", "2026",
                "https://urt.cc/x/5.pdf"),
            "https://urt.cc/reportage/a3/": self._page(
                "En annan", "1", "3", "2026",
                "https://urt.cc/x/3.pdf"),
            "https://urt.cc/reportage/b2/": self._page(
                "En gammal", "2", "2", "2025",
                "https://urt.cc/x/2.pdf"),
        }

        def request(session, method, url):
            made.append(url)
            if url.endswith(".pdf"):
                return _FakePdf()
            if url in pages:
                return _FakeResponse(pages[url])
            if url == urt.URT.listings[0]:
                return _FakeResponse(self.LISTING)
            raise AssertionError("an unstubbed urt request: %s" % url)
        monkeypatch.setattr(urt.net, "make_session", lambda ua: object())
        monkeypatch.setattr(urt.net, "request", request)

    def test_a_caught_up_run_never_rereads_the_older_articles(self,
                                                              monkeypatch,
                                                              tmp_path):
        # a caught-up run reads the listing, its newest year's article
        # pages, and nothing else: the year behind the stop is never
        # re-read
        made = []
        self._request(monkeypatch, made)
        seen, new = urt.urt_sync(tmp_path, delay=0)
        assert (seen, new) == (4, 4)
        made.clear()
        seen, new = urt.urt_sync(tmp_path, delay=0)
        assert (seen, new) == (3, 0)
        assert made == [urt.URT.listings[0],
                        "https://urt.cc/reportage/a7/",
                        "https://urt.cc/reportage/a5/",
                        "https://urt.cc/reportage/a3/"]
        assert "https://urt.cc/reportage/b2/" not in made
        mark = json.loads((tmp_path / "urt" / ".watermark.json")
                          .read_text(encoding="utf-8"))
        assert mark["dirty"] is False
        assert mark["last_harvest"]


# --------------------------------------------------------------------------
# the euar listing readers
# --------------------------------------------------------------------------

class TestEuarIssues:
    def test_the_index_names_every_issue_since_1998(self, monkeypatch):
        _serve(euar, monkeypatch, _read("euar-nyhetsbrev.html"))
        assert euar._euar_issues(object()) == [
            "https://euocharbetsratt.se/nyhetsbrev/nordiskt-nyhetsbrev-2-2026/",
            "https://euocharbetsratt.se/nyhetsbrev/"
            "nordiskt-nyhetsbrev-nr-1-2026/",
            "https://euocharbetsratt.se/nyhetsbrev/"
            "nordiskt-nyhetsbrev-nr-3-4-2020/",
            "https://euocharbetsratt.se/nyhetsbrev/"
            "nordiskt-nyhetsbrev-nr-1-1998/",
        ]

    def test_issue_code_off_the_address(self):
        # the combined issues keep their two halves in the address
        assert euar._euar_issue_code(
            "https://euocharbetsratt.se/nyhetsbrev/"
            "nordiskt-nyhetsbrev-nr-3-4-2020/") == ("3-4", "2020")


class TestEuarRecords:
    ISSUE_URL = ("https://euocharbetsratt.se/nyhetsbrev/"
                 "nordiskt-nyhetsbrev-nr-3-4-2020/")

    def test_featured_and_remaining_cards_are_one_kind(self):
        # the featured cards are the items the walk once dropped: the
        # reader takes every article card, and the featured cards' own tag
        # states the same issue the page's h1 does, so the cross-check
        # passes
        records = euar._euar_records_from_page(
            _read("euar-issue-2020-3-4.html"), self.ISSUE_URL)
        assert [r["basefile"] for r in records] == [
            "euar/2020-3-4-0%d" % i for i in range(1, 5)]
        first = records[0]
        assert first["titel"] == ("Nya regler om cabotage och förarnas "
                                  "villkor utmanas i EU-domstolen")
        # the document is the item's own page, an absolute address
        assert first["document_url"] == (
            "https://euocharbetsratt.se/artiklar/"
            "nya-regler-om-cabotage-och-forarnasvillkor-utmanas-i-eu-domstolen/")
        assert records[1]["titel"] == ("Viktigt bidrag till diskussionen "
                                       "om hur EU:s arbetsrätt ska förstås")
        # the item's page states the author, so the record states none
        assert all(r["fattare"] is None for r in records)

    def test_a_card_tag_beside_a_different_issue_refuses(self):
        html = ("<html><body><h1>Nordiskt nyhetsbrev 1 2026</h1>"
                "<article><a href=\"/artiklar/x/\"></a>"
                '<p class="tag">Nr 2 2026</p><h2>En rubrik</h2>'
                "</article></body></html>")
        with pytest.raises(ValueError, match="a card tag states"):
            euar._euar_records_from_page(html, self.ISSUE_URL)

    def test_a_card_linking_elsewhere_is_page_material(self):
        html = ("<html><body><h1>Nordiskt nyhetsbrev 1 2026</h1>"
                "<article><a href=\"/nagra-tal/\"></a>"
                "<h2>Ett tal</h2></article>"
                "<article><a href=\"/artiklar/x/\"></a>"
                "<h2>En artikel</h2></article></body></html>")
        records = euar._euar_records_from_page(html, self.ISSUE_URL)
        assert [r["basefile"] for r in records] == ["euar/2026-1-01"]
        assert records[0]["document_url"] == \
            "https://euocharbetsratt.se/artiklar/x/"

    def test_an_issue_page_with_no_items_refuses(self):
        with pytest.raises(ValueError, match="sets no items"):
            euar._euar_records_from_page(
                "<html><body><h1>Nordiskt nyhetsbrev 1 2026</h1>"
                "</body></html>", self.ISSUE_URL)


class TestEuarSync:
    def test_dead_issue_pages_are_skips_not_crashes(
            self, monkeypatch, tmp_path, capsys):
        # the journal has taken its oldest issue pages offline: a dead page
        # must not stop the sweep, it is logged as a skip, and the live
        # issues' items are stored behind it
        index = _read("euar-nyhetsbrev.html")
        issue = _read("euar-issue-2020-3-4.html")
        article = _read("euar-article-2020.html")

        class Page:
            def __init__(self, html):
                self.text, self.content = html, html.encode("utf-8")

        def request(session, method, url):
            if url == "https://euocharbetsratt.se/nyhetsbrev/":
                return Page(index)
            if url.endswith("nordiskt-nyhetsbrev-nr-3-4-2020/"):
                return Page(issue)
            if "/artiklar/" in url:
                return Page(article)
            err = requests.HTTPError("404 Client Error")
            err.response = Page("")
            err.response.status_code = 404
            raise err

        monkeypatch.setattr(euar.net, "make_session", lambda ua: object())
        monkeypatch.setattr(euar.net, "request", request)
        seen, new = euar.euar_sync(tmp_path, delay=0)
        out = capsys.readouterr().out
        assert (seen, new) == (4, 4)
        # the three dead issue pages are logged as skips, and the run goes on
        assert out.count("is gone (HTTP 404)") == 3
        # the live issue's items are stored behind the skips
        for i in range(1, 5):
            assert (tmp_path / "euar" /
                                ("euar-2020-3-4-0%d.json" % i)).is_file()


class TestEuarWatermark:
    INDEX = ("<html><body>"
             '<a href="https://euocharbetsratt.se/nyhetsbrev/nordiskt-nyhetsbrev-2-2026/"></a>'
             '<a href="https://euocharbetsratt.se/nyhetsbrev/nordiskt-nyhetsbrev-nr-1-2026/"></a>'
             '<a href="https://euocharbetsratt.se/nyhetsbrev/nordiskt-nyhetsbrev-nr-3-4-2020/"></a>'
             '<a href="https://euocharbetsratt.se/nyhetsbrev/nordiskt-nyhetsbrev-nr-1-1998/"></a>'
             "</body></html>")

    @staticmethod
    def _card(title, n, tag):
        return ("<article><h2>%s</h2>"
                '<a href="https://euocharbetsratt.se/artiklar/item-%s/">%s</a>'
                '<p class="tag">%s</p></article>' % (title, n, title, tag))

    # the newest issue sets four items, enough for the walk's stop; the
    # older issues set fewer, and the oldest is the one the archive keeps
    ISSUE_LATEST = ("<html><body>"
                    "<h1>Nordiskt nyhetsbrev 2 2026</h1>"
                    + _card("En rubrik", "21", "Nr 2 2026")
                    + _card("En annan", "22", "Nr 2 2026")
                    + _card("En tredje", "23", "Nr 2 2026")
                    + _card("En fjärde", "24", "Nr 2 2026")
                    + "</body></html>")
    ISSUE_A = ("<html><body>"
               "<h1>Nordiskt nyhetsbrev 1 2026</h1>"
               + _card("En rubrik", "11", "Nr 1 2026")
               + _card("En annan", "12", "Nr 1 2026")
               + "</body></html>")
    ISSUE_B = ("<html><body>"
               "<h1>Nordiskt nyhetsbrev 3-4 2020</h1>"
               + _card("En gammal", "201", "Nr 3-4 2020")
               + "</body></html>")
    ISSUE_C = ("<html><body>"
               "<h1>Nordiskt nyhetsbrev 1 1998</h1>"
               + _card("En fornstig", "1998", "Nr 1 1998")
               + "</body></html>")
    ITEM_PAGE = '<html><body><div class="post-single-content">text</div>' \
        "</body></html>"

    def test_a_card_to_a_dead_item_link_contributes_nothing(self):
        # the journal has broken four of its own item links and not mended
        # them: a card to one of the addresses answers 404 on every run, and
        # a per-doc failure there would keep the store dirty forever. The
        # walk writes no record for a card to one of the addresses, and the
        # live cards around it are recorded as usual
        dead = sorted(euar.DEAD_ITEM_URLS)[0]
        html = ("<html><body>"
                "<h1>Nordiskt nyhetsbrev 2 2026</h1>"
                + self._card("En levande", "21", "Nr 2 2026")
                + ("<article><h2>En d\u00f6d</h2>"
                   '<a href="%s">En d\u00f6d</a>'
                   '<p class="tag">Nr 2 2026</p></article>' % dead)
                + "</body></html>")
        records = euar._euar_records_from_page(html, "https://x/issue/")
        assert [r["basefile"] for r in records] == ["euar/2026-2-01"]

    def _request(self, monkeypatch, made):
        pages = {
            "https://euocharbetsratt.se/nyhetsbrev/nordiskt-nyhetsbrev-2-2026/":
                self.ISSUE_LATEST,
            "https://euocharbetsratt.se/nyhetsbrev/"
            "nordiskt-nyhetsbrev-nr-1-2026/": self.ISSUE_A,
            "https://euocharbetsratt.se/nyhetsbrev/"
            "nordiskt-nyhetsbrev-nr-3-4-2020/": self.ISSUE_B,
            "https://euocharbetsratt.se/nyhetsbrev/"
            "nordiskt-nyhetsbrev-nr-1-1998/": self.ISSUE_C,
        }

        def request(session, method, url):
            made.append(url)
            if "/artiklar/" in url:
                return _FakeResponse(self.ITEM_PAGE)
            if url == "https://euocharbetsratt.se/nyhetsbrev/":
                return _FakeResponse(self.INDEX)
            if url in pages:
                return _FakeResponse(pages[url])
            raise AssertionError("an unstubbed euar request: %s" % url)
        monkeypatch.setattr(euar.net, "make_session", lambda ua: object())
        monkeypatch.setattr(euar.net, "request", request)

    def test_a_caught_up_run_reads_only_the_newest_issue(self, monkeypatch,
                                                          tmp_path):
        # a caught-up run reads the index, the newest issue page, and
        # nothing else: the archive behind it is never re-read
        made = []
        self._request(monkeypatch, made)
        seen, new = euar.euar_sync(tmp_path, delay=0)
        assert (seen, new) == (8, 8)
        made.clear()
        seen, new = euar.euar_sync(tmp_path, delay=0)
        assert (seen, new) == (3, 0)
        assert made == ["https://euocharbetsratt.se/nyhetsbrev/",
                        "https://euocharbetsratt.se/nyhetsbrev/"
                        "nordiskt-nyhetsbrev-2-2026/"]
        mark = json.loads((tmp_path / "euar" / ".watermark.json")
                          .read_text(encoding="utf-8"))
        assert mark["dirty"] is False
        assert mark["last_harvest"]


# --------------------------------------------------------------------------
# the watermark walks: a backfill reads the whole archive, and a caught-up
# run reads only the index and the newest issue's page
# --------------------------------------------------------------------------

class TestSiplrSync:
    INDEX = ("<html><body>"
             '<a href="https://stockholmiplawreview.com/issue-1-2024/">1 2024</a>'
             '<a href="https://stockholmiplawreview.com/issue-2-2024/">2 2024</a>'
             '<a href="https://stockholmiplawreview.com/issue-1-2025/">1 2025</a>'
             "</body></html>")
    # the newest issue sets its heading in capitals, the hand the reader
    # once missed: three articles, enough for the walk's stop
    ISSUE_LATEST = ("<html><body><h1>ISSUE #1 2025</h1>"
                    "<h3>En artikel By En Fattare</h3>"
                    "<h3>En annan By En Annan</h3>"
                    "<h3>En tredje By En Tredje</h3>"
                    '<a href="https://x/a.pdf">a.pdf</a>'
                    '<a href="https://x/b.pdf">b.pdf</a>'
                    '<a href="https://x/c.pdf">c.pdf</a>'
                    "</body></html>")
    ISSUE_A = ("<html><body><h1>Issue #2 2024</h1>"
               "<h3>En artikel By En Fattare</h3>"
               '<a href="https://x/d.pdf">d.pdf</a>'
               "</body></html>")
    ISSUE_B = ("<html><body><h1>Issue #1 2024</h1>"
               "<h3>En artikel By En Fattare</h3>"
               '<a href="https://x/e.pdf">e.pdf</a>'
               "</body></html>")

    def _request(self, monkeypatch, made):
        def request(session, method, url):
            made.append(url)
            if url == "https://stockholmiplawreview.com/issues/":
                return _FakeResponse(self.INDEX)
            if url.endswith("/issue-1-2025/"):
                return _FakeResponse(self.ISSUE_LATEST)
            if url.endswith("/issue-2-2024/"):
                return _FakeResponse(self.ISSUE_A)
            if url.endswith("/issue-1-2024/"):
                return _FakeResponse(self.ISSUE_B)
            return _FakePdf()
        monkeypatch.setattr(siplr.net, "make_session", lambda ua: object())
        monkeypatch.setattr(siplr.net, "request", request)

    def test_a_caught_up_run_reads_only_the_newest_issue(self, monkeypatch,
                                                         tmp_path):
        made = []
        self._request(monkeypatch, made)
        seen, new = siplr.siplr_sync(tmp_path, delay=0)
        assert (seen, new) == (5, 5)
        # the backfill: the index, every issue page, every article PDF
        assert made.count("https://stockholmiplawreview.com/issues/") == 1
        assert made.count("https://x/a.pdf") == 1
        made.clear()
        # caught up: the index, the newest issue page, and nothing else --
        # the archive behind it is never re-read
        seen, new = siplr.siplr_sync(tmp_path, delay=0)
        assert (seen, new) == (3, 0)
        assert made == ["https://stockholmiplawreview.com/issues/",
                        "https://stockholmiplawreview.com/issue-1-2025/"]
        # the run completed clean: the watermark is stored and not dirty
        mark = json.loads((tmp_path / "siplr" / ".watermark.json")
                          .read_text(encoding="utf-8"))
        assert mark["dirty"] is False
        assert mark["last_harvest"]


class TestNjelSync:
    @staticmethod
    def _card(issue_id, title, volume, number, year):
        return ('<h2><a class="title" '
                'href="https://journals.lub.lu.se/njel/issue/view/%s">%s</a>'
                '<div class="series">Vol. %s No. %s (%s)</div></h2>'
                % (issue_id, title, volume, number, year))

    ARCHIVE = ("<html><body>"
               + _card(30, "Vol 8", "8", "1", "2025")
               + _card(20, "Vol 7 No 2", "7", "2", "2024")
               + _card(10, "Inaugural Issue", "7", "1", "2024")
               + "</body></html>")

    @staticmethod
    def _summary(title, *, pdf=True):
        block = ('<div class="article-summary media"><h3>%s</h3>'
                 '<div class="authors">En Fattare</div>'
                 '<p class="pages">5-9</p>' % title)
        if pdf:
            block += ('<a class="galley-link" '
                      'href="https://journals.lub.lu.se/njel/article/view/123">'
                      'PDF</a>')
        return block + "</div>"

    ISSUE_LATEST = ("<html><body>"
                    + _summary("En artikel")
                    + _summary("En annan")
                    + _summary("En tredje", pdf=False)
                    + "</body></html>")
    ISSUE_A = ("<html><body>" + _summary("En artikel") + "</body></html>")
    ISSUE_B = ("<html><body>" + _summary("En artikel") + "</body></html>")

    def _request(self, monkeypatch, made):
        def request(session, method, url):
            made.append(url)
            if url == "https://journals.lub.lu.se/njel/issue/archive":
                return _FakeResponse(self.ARCHIVE)
            if url.endswith("/issue/view/30"):
                return _FakeResponse(self.ISSUE_LATEST)
            if url.endswith("/issue/view/20"):
                return _FakeResponse(self.ISSUE_A)
            if url.endswith("/issue/view/10"):
                return _FakeResponse(self.ISSUE_B)
            return _FakePdf()
        monkeypatch.setattr(njel.net, "make_session", lambda ua: object())
        monkeypatch.setattr(njel.net, "request", request)

    def test_a_caught_up_run_reads_only_the_newest_issue(self, monkeypatch,
                                                         tmp_path):
        made = []
        self._request(monkeypatch, made)
        seen, new = njel.njel_sync(tmp_path, delay=0)
        assert (seen, new) == (5, 5)
        # two of the newest issue's three articles carry PDFs, the third is
        # stored beside none: the backfill fetches four PDFs in all
        assert sum(1 for u in made if u.endswith("/article/download/123")) == 4
        made.clear()
        seen, new = njel.njel_sync(tmp_path, delay=0)
        assert (seen, new) == (3, 0)
        assert made == ["https://journals.lub.lu.se/njel/issue/archive",
                        "https://journals.lub.lu.se/njel/issue/view/30"]
        mark = json.loads((tmp_path / "njel" / ".watermark.json")
                          .read_text(encoding="utf-8"))
        assert mark["dirty"] is False


# --------------------------------------------------------------------------
# the lod listing readers
# --------------------------------------------------------------------------

class TestLodListings:
    def test_the_index_names_every_year(self):
        assert lod._year_links(_read("lod-journal.html")) == [
            "https://lod.lovdata.no/journal/%d" % y
            for y in range(2026, 2017, -1)]

    def test_a_year_page_names_its_issues_newest_first(self):
        assert lod._issue_links(_read("lod-year-2022.html")) == [
            "https://lod.lovdata.no/journal/2022/4",
            "https://lod.lovdata.no/journal/2022/3",
            "https://lod.lovdata.no/journal/2022/2",
            "https://lod.lovdata.no/journal/2022/1",
        ]

    def test_a_print_only_year_contributes_nothing(self):
        # the 2018-2021 volumes' cards link only the issue PDF: an expected
        # empty page, not an error -- no year floor is coded
        assert lod._issue_links(_read("lod-year-2021.html")) == []


class TestLodRecords:
    ISSUE_URL = "https://lod.lovdata.no/journal/2022/3"

    def test_the_table_of_contents_lines_are_the_records(self):
        records = lod._lod_records_from_page(
            _read("lod-issue-2022-3.html"), self.ISSUE_URL)
        assert [r["basefile"] for r in records] == [
            "lod/2022-3-%02d" % i for i in range(1, 14)]
        first = records[0]
        assert first["titel"] == ("Dataskyddet som hinder mot "
                                  "maskininlärning och samhällsnyttig "
                                  "analys?")
        # the theme heading above an entry is the entry's kind, and stands
        # for every entry until the next theme
        assert first["kind"] == "Leder"
        assert [r["kind"] for r in records[1:5]] == [
            "Artikler", "Artikler", "Artikler", "JusNytt"]
        assert records[7]["titel"] == "Nytt om personvern"
        assert records[7]["kind"] == "Nytt om personvern"
        # the issue's own publication day, off its page's H2: every record
        # of the issue carries it, and it drives the harvest watermark
        assert all(r["date"] == "2022-10-28" for r in records)
        # the site's raw-space address is stored percent-encoded
        assert records[7]["document_url"] == (
            "https://lod.lovdata.no/article/2022/10/"
            "Nytt%20om%20personvern")
        # the article's page states the author, so the record states none
        assert all(r["fattare"] is None for r in records)

    def test_a_page_stating_another_issue_refuses(self):
        with pytest.raises(ValueError, match="the page states issue"):
            lod._lod_records_from_page(
                _read("lod-issue-2022-3.html"),
                "https://lod.lovdata.no/journal/2022/4")

    def test_a_soft_hyphenated_title_is_filed_clean(self):
        # Lovdata typesets titles and running text with soft hyphens
        # ("Cyber\xadsikkerhed bliver produkt\xadsikkerhed" in the 1/2026
        # issue): invisible on the page, noise in a stored title
        html = ('<html><body><section id="frontcol2">'
                "<h1>Innhold nr. 165 1/2026</h1><h2>2026-03-20</h2>"
                '<ul><li><h2 class="theme" lang="nb">Leder</h2>'
                '<a href="https://lod.lovdata.no/article/2026/03/'
                'Cybersikkerhed bliver produktsikkerhed">'
                "<h3>Cyber­sikkerhed bliver produkt­sikkerhed"
                "</h3></a></li></ul></section></body></html>")
        records = lod._lod_records_from_page(
            html, "https://lod.lovdata.no/journal/2026/1")
        assert records[0]["titel"] == \
            "Cybersikkerhed bliver produktsikkerhed"

    def test_an_issue_page_with_no_articles_refuses(self):
        # the print-only volumes' addresses answer with an empty shell
        # ("Innhold nr.  4/2021" over an empty list)
        html = ('<html><body><section id="frontcol2">'
                "<h1>Innhold nr.  4/2021</h1><h2>2021-01-15</h2>"
                "<ul></ul></section></body></html>")
        with pytest.raises(ValueError, match="sets no articles"):
            lod._lod_records_from_page(
                html, "https://lod.lovdata.no/journal/2021/4")


def _lod_issue_page(issue, year, date, n_articles):
    """One lod issue page as the site sets it: the contents column with the
    issue's H1, its publication-day H2 and one entry per article."""
    entries = "".join(
        '<li><a href="https://lod.lovdata.no/article/%s/01/Artikkel %s-%d">'
        "<h3>Artikkel %s-%d</h3></a></li>" % (year, issue, i, issue, i)
        for i in range(1, n_articles + 1))
    return ('<html><body><section id="frontcol2">'
            "<h1>Innhold nr. 160 %s/%s</h1><h2>%s</h2>"
            '<ul><li><h2 class="theme" lang="nb">Leder</h2></li>%s</ul>'
            "</section></body></html>" % (issue, year, date, entries))


class TestLodWatermark:
    """A caught-up run reads the index page and the newest issue's page and
    stops there: the issues, once published, receive no new article, so a
    run of already-stored articles at the top says the archive is caught
    up -- the svjt gate on a shallow listing."""

    BASE = "https://lod.lovdata.no"
    CARDS = ('<article><a href="%s/journal/2026/2">2/2026</a></article>'
             '<article><a href="%s/journal/2026/1">1/2026</a></article>'
             % (BASE, BASE))
    INDEX = ('<html><body><nav class="years"><ul>'
             '<li><a href="%s/journal/2026">2026</a></li>'
             '<li><a href="%s/journal/2025">2025</a></li>'
             "</ul></nav>%s</body></html>" % (BASE, BASE, CARDS))
    YEAR_2025 = ('<html><body>'
                 '<article><a href="%s/journal/2025/2">2/2025</a></article>'
                 '<article><a href="%s/journal/2025/1">1/2025</a></article>'
                 "</body></html>" % (BASE, BASE))
    ISSUES = {
        "2026/2": _lod_issue_page("2", "2026", "2026-06-26", 3),
        "2026/1": _lod_issue_page("1", "2026", "2026-03-20", 3),
        "2025/2": _lod_issue_page("2", "2025", "2025-12-01", 3),
        "2025/1": _lod_issue_page("1", "2025", "2025-09-01", 3),
    }
    ARTICLE = ('<html><body><section class="chapter" id="maincolwidth">'
               "<p>Tekst.</p></section></body></html>")

    def _fake_request(self, fetched):
        def request(session, method, url, **kwargs):
            fetched.append(url)
            if url == self.BASE + "/journal":
                return _FakeResponse(self.INDEX)
            if url == self.BASE + "/journal/2026":
                return _FakeResponse("<html><body>%s</body></html>"
                                     % self.CARDS)
            if url == self.BASE + "/journal/2025":
                return _FakeResponse(self.YEAR_2025)
            for key, issue_html in self.ISSUES.items():
                if url == self.BASE + "/journal/" + key:
                    return _FakeResponse(issue_html)
            assert "/article/" in url, url
            return _FakeResponse(self.ARTICLE)
        return request

    def test_a_caught_up_run_stops_at_the_newest_issue(self, monkeypatch,
                                                       tmp_path):
        monkeypatch.setattr(lod.net, "make_session", lambda ua: object())
        fetched = []
        monkeypatch.setattr(lod.net, "request", self._fake_request(fetched))

        # first run: empty store, so the walk reads every year and issue
        # page and stores all twelve articles; the watermark completes clean
        seen, new = lod.lod_sync(tmp_path, delay=0.0)
        assert (seen, new) == (12, 12)
        assert any(u.endswith("/journal/2025/1") for u in fetched)
        wm = json.loads((tmp_path / "lod" / ".watermark.json").read_text())
        assert wm["dirty"] is False and wm["last_harvest"] == "2026-06-26"

        # second run, caught up: the newest issue's articles are all
        # already stored, so the walk stops inside that issue -- the index
        # page and the newest issue's page are the only listing fetches,
        # and nothing is stored
        fetched.clear()
        seen, new = lod.lod_sync(tmp_path, delay=0.0)
        assert new == 0
        assert fetched == [self.BASE + "/journal",
                           self.BASE + "/journal/2026/2"]


class TestLodVerifyPage:
    def test_an_article_page_passes(self):
        # no raise: the page sets its running text in section#maincolwidth
        lod.verify_page(_read("lod-article-2022.html"))

    def test_a_served_issue_page_is_rejected(self):
        with pytest.raises(ValueError, match="non-article page"):
            lod.verify_page(_read("lod-issue-2022-3.html"))


# --------------------------------------------------------------------------
# the sync dispatch: the nine hosts fan out, and one that fails is
# reported, not fatal
# --------------------------------------------------------------------------

class TestSyncDispatch:
    def test_the_default_fans_out_one_worker_per_scope(self, monkeypatch,
                                                       tmp_path):
        # two scopes wait for each other: a serial run never starts the
        # second, so the barrier times out and the test fails
        gate = threading.Barrier(2, timeout=5)

        def runner(scope):
            def run(root, full=False, only=None, limit=None, delay=0.5):
                gate.wait()
                return (1, 0)
            return run

        monkeypatch.setattr(download, "SYNC",
                            {s: runner(s) for s in ("a", "b")})
        download.sync(tmp_path, scopes=["a", "b"])

    def test_a_failing_scope_is_reported_and_the_others_run(self, monkeypatch,
                                                            tmp_path):
        ran = []
        lock = threading.Lock()

        def good(root, full=False, only=None, limit=None, delay=0.5):
            with lock:
                ran.append("a")
            return (1, 0)

        def bad(root, full=False, only=None, limit=None, delay=0.5):
            with lock:
                ran.append("b")
            raise ValueError("b is broken")

        monkeypatch.setattr(download, "SYNC", {"a": good, "b": bad})
        with pytest.raises(RuntimeError, match="b is broken") as ei:
            download.sync(tmp_path, scopes=["a", "b"])
        # the failure did not withhold the other scope, and the run ends red
        assert sorted(ran) == ["a", "b"]
        assert "1 of 2 scopes failed" in str(ei.value)


# --------------------------------------------------------------------------
# the registry
# --------------------------------------------------------------------------

class TestRegistry:
    def test_the_nine_journals_are_data(self):
        assert set(BY_KOD) == {"svjt", "jp", "ft", "nmt", "njel", "siplr",
                               "urt", "euar", "lod"}
        # two kinds of document, keyed off the registry, not branches
        assert [j.kod for j in JOURNALS if j.html_document] == [
            "svjt", "euar", "lod"]
        assert {j.kod: j.sida_kalla for j in JOURNALS
                if not j.html_document} == {
            "jp": "footer", "ft": "head", "nmt": "record", "njel": "record",
            "urt": "record", "siplr": "footer"}


# --------------------------------------------------------------------------
# the parse: the mining contract
# --------------------------------------------------------------------------

def _record(basefile, **over):
    rec = {"basefile": basefile, "journal": basefile.split("/", 1)[0],
           "year": "2026", "issue": "01",
           "titel": "Bortfall av hemfesteringsrätt",
           "fattare": "Anna Smith-Olofsson",
           "sammanfattning": "En studie av reglerna om hemfesteringsrätt.",
           "source_url": "https://svjt.se/svjt/2026/104",
           "document_url": None}
    rec.update(over)
    return rec


class TestParse:
    def test_svjt_parse(self, tmp_path):
        root = tmp_path / "svjt"
        root.mkdir()
        (root / "svjt-2026-104.json").write_text(
            json.dumps(_record("svjt/2026-104", issue="104")),
            encoding="utf-8")
        shutil.copy(FILES / "svjt-2026-104.html",
                    root / "svjt-2026-104.html")
        art = parse.parse("svjt/2026-104", tmp_path)
        assert art["uri"] == "https://lagen.nu/lawreview/svjt/2026-104"
        assert art["type"] == "juridisk_artikel"
        assert art["identifier"] == "SvJT 2026 s. 104"
        # the year the publisher states, widened to a representative day so
        # the catalog's date projection sees a 10-char ISO date, the shape
        # every dated document in the corpus stores
        assert art["date"] == "2026-07-01"
        md = art["metadata"]
        assert md["title"] == "Bortfall av hemfesteringsrätt"
        assert md["fattare"] == "Anna Smith-Olofsson"
        assert md["publisher"] == BY_KOD["svjt"].namn
        # the mining contract: every paragraph survives, as an ordinary
        # stycke, and the citations inside are linked
        blocks = art["structure"]
        assert len(blocks) == 4
        assert all(b["type"] == "stycke" for b in blocks)
        joined = " ".join(t for b in blocks for t in b["text"]
                          if isinstance(t, str))
        assert "Hemfesteringsrätten regleras" in joined
        uris = [n["uri"] for b in blocks for n in b["text"]
                if isinstance(n, dict) and n.get("uri")]
        # the scanner pinned "5 kap. 1 § lag (1961:37)" onto the paragraf
        assert "https://lagen.nu/1961:37#K5P1" in uris

    def test_jp_parse(self, tmp_path):
        root = tmp_path / "jp"
        root.mkdir()
        rec = _record("jp/2026-01-01", issue="01", seq="01", kind=None,
                      sammanfattning=None,
                      source_url=("https://juridiskpublikation.se/"
                                  "tidskriften/nummer-012026/"),
                      document_url="https://juridiskpublikation.se/x.pdf")
        (root / "jp-2026-01-01.json").write_text(
            json.dumps(rec), encoding="utf-8")
        shutil.copy(FILES / "jp-sartryck-sample.pdf",
                    root / "jp-2026-01-01.pdf")
        art = parse.parse("jp/2026-01-01", tmp_path)
        # the minimal article citation: name, year, the opening page the
        # Särtryck's footer prints; the seq form is only the stand-in when
        # no page is on record
        assert art["identifier"] == "JP 2026 s. 37"
        assert art["metadata"]["sida"] == "37"
        assert art["journal"] == "jp"
        assert art["metadata"]["publisher"] == BY_KOD["jp"].namn
        # the mining contract: every paragraph of the stored PDF survives --
        # no cover removed, no footnote dropped, no structure read off it
        blocks = art["structure"]
        assert len(blocks) > 1
        assert all(b["type"] == "stycke" for b in blocks)
        assert all(b["text"] for b in blocks)


    def test_siplr_parse(self, tmp_path):
        root = tmp_path / "siplr"
        root.mkdir()
        rec = _record("siplr/2025-2-02", year="2025", issue="2", seq="02",
                      sammanfattning=None,
                      source_url=("https://stockholmiplawreview.com/"
                                  "issue-2-2025/"),
                      document_url=("https://stockholmiplawreview.com/wp/"
                                    "x.pdf"))
        (root / "siplr-2025-2-02.json").write_text(
            json.dumps(rec), encoding="utf-8")
        shutil.copy(FILES / "siplr-sample.pdf", root / "siplr-2025-2-02.pdf")
        art = parse.parse("siplr/2025-2-02", tmp_path)
        # the opening page the article's PDF footer prints; the running
        # head the conversion sets after it is no page to read as one
        assert art["identifier"] == "SIPLR 2025 s. 5"
        assert art["metadata"]["sida"] == "5"
        blocks = art["structure"]
        assert all(b["type"] == "stycke" for b in blocks)
        joined = " ".join(t for b in blocks for t in b["text"]
                          if isinstance(t, str))
        assert "trade secret" in joined

    def test_siplr_parse_a_heading_with_no_pdf(self, tmp_path):
        # the 2024 #2 issue lists one article beside no PDF: there is no
        # document to mine, and the article's place in the issue takes the
        # page's turn in the identifier
        root = tmp_path / "siplr"
        root.mkdir()
        rec = _record("siplr/2024-2-07", year="2024", issue="2", seq="07",
                      sammanfattning=None, document_url=None,
                      source_url=("https://stockholmiplawreview.com/"
                                  "issue-2-2024/"))
        (root / "siplr-2024-2-07.json").write_text(
            json.dumps(rec), encoding="utf-8")
        art = parse.parse("siplr/2024-2-07", tmp_path)
        assert art["identifier"] == "SIPLR 2024 #2-07"
        assert art["structure"] == []

    def test_lod_parse(self, tmp_path):
        root = tmp_path / "lod"
        root.mkdir()
        url = ("https://lod.lovdata.no/article/2022/10/"
               "Nytt%20om%20personvern")
        rec = _record("lod/2022-3-08", year="2022", issue="3", seq="08",
                      kind="Nytt om personvern", titel="Nytt om personvern",
                      fattare=None, sammanfattning=None,
                      source_url=url, document_url=url)
        (root / "lod-2022-3-08.json").write_text(
            json.dumps(rec), encoding="utf-8")
        shutil.copy(FILES / "lod-article-2022.html",
                    root / "lod-2022-3-08.html")
        art = parse.parse("lod/2022-3-08", tmp_path)
        # the citation stops at the issue -- the journal prints no page
        # numbers on the web edition, and the basefile's seq alone keeps
        # the issue's articles apart
        assert art["identifier"] == "Lov & Data 3/2022"
        # the issue's publication day, off the page's own issue line
        assert art["date"] == "2022-10-28"
        md = art["metadata"]
        # the author is the name the author line links, its affiliation
        # ("partner i Gorrissen Federspiel") left behind
        assert md["fattare"] == "Tue Goldschmieding"
        assert md["typ"] == "Nytt om personvern"
        assert md["publisher"] == BY_KOD["lod"].namn
        blocks = art["structure"]
        assert len(blocks) > 5
        assert all(b["type"] == "stycke" for b in blocks)
        joined = " ".join(t for b in blocks for t in b["text"]
                          if isinstance(t, str))
        assert "Erhvervsministerium" in joined
        # the author line is metadata, not text to mine
        assert "Gorrissen Federspiel" not in joined


class TestIdentifierStandIns:
    def test_an_njel_note_without_a_page_takes_its_place(self):
        # the journal's editorial notes set no page range in the listing
        # (the 2019(2) and 2021(1) notes): the article's place in the issue
        # takes the page's turn, the ft/nmt/siplr rule
        art = Artikel(journal="njel", year="2019", issue="2", seq="01",
                      titel="Editorial Note")
        assert art.identifier == "NJEL 2019(2) nr 01"


class TestArtifactSerialization:
    """The record the harvest writes round-trips through compress, the way
    the build's parse stage reads it back off disk."""

    def test_record_roundtrip(self, tmp_path):
        record = _record("svjt/2026-104")
        path = tmp_path / "svjt" / "svjt-2026-104.json"
        tmp_path.joinpath("svjt").mkdir()
        harvest.write_record(path, record)
        back = compress.read_json(path)
        assert back == record


class TestVerifyPage:
    """The walk's check on a served svjt page: an article body, not a WAF
    challenge and not the listing the mirror serves in an article's place.
    A listing carries the article-node marker as often as an article (each of
    its cards links one), so only the body container tells the two apart."""

    LISTING = (
        '<html><body><div class="article-grid-item">'
        '<a class="node--type-article" href="/svjt/2026/104">en artikel</a>'
        '</div></body></html>'
    )
    ARTICLE = (
        '<html><body><article class="node node--type-article">'
        '<div class="body"><p>text</p></div>'
        '</article></body></html>'
    )

    def test_an_article_page_with_a_body_passes(self):
        # no raise: the page sets its running text in div.body
        download.verify_page(self.ARTICLE)

    def test_a_served_listing_is_rejected_despite_the_article_marker(self):
        # the regression the old check missed: a listing has node--type-article
        # but no div.body, so it is not an article body
        with pytest.raises(ValueError, match="non-article page"):
            download.verify_page(self.LISTING)


# --------------------------------------------------------------------------
# the rail contract: mined, not published
# --------------------------------------------------------------------------

class TestRailContract:
    """The article's only publication surface is its line in the "Artiklar"
    rail of the documents it cites, and that line links to the journal's own
    page for the article. It gets no page, browse tree, frontpage entry,
    feed or search index of its own -- any of those back is a contract
    breach, so the wiring is pinned here rather than by eyeball."""

    def test_no_page_tree_frontpage_feed_or_index_of_its_own(self):
        assert "lawreview" not in SOURCE_RENDERERS          # no page of its own
        assert "lawreview" not in render.SOURCE_ORDER       # not on the frontpage
        assert "lawreview" not in facets.SCHEMES            # no browse tree
        assert "lawreview" not in facets.SOURCE_LABELS
        assert "lawreview" not in feeds.BY_SOURCE           # no feed
        assert "lawreview" in UNSEARCHED                    # no search hits

    def test_the_rail_line_links_to_the_journal_page(self):
        # the line names the article with its short_id and author, and the
        # name links to the journal's own url (the catalog's source_url) --
        # never to a lagen.nu page, which the article does not have
        li = page._citer_line(
            ("https://lagen.nu/lawreview/jp/2009-01-01", "JP 2009 s. 37",
             "En leverantörs möjligheter att överpröva beslut från Systembolaget",
             "lawreview", "jp", "2009-07-01", "",
             "Rickard Bergflo",
             "https://juridiskpublikation.se/en-leverantors-mojligheter/"))
        assert li == ('<li><a href="https://juridiskpublikation.se/'
                      'en-leverantors-mojligheter/">En leverantörs '
                      'möjligheter att överpröva beslut från Systembolaget '
                      '(Rickard Bergflo, JP 2009 s. 37)</a></li>')
        assert "lagen.nu" not in li and "#" not in li

    def test_an_authorless_article_omits_the_author(self):
        # descriptive fell back to the ident (labels._lawreview): no author to
        # write, so the parenthetical carries the short_id alone
        li = page._citer_line(
            ("https://lagen.nu/lawreview/svjt/2026-104", "SvJT 2026 s. 104",
             "Bortfall av hemfesteringsrätt", "lawreview", "svjt",
             "2026-07-01", "", "SvJT 2026 s. 104",
             "https://svjt.se/svjt/2026/104"))
        assert li == ('<li><a href="https://svjt.se/svjt/2026/104">'
                      'Bortfall av hemfesteringsrätt (SvJT 2026 s. 104)'
                      '</a></li>')

    def test_the_lod_rail_line_stops_at_the_issue(self):
        # the journal prints no page numbers, so the line completes the
        # title with the author and the issue citation alone
        li = page._citer_line(
            ("https://lagen.nu/lawreview/lod/2022-3-08",
             "Lov & Data 3/2022", "Nytt om personvern", "lawreview", "lod",
             "2022-10-28", "", "Tue Goldschmieding",
             "https://lod.lovdata.no/article/2022/10/"
             "Nytt%20om%20personvern"))
        assert li == ('<li><a href="https://lod.lovdata.no/article/2022/10/'
                      'Nytt%20om%20personvern">Nytt om personvern '
                      '(Tue Goldschmieding, Lov &amp; Data 3/2022)</a></li>')