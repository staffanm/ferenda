"""Hermetic checks for lawreview's lawpub scope (the LAWPUB platform): the
listing's open/locked split, the record it mints per article, the identifier
the model mints, the mined artifact, and -- like the other walk-based
harvests -- that a caught-up run reads only as much of the listing as the
safety window requires."""

from pathlib import Path

import pytest

from ferenda.lawreview import lawpub
from ferenda.lawreview import parse as lawreview_parse
from ferenda.lawreview.lawpub import BY_ICON, kod_from_icon
from ferenda.lib import compress, page

FILES = Path(__file__).parent / "files"

# a PDF the platform serves for an item; enough magic bytes for verification,
# and it is what the fake reader hands back on a downloadsection fetch
PDF = b"%PDF-1.4\nfake article bytes\n"


def _read(name):
    return (FILES / name).read_text(encoding="utf-8")


class _Fake:
    def __init__(self, text, content=None):
        self.text = text
        self.content = content if content is not None else text.encode("utf-8")


# --------------------------------------------------------------------------
# the publisher registry
# --------------------------------------------------------------------------

class TestPublishers:
    def test_an_icon_names_its_publisher(self):
        # the icon's file stem reads off the registry, and the registry entry's
        # own abbreviation is the identifier's leading token -- the icon stem
        # and that abbreviation can differ (``sisl-icon.svg`` -> the publisher
        # whose kod is ``SSIL``)
        for src, kod in [("/utils/media/ft-icon.svg", "FT"),
                         ("/utils/media/sisl-icon.svg", "SSIL"),
                         ("/utils/media/siplr_icon.svg", "SIPLR")]:
            assert BY_ICON[kod_from_icon(src).lower()].kod == kod

    def test_an_unlisted_icon_is_no_registered_publisher(self):
        assert kod_from_icon("someone-else-icon.svg").lower() not in BY_ICON


# --------------------------------------------------------------------------
# the listing reader: open items are the records, locked ones are dropped
# --------------------------------------------------------------------------

class TestListing:
    def test_the_open_items_are_the_records(self):
        records = lawpub._open_records(_read("lawpub-articles.html"))
        # the page carries six items, two of them locked (no open icon); the
        # four open ones are the records, all from one publisher here
        assert len(records) == 4
        for record in records:
            assert record["journal"] == "lawpub"
            assert record["basefile"].startswith("lawpub/")
            assert record["kind"] == "doi"
            assert record["utgivare"] == "FT"
            assert record["date"] == "2026-07-15"
            # a DOI item has no section number: the download key is re-derived
            # from the article page at fetch time, so it is not known yet
            assert record["sectionid"] is None
            assert record["document_url"] is None
            assert record["source_url"].startswith("https://www.lawpub.se/artikel/")

    def test_a_doi_item_records_its_full_coordinates(self):
        record = lawpub._open_records(_read("lawpub-articles.html"))[0]
        assert record["doi"].startswith("10.53292/ba5659bf")
        assert record["basefile"] == "lawpub/" + record["doi"].replace("/", "-")
        assert record["utgivare_namn"] == "Förvaltningsrättslig tidskrift"
        # the page span the "Publicerad i" line states, a range or a single page
        assert record["sida"] and (
            record["sida"].isdigit() or "-" in record["sida"])
        assert record["open"] is True

    def test_the_locked_items_are_not_records(self):
        # the two locked items carry a "Stängd" badge, not the open-access icon,
        # so they leave no record at all
        all_items = _read("lawpub-articles.html").count('class="section-item"')
        open_count = len(lawpub._open_records(_read("lawpub-articles.html")))
        assert all_items == open_count + 2


# --------------------------------------------------------------------------
# the identifier the model mints
# --------------------------------------------------------------------------

