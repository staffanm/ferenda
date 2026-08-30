"""The regleringshierarki LLM passes (PRD phase 3): tasks A, B and C over one
chain component, in single-call or batched mode.

All three tasks are closed. The model never chooses which documents to
compare -- the chain does -- and never writes a name: every accepted output
is a verbatim substring of its own input (checked, and a discarded output is
a counted miss, never a retry loop), or an index into an offered list.

  A  subject span   one delegation clause -> the noun phrases it delegates
  B1 alignment      one phrase + the chain's terms -> an index or "inget"
  B2 rung probe     one concept + a document's outline -> a fragment id
  C  role           one (provision, term) -> one label from the fixed set
  D  chain subject  one chain's outlines -> the subject, a verbatim span
                    (PRD §5 rule 4 as relaxed 2026-08-28: a chain no rung
                    defines may be named by its own text, never composed)

Batching follows ``forarbete/aigenomforande.py``: stable ids per item, the
model answers per id, validation drops a bad item without poisoning its
batch (rule:errors-drive-retry-use-raise applies only to a structurally
unusable reply, which `llm.author` retries once)."""

import hashlib
import re
import time

from . import annstore, catalog, concepts, llm, text
from .hierarki import RE_LOPTEXT_PHRASE, _anchor_within
from .markdown import begrepp_uri
from .util import normalize_fold

ROLE_WORDS = {"definierar": "definierar", "ålägger": "alagger",
              "alagger": "alagger", "delegerar": "delegerar",
              "detaljerar": "detaljerar", "nämner": "namner",
              "namner": "namner", "vet ej": "namner"}
MAX_TOKENS = 16384      # a reasoning model spends budget before the JSON
BATCH_ITEMS = 12        # items per batched call -- a reasoning model spends
                        # completion budget per item, and an unbounded batch
                        # overflowed max_tokens on its first real chain
OUTLINE_CHARS = 110     # per-fragment slice in a B2 outline
D_OUTLINE_ROWS = 40     # per-document rows in a task-D chain outline
B2_MAX_OUTLINE = 600    # outline rows a probe may carry; socialförsäkrings-
                        # balken's full fragment list was 293k tokens -- a
                        # document past this cap is skipped and counted, never
                        # silently truncated
RE_LEAF_ANCHOR = re.compile(r"S\d|N\d|R\d")   # stycke/punkt/rubrik levels


def _groups(items, batched):
    if not batched:
        return [[i] for i in items]
    return [items[i:i + BATCH_ITEMS]
            for i in range(0, len(items), BATCH_ITEMS)]

PROMPT_A = """Du läser bestämmelser ur svenska författningar. Varje bestämmelse ger en eller flera myndigheter rätt att meddela föreskrifter.

Ange för varje bestämmelse varje sakämne som bemyndigandet avser, som en ordagrann fras hämtad ur bestämmelsens egen text -- kortast möjliga fras som namnger ämnet. Ta inte med myndighetsnamn, lagnamn eller paragrafhänvisningar. En bestämmelse som bara ger rätt att meddela verkställighetsföreskrifter, utan namngivna ämnen, får en tom lista.

%s

Svara med enbart JSON, ett objekt med en nyckel per bestämmelse:
{"K1": ["fras", ...], "K2": [], ...}"""

PROMPT_B1 = """Nedan finns en lista begrepp (kandidater) ur en svensk regelkedja, och ett antal textställen ur författningar i samma kedja. Avgör för varje textställe vilket kandidatbegrepp det handlar om.

Kandidater:
%s
%d. inget av begreppen

%s

Svara med enbart JSON, ett objekt med en nyckel per textställe:
{"T1": {"val": 2, "fras": "..."}, ...}
"val" är kandidatens nummer (eller numret för "inget av begreppen").
"fras" är den ordagranna fras i textstället som motsvarar begreppet; utelämna den vid "inget"."""

