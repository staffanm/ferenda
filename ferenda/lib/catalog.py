"""The derived catalog: a SQLite index over every parsed artifact plus the
cross-source citation graph it implies.

The `relate` phase depends only on the published artifacts, never on source
internals. The catalog is fully
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

import collections
import hashlib
import json
import re
import sqlite3
import threading
from datetime import date, datetime, timezone
from functools import partial
from pathlib import Path

from .. import config
from . import compress, concepts, labels, text, util
from .markdown import begrepp_uri
from .pinpoint import pinpoint_label

BASE = "https://lagen.nu/"


def norm_title(t: str) -> str:
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
    kind         TEXT,             -- the source's own document type, so the
                                   -- vocabulary is per source and a value is
                                   -- only unique paired with it: sfs is
                                   -- 'lag'/'forordning', dv 'case'/'judgment',
                                   -- forarbete 'prop'/'sou'/…, foreskrift the
                                   -- fs slug
    label        TEXT,             -- short display id (SFS number / referat)
    title        TEXT,             -- full heading
    descriptive  TEXT,             -- short descriptive citing form (labels, I1)
    short_id     TEXT,             -- bare identifier (labels, I2 listings)
    short_title  TEXT,             -- short human name (labels, I2 listings)
    description  TEXT,             -- source's one-line description (case sammanfattning)
    path         TEXT NOT NULL,    -- artifact json on disk
    source_url   TEXT,             -- authoritative publisher url ("Källa"), if any
    content_hash TEXT,             -- sha256 of the artifact bytes (incremental relate)
    expired      TEXT,             -- repeal-effective date (SFS upphavandedatum), if any
    date         TEXT,             -- the document's own date (förarbete/statute/decision), ISO
    publisher    TEXT,             -- issuing organization, for feed filtering
    inbound_count INTEGER,         -- document_inbound_count materialized at
                                   -- relate (stamp_inbound_counts); NULL on a
                                   -- catalog no relate has stamped yet
    snippet      TEXT              -- the document's own opening prose (or the
                                   -- dv sammanfattning), for the graph
                                   -- explorer's details panel; stamped at
                                   -- (re)extraction, NULL until then
);
CREATE TABLE IF NOT EXISTS links (
    from_uri    TEXT NOT NULL,   -- document making the citation (doc-level uri)
    from_anchor TEXT,            -- nearest enclosing node id in the citing doc
    predicate   TEXT NOT NULL,
    to_uri      TEXT NOT NULL,   -- full target incl. #fragment
    to_root     TEXT NOT NULL,   -- target document uri, fragment stripped
    text        TEXT,            -- citation surface text
    from_page   INTEGER          -- printed page the citation sits on, where the
                                 -- citing doc has pages (förarbete/DV PDFs), so
                                 -- an inbound line can say "s. 45" for an anchor
                                 -- with no citable designator of its own (S4)
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
    partial    INTEGER NOT NULL,    -- "genomför delvis"
    sfs_pinpoint TEXT               -- stycke/punkt within the paragraf ("S1",
                                    -- "S3N2"); '' = the whole paragraf
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
CREATE TABLE IF NOT EXISTS norm_chain (
    lower_uri   TEXT NOT NULL,  -- the subordinate document (no fragment)
    lower_pin   TEXT,           -- the provision of it the relation names, if any
    upper_uri   TEXT NOT NULL,  -- the document it derives its authority from
    upper_pin   TEXT,           -- the empowering/transposed provision, if named
    predicate   TEXT NOT NULL,  -- the typed relation that states it
    lower_level INTEGER NOT NULL,   -- rung of each end, so a walk can order them
    upper_level INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chain_lower ON norm_chain(lower_uri);
CREATE INDEX IF NOT EXISTS idx_chain_upper ON norm_chain(upper_uri);
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
CREATE TABLE IF NOT EXISTS directive_correspondence (
    new_uri      TEXT NOT NULL,     -- the recast EU act (doc uri, no fragment)
    new_article  TEXT NOT NULL,     -- its article number
    old_uri      TEXT NOT NULL,     -- the act it replaced
    old_article  TEXT NOT NULL,     -- the article that one had
    new_pinpoint TEXT,              -- the table cell's finer new-side ref ("12.1")
    old_pinpoint TEXT               -- ... and old-side ("18.3")
);
CREATE TABLE IF NOT EXISTS definitions (
    concept   TEXT NOT NULL,        -- the begrepp uri the defined term resolves to
    from_uri  TEXT NOT NULL,        -- the act that defines it (doc uri, no fragment)
    anchor    TEXT,                 -- the node stating it (K1P2S2 / 6.9)
    term      TEXT NOT NULL,        -- the term as that act writes it
    sentence  TEXT NOT NULL         -- the sentence that states the definition
);
CREATE INDEX IF NOT EXISTS idx_definitions_concept ON definitions(concept);
CREATE INDEX IF NOT EXISTS idx_definitions_from ON definitions(from_uri);
CREATE INDEX IF NOT EXISTS idx_dircorr_new
    ON directive_correspondence(new_uri, new_article);
CREATE INDEX IF NOT EXISTS idx_corr_new ON correspondence(new_uri);
CREATE INDEX IF NOT EXISTS idx_corr_old ON correspondence(old_uri);
CREATE INDEX IF NOT EXISTS idx_genomf_sfs ON genomforande(sfs_uri, sfs_anchor);
CREATE INDEX IF NOT EXISTS idx_links_to_uri  ON links(to_uri);
CREATE INDEX IF NOT EXISTS idx_links_from    ON links(from_uri);
-- their `to_root` sibling is deliberately NOT here: it is covering, and building
-- it over a populated table is minutes of work that `executescript(SCHEMA)`
-- would do on the serving path. `connect` creates it while the table is still
-- empty; `widen_to_root_index` rebuilds it later. See INDEX_TO_ROOT_COLUMNS.
-- `idx_docs_source` is deliberately NOT here: it covers (source, art_size), and
-- `art_size` is an ALTER-added column this script cannot reference. `connect`
-- creates it below, after the migrations. See INDEX_DOCS_SOURCE_COLUMNS.
"""

# The inbound-citation queries count one entry per (citing document, pinpoint),
# not one per link row, so they look a document up by `to_root` and then group by
# `from_uri, from_anchor`. A to_root-only index answers the *lookup* from the
# index and then fetches each matching row out of the 2.1 GB links table for the
# other two columns -- one scattered read per matching link, and Rättegångsbalken
# has 228 297 of them. Carrying the two grouped columns in the index makes it
# covering: the query never touches the table, and reads one contiguous index
# range instead. Both figures below are that same count, cold, per machine:
#
#            dev (NVMe, 0.068 ms/random read)   prod (disk, ~9-11 ms, ~100 IOPS)
#   narrow   2.81 s, 194 MB read                190 s
#   wide     0.24 s, 33 MB read                 (not yet measured)
#
# Which is why dev never noticed. Don't read the prod column as seeks x latency:
# 194 MB of 4 KB reads would be ~48 000 of them and so ~480 s, and it measured
# 190 -- readahead coalesces some of the range. The gap is the point, not its
# exact multiple.
#
# Everything that reports an inbound count paid this: `get_document`, `fetch`,
# `resolve_citation` and citation-shaped `search` over MCP; `/api/v1/document`
# and the same `search` resolution over REST (lib/pins.py); and, by volume the
# largest, lib/page.py once per rendered page in every generate worker.
#
# `to_root` stays leftmost, so this serves every plain `to_root = ?` lookup the
# narrow index served and replaces rather than joins it -- at the cost of a wider
# range to scan for the ones that only wanted `to_root` (stats/compute.py runs
# one such count per statute). The index measured 735 MB built alongside the
# narrow one on dev; built as its replacement, `DROP` returns the old pages to
# the freelist for `CREATE` to reuse, and the real corpus catalog went from
# 4.87 to 4.96 GB.
# `documents`' own covering index, for the same reason as its links-side sibling
# below. It serves two queries, and the column order is what lets one index do
# both:
#
#   * The ops dashboard's per-source totals, one `SELECT source, COUNT(*),
#     SUM(art_size) ... GROUP BY source`. A (source)-only index orders that scan
#     but cannot answer it, so the query reads the whole 173.8 MB table;
#     covering, it is a 5.5 MB index scan.
#   * The paginated listing (`documents()`, behind the MCP `list_documents` and
#     the browse pages): `WHERE source = ? ORDER BY uri LIMIT n`. Without `uri`
#     in the index the plan is `USE TEMP B-TREE FOR ORDER BY` -- every row of the
#     source read and sorted to return the first few, so the cost scales with the
#     source, not with `n`. Measured on prod, cold: sfs (11k rows) 28 s, dv (24k)
#     50 s, forarbete (97k) **154 s**, which is past nginx's 60 s
#     proxy_read_timeout -- the caller got a 504 while the query ran on. With
#     `uri` second, SQLite walks the index in order and stops after `n`.
#
# `source` leads, so every plain `WHERE source = ?` keeps the index it had, and
# `art_size` stays present so the dashboard's scan is still covering.
INDEX_DOCS_SOURCE_COLUMNS = ("source", "uri", "art_size")
_CREATE_DOCS_SOURCE = ("CREATE INDEX IF NOT EXISTS idx_docs_source ON documents(%s)"
                       % ", ".join(INDEX_DOCS_SOURCE_COLUMNS))

INDEX_TO_ROOT_COLUMNS = ("to_root", "from_uri", "from_anchor")
_CREATE_TO_ROOT = ("CREATE INDEX IF NOT EXISTS idx_links_to_root ON links(%s)"
                   % ", ".join(INDEX_TO_ROOT_COLUMNS))


def connect(path: Path | str, data_root: Path | None = None,
            exclusive: bool = False) -> sqlite3.Connection:
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
    if "descriptive" not in cols:
        con.execute("ALTER TABLE documents ADD COLUMN descriptive TEXT")
    for col in ("short_id", "short_title", "description"):
        if col not in cols:
            con.execute("ALTER TABLE documents ADD COLUMN %s TEXT" % col)
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
    if "inbound_count" not in cols:
        con.execute("ALTER TABLE documents ADD COLUMN inbound_count INTEGER")
    if "snippet" not in cols:
        con.execute("ALTER TABLE documents ADD COLUMN snippet TEXT")
    corr_cols = {row[1] for row in con.execute("PRAGMA table_info(correspondence)")}
    if "ikrafttrader" not in corr_cols:
        con.execute("ALTER TABLE correspondence ADD COLUMN ikrafttrader TEXT")
    link_cols = {row[1] for row in con.execute("PRAGMA table_info(links)")}
    if "from_page" not in link_cols:
        con.execute("ALTER TABLE links ADD COLUMN from_page INTEGER")
    genomf_cols = {row[1] for row in con.execute("PRAGMA table_info(genomforande)")}
    if "sfs_pinpoint" not in genomf_cols:
        con.execute("ALTER TABLE genomforande ADD COLUMN sfs_pinpoint TEXT")
    con.execute("CREATE INDEX IF NOT EXISTS idx_docs_publisher "
                "ON documents(source, publisher)")
    # (source, art_size), now that the ALTERs above have made art_size exist.
    # Free on a fresh catalog; on a populated one this is the *narrow* index
    # every catalog built before the widening still carries, and `IF NOT EXISTS`
    # matches its name, so it stays narrow until `rebuild` calls
    # `widen_docs_source_index` -- correct and slow, never a stall here.
    con.execute(_CREATE_DOCS_SOURCE)
    # Only while the table is empty, i.e. on a fresh catalog, where it is free.
    # Building this index over a populated links table is minutes of work, and
    # `connect` is on the serving path (`connect_ro` calls it for its one-time
    # migration, inside the first request, with every concurrent request queued
    # behind it). So a populated catalog that lacks the index -- built before the
    # widening, or left index-less by a relate that died mid-rebuild -- keeps
    # answering correctly and slowly rather than stalling here; `rebuild` is what
    # puts it right (`widen_to_root_index`).
    if not con.execute("SELECT 1 FROM links LIMIT 1").fetchone():
        con.execute(_CREATE_TO_ROOT)
    # Hand back a connection with no transaction in flight. `_record_data_root`
    # runs a DELETE or an INSERT, and sqlite3's legacy isolation_level opens an
    # implicit transaction before DML -- so without this, `connect` returned
    # mid-write. Anything that then issued an explicit BEGIN died with "cannot
    # start a transaction within a transaction": `rebuild` did exactly that via
    # `widen_docs_source_index`, on every catalog old enough to need the widening,
    # which killed `lagen all relate` corpus-wide. Committing here also makes the
    # schema migrations above durable at once, which is what a caller expects of
    # a factory that says "schema ensured".
    con.commit()
    return con


