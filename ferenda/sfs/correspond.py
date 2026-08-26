"""Derive the old-law -> new-law paragraf correspondence map for a restructured
statute, with an LLM, from the proposition's författningskommentar.

When a law is re-enacted in a new structure (säkerhetsskyddslagen 1996:627 ->
2018:585), the proposition's FK states, paragraf by paragraf, how the new
provision relates to the old one -- but in heterogeneous prose ("som delvis
motsvarar 1 § 1996 års säkerhetsskyddslag", "har förts över från 10 § andra
stycket …", "saknar motsvarighet") that a regex cannot reliably tell apart from
an incidental citation or a negation. So, like eurlex ai-annotate, an explicit
opt-in LLM pass reads the (paragraf-segmented) FK plus both laws' paragraf
inventories and emits {newParagraf, oldParagraf, relation, scope} edges.

Every edge is mechanically validated before it is kept: both endpoints must be
real paragrafs in the two laws, the relation/scope must be in the controlled
vocabulary, and the model's supporting quote must occur in the FK text -- so a
hallucinated anchor or an invented sentence is dropped, not stored. The result
is written by the caller as a `.corr` layer in the curated store (lib.annstore),
the mirror of the förarbete genomför edges.

The LLM is called only from `correspond`, reached only by `lagen sfs
ai-correspond`; never from a corpus-wide parse/relate/generate.
"""

import json
import re
from pathlib import Path

from ..lib import llm
from ..lib.util import normalize_fold as _norm

PROMPT = Path(__file__).with_name("correspondence_prompt.txt")
RELATIONS = {"motsvarar", "overfort"}
SCOPES = {"helt", "i_sak", "i_huvudsak", "delvis", None}
QUOTE_KEY = 40         # chars of a quote's normalised prefix that must occur in FK


def paragraf_index(art):
    """Ordered [(anchor, label)] for every paragraf in an SFS artifact: 'K3P17' ->
    '3 kap. 17 §' in a chaptered law, 'P32a' -> '32 a §' in a flat one. The anchor
    is the fragment id the renderer emits; the label is how the FK names the
    paragraf, so the model can map a cited "32 a §" onto the right anchor."""
    out = []

    def walk(nodes, kap=None):
        for n in nodes:
            if n.get("type") == "kapitel":
                walk(n.get("children", []), n.get("ordinal"))
                continue
            if n.get("type") == "paragraf" and n["id"] is not None:
                # id-suppressed paragrafs (temporal/dedup, nf.IdMinter) have
                # no anchor to link to -- offering them to the model would
                # only invite edges that validate_edges must then drop
                ordn = n.get("ordinal")
                out.append((n["id"], "%s kap. %s §" % (kap, ordn) if kap
                            else "%s §" % ordn))
            if n.get("children"):
                walk(n["children"], kap)

    walk(art["structure"])
    return out


def detect_old_law(new_art):
    """The SFS uri the new statute repeals, read from its transition provisions:
    the `dcterms:references` to another whole SFS in a stycke/punkt that mentions
    'upphäv' ("Genom lagen upphävs säkerhetsskyddslagen (1996:627)"). None if no
    such reference is found -- then the caller must be given the old law."""
    found = []

    def walk(node):
        if isinstance(node, list):
            for x in node:
                walk(x)
        elif isinstance(node, dict):
            runs = node.get("text")
            if isinstance(runs, list) and "upphäv" in _norm(
                    "".join(r if isinstance(r, str) else r.get("text", "")
                            for r in runs)):
                for r in runs:
                    uri = r.get("uri", "") if isinstance(r, dict) else ""
                    if (r.get("predicate") if isinstance(r, dict) else None) \
                            == "dcterms:references" and re.search(r"/\d{4}:\d+$", uri):
                        found.append(uri)
            for key in ("children", "content"):
                walk(node.get(key, []))

    walk(new_art.get("amendments", []))
    return found[0] if found else None


