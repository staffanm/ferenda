# FFFS — Finansinspektionen

Verdict: DEFECT
Site list: 395 entries (121 grundförfattningar, 274 ändringsförfattningar) from 1 page (https://www.fi.se/sv/vara-register/fffs/forteckning-fffs/)
We hold: 167 documents (fffs), newest FFFS 2025:13; site newest grundförfattning FFFS 2025:13 (newest ändringsförfattning on site: FFFS 2026:27, not separately modeled per corpus design)
Missing: 4 — FFFS 2023:13, FFFS 2023:22, FFFS 2024:20, FFFS 2025:8
Extra: 41 — 16 covered by a repeal we hold (legitimate: repealed base regs FI dropped from the current list); 25 are ändringsförfattningar we hold as standalone documents that the site now nests under their base's page instead of listing at top level (legitimate: harvested when FI's site listed them differently; examples FFFS 2017:14, FFFS 2018:24, FFFS 2021:12, FFFS 2021:25)
Repeal gaps: 0 — spot-checked FFFS 2018:10 → upphaver → FFFS 2005:11, present and correct
Title defects: 19 — 8 with masthead boilerplate baked in, 11 with no title at all (bare identifier)
Consolidations: site yes, we hold 47
Inherited: none (per assignment)
Issue: https://github.com/staffanm/ferenda/issues/53

## Evidence

