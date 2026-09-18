# STAFS — Styrelsen för ackreditering och teknisk kontroll (Swedac)

Verdict: DEFECT
Site list: 75 designations from 1 page (https://regelverk.swedac.se/foreskrifter/), plus a separate archive page with 208 more (see Missing)
We hold: 75 documents (stafs), newest STAFS 2026:4; site newest STAFS 2026:4
Missing: 206 — the site's "upphävda föreskrifter" archive holds 208 repealed
  designations; 2 also sit on the in-force page and are already in our corpus
  (STAFS 2018:7, STAFS 2009:15). Examples: STAFS 1992:7, STAFS 1994:26,
  STAFS 2006:4, STAFS 2011:5, STAFS 2016:8, STAFS 2019:4, STAFS 2019:5,
  STAFS 2019:6, STAFS 2019:7, STAFS 2024:10
Extra: 0 — the in-force page's 75 designations match our 75 exactly.
Repeal gaps: 2 — STAFS 2016:1 and STAFS 2022:9 (both konsoliderad texts) list
  their own later amending act (STAFS 2022:6, STAFS 2024:9) in `upphaver`.
  Both targets are still in force. This is a parse defect, not a harvest
  defect: the direction is backwards.
Title defects: 41 of 75 — two shapes. 24 documents carry the stray word
  "Publicerad" (and, twice, a garbled "xx xx 20xx" placeholder) spliced into
  the title. 17 documents (all konsoliderad texts) hold no title at all —
  `documents.title` equals the bare label ("STAFS 2016:1"). Examples below.
Consolidations: site yes (marks 14 designations "(konsoliderad)"), we hold 0
  in `files.consolidation` — not a defect: for every one of those 14, the
  "plain" and the "(konsoliderad)" landing page hang the identical PDF
  (verified for STAFS 2022:14 and STAFS 2020:1). Swedac keeps no separate
  original once a document is amended; there is nothing distinct to store as
  a consolidation.
Inherited: none (assignment lists no predecessor series).
Issue: https://github.com/staffanm/ferenda/issues/94

## Evidence

### 1. Enumeration
Fetched the entry page with `ferenda.lib.net.request` (politeness-compliant):

    from ferenda.lib.net import make_session, request, BROWSER_UA
    session = make_session(BROWSER_UA)
    r = request(session, "GET", "https://regelverk.swedac.se/foreskrifter/")

`BeautifulSoup(...).select('a[href*="/foreskrifter/swedac/stafs-"]')` returns
78 anchors: 75 unique designations, one "Rättelse STAFS 2025:2" correction
notice (not a document), and two designations (STAFS 2022:14, STAFS 2020:1)
each printed twice — once as the plain entry, once "(konsoliderad)" — both
pointing at the same PDF. No pagination markup on the page.

### 2/3. Missing / extra
Catalog query:

    sqlite3 site/data/catalog.sqlite (via python)
    select label from documents where kind='stafs'   -> 75 rows

`ours - theirs` and `theirs - ours` (in-force page only) are both empty sets.
Freshness matches: newest on the in-force page and in our corpus is
STAFS 2026:4.

The site links a second page from the entry page:

    <a href="/foreskrifter/swedac/upphavda/">Visa upphävda föreskrifter</a>

Fetched it the same way (`request(session, "GET",
"https://regelverk.swedac.se/foreskrifter/swedac/upphavda/")`, one request).
Its anchors match `a[href*="/upphavda/stafs-"]` — a different href shape
than the in-force page's `a[href*="/foreskrifter/swedac/stafs-"]`, so the
harvest's `link_select` (`ferenda/foreskrift/agencies.py:1906`,
`'a[href*="/foreskrifter/swedac/stafs-"]'`) never matches it, and
`STAFS.index_url` is the single in-force URL — no `index_urls` list reaches
the archive. 208 designations sit there, none paginated (no page-number or
"nästa" anchors found). Two also appear on the in-force page and are
already in our corpus (STAFS 2018:7, STAFS 2009:15 — the site itself lists
each of those on both pages, with identical titles; a site inconsistency,
not ours). The other 206 are entirely absent from our corpus. This is the
same shape as AFS #45 and EIFS #51: an "upphävda" archive the harvest's
index URL list does not visit.

### 4. Repeal marking
All `rpubl:upphaver` rows for stafs:

    select from_uri, to_uri from links
    where predicate='rpubl:upphaver' and from_uri like '%stafs%'

44 rows. Comparing (year, lopnummer) of `from_uri` against `to_uri` finds
exactly two where the "repealed" document is chronologically *later* than
the "repealing" one:

    stafs/2016:1  -> stafs/2022:6
    stafs/2022:9  -> stafs/2024:9

Both STAFS 2016:1 and STAFS 2022:9 are konsoliderad PDFs whose own masthead
reads (confirmed with `pdftotext -layout`):

    STAFS 2016:1: "Ändring införd: t.o.m. STAFS 2022:6"
    STAFS 2022:9: "Ändring införd: t.o.m. STAFS 2024:9"

