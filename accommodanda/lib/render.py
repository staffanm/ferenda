"""Site assembly -- the corpus-wide half of the `generate` phase (REWRITE.md §6).

Individual document pages are rendered by their own source
(``<source>/render.py``, dispatched through the registry `generate_site`
receives); what remains here is everything that spans sources and so belongs to
no one of them:

  * the frontpage, the folkrätt and EU-rätt landings and their cross-source axes;
  * the faceted browse tree, generated as a client of the REST API so the static
    pages and the API cannot drift;
  * the Atom feed pages and the shipped static chrome;
  * ``generate_site`` -- the render driver: freshness planning against the
    caller's manifest, then the pages themselves across a process pool.

The page kit every renderer stands on (``Site``, the node walk, the rail, the
page shell) lives in ``lib/page.py``; this module builds on it.
"""
import functools
import hashlib
import json
import re
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date
from html import escape
from pathlib import Path

from markupsafe import Markup

from . import (
    catalog,
    coe,
    compress,
    datasets,
    facets,
    feeds,
)
from .page import Site, doc_relpath, href, page, page_context, site_cross_digests
from .tpl import ENV

# the browser-facing static chrome (stylesheet, client scripts, robots.txt),
# shipped verbatim by render_aggregates -- real .css/.js files rather than
# embedded strings, so editors and linters see them as what they are
ASSETS = Path(__file__).parent / "assets"


LISTS = ENV.get_template("listings.html").module


def _render_search_page():
    """Static shell for the complete, API-backed result list at ``/sok/``."""
    return ENV.get_template("sok.html").render(page_context(
        "Sök", "Sök", "", solo=True))


def _render_admin_page():
    """Static shell for the editor login at ``/admin/``. The sign-in affordance
    lives here, not in the masthead -- editor.js mounts the credential form (or,
    when a session is already live, the logout control) into ``[data-admin-login]``,
    so an anonymous reader's chrome carries no login link."""
    return ENV.get_template("admin.html").render(page_context(
        "Logga in", "Admin", "", solo=True, eyebrow="Redaktörsinloggning"))


def _render_feed_page(item, entries, params=None):
    """Human-readable twin of an Atom document at the legacy ``/feed`` URL."""
    atom = feeds.feed_url(item.alias, atom=True, params=params)
    body = LISTS.feed_page_body(atom, [
        {"date": entry.published[:10], "url": entry.url,
         "title": entry.title, "summary": entry.summary}
        for entry in entries])
    discovery = Markup('<link rel="alternate" type="application/atom+xml" '
                       'href="%s">') % atom
    return page(item.title, "Nyheter", "", body, solo=True, head=discovery)


def _feed_index_groups(con):
    """The legacy all-feeds directory, reshaped from the current catalog."""
    groups = [("Nyheter", [("Nyheter om webbtjänsten", "sitenews", {})])]
    groups.append(("Lagar", [
        ("Alla förordningar", "sfs", {"rdf_type": "type/forordning"}),
        ("Alla lagar", "sfs", {"rdf_type": "type/lag"}),
        ("Alla författningar", "sfs", {}),
    ]))

    dv = []
    if catalog.document_count(con, "dv"):
        tree = facets.tree(con, "dv")
        dv = [("Rättsfall från %s" % bucket["label"], "dv",
               {"rpubl_rattsfallspublikation": bucket["key"]})
              for bucket in tree["buckets"]]
    dv.append(("Samtliga rättsfall", "dv", {}))
    groups.append(("Rättsfall", dv))

    type_labels = {"prop": "Alla propositioner", "sou": "Alla SOU",
                   "ds": "Alla Ds", "dir": "Alla kommittédirektiv",
                   "skr": "Alla skrivelser", "lr": "Alla lagrådsremisser",
                   "fm": "Alla förordningsmotiv", "so": "Alla SÖ"}
    kinds = [row[0] for row in con.execute(
        "SELECT DISTINCT kind FROM documents WHERE source = 'forarbete' ORDER BY kind")]
    fa = [(type_labels.get(kind, "Alla %s" % kind), "forarbeten",
           {"rdf_type": "type/" + kind}) for kind in kinds]
    fa.append(("Samtliga förarbeten", "forarbeten", {}))
    groups.append(("Förarbeten", fa))

    publishers = [("Författningar utgivna av %s" % label, "myndfs",
                   {"dcterms_publisher": "publisher/" + slug})
                  for slug, label, _count in feeds.publisher_options(con)]
    publishers.append(("Samtliga föreskrifter", "myndfs", {}))
    groups.append(("Föreskrifter", publishers))

    avg_labels = {"arn": "Allmänna reklamationsnämnden",
                  "jk": "Justitiekanslern", "jo": "Riksdagens ombudsmän",
                  "imy": "Integritetsskyddsmyndigheten",
                  "kkv": "Konkurrensverket"}
    organs = [row[0] for row in con.execute(
        "SELECT DISTINCT kind FROM documents WHERE source = 'avg' ORDER BY kind")]
    praxis = [("Dokument publicerade av %s" % avg_labels.get(kind, kind),
               "myndprax", {"dcterms_publisher": "publisher/" + kind})
              for kind in organs]
    praxis.append(("Samtliga dokument", "myndprax", {}))
    groups.append(("Praxis", praxis))

    rs_labels = {"fk": "Försäkringskassan", "migr": "Migrationsverket",
                 "kfm": "Kronofogdemyndigheten", "fi": "Finansinspektionen",
                 "imy": "Integritetsskyddsmyndigheten",
                 "kkv": "Konkurrensverket"}
    stallningstaganden = [
        ("Ställningstaganden publicerade av %s" % rs_labels.get(kind, kind),
         "myndrs", {"dcterms_publisher": "publisher/" + kind})
        for kind in (row[0] for row in con.execute(
            "SELECT DISTINCT kind FROM documents WHERE source = 'rs' ORDER BY kind"))]
    stallningstaganden.append(
        ("Samtliga rättsliga ställningstaganden", "myndrs", {}))
    groups.append(("Rättsliga ställningstaganden", stallningstaganden))

    groups.append(("EU-rätt", [
        ("Samtliga EU-rättsakter", "eurlex", {}),
        ("Samtliga riktlinjer och rekommendationer", "euvagledning", {}),
    ]))
    groups.append(("Begrepp", [("Alla nya och ändrade begrepp", "keyword", {})]))
    return groups


