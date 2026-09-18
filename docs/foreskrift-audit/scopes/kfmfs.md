# KFMFS — Kronofogdemyndighetens föreskrifter, Kronofogdemyndigheten

Verdict: DEFECT
Site list: 7 KFMFS designations from 1 page (https://www.kronofogden.se/om-kronofogden/dina-rattigheter-lagar-och-regler/foreskrifter-allmanna-rad-och-meddelanden), plus 2 non-KFMFS rows (KFM A 2025:1, KFM M 2025:1) correctly dropped by the harvester.
We hold: 17 documents (kfmfs), newest KFMFS 2026:2; site newest KFMFS 2026:2 (freshness OK).
Missing: 4 (confirmed 2, inferred 2) — KFMFS 2021:1, 2022:1, 2023:1, 2024:1. 2022:1 and 2023:1 are confirmed via a 2023-12-01 Wayback Machine snapshot; 2021:1 and 2024:1 are the two years the unbroken annual series skips and were never fetched.
Extra: 10 — KFMFS 2011:1 (repeal-only, upphavande=1, correctly targets rsfs/1991:38), 2012:1, 2013:1, 2014:1, 2015:1, 2016:1, 2017:2, 2018:1, 2019:1, 2020:1. All are spent annual "förbehållsbelopp" regulations the live page drops once superseded, not repealed documents. Legitimate, not a defect.
Repeal gaps: 0 — the one upphävande document we hold (KFMFS 2011:1) carries a correct `rpubl:upphaver` link to `rsfs/1991:38`. (We do not hold rsfs/1991:38 itself — that is an rsfs-scope gap, out of scope here.)
Title defects: 16 of 17 — two distinct patterns:
  - 7 documents keep the trailing "DESIGNATION |" left over after the harvest chrome ("pdf | NN kB") is stripped: 2007:1, 2008:1, 2016:2, 2017:1, 2025:1, 2026:1, 2026:2.
  - 9 documents have a footnote-marker digit glued onto the target year: 2012:1 "under år 2013 1", 2014:1 "under år 20151", 2020:1 "under år 20211", and 6 more of the same shape.
  Only KFMFS 2011:1 has a clean title.
Consolidations: site no, we hold 0 (0/17 download records carry files.consolidation). Match, not a defect.
Inherited: none registered. The current page names no RSFS document; the only RSFS reference is inside KFMFS 2011:1's own title ("upphävande av Riksskatteverkets föreskrifter (RSFS 1991:38)"), which is a citation, not a listing. Nothing to relocate.
Issue: https://github.com/staffanm/ferenda/issues/64

## Evidence

### Corpus query
```
sqlite3 file:site/data/catalog.sqlite?mode=ro
select label, title, date, source_url, expired, upphavande from documents where kind='kfmfs' order by label;
```
17 rows, listed in the Title defects table below. 8 of 17 (2011:1, plus the 7 clean-year-but-not-clean-suffix rows... see table) — full list:

| label | our title | defect |
|---|---|---|
| KFMFS 2007:1 | "...fältpersonal KFMFS 2007:1 \|" | designation + trailing pipe |
| KFMFS 2008:1 | "...borgenärsuppgifter KFMFS 2008:1 \|" | designation + trailing pipe |
| KFMFS 2011:1 | "...om innehållet i föreläggande..." | clean |
| KFMFS 2012:1 | "...under år 2013 1" | footnote digit |
| KFMFS 2013:1 | "...under år 2014 1" | footnote digit |
| KFMFS 2014:1 | "...under år 20151" | footnote digit |
| KFMFS 2015:1 | "...under år 20161" | footnote digit |
| KFMFS 2016:1 | "...under år 20171" | footnote digit |
| KFMFS 2016:2 | "...kvittning KFMFS 2016:2 \|" | designation + trailing pipe |
| KFMFS 2017:1 | "...avräkning KFMFS 2017:1 \|" | designation + trailing pipe |
| KFMFS 2017:2 | "...under år 20181" | footnote digit |
| KFMFS 2018:1 | "...under år 20191" | footnote digit |
| KFMFS 2019:1 | "...under 20201" | footnote digit |
| KFMFS 2020:1 | "...under 20211" | footnote digit |
| KFMFS 2025:1 | "...under 2026 KFMFS 2025:1 \|" | designation + trailing pipe |
| KFMFS 2026:1 | "...konkurs KFMFS 2026:1 \|" | designation + trailing pipe |
| KFMFS 2026:2 | "...företagsrekonstruktion KFMFS 2026:2 \|" | designation + trailing pipe |

### Site fetch (entry page, 1 request)
```python
from ferenda.lib.net import make_session, request, BROWSER_UA
session = make_session(BROWSER_UA)
r = request(session, "GET", "https://www.kronofogden.se/om-kronofogden/dina-rattigheter-lagar-och-regler/foreskrifter-allmanna-rad-och-meddelanden")
```
`div.documentList a[href*="/download/"]` (the harvester's own selector) returns 9 rows: 7 carry a `KFMFS YYYY:N` designation (2007:1, 2008:1, 2016:2, 2017:1, 2025:1, 2026:1, 2026:2 — the exact set of "extra" documents that are NOT spent), 2 do not (`KFM A 2025:1`, `KFM M 2025:1` — allmänna råd / information, correctly out of scope for `kind='kfmfs'`). Each anchor text has the raw shape `"<title> KFMFS YYYY:N | pdf | NN kB"`.

The page has 3 `div.documentList` sections (Föreskrifter / Allmänna råd / Meddelanden), no pagination, no separate upphävda/archive section, no mention of RSFS as a listed document.

### Title-cleanup bug (designation + trailing pipe)
`ferenda/foreskrift/parse.py`, `RE_TITLE_CHROME` strips from the literal token `pdf` to the end of the string:
```python
RE_TITLE_CHROME = re.compile(r"\s*[,(]?\s*(?:pdf|\.pdf)\b.*$", re.IGNORECASE | re.DOTALL)
```
Given the raw anchor text `"...fältpersonal KFMFS 2007:1 | pdf | 142 kB"`, this leaves `"...fältpersonal KFMFS 2007:1 |"` — the delimiting `|` that used to separate the designation from the `pdf` token survives because the regex's leading `\s*[,(]?\s*` only swallows a comma or an open paren before `pdf`, never a pipe. `clean_title()` then strips the identifier only as a *prefix* match (`^%s` in `re.sub(r"^%s\s*[-–—:]*\s*" % re.escape(identifier), ...)`), so a designation sitting in the middle or at the end of the raw text is never removed.

Reproduced directly:
```python
from ferenda.foreskrift.parse import RE_TITLE_CHROME, clean_title
raw = ("Kronofogdemyndighetens föreskrifter om vid vilka tillfällen särskilt "
       "tjänstekort får användas av Kronofogdemyndighetens fältpersonal "
       "KFMFS 2007:1 | pdf | 142 kB")
RE_TITLE_CHROME.sub("", raw)
# 'Kronofogdemyndighetens föreskrifter om vid vilka tillfällen särskilt tjänstekort
#  får användas av Kronofogdemyndighetens fältpersonal KFMFS 2007:1 |'
clean_title(raw, "KFMFS 2007:1")
# same string -- the trailing "|" and the designation both survive
```

### Title-cleanup bug (footnote digit)
The download records (`site/data/downloaded/foreskrift/kfmfs/kfmfs-<year>-1.json.br`, field `title`) already carry the raw harvested text with a bare digit glued onto the target year, e.g. for `kfmfs/2014:1`:
```
'Kronofogdemyndighetens föreskrifter om bestämmande av förbehållsbeloppet vid utmätning av lön m.m. under år 20151'
```
This is the harvest reading the anchor's footnote-marker text (a superscript "1" in the site's own markup, referencing a note elsewhere on the page) as part of the title. `clean_title()` has no rule for a bare trailing digit, so it passes through unchanged into `documents.title`. Confirmed by direct call:
```python
clean_title(raw, "KFMFS 2014:1")  # == raw, unchanged
```

### Missing documents (Wayback Machine cross-check, not counted against the 60-request agency-site budget)
```
curl http://web.archive.org/web/20231201193951/https://kronofogden.se/om-kronofogden/dina-rattigheter-lagar-och-regler/foreskrifter-allmanna-rad-och-meddelanden
```
lists, among others:
```
Kronofogdemyndighetens föreskrifter om bestämmande av förbehållsbeloppet vid löneutmätning under 2024 KFMFS 2023:1  | pdf | 203 kB
Kronofogdemyndighetens föreskrifter om bestämmande av förbehållsbeloppet vid löneutmätning under 2023 KFMFS 2022:1  | pdf | 203 kB
```
Neither `KFMFS 2023:1` nor `KFMFS 2022:1` is in our catalog or under `site/data/downloaded/foreskrift/kfmfs/`. Our holdings jump straight from `KFMFS 2020:1` (issued 2020, "under 2021") to `KFMFS 2025:1` (issued 2025, "under 2026") — a run of one designation a year, unbroken from 2007 to 2026 apart from this gap, strongly implying `KFMFS 2021:1` and `KFMFS 2024:1` also exist and were likewise never harvested.

### Consolidations
```python
import json, glob
from ferenda.lib import compress
for f in sorted(glob.glob("site/data/downloaded/foreskrift/kfmfs/*.json.br")):
    d = json.loads(compress.read_text(f[:-3]))
    print(d["basefile"], d["files"]["consolidation"])
```
All 17 records: `consolidation: []`. The site's listing has no separate konsoliderad column either — matches.

### Repeal check
```sql
select from_uri, predicate, to_uri from links where from_uri like '%kfmfs/2011:1%';
```
```
kfmfs/2011:1  rpubl:upphaver  rsfs/1991:38
```
Correct target and predicate. `rsfs/1991:38` itself is absent from `documents` (kind='rsfs' holds only 1985-2003 range documents, none from 1991) — an rsfs-scope gap, not a kfmfs defect.

## Checks passed
1. Enumerate — done (1 page, 9 rows).
4. Repeal marking — passed (the one repeal doc we hold is correct).
6. Consolidations — passed (match, no gap).
7. Inherited series — passed (nothing listed to inherit).
8. Freshness — passed (our newest matches the site's newest).

## Checks that found a defect
2. Missing documents — KFMFS 2021:1 through 2024:1.
5. Titles — 16 of 17 documents.
