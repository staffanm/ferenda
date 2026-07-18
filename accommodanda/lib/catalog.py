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
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import compress, concepts, util
from .markdown import begrepp_uri

BASE = "https://lagen.nu/"


def norm_title(t):
    """A law title normalised for matching a proposed-law name against the SFS
    title index: SFS number dropped, whitespace collapsed, lower-cased -- so
    'Lag (2015:671) om alternativ tvistlösning …' and the proposition's 'lag om
    alternativ tvistlösning …' compare equal."""
    return re.sub(r"\s+", " ", re.sub(r"\(\d{4}:\d+\)", "", t)).strip().lower()

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,   -- 'data_root' => absolute corpus root when the
    value TEXT                -- catalog lives outside it (catalog_root != data_root)
);
CREATE TABLE IF NOT EXISTS documents (
    uri          TEXT PRIMARY KEY,
    source       TEXT NOT NULL,    -- 'sfs' | 'dv'
    kind         TEXT,             -- 'law' | 'case'
    label        TEXT,             -- short display id (SFS number / referat)
    title        TEXT,             -- full heading
    path         TEXT NOT NULL,    -- artifact json on disk
    source_url   TEXT,             -- authoritative publisher url ("Källa"), if any
    content_hash TEXT,             -- sha256 of the artifact bytes (incremental relate)
    expired      TEXT,             -- repeal-effective date (SFS upphavandedatum), if any
    date         TEXT,             -- the document's own date (förarbete/statute/decision), ISO
    publisher    TEXT              -- issuing organization, for feed filtering
);
CREATE TABLE IF NOT EXISTS links (
    from_uri    TEXT NOT NULL,   -- document making the citation (doc-level uri)
    from_anchor TEXT,            -- nearest enclosing node id in the citing doc
    predicate   TEXT NOT NULL,
    to_uri      TEXT NOT NULL,   -- full target incl. #fragment
    to_root     TEXT NOT NULL,   -- target document uri, fragment stripped
    text        TEXT             -- citation surface text
);
CREATE TABLE IF NOT EXISTS concept_alias (
    variant   TEXT PRIMARY KEY,     -- an inflected/variant begrepp uri
    canonical TEXT NOT NULL         -- the concept it folds onto (lib.concepts)
);
CREATE TABLE IF NOT EXISTS concept_redirect (
    variant TEXT PRIMARY KEY,       -- an old begrepp name (a MediaWiki redirect)
    concept TEXT NOT NULL           -- the begrepp it now resolves to (its `aliases`)
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
CREATE TABLE IF NOT EXISTS fk_kommentar (
    sfs_uri    TEXT NOT NULL,       -- the commented statute
    sfs_anchor TEXT NOT NULL,       -- paragraf fragment (P3 / K2P1); '' = the law
    prop_uri   TEXT NOT NULL,       -- the proposition whose FK comments it
    prop_label TEXT,                -- its identifier, for display
    prop_date  TEXT,                -- its date, for newest-first ordering
    page       INTEGER,             -- the FK page in the prop (the #sidN anchor)
    text       TEXT NOT NULL        -- the commentary prose
);
CREATE INDEX IF NOT EXISTS idx_fk_sfs ON fk_kommentar(sfs_uri, sfs_anchor);
CREATE TABLE IF NOT EXISTS correspondence (
    new_uri  TEXT NOT NULL,         -- the new statute paragraf (full uri, doc#id)
    old_uri  TEXT NOT NULL,         -- the old paragraf it corresponds to (a
                                    -- repealed law's, or the same law's pre-
                                    -- renumbering beteckning)
    relation TEXT NOT NULL,         -- 'motsvarar' | 'overfort' | 'betecknas'
    scope    TEXT,                  -- 'helt'|'i_sak'|'i_huvudsak'|'delvis'|NULL
    prop_uri TEXT,                  -- the proposition stating the correspondence
    ikrafttrader TEXT               -- when the renumbering took effect
                                    -- ('betecknas' edges; references older than
                                    -- this mean the old beteckning)
);
CREATE INDEX IF NOT EXISTS idx_corr_new ON correspondence(new_uri);
CREATE INDEX IF NOT EXISTS idx_corr_old ON correspondence(old_uri);
CREATE INDEX IF NOT EXISTS idx_genomf_sfs ON genomforande(sfs_uri, sfs_anchor);
CREATE INDEX IF NOT EXISTS idx_links_to_uri  ON links(to_uri);
CREATE INDEX IF NOT EXISTS idx_links_to_root ON links(to_root);
CREATE INDEX IF NOT EXISTS idx_links_from    ON links(from_uri);
CREATE INDEX IF NOT EXISTS idx_docs_source   ON documents(source);
"""


def connect(path, data_root=None, exclusive=False):
    """A read-write connection to the catalog at `path`, schema ensured.

    `data_root` records the corpus root the stored (data_root-relative) artifact
    paths resolve against, for when the catalog lives outside it (`catalog_root !=
    data_root`). The build passes it on every relate (full or incremental), so the
    recorded root is written on a full rebuild and kept current thereafter; None
    (read-only callers, tests) leaves whatever is recorded untouched. `exclusive`
    opens a throwaway scratch for a full rebuild that will be atomically swapped in:
    it holds the file lock for the connection's whole life instead of re-locking per
    statement (each lock is a synchronous round-trip -- the cost that dominates a
    million-row rebuild, and the difference between local and NFS), and drops the
    rollback journal + fsync entirely, since a crashed rebuild is discarded and
    restarted, never recovered."""
    con = sqlite3.connect(path)
    if exclusive:
        # EXCLUSIVE before any journal pragma so the lock is held from the start
        # (and, on NFS, so WAL's index could live in heap -- moot here, journal is
        # OFF). OFF/OFF: no journal, no fsync -- maximum write throughput for a
        # scratch whose only durable moment is the final rename (cmd_relate fsyncs
        # it then).
        con.execute("PRAGMA locking_mode=EXCLUSIVE")
        con.execute("PRAGMA journal_mode=OFF")
        con.execute("PRAGMA synchronous=OFF")
    else:
        # the catalog is derived and rebuildable, so durability is not precious:
        # WAL (persistent, set once) lets readers proceed during a relate, and
        # NORMAL skips the per-commit fsync that FULL pays on multi-GB rebuilds
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    _record_data_root(con, path, data_root)
    # additive migration for catalogs built before a column existed -- CREATE
    # TABLE IF NOT EXISTS never alters an existing table. The new column is NULL
    # until that source is re-related (which re-reads every artifact anyway).
    cols = {row[1] for row in con.execute("PRAGMA table_info(documents)")}
    if "source_url" not in cols:
        con.execute("ALTER TABLE documents ADD COLUMN source_url TEXT")
    if "content_hash" not in cols:
        con.execute("ALTER TABLE documents ADD COLUMN content_hash TEXT")
    if "expired" not in cols:
        con.execute("ALTER TABLE documents ADD COLUMN expired TEXT")
    if "display" not in cols:
        con.execute("ALTER TABLE documents ADD COLUMN display TEXT")
    # (size, mtime_ns) of the artifact bytes, stored so incremental relate can
    # skip an untouched artifact by stat alone -- never reading + hashing it just
    # to confirm it is unchanged (rebuild). NULL until that source is re-related.
    if "art_size" not in cols:
        con.execute("ALTER TABLE documents ADD COLUMN art_size INTEGER")
    if "art_mtime_ns" not in cols:
        con.execute("ALTER TABLE documents ADD COLUMN art_mtime_ns INTEGER")
    if "date" not in cols:
        con.execute("ALTER TABLE documents ADD COLUMN date TEXT")
    if "publisher" not in cols:
        con.execute("ALTER TABLE documents ADD COLUMN publisher TEXT")
    corr_cols = {row[1] for row in con.execute("PRAGMA table_info(correspondence)")}
    if "ikrafttrader" not in corr_cols:
        con.execute("ALTER TABLE correspondence ADD COLUMN ikrafttrader TEXT")
    con.execute("CREATE INDEX IF NOT EXISTS idx_docs_publisher "
                "ON documents(source, publisher)")
    return con


_ro_lock = threading.Lock()
_ro_migrated = set()


def connect_ro(path):
    """A read-only connection to the catalog at `path`, for the serving layer
    (the REST endpoints and the MCP tools open one per request/tool call --
    SQLite connections are not shared across threads). The first call per
    catalog applies `connect`'s additive migrations (a catalog built by an
    older build may lack a column the queries select), lock-guarded so
    concurrent first requests don't race on the one-time ALTER; after that
    every connection stays read-only."""
    path = str(path)
    if path not in _ro_migrated:
        with _ro_lock:
            if path not in _ro_migrated:
                connect(path).close()
                _ro_migrated.add(path)
    return sqlite3.connect("file:%s?mode=ro" % path, uri=True)


def local(uri):
    return uri[len(BASE):] if uri.startswith(BASE) else uri


def strip_fragment(uri):
    return uri.split("#", 1)[0]


def _catalog_file(con):
    """The filesystem path backing a catalog connection's main database."""
    main = [file for _seq, name, file in con.execute("PRAGMA database_list")
            if name == "main"]
    assert main and main[0], "catalog connection is not backed by a file"
    return Path(main[0])


def _record_data_root(con, path, data_root):
    """Persist (or clear) the corpus root the catalog's stored paths resolve
    against. `None` leaves whatever is recorded untouched (read-only callers and
    tests). When the corpus root *is* the catalog file's own directory (the
    colocated default, catalog_root == data_root) nothing is stored, so `data_root`
    falls back to the file's parent and the catalog stays rsync-portable across
    hosts whose data_root differs (the historical contract). Only a genuinely
    separated layout records an absolute root -- which pins the catalog to *this*
    host's corpus path, so a separated catalog is not rsync-portable to a host whose
    data_root differs until that host runs its own relate (which re-records it)."""
    if data_root is None:
        return
    if Path(data_root).resolve() == Path(path).parent.resolve():
        con.execute("DELETE FROM meta WHERE key = 'data_root'")
    else:
        con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES "
                    "('data_root', ?)", (str(Path(data_root).resolve()),))


def _data_root(con):
    row = con.execute("SELECT value FROM meta WHERE key = 'data_root'").fetchone()
    if row and row[0]:
        return Path(row[0])
    return _catalog_file(con).parent


def data_root(con):
    """The corpus root a catalog's stored (data_root-relative) artifact paths
    resolve against. When the catalog lives outside the corpus (catalog_root !=
    data_root) a full rebuild records the absolute root in `meta`; otherwise this
    falls back to the directory the catalog file itself lives in (the colocated
    default -- which also keeps the catalog rsync-portable, see `_record_data_root`)."""
    return _data_root(con)


def quiesce_wal(path):
    """Fold a catalog's write-ahead log back into its main file and drop the
    `-wal`/`-shm` sidecars, leaving a self-contained single file.

    This is a precondition for renaming a freshly built catalog over a live one
    (`build._swap_catalog`): SQLite pairs a `-wal` with a database by *filename*,
    not content, so a stale `-wal` left beside the swapped-in file is silently
    re-applied by the next reader onto the new base -- serving a corrupt old/new
    mix (`integrity_check` still reports "ok"). The live catalog is in WAL mode
    after any incremental relate, and the serving layer holds read connections that
    keep the sidecars present, so this is the common case, not a corner one.

    A `PASSIVE` checkpoint (never blocks on readers) folds every committed frame
    into the main file, which then stands alone: running it *before* the rename
    leaves the old file complete for in-flight readers (they keep their open fds),
    while new readers, once the rename lands, find no `-wal` to misapply. A reader
    can only pin frames out of the checkpoint by holding a snapshot older than a
    later commit -- which cannot happen here (a full rebuild writes the scratch,
    never this live catalog, so nothing commits to it concurrently), so anything
    short of a full fold means a concurrent writer that must not exist: raise rather
    than strip a `-wal` whose un-folded frames the main file still needs. A no-op
    when `path` doesn't exist yet (first build) or carries no WAL (`log ==
    checkpointed` holds trivially: `0/0`, or `-1/-1` for a non-WAL file)."""
    path = Path(path)
    if not path.exists():
        return
    con = sqlite3.connect(path)
    try:
        _busy, log, checkpointed = con.execute(
            "PRAGMA wal_checkpoint(PASSIVE)").fetchone()
    finally:
        con.close()
    if log != checkpointed:
        raise RuntimeError(
            "catalog WAL only partially checkpointed (%d/%d frames) at %s -- a "
            "concurrent writer to the live catalog during a full rebuild?"
            % (checkpointed, log, path))
    for suffix in ("-wal", "-shm"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)


def artifact_path(root, stored):
    """Resolve a stored (data_root-relative) artifact path to an absolute Path, or
    None for a synthesized stub (empty `path`). `root` is `data_root(con)`. Thin
    domain-named wrapper over the shared `util.load_relpath`."""
    return util.load_relpath(root, stored)


def load_artifact(root, stored):
    """The parsed artifact JSON behind a documents row, `{}` for a synthesized
    stub (empty `path` -- begrepp rows have no artifact file). Reads through
    `compress` so a brotli-precompressed artifact tree serves unchanged."""
    p = artifact_path(root, stored)
    return json.loads(compress.read_bytes(p)) if p else {}


def artifact_updated(root, stored):
    """A documents row's artifact last-build time as an ISO 8601 UTC string,
    None for a synthesized stub or a missing file."""
    p = artifact_path(root, stored)
    return (datetime.fromtimestamp(compress.stat(p).st_mtime,
                                   timezone.utc).isoformat()
            if p and compress.exists(p) else None)


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
    plus a förarbete's `implements` (genomför-direktiv) edges and generic
    top-level `references` for relations expressed by source metadata rather
    than a literal body span (HUDOC's article facet, treaty crosswalks)."""
    out = []
    collect_links(art.get("structure"), None, out)
    for amendment in art.get("amendments", []):
        collect_links(amendment.get("content"), None, out)
    collect_links(art.get("body"), None, out)
    out += implements_links(art)
    # Source metadata can carry legal relations that have no literal span in
    # the body (HUDOC's article facet, a treaty's Swedish implementation).
    # Keep the contract generic: every producer emits ordinary link-run dicts.
    out += [(None, run) for run in art.get("references", [])]
    return out


def subject_links(art):
    """Concept (begrepp) edges from a court decision's `nyckelord`. nyckelord are
    metadata, not body text, so the inline-link walk misses them; each tags the
    case with a concept (`dcterms:subject`), so the concept page lists the cases
    tagged with it -- the case→concept half of the keyword graph."""
    return [(None, {"uri": begrepp_uri(n), "predicate": "dcterms:subject",
                    "text": n})
            for n in art.get("metadata", {}).get("nyckelord", []) if n.strip()]


def bemyndigande_links(art):
    """The bemyndigande edges a föreskrift artifact carries: it is *meddelad* (issued)
    under one or more empowering SFS paragrafer, a fact that lives in metadata, not
    the body text, so the inline-link walk misses it. The edge points föreskrift ->
    SFS paragraf, anchored to the whole regulation (a föreskrift is issued under a
    paragraf as a whole), so the statute paragraf's page lists the föreskrifter
    issued under it. `text` carries the föreskrift's id for the margin display."""
    label = art.get("identifier") or local(art["uri"])
    return [(None, {"uri": uri, "predicate": "rpubl:bemyndigande", "text": label})
            for uri in art.get("metadata", {}).get("bemyndigande", [])]


def curated_links(art):
    """The typed relation edges a court decision's curated metadata carries:
    the editor's Lagrum (`rpubl:lagrum`), Förarbeten (`rpubl:forarbete`),
    related cases (`rpubl:rattsfallshanvisning`) and Litteratur
    (`dcterms:relation`), normalized at parse time into the same inline-run
    shape body text uses ({"text": raw string, "runs": [...]}). These are
    metadata, not body text, so the inline-link walk misses them; much of it is
    editor-derived and never cited verbatim in the prose, so without this edge
    the graph is strictly weaker than the source. Field-driven: any source
    whose metadata stores runs-bearing entries under these keys contributes.
    Unanchored -- a curated relation belongs to the document, not a fragment."""
    md = art.get("metadata", {})
    return [(None, run)
            for key in ("lagrum", "forarbeten", "related", "litteratur")
            for entry in md.get(key) or [] if isinstance(entry, dict)
            for run in entry.get("runs") or []
            if isinstance(run, dict) and "uri" in run]


def definition_links(art):
    """Concept (begrepp) edges from an EU act's defined terms: each
    definitions-article point whose `defines` names a term tags the act with that
    concept (`dcterms:subject`), anchored to the point -- so an EU defined term
    joins the shared begrepp namespace alongside SFS/DV, and the concept page shows
    which EU act defines it. Only the **Swedish** manifestation contributes: the
    begrepp namespace is Swedish, so an English act's terms are not concepts here.
    (The act-local term-use interlinking -- a use links to the act's own definition
    point -- stays untouched; this only adds the cross-corpus concept edge.)"""
    if art.get("lang") != "swe":
        return []
    out = []

    def walk(node):
        if isinstance(node, dict):
            term = node.get("defines")
            if term and term.strip():
                out.append((node.get("id"),
                            {"uri": begrepp_uri(term), "predicate": "dcterms:subject",
                             "text": term}))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(art.get("structure"))
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
    # the canonical, name-prefixed title ("Meteoriten (NJA 2025 s. 897)") the
    # listings and every inbound citation read -- stamped onto the artifact at
    # parse time (build.dv_parse_run, via lib.casenaming.case_label), so the catalog
    # stays a pure consumer. The generic fallback covers an artifact parsed before
    # the field.
    referat = art.get("referat") or []
    malnr = art.get("malnummer") or []
    label = art.get("label") or (
        referat[0] if referat
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
    # CELEX (the short id citations use). A judgment's inbound-citation name is
    # the case citation stamped at parse ("C-311/18 (Schrems II)"), not its
    # "Domstolens dom (...)" Formex title; an act keeps its full title.
    label = art.get("celex") or local(art["uri"])
    title = (art.get("label") if art.get("doctype") == "judgment"
             else art.get("title")) or label
    return (art["uri"], "eurlex", art.get("doctype", "eurlex"),
            label, title, str(path))


def foreskrift_document(art, path):
    # an agency regulation; kind is the författningssamling (fffs/nfs/…), label
    # the short id citations + the bemyndigande margin use ("FFFS 2013:10")
    label = art.get("identifier") or local(art["uri"])
    title = art.get("metadata", {}).get("title") or label
    return (art["uri"], "foreskrift", art.get("fs", "foreskrift"),
            label, title, str(path))


def avg_document(art, path):
    # a JO/JK decision; kind is the organ (jo/jk), label the citation form
    # ("JO dnr 6356-2012" / "JK 2024/8082")
    label = art.get("identifier") or local(art["uri"])
    title = art.get("metadata", {}).get("title") or label
    return (art["uri"], "avg", art.get("org", "avg"), label, title, str(path))


def hudoc_document(art, path):
    label = art.get("ecli") or art.get("itemid") or local(art["uri"])
    title = art.get("title") or label
    return (art["uri"], "hudoc", art.get("doctype", "case-law"),
            label, title, str(path))


def coe_document(art, path):
    label = art.get("identifier") or ("CETS " + art.get("number", ""))
    title = art.get("title") or label
    return (art["uri"], "coe", art.get("doctype", "treaty"),
            label, title, str(path))


def icrc_document(art, path):
    # an IHL treaty; kind is the doctype (treaty/protocol/declaration), label the
    # short citation title (the folkrätt listing and any inbound citation use it)
    label = art.get("identifier") or ("ICRC " + art.get("number", ""))
    title = art.get("title") or label
    return (art["uri"], "icrc", art.get("doctype", "treaty"),
            label, title, str(path))


def untc_document(art, path):
    # a UN Treaty Collection instrument; kind is the doctype (treaty/protocol),
    # label the treaty title, number the MTDSG id
    label = art.get("identifier") or ("MTDSG " + art.get("number", ""))
    title = art.get("title") or label
    return (art["uri"], "untc", art.get("doctype", "treaty"),
            label, title, str(path))


def icc_document(art, path):
    # an ICC decision; kind is the decision type (judgment/sentence/…), label the
    # document number (the citation form), title the case name
    label = art.get("docnumber") or local(art["uri"])
    title = art.get("title") or label
    return (art["uri"], "icc", art.get("doctype", "judgment"),
            label, title, str(path))


def expired_date(art):
    """The date a document's repeal takes effect, if its metadata declares one (a
    statute's `rpubl:upphavandedatum`) -- else None. Stored on the documents row so
    the browse listings can omit a statute once the date has passed (still reachable
    by direct link and search)."""
    return art.get("metadata", {}).get("properties", {}).get("rpubl:upphavandedatum")


def document_date(art):
    """The document's own date (ISO yyyy-mm-dd), for chronological ordering of
    inbound references -- a förarbete's publication date, a statute's
    utfärdandedatum, a decision's date. Field-driven across sources; None when
    the artifact carries no date (the renderer sorts undated entries last)."""
    props = art.get("metadata", {}).get("properties", {})
    return (art.get("date") or art.get("avgorandedatum")
            or art.get("metadata", {}).get("beslutsdatum")
            or art.get("metadata", {}).get("utkomFranTryck")
            or props.get("rpubl:utfardandedatum")
            or props.get("rpubl:avgorandedatum")
            or props.get("rpubl:beslutsdatum"))


def document_publisher(art):
    """The issuing organization, normalized only structurally (not renamed).

    It is catalogued because legacy Atom publisher filters are public request
    parameters; serving one must not reopen and parse the whole artifact corpus.
    """
    metadata = art.get("metadata", {})
    return (metadata.get("publisher")
            or metadata.get("properties", {}).get("dcterms:publisher")
            or art.get("publisher"))


def display_title(art, title):
    """The human title a document shows wherever it is named to a reader -- the
    page heading, a search hit, a listing entry: the act's established short name
    plus its citing acronym when the artifact carries them
    ("Cyberresiliensförordningen (CRA)"), else the given `title` (the full
    heading). Field-driven, not source-keyed -- any source that stamps
    `shortname`/`abbr` gets the same treatment; the rest fall back to their title,
    which for every other source already is the page heading."""
    name = art.get("shortname") or title
    abbr = art.get("abbr")
    return "%s (%s)" % (name, abbr) if abbr else name


def document_row(art, path, source):
    return {"sfs": sfs_document, "dv": dv_document,
            "forarbete": forarbete_document, "kommentar": kommentar_document,
            "begrepp": begrepp_document, "eurlex": eurlex_document,
            "foreskrift": foreskrift_document,
            "avg": avg_document, "hudoc": hudoc_document,
            "coe": coe_document, "icrc": icrc_document,
            "untc": untc_document, "icc": icc_document}[source](art, path)


# --------------------------------------------------------------------------
# rebuild
# --------------------------------------------------------------------------

def content_hash(raw):
    """The change-detection key for an artifact: sha256 of its on-disk bytes.
    Stored on the documents row so relate (and, via the row, index) can skip an
    artifact whose bytes are unchanged since last time."""
    return hashlib.sha256(raw).hexdigest()


def _drop_document(con, uri):
    """Remove a document and everything keyed off it: its outbound links and
    its concept redirects."""
    con.execute("DELETE FROM links WHERE from_uri = ?", (uri,))
    con.execute("DELETE FROM documents WHERE uri = ?", (uri,))
    con.execute("DELETE FROM concept_redirect WHERE concept = ?", (uri,))


def _index_document(con, art, path, source):
    """(Re)write one document's rows: its documents row and outbound links,
    replacing any prior version keyed by the same uri."""
    uri = art["uri"]
    con.execute("DELETE FROM links WHERE from_uri = ?", (uri,))
    row = document_row(art, path, source)        # (uri, source, kind, label, title, path)
    con.execute(
        "INSERT OR REPLACE INTO documents "
        "(uri, source, kind, label, title, path, source_url, content_hash, "
        " expired, display, date, publisher) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (*row, art.get("source_url"),
         None,                 # content_hash filled by the caller (holds bytes)
         expired_date(art),
         display_title(art, row[4]),              # the reader-facing heading (row[4]=title)
         document_date(art), document_publisher(art)))
    rows = [(uri, anchor, run.get("predicate", "dcterms:references"),
             run["uri"], strip_fragment(run["uri"]), run.get("text"))
            for anchor, run in (artifact_links(art) + subject_links(art)
                                + definition_links(art)
                                + bemyndigande_links(art)
                                + curated_links(art))]
    con.executemany("INSERT INTO links VALUES (?,?,?,?,?,?)", rows)
    # a begrepp's `aliases` (old names from MediaWiki redirects) -> resolve to it
    con.execute("DELETE FROM concept_redirect WHERE concept = ?", (uri,))
    con.executemany("INSERT OR REPLACE INTO concept_redirect VALUES (?, ?)",
                    [(v, uri) for v in art.get("aliases", [])])
    return len(rows)


def source_content_signature(con, source):
    """A cheap fingerprint of a source's catalogued (uri, content_hash) rows --
    exactly what `index` syncs to OpenSearch. Unchanged since the last index ⟹
    the index is already current, so its per-source OpenSearch scan + diff can be
    skipped wholesale. Covers synthesized stubs (catalog rows with no artifact),
    which a file-based watermark would miss."""
    h = hashlib.sha256()
    for uri, chash in con.execute(
            "SELECT uri, content_hash FROM documents WHERE source = ? ORDER BY uri",
            (source,)):
        h.update(("%s\x1f%s\x1e" % (uri, chash or "")).encode())
    return h.hexdigest()


def catalog_signature(con):
    """A whole-catalog fingerprint of every document's (uri, content_hash) -- the
    corpus state `generate` renders from. Unchanged ⟹ no page's content or
    citation neighbourhood moved (every link traces to some artifact whose hash is
    here), so a full generate can be skipped. The .corr/.ann sibling layers, which
    relate doesn't fold into content_hash, are watermarked separately by the
    caller."""
    h = hashlib.sha256()
    for uri, chash in con.execute(
            "SELECT uri, content_hash FROM documents ORDER BY uri"):
        h.update(("%s\x1f%s\x1e" % (uri, chash or "")).encode())
    return h.hexdigest()


def _relativize_paths(con, source, root):
    """In-place migration of a pre-relative catalog: paths used to be stored
    absolute, which pinned the catalog to the host that built it. Rewrite this
    source's still-absolute rows to data_root-relative, so an rsync'd catalog
    resolves on a deploy host with a different data_root. A no-op once migrated
    (relative paths don't start with '/'). Runs on the host that built the rows,
    where the absolute path is genuinely under `root` -- `relative_to` raises if
    it is not (a catalog carried over unmigrated), surfacing the mistake rather
    than silently storing a broken path."""
    stale = con.execute("SELECT uri, path FROM documents "
                        "WHERE source = ? AND path LIKE '/%'", (source,)).fetchall()
    for uri, path in stale:
        con.execute("UPDATE documents SET path = ? WHERE uri = ?",
                    (util.store_relpath(path, root), uri))


def rebuild(catalog_path, source, artifact_paths, progress=None, force=False,
            data_root=None, exclusive=False):
    """Sync one source's rows in the catalog to its artifacts on disk.
    Incremental by content hash: an artifact whose bytes are unchanged since the
    last relate is left in place (not re-parsed); new/changed ones are
    re-extracted; rows whose artifact has vanished are dropped. `force`
    re-extracts every artifact regardless of hash. Single-process and
    transactional -- it sidesteps multi-writer SQLite contention. Empty artifacts
    (SkipDocument placeholders) carry no document.

    Returns (documents, links, changed): the source's row + link totals after the
    sync, and how many documents were (re)written this run."""
    con = connect(catalog_path, data_root=data_root, exclusive=exclusive)
    # artifact paths are stored data_root-relative (portable catalog); the root is
    # what `connect` just recorded (or the catalog file's own directory when the two
    # are colocated), never assumed to be catalog_path.parent -- catalog_root may
    # differ from data_root (config.CATALOG_ROOT).
    root = _data_root(con)
    _relativize_paths(con, source, root)
    # current catalog state for this source, keyed by artifact path (1:1 with a
    # document): path -> (uri, content_hash, art_size, art_mtime_ns). Path-less
    # rows (synthesized begrepp stubs) aren't artifact-backed, so they're owned by
    # synthesize_concepts, not this path-keyed sync.
    have = {row[0]: (row[1], row[2], row[3], row[4]) for row in con.execute(
        "SELECT path, uri, content_hash, art_size, art_mtime_ns "
        "FROM documents WHERE source = ?", (source,)) if row[0]}
    seen = set()
    written = set()          # uris (re)indexed this run, keyed independently of path
    changed = 0
    total = len(artifact_paths)
    for i, path in enumerate(map(Path, artifact_paths)):
        # `path` (absolute) is stat'd and read on disk; `key` (data_root-relative)
        # is what the row stores and `have` is keyed by -- so the incremental match
        # and the stored path both stay host-independent.
        key = util.store_relpath(path, root)
        seen.add(key)
        st = compress.stat(path)             # the on-disk (possibly .br) variant
        prev = have.get(key)
        # stat fast path: an artifact whose (size, mtime) match the ones recorded
        # at the last relate is untouched (parse rewrites bump the mtime), so trust
        # them like file_watermark does and skip the read + hash entirely. size 0
        # is an artifact-backed doc's row that never happens (a SkipDocument
        # placeholder carries no row, so prev is None), so it always falls through.
        if (not force and prev and prev[2] == st.st_size
                and prev[3] == st.st_mtime_ns):
            current = local(prev[0])
            written.add(prev[0])
            if progress:
                progress(i + 1, total, changed, current)
            continue
        raw = compress.read_bytes(path)      # decompressed artifact bytes
        if not raw.strip():
            # a SkipDocument placeholder: ensure no stale row survives at this path
            if prev:
                _drop_document(con, prev[0])
            current = path.stem
        else:
            digest = content_hash(raw)
            if not force and prev and prev[1] == digest:
                # bytes unchanged but the file was rewritten (mtime moved) -- skip
                # the parse, but refresh the stored stat so the next run hits the
                # fast path above instead of re-hashing this artifact again
                con.execute("UPDATE documents SET art_size = ?, art_mtime_ns = ? "
                            "WHERE uri = ?",
                            (st.st_size, st.st_mtime_ns, prev[0]))
                current = local(prev[0])
                written.add(prev[0])
            else:
                art = json.loads(raw)
                if prev and prev[0] != art["uri"]:   # uri moved under this path
                    _drop_document(con, prev[0])
                _index_document(con, art, key, source)
                con.execute("UPDATE documents SET content_hash = ?, art_size = ?, "
                            "art_mtime_ns = ? WHERE uri = ?",
                            (digest, st.st_size, st.st_mtime_ns, art["uri"]))
                changed += 1
                written.add(art["uri"])
                current = local(art["uri"])
        if progress:
            progress(i + 1, total, changed, current)
    # drop rows whose artifact vanished -- but a document's identity is its uri, not
    # its path: when an artifact moves to a new path (e.g. a storage-layout change)
    # its uri is re-indexed above under the new path, so it must NOT be dropped here
    # just because the old path is gone (that would delete the row we just wrote).
    for path, (uri, *_) in have.items():
        if path not in seen and uri not in written:
            _drop_document(con, uri)
    docs = con.execute("SELECT COUNT(*) FROM documents WHERE source = ?",
                       (source,)).fetchone()[0]
    edges = con.execute(
        "SELECT COUNT(*) FROM links WHERE from_uri IN "
        "(SELECT uri FROM documents WHERE source = ?)", (source,)).fetchone()[0]
    con.commit()
    con.close()
    return docs, edges, changed


# --------------------------------------------------------------------------
# concept synthesis -- a begrepp node for every defined term / nyckelord the
# corpus references, so the concept layer is the union of the machine-extracted
# terms and the hand-authored wiki concepts (relate post-pass)
# --------------------------------------------------------------------------

# a plausible concept name: starts with a letter (Swedish/accented included via
# \w under unicode), then letters/digits/spaces/hyphens, 2-60 chars. Rejects the
# formula/parenthetical fragments the SFS definition extractor sometimes emits as
# "terms" (`*/k/ utjämningsbelopp`, `(av personuppgifter)`) -- noise, not concepts.
RE_CONCEPT = re.compile(r"^[^\W\d_][\w \-–]{1,59}$")

BEGREPP = BASE + "begrepp/"


def _concept_form(uri):
    return uri[len(BEGREPP):].replace("_", " ")


def _concept_uri(form):
    return BEGREPP + form.replace(" ", "_")


def canonicalize_concepts(con):
    """Collapse the inflected/variant surface forms of each begrepp onto one
    canonical node (`lib.concepts`): cluster every referenced concept + wiki
    title, remap the variant link targets to the canonical uri, and record the
    mapping in `concept_alias` so the renderer resolves a variant uri baked into
    an artifact to the canonical page (the artifacts keep their variant uris --
    canonicalisation is a graph + render concern, no re-parse). Runs before
    `synthesize_concepts`, so stubs are minted for canonical forms. Returns the
    number of variant forms folded away."""
    targets = [r[0] for r in con.execute(
        "SELECT DISTINCT to_root FROM links WHERE to_root LIKE ?", (BEGREPP + "%",))]
    wiki = {_concept_form(r[0]) for r in con.execute(
        "SELECT uri FROM documents WHERE source = 'begrepp' AND path <> ''")}
    forms = {_concept_form(u) for u in targets
             if RE_CONCEPT.match(_concept_form(u))} | wiki
    concepts.register_wiki(wiki)
    con.execute("DELETE FROM concept_alias")
    folded = 0
    resolved = {}                            # uri -> its canonical, for redirect folding
    for canonical, variants in concepts.cluster(forms).items():
        canon_uri = _concept_uri(canonical)
        for variant in variants:
            v_uri = _concept_uri(variant)
            resolved[v_uri] = canon_uri
            if v_uri != canon_uri:
                con.execute("UPDATE links SET to_uri = ?, to_root = ? "
                            "WHERE to_root = ?", (canon_uri, canon_uri, v_uri))
                con.execute("INSERT OR REPLACE INTO concept_alias VALUES (?, ?)",
                            (v_uri, canon_uri))
                folded += 1
    # fold the explicit redirect aliases too (old MediaWiki names -> their
    # concept, itself possibly folded onto a canonical form). Author-declared, so
    # they win; same remap as an inflected variant, so links to the old name live.
    for variant, concept in con.execute("SELECT variant, concept FROM concept_redirect"):
        canon_uri = resolved.get(concept, concept)
        if variant != canon_uri:
            con.execute("UPDATE links SET to_uri = ?, to_root = ? "
                        "WHERE to_root = ?", (canon_uri, canon_uri, variant))
            con.execute("INSERT OR REPLACE INTO concept_alias VALUES (?, ?)",
                        (variant, canon_uri))
            folded += 1
    con.commit()
    return folded


def synthesize_concepts(con):
    """Mint a stub begrepp document for every concept the corpus *references* -- a
    statute's defined term (an SFS `dcterms:subject` link) or a case's nyckelord
    -- that has no wiki-authored page and whose name looks like a real concept
    (`RE_CONCEPT`). The stub carries no description (path empty, rendered as a
    synthesized shell), but it is a real node, so its page shows what defines and
    tags it, and links pointing at it stop dangling. Re-run on every relate;
    incremental relate no longer wipes the source, so this clears the previous
    stubs itself (path-less begrepp rows) before re-minting from the current link
    set, dropping ones the corpus no longer references. Returns the number minted."""
    prefix = BASE + "begrepp/"
    authored = {r[0] for r in con.execute(
        "SELECT uri FROM documents WHERE source = 'begrepp' AND path <> ''")}
    stubs = {r[0] for r in con.execute(
        "SELECT uri FROM documents WHERE source = 'begrepp' AND path = ''")}
    target = {uri for (uri,) in con.execute(
        "SELECT DISTINCT to_root FROM links WHERE to_root LIKE ?", (prefix + "%",))
        if uri not in authored
        and RE_CONCEPT.match(uri[len(prefix):].replace("_", " "))}
    # drop stubs the corpus no longer references (incremental relate no longer
    # wipes the source), then mint stubs for newly-referenced concepts
    for uri in stubs - target:
        con.execute("DELETE FROM documents WHERE uri = ?", (uri,))
    new = sorted(target - stubs)
    # a stub has no artifact; its searchable content is just its name, so give it
    # a stable content_hash off the name -- the index then skips it on a re-run
    # (a None hash would force it to re-index every time) instead of file bytes.
    con.executemany(
        "INSERT OR IGNORE INTO documents "
        "(uri, source, kind, label, title, path, source_url, content_hash, "
        " expired, display) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(uri, "begrepp", "begrepp", name, name, "", None,
          content_hash(("begrepp-stub\x1f" + name).encode()), None, name)
         for uri in new
         for name in [uri[len(prefix):].replace("_", " ")]])
    # backfill the stable hash on any stub minted before this column existed
    # (content_hash NULL) so index's content signature stops churning over them
    for (uri,) in con.execute("SELECT uri FROM documents WHERE source = 'begrepp' "
                              "AND path = '' AND content_hash IS NULL").fetchall():
        name = uri[len(prefix):].replace("_", " ")
        con.execute("UPDATE documents SET content_hash = ? WHERE uri = ?",
                    (content_hash(("begrepp-stub\x1f" + name).encode()), uri))
    con.commit()
    return len(new)


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