def _render_feed_index(con):
    groups = [
        {"heading": heading,
         "links": [{"atom": feeds.feed_url(alias, atom=True,
                                           params=params).removeprefix(feeds.BASE),
                    "html": feeds.feed_url(alias,
                                           params=params).removeprefix(feeds.BASE),
                    "label": label}
                   for label, alias, params in items]}
        for heading, items in _feed_index_groups(con)]
    return page("Alla nyhetsflöden", "Nyheter", "",
                LISTS.feed_index_body(groups), solo=True)


# the sources whose pages carry inline-editable content. A logged-in user edits
# the *commentary* (kommentar rail) on a host act's node -- the official body text
# stays read-only -- so the editable ref is the host's `annotates` basefile: the
# uri's local part, bar eurlex's `ext/celex/` prefix (the bare CELEX the
# commentary frontmatter keys on). A concept page edits its own body.
KOMMENTAR_HOSTS = ("sfs", "eurlex", "foreskrift", "forarbete")


def edit_meta(kind, ref, uri, source="", basefile=""):
    """The `<meta>` that tells editor.js what a page is and which markdown region
    an edit maps to. `source`/`basefile` additionally name the document's own
    identity when it is patchable (see lib.patch), so the editor can offer a
    "patch source" button beside the commentary one. Empty string disables editing
    on the page. Kept a plain string (not a page-shell param) so it can be injected
    uniformly into every renderer's output, including the editorial-site renderer."""
    return ('<meta name="lagen-doc" data-kind="%s" data-ref="%s" '
            'data-source="%s" data-basefile="%s" content="%s">'
            % (escape(kind), escape(ref), escape(source), escape(basefile),
               escape(uri)))


def _document_edit_meta(source, art):
    uri = art["uri"]
    if source in KOMMENTAR_HOSTS:
        local = catalog.local(uri)
        ref = local[len("ext/celex/"):] if local.startswith("ext/celex/") else local
        # the host act's own basefile is `ref`; every KOMMENTAR_HOSTS source is
        # patchable, so pass its identity through for the patch-source button
        return edit_meta("kommentar", ref, uri, source=source, basefile=ref)
    if source == "begrepp":
        return edit_meta("begrepp", art["title"], uri)
    return ""                            # dv / avg pages host no editable content


def _render_myndigheter(con):
    """The /myndigheter/ landing (T1): what förvaltningsmyndigheterna produce
    -- föreskrifter, avgöranden and rättsliga ställningstaganden -- introduced
    side by side, each linking into its own browse tree. The masthead's
    Myndigheter entry lands here."""
    fs = facets.tree(con, "foreskrift")
    body = LISTS.myndigheter_body(
        {"count": sum(b["count"] for b in fs["buckets"]),
         "series": len(fs["buckets"])},
        _myndighet_buckets(con, "avg"), _myndighet_buckets(con, "rs"))
    return page("Myndigheter", "Myndigheter", "", body, solo=True,
                eyebrow="Föreskrifter, avgöranden och ställningstaganden")


def _myndighet_buckets(con, source):
    """A source's top-level buckets as (label, browse url, count) -- the organ
    for avg, the myndighet for rs."""
    return [(b["label"], browse_url(source, [b["slug"]]), b["count"])
            for b in facets.tree(con, source)["buckets"]]


def render_document(art, source, site, renderers):
    """One document's page. `renderers` maps a source key to that source's own
    `render(art, site) -> str` (`build.SOURCE_RENDERERS`) -- lib cannot import a
    source, so the registry is composed by the caller and handed in. A source
    with no entry is a programming error, not a document to skip: the index
    access raises (rule:fail-fast).

    kommentar is deliberately absent -- it is an annotation rendered into
    statute rails (generate_site skips it), not a page of its own."""
    html = renderers[source](art, site)
    meta = _document_edit_meta(source, art)
    alias = feeds.alias_for_source(source)
    discovery = ('<link rel="alternate" type="application/atom+xml" '
                 'href="/dataset/%s/feed.atom">' % alias) if alias else ""
    # injected right before </head> (PAGE has exactly one) rather than threaded
    # through every per-source renderer's page() call
    return html.replace("</head>", discovery + meta + "</head>", 1)


# --------------------------------------------------------------------------
# frontpage
# --------------------------------------------------------------------------

# the document types, in the order they appear on the frontpage, with their
# Swedish collection labels. dv's documents (and so its browse index) live under
# /dom/, lagen.nu's grammar; every other source browses under its own name.
# kommentar is an annotation layer shown in the rail (no page tree), so it is
# not a browsable source on the frontpage
SOURCE_ORDER = ("sfs", "dv", "hudoc", "forarbete", "foreskrift", "avg", "rs",
                "eurlex", "edpb", "coe", "icrc", "untc", "icc", "begrepp")
