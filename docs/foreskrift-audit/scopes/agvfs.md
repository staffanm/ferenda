# AGVFS — AgVFS, Arbetsgivarverket

Verdict: DEFECT
Site list: 20 designations from 2 pages (https://www.arbetsgivarverket.se/avtal-och-skrifter?category=32)
We hold: 9 documents (agvfs), newest AgVFS 2026:3; site newest AGVFS 2026:3
Missing: 3 — AgVFS 2000:2, AgVFS 2001:1, AgVFS 2002:3
Extra: 0
Repeal gaps: 0 — the site marks no AgVFS document as upphävd; our links table shows no rpubl:upphaver rows for agvfs
Title defects: 7 of 9 — bare designation stands in for the real title (examples below)
Consolidations: site no, we hold 0
Inherited: none (assignment says none; the site's own AgVFS index also lists 7 SAVFS documents, 1979-1990, but no "savfs" fs exists in the corpus and the assignment does not name SAVFS as inherited, so this is reported but not treated as a defect)
Issue: https://github.com/staffanm/ferenda/issues/42

## Evidence

### 1. Enumerate
Fetched the entry page and page 2 (`?category=32&page=2`) with `ferenda.lib.net.request`.
The page's own embedded JSON (`__NEXT_DATA__`) states
`"pagination":{"total":20,"display":10,"totalDisplay":20,"currentPage":2,"pageCount":2}`.
Page 1 yields 10 anchors matching `a[href*="/avtal-och-skrifter/agvfs/agvfs-"]`
(AgVFS 2026:3, 2026:2, 2026:1, 2016:1, 2010:1, 2010:2 A2, 2010:2 A3, 2007:1,
2003:7, 2003:5). Page 2 yields 10 more items: 3 more AgVFS designations
(2002:3, 2001:1, 2000:2) and 7 SAVFS designations (1990:3, 1986:11, 1984:11,
1984:6, 1983:11, 1983:5, 1979:4).

### 2. Missing documents — harvest bug, index page 2 is never fetched
`ferenda/foreskrift/agencies.py` configures AGVFS with `enumerate=indexed_enumerate`
and a single `index_url` (no `index_urls` list, no pagination parameter). Looking
at `ferenda/foreskrift/harvest.py:531` (`indexed_enumerate`), it iterates
`p.get("index_urls", [agency.index_url])` — for AGVFS that is one URL, so page 2
of the site's own listing is never requested.

3 documents whose href already matches `link_select` (they carry "agvfs-" in the
path) sit only on page 2 and are absent from our corpus at every layer:

    $ ls site/data/artifact/foreskrift/agvfs/ | grep -E '2000-2|2001-1|2002-3'
    (no output)
    $ ls site/data/downloaded/foreskrift/agvfs/ | grep -E '2000-2|2001-1|2002-3'
    (no output)

AgVFS 2002:3 is not a dead end either — our own AgVFS 2007:1 amends it
(`links` row: `agvfs/2007:1 --rpubl:andrar--> agvfs/2002:3`), and five outside
documents (dir/2002:133, sou/2004:24 x4) cite it. AgVFS 2001:1 is cited from
sou/2003:85 and sou/2009:50. Both targets resolve to nothing in our corpus.

    sqlite3 catalog: select * from links where to_uri like '%agvfs/2002:3%' or to_uri like '%agvfs/2001:1%';

The 7 SAVFS items on page 2 are the predecessor series named in the site's own
category-32 index, but the assignment lists no inherited series for AGVFS and
no `savfs` fs exists anywhere in the corpus (`grep -rn savfs ferenda/foreskrift/`
returns nothing). Reported as an observation, not scored as a defect, per the
assignment.

### 3. Extra documents
None. Every one of our 9 held basefiles has a matching site entry.

### 4. Repeal marking
    $ grep -io 'upphävd\|upphört att gälla\|upphävts' agvfs_index.html agvfs_index2.html
    (no output)
No document is struck through or marked upphävd on either index page. The
catalog's `links` table has no `rpubl:upphaver` row with an agvfs target.
Nothing to check here — the shape does not apply to this scope today.

### 5. Titles — 7 of 9 documents carry only the designation as a title
The parse-stage source of truth (`site/data/artifact/foreskrift/agvfs/*.json`,
key `metadata.title`) is `None` for 7 of the 9 documents. The catalog then
falls back to the bare identifier, so `documents.title` repeats the
designation instead of holding a real title:

| designation | we hold (documents.title) | site heading (minus designation) |
|---|---|---|
| AgVFS 2003:7 | `AgVFS 2003:7` | Förordning om skyldighet för myndigheter att lämna uppgifter om anställdas kompetenskategorier |
| AgVFS 2007:1 | `AgVFS 2007:1` | Förordning om ändring i förordningen (AgVFS 2002:3 B3) om särskild ersättning till medföljare till anställd inom utrikesförvaltningen |
| AgVFS 2010:1 | `AgVFS 2010:1` | Förordning om ändring i förordningen (SAVFS 1983:11) om betalning av skatt på pensioner åt vissa lokalanställda vid svenska utlandsmyndigheter |
| AgVFS 2010:2 | `AgVFS 2010:2` | Förordning om ändring i förordningen (SAVFS 1990:3 A1) om utbetalning av personskadeersättning |
| AgVFS 2026:1 | `AgVFS 2026:1` | Förordning om anställning vid den nya myndigheten Myndigheten för utrikes underrättelser |
| AgVFS 2026:2 | `AgVFS 2026:2` | Förordning om anställning vid den nya myndigheten Miljöprövningsmyndigheten |
| AgVFS 2026:3 | `AgVFS 2026:3` | Förordning om ändring i förordningen (AGVFS 2026:1 B1) om anställning vid den nya myndigheten Myndigheten för utrikes underrättelser |

The 2 that succeed (AgVFS 2003:5, AgVFS 2016:1) are both titled "Föreskrift
om ändring i ...". The 7 that fail are all titled "Förordning om ...".

Root cause, `ferenda/foreskrift/parse.py:930` (`RE_TITLE_TYPE`):

    RE_TITLE_TYPE = re.compile(
        r"\b(?:föreskrifter|föreskrift|allmänna\s+råd|allmänt\s+råd|kungörelse"
        r"|tillkännagivande)\b", re.IGNORECASE)

`title_from_masthead` (parse.py:1108) anchors its extraction on this regex.
"förordning" is not one of the recognised type words, so every AGVFS
"Förordning ..." masthead returns `None`. Confirmed against the source PDF —
`agvfs-2003-7-regulation.pdf` extracts cleanly with `pdftotext -layout` and
prints its title in plain text on the first page ("Förordning om skyldighet
för myndigheter att lämna uppgifter om anställdas kompetenskategorier;"), so
this is a masthead-regex gap, not a PDF/OCR problem.

### 6. Consolidations
No landing page or index page mentions "konsoliderad"/"konsolidering"; each
landing page hangs exactly one PDF (the regulation itself). Every one of our
9 download records shows `consolidation: 0`. Site and corpus agree: none.

### 7. Inherited series
Assignment: none. The site's own index (page 2) additionally lists 7 SAVFS
documents (1979-1990) under the same category, but neither the corpus nor
`ferenda/foreskrift/agencies.py` holds or names an SAVFS/predecessor series
for Arbetsgivarverket. Reported, not scored, per the "assignment says none"
instruction — a design call for Staffan, not a filed defect.

### 8. Freshness
Site newest: AGVFS 2026:3 B3 (page 1, first item). We hold newest: AgVFS
2026:3, date 2026-04-14. Match.

### Commands used
    .venv/bin/python -c 'from ferenda.lib.net import make_session, request; ...'  # entry page + page 2 + 2 landing pages, 4 requests total
    sqlite3.connect("file:site/data/catalog.sqlite?mode=ro", uri=True)
    ferenda.lib.compress.read_text() over site/data/artifact/foreskrift/agvfs/*.json
    ls site/data/downloaded/foreskrift/agvfs/
    pdftotext -layout site/data/downloaded/foreskrift/agvfs/agvfs-2003-7-regulation.pdf -
    grep -n "RE_TITLE_TYPE\|title_from_masthead" ferenda/foreskrift/parse.py
    grep -n "AGVFS = Agency" -A 12 ferenda/foreskrift/agencies.py