def set_fk_kommentar(con, rows):
    """Replace the per-paragraf författningskommentar layer. Each row is
    (sfs_uri, sfs_anchor, prop_uri, prop_label, prop_date, page, text) --
    the FK prose a proposition writes for one statute paragraf (anchor '' for
    a law-level comment), resolved cross-document at relate time (forarbete.fk).
    Display-only: the statute rail shows the text with the prop as provenance;
    no links edge is stored -- a prop's own FK is not a citation."""
    con.execute("DELETE FROM fk_kommentar")
    con.executemany("INSERT INTO fk_kommentar VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()


def fk_kommentar_all(con):
    """Every FK commentary row, newest proposition first -- the renderer builds
    its per-(statute, anchor) rail index from this in one pass."""
    return con.execute(
        "SELECT sfs_uri, sfs_anchor, prop_uri, prop_label, prop_date, page, text "
        "FROM fk_kommentar ORDER BY prop_date DESC, prop_uri").fetchall()


def genomfor_for(con, sfs_uri, anchor):
    """The EU directive articles a statute paragraf transposes, for its margin:
    (directive, article, prop_uri, prop_label, pinpoint, partial)."""
    return con.execute(
        "SELECT directive, article, prop_uri, prop_label, pinpoint, partial "
        "FROM genomforande WHERE sfs_uri = ? AND sfs_anchor = ? "
        "ORDER BY directive, article", (sfs_uri, anchor)).fetchall()


# --------------------------------------------------------------------------
# old-law -> new-law paragraf correspondence (a restructuring proposition's
# författningskommentar, derived by the LLM `.corr` layer -- sfs.correspond)
# --------------------------------------------------------------------------

def set_correspondence(con, rows):
    """Replace the paragraf correspondence layer. Each row is (new_uri,
    old_uri, relation, scope, prop_uri, ikrafttrader) -- both endpoints full
    paragraf uris; ikrafttrader only on same-law renumbering ('betecknas')
    edges. Queried in both directions: the old paragraf's margin shows the
    new paragraf that supersedes it, and the new paragraf's margin shows the
    references citing the old one (the generic `inbound` on `old_uri`),
    date-split by ikrafttrader for renumberings."""
    con.execute("DELETE FROM correspondence")
    con.executemany("INSERT INTO correspondence VALUES (?,?,?,?,?,?)", rows)
    con.commit()


def correspondence_for_old(con, old_uri):
    """The new-law paragraf(s) that now correspond to an old (repealed) paragraf,
    for its margin: (new_uri, relation, scope, prop_uri, ikrafttrader)."""
    return con.execute(
        "SELECT new_uri, relation, scope, prop_uri, ikrafttrader "
        "FROM correspondence WHERE old_uri = ? ORDER BY new_uri",
        (old_uri,)).fetchall()


def correspondence_for_new(con, new_uri):
    """The old (repealed) paragraf(s) a new-law paragraf corresponds to, for its
    margin: (old_uri, relation, scope, prop_uri, ikrafttrader)."""
    return con.execute(
        "SELECT old_uri, relation, scope, prop_uri, ikrafttrader "
        "FROM correspondence WHERE new_uri = ? ORDER BY old_uri",
        (new_uri,)).fetchall()


# --------------------------------------------------------------------------
# queries (used by the renderer)
# --------------------------------------------------------------------------

# Inbound annotations show which *other* documents cite a target. A document's
# references to its own fragments (heading self-links like "12 kap."->#K12, and
# internal "enligt 3 §" cross-refs -- 41% of all edges) are excluded: they are
# the document's own outbound links, navigable in place, not external inbound.
_NOT_SELF = " AND l.from_uri <> l.to_root"
# bemyndigande is a typed relation with its own statute-paragraf margin
# ("Föreskrifter meddelade med stöd av …"), so it is kept out of the generic
# "Hänvisat till av" citation panel (and its count) -- like genomför, which is
# stored as an outbound edge and so never lands in the target's inbound at all.
_NOT_BEMYNDIGANDE = " AND l.predicate <> 'rpubl:bemyndigande'"


def inbound(con, uri, limit=None):
    """Documents citing exactly `uri`, one row per (citing document, pinpoint)
    as (from_uri, from_anchor, label, title, source) -- so a law citing from
    several places shows each pinpoint, and the renderer can group by source
    and render a human-readable label. Self-citations excluded. `limit` caps
    the rows (for display)."""
    # commentary is an annotation shown side-by-side in the rail, not a citing
    # document with a page of its own, so it never appears as an inbound link
    sql = ("SELECT l.from_uri, l.from_anchor, d.label, d.title, d.source "
           "FROM links l JOIN documents d ON d.uri = l.from_uri "
           "WHERE l.to_uri = ?" + _NOT_SELF + _NOT_BEMYNDIGANDE
           + " AND d.source <> 'kommentar' "
           "GROUP BY l.from_uri, l.from_anchor "
           "ORDER BY d.source, d.label, l.from_anchor")
    if limit is not None:
        sql += " LIMIT %d" % limit
    return con.execute(sql, (uri,)).fetchall()


def inbound_collapsed(con, uri, exclude_from=()):
    """Documents citing exactly `uri`, one row per citing *document* (not per
    pinpoint) as (from_uri, label, title, source, kind, date, anchors) -- the
    grain the "Hänvisat till av" panel renders, so a förarbete citing from a
    dozen avsnitt is one line whose `anchors` (comma-joined, NULL pinpoints
    dropped) the renderer turns into a pinpoint list. Self-citations, kommentar
    and bemyndigande excluded, plus any `exclude_from` uris (a statute's own
    förarbeten, shown once in their preparatory-works role instead)."""
    excl = ""
    params = [uri]
    if exclude_from:
        excl = " AND l.from_uri NOT IN (%s)" % ",".join("?" * len(exclude_from))
        params.extend(exclude_from)
    sql = ("SELECT l.from_uri, d.label, d.title, d.source, d.kind, d.date, "
           "GROUP_CONCAT(DISTINCT l.from_anchor) "
           "FROM links l JOIN documents d ON d.uri = l.from_uri "
           "WHERE l.to_uri = ?" + _NOT_SELF + _NOT_BEMYNDIGANDE
           + " AND d.source <> 'kommentar'" + excl
           + " GROUP BY l.from_uri "
           "ORDER BY d.source, d.date, d.label")
    return con.execute(sql, params).fetchall()


def bemyndigande_inbound(con, uri):
    """The föreskrifter issued (meddelade) under a statute paragraf -- the inbound
    side of the bemyndigande edge: (foreskrift_uri, label, title), one per
    regulation. Drives the paragraf's 'Föreskrifter meddelade med stöd av denna
    paragraf' margin. Joined to documents for the title; ordered by föreskrift id."""
    return con.execute(
        "SELECT DISTINCT l.from_uri, d.label, d.title "
        "FROM links l JOIN documents d ON d.uri = l.from_uri "
        "WHERE l.to_uri = ? AND l.predicate = 'rpubl:bemyndigande' "
        "ORDER BY d.label", (uri,)).fetchall()


def document_inbound_count(con, root_uri):
    """How many (citing document, pinpoint) entries cite a document *as a whole*
    -- any of its fragments or its bare uri. The 'most-hänvisade' authority
    signal (search ranking, the API's headline count), broader than
    `inbound_count`, which counts one exact uri. Self-citations excluded."""
    return con.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM links l WHERE l.to_root = ?"
        + _NOT_SELF + " GROUP BY l.from_uri, l.from_anchor)",
        (root_uri,)).fetchone()[0]