# the reader-facing source names, defined once in `facets` (which this module
# imports; the reverse would cycle) and re-exported here under the name the
# render layer has always used
SOURCE_LABEL = facets.SOURCE_LABELS
# the international-law sources share one masthead entry and one landing page
# (/folkratt/): a bespoke alphabetical treaty listing (coe) beside the faceted
# case browse (hudoc), which relocates under /folkratt/hudoc/. coe has no faceted
# browse tree of its own -- its whole listing lives on the landing page.
FOLKRATT_SOURCES = ("hudoc", "coe", "icrc", "untc", "icc")
FOLKRATT_LABEL = "Folkrätt"
# edpb browses under the EU-rätt masthead entry it shares with eurlex, the way
# hudoc browses under folkrätt: the guidance belongs beside the rättsakt it
# interprets, and has no address of its own to justify
BROWSE_DIR = {"dv": "dom", "hudoc": "folkratt/hudoc",
              "edpb": "eurlex/vagledning"}


def browse_dir(source):
    return BROWSE_DIR.get(source, source)


def _most_cited(con, source):
    """The 25 most-referenced documents of a source as ranked-list <li>s (the
    highlight reels on the frontpage), or '' if the source is empty."""
    rows = con.execute(
        "SELECT d.uri, COALESCE(d.title, d.label), COUNT(DISTINCT l.from_uri) c "
        "FROM links l JOIN documents d ON d.uri = l.to_root "
        "WHERE d.source = ? AND l.from_uri <> l.to_root "
        "GROUP BY l.to_root ORDER BY c DESC LIMIT 25", (source,)).fetchall()
    return [{"href": href(u), "label": t, "count": c} for u, t, c in rows]


def _index_rows(n):
    """The frontpage source rows as (route, label, count): each browsable source
    in SOURCE_ORDER, but the international-law sources collapsed into one
    'Folkrätt' row (their combined count, linking to the shared landing) at the
    position of the first one present."""
    seen = False
    for s in SOURCE_ORDER:
        if s in FOLKRATT_SOURCES:
            if seen:
                continue
            seen = True
            total = sum(n.get(x, 0) for x in FOLKRATT_SOURCES)
            if total:
                yield "/folkratt/", FOLKRATT_LABEL, total
        elif n.get(s):
            yield "/%s/" % browse_dir(s), SOURCE_LABEL.get(s, s), n[s]


def _render_index(con):
    n = {s: c for s, c in catalog.counts(con).items() if s != "kommentar"}
    # key "entries", not "items": on a dict, Jinja's attribute lookup finds
    # dict.items (the method) before the key
    cols = [{"heading": heading, "entries": entries}
            for source, heading in (("sfs", "Mest hänvisade författningar"),
                                    ("dv", "Mest hänvisade rättsfall"))
            if (entries := _most_cited(con, source))]
    body = LISTS.index_body(sum(n.values()), sum(1 for s in n if n[s]),
                             list(_index_rows(n)), cols)
    return page("lagen.nu", "Start", "", body,
                eyebrow="Sveriges lagar, med kontext", solo=True)


# --------------------------------------------------------------------------
# the international-law (folkrätt) landing at /folkratt/: a bespoke page, not a
# faceted browse. The Council-of-Europe treaties are listed alphabetically by
# their significant title (the SFS listing convention), each with its amending
# protocols nested beneath it, split into a curated central set (the treaties
# named in coe/data/names.json) and the rest A-Z. The European Court of Human
# Rights sits beside them as links into its own faceted browse (relocated under
# /folkratt/hudoc/) plus a most-cited reel.
# --------------------------------------------------------------------------

@functools.lru_cache(maxsize=None)
def _treaty_named(path):
    """A hand-edited treaty names.json (coe or icrc) as {number: entry}: the
    curated central instruments surfaced first on the folkrätt page, each
    carrying the informal Swedish name(s) (`label`) and acronym (`abbr`), either
    a string or a list. Cached per file -- the folkrätt page rebuilds often."""
    return {number: entry
            for number, entry in json.loads(path.read_text("utf-8")).items()
            if isinstance(entry, dict)}


def _ext_number(uri):
    return uri.rsplit("/", 1)[-1]                 # '…/ext/coe/005' -> '005'


def _treaty_rows(con, source):
    """The catalogued treaties of a folkrätt source as the row dicts the two
    listings build from (number parsed off the uri, title/kind/date/identifier,
    plus the artifact path for a listing that needs a field the catalog omits)."""
    return [{"uri": uri, "number": _ext_number(uri), "kind": kind,
             "title": title, "identifier": label, "date": doc_date, "path": path}
            for uri, _src, kind, label, title, _url, path, _display, doc_date,
                _sid, _stitle, _desc
            in catalog.facet_documents(con, source)]


def _first(value):
    """The primary form of a names.json `label`/`abbr` (a string, or the first of
    a list)."""
    return value[0] if isinstance(value, list) else value


def _treaty_parenthetical(row, named, reference):
    """The subdued gloss after a treaty title: its informal Swedish name and
    acronym where registered, then always the given reference --
    'Europakonventionen, EKMR, ETS No. 005', or just 'ICRC 195'."""
    entry = named.get(row["number"]) or {}
    parts = []
    if entry.get("label"):
        name = _first(entry["label"])
        parts.append(name[:1].upper() + name[1:])
    if entry.get("abbr"):
        parts.append(_first(entry["abbr"]))
    parts.append(reference)
    return ", ".join(parts)


def _coe_sort_key(title):
    return coe.significant_title(title)[1].lower()


def _coe_nest(rows):
    """Group Council-of-Europe rows into top-level instruments each carrying its
    amending protocols. A protocol whose parent name (parsed from its title)
    prefix-matches a convention in the corpus nests under it; one that matches
    nothing (a protocol to a protocol, or a parent outside the corpus) stands as
    its own top-level entry. Returns (top_level_rows, {parent_number: [protocol
    rows]}), both ordered for display."""
    conventions = [r for r in rows if r["kind"] != "protocol"]
    # longest title first so a protocol's parent name matches the most specific
    # convention it starts with, not a shorter convention that shares a prefix
    by_title = sorted(conventions, key=lambda r: -len(r["title"]))
    children, orphans = {}, []
    for r in rows:
        if r["kind"] != "protocol":
            continue
        reference = coe.protocol_reference(r["title"])
        parent = next((c for c in by_title if reference
                       and reference.lower().startswith(c["title"].lower())), None)
        if parent:
            children.setdefault(parent["number"], []).append(r)
        else:
            orphans.append(r)
    for kids in children.values():
        kids.sort(key=lambda r: (r["date"] or "", r["number"]))
    top = sorted(conventions + orphans, key=lambda r: _coe_sort_key(r["title"]))
    return top, children


