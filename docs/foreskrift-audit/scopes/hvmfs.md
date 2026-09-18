# HVMFS — Havs- och vattenmyndighetens författningssamling, Havs- och vattenmyndigheten

Verdict: DEFECT
Site list: 30 designations from 1 page (https://www.havochvatten.se/vagledning-foreskrifter-och-lagar/foreskrifter.html)
We hold: 29 documents (21 hvmfs, 8 fifs), newest HVMFS 2025:2; site newest HVMFS 2025:2
Missing: 1 — HVMFS 2022:19 (Upphävande av NFS 2008:16), dropped by an over-broad `skip_re`
Extra: 0
Repeal gaps: 1 — NFS 2008:16 not marked upphävd, because its repealer (HVMFS 2022:19) is missing from our corpus
Title defects: 9 — 8 documents carry the bare label as title (missing regulation PDF), 1 carries a stray footnote digit
Consolidations: site yes, we hold 25/29 (17/21 hvmfs, 8/8 fifs)
Inherited: fifs — all 8 site-listed FIFS designations sit under the `fifs` slug (not renumbered into hvmfs); site listing matches exactly
Issue: https://github.com/staffanm/ferenda/issues/56

## Evidence

### 1. Enumerate
Fetched the entry page (single GET, `ferenda.lib.net.request`), no pagination markers found
(`grep -i "pagination|sida [0-9]|page=|nästa sida|load-more|visa fler"` = no hits).
`a[href*="/foreskrifter/register-"]` (the agency's own `link_select`) yields 30 anchors:
21 carry an "HVMFS YYYY:N" designation, 8 an "FIFS YYYY:N" designation, 1 carries no
designation in its link text ("Upphävande av Naturvårdsverkets föreskrifter (NFS 2008:16)…").

### 2/3. Missing / extra
Catalog: `select uri,kind,label,title,date,source_url from documents where kind in ('hvmfs','fifs')`
→ 21 hvmfs + 8 fifs = 29 rows, one row per HVMFS/FIFS designation on the site. Every
designation on the site maps to a row we hold, and every row we hold maps to a site
designation. Extra: none.

Missing: the 30th anchor. Its landing page
(`register-stod/upphavande-av-naturvardsverkets-foreskrifter-nfs-200816-…html`) is a real,
separately numbered document: its "Ändringar" section names "HVMFS 2022:19", and its PDF
download link carries both the text "Ursprunglig utgåva" and, as a second `<a>` to the same
href, the text "HVMFS 2022:19" (filename `HVMFS 2022-19-ev.pdf`). We hold no `hvmfs/2022:19`
document (checked catalog, `site/data/downloaded/foreskrift/hvmfs/`,
`site/data/artifact/foreskrift/hvmfs/`).

Root cause: `ferenda/foreskrift/agencies.py` configures HVMFS with
`skip_re: r"Naturvårdsverkets föreskrifter \(NFS"`, matched in
`ferenda/foreskrift/harvest.py:indexed_enumerate` against the *index-page anchor text*
before the landing page is ever fetched:

```
for a in soup.select(p["link_select"]):
    text = a.get_text(" ", strip=True)
    if skip and skip.search(text):
        continue
```

The index-page anchor text for this row is "Upphävande av Naturvårdsverkets föreskrifter
(NFS 2008:16) och allmänna råd om bidrag och ersättningar för viltskador" — it matches
`skip_re` and the row is dropped before `resolve_landing` runs, so the landing page's own
"HVMFS 2022:19" designation is never seen. The code comment at agencies.py:520-522
explains the intent (this row names only the foreign NFS act, "no own HVMFS number in the
listing text") — true for the index page, but the landing page does carry the document's
own number.

### 4. Repeal marking
The index page lists in-force text only; it prints no "upphävd" / "upphört att gälla"
marker anywhere (`grep -ci "upphör|upphävd" hvmfs_index.html` = 0). No struck-through
entries to check against our records this way.

However, HVMFS 2022:19 is itself a repeal of NFS 2008:16 (Naturvårdsverket).
`SELECT uri,kind,label FROM documents WHERE uri='https://lagen.nu/nfs/2008:16'` shows we
hold that document, with no `rpubl:upphaver` link pointing at it — because the repealing
document (HVMFS 2022:19) is missing from our corpus (see #2/3). This is a harvest-defect
repeal gap under the briefing's repeal model, not a request to flag the target itself.

### 5. Titles
Compared all 21 hvmfs titles against the site's link text / landing `<h1>`:

| designation | site title | our title |
|---|---|---|
| HVMFS 2013:27 | Kalkning av sjöar och vattendrag | HVMFS 2013:27 |
| HVMFS 2015:18 | Stödvillkor och återbetalningsskyldighet för kontrollutrustning | HVMFS 2015:18 |
| HVMFS 2015:26 | Övervakning av ytvatten | HVMFS 2015:26 |
| HVMFS 2015:34 | Förvaltningsplaner och åtgärdsprogram för ytvatten | HVMFS 2015:34 |
| HVMFS 2016:17 | Små avloppsanordningar för hushållsspillvatten | HVMFS 2016:17 |
| HVMFS 2017:8 | Fiskefartygs tillträde till hamnar | HVMFS 2017:8 |
| HVMFS 2017:20 | Kartläggning och analys av ytvatten | HVMFS 2017:20 |
| HVMFS 2025:2 | Förbud mot trålfiske efter pelagiska arter … | HVMFS 2025:2 |

For each of these 8, `site/data/downloaded/foreskrift/hvmfs/hvmfs-<basefile>.json` has
`files.regulation: null` and `title: null` — we never downloaded the original regulation
PDF, so `parse_pdf`/`title_from_masthead` never ran and the catalog falls back to the bare
label. The other 13 hvmfs documents (and all 8 fifs) carry a real descriptive title.

Root cause (checked on 2015:26, 2015:34, 2016:17, 2017:8, 2025:2, 2013:27, 2015:18,
2017:20 landing HTML): each landing page hangs the same PDF href under **two** `<a>` tags
— one labelled generically ("Ursprunglig utgåva pdf, …") and one labelled with the
document's own designation ("HVMFS 2017:20 pdf, …"), in that order. `resolve_landing`
(`ferenda/foreskrift/harvest.py`, the `for a in soup.select(...)` loop) dedups by href
*before* classifying:

```
for a in soup.select(agency.params.get("pdf_select", 'a[href$=".pdf"]')):
    href = a.get("href")
    if not href or href in seen:
        continue
    seen.add(href)
    ...
    result = classify(a, fs, arsutgava, lopnummer)
    if result is None:
        continue
```

The first anchor ("Ursprunglig utgåva…", no designation in its text) fails
`classify_file` (no FS number in the text) and is skipped — but its href is already
marked `seen`, so the second anchor (same href, designation in its text, which *would*
classify as `role="regulation"`) is never even offered to `classify`. The regulation PDF
is silently never fetched.

HVMFS 2017:8 shows a variant of the same root cause: the site splits the link text itself
across two `<a>` tags at the same href ("HVMFS" / "2017:8"); the first, text-only "HVMFS"
anchor fails to classify and blocks the second.

Separately, one hvmfs title carries PDF-extraction junk: HVMFS 2012:14's title is
"Havs- och vattenmyndighetens föreskrifter 1 och allmänna råd om badvatten" — a stray
footnote-reference digit ("1", pointing at an EU-directive footnote; the artifact's
`genomfor` names CELEX 32006L0007) has bled into the title text. This looks like a
`title_from_masthead` footnote-stripping gap, isolated to this one document (checked all
21 hvmfs titles for a standalone digit; only this one and the two already-listed
placeholder titles matched).

### 6. Consolidations
Site publishes "Konsoliderad utgåva"/"Konsoliderad version" PDFs on almost every landing
page. We hold a consolidation for 17/21 hvmfs (the 4 missing are folded into the 8
regulation-missing documents above — 2020:25 among them has both regulation and
consolidation, so 17 + 4 more that lack a regulation but not necessarily a consolidation)
and 8/8 fifs. Not a "site publishes, we hold none" defect.

### 7. Inherited: fifs
All 8 site-listed FIFS designations (1982:3, 1994:14, 2004:2, 2004:25, 2004:36, 2004:37,
2010:14, 2010:15) sit under `kind='fifs'` in the catalog, not `hvmfs` — confirmed by direct
query. The site's own listing (mixed into the same HVMFS index page) matches our fifs
holdings exactly: 8 designations, 8 rows, same set. No renumbering into hvmfs, no #50/#52
byte-identical-PDF pattern found for this pair.

### 8. Freshness
Site newest: HVMFS 2025:2 (top of the live index). We hold HVMFS 2025:2. Matches.

### Requests used
4 live HTTP requests via `ferenda.lib.net.request` (1 index page + 1 landing page for the
missing document + 2 landing pages re-fetched to confirm the dedup bug), well under the
60-request budget. No blocks encountered.
