# Regleringshierarki LLM-pass benchmark — 2026-08-27/28

Single vs batched calls, qwen3.8-27B (llama.cpp, `--reasoning-effort low`)
vs gemma-4-31B-it-qat, both local on the 3090 (port 8123), against the
hand-keyed golden for the PRD's ten worked example chains
(`test/files/regleringshierarki/golden-ten.json`). Harness: `bench.py`;
raw per-chain rows in `results/*.json`; every LLM reply checkpointed in
`results/*.calls.jsonl`. Scores below use the every-word-inflection scorer
(`*-final` rescores).

## All 11 chains, batched legs

| leg | A recall | A precision | rungs | roles right | calls | wall | s/call |
| --- | --- | --- | --- | --- | --- | --- | --- |
| qwen3.8 batch | 39/39 | 41/45 | 15/28 | 11 | 141 | 78 min | 33.0 |
| gemma4 batch | 38/39 | 44/49 | 16/28 | 12 | 175 | 100 min | 34.3 |

## The shared 5 chains, all four legs

| leg | A recall | A precision | rungs | calls | wall | s/call | compl-tok/call |
| --- | --- | --- | --- | --- | --- | --- | --- |
| qwen3.8 batch | 28/28 | 27/28 | 9/19 | 60 | 30 min | 30.4 | 2152 |
| qwen3.8 single | 26/28 | 25/26 | 10/19 | 410 | 59 min | 8.7 | 588 |
| gemma4 batch | 28/28 | 29/31 | 9/19 | 72 | 43 min | 36.1 | 3017 |
| gemma4 single | 28/28 | 28/30 | 9/19 | 449 | 112 min | 15.0 | 1221 |

## Findings

1. **Batching costs no accuracy.** Rung recall and A-scores are identical
   within one hit across modes, for both models. The per-item guards do the
   work: 1–6 discarded outputs per leg across hundreds of calls.
2. **Batching wins ~2× wall time** (qwen 30 vs 59 min, gemma 43 vs 112 on
   the shared five), not the 10× the call-count suggests: a single-item
   call is individually cheaper (~590 completion tokens vs ~2,200), so the
   amortization gain is real but bounded.
3. **qwen3.8 at reasoning-low is the local model choice**: 20–60% faster in
   every configuration, accuracy within one rung of gemma. Gemma is more
   verbose per item (3,017 vs 2,152 completion tokens per batched call) and
   decodes slower on this card. The "no-reasoning models are faster" prior
   from ai-genomforande did not transfer: at reasoning-low, qwen's thinking
   overhead is smaller than gemma's verbosity.
4. **The rung ceiling is structural, not model quality.** Of the 28
   scoreable rungs, ~5 sit on chains where no provision defines the subject
   at any rung (nedsättning, flygcertifikat) — PRD §5 rule 4 makes those
   unreachable for every closed pass; 4 more are marked "hand" by the
   source artifact itself. Models hit 15–16 of the ~23 practically
   reachable (≈70%). The bedömningsstöd family (5 rungs) was recovered by
   the every-word inflection fix to `concepts.term_pattern`, not by either
   model.
5. **Corpus projection** (measured, not assumed): ~30k items ≈ 2,500
   batched calls × ~33 s ≈ **23 h on the 3090**, one-time and resumable
   per call (the checkpoint cache). Remote parallel execution (Berget)
   compresses that to hours at low single-digit euros.

## Addendum 2026-08-28/29 — task D and the corrected runs

Rule 4 was relaxed (task D: a chain's subject as a verbatim span from its
own outlines) and two defects found by publishing were fixed (the
specializing-span mint swallow; every-word inflection in the matcher).
Re-scored on the widened 39-rung golden, gemma4 batched:

| leg | subjects | rungs | roles right | calls | wall |
| --- | --- | --- | --- | --- | --- |
| without D | 7/11 | 16/35 | 12 | 175 | 100 min |
| with D | 8/11 | 20/35 | 15 | 191 | 104 min |
| with D, mint fix | 8/11 | 22/35 | 17 | 81 paid | 47 min |

The no-golden control (säkerhetsskydd -> PMFS 2022:1, 25 documents) found
*säkerhetsskyddsavtal* -- the PRD's opening example -- among 19 concepts on
PMFS 2022:1, at 146 calls.

**Corpus projection, corrected:** the 524-component `--all` set holds
12,869 document-slots; at the measured 4-6 calls/slot and ~33 s/call the
local run is 450-650 GPU-hours, not the 23 h §5 above estimated from item
counts. Compression levers: Berget in parallel (~a day, tens of euros), a
shared cross-component call cache (the ~30-40% document overlap dedupes),
and tighter task-C scoping.
