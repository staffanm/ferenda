"""Legacy-compatible Atom feeds over the ferenda catalog."""

import json
import os
import xml.etree.ElementTree as ET

from ferenda.lib import catalog, feeds, render

ATOM = "{http://www.w3.org/2005/Atom}"


def _catalog(tmp_path):
    db = tmp_path / "catalog.sqlite"

    law = tmp_path / "law.json"
    law.write_text(json.dumps({
        "uri": "https://lagen.nu/2024:1",
        "metadata": {"properties": {
            "dcterms:title": "Lag (2024:1) om prov",
            "rpubl:utfardandedatum": "2024-01-02"}},
        "structure": [],
    }))
    regulation = tmp_path / "regulation.json"
    regulation.write_text(json.dumps({
        "uri": "https://lagen.nu/2025:2",
        "metadata": {"properties": {
            "dcterms:title": "Förordning (2025:2) om prov",
            "rpubl:utfardandedatum": "2025-02-03"}},
        "structure": [],
    }))
    # An older publication updated after the newer publication must lead a
    # new-and-updated feed.
    os.utime(regulation, ns=(1_700_000_001_000_000_000,) * 2)
    os.utime(law, ns=(1_700_000_002_000_000_000,) * 2)
    catalog.rebuild(db, "sfs", [law, regulation])

    rule = tmp_path / "rule.json"
    rule.write_text(json.dumps({
        "type": "foreskrift", "uri": "https://lagen.nu/nfs/2025:1",
        "identifier": "NFS 2025:1", "fs": "nfs",
        "metadata": {"title": "Provföreskrift", "publisher": "Naturvårdsverket",
                     "utkomFranTryck": "2025-03-04"},
        "structure": [],
    }))
    catalog.rebuild(db, "foreskrift", [rule])
    return catalog.connect(db)


def test_a_feed_reads_newest_date_first_and_uses_stable_document_ids(tmp_path):
    """The entries a reader sees are ordered by the document's own date, newest
    first. Ordering them by artifact mtime instead printed the dv feed as 2000,
    2005, 2002, 1998 down the page -- every line dated, no line in order."""
    con = _catalog(tmp_path)
    rows = feeds.entries(con, feeds.dataset("sfs"))
    assert [row.uri for row in rows] == [
        "https://lagen.nu/2025:2", "https://lagen.nu/2024:1"]
    atom = feeds.render_atom(feeds.dataset("sfs"), rows)
    root = ET.fromstring(atom)
    assert root.find(ATOM + "id").text == "https://lagen.nu/dataset/sfs/feed.atom"
    assert [node.text for node in root.findall(ATOM + "entry/" + ATOM + "id")] \
        == ["https://lagen.nu/2025:2", "https://lagen.nu/2024:1"]
    assert root.findall(ATOM + "entry/" + ATOM + "published")[-1].text \
        == "2024-01-02T00:00:00Z"


def test_a_feed_still_selects_the_documents_it_last_updated(tmp_path):
    """Which documents a feed holds is a different question from how they are
    ordered: it is a feed of new *and updated* documents, so the selection is by
    artifact mtime. The older publication was re-parsed last, so it is the one
    a one-entry feed carries -- even though the newer publication would lead the
    page when both are in."""
    con = _catalog(tmp_path)
    assert [row.uri for row in feeds.entries(con, feeds.dataset("sfs"), limit=1)] \
        == ["https://lagen.nu/2024:1"]


def test_legacy_query_parameters_filter_feeds(tmp_path):
    con = _catalog(tmp_path)
    sfs = feeds.dataset("sfs")
    assert [row.uri for row in feeds.entries(con, sfs, rdf_type="type/lag")] \
        == ["https://lagen.nu/2024:1"]
    assert [row.uri for row in feeds.entries(
        con, sfs, rdf_type="type/forordning")] == ["https://lagen.nu/2025:2"]

    myndfs = feeds.dataset("myndfs")
    rows = feeds.entries(
        con, myndfs, dcterms_publisher="publisher/naturvardsverket")
    assert [row.uri for row in rows] == ["https://lagen.nu/nfs/2025:1"]
    atom = feeds.render_atom(
        myndfs, rows,
        {"dcterms_publisher": "publisher/naturvardsverket"})
    assert ("/dataset/myndfs/feed.atom?dcterms_publisher="
            "publisher%2Fnaturvardsverket") in atom


def test_nonmatching_publisher_filter_does_not_open_artifacts(tmp_path, monkeypatch):
    con = _catalog(tmp_path)
    monkeypatch.setattr(
        feeds.catalog, "load_artifact",
        lambda *_args: (_ for _ in ()).throw(AssertionError("artifact scan")))
    assert feeds.entries(
        con, feeds.dataset("myndfs"),
        dcterms_publisher="publisher/finns_inte") == []