def validate_edges(raw, new_anchors, old_anchors, old_uri, fk):
    """Keep only the model's edges that check out: both endpoints are real
    paragrafs, relation/scope are in the controlled vocabulary, and the supporting
    quote actually occurs in the FK text (a 40-char prefix, whitespace/case
    normalised). Drops hallucinated anchors and invented quotes. Returns
    (edges, rejected)."""
    norm_fk = _norm(fk)
    edges, rejected = [], []
    for e in raw:
        new_a, old_a, key = (e.get("newParagraf"), e.get("oldParagraf"),
                             _norm(e.get("quote", ""))[:QUOTE_KEY])
        if (new_a in new_anchors and old_a in old_anchors
                and e.get("relation") in RELATIONS and e.get("scope") in SCOPES
                and key and key in norm_fk):
            edges.append({"newParagraf": new_a, "oldParagraf": old_a,
                          "oldUri": old_uri + "#" + old_a,
                          "relation": e["relation"], "scope": e.get("scope"),
                          "quote": e["quote"].strip()})
        else:
            rejected.append(e)
    return edges, rejected


def _inventory(idx):
    # dict.fromkeys dedupes the few paragraf anchors a consolidated structure
    # repeats, keeping document order, so the model isn't fed a line twice
    return "\n".join("%s = %s" % kv for kv in dict.fromkeys(idx))


def build_prompt(new_idx, old_idx, fk):
    return (PROMPT.read_text()
            .replace("[[NEW_LAW]]", _inventory(new_idx))
            .replace("[[OLD_LAW]]", _inventory(old_idx))
            .replace("[[KOMMENTAR]]", fk))


def correspond(new_art, prop_art, old_art, fk):
    """Derive and validate the correspondence edges from the proposition's
    författningskommentar text `fk` (extracted by `forarbete.kommentar.fk_section`
    -- reading the proposition artifact is förarbete's job, so build composes the
    two verticals rather than sfs importing forarbete); return (payload, stats).
    The payload is `{"correspondence": {...}}`, with the new-law paragraf anchors
    relative to `new_art` (the caller stores it keyed by the new statute)."""
    new_idx, old_idx = paragraf_index(new_art), paragraf_index(old_art)
    if not fk:
        # validated (not asserted) before the LLM spend: a missing FK
        # subsection is bad input data, not a programming bug
        # (rule:errors-drive-retry-use-raise)
        raise ValueError("no författningskommentar subsection for %s in %s"
                         % (new_art["uri"], prop_art.get("identifier")))
    raw = json.loads(llm.complete(build_prompt(new_idx, old_idx, fk)))
    edges, rejected = validate_edges(
        raw.get("correspondences", []), {a for a, _ in new_idx},
        {a for a, _ in old_idx}, old_art["uri"], fk)
    sidecar = {"correspondence": {"newLaw": new_art["uri"],
                                  "oldLaw": old_art["uri"],
                                  "proposition": prop_art["uri"], "edges": edges}}
    return sidecar, {"raw": len(raw.get("correspondences", [])),
                     "emitted": len(edges), "rejected": len(rejected)}


# --- the mechanical route: a proposition's jämförelsetabell bilagor -------
#
# When the proposition itself ships old<->new comparison tables (OSL prop
# 2008/09:150 bilaga 7/8, socialtjänstlagen prop 2024/25:89 bilaga 16/17),
# no LLM is needed: forarbete.jamforelse extracts the raw two-column rows and
# `table_correspond` reads the cells as provisions of the two laws, emitting
# the exact `.corr` payload `correspond` does. The tables map at stycke/punkt
# granularity; anchors exist at paragraf level, so rows collapse to paragraf
# edges -- a paragraf pair whose contributing rows disagree (or that the table
# itself qualifies) is scoped "delvis".

# one reference group in a table cell: an optional "N kap." prefix and one or
# more §-ordinals ("1", "1 a", "6 och 7", "3–5") closed by § or §§ -- or, the
# tables' recurring typo, by a bare stycke qualifier ("14 kap. 10 femte
# stycket" for 14 kap. 10 § femte stycket)
RE_REF = re.compile(
    r"(?:(\d+(?:\s?[a-z])?)\s*kap\.?\s*)?"
    r"(\d+(?:\s?[a-z])?(?:\s*(?:,|och|eller|–|—|-)\s*\d+(?:\s?[a-z])?)*)"
    r"\s*(?:§§?|(?=(?:första|andra|tredje|fjärde|femte|sjätte|sjunde|åttonde"
    r"|nionde|tionde)\s+styck))")
