"""Atom feeds over the catalog at lagen.nu's stable feed URLs.

Each public repository uses ``/dataset/<alias>/feed`` and ``feed.atom``.
Faceted feeds use query parameters rather than new paths. The module owns the
compatibility map between public aliases and internal source names. Every
browsable source has a feed. Sources without a compatibility alias use their
own name.

Every feed -- the editorial news feed included -- is one screen with the same
chrome: the entries in the reading column and the source selector in the left
rail (`nav`), so a reader arrives at one feed and can reach every other.

That screen is assembled here rather than in `lib/render.py`, the other half of
site assembly, because `api/app.py` renders it live for a filtered request:
importing the render driver into the serving path would drag the process pool
and the asset shipper in with it. The dependency cannot run the other way --
`render.py` imports this module.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html import escape
from urllib.parse import urlencode

from markupsafe import Markup

from . import catalog, facets, labels, layout, util
from .page import page
from .tpl import ENV

LISTS = ENV.get_template("listings.html").module

BASE = catalog.BASE.rstrip("/")
LIMIT = 200                    # legacy main feeds held up to 2 * archivesize(100)


@dataclass(frozen=True)
class Dataset:
    alias: str
    source: str
    title: str


DATASETS = (
    Dataset("sfs", "sfs", "Alla författningar"),
    Dataset("dv", "dv", "Samtliga rättsfall"),
    Dataset("forarbeten", "forarbete", "Samtliga förarbeten"),
    Dataset("myndfs", "foreskrift", "Samtliga föreskrifter"),
    Dataset("myndprax", "avg", "Samtliga dokument"),
    Dataset("myndrs", "rs", "Samtliga rättsliga ställningstaganden"),
    Dataset("eurlex", "eurlex", "Samtliga EU-rättsakter"),
    Dataset("euvagledning", "guidance",
            "Samtliga vägledningar från EU-organ"),
    # The folkrätt sources have no compatibility alias, so each uses its own
    # source name.
    Dataset("hudoc", "hudoc", "Samtliga avgöranden från Europadomstolen"),
    Dataset("coe", "coe", "Samtliga fördrag från Europarådet"),
    Dataset("icrc", "icrc", "Samtliga humanitärrättsliga fördrag"),
    Dataset("untc", "untc", "Samtliga FN-fördrag"),
    Dataset("icc", "icc",
            "Samtliga avgöranden från Internationella brottmålsdomstolen"),
    Dataset("icj", "icj",
            "Samtliga avgöranden från Internationella domstolen"),
    Dataset("keyword", "begrepp", "Alla nya och ändrade begrepp"),
)
# The editorial news feed. It is not a catalog source (site/render.py writes its
# page and its Atom document from the authored artifact), but it is a feed on
# the same URLs, so the selector lists it first. Its URL keeps the trailing
# slash `lib/tpl.py`'s masthead entry uses: this one page is a written directory
# index, where every other feed is an api/app.py route. Three more copies of the
# literal live in tpl.py, site/render.py and app.py's routes -- consolidating
# them means giving the URL grammar a home in layout.py, which is a wider change
# than this module.
SITENEWS_ALIAS = "sitenews"
SITENEWS_URL = "/dataset/sitenews/feed/"
SITENEWS_LABEL = "Nyheter"
# the directory of every feed, the filtered ones included. It is the rail's
# last entry, and one of the screens the rail renders on, so it needs a name of
# its own to be marked as current on itself.
INDEX_ALIAS = "index"
INDEX_URL = "/dataset/sitenews/"
BY_ALIAS = {dataset.alias: dataset for dataset in DATASETS}
BY_SOURCE = {dataset.source: dataset for dataset in DATASETS}


@dataclass(frozen=True)
class _Entry:
    uri: str
    url: str
    title: str
    published: str | None      # None when the document carries no date at all
    updated: str
    summary: str


def dataset(alias):
    return BY_ALIAS.get(alias)


def alias_for_source(source):
    item = BY_SOURCE.get(source)
    return item.alias if item else None


def feed_url(alias, atom=False, params=None):
    url = "%s/dataset/%s/feed%s" % (BASE, alias, ".atom" if atom else "")
    if params:
        url += "?" + urlencode(params)
    return url


def _slug(value):
    return util.text_slug(value, sep="_")


def _rfc3339(value):
    """An ISO date/datetime or artifact mtime -> an Atom timestamp."""
    if not value:
        return None
    value = str(value).strip().replace(" ", "T")
    if len(value) == 10:
        value += "T00:00:00"
    if value.endswith("+00:00"):
        value = value[:-6] + "Z"
    elif not value.endswith("Z") and not re.search(r"[+-]\d\d:\d\d$", value):
        value += "Z"
    return value


def _mtime(ns):
    if not ns:
        return None
    return datetime.fromtimestamp(ns / 1_000_000_000, timezone.utc) \
        .isoformat().replace("+00:00", "Z")


def _sfs_type(title, local):
    if labels.sfs_is_statute(title or "", local):
        return "lag"
    if re.match(r"^förordning(?:en)?\b", title or "", re.I):
        return "forordning"
    return "ovrigt"


def _matches(item, row, rdf_type=None,
             rpubl_rattsfallspublikation=None, dcterms_publisher=None):
    uri, _source, kind, _label, title, _path, _display, _date, _mtime_ns, publisher = row
    local = catalog.local(uri)
    if rdf_type:
        wanted = rdf_type.rsplit("/", 1)[-1]
        actual = _sfs_type(title, local) if item.source == "sfs" else kind
        if actual != wanted:
            return False
    if rpubl_rattsfallspublikation:
        facet_row = facets.Row(uri, local, kind, row[3], title, row[6])
        if item.source != "dv" or facets.SCHEMES["dv"][0].key(facet_row) \
                != rpubl_rattsfallspublikation:
            return False
    if dcterms_publisher:
        wanted = dcterms_publisher.rsplit("/", 1)[-1]
        # avg and rs are catalogued with the agency's short code as their kind
        # (jo, kkv, fk, migr…), which is the publisher a feed filters on; every
        # other source has to slug its publisher's spelled-out name
        actual = (kind if item.source in ("avg", "rs")
                  else _slug(publisher or ""))
        if actual != wanted:
            return False
    return True


def entries(con, item, rdf_type=None, rpubl_rattsfallspublikation=None,
            dcterms_publisher=None, limit=LIMIT):
    """Newest entries for a dataset and its legacy facet parameters.

    Two different orders, on purpose. Which documents a feed *holds* is decided
    by artifact mtime -- it is a feed of new and updated documents, and a
    document we re-parsed is one we updated. How they are *presented* is by the
    document's own date, newest first: a reader scanning the page reads the
    dates, and mtime order printed them 2000, 2005, 2002, 1998 down the page.

    A document that carries no date at all -- every begrepp, 29% of the
    förarbeten, 12% of the föreskrifter -- sorts last and prints no date. It is
    listed, not dated.

    A document whose declared expiry has passed is omitted, the same rule the
    browse listings and search apply: a repealed act and a withdrawn rättsligt
    ställningstagande no longer state law, and a feed of a corpus is a listing
    of it. Selecting by artifact mtime is what made that urgent -- a re-parse
    bumps every document it touches into the feed, so re-parsing a corpus with
    699 withdrawn positions in it would have carried all 699 in."""
    root = catalog.data_root(con)
    expired = catalog.expired_uris(con, date.today().isoformat())
    rows = con.execute(
        "SELECT uri, source, kind, label, title, path, display, date, art_mtime_ns, "
        "publisher "
        "FROM documents WHERE source = ? AND path <> '' "
        "ORDER BY art_mtime_ns DESC, COALESCE(date, '') DESC, uri DESC",
        (item.source,))
    out = []
    for row in rows:
        if row[0] in expired:
            continue
        if not _matches(item, row, rdf_type, rpubl_rattsfallspublikation,
                        dcterms_publisher):
            continue
        # Only the at-most `limit` selected rows need their artifact summary/date;
        # filtering itself is catalog-only, including publisher filters.
        art = catalog.load_artifact(root, row[5])
        updated = _mtime(row[8]) or _rfc3339(row[7]) or "1970-01-01T00:00:00Z"
        published = _rfc3339(row[7] or catalog.document_date(art))
        title = row[6] or row[4] or row[3] or catalog.local(row[0])
        summary = (art.get("sammanfattning")
                   or art.get("metadata", {}).get("sammanfattning") or title)
        if not isinstance(summary, str):
            summary = title
        out.append(_Entry(row[0], BASE + layout.page_url(row[0]), title,
                         published, updated, summary))
        if len(out) == limit:
            break
    # A document with no date of its own has no place in a date order, so it
    # sorts behind every dated entry and prints no date at all. Falling back to
    # the artifact mtime instead stamped it with the day we last parsed it: all
    # 200 föreskrifter on the myndfs feed read "2026-08-20", and 39 undated SOUs
    # led the förarbete feed ahead of this year's propositioner (rule:fail-fast
    # -- `catalog.document_date` returns None because the corpus cannot answer,
    # and the reader is owed that answer, not a manufactured one).
    out.sort(key=lambda entry: (entry.published is not None, entry.published or "",
                                entry.updated, entry.uri), reverse=True)
    return out


def render_atom(item, rows, params=None):
    self_url = feed_url(item.alias, atom=True, params=params)
    html_url = feed_url(item.alias, params=params)
    updated = max((row.updated for row in rows), default="1970-01-01T00:00:00Z")
    body = []
    for row in rows:
        # atom:published is optional and atom:updated is not, so an undated
        # document publishes only the instant we last touched it -- there is no
        # honest value for when it was issued
        body.append(
            "<entry><title>%s</title><id>%s</id>"
            '<link rel="alternate" href="%s"/>%s<updated>%s</updated>'
            '<summary type="text">%s</summary></entry>'
            % (escape(row.title), escape(row.uri), escape(row.url),
               "<published>%s</published>" % row.published if row.published else "",
               row.updated, escape(row.summary)))
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<feed xmlns="http://www.w3.org/2005/Atom">'
            "<title>%s</title><id>%s</id><updated>%s</updated>"
            '<author><name>lagen.nu</name><uri>%s</uri></author>'
            '<link rel="self" href="%s"/>'
            '<link rel="alternate" href="%s"/>%s</feed>\n'
            % (escape(item.title), escape(self_url), updated, BASE,
               escape(self_url), escape(html_url), "".join(body)))


def nav(current):
    """The source selector every feed page carries in its left rail: the news
    feed, then one entry per dataset, named as the browse tree names the source
    (`facets.SOURCE_LABELS`). `current` is the alias of the screen being
    rendered -- a dataset alias, `SITENEWS_ALIAS`, or `INDEX_ALIAS`.

    The filtered feeds (a publisher's föreskrifter, a court's rättsfall) are not
    in the rail -- föreskrift alone has 140 publishers -- so it ends with the
    directory (`INDEX_ALIAS`) that lists them all."""
    links = [{"url": SITENEWS_URL, "label": SITENEWS_LABEL,
              "current": current == SITENEWS_ALIAS}]
    links += [{"url": feed_url(item.alias).removeprefix(BASE),
               "label": facets.SOURCE_LABELS[item.source],
               "current": current == item.alias} for item in DATASETS]
    return LISTS.feed_nav(links, INDEX_URL, current == INDEX_ALIAS)


def render_page(item, rows, params=None):
    """The human-readable twin of an Atom document: its entries in the site
    chrome, the selector in the rail. Static generation (`render.render_aggregates`)
    and a live filtered request (`api.app`) render through this one function, so
    the generated page and the query-parameter one cannot differ."""
    atom = feed_url(item.alias, atom=True, params=params)
    body = LISTS.feed_page_body(nav(item.alias), item.title, atom, [
        {"date": row.published[:10] if row.published else "", "url": row.url,
         "title": row.title, "summary": row.summary}
        for row in rows])
    return page(item.title, "Nyheter", "", body, solo=True, body_class=" browse",
                own_h1=True,
                head=Markup('<link rel="alternate" type="application/atom+xml" '
                            'href="%s">') % atom)


def publisher_options(con):
    """Current föreskrift publishers as ``(legacy_slug, label, count)``."""
    rows = con.execute(
        "SELECT publisher, COUNT(*) FROM documents "
        "WHERE source = 'foreskrift' AND publisher IS NOT NULL "
        "GROUP BY publisher ORDER BY publisher")
    return [(_slug(label), label, count) for label, count in rows]