def _coe_entry(row, named, children):
    """One COE instrument as the nested dict listings.coe_entry renders
    (recursively -- the protocols under their convention)."""
    pre, key = coe.significant_title(row["title"])
    return {"url": href(row["uri"]), "pre": pre, "key": key or row["title"],
            "ref": _treaty_parenthetical(row, named, row["identifier"]),
            "children": [_coe_entry(k, named, children)
                         for k in children.get(row["number"], [])]}


def _coe_listing(con):
    """The Council-of-Europe half of the folkrätt page: the central treaties, then
    every other instrument A-Z, protocols nested. '' when the corpus has none."""
    rows = _treaty_rows(con, "coe")
    if not rows:
        return ""
    named = _treaty_named(datasets.COE_NAMES)
    top, children = _coe_nest(rows)
    groups = Markup("").join(
        LISTS.folkratt_group(heading, Markup("").join(
            LISTS.coe_entry(_coe_entry(r, named, children)) for r in members))
        for heading, members in (
            ("Centrala fördrag", [r for r in top if r["number"] in named]),
            ("Övriga fördrag", [r for r in top if r["number"] not in named]))
        if members)
    return LISTS.folkratt_section("Europarådet", groups)


def _icrc_entry(row, named):
    # a flat entry (icrc has no protocol nesting): the full title, then the gloss
    # closing on the ICRC treaty number rather than an ETS/CETS reference
    return LISTS.treaty_li(
        href(row["uri"]), row["title"],
        _treaty_parenthetical(row, named, "ICRC %s" % row["number"]))


# the ICRC's own field_treaty_topics taxonomy, in display order, mapped to the
# Swedish headings that carve the non-central instruments into a browsable index
# (the last entry is the catch-all: its topic plus any unmapped/absent topic).
ICRC_TOPIC_GROUPS = (
    ("Methods and Means of Warfare", "Stridsmetoder och stridsmedel"),
    ("Naval and Air Warfare", "Sjö- och luftkrigföring"),
    ("Victims of Armed Conflicts", "Skydd av krigets offer"),
    ("Cultural Property", "Skydd av kulturegendom"),
    ("Criminal Repression", "Straffrättsligt ansvar"),
    ("Other treaties relating to IHL", "Övriga fördrag"),
)


def _icrc_topic(root, path):
    """The primary ICRC subject a treaty files under -- the first of its
    field_treaty_topics, read from the artifact the catalog omits it from
    (`root` is `catalog.data_root(con)`, so the stored relative path resolves)."""
    topics = catalog.load_artifact(root, path).get("metadata", {}).get("topics") or []
    return topics[0] if topics else None


# shared folkrätt-listing emitters: every treaty/decision source renders its
# groups the same way (a <h3> subject/type heading over a treaty <ul>, wrapped in
# a source <section>), so the HTML lives here once and each listing supplies only
# its bucketing and its per-row `entry` renderer.
def _folkratt_group(heading, members, entry):
    return LISTS.folkratt_group(heading,
                                 Markup("").join(entry(r) for r in members))


def _folkratt_section(title, groups):
    body = Markup("").join(group for group in groups if group)
    return LISTS.folkratt_section(title, body) if body else ""


def _grouped_listing(title, rows, group_of, labels, entry, reverse=False):
    """A folkrätt listing bucketed by `group_of`, headed by the `labels` in their
    order then any trailing bucket, each group sorted by (date, title). '' when
    the source has no rows. The two flat sources (untc, icc) share this whole
    body; icrc keeps its own (a central group + orphan-folded topic index)."""
    if not rows:
        return ""
    by_group = {}
    for row in rows:
        by_group.setdefault(group_of(row), []).append(row)
    order = list(labels) + [key for key in by_group if key not in labels]
    groups = []
    for key in order:
        members = sorted(by_group.get(key, []),
                         key=lambda r: (r["date"] or "", r["title"].lower()),
                         reverse=reverse)
        if members:
            groups.append(_folkratt_group(labels.get(key, key), members, entry))
    return _folkratt_section(title, groups)


def _icrc_listing(con):
    """The ICRC half of the folkrätt page: the central Geneva-law instruments
    first, then the rest carved into a subject index by the ICRC topic taxonomy,
    each group chronological. '' when the corpus has none."""
    rows = _treaty_rows(con, "icrc")
    if not rows:
        return ""
    named = _treaty_named(datasets.ICRC_NAMES)
    root = catalog.data_root(con)
    entry = lambda row: _icrc_entry(row, named)
    central = sorted((r for r in rows if r["number"] in named),
                     key=lambda r: int(re.sub(r"\D", "", r["number"]) or 0))
    by_topic = {}
    for row in rows:
        if row["number"] not in named:
            by_topic.setdefault(_icrc_topic(root, row["path"]), []).append(row)
    known = {topic for topic, _heading in ICRC_TOPIC_GROUPS}
    orphans = [row for topic, group in by_topic.items() if topic not in known
               for row in group]
    groups = []
    if central:
        groups.append(_folkratt_group("Genèvekonventionerna och tilläggsprotokollen",
                                      central, entry))
    for topic, heading in ICRC_TOPIC_GROUPS:
        members = by_topic.get(topic, [])
        if topic == ICRC_TOPIC_GROUPS[-1][0]:        # the catch-all absorbs orphans
            members = members + orphans
        if members:
            members = sorted(members, key=lambda r: (r["date"] or "", r["title"].lower()))
            groups.append(_folkratt_group(heading, members, entry))
    return _folkratt_section("Internationell humanitär rätt (ICRC)", groups)


