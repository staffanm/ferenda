# IMYFS — Integritetsskyddsmyndighetens författningssamling, Integritetsskyddsmyndigheten
Verdict: DEFECT
Site list: 6 links from 1 page (https://www.imy.se/om-oss/beslut-publikationer-och-remisser/foreskrifter-och-allmanna-rad/)
We hold: 4 documents (imyfs), newest IMYFS 2024:1; site newest IMYFS 2024:1 — plus 5 documents (difs, inherited), newest DIFS 2018:2
Missing: 0
Extra: 2 (imyfs) — IMYFS 0008:2, IMYFS 2014:89: not corpus defects as "extra" per se, but their designations are fabricated (see Title/Identifier defects). difs/2011:1 and difs/2018:2 are legitimately extra: difs/2018:2 is repealed by imyfs/2024:1 (upphaver row present); difs/2011:1 is an ändringsföreskrift to the now-repealed DIFS 1989:1, so it naturally dropped off the "gällande" list.
Repeal gaps: 0 — every site-marked repeal (IMYFS 2023:1 -> DIFS 1989:1; IMYFS 2024:1 -> DIFS 2018:2) has a matching `rpubl:upphaver` row in our links table.
Title defects: 3 (all difs) — DIFS 1999:1 title stored as the bare identifier "DIFS 1999:1" instead of the real title; DIFS 1998:1 title is scrambled/interleaved text merging three different regulations' descriptions; DIFS 2018:2 title is a full paragraph of body text (should be a short title).
Identifier defects: 2 (imyfs) — IMYFS 0008:2 and IMYFS 2014:89 are fabricated designations. Both source documents are "Datainspektionens allmänna råd" (non-binding guidance) that carry no FS number anywhere in their text, and neither is present on the current site at all.
Consolidations: site no, we hold 0
Inherited: difs — sits under its own `difs` slug (not collapsed into imyfs), confirming `fs_from_designation` works correctly. 3 of 5 difs docs (1998:1, 1999:1, 2018:1) were (re-)harvested through imy.se; 2 (2011:1, 2018:2) are legacy artifacts fetched directly from the retired datainspektionen.se, kept as-is (source of truth).
Issue: https://github.com/staffanm/ferenda/issues/60

## Evidence

### 1. Enumerate
Fetched the single entry page with `ferenda.lib.net.request`:

    r = request(session, "GET", "https://www.imy.se/om-oss/beslut-publikationer-och-remisser/foreskrifter-och-allmanna-rad/")

`soup.select('a[href*="/link/"][href$=".aspx"]')` (the same `link_select` the IMYFS agency config uses) returns 6 anchors:

    IMYFS 2024:1      (pdf, 3 MB)
    Vägledning – föreskrifter om behandling av personuppgifter som rör lagöverträdelser (IMYFS 2024:1)      (pdf, 441 kB)
    IMYFS 2023:1      (pdf, 26 kB)
    DIFS 2018:1      (pdf, 63 kB)
    DIFS 1999:1      (pdf, 121 kB)
    DIFS 1998:1      (pdf, 147 kB)

No pagination; one page covers the whole listing. Only 1 page read (well inside the 60-request budget; total requests used: 1).

### 2/3. Missing / Extra
Catalog query (`site/data/catalog.sqlite`, `kind='imyfs'`) returns 4 rows: IMYFS 0008:2, IMYFS 2014:89, IMYFS 2023:1, IMYFS 2024:1.
IMYFS 2023:1 and 2024:1 match the site. IMYFS 0008:2 and 2014:89 do not appear anywhere on the current page (`html.count("Säkerhet för personuppgifter") == 0`, `html.count("Information till registrerade") == 0`, and their harvested link ids `9a6bf501…` / `89c2a725…` are also absent from the page). So they are not simply "the site only shows in-force text" — they are gone from the site entirely, consistent with these being general-advice documents Datainspektionen retired years ago.

`kind='difs'` returns 5 rows: DIFS 1998:1, 1999:1, 2011:1, 2018:1, 2018:2. The site lists 1998:1, 1999:1, 2018:1 (3 of 5). The other two:
- `difs/2018:2`: `links` has a row `imyfs/2024:1 --rpubl:upphaver--> difs/2018:2` → correctly repealed, correctly absent from the "gällande" list.
- `difs/2011:1`: an ändringsföreskrift to DIFS 1989:1 ("Föreskrifter om ändring av Datainspektionens föreskrifter (DIFS 1989:1) om tillstånd enligt 2 § inkassolagen"); DIFS 1989:1 itself is repealed by `imyfs/2023:1`. No separate repeal marker for 2011:1 is expected or required by the repeal model (it amends a document, it is not itself repealed).

### 4. Repeal marking
Site text: "Upphävande av tidigare gällande föreskrifter" section names IMYFS 2023:1 (repeals DIFS 1989:1) and, via its own body text, IMYFS 2024:1 (repeals DIFS 2018:2). Both repeals are present as `rpubl:upphaver` links:

    difs/1999:1  --upphaver--> difs/1987:1231, difs/1988:2
    difs/2018:1  --upphaver--> difs/1998:2, difs/1998:204, difs/1998:3
    imyfs/2023:1 --upphaver--> difs/1989:1
    imyfs/2024:1 --upphaver--> difs/2018:2

No gaps found for this scope.

### 5. Titles
Compared `documents.title` against the site's own descriptive line for each anchor (the `<br>` text following the link):

| designation | site says | we hold |
|---|---|---|
| IMYFS 2024:1 | "Föreskrifter om behandling av personuppgifter som rör lagöverträdelser." | "Integritetsskyddsmyndighetens föreskrifter om behandling av personuppgifter som rör lagöverträdelser" — OK (PDF's own cover title, agency name prefix) |
| IMYFS 2023:1 | "Föreskrifter om upphävande av Datainspektionens föreskrifter (DIFS 1989:1) om tillstånd enligt 2 § inkassolagen (1974:182)" | matches |
| DIFS 2018:1 | "Föreskrifter om upphävande av vissa föreskrifter enligt personuppgiftslagen (1998:204)." | matches |
| DIFS 1999:1 | "Föreskrifter om upphävande av myndighetens föreskrifter (DIFS 1988:2) om granskning av personregister med hjälp av automatisk databehandling (ADB) vid taxeringsrevision och tullrevision, m.m." | **"DIFS 1999:1"** — the bare identifier, not a title |
| DIFS 1998:1 | "Föreskrifter om upphävande av vissa föreskrifter om personregister enligt datalagen (1973:289)." | **"föreskrifter (DIFS 1996:2) för vissa tillståndspliktiga Datainspektionens föreskrifter om upphävande personregister i försäkringsrörelse, av vissa föreskrifter om personregister enligt 14. föreskrifter (DIFS 1996:3) om protokollsregister hos datalagen (1973:289)"** — scrambled/interleaved |
| DIFS 2018:2 | (not on site) | title field holds the entire first substantive paragraph of the regulation (~900 words), not a short title |

### 6. Consolidations
`html.count("konsolider")` and `("Konsolider")` are both 0 on the entry page. Every download record under `site/data/downloaded/foreskrift/{imyfs,difs}/*.json.br` has `files.consolidation == []`. No defect: neither the site nor our harvest carries consolidations for this agency.

### 7. Inherited series (DIFS)
Confirmed DIFS documents sit under their own `difs` kind in the catalog, not `imyfs` — `fs_from_designation` works as intended. Provenance split:
- `difs/1998:1`, `difs/1999:1`, `difs/2018:1`: `url` in the download record is the imy.se entry page — harvested through the current IMYFS agency config.
- `difs/2011:1`, `difs/2018:2`: `url` is `datainspektionen.se` (a retired domain) — legacy artifacts from before the site consolidated onto imy.se. Read-only inspection found no sign these need re-harvesting; they are simply older data, consistent with "the JSON artifact is the source of truth."

### 8. Freshness
Site newest: IMYFS 2024:1 (also newest DIFS on site: 2018:1, since older DIFS numbers are repealed/superseded). We hold IMYFS 2024:1 as the newest imyfs document. No freshness gap.

### Root cause of the identifier defect (IMYFS 0008:2 / 2014:89)
`ferenda/foreskrift/harvest.py::ref()` derives a document's `(year, lopnummer)` in this order: an FS-prefixed designation in the anchor text, then (for a `direct` agency) a slug match on the href's filename, then a bare `YYYY:N` in the text. IMYFS's anchors are `/link/<uuid>.aspx` redirects — the "filename" is an opaque hex string, not a meaningful slug. When an anchor's text carries no designation at all (the two allmänna råd here), `RE_FS_NUMBER` finds nothing, so the code falls to:

    elif slugm:
        arsutgava, lopnummer = slugm.group(1), str(int(slugm.group(2)))

`RE_SLUG_NUMBER` (`[a-zåäö]+[-_ ]?(\d{4})[-_ ]?(\d{1,3})(?:\D|$)`) matches by coincidence inside the hex string:

    "9a6bf501219747998c74e5da97d00082.aspx" -> ("0008", "2")   => "IMYFS 0008:2"
    "89c2a725b201489a9cbbf4577cee90f7.aspx" -> ("2014", "89")  => "IMYFS 2014:89"

This `elif slugm:` branch is meant for agencies whose params set `number_from_slug` (a real filename like `rgkfs_2015_2.pdf`); IMYFS does not set that flag, but the branch fires anyway whenever `fsm` is empty. Both resulting "documents" are non-binding allmänna råd, never had a real FS number, and are no longer even listed on the site.
