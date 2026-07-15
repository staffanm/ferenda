# Local LLM runbook — Qwen3.6-35B-A3B on llama.cpp

How to run a local, vision-capable reasoning model on the workstation and point
the opt-in `ai-*` passes at it instead of Berget. This is the operator's guide:
build, model files, launch, verify, and the measured limits. For what the `ai-*`
passes *are* read [`operating/README.md`](operating/README.md#5-per-source-pipelines);
for the client itself read `accommodanda/lib/llm.py`.

**Why bother.** The `ai-*` passes are metered API calls against Berget
(`llm_model`, default `openai/gpt-oss-120b`). A local model is unmetered and
private, which is what makes *bulk* and *experimental* passes affordable — the
EUR-Lex corpus alone is ~21,600 acts and ~192M prompt tokens, which is a
different proposition on someone else's invoice. The tradeoff is a smaller model
and a single GPU's throughput, so Berget remains the right call for one-off
quality work.

## 1. Prerequisites

| Requirement | Why |
|---|---|
| **NVIDIA GPU, 24 GB VRAM** (RTX 3090 here) | the model + a 262k context land at ~21.5 GB |
| **CUDA toolkit 12.x** + driver | `nvcc`; the build targets compute capability 8.6 for a 3090 |
| **llama.cpp checkout** | `~/llama.cpp`; needs a recent master — see below |
| **~24 GB disk** | the GGUF weights |

The model is **Qwen3.6-35B-A3B**: a 35B-total / 3B-active MoE, natively
262,144-token context, with a vision encoder and reasoning ("thinking") on by
default. Its architecture is *hybrid* — only 10 of 40 layers use full attention,
the other 30 are linear (Gated DeltaNet). That detail is the whole reason a 262k
context is affordable on one consumer card: the KV cache is ~5.2 GB rather than
the ~20 GB a conventional 40-layer model would need.

llama.cpp support is recent (arch `qwen35moe`, projector `qwen3vl_merger`).
Build from master, not a release tarball, and expect the rough edges in §6.

## 2. Build llama.cpp

```sh
cd ~/llama.cpp
cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=86 -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j $(nproc) --target llama-server
```

`CMAKE_CUDA_ARCHITECTURES=86` is the 3090; adjust for another card (89 = Ada,
90 = Hopper). Verify the binary is the one you just built — a stale binary from
an earlier checkout is the classic way to spend an hour debugging a fixed bug:

```sh
./build/bin/llama-server --version    # must match `git rev-parse --short HEAD`
```

**If the build fails in the web-UI assets step** (`llama-ui-embed failed`,
`missing required asset(s)`), the `tools/ui` build dir is stale from an older
checkout. It is not a code error:

```sh
rm -rf build/tools/ui && cmake --build build --config Release -j $(nproc) --target llama-server
```

Note that piping the build through `tail` masks its exit code (you get `tail`'s).
Check `${PIPESTATUS[0]}` or don't pipe.

## 3. Model files

Two files from the `unsloth/Qwen3.6-35B-A3B-MTP-GGUF` repo on Hugging Face —
the quantized weights and the vision projector:

```sh
huggingface-cli download unsloth/Qwen3.6-35B-A3B-MTP-GGUF \
  Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf mmproj-BF16.gguf \
  --local-dir ~/llama.cpp/unsloth/Qwen3.6-35B-A3B-MTP-GGUF
```

| File | Size | What |
|---|---|---|
| `Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf` | 22.8 GB | weights, Unsloth dynamic Q4_K_XL |
| `mmproj-BF16.gguf` | 902 MB | vision projector — **required for images**; without it the server is text-only |

`UD-Q4_K_XL` is the quality/size sweet spot for 24 GB. A larger quant does not
fit alongside a long context; a smaller one costs accuracy on legal text.

## 4. Start the server

```sh
~/llama.cpp/run-qwen36.sh      # serves http://127.0.0.1:8123
```

That script is just the invocation below. Each flag earns its place:

```sh
llama-server \
  -m .../Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf \
  --mmproj .../mmproj-BF16.gguf \
  -c 262144 \
  -fa on \
  -ctk f16 -ctv f16 \
  --no-mmap \
  --image-min-tokens 1024 \
  --parallel 1 \
  --host 127.0.0.1 --port 8123
```

| Flag | Why |
|---|---|
| *(no `-ngl` / `-ncmoe`)* | **deliberate.** llama.cpp auto-fits the CPU/GPU expert split to free VRAM. Setting either flag silently disables auto-fit entirely (`failed to fit params: n_gpu_layers already set by user, abort`) and you hand-tune worse than it does. |
| `-c 262144` | the native context. Auto-fit respects a user-set `-c` and offloads MoE experts around it. |
| `-ctk f16 -ctv f16` | full-fidelity KV. Thanks to the hybrid layers it costs only 5.2 GB, so there is no reason to quantize it — `q8_0` saves 2.4 GB and buys ~9% speed, which you do not need. |
| `--image-min-tokens 1024` | llama.cpp warns that Qwen-VL needs ≥1024 image tokens for grounding accuracy. A floor, not a cost — a full page already exceeds it. |
| `--no-mmap` | recommended once experts are overridden to CPU. |
| `--parallel 1` | **not tunable — see §6.** Higher values crash the server. |

Resulting VRAM at 262k context: **model 15.8 GB + KV 5.2 GB + compute 0.5 GB ≈
21.5 GB** of 24 GB.

## 5. Verify

```sh
curl -s http://127.0.0.1:8123/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Svara med JSON: {\"ok\":true}"}],
       "max_tokens":2000,
       "chat_template_kwargs":{"enable_thinking":false}}' | jq -r '.choices[0].message.content'
```

The endpoint is OpenAI-compatible (`/v1/chat/completions`), which is the same
shape `lib/llm.py` already speaks, including the inline base64 `image_url` data
URIs `vision_content` builds.

## 6. Measured behaviour and limits

Benchmarked on an RTX 3090 against this corpus (llama.cpp b10027):

| | |
|---|---|
| Context | 262,144 tokens (native; no YaRN needed) |
| Rasterized A4 page @150 DPI | ~2,176 tokens → **~120 pages** fit in context |
| Full GDPR (173 recitals + 99 articles) | ~97k tokens — ~40% of context |
| Prompt processing | ~870–1,460 tok/s |
| Generation | ~87 tok/s (empty context) → ~67 tok/s (at 100k) |
| Batch throughput (`--parallel 1`) | ~911 tok/s total; 98/98 acts, zero errors |

**Two upstream bugs — both hard blockers, both worth retesting later.** Each
produces `CUDA error: an unsupported value or parameter was passed to the
function` (`ggml-cuda.cu:104`) and kills the server. They look like defects in
the new hybrid-DeltaNet paths:

- **`--parallel > 1` crashes.** Reproduced at 2, 4 and 8, with and without a
  grammar, with and without `-ctxcp`. This is why `--parallel 1` is fixed above,
  and it caps throughput at one request at a time.
- **`--spec-type draft-mtp` crashes** after ~700–1,000 decoded tokens, even at
  `--parallel 1`. Retest it after upstream fixes: MTP self-speculation runs at
  **127 t/s vs 87** while it lives (the weights already ship the MTP head), so it
  is ~1.5x free once it works.

### Sampling and the thinking mode

Thinking is **on by default** (the chat template emits `<think>`). `/no_think`
in the prompt does nothing — the real switches are
`chat_template_kwargs: {"enable_thinking": false}` per request, or
`--reasoning-budget N` to cap it.

Three things that bite, in descending order of how much time they cost:

1. **Budget `max_tokens` for the reasoning, not the answer.** With thinking on
   and too small a budget, reasoning consumes the whole allowance and the reply
   comes back with **empty content** and `finish_reason: "length"` — full compute
   cost, nothing to parse. A 5 KB JSON answer can sit behind 20k+ reasoning
   tokens (the GDPR article↔recital mapping used 25,346). `complete_thread`
   already raises on a `length` finish (`rule:errors-drive-retry-use-raise`), so
   this surfaces as a `ValueError` rather than silent truncation — believe it and
   raise `max_tokens`.
2. **Constrain JSON with a schema, don't ask nicely.** Unconstrained, the model
   emitted `""One-stop-shop"-mekanism"` — unescaped quotes inside a JSON string,
   invalid and unparseable. The identical prompt with
   `response_format: {"type": "json_schema", …}` parsed strictly on the first try.
   `strip_fence` handles code fences; it cannot handle this. A schema makes the
   failure class structurally impossible.
3. **Vision localization finds the page, not the pixel.** The first real
   `sfs ai-includegraphics` run (2004:629, six maps across three published
   PDFs, ~3 min/PDF): every gap was placed on a plausible page with a good
   alt text, but no bbox survived review unedited — one map was split in two,
   one "located" on the amendment's text-only title page, most boundaries
   clipped the figure. That failure mode is exactly what the per-entry
   `verified` workflow absorbs: treat the model's output as candidate
   geometry, hand-fix the bboxes in the `.graphics` layer (raw PDF points,
   `px * 72 / 150`), verify entry by entry. Berget's Kimi remains the pick
   for a large batch where hand-curating every crop is unaffordable.
4. **`temperature=0` is wrong for this model.** It is the default (right for
   gpt-oss on Berget) but not here: Qwen3.6 asks for temp 1.0 / top_p 0.95 /
   top_k 20 in thinking mode (0.7 / 0.8 / 20 for instruct), and greedy decoding
   in thinking mode tends to loop. Set `llm_temperature`/`llm_top_p` alongside
   `llm_base_url` (§7). `top_k` is not plumbed — no caller has needed it.

## 7. Pointing accommodanda at it

Three config keys aim the `ai-*` passes at the local server. Each has the usual
env → `config.yml` → default precedence:

```yaml
# config.yml
llm_base_url: http://127.0.0.1:8123/v1   # default https://api.berget.ai/v1
llm_temperature: 1.0                     # default 0
llm_top_p: 0.95                          # default unset (endpoint's own default)
```

| Key | Env | Default |
|---|---|---|
| `llm_base_url` | `LLM_BASE_URL` | `https://api.berget.ai/v1` |
| `llm_temperature` | `LLM_TEMPERATURE` | `0` |
| `llm_top_p` | `LLM_TOP_P` | unset |

`llm_base_url` omits the `/chat/completions` path — `lib/llm` appends it. **A
local endpoint needs no API key**: `lib/llm.auth_headers` demands
`BERGET_API_KEY` only when the host is not `localhost`/`127.0.0.1`/`::1`, where a
missing key is a real misconfiguration and should fail before the pass starts
rather than 401 halfway through a corpus.

`llm_model`/`BERGET_MODEL` is irrelevant here — llama.cpp serves whatever GGUF it
loaded and ignores the name — but set it to something honest anyway so `/ops` and
the run ledger record which model authored a sidecar. The same goes for
`vision_model`/`BERGET_VISION_MODEL`, which the vision passes
(`sfs ai-includegraphics`) record instead of `llm_model`; Qwen3.6's vision
encoder serves those too (see §6 for what to expect from it).

Set the sampling keys **together with** the base URL. The default temperature 0
is right for gpt-oss on Berget but wrong here: Qwen3.6 asks for 1.0 / top_p 0.95
in thinking mode and loops under greedy decoding (§6). An out-of-range value
raises `ConfigError` rather than being clamped — a silently corrected sampling
knob would change every reply without saying so.

A one-shot run against the local box, leaving `config.yml` alone:

```sh
LLM_BASE_URL=http://127.0.0.1:8123/v1 LLM_TEMPERATURE=1.0 LLM_TOP_P=0.95 \
  lagen eurlex ai-annotate 32016R0679
```

Defaults are unchanged: with none of these keys set the client posts to Berget at
temperature 0 with no `top_p` key, exactly as before.
