"""The faceted browse tree: one page per bucket, generated from the REST API.

A whole source is too large for one flat listing, so it is sliced into one or
two facets (a law's subject initial, a case's court + year). Every leaf bucket
becomes its own page ("Författningar som börjar på A", "NJA – Högsta domstolen
2024") with a navigator linking the sibling buckets, so the site is browsable
with no JS.

The generator is deliberately a **client of its own REST API**: it reads the
browse model from ``GET /api/v1/browse`` (the navigator plus each leaf bucket's
ordered, labelled documents) through an in-process ``TestClient`` and writes
static HTML, rather than querying the catalog directly. One projection, so the
static pages and the live API cannot drift.

That is also why this module is not in ``lib/``: it imports ``api.app``, and
``lib/`` may not (rule:lib-never-imports-vertical). Here, beside ``build.py`` in
the composing layer, importing both the API and the render layer is the normal
direction -- which is what retired the checker's last allowlist entry.
"""

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from markupsafe import Markup

from .api import app as api_service
from .lib import catalog, compress, facets, feeds
from .lib.page import page
from .lib.render import (
    LISTS,
    SOURCE_LABEL,
    browse_dir,
    browse_url,
    cross_nav,
    eurlex_axis,
    folkratt_axis,
)

# --------------------------------------------------------------------------
# faceted browse. A whole source is too large for one flat listing, so it is
# sliced into one or two facets (a law's subject initial, a case's court + year).
# The generator is a *client of the REST API*: it reads the browse model from
# GET /api/v1/browse (the navigator + each leaf bucket's ordered, labelled
# documents) and writes static HTML -- it never touches the catalog directly.
# Every leaf bucket becomes its own page ("Författningar som börjar på A",
# "NJA – Högsta domstolen 2024") with a navigator linking the sibling buckets,
# so the site is browsable with no JS.
# --------------------------------------------------------------------------

def _browse_client(catalog_path):
    """An in-process API client bound to `catalog_path` -- the generator consumes
    the same REST endpoints a network client would, with no running server. The
    get_con override is cleared by `generate_all`'s own finally."""
    def _con():
        con = sqlite3.connect("file:%s?mode=ro" % catalog_path, uri=True)
        try:
            yield con
        finally:
            con.close()
    api_service.app.dependency_overrides[api_service.get_con] = _con
    return TestClient(api_service.app)


def _browse_item(doc):
    """One listing entry as a `<dt>` (the bold linked id) + optional `<dd>` (its
    name/description). A statute keeps its split title -- the designation/number
    prefix subdued, the sort subject emphasised, so the eye lands on where it files
    (a non-statute dims the whole entry). Every other source shows the bare id as
    the term and its short name (a case: `namn: sammanfattning`) as the definition."""
    name, desc = doc.get("short_title"), doc.get("description")
    return LISTS.browse_item({
        "subdued": doc.get("subdued"), "url": doc["url"],
        "key": doc.get("key"), "pre": doc.get("pre"),
        "short_id": doc.get("short_id"), "display": doc.get("display"),
        # the definition: a case's `namn: sammanfattning` (name only when it
        # has one), otherwise the short name alone. Always a <dd> (even empty)
        # so the two-column dt/dd grid stays aligned row for row.
        "text": "%s: %s" % (name, desc) if name and desc
                else (desc or name or ""),
        # this entry is the konsoliderade version (B4)
        "consolidated": doc.get("consolidated"),
        # a föreskrift's ändringsförfattningar, nested under it (F5)
        "amendments": [{"url": a["url"], "subdued": a.get("subdued"),
                        "short_id": a.get("short_id"),
                        "display": a.get("display")}
                       for a in doc.get("amendments") or []]})


# the DV browse buckets group into these forms, in this reading order; a bucket
# with only one present shows no headers (nothing to distinguish)
_DV_VARIANTS = (("dom", "Domar"), ("referat", "Referat"), ("notis", "Notiser"))


def _dv_listing(docs):
    """A court+year bucket's cases, grouped Domar / Referat / Notiser (headed only
    when more than one form is present). Referat and Notiser keep their referat-
    number order (facets._dv_doc_sort); bare Domar are re-sorted by avgörandedatum,
    newest first (R2)."""
    groups = {k: [] for k, _ in _DV_VARIANTS}
    for d in docs:
        groups.get(d.get("variant") or "referat", groups["referat"]).append(d)
    groups["dom"].sort(key=lambda d: d.get("date") or "", reverse=True)
    present = [(k, label) for k, label in _DV_VARIANTS if groups[k]]
    return Markup("").join(
        LISTS.dv_group(label if len(present) > 1 else None,
                        Markup("").join(_browse_item(d) for d in groups[k]))
        for k, label in present)