# beyond this, a cell stops talking about the counterpart law: a soft "jfr"
# pointer, or a provision in some *other* statute ("SFS 2009:724 5 §")
RE_CUT = re.compile(r"\b(?:jfr|SFS\s+\d{4}:\d+)", re.IGNORECASE)
# a whole parenthetical naming another statute -- "(18 § SFS 2009:724)" puts
# the § *before* the SFS number, so a forward cut alone would keep its ref
RE_OTHER_LAW = re.compile(r"\([^()]*SFS\s+\d{4}:\d+[^()]*\)?")
# an explicit no-counterpart cell: a dash (also the SFB register's "--"),
# "(ny)" / bare "ny" (PBL's style), "(upphävd)" / "Upphävda" or empty --
# possibly with the opening parenthesis a "jfr" cut leaves behind ("- (")
RE_NONE = re.compile(
    r"^\s*(?:[-–—]{1,2}|\(?ny\)?|\(?[Uu]pphävda?\)?|[Uu]tgår)?\s*\(?\s*$")
# a prop-local law shorthand riding a reference ("23 kap. 3 § SBL",
# "SFBP 2 kap. 4 §") or a spelled-out other-law phrase ("11 §
# kassaregisterlagen", "2 § lagen om deklarationsombud"): the ref belongs to
# that law, not to the table's counterpart column law
RE_TAG = re.compile(r"(?<![A-ZÅÄÖa-zåäö])([A-ZÅÄÖ]{2,6})(?![A-ZÅÄÖa-zåäö])")
RE_LAWPHRASE = re.compile(r"\b(?:lagen om \S+|\w+lag(?:en)?\s*\(\d{4}:\d+\)|"
                          r"\w{4,}lagen)\b")


def _ordkey(text):
    """A kap/§ ordinal as written ("6 a", "6a") -> its comparison key."""
    return text.replace(" ", "").lower() if text else None


def _expand(ordinals):
    """One ref group's ordinal list ("1", "6 och 7", "3–5") -> the individual
    ordinals; a pure-numeric dash pair expands as a range."""
    parts = [p.strip() for p in re.split(r",|\boch\b|\beller\b", ordinals)]
    out = []
    for p in parts:
        dash_char = next((d for d in "–—-" if d in p), "-")
        lo, dash, hi = p.partition(dash_char)
        if dash and lo.strip().isdigit() and hi.strip().isdigit():
            out += [str(n) for n in range(int(lo), int(hi) + 1)]
        else:
            out.append(p)
    return [p for p in out if p]


