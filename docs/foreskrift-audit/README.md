# Föreskrift source audit, September 2026

Every one of the 74 live föreskrift harvest scopes was compared against the
agency's own listing. This is what was found, what has been fixed, and what is
left. The per-scope evidence is in [`scopes/`](scopes/), one file per scope; the
instructions each review followed are in [`method.md`](method.md).

This file is the handover record for the audit and the 2026-09-14 fix
pass. The per-scope reviews in `scopes/` are as written at audit time and
are not updated; where this README and a scope file disagree, this file is
the later measurement.

## What was done

One review per scope, five at a time. Each review fetched the agency's entry
page, enumerated the samling the way the scope's own code does, and compared
that list against the corpus. Eight checks: missing documents, extra documents,
repeal marking, titles, consolidations, inherited predecessor series, freshness,
and the enumeration itself.

Three checks ran over the whole corpus rather than per scope, because they only
mean anything at that scale:

- Every stored regulation PDF was read with `pdftotext` and its printed number
  compared with the number we minted. 11 069 of 11 523 agree.
- The same files, compared on the printed *series name*. That is the check the
  number-only one cannot make: a document filed under the wrong series with the
  right number passes it.
- Byte-identical PDFs under two designations, which is always wrong.

## Where it stands, 2026-09-14

The audit filed 67 issues, #41 to #107. A fix pass on 2026-09-14 addressed them
as five shared changes rather than 67 patches, then ran
`lagen foreskrift all --force` over all 80 scopes.

The corpus grew from 12 470 föreskrifter to 13 699.

| measure | before the fix pass | after |
| --- | ---: | ---: |
| documents | 12 470 | 13 699 |
| repeal gaps (title names a repeal, no target) | 245 | 185 |
| titles: body text but no title | 277 | 218 |
| titles: chrome, body dump, leading designation | 97 | 43 |
| titles: no title and no body at all | 42 | 149 |

The last row went up, and the reason is the archives. 3 249 documents that were
unreachable before are now held, and 107 of them carry no usable PDF. That is a
harvest gap in documents the corpus could not see at all before, not a
regression in anything that worked.

### What the archives delivered

Per-scope, against each issue's own prediction:

| issue | scope | issue said missing | measured |
| --- | --- | ---: | ---: |
| #94 | stafs | 206 | +206 |
| #67 | livsfs | 354 | +368 |
| #79 | prvfs | 109 | +108 |
| #95 | stemfs | 16 | +82 |
| #77 | pmfs | 53 | +53 |
| #74 | myhfs | 19 | +25 |
| #71 | mcffs | 178 | +13 mcffs, +72 msbfs, +77 säifs, +88 srvfs |

The mcffs row is `fs_from_designation` working as designed: MSB's archive is
mostly MSBFS, SÄIFS and SRVFS documents, and each one is stored under its own
samling rather than renumbered into mcffs. The same rule put #96's TRMFS 2017:2
and 2017:3 under `trmfs`, and #44's two Bostadsstyrelsen documents under `bofs`.

### Three claims in this README were wrong

The fix pass disproved three things the audit asserted. They are corrected here
because the next reader should not re-derive them:

- **stafs was never covered by the committed `archive_links` work.** Its
  `link_select` was `a[href*="/foreskrifter/swedac/stafs-"]`, and its archive
  rows sit at `/foreskrifter/swedac/upphavda/stafs-…`, which never matched.
- **eifs still cannot use the archive.** `archive_links` reaches the page, but
  the archive's cells are `/download/…pdf` links and `resolve_landing` needs a
  landing page.
- **The repeal parser should not be scoped to the ikraftträdande block.** The
  audit proposed it. A repeal is declared in four places — the ingress, the
  title, a decision sentence and the transitional block — and only the last
  survives that scoping, while 1 294 documents already read correctly.

### One defect the audit missed

Every SOSFS cover prints "Socialstyrelsen ger årligen ut en förteckning över
gällande föreskrifter". `RE_FORTECKNING` read that as the document declaring
itself a förteckning, which disarmed the repeal step for all 58 cached SOSFS
documents. That, not the run-on #58 hypothesised, is why `sosfs/2008:17`
recorded nothing.

### The archive is unreachable on an incremental run