PROMPT_B2 = """Nedan finns en innehållsöversikt över %s -- varje rad är ett avsnitts id följt av dess inledande ord.

%s

Ange för varje begrepp nedan vilket avsnitt som definierar, ålägger skyldigheter kring eller preciserar begreppet. Om inget avsnitt gör det, svara "inget". Ordet kan ha en annan form eller ett annat namn i dokumentet.

Begrepp:
%s

Svara med enbart JSON: {"begreppet": {"id": "23", "fras": "den ordagranna fras i avsnittsraden eller begreppet"}, ...} eller {"begreppet": {"id": "inget"}}"""

PROMPT_D = """Nedan finns innehållsöversikter för författningarna i en regelkedja: en EU-rättsakt, en lag, en förordning och/eller myndighetsföreskrifter som hänger ihop genom bemyndiganden. Varje rad är ett avsnitts id följt av dess inledande ord.

%s

Vilket sakämne följer kedjan genom nivåerna? Sök det begrepp som den nedersta författningen preciserar och som en högre nivå ålägger skyldigheter kring eller delegerar föreskriftsrätt om. Det är det begrepp en läsare skulle slå upp -- inte kedjans övergripande tema och inte vad någon enskild författning handlar om.

Exempel: i en kedja där förordningen delegerar "föreskrifter om vad som utgör en betydande incident" och föreskriften anger tidsgränser för sådana incidenter är svaret "betydande incident" -- inte "cybersäkerhet".

Svara med högst tre ordagranna fraser hämtade ur texterna ovan (kortast möjliga fras som namnger ämnet). Ta inte med myndighetsnamn eller författningsnamn.

Svara med enbart JSON: {"amnen": ["fras", ...]}"""

PROMPT_C = """Nedan finns bestämmelser ur svenska författningar, var och en med ett begrepp. Ange vilken roll bestämmelsen har för begreppet:

definierar -- anger vad begreppet betyder
ålägger -- knyter en skyldighet eller rättighet till begreppet
delegerar -- ger en myndighet rätt att meddela föreskrifter om begreppet
detaljerar -- preciserar begreppet eller gör det mätbart
nämner -- använder begreppet utan något av ovanstående

%s

Svara med enbart JSON, ett objekt med en nyckel per bestämmelse:
{"R1": "delegerar", ...}. "vet ej" är tillåtet."""


def _block(bid, body):
    return "[%s]\n%s\n[/%s]" % (bid, body, bid)


def _reply_json(reply):
    vals = [v for v in llm.json_values(reply) if isinstance(v, dict)]
    if not vals:
        raise ValueError("reply holds no JSON object")
    out = {}
    for v in vals:
        out.update(v)
    return out


# --------------------------------------------------------------------------
# task A -- subject spans
# --------------------------------------------------------------------------

def run_a(clauses, batched, stats, cache=None, progress=None):
    """clauses: [(doc, anchor, text)] -> {(doc, anchor): [span, ...]}.
    Batched: all clauses in one call; single: one call per clause."""
    out = {}
    groups = _groups(clauses, batched)
    for group in groups:
        by_id = {"K%d" % (i + 1): c for i, c in enumerate(group)}
        prompt = PROMPT_A % "\n\n".join(
            _block(bid, c[2]) for bid, c in by_id.items())

        def validate(reply, by_id=by_id):
            got = _reply_json(reply)
            result = {}
            for bid, (doc, anchor, ctext) in by_id.items():
                spans = got.get(bid, [])
                if not isinstance(spans, list):
                    # a malformed or missing per-clause answer is a counted
                    # miss, never a silent "no subjects" -- the module's
                    # contract, and what b1/b2/c already do
                    stats["a_discarded"] += 1
                    spans = []
                kept = []
                for s in spans:
                    if isinstance(s, str) \
                            and normalize_fold(s) in normalize_fold(ctext):
                        kept.append(" ".join(s.split()))
                    else:
                        stats["a_discarded"] += 1
                result[(doc, anchor)] = kept
            return result

        out.update(_call(prompt, validate, stats, "a", cache, progress))
    return out


