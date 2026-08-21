# Handover: getting the LOU core canon into the EU case-law rail

**Goal.** A curated canon of ~60 EU Court judgments is regarded as core practice
for LOU (2016:1145). As of the first measurement (2026-07-26 morning) the
generated LOU page showed **13** of them. After the work below (same day) it
shows **60 of 60**. Done.

The canon holds **67 case numbers but 60 distinct judgments** — seven entries are
the second number of a joined case, which CELLAR files under the first number
only. Those seven are **not** misses and were not chased.

## What was done (2026-07-26, all uncommitted in the working tree)

Each step was measured against the generated page with the verification recipe
below; the counts are the page's, not estimates.

1. **Blocker 1 — the Formex `<NP>` element (13 → 43).**
   `eurlex/parse.py _parse_judgment_contents` read only `NP.ECR`; judgments
   before ~2012 wrap their numbered paragraphs in plain `NP` (`NO.P` marker +
   `TXT`), so two thirds of the judgment corpus parsed to header + preamble and
   stopped. The fix walks the body's top-level `NP`s (never descending into
   `NP`/`NP.ECR` — an inner `NP` is a quoted list item of a cited act — nor
   into `JURISDICTION`), sharing one `_np_paragraph` reader with the opinion
   path. `parse_judgment` now also finds `JURISDICTION` nested inside
   `CONTENTS.JUDGMENT` (where it actually lives in all 300 sampled files), so
   the ruling blocks exist again. Locked in with a fixture trimmed from the
   real C-513/99 Concordia Bus Formex (`test_eurlex_parse.py`).
   Reparsed all 63,902 eurlex docs (0 errors); relate re-extracted the 16,220
   artifacts that changed. Concordia went from 8 nodes / 0 links to 96
   numbered paragraphs / 16 act-article targets.

2. **Pinpoint-precise assignment (43 → 50; reworked same day).**
   The first fix widened the exact-match join with a dotted range so `#57.4`
   citations reached the `#57` target — which surfaced Remondis, Fabricom,
   Max Havelaar, Forposta, Delta Antrepriză, TNS Dimarso and Pressetext, but
   over-joined: every 57-citing case showed at all seven 13 kap. paragrafer.
   Replaced by `catalog.caselaw_anchored`: the statute's whole rail is
   assigned in one pass, each citation landing on the paragraf whose
   genomförande *pinpoint* covers it most deeply (a 57.4 case sits at
   13 kap. 3 §, not at 13 kap. 1–7 §§); ties prefer a direct claim over an
   inherited one, then the first paragraf in statute order; a citation
   nobody's pinpoint covers falls back to the article family's first
   paragraf. Claims on anchors the rendered consolidation no longer has
   (22 kap., renumbered away 2021) cascade to a live paragraf. The lineage
   walk (`predecessor_atoms`) keeps the correlation tables' own sub-article
   precision where they have any (`new_pinpoint`/`old_pinpoint`). Tests:
   the `caselaw_anchored`/`predecessor_atoms` suite in
   `test_eurlex_correspond.py`, plus the `test_site.py` rail tests.

3. **Directive lineage extended to the first generation (50 → 52).**
   The 1993 codifications' correlation tables are absent from the CELEX-era
   text we hold (`/* Tables: see OJ */`), so the lineage stopped at 92/50,
   93/36, 93/37. Two source patches (`patches/eurlex/1993/` in the content repo)
   restore them: 93/37's Annex VIII (71/305 → 93/37) typed from the OJ PDF
   (EUR-Lex EN — verified the only possibility: the act's EUR-Lex metadata
   lists special editions only in the 2004-accession languages, no Swedish or
   Finnish edition exists, CELLAR holds only `eng.html`, and the SV
   legal-content render endpoints return 202 indefinitely), 93/36's bilaga VI
   (77/62, 80/767, 88/295, 90/531, 92/50 → 93/36) from svensk specialutgåva
   06/04 (that act *does* have a Swedish special edition, chapter 06 vol 004
   p. 126–176 — the PDF EUR-Lex serves as its SV manifestation). `correspond.HEADER_ACT` learned English act designations
   ("Directive 71/305/EEC") since the Swedish citation grammar never links
   them, and `catalog.LINEAGE_DEPTH` went 2 → 3 so a LOU paragraf's 2014/24
   article reaches the 1971/1977 directives. That brought in Dundalk and
   SIAC Construction. 41 articles of 71/305+77/62 are now rail targets.

