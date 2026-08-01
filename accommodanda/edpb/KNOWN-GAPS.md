# edpb — known gaps

What this vertical does *not* do, and why, so the next reader does not have to
re-derive it. Measured against the 60-document corpus harvested 2026-08-01.

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

`lagrum.VAGLEDNING` links every form that names a document by its **number**:
`Riktlinjer 05/2020`, `riktlinjerna 8/2022`, `riktlinjen 4/2019`,
`Rekommendation(er) NN/ÅÅÅÅ`, `WP 243`, `WP248 rev.01`. Padded and unpadded
citations mint one address.

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
6/2020, 03/2022, 02/2025, most of the WP29 set — and read as plain prose with
positional ids. That is the source's shape, not a parse failure: the
running-sequence rule (`parse.numbered_breaks`) is deliberately conservative and
finds no numbering where the document sets none.

A second population sits between the two and used to be mistaken for the first:
documents that number their **sections** "1." and "2." and set plain prose under
them. `join_continuations` assumed every substantive paragraph carried a number,
so each section number swallowed everything until the next — **WP 250 was a
single 46,000-character block, WP 248 a 33,000-character one**, and WP 244 and
Riktlinjer 04/2020 the same in miniature. The premise is now tested before it is
relied on (`parse.PUNKT_COVERAGE_MIN`): measured over the corpus a
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