def cell_refs(cell, kap=None, tag=None):
    """A table cell -> [((kap, para), scope)] provision references. A bare-§
    group inherits the kap of the previous group in the same cell, or the
    `kap` seed -- some tables (kommunallagen prop 2016/17:171) group rows
    under a bare "1 kap." heading row and write the cells kap-less. Scope is
    "delvis" when the text following this group (up to the next group) says
    so -- for the first group also the text before it ("delvis ny samt 1 kap.
    3 §", PBL's style), else "helt". Text after a "jfr" or another statute's
    SFS number is not about the counterpart law and is cut.

    A multi-law register cell tags each reference with a prop-local law
    shorthand ("1 kap. 1 § TL, 1 kap. 1 och 2 a §§ SBL"; "SFBP 2 kap. 4 §")
    or a spelled-out law name ("11 § kassaregisterlagen"). A group's owner is
    an adjacent-preceding tag, else the first tag after it; only untagged
    groups and groups owned by `tag` (the caller's shorthand for the
    counterpart law) are kept."""
    cell = RE_CUT.split(RE_OTHER_LAW.sub("", cell))[0]
    if RE_NONE.match(cell):
        return []
    groups = list(RE_REF.finditer(cell))
    tags = sorted([(m.start(), m.end(), m.group(1))
                   for m in RE_TAG.finditer(cell)]
                  + [(m.start(), m.end(), "\x00other-law")
                     for m in RE_LAWPHRASE.finditer(cell)])
    refs = []
    for i, m in enumerate(groups):
        kap = _ordkey(m.group(1)) or kap
        before = [t for s, e, t in tags
                  if 0 <= m.start() - e <= 2 and not cell[e:m.start()].strip()]
        # a trailing tag owns the group unless a parenthesis opens between
        # them: in "2 §, 38 § … (samt 1 § lagen om …)" the phrase owns only
        # the parenthetical's own reference, not the plain ones before it
        after = [t for s, e, t in tags
                 if s >= m.end() and "(" not in cell[m.end():s]]
        owner = before[0] if before else (after[0] if after else None)
        if owner is not None and owner != tag:
            continue
        segment = (cell[:m.start()] if i == 0 else "") \
            + cell[m.end():groups[i + 1].start() if i + 1 < len(groups)
                   else len(cell)]
        scope = "delvis" if "delvis" in segment else "helt"
        refs += [((kap, _ordkey(p)), scope) for p in _expand(m.group(2))]
    return refs


def anchor_map(idx):
    """paragraf_index output -> {(kap, para): anchor} lookup, ordinals
    normalised the way `cell_refs` produces them."""
    out = {}
    for anchor, label in idx:
        m = RE_REF.match(label)
        assert m, "unparseable paragraf label %r" % label
        out.setdefault((_ordkey(m.group(1)), _ordkey(m.group(2))), anchor)
    return out


# a table row that is a bare chapter heading ("1 kap.", "3 kap. NML"): it maps
# nothing itself but sets the kap context the following bare-§ rows inherit
RE_KAP_ROW = re.compile(r"^(\d+(?:\s?[a-z])?)\s?kap\.(?:\s+\S+)?\s*$")

# a table is kept only when its rows resolve nearly as well as the prop's
# best table does for this law pair (see table_correspond)
SELECT_MARGIN = 0.85


def _lookup(amap, ref):
    """Resolve one (kap, para) cell reference in an anchor map, absorbing the
    two ways a table and a statute disagree about chapters. A law numbered
    continuously across chapters (1967 års patentlag) keys its map kap-less,
    so a cell's redundant "2 kap." prefix must not break the lookup; the
    mirror case (a kap-less cell against a chaptered map) resolves only when
    the paragraf ordinal is unambiguous across chapters."""
    kap, para = ref
    anchor = amap.get(ref)
    if anchor is None and kap is not None:
        anchor = amap.get((None, para))
    if anchor is None and kap is None:
        hits = {a for (k, p), a in amap.items() if p == para}
        anchor = hits.pop() if len(hits) == 1 else None
    return anchor


def _row_refs(tab, tag=None):
    """The table's rows as (row, left_refs, right_refs), threading the
    left column's chapter-heading context (RE_KAP_ROW) into bare-§ cells.
    `tag` is the counterpart law's prop-local shorthand (see cell_refs)."""
    out, kap = [], None
    for row in tab["rows"]:
        m = RE_KAP_ROW.match(row[0])
        if m and "§" not in row[0]:
            kap = _ordkey(m.group(1))
            continue
        out.append((row, cell_refs(row[0], kap=kap, tag=tag),
                    cell_refs(row[1], tag=tag)))
    return out


def _score(row_refs, old_col, new_map, old_map):
    """How many of the table's rows fully resolve under the assumption that
    column `old_col` holds the old law: the row count whose left and right
    refs all land in their assigned inventories."""
    n = 0
    for _row, left_refs, right_refs in row_refs:
        refs = [left_refs, right_refs]
        old_refs, new_refs = refs[old_col], refs[1 - old_col]
        if (new_refs and old_refs
                and all(_lookup(new_map, r) for r, _ in new_refs)
                and all(_lookup(old_map, r) for r, _ in old_refs)):
            n += 1
    return n


