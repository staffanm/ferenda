---
name: ux-audit
description: Browse the generated site as a reader and write up what makes sense, what is broken, and what is built but not paying off. Produces a dated markdown report with embedded screenshots and URLs. Use when the user asks to browse/review the site as a user, do a UX pass, or find what is broken in the UI.
---

# Auditing the rendered site as a reader

The deliverable is a markdown file with screenshots and URLs, not a chat
summary. Everything below exists to make that file trustworthy.

## 0. Do not read the existing issue lists first

**Before browsing, do not open `ISSUES.md`, prior audit reports, or any
other backlog.** Reading one primes you to check items off instead of
looking, and to skip whole areas as "already known" — that is exactly how
a previous audit missed the JO heading bug, the ARN duplicated title and
1 695 unreachable föreskrifter, all in areas a stale list had marked
covered.

Browse first, write your findings, *then* diff against the existing lists
in a final pass. Backlogs here go stale fast: on the last run most of
`ISSUES.md` described behaviour that had already been fixed.

## 1. Get a site in front of you

```sh
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/   # already serving?
lagen all serve                                                   # if not (site + API, one process)
```

Note the generated tree's freshness before drawing conclusions — a
finding that is really "this page is from last week's build" is a
different bug from a layout bug (see §5).

## 2. Pick a reader and walk journeys, not pages

Adopt a concrete persona and say which one in the report. The useful
default: *understands legal sources, is not a legal expert* — knows what
a proposition and a prejudikat are, does not know the corpus.

Walk whole journeys:

- **Entry** — frontpage, then every top-nav destination. Does each say
  where you are, or drop you inside an arbitrary bucket?
- **Read a document of every kind** — statute, court decision, agency
  decision, föreskrift, förarbete, EU act, treaty, begrepp. One of each,
  minimum.
- **Follow context** — from a paragraph out to its cases, comments and
  förarbeten, and back.
- **Search** — one plain-language question, one pinpoint citation, one
  concept word. Compare the ⌘K palette against `/sok/`.
- **Interactive chrome** — hover previews, split view, version compare,
  facsimile toggles, rail expansion. These are the features most likely
  to be built but not paying off.
- **Mobile** — 390×844, `is_mobile=True`.

Use the scripts in `scripts/` (see §6) rather than rewriting a harness.

## 3. Never invent a URL

Pull real basefiles from the catalog, and pull real link targets from the
page you are on. Guessed URLs 404, and a 404 you caused looks exactly
like a broken link in the report.

```sh
.venv/bin/python -c "
import sqlite3
c = sqlite3.connect('site/data/catalog.sqlite')
for r in c.execute(\"select uri from documents where source=? order by random() limit 3\", ('dv',)):
    print(r[0])"
```

```sh
curl -s http://localhost:8000/myndigheter/ | grep -o 'href=\"[^\"]*\"' | sort -u
```

## 4. Measure before you claim

A screenshot shows a symptom; the claim in the report needs a cause.
Every finding gets one line of evidence a reader can re-run.

- **Counts and coverage** — from the catalog, not from eyeballing a
  listing. `select source, count(*) from documents group by source`.
- **The link graph** — `links.to_root` against `documents.uri` finds
  citation dead ends and bogus targets. Sources with zero inbound links
  are browse-only corners of the site.
- **Computed style, not appearance** — read `getComputedStyle` on the
  actual element. Measuring the wrong node produced a false "font family
  mismatch" finding last run; the label matched all along.
- **The API behind a UI** — when a page looks wrong, hit
  `/api/v1/search` etc. directly to see whether the data or the rendering
  is at fault. They need different fixes and the report should say which.

Prefer disproving your own finding before writing it down.

## 5. Check for stale generated pages

Before blaming layout code, ask whether the page was rebuilt at all.

```sh
find site/data/generated/<area> -name 'index.html.br' -printf '%TY-%Tm-%Td\n' | sort | uniq -c
```

A split like `999 pages from 2026-07-29 / 604 from 2026-08-02` means
generate is not reaping output whose input dropped out of the index set.
Those pages keep serving the previous masthead, the previous nav and the
previous naming — which reads as a dozen unrelated UI bugs. Find the root
cause once instead of filing the symptoms.

A quick tell: compare the top nav on a suspect page against a known-fresh
one. Divergent chrome means divergent build.

## 6. The harness

`scripts/browse.py` — walk a list of URLs, screenshot each, and dump per
page: status, title, `h1`s, nav labels, rail headings, `dl.meta` keys and
values, heading list, anchor and empty-anchor counts, console
errors/warnings, failed requests.

```sh
.venv/bin/python .claude/skills/ux-audit/scripts/browse.py \
    --out <shots-dir> / /sfs/ /dom/ /folkratt/
```

`scripts/shot.py` — one URL, optionally scrolled to an anchor or a pixel
offset, optionally full-page, optionally clipped to a selector or a region.

```sh
.venv/bin/python .claude/skills/ux-audit/scripts/shot.py \
    --out <shots-dir> --name brb-k3p5 '/1962:700#K3P5'
.venv/bin/python .claude/skills/ux-audit/scripts/shot.py \
    --out <shots-dir> --name rail --scroll 14000 --clip 1200,0,400,1000 --scale 2 '/2009:400'
```

An image over 2000px in either dimension cannot be read back, so two
traps to avoid:

- `--full --scale 2` on a long document blows the limit. Drop the scale
  or clip instead.
- `--selector` on an absolutely-positioned container grabs its full
  height: `--selector .rail` on Brottsbalken yields a 288×236118 strip.
  For rail detail use `--scroll` plus `--clip` on the right-hand column.

For interaction — hovers, keyboard, clicking a rail stub, opening the
palette — write a throwaway Playwright script in the scratchpad. Read the
asset JS first (`accommodanda/lib/assets/*.js`) to get the real selectors
and key bindings; guessing them produces "feature is broken" findings
about features that work.

## 7. Write the report

Write to `ux-audit/<YYYY-MM-DD>/findings.md`, screenshots in
`ux-audit/<YYYY-MM-DD>/shots/`, referenced with relative paths so the
markdown renders anywhere.

Group findings by area, each with a short prefix and a number, in the
style of `ISSUES.md` — pick prefixes that do not collide with the ones
already in use there (currently `C`, `F`, `A`, `T`, `S`). One line per
finding, then evidence.

````markdown
## R — kontextrutorna

### R2 The open panel hides the stubs behind it

<http://localhost:8000/2009:400> — scroll to 7 kap. 4 §

![rail overlap](shots/rail-overlap.png)

Background is `color(srgb 0.96 0.96 0.97 / 0.88)` against a page
background of nearly the same hue, and 6 stubs sit inside the panel's
bounds at this scroll position. The translucency exists but buys nothing
at that alpha.
````

Open the report with the persona, the build timestamp of the site you
browsed, and the three buckets the user asks for:

- **What makes sense** — name it specifically. This is the calibration
  that makes the rest credible, and it stops a fixed thing being refiled.
- **What is clearly broken** — visible wrongness or a wrong answer to the
  reader.
- **What is in place but not paying off** — machinery that exists and
  works, whose value never reaches the page. Usually the highest-leverage
  section; find it by comparing what the catalog and API know against
  what the page shows.

Close with a recommended first fix and why.

## 8. Only now, reconcile with the backlog

Read `ISSUES.md` and any previous report. For each existing entry, say
whether this run found it still live, fixed, or partly fixed — and back
"fixed" with a measurement, not a description match. Do not edit
`ISSUES.md`: it is the user's file. Keep your findings in your own report
and let them merge.
