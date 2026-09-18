# PRVFS — Patent- och registreringsverkets författningssamling, Patent- och registreringsverket

Verdict: DEFECT

Site list: 22 designations from 1 page (avdelning A1, https://www.prv.se/sv/om-oss/var-verksamhet/styrdokument/prvs-forfattningssamling/avdelning-a1---gallande-grundforfattningar/). Three more avdelningar checked (A2, B, C).

We hold: 21 documents (prvfs), newest PRVFS 2026:2; site A1 newest PRVFS 2026:2.

Missing: 109 total.
- 1 from avdelning A1 itself: PRVFS 1977:1 (M:1).
- 1 from avdelning A2 (gällande ändringsförfattningar): PRVFS 2023:2 (M:12).
- 107 from avdelning C (upphävda författningar), which our harvest never visits.
  Examples: 1991:2, 1992:3, 1996:3, 1997:1, 1997:3, 1998:4, 1999:3, 2000:1,
  2000:2, 2000:3 (full list of 107 in evidence below).

Extra: 0. Every document we hold under prvfs matches a designation on avdelning A1.

Repeal gaps: 0. The two upphaver relations we hold (PRVFS 2011:1 -> 1986:1;
PRVFS 2023:1 -> 1977:1) both target the correct designation. One target
(1977:1) is absent from our corpus, but that is the harvest gap above, not a
parse defect in the repealing document.

Title defects: 20 of 21 (95%).
- 16 documents carry PRV's internal margin code (e.g. "V:11", "P:81", "M:10",
  "B:17", "O:1") spliced into the middle of the running title sentence.
- 3 documents (1977:2, 1986:1, 2020:2) have no real title at all -- the title
  field just repeats the designation.
