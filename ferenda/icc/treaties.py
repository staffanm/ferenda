"""Which instruments an ICC decision applies.

The Court's own constitutive treaty is in the corpus as ``icrc/585`` with
its 128 articles anchored, and until now nothing pointed at it: all 269
decisions carried an empty `references`, so the Rome Statute -- and every one
of the 111 ICRC treaties -- had zero inbound links. The decisions are made of
those citations: 13,887 "article N ... of the Statute" forms across 244 of
the 269.

The matching is `lib.treatyref`'s. What is local here is the Court's own
shorthand: inside an ICC decision "the Statute" is the Rome Statute and nothing
else, and the Rules of Procedure and Evidence -- named 1,752 times -- are not a
treaty the corpus holds, so they resolve to nothing rather than to a guess.
"""

import functools
from pathlib import Path

from ..lib import treatyref
from ..lib.lagrum import Ref, yield_overlaps
from .model import RE_DOC_BASE, decision_uri

ROME_STATUTE = "icrc/585"
# The Court's own shorthand for its constitutive treaty. Safe only here: the
# same words in an ICJ judgment mean the Statute of that Court, which is why
# `treatyref` takes it as a caller's addition rather than curating it.
SHORT_FORMS = (("the Statute", ROME_STATUTE),
               ("the present Statute", ROME_STATUTE),
               ("ICC Statute", ROME_STATUTE))


def references(text):
    """The instruments this decision cites, article-level where it names one."""
    return treatyref.references(text, extra=SHORT_FORMS)


@functools.lru_cache(maxsize=1)
def _held(root):
    """Base document number -> held basefile. A decision cites a sibling by
    its base number ("ICC-02/11-01/11-129") where the corpus may hold a
    variant of it (the -Red redaction, a -Corr) -- the base maps onto
    whichever is on disk, the unsuffixed record first where both are."""
    held = {}
    for path in sorted(Path(root).glob("ICC-*.json*")):
        basefile = path.name.split(".json")[0]
        m = RE_DOC_BASE.match(basefile.replace("_", "/"))
        if m:
            held.setdefault(m.group(0), basefile)
    return held


def refs(text, doc_number, root):
    """The inline-linkable citation spans of one block of decision text, as
    `lagrum.Ref`s: every treaty citation `treatyref.spans` resolves, plus the
    Court's own filing numbers -- an ICC decision cites its siblings by
    document number ("ICC-02/11-01/11-129") on nearly every page, and 1,687
    of those citations point at decisions the corpus holds."""
    own = RE_DOC_BASE.match(doc_number)
    numbers = []
    for m in RE_DOC_BASE.finditer(text):
        base = m.group(0)
        if (own and base == own.group(0)) or base not in _held(root):
            continue
        numbers.append(Ref(m.start(), m.end(), base, treatyref.PREDICATE,
                           decision_uri(_held(root)[base])))
    # the two grammars cannot overlap today, but interleave requires
    # disjoint spans and the treaty side rests on curated name data -- so
    # the merge filters like every other two-list caller, the filing
    # number (the Court's own identity) winning
    treaty = yield_overlaps(treatyref.refs(text, extra=SHORT_FORMS), numbers)
    return sorted(numbers + treaty, key=lambda ref: ref.start)