# --------------------------------------------------------------------------
# task B1 -- phrase alignment
# --------------------------------------------------------------------------

def run_b1(units, candidates, batched, stats, cache=None,
           progress=None):
    """units: [(doc, anchor, phrase, context_text)] against the ordered
    candidate term list -> {(doc, anchor, phrase): term or None}."""
    if not units or not candidates:
        return {}
    menu = "\n".join("%d. %s" % (i + 1, t) for i, t in enumerate(candidates))
    none_ix = len(candidates) + 1
    out = {}
    groups = _groups(units, batched)
    for group in groups:
        by_id = {"T%d" % (i + 1): u for i, u in enumerate(group)}
        prompt = PROMPT_B1 % (menu, none_ix, "\n\n".join(
            _block(bid, u[3][:900]) for bid, u in by_id.items()))

        def validate(reply, by_id=by_id):
            got = _reply_json(reply)
            result = {}
            for bid, (doc, anchor, phrase, ctext) in by_id.items():
                v = got.get(bid)
                if not isinstance(v, dict) or not isinstance(v.get("val"), int) \
                        or not 1 <= v["val"] <= none_ix:
                    stats["b1_discarded"] += 1
                    continue
                if v["val"] == none_ix:
                    result[(doc, anchor, phrase)] = None
                    continue
                fras = v.get("fras")
                if not (isinstance(fras, str)
                        and normalize_fold(fras) in normalize_fold(ctext)):
                    stats["b1_discarded"] += 1
                    continue
                result[(doc, anchor, phrase)] = candidates[v["val"] - 1]
            return result

        out.update(_call(prompt, validate, stats, "b1", cache, progress))
    return out


# --------------------------------------------------------------------------
# task B2 -- silent-rung probe
# --------------------------------------------------------------------------

def run_b2(doc, label, outline, terms, batched, stats, cache=None,
           progress=None):
    """One document's outline probed for `terms` -> {term: fragment id}.
    `outline` is [(fragment id, opening words)]. Batched: every term in one
    call; single: one call per term."""
    if not outline or not terms:
        return {}
    ids = {fid for fid, _t in outline}
    otext = "\n".join("%s: %s" % (fid, t) for fid, t in outline)
    out = {}
    groups = _groups(terms, batched)
    for group in groups:
        prompt = PROMPT_B2 % (label, otext,
                              "\n".join("- %s" % t for t in group))

        def validate(reply, group=group):
            got = _reply_json(reply)
            result = {}
            for term in group:
                v = got.get(term)
                if not isinstance(v, dict) or not isinstance(v.get("id"), str):
                    stats["b2_discarded"] += 1
                    continue
                if v["id"] == "inget":
                    result[term] = None
                elif v["id"] in ids:
                    result[term] = v["id"]
                else:
                    stats["b2_discarded"] += 1
            return result

        out.update(_call(prompt, validate, stats, "b2", cache, progress))
    return out


# --------------------------------------------------------------------------
# task D -- the chain's subject, a verbatim span from its own text
# --------------------------------------------------------------------------

def run_d(material, stats, cache=None, progress=None):
    """One chain's document outlines -> up to three subject spans. One call
    per chain; the guard is the usual one -- every span must be a verbatim
    substring of the material the model saw, so a composed name ("brandskydd
    på passagerarfartyg") is impossible by construction."""
    folded = normalize_fold(material)

    def validate(reply):
        got = _reply_json(reply)
        spans = got.get("amnen", [])
        if not isinstance(spans, list):
            raise ValueError("amnen is not a list")
        kept = []
        for span in spans[:3]:
            if isinstance(span, str) and normalize_fold(span) in folded:
                kept.append(" ".join(span.split()))
            else:
                stats["d_discarded"] += 1
        return {"amnen": kept}

    return _call(PROMPT_D % material, validate, stats, "d",
                 cache, progress)["amnen"]


# --------------------------------------------------------------------------
# task C -- roles
# --------------------------------------------------------------------------

