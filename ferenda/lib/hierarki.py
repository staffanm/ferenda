"""Regleringshierarki -- one ladder per concept through the norm levels.

A subject is regulated at three or four levels at once (EU-rätt -> lag ->
förordning -> föreskrift), and no level tells the reader that. This module
builds the two derived catalog tables that make the ladder renderable:

* ``delegation_edge`` -- the förordning->lag rung that Swedish förordningar
  mostly do not state (286 of 300 sampled carry neither ingress formula).
  Two things say it anyway. The **title pair**: the couple is named twice
  over, "Arkivförordning (1991:446)" under "Arkivlag (1990:782)", "Förordning
  (2004:1101) om luftfartsskydd" under "Lag (2004:1100) om luftfartsskydd".
  The **delegation clause**: the empowering provision a föreskrift stands on
  cites, in its own text, the lag it delegates under ("Transportstyrelsen får
  meddela föreskrifter **enligt 7 kap. 2 § 1 fartygssäkerhetslagen** om hur
  ett fartyg skall vara konstruerat ..."). Those citations are already
  ``links`` rows at stycke granularity, so that rung is one SQL join -- no
  artifact is re-read and no text is re-parsed (rule:artifact-is-truth: the
  inline link the page renders *is* the edge). The title pair is the stronger
  basis, and where it exists the clause citations of that förordning are
  dropped rather than published beside it.

* ``regleringshierarki`` -- one row per (concept, provision), grouped into
  ladders by ``chain_root``. Built from ``definitions`` + the chain
  (``norm_chain`` union ``delegation_edge``) by the mechanical passes of
  PRD-regleringshierarki.md: verbatim descent (P2), genomförande pairing
  (P3), and delegation-clause subjects (P4's mechanical half).

Both rebuild whole at relate, in ``build.cmd_relate``'s cross-document block,
strictly after ``canonicalize_concepts`` (rows store canonical concept uris)
and after ``rebuild_norm_chain`` (which DELETEs its table, so the derived
edges must always be re-inserted after it).
"""

import json
import re

from . import annstore, catalog, concepts, history, text
from .util import normalize_fold, split_numalpha

# a förordning issued under the government's own residual power (8 kap. RF)
# has no delegating lag; a delegation clause citing regeringsformen says so
REGERINGSFORMEN = catalog.BASE + "1974:152"

# the predicate a delegation_edge row carries in a `via` path -- derived at
# relate, deliberately NOT in catalog.CHAIN_PREDICATES (no document states it)
DELEGATION = "rinfoex:delegationskedja"

# the SFS number a title carries ("Arkivlag (1990:782)")
RE_TITLE_NR = re.compile(r"\s*\(\d{4}:\d+\s*\w*\)")


def _title_keys(title, tail):
    """The name keys of one act title, for the general/specific title pair.

    Swedish drafting names the couple twice over, and both shapes appear:
    compound ("Arkivförordning" beside "Arkivlag") and shared subject
    ("Förordning om luftfartsskydd" beside "Lag om luftfartsskydd"). `tail`
    is the alternation for the instrument word, so the same reading serves
    both ends of the pair."""
    text_ = " ".join(RE_TITLE_NR.sub(" ", title).split()).lower()
    keys = set()
    rest = re.match(r"^(?:%s)\b\s*(.+)$" % tail, text_)
    if rest:
        keys.add(("om", rest.group(1)))
    compound = re.match(r"^(\w+?)(?:%s)$" % tail, text_)
    # a stem of three letters or fewer is not a subject ("Sjölag")
    if compound and len(compound.group(1)) > 3:
        keys.add(("stam", compound.group(1)))
    return keys