def _old_side(tab, row_refs, old_sfs, new_map, old_map):
    """Which column (0/1) of a table holds the old law, or None when nothing
    decides (the caller skips such a table -- feeding one whose direction is
    unknown would emit reversed edges). The header cell that cites the old
    law's SFS number decides when it can ("Sekretesslag (1980:100)"); a
    header of prop-local shorthands ("Bestämmelse i NML" / "Bestämmelse i
    ML") or none at all falls back to resolution scoring: the orientation
    under which strictly more rows resolve in both inventories."""
    header = tab["header"] or ("", "")
    hits = [i for i, cell in enumerate(header) if old_sfs in cell]
    if len(hits) == 1:
        return hits[0]
    scores = [_score(row_refs, old_col, new_map, old_map)
              for old_col in (0, 1)]
    if scores[0] == scores[1]:
        return None
    return 0 if scores[0] > scores[1] else 1


def relevant_tables(tabs, old_sfs, tag=None):
    """The tables that map against the old law `old_sfs` ("1980:100"): those
    whose title-page/section prose or header cites its SFS number -- or, when
    the caller supplies the law's prop-local shorthand `tag`, whose header
    carries it ("NVL | SFB", the SFB register's per-law sections). A prop can
    carry tables for several law pairs (prop 2021/22:61 renews both the
    tobacco and the alcohol tax acts) plus EU-directive correspondence tables
    -- feeding the wrong pair's table would emit garbage edges. When nothing
    cites the number (headers of prop-local shorthands and terse titles), all
    tables are returned and orientation scoring is the only guard."""
    def cites(t):
        if old_sfs in t["text"] or old_sfs in "".join(t["header"] or ()):
            return True
        return bool(tag) and any(
            re.search(r"(?<![A-ZÅÄÖa-zåäö])%s(?![A-ZÅÄÖa-zåäö])"
                      % re.escape(tag), cell)
            for cell in (t["header"] or ()))
    cited = [t for t in tabs if cites(t)]
    return cited or tabs


def table_correspond(new_art, prop_art, old_art, tabs, tag=None):
    """The `.corr` payload from a proposition's jämförelsetabell bilagor
    (forarbete.jamforelse.tables output) -- the mechanical mirror of
    `correspond`, same payload, no LLM. Each table is oriented by `_old_side`
    (header SFS citation, else resolution scoring); a table that orients
    neither way is skipped (another law pair's table, or layout noise), and
    only if none orients does the call raise. Every cell reference is
    resolved against the two laws' paragraf inventories (an unresolvable row
    is rejected, not guessed), and paragraf pairs seen more than once keep
    the weaker scope. The caller picks the tables (`relevant_tables`) and
    passes `tag`, the old law's prop-local shorthand, when the tables ride
    their references with law tags (see cell_refs). Returns (payload,
    stats)."""
    old_sfs = old_art["uri"].rsplit("/", 1)[-1]
    new_map, old_map = (anchor_map(paragraf_index(new_art)),
                        anchor_map(paragraf_index(old_art)))
    edges, stats = {}, {"rows": 0, "none": 0, "rejected": 0, "skipped": 0}
    # orient each table and grade how well its rows resolve against this law
    # pair; a table that orients neither way, or resolves clearly worse than
    # the best one, is another pair's table (prop 2021/22:61 carries
    # paragrafnycklar for tobaksskatt, alkoholskatt *and* LSE -- the wrong
    # pair's rows still part-resolve, so a tie test alone cannot catch them)
    # or layout noise, and is skipped
    graded = []
    for tab in tabs:
        row_refs = _row_refs(tab, tag)
        old_col = _old_side(tab, row_refs, old_sfs, new_map, old_map)
        refbearing = [1 for _r, lr, rr in row_refs if lr and rr]
        frac = (_score(row_refs, old_col, new_map, old_map) / len(refbearing)
                if old_col is not None and refbearing else 0)
        graded.append((tab, row_refs, old_col, frac))
    best = max((frac for *_x, frac in graded), default=0)
    for _tab, row_refs, old_col, frac in graded:
        if old_col is None or frac < SELECT_MARGIN * best:
            stats["skipped"] += 1
            continue
        for row, left_refs, right_refs in row_refs:
            stats["rows"] += 1
            # the left column enumerates one law's provisions; the right
            # holds the counterpart -- or an explicit none-marker
            if not right_refs and RE_NONE.match(RE_CUT.split(row[1])[0]):
                stats["none"] += 1
                continue
            new_refs, old_refs = ((left_refs, right_refs) if old_col == 1
                                  else (right_refs, left_refs))
            pairs = [(na, oa, "delvis" if "delvis" in (ns, os_) else "helt")
                     for (n, ns) in new_refs for (o, os_) in old_refs
                     if (na := _lookup(new_map, n)) and (oa := _lookup(old_map, o))]
            if not pairs:
                stats["rejected"] += 1
                continue
            for na, oa, scope in pairs:
                key = (na, oa)
                quote = "%s — %s" % (row[0], row[1])
                prev = edges.get(key)
                if prev is None:
                    edges[key] = {"newParagraf": na, "oldParagraf": oa,
                                  "oldUri": old_art["uri"] + "#" + oa,
                                  "relation": "motsvarar", "scope": scope,
                                  "quote": quote}
                elif prev["scope"] != scope:
                    prev["scope"] = "delvis"
    if stats["skipped"] == len(tabs):
        raise ValueError("no jämförelsetabell orients and resolves against "
                         "old law %s" % old_sfs)
    stats["emitted"] = len(edges)
    payload = {"correspondence": {"newLaw": new_art["uri"],
                                  "oldLaw": old_art["uri"],
                                  "proposition": prop_art["uri"],
                                  "edges": list(edges.values())}}
    return payload, stats


