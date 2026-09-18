# NFS — Naturvårdsverkets författningssamling, Naturvårdsverket

Verdict: DEFECT
Site list: 439 designations from 1 page each for two filter states (215 "Gällande" + 224 "Upphävda"), entry https://www.naturvardsverket.se/lagar-och-regler/foreskrifter-och-allmanna-rad/
We hold: 279 documents (nfs) + 32 (snfs), newest NFS 2026:8; site newest NFS 2026:8 (freshness OK)
Missing: 126 confirmed (88 NFS + 38 SNFS) — repealed regulations behind the "Upphävda" filter, which the harvester never queries. Examples: NFS 1999:7, NFS 1999:8, NFS 1999:9, NFS 2000:2, NFS 2000:4, SNFS 1980:1, SNFS 1983:2, SNFS 1984:10, SNFS 1986:3, SNFS 1987:15
Extra: 24 — 13 fit issue #50's pattern (SNFS PDF minted as NFS, e.g. NFS 1987:12/13/14, 1987:6/7, 1989:11, 1990:11, 1994:2, 1996:14/15, 1998:4) plus 3 more with a new fabrication (see below: NFS 1991:2/1992:1/1993:6). The remaining 11 (NFS 2010:14, 2016:5, 2017:2, 2019:2, 2019:3; SNFS 1990:2/3/8, 1994:4, 1997:7) carry pre-redesign `naturvardsverket.se/Stod-i-miljoarbetet/...` source URLs and are not returned by the new site's search API at all (legacy import, not on the current site under either filter) — legitimate, no action.
Repeal gaps: 38 of 82 held-and-site-revoked documents carry no `rpubl:upphaver` link. Spot-checked: the repealing act is consistently one of the 126 missing designations above (e.g. NFS 2020:3 has no repealer in our corpus and NFS 2020:3 is itself an amendment now shown revoked on the site). This is fully explained by the harvest gap, not a separate parse bug.
Title defects: 0 of 12 sampled (NFS 2025:3 .. NFS 2026:8) — our titles match the site's `heading` field verbatim.
Consolidations: site yes (PDF filenames include "-konsoliderad-"), we hold 23 of 276 download records with `files.consolidation` (proportionate to how many base regs were later amended; no defect found)
Inherited: snfs — 32 held, all inherited SNFS documents sit correctly under the `snfs` slug (not renumbered into nfs); 10 of them are also wrongly duplicated under `nfs` per #50 (see Extra); one further SNFS document (SNFS 1992:12, a "bilavgaskontroll" grundföreskrift) sits behind the revoked filter with a null `nfsText` field and is missing entirely — folded into the 126-count above.
Issue: https://github.com/staffanm/ferenda/issues/80

## Evidence

### Catalog counts
    .venv/bin/python -c "sqlite3 catalog.sqlite ... count(*) where kind='nfs'"   -> 279
    ... where kind='snfs'                                                        -> 32
    newest nfs: NFS 2026:8 (2026-06-25); newest snfs: SNFS 1998:7

### Enumeration — the revoked-facet gap
The agency config's `api_url` (ferenda/foreskrift/agencies.py:135) is:
    https://www.naturvardsverket.se/api/naturvardsverket/regulation/search/?s=500&id=7925&lang=sv
Calling it (as `json_enumerate` does) returns `searchModel.numberOfHits=215`,
`searchModel.totalHits=439`. The facet block in the same response:
    {"key":"RevokedDate","facetOptions":[
      {"key":"all","hits":439},{"key":"valid","hits":215,"selected":true},
      {"key":"revoked","hits":224}]}
The default query silently applies `RevokedDate=valid`. Reproduced the
"Upphävda" click in headless Chromium and captured the real network request:
    https://www.naturvardsverket.se/api/naturvardsverket/regulation/search/?facets=RevokedDate:revoked&p=1&s=12&id=7925&lang=sv
Replaying it with `s=500` returns `numberOfHits=224`, matching the facet count
(215+224=439=totalHits). The harvester's `api_url` has no `facets=` parameter
and therefore never sees these 224 documents. This is the same shape as
AFS #45 / EIFS #51 (an archive of revoked documents outside the harvest's
reach), for the `nfs` scope specifically.