def name_pairs(con):
    """``{förordning uri: lag uri}`` -- the general/specific title pair.

    A förordning that details a lag mostly says so only in its name:
    Arkivförordning (1991:446) under Arkivlag (1990:782), Säkerhetsskydds-
    förordning (2021:955) under Säkerhetsskyddslag (2018:585), Förordning
    (2004:1101) om luftfartsskydd under Lag (2004:1100) om luftfartsskydd.
    Only an unambiguous match counts (3 of 3,540 gällande förordningar match
    two lagar, and those get no edge).

    Measured 2026-08-29 on the dev catalog: 615 pairs. Where the delegation
    clauses also name a lag the two agree in 218 of 225 förordningar, and
    the rule supplies a parent for 337 förordningar no clause reached."""
    lagar, forordningar = {}, []
    for uri, title, kind in con.execute(
            "SELECT uri, title, kind FROM documents WHERE source = 'sfs' "
            "AND expired IS NULL AND kind IN ('lag', 'forordning') "
            "AND title IS NOT NULL"):
        if kind == "lag":
            for key in _title_keys(title, r"lag|balk"):
                lagar.setdefault(key, set()).add(uri)
        else:
            forordningar.append((uri, _title_keys(title, r"förordning")))
    pairs = {}
    for uri, keys in forordningar:
        hits = set().union(*[lagar.get(k, set()) for k in keys]) if keys \
            else set()
        if len(hits) == 1:
            pairs[uri] = hits.pop()
    return pairs


def derive_delegation_edges(con):
    """Fill ``delegation_edge`` from the title pair and the delegation
    clauses' own citations.

    The title pair (``name_pairs``) is the stronger basis and wins outright:
    where a förordning has one, its citation-derived parents are dropped
    rather than published beside it. A delegation clause cites a lag for
    reasons other than delegating -- arkivförordningen 3 § names 2 kap. 12 §
    tryckfrihetsförordningen for the handlingar the myndighet must weigh, and
    that citation made TF the root of Riksarkivet's whole 368-document
    hierarchy. Measured 2026-08-29: the rule drops 62 such parents over 42
    förordningar, and 10 of 10 hand-read were cross-references (jaktför-
    ordningen under lagen om tillsyn över hundar och katter, polisförordningen
    under ordningslagen).

    The citation basis reads, for every förordning provision a föreskrift
    stands on (the pinned upper end of a föreskrift->förordning
    ``norm_chain`` row), the lag references that provision's text carries in
    ``links``. The pinned-beats-bare rule from the föreskrift parse applies
    per clause: when the clause cites both "7 kap. 2 § fartygssäkerhetslagen"
    and the bare law, the paragraf pin is the edge and the bare mention is
    dropped.

    Returns ``(inserted, stated_duplicates)`` -- an edge the förordning
    already *states* in its bemyndigandeupplysning (an exact förordning->lag
    ``norm_chain`` row) is counted, not re-inserted."""
    con.execute("DELETE FROM delegation_edge")
    rows = con.execute(
        "SELECT DISTINCT nc.upper_uri, nc.upper_pin, l.to_root, l.to_uri "
        "FROM (SELECT DISTINCT upper_uri, upper_pin FROM norm_chain "
        "      WHERE predicate = 'rpubl:bemyndigande' "
        "        AND upper_pin IS NOT NULL "
        "        AND lower_level = 3 AND upper_level = 2) nc "
        "JOIN links l ON l.from_uri = nc.upper_uri "
        "  AND (l.from_anchor = nc.upper_pin "
        "       OR l.from_anchor LIKE nc.upper_pin || 'S%') "
        "  AND l.predicate LIKE '%references' "
        "JOIN (" + catalog._LEVEL_SELECT + ") lvl "
        "  ON lvl.uri = l.to_root AND lvl.lvl = 1 "
        "WHERE l.to_root != ?", (REGERINGSFORMEN,)).fetchall()
    # group the citations per delegation clause, then pinned-beats-bare
    clauses = {}
    for lower_uri, lower_pin, law, target in rows:
        clauses.setdefault((lower_uri, lower_pin), set()).add((law, target))
    stated = set(con.execute(
        "SELECT lower_uri, lower_pin, upper_uri, upper_pin FROM norm_chain "
        "WHERE predicate = 'rpubl:bemyndigande' "
        "  AND lower_level = 2 AND upper_level = 1"))
    pairs = name_pairs(con)
    stated_pairs = {(lower, upper) for lower, _lp, upper, _up in stated}
    edges, duplicates = [], 0
    for forordning, lag in sorted(pairs.items()):
        # a förordning that states its parent in the bemyndigandeupplysning
        # needs no derived edge to the same lag, whatever the pins
        if (forordning, lag) in stated_pairs:
            duplicates += 1
            continue
        # the title pair is a statement about the two documents, not about a
        # provision: an empty lower_pin is what a document-level edge looks
        # like here, and it matches no clause in `fyller_ut_index`
        edges.append((forordning, "", lag, None))
    for (lower_uri, lower_pin), targets in sorted(clauses.items()):
        if lower_uri in pairs:
            continue            # the title pair already names this parent
        pinned_laws = {law for law, target in targets if "#" in target}
        for law, target in sorted(targets):
            if "#" not in target and law in pinned_laws:
                continue
            edge = (lower_uri, lower_pin, law, catalog.fragment(target))
            if edge in stated:
                duplicates += 1
                continue
            edges.append(edge)
    con.executemany("INSERT INTO delegation_edge (lower_uri, lower_pin, "
                    "upper_uri, upper_pin) VALUES (?,?,?,?)", edges)
    # commit here: the relate caller closes the connection without one, and an
    # uncommitted rebuild is silently discarded (see rebuild_norm_chain)
    con.commit()
    return len(edges), duplicates