@functools.lru_cache(maxsize=1)
def _untc_curated():
    """The curated UN Treaty Collection list as {mtdsg_no: entry} -- the Swedish
    name/acronym and subject group the catalog does not carry. Cached: the
    folkrätt page rebuilds often (as with `_treaty_named`)."""
    return {t["mtdsg_no"]: t
            for t in json.loads(datasets.UNTC_TREATIES.read_text("utf-8"))["treaties"]}


# the subject groups the UN instruments file under, in display order (any group
# not listed here trails, so a new curated group needs no code change)
UNTC_GROUP_ORDER = ("Traktaträtt och havsrätt", "Mänskliga rättigheter",
                    "Flyktingrätt")


def _untc_entry(row, curated):
    entry = curated.get(row["number"]) or {}
    named = {row["number"]: {"label": entry.get("sv"), "abbr": entry.get("abbr")}}
    return LISTS.treaty_li(
        href(row["uri"]), row["title"],
        _treaty_parenthetical(row, named, "MTDSG %s" % row["number"]))


def _untc_listing(con):
    """The UN half of the folkrätt page: the curated instruments grouped by
    subject (law of treaties/sea, human rights, refugees), each chronological."""
    curated = _untc_curated()
    return _grouped_listing(
        "Förenta nationerna (FN)", _treaty_rows(con, "untc"),
        lambda row: (curated.get(row["number"]) or {}).get("group") or "Övriga",
        {group: group for group in UNTC_GROUP_ORDER},
        lambda row: _untc_entry(row, curated))


@functools.lru_cache(maxsize=1)
def _icc_types():
    """The curated ICC decision kinds -> Swedish heading, in display order (the
    catalog does not carry the label). Cached (the folkrätt page rebuilds often)."""
    return {t["kind"]: t["label"]
            for t in json.loads(datasets.ICC_DECISION_TYPES.read_text("utf-8"))["types"]}


def _icc_entry(row):
    return LISTS.treaty_li(
        href(row["uri"]), row["title"] or row["identifier"],
        ", ".join(part for part in (row["identifier"], row["date"]) if part))


def _icc_listing(con):
    """The ICC half of the folkrätt page: the substantive decisions grouped by
    Rome-Statute decision type, each group newest first."""
    return _grouped_listing(
        "Internationella brottmålsdomstolen (ICC)", _treaty_rows(con, "icc"),
        lambda row: row["kind"], _icc_types(), _icc_entry, reverse=True)


def _hudoc_section(con):
    """The European Court of Human Rights half of the landing: the most-cited reel
    and a link into the case browse. Doc-type navigation lives in the shared
    top-level selector, so this no longer repeats the facet links. '' when empty."""
    if not catalog.document_count(con, "hudoc"):
        return ""
    return LISTS.hudoc_section(_most_cited(con, "hudoc"))


# A cross-source selector is a list of ``(axis heading, entries)`` groups, each
# entry ``(id, label, url, count)``. It rides full-width above a browse listing
# (and above the folkrätt landing), so a reader switches between the families a
# masthead entry covers from anywhere -- and because it already carries the
# source's own primary axis, the browse rail beside it does not repeat it.


# the shared "Dokumenttyp" selector carried by every folkrätt aggregate page (the
# landing and the hudoc browse leaves). Entries: the Council-of-Europe treaties as
# one "Fördrag" bucket (protocols nest under their convention, not as a sibling
# type) plus each HUDOC case type (currently only "Domar"). Data-driven, so a new
# case type or the later UN/ICJ sources extend it without a code change.
def folkratt_axis(con):
    n = catalog.counts(con)
    entries = []
    if n.get("coe"):
        entries.append(("coe", "Fördrag", "/folkratt/", n["coe"]))
    if n.get("icrc"):
        entries.append(("icrc", "IHL-fördrag", "/folkratt/", n["icrc"]))
    if n.get("untc"):
        entries.append(("untc", "FN-fördrag", "/folkratt/", n["untc"]))
    if n.get("icc"):
        entries.append(("icc", "ICC-avgöranden", "/folkratt/", n["icc"]))
    if n.get("hudoc"):
        for b in facets.tree(con, "hudoc")["buckets"]:
            entries.append(("hudoc:" + b["slug"], b["label"],
                            browse_url("hudoc", [b["slug"]]), b["count"]))
    return [("Dokumenttyp", entries)]


def cross_nav(groups, active_id):
    return LISTS.top_axis_nav(groups, active_id)


# the shared selector carried by every EU-rätt browse page, so a reader switches
# between the rättsakter and the guidance written *about* them from anywhere. The
# EDPB's riktlinjer and rekommendationer have no CELEX and are not rättsakter,
# which is why they are a source of their own rather than an eurlex doctype --
# but they belong beside the förordning they interpret, which is what this
# selector is for (the folkrätt landing's selector, second use).
#
# One group per issuing body rather than one flat row of document types: a
# listing of riktlinjer is the EDPB's, not the union legislator's, and a reader
# who cannot see whose document it is has no way to weigh it -- a riktlinje
# binds nobody, a förordning binds everyone. The heading is what says so, and
# the three EDPB series sit under it as the series they are.
#
# eurlex takes its SOURCE_LABEL unchanged. edpb overrides it, and the override
# is the point: SOURCE_LABEL names that source by what most of it is ("EU:s
# dataskyddsriktlinjer", which is what the frontpage row wants), while this
# heading stands over all three of its series -- and the artikel 29-gruppens
# vägledningar are not riktlinjer. What a group heading has to say is who
# issued what is under it.
_EU_AXIS_LABEL = {"eurlex": SOURCE_LABEL["eurlex"],
                  "edpb": "EDPB:s vägledningar"}