`HarvestWatermark.should_stop` (`lib/harvest.py:154`) treats the first listing
row that is old and already on disk as conclusive, and `walk`
(`lib/harvest.py:419`) then breaks and abandons the enumerator. An agency's
archive is queued *after* its in-force listing, so an incremental run never
reaches it. Measured: `lagen foreskrift download stafs` without `--force` makes
one HTTP request, sees 5 items, and never fetches the archive URL.

Production now runs `lagen foreskrift download --force` monthly (1st, 06:00) for
this reason. The general form — exposing `walk`'s existing `deep` mode as a
flag — is [issue #110](https://github.com/staffanm/ferenda/issues/110).

### Records removed

Three records were never föreskrifter and are gone: `kovfs/2021:1` (a search
results page), `sifs/2018:8` (a vägledning for LIFS 2018:8) and
`memyfs/2024:954` (SFS 2024:954). Each is now refused at enumerate, so none
returns. `lagen foreskrift reap` then removed 48 records that belong under a
predecessor samling.

A fix that stops minting a bad document does not remove the one already minted,
and `reap` only handles a samling reassignment. Removing those three took a
manual delete plus a `relate` re-run.

### What is still open

Named, with the measured reason:

- **eifs, kvfs, migrfs** cannot reach their missing documents: direct PDF links
  where a landing page is needed, a Sitevision component that needs the browser
  transport, and a register published as an .xlsx.
- **uhrfs** finds no archive page — `archive_links` returns three PDFs.
- **trvfs** cannot be fixed at enumerate. The register prints no series at all,
  and all 142 stored PDFs were checked: 1986:1 prints VVFS, 1987:15 and 1987:16
  print TSVFS, and 2010 holds both. Only the masthead decides.
- **sjofs Bekantgörande notices** (68 documents) and **ssmfs titles** need a
  per-scope masthead rule passed as data. `running_furniture` removes ssmfs's
  own title continuation lines, and requiring three pages to fix it cost eight
  SOSFS titles.
- **The rpsfs FAP margin code** prints on page 1 only, so `running_furniture`
  never sees it; a shape rule would also delete "EN 1090-1" from a real title.
- **#69 lmfs** needs a change in `lib/pdftext.py`, which seven sources share. The
  proposed patch re-adds what a page sets below a dotted region. Measured over
  40 sampled documents per source, it re-adds footer chrome to rs
  (`imy@imy.se`, `Telefon:`) and real table-of-contents lines to lawreview,
  whose leaders use `…` rather than four dots. It buys 4 documents and was not
  applied.

## The 2026-09-16 pass

Eleven commits, `fc0cc7fa` through `bbe4efb8`. The work went in four steps.

### Step 1 — a listing row that links the document, not a landing page

25 documents stored an empty `regulation` slot and had no text at all.
Energimyndigheten's archive rows link the PDF directly, where every other
agency links a landing page to scrape. `resolve_landing` decoded PDF bytes as
HTML, found no anchor, and stored nothing. It now sniffs the response bytes.
21 stemfs and 4 nutfs, proven live on stemfs/2005:10 and nutfs/1998:3.

### Step 2 — three repeal-reading fixes

+12 repeal relations, 0 lost, measured by isolation over 3 103 documents.

- **The furniture removal ate references inside brackets.**
  `running_furniture` returns what a document prints on more than one page,
  which for many agencies is its own designation ("SJVFS") or a page number.
  The masthead cleanup replaced every occurrence, including inside the title's
  own bracket: SJVFS 2019:1's "(SJVFS 2004:39)" read "( 2004:39)" and LIVSFS
  2005:10's "(SLVFS 2001:30)" read "(SLVFS 2001:0)". Removal now happens
  outside brackets only. 20 titles repaired.
- **The second column splits a repeal target in half.** FFFS 2017:19 prints
  "Utkom från trycket den 17 november 2017" beside its title, and the joined
  text reads "(FFFS den 17 november 2017 2011:37)". `extract_metadata` now
  reads the repeal noun form from the cleaned masthead as well as the raw one
  -- that form only, because the same cleaning mangles a masthead whose
  furniture is table debris (LVFS 2011:16).
- **Migrationsverket numbers its föreskrifter "N/YYYY".** "Migrationsverkets
  föreskrift (19/2010)" is MIGRFS 2010:19. Declared per series in
  `series.json` as `number_form`, never widened into the shared pattern: the
  same spelling is how an EU act is cited, and the unguarded form read MSBFS
  2018:3's "EU-förordning (305/2011)" as a föreskrift.

### Step 3 — the masthead title, and a scan whose only text is a stamp

+66 titles, 0 lost, 20 changed and every change a repair.

- **A title needs an ending, not a stop word.** 95 documents print a whole
  masthead title and neither a semicolon nor a decision clause -- every
  Arbetarskyddsstyrelsens kungörelse, and Swedac's konsoliderade texter.
  `title_from_masthead` now reads in two passes: the printed stop first, a
  plain sentence end second. Second, not first, because a masthead often
  carries a standing sentence with a type word in it ("I (AFS) publiceras
  myndighetens föreskrifter och allmänna råd"), and admitting it in one pass
  published that instead of the title for 50 documents. Either pass still
  requires an ending: 37 of the 67 documents the weaker ending reaches print
  none, and they came out as run-ons -- AFS 1993:2's own title twice,
  truncated mid-word at 333 characters.
- **A stamp is not a text layer.** MSB republishes its predecessors'
  regulations as copier scans -- page 1 is a single 200 ppi image -- with an
  "[UPPHÄVD]" stamp laid over it. That stamp is the PDF's only text object;
  `pdffonts` lists one font, the Garamond-Bold it is set in. The layer was
  therefore not empty and the OCR fallback never fired, so 28 documents
  published the stamp as their whole body. `lib.pdftext.only_furniture` judges
  a multi-page layer on its lines: a document of its own prints something on
  one page that it does not print on the next. It lives in `lib/` because
  `ferenda/icc/render.py:30` already documents the same defect for a court
  stamp, and `pages_with_ocr` now uses it too.

### Step 4 — five agencies' archives and registers

| issue | scope | what it was, and what it is now |
| --- | --- | --- |
| #105 | uhrfs | `archive_links` took three repealing föreskrifter for the archive and `ARCHIVE_MAX` cut the real listing off the queue. An anchor whose href names a document file is no longer a candidate. 35 designations and 13 consolidations reachable. |
| #98 | trvfs | The register's id says nothing about the samling, so 103 of 142 documents sat under trvfs while their masthead prints VVFS or TSVFS. The Rubrik names the issuing agency, which agrees with all 142 stored mastheads. 101 documents relocated on disk. |
| #68 | kvfs | The listing is a Blazor component with no HTTP shape; the sitemap names all 119 landing pages. 39 documents we did not hold, KVFS 2008:16 among them -- its page links KVFS 2008:18's file, so the audit's dedup lost the row. |
| #75 | migrfs | The `MIGRFS 5/2011` row is read rather than skipped. The 85 missing documents are an upstream loss; see below. |
| #51 | eifs | The archive has been fetched every run since `a2f510e2`, but its rows link the `/download/` PDF where a landing page was expected, so the selector matched 1 anchor of 122. Live enumerate 36 refs -> 109. |

### Two corpus repairs, done by hand

- **trvfs.** 101 documents, 305 files, into `vvfs/` (92) and a new `tsvfs/`
  (9). Two byte-identical duplicates dropped. The manifest was rebuilt from
  the stored PDFs rather than taken on trust: 94 VVFS, 39 TRVFS, 9 TSVFS, every
  one read from its own printed number. `reap` does not cover this shape --
  measured, `superseded()` returns 0 -- and two of the documents are repealed
  and off the listing, so a re-download would have stranded them under trvfs
  for ever. Move first, then the code: enumerate's `is_downloaded` checks the
  record path, so the other order re-downloads 100 documents.
- **kvfs.** 11 records held a konsoliderad PDF in the `regulation` slot, under
  the name `kvfs-YYYY-N-regulation.pdf` -- one name for two different
  versions. Each file is renamed `-consolidation-0.pdf` and moved to the
  `consolidation` slot; `regulation` waits for the as-published text.
  `kvfs_resolve` carries a stored consolidation through a re-resolve, because
  Kriminalvården has unlisted all eleven and they answer only at the urls our
  own records remember.

Backups: `scratchpad/trvfs-move-backup.tar`,
`scratchpad/kvfs-consolidation-backup.tar`.

### Two parse findings, applied 2026-09-16 in 5d974b67 and 5104b31f

Both touch `parse.py`, so either one re-parses the whole föreskrift corpus.
Measured by isolation over 6 369 documents, HEAD against the pair: **4
documents gain a repeal target, 2 lose one, and each loss is paired with a gain
on the same document** -- those two are the corrections themselves. No
amendment relation and no title changes anywhere.

**A bare reference standing after another agency's name.** A föreskrift often
names a regulation by agency plus a bare number in brackets, with no
designation: EIFS 2012:4 prints "Närings- och teknikutvecklingsverkets
föreskrifter och allmänna råd (1995:1)". `RE_BARE_OWN_REF` assumes the agency
in that phrase is the document's own, so the number is read into the
document's own samling and we record `eifs/1995:1` -- a document that has
never existed. The regulation repealed is NUTFS 1995:1.

The evidence is in the sentence and in our data already: the phrase names the
agency, and `series.json` stores every samling's title. Swept over 15 560
artifacts, 8 references stand after a foreign agency name. Five are Riksarkivet
citing "Riksarkivets föreskrifter (2019:12)", where the name owns both `rafs`
and `rams`; refusing an ambiguous name leaves those at today's correct answer.
A wider sweep than the agent's found three false positives its rule would have
created, so each is now a guard: the possessive stands immediately before the
type word and the number immediately after it (RFFS 1993:8 prints "Boverkets
föreskrifter till 19 § lagen (1988:786)", an act; RSFS 1997:5 the same shape),
and no designation stands between them (HSLF-FS 2026:27 prints
"Socialstyrelsens föreskrifter HSLF-FS (2023:33)", whose number is the printed
samling's).

Measured result: EIFS 2012:4 moves to `nutfs/1995:1`, MSBFS 2012:3 to
`srvfs/2006:11` ("Statens räddningsverks föreskrifter (2006:11)"), and MIGRFS
2019:7 gains `sivfs/1999:5` ("Statens invandrarverks föreskrifter (1999:5)").
The ÅFS and TRVFS rows named earlier are the same shape and read correctly now,
but neither document changed in the sample, so they stay unconfirmed.

Issue #51 proposed fixing this by declaring a predecessor chain in
`series.json` instead. That was built and rejected: it requires declaring
STEMFS succeeded by EIFS, and STEMFS is a live samling with 109 records and a
2026 document.

**A repeal list cut short at "ändring i".** When a document repeals a list of
regulations, the parser cuts the list at the first "ändring i". That is usually
right -- in "(KVFS 2007:6) om ändring i … (KVFS 2006:26)" the words open the
*target's own* title, and what follows describes that target rather than naming
a new one. But the same words can open the *next item*: EIFS 2013:7 repeals
EIFS 2010:2 and EIFS 2011:5, and the second is introduced as "och
Energimarknadsinspektionens föreskrifter om ändring i …". The cut fires there
and EIFS 2011:5 is lost.

The rule took four corpus runs to get right, and the first three were wrong in
ways no unit case would have shown. What works: the verb belongs to the
designation last printed unless **another agency's possessive** stands between
the two, and a verb standing before every designation in the item ends it too
(an ändringsförfattning describing itself, naming no target of its own).

The gap's *length* is not the signal, which is what the first two attempts
assumed:

| document | between the bracket and the verb | one document or two |
| --- | --- | --- |
| MIGRFS 2019:8 | `med` | one |
| SKSFS 2012:2 | `och allmänna råd om` | one |
| EIFS 2013:7 | `om elleverantörers skyldighet och Energimarknadsinspektionens föreskrifter om` | two |

Each wrong version minted a base regulation as repealed. The third -- cut only
where a bracket precedes -- dropped the other half of the rule and pulled in
three more: PMFS 2016:19's RPSFS 2007:5 (the base), NFS 2013:12's NFS 2006:9
(the base it amends) and DVFS 1998:1's "expeditionskungörelsen (1964:618)", an
SFS act.

Measured result: 2 documents gain a target, EIFS 2013:7 -> eifs/2011:5 and
SKOLFS 2005:20 -> skolfs/1996:2. The earlier estimate also named SÄIFS 1998:7;
it does not change.

One weakness this exposed and did not fix: `RE_BARE_OWN_REF` reads
"kungörelsen (1964:618)" as a bare reference to the citing document's own
series, so an SFS act in brackets becomes a föreskrift. Nothing in the shipped
rule reaches it, and its extent is unmeasured.

### One upstream loss

Migrationsverket's 85 missing documents cannot be recovered from the
publisher. The register is an `.xlsx` -- readable with the stdlib, no
dependency needed, since an xlsx is a zip of XML -- but it names no url for
anything. Sitevision addresses a file by node id alone, and every delisted
node answers 404, including nodes for documents we do hold (MIGRFS 2013:1,
2003:7). The site's own search API returns 40 hits for "MIGRFS": the 30 PDFs
the page links, the register, and 9 pages. `web.archive.org` has a capture for
42 of the 85; the other 43 exist nowhere. Harvesting the 42 from a third party
is a separate decision -- it puts `web.archive.org` in every record's `url`
instead of the publisher.

## The incidents worth knowing about

**The slug regression.** Making the printed designation decide the samling
routed it through a fold that keeps å, ä and ö, so one run filed 51
Elsäkerhetsverket documents under an `elsäkfs` no registry knows. Fixed by
`harvest.series_slug`, and repaired in the corpus. Four series print a
designation their slug transliterates: ÅFS, RÅFS, SJÖFS, ELSÄK-FS.

**The PDF checks are worklists, not verdicts.** The number check's 108 rows
include whole blocks of false positives: `rfs` documents never print their own
designation, and Skatteverket prints "SKVFS 2013: 18" with a space. Read the
PDF before acting on a row.

## Per-scope results

**held** and every defect count are the audit's own, as each review reported
them. **now** is the document count after the 2026-09-14 fix pass and its
`--force` sweep, measured from the artifacts on disk. A missing count can be
ordinary freshness lag rather than a defect; the scope report says which. A bold
issue number is closed.

Read a rise in **now** against the **missing** column beside it: `stafs` 75 to
281 against 206 missing is the archive arriving.

No rise does not always mean no fix. `fs_from_designation` stores each document
under the samling its own designation names, so a scope whose archive holds
predecessor-series documents sees them land elsewhere. `tppvfs` stays at 1 while
its two recovered documents sit under `trmfs`, and `mcffs` rises by 13 while its
archive delivers 72 to `msbfs`, 77 to `säifs` and 88 to `srvfs`. Where no rise
really does mean the issue is open — `eifs`, `uhrfs`, `kvfs`, `migrfs` — the
reason is in "What is still open" above.

| scope | agency | held | now | verdict | missing | repeal gaps | title defects | issue |
| --- | --- | ---: | ---: | --- | ---: | ---: | ---: | --- |
| `aafs` | Åklagarmyndigheten | 213 | 213 | Ok | — | — | — | — |
| `affs` | Arbetsförmedlingen | 10 | 10 | Ok | — | — | — | — |
| `afs` | Arbetsmiljöverket | 130 | 130 | Defect | 49 | 15 | 18 | #45 |
| `agvfs` | Arbetsgivarverket | 9 | 9 | Defect | 3 | — | 7 | #42 |
| `bfnar` | Bokföringsnämnden | 73 | 73 | Defect | — | 8 | — | **#41** |
| `bfs` | Boverket | 124 | 123 | Defect | 1 | 1 | — | #44 |
| `bolfs` | Bolagsverket | 32 | 32 | Defect | 4 | — | 3 | #46 |
| `csnfs` | Centrala studiestödsnämnden | 13 | 13 | Defect | — | 1 | — | #43 |
| `dvfs` | Domstolsverket | 263 | 263 | Ok | — | — | — | — |
| `eifs` | Energimarknadsinspektionen | 67 | 67 | Defect | 32 | 2 | — | #51 |
| `elsakfs` | Elsäkerhetsverket | 37 | 56 | Defect | — | 1 | 14 | #47 |
| `fffs` | Finansinspektionen | 167 | 167 | Defect | 4 | — | 19 | #53 |
| `ffs` | Försvarsmakten | 177 | 178 | Defect | 1 | 4 | 2 | #48 |
| `fkfs` | Försäkringskassan | 161 | 161 | Defect | — | 61 | — | #49 |
| `hslffs-fohm` | Folkhälsomyndigheten | 663 | 665 | Defect | — | 3 | — | **#54** |
| `hslffs-ivo` | Inspektionen för vård och omsorg | 663 | 665 | Ok | — | — | — | — |
| `hslffs-lv` | Läkemedelsverket | 663 | 665 | Defect | 2 | — | 1 | #55 |
| `hslffs-mfof` | Myndigheten för familjerätt och … | 663 | 665 | Defect | 1 | 1 | 2 | **#102** |
| `hslffs-sos` | Socialstyrelsen | 663 | 665 | Defect | 1 |  | 4 | #58 |
| `hslffs-tlv` | Tandvårds- och läkemedelsförmåns… | 663 | 665 | Defect | 1 | — | 15 | #59 |
| `hvmfs` | Havs- och vattenmyndigheten | 21 | 21 | Defect | 1 | 1 | 9 | #56 |
| `iaffs` | Inspektionen för arbetslöshetsfö… | 7 | 7 | Defect | 66 | 5 | — | #57 |
| `imyfs` | Integritetsskyddsmyndigheten | 4 | 4 | Defect | — | — | 3 | #60 |
| `kamfs` | Kammarkollegiet | 82 | 82 | Defect | — | 1 | 4 | **#62** |
| `kbvfs` | Kustbevakningen | 4 | 4 | Defect | — | — | 1 | **#61** |
| `kfmfs` | Kronofogdemyndigheten | 17 | 17 | Defect | 4 | — | 16 | #64 |
| `kfs` | Kommerskollegium | 4 | 4 | Ok | — | — | — | — |
| `kifs` | Kemikalieinspektionen | 3 | 3 | Defect | 3 | — | — | **#65** |
| `kkvfs` | Konkurrensverket | 11 | 11 | Defect | — | — | 1 | **#103** |
| `kovfs` | Konsumentverket | 49 | 48 | Defect | 1 | — | 8 | #66 |
| `krfs` | Statens kulturråd | 24 | 24 | Defect | — | — | 1 | **#63** |
| `kvfs` | Kriminalvården | 81 | 81 | Defect | 38 | 3 | 10 | #68 |
| `livsfs` | Livsmedelsverket | 34 | 402 | Defect | 354 | — | — | #67 |
| `lmfs` | Lantmäteriet | 93 | 81 | Defect | 1 | 4 | — | #69 |
| `mcffs` | Myndigheten för civilt försvar (… | 12 | 25 | Defect | 178 | — | 17 | #71 |
| `mdffs` | Myndigheten för digital förvaltn… | 6 | 6 | Defect | 5 | — | 3 | #70 |
| `memyfs` | Mediemyndigheten | 6 | 5 | Defect | 8 | 2 | 1 | #72 |
| `migrfs` | Migrationsverket | 122 | 122 | Defect | 85 | 8 | 3 | #75 |
| `mtfs` | Tillväxtanalys | 16 | 16 | Defect | — | — | — | **#73** |
| `myhfs` | Myndigheten för yrkeshögskolan | 60 | 85 | Defect | 19 | — | 17 | #74 |
| `nfs` | Naturvårdsverket | 279 | 265 | Defect | 126 | 38 | — | #80 |
| `pfs` | Pensionsmyndigheten | 115 | 115 | Defect | — | 9 | — | #78 |
| `pmfs` | Polismyndigheten | 124 | 177 | Defect | 53 | 6 | — | #77 |
| `prvfs` | Patent- och registreringsverket | 21 | 129 | Defect | 109 | — | 20 | #79 |
| `ptsfs` | Post- och telestyrelsen | 45 | 47 | Defect | 2 | — | 5 | #81 |
| `rafs` | Riksarkivet | 90 | 90 | Defect | 3 | 5 | 4 | #82 |
| `rams` | Riksarkivet (myndighetsspecifika… | 366 | 366 | Defect | — | 53 | — | — |
| `rfs` | Riksdagsförvaltningen | 274 | 274 | Defect | — | 2 | — | **#83** |
| `rgkfs` | Riksgälden | 26 | 26 | Defect | 3 | — | 1 | #84 |
| `rifs` | Revisorsinspektionen | 14 | 14 | Defect | — | 2 | 4 | #85 |
| `rpsfs` | Rikspolisstyrelsen | 92 | 92 | Defect | — | 30 | 6 | #86 |
| `scbfs` | Statistiska centralbyrån | 202 | 202 | Defect | — | 4 | 1 | #89 |
| `sgufs` | Sveriges geologiska undersökning | 6 | 6 | Ok | — | — | — | — |
| `sifs` | Spelinspektionen | 25 | 16 | Defect | 2 | — | 6 | #88 |
| `sisfs` | Statens institutionsstyrelse | 30 | 30 | Defect | — | — | — | #87 |
| `sjofs` | Sjöfartsverket | 181 | 189 | Defect | 8 | 1 | 94 | #90 |
| `sjvfs` | Statens jordbruksverk | 1459 | 1459 | Ok | — | — | — | — |
| `skolfs` | Statens skolverk | 2557 | 2557 | Defect | — | 556 | — | #91 |
| `sksfs` | Skogsstyrelsen | 56 | 56 | Ok | — | — | — | — |
| `skvfs` | Skatteverket | 547 | 547 | Defect | 13 | — | 9 | #92 |
| `ssmfs` | Strålsäkerhetsmyndigheten | 47 | 48 | Defect | 2 | — | 35 | #93 |
| `stafs` | Styrelsen för ackreditering och … | 75 | 281 | Defect | 206 | 2 | 41 | #94 |
| `stemfs` | Energimyndigheten | 27 | 109 | Defect | 16 | — | — | #95 |
| `stfs` | Sametinget | 33 | 33 | Blocked |  | — |  | — |
| `stkfa` | Statskontoret | 0 | 0 | Ok | — | — | — | — |
| `tfs` | Tullverket | 339 | 339 | Defect | 1 | 2 | 65 | #97 |
| `tppvfs` | Totalförsvarets plikt- och prövn… | 1 | 1 | Defect | 2 | — | — | **#96** |
| `trvfs` | Trafikverket | 142 | 142 | Defect | 1 | 2 | — | #98 |
| `tsfs` | Transportstyrelsen | 839 | 842 | Defect | 3 | 5 | — | #104 |
| `tvfs` | Tillväxtverket | 10 | 10 | Defect | 4 | — | — | **#99** |
| `ufs` | Upphandlingsmyndigheten | 5 | 5 | Defect | — | — | 2 | **#106** |
| `uhrfs` | Universitets- och högskolerådet | 42 | 42 | Defect | 35 | — | — | #105 |
| `valfs` | Valmyndigheten | 17 | 17 | Defect | — | — | — | **#107** |
| `vrfs` | Vetenskapsrådet | 8 | 8 | Ok | — | — | — | — |

Totals across the reports, as the audit found them: 1 451 documents named as
missing over 42 scopes, 838 repeal gaps over 32, and 472 title defects over 38.
Ten scopes were clean.

After the fix pass: 3 249 of those documents are held, repeal gaps are 185 and
title defects 410. Seven more issues are closed — #61, #62, #73, #83, #96, #106
and #107 — each against the stored artifact, not against an agent's claim.

## Consequence, measured

At audit time: 11 983 distinct föreskrift URIs cited somewhere in the corpus
resolved to nothing, across 50 477 citations, the largest being `tsfs` 7 546,
`fffs` 3 507, `skolfs` 3 314, `afs` 3 091 and `sosfs` 3 041.

Re-measured after the fix pass, counting every `links.to_uri` that matches
`https://lagen.nu/<fs>/<year>:<n>` for a samling the corpus has a directory
for, and that no `documents.uri` holds: **12 112 URIs across 51 851
citations**. The largest are `skolfs` 2 098, `tsfs` 1 199, `rams` 1 004,
`fffs` 567, `bfs` 459 and `rffs` 446.

Do not read those two as a before and after. The per-scope shape differs too
much for one method to have produced both — `tsfs` alone moves from 7 546 to
1 199 — so the audit's figure was measured some other way, and how is not
recorded. The second measurement is the one whose method is written down.

What both say is the same thing: our own documents cite thousands of
föreskrifter the corpus does not hold, so a missing document is not invisible
to a reader.