# --------------------------------------------------------------------------
# the ladder builder: one regleringshierarki row per (concept, provision)
# --------------------------------------------------------------------------

# a löptext definition whose definiendum is longer than lib.begrepp's term cap
# ("Med betydande incident som har orsakat allvarlig driftstörning ... avses").
# The parse never mints a concept for it (a composed name no one looks up);
# the builder instead aligns the phrase against the terms the chain above
# already offers and keeps the whole phrase as the row's label (PRD §5 rule 3)
RE_LOPTEXT_PHRASE = re.compile(
    r"\bmed ([\w /-]{3,160}?) (?:avses|förstås)\b")
# the lydelse trailer a consolidated SFS provision closes on ("Lag
# (2026:623).") -- the amendment that last touched it, hence the date an
# upward pin was shaken (PRD §9.4)
RE_LYDELSE_TRAILER = re.compile(r"(?:Lag|Förordning) \((\d{4}:\d+)\)\.?\s*$")


def _anchor_within(anchor, pin):
    """Whether fragment `anchor` sits at or under provision `pin` in the SFS
    id grammar (ids concatenate without a separator): K2P1S2 is within K2P1,
    K2P12 is not (the digit continues the paragraf number)."""
    if anchor == pin:
        return True
    return (anchor.startswith(pin)
            and not anchor[len(pin)].isdigit() and anchor[len(pin)] != "-")


def _doc_info(con):
    """uri -> (level, kind, date, expired, path) for every ranked document --
    the documents that can be a rung at all."""
    out = {}
    for uri, source, kind, date, expired, path in con.execute(
            "SELECT uri, source, kind, date, expired, path FROM documents"):
        level = catalog.norm_level(source, kind)
        if level is not None:
            out[uri] = (level, kind, date, expired, path)
    return out


def _up_edges(con):
    """lower doc -> its upward chain edges (lower, lpin, upper, upin,
    predicate), norm_chain union delegation_edge."""
    up = {}
    for row in con.execute("SELECT lower_uri, lower_pin, upper_uri, "
                           "upper_pin, predicate FROM norm_chain"):
        up.setdefault(row[0], []).append(tuple(row))
    for l, lp, u, upin in con.execute("SELECT lower_uri, lower_pin, "
                                      "upper_uri, upper_pin "
                                      "FROM delegation_edge"):
        up.setdefault(l, []).append((l, lp, u, upin, DELEGATION))
    return up


def _ancestors(doc, up):
    """Every document reachable upward from `doc`, with one deterministic
    shortest edge path to each: {ancestor: [edge, ...]} (edges bottom-up,
    `doc`'s own edge first). BFS; ties break on the sorted edge tuple."""
    paths, frontier = {}, [(doc, [])]
    while frontier:
        nxt = []
        for node, path in frontier:
            for edge in sorted(up.get(node, []),
                               key=lambda e: tuple(x or "" for x in e)):
                upper = edge[2]
                if upper == doc or upper in paths:
                    continue
                paths[upper] = path + [edge]
                nxt.append((upper, paths[upper]))
        frontier = nxt
    return paths


def _definitions(con, info):
    """from_uri -> [(concept, term, anchor)] over the ranked documents, in
    the stored (document) order."""
    defs = {}
    for concept, from_uri, anchor, term in con.execute(
            "SELECT concept, from_uri, anchor, term FROM definitions"):
        if from_uri in info:
            defs.setdefault(from_uri, []).append((concept, term, anchor))
    return defs


