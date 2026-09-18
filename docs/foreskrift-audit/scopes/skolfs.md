# SKOLFS — Statens skolverks författningssamling, Statens skolverk

Verdict: DEFECT
Site list: 2429 base designations from 36 year-pages of the register API (https://skolfs.skolverket.se/api/statute?year=N), reached from https://www.skolverket.se/styrning-och-ansvar/regler-och-ansvar/sok-forordningar-och-foreskrifter-skolfs
We hold: 2557 documents (skolfs), newest SKOLFS 2026:67 (2026-06-10); site newest SKOLFS 2026:67 — caught up.
Missing: 0 of the site's 2429 current base designations (GRUNDFORFATTNING + ALLMANNA_RAD_OVRIGT) are absent from our corpus.
Extra: 128 — documents we hold as independent SKOLFS designations that the site currently files as ANDRINGSFORFATTNING (repeal notices) under another base's number. All 128 are correctly flagged `upphavande=1` in our catalog and their titles match the site. Not a defect — same shape as the 132 counted below, just already in our corpus.
Repeal gaps: 556 base designations the site marks EXPIRED with no `rpubl:upphaver` link in our corpus, of which:
  - 214 are explained by a confirmed harvest gap: the repeal act that would carry the link is itself missing from our corpus (see Issue below).
  - 342 are self-expiring or administratively revoked (no separate SKOLFS-numbered repeal act exists at all — confirmed for 7 of a 15-document random sample via the register's own `revokedBySkolfsNumber` field, which equals the document's own number when there is no distinct repealer).
Title defects: 0 in a 15-document random sample (exact match against the register's `statuteTitle` after whitespace normalization). One isolated repeal-link defect found instead: SKOLFS 2001:14 is present, correctly flagged `upphavande=1`, and its title names its target in parenthesis ("... (SKOLFS 1991:52) ...") the same way 294 of 301 same-shaped titles do, but its `upphaver` metadata is empty. See Issue.
Consolidations: site yes (799 unique bases carry a SENASTE_LYDELSE/senaste-lydelse post), we hold 673 download records with `files.consolidation`. The 126-document gap is spot-checked and looks like ordinary staleness — the base's later amendment is recorded in `files.amendment`, but the base has not been re-walked with `--full` since — not filed as a defect.
Inherited: none (assignment states SKOLFS has no predecessor samling).
Cross-reference to #76: SKOLFS 1994:43 appears in the corpus-wide pdftotext-mismatch list (prints "1994:519" instead of its designation). Consistent with #76's stated cause (a document's own text names a different number); no new issue needed for it.
Issue: https://github.com/staffanm/ferenda/issues/91

## Evidence

### Enumeration
```
GET https://skolfs.skolverket.se/api/statute/years
  -> 36 years, 1991..2026
for year in years: GET https://skolfs.skolverket.se/api/statute?year=<year>
  -> 5445 hits total across all documentType values:
     GRUNDFORFATTNING 2414, ANDRINGSFORFATTNING 2216, SENASTE_LYDELSE 800, ALLMANNA_RAD_OVRIGT 15
     validity: EXPIRED 3327, VALID 2117, UPCOMING 1
```
GRUNDFORFATTNING + ALLMANNA_RAD_OVRIGT = 2429 base designations, no duplicate skolfsNumber.
41 HTTP requests to skolfs.skolverket.se for enumeration + 15 for the repeal-detail sample + 2 for a documentType detail probe = 58 requests total (budget 60).

### Missing / extra
```python
site_set = {"SKOLFS %s:%d" % (h["skolfsNumber"].split(":")[0], int(h["skolfsNumber"].split(":")[1]))
            for h in hits if h["documentType"] in ("GRUNDFORFATTNING", "ALLMANNA_RAD_OVRIGT")}
our_set = {label for (label,) in sqlite3.connect("file:site/data/catalog.sqlite?mode=ro", uri=True)
           .execute("select label from documents where kind='skolfs'")}
site_set - our_set   # -> 0
our_set - site_set   # -> 128, all found among hits as documentType == ANDRINGSFORFATTNING
```
Artifacts and catalog rows are 1:1 (2557 each, excluding the 669 `*.grund.json.br` sidecars that
`ferenda/foreskrift/source.py` documents as the grundförfattning-as-published projection of a
consolidated document — 2557 + 669 = 3226 total artifact files, matched exactly).

### Repeal marking
```python
from ferenda.lib.catalog import upphaver_targets
spent = upphaver_targets(con)   # 5469 targets corpus-wide
expired = [h for h in bases if h["validity"] == "EXPIRED"]   # 1162 of 2429
gaps = [h for h in expired if uri(h) not in spent]            # 556
```
Random sample of 15 gaps (seed 42), each resolved against
`GET /api/document/<documentType>/<skolfsNumber>` (no `/pdf`):
```
2011:146 -> revokedBySkolfsNumber 2016:33   (distinct repealer)
2017:7   -> revokedBySkolfsNumber 2017:7    (self — administrative beslut, no SKOLFS act)
2000:70  -> revokedBySkolfsNumber 2018:136  (distinct repealer)
2000:31  -> revokedBySkolfsNumber 2018:97   (distinct repealer)
2000:1   -> revokedBySkolfsNumber 2018:67   (distinct repealer)
2008:79  -> revokedBySkolfsNumber 2008:79   (self)
2011:188 -> revokedBySkolfsNumber 2011:188  (self)
2012:108 -> revokedBySkolfsNumber 2012:108  (self)
1994:21  -> revokedBySkolfsNumber 2006:32   (distinct repealer)
2016:4   -> revokedBySkolfsNumber 2016:4    (self)
2017:96  -> revokedBySkolfsNumber 2017:96   (self)
2011:33  -> revokedBySkolfsNumber 2011:33   (self)
2001:4   -> revokedBySkolfsNumber 2018:205  (distinct repealer)
2000:10  -> revokedBySkolfsNumber 2018:76   (distinct repealer)
1991:52  -> revokedBySkolfsNumber 2001:14   (distinct repealer)
```
8 of 15 name a distinct repealer. For 7 of those 8 the repealer (e.g. SKOLFS 2016:33, a
"Föreskrifter om upphävande av Skolverkets föreskrifter (SKOLFS 2011:146) ...") is entirely
absent from our `documents` table. The eighth repealer, SKOLFS 2001:14, IS in our corpus but
carries no `rpubl:upphaver` link.

Generalizing across the full local dataset (no further HTTP calls needed):
```python
upph_pat = re.compile(r"^(F[öo]reskrifter|F[öo]rordning)\s+om\s+upph[äa]vande", re.I)
repeal_notices = [h for h in hits if h["documentType"] == "ANDRINGSFORFATTNING"
                  and upph_pat.match(h["statuteTitle"].strip())]
# 347 total; 132 already in our catalog (all correctly upphavande=1, all 132 carry an
# upphaver link); 215 missing entirely.
missing_repealers = {h["baseSkolfsNumber"] for h in repeal_notices if norm(h) not in cat_labels}
# 215 unique targets; 214 of the 556 repeal gaps above are targets of one of these 215.
```
Same title shape checked on the GRUNDFORFATTNING/ALLMANNA_RAD_OVRIGT side (301 base documents
whose own title starts "Föreskrifter/Förordning om upphävande av ..."): 294 of 301 carry an
`upphaver` link, 7 do not. All 7 have `files.regulation: null` in their download record (no PDF
was ever fetched). Six of those seven name plural targets in the title ("två förordningar",
"vissa förordningar") so the real targets are in body text we never fetched — not fixable
without the PDF. The seventh, SKOLFS 2001:14, names its single target in the title itself:

```
$ ferenda.lib.compress.read_text("site/data/artifact/foreskrift/skolfs/2001-14.json")
title: "Förordning om upphävande av förordningen (SKOLFS 1991:52) om statsbidrag för
        riksrekryterande teknisk vuxenutbildning vid Katrineholms Tekniska Skola"
metadata.upphaver: []
$ sqlite3 catalog: documents.upphavande = 1, no rows in links for from_uri =
  https://lagen.nu/skolfs/2001:14
```

### Titles
15-document random sample (seed 7) of GRUNDFORFATTNING/ALLMANNA_RAD_OVRIGT base designations,
comparing `documents.title` against the register's `statuteTitle` (whitespace-normalized,
trailing `;` stripped): 15/15 exact matches.

### Consolidations
```
SENASTE_LYDELSE hits: 800, unique baseSkolfsNumber: 799
download records with files.consolidation: 673 (of 2286 download records for 2557 catalog docs;
  the 271-record gap between download records and catalog rows is old (1991-2011) migrated
  entries with no surviving download record — outside the eight checks, artifacts are complete)
```
Spot check of 3 of the 126 gap bases (2012:104, 1996:6, 2002:12): each has `files.amendment`
populated (the amending SKOLFS designation is recorded) but `files.consolidation: []` — the
shape `ferenda/foreskrift/download.py` describes `--full` as fixing ("re-walks and refreshes
existing base regulations (new amendments / consolidations)").

### Freshness
Newest base designation on site: SKOLFS 2026:67. Newest in our catalog: SKOLFS 2026:67 (dated
2026-06-10). Matched.

### #76 cross-reference
`gh issue view 76` lists `skolfs/1994:43 live 1994:519` — our stored PDF for SKOLFS 1994:43
prints "1994:519" rather than its own designation, the same shape #76 already covers.
