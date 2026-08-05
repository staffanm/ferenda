"""`lagen remisser ai-analyze <basefile>` -- the sole LLM pass for the remiss
corpus: map one organisation's remissvar onto the *specific sections* of the
SOU/Ds it discusses, with a per-section sentiment score and a verbatim quote.
An argument may name a single answer or a whole *ärende*, which `answers`
expands to every answer fetched for it -- the useful unit, since a remiss is
read as "what did the instances say about this betänkande".

The remiss corpus is never published as its own pages; the point of this pass is
to feed the referred förarbete's page a context rail ("here's what
Kammarkollegiet said about chapter 4"). So for one answer we load its parsed
`Remissvar` artifact, load the referred förarbete artifact for its section
outline (the nested `avsnitt` tree, each node's `id` the join anchor), and ask
the configured Berget model to place the answer's commentary on those ids -- an
overall stance plus a segment per section actually discussed. The reply is
validated strictly (every cited id must be a real section, every quote a verbatim
substring of the answer text) and written as a `.ann` layer in the curated store
(lib.annstore, the git-backed WIKI_ROOT/ann tree), so a later rendering pass
surfaces it on the förarbete page. A verified (hand-checked) layer is never
regenerated without --force.

Like every ai-* action the LLM is called only here, on an explicit analyze of a
named basefile -- never from a corpus-wide parse/relate/generate.
"""

import difflib
import json
from datetime import date
from pathlib import Path

from ..lib import annstore, compress, layout, llm
from ..lib.text import runs_text, sentences
from ..lib.util import basefile_slug, normalize_space, write_atomic
from . import download
from .model import Remiss, Remissvar, org_slug

PROMPT = Path(__file__).with_name("sentiment_prompt.txt")
OUTLINE_PLACEHOLDER = "[OUTLINE]"
TEXT_PLACEHOLDER = "[SVAR TEXT]"
# the outline lists one section per line, each led by the exact id the model must
# copy; the heading is truncated so a long förarbete's outline stays a small part
# of the prompt (the answer text is the bulk the model reasons over)
LABEL_MAX = 160
# the answer is at most a handful of pages -- far smaller than the guidance PDFs
# wiki/annotate feeds its 32000-token budget -- but the Berget model still reasons
# over the whole answer before emitting JSON, so the completion budget must cover
# a chain-of-thought plus a quote-carrying segment list (the endpoint default of
# 4096 would truncate a long reasoning trace into a `length` finish)
MAX_TOKENS = 16000
# what kind of sentence the quote is. "grund" carries the reason the instance
# gives (the reader-valuable part); "standpunkt" says the answer stated no reason
# for this section, only its position. Swedish remissvar are asymmetric here --
# an organisation argues when it objects and often just endorses when it agrees --
# so a large share of positive segments genuinely have no grounds to quote, and
# the honest answer must be available or the model will invent one.
QUOTE_TYPES = frozenset({"grund", "standpunkt"})


class Unanalyzable(ValueError):
    """The model twice failed to produce a usable analysis of one answer.

    Its own name, because the caller has to tell this apart from every other
    ValueError `analyze` can surface -- a verified layer refused by
    `annstore.guard`, a corrupt artifact failing `json.loads` -- which are
    permanent faults. Only this one is worth another draw: sampling is
    stochastic, so a re-run genuinely re-samples. Reporting the others as
    "re-run to retry" would promise a retry that can never succeed."""


# how many consecutive sentences one citation may cover. A reason usually lives
# in one sentence and sometimes runs into the next; three is enough for the
# "claim, then its ground" pattern and small enough that a rail excerpt stays an
# excerpt rather than a paragraph.
MAX_SPAN = 3
# a reworded quote is snapped back to the answer's own wording only when one unit
# is plainly the one meant: this similar, and this far clear of the runner-up.
# Calibrated over every rejected quote this corpus produced -- at 0.90/0.05 half
# recover, and those left behind are the ones whose source is itself damaged (a
# footnote spliced into the sentence, OCR debris), where "restoring the original"
# would publish the damage.
SNAP_MIN = 0.90
SNAP_MARGIN = 0.05


def _avsnitt(nodes):
    """Every `avsnitt` node in a nested förarbete `structure`, depth-first in
    document order (the sub-sections of a section follow it, before its sibling)."""
    for node in nodes:
        if isinstance(node, dict) and node.get("type") == "avsnitt":
            yield node
            yield from _avsnitt(node.get("children", []))


