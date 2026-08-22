# guidance — known gaps

What this source does *not* do, and why, so the next reader does not have to
re-derive it. The EDPB figures are measured against the 60-document corpus
harvested 2026-08-01, the EBA ones against the single-rulebook walk of
2026-08-21.

## EBA: a Swedish document under an English title — closed 2026-08-21

An EBA leaf page states its title in English only — the h1 of the ancillary
services page reads "Guidelines on Ancillary Services Undertakings", while the
Swedish PDF it links opens "Riktlinjer för specificering av kriterierna för
identifiering av de verksamheter som avses i artikel 4.1.18 i förordning (EU)
nr 575/2013". The harvest records the page's title, so a Swedish document was
published under an English heading.

`parse.eba_cover_title` now reads the Swedish title off the cover of the same
PDF the body comes from. It reads all 72 of the Swedish documents. The rule:
take the first cover paragraph that is not the shouted running head (an
uppercase-letter ratio over 0.8) and carries a vägledningsord, join a
continuation the EBA set on the next line (a bare lead word, or a line opening
with a preposition), and take the number, the date and the distribution mark off
the ends of the result — never its middle, since an amending riktlinje names the
riktlinje it amends by number inside its own title. One document ships with the
EBA's own unfilled template ("EBA/GL/20XX/XX DD månad ÅÅÅÅ"); the real title
stands further down the same cover.

The English documents keep the record's title, which is already in their own
language.

## EBA: about half the numbered corpus is missing (open, 2026-08-22)

The EBA numbers `EBA/GL/ÅÅÅÅ/NN` sequentially per year, so the numbers we hold
count the ones we do not. We hold **80**, and there are **82 gaps** below each
year's own highest held number — a floor, not a total: seven numbers named on
the EBA's own pages (2012/04, 2012/05, 2015/21, 2015/22, 2017/17, 2020/15,
2022/15) sit *above* our per-year ceiling, so the real maxima are higher.

Three causes, all measured, none of them scope:

**1. A repealed riktlinje is not dropped from the single rulebook — it is kept
as a previous version of the same leaf, and the walk never follows the link.**
Every leaf carries a "Summary of document history" block whose dropdown links
the same path plus `?version=ÅÅÅÅ`. `…/guidelines-application-definition-default?version=2016` *is* EBA/GL/2016/07, with all 23 translations and a
Swedish PDF named `Guidelines on default definition (EBA-GL-2016-07)_SV.pdf`.
`eba_download` reads only the leaf's current version. On a random sample of 40
of the 80 stored leaves, 10 have previous versions, 16 in total; 14 offer a
Swedish PDF and together they name 11 numbers we do not hold. A previous
version's page carries **no repeal marker** — the status field is empty and its
`time[datetime]` is an application date, not an adoption date.

**2. The older leaves use a second markup — read since 2026-08-22.** There the
final riktlinje is not in `.document-download__item` at all — that list holds
only the consultation paper, the BSG response and the hearing slides. The
document sits in `ul.RelatedList`, and its translations in a
`.RelatedTranslations` dropdown beside it, with the language in the link text
("sv svenska") rather than in a badge and the file's uuid *after* its name in
the path (`…-SV.pdf/39b2fd04-…`), so the href ends in no suffix at all and
`href$=".pdf"` missed it. 43 of the 209 unstored leaves have `guideline` in
their slug. `parse_leaf` now reads the language menu and the list: on the three
unstored guideline leaves of one ämnessida it recovers EBA/GL/2015/09 and
EBA/GL/2016/02, both Swedish and neither held, and the third has one English
file naming no number and falls through to the cover read as before.

**3. Five of the 80 held records are the amending act, filed under the number it
amends — fixed 2026-08-22.** `cover_identity` took the *first* number on the
cover, and an
amending riktlinje's cover names both ("SLUTRAPPORT OM RIKTLINJER FÖR ÄNDRING
AV RIKTLINJERNA EBA/GL/2015/12 EBA/GL/2024/10"). Confirmed three ways — the
cover title, the stored file name, and the cover regex over all 80 PDFs:

