# HSLFFS-SOS — HSLF-FS, Socialstyrelsen

Verdict: DEFECT
Site list: 427 designations (274 HSLF-FS/HSLS-FS + 153 SOSFS) from 2 pages
(https://www.socialstyrelsen.se/kunskapsstod-och-regler/regler-och-riktlinjer/foreskrifter-och-allmanna-rad/,
plus the konsoliderade-foreskrifter sub-page)
We hold: 511 documents (publisher=Socialstyrelsen: hslffs=289, sosfs=222),
newest HSLF-FS 2026:32; site newest HSLF-FS 2026:32 (match). SOSFS: we hold
newest SOSFS 2016:48, site's newest listed SOSFS is 2015:22 (no gap, ours is
ahead).
Missing: 1 — HSLF-FS 2024:5 (on the site, PDF and consolidation HTML sit on
disk, but no download record and no catalog entry — a harvest run that never
finished for this document)
Extra: 99 (publisher=Socialstyrelsen, not in the current site listing) — 75
covered by an `upphaver` link we hold; 24 not covered by any `upphaver` link,
of which spot checks show most are the same parse defect as the repeal-gap
finding below, not a real gap
Repeal gaps: ~34 (confirmed root cause below) — the document's own text
repeals a named regulation, but `metadata.upphaver` is empty
Title defects: 4 — SOSFS 1996:21 (title == label, no real title); HSLF-FS
2016:53 (title is the entire enacting/bemyndigande clause, ~500 words);
HSLF-FS 2020:17 and HSLF-FS 2021:10 (masthead furniture "Utkom från trycket
... den <date>" spliced into the middle of the title)
Consolidations: site yes (76 konsoliderade förteckning rows), we hold 81
download records with consolidation files for Socialstyrelsen documents — OK
Inherited: sosfs — 223 held (222 Socialstyrelsen + 1 Rättsmedicinalverket),
correctly filed under the `sosfs` slug; all 153 SOSFS designations the site
currently lists are present in our corpus; the 69 SOSFS documents we hold
beyond that list are older documents the site has since dropped, mostly
explained by the same repeal-extraction defect, not a renumbering problem
Issue: https://github.com/staffanm/ferenda/issues/58

## Evidence

### 1. Enumeration

Read the harvester's own code first (`ferenda/foreskrift/hslffs.py`,
`sos_enumerate`/`sos_publications`/`sos_konsoliderade`) and reproduced it with
plain HTTP (2 requests total against the agency site for this step):

    GET https://www.socialstyrelsen.se/kunskapsstod-och-regler/regler-och-riktlinjer/foreskrifter-och-allmanna-rad/
      -> embedded PublicationCategoryList JSON, 460 entries, 427 match an FS
         number (33 dropped: annual "Register över författningar",
         "Förteckning över gällande författningar", handböcker, etc — by
         design, per the harvester's own docstring)
    GET .../foreskrifter-och-allmanna-rad/konsoliderade-foreskrifter/
      -> 76 konsoliderade rows

Designation counts in the 427: HSLF-FS 273, HSLS-FS 1 (a known site misprint
the harvester aliases to hslffs), SOSFS 153.

A third GET (of the same index page) checked for a "struck through /
upphävd" marker on the flat list, for check 4: `upphävd`, `upphävts`,
`upphört att gälla` and `har upphört` all occur 0 times. The list only adds
and removes rows; it never marks one as repealed in place. So check 4 (site
strikethrough vs our repeal links) has no case to check here — the site
carries no such marker.

Catalog counts (`site/data/catalog.sqlite`):

    SELECT kind, publisher, count(*) FROM documents WHERE kind IN ('hslffs','sosfs') GROUP BY kind, publisher
    hslffs | Socialstyrelsen | 289
    sosfs  | Socialstyrelsen | 222
    sosfs  | Rättsmedicinalverket | 1
    (plus hslffs rows for the other five publishers: E-hälsomyndigheten 4,
    Folkhälsomyndigheten 79, IVO 10, Läkemedelsverket 233, MFoF 6,
    Rättsmedicinalverket 10, TLV 32 — out of this scope's audit)

### 2. Missing: HSLF-FS 2024:5

Site: `/publikationer/hslf-fs-20245-socialstyrelsens-foreskrifter-om-uppgiftsskyldighet-till-socialstyrelsens-medicinska-fodelseregister-2024-3-8995/`,
"Socialstyrelsens föreskrifter om uppgiftsskyldighet till Socialstyrelsens
medicinska födelseregister". Not in `documents` under any kind.

    find site/data/downloaded/foreskrift/hslffs -iname "*2024-5*"
    hslffs-2024-5-consolidation-0.html.br
    hslffs-2024-5-regulation.pdf
    (no hslffs-2024-5.json.br, no hslffs-2024-5.html.br)

The regulation PDF and the konsoliderad-page HTML were fetched, but
`resolve_page` (`ferenda/foreskrift/hslffs.py`) never reached its final
`compress.write_download(landing)` / `write_record(...)` calls — those two
files are always written together, right after the per-document work, and
neither exists. Re-running `hslffs.sos_amendments` against the stored
consolidation HTML does **not** raise (`[]`, a legitimate no-amendments-yet
answer), so the code itself does not reproduce a crash on this content — this
looks like a harvest run that was interrupted before it finished this
basefile, leaving orphaned partial downloads and no record. Re-downloading
this one basefile would fix it; I made no such run (read-only per the
briefing).

### 3. Extra: 99 not on the current site list

    site_keys = {fs}|{year}|{nr} for the 427 site entries
    soc_catalog = documents WHERE kind IN (hslffs,sosfs) AND publisher='Socialstyrelsen'  # 511
    extra = soc_catalog - site_keys                                                       # 99

Cross-checked against `catalog.upphaver_targets(con)` (5469 targets
corpus-wide): 75 of the 99 are covered (a document we hold repeals them, so
"not on the site's current list" is expected — the site does not keep spent
regulations visible). 24 are not covered by any `upphaver` link. Spot checks
on several of those 24 (SOSFS 2005:29, 2007:8, 2008:17 and their
ändringsförfattningar) show the same root cause as the repeal-gap finding
below: the base act's own text repeals its predecessor, but the extraction
regex fails silently on it, so no `upphaver` link exists for us to check
against. These 24 are very likely also legitimately repealed, not a harvest
gap.

### 4. Repeal gaps (confirmed parse defect)

Automated scan: for every Socialstyrelsen hslffs/sosfs document, flatten its
`structure` to plain text and look for `upphäv(s|er) ... (<FS> <year>:<nr>)`
with no `§` before the designation (a whole-regulation repeal, not a
paragraph repeal); compare against `metadata.upphaver`.

    36 documents match the text pattern with metadata.upphaver == []
    2 are real exceptions (HSLF-FS 2015:30, 2016:3 repeal specific
      paragraphs of another regulation, not the whole thing -- correctly
      excluded by `_repeal_object`'s own provision check)
    ~34 are genuine misses

Root cause, confirmed by direct reproduction. `SOSFS 2008:17`'s text:

    "2. Genom författningen upphävs – Socialstyrelsens föreskrifter (SOSFS
    1996:26) Målbeskrivningar ... , – Socialstyrelsens föreskrifter och
    allmänna råd (SOSFS 1996:27) Läkarnas ... m.m. − För läkare som har fått
    legitimation före den 1 juli 2006 ... 2013.SocialstyrelsenLARS-ERIK HOLM..."

`RE_ERSATTER` (`ferenda/foreskrift/parse.py:180`) captures `.{0,600}?` after
the verb, ended by `\.\s+(?=[A-ZÅÄÖ−])` — a period **followed by
whitespace**. The PDF text glues the transitional clause straight into the
signature block with no space ("2013.Socialstyrelsen"), so within the
600-character window there is no matching terminator and the whole match
fails — not a partial match, no match at all, so `_repeal_object` is never
called and the two SOSFS 1996:26/1996:27 targets are dropped silently.

Reproduced directly (not just read off the stored artifact):

    .venv/bin/python -c "
    from ferenda.foreskrift.parse import extract_metadata, PARSE_TYPES
    from ferenda.lib.lagrum import sfs_parser
    # text = flattened structure of site/data/artifact/foreskrift/sosfs/2008-17.json
    print(extract_metadata(text, '', sfs_parser('foreskrift', PARSE_TYPES), fs='sosfs')['upphaver'])
    "
    # []

For `SOSFS 2015:8` (repeals `SOSFS 2008:17`, a shorter, single-target,
still-broken clause) the same reproduction returns
`['https://lagen.nu/sosfs/2008:17']` when run directly, proving the rule
*can* read this shape — the live artifact's `metadata.upphaver` for
`sosfs/2015:8` is nonetheless `[]`, so whatever ran at harvest/parse time hit
a version of the text (raw PDF extraction, before the structuring that
produced the stored JSON) where the same 600-char/no-space-terminator problem
applied.

Other confirmed misses from the 34: SOSFS 2003:13 → SOSFS 1984:32, SOSFS
2009:28 → SOSFS 2006:17 and SOSFS 1973:3, HSLF-FS 2018:54 → SOSFS 2005:29.

### 5. Titles

Compared all 412 Socialstyrelsen documents whose designation is also on the
current site list (site title = the list entry's `name`, with the leading
designation stripped) plus a random sample of 10 read by eye. 4 confirmed
defects:

| designation | we hold | site says |
|---|---|---|
| SOSFS 1996:21 | `SOSFS 1996:21` (the label, no title) | "Socialstyrelsens föreskrifter och allmänna råd om rätt för barnmorskor att förskriva läkemedel i födelsekontrollerande syfte" |
| HSLF-FS 2016:53 | "Socialstyrelsens föreskrifter HSLF-FS om ändring i föreskrifterna (SOSFS 2005:29) om 2016:53 utfärdande av intyg inom hälso- och sjukvården m.m.; Utkom från trycket beslutade den 25 maj 2016. Socialstyrelsen föreskriver med stöd av 2 § 1 förordningen (1985:796) ... [continues for the whole enacting formula and bemyndigande clause]" | "Socialstyrelsens föreskrifter om ändring i föreskrifterna (SOSFS 2005:29) om utfärdande av intyg inom hälso- och sjukvården m.m." |
| HSLF-FS 2020:17 | "Socialstyrelsens allmänna råd HSLF-FS om tillämpningen av förordningen (2020:163) om 2020:17 tillfälligt förbud mot besök i särskilda boendeformer Utkom från trycket för äldre för att förhindra spridningen av sjukdomen den 24 april 2020 covid-19" | "Socialstyrelsens allmänna råd om tillämpningen av förordningen (2020:163) om tillfälligt förbud mot besök i särskilda boendeformer för äldre för att förhindra spridningen av sjukdomen covid-19" |
| HSLF-FS 2021:10 | "Socialstyrelsens föreskrifter HSLF-FS om ändring i föreskrifterna och allmänna råden 2021:10 (HSLF-FS 2017:80) om legitimation för yrke inom Utkom från trycket hälso- och sjukvården vid utbildning från tredjeland; den 11 februari 2021" | "Socialstyrelsens föreskrifter om ändring i föreskrifterna och allmänna råden (HSLF-FS 2017:80) om legitimation för yrke inom hälso- och sjukvården vid utbildning från tredjeland" |

All four show the same shape: the document's own designation number and/or
the "Utkom från trycket ... den <date>" masthead line is spliced into the
middle of the title, instead of being recognised as furniture and removed.

### 6. Consolidations

Site publishes a dedicated "konsoliderade föreskrifter" index (76 rows).

    grep -c files.consolidation in downloaded/foreskrift/{hslffs,sosfs}/*.json.br (publisher=Socialstyrelsen)
    -> 81 download records carry at least one consolidation file

No defect: we hold consolidations, roughly matching the site's own count (the
small excess is expected — some base acts have more than one consolidation
snapshot on disk over time).

### 7. Inherited series: sosfs

    SELECT count(*) FROM documents WHERE kind='sosfs'   -- 223 (222 Socialstyrelsen, 1 Rättsmedicinalverket)

All correctly sit under the `sosfs` slug (`documents.kind='sosfs'`,
`uri` like `https://lagen.nu/sosfs/...`), never renumbered into `hslffs`.
All 153 SOSFS designations the site's publication list currently carries are
present in our 223. The other 69 we hold are older SOSFS documents the site
no longer lists — the same repeal-extraction defect from section 4 explains
most of them (their repealer's `upphaver` link is missing, not that the
repeal doesn't exist).

### 8. Freshness

    newest site HSLF-FS: 2026:32 ("... vävnadsprover i PKU-biobanken")
    newest ours   HSLF-FS: 2026:32                                    -- match
    newest site SOSFS:    2015:22 (an upphävande notice)
    newest ours   SOSFS:  2016:48                                     -- we hold one later than the site currently shows, not a gap