def _facet_links(source, buckets, parent_slugs, active_keys, depth):
    return [{"url": browse_url(source, parent_slugs + [b["slug"]]),
             "current": (depth < len(active_keys)
                         and active_keys[depth] == b["key"]),
             "label": b["label"], "count": b["count"]}
            for b in buckets]


def _facet_nav(source, view, active_keys, primary_in_banner=False):
    """The navigator: the primary buckets as links, plus -- under the active
    primary -- its secondary buckets (the year/… within a court/type). A primary
    axis with a single bucket is not navigable (nothing to choose), so it is
    omitted -- e.g. HUDOC's lone 'Domar' type. It is omitted too when the page
    carries a cross-source selector that already lists the same buckets
    (`primary_in_banner`): eurlex's document types and edpb's series appear in
    the EU-rätt banner above, and a rail repeating them below is the same
    choice offered twice. The föreskrift years never list here either: a large
    samling's year axis rides on top of the list (F4), a small one has no year
    split at all (F3)."""
    levels, buckets = view["levels"], view["buckets"]
    parts = ([{"axis": levels[0]},
              {"axis": None,
               "links": _facet_links(source, buckets, [], active_keys, 0)}]
             if len(buckets) > 1 and not primary_in_banner else [])
    if len(levels) > 1 and source != "foreskrift":
        cur = next((b for b in buckets if b["key"] == active_keys[0]), None)
        if cur and cur["children"]:
            parts.append({"axis": _secondary_axis(source, active_keys[0],
                                                  levels[1])})
            parts.append({"axis": None,
                          "links": _facet_links(source, cur["children"],
                                                [cur["slug"]], active_keys, 1)})
    return LISTS.facet_nav(parts)


def _secondary_axis(source, primary_key, default):
    """The heading for the second facet axis. Usually the level's own name ('År'),
    but the eurlex Fördrag are grouped by treaty family, not year (E1), so that
    branch is headed neutrally instead of mislabelled 'År'."""
    return "Kategori" if source == "eurlex" and primary_key == "treaty" else default


def _bucket_heading(source, levels, nodes):
    """The reading heading for a leaf bucket -- 'Författningar som börjar på A',
    'NJA – Högsta domstolen 2024', 'Förordningar 2016'. The eurlex Fördrag family
    label is self-describing, so it stands alone rather than trailing 'Fördrag'.
    A författningssamling heads by its official name + designation --
    'Åklagarmyndighetens författningssamling (ÅFS)' (F2) -- with the year
    appended only where the samling is year-partitioned."""
    if source == "foreskrift":
        info = facets.fs_series_info(nodes[0]["key"])
        series = ("%s (%s)" % (info["title"], info["designation"]) if info
                  else nodes[0]["label"])
        return "%s %s" % (series, nodes[1]["label"]) if len(nodes) > 1 else series
    if len(levels) == 1:
        return "%s som börjar på %s" % (SOURCE_LABEL.get(source, source), nodes[0]["key"])
    if source == "eurlex" and nodes[0]["key"] == "treaty":
        return nodes[1]["label"]
    return "%s %s" % (nodes[0]["label"], nodes[1]["label"])


def _fs_bucket_slugs(prim):
    """The fs slugs actually contributing documents to a series bucket --
    nested amendments included -- from the bucket's own rows, so the
    succession note can never claim more than the listing shows."""
    docs = prim.get("documents") or [d for sec in prim.get("children") or []
                                     for d in sec.get("documents") or []]
    return {catalog.local(d["uri"]).split("/")[0]
            for doc in docs for d in (doc, *(doc.get("amendments") or []))}


def _fs_series_note(prim):
    """The succession note on a samling that carries a predecessor's documents
    (F8): 'Här listas även äldre föreskrifter ur Datainspektionens
    författningssamling (DIFS).' -- or '' where nothing folded in. Only
    predecessors whose documents are actually in the bucket are named: the
    registry knows RÅFS preceded ÅFS, but with no RÅFS document in the corpus
    the page must not say it lists any."""
    names = ["%s (%s)" % (entry["title"], entry["designation"])
             for slug, entry in facets.fs_predecessors(prim["key"])
             if slug in _fs_bucket_slugs(prim)]
    if not names:
        return ""
    listed = (" och ".join([", ".join(names[:-1]), names[-1]])
              if len(names) > 1 else names[0])
    return "Här listas även äldre föreskrifter ur %s." % listed