| filed as | the stored file is |
|---|---|
| 2015/12 | `GL Amending on arrears and foreclosure (EBA GL 2024 10)_SV_COR.pdf` |
| 2018/01 | `Guidelines amending EBAGL201801 CRR quick fix COVID_SV.pdf` (2020/12) |
| 2018/05 | `Guidelines amending EBA GL on Fraud reporting under PSD2_COR_SV.pdf` (2020/01) |
| 2018/10 | `GL amending EBA GL 2018 10 (EBA GL 2022 13)_SV.pdf` (2022/13) |
| 2020/14 | `GL G-SIIs indicators (EBA GL 2023 10) amending EBA GL 2020 14_SV.pdf` (2023/10) |

So the gap table is wrong in both directions: those five originals are not held,
and five held numbers name the wrong document. `cover_number` now removes the
amended number — the one introduced by "om/för ändring av riktlinjerna" or
"amending Guidelines" — before it looks, and the EBA's unfilled `EBA/GL/20XX/XX`
template with it. `parse.eba_titel` still refuses the cover title for these
five, which is what keeps an artifact from contradicting itself where a
document's identity and its file disagree at all.

**The publications archive is not the missing listing.** Its filter parameter is
`document_type` (Guidelines = 250, Recommendations = 255), and
`?document_type=250` is 10 pages of 15 — 149 rows exactly. All 149 link straight
to a file rather than to a leaf, **none of them Swedish**, and only 41 distinct
EBA/GL numbers appear anywhere in a row. The single rulebook already holds the
full set; it is the walk that stops short.

**All three are fixed in the harvester as of 2026-08-22**, and the numbers above
describe the corpus as it stands until the next run. `eba_sync` now walks the
version dropdown as part of its queue, reads the older leaves' language menu,
takes the number off the chosen document's href where it is there (which is
free, and unlike a page-wide match cannot read a superseded document as its own
successor), and refuses to take the first number on an amending cover. A
superseded version is recorded with `ersatt_av`, which mints the shared repeal
vocabulary on its artifact. A first run costs an estimated 450-500 requests, and
at the EBA's own `Crawl-delay: 10` about 80 minutes.

**Politeness — fixed 2026-08-22.** `https://www.eba.europa.eu/robots.txt` sets
`Crawl-delay: 10` and disallows `/search`. `eba_sync` ran at `delay=0.5`, twenty
times faster than the host asks. `lib/net.request` now waits out whatever
Crawl-delay a host states, for every source, so the EBA is read at 10 seconds
without `eba_sync` knowing about it. That raises the cost of a full EBA run from
about 10 minutes to about 80. The `/search` Disallow is *not* enforced by that
change; no harvester uses it, and the version dropdown makes it unnecessary.

## EBA: identity costs a download on the first run

Of the 289 leaves in the single rulebook, only 52 print an `EBA-GL-ÅÅÅÅ-NN`
token anywhere on the page, and two of those print two. Every other document
states its number only on its own cover, so the first harvest downloads each
PDF to name it. `eba_download.known_identities` keeps that from repeating: a
leaf whose address and linked document are both unchanged is read back off the
stored record, so a steady run fetches nothing.

## Scope: two series plus the endorsed WP29 set

The EDPB publishes ~530 documents across 20 types. This vertical carries three
series — **riktlinjer** (37 harvested of 46 EDPB pages), **rekommendationer**
(7) and the closed **artikel 29-gruppens vägledningar** the EDPB endorsed (all
16) — because those are the interpretive layer over a regulation the site
already holds, and 52 of the 60 exist in Swedish.

Deliberately not carried:

- **Art. 64 opinions (257 pages)** — the largest pile and the lowest yield:
  overwhelmingly routine opinions on draft accreditation requirements, BCR
  approvals and national DPIA lists, and only 56 have a Swedish version.
- **EDPB correspondence (114)**, reports/statements/letters, Support Pool of
  Experts studies, external legal studies, task force reports — informal,
  English-only, rarely cited as authority.
- **Rules of procedure, internal documents, procedures, MoUs** — housekeeping.
- **Art. 65 binding decisions (12)** and **legislative opinions (20)** — both
  defensible additions; neither is guidance, so both were left for a decision of
  their own rather than folded in here.
