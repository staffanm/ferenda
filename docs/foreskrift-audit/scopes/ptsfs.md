# PTSFS — Post- och telestyrelsens författningssamling, Post- och telestyrelsen

Verdict: DEFECT
Site list: 44 designations from 1 page (https://pts.se/regelbibliotek/gallande-foreskrifter-och-allmanna-rad/)
We hold: 45 documents (ptsfs), newest PTSFS 2026:3; site newest PTSFS 2026:3
Missing: 2 — PTSFS 2016:6, PTSFS 2022:10 (both real, currently-listed regulations; see below)
Extra: 1 — PTSFS 2021:3 (repealed by PTSFS 2025:5, which we hold with a correct `upphaver` link; not a defect)
Repeal gaps: 0 confirmed of the ordinary kind (every upphavande=1 document with a real target has a correct `upphaver` link); see Title/harvest defects below for a related but different failure
Title defects: 5 — PTSFS 2006:1, 2007:1, 2009:6, 2025:2, 2025:4 (catalog title is the bare designation, e.g. "PTSFS 2007:1", instead of the real title)
Consolidations: site yes, we hold 2 (PTSFS 2022:3, PTSFS 2023:2) — matches the 2 documents on record with amendments
Inherited: none (no predecessor series for this scope)
Issue: https://github.com/staffanm/ferenda/issues/81

## Evidence

### 1. Enumerate

    .venv/bin/python -c "... request(session,'GET','https://pts.se/regelbibliotek/gallande-foreskrifter-och-allmanna-rad/') ..."

200 OK, 27596 bytes. `.secondlevel__content__box a[href^="/regelbibliotek/"]` (the selector `ferenda/foreskrift/agencies.py`'s PTSFS config uses) yields 44 anchors, one page, no pagination controls found. This matches `indexed_enumerate`'s single-page assumption — the index is genuinely one page, so `indexed_enumerate` is not truncating anything here.

I searched pts.se and the web for a separate "upphävda föreskrifter" archive (the pattern already found for AFS #45, EIFS #51, IAFFS #57, KIFS #65, MCFFS #71, MYHFS #74). `/regelbibliotek/` and `/regelbibliotek/regelbibliotek-sok/` contain no "upphävd"/"arkiv" text or links, a `site:pts.se` web search turns up nothing, and guessed URLs (`upphavda-foreskrifter/`, `upphavda-regler/`, `historik/`) all 404. PTS does not appear to publish a separate repealed-regulations index; each repealed regulation instead keeps its own live landing page, unlinked from `gallande-foreskrifter-och-allmanna-rad`. A headless-Chromium fetch of the search page hit the same Radware WAF gate the agency config's comment already documents ("Radware gate -> browser UA + sv locale") — a plain `requests`-based fetch with `BROWSER_UA` works fine, so the block is a headless-browser fingerprint issue, not a site-wide block; I did not pursue it further since the static crawl found no archive link to run it against anyway.

### 2/3. Missing / extra

    SELECT label FROM documents WHERE kind='ptsfs'   -- 45 rows
    site designations (regex `PTSFS\s*(\d{4}:\d+)` on each anchor's text) -- 44

Diffed by designation string: 0 missing, 1 extra (`PTSFS 2021:3`). But 3 of the 44 site rows carry **two** PTSFS designations in their anchor text, in the order "(repealed designation) ... (repealing designation)" — the reverse of the usual "(repealing designation) ... (repealed designation)" order every other upphävande row uses:

    PTSFS 2009:6 | Upphävande av PTS allmänna råd (PTSFS 2009:6) om underrättelse vid villkorsändring (PTSFS 2022:10)
    PTSFS 2007:1 | Upphävande av allmänna råd (PTSFS 2007:1) om information om tjänstekvalitet (PTSFS 2016:6)

Fetching each landing page confirms the real, currently-listed regulation is the **second** number, not the first:

    https://pts.se/regelbibliotek/upphavande-av-pts-allmanna-rad-ptsfs-20096-om-underrattelse-vid-villkorsandring/
      h1: Upphävande av PTS allmänna råd (PTSFS 2009:6) om underrättelse vid villkorsändring (PTSFS 2022:10)
      pdf: .../ptsfs-2022_10-upphavande-av-ptsfs-2009_6_dnr-22-1414.pdf   <- this is PTSFS 2022:10

    https://pts.se/regelbibliotek/upphavande-av-allmanna-rad-ptsfs-20071-om-information-om-tjanstekvalitet-ptsfs-20166/
      h1: Upphävande av allmänna råd (PTSFS 2007:1) om information om tjänstekvalitet (PTSFS 2016:6)
      pdf: .../ptsfs-2016-6.pdf                                          <- this is PTSFS 2016:6

`ferenda/foreskrift/harvest.py`'s `ref()` picks the *first* `PTSFS YYYY:N` match in the index anchor's text (`RE_FS_NUMBER.search(ident_text)`) as the document's own number. For every other PTS upphävande row that first number is the repealer's own number, so it works; for these two rows it is the *repealed* target's number, so `ref()` mints the wrong basefile. The result: our corpus holds two empty phantom records, `ptsfs/2007:1` and `ptsfs/2009:6` (see download records below), and is **missing the two real, currently-in-force regulations PTSFS 2016:6 and PTSFS 2022:10 entirely**.

The docstring of `ref()` already documents this exact ambiguity for KKVFS and has a param (`number_from_slug`) to resolve it from the PDF filename instead of the index text — that param is not set for PTSFS.

### 4. Repeal marking

    SELECT from_uri,predicate,to_uri FROM links WHERE from_uri LIKE '%/ptsfs/%' AND predicate LIKE '%upphaver%'

Every `upphavande=1` document with a genuine target has a correct `rpubl:upphaver` row (e.g. `ptsfs/2016:1 -> ptsfs/2004:2`, `ptsfs/2022:1 -> ptsfs/2008:1`, 30 rows total). No ordinary repeal gap.

But `PTSFS 2025:4` — which the site's own page title says repeals PTSFS 2022:11 — has an empty `upphaver` list in our artifact:

    site h1: "Föreskrifter (PTSFS 2025:4) om upphävande av ... säkerhet i nät och tjänster (PTSFS 2022:11)"
    our artifact site/data/artifact/foreskrift/ptsfs/2025-4.json:
      "upphaver": [], "title": null, "beslutsdatum": null
      "andradAv": ["https://lagen.nu/ptsfs/2024:4"]   <- a designation that does not exist on the site

Cause: the actual PDF PTS hosts for this document is misnamed on PTS's own side —
`foreskrifter-2024-4-om-upphavande-av-foreskrifter-om-sakerhet-i-nat-och-tjanster-ptsfs-2022-11.pdf`
(PTS's own filename typo: "2024-4" where the document's own title says 2025:4). `classify_href()` in
`ferenda/foreskrift/harvest.py` reads the number out of the PDF filename slug and compares it against
`base_ars`/`base_lop` (from the index text, correctly "2025", "4"); since "2024" != "2025" it classifies
the file as an "amendment" reference to a nonexistent "PTSFS 2024:4" instead of downloading it as the
regulation body. The document exists in our catalog under the right designation (2025:4) but with no
PDF, no dates, and no `upphaver` link — a genuine repeal gap on a document we do hold.

### 5. Titles

Compared 15 titles against the site's own anchor text (see the full table generated during the audit).
Twelve match once the document's own designation is stripped out. Five are junk — the bare designation
used as a fallback title because parsing found nothing better:

| designation | our title | cause |
|---|---|---|
| PTSFS 2006:1 | "PTSFS 2006:1" | no PDF ever fetched (see below) |
| PTSFS 2007:1 | "PTSFS 2007:1" | wrong basefile — see Missing/Extra |
| PTSFS 2009:6 | "PTSFS 2009:6" | wrong basefile — see Missing/Extra |
| PTSFS 2025:4 | "PTSFS 2025:4" | no PDF ever fetched — see Repeal marking |
| PTSFS 2025:2 | "PTSFS 2025:2" | PDF fetched fine, title extraction fails (see below) |

`PTSFS 2006:1`'s download record has `"regulation": null` and empty `amendment`/`memo`/`attachment`
lists — nothing at all was captured, even as a reference. Its PDF filename is
`2006_1_upphavande_foreskrift_provforrattare.pdf` — digits first, no leading letters. `classify_href`'s
`RE_SLUG_NUMBER = r"[a-zåäö]+[-_ ]?(\d{4})[-_ ]?(\d{1,3})(?:\D|$)"` requires a letter run immediately
before the year, so it never matches this filename shape and `classify_href` returns `None` for the
only PDF on the page — the file is silently dropped.

`PTSFS 2025:2` is different: it fetched a full PDF, parsed structure, dates and even a correct
`upphaver` link (to `ptsfs/2019:1`) — only the title is `null`. Its level-1 rubrik in the artifact is a
list of text/citation segments where the document's own designation is a `dcterms:references` link
embedded mid-sentence ("Post- och telestyrelsens allmänna råd ( [PTSFS 2025:2] ) om den svenska
frekvensplanen") rather than one plain string — title extraction does not handle that shape and leaves
the title empty.

### 6. Consolidations

    grep for files.consolidation across site/data/downloaded/foreskrift/ptsfs/*.json.br

2 of 45 download records carry a `consolidation` file (PTSFS 2022:3, PTSFS 2023:2) — both are exactly
the two PTS documents on record with a later amendment (`andradAv`), so this matches expectation; no
consolidation-coverage gap found.

### 7. Inherited series

None declared for this scope (assignment says "Inherited series: none"). No check performed.

### 8. Freshness

Newest on site and newest held both PTSFS 2026:3 (2026-02-18); PTSFS 2026:1/2026:2/2026:3 all have a
downloaded PDF. No freshness gap.
