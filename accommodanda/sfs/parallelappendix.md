# Parallel-text appendix parser — status and handoff

`accommodanda/sfs/parallelappendix.py` parses a statute whose sole `Bilaga` is a
convention printed as parallel text — the same treaty in two or three languages
(the European Convention on Human Rights, tax and social-security conventions,
the ~40 tax information-exchange agreements, and so on). It handles this one
appendix shape with **no per-law knowledge** and is wired into the SFS pipeline
(`sfs/__init__.py::_assemble`). A parallel appendix becomes a
`Konventionsbilaga`; anything else uses the ordinary flat statute parser.

The goal is **a valid, aligned parse**, not any particular byte-for-byte shape.
The JSON artifact remains authoritative; SQLite and rendered HTML are derived.

## How it works

Core idea: **structure finds the blocks; language detection labels them.**

1. `parse(text)` splits a statute on its single `Bilaga`. No appendix, or more
   than one appendix, means this specialised parser declines with `None`.
2. `paragraphs(text)` normalises blank-line paragraphs and recovers layout
   forms that still have an unambiguous structural reading:
   - an `Article`/`Artikel`/Turkish `Madde` heading and its title/body printed
     in one paragraph;
   - a sequential article heading glued after sentence-final punctuation;
   - an article heading glued to a division title;
   - standard SFS `/…/` amendment directives before a heading.
   A prose reference such as `as set out in\nArticle 5 of the Convention` is not
   split: the number is not sequential after a completed sentence.
3. `language_blocks(paras)` finds each `Article 1` sequence, detects its first
   substantial body text, coalesces adjacent sequences in the same language,
   and then labels each complete block once. Short titles and division headings
   are treated as structural boundary material, not independent language votes.
4. `parse_block(...)` reads instruments, articles and divisions. A fresh
   `Article 1` or `Protocol …` starts an instrument; Roman article numbers and
   French `Article premier` fold to Arabic ordinals. Division subtitles on the
   following paragraph are retained in the heading.
5. `to_model(runs)` requires the ordered instrument/article sequences to agree.
   Divisions are navigation rather than legal text, so one language may omit a
   compatible division heading; the missing title becomes an empty cell. A
   conflicting division order or article sequence raises `AppendixMisaligned`,
   and `_assemble` deliberately flat-parses the source instead.
6. `lenient(...)` performs only harmless text normalisation: de-hyphenating a
   word split across a break and joining a marker's continuation line.

Duplicated or reordered source articles are never guessed away. The strict
article-sequence check is the safety boundary.

## Corpus result

On the current downloaded SFS corpus, 107 appendices contain at least two
structurally recognised `Article 1` headings:

| Outcome | Count |
|---|---:|
| **Aligned `Konventionsbilaga`** | **95 (89%)** |
| Parallel-looking but structurally inconsistent → flat | 5 (5%) |
| One detected language → flat | 7 (6%) |

This is up from 84 aligned appendices before the structural-boundary work.
Besides the previously working ECHR, Montreal, tax-information-exchange, tax,
social-security, customs and health-care families, the parser now handles:

- division/layout cases `2004:519`, `2005:234`, `2012:318`, `2015:860` and
  `2018:1197` (the Convention on the Rights of the Child);
- glued-heading cases `2009:1119`, `2014:340`, `2016:408`, `2016:927` and
  `2016:928`;
- directive-wrapped ATMF `2022:366`, including its ragged paragraph columns.

### Deliberate flat fallbacks (5)

Three sources have genuinely different article sequences between their printed
languages:

- `2014:834` repeats English Article 7;
- `1996:877` repeats English Article 16;
- `2012:638` repeats Swedish Articles 17–22.

Those need curated source patches if they should become parallel artifacts; the
parser must not silently discard legal text.

`2015:338` and `2018:181` bundle several treaties (COTIF/CIV/CIM/ATMF and
others), each with its own language copies. Their sequence is not the single
convention-plus-protocol shape this module owns. Flat fallback is preferable to
per-law grouping rules here.

### Correctly monolingual (7)

These are Swedish-only texts or bundles with repeated article sequences, not
parallel corpora: `1959:467`, `1969:200`, `1971:850`, `1987:1119`, `1992:138`,
`1992:588`, `1998:358`.

## Anchors and treaty identity

Each instrument carries a **protocol number** (the printed number, `1` for an
unnumbered additional protocol, `None` for the base convention) and its **title
and preamble** as `ingresser` — the formal title and "have agreed as follows"
recital that precede Article 1 are incorporated text, not boilerplate to drop.

The SFS projection (`nf.py`) turns the protocol number into a stable anchor: the
base convention at the bilaga fragment `#B1`, each protocol at `#B1P<n>` (`#B1P4`
for Protocol No. 4). Treaty identity is then a **table**, not code: the curated
`sfs/data/incorporates.json` maps `{sfs}#{fragment}` to the `source/number` of
the treaty an instrument reproduces (`"1994:1219#B1P4": "coe/046"`), which the
projection resolves to an `ext/coe/046` URI so the articles become citable
`/coe/046#Ax` targets. The `source` prefix keeps it general — other treaty series
than CoE can be added. An instrument absent from the map still anchors
structurally but mints no treaty URI; no SFS-number branching lives in the
parser.

## Remaining improvements

1. **Ragged rendering.** The model pads the shorter languages with empty cells
   to preserve aligned rows. Rendering each language's paragraphs as an
   independent flow would read better where the official copies have different
   paragraph counts.
2. **Multi-treaty grouping, if justified.** Supporting the two COTIF bundles
   needs a generic, tested grouping rule and probably a richer model. Do not add
   SFS-number switches to this parser.

## Regression coverage

`test/test_parallelappendix.py` checks the three-language ECHR fixture through
SFS normal-form projection and a compact layout fixture covering directives,
Roman numerals, English/Swedish/Turkish article keywords, heading/body joins,
safe glued headings, subtitles and a division omitted in one language. Parser
fixes belong in those frozen fixtures or in the golden corpus
(`rule:lock-in-with-fixture`).

## Reproduce the tally

```python
import json
from collections import Counter
from pathlib import Path

from accommodanda.lib import patch
from accommodanda.sfs import parallelappendix as pa

outcomes = Counter()
for path in Path("site/data/downloaded/sfs").glob("*/*.json"):
    source = json.loads(path.read_text())
    raw = source.get("fulltext", {}).get("forfattningstext")
    if not isinstance(raw, str):
        continue
    sfs = f"{path.parent.name}:{path.stem}"
    text = patch.apply("sfs", sfs, raw.replace("\r", ""))
    parts = pa.RE_APPENDIX.split(text)
    if len(parts) != 2:
        continue
    paras = pa.paragraphs(parts[1])
    if sum(pa._article_number(paragraph) == 1 for paragraph in paras) < 2:
        continue
    try:
        outcomes["aligned" if pa.parse(text) else "single-language"] += 1
    except pa.AppendixMisaligned:
        outcomes["misaligned"] += 1

print(outcomes)
```