def section_outline(structure):
    """`(outline_text, valid_ids)` for a parsed förarbete: one line per section as
    `[<id>] <heading>` (the id the exact anchor the model copies into a segment's
    `forarbete_id`, validated against `valid_ids` on return) and the set of those
    ids. Headings are truncated -- the model needs to recognise a section, not
    read it -- mirroring wiki/annotate's `act_map`, but walking förarbete's
    `avsnitt` tree instead of eurlex's `anchored_blocks`."""
    lines, ids = [], set()
    for node in _avsnitt(structure):
        ids.add(node["id"])
        lines.append("[%s] %s" % (node["id"], runs_text(node["text"]).strip()[:LABEL_MAX]))
    return "\n".join(lines), ids


def answer_units(full_text):
    """The quotable units of an answer, flat and in document order: sentences,
    split further at the colon/dash that introduces a clause so a verdict and its
    reason are separately quotable. Paragraph boundaries end a unit whether or
    not the paragraph ended in a full stop (PDF prose often does not), so a bare
    list item is a unit like any other.

    These are what `snap_to_source` matches a reworded quote against, so the
    granularity is the granularity of the repair: too coarse and a quote that
    trimmed a lead-in cannot be recognised, too fine and two units compete."""
    return [s for para in full_text for s in sentences(para, clause_breaks=True)]


def snap_to_source(quote, units, haystack):
    """The answer's own wording for a `quote` the model reworded, or None when no
    unit is clearly the one meant.

    Candidates are filtered to those that actually occur in `haystack` first.
    Joining consecutive units with a space does *not* always reproduce the
    source: the unit split drops a letterless chunk (the dash introducing a
    bulleted ground), so a two-unit span can reassemble text that is not
    contiguous in the answer -- exactly the splice the prompt forbids. Filtering
    keeps the one guarantee this pass rests on: whatever reaches the `.ann`
    occurs verbatim in the answer.

    The model does not fabricate -- measured over every rejected quote in this
    corpus, all were 60-95%% similar to a real passage and none was invented. The
    failure is misquotation: a dropped clause, a normalised word order,
    "förordrar" for "förordar". Recovering the source wording is therefore a
    lookup, not a guess, *provided* one candidate stands clearly above the rest.

    Both bars must be cleared. `SNAP_MIN` keeps the match close enough to be the
    same passage; `SNAP_MARGIN` requires the runner-up to be visibly worse, which
    is what rules out an answer that says nearly the same thing twice. Below
    them the caller rejects, which is the right outcome: the quotes that match
    weakly are the ones whose source is itself damaged (a footnote spliced into
    the sentence, OCR debris), and "restoring" those would publish the damage."""
    scored = sorted(
        ((difflib.SequenceMatcher(None, quote, u, autojunk=False).ratio(), u)
         for u in _spans(units) if normalize_space(u) in haystack),
        key=lambda r: -r[0])
    if not scored or scored[0][0] < SNAP_MIN:
        return None
    runner_up = next((r for r, u in scored[1:] if u != scored[0][1]), 0.0)
    return scored[0][1] if scored[0][0] - runner_up >= SNAP_MARGIN else None


def _spans(units, k=MAX_SPAN):
    """Every run of 1..k consecutive units -- a quote may legitimately cover more
    than one, so the candidates must too."""
    for i in range(len(units)):
        for n in range(1, k + 1):
            if i + n <= len(units):
                yield " ".join(units[i:i + n])


def _check_scored(obj, where, valid_ids=None):
    """Shape-check one scored object (overall, or a segment when `valid_ids` is
    given): a numeric `sentiment` in [-1, 1], a `quote_type`, and -- for a
    segment -- a `forarbete_id` that is a real section. The quote is handled
    separately by `_resolved_quote`, which can repair it rather than only reject
    it. Raises `ValueError` naming the fault (fed back on the retry) so a
    hallucinated id never reaches the `.ann`."""
    if not isinstance(obj, dict):
        raise ValueError("%s is not an object" % where)
    sentiment = obj.get("sentiment")
    # bool is an int subclass; a JSON `true` must not pass as a score
    if isinstance(sentiment, bool) or not isinstance(sentiment, (int, float)):
        raise ValueError("%s has a non-numeric sentiment" % where)
    if not -1 <= sentiment <= 1:
        raise ValueError("%s sentiment %r is outside [-1, 1]" % (where, sentiment))
    # the score already carries the stance, so a quote that merely restates it
    # adds nothing; `quote_type` makes that distinction explicit rather than
    # leaving a reader (or the rail) to guess whether "Tillstyrks" is all the
    # answer said or all the model found. Naming "standpunkt" as a legitimate
    # answer is what keeps the model from inventing grounds for the many answers
    # that endorse without giving any.
    if obj.get("quote_type") not in QUOTE_TYPES:
        raise ValueError("%s has quote_type %r, expected one of %s"
                         % (where, obj.get("quote_type"), sorted(QUOTE_TYPES)))
    if valid_ids is not None and obj.get("forarbete_id") not in valid_ids:
        raise ValueError("segment cites forarbete_id %r not in the outline"
                         % obj.get("forarbete_id"))