def test_public_aliases_map_to_source_names():
    assert feeds.dataset("forarbeten").source == "forarbete"
    assert feeds.dataset("myndfs").source == "foreskrift"
    assert feeds.dataset("myndprax").source == "avg"
    assert feeds.dataset("myndrs").source == "rs"
    assert feeds.dataset("keyword").source == "begrepp"


def test_every_dataset_is_reachable_from_the_feed_index(tmp_path):
    """A `Dataset` the feed index does not list is a feed written to disk that
    nothing on the site links -- which is how the rs feed shipped unreachable.
    The index is hand-built per source, so this is the guard that adding a
    dataset and forgetting its group cannot pass silently."""
    # an empty catalog is enough: the per-publisher rows vary with the corpus,
    # but every dataset's own "Samtliga …" entry is unconditional
    con = catalog.connect(tmp_path / "catalog.sqlite")
    listed = {alias for _group, links in render._feed_index_groups(con)
              for _label, alias, _params in links}
    assert {d.alias for d in feeds.DATASETS} <= listed


def test_feed_index_names_its_series_from_the_facet_scheme(tmp_path):
    """The förarbete types, the avg organs and the rs myndigheter are named --
    and ordered -- by `facets.SCHEMES`, the one table that names a bucket of
    those sources (the browse tree and the /myndigheter landing read it too).
    Restated in the feed index, the copies drifted: betänkanden listed as "Alla
    bet" and JO as "Riksdagens ombudsmän", where its own browse bucket says
    "Justitieombudsmannen (JO)".

    The bucket key doubles as the feed's filter value, so each generated link
    must also select the documents it promises -- a facet key that stopped being
    the catalog `kind` would leave every link listing nothing."""
    db = tmp_path / "catalog.sqlite"
    bet = tmp_path / "bet.json"
    bet.write_text(json.dumps({
        "uri": "https://lagen.nu/bet/2024/25:JuU1", "doctype": "bet",
        "identifier": "Bet. 2024/25:JuU1", "title": "Ett betänkande",
        "date": "2024-11-01", "body": []}))
    catalog.rebuild(db, "forarbete", [bet])
    beslut = tmp_path / "jo.json"
    beslut.write_text(json.dumps({
        "uri": "https://lagen.nu/avg/jo/2340-2025", "org": "jo",
        "identifier": "JO dnr 2340-2025",
        "metadata": {"title": "Ett beslut", "beslutsdatum": "2025-03-04"}}))
    catalog.rebuild(db, "avg", [beslut])
    stallning = tmp_path / "fk.json"
    stallning.write_text(json.dumps({
        "uri": "https://lagen.nu/rs/fk/2025-01", "org": "fk",
        "identifier": "FKRS 2025:01",
        "metadata": {"title": "Ett ställningstagande", "beslutsdatum": "2025-01-02"}}))
    catalog.rebuild(db, "rs", [stallning])
    con = catalog.connect(db)

    groups = dict(render._feed_index_groups(con))
    assert ("Alla betänkanden", "forarbeten", {"rdf_type": "type/bet"}) \
        in groups["Förarbeten"]
    assert ("Dokument publicerade av Justitieombudsmannen (JO)", "myndprax",
            {"dcterms_publisher": "publisher/jo"}) in groups["Praxis"]
    assert ("Ställningstaganden publicerade av Försäkringskassan (FKRS)",
            "myndrs", {"dcterms_publisher": "publisher/fk"}) \
        in groups["Rättsliga ställningstaganden"]
    # every generated link selects its own documents
    for heading in ("Förarbeten", "Praxis", "Rättsliga ställningstaganden"):
        for _label, alias, params in groups[heading]:
            assert feeds.entries(con, feeds.dataset(alias), **params), \
                "%s: %s selects nothing" % (alias, params)