4. **Wiki commentary for six cases the derived route could not reach
   (52 → 58; see step 6 for two that later became derivable).** In `../lagen-wiki/commentary/sfs/2016/1145.md` (validated,
   no dangling anchors):
   - *Beentjes* (1 kap. 22 § — funktionellt myndighetsbegrepp; 17 kap. 1 § —
     sociala kontraktsvillkor). Pre-1995, English text only, and the citation
     grammar is Swedish-only, so no derived route exists (Blocker 2).
   - *Ballast Nedam* (14 kap. 6 §). Same pre-1995/English situation.
   - *Succhi di Frutta* (4 kap. 1 §). A CAP-regulation tender; cites no
     procurement directive at all.
   - *Rüffert* (17 kap. 2 §). Cites only utstationeringsdirektivet 96/71,
     which LOU does not transpose.
   - *Kommissionen mot Italien C-337/05* (6 kap. 14 §). Cites 77/62 in its
     88/295 amendment lydelse — article numbers outside every correlation
     table.
   - *UNIX C-359/93* (9 kap. 6 §). Cites only 89/665 art 3 (the Commission
     corrective mechanism, not transposed into LOU).

5. **The remedies-directive route is a dead end; commentary for the last two
   (58 → 60).** Hotel Loutraki (C-145/08) and Archus (C-131/16) cite the
   remedies directives (89/665, 2007/66). The ai-genomforande rerun over
   prop 2015/16:195 with those directives added (`LLM_BATCH_CHARS=60000`,
   `MAX_TOKENS` raised 32k → 64k in `aigenomforande.py` after Qwen blew the
   limit on a 123-§ batch) completed cleanly — 795 edges, F0.5 0.996 against
   the golden layer, no regression vs the gemma `.ann` it replaced — but
   found only **one** 89/665 edge, onto LUK 8 kap. 7 §, none onto LOU.
   Root cause, verified in the prop text: LOU's 20 kap. författningskommentar
   in prop 2015/16:195 doesn't restate the remedies-article mapping, it
   cross-references prop 2009/10:180. The mapping does not exist in this
   prop, so no LLM pass over it can find it. Both cases got commentary
   instead: Loutraki at 2 kap. 12 § (huvudföremålsregeln), Archus at
   4 kap. 9 § (komplettering av anbud).