def document_inbound_counts(con):
    """`document_inbound_count` for every cited root at once -- {root_uri:
    count}, same semantics as the per-uri query. One pass over the links table
    instead of one GROUP-BY subquery per document (the full-reindex path)."""
    return dict(con.execute(
        "SELECT to_root, COUNT(*) FROM (SELECT l.to_root, 1 FROM links l "
        "WHERE 1=1" + _NOT_SELF + " GROUP BY l.to_root, l.from_uri, "
        "l.from_anchor) GROUP BY to_root"))


def counts(con):
    return dict(con.execute(
        "SELECT source, COUNT(*) FROM documents GROUP BY source").fetchall())


def expired_uris(con, today):
    """The uris whose declared repeal date (`expired`) is on or before `today` (an
    ISO date string) -- repealed statutes to drop from the browse listings. A
    future repeal date (not yet in force) is kept."""
    return {r[0] for r in con.execute(
        "SELECT uri FROM documents WHERE expired IS NOT NULL AND expired <= ?",
        (today,))}


def concept_aliases(con):
    """The variant-uri -> canonical-uri map (`concept_alias`), so the renderer can
    resolve a begrepp link baked into an artifact onto its canonical concept page."""
    return dict(con.execute("SELECT variant, canonical FROM concept_alias"))


