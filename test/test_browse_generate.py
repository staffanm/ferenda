"""The browse generator's directory hygiene: pages whose bucket has left the
tree are removed (B1), and a författningssamling that a rename folded into a
successor keeps an address that says where its föreskrifter went (B2).

Hermetic -- no catalog and no API client; both units take the browse *view*
(what the API would have returned) and an output directory.
"""

import re

from ferenda.site import browse
from ferenda.lib import compress

# the shape /api/v1/browse returns: one level, the live series as buckets
VIEW = {"levels": ["Serie"],
        "buckets": [{"key": "FKFS", "slug": "fkfs", "label": "FKFS", "count": 2,
                     "children": None},
                    {"key": "MCFFS", "slug": "mcffs", "label": "MCFFS", "count": 1,
                     "children": None}]}


def _page_text(path):
    return compress.read_text(path / "index.html")


def test_reap_removes_directories_this_run_did_not_write(tmp_path):
    keep = tmp_path / "foreskrift" / "fkfs"
    keep.mkdir(parents=True)
    (keep / "index.html").write_text("current")
    for gone in ("rffs", "rffs/2004", "bogusfs"):
        d = tmp_path / "foreskrift" / gone
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text("from an older build")

    removed = browse._reap_browse(tmp_path, "foreskrift", {keep})

    assert removed == 3
    assert keep.exists()
    assert not (tmp_path / "foreskrift" / "rffs").exists()
    assert not (tmp_path / "foreskrift" / "bogusfs").exists()


def test_reap_keeps_a_nested_child_written_this_run(tmp_path):
    # depth-first order matters: a parent is only removed after its children,
    # and a written child must keep its parent alive
    parent = tmp_path / "foreskrift" / "skolfs"
    child = parent / "2026"
    child.mkdir(parents=True)
    (child / "index.html").write_text("current")
    (parent / "index.html").write_text("current")

    assert browse._reap_browse(tmp_path, "foreskrift", {parent, child}) == 0
    assert child.exists()


def test_succeeded_series_page_names_the_samling_that_carries_it_now(tmp_path):
    written = browse._write_succeeded_series(tmp_path, "foreskrift", VIEW)

    rffs = tmp_path / "foreskrift" / "rffs"
    assert rffs in written
    html = _page_text(rffs)
    assert "Riksförsäkringsverkets författningssamling (RFFS)" in html
    note = re.sub("<[^>]+>", "", re.search(r'browse-note">(.*?)</p>', html).group(1))
    assert note == ("Serien är avslutad. Föreskrifterna listas numera under "
                    "Försäkringskassans författningssamling (FKFS).")
    assert 'href="/foreskrift/fkfs/"' in html


def test_succeeded_series_follows_the_whole_chain(tmp_path):
    # säifs -> srvfs -> msbfs -> mcffs: an intermediate is as retired as the
    # slug we are on, so the reader is sent to the samling that holds them today
    browse._write_succeeded_series(tmp_path, "foreskrift", VIEW)
    html = _page_text(tmp_path / "foreskrift" / "säifs")
    assert "Myndigheten för civilt försvars författningssamling (MCFFS)" in html
    assert 'href="/foreskrift/mcffs/"' in html


def test_a_live_series_gets_no_succeeded_page(tmp_path):
    written = browse._write_succeeded_series(tmp_path, "foreskrift", VIEW)
    assert tmp_path / "foreskrift" / "fkfs" not in written
    assert not (tmp_path / "foreskrift" / "fkfs").exists()


def test_reap_never_reaches_into_another_sources_browse_root(tmp_path):
    # edpb browses under eurlex/vagledning and hudoc under folkratt/hudoc, so a
    # source's browse root can *contain* another's. Reaping eurlex deleted the
    # whole EDPB tree on every run, since none of it is in eurlex's `written`.
    eurlex = tmp_path / "eurlex"
    keep = eurlex / "directive"
    keep.mkdir(parents=True)
    (keep / "index.html").write_text("current")
    edpb = eurlex / "vagledning" / "riktlinjer"
    edpb.mkdir(parents=True)
    (edpb / "index.html").write_text("edpb's own page")
    (eurlex / "vagledning" / "index.html").write_text("edpb landing")

    browse._reap_browse(tmp_path, "eurlex", {keep})

    assert (edpb / "index.html").exists()
    assert (eurlex / "vagledning" / "index.html").exists()
    assert (keep / "index.html").exists()