- The **register of final one-stop-shop decisions** (1,333 national decisions,
  Swedish ones included) is a different kind of corpus — national avgöranden,
  not EDPB guidance — and belongs to its own vertical if it is ever taken.

## The endorsed WP29 set, and the two that took a route of their own

Endorsement 1/2018 endorsed **sixteen** artikel 29-gruppen documents (the list
is on `/endorsed-wp29-guidelines_en` and in the endorsement itself). All sixteen
are carried, but two of them are not the Commission newsroom's copies, and the
reason is worth stating because it is the one place this vertical publishes a
file it did not get from the issuing body.

The two **BCR application forms** — WP 264 (controllers) and WP 265
(processors) — were published as Word *forms*, not as documents. There is no
authoritative PDF of either and never was: item 623848 still serves WP 265 as a
`.doc`, which this vertical cannot read, and item 623850 is worse — its title
and date are WP 264's, but the file behind its download link is the **WP263
PDF**, byte-identical to what item 623056 serves, cover and all. So every PDF of
these two anywhere is somebody's conversion.

Hessens tillsynsmyndighet (HBDI) publishes one of each in its own BCR guidance,
and those are what is carried. What makes a conversion trustworthy here is not
the host but what it can be checked against, so each was checked:

- **WP 264** against the Greek tillsynsmyndighets independent conversion
  (`dpa.gr`, a different Word export three years earlier) — 4,507 words,
  identical but for line breaking.
- **WP 265** against the working party's **own Word file** from the newsroom.
  The PDF even carries that file's author metadata. Comparing the two leaves
  nothing unaccounted for but 18 footnote markers the two extractors glue to
  the preceding word differently.

`parse.wp_cover` re-checks on every parse that each file names its own WP
number, so a mirror that ever starts serving something else fails the parse
rather than quietly filing the wrong text.

Eight of the sixteen have no EDPB page of their own at all — the endorsement
page links straight out to the newsroom, or to the later document that replaced
them — so those are sourced to the endorsement page itself, which is the EDPB's
own statement that they belong here. Of the eight that do have one, seven are
the `/documents/guideline/` stubs described above; the eighth is the position
paper's, which carries no file either but does state its title and date
correctly, and is where that entry's registry values were read off.

**One of them has no WP number**: the position paper on the artikel 30.5
derogation from the record-keeping obligation. It also sets no cover — the
title runs in the opening prose and the document dates itself nowhere — so both
are written down in `series.WP29` off the EDPB's own page for it, and it is
addressed and cited by subject (`edpb/wp/artikel-30-5`) rather than by a number
it does not have.

**Five of the sixteen are English-only** for source reasons rather than
editorial ones: WP 259's language archive names Swedish by the country code
(`_SE.pdf`, not `_sv.pdf`) and is read; WP 257's is a **7-Zip** file, which no
stdlib reader opens, so its Swedish version is unreachable without a new
dependency for one document. WP 263, the two BCR forms and the position paper
were never translated.

One further quirk of the HBDI conversions: WP 264 sets the running masthead in
title case ("ARTICLE 29 Data Protection Working Party") where every other
document sets it in caps, which left it standing as the document's first block
and, behind it, the cover's copy of the title that `drop_repeated_title` then
never reached. `RE_MASTHEAD` matches that casing too — but **anchored to a line
of its own**, and only for the English name. The pattern removes to the end of
the line, and the group names itself in running prose hundreds of times across
this corpus ("… anser artikel 29-arbetsgruppen att …"), so a case-insensitive
unanchored match on that name would delete body text wholesale. Measured before
it was written: 230 paragraphs across 32 of the 60 documents would have lost
text to it.

## How IMY actually cites this guidance (and the bug that hid it)

IMY names a vägledning in prose and **grounds it with the number in the
footnote below**:

> "Europeiska dataskyddsstyrelsen (EDPB) har antagit riktlinjer om beräkning av
> administrativa sanktionsavgifter …" ⁴²
> — *fotnot 42:* "Riktlinjer 04/2022 om beräkning av administrativa
> sanktionsavgifter enligt dataskyddsförordningen."

