"""lawreview vertical (tidskriftsartiklar: Svensk Juristtidning and Juridisk
Publikation): the listing readers off the journals' own pages (both issue-page
templates and the cross-year promoted card), the issue number rules, and the
parse's mining contract (the whole text survives to the citation scanner,
nothing is re-typeset).

Hermetic: the fixtures under ``test/files/lawreview/`` are trimmed captures
of the live listing and article pages, and the one PDF parse is a minted
two-leaf jp Särtryck (``jp-sartryck-sample.pdf``, cover + the article's
first leaf with its "sida 37" footer). The jp host's WAF ride-out is tested
where the rule now lives: `test/test_net.py`.
"""

import json
import shutil
from pathlib import Path

import pytest

from accommodanda.build import SOURCE_RENDERERS, UNSEARCHED
from accommodanda.lawreview import download, parse
from accommodanda.lawreview.journals import BY_KOD
from accommodanda.lib import compress, facets, feeds, harvest, page, render

FILES = Path(__file__).parent / "files" / "lawreview"


def _read(name):
    return (FILES / name).read_text(encoding="utf-8")


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
    def test_a_failing_issue_page_becomes_a_skip(self, monkeypatch, tmp_path):
        # a challenged page must not stop the sweep, and must not vanish
        # either: it rides into the walk as a Skip, which counts and logs it
        monkeypatch.setattr(download.net, "make_session", lambda ua: object())
        monkeypatch.setattr(download, "_jp_issues",
                            lambda s: [("nummer-012026", "Nummer 1/2026")])
        def broken_page(session, slug, label, delay):
            raise ValueError("jp %s: no entry content" % slug)
        monkeypatch.setattr(download, "_jp_issue_records", broken_page)
        captured = {}
        def fake_walk(root, pending, **kw):
            captured["pending"] = list(pending)
            return (0, 0)
        monkeypatch.setattr(download.harvest, "walk_records", fake_walk)
        download.jp_sync(tmp_path)
        [skip] = captured["pending"]
        assert isinstance(skip, harvest.Skip)
        assert "nummer-012026" in skip.reason

    def test_an_unknown_slug_fails_the_run(self, monkeypatch, tmp_path):
        # a slug shape the registry does not hold is a code gap, not an
        # issue to skip: the run fails loud before any page is fetched
        monkeypatch.setattr(download.net, "make_session", lambda ua: object())
        monkeypatch.setattr(download, "_jp_issues",
                            lambda s: [("specialnummer-x", "Specialnummer")])
        with pytest.raises(ValueError, match="no jp issue code"):
            download.jp_sync(tmp_path)


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