# What each kind of EU document *is*, one sentence, on that type's browse page
# (N2). The EU-rätt landing is one of these pages, so it stops being a bare list
# of treaty versions. What a reader needs first is who the document binds -- a
# förordning binds everyone directly, a direktiv binds only the states and only
# as to the result, a generaladvokat's förslag binds nobody -- because that is
# what decides whether the text answers their question at all.
_EU_TYPE_NOTE = {
    "treaty": "Unionens grundfördrag: den konstitutionella grund medlemsstaterna "
              "har slutit med varandra och som all annan EU-rätt vilar på.",
    "regulation": "En förordning gäller direkt som lag i varje medlemsstat, utan "
                  "att först behöva genomföras i svensk rätt.",
    "directive": "Ett direktiv binder medlemsstaterna i fråga om det resultat som "
                 "ska uppnås, men överlåter åt varje stat att välja form och "
                 "medel. Det gäller alltså inte direkt här, utan genomförs genom "
                 "svensk lag eller förordning.",
    "decision": "Ett beslut är bindande i alla sina delar, men bara för dem som "
                "det riktar sig till.",
    "judgment": "EU-domstolens avgöranden. Domstolen tolkar EU-rätten med "
                "bindande verkan, oftast sedan en nationell domstol har begärt "
                "förhandsavgörande.",
    "opinion": "Generaladvokatens förslag till avgörande är en fristående "
               "rättsutredning inför domstolens dom. Det binder inte domstolen, "
               "men följs ofta och används för att förstå domskälen.",
    "act": "Rekommendationer, yttranden och andra rättsakter som inte binder på "
           "samma sätt som förordningar, direktiv och beslut.",
    "riktlinjer": "Europeiska dataskyddsstyrelsens riktlinjer om hur "
                  "dataskyddsförordningen ska tillämpas. De är inte bindande, men "
                  "väger tungt i tillsynsmyndigheternas praxis.",
    "rekommendationer": "Europeiska dataskyddsstyrelsens rekommendationer: "
                        "praktisk vägledning i en avgränsad fråga, utan bindande "
                        "verkan.",
    "wp": "Artikel 29-gruppen var EDPB:s föregångare. Dess vägledningar rör "
          "dataskyddsdirektivet, men flera av dem har uttryckligen bekräftats "
          "under dataskyddsförordningen.",
}


def _bucket_note(source, nodes):
    """The editorial line under a browse bucket's heading: what a föreskrift
    samling carries beyond its own series (F8), or what a kind of EU document
    is and whom it binds (N2). '' where the bucket has nothing to add."""
    if source == "foreskrift":
        return _fs_series_note(nodes[0])
    if source in ("eurlex", "edpb"):
        return _EU_TYPE_NOTE.get(nodes[0]["key"], "")
    return ""


def render_facet_page(source, view, nodes, banner="", primary_in_banner=False):
    """A single browse bucket page: an optional cross-source `banner` (the shared
    folkrätt selector, the EU-rätt selector, a large samling's year axis), the
    navigator, and this leaf bucket's document list. `nodes` is the bucket-node
    path (one per level, or the single merged node of an unpartitioned samling);
    the leaf carries its `documents` (from the API, already ordered and
    labelled). `primary_in_banner` says the banner already lists this source's
    primary buckets, so the rail leaves them to it."""
    heading = _bucket_heading(source, view["levels"], nodes)
    docs = nodes[-1].get("documents") or []
    if not docs:
        listing = LISTS.empty()
    elif source == "dv":                                 # grouped Domar/Referat/Notiser
        listing = _dv_listing(docs)
    else:
        # SFS is a dt-only split title (single column); every other source is a
        # two-column dt/dd definition list (the bold id left, its name/desc right)
        css = "browse-list" if source == "sfs" else "browse-list def"
        listing = Markup('<dl class="%s">%s</dl>') % (
            css, Markup("").join(_browse_item(d) for d in docs))
    body = LISTS.facet_page_body(
        Markup(banner), _facet_nav(source, view, [n["key"] for n in nodes],
                                   primary_in_banner),
        heading, len(docs), listing, note=_bucket_note(source, nodes))
    alias = feeds.alias_for_source(source)
    discovery = (Markup('<link rel="alternate" type="application/atom+xml" '
                        'href="/dataset/%s/feed.atom">') % alias) if alias else ""
    return page(heading, "Bläddra", "", body, solo=True, head=discovery,
                body_class=" browse", own_h1=True)