Of the 138 IMY-beslut in the corpus, 83 name this guidance and **43 of those
carry its number** — but not one of those numbers reached the artifact, because
`lib.pdftext.classify_letterhead` drops every paragraph set below the running
size, which is exactly where the notes are (body 14pt → notes 11pt; body 17pt →
notes 9pt). The identifying citation was being discarded by the parser.

Fixed by `lib.pdftext.letterhead_footnotes`, which reads the same Para stream a
second time and returns what the classifier dropped, with the page furniture
that shares the small size (masthead, page marks, margin values, anything too
short to be prose) taken out. `avg` and `edpb` opt in; the block stream every
other caller consumes is unchanged. All 43 IMY decisions whose PDF names a
number now resolve it, and the fix recovers **1,200 citations across 811 notes
in 131 avg decisions** — 686 of them to EU acts and 185 to EDPB guidance — plus
2,020 more inside the EDPB corpus's own 1,236 notes.

A second half of the same bug: `lib.text.BODY_SECTIONS` -- "what the reader
sees, the index stores and the link walk reads" -- listed only `structure` and
`body`, so notes that *did* reach an artifact still reached neither the graph
nor the search index. That had been silently true of `dv`'s endnotes since HD
started printing them in 2023. With `"footnotes"` added, the IMY→EDPB graph went
from 13 catalogued edges to 219 and from 12 decisions to 43.

Scope, checked rather than assumed: `forarbete` never had this bug (it keeps
notes as `"fotnot"`-typed nodes inside `structure`, so they were always walked);
`foreskrift`, `remisser` and the treaty sources classify on text markers with no
size rule and drop nothing. `rs` opted in with the same three lines (3,996
notes, 4,243 citations).

Still uncarried: `kkv`'s notes. Konkurrensverkets decisions arrive in three
formats through one dispatcher (`kkv_read_document`), so opting that organ in
means threading a third return value through it; JO's, JK's and ARN's templates
set no notes at all.

## The citation grammar catches the number, not the subject

`lagrum.VAGLEDNING` links every form that names a document by its **number**.
For the EDPB: `Riktlinjer 05/2020`, `riktlinjerna 8/2022`, `riktlinjen 4/2019`,
`Rekommendation(er) NN/ÅÅÅÅ`, `WP 243`, `WP248 rev.01`. Padded and unpadded
citations mint one address.

Five more bodies were added on 2026-08-21, each cited by a number carrying its
own acronym, so the number alone names the document: `ESRB/2017/6`,
`EBA/GL/2021/05` (and `EBA/REC/…`), `ESMA/2013/720`, `ESMA35-43-349`,
`JC/GL/2024/36`, `CON/2013/82`, `BoR (11) 67`. Checked against the corpus: all
1 967 documents of those five have their own printed number resolve to their
own page.

**EIOPA is deliberately absent**, though 40 citations name it by number. Its
number (`EIOPA-BoS-19/465`) does not say whether the document is a riktlinje or
a rekommendation, and its address carries that series segment. Minting from the
number alone would guess, and a wrong guess is a link to a different document
rather than to none. It needs the same thing the title grammar needs: a lookup
from the number to the document we hold.

**ACER is absent for a different reason.** It is cited as `Decision No 67/2022`
(306 such mentions in the corpus), but ACER's documents are identified here by a
slug of their title (`acer/ramriktlinjer/capacity-allocation-and-congestion-management-for-electricity`), so there is no number on our side to match. The
same shape is also printed for EU decisions that are not ACER's at all
(`Decision No 39/1984`), which is a second reason the form cannot carry a link
by itself.

**Where the number grammar pays.** Förarbeten print 350 of these numbers across
115 documents. Föreskrifter print exactly **one** in the whole corpus of 12 903,
so `foreskrift` keeps its narrow parse-type set rather than paying a full
re-parse for a single link — föreskrifter cite this material by title, which is
the open work below.

It does **not** catch the form IMY actually uses most of the time. Of 138 IMY
beslut in the corpus, 43 cite this guidance, and the majority cite it by
*subject* rather than by number:

> "Europeiska dataskyddsstyrelsen (EDPB) har antagit **riktlinjer om beräkning
> av administrativa sanktionsavgifter** …", "EDPB:s **riktlinjer om samtycke**",
> "**riktlinjer om öppenhet**, WP260 rev. 01"

A named surface (the `EU_NAMNAKT` pattern, over phrases harvested from the
titles) was prototyped and **rejected as unsafe**: derived mechanically, the
phrases that are unique across the corpus degrade to meaningless fragments
("om åtgärder", "om den", "om europeiska"), which would match arbitrary prose,
while the phrases a practitioner actually writes are ambiguous — 24 of them,
including "om anmälan av personuppgiftsincidenter" (WP 250 *and* Riktlinjer
9/2022) and "om certifiering" (1/2018 and 07/2022). Worse, **WP 244 and
Riktlinjer 8/2022 have byte-identical titles** (the latter is the targeted
update of the former), so no rule over titles can separate them.

A safe named surface therefore needs a *hand-curated* alias list, the way
`sfs/data/namedlaws.json` is hand-curated — someone deciding that "riktlinjer om
samtycke" means Riktlinjer 05/2020. That is editorial work, not extraction, and
is left for a decision rather than invented here.

It also matters much less than it looked: with the footnotes recovered, the
subject-cited form is nearly always *accompanied* by the numbered one in the
note below, so the numbered grammar reaches the citation anyway.

## A guideline IMY cites that this corpus does not carry

IMY cites **Riktlinjer 1/2024 om berättigat intresse** (artikel 6.1 f) five
times. It is not here, and the reason is structural rather than an oversight:
its public consultation closed in November 2024 and the EDPB has **not yet
re-adopted it as a final version**, so it lives under `/public-consultations/`
and never enters the `/documents/guideline/` tree the sitemap harvest walks. A
Swedish version exists.

### A citation resolves to the version that existed when it was made

The reason to carry it is not completeness — it is that **the consulted version
is the one that was cited**. IMY wrote "riktlinjer 1/2024" in 2025 against the
text the EDPB had published then, and the EDPB renumbers between versions: a
guideline is re-adopted with paragraphs inserted, merged and moved, so punkt 42
of the adopted text is generally not punkt 42 of the draft. Serving the adopted
version under that citation does not give the reader a better answer, it gives
them a different paragraph — the citation silently resolves to the wrong text.

So the version condition stated above cuts the *other* way from how this file
first put it. It is not "a draft is not what the board says today, therefore
leave it out". It is: the current version is what a reader arriving with no
citation should land on, **and** a citation naming a superseded version has to
resolve to that version. Both, not one.

That has a consequence for the model, which is why it is recorded here rather
than left as a harvest question: `vagledning_uri` mints **one address per
number** (`edpb/riktlinjer/01-2024`), with the version carried only as
metadata. Addressing a specific version needs the address to say so — the shape
`sfs` already has for its lydelser, where the consolidated text is the default
and each superseded one keeps an address of its own. Taking the consultation
drafts therefore means extending the URI scheme, not just widening the harvest.

Future work, deliberately not attempted here: an `ai-final-mapping` command
that reads a draft and its adopted version together and derives the paragraph
correspondence between them, so a citation to a draft punkt can offer the
reader the adopted paragraph that carries the same text. Until that exists, the
two versions are separate documents and a citation pins one of them.

## Documents that number no paragraphs

The numbered punkt is the citable unit and becomes the anchor (`#punkt27`).
Many documents number nothing at all — Riktlinjer 2/2018, 3/2018, 8/2020,
6/2020, 03/2022, most of the WP29 set — and read as plain prose with
positional ids. That is the source's shape, not a parse failure: the
running-sequence rule (`parse.numbered_breaks`) is deliberately conservative and
finds no numbering where the document sets none.