def document(con, uri):
    """A document's catalog row (uri, source, kind, label, title, path), or
    None -- the metadata behind an API /document lookup."""
    return con.execute(
        "SELECT uri, source, kind, label, title, path FROM documents "
        "WHERE uri = ?", (uri,)).fetchone()


def document_by_prefix(con, uri_prefix):
    """The one document whose uri extends `uri_prefix`, or None when nothing
    or several match. GLOB, not LIKE, so the literal '_'/'.' in lagen.nu URIs
    match themselves -- how a bare page-number SFS id resolves ("...1904:48"
    + "_s." -> the 1904:48_s.1 row) when only the catalog knows the page."""
    rows = con.execute(
        "SELECT uri, source, kind, label, title, path FROM documents "
        "WHERE uri GLOB ?", (uri_prefix + "*",)).fetchall()
    return rows[0] if len(rows) == 1 else None


def document_meta(con, uri):
    """(kind, label, title, date) for a uri, or None -- the columns the inbound
    labels and the preparatory-works section need without loading the artifact."""
    return con.execute(
        "SELECT kind, label, title, date FROM documents WHERE uri = ?",
        (uri,)).fetchone()


def document_display(con, uri):
    """The stored reader-facing heading (`documents.display`, written at
    relate), or None -- so a lookup need not load the artifact to label a hit."""
    row = con.execute("SELECT display FROM documents WHERE uri = ?",
                      (uri,)).fetchone()
    return row[0] if row else None


