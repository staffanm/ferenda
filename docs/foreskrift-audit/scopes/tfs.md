# TFS — Tullverkets författningssamling, Tullverket

Verdict: DEFECT
Site list: 340 designations from 1 page (https://www.tullverket.se/omoss/dethargortullverket/verksamhetochorganisation/lagarochforeskrifter/sokitullverketsstyrdokument.4.7df61c5915510cfe9e7f3c1.html)
We hold: 339 documents (tfs), newest TFS 2026:6; site newest TFS 2026:7
Missing: 1 — TFS 2026:7 (published 2026-07-03, decided 2026-06-18; still absent after our 2026-07-09 download run)
Extra: 0
Repeal gaps: 2 — TFS 2008:12 and TFS 2014:6 carry no incoming `upphaver` link, because the repealing document (TFS 2026:7) is missing from the corpus. Same root cause as the missing document above.
Title defects: 65 (of 339, 19%) — our `documents.title` equals the bare label (e.g. "TFS 2024:17") while the site and the PDF both print a real title. Examples:
  - TFS 2024:17: ours "TFS 2024:17", site "Tullverkets tillkännagivande (TFS 2024:17) av värdegräns för lämnande av tullvärderelaterade uppgifter i tulldeklarationen i vissa fall."
  - TFS 2009:11: ours "TFS 2009:11", site "Tullverkets tillkännagivande (TFS 2009:11) av vissa beloppsgränser uttryckta i svenska kronor."
  - TFS 2024:2: ours "TFS 2024:2", site "Upphävande (TFS 2024:2) av Tullverkets allmänna råd (TFS 2010:7) till 7 kap. 8 § tredje stycket mervärdesskattelagen (1994:200)."
  (TFS 1994:64 is excluded from this count: the PDF itself says "Någon författning med nummer TFS 1994:64 har inte givits ut" — no title exists on the site either. Not a defect.)
Consolidations: site yes (9 "sammanställd" PDFs, e.g. TFS 2016:2 amended 22 times), we hold 0
Inherited: none (per assignment)
Issue: https://github.com/staffanm/ferenda/issues/97

## Evidence

### 1. Enumerate

The site's "sökitullverketsstyrdokument" page runs its search server-side; the
harvest POSTs `searchString=( metadata.dokumenttyp:TFS )` with the "« Allt »"
(show all) button already selected (see `agencies.py` TFS params). One POST
returns the entire samling in one page — no further pagination or an
"upphävda" archive to visit separately (the response already carries
"Upphäver TFS …" annotations for repealed regulations inline).

    from ferenda.lib.net import request
    r = request(session, "POST", index_url, data={"searchString": "( metadata.dokumenttyp:TFS )",
        "sortval": "NrU", "step": "0.0", "next": "0.0", "submit": "« Allt »"})

Parsed with `a[rel="external"][href*="/download/"]` (the same `link_select` the
harvest uses) and `RE_FS_NUMBER` to pull the designation out of each anchor's
text: 340 unique `TFS YYYY:N` designations. 66 further anchor texts read
"Visa sammanställd" / "*saknar TFS nr*" — consolidated-PDF links with no
designation of their own, correctly skipped by `ref()` (see check 6).

HTTP requests used against tullverket.se: 2 of the 60 allowed (the index POST,
and one PDF fetch for TFS 2026:7 to read its own masthead).

### 2/3/8. Missing / extra / freshness

    sqlite3 site/data/catalog.sqlite "select label from documents where kind='tfs'"
    # 339 rows

    site designations (340) - ours (339) = {"TFS 2026:7"}
    ours (339) - site designations (340) = {}  (0 extra)

TFS 2026:7's own PDF masthead:

    Föreskrifter om upphävande av Tullverkets föreskrifter (TFS 2008:12)
    om internationella vägtransporter;
    TFS 2026:7
    Utkom från trycket den 3 juli 2026
    beslutade den 18 juni 2026.

Our last download run for tfs finished 2026-07-09 23:00 (mtime of the
`downloaded/foreskrift/tfs/*.json.br` files) — six days after the document
went to print. It never entered `downloaded/` or `artifact/`. This is a
harvest gap, not a freshness lag.

### 4. Repeal marking

TFS 2026:7 repeals both TFS 2008:12 and TFS 2014:6 ("Upphäver TFS 2008:12,
TFS 2014:6" on the site row). Neither shows an inbound `rpubl:upphaver` link
in our `links` table:

    select from_uri, predicate, to_uri from links
      where to_uri in ('https://lagen.nu/tfs/2008:12', 'https://lagen.nu/tfs/2014:6')
      and predicate like '%upphav%';
    -- 0 rows

This is the harvest-defect shape named in the briefing: the repealing document
itself is absent from the corpus, so it cannot carry the relation. No status
flag was requested or proposed on the repealed documents.

### 5. Titles

    sqlite3 site/data/catalog.sqlite "select label,title from documents where kind='tfs'"
    # 66 rows have title == label

Checked every one of the 66 against the site's own description text for that
row (the `<span class="normal">` block following the anchor). All 66 have a
real title on the site; none is genuinely titleless except TFS 1994:64, whose
own PDF says no such regulation was ever issued.

Root cause, confirmed by running the parser's own `_body_start` /
`title_from_masthead` against the stored PDFs (64 of the 66 land here):

    from ferenda.foreskrift.parse import _pages, parse_body, _body_start, _repair_ocr
    blocks, notes = parse_body(_pages(path), identifier)
    _repair_ocr(blocks)
    start = _body_start(blocks)   # returns 0 for these 64

`_body_start` looks for (1) a `kapitel`/`paragraf` block — absent, these
documents are short declaratives with no §§; (2) `RE_PREAMBLE_END` matching
`föreskriver|kungör|beslutar|meddelar` — absent, because a tillkännagivande's
closing clause reads "Tullverket **tillkännager**1 … följande" and an
upphävande's reads "Tullverket **upphäver** …", neither verb is in the list;
(3) `RE_BESLUTAD_LINE.match()` against "beslutat den …" / "beslutade den …" —
`.match()` anchors at the block's start, but pdftohtml merges the "Utkom från
trycket" / "den <date>" line with the following "beslutat den <date>." line
into one block (confirmed: block 5 of TFS 2024:17 reads `"den 4 december 2024
beslutat den 19 november 2024."`), so the anchor never lines up. All three
checks fail, `_body_start` falls back to 0, `title_from_masthead` gets an
empty masthead and returns `None`.

Two further, single-document causes among the 66:
  - TFS 2013:3 is titled "Förordning om ändring i …" — `RE_TITLE_TYPE` lists
    föreskrifter/föreskrift/allmänna råd/allmänt råd/kungörelse/tillkännagivande
    but not "förordning".
  - TFS 2021:2 (an Inspektionen för strategiska produkter regulation printed
    in TFS) has a title running past `TITLE_MAX` (300 chars) before its own
    semicolon; the 300-char window ends 7 characters short of it, so
    `RE_TITLE_END` finds no stop and the candidate is discarded.

### 6. Consolidations

The site's "Visa sammanställd" links point at 9 distinct consolidated PDFs
(58 anchors in total, since a base regulation's every later amendment row
repeats the same "Visa sammanställd" link):

    2006010s.pdf 2016021s.pdf 2019006s.pdf 2000029s.pdf 2015001s.pdf
    1996021s.pdf 2016023s.pdf 2012003s.pdf 2016002s.pdf

TFS 2016:2 (en tullordning) alone has been amended 22 times and has its own
sammanställd version. None of the 339 `downloaded/foreskrift/tfs/*.json.br`
records carry anything under `files.consolidation`:

    for f in glob('site/data/downloaded/foreskrift/tfs/*.json.br'):
        data['files']['consolidation']   # == [] every time

### 7. Inherited series

None — the assignment lists no predecessor samling for tfs.