def test_reap_removes_only_the_index_pages_it_writes(tmp_path):
    # a browse bucket that shares a directory with anything else keeps that
    # content, and the directory with it
    d = tmp_path / "foreskrift" / "gone"
    d.mkdir(parents=True)
    (d / "index.html").write_text("stale")
    (d / "index.html.br").write_text("stale")
    (d / "something-else.json").write_text("not ours")

    browse._reap_browse(tmp_path, "foreskrift", set())

    assert not (d / "index.html").exists() and not (d / "index.html.br").exists()
    assert (d / "something-else.json").exists() and d.exists()


def test_reap_still_works_for_a_source_nested_inside_another(tmp_path):
    # edpb's own root is eurlex/vagledning. Guarding against every *other*
    # source's root, rather than only the ones inside this one, made the reaper
    # a silent no-op here -- eurlex is an ancestor of edpb's root, so every
    # candidate looked like someone else's tree.
    root = tmp_path / "eurlex" / "vagledning"
    keep = root / "riktlinjer"
    keep.mkdir(parents=True)
    (keep / "index.html").write_text("current")
    gone = root / "2019"
    gone.mkdir()
    (gone / "index.html").write_text("a year bucket that moved")

    assert browse._reap_browse(tmp_path, "guidance", {keep}) == 1
    assert not gone.exists()
    assert (keep / "index.html").exists()


# --------------------------------------------------------------------------
# the year split: a bucket small enough to read at once is one page (F3/F4)
# --------------------------------------------------------------------------

class _FakeClient:
    """Stands in for the API: `generate_browse` asks it for one browse view."""

    def __init__(self, view):
        self.view = view

    def get(self, _path, params=None):
        return self

    def json(self):
        return self.view


def _agency_view(source, level, buckets, variant=None):
    """A two-level browse view: one primary bucket per agency, each split by
    year, in the shape `/api/v1/browse` returns. `variant` is what a grouped
    listing (dv, an EU type) files every entry under -- one value, or one per
    primary bucket."""
    return {"source": source, "levels": [level, "År"], "default": [],
            "buckets": [
                {"key": key, "slug": key, "label": key.upper(),
                 "count": sum(years.values()),
                 "children": [
                     {"key": y, "slug": y, "label": y, "count": n,
                      "children": None,
                      "documents": [{"url": "/%s/%s/%d" % (key, y, i),
                                     "short_id": "%s %s:%d" % (key, y, i),
                                     "short_title": "T",
                                     "variant": (variant.get(key)
                                                 if isinstance(variant, dict)
                                                 else variant)}
                                    for i in range(n)]}
                     for y, n in years.items()]}
                for key, years in buckets.items()]}


def _written_dirs(out_root, source):
    return sorted(str(p.relative_to(out_root / source).as_posix())
                  for p in (out_root / source).rglob("*")
                  if p.is_dir())


def test_begrepp_bucket_lists_terms_in_columns_and_bolds_the_described(tmp_path):
    """A concept has no identifier apart from its name, so the two-column
    dt/dd listing had nothing for the second column and printed the term
    twice. It did so for 571 of 30 882 concepts -- the ones with a wiki page,
    which are the only ones with an artifact and so the only ones with labels;
    the other 30 311 are stub rows for a term the corpus merely uses. The term
    alone is the entry; bold is what says which ones are described."""
    view = {"source": "begrepp", "levels": ["Bokstav"], "default": [],
            "buckets": [{"key": "a", "slug": "a", "label": "A", "count": 2,
                         "children": None,
                         "documents": [
                             # a wiki page describes this one: it has an
                             # artifact, so relate stamped it labels
                             # (labels._begrepp makes the term every form)
                             {"url": "/begrepp/Abandonering",
                              "short_id": "Abandonering",
                              "short_title": "Abandonering"},
                             # a stub row for a term the corpus only uses: no
                             # artifact, so no short_title -- which is the
                             # whole difference, since facets._browse_doc
                             # fills a stub's short_id from its label
                             {"url": "/begrepp/Abonnemangsavtal",
                              "short_id": "Abonnemangsavtal"}]}]}
    browse.generate_browse(_FakeClient(view), "begrepp", tmp_path)
    page = _page_text(tmp_path / "begrepp" / "a")
    assert ('<li><a href="/begrepp/Abandonering"><strong>Abandonering</strong>'
            '</a></li>') in page
    assert ('<li><a href="/begrepp/Abonnemangsavtal">Abonnemangsavtal'
            '</a></li>') in page
    # the term list is its own form -- no dt/dd, so no repeated term
    assert '<ul class="browse-list terms">' in page
    assert "<dd>" not in page
    # the term is printed once, as the link -- not again as its own definition
    assert page.count(">Abandonering<") == 1


