"""`lagen forarbete ai-genomforande <prop-basefile> [<CELEX> ...]` -- author,
with an LLM, the directive->paragraf transposition map for the EU directive(s) a
proposition transposes, out of its författningskommentar.

The mechanical route (`kommentar.extract`) reads only the formulaic sentence
"Paragrafen genomför artikel N i direktivet". A proposition states many
transpositions less rigidly ("Bestämmelsen motsvarar artikel 23.4", "Genom
paragrafen genomförs artikel 8", or a whole-law "lagen genomför direktivet"
that names no article), and many paragrafer are purely national (administration,
överklagande, bemyndiganden) with no directive correspondence at all. There is
no 1:1 mapping: a paragraf transposes zero, one or several articles, an article
spreads over several paragrafer, and most of a directive's articles need never
appear.

So, like `sfs.correspond` and `eurlex.annotate`, an explicit opt-in LLM pass
reads the författningskommentar and classifies which of the proposition's
directives' articles each paragraf transposes. The call granularity is *one LLM
call per proposed law* (a huge FK is chunked by a character budget), like
`sfs.correspond` -- the whole law's commentary in one prompt gives the model the
cross-paragraf context an isolated paragraf lacks, at a fraction of the calls.
Two groundings keep it honest despite the batch size:

  * paragraf identity is never asked of the model -- each FK entry (already
    segmented by `fk.extract` into {law, chapter, paragrafer, kommentar}) is
    given a stable id (E1, E2, …) and the model returns the *id*, so it can only
    ever pick a real paragraf, never invent one;
  * a proposition often transposes several directives at once (a financial-
    sector omnibus, the NIS2+CER pair); each directive is tagged (A, B, …) with
    its own article inventory and the model must name the tag, reading which
    directive each sentence cites.

Every returned mapping is validated: a known entry id, a known directive tag,
every cited article resolving (via `kommentar.parse_articles`, so a bare "21", a
pinpoint "21.1–21.3" and a lettered "23.4 a" all reduce to a real article of that
directive) to that directive's inventory, and a supporting quote occurring in the
entry's own commentary. An item failing any check is dropped, not stored (the
`sfs.correspond` discipline). The result is a `.ann` layer in the curated store
(lib.annstore), a richer superset of the artifact's mechanical `implements` that
`genomforande.resolve` prefers at relate time.

NOTE ON ENDPOINT: validated on the whole cybersäkerhetslag prop (43 candidate
§§, 2 calls) against both the Berget hosted API (`openai/gpt-oss-120b`: 40
paragrafer, 0 rejected, ~47 s) and the local llama.cpp Qwen (`qwen3.6-35b-a3b`:
same 40 paragrafer, ~3:46, **byte-identical across repeat runs**). Both endpoints
give equivalent quality; the difference is speed -- Berget is faster and has no
`--parallel 1` serial cap, so a corpus run should prefer it, but local is
perfectly usable. (An earlier read that local was "nondeterministic" was a
misdiagnosis of the pinpoint-validation bug below: the model intermittently
emitted a bare "21" vs a pinpoint "21.1", and only the bare form passed the
old validator, so the *kept* count swung while the mappings were stable.)
`MAX_TOKENS` must be generous -- a reasoning model spends the budget reasoning
before it emits the JSON, and a truncated reply (finish_reason "length") loses
the whole batch. The LLM is called only from here, never from
parse/relate/generate.
"""

import re
from pathlib import Path

from .. import config
from ..lib import compress, lagrum, layout, llm
from ..lib.util import normalize_fold as _norm
from . import kommentar
from .structure import flatten