def corr_rows(sidecar):
    """The catalog rows (new_uri, old_uri, relation, scope, prop_uri,
    ikrafttrader) for one `.corr` sidecar -- each edge's new-law paragraf
    anchor joined to the new law's uri, the old paragraf carried as its full
    uri, `ikrafttrader` only on same-law renumbering edges (relation
    "betecknas"). The relate post-pass loads these into the `correspondence`
    table (catalog.set_correspondence)."""
    c = sidecar["correspondence"]
    return [(c["newLaw"] + "#" + e["newParagraf"], e["oldUri"],
             e["relation"], e.get("scope"), c.get("proposition"),
             e.get("ikrafttrader"))
            for e in c["edges"]]


# --- the renumbering route: SFSR's own omfattning field --------------------
#
# When an amendment renumbers provisions *within* a law (RF via SFS 2010:1408:
# "nuvarande 4 kap. 4, 5, 6, 7, 8, 9, 10 §§ betecknas 4 kap. 6, 7, 10, 11,
# 12, 13, 14 §§, …"), a reference to "4 kap. 4 §" means different provisions
# depending on whether it was written before or after the amendment's entry
# into force. The register's omfattning field states the renumbering
# authoritatively, so the same-law correspondence layer is derived from it
# mechanically -- edges carry relation "betecknas" and the amendment's
# ikrafttradandedatum, which the renderer uses to split inbound references
# temporally. Old-side anchors are minted from the *old* labels and are
# deliberately NOT validated against the current inventory: a renumbered-away
# label may be gone today, or reused by a different provision -- that is the
# very ambiguity the layer records. New-side anchors of an *older*
# renumbering may likewise have been renumbered again later (RF 1976:871 ->
# 2010:1408); keeping them unvalidated is what lets the chain compose.

# one omfattning renumbering clause: "nuvarande <spec> betecknas <spec>"
# (an "; "-separated changecat may hold several, ", nuvarande"-separated;
# SFSR sometimes sets a stray comma before "betecknas")
RE_BETECKNAS = re.compile(
    r"nuvarande\s+(?P<old>.+?),?\s+betecknas\s+(?P<new>.+?)\s*$")