def _term_starts(phrase, terms):
    """The (concept, term) whose term opens `phrase` (folded, inflection-wide
    on the term's last word), the longest term winning; None when none does.
    "betydande incident som har orsakat allvarlig driftstörning" starts with
    the chain's *betydande incident* -- the sibling "säkerhetsanalys" never
    matches "säkerhetsskyddsanalys..." (word-bounded, whole words)."""
    folded = normalize_fold(phrase)
    best = None
    for concept, term, pattern in terms:
        m = pattern.match(folded)
        if m and (best is None or len(term) > len(best[1])):
            best = (concept, term)
    return best


def hierarki_layers():
    """doc uri -> the curated regleringshierarki rows for it, read from every
    `.ann` layer in the sfs, foreskrift and eurlex annstore trees (the
    ai-hierarki output). The join is the uri the layer records, never a
    path -- the `genomforande_layers` model. A `.ann` without a
    `regleringshierarki` payload is another layer kind and is skipped."""
    out = {}
    for tree in ("sfs", "foreskrift", "eurlex"):
        for p in annstore.tree(tree).rglob("*.ann"):
            layer = json.loads(p.read_text()).get("regleringshierarki")
            if layer:
                out.setdefault(layer["uri"], []).extend(layer["rows"])
    return out


def rebuild_regleringshierarki(con, curated=None):
    """Rebuild the `regleringshierarki` table whole: the mechanical passes of
    PRD-regleringshierarki.md over `definitions` and the chain, plus the
    curated `.ann` rows (`curated`, from `hierarki_layers` -- the ai-hierarki
    output), which win over a mechanical row on the same (concept, doc,
    anchor) and land with source='llm'. Runs strictly after
    `canonicalize_concepts` (concept uris are stored canonical; curated uris
    fold through `concept_alias`) and after `rebuild_norm_chain` +
    `derive_delegation_edges` (the chain it walks). Returns a counter dict
    for the relate summary line; commits itself, like every relate post-pass
    (an uncommitted rebuild is silently discarded when the caller closes the
    connection)."""
    info = _doc_info(con)
    up = _up_edges(con)
    defs = _definitions(con, info)
    root = catalog.data_root(con)
    paths = {uri: p for uri, (_l, _k, _d, _e, p) in info.items()}
    stats = {"rows": 0, "docs_scanned": 0, "chain_docs_no_concept": 0,
             "verbatim": 0, "genomforande": 0, "aligned_labels": 0,
             "ladders": 0, "single_dropped": 0, "defs_off_chain": 0,
             "curated_rows": 0, "concept_stubs": 0}

    arts = {}

    def art(uri):
        if uri not in arts:
            arts[uri] = catalog.load_artifact(root, paths[uri])
        return arts[uri]

    # ancestor walk + candidate terms per chain-connected document
    chain_docs = sorted(set(up) | {e[2] for es in up.values() for e in es})
    ancestors = {doc: _ancestors(doc, up) for doc in chain_docs}
    patterns = {}      # (concept, term) -> compiled pattern, built once

    def candidate_terms(doc):
        """(concept, term, pattern) defined by `doc`'s proper ancestors --
        "the chain above a document offers a median of 3 defined terms"."""
        out = []
        for anc in ancestors.get(doc, ()):
            for concept, term, _anchor in defs.get(anc, ()):
                key = (concept, term)
                if key not in patterns:
                    patterns[key] = concepts.term_pattern(term)
                out.append((concept, term, patterns[key]))
        return out

    # upper_pin index: which (doc, anchor) is a delegation/bemyndigande target
    delegation_pins = {}
    for _lower, edges in up.items():
        for _l, _lp, upper, upin, _pred in edges:
            if upin:
                delegation_pins.setdefault(upper, set()).add(upin)

    # rows keyed (concept, doc, role): anchor order preserved for O4's `also`
    rows = {}

    def add(concept, doc, anchor, role, label=None, source="verbatim"):
        row = rows.setdefault((concept, doc, role),
                              {"anchors": [], "label": label,
                               "source": source})
        if anchor not in row["anchors"]:
            row["anchors"].append(anchor)
        if label and not row["label"]:
            row["label"] = label

    # pass 0: every definition on a chain-connected document is a row; a
    # definition off the chain has no ladder to join and is only counted
    stats["defs_off_chain"] = sum(len(v) for d, v in defs.items()
                                  if d not in set(chain_docs))
    for doc in chain_docs:
        for concept, _term, anchor in defs.get(doc, ()):
            add(concept, doc, anchor, "definierar")

    # passes P2 (verbatim descent) + the label alignments, per chain document
    for doc in chain_docs:
        cands = candidate_terms(doc)
        if not cands:
            if doc not in defs:
                stats["chain_docs_no_concept"] += 1
            continue
        own = {concept for concept, _t, _a in defs.get(doc, ())}
        descend = [(c, t, p) for c, t, p in cands if c not in own]
        stats["docs_scanned"] += 1
        pins = delegation_pins.get(doc, set())
        for frag_uri, frag_text in text.fragment_texts(art(doc)):
            anchor = catalog.fragment(frag_uri)
            folded = normalize_fold(frag_text)
            for concept, _term, pattern in descend:
                if pattern.search(folded):
                    role = ("delegerar"
                            if any(_anchor_within(anchor, p) for p in pins)
                            else "namner")
                    add(concept, doc, anchor, role)
                    stats["verbatim"] += 1
            # a löptext definition too long for a minted term: align the
            # phrase against the chain's terms, keep the phrase as the label
            for m in RE_LOPTEXT_PHRASE.finditer(folded):
                hit = _term_starts(m.group(1), cands)
                if hit and normalize_fold(hit[1]) != m.group(1):
                    add(hit[0], doc, anchor, "definierar",
                        label=m.group(1), source="verbatim")
                    stats["aligned_labels"] += 1
        # a defined term whose own phrase opens with an ancestor's term files
        # under the ancestor's concept as well, the phrase as its label
        for concept, term, anchor in defs.get(doc, ()):
            hit = _term_starts(term, [(c, t, p) for c, t, p in cands
                                      if c != concept])
            if hit:
                add(hit[0], doc, anchor, "definierar", label=term)
                stats["aligned_labels"] += 1

    # pass P3: a lag definition and a directive definition joined by the same
    # genomforande row are the same concept -- the directive provision joins
    # the sfs term's ladder, its own term as the label
    for sfs_uri, sfs_anchor, directive, article, pinpoint in con.execute(
            "SELECT sfs_uri, sfs_anchor, directive, article, pinpoint "
            "FROM genomforande"):
        base = catalog.strip_fragment(directive)
        if sfs_uri not in defs or base not in defs:
            continue
        atoms = catalog._atomize(pinpoint, article)
        for concept, _term, anchor in defs[sfs_uri]:
            if not _anchor_within(anchor or "", sfs_anchor):
                continue
            for d_concept, d_term, d_anchor in defs[base]:
                if d_concept == concept or not d_anchor:
                    continue
                if any(catalog._covers(atom, d_anchor)
                       or catalog._covers(d_anchor, atom) for atom in atoms):
                    add(concept, base, d_anchor, "definierar",
                        label=d_term, source="genomforande")
                    stats["genomforande"] += 1

    # a container fragment repeats its descendants' text, so one match lands
    # on the kapitel and its paragraf both -- keep only the deepest anchors
    for row in rows.values():
        anchors = row["anchors"]
        row["anchors"] = [a for a in anchors
                          if a is None or not any(
                              o and o != a and _anchor_within(o, a)
                              for o in anchors)]
    # a provision that files under a stronger role for the concept needs no
    # weaker row on (or containing) the same anchor
    strength = {"definierar": 0, "delegerar": 1, "namner": 2}
    for key in sorted(rows, key=lambda k: strength.get(k[2], 9)):
        if key not in rows:
            continue
        stronger = [a for r, s in strength.items() if s < strength[key[2]]
                    for a in rows.get((key[0], key[1], r),
                                      {"anchors": []})["anchors"] if a]
        if stronger:
            row = rows[key]
            row["anchors"] = [a for a in row["anchors"]
                              if a and not any(a == b or _anchor_within(b, a)
                                               for b in stronger)]
            if not row["anchors"]:
                del rows[key]

    # the curated rows: alias-folded, override-on-same-anchor, source llm
    aliases = catalog.concept_aliases(con)
    for doc, layer_rows in (curated or {}).items():
        if doc not in info:
            continue
        for lr in layer_rows:
            concept = aliases.get(lr["concept"], lr["concept"])
            anchor = lr.get("anchor")
            role = lr.get("role") or "namner"
            for key in [k for k in rows if k[0] == concept and k[1] == doc]:
                row = rows[key]
                row["anchors"] = [a for a in row["anchors"]
                                  if a != anchor
                                  and not (a and anchor
                                           and _anchor_within(anchor, a))]
                if not row["anchors"]:
                    del rows[key]
            row = rows.setdefault((concept, doc, role),
                                  {"anchors": [], "label": lr.get("label"),
                                   "source": "llm"})
            row["source"] = "llm"
            if anchor not in row["anchors"]:
                row["anchors"].append(anchor)
            stats["curated_rows"] += 1

    # ladder assembly: root + via per (concept, doc), then keep only groups
    # that say more than a lone definition would. The root is the highest
    # *row-bearing* document reachable upward -- never a merely reachable one:
    # a lag transposes many directives, and climbing to whichever of them
    # sorts first would hang säkerhetsskyddsanalys under an unrelated
    # directive. Silent rungs still render, but only *between* row-bearing
    # rungs (off the via path) -- "förordningen är tyst" -- never above the
    # ladder's top claim.
    upphavd = _repeal_dates(con, info, art)
    concept_docs = {}
    for (concept, doc, _role) in rows:
        concept_docs.setdefault(concept, set()).add(doc)
    assembled = {}
    for (concept, doc, role), row in rows.items():
        anc = ancestors.get(doc, {})
        bearing = ({doc} | set(anc)) & concept_docs[concept]
        top = min(info[d][0] for d in bearing)
        candidates = sorted(
            (d for d in bearing if info[d][0] == top),
            key=lambda d: (not any(c == concept for c, _t, _a in
                                   defs.get(d, ())), d))
        chain_root = candidates[0]
        via = anc.get(chain_root, [])
        assembled.setdefault((concept, chain_root), []).append(
            (doc, role, row, via))
    inserts = []
    for (concept, chain_root), group in sorted(assembled.items()):
        docs_in = {doc for doc, _r, _row, _v in group}
        if len(docs_in) < 2 and not any(via for _d, _r, _row, via in group):
            stats["single_dropped"] += 1
            continue
        stats["ladders"] += 1
        for doc, role, row, via in sorted(
                group, key=lambda g: (info[g[0]][0], g[0], g[1])):
            level, kind, _date, _expired, _path = info[doc]
            # natural order (1 kap. before 10 kap.), and the naturally first
            # provision is the row's primary anchor
            anchors = sorted((a for a in row["anchors"] if a),
                             key=split_numalpha) or row["anchors"]
            stated, shaken = _edge_dates(via, info, art)
            inserts.append((
                concept, doc, anchors[0],
                json.dumps(anchors[1:]) if len(anchors) > 1 else None,
                level, kind, role, row["label"], chain_root,
                json.dumps(via) if via else None, row["source"],
                stated, upphavd.get(doc), shaken))
    con.execute("DELETE FROM regleringshierarki")
    con.executemany(
        "INSERT INTO regleringshierarki (concept, doc_uri, anchor, also, "
        "level, kind, role, label, chain_root, via, source, stated, "
        "upphavd, via_amended) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        inserts)
    stats["rows"] = len(inserts)
    stats["concept_stubs"] = _synthesize_ladder_stubs(con)
    con.commit()
    return stats


def _synthesize_ladder_stubs(con):
    """Mint a stub begrepp document for every ladder concept without a page,
    mirroring `catalog.synthesize_concepts`' stub shape -- a task-D concept
    (mönstring) is referenced by no link, so that pass never sees it.
    `synthesize_concepts` runs earlier in the same relate block and clears
    unreferenced stubs, so these re-mint on every rebuild; the stable
    content_hash keeps the index from churning over them."""
    have = {u for (u,) in con.execute(
        "SELECT uri FROM documents WHERE source = 'begrepp'")}
    prefix = catalog.BASE + "begrepp/"
    want = {u for (u,) in con.execute(
        "SELECT DISTINCT concept FROM regleringshierarki")
        if u.startswith(prefix)
        and catalog.RE_CONCEPT.match(u[len(prefix):].replace("_", " "))}
    new_stubs = sorted(want - have)
    con.executemany(
        "INSERT OR IGNORE INTO documents "
        "(uri, source, kind, label, title, path, source_url, content_hash, "
        " expired, display) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(uri, "begrepp", "begrepp", name, name, "", None,
          catalog.content_hash(("begrepp-stub\x1f" + name).encode()),
          None, name)
         for uri in new_stubs
         for name in [uri[len(prefix):].replace("_", " ")]])
    return len(new_stubs)


