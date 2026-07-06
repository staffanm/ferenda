"""`lagen remisser ai-analyze <basefile>` -- the sole LLM pass for the remiss
corpus: map one organisation's remissvar onto the *specific sections* of the
SOU/Ds it discusses, with a per-section sentiment score and a verbatim quote.

The remiss corpus is never published as its own pages; the point of this pass is
to feed the referred förarbete's page a context rail ("here's what
Kammarkollegiet said about chapter 4"). So for one answer we load its parsed
`Remissvar` artifact, load the referred förarbete artifact for its section
outline (the nested `avsnitt` tree, each node's `id` the join anchor), and ask
the configured Berget model to place the answer's commentary on those ids -- an
overall stance plus a segment per section actually discussed. The reply is
validated strictly (every cited id must be a real section, every quote a verbatim
substring of the answer text) and written as a `.ann` sidecar next to the
artifact, so a later rendering pass surfaces it on the förarbete page.

Like every ai-* action the LLM is called only here, on an explicit analyze of a
named basefile -- never from a corpus-wide parse/relate/generate.
"""

import json
from pathlib import Path

from ..lib import compress, layout, llm
from ..lib.text import runs_text
from ..lib.util import basefile_slug, normalize_space
from .model import Remissvar

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


def _check_scored(obj, haystack, where, valid_ids=None):
    """Shape-check one scored object (overall, or a segment when `valid_ids` is
    given): a numeric `sentiment` in [-1, 1], a non-empty `quote` that is a
    verbatim substring of the answer, and -- for a segment -- a `forarbete_id`
    that is a real section. Raises `ValueError` naming the fault (fed back on the
    retry) so a hallucinated id or an invented quote never reaches the `.ann`."""
    if not isinstance(obj, dict):
        raise ValueError("%s is not an object" % where)
    sentiment = obj.get("sentiment")
    # bool is an int subclass; a JSON `true` must not pass as a score
    if isinstance(sentiment, bool) or not isinstance(sentiment, (int, float)):
        raise ValueError("%s has a non-numeric sentiment" % where)
    if not -1 <= sentiment <= 1:
        raise ValueError("%s sentiment %r is outside [-1, 1]" % (where, sentiment))
    quote = obj.get("quote")
    if not (isinstance(quote, str) and quote.strip()):
        raise ValueError("%s has an empty quote" % where)
    # the model paraphrases unless forced not to; the quote powers a rail excerpt,
    # so require it be actual answer text -- compared whitespace-normalised, not
    # byte-exact, since the PDF-extracted text wraps and re-spaces freely
    if normalize_space(quote) not in haystack:
        raise ValueError("%s quote is not a verbatim substring of the answer: %r"
                         % (where, quote[:80]))
    if valid_ids is not None and obj.get("forarbete_id") not in valid_ids:
        raise ValueError("segment cites forarbete_id %r not in the outline"
                         % obj.get("forarbete_id"))


def _validate(content, valid_ids, haystack):
    """Parse and shape-check the model's reply into the `.ann` payload:
    `{"overall": {...}, "segments": [...]}`. `segments` may be empty (an answer can
    be purely general). Raises `ValueError` -- not assert, per
    rule:errors-drive-retry-use-raise, the retry loop load-bears on the raise which
    `-O` would strip -- on anything malformed."""
    data = json.loads(llm.strip_fence(content))
    if not isinstance(data, dict):
        raise ValueError("response is not a JSON object")
    overall = data.get("overall")
    _check_scored(overall, haystack, "overall")
    segments = data.get("segments")
    if not isinstance(segments, list):
        raise ValueError("response lacks a segments list")
    for seg in segments:
        _check_scored(seg, haystack, "a segment", valid_ids=valid_ids)
    return {
        "overall": {"sentiment": float(overall["sentiment"]),
                    "quote": overall["quote"]},
        "segments": [{"forarbete_id": s["forarbete_id"],
                      "sentiment": float(s["sentiment"]),
                      "quote": s["quote"]} for s in segments],
    }


def _author(prompt, valid_ids, haystack):
    """Call the model and validate; on a malformed reply, retry once as a real
    follow-up turn -- the model's own prior reply replayed as an `assistant`
    message, then a short `user` message naming exactly what failed and asking
    for a fix. This lets it correct the one broken part directly, rather than
    re-deriving the whole answer from an ever-longer single user message with the
    correction tacked onto the end (which forces it to redo the entire task from
    scratch, over the same outline and answer text, and often reproduces the same
    mistake since nothing points it at what specifically to change). Raises if
    the second answer is bad too."""
    messages = [{"role": "user", "content": prompt}]
    for attempt in range(2):
        reply = llm.complete_thread(messages, max_tokens=MAX_TOKENS)
        try:
            return _validate(reply, valid_ids, haystack)
        except ValueError as exc:
            if attempt:
                raise
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user", "content":
                             "DITT SVAR UNDERKÄNDES: %s\n"
                             "Rätta ENDAST detta i ditt svar och följ alla regler "
                             "i den ursprungliga instruktionen exakt. Svara med "
                             "hela den korrigerade JSON-strukturen igen." % exc})


def analyze(basefile):
    """Author and write the `.ann` sentiment layer for one remissvar basefile
    ("<case-slug>/<org-slug>"); returns the written path."""
    art_path = layout.artifact("remisser", basefile)
    svar = Remissvar.from_dict(json.loads(compress.read_bytes(art_path)))
    assert svar.remitterat, (
        "%s references no förarbete document (remitterat is empty) -- nothing to "
        "map onto; the caller should have scoped it out" % basefile)
    # v1 handles only the first cross-ref: a remiss almost always sends out exactly
    # one SOU/Ds, and the rare multi-document referral is deferred rather than
    # guessed at (each would need its own outline + a merged sidecar shape).
    typ, fa_basefile = svar.remitterat[0]["typ"], svar.remitterat[0]["basefile"]
    # remitterat carries the colon identifier ("2019:61"); the förarbete artifact
    # tree is keyed by the filesystem slug ("2019-61"), so slug it for the join
    host_path = layout.artifact("forarbete", "%s/%s" % (typ, basefile_slug(fa_basefile)))
    assert compress.exists(host_path), (
        "%s: no parsed förarbete artifact at %s -- run "
        "`lagen forarbete parse %s/%s` first"
        % (basefile, host_path, typ, fa_basefile))
    outline, valid_ids = section_outline(
        json.loads(compress.read_bytes(host_path))["structure"])
    assert valid_ids, ("%s host förarbete %s/%s has no sections to map onto"
                       % (basefile, typ, fa_basefile))

    text = "\n\n".join(svar.full_text)
    prompt = (PROMPT.read_text().replace(OUTLINE_PLACEHOLDER, outline)
              .replace(TEXT_PLACEHOLDER, text))
    result = _author(prompt, valid_ids, normalize_space(text))

    path = art_path.with_suffix(".ann")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return path