def widen_docs_source_index(con: sqlite3.Connection) -> bool:
    """Rebuild `idx_docs_source` covering (INDEX_DOCS_SOURCE_COLUMNS) if it is
    not already, returning whether it had to -- the `widen_to_root_index`
    pattern, for the reason given at INDEX_DOCS_SOURCE_COLUMNS and with the same
    transaction discipline. Idempotent; asks `index_info` for the indexed
    columns, so a half-widened index is detected as the miss it is.

    Cheap next to its links-side sibling (296k rows, not 15M), but it belongs
    here rather than in `connect` for the same reason: `connect` is on the
    serving path, and re-sorting a table is build-cost work."""
    if tuple(row[2] for row in con.execute(
            "PRAGMA index_info(idx_docs_source)")) == INDEX_DOCS_SOURCE_COLUMNS:
        return False
    con.execute("BEGIN IMMEDIATE")
    con.execute("DROP INDEX IF EXISTS idx_docs_source")
    con.execute(_CREATE_DOCS_SOURCE)
    con.execute("COMMIT")
    return True


def widen_to_root_index(con: sqlite3.Connection) -> bool:
    """Rebuild `idx_links_to_root` covering (INDEX_TO_ROOT_COLUMNS) if it is not
    already, returning whether it had to. Idempotent; asks `index_info` for the
    indexed columns rather than pattern-matching the recorded CREATE, so a
    half-widened index is detected as the miss it is.

    Called from `rebuild` and nowhere else, deliberately: re-sorting every link
    row is ordinary for a relate and quite wrong for the serving path (see
    `connect`). In one transaction, because SQLite commits bare DDL as it goes --
    a `DROP` that committed before its `CREATE` died would leave a populated
    catalog with no index at all, which is slower than the narrow one it
    replaced and reads as a plan regression rather than as a crash.
    """
    if tuple(row[2] for row in con.execute(
            "PRAGMA index_info(idx_links_to_root)")) == INDEX_TO_ROOT_COLUMNS:
        return False
    con.execute("BEGIN IMMEDIATE")
    con.execute("DROP INDEX IF EXISTS idx_links_to_root")
    con.execute(_CREATE_TO_ROOT)
    con.execute("COMMIT")
    return True


_ro_lock = threading.Lock()
_ro_migrated = set()


def connect_ro(path: Path | str) -> sqlite3.Connection:
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
    # check_same_thread=False: FastAPI runs a sync dependency and its sync
    # endpoint in the threadpool, and under concurrent load they land on
    # *different* worker threads -- the default check then 500s a request
    # whose connection was handed across. The per-request connection is still
    # used by one thread at a time, which is the hazard the check guards.
    return sqlite3.connect("file:%s?mode=ro" % path, uri=True,
                           check_same_thread=False)


def local(uri: str) -> str:
    return uri[len(BASE):] if uri.startswith(BASE) else uri


def strip_fragment(uri: str) -> str:
    return uri.split("#", 1)[0]


def fragment(uri):
    """The fragment of a uri, or None where it has none -- `strip_fragment`'s
    other half."""
    return uri.split("#", 1)[1] if "#" in uri else None


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


# roots already probed -- one row read plus one stat per distinct root, not per
# call, since `data_root` is called on every render and every search hit
_checked_roots: set[Path] = set()


# rows to probe before calling a root wrong. A misresolved root misses
# *systematically*; one missing artifact is an ordinary state (a document
# deleted, or catalogued before it was parsed), and below a sample this size the
# two are indistinguishable -- which is the shape of the only false positive this
# check can produce, so it declines to judge there instead.
ROOT_SAMPLE = 20


def _resolves(con, root):
    """Whether this catalog's stored paths find their artifacts under `root`.

    Asked of real rows rather than of the tree's shape: the stored path is
    exactly what every reader joins to the root, so resolving one is the
    question, and it stays true whatever the layout. Undecidable (too few rows)
    counts as resolving -- the caller then keeps what it had."""
    rows = con.execute("SELECT path FROM documents WHERE path != '' LIMIT ?",
                       (ROOT_SAMPLE,)).fetchall()
    # `_artifact_path` is None only for the empty stored path the query excludes
    return (len(rows) < ROOT_SAMPLE
            or any((p := _artifact_path(root, r[0])) and compress.exists(p)
                   for r in rows))


def _data_root(con):
    row = con.execute("SELECT value FROM meta WHERE key = 'data_root'").fetchone()
    if row and row[0]:
        return Path(row[0])
    # The colocated default, but only where the catalog really is colocated: a
    # corpus root has an `artifact/` tree beside the catalog file, and that is
    # what makes the guess a guess rather than an assumption.
    #
    # Where it is not -- a separated layout whose catalog carries no recorded
    # root -- the running process's own configured root is better information
    # than the directory the file happens to sit in. That combination is not
    # hypothetical: a catalog built on dev is *deliberately* left unstamped so it
    # stays rsync-portable, and prod puts the catalog on a local disk because its
    # data_root is root_squashed NFS. Rsync one to the other and every stored
    # path resolved against the catalog's own directory, so every artifact read
    # missed -- MCP text retrieval returned nothing for the entire corpus while
    # the artifacts sat healthy one mount away, and the generated pages, being
    # static, gave no hint. Prod only re-recorded the root by running its own
    # relate, which had been disabled since an unrelated NFS fault.
    beside = _catalog_file(con).parent
    return beside if _resolves(con, beside) else config.DATA


def data_root(con: sqlite3.Connection) -> Path:
    """The corpus root a catalog's stored (data_root-relative) artifact paths
    resolve against. When the catalog lives outside the corpus (catalog_root !=
    data_root) a full rebuild records the absolute root in `meta`; otherwise this
    falls back to the directory the catalog file itself lives in (the colocated
    default -- which also keeps the catalog rsync-portable, see `_record_data_root`).

    Checked once per process, because the failure is otherwise silent: a root
    pointing at a tree with no artifacts reads as "every document is missing"
    rather than as a misconfiguration, and only the consumers that read artifacts
    at runtime notice (the generated pages are static and keep serving). A
    catalog with rows must have an artifact tree under its root
    (rule:fail-fast)."""
    root = _data_root(con)
    if root not in _checked_roots:
        assert _resolves(con, root), (
            "catalog %s resolves its artifact paths against %s, where its own "
            "rows find no artifact -- record the real corpus root in its `meta` "
            "table, or point config's data_root at it"
            % (_catalog_file(con), root))
        _checked_roots.add(root)
    return root


def quiesce_wal(path: Path | str) -> None:
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


def _artifact_path(root: Path, stored: str) -> Path | None:
    """Resolve a stored (data_root-relative) artifact path to an absolute Path, or
    None for a synthesized stub (empty `path`). `root` is `data_root(con)`. Thin
    domain-named wrapper over the shared `util.load_relpath`."""
    return util.load_relpath(root, stored)


def load_artifact(root, stored):
    """The parsed artifact JSON behind a documents row, `{}` for a synthesized
    stub (empty `path` -- begrepp rows have no artifact file). Reads through
    `compress` so a brotli-precompressed artifact tree serves unchanged."""
    p = _artifact_path(root, stored)
    return compress.read_json(p) if p else {}


def artifact_updated(root, stored):
    """A documents row's artifact last-build time as an ISO 8601 UTC string,
    None for a synthesized stub or a missing file."""
    p = _artifact_path(root, stored)
    return (datetime.fromtimestamp(compress.stat(p).st_mtime,
                                   timezone.utc).isoformat()
            if p and compress.exists(p) else None)


# --------------------------------------------------------------------------
# edge extraction -- one generic walk over any artifact node tree
# --------------------------------------------------------------------------

def collect_links(node, anchor, page, out):
    """Walk an artifact node tree, appending (anchor, page, run) for every
    inline link, attributed to the nearest enclosing node `id` and the printed
    page it sits on (sources parsed from a PDF tag their blocks with one; the
    rest carry None throughout). Handles the two leaf carriers of runs: a node's
    `text` list and a table `rad`'s `cells` (a list of cells, each itself a runs
    list)."""
    if isinstance(node, dict):
        anchor = node.get("id") or anchor
        page = node.get("page") or page
        for key, value in node.items():
            if key == "text" and isinstance(value, list):
                out += [(anchor, page, run) for run in value
                        if isinstance(run, dict) and "uri" in run]
            elif key == "cells":
                for cell in value:
                    out += [(anchor, page, run) for run in cell
                            if isinstance(run, dict) and "uri" in run]
            else:
                collect_links(value, anchor, page, out)
    elif isinstance(node, list):
        for item in node:
            collect_links(item, anchor, page, out)


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
            out.append((anchor, rec.get("page"),
                        {"uri": uri, "predicate": rec["predicate"],
                         "text": rec.get("sentence")}))
    return out


def artifact_links(art):
    """Every inline citation in an artifact, from the body-bearing sections
    of either source: SFS `structure` + the amendments' `content`, DV `body`,
    a föreskrift's presented consolidation (which replaces its base
    `structure` -- text.body_sections owns that choice, so the graph carries
    exactly the citations the rendered page shows), plus a förarbete's
    `implements` (genomför-direktiv) edges and generic top-level `references`
    for relations expressed by source metadata rather than a literal body
    span (HUDOC's article facet, treaty crosswalks). Entries are
    (anchor, page, run) -- the body walk is the only producer that knows a
    printed page, so the metadata edges below carry None."""
    out = []
    for nodes in text.body_sections(art):
        collect_links(nodes, None, None, out)
    for amendment in art.get("amendments", []):
        collect_links(amendment.get("content"), None, None, out)
    out += implements_links(art)
    # Source metadata can carry legal relations that have no literal span in
    # the body (HUDOC's article facet, a treaty's Swedish implementation).
    # Keep the contract generic: every producer emits ordinary link-run dicts.
    out += [(None, None, run) for run in art.get("references", [])]
    return out


def subject_links(art):
    """Concept (begrepp) edges from a court decision's `nyckelord`. nyckelord are
    metadata, not body text, so the inline-link walk misses them; each tags the
    case with a concept (`dcterms:subject`), so the concept page lists the cases
    tagged with it -- the case→concept half of the keyword graph."""
    return [(None, {"uri": begrepp_uri(n), "predicate": "dcterms:subject",
                    "text": n})
            for n in art.get("metadata", {}).get("nyckelord", []) if n.strip()]


# --------------------------------------------------------------------------
# the norm hierarchy (norm_chain)
# --------------------------------------------------------------------------

# Which rung of the Swedish norm hierarchy a document occupies, keyed by
# (source, kind) -- both catalog data, so this stays a table rather than a
# branch on any particular source. A source not listed has no place in the
# hierarchy: a förarbete, a dom and a JO-beslut are *about* rules without being
# rules, and the chain must not pretend otherwise.
NORM_LEVEL = {("eurlex", None): 0, ("sfs", "lag"): 1,
              ("sfs", "forordning"): 2, ("foreskrift", None): 3}

# `norm_chain` is metadata, not a rendered panel. A first attempt showed it in
# the context rail and the audit found the display is the hard part, not the
# data: on a paragraf that transposes an EU article the rung duplicated the
# richer "Genomför EU-rätt" row, and the lag<->förordning rung is present for
# the ~700 förordningar that state their authority in one of two fixed ingress
# formulas and absent for the rest, with nothing on the page to explain the
# difference. Helping a reader up and down the hierarchy needs an editorial
# layer, so the table is built and left for one to read.

# The typed relations that place one document under another. Each is stated by
# the *subordinate* document about the one above it -- a föreskrift names the
# paragraf empowering it, a statute the directive it transposes -- so the citing
# end is always the lower rung. Plain references are deliberately absent: an
# ordinary cross-reference says two provisions are related, not that one derives
# its authority from the other, and admitting them turns the chain into the
# citation graph it exists to be distinguishable from.
CHAIN_PREDICATES = ("rpubl:bemyndigande", "rpubl:genomforDirektiv",
                    "rinfoex:kompletterar")


def norm_level(source, kind):
    """The document's rung, or None if it is not itself a rule."""
    return NORM_LEVEL.get((source, kind), NORM_LEVEL.get((source, None)))


def _sfs_authority_links(art):
    """The authority edges a *förordning* artifact carries: the empowering
    provisions its bemyndigandeupplysning names ("Denna förordning är meddelad
    med stöd av 1 kap. 8 § cybersäkerhetslagen i fråga om 4 §") and the act its
    ingress says it completes ("innehåller kompletterande bestämmelser till
    säkerhetsskyddslagen (2018:585)"). Read at parse time (sfs.bemyndigande);
    the catalog only publishes them as typed edges.

    A bemyndigande edge is anchored to the provision it authorises where the
    clause names one, so the chain can be walked provision-to-provision; the
    clause's own punkt is not a useful anchor and is not kept. `kompletterar`
    is document-level by nature and carries no anchor."""
    out = [(fragment(provision),
            {"uri": entry["lagrum"], "predicate": "rpubl:bemyndigande",
             "text": entry["lagrum"]})
           for entry in art.get("bemyndigande", [])
           for provision in (entry["provisions"] or [""])]
    return out + [(None, {"uri": uri, "predicate": "rinfoex:kompletterar",
                          "text": uri})
                  for uri in art.get("kompletterar", [])]