def _doc_filter(source, kind):
    """A (WHERE-clause, params) pair shared by `documents` and `document_count`."""
    clauses, params = [], []
    if source:
        clauses.append("source = ?")
        params.append(source)
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), params


def documents(con, source=None, kind=None, limit=None, offset=0):
    """A filtered, paginated document listing as (uri, source, kind, label,
    title, source_url, path, display) rows, ordered by uri -- the id/metadata
    index that drives /document lookups and the browse listings (not full-text
    search). `display` is the reader-facing heading (catalog.display_title).
    `source`/`kind` filter; `limit`/`offset` page."""
    where, params = _doc_filter(source, kind)
    sql = ("SELECT uri, source, kind, label, title, source_url, path, display "
           "FROM documents" + where + " ORDER BY uri")
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params += [limit, offset]
    return con.execute(sql, params).fetchall()


def facet_documents(con, source):
    """Catalog rows needed by faceted browse, including the document date.

    The public ``documents`` tuple predates the date column and is kept stable
    for REST/feed callers. Facets need the date for sources whose identifier
    does not encode a year (HUDOC item ids and CETS numbers).
    """
    return con.execute(
        "SELECT uri, source, kind, label, title, source_url, path, display, date "
        "FROM documents WHERE source = ? ORDER BY uri", (source,)
    ).fetchall()


