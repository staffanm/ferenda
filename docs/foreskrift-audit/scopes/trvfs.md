# TRVFS — Trafikverkets författningssamling, Trafikverket
Verdict: DEFECT
Site list: 151 grundföreskrifter (Upphavda=false filter) from 1 page — the whole
collection returns from one POST to
https://trvfs.ea.trafikverket.se/TRVFS/Home/SearchInDocCollection (Ar=Alla).
We hold: 142 documents (trvfs), newest TRVFS 2026:1; site newest TRVFS 2026:1
(match). Also 11 documents under vvfs.
Missing: 1 — TRVTFS 2012:3 ("Trafikverkets föreskrifter om bärighetsklasser i
Norrbottens län"), a distinct designation the harvester's routing does handle
in principle (falls through to fs="trvtfs") but no trvtfs documents exist
anywhere in the corpus. Minor, single document.
Extra: 3 not on the site's in-force list — trvfs/1998:111 and trvfs/2003:67
(both repealed 2026-09-01, see Repeal gaps below) and vvfs/2008:418 (the #50
duplicate; legitimately in force, just under the wrong slug pairing).
Repeal gaps: 2 — trvfs/1998:111 (site: "Upphävd av TSFS 2026:82") and
trvfs/2003:67 (site: "Upphävd av TSFS 2026:83"). Neither repealer is in our
tsfs corpus; the newest tsfs we hold is TSFS 2026:63-67 (2026-06-15). Root
cause sits in the tsfs harvest's freshness, not in trvfs.
Title defects: 0 in a 8-document sample against the site's "Rubrik" field
(1996:1, 1996:633, 1997:379, 1998:111, 2003:67, TRVTFS 2012:3, plus two more).
Titles match. (CR/LF-wrapped titles appear but that is the repo-wide artifact
formatting, not a trvfs-specific defect.)
Consolidations: site no, we hold 0. Matches the harvester's own comment that
Trafikverket publishes no konsoliderad versions.
Inherited: vvfs (Vägverket) — 101 of our 142 trvfs documents (71%) print VVFS
or TSVFS on their own PDF masthead, not TRVFS. See "The real defect" below.
Issue: https://github.com/staffanm/ferenda/issues/98

## The real defect: mass single-copy misrouting into trvfs

Issue #50 already diagnosed the root cause for this scope: `trv_enumerate`
routes a document's fs off the register's own DocumentHistory URL prefix (a
bare numeric id -> trvfs, a "VVFS"-prefixed id -> vvfs) instead of reading the
designation the document itself prints, the way `fs_from_designation` does for
scopes that get it right. #50 reported only the symptom visible to a
duplicate-content scan: 2 pairs (trvfs/2008:418 = vvfs/2008:418,
trvfs/2010:38 = vvfs/2010:38) where the register happens to expose the same
document under both a bare and a VVFS-prefixed id.

That scan is blind to the far larger case: a document that sits under only one
id (bare) in the register, and so gets minted only once, under trvfs, even
though the document's own masthead says VVFS or TSVFS. I read pages 1-2 of all
142 stored regulation PDFs with pdftotext and looked for the printed
designation:

    39  print TRVFS <year>:<n>   -- correctly routed (2010 or later)
    93  print VVFS  <year>:<n>   -- Vägverket, wrongly routed to trvfs
     8  print TSVFS <year>:<n>   -- Trafiksäkerhetsverket, wrongly routed to trvfs
     2  no text layer (scanned)  -- trvfs/1991:1, trvfs/1999:165; both dated
                                    before Trafikverket existed, likely also
                                    VVFS or TSVFS but unconfirmed

Trafikverket was created 2010-04-01 (Vägverket's and Banverket's road functions
merged into it). A document dated 1981-2010 cannot be a genuine TRVFS
regulation. Two independent, agency-authored sources confirm the correct
series for the specific documents named in the audit brief:

| basefile (ours) | our title | PDF masthead (page 1) | corroborating text |
| --- | --- | --- | --- |
| trvfs/1996:1 | "Förordning med särskilda bestämmelser om förarbehörighet inom Luftfartsverkets brand- och räddningstjänst m.m" | `VVFS 1996:1` | trvfs/1998:41's own Rubrik: "...i förordningen (VVFS 1996:1)..." |
| trvfs/1996:633 | "Förordning med särskilda bestämmelser om förarbehörighet inom räddningstjänsten" | `VVFS 1996:633` | trvfs/1998:42's own Rubrik: "...i förordningen (VVFS 1996:633)..." |
| trvfs/1997:379 | "Vägverkets föreskrifter (1997:379) om statsbidrag..." | `VVFS 1997:379` | title is self-labelled |
| trvfs/2009:946 | "Bekantgörande i andra hand..." | `VVFS 2009:946` | masthead |
| trvfs/1981:22, 1987:15, 1987:16, 1988:17, 1989:77, 1990:23, 1992:41, 1983:91 | Trafiksäkerhetsverket titles | `TSVFS <year>:<n>` | masthead |

Answering the brief's specific question: TRVFS does not republish SFS-numbered
regulations under its own numbering. trvfs/1996:1 and trvfs/1996:633 are
central-government "förordningar" (signed by a minister, "Regeringen
föreskriver") that carry no SFS number at all — the law that created them
directs "Denna förordning skall kungöras i Vägverkets författningssamling",
so VVFS is their only designation. The "Bekantgörande i andra hand" documents
(trvfs/2009:11-28, 2009:946, 2010:3 etc.) are short notices, required whenever
an SFS-numbered act amends or repeals a document already published in an
agency's FS; every one I checked (2009:946, plus the masthead scan above)
prints VVFS, not TRVFS. None of this belongs under the trvfs slug — all of it
is Vägverket's own författningssamling, wrongly minted under its successor's
slug because the register's bare-vs-prefixed URL convention is not a reliable
signal for which agency issued the document.

Net: 101 of 142 stored "trvfs" documents (99 after excluding the 2 already
named in #50) should sit under vvfs (93) or a tsvfs slug the pipeline does not
register at all (8). tsvfs holds 0 documents anywhere in the corpus today.

## Evidence

    # index (1 POST, whole collection at once)
    trv_enumerate's own POST, Ar=Alla -> 669 <a href="/DocumentHistory/...">,
    669 unique hrefs, 534 bare, 39 "VVFS"-prefixed, 1 "TRVTFS"-prefixed.
    Grundföreskrift-only rows: 151 (140 route to trvfs, 10 to vvfs, 1 unrouted
    "trvtfs" designation with no matching corpus fs).

    # our holdings
    sqlite3 file:site/data/catalog.sqlite?mode=ro
      select count(*) from documents where kind='trvfs';  -- 142
      select count(*) from documents where kind='vvfs';   -- 11
      select count(*) from documents where kind='tsvfs';  -- 0

    # masthead scan (no network; all PDFs already on disk)
    for f in site/data/downloaded/foreskrift/trvfs/*-regulation.pdf; do
      pdftotext -l 2 "$f" - | grep -oE '(TRVFS|VVFS|TSVFS) [0-9]{4}:[0-9]+'
    done
    # -> 39 TRVFS, 93 VVFS, 8 TSVFS, 2 blank (scanned)

    # confirms the two named amending documents cite VVFS explicitly
    trvfs/1998:41  metadata.title: ...i förordningen (VVFS 1996:1)...
    trvfs/1998:42  metadata.title: ...i förordningen (VVFS 1996:633)...

    # repeal gaps
    DocumentHistory/1998-111 -> "Upphävd 2026-09-01", "Upphävd av TSFS 2026:82"
    DocumentHistory/2003-67  -> "Upphävd 2026-09-01", "Upphävd av TSFS 2026:83"
    select max(label) ... where kind='tsfs' -- newest held is TSFS 2026:63-67
    (2026-06-15); 2026:82/83 not present.

    # missing TRVTFS entry
    DocumentHistory/TRVTFS2012-3 -> Rubrik "Trafikverkets föreskrifter
    (TRVTFS 2012:3) om bärighetsklasser i Norrbottens län"; no trvtfs fs
    registered, no such document anywhere in site/data.

HTTP requests used: 1 POST (index) + 8 GET (family pages: 1996-1, 1996-633,
1997-379, 1998-111, 2003-67, TRVTFS2012-3) = 9 of the 60-request budget. No
block encountered. Every regulation PDF used for the masthead scan was already
on disk from a prior harvest; none were re-fetched.