def rebuild_norm_chain(con):
    """Recompute `norm_chain` from the typed authority edges in `links`.

    One row per stated relation, both ends resolved to a rung. An edge whose
    either end is not a rule (a förarbete citing a föreskrift, a document not in
    the corpus) is dropped: the chain answers "what authorises this", and only a
    rule can. An edge that does not descend a rung is dropped too -- a föreskrift
    amending a sibling föreskrift is a relation between equals, not authority."""
    con.execute("DELETE FROM norm_chain")
    con.execute(
        "INSERT INTO norm_chain (lower_uri, lower_pin, upper_uri, upper_pin, "
        "                        predicate, lower_level, upper_level) "
        "SELECT DISTINCT l.from_uri, l.from_anchor, l.to_root, "
        "       CASE WHEN instr(l.to_uri, '#') > 0 "
        "            THEN substr(l.to_uri, instr(l.to_uri, '#') + 1) END, "
        "       l.predicate, lo.lvl, up.lvl "
        "  FROM links l "
        "  JOIN (%s) lo ON lo.uri = l.from_uri "
        "  JOIN (%s) up ON up.uri = l.to_root "
        " WHERE l.predicate IN (%s) AND lo.lvl > up.lvl"
        % (_LEVEL_SELECT, _LEVEL_SELECT,
           ",".join("'%s'" % p for p in CHAIN_PREDICATES)))
    # commit like every other relate post-pass (set_correspondence,
    # synthesize_concepts): the caller closes the connection without one, so an
    # uncommitted rebuild is silently discarded and the table keeps whatever it
    # held before -- which reads as "the chain did not change", not as a failure
    con.commit()
    return con.execute("SELECT count(*) FROM norm_chain").fetchone()[0]


# documents resolved to a rung, as a subquery: the level table expressed in SQL
# so the join happens in one statement rather than a row-at-a-time Python walk
_LEVEL_SELECT = "SELECT uri, CASE %s END AS lvl FROM documents WHERE %s" % (
    " ".join("WHEN source = '%s'%s THEN %d"
             % (source, "" if kind is None else " AND kind = '%s'" % kind, lvl)
             for (source, kind), lvl in NORM_LEVEL.items()),
    " OR ".join("source = '%s'" % source for source in
                dict.fromkeys(source for source, _ in NORM_LEVEL)))


def _bemyndigande_links(art):
    """The bemyndigande edges a föreskrift artifact carries: it is *meddelad* (issued)
    under one or more empowering SFS paragrafer, a fact that lives in metadata, not
    the body text, so the inline-link walk misses it. The edge points föreskrift ->
    SFS paragraf, anchored to the whole regulation (a föreskrift is issued under a
    paragraf as a whole), so the statute paragraf's page lists the föreskrifter
    issued under it. `text` carries the föreskrift's id for the margin display."""
    label = art.get("identifier") or local(art["uri"])
    return [(None, {"uri": uri, "predicate": "rpubl:bemyndigande", "text": label})
            for uri in art.get("metadata", {}).get("bemyndigande", [])]


# metadata relation-list keys -> the stable typed predicate each publishes.
# andradAv is the register inverse (the listed ändringsförfattning X ändrar
# this document); genomforDirektiv is the same predicate the förarbete
# implements-edges and the SFS genomförande layer use.
_RELATION_PREDICATES = (("andrar", "rpubl:andrar"),
                        ("upphaver", "rpubl:upphaver"),
                        ("genomfor", "rpubl:genomforDirektiv"),
                        ("andradAv", "rinfoex:andradAv"))


def relation_links(art):
    """The typed relation edges a document's metadata carries as plain uri
    lists: what it amends (`andrar`), replaces/repeals (`upphaver`), transposes
    (`genomfor`) and is amended by (`andradAv`, the amendment register's
    inverse). These are metadata, not body text, so the inline-link walk misses
    them. Field-driven: any source whose metadata stores uri lists under these
    keys contributes (today the föreskrift vertical). Unanchored -- the
    relation belongs to the document; `text` carries the document's own id so
    the target's mirror display can name it."""
    label = art.get("identifier") or local(art["uri"])
    return [(None, {"uri": uri, "predicate": pred, "text": label})
            for key, pred in _RELATION_PREDICATES
            for uri in art.get("metadata", {}).get(key) or []]


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
# legal definitions (the `definitions` table)
# --------------------------------------------------------------------------
#
# A defined term's begrepp page is an index of where the term is used, and the
# most useful thing it can show is what each act says the term *means*. The
# link alone cannot: "säkerhetsskyddslagen 1 kap. 1 § 1 st" tells a reader
# where to look, not what they would find. So relate stores the defining
# sentence beside the edge -- it is reading the artifact anyway, and a page that
# fetched it at render time would open one artifact per occurrence (a term
# defined in 100 acts, on every one of ~28,900 concept pages).
#
# The two corpora state a definition in different places, so each is read where
# it puts one:
#
#   SFS     an inline `dcterms:subject` term run over the definiendum's own span
#           (`sfs.begrepp`). The node around it is a whole stycke and often
#           holds more than the definition -- brottsbalken 10 kap. 8 § 1 st runs
#           "Fullgör man ej ... dömes för fyndförseelse till böter. Underlåter
#           man ..." -- so the unit stored is the *sentence* carrying the term.
#   eurlex  a definitions-article point, whose whole text is the definition
#           ("risk: risk för förlust eller störning orsakad av en incident.").


def _defining_sentence(runs, index):
    """The sentence of `runs` that carries the run at `index`.

    Offsets are exact -- `runs_text` concatenates runs with no separator, and
    `text.sentences` only strips, so every unit is a substring at a known
    position. `sentences` discards only a chunk carrying no letters, and the
    term run's own text carries some, so the chunk holding it always survives
    and some unit must cover it. Asserted rather than defaulted to the whole
    node: falling back silently would store exactly the untrimmed stycke this
    function exists to avoid, with nothing recorded (rule:fail-fast)."""
    body = text.runs_text(runs)
    start = len(text.runs_text(runs[:index]))
    at = 0
    for sentence in text.sentences(body):
        at = body.index(sentence, at)
        if at <= start < at + len(sentence):
            return sentence
        at += len(sentence)
    raise AssertionError(
        "no sentence covers the term run at offset %d of %r" % (start, body))


# a definition that has said all it has to say: its text closes on a sentence
# terminator. One that stops on a colon, or on nothing at all, is a lead-in and
# its body is the list underneath ("Med skatt avses i denna lag, om inte annat
# anges", uppbördslagen 1 §; "finansiellt företag: någon av följande enheter:",
# 32005L0068 art. 2.1 o).
_CLOSED = re.compile(r"[.!?][\"'’”»)\]]*\s*$")


def _with_sublist(node, lead):
    """`lead` extended with the node's sub-list, where the lead-in is open.

    Having children is *not* the test. 1 353 of 14 034 SFS definitions sit on a
    node that carries some, and on 245 of them the definition is already a whole
    sentence: brottsbalken 6 kap. 1 § states våldtäkt in 257 characters and then
    lists the acts it covers, and appending those turned the definition into
    1 185 characters of the whole paragraf -- with the "Lag (2026:852)."
    trailer wedged in the middle, since a node's own runs come before its
    descendants'. So the two shapes separate on whether the text closes."""
    return lead if _CLOSED.search(lead) or not node.get("children") \
        else text.node_text(node)


def _term_runs(runs, node, anchor, out, whole=None):
    """Every defined-term run in `runs`, with the text that states its
    definition. `whole` overrides the sentence pick for a carrier that *is* the
    definition and holds no other sentence -- a table row."""
    for i, run in enumerate(runs):
        if isinstance(run, dict) and run.get("kind") == "term" \
                and run.get("predicate") == "dcterms:subject" and "uri" in run:
            out.append((run["uri"], anchor, run.get("text") or "",
                        whole if whole is not None
                        else _with_sublist(node, _defining_sentence(runs, i))))


def _row_text(cells):
    """A table row as one line: "Småhus: En- och tvåbostadshus ...". 336 SFS
    definitions are written as a two-column table under an "I denna lag betyder"
    stem, and the term cell alone states nothing."""
    head, *rest = [text.runs_text(cell).strip() for cell in cells]
    return ": ".join([head, " ".join(r for r in rest if r)]) if any(rest) else head


def _walk_definitions(node, out, anchor=None):
    """Both carriers, in one walk: an inline term run (SFS) and a `defines`
    point (eurlex). The anchor is the nearest enclosing node id, exactly as
    `collect_links` attributes a citation -- a table row carries none of its
    own, and its definition still has to link somewhere."""
    if isinstance(node, dict):
        anchor = node.get("id") or anchor
        if node.get("defines"):
            # an eurlex definitions-article point is the definition whole, and
            # its own text stops at the colon when the definition is a sub-list
            out.append((begrepp_uri(node["defines"]), anchor, node["defines"],
                        _with_sublist(node, text.runs_text(node.get("text") or ""))))
        for key, value in node.items():
            if key == "text" and isinstance(value, list):
                _term_runs(value, node, anchor, out)
            elif key == "cells":
                # the row is the definition, not one sentence of it, so every
                # cell's term takes the whole row as its text
                row = _row_text(value)
                for cell in value:
                    _term_runs(cell, node, anchor, out, whole=row)
            else:
                _walk_definitions(value, out, anchor)
    elif isinstance(node, list):
        for item in node:
            _walk_definitions(item, out, anchor)


def definition_sentences(art):
    """(concept, anchor, term, sentence) for every legal definition the artifact
    states. Empty for a document that defines nothing, which is nearly all of
    them.

    Only the **Swedish** manifestation of an EU act contributes, the same rule
    `definition_links` applies: the begrepp namespace is Swedish, so an English
    act's terms are not concepts here. A sentence that came out empty is dropped
    -- a definition a reader cannot read is worse than a bare link."""
    if art.get("lang") not in (None, "swe"):
        return []
    out = []
    for nodes in text.body_sections(art):
        _walk_definitions(nodes, out)
    rows = [(concept, anchor, term.strip(), " ".join(sentence.split()))
            for concept, anchor, term, sentence in out if term.strip()]
    # A sentence that says nothing but the term quotes nothing: the source left
    # the definition body empty ("total tillåten fångstmängd (TAC): ",
    # 32015R0104 art. 3 f) or the SFS detector read a bare list fragment as a
    # term ("brott som avses i", brottsbalken 2 kap. 5 § 2 st). The row stays,
    # with an empty sentence -- the act does define the term and the concept
    # page still has to list it; there is just nothing to quote. Dropping the
    # row instead would have hidden 863 concepts' occurrences outright.
    return [(concept, anchor, term,
             "" if sentence.rstrip(".:,; ") == term.rstrip(".:,; ") else sentence)
            for concept, anchor, term, sentence in rows]


def concept_definitions(con, concept):
    """What every act that defines `concept` says it means: Swedish acts first,
    then the EU ones, each by its own citing name. That is the order the context
    rail already uses (`page.RAIL_SECTION_ORDER` puts `sfs` before `eurlex`), and
    a reader looking up a Swedish legal term reads the Swedish acts first. Each
    row is (uri, anchor, descriptive, term, sentence) -- `uri`+`anchor` is the
    link, `descriptive` the act's citing name."""
    return con.execute(
        "SELECT d.from_uri, d.anchor, doc.descriptive, d.term, d.sentence "
        "FROM definitions d JOIN documents doc ON doc.uri = d.from_uri "
        "WHERE d.concept = ? "
        "ORDER BY doc.source = 'eurlex', doc.descriptive, d.anchor",
        (concept,)).fetchall()


# --------------------------------------------------------------------------
# document rows
# --------------------------------------------------------------------------

# For these sources the catalog's naming IS labels' naming: label is
# `short_id` and title is `official_title`, from the same
# `labels.document_labels` call that stamps the descriptive column -- one
# authority, so the citation line and the page eyebrow cannot drift apart.
# Verified against the live catalog before the merge (2026-08-08): 0 of
# 2,463 sampled rows differed. Only `kind` stays per-source, as data.
# The bespoke builders below each say why their label/title deliberately
# differ from labels' forms.
_LABELLED_KIND = {
    # lag vs forordning rather than one 'law': a förordning is subordinate to
    # the lag that delegates to it, and collapsing the two made the norm
    # hierarchy unreadable from the catalog -- 2025:1506 and 2025:1507 are one
    # rung apart, not the same kind of thing (`labels.sfs_is_statute`)
    "sfs": lambda art, lb: ("lag" if labels.sfs_is_statute(
        lb.official_title, local(art["uri"])) else "forordning"),
    "forarbete": lambda art, lb: art.get("doctype", "forarbete"),
    "kommentar": lambda art, lb: "kommentar",
    "begrepp": lambda art, lb: "begrepp",
    "avg": lambda art, lb: art.get("org", "avg"),      # the organ (jo/jk/…)
    "rs": lambda art, lb: art.get("org", "rs"),        # the agency (fk/imy/…)
    "guidance": lambda art, lb: art.get("serie") or art["utgivare"],
    "lawreview": lambda art, lb: art["journal"],       # the journal (svjt/jp)
    "coe": lambda art, lb: art.get("doctype", "treaty"),
}


def _labelled_document(source, art, path):
    lb = labels.document_labels(source, art)
    return (art["uri"], source, _LABELLED_KIND[source](art, lb),
            lb.short_id, lb.official_title, str(path))