def _repeal_dates(con, info, art):
    """doc -> its repeal date, for every ranked document known repealed:
    `documents.expired` where the source stamps it (sfs, eurlex); for a
    föreskrift -- where `expired` is NULL even when repealed -- the inbound
    rpubl:upphaver link, dated by the repealing document's own
    ikraftträdande (its `documents.date` is the beslutsdatum, which would
    print "upphävd" while the rules were still in force). An undated repeal
    is the sentinel, never a wrong date (PRD §9.5)."""
    out = {uri: expired
           for uri, (_l, _k, _d, expired, _p) in info.items() if expired}
    for repealed, repealer in con.execute(
            "SELECT to_root, from_uri FROM links "
            "WHERE predicate = 'rpubl:upphaver'"):
        if repealed in info and repealed not in out and repealer in info:
            ikraft = (art(repealer).get("metadata") or {}).get(
                "ikrafttradandedatum")
            out[repealed] = ikraft or catalog.EXPIRED_UNDATED
    return out


def _edge_dates(via, info, art):
    """(stated, via_amended) for one row's upward path: `stated` is the date
    of the document at the lower end of the top edge (the one that read the
    delegation); `via_amended` the latest ikraftträdande of an SFS amendment
    that touched any upward pin on the path after that edge's lower document's
    date -- the MCFFS 2026:11 / SFS 2026:623 case: the pinned paragrafer were
    amended after the föreskrift was decided, so the edge is dated, not
    broken. Read off the pinned fragment's own lydelse trailer; no trailer
    means NULL -- "not known shaken", never a guess."""
    if not via:
        return None, None
    stated = info[via[-1][0]][2]
    shaken = None
    for lower, _lpin, upper, upin, _pred in via:
        lower_date = info[lower][2]
        if not (upin and lower_date):
            continue
        upper_art = art(upper)
        if not upper_art.get("amendments"):
            continue
        m = RE_LYDELSE_TRAILER.search(
            text.fragment_text(upper_art, upin).strip())
        if not m:
            continue
        ikraft, _fa = history.amendment_info(upper_art).get(m.group(1),
                                                            (None, None))
        if ikraft and ikraft > lower_date and (not shaken or ikraft > shaken):
            shaken = ikraft
    return stated, shaken


