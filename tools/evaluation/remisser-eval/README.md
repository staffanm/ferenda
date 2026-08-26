# remisser ai-analyze evaluation harness

Scores `lagen remisser ai-analyze` output against a hand-built answer key, so a
change to the prompt or the validator can be judged on measurements rather than
on a few eyeballed layers. Same shape as `tools/aigenomforande-bench`: the
scripts live here, the ground truth lives in the curated store
(`WIKI_ROOT/ann/remisser/**.ann.key`) where git versions it beside the generated
layers.

## Why a key rather than spot checks

Reading a handful of layers tells you they look plausible. It does not tell you
that 6% of answers lose the organisation's only objection, that 60% of sentiment
scores sit at exactly ±1.0, or that a prompt change traded 9 points of quote
accuracy for 6 points of section precision. All three came out of this harness.

## The key

One `.ann.key` per answer, authored by reading *only* the answer and the
inquiry's section outline -- never any generated layer, or the key inherits the
machine's mistakes. Per section it records the defensible sentiment *interval*
(a range, because a careful reader would accept more than one score), the
on-point sentence(s), and whether the answer states a reason at all. Per answer
it records whether anything is criticised, which is the measure that matters
most: a rail that silently drops an organisation's objection is worse than one
that scores it imprecisely.

    {"basefile": "sou/2026-20/lunds-kommun",
     "overall": {"sentiment_min": -0.9, "sentiment_max": -0.5,
                 "reason_stated": true, "on_point": ["..."]},
     "sections": [{"forarbete_id": "a8.1.1", "sentiment_min": -1.0,
                   "sentiment_max": -0.6, "reason_stated": true,
                   "on_point": ["..."], "note": "..."}],
     "has_criticism": {"present": true, "paragraphs": ["p12"], "summary": "..."},
     "engagement": "substantive"}

## Running it

    # 1. pick the answers and write one briefing file per answer
    python tools/remisser-eval/make_briefs.py sou/2026-20 50 /tmp/briefs

    # 2. author a key per brief (one subagent per answer, reading only the brief)
    #    then import the JSON into the curated store
    python tools/remisser-eval/import_keys.py /tmp/keys

    # 3. score any layer tree against the key
    python tools/remisser-eval/evaluate.py sou/2026-20 <layer-root> "label"

`<layer-root>` is `$WIKI_ROOT/ann` for the live layers, or a scratch annstore
root written by an experimental run -- which is how two prompts are compared on
identical answers.

## Choosing the answers

`make_briefs.py` takes the longest answers of one ärende. Length is a structural
proxy for engagement and it works: a random sample of the corpus returned
boilerplate "vi har inga synpunkter" replies with zero sections, which measure
nothing. Picking one ärende also means every answer shares a section outline, so
section recall is comparable across them.

## Known limits

- The key is one reader's judgement. Sentiment intervals are deliberately wide;
  differences under ~4 points on 50 answers are not conclusive.
- A key records the artifact hashes it was authored against. **Reparsing the
  corpus can invalidate the `on_point` strings** -- they are verbatim from the
  text as it was extracted then, and cleaning changes that text. Re-check with
  `evaluate.py`, which reports a key whose on-point sentence no longer occurs.