def dv_document(art, path):
    # bespoke: the catalog label is the WHOLE name-prefixed case label
    # ("Meteoriten (NJA 2025 s. 897)") that listings and every inbound citation
    # line print, where labels' short_id is the bare id ("NJA 2025 s. 897") for
    # the page eyebrow. Stamped onto the artifact at parse time
    # (build.dv_parse_run, via lib.casenaming.case_label), so the catalog stays
    # a pure consumer. labels.dv_fallback_label owns the pre-stamp fallback
    # chain (shared with labels._dv so the two never drift).
    label = labels.dv_fallback_label(art)
    return (art["uri"], "dv", "case", label, label, str(path))


def _eurlex_document(art, path):
    # bespoke: kind is the doctype (regulation/directive/judgment/treaty);
    # label is the CELEX (the short id citations use), where labels' short_id
    # is the printed designation ("(EU) 2016/679"). A judgment's
    # inbound-citation name is the case citation stamped at parse
    # ("C-311/18 (Schrems II)"), not its "Domstolens dom (...)" Formex title;
    # an act keeps its full title.
    label = art.get("celex") or local(art["uri"])
    title = (art.get("label") if art.get("doctype") == "judgment"
             else art.get("title")) or label
    return (art["uri"], "eurlex", art.get("doctype", "eurlex"),
            label, title, str(path))


def _foreskrift_document(art, path):
    # bespoke: kind is the författningssamling (fffs/nfs/…), label the short id
    # citations + the bemyndigande margin use ("FFFS 2013:10"). The title is
    # the artifact's own, where labels' official_title interpolates the
    # designation into it ("Skolverkets föreskrifter (SKOLFS 2024:598) om …").
    label = art.get("identifier") or local(art["uri"])
    title = art.get("metadata", {}).get("title") or label
    return (art["uri"], "foreskrift", art.get("fs", "foreskrift"),
            label, title, str(path))


def hudoc_document(art, path):
    # bespoke: label is the ECLI (a stable machine id; the itemid as fallback),
    # where labels' short_id is the application number ("no. 8906/19") the page
    # eyebrow shows.
    label = art.get("ecli") or art.get("itemid") or local(art["uri"])
    title = art.get("title") or label
    return (art["uri"], "hudoc", art.get("doctype", "case-law"),
            label, title, str(path))


def icrc_document(art, path):
    # bespoke: kind is the doctype (treaty/protocol/declaration), label the
    # treaty's full identifier ("Geneva Convention (I) on Wounded and Sick …"),
    # where labels' short_id is the curated abbreviation ("GK I").
    label = art.get("identifier") or ("ICRC " + art.get("number", ""))
    title = art.get("title") or label
    return (art["uri"], "icrc", art.get("doctype", "treaty"),
            label, title, str(path))


def untc_document(art, path):
    # bespoke: kind is the doctype (treaty/protocol), label the treaty title
    # (its identifier; the MTDSG id is the number), where labels' short_id is
    # the curated abbreviation ("CRC", "CMW").
    label = art.get("identifier") or ("MTDSG " + art.get("number", ""))
    title = art.get("title") or label
    return (art["uri"], "untc", art.get("doctype", "treaty"),
            label, title, str(path))


def icc_document(art, path):
    # bespoke: kind is the decision type (judgment/sentence/…), label the
    # DOCUMENT number ("ICC-01/05-01/13-1964", the citation form for the
    # specific decision), where labels' short_id is the CASE number the page
    # eyebrow shows. Title is the case name.
    label = art.get("docnumber") or local(art["uri"])
    title = art.get("title") or label
    return (art["uri"], "icc", art.get("doctype", "judgment"),
            label, title, str(path))


def icj_document(art, path):
    # kind is the decision type (judgment/advisory opinion/order); label is the
    # Court's own citing form ("ICJ 70 (Judgment, 1986-06-27)"), where labels'
    # short_id is the bare case number the page eyebrow shows. Title is the
    # case name, which is how every citation to an ICJ decision reads.
    label = art.get("identifier") or local(art["uri"])
    title = art.get("title") or label
    return (art["uri"], "icj", art.get("doctype", "dom"),
            label, title, str(path))


# the column `_expired_date` fills is compared against an ISO date, so only an
# ISO date may go into it
RE_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


#: the `expired` value for a document whose issuer states that it is superseded
#: but never when. Any date already past would do; this one reads as what it is
#: in a database dump and cannot collide with a real repeal date. The artifact
#: keeps the truth -- no `upphavd` -- so a page still prints no date it was
#: never told.
EXPIRED_UNDATED = "0001-01-01"


def _expired_date(art: dict) -> str | None:
    """The date a document stopped stating law, if its metadata declares one --
    else None. Stored on the documents row so that once the date has passed the
    document drops out of every *listing* of the corpus -- the browse trees
    (`facets._rows`), the feeds (`feeds.entries`) and search results
    (`search.REPEALED_IN_FORCE`) -- and off the context rail
    (`page._inbound_groups`, the I3 rule). It stays reachable by direct link,
    and stays *in* the search index carrying this date, so the query filter is
    the only thing between it and a reader: an advanced "search expired" option
    is a query change, not a reindex.

    Three kinds of document declare one, for the same reason. A statute names its
    repeal date (`rpubl:upphavandedatum`). A rättsligt ställningstagande is in
    force until the agency withdraws it, and a withdrawn one no longer says how
    the agency reads the rule -- which is the only reason it was on that
    paragraf's rail. Reading a paragraf whose rail listed thirteen
    ställningstaganden, twelve of them withdrawn, is what this covers. An EU act
    carries the date CELLAR says it stopped being in force, stamped on the
    artifact as a plain `expired` key (`cellar.notice_repeal_date`) --
    32016R0679 article 94 repealed 31995L0046 with effect from 2018-05-24.

    A date has to be an ISO one, because this column is compared against one.
    Where the issuer names a *successor* but no usable date, the document is
    expired all the same, at `EXPIRED_UNDATED`: it said that this wording was
    replaced, and reading the absence of a day as the absence of a repeal would
    leave a superseded wording listed as current -- the error this column exists
    to prevent. The EBA is the case it was added for; its version pages carry no
    repeal marker at all and their only date is an application date.

    That reaches one document outside guidance:
    `rs/kkv/2019:1`, which Konkurrensverket declares upphävt and replaced by
    2022:2 while dating it in prose ("20 oktober 2025"). Hiding it is what the
    rest of this docstring already argues for -- a withdrawn ställningstagande
    no longer says how the agency reads the rule, which is the only reason it
    was on that paragraf's rail.

    A withdrawal with neither a date nor a successor still stays listed: that is
    an issuer saying less than it knows, not a document we can place in time."""
    metadata = art.get("metadata", {})
    if metadata.get("status") == "upphävt":
        withdrawn = metadata.get("upphavd") or ""
        if RE_ISO_DATE.match(withdrawn):
            return withdrawn
        # an issuer that states *that* a document is superseded but never
        # *when*: the EBA's version pages carry no repeal marker at all, and
        # their only date is an application date. Reading the absence of a day
        # as the absence of a repeal would leave a superseded wording listed as
        # current, which is the error this column exists to prevent.
        return EXPIRED_UNDATED if (metadata.get("ersattAv")
                                   or metadata.get("ersattAvKalla")) else None
    declared = (art.get("expired")
                or metadata.get("properties", {}).get("rpubl:upphavandedatum"))
    return declared if declared and RE_ISO_DATE.match(declared) else None


def document_date(art: dict) -> str | None:
    """ONE date per document (ISO yyyy-mm-dd), for chronological ordering of
    listings and inbound references. This is a *projection*, not a key
    convention: the per-source keys deliberately name different events (a
    ruling's avgörandedatum, an agency decision's beslutsdatum, a väglednings
    antagen, a statute's utfärdandedatum, a föreskrifts tryck/ikraft dates, a
    treaty's opening/adoption/conclusion) and must not be collapsed onto one
    key -- a document has several dates; this chain just picks the one a
    listing sorts by. None when the artifact carries no date (the renderer
    sorts undated entries last)."""
    props = art.get("metadata", {}).get("properties", {})
    return (art.get("date") or art.get("avgorandedatum")
            or art.get("metadata", {}).get("beslutsdatum")
            or art.get("metadata", {}).get("antagen")
            or art.get("metadata", {}).get("utkomFranTryck")
            or props.get("rpubl:utfardandedatum")
            or props.get("rpubl:avgorandedatum")
            or props.get("rpubl:beslutsdatum"))


def _document_description(art, source):
    """A source's own one-line description of a document, for the browse listing --
    a court decision's sammanfattning (the referatrubrik that heads the entry after
    its number). None where a source has no such abstract, so the listing shows the
    short_title alone."""
    if source == "dv":
        return art.get("metadata", {}).get("sammanfattning")
    return None


_SNIPPET_LEN = 340


# node types that read as furniture, not prose: headings in both grammars,
# an EU act's preamble formalities ("med beaktande av …" citation nodes) and
# footnotes. The first *recital* is the act's own opening statement and wins.
_NOT_PROSE = frozenset({"heading", "citation", "note"})


def _node_text(node):
    return "".join(run if isinstance(run, str) else (run.get("text") or "")
                   for run in node.get("text") or []).strip()


def cut_snippet(text):
    """A snippet cut to one length, on a word boundary, with an ellipsis where
    it was cut. Relate stamps every `documents.snippet` through this, and
    /api/v1/card cuts a provision's own words the same way, so the two read
    alike in the same popover (rule:second-use-goes-to-lib)."""
    if len(text) > _SNIPPET_LEN:
        return text[:_SNIPPET_LEN].rsplit(" ", 1)[0] + " …"
    return text


def _prose_candidates(nodes):
    """Depth-first over an artifact tree: every non-furniture node whose text
    runs join to a real paragraph (>= 80 chars), uncut."""
    for node in nodes:
        if not isinstance(node, dict):
            continue
        kind = node.get("type") or ""
        if "rubrik" not in kind and kind not in _NOT_PROSE:
            text = _node_text(node)
            # an ISSN line is a författningssamling's masthead, not
            # prose; and a low alphanumerics-and-spaces ratio is OCR debris
            # off a scanned page (".-lascs.srii<~nt I J / …"), not a
            # paragraph -- digits stay welcome, lagtext is full of them
            if (len(text) >= 80 and not text.startswith("ISSN ")
                    and sum(c.isalnum() or c == " " for c in text)
                    / len(text) >= .8):
                yield text
        yield from _prose_candidates(node.get("children") or [])


def first_prose(art):
    """The document's own opening prose: the first non-furniture node whose
    text runs join to a real paragraph (>= 80 chars), cut at a word boundary
    around 340 chars. Structure-generic -- a förarbete lands on its first
    running paragraph, a wiki concept on its defining paragraph (`body` is
    the wiki artifacts' tree). None when the artifact opens with nothing
    prose-like (scanned page-image documents, bare registries)."""
    for prose in _prose_candidates(art.get("structure") or
                                   art.get("body") or []):
        return cut_snippet(prose)
    return None


def _first_of_type(nodes, kind):
    for node in nodes:
        if isinstance(node, dict):
            if node.get("type") == kind:
                return node
            found = _first_of_type(node.get("children") or [], kind)
            if found:
                return found
    return None


def _paragraf_prose(art):
    """An författning's opening as a reader cites it: the first paragraf's
    first stycke, led by its designation -- "1 kap. 1 § Fast egendom är
    jord. …". The walk to the paragraf skips every heading by construction;
    a paragraf carries its stycken as children (or, in older shapes, its own
    text runs)."""
    par = _first_of_type(art.get("structure") or [], "paragraf")
    if not par:
        return None
    stycke = _first_of_type(par.get("children") or [], "stycke")
    body = _node_text(stycke) if stycke else _node_text(par)
    if not body:
        return None
    # a stycke that introduces a list carries the items as punkt children --
    # quote the first item and say with an ellipsis that the list goes on
    punkt = _first_of_type((stycke or par).get("children") or [], "punkt")
    if punkt and _node_text(punkt):
        body = "%s %s …" % (body, _node_text(punkt))
    where = pinpoint_label(par.get("id") or "")
    return cut_snippet(("%s %s" % (where, body)) if where else body)


def _word_cap(text, words=50):
    """A narrative snippet capped at `words` words, ellipsis when cut."""
    parts = text.split()
    if len(parts) <= words:
        return text
    return " ".join(parts[:words]) + " …"


def _numbered_ground(art):
    """An EU court decision's own opening: its first numbered paragraph --
    "Begäran om förhandsavgörande avser tolkningen av artikel 4.1 …" --
    capped at 50 words. The keyword strings and quoted legislation that
    precede it (a judgment quotes whole recitals) are passed over."""
    def walk(nodes):
        for node in nodes:
            if isinstance(node, dict):
                if node.get("type") == "paragraph" and node.get("num"):
                    body = _node_text(node)
                    # an old judgment numbers its section headings too
                    # ("Facts and procedure") -- a ground is a sentence
                    if len(body) >= 40:
                        return _word_cap(body)
                found = walk(node.get("children") or [])
                if found:
                    return found
        return None
    return walk(art.get("structure") or [])


