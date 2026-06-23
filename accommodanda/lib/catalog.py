"""The derived catalog: a SQLite index over every parsed artifact plus the
cross-source citation graph it implies.

This is the reborn `relate` phase (REWRITE.md §6 / layer 3). It depends only
on the published artifacts -- never on source internals -- and is fully
rebuildable from them, so it is derived data, not a source of truth. Its
reason to exist is the inbound-link graph: which cases and which other laws
cite a given statute paragraph. That graph, annotated back onto the paragraph
at generate time, is lagen.nu's signature feature.

Every artifact carries its discovered citations *inline* (a text node is a
list of plain runs interleaved with {"predicate","uri","text"} link dicts),
uniformly across SFS and DV, and both verticals mint the same
`https://lagen.nu/<id>#<fragment>` URIs -- so a single generic walk extracts
the edges from either source.
"""

import hashlib
import json
import re
import sqlite3
from pathlib import Path

BASE = "https://lagen.nu/"


def norm_title(t):
    """A law title normalised for matching a proposed-law name against the SFS
    title index: SFS number dropped, whitespace collapsed, lower-cased -- so
    'Lag (2015:671) om alternativ tvistlösning …' and the proposition's 'lag om
    alternativ tvistlösning …' compare equal."""
    return re.sub(r"\s+", " ", re.sub(r"\(\d{4}:\d+\)", "", t)).strip().lower()

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    uri    TEXT PRIMARY KEY,
    source TEXT NOT NULL,        -- 'sfs' | 'dv'
    kind   TEXT,                 -- 'law' | 'case'
    label  TEXT,                 -- short display id (SFS number / referat)
    title  TEXT,                 -- full heading
    path   TEXT NOT NULL         -- artifact json on disk
);
CREATE TABLE IF NOT EXISTS links (
    from_uri    TEXT NOT NULL,   -- document making the citation (doc-level uri)
    from_anchor TEXT,            -- nearest enclosing node id in the citing doc
    predicate   TEXT NOT NULL,
    to_uri      TEXT NOT NULL,   -- full target incl. #fragment
    to_root     TEXT NOT NULL,   -- target document uri, fragment stripped
    text        TEXT             -- citation surface text
);
CREATE TABLE IF NOT EXISTS fragments (
    uri     TEXT PRIMARY KEY,       -- a node's fragment uri (doc#id)
    snippet TEXT                    -- its text + list items, for link tooltips
);
CREATE TABLE IF NOT EXISTS genomforande (
    sfs_uri    TEXT NOT NULL,       -- the statute paragraf transposing the article
    sfs_anchor TEXT NOT NULL,       -- its fragment id (P3 / K2P1)
    directive  TEXT NOT NULL,       -- the EU directive uri (ext/celex/...)
    article    TEXT NOT NULL,       -- the directive article number
    prop_uri   TEXT NOT NULL,       -- the proposition stating the relation
    prop_label TEXT,                -- its identifier, for display
    pinpoint   TEXT,                -- the article pinpoint (e.g. "21.1")
    partial    INTEGER NOT NULL     -- "genomför delvis"
);
CREATE INDEX IF NOT EXISTS idx_genomf_sfs ON genomforande(sfs_uri, sfs_anchor);
CREATE INDEX IF NOT EXISTS idx_links_to_uri  ON links(to_uri);
CREATE INDEX IF NOT EXISTS idx_links_to_root ON links(to_root);
CREATE INDEX IF NOT EXISTS idx_links_from    ON links(from_uri);
CREATE INDEX IF NOT EXISTS idx_docs_source   ON documents(source);
"""


def connect(path):
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def local(uri):
    return uri[len(BASE):] if uri.startswith(BASE) else uri


def strip_fragment(uri):
    return uri.split("#", 1)[0]


# --------------------------------------------------------------------------
# edge extraction -- one generic walk over any artifact node tree
# --------------------------------------------------------------------------

def collect_links(node, anchor, out):
    """Walk an artifact node tree, appending (anchor, run) for every inline
    link, attributed to the nearest enclosing node `id`. Handles the two
    leaf carriers of runs: a node's `text` list and a table `rad`'s `cells`
    (a list of cells, each itself a runs list)."""
    if isinstance(node, dict):
        anchor = node.get("id") or anchor
        for key, value in node.items():
            if key == "text" and isinstance(value, list):
                out += [(anchor, run) for run in value
                        if isinstance(run, dict) and "uri" in run]
            elif key == "cells":
                for cell in value:
                    out += [(anchor, run) for run in cell
                            if isinstance(run, dict) and "uri" in run]
            else:
                collect_links(value, anchor, out)
    elif isinstance(node, list):
        for item in node:
            collect_links(item, anchor, out)


def implements_links(art):
    """The genomför-direktiv edges a förarbete artifact carries (extracted from
    its författningskommentar into the `implements` section): each statement ->
    one edge per EU directive article it transposes, anchored to the page the
    statement sits on (the förarbete's `#sid{N}`, so inbound pinpoints the page).
    The stronger *implements* relation, kept as a typed section because the
    parser cannot splice it back into the flat PDF text as an inline link."""
    out = []
    for rec in art.get("implements", []):
        anchor = "sid%d" % rec["page"] if rec.get("page") else None
        for uri in rec.get("uris", []):
            out.append((anchor, {"uri": uri, "predicate": rec["predicate"],
                                 "text": rec.get("sentence")}))
    return out


def artifact_links(art):
    """Every inline citation in an artifact, from the body-bearing sections
    of either source: SFS `structure` + the amendments' `content`, DV `body`,
    plus a förarbete's `implements` (genomför-direktiv) edges."""
    out = []
    collect_links(art.get("structure"), None, out)
    for amendment in art.get("amendments", []):
        collect_links(amendment.get("content"), None, out)
    collect_links(art.get("body"), None, out)
    out += implements_links(art)
    return out


SNIPPET_LEN = 220


def runs_text(runs):
    """Flatten an inline-run list (str runs + link dicts) to plain text."""
    if isinstance(runs, str):
        return runs
    return "".join(r if isinstance(r, str) else r.get("text", "") for r in runs)


def node_snippet(node):
    """A node's text plus its list items, flattened and truncated -- what a
    link to this node shows as a hover tooltip (e.g. a paragraph and its
    numbered points)."""
    parts = [runs_text(node.get("text", []))]
    for child in node.get("children", []):
        marker = child.get("ordinal")
        parts.append((("%s. " % marker) if marker else "") + node_snippet(child))
    text = " ".join(p for p in parts if p).strip()
    return text[:SNIPPET_LEN] + ("…" if len(text) > SNIPPET_LEN else "")


def collect_fragments(node, doc_uri, out):
    """(fragment-uri, snippet) for every id-bearing node in an artifact tree."""
    if isinstance(node, dict):
        if node.get("id"):
            out.append((doc_uri + "#" + node["id"], node_snippet(node)))
        for value in node.values():
            collect_fragments(value, doc_uri, out)
    elif isinstance(node, list):
        for item in node:
            collect_fragments(item, doc_uri, out)


def artifact_fragments(art):
    out = []
    collect_fragments(art.get("structure"), art["uri"], out)
    collect_fragments(art.get("body"), art["uri"], out)
    return out


# --------------------------------------------------------------------------
# document rows
# --------------------------------------------------------------------------

def sfs_document(art, path):
    props = art.get("metadata", {}).get("properties", {})
    return (art["uri"], "sfs", "law", "SFS " + local(art["uri"]),
            props.get("dcterms:title") or ("SFS " + local(art["uri"])),
            str(path))


def dv_document(art, path):
    referat = art.get("referat") or []
    malnr = art.get("malnummer") or []
    label = (referat[0] if referat
             else ("%s %s" % (art.get("court", ""), malnr[0])).strip()
             if malnr else art.get("court") or local(art["uri"]))
    return (art["uri"], "dv", "case", label, label, str(path))


def forarbete_document(art, path):
    label = art.get("identifier") or local(art["uri"])
    return (art["uri"], "forarbete", art.get("type", "forarbete"),
            label, art.get("title") or label, str(path))


def kommentar_document(art, path):
    # title carries the author (shown in the inbound entry); label is generic
    return (art["uri"], "kommentar", "kommentar", "Kommentar",
            art.get("author") or "Kommentar", str(path))


def begrepp_document(art, path):
    title = art.get("title") or local(art["uri"])
    return (art["uri"], "begrepp", "begrepp", title, title, str(path))


def eurlex_document(art, path):
    # kind is the doctype (regulation/directive/judgment/treaty); label is the
    # CELEX (the short id citations use)
    label = art.get("celex") or local(art["uri"])
    return (art["uri"], "eurlex", art.get("doctype", "eurlex"),
            label, art.get("title") or label, str(path))


def document_row(art, path, source):
    return {"sfs": sfs_document, "dv": dv_document,
            "forarbete": forarbete_document, "kommentar": kommentar_document,
            "begrepp": begrepp_document, "eurlex": eurlex_document}[source](
                art, path)


# --------------------------------------------------------------------------
# rebuild
# --------------------------------------------------------------------------

def rebuild(catalog_path, source, artifact_paths, progress=None):
    """Drop and re-index one source's rows in the catalog from its artifacts.
    Single-process and transactional -- a few inserts per doc over ~tens of
    thousands of docs is seconds, and it sidesteps multi-writer SQLite
    contention. Empty artifacts (SkipDocument placeholders) are skipped."""
    con = connect(catalog_path)
    con.execute("DELETE FROM links WHERE from_uri IN "
                "(SELECT uri FROM documents WHERE source = ?)", (source,))
    con.execute("DELETE FROM documents WHERE source = ?", (source,))
    # fragments are keyed by doc#id and refreshed via INSERT OR REPLACE below;
    # any orphaned by a removed doc are harmless (never queried)
    docs = 0
    edges = 0
    total = len(artifact_paths)
    for i, path in enumerate(map(Path, artifact_paths)):
        raw = path.read_bytes()
        if raw.strip():
            art = json.loads(raw)
            con.execute("INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?)",
                        document_row(art, path, source))
            rows = [(art["uri"], anchor,
                     run.get("predicate", "dcterms:references"),
                     run["uri"], strip_fragment(run["uri"]), run.get("text"))
                    for anchor, run in artifact_links(art)]
            con.executemany("INSERT INTO links VALUES (?,?,?,?,?,?)", rows)
            con.executemany("INSERT OR REPLACE INTO fragments VALUES (?,?)",
                            artifact_fragments(art))
            docs += 1
            edges += len(rows)
            current = local(art["uri"])
        else:
            current = path.stem
        if progress:
            progress(i + 1, total, docs, edges, current)
    con.commit()
    con.close()
    return docs, edges


# --------------------------------------------------------------------------
# genomför-direktiv relations (a förarbete pins an EU article to a statute
# paragraf; resolved cross-document at relate time -- see forarbete.genomforande)
# --------------------------------------------------------------------------

def set_genomforande(con, rows):
    """Replace the pinned genomför-direktiv relations. Each row is
    (sfs_uri, sfs_anchor, directive, article, prop_uri, prop_label, pinpoint,
    partial). Stored twice: in `genomforande` (the statute paragraf's margin
    display, with provenance) and as an sfs-paragraf -> directive-article edge in
    `links` (so the directive article's inbound shows the implementing statute,
    reusing the generic inbound machinery)."""
    con.execute("DELETE FROM genomforande")
    con.execute("DELETE FROM links WHERE predicate = 'rpubl:genomforDirektiv' "
                "AND from_uri IN (SELECT uri FROM documents WHERE source='sfs')")
    con.executemany("INSERT INTO genomforande VALUES (?,?,?,?,?,?,?,?)", rows)
    con.executemany("INSERT INTO links VALUES (?,?,?,?,?,?)",
                    [(sfs_uri, anchor, "rpubl:genomforDirektiv",
                      directive + "#" + article, directive, prop_label)
                     for (sfs_uri, anchor, directive, article, prop_uri,
                          prop_label, pin, partial) in rows])
    con.commit()


def genomfor_for(con, sfs_uri, anchor):
    """The EU directive articles a statute paragraf transposes, for its margin:
    (directive, article, prop_uri, prop_label, pinpoint, partial)."""
    return con.execute(
        "SELECT directive, article, prop_uri, prop_label, pinpoint, partial "
        "FROM genomforande WHERE sfs_uri = ? AND sfs_anchor = ? "
        "ORDER BY directive, article", (sfs_uri, anchor)).fetchall()


# --------------------------------------------------------------------------
# queries (used by the renderer)
# --------------------------------------------------------------------------

# Inbound annotations show which *other* documents cite a target. A document's
# references to its own fragments (heading self-links like "12 kap."->#K12, and
# internal "enligt 3 §" cross-refs -- 41% of all edges) are excluded: they are
# the document's own outbound links, navigable in place, not external inbound.
_NOT_SELF = " AND l.from_uri <> l.to_root"


def inbound(con, uri, limit=None):
    """Documents citing exactly `uri`, one row per (citing document, pinpoint)
    as (from_uri, from_anchor, label, title, source) -- so a law citing from
    several places shows each pinpoint, and the renderer can group by source
    and render a human-readable label. Self-citations excluded. `limit` caps
    the rows (for display)."""
    sql = ("SELECT l.from_uri, l.from_anchor, d.label, d.title, d.source "
           "FROM links l JOIN documents d ON d.uri = l.from_uri "
           "WHERE l.to_uri = ?" + _NOT_SELF + " "
           "GROUP BY l.from_uri, l.from_anchor "
           "ORDER BY d.source, d.label, l.from_anchor")
    if limit is not None:
        sql += " LIMIT %d" % limit
    return con.execute(sql, (uri,)).fetchall()


def inbound_count(con, uri):
    """How many (citing document, pinpoint) entries cite exactly `uri`."""
    return con.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM links l WHERE l.to_uri = ?"
        + _NOT_SELF + " GROUP BY l.from_uri, l.from_anchor)", (uri,)).fetchone()[0]


