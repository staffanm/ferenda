# Documentation

Documentation for the accommodanda pipeline behind lagen.nu, by audience.

| I am… | Read |
|---|---|
| **running** the code — fresh checkout to `lagen all serve` | [`operating/`](operating/README.md) |
| **developing** the code — sources, stages, adding a vertical | [`developing/`](developing/README.md) |
| **consuming** the API / JSON / bulk data | [`api/`](api/README.md) |
| an **end user** of lagen.nu (svenska) | the `/om/*` pages in the `lagen-wiki` content repo (`site/om/`) |

End-user help pages live in the git-backed content repo (`lagen-wiki`, `site/om/`),
not here — they are published at `/om/<slug>` and edited as wiki markdown.

Background and reference (not audience guides):

- [`../REWRITE.md`](../REWRITE.md) — why the system is shaped this way; done vs. pending.
- [`../accommodanda/README.md`](../accommodanda/README.md) — the module map.
- [`conventions.md`](conventions.md) — the citable coding-rule catalog.
- [`deploy-vps.md`](deploy-vps.md) — the production deployment runbook.
- [`local-llm.md`](local-llm.md) — running a local vision/reasoning model (Qwen3.6 on llama.cpp) for the `ai-*` passes.