def _recital_prose(art):
    """An EU act's own opening statement: the first preamble recital, led by
    its number -- "(1) Skyddet för fysiska personer …"."""
    recital = _first_of_type(art.get("structure") or [], "recital")
    if not recital:
        return None
    body = _node_text(recital)
    if not body:
        return None
    num = recital.get("num")
    return cut_snippet(("(%s) %s" % (num, body)) if num else body)


# how an international court's decision opens before any substance: bench
# rosters, composition lines and the ICC's cover-page furniture ("Decision
# to be notified …", "SITUATION IN …") -- all census-found, none prose
_ROSTER = re.compile(r"^(Before\s*:|Before\s+(?:Judge|President)\b"
                     r"|Present\s*:"
                     r"|Present\s+(?:President|Vice-President|Judges?)\b"
                     r"|Composed\b|Composée\b"
                     r"|The Court,|The International Court of Justice,"
                     r"|Decision to be notified|Judgment to be notified"
                     r"|To be notified"
                     r"|SITUATION IN |IN THE CASE OF )")


def _document_snippet(art, source):
    """What the details panel opens with, per what each source actually has:
    the dv sammanfattning; an författning's (SFS or föreskrift) first
    paragraf with its "1 §"/"1 kap. 1 §" designation; an EU act's first
    recital with its "(1)" and an EU court decision its first numbered
    ground; a hudoc case's conclusions ("Violation of P1-1"
    -- its body text opens with procedural boilerplate); an ICC/ICJ
    decision's first paragraph past the bench roster; a journal article's
    first paragraph capped at 50 words; and the opening prose for everyone
    else."""
    described = _document_description(art, source)
    if described:
        return described
    if source in ("sfs", "foreskrift"):
        return _paragraf_prose(art) or first_prose(art)
    if source == "hudoc":
        return "; ".join(
            art.get("metadata", {}).get("conclusions") or []) or None
    if source == "lawreview":
        # the article's own first paragraph, capped at 50 words -- long
        # enough to say what it is about, short enough that mined OCR text
        # cannot ramble (the garbage gate in _prose_candidates still refuses
        # debris outright)
        for prose in _prose_candidates(art.get("structure") or []):
            return _word_cap(prose)
        return None
    if source in ("icc", "icj"):
        for candidate in _prose_candidates(art.get("structure") or []):
            if not _ROSTER.match(candidate):
                return cut_snippet(candidate)
        return None
    if source == "eurlex":
        # case law opens on its first numbered ground; only the *acts* take
        # the recital path -- a judgment quotes whole recitals of the act it
        # interprets, and the recital finder would happily serve those.
        # Case law is the CELEX's own sector: a 6-leading number, AG
        # opinions included
        if art["uri"].rsplit("/", 1)[-1].startswith("6"):
            ground = _numbered_ground(art)
            if ground:
                return ground
        else:
            recital = _recital_prose(art)
            if recital:
                return recital
    # a pre-Formex EU act (and the odd treaty) opens with its own title as a
    # plain text node -- a snippet that echoes the title says nothing the
    # panel does not already show, so skip past it to the next paragraph
    title = (art.get("title") or "").strip().casefold()
    for prose in _prose_candidates(art.get("structure") or
                                   art.get("body") or []):
        if title and prose[:60].strip().casefold() == title[:60].strip():
            continue
        return cut_snippet(prose)
    return None


def _document_publisher(art: dict) -> str | None:
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


# source -> its document-row builder. Module-level so `document_row` (~500k
# calls per full relate) doesn't rebuild this dispatch dict on every call.
_DOCUMENT_BUILDERS = {
    source: partial(_labelled_document, source) for source in _LABELLED_KIND
} | {
    "dv": dv_document, "eurlex": _eurlex_document,
    "foreskrift": _foreskrift_document, "hudoc": hudoc_document,
    "icrc": icrc_document, "untc": untc_document, "icc": icc_document,
    "icj": icj_document,
}


def document_row(art, path, source):
    return _DOCUMENT_BUILDERS[source](art, path)


# --------------------------------------------------------------------------
# rebuild
# --------------------------------------------------------------------------

def content_hash(raw: bytes) -> str:
    """The change-detection key for an artifact: sha256 of its on-disk bytes.
    Stored on the documents row so relate (and, via the row, index) can skip an
    artifact whose bytes are unchanged since last time."""
    return hashlib.sha256(raw).hexdigest()


def _drop_document(con, uri):
    """Remove a document and everything keyed off it: its outbound links, its
    EU-act lineage and its concept redirects."""
    con.execute("DELETE FROM links WHERE from_uri = ?", (uri,))
    con.execute("DELETE FROM definitions WHERE from_uri = ?", (uri,))
    con.execute("DELETE FROM directive_correspondence WHERE new_uri = ?", (uri,))
    con.execute("DELETE FROM documents WHERE uri = ?", (uri,))
    con.execute("DELETE FROM concept_redirect WHERE concept = ?", (uri,))


def _index_document(con, art, path, source):
    """(Re)write one document's rows: its documents row and outbound links,
    replacing any prior version keyed by the same uri."""
    uri = art["uri"]
    con.execute("DELETE FROM links WHERE from_uri = ?", (uri,))
    # what this act says its defined terms mean -- the begrepp page's reading
    # matter, stored beside the edge that points at it (see definition_sentences)
    con.execute("DELETE FROM definitions WHERE from_uri = ?", (uri,))
    con.executemany(
        "INSERT INTO definitions VALUES (?,?,?,?,?)",
        [(concept, uri, anchor, term, sentence)
         for concept, anchor, term, sentence in definition_sentences(art)])
    # the EU-act lineage the act's own jämförelsetabell states, extracted at
    # parse time into the artifact (eurlex/correspond.py). Written here rather
    # than in a cross-document post-pass because that would mean re-reading
    # every one of the ~64k eurlex artifacts on every relate to find the ~2%
    # that carry one; here the artifact is already open and the rows are
    # incremental like the links beside them.
    con.execute("DELETE FROM directive_correspondence WHERE new_uri = ?", (uri,))
    con.executemany(
        "INSERT INTO directive_correspondence VALUES (?,?,?,?,?,?)",
        [(uri, e["newArticle"], e["oldLaw"], e["oldArticle"],
          e.get("newPinpoint"), e.get("oldPinpoint"))
         for e in art.get("correspondence") or []])
    row = document_row(art, path, source)        # (uri, source, kind, label, title, path)
    lb = labels.document_labels(source, art)
    # a treaty's artifact title is the bare CELEX (no extractable heading); the
    # curated name computed by labels is the reader-facing heading instead (E1),
    # keeping the listing display in step with the page header
    display = (lb.official_title if source == "eurlex" and art.get("doctype") == "treaty"
               else display_title(art, row[4]))
    con.execute(
        "INSERT OR REPLACE INTO documents "
        "(uri, source, kind, label, title, path, source_url, content_hash, "
        " expired, display, date, publisher, descriptive, "
        " short_id, short_title, description, snippet) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (*row, art.get("source_url"),
         None,                 # content_hash filled by the caller (holds bytes)
         _expired_date(art),
         display,                                 # the reader-facing heading
         document_date(art), _document_publisher(art),
         # the reader-facing name forms the listings + inbound panels use (labels;
         # I1/I2): descriptive citing form, the bare id, the short name, and the
         # source's own one-line description (a case's sammanfattning)
         lb.descriptive_label, lb.short_id, lb.short_title,
         _document_description(art, source),
         _document_snippet(art, source)))
    # the metadata producers describe the document, not a place in it, so they
    # pad the body walk's (anchor, page, run) shape with a pageless entry
    edges = artifact_links(art) + [
        (anchor, None, run)
        for anchor, run in (subject_links(art) + definition_links(art)
                            + _bemyndigande_links(art)
                            + _sfs_authority_links(art) + relation_links(art)
                            + curated_links(art))]
    rows = [(uri, anchor, run.get("predicate", "dcterms:references"),
             run["uri"], strip_fragment(run["uri"]), run.get("text"), page)
            for anchor, page, run in edges]
    con.executemany(
        "INSERT INTO links (from_uri, from_anchor, predicate, to_uri, to_root, "
        "text, from_page) VALUES (?,?,?,?,?,?,?)", rows)
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
    widen_to_root_index(con)     # build-cost work belongs here, not in serving
    widen_docs_source_index(con)   # ... and so does its documents-side sibling
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
        # them like file_fingerprint does and skip the read + hash entirely. size 0
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
    number of variant forms folded away.

    The `definitions` rows fold with the links they sit beside. Left behind,
    they strand on a page nobody renders: the wiki page *Risken* absorbs the
    form *Risk*, and *Risk*'s 31 legaldefinitioner then have no page while the
    page has no definitions. 1 077 rows over 494 concepts were in that state."""
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
                con.execute("UPDATE definitions SET concept = ? WHERE concept = ?",
                            (canon_uri, v_uri))
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
            con.execute("UPDATE definitions SET concept = ? WHERE concept = ?",
                        (canon_uri, variant))
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
    partial, sfs_pinpoint) -- sfs_pinpoint the stycke/punkt within the paragraf
    ("S1", "S3N2"; '' = the whole paragraf), already existence-checked against
    the published law by the resolver. Stored twice: in `genomforande` (the
    statute paragraf's margin display, with provenance) and as an sfs-paragraf ->
    directive-article edge in `links` (so the directive article's inbound shows
    the implementing statute, reusing the generic inbound machinery). Column
    names are spelled out because a migrated catalog has sfs_pinpoint appended
    last while a fresh one follows the schema -- positional VALUES would
    misalign on one of them."""
    con.execute("DELETE FROM genomforande")
    con.execute("DELETE FROM links WHERE predicate = 'rpubl:genomforDirektiv' "
                "AND from_uri IN (SELECT uri FROM documents WHERE source='sfs')")
    con.executemany(
        "INSERT INTO genomforande (sfs_uri, sfs_anchor, directive, article, "
        "prop_uri, prop_label, pinpoint, partial, sfs_pinpoint) "
        "VALUES (?,?,?,?,?,?,?,?,?)", rows)
    con.executemany(
        "INSERT INTO links (from_uri, from_anchor, predicate, to_uri, to_root, "
        "text) VALUES (?,?,?,?,?,?)",
        [(sfs_uri, anchor, "rpubl:genomforDirektiv",
          directive + "#" + article, directive, prop_label)
         for (sfs_uri, anchor, directive, article, prop_uri,
              prop_label, pin, partial, sfs_pin) in rows])
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


def fk_kommentar_all(con, uris=None):
    """Every FK commentary row, newest proposition first -- the renderer builds
    its per-(statute, anchor) rail index from this in one pass. `uris` scopes
    the rows to those host statutes (the targeted generate renders a handful of
    pages, not the corpus); None keeps the full-corpus read."""
    if uris is not None and not uris:
        return []
    where = ("" if uris is None
             else " WHERE sfs_uri IN (%s)" % ",".join("?" * len(uris)))
    return con.execute(
        "SELECT sfs_uri, sfs_anchor, prop_uri, prop_label, prop_date, page, text "
        "FROM fk_kommentar%s ORDER BY prop_date DESC, prop_uri" % where,
        list(uris or ())).fetchall()


# how many generations back a paragraf's article set is carried. Two hops is
# what the procurement chain needs (2014/24 -> 2004/18 -> 92/50 & 93/36-38) and
# is where the relation stops being one a reader would accept unexplained: by
# the third generation the article has usually been split and merged past
# recognition, and art. 2 (definitions) in four generations would swamp the rail.
# three generations: a LOU paragraf's 2014/24 article reaches 2004/18 (1),
# the 1992-93 codifications (2) and the original 1971/1977 directives (3) --
# the generation Dundalk and SIAC Construction cite. The walk is BFS over
# explicit correlation-table pairs, so depth only follows tables the recasts
# themselves published.
LINEAGE_DEPTH = 3


def _covers(atom, pinpoint):
    """Whether article atom `atom` covers `pinpoint`: "57" covers "57.4" and
    itself; "57.4" covers "57.4.a"; "57" does not cover "570" (the dot bound)."""
    return pinpoint == atom or pinpoint.startswith(atom + ".")


def _deepest_cover(pairs, pinpoint):
    """Of the `(atom, payload)` pairs whose atom covers `pinpoint`, the
    payloads at the deepest cover level; empty when nothing covers. The one
    place the "most precise claim wins" rule is spelled out -- shared by the
    lineage walk (`predecessor_atoms`) and the rail assignment
    (`caselaw_anchored`), which must agree on it."""
    covering = [(atom, payload) for atom, payload in pairs
                if _covers(atom, pinpoint)]
    if not covering:
        return []
    deepest = max(atom.count(".") for atom, _payload in covering)
    return [payload for atom, payload in covering
            if atom.count(".") == deepest]


def _atomize(pinpoint, article):
    """The article atoms a genomförande pinpoint claims, dot-normalized:
    "57.2, 57.4 a" -> ["57.2", "57.4.a"]. An empty pinpoint claims the whole
    article."""
    atoms = [p.strip().replace(" ", ".")
             for p in (pinpoint or "").split(",") if p.strip()]
    return atoms or [article]