PROMPT = Path(__file__).with_name("genomforande_prompt.txt")
CELEX_BASE = lagrum.CELEX_BASE
# the optional Swedish-side pinpoint within the commented paragraf, in the SFS
# element-id syntax the statute artifacts mint ("S1" = första stycket,
# "S3N2" = tredje stycket 2 p) -- shape-checked here, existence-checked against
# the published law at resolve time
RE_SFS_PINPOINT = re.compile(r"S\d+(?:N\d+[a-z]?)?")
QUOTE_KEY = 30         # chars of a quote's normalised prefix that must occur in FK
# the FK commentary budget per LLM call (chars). A law whose candidate entries
# exceed it is split into several calls at entry boundaries. Sized so every
# real law's whole commentary is ONE call (largest measured: elmarknadslagen,
# ~105k chars): the 2025/26 golden benchmark showed splitting a law into
# isolated slices costs precision -- on the two biggest props the sliced runs
# produced 9 and 11 false positives where whole-law calls produced 0 and 6,
# with recall unchanged. The cap exists only so a pathological FK still fits
# the smallest deployment target (the local server's 64k context: ~43k prompt
# tokens + directive catalog + reply). Config knob `llm_batch_chars` /
# $LLM_BATCH_CHARS: a giant FK plus a large directive catalog (momslagen
# 2022/23:46, LOU/LUF/LUK 2015/16:195) overflows the local 64k context at the
# default and truncates -- rerun those with a smaller budget
# (LLM_BATCH_CHARS=60000) instead of losing the prop.
BATCH_CHARS = config.LLM_BATCH_CHARS
# the completion-token ceiling per call. A ceiling, not a target -- the model
# stops when its answer is done and unused budget costs nothing, so this is
# sized only to (a) fit the longest-reasoning model's chain (Kimi-K2.6
# truncated a full BATCH_CHARS batch at 16k where gpt-oss used ~5k; local
# Qwen3.6 thinking at temp 1.0 blew 32k on a 123-§ LUF batch of prop
# 2015/16:195 even with LLM_BATCH_CHARS=60000) and (b) still cut off a
# runaway reasoning loop. Too low and the reply truncates (finish "length")
# and the whole batch is lost.
MAX_TOKENS = 64000


def detect_directives(prop_art):
    """The base CELEX ids of every directive the proposition's mechanical
    `implements` names (pinpoint fragments stripped, deduped, sorted) -- the
    default target set when the CLI is given no explicit CELEX. Empty when the
    prop carries no genomför-direktiv statement at all (then the pass has nothing
    to map and the caller skips it)."""
    return sorted({r["directive"].rsplit("/", 1)[-1].split("#")[0]
                   for r in prop_art.get("implements", []) if r.get("directive")})


def directive_articles(celex):
    """The article inventory of a directive from its parsed eurlex artifact:
    {num -> heading}. Reads the eurlex artifact as data (as `genomforande` reads
    SFS artifacts), never importing the eurlex vertical. Raises if the directive
    is not parsed -- the model must be given a real article list to validate
    against, never a guessed range."""
    path = layout.artifact("eurlex", celex)
    assert compress.exists(path), \
        "%s: no parsed eurlex artifact -- run `lagen eurlex parse %s` first" \
        % (celex, celex)
    art = compress.read_json(path)
    out = {}

    def walk(node):
        if isinstance(node, list):
            for x in node:
                walk(x)
        elif isinstance(node, dict):
            if node.get("type") == "article" and node.get("num"):
                text = node["text"]
                out[node["num"]] = (text if isinstance(text, str)
                                    else kommentar.plain(text)).strip()
            for v in node.values():
                walk(v)

    walk(art["structure"])
    # load-bearing, not an invariant: an empty inventory would silently reject
    # every mapping after full LLM spend -- raise (not assert, which -O strips;
    # rule:errors-drive-retry-use-raise)
    if not out:
        raise ValueError("%s: parsed artifact carries no articles" % celex)
    return out


def directive_aliases(prop_art):
    """base CELEX -> the Swedish aliases the proposition uses for it ("NIS 2-
    direktivet"), read from `kommentar.resolve_directives` and kept only where
    the alias resolves to *this* base CELEX (dropping the resolver's near-miss
    aliases that grabbed a co-cited act). Used only to label a directive in the
    prompt so the model can match a commentary sentence's directive name to the
    right tag; the authoritative label is always the eurlex title."""
    blocks = flatten(prop_art["structure"])
    resolved = kommentar.resolve_directives(blocks, kommentar._refparser(), "prop")
    out = {}
    for alias, uri in resolved.items():
        if alias == "default":
            continue
        base = uri.rsplit("/", 1)[-1].split("#")[0]
        out.setdefault(base, []).append(alias)
    # shortest alias first -- the terse "cer-direktivet" over "här benämnt …"
    return {b: sorted(set(a), key=len) for b, a in out.items()}


def build_catalog(celexes, prop_art):
    """[{tag, celex, uri, label, articles, valid}] for the prompt and validator:
    one entry per target directive, tagged A, B, … in `celexes` order. `label`
    joins the prop's own alias for the directive (when it has one) to the eurlex
    title, so a commentary sentence naming the directive maps cleanly to a tag."""
    aliases = directive_aliases(prop_art)
    catalog = []
    for i, celex in enumerate(celexes):
        articles = directive_articles(celex)
        art = compress.read_json(layout.artifact("eurlex", celex))
        title = (art.get("title") or celex).strip()
        alias = (aliases.get(celex) or [None])[0]
        label = "%s (%s)" % (alias, title) if alias else title
        catalog.append({"tag": chr(ord("A") + i), "celex": celex,
                        "uri": CELEX_BASE + celex, "label": label,
                        "articles": articles, "valid": set(articles)})
    return catalog