def _write_browse(out_root, source, slugs, html):
    target = Path(out_root).joinpath(browse_dir(source), *slugs)
    target.mkdir(parents=True, exist_ok=True)
    compress.write_text(target / "index.html", html,
                        encodings=compress.PAGE_ENCODINGS)
    return target


def _write_succeeded_series(out_root, source, view):
    """A page at every författningssamling that a rename or a disbandment took
    out of the tree, saying where its föreskrifter list now (B2), and returning
    the directories written so the reaper keeps them.

    Their documents fold into the successor's bucket (F8), so no bucket -- and
    before this no page -- carried their slug, while their addresses stayed in
    circulation: linked from elsewhere, indexed, and cited in the föreskrifter
    themselves. The successor's page has said what it carries for a while; this
    is the other direction."""
    live = {b["slug"].lower() for b in view["buckets"]}
    written = set()
    for slug, entry in sorted(facets.FS_SERIES.items()):
        successor = entry.get("successor")
        if not successor or slug in live:
            continue
        # follow the chain to the samling that carries the documents today
        # (säifs -> srvfs -> msbfs -> mcffs), which is the only one to send a
        # reader to -- an intermediate is as retired as the slug we are on
        final = facets.fs_live_series(slug)
        heading = "%s (%s)" % (entry["title"], entry["designation"])
        html = page(
            heading, "Bläddra", "",
            LISTS.succeeded_series_body(
                _facet_nav(source, view, [final.upper()]), heading,
                "%s (%s)" % (facets.FS_SERIES[final]["title"],
                             facets.FS_SERIES[final]["designation"]),
                browse_url(source, [final])),
            solo=True, body_class=" browse", own_h1=True)
        written.add(_write_browse(out_root, source, [slug], html))
    return written


def _reap_browse(out_root, source, written):
    """Delete the browse pages this run did not write, deepest-first so a
    parent is considered only after its children.

    Generate rebuilds the pages it still plans, but nothing used to remove the
    output of a bucket that had *left* the tree -- and a författningssamling
    leaves the tree for good when its agency is renamed and F8 folds it into a
    successor. The abandoned pages kept serving, with the masthead, the series
    nav and the naming of whichever build last wrote them, which reads as a
    dozen unrelated UI bugs rather than one un-reaped directory (B1).

    Two things keep this from reaching past its own output. A source's browse
    root can *contain* another's -- edpb browses under `eurlex/vagledning`, and
    hudoc under `folkratt/hudoc` -- so a sibling's root is pruned rather than
    walked; without that, generating eurlex deleted the whole EDPB tree on
    every run, since none of it is in eurlex's `written`. And only the index
    files this module writes are removed, with the directory going only if it
    is then empty: a browse bucket that shares a directory with anything else
    (a document page, a future sidecar) keeps that content."""
    root = Path(out_root) / browse_dir(source)
    if not root.is_dir():
        return 0
    # only the roots nested *inside* this one need protecting. A root that is an
    # ancestor of ours is not a sibling to leave alone -- guarding against it too
    # made the reaper a silent no-op for edpb, whose own root sits under eurlex's.
    others = {p for p in (Path(out_root) / browse_dir(s) for s in facets.sources()
                          if s != source)
              if p != root and p.is_relative_to(root)}
    reaped = 0
    for d in sorted(root.rglob("*"), key=lambda p: -len(p.parts)):
        if not d.is_dir() or d in written:
            continue
        if any(o == d or o in d.parents for o in others):
            continue                    # another source's browse root, or inside one
        for f in d.glob("index.html*"):
            f.unlink()
        if not any(d.iterdir()):
            d.rmdir()
            reaped += 1
    return reaped


# a författningssamling with fewer documents than this (amendments included)
# lists on one page (F3); at or above it, one page per year with the year axis
# on top of the list (F4)
FS_YEAR_SPLIT_MIN = 200


def _fs_docs(sec):
    """A year bucket's total listing size: its entries plus their nested
    ändringsförfattningar -- what decides whether a samling needs year pages."""
    docs = sec.get("documents") or []
    return len(docs) + sum(len(d.get("amendments") or []) for d in docs)