def predecessor_atoms(con, act, atom, depth=LINEAGE_DEPTH):
    """The `(act uri, atom, hops)` an EU-act article atom (a dotted pinpoint,
    "57.4", or a bare article) traces back to through the recasts' own
    correlation tables, breadth-first, `depth` generations deep.

    Each hop keeps as much precision as the table offers, per predecessor act:
    the rows *inside* the atom's claim if there are any (57 -> the 45.1 the
    table itemizes), else the deepest rows *covering* it (57.1 through the bare
    57-row when nothing finer exists), else -- the atom is finer than every row
    for its article, like 57.4 against a table that only lists 57.1 -- the
    plain old-article numbers, precision the table simply does not have.
    Excludes the starting pair; yields each ancestor atom once at its
    shallowest hop, deterministically ordered (the query's ORDER BY), because
    the rail renders what it is handed and an unordered walk would churn the
    page."""
    seen, frontier, out = {(act, atom)}, [(act, atom)], []
    for hop in range(1, depth + 1):
        found = []
        for uri, a in frontier:
            per_act = {}
            for old_uri, old_article, new_pin, old_pin in con.execute(
                    "SELECT old_uri, old_article, "
                    "  COALESCE(NULLIF(new_pinpoint, ''), new_article), "
                    "  COALESCE(NULLIF(old_pinpoint, ''), old_article) "
                    "FROM directive_correspondence "
                    "WHERE new_uri = ? AND new_article = ? "
                    "ORDER BY old_uri, old_pinpoint", (uri, a.split(".", 1)[0])):
                per_act.setdefault(old_uri, []).append(
                    (old_article, new_pin.replace(" ", "."),
                     old_pin.replace(" ", ".")))
            for old_uri, rows in per_act.items():
                inside = [op for _oa, np, op in rows if _covers(a, np)]
                if not inside:
                    inside = (_deepest_cover([(np, op) for _oa, np, op in rows],
                                             a)
                              or sorted({oa for oa, _np, _op in rows}))
                found.extend((old_uri, anc) for anc in inside)
        frontier = []
        for pair in found:
            if pair in seen:
                continue
            seen.add(pair)
            frontier.append(pair)
            out.append((*pair, hop))
        if not frontier:
            break
    return out


# a statute anchor's position in the act (K2aP7b -> kapitel (2, "a"),
# paragraf (7, "b")); what "the first paragraf" means when a citation must
# choose between several matching genomförande claims
_ANCHOR_ORD = re.compile(r"(?:K(\d+)([a-z]*))?P(\d+)([a-z]*)$")


def _anchor_order(anchor):
    m = _ANCHOR_ORD.fullmatch(anchor)
    assert m, f"unorderable genomförande anchor {anchor!r}"
    return (int(m[1] or 0), m[2] or "", int(m[3]), m[4] or "")


def caselaw_anchored(con, sfs_uri, live=None):
    """The statute's whole EU case-law rail, assigned in one pass:
    `{sfs_anchor: [((case uri, label, descriptive, date), {(act uri, cited
    pinpoint, transposed atom, hops), ...}), ...]}`, cases newest first.

    `live`, when given, is the set of anchors the rendered consolidation
    actually has; genomförande claims outside it are skipped, so a claim
    following the proposition's original numbering (LOU's 22 kap. tillsyn,
    renumbered away in 2021) cannot swallow a case into a paragraf that no
    longer exists -- the citation cascades to the article family's first
    live claimant instead.

    A judgment attaches to the paragraf whose genomförande pinpoint matches
    its citation most precisely: a case on artikel 57.4 belongs next to the
    paragraf transposing 57.4, not next to all seven transposing some part of
    artikel 57. Concretely, per cited act-fragment: the deepest claim covering
    the citation wins; with no covering claim (the citation is coarser than
    every claim, or names a punkt nobody claims) it falls back to the claims
    on the same article; ties -- several paragrafer claiming the same
    pinpoint -- go to a direct claim over an inherited one, then to the first
    paragraf in statute order. Claims are widened through the directive
    lineage (`predecessor_atoms`), so a Teckal citing 93/36 still reaches the
    paragraf transposing 2014/24 artikel 12. Judgments are the sector-6
    CJ/TJ/FJ documents -- an AG opinion (CC) or order (CO/TO) is not settled
    practice and stays out."""
    entries = []               # (act, atom, anchor, transposed atom, hops)
    for anchor, act, article, pinpoint in con.execute(
            "SELECT DISTINCT sfs_anchor, directive, article, "
            "  COALESCE(pinpoint, '') FROM genomforande "
            "WHERE sfs_uri = ? ORDER BY sfs_anchor, directive, article",
            (sfs_uri,)):
        if live is not None and anchor not in live:
            continue
        entries.extend((act, atom, anchor, atom, 0)
                       for atom in _atomize(pinpoint, article))
    seen = {(act, atom, anchor) for act, atom, anchor, _t, _h in entries}
    for act, atom, anchor, _t, _h in list(entries):
        for old_uri, old_atom, hop in predecessor_atoms(con, act, atom):
            if (old_uri, old_atom, anchor) in seen:
                continue
            seen.add((old_uri, old_atom, anchor))
            entries.append((old_uri, old_atom, anchor, atom, hop))
    by_act = {}
    for entry in entries:
        by_act.setdefault(entry[0], []).append(entry)
    assigned = {}              # anchor -> {case uri: (row, {provenance})}
    for act, ents in sorted(by_act.items()):
        cited = {}             # fragment -> {case row}
        for to_uri, uri, label, descriptive, case_date in con.execute(
                "SELECT l.to_uri, d.uri, d.label, d.descriptive, d.date "
                "FROM links l JOIN documents d ON d.uri = l.from_uri "
                "WHERE l.to_uri > ? || '#' AND l.to_uri < ? || '$' "
                "  AND d.source = 'eurlex' "
                "  AND substr(d.uri, instr(d.uri, '/celex/') + 7, 1) = '6' "
                "  AND substr(d.uri, instr(d.uri, '/celex/') + 12, 2) IN "
                "      ('CJ', 'TJ', 'FJ')", (act, act)):
            cited.setdefault(to_uri.partition("#")[2], set()).add(
                (uri, label, descriptive, case_date))
        for fragment, cases in sorted(cited.items()):
            candidates = _deepest_cover([(e[1], e) for e in ents], fragment)
            if not candidates:
                candidates = [e for e in ents if e[1].split(".", 1)[0]
                              == fragment.split(".", 1)[0]]
                if not candidates:
                    continue
            _act, _atom, anchor, transposed, hop = min(
                candidates, key=lambda e: (e[4], _anchor_order(e[2])))
            slot = assigned.setdefault(anchor, {})
            for row in cases:
                slot.setdefault(row[0], (row, set()))[1].add(
                    (act, fragment, transposed, hop))
    return {anchor: sorted(cases.values(),
                           key=lambda r: (r[0][3] or "", r[0][0]), reverse=True)
            for anchor, cases in assigned.items()}


def dangling_targets(con, prefix):
    """Documents the corpus *cites* but does not *hold*: `(uri, links, citing
    documents)` for every link target whose root uri starts with `prefix` and
    has no row in `documents`, most-cited first (ties broken by uri, so a run
    is reproducible).

    The corpus knows its own gaps: it is the want-list for a targeted harvest,
    computed from the citation graph rather than guessed. Its first use is the
    repealed EU acts -- the sector-3 stock came from a CELLAR bulk dump, which
    ships only acts in force, so every directive a judgment interprets and time
    has since replaced is cited from thousands of places and held nowhere."""
    return con.execute(
        "SELECT l.to_root, COUNT(*), COUNT(DISTINCT l.from_uri) "
        "FROM links l LEFT JOIN documents d ON d.uri = l.to_root "
        "WHERE l.to_root LIKE ? AND d.uri IS NULL "
        "GROUP BY l.to_root ORDER BY COUNT(*) DESC, l.to_root",
        (prefix.replace("%", "") + "%",)).fetchall()


def dangling_anchors(con, sources):
    """Links whose *fragment* names no node in the document they point at:
    `(from_uri, to_uri, count)`, most-cited first, over targets whose `source`
    is in `sources`.

    `dangling_targets` above answers the other half -- a document the corpus
    cites and does not hold. This one is about a document it *does* hold, cited
    at a provision that is not in it, which is the failure a link count cannot
    show: the link exists, the target exists, and the anchor goes nowhere.

    It generalises `wiki.parse.dangling_anchors` (which asks the same question
    of one kommentar and its host act) to the whole citation graph, because the
    same defect turned up far from the commentary layer: 126 treaty references
    pointed at an `#A42` on a Hague Convention that anchors its Regulations'
    articles under `#Annex42`, and every count involved looked healthy. (The
    curated table now targets the 1907 Convention, `ext/icrc/195`, so that exact
    uri no longer occurs -- the shape of the failure is the point.)

    `sources` is not a convenience filter: the audit is only *answerable* for a
    source whose page offers exactly the anchors its artifact carries. The
    others mint anchors at render time that no `structure` node holds -- sfs a
    change-act anchor per amendment (`1999:1229#L2007:1419`), eurlex an article
    and stycke alias (`32009R1107#29.6`), forarbete a page marker
    (`prop/1975:103#sid355`), coe a sub-paragraph pinpoint (`coe/005#A5P1Ld`),
    and `Toc` a generated anchor for a heading with no id -- and asked of every
    source it returns 1 612 832 pairs, a number nobody can act on rather than a
    finding. The caller names the sources it can answer for
    (`build.ANCHOR_EXACT`), which is also what keeps the pass cheap: only those
    targets' artifacts are read, each once.
    """
    root = data_root(con)
    # streamed, not fetched: the join covers every anchored link in the corpus
    # (6.9 million of them here), and materialising them costs gigabytes for a
    # loop that reads each row once and keeps only the misses
    nodes, out = {}, collections.Counter()
    for from_uri, to_uri, path in con.execute(
            "SELECT l.from_uri, l.to_uri, d.path FROM links l "
            "JOIN documents d ON d.uri = l.to_root "
            "WHERE instr(l.to_uri, '#') > 0 AND d.path <> '' "
            "AND d.source IN (%s)" % ",".join("?" * len(sources)),
            tuple(sources)):
        if path not in nodes:
            nodes[path] = _node_ids(compress.read_json(root / path))
        if to_uri.split("#", 1)[1] not in nodes[path]:
            out[(from_uri, to_uri)] += 1
    return [(from_uri, to_uri, count)
            for (from_uri, to_uri), count
            in sorted(out.items(), key=lambda item: (-item[1], item[0]))]


def _node_ids(art):
    """Every anchor a document offers, at any depth."""
    found = set()
    stack = list(art.get("structure") or [])
    while stack:
        node = stack.pop()
        if node.get("id"):
            found.add(node["id"])
        stack.extend(node.get("children") or [])
    return found


def genomfor_for(con, sfs_uri, anchor):
    """The EU directive articles a statute paragraf transposes, for its margin:
    (directive, article, prop_uri, prop_label, pinpoint, partial, sfs_pinpoint).
    sfs_pinpoint narrows the claim to a stycke/punkt of the paragraf ("S1",
    "S3N2"); empty means the whole paragraf."""
    return con.execute(
        "SELECT directive, article, prop_uri, prop_label, pinpoint, partial, "
        "COALESCE(sfs_pinpoint, '') "
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
# typed relations with their own dedicated displays are kept out of the
# generic "Hänvisat till av" citation panel (and its count): bemyndigande has
# the statute-paragraf margin, upphäver the target's "Upphävs eller ersätts
# av" group, andrar/andradAv the amendment register. genomforDirektiv stays
# in: the directive page's inbound panel is exactly where its transposing
# documents (förarbete implements-edges, föreskrifter) belong.
_NOT_TYPED = (" AND l.predicate NOT IN ('rpubl:bemyndigande', 'rpubl:andrar',"
              " 'rpubl:upphaver', 'rinfoex:andradAv')")


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
           "WHERE l.to_uri = ?" + _NOT_SELF + _NOT_TYPED
           + " AND d.source <> 'kommentar' "
           "GROUP BY l.from_uri, l.from_anchor "
           "ORDER BY d.source, d.label, l.from_anchor")
    if limit is not None:
        sql += " LIMIT %d" % limit
    return con.execute(sql, (uri,)).fetchall()


# A pinpointed citation ("1 kap. 18 § lagen (2016:1145) om offentlig upphandling")
# yields two link rows from the same spot: the pinpoint (…#K1P18) and the bare SFS
# number (…/2016:1145). The bare half says nothing the pinpoint does not, and it
# made the act's whole-document panel read as if half the corpus cited the law as
# such (S2). Drop a whole-document row whenever the citing *document* reaches into
# the document anywhere -- that document's reading of the act is already shown, in
# the rail of each paragraf it pinpoints, and matching only the same citing spot
# still left Brottsbalken with 4 899 whole-document citers where 1 804 name the act
# and nothing inside it (R4). A less-cited act barely notices: 1999:175 goes from
# 72 to 59, so the whole-document rail keeps its use where it has one.
_SUPERSEDED_BY_PINPOINT = (
    " AND NOT EXISTS (SELECT 1 FROM links p WHERE p.from_uri = l.from_uri"
    " AND p.to_root = l.to_root AND p.to_uri <> l.to_uri)")


