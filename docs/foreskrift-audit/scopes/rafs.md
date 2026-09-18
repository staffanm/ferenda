# RAFS — RA-FS, Riksarkivet

Verdict: DEFECT
Site list: 73 designations (30 base regulations, 43 amendments) from 1 API
call, no pagination (https://foreskrifter.riksarkivet.se/rafs is an Angular
page backed entirely by `api/rafs/sok`)
We hold: 90 documents (rafs), newest RA-FS 2024:8; site newest RA-FS 2025:3
Missing: 3 — RA-FS 2025:1, RA-FS 2025:2, RA-FS 2025:3 (a freshness gap, over
a year old)
Extra: 20 — 13 confirmed repealed by a document we hold; 7 not linked as
repealed: RA-FS 2002:4, 2005:2 (repeal text exists but the parser misses it,
see below), 2005:3, 2012:3, 2012:9, 2015:2, 2018:9 (amendments to a base that
is itself repealed, or a plain replacement with no explicit upphävande —
legitimate)
Repeal gaps: 5 documents (RA-FS 2011:1, 2013:6, 2015:1, 2015:5, 2018:11) name
their repeal targets by designation in the PDF text, but `metadata.upphaver`
is empty for all five — a parser defect, still present in current code.
One more document (RA-FS 2018:10) states its repeal plainly and current code
parses it correctly when re-run, but the stored artifact predates the fix
and needs a reparse, not a code change.
Title defects: 4 — a designation injected mid-title, "Utkom från trycket"
boilerplate left in place, OCR garbage, and a stray footnote digit
(RA-FS 1997:4, 2002:1, 2010:2, 2012:1)
Consolidations: site yes (5 of the 30 base regulations have one), we hold
5 of 5 — no gap
Inherited: none — RA-FS has no predecessor series in this assignment
Issue: https://github.com/staffanm/ferenda/issues/82

## RE_FS_REF and the "RA-FS" hyphen

`RE_FS_REF` (`ferenda/foreskrift/parse.py:284`) matches "RA-FS 2020:1" and
"RAFS 2020:1" correctly, uppercase only (case sensitivity per #54 does not
bite here — Riksarkivet always prints the designation in capitals). But it
does not match the designation when its hyphen is an en dash or em dash
("RA–FS", "RA—FS"), the character OCR / PDF-to-text extraction produces
sometimes in scanned older PDFs.

Confirmed effect: RA-FS 2019:2's own title reads "... (RA–FS 1991:1) ..."
(en dash). `metadata.andrar` for RA-FS 2019:2 is empty; it should be
`["https://lagen.nu/rafs/1991:1"]`. Verified with current code:

    .venv/bin/python -c "
    from ferenda.foreskrift import parse as P
    class D:
        def parse_text(self, t, context=None): return []
    decl = 'Föreskrifter om ändring av Riksarkivets föreskrifter RA–FS 1991:1 ...'
    print(P.extract_metadata('Dummy text.', decl, D(), fs='rafs')['andrar'])
    "
    # -> []

Only RA-FS 2019:2 is affected among the 3 records that contain an en/em
dash designation (2010:2 and 2021:3 get their `andrar`/`upphaver` from a
clean-hyphen mention elsewhere in the same text).

## Evidence

### Enumeration

    .venv/bin/python -c "
    from ferenda.lib.net import request
    import requests
    session = requests.Session()
    url = 'https://foreskrifter.riksarkivet.se/api/rafs/sok?nummer=&rubrik=&fulltext=&myndighet=&arkivbildare=&sokBlandGiltiga=true'
    r = request(session, 'GET', url, parse_json=True)
    print(len(r['traffLista']))
    "
    # -> 73 (30 with nummer == grundforfattning, i.e. base regulations)

Two HTTP requests made in total (one 404 probing a nonexistent
`sokBlandGiltiga=false` parameter), well under the 60-request budget.

### Missing / freshness

    site - ours = {'2025:1', '2025:2', '2025:3'}

### Extra and repeal shape

    select label, upphavande from documents where kind='rafs'
    -- 20 designations present in our catalog absent from the site's 73;
    -- 13 of them are targets of an `rpubl:upphaver` link from a document we
    -- hold (legitimate: the site only lists in-force text).

