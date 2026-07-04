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

from . import concepts
from .markdown import begrepp_uri
from .text import runs_text

BASE = "https://lagen.nu/"


def norm_title(t):
    """A law title normalised for matching a proposed-law name against the SFS
    title index: SFS number dropped, whitespace collapsed, lower-cased -- so
    'Lag (2015:671) om alternativ tvistlösning …' and the proposition's 'lag om
    alternativ tvistlösning …' compare equal."""
    return re.sub(r"\s+", " ", re.sub(r"\(\d{4}:\d+\)", "", t)).strip().lower()

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    uri          TEXT PRIMARY KEY,
    source       TEXT NOT NULL,    -- 'sfs' | 'dv'
    kind         TEXT,             -- 'law' | 'case'
    label        TEXT,             -- short display id (SFS number / referat)
    title        TEXT,             -- full heading
    path         TEXT NOT NULL,    -- artifact json on disk
    source_url   TEXT,             -- authoritative publisher url ("Källa"), if any
    content_hash TEXT,             -- sha256 of the artifact bytes (incremental relate)
    expired      TEXT              -- repeal-effective date (SFS upphavandedatum), if any
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
CREATE TABLE IF NOT EXISTS correspondence (
    new_uri  TEXT NOT NULL,         -- the new statute paragraf (full uri, doc#id)
    old_uri  TEXT NOT NULL,         -- the old (repealed) paragraf it corresponds to
    relation TEXT NOT NULL,         -- 'motsvarar' | 'overfort'
    scope    TEXT,                  -- 'helt'|'i_sak'|'i_huvudsak'|'delvis'|NULL
    prop_uri TEXT                   -- the proposition stating the correspondence
);
CREATE INDEX IF NOT EXISTS idx_corr_new ON correspondence(new_uri);
CREATE INDEX IF NOT EXISTS idx_corr_old ON correspondence(old_uri);
CREATE INDEX IF NOT EXISTS idx_genomf_sfs ON genomforande(sfs_uri, sfs_anchor);
CREATE INDEX IF NOT EXISTS idx_links_to_uri  ON links(to_uri);
CREATE INDEX IF NOT EXISTS idx_links_to_root ON links(to_root);
CREATE INDEX IF NOT EXISTS idx_links_from    ON links(from_uri);
CREATE INDEX IF NOT EXISTS idx_docs_source   ON documents(source);
"""


def connect(path):
    con = sqlite3.connect(path)
    # the catalog is derived and rebuildable, so durability is not precious:
    # WAL (persistent, set once) lets readers proceed during a relate, and
    # NORMAL skips the per-commit fsync that FULL pays on multi-GB rebuilds
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
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


SNIPPET_LEN = 220


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
    # CELEX (the short id citations use)
    label = art.get("celex") or local(art["uri"])
    return (art["uri"], "eurlex", art.get("doctype", "eurlex"),
            label, art.get("title") or label, str(path))


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


def expired_date(art):
    """The date a document's repeal takes effect, if its metadata declares one (a
    statute's `rpubl:upphavandedatum`) -- else None. Stored on the documents row so
    the browse listings can omit a statute once the date has passed (still reachable
    by direct link and search)."""
    return art.get("metadata", {}).get("properties", {}).get("rpubl:upphavandedatum")


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
            "avg": avg_document}[source](art, path)


# --------------------------------------------------------------------------
# rebuild
# --------------------------------------------------------------------------

def content_hash(raw):
    """The change-detection key for an artifact: sha256 of its on-disk bytes.
    Stored on the documents row so relate (and, via the row, index) can skip an
    artifact whose bytes are unchanged since last time."""
    return hashlib.sha256(raw).hexdigest()


def _drop_document(con, uri):
    """Remove a document and everything keyed off it: its outbound links and its
    fragment snippets (doc#id and every doc#id child)."""
    con.execute("DELETE FROM links WHERE from_uri = ?", (uri,))
    con.execute("DELETE FROM documents WHERE uri = ?", (uri,))
    # range predicate instead of LIKE 'uri#%': LIKE is case-insensitive by
    # default so it cannot use the primary-key index and full-scans the
    # multi-million-row table per dropped document ('$' is '#' + 1)
    con.execute("DELETE FROM fragments WHERE uri = ? "
                "OR (uri >= ? || '#' AND uri < ? || '$')",
                (uri, uri, uri))
    con.execute("DELETE FROM concept_redirect WHERE concept = ?", (uri,))


def _index_document(con, art, path, source):
    """(Re)write one document's rows: its documents row, outbound links and
    fragment snippets, replacing any prior version keyed by the same uri."""
    uri = art["uri"]
    con.execute("DELETE FROM links WHERE from_uri = ?", (uri,))
    row = document_row(art, path, source)        # (uri, source, kind, label, title, path)
    con.execute(
        "INSERT OR REPLACE INTO documents "
        "(uri, source, kind, label, title, path, source_url, content_hash, "
        " expired, display) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (*row, art.get("source_url"),
         None,                 # content_hash filled by the caller (holds bytes)
         expired_date(art),
         display_title(art, row[4])))             # the reader-facing heading (row[4]=title)
    rows = [(uri, anchor, run.get("predicate", "dcterms:references"),
             run["uri"], strip_fragment(run["uri"]), run.get("text"))
            for anchor, run in (artifact_links(art) + subject_links(art)
                                + definition_links(art)
                                + bemyndigande_links(art))]
    con.executemany("INSERT INTO links VALUES (?,?,?,?,?,?)", rows)
    con.executemany("INSERT OR REPLACE INTO fragments VALUES (?,?)",
                    artifact_fragments(art))
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


def rebuild(catalog_path, source, artifact_paths, progress=None, force=False):
    """Sync one source's rows in the catalog to its artifacts on disk.
    Incremental by content hash: an artifact whose bytes are unchanged since the
    last relate is left in place (not re-parsed); new/changed ones are
    re-extracted; rows whose artifact has vanished are dropped. `force`
    re-extracts every artifact regardless of hash. Single-process and
    transactional -- it sidesteps multi-writer SQLite contention. Empty artifacts
    (SkipDocument placeholders) carry no document.

    Returns (documents, links, changed): the source's row + link totals after the
    sync, and how many documents were (re)written this run."""
    con = connect(catalog_path)
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
        key = str(path)
        seen.add(key)
        st = path.stat()
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
        raw = path.read_bytes()
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
                _index_document(con, art, path, source)
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
    """Replace the paragraf correspondence layer. Each row is
    (new_uri, old_uri, relation, scope, prop_uri) -- both endpoints full paragraf
    uris. Queried in both directions: the old paragraf's margin shows the new
    paragraf that supersedes it, and the new paragraf's margin shows the cases
    citing the old one (the generic `inbound` on `old_uri`)."""
    con.execute("DELETE FROM correspondence")
    con.executemany("INSERT INTO correspondence VALUES (?,?,?,?,?)", rows)
    con.commit()


def correspondence_for_old(con, old_uri):
    """The new-law paragraf(s) that now correspond to an old (repealed) paragraf,
    for its margin: (new_uri, relation, scope, prop_uri)."""
    return con.execute(
        "SELECT new_uri, relation, scope, prop_uri FROM correspondence "
        "WHERE old_uri = ? ORDER BY new_uri", (old_uri,)).fetchall()


def correspondence_for_new(con, new_uri):
    """The old (repealed) paragraf(s) a new-law paragraf corresponds to, for its
    margin: (old_uri, relation, scope, prop_uri)."""
    return con.execute(
        "SELECT old_uri, relation, scope, prop_uri FROM correspondence "
        "WHERE new_uri = ? ORDER BY old_uri", (new_uri,)).fetchall()


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


def inbound_count(con, uri):
    """How many (citing document, pinpoint) entries cite exactly `uri`."""
    return con.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM links l WHERE l.to_uri = ?"
        + _NOT_SELF + _NOT_BEMYNDIGANDE
        + " GROUP BY l.from_uri, l.from_anchor)", (uri,)).fetchone()[0]


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


def snippet(con, uri):
    """The stored text snippet for a fragment uri (link-tooltip text), or None."""
    row = con.execute("SELECT snippet FROM fragments WHERE uri = ?",
                      (uri,)).fetchone()
    return row[0] if row else None


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
