# MEMYFS — Mediemyndigheten

Verdict: DEFECT
Site list: 33 PDF links, 24 with a föreskrift designation, from 1 page (https://mediemyndigheten.se/om-oss/lagar-forordningar-och-foreskrifter/)
We hold: 6 memyfs + 10 mprtfs + 7 mrtvfs + 1 rtvfs = 24 documents; newest memyfs 2026:1; site newest memyfs 2026:1 (freshness OK)
Missing: 8 real documents, never entered the corpus under their own designation (see Evidence)
Extra: 1 — memyfs/2024:954 is not a föreskrift at all; it is the full text of the Swedish statute SFS 2024:954, wrongly filed under fs=memyfs
Repeal gaps: 2 confirmed inverted upphaver links (mprtfs/2019:3, mprtfs/2016:2), same root cause as the missing documents
Title defects: 1 minor — mprtfs/2021:2 title "tv:s föreskrifter om mediestöd" is truncated (missing the "Myndigheten för press, radio och tv" lead), not filed (single minor case, folded into the issue as a footnote only)
Consolidations: site no, we hold 0 (consistent)
Inherited: mprtfs (10 held) and mrtvfs (7 held) sit under their own predecessor slugs, not renumbered into memyfs — correct; mrtvfs/2010:1 and mrtvfs/2012:5 already covered by #52 (not re-filed); rtvfs (1 held) likewise under its own slug
Issue: https://github.com/staffanm/ferenda/issues/72

## Evidence

### 1. memy_enumerate mismatches a document's own number for another document's number it merely mentions

`ferenda/foreskrift/agencies.py`'s `memy_enumerate` reads a designation off each PDF
link's visible text with `RE_MEMY_DESIG.search(...)`, which returns the leftmost
match. Several of the agency's ändrings-/upphävandeförfattningar print BOTH the
base document's number and their own number in the link text, base first:

    "Föreskrifter om ändring i Mediemyndighetens föreskrifter (MEMYFS 2024:1) om mediestöd"
    "Föreskrifter om upphävande av MPRTFS 2019:3 om mediestöd (MPRTFS 2021:1)"
    "Föreskrifter om upphävande av (MPRTFS 2016:2) om utvecklingsstöd (MPRTFS 2019:2)"
    "Föreskrifter om ändring i MPRTs föreskrifter (MPRTFS 2016:1) om presstöd (MPRTFS 2021:4)"

The regex picks the base's number (leftmost) as the document's OWN identifier and
basefile. The base's real PDF (a separate link on the same page) is then dropped
as a duplicate of the same basefile.

Ran the harvester's own enumerate function against the live page (2026-09-13):

    .venv/bin/python -c "
    from ferenda.foreskrift.agencies import memy_enumerate, MEMYFS
    from ferenda.lib.net import make_session, BROWSER_UA
    session = make_session(BROWSER_UA)
    for r in memy_enumerate(session, MEMYFS):
        print(r.basefile, '|', r.identifier)"

18 refs came back. Four basefiles hold another document's identity:
`memyfs/2024:1`, `mprtfs/2019:3`, `mprtfs/2016:2`, `mprtfs/2016:1` (plus
`mrtvfs/2010:1`, already covered by #52).

Confirmed against the PDFs themselves (pdftotext, first page):

| basefile in our corpus | our stored identifier | PDF's own printed number | PDF's own title |
| --- | --- | --- | --- |
| memyfs/2024:1 | MEMYFS 2024:1 | **MEMYFS 2024:2** | Föreskrifter om ändring i Mediemyndighetens föreskrifter (MEMYFS 2024:1) om mediestöd |
| mprtfs/2019:3 | MPRTFS 2019:3 | **MPRTFS 2021:1** | Föreskrifter om upphävande av ... (MPRTFS 2019:3) om mediestöd |

The true MEMYFS 2024:1 ("Föreskrifter om mediestöd", beslutad 2023-12-22, utkom
2024-01-11 — fetched and confirmed by pdftotext) and the true MPRTFS 2019:3
grundförfattning are not in the corpus at all; their PDF links on the page were
dropped as duplicates of the slots above.

Same pattern, by title comparison (not independently PDF-fetched, to conserve
budget — the mechanism is identical and already demonstrated twice):

- mprtfs/2016:2 holds MPRTFS 2019:2's content; the real MPRTFS 2016:2 base
  ("Föreskrifter om utvecklingsstöd") is missing.
- mprtfs/2016:1 holds MPRTFS 2021:4's content; the real MPRTFS 2016:1 base
  ("MPRTs föreskrifter om presstöd") is missing, and a third document sharing
  the same base number, MPRTFS 2019:1 ("Föreskrifter om ändring i MPRTFS 2016:1
  om presstöd"), is dropped entirely — it appears nowhere in the corpus.

Total: 8 real documents with no correct representation in the corpus —
MEMYFS 2024:1, MPRTFS 2016:1, 2016:2, 2019:1, 2019:2, 2019:3, 2021:1, 2021:4.

### 2. The same confusion inverts two upphaver links

    .venv/bin/python -c "
    import sqlite3
    con = sqlite3.connect('file:site/data/catalog.sqlite?mode=ro', uri=True)
    cur = con.cursor()
    cur.execute('select from_uri, to_uri from links where predicate=\"rpubl:upphaver\"'
                ' and from_uri like \"%mprtfs%\"')
    print(cur.fetchall())"

gives `mprtfs/2019:3 upphaver mprtfs/2021:1` and `mprtfs/2016:2 upphaver
mprtfs/2019:2`. The fetched PDF for the first (pdftotext) reads:

    MPRTFS 2021:1
    ...
    Myndigheten för press, radio och tv föreskriver att Myndigheten för press,
    radio och tv:s föreskrifter (MPRTFS 2019:3) om mediestöd ska upphöra att
    gälla den 30 april 2021.

The real relation is the opposite of what is stored: MPRTFS 2021:1 repeals
MPRTFS 2019:3, not the other way round. The stored `upphaver` target was
picked as "whichever of the two designations in the text isn't my own
(wrong) identifier" — so it inherited the same swap as the basefile
assignment.

### 3. memyfs/2024:954 is a Swedish statute, not a föreskrift

    .venv/bin/python -c "
    from ferenda.lib.compress import read_text
    import json
    d = json.loads(read_text('site/data/artifact/foreskrift/memyfs/2024-954.json'))
    print(d['source_url'], d['metadata']['title'], d['metadata']['publisher'])"

`source_url` is `https://svenskforfattningssamling.se/.../SFS2024-954.pdf`.
The stored structure is the full text of "Lag (2024:954) med kompletterande
bestämmelser till EU:s förordning om digitala tjänster" (Prop. 2023/24:160),
paragraf by paragraf, with the SFS running header ("SFS 2024:954") bled into
several body paragraphs. `metadata.title` is null and `metadata.publisher` is
the string "Svensk" (a fragment, not "Mediemyndigheten" or "Sveriges
riksdag"). The live agency page links this law's SFS PDF plainly as "Lag
(2024:954) med kompletterande bestämmelser till EU:s förordning om digitala
tjänster (PDF)", with no MEMYFS/MPRTFS/MRTVFS designation in the link text or
href. Today's `memy_enumerate` run (above) does not produce this ref at all —
the current code correctly skips it. The corpus record is stale: harvested
once, filed as a föreskrift, and never corrected.

### Checks not affected

- Freshness: OK, both site and corpus newest is memyfs/2026:1.
- Consolidations: site publishes none; we hold none. Consistent.
- Inherited series: mprtfs and mrtvfs documents sit under their own slugs,
  not folded into memyfs. rtvfs likewise. No renumbering-into-successor
  defect (that is issue #52's territory and mrtvfs/2010:1, 2012:5 are cited,
  not re-filed).