def run_c(units, batched, stats, cache=None, progress=None):
    """units: [(doc, anchor, term, provision_text)] -> {(doc, anchor, term):
    role} (ascii role values)."""
    out = {}
    groups = _groups(units, batched)
    for group in groups:
        by_id = {"R%d" % (i + 1): u for i, u in enumerate(group)}
        prompt = PROMPT_C % "\n\n".join(
            _block(bid, "Begrepp: %s\n%s" % (u[2], u[3][:900]))
            for bid, u in by_id.items())

        def validate(reply, by_id=by_id):
            got = _reply_json(reply)
            result = {}
            for bid, (doc, anchor, term, _t) in by_id.items():
                v = got.get(bid)
                role = ROLE_WORDS.get(normalize_fold(v)) \
                    if isinstance(v, str) else None
                if role is None:
                    stats["c_discarded"] += 1
                    continue
                result[(doc, anchor, term)] = role
            return result

        out.update(_call(prompt, validate, stats, "c", cache, progress))
    return out


def _call(prompt, validate, stats, task, cache=None, progress=None):
    """One validated LLM exchange. With a `cache` (a mapping persisting on
    write, keyed by the prompt's hash) a finished request is never paid
    twice; the bench wires one (tools/hierarki-bench). The production
    command runs cacheless and resumes at component granularity instead --
    a crash re-pays at most the in-flight component."""
    key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    if cache is not None and key in cache:
        stats[task + "_cached"] += 1
        return cache[key]
    t0 = time.perf_counter()
    p0, c0 = llm.USAGE["prompt_tokens"], llm.USAGE["completion_tokens"]
    result = llm.author(prompt, validate, max_tokens=MAX_TOKENS)
    stats[task + "_calls"] += 1
    stats[task + "_seconds"] += time.perf_counter() - t0
    stats[task + "_prompt_tokens"] += llm.USAGE["prompt_tokens"] - p0
    stats[task + "_completion_tokens"] += llm.USAGE["completion_tokens"] - c0
    if cache is not None:
        cache[key] = result
    if progress:
        progress(task)
    return result


# --------------------------------------------------------------------------
# the component pipeline
# --------------------------------------------------------------------------

def new_stats():
    s = {}
    for task in ("a", "b1", "b2", "c", "d"):
        for k in ("calls", "seconds", "prompt_tokens", "completion_tokens",
                  "discarded"):
            s["%s_%s" % (task, k)] = 0
        s[task + "_cached"] = 0
    s["b2_skipped"] = 0
    return s


def _fragments(art):
    """[(anchor, text)] of one artifact, deepest-first order preserved."""
    return [(f.split("#", 1)[1], t) for f, t in text.fragment_texts(art)]


def _deepest(anchors):
    """Only the deepest anchors of a hit set (a container repeats its
    descendants' text)."""
    return [a for a in anchors
            if not any(o != a and _anchor_within(o, a) for o in anchors)]


