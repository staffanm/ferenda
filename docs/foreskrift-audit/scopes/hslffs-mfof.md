# hslffs-mfof — HSLF-FS, Myndigheten för familjerätt och föräldraskapsstöd (MFoF)

Verdict: DEFECT
Site list: 6 designations from 2 pages (entry URL 404s; the content moved to
  https://mfof.se/familjeratt/regler-och-riktlinjer/foreskrifter-och-allmanna-rad
  and https://mfof.se/internationella-adoptioner/regler-och-riktlinjer/allmanna-rad-gallande-internationella-adoptioner)
We hold: 6 documents (hslffs, MFoF-published), newest HSLF-FS 2023:3; site newest HSLF-FS 2025:64
Missing: 1 — HSLF-FS 2025:64 ("Nya allmänna råd om vårdnad, boende och umgänge")
Extra: 0
Repeal gaps: 1 — HSLF-FS 2017:51 is superseded by the missing HSLF-FS 2025:64
Title defects: 2 kinds — 1 truncated title (hslffs/2022:25), 2 titles carrying
  a leaked role label (hslffs/2021:64, hslffs/2022:66)
Consolidations: site yes (1 base act), we hold 1 — matches, no defect
Inherited: MIAFS — not published anywhere on the new site (site search:
  "no hits"); we hold 0 miafs documents. Nothing to reconcile.
Issue: https://github.com/staffanm/ferenda/issues/102

## Evidence

### Entry page moved (site relaunch 2026-09-10)

The registered `index_url` in `ferenda/foreskrift/agencies.py` (HSLFFS_MFOF) is
`https://mfof.se/sarskilda-innehallssidor/foreskrifter-och-allmanna-rad.html`.
Both with and without `www.` this now answers HTTP 404 ("Sidan kunde inte
hittas"):

    .venv/bin/python3 -c "
    from ferenda.lib.net import make_session, request
    s = make_session('ferenda-audit/1.0')
    request(s, 'GET', 'https://mfof.se/sarskilda-innehallssidor/foreskrifter-och-allmanna-rad.html')
    "
    # -> requests.exceptions.HTTPError: HTTP 404

MFoF's own front page links a news item dated 2026-09-10,
"Välkommen till nya mfof.se!" ("we relaunched mfof.se with a new structure and
navigation"), confirming this is a real site restructuring, not a transient
outage. Our last successful harvest of this scope predates the relaunch by
8 days:

    stat -c '%y %n' site/data/downloaded/foreskrift/hslffs/hslffs-2023-3.json.br
    # 2026-09-02 16:43:37

The content that used to live at the one static index page is now split
across at least two topic pages found via the site's own search
(`/sokresultatsida.html?query=...`):

- `https://mfof.se/familjeratt/regler-och-riktlinjer/foreskrifter-och-allmanna-rad`
  — vårdnad/boende/umgänge, faderskap/föräldraskap, informationssamtal
- `https://mfof.se/internationella-adoptioner/regler-och-riktlinjer/allmanna-rad-gallande-internationella-adoptioner`
  — internationell adoption (HSLF-FS 2022:25)

### Catalog query (our records)

    .venv/bin/python3 -c "
    import sqlite3
    con = sqlite3.connect('file:site/data/catalog.sqlite?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    for r in con.execute(\"select label, title, date from documents where kind='hslffs' and publisher like '%familjerätt%'\"):
        print(dict(r))
    "

Returns 6 rows: HSLF-FS 2017:51, 2021:64, 2022:18, 2022:25, 2022:66, 2023:3.
All 6 still show up on the (relocated) site, so there are no extra records.

### Missing document and repeal gap

The new family-law page names, as its current text for vårdnad/boende/umgänge:

    "Nya allmänna råd om vårdnad boende och umgänge (HSLF-FS 2025:64) pdf, 253.5 kB."

and repeatedly refers back to our held HSLF-FS 2017:51 in the past tense:

    "I tidigare allmänna råd (2017:51) om socialnämndens ansvar för vissa
    frågor om vårdnad, boende och umgänge fanns rekommendationer om detta..."

(eight such references on the page). HSLF-FS 2025:64 is not in our corpus:

    .venv/bin/python3 -c "
    import sqlite3
    con = sqlite3.connect('file:site/data/catalog.sqlite?mode=ro', uri=True)
    print(con.execute(\"select count(*) from documents where uri='https://lagen.nu/hslffs/2025:64'\").fetchone())
    "
    # (0,)

Per the briefing's repeal model, this is a harvest defect: the repealing
document (2025:64) is missing from the corpus entirely, so HSLF-FS 2017:51
cannot carry an inbound `rpubl:upphaver` link. Root cause is the broken
`index_url` above — the new page was never visited.

### Title defects

1. `hslffs/2022:25` — stored title is truncated mid-parenthesis:

       .venv/bin/python3 -c "
       from ferenda.lib import compress
       import json
       d = json.loads(compress.read_text('site/data/artifact/foreskrift/hslffs/2022-25.json'))
       print(repr(d['metadata']['title']))
       "
       # 'Myndigheten för familjerätt och föräldraskapsstöds allmänna råd om
       #  socialnämndens handläggning av ärenden om internationell adoption
       #  (HSLF-FS'

   Site's real title (confirmed on the new adoption page and in the download
   record's raw link text) is "...internationell adoption (HSLF-FS 2022:25)".
   The raw harvested text interleaves download chrome mid-parenthesis:

       "...internationell adoption (HSLF-FS pdf, 233.2 kB, öppnas i nytt
       fönster. 2022:25) (pdf) pdf, 233.2 kB, öppnas i nytt fönster."

   `ferenda/foreskrift/parse.py`'s `RE_TITLE_CHROME` regex
   (`r"\s*[,(]?\s*(?:pdf|\.pdf)\b.*$"`, `re.DOTALL`) removes everything from
   the *first* "pdf" token to the end of the string. Here the first "pdf"
   token sits before the real designation number, so `clean_title` cuts off
   "2022:25)" and any following text along with the chrome. This is the same
   split-anchor shape `hslffs.bare_number`'s docstring already documents for
   the *number* extraction ("MFoF interleaves the download chrome into the
   row") — `clean_title` has no matching handling for the *title*.

2. `hslffs/2021:64` and `hslffs/2022:66` — stored titles carry the site's own
   role label verbatim:

       "Allmänna råd om socialnämndens utredning och fastställande av
       faderskap eller föräldraskap (HSLF-FS 2021:64 GRUNDFÖRFATTNING )"
       "Allmänna råd om socialnämndens utredning och fastställande av
       faderskap eller föräldraskap (HSLF-FS 2022:66 ÄNDRINGSFÖRFATTNING,
       trädde i kraft 1 februari 2023 )"

   `parse.py` already defines `RE_TITLE_BOILERPLATE` to recognise
   "grundförfattning"/"ändringsförfattning"/"konsoliderad" as role words, but
   `clean_title` only uses it to decide whether *any* title-like text
   survives (the `probe` variable) — it never removes the matched words from
   the title it returns. A grep of the whole `hslffs` kind confirms this only
   happens on MFoF's two records:

       select label, title from documents where kind='hslffs'
         and (title like '%GRUNDFÖRFATTNING%' or title like '%ÄNDRINGSFÖRFATTNING%');
       -- 2 rows, both hslffs/2021:64 and hslffs/2022:66 (MFoF)

### Consolidations

The family-law page lists one konsoliderad version, of HSLF-FS 2021:64
("HSLF-FS 2021:64 KONSOLIDERAD VERSION, trädde i kraft 1 februari 2023"). Our
download record for that basefile carries exactly one consolidation file:

    site/data/downloaded/foreskrift/hslffs/hslffs-2021-64-consolidation-0.pdf

No other MFoF base act has a konsoliderad version on the site. Matches.

### Inherited series (MIAFS)

MFoF's `params["samlingar"]` registers only `{"hslffs": "HSLF-FS"}` — no
MIAFS mapping. The site's own search returns zero hits for "MIAFS"
(`https://mfof.se/sokresultatsida.html?query=MIAFS` → "Din sökning MIAFS gav
ingen träff"). The catalog holds zero `miafs` documents:

    select kind, count(*) from documents where kind like '%miafs%';
    -- (no rows)

Nothing to reconcile: neither the site nor our corpus carries the predecessor
series under any slug.

### Not filed here (already covered elsewhere)

- #50 (predecessor-series document also minted under successor slug):
  N/A — MFoF has no predecessor slug registered and none of its 6 documents
  duplicate a document under another slug.
- #76 (pdftotext designation mismatch): none of MFoF's 6 documents appear in
  the #76 list (`hslffs/2020:81`, `hslffs/2021:53`, `hslffs/2023:25` are all
  Socialstyrelsen/TLV documents, not MFoF).

### Checks that passed

3 (extra documents — none), 6 (consolidations — matches), 7 (inherited
series — nothing to reconcile).
