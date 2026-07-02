---
name: foreskrift-as-published-not-consolidated
description: Föreskrifter are as-published immutable documents, not consolidated text; consolidation metadata is "last amendment incorporated", never a date
metadata:
  type: feedback
---

The myndighetsföreskrift corpus is **as-published, immutable documents** — a
grundförfattning and its ändringsförfattningar are each a fixed historical
artifact that never changes (an amendment changes the base by being a separate
later document, not by mutating it). This is the opposite of the SFS source we
built first, where the text we handle *is* the consolidated in-force version — so
do not carry SFS "consolidated/in-force/cutoff-date" thinking into föreskrifter
(or into the not-yet-fetched pure-as-published SFS source, svenskforfattningssamling.se,
which is the closer analogue — see [[svenskforfattningssamling-source]]).

A **konsoliderad version** is an *inofficial* compilation (the printed
författning stays the officially valid text; an officially consolidated reprint
is instead an "Omtryck"). Only a small subset are consolidated — 108/1218 (8.9%)
in the harvested corpus.

**Why:** I wrongly modelled a consolidation's currency as a "cutoff date",
reasoning from SFS. The correct data point is **"amendments incorporated up to
and including"** = the most recent amendment FS number (the last amendment folded
in), e.g. `konsolideradTom = https://lagen.nu/fffs/2026:6`. Dates are wrong:
a "Senast uppdaterad" date is just when the file was regenerated (irrelevant),
and the most-recent-amendment's enactment date conflates *which* amendment was
folded in with *when that amendment was enacted* — two distinct properties.

**How to apply:** A consolidated föreskrift PDF lists its incorporated amendments
(FFFS masthead: `Ändringar: FFFS 2014:29, …, FFFS 2026:6`); the last is the data
point. Base/amendment regs need no temporal/currency metadata at all. See
[[foreskrift-vertical]] for the vertical's structure.
