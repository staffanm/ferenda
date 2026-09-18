# MIGRFS — Migrationsverkets författningssamling, Migrationsverket

Verdict: DEFECT
Site list: 29 in-force links (1 page) + 195 designations from a linked
"Förteckning" register (20 sheets in one xlsx), from
https://www.migrationsverket.se/om-migrationsverket/styrning-och-uppfoljning/foreskrifter.html
We hold: 122 documents (migrfs), newest MIGRFS 2026:11; site newest MIGRFS 2026:11 (match)
Missing: 85 — MIGRFS 2010:1..21 (19 of 21), 2011:1-3,7-10, 2012:1-9,
  2013:3-9, 2014:1-8,12, 2015:2,4-7,9, 2016:2-4,6-8,10-12, 2017:1,5,6,11,12,
  2018:1, 2022:2, 2023:5,6, 2024:4,6, 2025:1,3-7,11,13, 2026:7 (full list in
  evidence)
Extra: 12 — pre-2010 documents outside the register's stated 2010-2026
  scope (2003:7, 2004:2, 2004:5, 2006:3, 2006:5, 2008:4, 2009:1, 2009:4,
  2009:6); plus 2020:9 (register spells it "MIGRF 2020:9", a typo, not
  missing) and 2021:12/2021:13 (real documents the register omits
  entirely — a gap in Migrationsverket's own bookkeeping, not ours)
Repeal gaps: 8 confirmed — 2019:3, 2019:9, 2020:6, 2021:6, 2021:10,
  2021:13, 2022:1, 2024:5 each print an explicit repeal of a named older
  MIGRFS in their own PDF text, but our parsed `upphaver` is empty for
  every one
Title defects: 3 — MIGRFS 2003:7 (no title, falls back to the
  designation), MIGRFS 2004:2 and 2004:5 (title = the whole document body,
  1,800+ characters, instead of a subject line)
Consolidations: site no, we hold 0 (matches — not a defect)
Inherited: sivfs — we hold 4 (1992:2, 1994:2, 1994:21, 1999:5); the
  migrfs register names 3 more SIVFS designations we do not hold
  (1997:11, 1998:2, 1998:8); no sivfs document was found renumbered under
  migrfs (checked: migrfs/2009:6 correctly cites sivfs/1994:6 and
  sivfs/1996:2, not a migrfs remint — no #50-style defect here)
Issue: https://github.com/staffanm/ferenda/issues/75

## Evidence

### 1. Enumerate

Entry page (1 request):
    https://www.migrationsverket.se/om-migrationsverket/styrning-och-uppfoljning/foreskrifter.html
Static page, `a[href*="migrfs" i][href$=".pdf"]` (per
`ferenda/foreskrift/agencies.py` MIGRFS.params) yields 29 links, all
"gällande" (in-force) MIGRFS, e.g. "MIGRFS 2026:11pdf, 17.4 kB, öppnas i
nytt fönster." → /download/…/MIGRFS_2026_11.pdf. One of the 29,
"MIGRFS 5/2011pdf" → migrfs052011.pdf, is dropped by the harvest's
`skip_re` (old N/YYYY numbering) — expected, already documented in code.

No pagination (`grep -io 'sida\|page='` found nothing but the bare word
"sida").

The page's own text points past the 29-link listing to a fuller record
(1 request, an .xlsx, not a .pdf — so the `link_select` selector never
sees it):

    "Gällande MIGRFS publiceras här på sidan. Övriga MIGRFS, och MIGRFS
    äldre än två år som enbart gäller upphävande av en annan MIGRFS,
    finns i förteckningen:"
    -> /download/18.2cd2e409193b84c506a327b9/1785754907301/Forteckning_MIGRFS_2010-2026.xlsx

That workbook has 20 sheets: "Gällande MIGRFS 2010-2026" (40 rows),
"Gäll MIGRFS 2010-2026, enb upph" (15 rows, repeal-only documents still
in force), and one "Upphävda MIGRFS, <year>" sheet per year 2010-2026.
Parsed with openpyxl, normalizing both "MIGRFS YYYY:N" and the older
"MIGRFS N/YYYY" form to the same key, 195 distinct designations,
2010-2026.

### 2. Missing (85)

    python3 -c "
    import sqlite3, json
    con = sqlite3.connect('file:site/data/catalog.sqlite?mode=ro', uri=True)
    held = {r[0] for r in con.execute(\"select label from documents where kind='migrfs'\")}
    site = set(json.load(open('normalized2.json')).keys())   # from the xlsx, see script above
    print(sorted(site - held))"
    # 85 designations, none of them present as an artifact or a download
    # record either (site/data/artifact/foreskrift/migrfs and
    # site/data/downloaded/foreskrift/migrfs both hold exactly 122 files,
    # the same 122 the catalog lists)

This is the recurring "archive the harvest never visits" shape (cf. #45
AFS, #51 EIFS), here as a linked Excel register instead of an HTML page.
The register gives no direct download URL for the older entries (checked
with openpyxl's `cell.hyperlink` across every "Upphävda" sheet — none of
the per-document rows carry one), so retrieving the 85 PDFs will need a
second look at how migrationsverket.se serves them, if it still does.

### 3. Extra (12)

    python3 -c "
    ... site - held vs held - site ..."
    # held - site = {2003:7, 2004:2, 2004:5, 2006:3, 2006:5, 2008:4,
    #                2009:1, 2009:4, 2009:6, 2020:9, 2021:12, 2021:13}

The first nine predate 2010, outside the register's stated "2010-2026"
scope — no site evidence either way. 2020:9 appears in the register as
"MIGRF 2020:9" (missing the S — their typo, not ours). 2021:12 and
2021:13 do not appear anywhere in the register at all, even though
2021:13 is *named* as a repealer by another row ("MIGRFS 08/2015 …
Upphävdes 2022-01-01 av MIGRFS 2021:13") — the register is
self-inconsistent here, not a corpus defect.

### 4. Repeal gaps (8, confirmed by reading the PDF)

    for f in migrfs-2019-3 migrfs-2019-9 migrfs-2020-6 migrfs-2021-6 \
             migrfs-2021-10 migrfs-2021-13 migrfs-2022-1 migrfs-2024-5; do
      pdftotext site/data/downloaded/foreskrift/migrfs/$f-regulation.pdf -
    done
    # each prints an explicit "Samtidigt upphör Migrationsverkets
    # föreskrift(er) (MIGRFS NN/YYYY) att gälla" / "MIGRFS N/YYYY upphör
    # att gälla" clause naming an older MIGRFS by its slash-form number

    python3 -c "
    from ferenda.lib.compress import read_text
    import json
    for b in ['2019-3','2019-9','2020-6','2021-6','2021-10','2021-13','2022-1','2024-5']:
        d = json.loads(read_text(f'site/data/artifact/foreskrift/migrfs/{b}.json'))
        print(d['identifier'], d['metadata']['upphaver'])"
    # every one: []

Root cause: `RE_FS_REF` in `ferenda/foreskrift/parse.py` is
`r"\b([A-ZÅÄÖ]+(?:-| )?(?:FS|FA))\s*(\d{4}):(\d+)"` — it requires the
colon "YYYY:N" form. Migrationsverket numbered MIGRFS "N/YYYY" (slash,
lopnummer first) until 2018; a repeal clause naming an older regulation
that way ("MIGRFS 01/2016", "MIGRFS 08/2015", "MIGRFS 06/2011", or even
bare "(11/2014)" after "föreskrift" singular) matches neither
`RE_FS_REF` nor `RE_BARE_OWN_REF` (which additionally only recognizes
plural "föreskrifter(na)", not singular "föreskrift"). `_repeal_object`
then keeps the clause but extracts no designation from it, and
`upphaver` stays empty — no exception, no log, just a silently missing
relation.

### 5. A second, unrelated upphaver defect: SFS citations minted as migrfs

    python3 -c "
    import glob, json
    from ferenda.lib.compress import read_text
    for p in sorted(glob.glob('site/data/artifact/foreskrift/migrfs/*.json.br')):
        d = json.loads(read_text(p[:-3]))
        for u in d['metadata'].get('upphaver', []):
            tail = u.rsplit('/', 1)[-1]
            y, _, n = tail.partition(':')
            if not (y.isdigit() and n.isdigit() and int(n) < 30):
                print(d['identifier'], u)"
    # MIGRFS 2020:7  -> https://lagen.nu/migrfs/2017:193
    # MIGRFS 2020:8  -> https://lagen.nu/migrfs/2010:1122
    # MIGRFS 2020:9  -> https://lagen.nu/migrfs/1990:927
    # MIGRFS 2020:13 -> https://lagen.nu/migrfs/2017:193
    # MIGRFS 2023:12 -> https://lagen.nu/migrfs/2010:1122
    # MIGRFS 2023:13 -> https://lagen.nu/migrfs/2017:193
    # MIGRFS 2023:14 -> https://lagen.nu/migrfs/1990:927
    # MIGRFS 2025:8  -> https://lagen.nu/migrfs/2017:193
    # MIGRFS 2025:9  -> https://lagen.nu/migrfs/2010:1122
    # MIGRFS 2025:10 -> https://lagen.nu/migrfs/1990:927

None of these "migrfs" designations exist (lopnummer 193/1122/927 is
absurd for this samling); 1990:927, 2010:1122 and 2017:193 are the real
SFS regulations these MIGRFS are issued under (`bemyndigande`, correctly
also present as `https://lagen.nu/1990:927#P42` etc.). Example (MIGRFS
2020:9's own metadata):

    "bemyndigande": ["https://lagen.nu/1990:927#P42"],
    "upphaver": ["https://lagen.nu/migrfs/1990:927", "https://lagen.nu/migrfs/2017:9"]

Root cause: `RE_BARE_OWN_REF` is
`r"(?:föreskrifter(?:na)?(?:\s+och\s+allmänna\s+råd)?|kungörelsen?)[^()]*\((\d{4}):(\d+)\)"`
— `[^()]*` is unbounded, so once the word "föreskrifter" has appeared
anywhere inside the up-to-600/2500-character repeal-clause window, the
*next* bare parenthesis it meets is accepted as the document's own
series, even when that parenthesis actually follows "förordningen" (an
SFS citation) several sentences later.

### 6. Titles (10+ checked)

    select label, title from documents where kind='migrfs' order by label
      limit 15;   -- and again "order by label desc limit 15"

27 of the 30 sampled read as a normal subject line. Three do not:

| designation    | we hold (title)                                                        | site/PDF says |
|----------------|-------------------------------------------------------------------------|---------------|
| MIGRFS 2003:7  | "MIGRFS 2003:7" (no title)                                              | legacy import, `title: null`, PDF masthead not read either |
| MIGRFS 2004:2  | full 1,857-character body text ("… beslutade den 13 augusti 2004. Sverige har ingått avtal … kan uppvisa giltig sjukförsäkring …") | "Migrationsverkets föreskrifter med bemyndigande för Sveriges ambassad i Canberra att bevilja uppehålls- och arbetstillstånd (ferietillstånd) för medborgare i Australien och Nya Zeeland" |
| MIGRFS 2004:5  | full 1,835-character body text (same shape)                            | "Migrationsverkets allmänna råd för att tillämpa förordningen (1994:362) om vårdavgifter m.m. för vissa utlänningar" |

    python3 -c "
    import json
    from ferenda.lib.compress import read_text
    d = json.loads(read_text('site/data/downloaded/foreskrift/migrfs/migrfs-2004-2.json'))
    print(len(d['title']))"
    # 1857 -- the harvested 'title' field itself already holds the whole
    # document, not link chrome; clean_title() only rejects link chrome,
    # so a long real-prose block passes straight through

### 7. Consolidations

No "konsolider…" text anywhere on the entry page; 0 of 122 download
records carry `files.consolidation`. Matches — not a defect.

### 8. Freshness

Site: newest in-force link and newest register row both MIGRFS 2026:11
(fastställd 2026-07-14, publicerad 2026-07-15). We hold MIGRFS 2026:11.
Match.

### Inherited series: sivfs

    select label from documents where kind='sivfs';
    -- SIVFS 1992:2, 1994:2, 1994:21, 1999:5 (4)

The migrfs register's "enb upph" sheet additionally names, among
Migrationsverket's still-standing repeal notices:

    SIVFS 1998:8  upphävde SIVFS 1992:7   (ikraftträdande 1998-06-01)
    SIVFS 1998:2  upphävde SIVFS 1992:17  (ikraftträdande 1998-02-10)
    SIVFS 1997:11 upphävde SIVFS 1993:21  (ikraftträdande 1997-09-30)

None of these three are in our sivfs holdings. No #50-style defect: the
sivfs documents we do hold are correctly cited from migrfs (e.g.
migrfs/2009:6's `upphaver` correctly points at `sivfs/1994:6` and
`sivfs/1996:2`, not a migrfs remint).
