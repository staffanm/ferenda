"""Repair the systematic character confusions in the Court's scanned Reports.

Every ICJ decision before ~2002 is published as a scan of the printed *I.C.J.
Reports* with an OCR text layer baked in. The layer is good but it carries a
small, systematic error class -- measured over ten decisions (203,745 tokens):

    "to secure the annulment of al1 the consequences"   (all, 1970)
    "an arnount corresponding to al1 the incidental damage"  (amount)
    "so that the Charnber is not called upon"           (Chamber, 1990)
    "tlie Court considers that witliin the meaning"     (the, within)

Two confusions dominate -- ``l`` read as ``1`` (400 occurrences) and ``m`` read
as ``rn`` (235) -- with five smaller ones behind them. Together they account for
about 0.43% of tokens, which is small in a page and fatal in a search index:
"Judgrnent" and "Corivention" are unfindable.

The repair is *dictionary-guided*, not a list of known bad words. A token is
rewritten only when a confusion turns it into a word the Court itself uses and
the token is not already one. That is what keeps "Article 1" and "VIII" intact:
neither becomes a word under any rule. A token that two rules turn into two
different words is left alone -- an ambiguous reading is not a repair.

The vocabulary is the Court's own, harvested from the decisions it published
*born-digital* (2002 onward, `tools/corpus/icj_vocabulary.py`). Those need no repair,
so the corpus that defines "a word" never depends on the repair being right.
"""

import functools
import re
from pathlib import Path

VOCABULARY = Path(__file__).resolve().parent / "data" / "vocabulary.txt"

# Ordered by how much each recovers. Both directions of the m/rn confusion are
# real: the scanner splits "m" into "rn" ("rnay", "Judgrnent") and fuses "rn"
# into "m" ("conceming" for concerning, "eastem" for eastern).
CONFUSIONS = (("rn", "m"), ("m", "rn"), ("1", "l"), ("1", "i"), ("li", "h"),
              ("ri", "n"), ("cl", "d"), ("c", "e"), ("vv", "w"), ("0", "o"))
# A token this short carries too little evidence: at two characters almost any
# substitution lands on some word ("88" -> "BB"), and the guard below cannot
# tell a recovery from a coincidence.
MIN_LENGTH = 3
# what counts as one token for repair: letters, digits and the apostrophe the
# Court sets in "State's". Punctuation and whitespace are separators and are
# copied through untouched.
RE_TOKEN = re.compile(r"[A-Za-z0-9']+")


@functools.lru_cache(maxsize=1)
def vocabulary():
    """The Court's own vocabulary, lower-cased."""
    return frozenset(VOCABULARY.read_text("utf-8").split())


def _candidates(token, known):
    """The distinct known words this token becomes under one confusion each."""
    found = set()
    for wrong, right in CONFUSIONS:
        candidate = token.replace(wrong, right)
        if candidate != token and candidate.lower() in known:
            found.add(candidate)
        # the same substitution on a capitalised token ("Judgrnent", "Cornmittee")
        lowered = token.lower().replace(wrong, right)
        if lowered != token.lower() and lowered in known:
            found.add(_recase(token, lowered))
    return found


def _recase(token, repaired):
    """Give the repaired word the token's own casing.

    All caps is a third shape and it matters: the Reports set every heading in
    it, and `parse._is_heading` tests `text == text.upper()`, so recasing one
    word of "JUDGRNENT OF THE COURT" to "Judgment" turns a heading into a
    paragraph."""
    if token.isupper():
        return repaired.upper()
    return repaired.capitalize() if token[:1].isupper() else repaired


def repair_token(token, known):
    """One token, repaired -- or unchanged when it is a number, already a word,
    too short to judge, or ambiguous between two readings.

    A token of nothing but digits is left alone whatever the rules say. It is
    the Court's own numbering, and the ``1``->``l`` and ``1``->``i`` rules read
    it as a misprint: paragraph "111." became "iii.", which silently ended the
    paragraph sequence and cost the 2012 Belgium v. Senegal judgment its
    numbering from paragraph 111 to the end.
    """
    if (len(token) < MIN_LENGTH or token.isdigit()
            or token.lower() in known or not any(c.isalpha() for c in token)):
        return token
    candidates = _candidates(token, known)
    return candidates.pop() if len(candidates) == 1 else token


def repair(text, known=None):
    """`text` with the scan's character confusions repaired, and the number of
    tokens that changed."""
    known = vocabulary() if known is None else known
    repairs = 0

    def one(match):
        nonlocal repairs
        repaired = repair_token(match.group(0), known)
        repairs += repaired != match.group(0)
        return repaired

    return RE_TOKEN.sub(one, text), repairs