def run_component(con, docs, clauses, batched, cache=None,
                  progress=None):
    """The A -> mint -> verbatim -> B1 -> B2 -> C pipeline over one chain.

    `docs` are the chain's document uris in rung order; `clauses` the
    delegation clauses to read ([(doc, anchor)]). Returns (rows, stats):
    rows are (doc, anchor, term, role, label) with ascii roles."""
    stats = new_stats()
    root = catalog.data_root(con)
    paths = dict(con.execute(
        "SELECT uri, path FROM documents WHERE uri IN (%s)"
        % ",".join("?" * len(docs)), docs))
    arts = {uri: catalog.load_artifact(root, paths[uri])
            for uri in docs}
    frags = {uri: _fragments(arts[uri]) for uri in docs}
    labels = dict(con.execute(
        "SELECT uri, descriptive FROM documents WHERE uri IN (%s)"
        % ",".join("?" * len(docs)), docs))

    # existing terms on the chain
    terms = {}          # folded term -> display term
    defs = {}           # (doc, folded term) -> anchor
    for doc in docs:
        for _concept, anchor, term in con.execute(
                "SELECT concept, anchor, term FROM definitions "
                "WHERE from_uri = ?", (doc,)):
            folded = normalize_fold(term)
            terms.setdefault(folded, term)
            defs[(doc, folded)] = anchor

    # A: subject spans over the delegation clauses
    clause_units = [(doc, anchor, " ".join(
        text.fragment_text(arts[doc], anchor).split()))
        for doc, anchor in clauses]
    clause_units = [u for u in clause_units if u[2]]
    spans = run_a(clause_units, batched, stats, cache, progress)

    rows = {}           # (doc, anchor, folded term) -> [role, label]

    def add(doc, anchor, folded, role=None, label=None):
        row = rows.setdefault((doc, anchor, folded), ["namner", None])
        if role:
            row[0] = role
        if label and not row[1]:
            row[1] = label

    # mint a concept per span head unless it matches an existing term; the
    # clause row itself is delegerar for the subject
    # a span folds onto an existing term only when it IS that term (an
    # inflection variant, fullmatch) -- "betydande incident" contains
    # "incident" but is a distinct concept, and .search() swallowed it
    for (doc, anchor), phrases in spans.items():
        for phrase in phrases:
            folded = normalize_fold(phrase)
            hit = next((f for f in terms
                        if concepts.term_pattern(terms[f]).fullmatch(folded)),
                       None)
            key = hit or folded
            if not hit:
                terms[folded] = phrase
            add(doc, anchor, key, role="delegerar")

    # D: the chain's own subject, read off the documents' outlines -- the
    # door for a chain no rung defines (PRD §5 rule 4 as relaxed). The span
    # only mints the concept; its rows come from the verbatim pass and B2
    material = []
    for doc in docs:
        # unlike the B2 probe, headings stay in: a rubrik is where a chain
        # names its subject ("Nedsättning av återbetalningsbelopp")
        rows_ = [(a, " ".join(t.split())[:OUTLINE_CHARS])
                 for a, t in frags[doc]
                 if a and not re.search(r"S\d|N\d", a)][:D_OUTLINE_ROWS]
        material.append("== %s ==\n%s" % (
            labels.get(doc) or doc,
            "\n".join("%s: %s" % r for r in rows_)))
    for phrase in run_d("\n\n".join(material), stats, cache, progress):
        folded = normalize_fold(phrase)
        if not any(concepts.term_pattern(terms[f]).fullmatch(folded)
                   for f in terms):
            terms[folded] = phrase

    # verbatim pass: every term matched over every chain document
    patterns = {f: concepts.term_pattern(t) for f, t in terms.items()}
    for doc in docs:
        for folded, pattern in patterns.items():
            # an *anchored* definition is the authoritative row and stands
            # in for the term's other occurrences. 171 of the corpus's 54,132
            # definition rows carry no anchor (the parse found the definition
            # but could not place it), and such a row can neither render on a
            # provision nor sort against the anchored ones -- it crashed the
            # ladder with "'<' not supported between NoneType and str". Those
            # fall through to the verbatim pass, which finds where the term
            # actually occurs.
            if defs.get((doc, folded)):
                add(doc, defs[(doc, folded)], folded, role="definierar")
                continue
            hits = [a for a, t in frags[doc]
                    if a and pattern.search(normalize_fold(t))]
            for a in _deepest(hits):
                add(doc, a, folded)

    # B1: the long löptext phrases nothing matched
    b1_units = []
    for doc in docs:
        for anchor, ftext in frags[doc]:
            folded_text = normalize_fold(ftext)
            for m in RE_LOPTEXT_PHRASE.finditer(folded_text):
                phrase = m.group(1)
                if any(p.match(phrase) for p in patterns.values()):
                    continue        # the verbatim pass already filed it
                if anchor and len(phrase.split()) > 2:
                    b1_units.append((doc, anchor, phrase, ftext))
    menu = [terms[f] for f in sorted(terms)]
    aligned = run_b1(b1_units, menu, batched, stats, cache, progress)
    for (doc, anchor, phrase), term in aligned.items():
        if term:
            add(doc, anchor, normalize_fold(term), role="definierar",
                label=phrase)

    # B2: chain subjects still silent in some document -- probe its outline.
    # Only the A-minted and B1-aligned subjects, never every defined term.
    subjects = {normalize_fold(p) for spans_ in spans.values()
                for p in spans_} \
        | {normalize_fold(t) for t in aligned.values() if t}
    for doc in docs:
        missing = [terms[f] for f in sorted(subjects)
                   if f in terms and not any(k[0] == doc and k[2] == f
                                             for k in rows)]
        if not missing:
            continue
        # provision-level rows only (a stycke repeats its paragraf), and a
        # document whose outline still exceeds the cap is skipped, counted
        outline = [(a, " ".join(t.split())[:OUTLINE_CHARS])
                   for a, t in frags[doc]
                   if a and not RE_LEAF_ANCHOR.search(a)]
        if len(outline) > B2_MAX_OUTLINE:
            stats["b2_skipped"] += len(missing)
            continue
        probed = run_b2(doc, labels.get(doc) or doc, outline,
                        missing, batched, stats, cache, progress)
        for term, anchor in probed.items():
            if anchor:
                add(doc, anchor, normalize_fold(term))

    # C: a role for the unsettled rows of the ladder-worthy concepts -- the
    # chain's subjects plus any term defined at two or more rungs. Every
    # other namner row stays namner (an honest role, not a gap).
    multi = {f for f in terms
             if len({d for (d, ff) in defs if ff == f}) >= 2}
    c_scope = subjects | multi
    c_units = []
    for (doc, anchor, folded), (role, _label) in sorted(rows.items()):
        if role == "namner" and folded in c_scope:
            c_units.append((doc, anchor, terms[folded], " ".join(
                text.fragment_text(arts[doc], anchor).split())[:1200]))
    for (doc, anchor, term), role in run_c(c_units, batched, stats,
                                           cache, progress).items():
        rows[(doc, anchor, normalize_fold(term))][0] = role

    return [(doc, anchor, terms[folded], role, label)
            for (doc, anchor, folded), (role, label) in sorted(rows.items())], \
        stats