def eurlex_axis(con):
    return [(_EU_AXIS_LABEL[source],
             [("%s:%s" % (source, bucket["slug"]), bucket["label"],
               browse_url(source, [bucket["slug"]]), bucket["count"])
              for bucket in facets.tree(con, source)["buckets"]])
            for source in ("eurlex", "edpb")
            if catalog.document_count(con, source)]


def render_folkratt(con):
    body = Markup("").join(
        part for part in (_coe_listing(con), _icrc_listing(con),
                          _untc_listing(con), _icc_listing(con),
                          _hudoc_section(con)) if part)
    if body:
        body = cross_nav(folkratt_axis(con), "coe") + body
    return page("Folkrätt", "Folkrätt", "", body or LISTS.empty(),
                eyebrow="Internationell rätt och mänskliga rättigheter", solo=True)


def browse_url(source, slugs):
    """Absolute URL of a browse bucket page (a directory, trailing slash)."""
    return "/" + "/".join([browse_dir(source), *slugs]) + "/"


# per-worker render state, set once per process by _render_init -- the catalog
# connection and Site can't cross the ProcessPool fork, so each worker builds its
# own once and renders many pages against it (mirrors build.run_action's pattern)
_RENDER: dict = {}


def _render_init(catalog_path, out_root, renderers):
    con = catalog.connect(catalog_path)
    _RENDER.update(con=con, site=Site.from_catalog(con), out_root=Path(out_root),
                   renderers=renderers)


def _write_page(uri, source, path, title, site, out_root, renderers):
    """Render one document to its HTML file, returning True; or False (skipped)
    when its catalog row points at an artifact that has vanished -- a catalog that
    is transiently ahead of the artifact tree (a source re-parsed, dropping a
    document, but not yet re-related). A synthesized concept stub has no artifact
    on disk (empty path) and renders a shell whose content is its aggregated
    inbound; everything else loads its artifact."""
    try:
        art = (json.loads(compress.read_bytes(path)) if path
               else {"uri": uri, "type": source, "title": title})
    except FileNotFoundError:
        return False               # stale catalog row; run `lagen <source> relate`
    out = Path(out_root) / doc_relpath(uri)
    out.parent.mkdir(parents=True, exist_ok=True)
    compress.write_text(out, render_document(art, source, site, renderers),
                        encodings=compress.PAGE_ENCODINGS)
    return True


def _render_one(job):
    """ProcessPool entry point: render `job` (uri, source, path, title) against
    this worker's prebuilt Site, returning (uri, written)."""
    written = _write_page(*job, _RENDER["site"], _RENDER["out_root"],  # ty: ignore[too-many-positional-arguments]  # job is a 4-tuple; ty cannot see arity through *
                          _RENDER["renderers"])
    return job[0], written


