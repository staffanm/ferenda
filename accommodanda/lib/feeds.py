"""Atom feeds over the rebuilt catalog, at lagen.nu's legacy feed URLs.

The old Ferenda site exposed each repository as ``/dataset/<alias>/feed`` and
``feed.atom``.  Faceted feeds used query parameters rather than new paths.  The
rewrite's source names differ for a few repositories, so this module is the one
compatibility map and the pure feed renderer shared by static generation and the
live query-parameter endpoints.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from urllib.parse import urlencode

from . import catalog, facets, layout, util

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
    Dataset("keyword", "begrepp", "Alla nya och ändrade begrepp"),
    Dataset("eurlex", "eurlex", "Samtliga EU-rättsakter"),
)
BY_ALIAS = {dataset.alias: dataset for dataset in DATASETS}
BY_SOURCE = {dataset.source: dataset for dataset in DATASETS}


@dataclass(frozen=True)
class Entry:
    uri: str
    url: str
    title: str
    published: str
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
    if facets.sfs_is_statute(title or "", local):
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
        actual = kind if item.source == "avg" else _slug(publisher or "")
        if actual != wanted:
            return False
    return True


def entries(con, item, rdf_type=None, rpubl_rattsfallspublikation=None,
            dcterms_publisher=None, limit=LIMIT):
    """Newest entries for a dataset and its legacy facet parameters."""
    root = catalog.data_root(con)
    rows = con.execute(
        "SELECT uri, source, kind, label, title, path, display, date, art_mtime_ns, "
        "publisher "
        "FROM documents WHERE source = ? AND path <> '' "
        "ORDER BY art_mtime_ns DESC, COALESCE(date, '') DESC, uri DESC",
        (item.source,))
    out = []
    for row in rows:
        if not _matches(item, row, rdf_type, rpubl_rattsfallspublikation,
                        dcterms_publisher):
            continue
        # Only the at-most `limit` selected rows need their artifact summary/date;
        # filtering itself is catalog-only, including publisher filters.
        art = catalog.load_artifact(root, row[5])
        updated = _mtime(row[8]) or _rfc3339(row[7]) or "1970-01-01T00:00:00Z"
        published = _rfc3339(row[7] or catalog.document_date(art)) or updated
        title = row[6] or row[4] or row[3] or catalog.local(row[0])
        summary = (art.get("sammanfattning")
                   or art.get("metadata", {}).get("sammanfattning") or title)
        if not isinstance(summary, str):
            summary = title
        out.append(Entry(row[0], BASE + layout.page_url(row[0]), title,
                         published, updated, summary))
        if len(out) == limit:
            break
    out.sort(key=lambda entry: (entry.updated, entry.published, entry.uri), reverse=True)
    return out


def render_atom(item, rows, params=None):
    self_url = feed_url(item.alias, atom=True, params=params)
    html_url = feed_url(item.alias, params=params)
    updated = max((row.updated for row in rows), default="1970-01-01T00:00:00Z")
    body = []
    for row in rows:
        body.append(
            "<entry><title>%s</title><id>%s</id>"
            '<link rel="alternate" href="%s"/>'
            "<published>%s</published><updated>%s</updated>"
            '<summary type="text">%s</summary></entry>'
            % (escape(row.title), escape(row.uri), escape(row.url),
               row.published, row.updated, escape(row.summary)))
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<feed xmlns="http://www.w3.org/2005/Atom">'
            "<title>%s</title><id>%s</id><updated>%s</updated>"
            '<author><name>lagen.nu</name><uri>%s</uri></author>'
            '<link rel="self" href="%s"/>'
            '<link rel="alternate" href="%s"/>%s</feed>\n'
            % (escape(item.title), escape(self_url), updated, BASE,
               escape(self_url), escape(html_url), "".join(body)))


def render_html(item, rows, params=None):
    """Dependency-free HTML twin used for live filtered feed requests."""
    atom = feed_url(item.alias, atom=True, params=params)
    articles = "".join(
        '<article><p><time datetime="%s">%s</time></p>'
        '<h2><a href="%s">%s</a></h2><p>%s</p></article>'
        % (entry.published, entry.published[:10], escape(entry.url),
           escape(entry.title), escape(entry.summary))
        for entry in rows)
    return ('<!doctype html><html lang="sv"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>%s</title><link rel="alternate" type="application/atom+xml" '
            'href="%s"></head><body><main><h1>%s</h1>'
            '<p><a href="%s">Atom-flöde</a></p>%s</main></body></html>'
            % (escape(item.title), escape(atom), escape(item.title), escape(atom),
               articles or "<p>Inga dokument.</p>"))


def publisher_options(con):
    """Current föreskrift publishers as ``(legacy_slug, label, count)``."""
    rows = con.execute(
        "SELECT publisher, COUNT(*) FROM documents "
        "WHERE source = 'foreskrift' AND publisher IS NOT NULL "
        "GROUP BY publisher ORDER BY publisher")
    return [(_slug(label), label, count) for label, count in rows]