def candidate_entries(prop_art):
    """The författningskommentar entries worth sending to the model: those whose
    commentary text mentions an artikel or a direktiv at all. An entry that names
    neither cannot assert a transposition, so it is dropped from the prompt (it
    would only be output-token noise). Entries keep their `fk.extract` identity
    (law/chapter/paragrafer/page)."""
    out = []
    for e in prop_art.get("kommentarer", []):
        text = (e.get("kommentar") or "").lower()
        if ("artikel" in text or "direktiv" in text) and e.get("paragrafer"):
            out.append(e)
    return out


def batches(entries, budget=BATCH_CHARS):
    """Split the candidate entries into per-call batches: never mixing two laws
    (a batch is one proposed lag's commentary, the `sfs.correspond` granularity),
    and never exceeding `budget` chars of commentary (a huge single law is cut at
    entry boundaries so one prompt stays a size the model reasons over well and
    whose reply fits MAX_TOKENS). A single entry larger than the budget is its
    own batch rather than dropped."""
    out, cur, size, law = [], [], 0, None
    for e in entries:
        elen = len(e.get("kommentar") or "")
        if cur and (e.get("law") != law or size + elen > budget):
            out.append(cur)
            cur, size = [], 0
        cur.append(e)
        size += elen
        law = e.get("law")
    if cur:
        out.append(cur)
    return out


def directives_block(catalog):
    """The tagged directive+article catalog rendered for the prompt: each
    directive's tag and label, then its articles as `num = heading` in numeric
    order -- the controlled vocabulary the model must pick a tag and numbers
    from."""
    parts = []
    for d in catalog:
        lines = ["[%s] %s:" % (d["tag"], d["label"])]
        lines += ["  %s = %s" % (num, d["articles"][num].replace(
            "Artikel %s – " % num, "").replace("Artikel %s - " % num, ""))
            for num in sorted(d["articles"], key=lambda n: (len(n), n))]
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _where(entry):
    para = ", ".join(entry["paragrafer"])
    return ("%s kap. %s §" % (entry["chapter"], para) if entry.get("chapter")
            else "%s §" % para)


def render_batch(batch):
    """(by_id, fk_text) for one batch: each entry gets a stable id E1, E2, …
    (its 1-based position in the batch), rendered as `[E<n>] <where>` above its
    commentary. `by_id` maps the id back to the entry so a returned mapping
    resolves to a real, already-segmented paragraf -- the model never emits
    paragraf identity, only picks an id."""
    by_id, blocks = {}, []
    for i, e in enumerate(batch, 1):
        eid = "E%d" % i
        by_id[eid] = e
        blocks.append("[%s] %s\n%s" % (eid, _where(e), e["kommentar"]))
    return by_id, "\n\n".join(blocks)


def build_prompt(fk_text, block):
    return (PROMPT.read_text()
            .replace("[[DIRECTIVES]]", block)
            .replace("[[FK]]", fk_text))


def _articles(model_articles, valid):
    """(pinpoints, articles) for one mapping's `articles` field, or None if any
    cited article is not a real article of the directive. The field's items may
    be bare numbers ("21"), pinpoints ("21.1–21.3", "2.2 f") or lettered
    ("23.4 a"); `kommentar.parse_articles` -- the same golden parser the
    mechanical route uses -- reduces them to base article numbers (validated
    against `valid`) and expanded pinpoints. Returning None rejects the whole
    mapping: a cited article outside the inventory is a misread, not a partial."""
    pins, bases = kommentar.parse_articles(", ".join(str(a) for a in model_articles))
    if not bases or any(b not in valid for b in bases):
        return None
    return pins, bases


