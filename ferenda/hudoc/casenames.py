"""ECHR cases by party names and application number: the committed snapshot.

Swedish förarbeten (and court decisions) cite Strasbourg case law by name --
"Europadomstolens dom den 13 juli 2000 i målet Elsholz mot Tyskland" -- and
4,480 documents mention the court without a single one linking into the held
corpus of 46,045 decisions. The citation engine can resolve those names, but
`lib` may not read a vertical's stored records (rule:lib-never-imports-
vertical), so the join surface ships the same way DV's named precedents do:
this module writes ``hudoc/data/casenames.json`` from the records on disk,
and `lib.datasets` reads the committed snapshot back as pure JSON.

The keys are `citations`' own (`_norm`-folded applicant/respondent plus the
Court's serial for repeat cases), so the Swedish matcher and the English
inline matcher resolve one name the same way. Values keep every judgment and
decision candidate (kind, date, itemid) rather than a pre-picked winner:
picking is the *citation's* problem -- a date printed beside the citation
must be able to tell a chamber judgment from the Grand Chamber's.

Re-run ``lagen hudoc casenames`` after a harvest to refresh the snapshot.
"""

import json

from ..lib.datasets import EMD_CASES
from . import citations

KIND = {"judgment": "j", "decision": "d"}


def build(root):
    """The snapshot dict, from the records on disk."""
    by_no, by_name, _respondents, _identity = citations.index(root)

    def keep(entries):
        return [[KIND[kind], date, itemid] for kind, date, itemid in entries
                if kind in KIND]

    cases = {"|".join(key): kept
             for key, entries in sorted(by_name.items())
             if (kept := keep(entries))}
    appnos = {no: kept for no, entries in sorted(by_no.items())
              if (kept := keep(entries))}
    return {"_comment": "ECHR cases keyed for the citation engine: cases by "
                        "normalized 'applicant|respondent|serial' (lib."
                        "hudoc-citations _norm folding), appnos by the "
                        "Court's application number. Values are [kind, date, "
                        "itemid] candidates, kind j=judgment d=decision. "
                        "Generated from the stored HUDOC records by `lagen "
                        "hudoc casenames`; do not hand-edit.",
            "cases": cases, "appnos": appnos}


def write(root, path=EMD_CASES):
    snapshot = build(root)
    path.write_text(json.dumps(snapshot, ensure_ascii=False,
                               separators=(",", ":")) + "\n", encoding="utf-8")
    return len(snapshot["cases"]), len(snapshot["appnos"])