# --------------------------------------------------------------------------
# the command surface: one lag's chain component, run and published
# --------------------------------------------------------------------------

def _layer_slug(uri):
    return uri.split("//", 1)[1].replace("/", "-").replace(":", "-")


def component(con, seed):
    """The seed lag's chain component: its förordningar (stated and
    delegation-derived), their gällande föreskrifter, and any EU rung --
    plus the pinned delegation clauses to read. One component is the unit
    the ai-hierarki command runs and resumes on."""
    docs = {seed}
    for (lower,) in con.execute(
            "SELECT DISTINCT lower_uri FROM norm_chain "
            "WHERE upper_uri = ? AND lower_level = 2", (seed,)):
        docs.add(lower)
    for (lower,) in con.execute(
            "SELECT DISTINCT lower_uri FROM delegation_edge "
            "WHERE upper_uri = ?", (seed,)):
        docs.add(lower)
    marks = ",".join("?" * len(docs))
    fs = [r[0] for r in con.execute(
        "SELECT DISTINCT nc.lower_uri FROM norm_chain nc "
        "JOIN documents d ON d.uri = nc.lower_uri "
        "WHERE nc.upper_uri IN (%s) AND nc.lower_level = 3 "
        "AND d.expired IS NULL" % marks, list(docs))]
    clauses = list(con.execute(
        "SELECT DISTINCT upper_uri, upper_pin FROM norm_chain "
        "WHERE upper_uri IN (%s) AND lower_level = 3 "
        "AND upper_pin IS NOT NULL" % marks, list(docs)))
    clauses += list(con.execute(
        "SELECT DISTINCT upper_uri, upper_pin FROM delegation_edge "
        "WHERE upper_uri = ? AND upper_pin IS NOT NULL", (seed,)))
    for (upper,) in con.execute(
            "SELECT DISTINCT upper_uri FROM norm_chain "
            "WHERE lower_uri IN (%s) AND upper_level = 0" % marks,
            list(docs)):
        docs.add(upper)
    return sorted(docs | set(fs)), sorted(set(clauses))


