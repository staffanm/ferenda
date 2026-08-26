# Documentation

Documentation for Ferenda and lagen.nu, grouped by audience.

| I am… | Read |
|---|---|
| **running** the code — fresh checkout to `lagen all serve` | [`operating/`](operating/README.md) |
| **developing** the code — sources, stages, adding a vertical | [`developing/`](developing/README.md) |
| **consuming** the API / JSON / bulk data | [`api/`](api/README.md) |
| an **end user** of lagen.nu (svenska) | the `/om/*` pages in the `lagen-wiki` content repo (`site/om/`) |

End-user help pages live in the git-backed content repo (`lagen-wiki`, `site/om/`),
not here — they are published at `/om/<slug>` and edited as wiki markdown.

Reference documents:

- [`developing/source-map.md`](developing/source-map.md) — every module in the package and what it owns.
- [`conventions.md`](conventions.md) — the citable coding-rule catalog.
- [`prd-stats.md`](prd-stats.md) — the corpus-measurement catalog for the `stats` source, with the unmet measurements marked.
- [`local-llm.md`](local-llm.md) — running a local vision/reasoning model (Qwen3.6 on llama.cpp) for the `ai-*` passes.