6. **English citation grammar for pre-accession case law (Blocker 2 fixed).**
   The citation engine gained an English EULAGSTIFTNING surface
   (`lagrum.LagrumParser(lang="eng")`: "Article 29 (5) of Directive
   71/305/EEC", "Regulation (EEC) No 2092/91", the-directive anaphora) and
   the pre-1989 case-number form ("Case 31/87", "mål 45/87" — marker word
   required, court defaults to the ECJ); `eurlex/parse.py` picks the grammar
   by the manifestation's language, and `REPORT.HEARING` Formex now parses
   (for the oldest cases, Beentjes included, the hearing report is the only
   text CELLAR holds — its "Relevant legislation" section carries the act
   citations). Beentjes went 1 note block / 0 links → 10 blocks / 18 links
   (71/305 arts 1, 20, 25, 26, 29); Ballast Nedam 1 → 16 links (71/305 arts
   1, 16, 21, 23, 25, 26, 28). Confirmed on the regenerated page after a
   full eurlex reparse (63,902 docs, 0 errors) + relate: Beentjes joins
   derivedly at 2:1, 4:9, 4:11 and 10:7, Ballast Nedam at 2:1, 4:4, 4:11,
   13:1 and 15:16, each attributed "om artikel N i 71/305, motsvarar …";
   the page grew 325 → 344 distinct cases from the enriched old-case links.
   Their commentary sections remain (the canon's paragrafer — 1:22/17:1 and
   14:6 — are not among the derived hits, since those points don't follow
   from transposed-article citations), with the "hittas inte via
   direktivsläktskapet" sentences corrected.

## Still open

- **The remedies chapters (20–22 kap.) still have no genomförande layer.**
  If that mapping is ever wanted derivedly, the source is prop 2009/10:180
  (old-LOU 16 kap.) plus the 2016 renumbering — a separate project, not a
  rerun of the pass above.
- **Full-corpus regenerate + index, again.** A full generate + index ran
  after the NP fix (280,017 pages, full reindex, 0 errors) — but the
  English-grammar reparse and the pinpoint-precise join then re-staled the
  corpus once more, and only the LOU page has been regenerated since
  (scoped `--no-deps --ignore-code-changes -f sfs generate 2016:1145`).
  Another full `lagen all generate` + `index` is needed; also note the
  citation-engine change (old case numbering, shared `lagrum.py`) staleness
  may reach the other verticals' parse fingerprints on their next
  un-scoped run.
- **`PINPOINT_CAP = 5` in `_citer_line`** still truncates inline pinpoint
  links ("m.fl."), deliberately unchanged — needs an inline-collapse UI idiom.
- The `62007CJ0573`-style `#2341` / `#234` junk pinpoints (an "artikel 234
  EG" anaphora mis-pinned onto a directive) predate this work and are
  untouched; they only ever over-join, never hide a case.

## What is already working — do not re-investigate

- **The rail join** (`render.eu_caselaw_margin` → `catalog.caselaw_anchored`)
  is correct and pinpoint-precise; the directive pages already carry
  per-punkt rails (`#57.4` has its own inbound panel, separate from `#57`).
- **Directive lineage for procurement is complete to 1971/1977**:
  `directive_correspondence` holds 2014/24→2004/18 (130 pairs),
  2004/18→{92/50, 93/36, 93/37} (67/50/59), 93/37→71/305 (36 edges),
  93/36→{77/62, 80/767, 88/295, 90/531, 92/50} (38 edges).
- **LOU's 2014/24 transposition mapping is healthy** (384 edges, 74 articles).
- **Hard caps in the rails are gone** (`lib/render._capped_list`, uncommitted
  from the previous session): a cap now only decides what starts collapsed.

## Verification recipe

```python
# 1. how many canon judgments reach the page
import re
from accommodanda.lib import compress
html = compress.read_text("site/data/generated/2016:1145.html")
present = set(re.findall(r"6\d{4}[A-Z]{2}\d{4}", html))

# 2. is a given judgment even a candidate for some LOU paragraf -- and where?
from accommodanda import build
from accommodanda.lib import catalog
con = catalog.connect(build.CATALOG)
LOU = "https://lagen.nu/2016:1145"
assigned = catalog.caselaw_anchored(con, LOU)   # anchor -> [(case row, prov)]
candidates = {row[0].rsplit("/", 1)[-1]
              for cases in assigned.values() for row, _prov in cases}

# 3. did a judgment yield any act citations at all?
con.execute("SELECT DISTINCT to_uri FROM links WHERE from_uri=?",
            ("https://lagen.nu/ext/celex/61999CJ0513",)).fetchall()
```

## The canon, case by case

Status measured 2026-07-26 against the regenerated page after steps 1–5.
**shown** = derived via the rail; *commentary* = shown via the hand-written
commentary layer.

| case | name | LOU kap. | CELEX | status |
|---|---|---|---|---|
| 31/87 | Beentjes | 1,16 | 61987CJ0031 | **shown** (eng text, 71/305 lineage) + commentary (1 kap. 22 §, 17 kap. 1 §) |
| C-44/96 | Mannesmann | 1 | 61996CJ0044 | **shown** |
| C-360/96 | BFI Holding | 1 | 61996CJ0360 | **shown** |
| C-380/98 | University of Cambridge | 1 | 61998CJ0380 | **shown** |
| C-223/99 | Agora | 1 | 61999CJ0223 | **shown** |
| C-260/99 | Excelsior | 1 | 61999CJ0260 | joined with C-223/99 |
| C-220/05 | Auroux | 1 | 62005CJ0220 | **shown** |
| C-451/08 | Helmut Müller | 1 | 62008CJ0451 | **shown** |
| C-107/98 | Teckal | 2,3 | 61998CJ0107 | **shown** |
| C-145/08 | Hotel Loutraki | 2 | 62008CJ0145 | commentary (2 kap. 12 §; cites only remedies directives) |
| C-149/08 | Hotel Loutraki II | 2 | 62008CJ0149 | joined with C-145/08 |
| C-26/03 | Stadt Halle | 3 | 62003CJ0026 | commentary (3 kap. 12 §; not a derived candidate) |
| C-458/03 | Parking Brixen | 3 | 62003CJ0458 | **shown** |
| C-340/04 | Carbotermo | 3 | 62004CJ0340 | **shown** |
| C-324/07 | Coditel Brabant | 3 | 62007CJ0324 | **shown** |
| C-573/07 | Sea | 3 | 62007CJ0573 | **shown** |
| C-182/11 | Econord | 3 | 62011CJ0182 | **shown** |
| C-183/11 | Econord II | 3 | 62011CJ0183 | joined with C-182/11 |
| C-480/06 | Kommissionen mot Tyskland (Hamburg) | 3 | 62006CJ0480 | **shown** |
| C-386/11 | Piepenbrock | 3 | 62011CJ0386 | **shown** |
| C-51/15 | Remondis | 3 | 62015CJ0051 | **shown** |
| C-383/21 | Sambre & Biesme | 3 | 62021CJ0383 | **shown** |
| C-384/21 | Sambre & Biesme II | 3 | 62021CJ0384 | joined with C-383/21 |
| C-324/98 | Telaustria | 4 | 61998CJ0324 | **shown** |
| C-19/00 | SIAC Construction | 4 | 62000CJ0019 | **shown** |
| C-496/99 | Succhi di Frutta | 4 | 61999CJ0496 | commentary (cites no procurement act) |
| C-21/03 | Fabricom | 4 | 62003CJ0021 | **shown** |
| C-34/03 | Fabricom II | 4 | 62003CJ0034 | joined with C-21/03 |
| C-538/13 | eVigilo | 4 | 62013CJ0538 | **shown** |
| C-337/05 | Kommissionen mot Italien | 6 | 62005CJ0337 | commentary (88/295 lydelse) |
| C-275/08 | Kommissionen mot Tyskland | 6 | 62008CJ0275 | **shown** |
| C-599/10 | SAG ELV Slovensko | 6,16 | 62010CJ0599 | **shown** |
| C-336/12 | Manova | 6 | 62012CJ0336 | **shown** |
| C-131/16 | Archus och Gama | 6 | 62016CJ0131 | commentary (4 kap. 9 §; cites 2004/17 + remedies directives) |
| C-216/17 | Coopservice | 7 | 62017CJ0216 | **shown** |
| C-23/20 | Simonsen & Weel | 7 | 62020CJ0023 | **shown** |
| 45/87 | Dundalk | 9 | 61987CJ0045 | **shown** |
| C-359/93 | UNIX | 9 | 61993CJ0359 | commentary (cites only 89/665 art 3) |
| C-368/10 | Max Havelaar | 9 | 62010CJ0368 | **shown** |
| C-424/23 | DYKA Plastics | 9 | 62023CJ0424 | **shown** |
| C-226/04 | La Cascina | 13 | 62004CJ0226 | **shown** |
| C-228/04 | La Cascina II | 13 | 62004CJ0228 | joined with C-226/04 |
| C-465/11 | Forposta | 13 | 62011CJ0465 | **shown** |
| C-124/17 | Vossloh Laeis | 13 | 62017CJ0124 | **shown** |
| C-41/18 | Meca | 13 | 62018CJ0041 | **shown** |
| C-267/18 | Delta Antrepriză | 13 | 62018CJ0267 | **shown** |
| C-395/18 | Tim | 13 | 62018CJ0395 | **shown** |
| C-176/98 | Holst Italia | 14 | 61998CJ0176 | **shown** |
| C-389/92 | Ballast Nedam | 14 | 61992CJ0389 | **shown** (eng text, 71/305 lineage) + commentary (14 kap. 6 §) |
| C-94/12 | Swm Costruzioni | 14 | 62012CJ0094 | **shown** |
| C-324/14 | Partner Apelski Dariusz | 14 | 62014CJ0324 | **shown** |
| C-387/14 | Esaprojekt | 14 | 62014CJ0387 | **shown** |
| C-210/20 | Rad Service | 14 | 62020CJ0210 | **shown** |
| C-513/99 | Concordia Bus | 16 | 61999CJ0513 | **shown** |
| C-448/01 | EVN Wienstrom | 16 | 62001CJ0448 | **shown** |
| C-532/06 | Lianakis | 16 | 62006CJ0532 | **shown** |
| C-601/13 | Ambisig | 16 | 62013CJ0601 | **shown** |
| C-6/15 | TNS Dimarso | 16 | 62015CJ0006 | **shown** |
| C-285/99 | Lombardini | 16 | 61999CJ0285 | **shown** |
| C-286/99 | Mantovani | 16 | 61999CJ0286 | joined with C-285/99 |
| C-346/06 | Rüffert | 17 | 62006CJ0346 | commentary (cites only 96/71) |
| C-549/13 | Bundesdruckerei | 17 | 62013CJ0549 | **shown** |
| C-115/14 | RegioPost | 17 | 62014CJ0115 | **shown** |
| C-454/06 | Pressetext | 17 | 62006CJ0454 | **shown** |
| C-91/08 | Wall | 17 | 62008CJ0091 | **shown** |
| C-549/14 | Finn Frogne | 17 | 62014CJ0549 | **shown** |
| C-160/08 | Kommissionen mot Tyskland (C-160/08) | 17 | 62008CJ0160 | **shown** |

Counts: 53 shown derived (Beentjes and Ballast Nedam through their English
texts), 7 commentary-only (incl. Stadt Halle, on the page but not a derived
candidate), 7 joined-case partners. 60 of 60 distinct judgments on the page.