Diffing the 224 revoked designations against `documents.label` for kind in
('nfs','snfs') leaves 141 designations we never captured: 88 `NFS *`, 38
`SNFS *`, 13 `AR *` (see below), 2 `RR *` (an older advisory series, out of
scope for a "föreskrift" — not counted as a defect).

### A second, distinct fabrication: "AR" (Allmänna råd) documents
`ferenda/foreskrift/harvest.py`'s `ref()` selects the designation with
`RE_FS_NUMBER = r"\b([A-ZÅÄÖ-]+FS)\s*(\d{4}):(\d+)"` first. Naturvårdsverket's
site also carries a small pre-1994 series numbered "AR" (Allmänna råd —
general advice, not a föreskrift), whose designation text does not end in
"FS" and never matches `RE_FS_NUMBER`. `ref()` falls through to the bare
`RE_COLON_NUMBER = r"(\d{4}):(\d+)"`, which matches the "1991:2" inside
"AR 1991:2", and then stamps `agency.fs.upper()` ("NFS") as the designation
prefix since no fs override applies. Three documents in our corpus carry a
designation the agency never printed:

| our basefile | identifier we minted | site's own designation | site url |
| --- | --- | --- | --- |
| `nfs/1991:2` | NFS 1991:2 | AR 1991:2 | .../1991/ar-912/ |
| `nfs/1992:1` | NFS 1992:1 | AR 1992:1 | .../1992/ar-921/ |
| `nfs/1993:6` | NFS 1993:6 | AR 1993:6 | .../1993/ar-936-/ |

Confirmed against the artifact and download record, e.g.
`site/data/artifact/foreskrift/nfs/1991-2.json` has `"identifier": "NFS 1991:2"`
and `"source_url": ".../1991/ar-912/"`; the corresponding download record has
`"files": {"regulation": null, ...}` — no PDF was ever fetched for it either.
Six further 2-digit-year AR designations (AR 97:5, 97:1, 96:3, 89:5, 96:2/4,
97:2/3/4, 93:7, 99:4/5, 94:2, 95:4) don't match `RE_COLON_NUMBER` (needs a
4-digit year) at all and are silently dropped — not a fabrication, but also
not captured under any designation.

    .venv/bin/python -c "re.compile(r'\b([A-ZÅÄÖ-]+FS)\s*(\d{4}):(\d+)', re.I).search('AR 1991:2')"  -> None
    .venv/bin/python -c "re.compile(r'(\d{4}):(\d+)').search('AR 1991:2')"                            -> matches '1991:2'

### Repeal gaps sample
    NFS 2020:8 -> repealed by nfs/2023:13 (rpubl:upphaver present, OK)
    NFS 2018:7 -> repealed by nfs/2020:9 (OK)
    NFS 2020:3 -> no rpubl:upphaver row; NFS 2020:3 is itself listed "Upphävda"
                  on the site and is an amendment to NFS 2004:4 — its repealer
                  is one of the 126 missing designations above.

### Titles (10+ sampled, all matched)
NFS 2026:8, 2026:7, 2026:6, 2026:5, 2026:4, 2026:3, 2026:2, 2026:1, 2025:6,
2025:5, 2025:4, 2025:3 — `documents.title` equals the site's `heading` for
every one (checked via the same search API's `heading` field vs. our
catalog).

### Consolidations
    grep files.consolidation across site/data/downloaded/foreskrift/nfs/*.json.br
    -> 23 of 276 download records carry a consolidation PDF; site PDF names
       carry "-konsoliderad-" confirming the practice exists. No systemic gap
       found (not every base regulation has been amended).

### Extra (ours, not on the current site under either filter)
24 designations. 13 are #50-shaped (SNFS/AR content served under an NFS
number pointing at a `snfs-*`/`ar-*` URL). The other 11 all carry
`naturvardsverket.se/Stod-i-miljoarbetet/Rattsinformation/...` source URLs —
the pre-redesign site structure, from a legacy import that predates the
current search API and was never re-matched against it. Treated as
legitimate per the briefing (§3), not filed.

### HTTP budget
Agency site: 2 direct API GETs to establish the facet split, ~9 parameter
probes to find the working `facets=` syntax, ~5 headless-Chromium page loads
(cookie banner, opening the dropdown, capturing the real XHR) to recover the
exact working query, plus 2 landing-page fetches for the consolidation check.
No blocks encountered (no 403/WAF/captcha).