def generate_site(catalog_path, out_root, renderers, progress=None, fresh=None,
                  record=None, only=None, source=None, jobs=1, extra=None,
                  write_index=True):
    """Render every catalogued document to static HTML. `renderers` maps a source
    key to that source's own `render(art, site) -> str` (`build.SOURCE_RENDERERS`);
    lib cannot import a source, so the registry is composed by the caller. Its
    values must be module-level functions -- they are pickled by qualified name
    into each pool worker. `fresh(uri, out_path,
    art_path, dep_digest) -> bool` lets the caller skip a page whose inputs are
    unchanged (incremental generate); `record(uri, art_path, dep_digest)` is
    called after a page is (re)rendered so the caller can store its new
    signature. `art_path` is the page's own artifact (content-hashed by the
    caller); `dep_digest` captures its citation relationships (set-based) plus,
    where present, the content of cross-document layers rendered onto the page
    (site_cross_digests) and its current repeal status.
    `only`, a set of artifact path strings, restricts the run to those documents
    (a targeted `lagen <source> generate <id>`) -- the corpus-wide aggregate
    pages are then left untouched. `extra` appends pre-scoped (uri, source,
    path, title) page rows that have no catalog row (the sfs historical
    consolidations). `jobs>1` renders the stale pages across a process pool.
    Returns (total_pages, rendered) -- rendered < total when pages were
    skipped."""
    out_root = Path(out_root)
    con = catalog.connect(catalog_path)
    rows = con.execute(
        "SELECT uri, source, path, title, content_hash FROM documents "
        "ORDER BY source, uri").fetchall()
    # stored paths are data_root-relative (portable catalog); resolve to absolute
    # here so `only`, the fresh/record callbacks and _write_page all work in
    # absolute paths, exactly as before. A stub's empty path stays empty.
    root = catalog.data_root(con)
    rows = [(uri, src, str(root / path) if path else path, title, chash)
            for (uri, src, path, title, chash) in rows]
    # the two scopes COMPOSE: `source` narrows to one source (incl. stubs),
    # `only` to specific artifacts. The editor's post-commit rebuild passes both
    # (one host page within a source); treating `source` as overriding `only`
    # made every editor checkout scan the whole source instead of rendering the
    # one dirty page.
    if source is not None:                       # whole-source scope (incl. stubs)
        rows = [r for r in rows if r[1] == source]
    if only is not None:                         # specific-document scope
        rows = [r for r in rows if r[2] in only]
    # commentary is an annotation rendered into statute rails, not a page of its own
    rows = [r for r in rows if r[1] != "kommentar"]
    # uncatalogued pages (sfs historical consolidations) carry no catalog row, so
    # no stored content_hash -- the caller re-hashes their artifact from disk (few)
    rows += [(uri, src, path, title, None) for (uri, src, path, title)
             in (extra or ())]

    # the Site is built after scoping so an `only`-run's build is targeted too:
    # its cross-content indexes are queried for just the plan's uris (Site.
    # from_catalog) -- per-host data, so the scoped build renders and signs those
    # pages identically to the full one
    site = Site.from_catalog(
        con, target_uris=[r[0] for r in rows] if only is not None else None)
    # the whole-corpus dependency digests in one batched pass (not one pair of
    # subqueries per document -- the 124k-page planning loop). An `only`-scoped
    # run (a handful of pages: the targeted CLI render, the editor's post-commit
    # rebuild) instead looks up just its own uris -- the batched pass streams the
    # whole 10M-row links table (~30 s), which dwarfs rendering one page. A
    # link-less uri is absent either way and takes the empty default.
    deps = (catalog.page_dependency_digests_for(con, [r[0] for r in rows])
            if only is not None else catalog.page_dependency_digests(con))
    # cross-document content (kommentar prose/.ann, remiss .ann, .corr rows)
    # renders onto OTHER documents' pages -- fold a per-host content digest into
    # the dependency digest so editing it re-renders the host page
    cross = site_cross_digests(site)
    # a repeal is presented against today's date (render_sfs marks the page
    # upphävd, facets drop it from browse), so a page's freshness must carry its
    # current in-force status: the day the date passes, the fold flips and the
    # page re-renders (rule:respect-source-temporality)
    expired = catalog.expired_uris(con, date.today().isoformat())

    # Freshness planning is single-threaded: it reads the catalog + manifest and
    # hashes inputs (the manifest lives here in the parent). Fresh pages advance
    # the counter at once; stale ones go to `plan` to be rendered (in parallel).
    total = len(rows)
    done = rendered = 0
    plan = []                # (uri, source, path, title, dep, chash) needing render
    # doc_relpath is not injective (begrepp/Fartyg_från_en_icke-avtalsslutande_part
    # and begrepp/Fartyg_från_en_icke_avtalsslutande_part slug to one file -- the
    # hyphen and the underscore both become '_'), so two catalogued uris landing
    # on one path would clobber each other's page and race on the deterministic
    # .tmp name under jobs>1. The first uri wins the path and the rest are
    # dropped from the plan, which removes both hazards.
    #
    # A duplicate concept on the wiki is a data-quality problem in one page; it
    # must not cost a 300k-page generate, so this warns and carries on rather
    # than raising (the deliberate exception to rule:fail-fast here -- the run
    # can continue correctly, and the warning names what to fold).
    outs: dict = {}          # output relpath -> uri
    collisions = []          # (winner uri, dropped uri, shared relpath)
    for (uri, src, path, title, chash) in rows:
        rel = doc_relpath(uri)
        if outs.setdefault(rel, uri) != uri:
            collisions.append((outs[rel], uri, rel))
            done += 1        # counted as handled, so the progress total still adds up
            continue
        out = out_root / rel
        dep = deps.get(uri, catalog.EMPTY_DEP_DIGEST)
        if uri in cross or uri in expired:
            dep = hashlib.sha256(
                ("%s\x1f%s\x1f%s" % (dep, cross.get(uri, ""),
                                     "expired" if uri in expired else "")
                 ).encode()).hexdigest()
        if fresh and fresh(uri, out, path, dep, chash):
            done += 1
            if progress and done % 500 == 0:
                progress(done, total, catalog.local(uri), rendered)
        else:
            plan.append((uri, src, path, title, dep, chash))

    skipped = []                 # uris whose artifact vanished (stale catalog rows)

    def finish(uri, path, dep, chash, written):
        nonlocal done, rendered
        done += 1
        if not written:
            skipped.append(uri)
            return               # a vanished artifact records no fresh signature
        rendered += 1
        if record:
            record(uri, path, dep, chash)
        if progress:
            progress(done, total, catalog.local(uri), rendered)

    if jobs > 1 and len(plan) > 1:
        with ProcessPoolExecutor(max_workers=jobs, initializer=_render_init,
                                 initargs=(catalog_path, out_root, renderers)) as pool:
            futures = {pool.submit(_render_one, job[:4]): job for job in plan}
            for fut in as_completed(futures):
                _uri, written = fut.result()     # propagate a render error (abort)
                uri, src, path, title, dep, chash = futures[fut]
                finish(uri, path, dep, chash, written)
    else:
        for (uri, src, path, title, dep, chash) in plan:
            written = _write_page(uri, src, path, title, site, out_root,
                                  renderers)
            finish(uri, path, dep, chash, written)

    if skipped:
        sys.stderr.write(
            "\nwarning: skipped %d page(s) whose artifact has vanished -- the "
            "catalog is ahead of the artifact tree; run the source's `relate` to "
            "prune (e.g. %s)\n" % (len(skipped), ", ".join(
                sorted(catalog.local(u) for u in skipped[:5]))))

    if collisions:
        sys.stderr.write(
            "\nwarning: %d output path collision(s) -- these documents share a "
            "rendered filename with another, so only the first was written. Fold "
            "each duplicate (an `aliases:` redirect on the wiki page) and "
            "re-generate:\n" % len(collisions))
        for winner, dropped, rel in collisions[:20]:
            sys.stderr.write("  %s\n    dropped in favour of %s (both -> %s)\n"
                             % (catalog.local(dropped), catalog.local(winner), rel))
        if len(collisions) > 20:
            sys.stderr.write("  ... and %d more\n" % (len(collisions) - 20))

    if only is None and source is None:          # corpus-wide pages on a full run
        render_aggregates(con, out_root, write_index=write_index)
    if progress:
        progress(total, total, "", rendered)
    con.close()
    return total, rendered