def test_a_small_agency_lists_on_one_page(tmp_path):
    """A myndighet whose whole output fits on a screen should not be spread over
    a year selector: Kronofogdens 22 ställningstaganden across 11 years is two
    entries a page and eleven clicks to read the corpus."""
    view = _agency_view("rs", "Myndighet",
                        {"kfm": {"2019": 8, "2020": 7, "2021": 7}})
    browse.generate_browse(_FakeClient(view), "rs", tmp_path)
    assert _written_dirs(tmp_path, "rs") == ["kfm"], "no year pages"
    page = _page_text(tmp_path / "rs" / "kfm")
    for year in ("2019", "2020", "2021"):
        assert "kfm %s:0" % year in page, "every year's documents on the one page"
    # the heading names the bucket, with no year to append
    assert "<h1>KFM <span" in page
    # and no year is offered anywhere: a link to a page this run did not write
    assert "/rs/kfm/2019/" not in page


def test_a_large_agency_keeps_its_year_pages(tmp_path):
    """Past the threshold the list is too long to scan, so the year axis earns
    its click and rides as a banner on each page."""
    view = _agency_view("avg", "Organ",
                        {"jo": {"2024": browse.YEAR_SPLIT_MIN, "2025": 10}})
    browse.generate_browse(_FakeClient(view), "avg", tmp_path)
    assert _written_dirs(tmp_path, "avg") == ["jo", "jo/2024", "jo/2025"]
    # the year selector is on the page, which is what makes the split navigable
    assert "2025" in _page_text(tmp_path / "avg" / "jo" / "2024")


def test_the_split_applies_to_what_a_myndighet_issues(tmp_path):
    """One policy, four sources -- föreskrifter, avgöranden, rättsliga
    ställningstaganden and the EU-rätt types. Every other faceted source keeps
    paging by year: the rule is a display policy, and widening it would change
    addresses that already exist. The year axis rides on top only for what a
    myndighet issues; eurlex's top is the type selector."""
    assert browse.YEAR_SPLIT_SOURCES == {"foreskrift", "avg", "rs", "eurlex"}
    assert browse.YEAR_AXIS_ON_TOP == {"foreskrift", "avg", "rs"}
    view = _agency_view("dv", "Domstol", {"nja": {"2024": 3, "2025": 2}},
                        variant="referat")
    browse.generate_browse(_FakeClient(view), "dv", tmp_path)
    assert _written_dirs(tmp_path, "dom") == ["nja", "nja/2024", "nja/2025"]


def test_every_ancestor_of_a_deeper_leaf_gets_a_landing(tmp_path):
    """guidance is three levels (Utgivare -> Serie -> År), and the facet rail
    links every series directory from every sibling page. Landing copies were
    written only along a primary bucket's *first* depth-first path, so a second
    series with year children -- edps/yttranden here -- got no index page while
    the rail linked it site-wide, and `_reap_browse` deleted any index a
    previous run had left there."""
    def series(key, years):
        return {"key": key, "slug": key, "label": key.upper(),
                "count": sum(years.values()),
                "children": [
                    {"key": y, "slug": y, "label": y, "count": n,
                     "children": None,
                     "documents": [{"url": "/%s/%s/%d" % (key, y, i),
                                    "short_id": "%s %s:%d" % (key, y, i),
                                    "short_title": "T"} for i in range(n)]}
                    for y, n in years.items()]}
    view = {"source": "guidance", "levels": ["Utgivare", "Serie", "År"],
            "default": [],
            "buckets": [
                {"key": "edps", "slug": "edps", "label": "EDPS", "count": 4,
                 "children": [series("riktlinjer", {"2024": 1, "2025": 1}),
                              series("yttranden", {"2023": 2})]},
                # a small utgivare's series is itself a leaf: `only_above`
                # builds no year level under it, so the tree is mixed-depth
                {"key": "ecb", "slug": "ecb", "label": "ECB", "count": 1,
                 "children": [{"key": "con", "slug": "con", "label": "CON",
                               "count": 1, "children": None,
                               "documents": [{"url": "/con/1",
                                              "short_id": "CON 1",
                                              "short_title": "T"}]}]}]}
    browse.generate_browse(_FakeClient(view), "guidance", tmp_path)
    root = tmp_path / "eurlex" / "vagledning"
    dirs = _written_dirs(tmp_path, "eurlex/vagledning")
    assert dirs == ["ecb", "ecb/con",
                    "edps", "edps/riktlinjer", "edps/riktlinjer/2024",
                    "edps/riktlinjer/2025", "edps/yttranden",
                    "edps/yttranden/2023"]
    for d in dirs:
        assert any((root / d).glob("index.html*")), "%s has no index page" % d
    # a landing shows the directory's own first leaf, not its sibling's
    assert "yttranden 2023:0" in _page_text(root / "edps" / "yttranden")
    assert "riktlinjer 2024:0" in _page_text(root / "edps")