### 1. Enumeration
`ferenda.foreskrift.agencies.fi_enumerate` fetched the single förteckning page
(https://www.fi.se/sv/vara-register/fffs/forteckning-fffs/) and found 126
hrefs matching `RE_FI_BASE` (`/sok-fffs/(\d{4})/(\d{4})(\d+)/?$`).

The same page, parsed as structured data (each result is a `<dl>` with
Nummer/Rubrik/Typ), holds "395 träffar" — every FFFS ever issued, base and
amendment, in one unpaginated response: 121 tagged `Grundförfattning`, 274
tagged `Ändringsförfattning`. No pagination was needed; this is the
complete list.

### 2. Missing (4)
Comparing the 121 `Typ=Grundförfattning` identifiers against our 167 catalog
rows:

    FFFS 2023:13 — Finansinspektionens föreskrifter om ägar-, ägarlednings-
                   och ledningsprövning i kreditinstitut
    FFFS 2023:22 — Finansinspektionens föreskrifter och allmänna råd om
                   viss finansiell verksamhet
    FFFS 2024:20 — Föreskrifter om rapportering av incidenter och
                   informationsregister enligt EU:s förordning om digital
                   operativ motståndskraft för finanssektorn (DORA)
    FFFS 2025:8  — Föreskrifter om upphävande av Finansinspektionens
                   föreskrifter och allmänna råd (FFFS 2014:8) om viss
                   verksamhet med konsumentkrediter

Root cause: FI moved these four detail pages to slug-style URLs
(`/sok-fffs/2023/finansinspektionens-foreskrifter-fffs-202313-...
-kreditinstitut/`, `/sok-fffs/2025/fffs-20258/`) instead of the numeric
`/sok-fffs/YYYY/YYYYNNN/` form every other entry uses. `RE_FI_BASE` only
matches the numeric form, so `fi_enumerate` silently drops these four —
they are never queued for download. Confirmed absent from `documents`:

    sqlite3 -readonly site/data/catalog.sqlite \
      "select label from documents where kind='fffs' and label in
       ('FFFS 2023:13','FFFS 2023:22','FFFS 2024:20','FFFS 2025:8')"
    -> (no rows)

### 3. Extra (41)
41 catalog rows have no matching top-level `Grundförfattning` entry on the
site. Checked each against `links` for a `rpubl:upphaver` edge pointing at
it:

    select from_uri from links where predicate='rpubl:upphaver'
      and to_uri='https://lagen.nu/fffs/<label>'

16 are repealed base regulations FI has dropped from its current list once
spent (e.g. FFFS 2007:4, repealed by FFFS 2024:14; FFFS 2010:1, repealed
by FFFS 2025:13) — legitimate, matches the repeal model.

The other 25 (e.g. FFFS 2017:14, FFFS 2018:24, FFFS 2020:2, FFFS 2021:12,
FFFS 2021:25) are ändringsförfattningar we hold as their own document. On
the current site they appear only nested under their base's page ("Ändring
av Grundförfattning") — e.g. FFFS 2020:2's own `source_url` in our record
is `.../sok-fffs/2014/201433/20202/`, a URL shape FI no longer surfaces at
top level. These were harvested when the site listed them differently and
are not a corpus defect; they simply predate a site reorganization.

### 4. Repeal marking
Sampled FFFS 2005:11, whose landing page reads "Upphävd 2018-10-01, se
FFFS 2018:10":

    select from_uri,predicate,to_uri from links
      where predicate='rpubl:upphaver' and to_uri like '%fffs/2005:11%'
    -> FFFS 2018:10 -- rpubl:upphaver --> FFFS 2005:11

Correct. No repeal gap found in the sample checked.

### 5. Title defects (19)
**(a) Masthead boilerplate not stripped — 8 documents.** FI's older PDF
mastheads print "Prenumerera även/också per e-post på www.fi.se." as a
subscription line. `RE_MASTHEAD_BOILERPLATE` in
`ferenda/foreskrift/parse.py` strips `www\.[\w.-]+` but not the
"Prenumerera ... på" clause in front of it, so that clause survives and
gets glued onto the real title:

    FFFS 2001:8  ours: "Prenumerera även per e-post på Finansinspektionens
                        allmänna råd om inlåningskonton och tillhörande
                        banktjänster"
                 site: "Allmänna råd om inlåningskonton och tillhörande
                        banktjänster"
    FFFS 2002:11 ours: "Prenumerera även per e-post på Finansinspektionens
                        föreskrifter om skyldighet att elektroniskt lämna
                        uppgifter om handel med vissa finansiella
                        instrument"

    pdftotext site/data/downloaded/foreskrift/fffs/fffs-2001-8-regulation.pdf -
    -> line 4: "Prenumerera även per e-post på www.fi.se."

Full list: FFFS 2001:8, 2002:8, 2002:11, 2002:23 ("även"); 2004:4, 2004:10,
2004:15, 2005:1 ("också").

**(b) No title at all, falls back to the bare identifier — 11
documents.** `documents.title == documents.label` for FFFS 1991:12,
1991:15, 1991:16, 1991:17, 1992:3, 2005:11, 2007:17, 2011:27, 2013:9,
2017:11, 2017:22. Ten of these have a real title on the site (checked via
the structured Rubrik field); only FFFS 2005:11's grundförfattning is old
enough to have dropped off the current list too, but its own landing page
still names it "Föreskrifter och allmänna råd om försäkringsförmedling".

Two distinct root causes found:

  - **Table of contents precedes the masthead.** FFFS 2007:17 and FFFS
    2013:9 print an "INNEHÅLL" page ahead of the real masthead/title
    sentence, which sits several pages in. Title extraction never reaches
    it.

        pdftotext -layout fffs-2013-9-regulation.pdf - | head -8
        -> "INNEHÅLL ... FFFS 2013:9" (no title text)
        pdftotext -layout fffs-2013-9-regulation.pdf - | sed -n '284,291p'
        -> "Finansinspektionens föreskrifter / om värdepappersfonder;"
           (the real title, ~280 lines in)

  - **Wrong PDF classified as the regulation.** For FFFS 2005:11, 2011:27
    and 2017:11 the file stored as `files.regulation` is not the
    regulation text at all:

        FFFS 2005:11 -> "Rättelseblad till FFFS 2005:11" (a corrigendum)
        FFFS 2011:27 -> "FÖRFATTNINGSKOMMENTARER" (a commentary letter)
        FFFS 2017:11 -> "Enligt sändlista ... Fi Dnr 16-2467" (a cover letter)

    For FFFS 2005:11 the landing page carries both the real regulation
    link (`FFFS 2005:11 -> fffs0511.pdf`) and the corrigendum
    (`Rättelseblad till FFFS 2005:11 -> fffs0511_rattelseblad.pdf`); the
    harvest classifier picked the corrigendum instead of the regulation:

        grep -A0 "contentassets" fffs-2005-11.html | grep pdf
        -> "FFFS 2005:11 | .../fffs0511.pdf" (correct, present, unused)
           "Rättelseblad till FFFS 2005:11 | .../fffs0511_rattelseblad.pdf"
           (this is what files.regulation.url points at instead)

    FFFS 1991:12, 1991:15, 1992:3 use an old all-caps masthead style
    ("FINANSINSPEKTIONENS FÖRFATTNINGSSAMLING" / predecessor-samling
    names) that differs from the mixed-case form the regex expects;
    not fully root-caused, filed as a related symptom.

### 6. Consolidations
FI publishes konsoliderade versions ("FFFS 2002:11 (konsoliderad
version)" alongside the base on its landing page). Of 166 fffs download
records, 47 carry `files.consolidation`:

    grep -l consolidation ... -> 47/166

Matches expectation; no defect.

### 7. Inherited series
None assigned for this scope.

### 8. Freshness
Newest grundförfattning on site: FFFS 2025:13 (an upphävande). Newest we
hold: FFFS 2025:13. Matches — modulo the missing FFFS 2025:8 (harvest
defect above), which sits between FFFS 2025:2 and FFFS 2025:13.