# The browser chrome from lib/assets/, in the order the page loads them:
# matomo.js first (it depends on nothing, and the bundle is one script -- an
# uncaught error in any module stops the rest, so the analytics ping must not sit
# downstream of the reading chrome); then dom.js, which defines window.lagenDom
# (the shared vocabulary the others build on) and MUST precede them; the rest are
# order-independent IIFEs, editor.js last. They are concatenated into one
# script.js so the page links a single URL -- adding a module changes only
# script.js, never the per-page HTML, so a new script ships as an --assets-only
# refresh instead of forcing a full corpus regenerate.
SCRIPT_FILES = ("matomo.js", "dom.js", "scrollspy.js", "search.js", "popover.js",
                "fullsearch.js", "versions.js", "faksimil.js", "drawers.js",
                "editor.js")
SCRIPT_BUNDLE = "script.js"     # the single served URL (render.PAGE links it)


def _bundled_script():
    """The concatenated script.js: every lib/assets JS file in load order, each
    behind a banner comment so a stack trace or view-source still names its origin
    file. The files are self-contained IIFEs, so concatenation is order-preserving
    and semantically identical to the former one-tag-per-file loading."""
    return "\n".join("/* === %s === */\n%s" % (name,
                     (ASSETS / name).read_text(encoding="utf-8"))
                     for name in SCRIPT_FILES)


def write_assets(out_root):
    """Copy the static browser chrome (lib/assets/) into the generated tree -- the
    concatenated script.js bundle, robots.txt, and the stylesheet (reader CSS with
    the editor layer appended). Depends on nothing but the asset files, so it is
    the whole of an asset-only refresh (`lagen all generate --assets-only`) after
    a CSS/JS change -- no catalog, no relate, no HTML re-render. Rides the same
    precompression as the pages (nginx serves the .br/.gz as-is); tiny files stay
    plain via the size floor in compress.write."""
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    compress.write_text(out_root / SCRIPT_BUNDLE, _bundled_script(),
                        encodings=compress.PAGE_ENCODINGS)
    compress.write_text(out_root / "robots.txt",
                        (ASSETS / "robots.txt").read_text(encoding="utf-8"),
                        encodings=compress.PAGE_ENCODINGS)
    # style.css ships the self-hosted @font-face set first (assets/fonts/,
    # replacing the fonts.googleapis.com stylesheet -- no visitor request
    # leaves the site for a font), then the reader stylesheet, then the editor
    # layer -- one request, and the editor rules are inert without a logged-in
    # session.
    compress.write_text(out_root / "style.css",
                        (ASSETS / "fonts" / "fonts.css").read_text(encoding="utf-8")
                        + (ASSETS / "style.css").read_text(encoding="utf-8")
                        + (ASSETS / "editor.css").read_text(encoding="utf-8"),
                        encodings=compress.PAGE_ENCODINGS)
    # the font binaries themselves: woff2 is already compressed, so they are
    # stored plain (no .br sibling), under the /fonts/ urls fonts.css names
    fonts_dir = out_root / "fonts"
    fonts_dir.mkdir(parents=True, exist_ok=True)
    for font in sorted((ASSETS / "fonts").glob("*.woff2")):
        compress.write_bytes(fonts_dir / font.name, font.read_bytes(),
                             encodings=())


def render_aggregates(con, out_root, write_index=True):
    """Write the corpus-wide pages -- stylesheet, scripts, frontpage, the
    folkrätt/myndigheter landings and the feeds -- from the catalog. They depend
    on the whole document set (not on any single artifact), so they are cheap and
    always rebuilt; `lagen all generate --aggregates-only` runs just this,
    skipping the per-document render.

    The faceted browse tree is *not* written here: it is generated as a client
    of the REST API and so lives outside lib/ (`accommodanda.browse`).
    `build.cmd_generate` calls `browse.generate_all` alongside the full-corpus
    generate -- it is the one place that composes the two.
    `write_index=False` skips the generic corpus-stats frontpage -- the caller
    (build.cmd_generate) then writes a curated editorial frontpage in its place,
    so this never write-then-clobbers `index.html`."""
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    write_assets(out_root)
    if write_index:
        compress.write_text(out_root / "index.html", _render_index(con),
                            encodings=compress.PAGE_ENCODINGS)
    folkratt_dir = out_root / "folkratt"
    folkratt_dir.mkdir(parents=True, exist_ok=True)
    compress.write_text(folkratt_dir / "index.html", render_folkratt(con),
                        encodings=compress.PAGE_ENCODINGS)
    myndigheter_dir = out_root / "myndigheter"
    myndigheter_dir.mkdir(parents=True, exist_ok=True)
    compress.write_text(myndigheter_dir / "index.html", _render_myndigheter(con),
                        encodings=compress.PAGE_ENCODINGS)
    search_dir = out_root / "sok"
    search_dir.mkdir(parents=True, exist_ok=True)
    compress.write_text(search_dir / "index.html", _render_search_page(),
                        encodings=compress.PAGE_ENCODINGS)
    admin_dir = out_root / "admin"
    admin_dir.mkdir(parents=True, exist_ok=True)
    compress.write_text(admin_dir / "index.html", _render_admin_page(),
                        encodings=compress.PAGE_ENCODINGS)
    # The legacy feed directory and per-repository feeds. Query-parameter
    # variants are rendered live by api/app.py; these unfiltered copies keep the
    # generated tree independently publishable at the same stable URLs.
    feed_index = out_root / "dataset" / "sitenews"
    feed_index.mkdir(parents=True, exist_ok=True)
    compress.write_text(feed_index / "index.html", _render_feed_index(con),
                        encodings=compress.PAGE_ENCODINGS)
    for item in feeds.DATASETS:
        entries = feeds.entries(con, item)
        target = out_root / "dataset" / item.alias
        (target / "feed").mkdir(parents=True, exist_ok=True)
        compress.write_text(target / "feed.atom", feeds.render_atom(item, entries),
                            encodings=compress.PAGE_ENCODINGS)
        compress.write_text(target / "feed" / "index.html",
                            _render_feed_page(item, entries),
                            encodings=compress.PAGE_ENCODINGS)