# --------------------------------------------------------------------------
# the EU-rätt pages: one type row on top, the years in the rail, a small type
# on one page, a year's acts grouped by who enacted them
# --------------------------------------------------------------------------

_EU_AXIS = [("Dokumenttyp", [
    ("eurlex:treaty", "Fördrag", "/eurlex/treaty/", 5),
    ("eurlex:regulation", "Förordningar", "/eurlex/regulation/", 250),
    ("guidance", "EU-organens vägledningar", "/eurlex/vagledning/", 3)])]


def test_a_small_eu_type_is_one_page_and_the_root_opens_on_it(tmp_path):
    view = _agency_view("eurlex", "Typ", {"treaty": {"2016": 3, "2012": 2},
                                          "regulation": {"2024": 150, "2023": 100}},
                        variant={"treaty": "current", "regulation": "ep"})
    browse.generate_browse(_FakeClient(view), "eurlex", tmp_path, cross_axis=_EU_AXIS)
    assert _written_dirs(tmp_path, "eurlex") \
        == ["regulation", "regulation/2023", "regulation/2024", "treaty"]
    treaty = _page_text(tmp_path / "eurlex" / "treaty")
    assert _page_text(tmp_path / "eurlex") == treaty       # /eurlex/ is the Fördrag page
    assert 'href="/eurlex/treaty/2016/"' not in treaty     # no year links to pages not written
    assert 'aria-current="page">Fördrag' in treaty          # the type row marks it
    reg = _page_text(tmp_path / "eurlex" / "regulation" / "2024")
    assert 'href="/eurlex/regulation/2023/"' in reg          # the split type's years, in the rail
    assert '<h2 class="facet-axis">År</h2>' in reg
    assert 'aria-current="page">Förordningar' in reg


def test_a_guidance_page_marks_the_guidance_entry_and_lists_the_bodies_in_the_rail(tmp_path):
    view = _agency_view("guidance", "Utgivare", {"edpb": {"2020": 2}, "ecb": {"2021": 1}})
    browse.generate_browse(_FakeClient(view), "guidance", tmp_path, cross_axis=_EU_AXIS)
    html = _page_text(tmp_path / "eurlex" / "vagledning" / "edpb" / "2020")
    assert 'aria-current="page">EU-organens vägledningar' in html
    assert html.count('aria-current="page"') == 3     # the type, the body, the year
    assert '<h2 class="facet-axis">Utgivare</h2>' in html
    assert 'href="/eurlex/vagledning/ecb/"' in html


def test_an_eu_year_groups_its_acts_by_who_enacted_them():
    docs = [{"url": "/celex/32024R0436", "short_id": "(EU) 2024/436",
             "short_title": "Revisioner", "variant": "commission"},
            {"url": "/celex/32024R1689", "short_id": "(EU) 2024/1689",
             "short_title": "AI-förordningen", "variant": "ep"},
            {"url": "/celex/31978R2962", "short_id": "31978R2962",
             "short_title": "", "variant": "untitled"}]
    html = browse._grouped_listing(docs, browse._EU_FORMS["regulation"])
    assert re.findall(r'listing-head">([^<]+)<', html) == [
        "Europaparlamentets och rådets förordningar", "Kommissionens förordningar",
        "Utan titel"]
    assert html.index("2024/1689") < html.index("2024/436")
    # one form present: no heading
    assert "listing-head" not in browse._grouped_listing(docs[:1], browse._EU_FORMS["regulation"])
    # a treaty entry is the name alone, so its run is the one-column list
    treaties = [{"url": "/celex/12016M/TXT", "key": "EU-fördraget", "pre": "",
                 "short_id": "12016M/TXT", "variant": "current"},
                {"url": "/celex/12007L/TXT", "key": "Lissabonfördraget", "pre": "",
                 "short_id": "12007L/TXT", "variant": "amending"}]
    html = browse._grouped_listing(treaties, browse._EU_FORMS["treaty"])
    assert re.findall(r'listing-head">([^<]+)<', html) == ["Ändringsfördrag"]
    assert html.startswith('<dl class="browse-list">')
    assert ">EU-fördraget</a>" in html and "<strong>" not in html


def test_a_guidance_series_page_carries_its_note():
    nodes = [{"key": "edpb", "label": "EDPB"}, {"key": "riktlinjer", "label": "Riktlinjer"}]
    assert browse._bucket_note("guidance", nodes).startswith("Europeiska dataskyddsstyrelsens riktlinjer")
    assert browse._bucket_note("guidance", nodes[:1]) == ""