def _fs_year_axis(source, prim):
    """The year selector of one samling, as a one-group cross-axis (F4)."""
    return [("År", [(sec["key"], sec["label"],
                     browse_url(source, [prim["slug"], sec["slug"]]),
                     sec["count"])
                    for sec in prim["children"]])]


def generate_browse(client, source, out_root, cross_axis=None):
    """Write every leaf-bucket page of one source from the API's browse model,
    plus the landing copies: a primary bucket's directory shows its first
    (default) child, and the source root shows the overall default bucket -- so
    /dom/, /dom/nja/ and /dom/nja/2025/ all resolve without a redirect or JS.
    `generate_all` already skips the sources the API does not facet -- kommentar,
    plus the four folkrätt instrument sources listed in full on their own
    landing -- so every `source` reaching here is faceted. `cross_axis` (a
    cross-source selector's groups) prepends that selector to each page,
    marking this source's primary bucket current -- passed for the sources that
    share a masthead entry with a sibling source, so a reader switches between
    them from any leaf: hudoc (the folkrätt selector) and eurlex/edpb (the
    EU-rätt one). That selector lists this source's own primary buckets, so the
    rail beside the listing leaves them to it rather than offering the same
    choice twice.

    A small författningssamling collapses its year buckets into one listing
    (F3); a large one keeps year pages, with the year selector as a top banner
    on each (F4)."""
    resp = client.get("/api/v1/browse", params={"source": source})
    view = resp.json()
    root_html = None
    written = set()
    for prim in view["buckets"]:
        banner = (cross_nav(cross_axis, "%s:%s" % (source, prim["slug"]))
                  if cross_axis else "")
        year_axis = None
        if source == "foreskrift" and prim["children"]:
            if sum(_fs_docs(sec) for sec in prim["children"]) < FS_YEAR_SPLIT_MIN:
                prim = dict(prim, children=None,
                            documents=[d for sec in prim["children"]
                                       for d in sec.get("documents") or []])
            else:
                year_axis = _fs_year_axis(source, prim)
        leaves = [[prim, sec] for sec in prim["children"]] if prim["children"] \
            else [[prim]]
        for i, nodes in enumerate(leaves):
            slugs = [n["slug"] for n in nodes]
            if year_axis:
                banner = cross_nav(year_axis, nodes[1]["key"])
            html = render_facet_page(source, view, nodes, banner=banner,
                                     primary_in_banner=cross_axis is not None)
            written.add(_write_browse(out_root, source, slugs, html))
            if len(nodes) > 1 and i == 0:        # primary landing = first child
                written.add(_write_browse(out_root, source, slugs[:1], html))
            if root_html is None:                # overall default = first leaf
                root_html = html
    written.add(_write_browse(out_root, source, [], root_html))
    if source == "foreskrift":
        written |= _write_succeeded_series(out_root, source, view)
    _reap_browse(out_root, source, written)


# --------------------------------------------------------------------------
# generate the whole site
# --------------------------------------------------------------------------


def generate_all(catalog_path, out_root, con):
    """Write the whole browse tree: every browsable source's facet pages.

    The API client is bound to `catalog_path` for the duration and its
    dependency override cleared afterwards -- `app.dependency_overrides` is
    process-global state, so leaving it set would bleed into any later use of
    the app in the same process (the test suite, an `api serve` in the same
    interpreter)."""
    folk_axis = folkratt_axis(con)
    eu_axis = eurlex_axis(con)
    client = _browse_client(catalog_path)
    try:
        for source in catalog.counts(con):
            # kommentar is an annotation layer, not a browsable source; the coe,
            # icrc, untc and icc instruments are listed in full on the folkrätt
            # landing instead of a faceted-by-year tree of their own
            if source in ("kommentar", "coe", "icrc", "untc", "icc"):
                continue
            # hudoc browses under /folkratt/hudoc/ and edpb under
            # /eurlex/vagledning/, each carrying the selector it shares with the
            # source it shares a masthead entry with; every other source browses
            # on its own
            generate_browse(
                client, source, out_root,
                cross_axis=(folk_axis if source == "hudoc"
                            else eu_axis if source in ("eurlex", "edpb")
                            else None))
    finally:
        api_service.app.dependency_overrides.pop(api_service.get_con, None)
