# RGKFS — Riksgäldskontorets författningssamling, Riksgälden
Verdict: DEFECT
Site list: 28 designations (grundförfattningar + ändringsförfattningar) from 1 page (https://www.riksgalden.se/sv/press-och-publicerat/foreskrifter/), cross-checked against Riksgälden's own författningsförteckning (2026-01-12, 10 pages, 28 entries)
We hold: 26 documents (rgkfs), newest RGKFS 2025:1; site newest RGKFS 2025:2
Missing: 3 — RGKFS 2020:2, RGKFS 2022:3, RGKFS 2025:2 (all in force, printed on their own PDFs and listed in the January 2026 förteckning)
Extra: 0
Repeal gaps: 0 — RGKFS 2015:3 correctly links rpubl:upphaver to RGKFS 2008:1
Title defects: 1 minor (NBSP in the mislabeled 2006:2 record's title), otherwise clean across all 26
Consolidations: site no (Riksgälden reprints as "omtryck", never a separate konsoliderad PDF), we hold 0
Inherited: none (per assignment)
Wrong designation: 2 — rgkfs/2006:2 should be rgkfs/2006:1; rgkfs/1990:978 is not an RGKFS document at all, it is SFS 1995:343
Issue: https://github.com/staffanm/ferenda/issues/84

## Evidence

### 1. Enumerate
Fetched the single index page with `ferenda.lib.net.request`. No pagination
markup found (no "sida", "ladda fler", "load more", `page=` parameter).
49 `a[href$=".pdf"]` anchors: 1 författningsförteckning, ~20 beslutspromemoria
(companion documents, correctly excluded by `skip_re`), 28 regulation/
amendment PDFs. Cross-checked the full designation list against Riksgälden's
own författningsförteckning (fetched 2026-01-12 edition), which independently
confirms all 28 designations, their bemyndigande, and which are ändrings- vs
grundförfattningar.

### 2/3/8. Missing documents and freshness
Fetched the three PDFs whose site link text carries no own designation
(byte-identical caption template "... i Riksgäldskontorets föreskrifter
(RGKFS 2016:2)/(RGKFS 2011:2) om ..."). `pdftotext -layout` on each prints
its own designation at the top of page 1:

    rgkfs-2020-2.pdf  -> RGKFS 2020:2  (ändring i RGKFS 2016:2, beslutad 2020-10-15)
    rgkfs-3-2022-tillg.pdf -> RGKFS 2022:3 (ändring i RGKFS 2011:2, beslutad 2022-12-12)
    rgkfs-2-2025-tillg3.pdf -> RGKFS 2025:2 (ändring i RGKFS 2016:2, beslutad 2025-10-22)

None of the three appear in `catalog.sqlite` under any rgkfs label. The
förteckning (2026-01-12) lists all three as gällande ändringsförfattningar,
confirming they are not later repeals of each other. RGKFS 2025:2 is also
the site's newest designation — our newest held document (RGKFS 2025:1) is
one document behind.

Likely cause: the shared `ref()` helper in `ferenda/foreskrift/harvest.py`
picks a document's own (year, lopnummer) via `RE_FS_NUMBER.search(ident_text)`,
which returns the *first* "XXXFS YYYY:N" match in the link text. When an
amendment's caption names only the base regulation ("... i Riksgäldskontorets
föreskrifter (RGKFS 2016:2) om insättningsgaranti") and never states its own
number, `ref()` takes the base's designation as the document's own, producing
a basefile that collides with the base regulation (or another wrongly
identified amendment) and gets silently dropped as an "already seen"
duplicate. The same mechanism is filed for a different agency in #72
("memyfs/mprtfs enumerate swaps a document's own number with the base it
amends"); this is a separate instance of the same code-level bug, confirmed
against RGKFS's own site and PDFs.

### 4. Repeal marking
    sqlite3 site/data/catalog.sqlite "select from_uri, predicate, to_uri from links
      where predicate='rpubl:upphaver' and (from_uri like '%rgkfs%' or to_uri like '%rgkfs%')"
    rgkfs/2006:2 -> rgkfs/2003:1   (RGKFS 2003:1 predates our RGKFS coverage; not on
                                     today's site, which lists only gällande text --
                                     not a corpus gap under the repeal model)
    rgkfs/2015:3 -> rgkfs/2008:1   (correct: RGKFS 2015:3's whole content is the
                                     upphävande of RGKFS 2008:1; documents.upphavande=1
                                     is set on 2015:3)

### 5. Titles
Compared all 26 `documents.title` rows against the site's link text /
förteckning. 25 match cleanly. One has a stray NBSP: rgkfs/2006:2's title
reads "... allmänna råd till\xa0förordning (2006:1097) ..." instead of a
plain space. This record is the same one carrying the wrong designation
(see below), so it is described there rather than filed as a separate title
defect.

### 6. Consolidations
    for f in downloaded/foreskrift/rgkfs/*.json.br: files.consolidation == []
0 of 26 download records carry a consolidation. The site never links a
konsoliderad PDF; amendments are folded into "omtryck" reprints of the base
(e.g. RGKFS 2011:1 is explicitly marked "(omtryck)"). Not a defect.

### 7. Inherited series
None listed for this assignment. No predecessor-slug documents found mixed
into rgkfs.

### #76 cross-check
`gh issue view 76` already lists `rgkfs/2006:2` (pdftotext prints "2006:1").
Verified independently below; not re-filed, cited in the new issue instead.
`rgkfs/1990:978` is NOT in #76's list -- its PDF prints "SFS 1995:343", a
different fs prefix, which #76's same-prefix regex apparently does not match.
This is a distinct, unnamed cause, so it is filed here.

    for f in downloaded/foreskrift/rgkfs/*-regulation.pdf:
      pdftotext -layout "$f" | grep -oE "RGKFS [0-9]{4}:[0-9]+|SFS [0-9]{4}:[0-9]+" | head -1
    rgkfs-1990-978 -> SFS 1995:343      (catalog label: RGKFS 1990:978)
    rgkfs-2006-2   -> RGKFS 2006:1      (catalog label: RGKFS 2006:2)
    (all other 24 PDFs print exactly the label we store)

### Riksgälden's older föreskrifter and the SFS question (task note)
Confirmed: Riksgäldskontoret's föreskrifter, before the RGKFS series began
(circa 2003), were published inside Svensk författningssamling. Our record
`rgkfs/1990:978` (identifier "RGKFS 1990:978", title "Riksgäldskontorets
föreskrifter (SFS 1990:978) om inskrivning i statsskuldboken") is in fact
**SFS 1995:343**, an ändringsförfattning to the grundförfattning SFS
1990:978. Riksgälden's own författningsförteckning lists it exactly this
way:

    Grundförfattningar
    Riksgäldskontorets föreskrifter (1990:978) om inskrivning i statsskuldboken
    Ändringsförfattning
    SFS 1995:343 (omtryck)

We already hold the grundförfattning correctly, under the sfs vertical:
`https://lagen.nu/1990:978` (kind=forordning, label "SFS 1990:978",
path artifact/sfs/1990/978.json). Its amending act, SFS 1995:343, is held
nowhere else in the corpus -- the only copy of its text is misfiled under
`foreskrift/rgkfs/1990-978.json` with a fabricated RGKFS identifier the PDF
never prints. The `rgkfs` slug is wrong for this document: it was never
issued under an RGKFS number.