def document_count(con, source=None, kind=None):
    """How many documents match the same `source`/`kind` filter -- the total for
    a paginated `documents` listing."""
    where, params = _doc_filter(source, kind)
    return con.execute("SELECT COUNT(*) FROM documents" + where,
                       params).fetchone()[0]


def outbound(con, uri):
    """Every citation a document makes, as (to_uri, predicate, text, from_anchor,
    target_label, target_title, target_source) -- target_* are NULL when the
    cited document is not (yet) in the corpus. The mirror of `inbound`."""
    return con.execute(
        "SELECT l.to_uri, l.predicate, l.text, l.from_anchor, "
        "       d.label, d.title, d.source "
        "FROM links l LEFT JOIN documents d ON d.uri = l.to_root "
        "WHERE l.from_uri = ? ORDER BY l.from_anchor, l.to_uri", (uri,)).fetchall()


_EMPTY_SIDE = hashlib.sha256().hexdigest()   # digest of zero inbound/outbound rows


def _combine_dep(inbound_hex, outbound_hex):
    return hashlib.sha256(
        ((inbound_hex or _EMPTY_SIDE) + "\x00"
         + (outbound_hex or _EMPTY_SIDE)).encode()).hexdigest()


# The dependency digest a page with no inbound *and* no outbound edges gets --
# the default for a uri absent from `page_dependency_digests` (generate looks up
# every catalogued uri, including the link-less ones).
EMPTY_DEP_DIGEST = _combine_dep(None, None)