def snippet(con, uri):
    """The stored text snippet for a fragment uri (link-tooltip text), or None."""
    row = con.execute("SELECT snippet FROM fragments WHERE uri = ?",
                      (uri,)).fetchone()
    return row[0] if row else None


def counts(con):
    return dict(con.execute(
        "SELECT source, COUNT(*) FROM documents GROUP BY source").fetchall())


def page_dependency_digest(con, uri):
    """A digest of everything *besides uri's own artifact* that its rendered page
    depends on, for incremental generate. Identity/set-based, not content-based:
    cited and citing documents are effectively immutable (a case or förarbete
    never changes once published), so a page goes stale when the *set* of its
    relationships changes -- a new case starts citing it, an old one drops out,
    or a document it links to appears/disappears -- not when an unchanged
    neighbour's bytes change. Two parts:

      * inbound -- the (citing doc, pinpoint, label) rows it renders in its
        margins and panel: a new or removed citer changes this;
      * outbound -- the set of hosted documents it links to, so a link goes live
        the moment its target is parsed (and dims if the target disappears).

    Self-citations excluded; external targets we don't host drop out of the join."""
    h = hashlib.sha256()
    for row in con.execute(
            "SELECT l.from_uri, l.from_anchor, d.label, d.title, d.source "
            "FROM links l JOIN documents d ON d.uri = l.from_uri "
            "WHERE l.to_root = ?1 AND l.from_uri <> l.to_root "
            "ORDER BY 1, 2", (uri,)):
        h.update(("\x1f".join("" if c is None else c for c in row)).encode())
        h.update(b"\x1e")
    h.update(b"\x00")
    for (target,) in con.execute(
            "SELECT DISTINCT l.to_root FROM links l "
            "JOIN documents d ON d.uri = l.to_root "
            "WHERE l.from_uri = ?1 AND l.to_root <> l.from_uri ORDER BY 1", (uri,)):
        h.update(target.encode())
        h.update(b"\x1e")
    return h.hexdigest()