def inbound_collapsed(con, uris, exclude_from=(), whole_document=False):
    """Documents citing exactly `uris` -- one target, or the several that share
    one rail panel (a paragraf and its first stycke, C2), collapsed together so
    a document citing both is still one line. One row per citing *document* (not
    per pinpoint) as (from_uri, label, title, source, kind, date, anchors) -- the
    grain the "Hänvisat till av" panel renders, so a förarbete citing from a
    dozen avsnitt is one line whose `anchors` (comma-joined, NULL pinpoints
    dropped) the renderer turns into a pinpoint list. Each anchor is written
    `id@page`, the page empty where the citing document has none: the two travel
    as one field because two GROUP_CONCATs of the same group are not promised to
    agree on order. The row's last column is the citer's `source_url` -- set
    for nearly every source, since the catalog records each document's
    publisher page. The renderer links it only for a citer style marked
    `external` (a tidskriftsartikel, which has no page of its own on this
    site); every other line keeps linking the local page.
    Self-citations, kommentar and bemyndigande excluded, plus any
    `exclude_from` uris (a statute's own förarbeten, shown once in their
    preparatory-works role instead).

    `whole_document` says `uris` are documents, not nodes inside one, and drops
    the citing spots that also pinpoint into them (`_SUPERSEDED_BY_PINPOINT`)."""
    assert not isinstance(uris, str), "inbound_collapsed takes a sequence of uris"
    params = list(uris)
    excl = ""
    if exclude_from:
        excl = " AND l.from_uri NOT IN (%s)" % ",".join("?" * len(exclude_from))
        params.extend(exclude_from)
    targets = " AND l.to_uri IN (%s)" % ",".join("?" * len(uris))
    sql = ("SELECT l.from_uri, d.label, d.title, d.source, d.kind, d.date, "
           "GROUP_CONCAT(DISTINCT l.from_anchor || '@' || "
           "IFNULL(l.from_page, '')), d.descriptive, d.source_url "
           "FROM links l JOIN documents d ON d.uri = l.from_uri "
           "WHERE 1" + targets + _NOT_SELF + _NOT_TYPED
           + " AND d.source <> 'kommentar'" + excl
           + (_SUPERSEDED_BY_PINPOINT if whole_document else "")
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


def upphaver_inbound(con, uri):
    """The regulations whose own text says they replace or repeal `uri` -- the
    inbound side of the rpubl:upphaver edge: (from_uri, label, title), one per
    replacing regulation. Drives the target page's 'Upphävs eller ersätts av'
    group (the wording matches the evidence: the extraction reads the
    'ersätter/upphäver …' clause, which conflates the two)."""
    return con.execute(
        "SELECT DISTINCT l.from_uri, d.label, d.title "
        "FROM links l JOIN documents d ON d.uri = l.from_uri "
        "WHERE l.to_uri = ? AND l.predicate = 'rpubl:upphaver' "
        "ORDER BY d.label", (uri,)).fetchall()


def andrar_inbound(con, uri):
    """The regulations whose own text says they amend `uri` -- the inbound side
    of the rpubl:andrar edge: (from_uri, label, title), one per amending
    regulation.

    A base regulation otherwise learns it was amended only from its own
    harvest record's amendment register, which is whatever the agency chose to
    list on its landing page. SJÖFS 2005:25 has an empty register while SJÖFS
    2006:39 says in its own title that it amends it, so the base page said
    nothing about having been changed at all."""
    return con.execute(
        "SELECT DISTINCT l.from_uri, d.label, d.title "
        "FROM links l JOIN documents d ON d.uri = l.from_uri "
        "WHERE l.to_uri = ? AND l.predicate = 'rpubl:andrar' "
        "ORDER BY d.label", (uri,)).fetchall()


def upphaver_targets(con):
    """Every uri some other document's text repeals or replaces (the target
    side of all rpubl:upphaver edges) -- what the föreskrift browse listing
    subdues as no longer in force. The evidence is the replacing documents'
    own repeal clauses; there is no authoritative status field."""
    return {r[0] for r in con.execute(
        "SELECT DISTINCT to_uri FROM links WHERE predicate = 'rpubl:upphaver'")}


def andrar_edges(con):
    """amending uri -> amended uri for every rpubl:andrar edge -- what the
    föreskrift browse uses to nest each ändringsförfattning under its base
    regulation. A document amending several picks the first (the browse nests
    it once; the others still carry the typed edge on their own pages)."""
    edges = {}
    for from_uri, to_uri in con.execute(
            "SELECT from_uri, to_uri FROM links "
            "WHERE predicate = 'rpubl:andrar' ORDER BY from_uri, to_uri"):
        edges.setdefault(from_uri, to_uri)
    return edges


def document_inbound_count(con: sqlite3.Connection, root_uri: str) -> int:
    """How many (citing document, pinpoint) entries cite a document *as a whole*
    -- any of its fragments or its bare uri. The 'most-hänvisade' authority
    signal (search ranking, the API's headline count), broader than
    `inbound_count`, which counts one exact uri. Self-citations excluded.

    Answered from the `inbound_count` column relate stamps: the live count
    walks the to_root index range, and the ECHR's is 1.4M entries -- tens of
    seconds cold on prod's disk, per pinned search hit. A catalog no relate
    has stamped yet (NULL) still counts live, until its next relate."""
    row = con.execute("SELECT inbound_count FROM documents WHERE uri = ?",
                      (root_uri,)).fetchone()
    if row and row[0] is not None:
        return row[0]
    return inbound_counts_for(con, [root_uri]).get(root_uri, 0)


def stamp_inbound_counts(con: sqlite3.Connection) -> int:
    """Materialize `document_inbound_count` for every document -- run at the
    end of every relate that changed anything (an incremental relate moves
    other documents' counts too: the re-related document's own citations).
    One whole-corpus pass (~9 s) instead of a per-request index-range count."""
    counts = document_inbound_counts(con)
    con.execute("UPDATE documents SET inbound_count = 0")
    con.executemany("UPDATE documents SET inbound_count = ? WHERE uri = ?",
                    [(n, uri) for uri, n in counts.items()])
    return len(counts)


def document_inbound_counts(con: sqlite3.Connection) -> dict[str, int]:
    """`document_inbound_count` for every cited root at once -- {root_uri:
    count}, same semantics as the per-uri query. One pass over the links table
    instead of one GROUP-BY subquery per document (the full-reindex path)."""
    return dict(con.execute(
        "SELECT to_root, COUNT(*) FROM (SELECT l.to_root, 1 FROM links l "
        "WHERE 1=1" + _NOT_SELF + " GROUP BY l.to_root, l.from_uri, "
        "l.from_anchor) GROUP BY to_root"))


# uris per `inbound_counts_for` query. SQLite binds one variable per uri and
# caps them at SQLITE_MAX_VARIABLE_NUMBER, 32 766 on this build; asking for
# more raises OperationalError("too many SQL variables"), which reaches a
# caller as an unhandled 500. The ECHR (`ext/coe/005`) alone has 50 626
# citers, and the next four roots are 19 570 / 15 013 / 13 656 / 12 018, so the
# corpus is one nightly growth away from more. Well under the cap rather than
# at it, because the margin costs nothing: 50 626 citers is six queries.
_COUNT_CHUNK = 10_000


def inbound_counts_for(con: sqlite3.Connection, uris) -> dict[str, int]:
    """`document_inbound_count` for a named set of documents -- {uri: count}
    over exactly `uris`, omitting the ones nothing cites.

    The rail orders its case-law entries by this (D4), and it needs the counts
    only for the citers on the page in front of it. The whole-corpus
    `document_inbound_counts` is a 13.5M-row pass costing ~9 s and 209k entries,
    which is right for the reindex that reads all of it and wrong per render
    worker -- and wronger still on an `only`-scoped one-page render, which the
    rest of the render path is careful to keep targeted.

    Asked for more than `_COUNT_CHUNK` uris it queries in chunks and merges.
    Each uri lands in exactly one chunk and the count is grouped by `to_root`,
    so merging is a plain dict update -- no uri is counted twice."""
    uris = list(uris)
    counts: dict[str, int] = {}
    for start in range(0, len(uris), _COUNT_CHUNK):
        chunk = uris[start:start + _COUNT_CHUNK]
        counts.update(con.execute(
            "SELECT to_root, COUNT(*) FROM (SELECT l.to_root, 1 FROM links l "
            "WHERE l.to_root IN (%s)" % ",".join("?" * len(chunk)) + _NOT_SELF
            + " GROUP BY l.to_root, l.from_uri, l.from_anchor) GROUP BY to_root",
            chunk))
    return counts


def counts(con: sqlite3.Connection) -> dict[str, int]:
    return dict(con.execute(
        "SELECT source, COUNT(*) FROM documents GROUP BY source").fetchall())


def source_stats(con):
    """{source: (docs, bytes)}: document count and summed artifact size per
    source, for the ops dashboard. `art_size` is NULL on synthesized stubs (rows
    with no artifact on disk), so COALESCE it to 0."""
    return {row[0]: (row[1], row[2]) for row in con.execute(
        "SELECT source, COUNT(*), COALESCE(SUM(art_size), 0) "
        "FROM documents GROUP BY source ORDER BY source").fetchall()}


def expired_uris(con: sqlite3.Connection, today: str) -> set[str]:
    """The uris whose declared repeal date (`expired`) is on or before `today` (an
    ISO date string) -- repealed statutes to drop from the browse listings. A
    future repeal date (not yet in force) is kept."""
    return {r[0] for r in con.execute(
        "SELECT uri FROM documents WHERE expired IS NOT NULL AND expired <= ?",
        (today,))}


def concept_aliases(con: sqlite3.Connection) -> dict[str, str]:
    """The variant-uri -> canonical-uri map (`concept_alias`), so the renderer can
    resolve a begrepp link baked into an artifact onto its canonical concept page."""
    return dict(con.execute("SELECT variant, canonical FROM concept_alias"))


def document(con: sqlite3.Connection, uri: str) -> dict | None:
    """A document's catalog row (uri, source, kind, label, title, path,
    descriptive), or None -- the metadata behind an API /document lookup.

    `descriptive` sits last so the positional reads of the first six (path is
    `row[5]` in three callers) keep their places."""
    return con.execute(
        "SELECT uri, source, kind, label, title, path, descriptive "
        "FROM documents WHERE uri = ?", (uri,)).fetchone()


def document_by_prefix(con, uri_prefix):
    """The one document whose uri extends `uri_prefix`, or None when nothing
    or several match. GLOB, not LIKE, so the literal '_'/'.' in lagen.nu URIs
    match themselves -- how a bare page-number SFS id resolves ("...1904:48"
    + "_s." -> the 1904:48_s.1 row) when only the catalog knows the page.

    The same columns `document` answers with, in the same order: `pins` takes
    whichever of the two returns a row and unpacks it one way."""
    rows = con.execute(
        "SELECT uri, source, kind, label, title, path, descriptive "
        "FROM documents WHERE uri GLOB ?", (uri_prefix + "*",)).fetchall()
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


def _doc_filter(source, kind, include_expired=False):
    """A (WHERE-clause, params) pair shared by `documents` and `document_count`.

    A document whose declared repeal date has passed is left out unless
    `include_expired`, so an enumeration of the corpus states current law the
    same way the browse trees and search results do (`_expired_date`). A future
    repeal date is not yet a repeal and stays. The document itself stays
    reachable by uri and through the reference graph.

    This default reaches `document_count`'s other callers too: `render.py` uses
    it as a "does the corpus hold any X" test before building a source's index
    pages, which now reads "any X still stating law". That is the same question
    `facets.tree` answers for the buckets on those pages, so the two agree; pass
    `include_expired=True` at a call site that means held-at-all regardless."""
    clauses, params = [], []
    if source:
        clauses.append("source = ?")
        params.append(source)
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    if not include_expired:
        clauses.append("(expired IS NULL OR expired > ?)")
        params.append(date.today().isoformat())
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), params


def documents(con, source=None, kind=None, limit=None, offset=0,
              include_expired=False):
    """A filtered, paginated document listing as (uri, source, kind, label,
    title, source_url, path, display) rows, ordered by uri -- the id/metadata
    index that drives /document lookups and the browse listings (not full-text
    search). `display` is the reader-facing heading (catalog.display_title).
    `source`/`kind` filter; `limit`/`offset` page; `include_expired` puts
    repealed documents back in."""
    where, params = _doc_filter(source, kind, include_expired)
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
        "SELECT uri, source, kind, label, title, source_url, path, display, date, "
        "short_id, short_title, description "
        "FROM documents WHERE source = ? ORDER BY uri", (source,)
    ).fetchall()


def document_count(con: sqlite3.Connection, source: str | None = None,
                   kind: str | None = None, include_expired: bool = False) -> int:
    """How many documents match the same `source`/`kind`/`include_expired`
    filter -- the total for a paginated `documents` listing."""
    where, params = _doc_filter(source, kind, include_expired)
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