def page_dependency_digests(con):
    """`{uri: digest}` for every document with a citation relationship -- a digest
    of everything *besides its own artifact* that its rendered page depends on, for
    incremental generate. Identity/set-based, not content-based: cited and citing
    documents are effectively immutable (a case or förarbete never changes once
    published), so a page goes stale when the *set* of its relationships changes --
    a new case starts citing it, an old one drops out, or a document it links to
    appears/disappears -- not when an unchanged neighbour's bytes change. Two parts,
    combined into the per-uri digest:

      * inbound -- the (citing doc, pinpoint, label) rows it renders in its
        margins and panel: a new or removed citer changes this;
      * outbound -- the set of hosted documents it links to, so a link goes live
        the moment its target is parsed (and dims if the target disappears).

    One streamed pass over the whole `links` table per part instead of two
    subqueries per document (the 124k-document generate-planning loop); a uri with
    neither part is absent from the result and takes `EMPTY_DEP_DIGEST`.
    Self-citations excluded; external targets we don't host drop out of the join."""
    # inbound: ordered by target so one pass groups each cited root's citation rows
    inbound = {}
    cur, h = None, hashlib.sha256()
    for root, *fields in con.execute(
            "SELECT l.to_root, l.from_uri, l.from_anchor, d.label, d.title, d.source "
            "FROM links l JOIN documents d ON d.uri = l.from_uri "
            "WHERE l.from_uri <> l.to_root "
            "ORDER BY l.to_root, l.from_uri, l.from_anchor"):
        if root != cur:
            if cur is not None:
                inbound[cur] = h.hexdigest()
            cur, h = root, hashlib.sha256()
        h.update(("\x1f".join("" if c is None else c for c in fields)).encode())
        h.update(b"\x1e")
    if cur is not None:
        inbound[cur] = h.hexdigest()
    # outbound: ordered by citing doc so one pass groups the hosted targets it links
    outbound = {}
    cur, h = None, hashlib.sha256()
    for from_uri, target in con.execute(
            "SELECT DISTINCT l.from_uri, l.to_root FROM links l "
            "JOIN documents d ON d.uri = l.to_root "
            "WHERE l.to_root <> l.from_uri ORDER BY l.from_uri, l.to_root"):
        if from_uri != cur:
            if cur is not None:
                outbound[cur] = h.hexdigest()
            cur, h = from_uri, hashlib.sha256()
        h.update(target.encode())
        h.update(b"\x1e")
    if cur is not None:
        outbound[cur] = h.hexdigest()
    return {uri: _combine_dep(inbound.get(uri), outbound.get(uri))
            for uri in inbound.keys() | outbound.keys()}
