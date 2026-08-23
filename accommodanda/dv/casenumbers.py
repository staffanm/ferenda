"""Held decisions by the case number they were filed under: the committed
snapshot.

A decision is cited two ways. Once it is published it has a referat number
("NJA 2009 s. 672"), which the citation engine reads straight off the text. For
the months before that -- and afterwards in law review articles, which keep
citing what the reader could look up at the time -- it is named by court, date
and case number: "Högsta domstolens dom 2009-11-03 T 3-08". Nothing in that
string says which referat it became, so the citation engine can only resolve it
against the corpus.

`lib` may not read a vertical's stored documents (rule:lib-never-imports-
vertical), so the join surface ships the way the JO ämbetsberättelse index and
DV's named precedents do: this module writes ``dv/data/casenumbers.json`` from
the artifacts on disk and `lib.datasets` reads it back as pure JSON.

A decision with no recorded date carries an empty one (19 of the 23,739
artifacts), so the candidate lists stay sortable and comparable as data.

Every candidate is kept, never a pre-picked winner. A case number is not a key:
298 of the 24,411 held numbers name more than one decision -- the same number
in another court's series (B 53-11 is both an AD and an HD case), or two
Arbetsdomstolen cases a year apart. Picking is the citation's problem, and
`lib/malnummer` picks with the court and date the citation prints.

Re-run ``lagen dv casenumbers`` after a parse run to refresh the snapshot.
"""

import json

from ..lib import catalog, compress, layout, malnummer
from ..lib.datasets import CASENUMBERS


def build():
    """The snapshot dict, from the dv artifacts on disk, plus the case numbers it
    refused (a printed form `lib/malnummer` cannot read back -- the caller
    reports them rather than shipping keys nothing can ever match)."""
    numbers, courts, refused = {}, {}, []
    for path in layout.artifacts("dv"):
        art = json.loads(compress.read_text(str(path)))
        courts.setdefault(art["court"], set()).add(art["court_namn"])
        for number in art["malnummer"]:
            # a key only counts if the matcher reads the whole value back as one
            # case number: 268 of the 24,995 printed values are shapes it never
            # produces ("05-3", "2000-2", "----", and the en-dashed multi-case
            # "1376–1383-15"), and as keys they would sit there unmatchable
            normalized = malnummer.normalize(number)
            if malnummer.find(normalized) != [normalized]:
                refused.append(number)
                continue
            numbers.setdefault(normalized, []).append(
                [art["court"], art["avgorandedatum"] or "",
                 catalog.local(art["uri"])])
    return {"_comment": "Held court decisions keyed by the case number they "
                        "were filed under, normalized by lib/malnummer. Values "
                        "are [court, decision date, local uri] candidates -- a "
                        "case number names more than one decision often enough "
                        "that the citation's own court and date decide. The "
                        "courts map is what each code calls itself; the phrases "
                        "a citation uses for them are lib/malnummer's. "
                        "Generated from the parsed dv artifacts by `lagen dv "
                        "casenumbers`; do not hand-edit.",
            "courts": {code: sorted(names)
                       for code, names in sorted(courts.items())},
            "numbers": {number: sorted(entries)
                        for number, entries in sorted(numbers.items())}}, refused


def write(path=CASENUMBERS):
    snapshot, refused = build()
    path.write_text(json.dumps(snapshot, ensure_ascii=False,
                               separators=(",", ":")) + "\n", encoding="utf-8")
    return len(snapshot["numbers"]), len(snapshot["courts"]), refused
