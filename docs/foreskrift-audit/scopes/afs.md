# AFS — Arbetsmiljöverket

Verdict: DEFECT
Site list: 86 designations from 2 pages (entry page: 15 in-force grundföreskrifter;
`upphavda-foreskrifter/` archive: 71 repealed grundföreskrifter) —
https://www.av.se/arbetsmiljoarbete-och-inspektioner/publikationer/foreskrifter/
We hold: 130 documents (afs), newest by designation AFS 2023:16 (dated 2023-06-19);
site newest AFS 2025:1 (an amendment to AFS 2023:10, listed on its
författningshistorik subpage, not a new base regulation)
Missing: 49 — 47 of the 71 site-listed repealed regulations, plus 2 recent
amendments (AFS 2024:3, AFS 2025:1). Examples: AFS 2001:1, AFS 2008:3, AFS 1996:7,
AFS 1999:4, AFS 1993:41, AFS 1995:5, AFS 2016:3, AFS 2018:1, AFS 2020:9, AFS 2024:3
Extra: 0 — every non-site designation we hold (ändringsföreskrifter, the 2005-2018
standalone upphävandeförfattningar) is a legitimate historical record the site
folds into a konsoliderad version or the upphävda archive; found no orphan.
Repeal gaps: 15 — every AFS 2023:1..15 grundföreskrift (the 2025-01-01 wholesale
replacement) has `metadata.upphaver == []`. At least 12 of them state an explicit
textual repeal ("Genom denna författning upphävs ... (AFS NNNN:N)") that the
parser detects as a `dcterms:references` link but never promotes to
`rpubl:upphaver`. Examples: AFS 2023:1 -> AFS 2001:1, AFS 2023:4 -> AFS 2008:3,
AFS 2023:5 -> AFS 2016:1 and AFS 1999:4, AFS 2023:6 -> AFS 2016:2 and AFS 1993:41,
AFS 2023:7 -> AFS 2016:4 and AFS 1995:5, AFS 2023:8 -> AFS 2012:5.
Title defects: 18+ confirmed junk titles (of 130). Examples:
  - AFS 1982:3: title is a body paragraph ("Föreskrifterna begränsas i
    anslutning härtill...") not a title.
  - AFS 1985:18: title names the WRONG regulation, AFS 1985:17 ("farliga
    ämnen"), while the document itself is "Frisörarbete" (hairdressing).
  - AFS 2012:1, AFS 2012:5: title is the standard PDF-cover disclaimer
    ("publiceras myndighetens föreskrifter och allmänna råd...").
  - AFS 2014:10, 2014:17, 2014:19, 2014:2, 2014:20, 2014:26, 2014:28, 2014:29:
    words doubled ("föreskrifter föreskrifter om om ändring ändring ii").
  - AFS 2010:2: truncated fragment, "föreskrifter Förordning om upphävande av
    vissa föreskrifter", no designation.
  - AFS 2021:2: garbled word order, "AFS" injected mid-sentence.
  - AFS 2022:4: its own designation repeated inside the title, plus a leaked
    "Utkom från trycket den 22 november 2022" clause.
Consolidations: site yes, we hold 5 downloaded (afs-2023-{10,11,13,14,15}
-consolidation-*.pdf); the other 10 base regulations have not been amended yet
so have no konsoliderad PDF on the site either.
Inherited: none (the assignment lists no predecessor samlingar for afs).
Issue: https://github.com/staffanm/ferenda/issues/45

## Evidence

### Enumeration
- Entry page fetched once:
  `https://www.av.se/arbetsmiljoarbete-och-inspektioner/publikationer/foreskrifter/`
  -- 15 links matching `/foreskrifter/afs-(\d{4})(\d+)/$`, AFS 2023:1..2023:15.
  No pagination, no mention of "upphävd"/"upphört" on this page: it lists only
  the in-force register, matching the `afs_enumerate` code comment.
- Followed the "Upphävda föreskrifter" link from a författningshistorik
  subpage to `.../foreskrifter/upphavda-foreskrifter/` -- one page, 71 distinct
  `AFS YYYY:N` designations, each with its own PDF landing page (no pagination).

### Corpus counts
    sqlite3 "file:site/data/catalog.sqlite?mode=ro" \
      "select count(*) from documents where kind='afs'"   # 130

Cross-checked the 71 site-listed repealed designations against
`documents.label`: 24 present, 47 absent. Full missing list (47):
AFS 1982:17, 1992:9, 1993:41, 1995:5, 1996:7, 1997:5, 1997:7, 1998:5, 1998:6,
1999:3, 1999:4, 1999:8, 2000:6, 2001:1, 2001:3, 2003:3, 2003:6, 2004:1, 2004:3,
2004:6, 2005:1, 2005:5, 2005:15, 2006:1, 2006:4, 2006:5, 2006:6, 2006:7, 2006:8,
2007:1, 2007:5, 2007:7, 2008:3, 2009:7, 2010:1, 2010:16, 2011:2, 2011:19,
2012:2, 2012:3, 2015:2, 2016:3, 2017:3, 2018:1, 2018:4, 2019:3, 2020:9.

### Repeal-marking parse defect (root cause traced)
The artifact for AFS 2023:1 carries the exact repeal sentence with a
correctly-linked citation, yet `metadata.upphaver` is empty:

    .venv/bin/python -c "
    from ferenda.lib.compress import read_text
    import json
    d = json.loads(read_text('site/data/artifact/foreskrift/afs/2023-1.json'))
    print(d['metadata']['upphaver'])   # []
    "
    # structure text (page 16, Övergångsbestämmelser) contains:
    # '... Genom denna författning upphävs Arbetsmiljöverkets föreskrifter (' +
    # {"predicate":"dcterms:references","text":"AFS 2001:1", ...} + ') om
    # systematiskt arbetsmiljöarbete ...'

Re-ran the real parser against the downloaded PDF directly:

    .venv/bin/python -c "
    from ferenda.foreskrift.parse import parse_pdf
    class P:
        def parse_text(self, t, context=None): return []
    s, meta, fn = parse_pdf(
        'site/data/downloaded/foreskrift/afs/afs-2023-1-regulation.pdf',
        'AFS 2023:1', P(), fs='afs')
    print(meta['upphaver'])   # []
    "

Traced the cause to `RE_FORTECKNING` (ferenda/foreskrift/parse.py:275),
checked against `text[:1500]` at line 764. Every AFS PDF cover page carries
the routine boilerplate sentence: "... hänvisas till senaste Förteckning
över föreskrifter och allmänna råd." -- a cross-reference to AV's own
regularly-published list of regulations, not a declaration that this document
IS such a list. The regex's `f[öo]\s?rteckning(?:en|ar)?\s+över\s+...` half
matches this boilerplate ("Förteckning över föreskrifter"), so
`extract_metadata` returns at line 765 before ever running the upphaver/andrar
extraction (lines 766-786, 787-798) for every AFS grundföreskrift whose cover
page includes that sentence. `bemyndigande`/`genomfor` are unaffected (computed
earlier in the function, lines 738-752).

Confirmed for all 15 AFS 2023:1..15:

    .venv/bin/python -c "
    import glob, json
    from ferenda.lib.compress import read_text
    for p in sorted(glob.glob('site/data/artifact/foreskrift/afs/2023-*.json.br')):
        if p.endswith('.grund.json.br'): continue
        d = json.loads(read_text(p[:-3]))
        print(d['identifier'], d['metadata']['upphaver'])
    "
    # every line prints []

Confirmed the false positive is masthead-specific, not present in older
ändringsföreskrifter (whose `rpubl:andrar`/`rpubl:upphaver` links work fine,
e.g. AFS 2023:16 -> AFS 2019:3, AFS 2016:5 -> AFS 1994:36/1999:6/2016:1).

### Freshness
The författningshistorik subpage for AFS 2023:10 already lists AFS 2024:3 and
AFS 2025:1 as amendments. Our own download record for this base
(`site/data/downloaded/foreskrift/afs/afs-2023-10.json.br`, fetched 2026-07-15)
already recorded their URLs under `files.amendment`, but no PDF was ever
fetched and no catalog row exists for either designation -- this may be
ordinary re-crawl lag rather than a code defect; reported for completeness,
not filed as a bug.

### Title defects
    sqlite3 "file:site/data/catalog.sqlite?mode=ro" \
      "select label, title from documents where kind='afs'"
Compared against the site's own titles on the "Upphävda föreskrifter" page and
the in-force register; see the examples list above. A simple heuristic scan
(duplicated adjacent word, or a title over 200 characters, or known boilerplate
phrases) flags 30 of 130 rows; manual review narrowed this to at least 18
confirmed junk titles (the rest are just verbose-but-accurate full masthead
titles, not defects).