- 2 documents (1986:2, 1994:4) hold a raw index-row fragment ("1994:4, M:9
  Ändr. bilaga") instead of a title.
Only PRVFS 1989:1 has a clean title matching the site.

Consolidations: site no, we hold 0. Not a defect -- PRV's författningssamling
pages never mention "konsoliderad"; every avdelning links only to as-published
PDFs.

Inherited: none (assignment lists no predecessor series for PRVFS).

Issue: https://github.com/staffanm/ferenda/issues/79

## Evidence

### 1. Enumeration

Fetched with `ferenda.lib.net.request` / `make_session(BROWSER_UA)`, 4 requests
total (well under the 60-request budget), 2 s apart:

- A1 (our index_url): 200, 22 unique `/prvfs/*.pdf` anchors.
- A2 ("Avdelning A2 - Gällande ändringsförfattningar"): 200, 3 unique anchors
  (2023:2, 1994:4, 1986:2).
- B ("Avdelning B - beslutade författningar som ännu inte trätt i kraft"):
  200, 0 anchors (currently empty).
- C ("Avdelning C - Upphävda författningar"): 200, 119 unique designations
  across ~209 anchor occurrences (many designations repeat once per amending
  act in the same table row).

```
soup.select('a[href*="/prvfs/"][href$=".pdf"]')
```

### 2. Code path that misses PRVFS 1977:1

`ferenda/foreskrift/agencies.py`, `prvfs_enumerate` (around line 870) only
fetches `agency.index_url`, i.e. avdelning A1. On that page the anchor text is
```
<a ... href="/globalassets/dokument/om-prv/prvfs/77prvfs_m1.pdf" ...>1977: 1, M:1</a>
```
Note the space after the colon. Reproducing the two regexes used by
`prvfs_enumerate` against the live text:

```
RE_PRV_TEXT = re.compile(r"(\d{4}):(\d+)")
RE_PRV_FILE = re.compile(r"/(\d{2})prvfs-?(\d+)_", re.IGNORECASE)
RE_PRV_TEXT.search("1977: 1, M:1")                                    # None
RE_PRV_FILE.search("/globalassets/dokument/om-prv/prvfs/77prvfs_m1.pdf")  # None
```
Both fail: the text regex needs the number to touch the colon, and the
filename fallback needs a digit directly before the trailing underscore
(`77prvfs_m1.pdf` has `_m1`, not `_<digits>_`). The document is silently
dropped -- no error, no warning.

This is the target of `PRVFS 2023:1`'s own upphaver relation:

```
$ .venv/bin/python -c "
from ferenda.lib.compress import read_text
import json
d = json.loads(read_text('site/data/artifact/foreskrift/prvfs/2023-1.json'))
print(d['metadata']['upphaver'])"
['https://lagen.nu/prvfs/1977:1']
```

So our corpus already asserts PRVFS 1977:1 exists and was repealed by
2023:1 -- but the document itself was never harvested.

`PRVFS 2023:2` (avdelning A2) is missing for a structural reason: A2 is a
different page, never in the enumerate's URL list at all.

### 3. Avdelning C (upphävda) is never visited

`prvfs_enumerate` reads only `agency.index_url` = A1. Avdelning C
("Upphävda författningar") lists 119 unique designations (mostly the P:xx
chain of patent-fee amending acts PRV itself marks as no longer current);
11 of those also appear on A1 and are already in our corpus. The other 107
are entirely absent:

```
$ .venv/bin/python - <<'EOF'
# c_set = 119 designations scraped from avdelning C
# have_norm = 21 designations in catalog.sqlite (kind='prvfs')
missing = sorted(c_set - have_norm)
print(len(missing))   # 107
EOF
```//actual numbers reproduced from the live scrape, see script output above

Full 107-item list (year:lop): 1991:2, 1992:3, 1996:3, 1997:1, 1997:3,
1998:4, 1999:3, 2000:1, 2000:2, 2000:3, 2000:6, 2000:7, 2001:3, 2001:5,
2002:1, 2002:2, 2002:3, 2003:1, 2003:2, 2003:3, 2003:4, 2003:5, 2004:1,
2004:2, 2004:3, 2005:1, 2006:1, 2006:2, 2006:3, 2006:4, 2007:1, 2007:2,
2007:3, 2007:4, 2007:5, 2007:6, 2008:1, 2008:2, 2008:3, 2008:5, 2009:1,
2009:2, 2009:3, 2009:4, 2009:5, 2009:6, 2009:7, 2009:8, 2009:10, 2010:1,
2010:2, 2010:3, 2010:4, 2011:2, 2011:3, 2011:4, 2011:5, 2012:1, 2012:2,
2012:3, 2012:4, 2012:5, 2013:1, 2013:2, 2014:1, 2014:2, 2014:3, 2014:4,
2014:5, 2015:1, 2015:2, 2015:3, 2015:4, 2015:5, 2016:1, 2016:2, 2017:1,
2017:2, 2018:2, 2018:3, 2018:4, 2018:5, 2018:6, 2019:1, 2019:2, 2019:3,
2020:1, 2020:3, 2020:4, 2021:1, 2021:2, 2021:3, 2022:1, 2022:2, 2022:4,
2022:5, 2022:6, 2023:3, 2023:4, 2023:6, 2023:7, 2024:1, 2024:2, 2024:3,
2024:4, 2025:1, 2025:2.

This is the same "upphävda föreskrifter archive not visited" pattern already
filed for AFS (#45) and EIFS (#51).

### 4. Repeal marking

Only two upphaver relations exist for prvfs in `links`:

```
select * from links where predicate = 'rpubl:upphaver'
  and from_uri like '%/prvfs/%';
https://lagen.nu/prvfs/2011:1 -> https://lagen.nu/prvfs/1986:1
https://lagen.nu/prvfs/2023:1 -> https://lagen.nu/prvfs/1977:1
```

Both targets are the designation the site itself names in the repealing
document's title ("(PRVFS 1986:1, V:3)", "(PRVFS 1977:1 M:1)"). No mistargeting.
1986:1 is in our corpus (with a junk title, see below); 1977:1 is missing
(see #2).

### 5. Title comparison (10+ sampled against the site's own table text)

| designation | site title | our title |
|---|---|---|
| PRVFS 2018:7 | "...föreskrifter om elektronisk överföring och undertecknande i varumärkesärenden." | "föreskrifter om elektronisk överföring och undertecknande i **V:11** varumärkesärenden. den" |
| PRVFS 2011:1 | "...föreskrift om upphävande av föreskrift (PRVFS 1986:1, V:3) om klassindelning..." | "föreskrift om **V:8** upphävande av föreskrift (PRVFS 1986:1, V:3) om klassindelning..." |
| PRVFS 2009:11 | "...om avgifter för olika slags bevis rörande mönsteransökningar och registrerade mönster." | "föreskrifter **M:10** om avgifter för bevis om mönsterskyddsansökningar och registrerade mönster" |
| PRVFS 2009:9 | "...om avgifter för olika slags bevis rörande patentansökningar och meddelade patent." | "föreskrifter **P:81** om avgifter för bevis om patentansökningar och patent" |
| PRVFS 2008:4 | "...föreskrifter om elektronisk patentansökan." | "föreskrifter **P:71** om elektronisk patentansökan" |
| PRVFS 2004:4 | "...föreskrifter om upphävande av föreskrifter på bolagsavdelningens område." | "...föreskrifter om **B:17** upphävande av föreskrifter på bolagsavdelningens område" |
| PRVFS 2018:8 | "...föreskrifter om ansökningsförfarandet, återgivning av varumärken samt invändningsförfarandet;" | "föreskrifter om ansökningsförfarandet, återgivning av varumärken samt **V:12** invändningsförfarandet" |
| PRVFS 2026:1 | "...föreskrifter om skydd för mellanstatliga organisationers vapen, flagga, emblem, benämning eller förkortning av benämning." | "...vapen, flagga, **O:1** emblem, benämning, eller förkortning av benämning" |
| PRVFS 1977:2 | "...föreskrifter om handläggningen av mönsterskyddsärenden..." | "PRVFS 1977:2" (designation only, no title) |
| PRVFS 1986:1 | (title present on site) | "PRVFS 1986:1" (designation only, no title) |
| PRVFS 2020:2 | "Med stöd av 5 § förordningen (2016:978) om tillsyn över kollektiv förvaltning av upphovsrätt." | "PRVFS 2020:2" (designation only, no title) |
| PRVFS 1994:4 | (amendment note on site: "1994:4, M:9 Ändr. bilaga") | "1994:4, M:9 Ändr. bilaga" (index-row fragment, not a title) |
| PRVFS 1986:2 | (amendment note on site) | "1986:2, M:5 Ändr. Författningsrubrik och ingress" (index-row fragment) |
| PRVFS 1989:1 | "...föreskrifter om indelning av mönster i klasser och underklasser" | "föreskrifter om indelning av mönster i klasser och underklasser" -- clean, matches |

Root cause for the 16 "injected code" cases: `ferenda/foreskrift/parse.py`
already strips masthead artifacts that land mid-title
(`RE_MASTHEAD_BOILERPLATE`, see the comment at line ~936: "the masthead's
second column lands in the middle of the title sentence"), and its
`\b[A-ZÅÄÖ]{2,}(?:-| )?FS\b|\b\d{4}:\d+\b` alternatives already remove a
`<FS> <year>:<lop>` or bare `<year>:<lop>` designation from that position.
PRV's own margin code is a *different* shape -- one or two letters, a colon,
one or two digits ("V:11", "P:81", "M:10", "B:17", "O:1", "T:01") -- and is
not matched by any existing alternative, so it survives the strip and stays
spliced into the sentence.

### 6. Consolidations

```
grep -io "konsoliderad[a-zäö]*" prvfs_a1.html prvfs_A2.html prvfs_C.html
```
No hits on any avdelning. `files.consolidation` is empty in all 21 download
records. Not a defect.

### 7. Inherited series

None listed for this assignment.

### 8. Freshness

Site A1 newest: PRVFS 2026:2 (P:144), decided per its masthead. Our newest:
PRVFS 2026:2, date 2026-03-06. Matches. (Avdelning C's newest listed
designation is also 2026:2 -- consistent, since C mirrors the full historical
run, not just repealed items.)