def _resolved_quote(obj, where, units, haystack):
    """The answer's own wording for one object's quote. A quote that is already
    verbatim stands; one the model reworded is snapped back to the unit it
    plainly meant, so the layer carries the organisation's words and not the
    model's paraphrase of them. A quote matching nothing clearly raises -- the
    retry draws again, and a second failure leaves no layer at all."""
    quote = obj.get("quote")
    if not (isinstance(quote, str) and quote.strip()):
        raise ValueError("%s has an empty quote" % where)
    if normalize_space(quote) in haystack:
        return quote
    snapped = snap_to_source(normalize_space(quote), units, haystack)
    if snapped is None:
        raise ValueError("%s quote is not a verbatim substring of the answer, and "
                         "no single passage is clearly the one meant: %r"
                         % (where, quote[:80]))
    return snapped


def _validate(content, valid_ids, units, haystack):
    """Parse and shape-check the model's reply into the `.ann` payload:
    `{"overall": {...}, "segments": [...]}`. `segments` may be empty (an answer can
    be purely general). Raises `ValueError` -- not assert, per
    rule:errors-drive-retry-use-raise, the retry loop load-bears on the raise which
    `-O` would strip -- on anything malformed."""
    data = json.loads(llm.strip_fence(content))
    if not isinstance(data, dict):
        raise ValueError("response is not a JSON object")
    overall = data.get("overall")
    _check_scored(overall, "overall")
    segments = data.get("segments")
    if not isinstance(segments, list):
        raise ValueError("response lacks a segments list")
    for seg in segments:
        _check_scored(seg, "a segment", valid_ids=valid_ids)
    return {
        "overall": {"sentiment": float(overall["sentiment"]),
                    "quote": _resolved_quote(overall, "overall", units, haystack),
                    "quote_type": overall["quote_type"]},
        "segments": [{"forarbete_id": s["forarbete_id"],
                      "sentiment": float(s["sentiment"]),
                      "quote": _resolved_quote(s, "a segment", units, haystack),
                      "quote_type": s["quote_type"]} for s in segments],
    }


def _load_analysed():
    """The ärende -> last-analysed-date index, `{}` when nothing has run yet."""
    path = layout.REMISSER_ANALYSED
    return json.loads(path.read_text()) if path.exists() else {}


def mark_analysed(arende, today=None):
    """Record that ai-analyze has run over `arende`, in the index `updatable`
    reads.

    The answer layers alone cannot carry this. An ärende analysed the week it
    opened may have had no answers yet, or every answer may have failed -- either
    way it leaves no `.ann`, so it would be invisible to `--update` and the
    answers arriving later would never be picked up. That is exactly the ärende
    an update pass exists for.

    One index file rather than a marker per ärende: this is bookkeeping, not
    authored output, and the curated store is git-tracked -- a per-ärende marker
    would dirty ~2,300 files on every pass, each rewritten only to restamp its
    date."""
    index = _load_analysed()
    index[arende] = (today or date.today()).isoformat()
    layout.REMISSER_ANALYSED.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(layout.REMISSER_ANALYSED,
                 json.dumps(index, ensure_ascii=False, indent=1, sort_keys=True))


def analysed_arenden():
    """Every ärende basefile ai-analyze has run over: those in the index, plus
    those carrying at least one answer layer.

    The union is deliberate. The index is the reliable record going forward, but
    layers written before it existed are equally good evidence that the ärende
    was analysed -- reading both means the feature works retroactively instead of
    forgetting everything analysed to date. A layer's path pins the ärende
    exactly: `relpath` slugs an answer to `<typ>/<ident-slug>/<org>`, one segment
    per level whatever the identifier looked like."""
    return sorted(set(_load_analysed())
                  | {"%s/%s" % (p.parent.parent.name, p.parent.name)
                     for p in annstore.tree("remisser").rglob("*.ann")})


def updatable(today=None):
    """The ärenden an `--update` run should re-analyse: those already analysed
    once whose remissperiod has not closed.

    Answers accumulate on an ärende page for the whole period, so an analysis
    made the week a remiss opened is missing whatever arrived after it. Bounded
    by the same deadline plus grace the download side re-polls by
    (`download.still_open`) -- past that no answer is coming, and re-running
    would spend the LLM to rewrite what it already wrote. Ärenden never analysed
    at all are *not* included: this refreshes a decision already taken, it does
    not decide which ärenden are worth analysing."""
    return [a for a in analysed_arenden()
            if download.still_open(
                Remiss.from_dict(json.loads(
                    compress.read_text(layout.remisser_arende(a)))), today)]