class TestIdentifier:
    @pytest.mark.parametrize("utgivare, date, sida, expected", [
        ("FT", "2015-10-01", "551", "FT 2015 s. 551"),
        ("SSIL", "2026-07-15", "373-406", "SSIL 2026 s. 373"),
        ("SIPLR", "2025-06-01", "12", "SIPLR 2025 s. 12"),
    ])
    def test_the_identifier_mirrors_the_journal_scopes(self, utgivare, date,
                                                       sida, expected):
        assert lawpub.Artikel(basefile="lawpub/x", titel="t", utgivare=utgivare,
                              utgivare_namn="n", date=date,
                              sida=sida).identifier == expected

    def test_the_edition_stands_in_for_a_missing_page(self):
        # where the line states no page span, the edition's name takes its place
        assert lawpub.Artikel(basefile="lawpub/x", titel="t", utgivare="FT",
                              utgivare_namn="n", date="2026-07-15",
                              utgava="1(2)").identifier == "FT 2026 1(2)"

    def test_the_year_of_an_undated_article_is_blank(self):
        assert lawpub.Artikel(basefile="lawpub/x", titel="t", utgivare="FT",
                              utgivare_namn="n", date=None,
                              sida="551").identifier == "FT s. 551"

    def test_the_uri_sits_under_the_lawreview_namespace(self):
        # the source's own uri minter answers for the scope, handle and all
        assert lawpub.Artikel(
            basefile="lawpub/10.53292-ba5659bf.1ef80a78", titel="t",
            utgivare="FT", utgivare_namn="n").uri \
            .endswith("/lawreview/lawpub/10.53292-ba5659bf.1ef80a78")
        assert lawpub.Artikel(
            basefile="lawpub/880", titel="t", utgivare="FT",
            utgivare_namn="n").uri.endswith("/lawreview/lawpub/880")


# --------------------------------------------------------------------------
# the mined artifact
# --------------------------------------------------------------------------

class TestParse:
    def test_the_record_and_the_text_are_mined_into_an_artifact(
            self, tmp_path, monkeypatch):
        root, basefile = str(tmp_path), "lawpub/10.53292-ba5659bf.1ef80a78"
        record = {
            "basefile": basefile, "journal": "lawpub", "kind": "doi",
            "sectionid": None, "doi": "10.53292/ba5659bf.1ef80a78",
            "utgivare": "SSIL", "utgivare_namn": "Scandinavian studies in law",
            "utgava": "8(2)", "date": "2026-07-15", "sida": "373-406",
            "titel": "An article", "fattare": "Ada Lovelace",
            "source_url": "https://www.lawpub.se/artikel/x",
            "document_url": None, "open": True}
        monkeypatch.setattr(compress, "read_json", lambda path, default=None: record)
        # the PDF's text is what is mined; here it is stubbed to known paragraphs
        monkeypatch.setattr(
            lawpub, "pdf_paragraph_texts",
            lambda path, key: ["Ingress med inledande mening.",
                               "Andra stycket, utan referenser."])
        # the source's parse dispatches the scope's basefiles here
        artifact = lawreview_parse.parse(basefile, root)
        assert artifact["type"] == "juridisk_artikel"
        assert artifact["journal"] == "lawpub"
        assert artifact["uri"].endswith(
            "/lawreview/lawpub/10.53292-ba5659bf.1ef80a78")
        assert artifact["identifier"] == "SSIL 2026 s. 373"
        assert [block["text"] for block in artifact["structure"]] == [
            ["Ingress med inledande mening."],
            ["Andra stycket, utan referenser."]]
        assert artifact["metadata"]["title"] == "An article"
        assert artifact["metadata"]["utgivare"] == "SSIL"
        assert artifact["metadata"]["publisher"] == "Scandinavian studies in law"
        assert artifact["source_url"].startswith("https://www.lawpub.se/")
        # a DOI item has no download URL until its article page is read at fetch
        assert "document_url" not in artifact

    def test_a_numeric_item_records_its_download_key(self, tmp_path, monkeypatch):
        root, basefile = str(tmp_path), "lawpub/6480"
        record = {
            "basefile": basefile, "journal": "lawpub", "kind": "numeric",
            "sectionid": 6480, "doi": None, "utgivare": "FT",
            "utgivare_namn": "Förvaltningsrättslig tidskrift",
            "utgava": "1(1)", "date": "2026-04-15", "sida": "1-4",
            "titel": "Editorial", "fattare": None,
            "source_url": "https://www.lawpub.se/artikel/6480",
            "document_url": "https://www.lawpub.se/utils/downloadsection/6480",
            "open": True}
        monkeypatch.setattr(compress, "read_json", lambda path, default=None: record)
        monkeypatch.setattr(lawpub, "pdf_paragraph_texts", lambda path, key: ["Text."])
        artifact = lawpub.parse(basefile, root)
        assert artifact["identifier"] == "FT 2026 s. 1"
        assert artifact["document_url"].endswith("/downloadsection/6480")


# --------------------------------------------------------------------------
# the harvest watermark: a caught-up run reads only the pages its window needs
# --------------------------------------------------------------------------

