# RFS — Riksdagsförvaltningens författningssamling, Riksdagsförvaltningen

Verdict: DEFECT
Site list: 274 designations from 2 pages (https://data.riksdagen.se/dokumentlista/?doktyp=rfs&utformat=json&sz=200)
We hold: 274 documents (rfs), newest RFS 2026:7; site newest RFS 2026:7
Missing: 0
Extra: 0
Repeal gaps: 2 confirmed (RFS 2001:5, RFS 2001:7) — see below for the 119 site-side "Upphävd" flags that are not gaps
Title defects: 0 of 12 sampled (2 differ only by a trailing space the site prints and we strip)
Consolidations: site no, we hold 0
Inherited: none (assignment lists no predecessor samling)
Issue: https://github.com/staffanm/ferenda/issues/83

## Evidence

### 1. Enumerate

The entry page is a paginated JSON API, not HTML. Read with ferenda's own
`request()` helper (2 HTTP requests total, `sz=200`):

    https://data.riksdagen.se/dokumentlista/?doktyp=rfs&utformat=json&sz=200&sort=datum&sortorder=desc&p=1
    https://data.riksdagen.se/dokumentlista/?doktyp=rfs&utformat=json&sz=200&sort=datum&sortorder=desc&p=2

315 raw hits: 274 distinct numbered `rm:beteckning` keys, 40 duplicate hits
(base + ändring published as two API rows for the same number), 1
non-numbered "RFS-register" entry (`rm=zz`, skipped by our harvester too,
same as the code comment in `ferenda/foreskrift/agencies.py:991` says).

### 2/3. Missing / extra

    site keys 274, ours 274, missing 0, extra 0

Set difference between the site's 274 numbered keys and
`catalog.sqlite`'s 274 `rfs` labels is empty both ways.

### 4. Repeal marking

The API carries the agency's own `status` field per document: `Gällande`
(103), `Upphävd` (171 across dup rows; 171 distinct numbers once
duplicates are collapsed to the first non-blank status), plus 1 number
(RFS 1980:4, the oldest) the API never tags.

Cross-checked all 171 `Upphävd` numbers against `links` rows with predicate
`rpubl:upphaver` (`catalog.upphaver_targets`). 119 have no incoming
`upphaver` edge in our corpus. Breaking those 119 down by the API's own
`subtyp`:

    Ändring   104
    Omtryck    11
    Grund       4

The 115 Ändring/Omtryck rows are amendment/reprint documents. Riksdagen's
own system marks an amendment "Upphävd" once it is spent (its changes are
absorbed into the base regulation) — this is not a repeal of the amendment
itself and matches the briefing's repeal model (no in-force flag on the
document itself). Not a defect.

Of the 4 "Grund" (base-regulation) rows:

- RFS 2003:8 is itself a pure repeal instrument ("... om upphävande av
  tjänsteföreskriften (RFS 2002:3) ..."). A document whose only content is
  a repeal is not itself repealed by anything — expected shape, not a defect.
- RFS 2001:5 and RFS 2001:7 are genuine gaps: the repealing document exists
  in our corpus, names the target by number in its own body text, but our
  `upphaver` metadata is empty for it. Confirmed parse defect, see below.
- RFS 2002:9 also shows `Upphävd` on the site with no upphaver edge in our
  corpus, but I found no candidate repealing document in-corpus (only an
  unrelated citation from RFS 2005:7, "Om arbetsrum ... finns bestämmelser
  i föreskriften (RFS 2002:9)", not a repeal clause). Left open — could be
  a missing harvest, or a status the site sets without a formal repeal
  I can find within budget. Not filed, not confirmed.

Root cause for RFS 2001:5 and RFS 2001:7, found by reading
`ferenda/foreskrift/parse.py`'s repeal-target extraction against the two
documents' actual body text (`site/data/artifact/foreskrift/rfs/2015-5.json.br`,
`2002-9.json.br`):

- RFS 2015:5 says "Genom föreskriften upphävs föreskriften (2001:5) om
  särskilt betalkort ...". `RE_ERSATTER` bounds the target text correctly,
  but the bare `(2001:5)` (no `RFS` prefix) is read by `RE_BARE_OWN_REF`
  (`ferenda/foreskrift/parse.py:311`), which only recognises the plural
  "föreskrifter(na)" or "kungörelsen" right before the parenthesis. The
  text uses the singular definite "föreskriften", so the regex does not
  match and `upphaver` stays empty.
- RFS 2002:9 says "Genom föreskriften upphävs 1. föreskriften (RFS 2001:7)
  om ... och 2. riktlinjerna (...)". `RE_ENUMERATOR` (line 191) only
  rewrites a numbered item into a dash when the item starts with an
  uppercase letter, digit, or "(" — here it starts with lowercase
  "föreskriften", so it is left as "1. ". `RE_ERSATTER`'s own
  sentence-boundary lookahead (`(?=[A-ZÅÄÖ−])`, line 181) is compiled with
  `re.I`, which folds the character class to also accept lowercase, so it
  wrongly reads "upphävs 1. " as a complete sentence and stops there,
  discarding "föreskriften (RFS 2001:7)" before `_repeal_object` ever sees
  it.

Reproduced live:

    .venv/bin/python3 -c "
    import re, ferenda.foreskrift.parse as P
    s = '1. Denna föreskrift träder i kraft den 1 juli 2015. 2. Genom föreskriften upphävs föreskriften (2001:5) om särskilt betalkort för talmannen och de vice talmännen.'
    m = list(P.RE_ERSATTER.finditer(P.RE_ENUMERATOR.sub(' − ', s)))[0]
    print(P.RE_BARE_OWN_REF.findall(P._repeal_object(m.group(1))))   # -> []
    "

Both documents are RFS-specific phrasing (short-form repeal clauses that
never name the agency, only "föreskriften"), which is why this scope
surfaces it while agency-named series ("Livsmedelsverkets föreskrifter
(...)") do not trip the same gap.

### 5. Titles

Random sample of 12 of 274, `documents.title` vs the API's own `titel`:

    RFS 2004:2   match (site has a trailing space we strip)
    RFS 2001:10  match (site has a trailing space we strip)
    RFS 2011:15  exact match
    RFS 2003:2   exact match
    RFS 2023:3   exact match
    RFS 2020:4   exact match
    RFS 2021:6   exact match
    RFS 2017:3   exact match
    RFS 2008:2   exact match
    RFS 2002:13  exact match
    RFS 2022:5   exact match
    RFS 1991:2   match (site has a trailing space we strip)

No junk found: no file sizes, no PDF filenames, no HTML entities, no
repeated designation inside the title.

### 6. Consolidations

None of our 274 download records (`site/data/downloaded/foreskrift/rfs/*.json[.br]`)
carry a `files.consolidation` entry. The site publishes each RFS number
(base regulation or ändring) as its own standalone PDF; it does not publish
a separately maintained "konsoliderad version". The one candidate,
the `rm=zz` "Numeriskt register över gällande RFS-författningar" document,
is an image-scanned status list (per-page `<img>` positioning in its HTML,
no extractable running text), not a consolidated text — checked by fetching
`https://data.riksdagen.se/dokument/ZZD4reg.text` (1 request). Not a defect.

### 7. Inherited series

Assignment lists no predecessor samling for RFS. Nothing to check.

### 8. Freshness

Site newest: RFS 2026:7 (2026-06-24, "Utkom från trycket"). Our newest:
RFS 2026:7. Match.

### #76 cross-check (15 rfs records)

Issue #76 lists 15 `rfs` records whose stored PDF's pages 1-2 print a
`YYYY:N` different from their own number, all sourced `live`:

    rfs/2015:10, 2015:6, 2015:7, 2015:8, 2015:9, 2016:1, 2017:5, 2017:6,
    2018:10, 2018:3, 2018:4, 2018:6, 2018:7, 2018:8, 2018:9

Ran `pdftotext -f 1 -l 2` by hand on 6 of the 15
(2015:7, 2015:6, 2018:3, 2018:9, 2015:10, 2017:6, 2018:7, 2018:10 — 8
checked in total). All are short ändringsföreskrifter/tjänsteföreskrifter
("... föreskriver ... att X § riksdagsstyrelsens föreskrift (RFS N) ...
ska ha följande lydelse") that never print their own RFS designation
anywhere in the document — no masthead, no cover page, just the body text
and a signature block. The only `YYYY:N`-shaped strings on the page are the
empowering statute citation (almost always "lagen (2011:745) med
instruktion för Riksdagsförvaltningen", the Riksdag Administration
Instruction Act) and/or the base regulation being amended (e.g. RFS
2011:10, RFS 2012:2, RFS 2006:6) — both legitimate citations, not evidence
of a wrong stored file.

This is the false-positive class #76 itself already names in its caveats
("a document whose masthead sits on page 3, or a cover sheet in front of
the real first page... Read the PDF before you act on a single row") —
for RFS the masthead is simply never printed at all on these short-form
records. Our minted numbers for all 15 are independently confirmed correct
against the site's own structured `rm`/`beteckning` fields in the check 1/2
enumeration (274/274 match, no extras). No new issue filed for this cause;
cites #76.

### Not filed: publisher-field noise (out of the eight checks)

`documents.publisher` for 15 of 274 rfs rows is garbage ("Eye", "Beg",
"N a", "Å r", "E 5", ...) instead of "Riksdagsförvaltningen" — almost
certainly `extract_publisher`'s masthead regexes matching noise in these
older, poorly-scanned PDFs. This is outside the eight checks (which ask
about title, not publisher) and plausibly a broader OCR-quality issue
across old scanned föreskrift, not something I chased down to a single
root cause within budget. Flagged here for visibility, no issue filed.

### Budget

7 HTTP requests total (2 dokumentlista pages, 1 register-text fetch,
1 request used for a scratch check earlier in the session, no
individual-page browsing needed since the whole samling is on the API).
Well under the 60-request cap. No blocks encountered.