def is_arende(basefile):
    """Whether a basefile names a whole remiss ärende rather than one answer.

    Decided by whether an ärende record exists at that basefile, because the
    shape alone cannot tell them apart: a promemoria's identifier carries its own
    slash ("pm/LI2026/01339"), so an ärende has two segments or three exactly as
    an answer does, and 1,050 of the stored ärenden are of that kind. One home
    for the rule -- `answers` expands on it and the CLI action decides on it."""
    return compress.exists(layout.remisser_arende(basefile))


def answers(basefile):
    """The remissvar basefiles one `ai-analyze` argument names. An *ärende*
    ("<typ>/<document id>" -- "sou/2026-21") expands to every answer actually
    fetched for it, in the order its record lists them; an argument that already
    names one answer ("<typ>/<document id>/<org-slug>") is returned unchanged, so
    the action takes either. Expansion reads the stored `Remiss` record rather
    than globbing the artifact tree: the record is what says an instance was
    downloaded at all, the same authority `remisser_list` parses from."""
    if not is_arende(basefile):
        assert compress.exists(layout.artifact("remisser", basefile)), (
            "%s is neither a stored ärende nor a parsed answer -- check the "
            "basefile (an ärende is '<typ>/<document id>', an answer adds "
            "'/<org-slug>')" % basefile)
        return [basefile]
    remiss = Remiss.from_dict(json.loads(
        compress.read_text(layout.remisser_arende(basefile))))
    return ["%s/%s" % (remiss.basefile, org_slug(inst.source_url))
            for inst in remiss.svar if inst.downloaded]


def analyze(basefile, force=False):
    """Author and write the `.ann` sentiment layer for one remissvar basefile
    ("<typ>/<document id>/<org-slug>"); returns the written path. Refuses (before the
    LLM spend) to regenerate a verified layer unless `force`."""
    out = annstore.path("remisser", basefile)
    annstore.guard(out, force)
    art_path = layout.artifact("remisser", basefile)
    svar = Remissvar.from_dict(json.loads(compress.read_bytes(art_path)))
    assert svar.remitterat, (
        "%s references no förarbete document (remitterat is empty) -- nothing to "
        "map onto; the caller should have scoped it out" % basefile)
    # v1 handles only the first cross-ref: a remiss almost always sends out exactly
    # one SOU/Ds, and the rare multi-document referral is deferred rather than
    # guessed at (each would need its own outline + a merged sidecar shape).
    ref = svar.remitterat[0]
    typ, fa_basefile = ref["typ"], ref["basefile"]
    # remitterat carries the colon identifier ("2019:61"); the förarbete artifact
    # tree is keyed by the filesystem slug ("2019-61"), so slug it for the join.
    # A promemoria is the awkward one: regeringen.se spells its diarienummer with
    # either case ("JU2026/01595" here, "Ju2026/01595" there), and forarbete keys
    # it on the *landing slug* whenever its own listing stated no dnr at all. The
    # remiss page can't tell which, so it carries both and the tree settles it.
    fa_slug = layout.resolve_basefile(
        "forarbete", "%s/%s" % (typ, basefile_slug(fa_basefile)),
        *(["%s/%s" % (typ, ref["slug"])] if ref.get("slug") else []))
    host_path = layout.artifact("forarbete", fa_slug)
    assert compress.exists(host_path), (
        "%s: no parsed förarbete artifact at %s -- run "
        "`lagen forarbete parse %s/%s` first"
        % (basefile, host_path, typ, fa_basefile))
    outline, valid_ids = section_outline(
        json.loads(compress.read_bytes(host_path))["structure"])
    assert valid_ids, ("%s host förarbete %s/%s has no sections to map onto"
                       % (basefile, typ, fa_basefile))

    text = "\n\n".join(svar.full_text)
    units = answer_units(svar.full_text)
    assert units, "%s: the parsed answer has no quotable text" % basefile
    prompt = (PROMPT.read_text().replace(OUTLINE_PLACEHOLDER, outline)
              .replace(TEXT_PLACEHOLDER, text))
    haystack = normalize_space(text)
    try:
        result = llm.author(
            prompt, lambda reply: _validate(reply, valid_ids, units, haystack),
            max_tokens=MAX_TOKENS)
    except ValueError as exc:
        raise Unanalyzable("%s: %s" % (basefile, exc)) from exc

    return annstore.write(out, result,
                          {**annstore.artifact_input("remisser", basefile),
                           **annstore.artifact_input("forarbete", fa_slug)}, force)
