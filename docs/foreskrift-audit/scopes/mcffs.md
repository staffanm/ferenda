# MCFFS — Myndigheten för civilt försvar (f.d. MSB)

Verdict: DEFECT

Site list: 344 designations from 35 pages (10 gällande + 25 upphävda)
(https://www.mcf.se/sv/regler/gallande-regler/?sortOrder=DescendingYear)

We hold: 158 documents across mcffs(12), msbfs(104), kbmfs(3), srvfs(25),
säifs(14). Newest we hold: MCFFS 2026:13 (2026-06-15). Newest on site (gällande):
MCFFS 2026:13. No freshness gap.

Missing: 178 — SÄI 1983:1 (misrouted, see Inherited) plus 177 documents that
exist only on the "upphävda-regler" archive, which the harvest never visits.
By prefix: MSBFS 35, SRVFS 63, SÄIFS 63, KBMFS 10, MCFFS 2, SÄI 2, SINDFS 2.
Examples: MSBFS 2021:9, MSBFS 2015:1, SRVFS 2008:1, SRVFS 2006:1, KBMFS 2005:1,
SÄIFS 2001:2, SÄIFS 1997:2, SÄIFS 1989:3, SÄI 1988:1, SINDFS 1983:7 (SIND =
predecessor of Sprängämnesinspektionen, an even earlier link in the chain than
säifs).

Extra: 0. Every one of our 158 documents maps 1:1 onto a site row: 98 onto the
gällande list plus 1 (mislabeled) plus 59 onto the upphävda list. No orphan
holdings. The 59 upphävda-list matches were not fetched by the current
mcf.se harvester — a sampled one (`säifs/2000:5`) carries
`"source": "myndfs-legacy"` and an `msb.se` URL, i.e. an older, separate
harvest. The current harvester has fetched nothing from `upphavda-regler`.

Repeal gaps: 0 confirmed parse defects. Sampled MCFFS 2026:9, MSBFS 2025:10,
MSBFS 2025:9, MSBFS 2012:1 — all `upphaver` targets are correct and sit under
the right predecessor slug. MSBFS 2012:1's targets (kbmfs/2002:1,
kbmfs/2003:5) are themselves in the "missing" set above — a symptom of the
archive-gap defect, not a separate repeal-marking bug.

Title defects: 17 — 9 msbfs + 8 srvfs documents (see table). `documents.title`
equals the label (e.g. "MSBFS 2020:6") while the PDF and the site both carry a
real title. Root cause traced to `title_from_masthead`/`_body_start` in
`ferenda/foreskrift/parse.py`: for these documents the extracted block order
puts a `kapitel`/`paragraf`/`allmanna_rad` marker ahead of the masthead
sentence, so the masthead window `blocks[:start]` is empty or too short and
`title_from_masthead` returns `None`.

Consolidations: site no (checked two landing pages, no "Konsoliderad" link),
we hold 0 across all 5 slugs. Matches.

Inherited:
- msbfs (104 held): every site "gällande" MSBFS row (59) sits correctly under
  msbfs, plus 45 held from the upphävda archive. 35 more MSBFS upphävda rows
  are missing entirely (harvest never visits that archive).
- kbmfs (3 held): both KBMFS gällande rows held correctly, 1 from the
  archive. 10 KBMFS upphävda rows missing entirely.
- srvfs (25 held): all 20 SRVFS gällande rows held correctly, 5 from the
  archive. 63 SRVFS upphävda rows missing (9 further "Meddelande NNNN:N" SRV
  items on the archive carry no FS-style designation at all and were not
  counted either way).