That sentence states which amendment the consolidated text folds in — it is
not a repeal. STAFS 2022:6 and STAFS 2024:9 are themselves current,
unrepealed ändringsförfattningar (`stafs/2022:6` artifact has
`"andrar": ["https://lagen.nu/stafs/2016:1"], "upphaver": []`, the correct
direction). `ferenda/foreskrift/parse.py`'s `upphaver` extraction
(`extract_metadata`, ~line 776) runs over the whole document body text, not
just the ingress; one of its repeal-clause regexes (`RE_UPPHOR_LIST` /
`RE_UPPHAVS_LIST`, DOTALL, up to 6000 chars) is the likely culprit — it can
span from a genuine repeal clause (STAFS 2016:1 does legitimately repeal
STAFS 2006:4) across page boundaries into the appended transitional text of
the *later* amending act, picking up its designation as a spurious repeal
target. `ferenda/foreskrift/parse.py` already has a dedicated
`parse_consolidation`/`konsoliderad_tom`/`masthead_amendments` path built for
exactly this "Ändring införd t.o.m." sentence, but these two STAFS records
are not routed through it (both show `andradAv: []`, `files.consolidation:
[]` — they go through the plain regulation path instead).

### 5. Titles
Catalog query for kind='stafs', comparing `title` to `label` and scanning
for "Publicerad" / "xx" / "20xx":

    Publicerad/placeholder leakage (24): STAFS 2020:2, 2022:1, 2022:2,
      2022:4, 2022:5, 2022:6, 2022:15, 2023:5, 2024:1..2024:9 (9 of them),
      2025:1, 2025:3..2025:7 (5), 2026:1, 2026:2
    No title at all, title==label (17): STAFS 1993:16, 2007:1, 2007:19,
      2007:2, 2007:3, 2008:4, 2008:8, 2009:26, 2013:11, 2014:2, 2014:4,
      2014:5, 2016:1, 2016:12, 2020:1, 2022:8, 2022:9

Example (`pdftotext -layout` page 1 of `stafs-2022-1-regulation.pdf`):

    Styrelsen för ackreditering och teknisk kontrolls föreskrifter    Publicerad
    om taxametrar                                                    den 14 juni 2022

The right-hand "Publicerad / den DD month YYYY" stamp is a sidebar next to
the title, introduced from 2020 onward. Our stored title is "Styrelsen för
ackreditering och teknisk kontrolls föreskrifter Publicerad om taxametrar"
— the site's own page prints a clean "Styrelsen för ackreditering och
teknisk kontrolls föreskrifter om taxametrar" (fetched
stafs-2022-1.html, h1). `ferenda/foreskrift/parse.py`'s
`RE_MASTHEAD_BOILERPLATE` only removes "Publicerad(e)" when it is
immediately followed by "den" (`Publicerade?\s+den`); the extracted line
order here puts the title's own left-column continuation ("om taxametrar")
between them, so "Publicerad" alone survives while the date is separately
stripped by the generic date pattern. For STAFS 2022:6 and STAFS 2024:3 the
same stamp is itself malformed in the source PDF (a literal "xx xx 20xx"
placeholder next to the real date), so what leaks into the title is
"xx xx 20xx" instead of "Publicerad".

For the second shape, the 17 konsoliderad PDFs open "Konsoliderad version
av …" (confirmed for STAFS 1993:16, STAFS 2013:11) with no masthead
furniture at all (no "författningssamling"/ISSN/Utgivare lines), a shape
`title_from_masthead` does not recognise, and 6 of the 17
(STAFS 2007:1/3/19, 2014:2/4/5) additionally extract as visibly garbled text
under `pdftotext` (a font-encoding problem, independent of the konsoliderad
shape). Both paths fall through to the bare identifier as title.

### 6. Consolidations
    grep files.consolidation across site/data/downloaded/foreskrift/stafs/*.json.br
    -> 0 of 75 records carry any consolidation entry

Checked whether that is right by fetching the plain and "(konsoliderad)"
landing pages for the two designations the in-force page lists twice:

    stafs-2022-14.html        pdf: stafs-2022-14-konsol.pdf
    stafs-2022-14-konsol.html pdf: stafs-2022-14-konsol.pdf   (same file)
    stafs-1993-14-konsol.html pdf: stafs-1993-14-konsol.pdf   (only entry)

Not a defect: there is no second, distinct PDF to hold as a consolidation.

### 7. Inherited series
None assigned to stafs.

### 8. Freshness
Site newest: STAFS 2026:4 (in-force page, first anchor). Ours: STAFS 2026:4
(catalog max label). Match.

### Issue #76 cross-check
The briefing's note said #76 lists 2 stafs documents. `gh issue view 76`'s
body and its 15 comments contain no `stafs` line at all — none of the 108
listed rows are stafs. Re-running #76's own check locally against all 75
stafs regulation PDFs (`pdftotext -f 1 -l 2`, then `pdftotext -layout`)
found no PDF printing a *different* number than we minted. Six PDFs
(STAFS 2007:1, 2007:3, 2007:19, 2014:2, 2014:4, 2014:5) print no number at
all on pages 1-2 under `pdftotext`, matching #76's separate "50 print no
number" bucket, not its "108 print a different number" bucket. The
briefing's premise does not hold for this scope; no action taken on it
beyond this note.