A second population sits between the two and used to be mistaken for the first:
documents that number their **sections** "1." and "2." and set plain prose under
them. `join_continuations` assumed every substantive paragraph carried a number,
so each section number swallowed everything until the next — **WP 250 was a
single 46,000-character block, WP 248 a 33,000-character one**, and WP 244 and
Riktlinjer 04/2020 the same in miniature (04/2020 has since left this population
for the third one below). The premise is now tested before it is relied on (`parse.PUNKT_COVERAGE_MIN`): measured over the corpus a
section-numbered document numbers at most 9 % of its paragraphs and a
punkt-numbered one at least 29 %, so the two populations are separable with a
wide margin. Below the threshold nothing is joined and the numbers anchor
nothing, since a number that is not a punkt is not what a citation to a punkt
means.

Adjudicated against **the 51 documents that existed before this change**: five
parse differently — WP 250, WP 248, WP 244, Riktlinjer 04/2020 and
Rekommendationer 1/2022 — and the other 46 byte-identically. Of the nine
documents added at the same time, WP 263 has the same shape and would have had
the same defect.

### Two documents print the number with no period

A third population was counted with the first until 2026-08-11: **Riktlinjer
02/2025 and 04/2020 set the number bare**, in a column of its own in the left
margin, where every other document prints "1." in the text. The conversion shows
the two apart:

> `<text top="442" left="66" width="14">1 </text>`
> `<text top="442" left="108" …>The concept commonly referred to by …</text>`

`RE_PUNKT` needs the period, so it matched nothing in either document, and both
read as plain prose: 02/2025's punkter 1–3 arrived as **one block** with one
positional id, and its punkter 4–6 as a second one behind the bullets. A decision
citing "punkt 2 i riktlinjer 02/2025" had nothing to land on.

The number is therefore read off its **geometry**, not its text, because the text
says nothing — "1 The concept commonly …" reads like any sentence opening with a
quantity. Two rules keep that safe, and both are measured rather than assumed:

- The column is the one **the document itself demonstrates**
  (`parse.punkt_margin`): a line whose leading fragment is digits alone, ending
  left of the body column, with the prose beginning *at* that column. poppler
  emits a wider two-digit number and the prose beside it as one fragment
  ("`10  Finally, the use of …`", starting at the margin's 66), which cannot be
  told from a table row whose first column holds a number — Riktlinjer 02/2022
  sets four of those in its annex ("2" at 59, "Artikel 60.2 – Den" at 91). Only
  the demonstrated column is trusted, so those rows pass for nothing.
- The block layer reads the number off the text, with no geometry left, so there
  the numbers have to **climb** (`parse.block_punkter`). Riktlinjer 04/2020 closes
  with a nine-item numbered checklist, which read as punkt 1–9 all over again and
  joined 90 paragraphs onto the wrong punkt. The line level's stricter rule — the
  number the document is *due* — cannot be reused here: the block stream does not
  number 1..N (front matter takes punkt 1 with it in Riktlinjer 09/2020, punkt 9
  goes missing in 03/2019), and applied there it would cost 8 documents every
  punkt they have, 09/2020 all 47.

Measured over the 60-document corpus: a number column is learned for **exactly
those two documents**, and the other 58 parse with the same punkter and the same
blocks as before. 02/2025 goes from 0 punkter to **137** (a contiguous 1–137) and
04/2020 from 0 to **49**, with the text unchanged to the character in both.
04/2020 also leaves the section-numbered population above, where it sat for a
reason that is now visible: the 9 % of its paragraphs that carried a number were
its bilaga's checklist headings ("1. Sammanfattning", "2. Definitioner"), not
punkter at all. Those still anchor nothing — the climb rejects them behind punkt
49 — which is what a citation to a punkt means.

Still glued, and a `lib` matter rather than an edpb one: consecutive bullets under
a punkt ("• distributed …", "• disintermediated …") arrive as one stycke, since
nothing but the marker separates them and `page_paragraphs` breaks on leading.

## Upstream data errors carried through

- **Riktlinjer 6/2020's Swedish page title is mojibake** on edpb.europa.eu
  ("betaltj nstdirektivet", "dataskyddsf rordningen" — the å/ä/ö are lost). The
  Swedish PDF's cover has it right. The page title is kept because it is what
  the source says and because the cover is a worse text everywhere else (PDF
  extraction glues hyphenated line breaks and truncates); correcting one
  document by preferring a differently-flawed source is not obviously right.
- The EDPB's stub page for **WP 250** is titled "Dataskyddsombud", which is WP
  243's subject, and **two pages exist for each of WP 242 and WP 260**. None of
  those titles is trusted: `series.WP29` records the newsroom item per document
  and `parse.wp_cover` reads the title and the adoption date off the document's
  own Swedish cover.
- **Riktlinjer 3/2018 and 5/2019** carry the English tail "- version adopted
  after public consultation" inside their Swedish titles, as the EDPB wrote
  them.

## Language

52 documents are published here in Swedish and 8 in English — Riktlinjer
01/2023, 02/2024 and 02/2025, for which the EDPB has issued no Swedish version,
and WP 257, WP 263, WP 264, WP 265 and the position paper, whose reasons are
above. An
English page carries a banner saying so, and the citation scan runs the English
surface of the engine for it. The WP29 Swedish translations live inside 10–28 MB
language ZIPs on the Commission newsroom; a routine run does not re-resolve them
(`--force` does).

## EUIPO: what the coordinate identity costs

Measured against the three current publications on 2026-08-21.

**Only the current edition is carried.** EUIPO revises the riktlinjerna about
once a year and the delivery app keeps ten editions. The identity is the
coordinate, so a new edition replaces the old at the same address and
`version` records which edition the reader has. A citation that names an older
edition therefore lands on text that has moved.

**The varumärkesriktlinjerna are in English today.** The Swedish translations
trail the English edition: Edition 2026 came into force 2026-07-01 and exists
in 22 languages, Swedish not among them, while the superseded Edition 2025 has
it. The harvest takes the current edition, so 22 of the 24 documents carry
English text and say so. A later run picks the Swedish up with no change of
address, because the number is EUIPO's own language-free scope code
(`part-b-section-4`, not `del-b-avsnitt-4`).

**Formgivnings- and GI-riktlinjerna are one document each.** EUIPO publishes
no PDF for the delar that carry those two families' own guidance — 223 topics
of "Prövning av ansökningar om registrerade EU-formgivningar", 116 of
"Prövning av ansökningar om ogiltigförklaring", and all nine delar of
GI-riktlinjerna resolve to the whole-volume PDF. So the volume is the document
(554 and 208 pages) and "del A, avsnitt 3" is not an address for them, only for
the varumärkesriktlinjerna.

**The front matter is dropped.** "1 Inledning" and the redaktionella noten
about the revision process resolve to the whole-volume PDF in every family —
five topics of the varumärkesvolymen, two of formgivningsvolymen, one of
GI-volymen. They state the revision procedure, not examination practice. The
only way to carry them would be to carry the 1 776-page varumärkesvolym whole.

**The chapter is not an anchor.** The delivery app serves the same text as
2 043 HTML topics, one per kapitel and subsection, each with its own address.
This harvest takes the PDF instead, so the anchors are the ones
`lib.pdftext` mints from the running text. A citation to "del B, avsnitt 4,
kapitel 3" lands on the avsnitt's page, not on the kapitel.

**The whole-volume documents' covers are not checked.** `parse._euipo_fields`
proves a varumärkesdokument is the del it is filed under, and that its cover
prints the avsnitt it is filed as. A volume's cover lists every del it contains
and states no coordinate, so there is nothing to check it against.

**Where the Swedish title is.** Both the app's own metadata (`Title` on
`/api/publications`, and every innehållsförteckningsnod) and the PDF cover are
in the publication's language. The file name is not a third place: `/binary/`
answers with no `Content-Disposition` at all, so the stored file is named from
our own basefile.

## Republication basis

These are republished under the EDPB's own copyright notice, which authorises
reuse for commercial and non-commercial purposes provided the source is
acknowledged and "the original meaning or message of the documents is not
distorted". Two things follow and are implemented rather than assumed: every
page links the EDPB page and the exact PDF it was made from, and every page
states its **version** — a riktlinje is adopted, consulted on and re-adopted,
and serving a superseded draft as current would be exactly the distortion the
notice forbids. Swedish law is not the basis: 9 § and 26 a § URL reach
*svenska* myndigheters yttranden and handlingar upprättade *hos svenska
myndigheter*, and the EDPB is neither.
