# `forarbete ai-genomforande` — handover notes

Working notes for the LLM directive→paragraf transposition pass and the
riksmöte-2025/26 mapping task. Written 2026-07-23. Not a spec; the durable
description lives in the module docstring (`accommodanda/forarbete/aigenomforande.py`),
`REWRITE.md` §7d and `accommodanda/README.md`.

## What the pass does

`lagen forarbete ai-genomforande <prop-basefile> [<CELEX> ...]` reads a
proposition's författningskommentar and, per paragraf, asks an LLM which
article(s) of which EU directive that paragraf transposes. Output is a `.ann`
layer in the curated store (`lib/annstore.py`, `WIKI_ROOT/ann/forarbete/...`),
a **richer superset of the mechanical `implements`** that `kommentar.extract`
already stamps on the artifact. `genomforande.resolve` prefers the authored
layer over the mechanical edges at relate time (per covered directive), so the
mapping renders in the statute paragraf margin ("Genomför EU-rätt") and as the
directive article's inbound.

Grounding (why the model can't hallucinate):
- **Paragraf identity is never asked of the model.** Each FK entry is already
  segmented by `fk.extract` into `{law, chapter, paragrafer, kommentar}`; the
  batch prompt gives each a stable id `E1`, `E2`, … and the model returns the
  *id*. It can only ever pick a real paragraf.
- **Directives are tagged** `A`, `B`, … each with its real article inventory
  (read from the eurlex artifact). A prop transposing several directives at
  once (financial omnibus, NIS2+CER) is one pass; the model names the tag.
- Every mapping is validated: known id, known tag, every cited article resolves
  to that directive's inventory, and the supporting quote occurs in the entry's
  commentary. Failing items are dropped, not stored.

## THE KEY LESSON: the pinpoint validator bug

The pass first looked catastrophic in batch mode (13/41 paragrafer, huge
"rejected" counts) and I wrongly blamed endpoint nondeterminism. **The real
cause was the validator.** The model returns articles as **dotted pinpoints** —
`"21.1–21.3"`, `"2.2 f"`, `"23.4 a"` — but the validator checked membership
against the *bare-number* inventory (`"21"`, `"2"`), so every pinpoint was
rejected as "unknown article" **even though the quote was correct**. Local Qwen
masked the bug by happening to emit bare numbers; gpt-oss on Berget emits
pinpoints and every mapping was rejected.

**Fix:** `_articles()` runs the model's `articles` field through
`kommentar.parse_articles` (the same golden parser the mechanical route uses),
which reduces `"21.1–21.3"` → base article `21` + pinpoints `21.1, 21.2, 21.3`.
The base is validated against the inventory; the pinpoints are kept for the
margin — strictly *better* data than the previous quote-parsing. Accepts bare
numbers and pinpoints alike.

Second, smaller issue: **`max_tokens`.** A reasoning model spends most of its
budget reasoning before the JSON; a low ceiling truncates the reply
(`finish_reason: "length"`) and the whole batch is lost. `MAX_TOKENS = 32000`
with `BATCH_CHARS = 150000` leaves headroom — it is a ceiling, not a target
(unused budget costs nothing), sized for the longest-reasoning model measured
(Kimi-K2.6 truncated full batches at 16k where gpt-oss used ~5k) while still
cutting off a runaway reasoning loop. `lib/llm.py` also retries a transient
5xx from a hosted endpoint (3 attempts, backoff) before propagating.

## Endpoint findings (measured on prop 28, the cybersäkerhetslag)

| Setup | Result | Time |
|---|---|---|
| Per-paragraf, local Qwen | 41 paragrafer, 66 edges, 16 articles | ~24 min |
| Per-law batch, either endpoint, **pre-fix** | 3–13 paragrafer (lossy) | ~20 min |
| Per-law batch, Berget `gpt-oss-120b` (fixed) | 40 paragrafer, 64 edges, 0 rejected | **47 s** |
| Per-law batch, local Qwen `qwen3.6-35b-a3b` (fixed) | 40 paragrafer, 65 edges, 0 rejected | ~3:46 |

- **Both endpoints give equivalent quality with the fix** (~97–98% of
  per-paragraf's 66 article-edges): Berget 64, local 65 (local caught the one
  Berget missed, kap 2 §10→art 6). Both reproduce ~56/57 mechanical edges and
  carry dotted pinpoints (14–15 of 40 edges).
- **Local Qwen is STABLE, not nondeterministic.** Two full repeat runs on local
  produced **byte-identical** layers (65 edges each, zero diff). The earlier
  "1/19 then 19/19" reading was a *misdiagnosis of the pinpoint bug*: the model
  intermittently emitted a bare "21" vs a pinpoint "21.1", and only the bare
  form passed the old validator, so the *kept* count swung wildly while the
  underlying mappings were stable. With the fix both formats pass and the count
  is stable.
- **The only real endpoint difference is speed.** Local is serial
  (`--parallel 1`) and its big-prompt calls are slow (~2 min/batch); Berget is
  ~5× faster and can run props concurrently. **Prefer Berget for a corpus run;
  local is perfectly usable, just slower.**

## How to run (Berget)

`BERGET_API_KEY` is in `.env`. `config.yml` still points `llm_base_url`/
`llm_model` at the local server, so override per-run via env (do NOT edit
config.yml):

```sh
LLM_BASE_URL=https://api.berget.ai/v1 BERGET_MODEL=openai/gpt-oss-120b \
  lagen forarbete ai-genomforande prop/2025-26-28
```

- No `<CELEX>` → directives auto-detected from the prop's `implements`.
- A directive whose eurlex artifact is absent is skipped with a warning
  (only `32002L0065` is missing — see below).
- `--force` overwrites an existing `generated` layer; a hand-`verified` layer
  refuses without it.
- Berget has no `--parallel 1` limit, so props can be run concurrently to go
  faster than the ~1 call/40s serial local rate.

Model: `openai/gpt-oss-120b` is the framework default and validated here.
Alternatives on Berget worth trying: `moonshotai/Kimi-K2.6`, `zai-org/GLM-5.2`.

## Eligible propositioner — riksmöte 2025/26 (20 props, 655 candidate entries)

Every 2025/26 prop whose parsed artifact carries a mechanical `implements` edge
(i.e. its författningskommentar names a directive it transposes). "Cand" = FK
entries mentioning artikel/direktiv (the per-paragraf work; the batch groups
these into ~1 call per proposed law).

| Prop | Cand | Directives (base CELEX) | Missing | Title |
|---|---|---|---|---|
| 2025/26:3 | 15 | 32011L0064 | — | Förenklad hantering av skattefritt bränsle … |
| 2025/26:16 | 18 | 32018L2001 | — | Förbättrad utformning av EU:s elmarknad |
| 2025/26:28 | 43 | 32022L2555 (NIS2) | — | En ny cybersäkerhetslag |
| 2025/26:43 | 1 | 32006L0112 | — | Sanktioner (mervärdesskatt) |
| 2025/26:84 | 35 | 32008L0048 | **32002L0065** | Stärkt konsumentskydd vid distansavtal |
| 2025/26:108 | 9 | 32008L0098 | — | Reformering av avfallslagstiftningen |
| 2025/26:118 | 18 | 32011L0092 | — | Tillståndsprövning enligt förnybartdirektivet |
| 2025/26:124 | 55 | 32002L0087 32009L0138 32013L0034 32013L0036 32014L0059 32016L2341 | — | Europeisk gemensam åtkomstpunkt (ESAP) |
| 2025/26:129 | 15 | 32024L1640 | — | Registret över verkliga huvudmän (AML) |
| 2025/26:146 | 5 | 32016L0801 | — | Migrationsregler för forskare/studenter |
| 2025/26:159 | 14 | 32024L1275 | — | Effektiv energianvändning (EED) |
| 2025/26:183 | 7 | 32015L1535 | — | Sänkt alkoholskatt, oberoende producenter |
| 2025/26:186 | 72 | 32009L0065 32011L0061 32014L0065 | — | En starkare fondmarknad |
| 2025/26:202 | 1 | 32000L0060 | — | Undantag art- och habitatdirektivet |
| 2025/26:240 | 107 | 32011L0092 32019L0944 | — | Nya lagar om elsystemet |
| 2025/26:253 | 118 | 32013L0036 | — | EU:s bankpaket (CRD) |
| 2025/26:262 | 57 | 32001L0055 | — | Utmönstring av permanent uppehållstillstånd |
| 2025/26:265 | 8 | 32008L0115 32024L1346 | — | Skärpta regler om uppsikt och förvar |
| 2025/26:278 | 18 | 32015L1535 | — | Mervärdesskatt vid gränshandel |
| 2025/26:303 | 39 | 32022L2555 32022L2557 (NIS2+CER) | — | Kritiska verksamheters motståndskraft (CER) |

- **6 props are multi-directive** (124, 186, 240, 265, 303, and 84 nominally).
  The batch pass handles them in one run via the tag catalog.
- **One directive is missing from the eurlex corpus:** `32002L0065` (the
  repealed Distance Marketing of Financial Services directive), used only by
  **prop 84**, which also implements `32008L0048` (present). Prop 84 runs
  against `32008L0048` only; to cover it fully, `lagen eurlex parse 32002L0065`
  first (may need downloading).

Detection is via the mechanical `implements`, so a prop that transposes a
directive **without** any formulaic "genomför artikel" sentence would be
missed by this list. None seen in 2025/26, but worth a broader scan (FK
mentioning a directive alias + "genomför") if aiming for completeness.

## Current state on disk (layers written)

- **prop 28** — Berget layer written (40 paragrafer, 16 articles). CURRENT.
- **prop 303** — an OLD **per-paragraf** multi-directive layer (37 edges) from
  an earlier local run. Should be **re-run on Berget** for consistency with 28.
- **all other 18 props** — not yet run.
- **relate not yet run** — the layers are authored but not folded into the
  SQLite catalog / site. Run `lagen relate` (or the `__corr__` post-pass) to
  pin them; `genomforande.resolve` reads the `.ann` layers via
  `genomforande_layers()` and supersedes the mechanical edges. Verified
  read-only that prop 28's 40-paragraf layer resolves to SFS 2025:1506.

Backups of the per-paragraf reference layers for 28 and 303 are in the session
scratchpad (`perparagraf_28.ann`, `perparagraf_303.ann`).

## To run the whole 2025/26 batch

```sh
for p in 3 16 28 43 84 108 118 124 129 146 159 183 186 202 240 253 262 265 278 303; do
  LLM_BASE_URL=https://api.berget.ai/v1 BERGET_MODEL=openai/gpt-oss-120b \
    lagen forarbete ai-genomforande prop/2025-26-$p --force
done
```

Rough cost: ~655 candidate entries → ~40–50 batches total (≈1 per proposed law,
big FKs split at 40k chars). At ~40 s/batch serial that's ~30 min; Berget allows
concurrency, so parallelising a few props cuts it further. Evaluate each with:
superset check vs mechanical `implements`, recall vs a per-paragraf spot-run,
quote-grounding (already enforced), and pinpoint richness. A scratch evaluator
is at `scratchpad/evaluate.py`.

## Full-corpus benchmark (2026-07-23, all 20 props, 4 models)

Ground truth: one Claude subagent (opus for the 9 large/multi-directive props,
sonnet for the rest) read each prop's candidate FK entries and adjudicated
every explicit paragraf→artikel linkage; converted through the SAME validator/
edge fan-out as the live pass into `.ann.golden` files beside the `.ann`
layers (`WIKI_ROOT/ann/forarbete/prop/2025/*.ann.golden`, ~660 article-edges
over ~440 paragraf-mappings). Edges compared on (law, chapter, paragraf,
directive, article); harness + full per-prop result table archived in
`tools/aigenomforande-bench/` (`dump_fk.py` → subagent adjudication →
`make_golden.py`; `bench_all.sh`/`bench_one.py`; `evaluate.py`;
`final_eval.txt`). `test_forarbete_aigenomforande.py` now scores any stored
`.ann` layer against its `.ann.golden` (precision/recall ≥ 0.90) so a
prompt/validator regression fails the suite.

| model | prec | recall | F1 | F0.5 | pinpoint agree | Σtime | cost |
|---|---|---|---|---|---|---|---|
| berget google/gemma-4-31B-it | 95.4% | **98.8%** | **97.1%** | **96.0%** | 646/677 | 10 min† | €0.19 |
| berget zai-org/GLM-5.2 | 97.9% | 76.2% | 85.7% | 92.7% | 501/522 | 21 min† | €2.11 |
| berget mistralai/Mistral-Medium-3.5-128B | 90.7% | 96.6% | 93.6% | 91.8% | 643/662 | 61 min† | €1.26 |
| local qwen3.6-35b-a3b (llama.cpp) | 88.8% | 93.6% | 91.1% | 89.7% | 634/641 | 64 min serial | free |
| berget openai/gpt-oss-120b | 93.2% | 73.9% | 82.4% | 88.6% | 431/506 | **19 min†** | **€0.21** |
| berget moonshotai/Kimi-K2.6 | **98.9%** | 53.1% | 69.1% | 84.4% | 342/364 | 80 min† | €1.98 |

† summed per-prop; Berget ran 4 props concurrently, wall ≈ ⅓. F0.5 weights
precision 2× over recall — the right lens for a legal margin note, where a
wrong genomför-claim is worse than a missing one.

- **Gemma-4-31B-it wins on every axis at once** — best F1 *and* F0.5, the
  only model to go 112/112 on prop 240 and 16/16 (0 fp) on the six-directive
  prop 124, 300/301 on 253, prop 16 correct — while being the second-cheapest
  and fastest. A 31B multilingual instruction-tuned model beating flagship
  reasoners suggests the task rewards Swedish reading + strict schema
  compliance, not chain-of-thought. Weaknesses: falls for the prop-3 trap
  (6 fp), 2 fp on trap 118, misses 262 (0/6), 4 fp on 108.
- **GLM-5.2 is the precision runner-up** (97.9%, only 11 fp corpus-wide,
  resists the prop-3 trap entirely) but conservative like Kimi (0/16 on 124,
  0/6 on 262, 216/301 on 253) and the most expensive.
- **Mistral-Medium was the accuracy pick of the first four** (300/301 on the
  bankpaket prop 253, 42/42 on 303); its fps concentrate on multi-directive
  attribution (124: 16 tp but 19 fp, 240: 26 fp). **Local Qwen is a close
  free option** (299/301 on 253!) but force-fits: 52 fps on prop 84 mapping
  konsumenträttighetsdirektiv citations onto the catalogued 2008/48 — the
  small model ignores the prompt's don't-force-fit rule the hosted ones obey.
- **gpt-oss trades recall for cost/speed** (loses 30/89 on 186, 86/301 on
  253); **Kimi is precise but far too conservative** (62/301 on 253, 0/16 on
  124) — and needed `MAX_TOKENS` 32k (truncated whole batches at 16k; that
  measurement drove the constant).
- Every model scores clean zeros on the wrong-directive trap props
  (84/118/183/278) with the tightened prompt (old prompt: gpt-oss force-fitted
  6 edges on 118). Residual trap fps only on prop 3 (5–9 for all but Kimi).
- All four are perfect on prop 28 (65/65) — the prop the earlier findings were
  calibrated on is the *easy* case; the big multi-law props separate models.
- **Whole-law batching beats sliced batching** (gemma, the 6 props whose laws
  split at the old 40k-char budget): precision 96.7%→98.9% with recall flat —
  prop 253's 9 fps and 5 of 240's 11 all came from isolated slices. Hence
  `BATCH_CHARS = 150000` (every real law one call; cap only so a pathological
  FK still fits the local server's 64k context).
- **Local Gemma QAT (UD-Q4_K_XL on the 3090, `run-gemma4.sh`) ≈ Berget's
  full-precision Gemma**: 95.6/96.1 vs 95.4/98.8 (prec/rec), F0.5 95.7 vs
  96.0; edge-identical on 15/20 props incl. the same trap fps. It loses
  multi-directive attribution on 124 (4/16 vs 16/16) and some of 240, wins
  262 (6/6 vs 0/6) and is cleaner on 253 (1 fp vs 9). ~37 min serial, free.
- **Wrong-catalog discovery:** for props 3, 43, 84, 118, 183, 278 the FK's
  explicit mappings target a directive `detect_directives` never catalogued
  (e.g. 278 maps mervärdesskattedirektivet while `implements` only names
  32015L1535; 118 maps förnybartdirektivet, catalog has only the MKB
  directive). Their goldens are legitimately empty w.r.t. the catalog. To
  cover them, pass the right CELEX explicitly (and parse it into eurlex
  first) — or broaden detection beyond the mechanical `implements`.

## Open items / caveats
- **Local endpoint:** the committed batch-per-law design works and is stable on
  local Qwen (proven byte-identical across runs); it is just ~5× slower than
  Berget because it is serial. No need for a separate per-paragraf path.
- **Not committed:** all changes are in the working tree
  (`accommodanda/forarbete/aigenomforande.py`, `genomforande_prompt.txt`,
  `genomforande.py`, `build.py`, `lib/llm.py` (USAGE tally, 5xx retry),
  `test/test_forarbete_aigenomforande.py`, `test/test_llm.py`, REWRITE/README,
  this file). Nothing has been committed. The `.ann.golden` ground-truth files
  live in the lagen-wiki repo, also uncommitted.
- **Data leaves the machine:** the pass sends prop + directive text to Berget.
  These are public legal documents, so no sensitivity issue — just noting it.