# --------------------------------------------------------------------------
# graph neighborhood -- the aggregated per-document view /api/v1/graph serves
# --------------------------------------------------------------------------

def graph_outbound(con, uri):
    """The distinct documents `uri` cites, largest first: (to_root, n, label,
    title, source, kind). The inner join drops targets outside the corpus --
    `graph_out_totals` still counts them -- and self-citations are the
    document's internal structure, `graph_internal`'s answer, not a
    neighbor."""
    return con.execute(
        "SELECT l.to_root, count(*) n, d.label, d.title, d.source, d.kind, "
        "       d.descriptive, d.inbound_count "
        "FROM links l JOIN documents d ON d.uri = l.to_root "
        "WHERE l.from_uri = ? AND l.to_root != l.from_uri "
        "GROUP BY l.to_root ORDER BY n DESC", (uri,)).fetchall()


def graph_inbound(con, uri):
    """The distinct documents citing `uri`, largest first -- the mirror of
    `graph_outbound`, walking idx_links_to_root.

    Every citer is labelled, which a *group-filtered* reply needs (the filter
    reads `source` and `kind`). An unfiltered reply carries only its top rows
    and wants `graph_inbound_counts` + `graph_labels` instead: see those."""
    return con.execute(
        "SELECT l.from_uri, count(*) n, d.label, d.title, d.source, d.kind, "
        "       d.descriptive, d.inbound_count "
        "FROM links l JOIN documents d ON d.uri = l.from_uri "
        "WHERE l.to_root = ? AND l.from_uri != l.to_root "
        "GROUP BY l.from_uri ORDER BY n DESC", (uri,)).fetchall()


# The join to `documents` in the two inbound queries above costs one random row
# lookup per *citer*, however few of them the reply carries. Article 6 ECHR has
# 50,624 citers, and labelling all of them to answer with 120 measured 5.4 s of
# a 5.8 s reply on prod's HDD-class disk (0.2 s of 0.3 s on dev's SSD). The
# aggregate alone is a covering-index walk of idx_links_to_root.
#
# The counts-only pair below drops the join. It fixes neither the totals nor the
# order -- both come from the full aggregate, exactly as before -- only *who*
# gets a label: the rows the reply carries, fetched by `graph_labels`.

def graph_inbound_counts(con, uri):
    """(from_uri, n) per distinct citer of `uri`, largest first, unlabelled --
    `graph_inbound` without the join to `documents`."""
    return con.execute(
        "SELECT l.from_uri, count(*) n FROM links l "
        "WHERE l.to_root = ? AND l.from_uri != l.to_root "
        "GROUP BY l.from_uri ORDER BY n DESC", (uri,)).fetchall()


def graph_labels(con, uris):
    """{uri: (label, title, source, kind, descriptive, inbound_count)} for a
    handful of documents -- what the counts-only queries leave for the caller
    to fetch, once the reply knows which rows it carries. A uri absent from
    `documents` is absent from the result. An empty `uris` asks `IN ()`,
    which SQLite accepts and nothing matches."""
    return {row[0]: row[1:] for row in con.execute(
        "SELECT uri, label, title, source, kind, descriptive, inbound_count "
        "FROM documents WHERE uri IN (%s)" % ",".join("?" * len(uris)),
        tuple(uris))}


def graph_induced_edges(con, uris):
    """Every document-level citation among `uris`: (from_uri, to_root, n).
    The deep neighbourhood view draws these instead of only spoke edges, so
    citers citing each other show as structure. Bounded by the caller (a few
    hundred uris -- two IN lists must stay under SQLite's variable cap)."""
    assert len(uris) <= 5000, "induced-edge query asked for %d uris" % len(uris)
    marks = ",".join("?" * len(uris))
    return con.execute(
        "SELECT from_uri, to_root, count(*) FROM links "
        "WHERE from_uri IN (%s) AND to_root IN (%s) AND from_uri != to_root "
        "GROUP BY 1, 2" % (marks, marks), (*uris, *uris)).fetchall()


def graph_out_totals(con, uri):
    """(links, distinct targets) for everything `uri` cites beyond itself,
    targets outside the corpus included."""
    return con.execute(
        "SELECT count(*), count(DISTINCT to_root) FROM links "
        "WHERE from_uri = ? AND to_root != from_uri", (uri,)).fetchone()


# a fragment plus its subdivisions, in both fragment grammars the corpus
# writes: a letter-opened tail ("K4P7" matches K4P7S2 but not K4P70; "A6"
# matches A6P1) and the EU acts' dot-joined tail ("6.1" matches 6.1.c and
# 6.1.S2 but not 6.10, whose next character is a digit). GLOB's specials
# (*?[]) do not occur in document uris or fragment ids.
def _frag_globs(frag):
    return (frag + "[A-Z]*", frag + ".*")


def graph_anchor_inbound(con, uri, frag):
    """The documents citing one provision -- rows naming `uri#frag` or a
    subdivision of it -- largest first."""
    letter, dot = _frag_globs(uri + "#" + frag)
    return con.execute(
        "SELECT l.from_uri, count(*) n, d.label, d.title, d.source, d.kind, "
        "       d.descriptive, d.inbound_count "
        "FROM links l JOIN documents d ON d.uri = l.from_uri "
        "WHERE l.to_root = ? AND (l.to_uri = ? OR l.to_uri GLOB ? "
        "                         OR l.to_uri GLOB ?) "
        "AND l.from_uri != l.to_root "
        "GROUP BY l.from_uri ORDER BY n DESC",
        (uri, uri + "#" + frag, letter, dot)).fetchall()


def graph_anchor_inbound_counts(con, uri, frag):
    """(from_uri, n) per distinct citer of one provision, largest first,
    unlabelled -- `graph_anchor_inbound` without the join to `documents`."""
    letter, dot = _frag_globs(uri + "#" + frag)
    return con.execute(
        "SELECT l.from_uri, count(*) n FROM links l "
        "WHERE l.to_root = ? AND (l.to_uri = ? OR l.to_uri GLOB ? "
        "                         OR l.to_uri GLOB ?) "
        "AND l.from_uri != l.to_root "
        "GROUP BY l.from_uri ORDER BY n DESC",
        (uri, uri + "#" + frag, letter, dot)).fetchall()


def graph_anchor_outbound(con, uri, frag):
    """The documents one provision cites: links leaving `uri` from the
    fragment or a subdivision of it, largest first."""
    letter, dot = _frag_globs(frag)
    return con.execute(
        "SELECT l.to_root, count(*) n, d.label, d.title, d.source, d.kind, "
        "       d.descriptive, d.inbound_count "
        "FROM links l JOIN documents d ON d.uri = l.to_root "
        "WHERE l.from_uri = ? AND (l.from_anchor = ? OR l.from_anchor GLOB ? "
        "                          OR l.from_anchor GLOB ?) "
        "AND l.to_root != l.from_uri "
        "GROUP BY l.to_root ORDER BY n DESC",
        (uri, frag, letter, dot)).fetchall()


def graph_anchor_out_totals(con, uri, frag):
    """(links, distinct targets) for everything one provision cites beyond
    its own document, targets outside the corpus included -- the anchor-level
    mirror of `graph_out_totals`."""
    letter, dot = _frag_globs(frag)
    return con.execute(
        "SELECT count(*), count(DISTINCT to_root) FROM links "
        "WHERE from_uri = ? AND (from_anchor = ? OR from_anchor GLOB ? "
        "                        OR from_anchor GLOB ?) "
        "AND to_root != from_uri", (uri, frag, letter, dot)).fetchone()


def graph_internal(con, uri):
    """A document's internal citation graph, one row per (citing anchor,
    cited fragment): the self-citations both neighbor queries exclude."""
    return con.execute(
        "SELECT l.from_anchor, l.to_uri, count(*) n FROM links l "
        "WHERE l.from_uri = ? AND l.to_root = ? AND l.from_anchor IS NOT NULL "
        "GROUP BY 1, 2", (uri, uri)).fetchall()


_EMPTY_SIDE = hashlib.sha256().hexdigest()   # digest of zero inbound/outbound rows


def _combine_dep(inbound_hex, outbound_hex):
    return hashlib.sha256(
        ((inbound_hex or _EMPTY_SIDE) + "\x00"
         + (outbound_hex or _EMPTY_SIDE)).encode()).hexdigest()


# The dependency digest a page with no inbound *and* no outbound edges gets --
# the default for a uri absent from `page_dependency_digests` (generate looks up
# every catalogued uri, including the link-less ones).
EMPTY_DEP_DIGEST = _combine_dep(None, None)


# Everything about one inbound citation that a page's rendering reads, and so
# everything its freshness has to cover. Named once because the batched pass and
# the scoped one below must hash byte-identically -- two copied SELECT lists were
# a divergence waiting to happen, and the digest decides every page's freshness.
#
# `to_uri`, `predicate` and `from_page` were added 2026-08-07 with the inbound
# artifact tree, but they were missing before it: the citation's *target* is what
# assigns it to a paragraf margin, its predicate is what routes it to the
# bemyndigande/ändrar panels instead, and its page is what the line reads as
# "s. 45". A citer re-parse that moved a pinpoint from `#K1P18` to `#K1P18a`
# without moving the citing anchor -- exactly what a lagrum-grammar fix does --
# therefore left the cited page fresh with the citation drawn in the wrong
# margin. `kind` and `date` join them because the rail groups and orders on them,
# as it already did on the `label`/`title`/`source` that were covered.
DEP_INBOUND_COLUMNS = ("l.from_uri", "l.from_anchor", "l.to_uri", "l.predicate",
                       "l.from_page", "d.label", "d.title", "d.source", "d.kind",
                       "d.date")
DEP_INBOUND_COLUMNS_SQL = ", ".join(DEP_INBOUND_COLUMNS)
# a total order over the link columns (the `d.` ones are functionally determined
# by from_uri), so two rows sharing a citing spot still hash in a fixed order
DEP_INBOUND_ORDER_SQL = ", ".join(c for c in DEP_INBOUND_COLUMNS
                                  if c.startswith("l."))


def _dep_row(fields) -> bytes:
    """One citation's contribution to a dependency digest. `str` because
    `from_page` is an INTEGER and the rest are TEXT."""
    return "\x1f".join("" if c is None else str(c) for c in fields).encode()


def page_dependency_digests(con):
    """`{uri: digest}` for every document with a citation relationship -- a digest
    of everything *besides its own artifact* that its rendered page depends on, for
    incremental generate. Identity/set-based, not content-based: cited and citing
    documents are effectively immutable (a case or förarbete never changes once
    published), so a page goes stale when the *set* of its relationships changes --
    a new case starts citing it, an old one drops out, or a document it links to
    appears/disappears -- not when an unchanged neighbour's bytes change. Two parts,
    combined into the per-uri digest:

      * inbound -- the citation rows it renders in its margins and panel
        (`DEP_INBOUND_COLUMNS`): a new or removed citer changes this, and so does
        a citer whose re-parse moved which provision it points at;
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
            "SELECT l.to_root, " + DEP_INBOUND_COLUMNS_SQL
            + " FROM links l JOIN documents d ON d.uri = l.from_uri "
            "WHERE l.from_uri <> l.to_root "
            "ORDER BY l.to_root, " + DEP_INBOUND_ORDER_SQL):
        if root != cur:
            if cur is not None:
                inbound[cur] = h.hexdigest()
            cur, h = root, hashlib.sha256()
        h.update(_dep_row(fields))
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


def page_dependency_digests_for(con, uris):
    """`page_dependency_digests` scoped to the given uris: the same per-uri
    digest via two indexed lookups per document instead of the corpus-wide
    streamed pass (~30 s over a 10M-row links table). For the scoped generate
    (`lagen <source> generate <id>`, the editor's post-commit rebuild) where the
    plan is a handful of pages, not 124k. Must stay byte-identical to the
    batched variant -- the digest enters the page signature stored in the
    manifest, so a divergence would flip every scoped page's freshness."""
    out = {}
    for uri in uris:
        h, rows = hashlib.sha256(), 0
        for fields in con.execute(
                "SELECT " + DEP_INBOUND_COLUMNS_SQL
                + " FROM links l JOIN documents d ON d.uri = l.from_uri "
                "WHERE l.to_root = ? AND l.from_uri <> l.to_root "
                "ORDER BY " + DEP_INBOUND_ORDER_SQL, (uri,)):
            h.update(_dep_row(fields))
            h.update(b"\x1e")
            rows += 1
        inbound = h.hexdigest() if rows else None
        h, rows = hashlib.sha256(), 0
        for (target,) in con.execute(
                "SELECT DISTINCT l.to_root FROM links l "
                "JOIN documents d ON d.uri = l.to_root "
                "WHERE l.from_uri = ? AND l.to_root <> l.from_uri "
                "ORDER BY l.to_root", (uri,)):
            h.update(target.encode())
            h.update(b"\x1e")
            rows += 1
        outbound = h.hexdigest() if rows else None
        if inbound is not None or outbound is not None:
            out[uri] = _combine_dep(inbound, outbound)
    return out