### Repeal-parsing defect (RE_UPPHOR_LIST)

RA-FS 2011:1's body: "... att följande av Riksarkivets allmänna råd för
kommuner och landsting ska upphöra att gälla: 1. Riksarkivets allmänna råd
(RA-FS 1997:8) ... 2. Riksarkivets allmänna råd (RA-FS 2002:2) ...". Four
more RA-FS documents (2013:6, 2015:1, 2015:5, 2018:11) use the same "följande
av Riksarkivets allmänna råd ... ska upphöra att gälla:" formula.
`RE_UPPHOR_LIST` requires "följande"/"nedanstående" to be followed
immediately (mod whitespace) by "föreskrifter|allmänna råd|författningar|
kungörelser" — the intervening "av Riksarkivets" breaks the match, so no
regex in `extract_metadata` captures the list, and `upphaver` comes back
empty. Confirmed with current code:

    .venv/bin/python -c "
    from ferenda.lib.compress import read_text
    from ferenda.foreskrift import parse as P
    import json
    class D:
        def parse_text(self, t, context=None): return []
    d = json.loads(read_text('site/data/artifact/foreskrift/rafs/2011-1.json'))
    text = ' '.join(t for n in d['structure'] for t in _walk(n))  # flatten structure text
    print(P.extract_metadata(text, '', D(), fs='rafs')['upphaver'])
    "
    # -> []

Two of the ten named targets are documents we hold and should show as
repealed: RA-FS 2002:4 (by 2018:11) and RA-FS 2005:2 (by 2018:11). Neither
carries an inbound `rpubl:upphaver` link today.

### Stale artifact (RA-FS 2018:10)

RA-FS 2018:10's transitional provision: "Genom denna författning upphävs
Riksarkivets föreskrifter och allmänna råd (RA-FS 2015:2) om gallring och
utlån av räkenskapsinformation m.m." Running the *current* `extract_metadata`
against this text returns `upphaver == ['https://lagen.nu/rafs/2015:2']` —
correct. The artifact on disk still has `upphaver: []`. This is a stale
artifact, not a code defect; it needs a reparse of `rafs/2018:10`, which the
issue notes but does not ask to be fixed as code.

### Title defects

    select label, title from documents where kind='rafs'
    -- vs. the API's own `rubrik` field, matched by nummer

    RA-FS 1997:4 ours: "Föreskrifter om ändring av Riksarkivets RA-FS 1997 :4
      föreskrifter (RA-FS 1991:1) och allmänna råd Utkom fråntrycket om arkiv
      hos statliga myndigheter; den 29 januari 1993"
    RA-FS 1997:4 site: "Föreskrifter om ändring av Riksarkivets föreskrifter
      (RA-FS 1991:1) och allmänna råd om arkiv hos statliga myndigheter"

    RA-FS 2002:1 ours: "... RA-FS 2002:1 (1999:1) ... inkom från trycket ...
      den l7 muj 2002"
    RA-FS 2002:1 site: "... (1999:1) ... i statliga myndigheters
      forskningsverksamhet"

    RA-FS 2010:2 ours: "Föreskrifter om ändring av Riksarkivets föreskrifter
      RA—FS 201032"  (the actual subject is missing entirely)
    RA-FS 2010:2 site: "Föreskrifter om ändring av Riksarkivets föreskrifter
      och allmänna råd (2006:1) om handlingar på papper"

    RA-FS 2012:1 ours ends "... arkiv hos statliga myndigheter1" (a stray
      footnote digit); site ends "... arkiv hos statliga myndigheter"

`clean_title` (`ferenda/foreskrift/parse.py:987`) strips a designation only
when it is the leading prefix of the harvest title, and never strips
"Utkom från trycket ..." wherever it lands inside the title — so a
designation or the boilerplate landing mid-string survives. All four
affected records come from a retired harvest ("source": "myndfs-legacy" in
their download record — the string does not appear anywhere in
`ferenda/`), so their stored title was never run through today's parser at
all.

### Consolidations

    grep files.consolidation in site/data/downloaded/foreskrift/rafs/*.json.br
    # 5 of 84 download records carry one; the API shows exactly 5 of 30 base
    # regulations have a "KONSOLIDERAD" PDF. No gap.
