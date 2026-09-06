# tools

Scripts that live beside the pipeline but are not part of it. `lagen` never
calls them. Run each one from the repository root, through `uv run`.

## corpus/ — measure the corpus and prove it correct

| Tool | Input | Output | Destructive |
|---|---|---|---|
| `golden_sfs.py` | a parsed SFS document (`.xhtml` or `.json`) and its reference twin | the normal form, or a section-by-section comparison | no |
| `golden_dv.py` | the retained distilled RDF under `site/data/dv/distilled/` and today's artifacts | per-field agreement counts plus example differences | no |
| `golden_dv_structure.py` | the retained parsed XHTML and today's artifacts | the instance/ruling skeleton compared per document | no |
| `golden_eurlex.py` | the EUR-Lex artifacts, plus a snapshot file | a metadata snapshot, or a snapshot-against-now comparison | writes the snapshot file it names |
| `render_equivalence.py` | the built site, in-process | a page snapshot under `DATA/render-eq/<label>`, or a diff between two labels | writes and overwrites the snapshot it labels |
| `pii_scan.py` | the published case-law artifacts | candidate personal-data identifiers as JSON | no |
| `pii_worklist.py` | `pii_scan.py`'s candidates | one ranked review row per document | no |
| `icj_vocabulary.py` | the harvested I.C.J. Reports PDFs | `ferenda/icj/data/vocabulary.txt` | overwrites that dataset |
| `namedlaws_history.py` | the SFS artifacts | the dates each named law held its name; `--write` updates the dataset | only with `--write` |
| `sfs_coverage_truth.py` | the SFS archive, the mirrored PDFs and `sfs.coverage` | a JSON row per archived consolidation reconstructed from its predecessor and diffed against the real file, plus the counts per status (match, mismatch, not_simple, unreadable) | no |
| `og_image.py` | the mark, the fonts and the palette under `ferenda/lib/assets/` | `ferenda/lib/assets/og-image.png`, the link-preview card every page's `og:image` names | overwrites that image |

The golden comparison is a change-detector, not an oracle. Read
[`../docs/developing/testing.md`](../docs/developing/testing.md) before acting
on a difference.

## screencast/ — film the site for the manual

| Tool | Input | Output | Destructive |
|---|---|---|---|
| `record.py` | a cast (`casts/<name>.json`: a viewport and a list of steps) and the public site | `<name>.webm` plus its poster `<name>.png` in the content repo's `site/media/`; a `shot` step writes a still there too | overwrites those files |

Needs the system `ffmpeg` (`apt install ffmpeg`): Playwright records VP8 with no quality setting, and the recorder re-encodes to VP9 at about half the size.

One cast per manual chapter (`../lagen-wiki/site/om/<chapter>.md` embeds
`<name>.webm`). Re-record a cast when the chapter's text changes, then commit
the new files in the content repo. It records the public site by default;
`--base http://localhost:8000` films a local `lagen serve` instead.

## operations/ — run and watch the live service

| Tool | Input | Output | Destructive |
|---|---|---|---|
| `fetch_poi.sh` | Maven Central | the POI jar stack in `vendor/poi/` | no; skips jars already there |
| `download-data.sh` | the prod host over ssh | the corpus and catalog in this checkout | adds and updates local files; never deletes |
| `keepwarm.sh` | the prod OpenSearch index | a warmed page cache | no |
| `apitail.sh` | the prod REST log | one line per request, followed live | no |
| `mcptail.sh` | the prod MCP log | one line per tool call, followed live | no |
| `eurlex_pipeline.sh` | an `unpack-bulk` run in progress | parses each document as it lands, then relates and generates | writes artifacts, as `lagen` does |
| `run-all-sou.sh` | `all-sou.txt` | one `remisser ai-analyze` layer per ärende | writes `.ann` layers; skips an ärende that has one |

`eurlex_pipeline.sh` and `bench_all.sh` hold an absolute checkout path. Edit it
before running them on another machine.

## evaluation/ — score the LLM passes against ground truth

| Tool | Input | Output | Destructive |
|---|---|---|---|
| `aigenomforande-bench/dump_fk.py` | the eligible props | the candidate författningskommentar entries the pass sees | writes into its own `fk/` directory |
| `aigenomforande-bench/bench_one.py` | one prop, plus the endpoint in the environment | the payload, stats and token usage as JSON | never touches the annstore |
| `aigenomforande-bench/make_golden.py` | the authored ground-truth mappings | `.ann.golden` layers in the curated store | writes those layers |
| `aigenomforande-bench/evaluate.py` | a benchmark run and its golden | precision and recall per edge field | no |
| `remisser-eval/import_keys.py` | an authored answer key | `.ann.key` layers in the curated store | writes those layers |
| `remisser-eval/make_briefs.py` | one ärende | the reading briefs a human answers from | writes brief files |
| `remisser-eval/evaluate.py` | an ärende's layers and its answer key | the score | no |

These passes call a metered LLM endpoint unless `llm_base_url` points at a
local model. See [`../docs/local-llm.md`](../docs/local-llm.md).

## migrations/ — one-time conversions kept for a reason

| Tool | Input | Output | Destructive |
|---|---|---|---|
| `compress_downloaded.py` | a `downloaded/` tree fetched before the compression policy | the same tree, Brotli-compressed in place | rewrites the download tree |
| `mediawiki_to_markdown.py` | the MediaWiki SQLite database | the `lagen-wiki` markdown repo, one commit per revision | writes a new repository |
| `wiki_artifact_diff.py` | that database and the converted repo | the losslessness report | no |

The MediaWiki conversion is finished; `lagen-wiki` is authoritative now. Both
files stay because `test/test_wiki.py` uses the converter and the old wikitext
path as its reference for the losslessness property.