# a spec: "12, 13 kap." (whole chapters) | "4 kap. 4, 5 §§" | "5 a, 5 b §§"
RE_SPEC_KAP = re.compile(r"^([\d ,a-z–—-]+?)\s*kap\.?\s*$")
RE_SPEC_PARA = re.compile(
    r"^(?:([\d ]+?[a-z]?)\s*kap\.?\s*)?([\d ,a-z–—-]+?)\s*§§?\s*$")


def _ordinals(text):
    """A spec's ordinal list, ranges expanded: "4, 5, 6" -> ["4", "5", "6"],
    "2-8" -> ["2" .. "8"], "5 a" -> ["5a"]."""
    return [_ordkey(p) for p in _expand(text)]


def _anchor(kap, para=None):
    """Mint the fragment anchor for a (kap, para) label the way nf does:
    K4P6, P5b, K12 (a whole chapter)."""
    if para is None:
        return "K%s" % kap
    return ("K%sP%s" % (kap, para)) if kap else ("P%s" % para)


def parse_betecknas(omfattning):
    """The renumbering pairs of one omfattning field:
    ([(old_kap, old_para, new_kap, new_para)], [(old_kap, new_kap)]) --
    paragraf-level moves and whole-chapter moves (paras None-free ordinals,
    kap None in a flat law). Raises ValueError on a clause whose sides don't
    pair up -- a silently half-parsed renumbering would misattribute
    references (rule:errors-drive-retry-use-raise)."""
    para_moves, kap_moves = [], []
    for changecat in omfattning.split("; "):
        if not changecat.startswith("nuvarande"):
            continue
        for clause in re.split(r",\s*(?=nuvarande\s)", changecat):
            m = RE_BETECKNAS.match(clause.strip())
            if not m:
                if "betecknas" in clause:
                    # a renumbering we failed to read must not be skipped --
                    # a half-parsed omfattning misattributes references
                    # (rule:errors-drive-retry-use-raise)
                    raise ValueError(
                        "unparseable renumbering clause %r" % clause)
                continue
            old, new = m.group("old"), m.group("new")
            mk_old, mk_new = RE_SPEC_KAP.match(old), RE_SPEC_KAP.match(new)
            if mk_old and mk_new:
                olds, news = _ordinals(mk_old.group(1)), _ordinals(mk_new.group(1))
                if len(olds) != len(news):
                    raise ValueError("unpairable renumbering clause %r" % clause)
                kap_moves += list(zip(olds, news, strict=True))
                continue
            mp_old, mp_new = RE_SPEC_PARA.match(old), RE_SPEC_PARA.match(new)
            if not (mp_old and mp_new):
                raise ValueError("unparseable renumbering clause %r" % clause)
            olds = _ordinals(mp_old.group(2))
            news = _ordinals(mp_new.group(2))
            if len(olds) != len(news):
                raise ValueError("unpairable renumbering clause %r" % clause)
            para_moves += [(_ordkey(mp_old.group(1)), o,
                            _ordkey(mp_new.group(1)), n)
                           for o, n in zip(olds, news, strict=True)]
    return para_moves, kap_moves


# one item group inside an "upph."/"nya" changecat, in the three shapes SFSR
# uses: "12 kap. 8 §" / "4 kap. 8, 9 §§" (chaptered paragrafs), a bare
# "8, 9 §§" (flat law), "2, 3, 8, 9, 10, 11 kap." (whole chapters -- the kap.
# must not be followed by a paragraf list, else it is the first shape), and
# "kap. 2, 3, 8" (the "nya kap. N" form)
RE_ITEM = re.compile(
    r"(?:(?P<pkap>\d[\d ,a-z–—-]*?)\s*kap\.?\s*)?(?P<paras>\d[\d ,a-z–—-]*?)\s*§§?"
    r"|(?P<kaps>\d[\d ,a-z–—-]*?)\s*kap\.?(?!\s*\d[\d ,a-z–—-]*?\s*§)"
    r"|kap\.?\s*(?P<kaps2>\d[\d ,a-z–—-]*)")