def test_document_date_covers_every_source_field_with_stable_precedence():
    """catalog.document_date is the one home for the date-field policy (feeds
    ordering, documents.date at relate, chronology panels). Each source's field
    must resolve, in the documented precedence order."""
    chain = [
        ({"date": "2024-01-01"}, "2024-01-01"),                       # forarbete
        ({"avgorandedatum": "2024-02-02"}, "2024-02-02"),             # dv
        ({"metadata": {"beslutsdatum": "2024-03-03"}}, "2024-03-03"),  # avg
        ({"metadata": {"utkomFranTryck": "2024-04-04"}}, "2024-04-04"),  # foreskrift
        ({"metadata": {"properties":
                       {"rpubl:utfardandedatum": "2024-05-05"}}}, "2024-05-05"),  # sfs
        ({"metadata": {"properties":
                       {"rpubl:avgorandedatum": "2024-06-06"}}}, "2024-06-06"),
        ({"metadata": {"properties":
                       {"rpubl:beslutsdatum": "2024-07-07"}}}, "2024-07-07"),
    ]
    # each field alone resolves
    for art, expected in chain:
        assert catalog.document_date(art) == expected
    # all fields at once: the chain's head wins
    everything = {
        "date": "2024-01-01", "avgorandedatum": "2024-02-02",
        "metadata": {"beslutsdatum": "2024-03-03",
                     "utkomFranTryck": "2024-04-04",
                     "properties": {"rpubl:utfardandedatum": "2024-05-05",
                                    "rpubl:avgorandedatum": "2024-06-06",
                                    "rpubl:beslutsdatum": "2024-07-07"}}}
    assert catalog.document_date(everything) == "2024-01-01"
    assert catalog.document_date({"metadata": {"properties": {}}}) is None


def test_an_expired_document_leaves_the_feed(tmp_path):
    """A feed of a corpus is a listing of it, so the rule the browse trees and
    search already apply holds here too: a repealed act and a withdrawn
    rättsligt ställningstagande no longer state law.

    The ordering is what made this urgent. Entries sort by artifact mtime, so a
    re-parse lifts every document it touched to the top -- re-parsing an rs
    corpus holding 699 withdrawn positions would have put all 699 above the
    newest one that still applies."""
    db = tmp_path / "catalog.sqlite"
    live = tmp_path / "live.json"
    live.write_text(json.dumps({
        "uri": "https://lagen.nu/rs/skv/8-1", "type": "stallningstagande",
        "org": "skv", "identifier": "Skatteverkets ställningstagande dnr 8-1",
        "designation": "8-1",
        "metadata": {"title": "Gällande", "publisher": "Skatteverket",
                     "nummer": "8-1", "status": "gällande",
                     "beslutsdatum": "2024-01-01"},
        "structure": []}))
    withdrawn = tmp_path / "withdrawn.json"
    withdrawn.write_text(json.dumps({
        "uri": "https://lagen.nu/rs/skv/8-2", "type": "stallningstagande",
        "org": "skv", "identifier": "Skatteverkets ställningstagande dnr 8-2",
        "designation": "8-2",
        "metadata": {"title": "Upphävt", "publisher": "Skatteverket",
                     "nummer": "8-2", "status": "upphävt",
                     "upphavd": "2025-06-01", "beslutsdatum": "2024-01-01"},
        "structure": []}))
    # the withdrawn one is the more recently re-parsed, so without the filter it
    # would head the feed
    os.utime(live, ns=(1_700_000_001_000_000_000,) * 2)
    os.utime(withdrawn, ns=(1_700_000_002_000_000_000,) * 2)
    catalog.rebuild(db, "rs", [live, withdrawn])
    con = catalog.connect(db)
    got = [e.title for e in feeds.entries(con, feeds.BY_SOURCE["rs"])]
    assert got == ["Gällande"], got
    con.close()


def test_every_feed_screen_carries_the_source_selector(tmp_path):
    """A feed page is one screen with the same chrome, whichever feed it is:
    the entries in the reading column, every other feed in the left rail. The
    editorial news feed and a live filtered request render through the same two
    macros as the generated per-dataset page, so a reader who arrives at one
    feed can reach the other fifteen.

    Before this the three drifted: the news feed was an editorial page, the
    generated feed page a bare listing, and a filtered request a chrome-free
    HTML twin with no stylesheet at all."""
    con = _catalog(tmp_path)
    item = feeds.dataset("sfs")
    html = feeds.render_page(item, feeds.entries(con, item))
    # the selector names every dataset, and the current one is marked
    for other in feeds.DATASETS:
        assert '"%s"' % feeds.feed_url(other.alias).removeprefix(feeds.BASE) in html
    assert feeds.SITENEWS_URL in html
    assert '<a href="/dataset/sfs/feed" aria-current="page">' in html
    # …on the browse screen (rail + reading column), inside the site chrome
    assert '<aside class="browse-facets">' in html
    assert '<link rel="stylesheet" href="/style.css">' in html
    # the news feed is a feed among the others: same rail, marked on itself
    news = feeds.nav(feeds.SITENEWS_ALIAS)
    assert '<a href="%s" aria-current="page">' % feeds.SITENEWS_URL in news
    assert '"/dataset/sfs/feed"' in news


def test_a_dataset_exists_for_every_browsable_source():
    """Every source the site browses has a feed, so the selector is a complete
    list of the corpus rather than the eight repositories the legacy site
    happened to publish -- the six folkrätt sources had none at all."""
    assert set(render.SOURCE_ORDER) <= {d.source for d in feeds.DATASETS}