def validate(reply, by_id, catalog):
    """Parse and content-check one batch reply into the mappings that check out:
    a known entry id, a known directive tag, every cited article resolving to
    that directive's inventory (`_articles`), and a supporting quote occurring in
    *that entry's* commentary (30-char normalised prefix). A mapping failing any
    check is dropped, never stored (the batch's one bad row must not poison the
    rest). The reply is read via `llm.json_values`, so a model that appends a
    second JSON object or trailing prose after a complete answer loses nothing
    -- every parseable object's `mappings` are merged and the per-item checks
    below guard correctness. Raises ValueError only on a structurally unusable
    reply (no object with a `mappings` list, or no leading JSON at all -- a
    JSONDecodeError is a ValueError) so `llm.author` retries the whole batch;
    per-item faults drop."""
    by_tag = {d["tag"]: d for d in catalog}
    payloads = [v for v in llm.json_values(reply)
                if isinstance(v, dict) and isinstance(v.get("mappings"), list)]
    if not payloads:
        raise ValueError("svaret saknar en 'mappings'-lista")
    items = []
    for m in (m for p in payloads for m in p["mappings"]):
        if not isinstance(m, dict):
            continue
        entry, tag = by_id.get(m.get("entry")), m.get("dir")
        quote = (m.get("quote") or "").strip()
        # an empty quote must not pass the containment check below vacuously
        # (""[:QUOTE_KEY] is "" and "" occurs in every string) -- no quote, no edge
        if entry is None or tag not in by_tag or not quote:
            continue
        resolved = _articles(m.get("articles") or [], by_tag[tag]["valid"])
        if resolved is None or _norm(quote)[:QUOTE_KEY] not in _norm(entry["kommentar"]):
            continue
        pins, bases = resolved
        item = {"entry": m["entry"], "tag": tag, "articles": bases,
                "pinpoints": pins, "partial": bool(m.get("partial")),
                "quote": quote}
        # the optional Swedish-side pinpoint (stycke/punkt within the paragraf,
        # SFS element-id syntax: "S1", "S3N2"). Forgiving: a malformed value is
        # disregarded, never a reason to drop the mapping -- the paragraf-level
        # reference stands on its own. Whether the stycke exists in the
        # *published* law is checked at resolve time (genomforande.resolve),
        # not here: the FK describes a proposed text with no SFS artifact yet.
        if RE_SFS_PINPOINT.fullmatch(str(m.get("sfs") or "")):
            item["sfs"] = m["sfs"]
        items.append(item)
    return items


def edges_for(item, by_id, catalog):
    """One validated mapping -> the `implements`-shaped edges it yields: one per
    commented paragraf of its entry (an entry commenting "5–7 §§" fans out). The
    shape mirrors exactly what `kommentar.extract` writes, so `genomforande.
    resolve` consumes an LLM edge and a mechanical one identically."""
    by_tag = {d["tag"]: d for d in catalog}
    entry, d = by_id[item["entry"]], by_tag[item["tag"]]
    articles, pinpoints = item["articles"], item["pinpoints"]
    return [{
        "predicate": "rpubl:genomforDirektiv",
        "directive": d["uri"],
        "articles": articles,
        "pinpoints": pinpoints,
        "uris": [d["uri"] + "#" + a for a in articles],
        "partial": item["partial"],
        "law": entry.get("law"),
        "chapter": entry.get("chapter"),
        "paragraf": para,
        **({"sfs": item["sfs"]} if item.get("sfs") else {}),
        "sentence": item["quote"],
        "page": entry.get("page"),
    } for para in entry["paragrafer"]]


def annotate(prop_art, celexes, progress=None):
    """Classify the proposition's författningskommentar against the directives
    `celexes`, one LLM call per batch (per proposed law, `batches`), and build the
    `.ann` genomförande layer payload. `progress(i, n, label)` is called before
    each batch for the CLI's per-batch line. Returns (payload, stats); the caller
    writes the payload via annstore. Reading the proposition artifact and the
    directive inventories both live here, in the vertical that owns propositioner
    and its EU-implementation semantics."""
    catalog = build_catalog(celexes, prop_art)
    block = directives_block(catalog)
    candidates = candidate_entries(prop_art)
    groups = batches(candidates)
    edges = []
    for i, batch in enumerate(groups):
        by_id, fk_text = render_batch(batch)
        if progress:
            progress(i, len(groups), "%s (%d §§)"
                     % (batch[0].get("law") or "?", len(batch)))
        items = llm.author(build_prompt(fk_text, block),
                           lambda reply, b=by_id: validate(reply, b, catalog),
                           max_tokens=MAX_TOKENS)
        edges += [e for item in items for e in edges_for(item, by_id, catalog)]
    payload = {"genomforande": {"directives": [d["uri"] for d in catalog],
                                "proposition": prop_art["uri"], "edges": edges}}
    per_dir = {}
    for e in edges:
        per_dir.setdefault(e["directive"], set()).update(e["articles"])
    stats = {"batches": len(groups), "candidates": len(candidates),
             "mapped_paragrafer": len({(e["law"], e["chapter"], e["paragraf"])
                                       for e in edges}),
             "edges": len(edges), "directives": len(catalog),
             "articles_covered": sum(len(v) for v in per_dir.values())}
    return payload, stats