def _listed_items(omfattning, prefix):
    """The (kap, para) items of an omfattning changecat ("upph. …"/"nya …"):
    {(kap, para)} with para None for whole chapters, kap None in a flat law.
    Only §/kap items -- rubriker and övergångsbestämmelser carry no
    anchors."""
    items = set()
    for changecat in omfattning.split("; "):
        if not changecat.startswith(prefix):
            continue
        # rubrik/övergångsbestämmelse runs carry no anchors, but a changecat
        # can resume after one ("nya 1 kap. 10 §, …, rubr. närmast före …,
        # nya kap. 2, 3, 8") -- drop the runs, not everything after them
        pieces = re.split(r",\s*(?=nya\s|rubr\.|p \d|övergångsbest)",
                          changecat)
        body = ", ".join(
            piece.removeprefix(prefix).removeprefix("nya").strip()
            for piece in pieces
            if not piece.startswith(("rubr.", "p ", "övergångsbest")))
        for m in RE_ITEM.finditer(body):
            if m.group("paras"):
                items |= {(_ordkey(m.group("pkap")), p)
                          for p in _ordinals(m.group("paras"))}
            else:
                items |= {(k, None) for k in
                          _ordinals(m.group("kaps") or m.group("kaps2"))}
    return items


def renumbering_payload(art):
    """The same-law `.corr` payload from a statute's own amendment register:
    every "nuvarande … betecknas …" clause becomes edges with relation
    "betecknas" and the amendment's ikrafttradandedatum. Whole-chapter moves
    expand per paragraf against the current artifact's inventory of the *new*
    chapter, minus everything that provably entered it after the move: the
    same amendment's "nya" provisions and explicit paragraf-move targets (an
    explicit move wins over the chapter default), and every later
    amendment's "nya" provisions and paragraf-move targets in that chapter --
    the current inventory stands in for the chapter's inventory at the time,
    so later additions must not mint edges backdated to the move. Returns
    (payload, stats)."""
    uri = art["uri"]
    idx = paragraf_index(art)
    # parse every renumbering amendment up front, in register (chronological)
    # order, so a chapter-move expansion can subtract later amendments' work
    parsed = []
    for am in art.get("amendments", []):
        props = am.get("properties", {})
        omf = props.get("rpubl:andrar") or ""
        if "betecknas" not in omf:
            continue
        para_moves, kap_moves = parse_betecknas(omf)
        parsed.append((props, omf, para_moves, kap_moves,
                       _listed_items(omf, "nya")))
    edges, stats = [], {"amendments": len(parsed), "edges": 0}
    for i, (props, omf, para_moves, kap_moves, nya) in enumerate(parsed):
        ikraft = props.get("rpubl:ikrafttradandedatum")
        amendment = props.get("dcterms:identifier")
        excluded = nya | {(nk, np) for _ok, _op, nk, np in para_moves}
        for _lp, _lo, later_moves, _lk, later_nya in parsed[i + 1:]:
            excluded |= later_nya
            excluded |= {(nk, np) for _ok, _op, nk, np in later_moves}
        para_moves = list(para_moves)
        for old_kap, new_kap in kap_moves:
            if any(k == new_kap and p is None for k, p in excluded):
                # a later amendment recreated the whole chapter: nothing in
                # today's inventory dates back to this move
                continue
            for _anchor_id, label in idx:
                mm = RE_SPEC_PARA.match(label)
                if not mm or _ordkey(mm.group(1)) != new_kap:
                    continue
                para = _ordkey(mm.group(2))
                if (new_kap, para) in excluded:
                    continue
                para_moves.append((old_kap, para, new_kap, para))
        for old_kap, old_para, new_kap, new_para in para_moves:
            edges.append({
                "newParagraf": _anchor(new_kap, new_para),
                "oldParagraf": _anchor(old_kap, old_para),
                "oldUri": "%s#%s" % (uri, _anchor(old_kap, old_para)),
                "relation": "betecknas", "scope": "helt",
                "quote": "%s: %s" % (amendment, omf[:120]),
                "ikrafttrader": ikraft})
    stats["edges"] = len(edges)
    payload = {"correspondence": {"newLaw": uri, "oldLaw": uri,
                                  "proposition": None, "edges": edges}}
    return payload, stats