def write_layers(con, rows, force=False, all_docs=()):
    """Publish pipeline rows ([(doc, anchor, term, role, label)]) as one
    `.ann` layer per document under <tree>/hierarki/, payload key
    ``regleringshierarki`` -- what `hierarki_layers` reads back at relate.
    A verified layer refuses regeneration without `force`
    (`annstore.guard`, inside `annstore.write`). `all_docs` additionally
    writes an *empty* layer for a component document with no rows, so "every
    document has a layer" means exactly "this component is done" -- the
    resume check a weeks-long corpus run stands on."""
    sources = dict(con.execute("SELECT uri, source FROM documents"))
    by_doc = {doc: [] for doc in all_docs}
    for doc, anchor, term, role, label in rows:
        by_doc.setdefault(doc, []).append(
            {"concept": begrepp_uri(term), "term": term, "anchor": anchor,
             "role": role, "label": label})
    written = 0
    for doc, doc_rows in sorted(by_doc.items()):
        source = sources.get(doc)
        if source not in ("sfs", "foreskrift", "eurlex"):
            continue
        p = annstore.tree(source) / "hierarki" / (_layer_slug(doc) + ".ann")
        p.parent.mkdir(parents=True, exist_ok=True)
        annstore.write(p, {"regleringshierarki": {"uri": doc,
                                                  "rows": doc_rows}},
                       inputs={}, force=force)
        written += 1
    return written


def candidate_lagar(con):
    """The gällande lagar worth an ai-hierarki run, two tiers: (1) the chain
    component reaches a föreskrift, through a förordning (stated or
    delegation-derived) or directly -- the full ladders; (2) the lag has an
    EU rung above (genomför/kompletterar) even with nothing below -- the
    two-rung pairs where task B aligns renamed terms the mechanical
    genomförande pairing cannot (a directive's *riskhanteringsåtgärder* is
    the lag's *säkerhetsåtgärder*). A lag with neither gives the passes no
    ladder to build. Measured 2026-08-29: 380 + 144 = 524 of 1,768 gällande
    lagar. Returns sfs basefiles ("2018:585"), sorted."""
    rows = con.execute("""
        SELECT DISTINCT lag FROM (
          SELECT t.lag FROM (
            SELECT de.upper_uri AS lag, de.lower_uri AS f
            FROM delegation_edge de
            UNION
            SELECT nc.upper_uri, nc.lower_uri FROM norm_chain nc
            WHERE nc.upper_level = 1 AND nc.lower_level = 2) t
          JOIN norm_chain fs ON fs.upper_uri = t.f AND fs.lower_level = 3
          UNION
          SELECT nc.upper_uri AS lag FROM norm_chain nc
          WHERE nc.upper_level = 1 AND nc.lower_level = 3
          UNION
          SELECT nc.lower_uri AS lag FROM norm_chain nc
          WHERE nc.lower_level = 1 AND nc.upper_level = 0)
        JOIN documents d ON d.uri = lag
        WHERE d.expired IS NULL""")
    return sorted(uri[len(catalog.BASE):] for (uri,) in rows
                  if uri.startswith(catalog.BASE))


def layer_sources(con):
    """uri -> source for every document a hierarki layer can exist for
    (the three annstore trees). Query once and reuse -- the corpus command
    calls this before its component loop, not inside it."""
    return {uri: src for uri, src in con.execute(
        "SELECT uri, source FROM documents "
        "WHERE source IN ('sfs', 'foreskrift', 'eurlex')")}


def layer_path(source, doc_uri):
    """Where `write_layers` puts (and a resume check finds) one document's
    hierarki layer."""
    return annstore.tree(source) / "hierarki" / (_layer_slug(doc_uri) + ".ann")
