"""The browse generator's directory hygiene: pages whose bucket has left the
tree are removed (B1), and a författningssamling that a rename folded into a
successor keeps an address that says where its föreskrifter went (B2).

Hermetic -- no catalog and no API client; both units take the browse *view*
(what the API would have returned) and an output directory.
"""

import re

from accommodanda import browse
from accommodanda.lib import compress

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

    assert browse._reap_browse(tmp_path, "edpb", {keep}) == 1
    assert not gone.exists()
    assert (keep / "index.html").exists()