# --------------------------------------------------------------------------
# the read side: what a page needs, shaped once
# --------------------------------------------------------------------------

def concept_ladders(con, concept):
    """The ladders one begreppssida shows: ``[{chain_root, root_kind,
    kompletterar, anchor_id, rungs}]``, one per chain_root, rungs in rung
    order. A rung is a table row (doc, anchor, also, level, kind, role,
    label, upphavd, via_amended, silent=False) or a synthesized silent one --
    a document on a via path that carries no row for the concept
    ("förordningen är tyst" is information, not an embarrassment), read out
    of the stored paths rather than minted as rows."""
    rows = con.execute(
        "SELECT doc_uri, anchor, also, level, kind, role, label, chain_root, "
        "via, upphavd, via_amended FROM regleringshierarki WHERE concept = ? "
        "ORDER BY chain_root, level, doc_uri", (concept,)).fetchall()
    ladders = []
    for root in dict.fromkeys(r[7] for r in rows):
        group = [r for r in rows if r[7] == root]
        row_docs = {r[0] for r in group}
        on_paths = {}      # doc -> the edge below it, off the via paths
        kompletterar = False
        for r in group:
            for edge in json.loads(r[8]) if r[8] else []:
                on_paths.setdefault(edge[2], edge)
                if edge[2] == root and edge[4] == "rinfoex:kompletterar":
                    kompletterar = True
        silent_docs = [d for d in on_paths if d not in row_docs]
        silent_info = {uri: (level, kind, expired) for uri, level, kind, expired
                       in _docs_meta(con, silent_docs)} if silent_docs else {}
        rungs = [{"doc": r[0], "anchor": r[1],
                  "also": json.loads(r[2]) if r[2] else [],
                  "level": r[3], "kind": r[4], "role": r[5], "label": r[6],
                  "upphavd": r[9], "via_amended": r[10], "silent": False}
                 for r in group]
        rungs += [{"doc": d, "anchor": None, "also": [], "level": info[0],
                   "kind": info[1], "role": None, "label": None,
                   "upphavd": info[2], "via_amended": None, "silent": True}
                  for d, info in silent_info.items()]
        rungs.sort(key=lambda r: (r["level"], r["silent"], r["doc"]))
        names = _descriptives(con, {r["doc"] for r in rungs})
        for r in rungs:
            r["descriptive"] = names.get(r["doc"])
        root_kind = next((r["kind"] for r in rungs if r["doc"] == root), None)
        ladders.append({
            "chain_root": root, "root_kind": root_kind,
            "kompletterar": kompletterar,
            "anchor_id": "rh-" + re.sub(r"[^\w.-]", "-", catalog.local(root)),
            "rungs": rungs})
    return ladders