- säifs (14 held): all 6 SÄIFS gällande rows held correctly, 8 from the
  archive. säifs/1990:2 and säifs/1995:4 share a byte-identical PDF —
  already covered by #52, not refiled here. 63 SÄIFS upphävda rows are
  missing. One predecessor document, SÄI 1983:1 (before the SÄI→SÄIFS
  rename; not itself in the assigned inherited list but caught by "must
  sit under its own slug"), is filed under **mcffs** as a fabricated
  "MCFFS 1983:1" instead of its own slug — see Issue.

Issue: https://github.com/staffanm/ferenda/issues/71

## Evidence

Entry page + pagination (99 "gällande" rows, 10 pages, `selectedpage=1..10`):

    request(session, "GET", "https://www.mcf.se/sv/regler/gallande-regler/?sortOrder=DescendingYear")
    request(session, "GET", ".../?sortOrder=DescendingYear&selectedpage=N")  # N=2..10

Archive page never in the harvest's index (245 rows, 25 pages):

    request(session, "GET", "https://www.mcf.se/sv/regler/upphavda-regler/")
    request(session, "GET", "https://www.mcf.se/sv/regler/upphavda-regler/?selectedpage=N")  # N=2..25

`ferenda/foreskrift/agencies.py` MCFFS config only sets
`index_url = ".../gallande-regler/?sortOrder=DescendingYear"` and
`params["page_url"]` pointing at the same path — `upphavda-regler` is never
referenced anywhere in the module.

Site rows parsed with `RE_FS_NUMBER`-style regex, by prefix:

    gällande (99):  MCFFS 11, MSBFS 59, SRVFS 20, KBMFS 2, SÄIFS 6, SÄI 1
    upphävda (245): MSBFS 80, SÄIFS 71, SRVFS 68, KBMFS 11, "Meddelande" 9
                    (no FS designation), MCFFS 2, SÄI 2, SINDFS 2

Catalog counts:

    sqlite3 site/data/catalog.sqlite "select kind, count(*) from documents
      where kind in ('mcffs','msbfs','kbmfs','srvfs','säifs') group by kind"
    -> mcffs 12, msbfs 104, kbmfs 3, srvfs 25, säifs 14 (total 158)

Set comparison (site gällande vs. our labels): only mismatch is `SÄI 1983:1`
(site) vs. `MCFFS 1983:1` (ours) — same document, same `source_url`
(`.../gallande-regler/saifs-19831/`), same PDF
(`15ecdf26-fe9d-40e7-a31e-e96c2e968c6a.pdf`).

Artifact for the misfiled document
(`site/data/artifact/foreskrift/mcffs/1983-1.json`):

    "identifier": "MCFFS 1983:1",
    "metadata": {"publisher": "Sprängämnesinspektionen", "title": null, ...},
    "source_url": "https://www.mcf.se/sv/regler/gallande-regler/saifs-19831/",
    structure[2].text == "Sprängämnesinspektionens författningssamling"
    structure[3].text == "SÄI 1983:1"

Download record `site/data/downloaded/foreskrift/mcffs/mcffs-1983-1.json`:

    "basefile": "mcffs/1983:1", "identifier": "MCFFS 1983:1",
    "files": {"regulation": {"text": "SÄI 1983:1 allmänna råd om
      dragskåpsutrustning för arbeten med perklorsyra (överklorsyra)", ...}}

Root cause, `ferenda/foreskrift/harvest.py`:

    RE_FS_NUMBER = re.compile(r"\b([A-ZÅÄÖ-]+FS)\s*(\d{4}):(\d+)", re.IGNORECASE)
    ...
    if agency.params.get("fs_from_designation") and fsm:
        designation = fsm.group(1)
        fs = doc_fs = fs_code(designation)
        identifier = "%s %s:%s" % (designation, arsutgava, lopnummer)

`RE_FS_NUMBER` requires the designation to end in "FS". "SÄI 1983:1" does not
end in "FS" (it predates the SÄI→SÄIFS rename), so `fsm` is `None`, the
`fs_from_designation and fsm` guard fails, and `ref()` falls through to
`agency.fs` ("mcffs") with `identifier = "MCFFS 1983:1"` — a document 43 years
older than the agency that "issued" it.

Title-defect examples (catalog title vs. site row text):

    MSBFS 2020:6  | ours: "MSBFS 2020:6" | site: "MSBFS 2020:6 föreskrifter om
                    informationssäkerhet för statliga myndigheter"
    MSBFS 2025:10 | ours: "MSBFS 2025:10" | site: "MSBFS 2025:10 upphävande av
                    Statens räddningsverks allmänna råd ... (SRVFS 2004:11)"
    SRVFS 2007:1  | ours: "SRVFS 2007:1" | site: "SRVFS 2007:1 allmänna råd och
                    kommentarer om brandvarnare i bostäder"

Placeholder-title query:

    select label from documents where kind='msbfs' and title = label
    -> MSBFS 2013:3, 2014:6, 2020:6, 2020:8, 2021:5, 2025:4, 2025:6, 2025:9, 2025:10
    select label from documents where kind='srvfs' and title = label
    -> SRVFS 1995:1, 2004:3, 2004:12, 2006:3, 2007:1, 2007:4, 2007:5, 2008:3

`site/data/artifact/foreskrift/msbfs/2020-6.json` structure[0] is a `kapitel`
node ("1 kap.") whose `children` hold the ISSN/masthead line, the title
rubrik, and the beslutade line — i.e. `_body_start` returned index 0 (the
`kapitel` marker), so `title_from_masthead(blocks, 0)` searched an empty
window and returned `None`.

Consolidation check: `site/data/downloaded/foreskrift/{mcffs,msbfs,kbmfs,srvfs,säifs}/*.json.br`
— `files.consolidation` is `[]` for all 158 records. Landing pages for
MCFFS 2026:1 and MCFFS 2026:9 carry no "Konsoliderad" text or link.

Repeal sample (`site/data/artifact/foreskrift/{fs}/{doc}.json` `metadata.upphaver`
cross-checked against `links` table, predicate `rpubl:upphaver`):

    MCFFS 2026:9  -> msbfs/2015:4, msbfs/2015:5           (both held, correct)
    MSBFS 2025:10 -> srvfs/2004:11                         (held, correct)
    MSBFS 2025:9  -> msbfs/2018:8, :9, :10, :11, 2024:4    (all held, correct)
    MSBFS 2012:1  -> kbmfs/2002:1, kbmfs/2003:5            (targets missing —
                     part of the 177-document archive gap, not a parse bug)