class TestWatermark:
    # page 0 is the newest (July 2026), page 1 the older backlog (March 2025),
    # page 2 the platform's EOF. All items are open and carry a section number,
    # so a fetch goes straight to its downloadsection URL -- no article-page hop.
    PAGE0 = [("100", "juli 2026", "1-10"), ("101", "juli 2026", "11-20")]
    PAGE1 = [("200", "mars 2025", "1-8"), ("201", "mars 2025", "9-16")]

    @staticmethod
    def _item(number, published, pages):
        return ('<div class="section-item">'
                '<div class="details">'
                '<h2><a href="/artikel/%s">Titel %s</a></h2>'
                '<div class="authors">'
                '<span class="author"><a href="/forfattare/1">A. Författare</a></span>'
                '</div>'
                '<p class="bookinfo">Publicerad i <a href="/utgava/1">FT 2026 1</a>, '
                '<span>%s</span><span> s. %s</span></p>'
                '</div>'
                '<div class="icons">'
                '<a href="/utgivare/4"><img src="/utils/media/ft-icon.svg" '
                'class="publisher-icon" /></a>'
                '<a href="/artikel/%s"><svg class="icon">'
                '<use xlink:href="#pdf_icon"></use></svg></a>'
                '<svg class="icon open"><use xlink:href="#open-access_icon"></use></svg>'
                '</div></div>') % (number, number, published, pages, number)

    @classmethod
    def _page(cls, items):
        if items is None:
            return "<span>EOF</span>"
        return "".join(cls._item(number, published, pages)
                       for number, published, pages in items)

    def test_a_caught_up_run_stops_short_of_the_depth(self, monkeypatch,
                                                      tmp_path):
        # pin the listing to the three pages above and hand back the platform's
        # own bytes for a downloadsection fetch
        monkeypatch.setattr(lawpub.net, "make_session", lambda ua: object())
        pages = [self._page(self.PAGE0), self._page(self.PAGE1), self._page(None)]
        listings = []

        def fake_request(session, method, url, **kwargs):
            if url == lawpub.LISTING:
                index = int(kwargs["data"]["pageIndex"])
                listings.append(index)
                return _Fake(pages[index])
            if url.startswith(lawpub.LAWPUB_BASE + "/artikel/"):
                return _Fake("<html><div data-sectionid='42'></div></html>")
            # a downloadsection fetch: the platform's own PDF bytes
            return _Fake("ignored", PDF)

        monkeypatch.setattr(lawpub.net, "request", fake_request)

        # first run: an empty store backfills the whole listing, all three pages
        seen, new = lawpub.lawpub_sync(tmp_path, full=True, delay=0.0)
        assert (seen, new) == (4, 4)
        assert listings == [0, 1, 2]                # it walked down to the EOF
        # the store files under the scope's own directory, like a journal's
        assert (tmp_path / "lawpub" / "lawpub-100.json").is_file()
        assert (tmp_path / "lawpub" / ".watermark.json").is_file()

        # second run, caught up: the newest page is all stored, so the walk reads
        # it, meets the March 2025 items past the safety boundary and stops --
        # it never reaches the EOF page and stores nothing new
        listings.clear()
        seen, new = lawpub.lawpub_sync(tmp_path, full=False, delay=0.0)
        assert new == 0
        assert listings == [0, 1]                   # newest page + the stop page
        assert 2 not in listings                    # never the EOF page


# --------------------------------------------------------------------------
# the rail contract: mined, not published -- the source's shared Artiklar row
# --------------------------------------------------------------------------

class TestRailContract:
    """A lawpub article's only publication surface is its line in the
    "Artiklar" rail of the documents it cites -- the same row the journal
    scopes' articles fill, natively now that the scope's artifacts are
    lawreview documents. The line links the platform's own page for the
    article, never a lagen.nu page, which the article does not have."""

    def test_the_rail_line_links_to_the_platform_page(self):
        li = page._citer_line(
            ("https://lagen.nu/lawreview/lawpub/880", "FT 2015 s. 551",
             "En upphandlingsrättslig studie", "lawreview", "lawpub",
             "2015-07-15", "", "Ada Lovelace",
             "https://www.lawpub.se/artikel/880"))
        assert li == ('<li><a href="https://www.lawpub.se/artikel/880">'
                      'En upphandlingsrättslig studie '
                      '(Ada Lovelace, FT 2015 s. 551)</a></li>')
        assert "lagen.nu" not in li