def _docs_meta(con, uris):
    marks = ",".join("?" * len(uris))
    return con.execute(
        "SELECT d.uri, lvl.lvl, d.kind, d.expired FROM documents d "
        "JOIN (" + catalog._LEVEL_SELECT + ") lvl ON lvl.uri = d.uri "
        "WHERE d.uri IN (%s)" % marks, list(uris)).fetchall()


def _descriptives(con, uris):
    return dict(con.execute(
        "SELECT uri, descriptive FROM documents WHERE uri IN (%s)"
        % ",".join("?" * len(uris)), list(uris)).fetchall())


def provision_index(con, target_uris=None):
    """doc_uri -> [(anchor, concept, ladder anchor_id)] for the rail: the
    provisions that carry a regleringshierarki row (also-anchors included --
    a sector restatement is the same ladder). Row anchors sit deeper than the
    rail's panel nodes (a definition anchors at K1P2S1N10, the panel at
    K1P2), so the margin matches by containment over this per-document list
    rather than by exact key. Scoped by `target_uris` like the Site's other
    cross-content indexes, so a one-page render does not pay for the
    corpus."""
    out = {}
    for doc, anchor, also, concept, root in con.execute(
            "SELECT doc_uri, anchor, also, concept, chain_root "
            "FROM regleringshierarki"):
        if target_uris is not None and doc not in target_uris:
            continue
        aid = "rh-" + re.sub(r"[^\w.-]", "-", catalog.local(root))
        for a in [anchor] + (json.loads(also) if also else []):
            entry = (a, concept, aid)
            if a and entry not in out.setdefault(doc, []):
                out[doc].append(entry)
    return out


def fyller_ut_index(con, target_uris=None):
    """doc_uri -> the upward chain a föreskrift page's per-chapter line
    states where no concept anchors the subject (PRD §8): ``{"forordning":
    (uri, pin), "lag": (uri, pin), "direktiv": [(uri, article, pinpoint)]}``.
    Only for a föreskrift whose chain is unambiguous -- exactly one pinned
    förordning provision, with exactly one derived lag edge. Splitting
    several pins over the chapters they empower is the ai-* pass's residue
    (PRD phase 3), so an ambiguous chain yields no line rather than a wrong
    one."""
    per_fs = {}
    for fs, u, upin in con.execute(
            "SELECT lower_uri, upper_uri, upper_pin FROM norm_chain "
            "WHERE lower_level = 3 AND upper_level = 2 "
            "AND upper_pin IS NOT NULL "
            "AND predicate = 'rpubl:bemyndigande'"):
        if target_uris is not None and fs not in target_uris:
            continue
        per_fs.setdefault(fs, set()).add((u, upin))
    deleg = {}
    for l, lp, u, upin in con.execute(
            "SELECT lower_uri, lower_pin, upper_uri, upper_pin "
            "FROM delegation_edge"):
        deleg.setdefault((l, lp), []).append((u, upin))
    out = {}
    for fs, pins in per_fs.items():
        if len(pins) != 1:
            continue
        clause = next(iter(pins))
        laws = deleg.get(clause, [])
        if len(laws) != 1:
            continue
        lag, lag_pin = laws[0]
        direktiv = con.execute(
            "SELECT directive, article, pinpoint FROM genomforande "
            "WHERE sfs_uri = ? AND sfs_anchor = ?",
            (lag, lag_pin)).fetchall() if lag_pin else []
        out[fs] = {"forordning": clause, "lag": (lag, lag_pin),
                   "direktiv": [tuple(d) for d in direktiv]}
    return out
