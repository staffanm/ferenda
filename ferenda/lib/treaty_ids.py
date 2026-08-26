"""Dependency-free UN/IHL treaty article-fragment grammar.

Kept in its own leaf module (no imports from the rest of the package) for the
same reason as `lib.coe_ids`: both producers of a treaty provision fragment
share one implementation -- `lib.treatyref` (the international courts' English
citations, which also needs `catalog.BASE`) and the citation engine
`lib.lagrum` (which links "artikel 24 i barnkonventionen"). Importing
`lib.treatyref` from `lib.lagrum` would close the cycle
lagrum -> treatyref -> catalog -> markdown -> lagrum, so the pure part lives
here instead (rule:second-use-goes-to-lib).
"""


def arabic(number):
    """An article number as an integer, roman or Arabic, for the range check."""
    if str(number).isdigit():
        return int(number)
    value, total = {"I": 1, "V": 5, "X": 10, "L": 50}, 0
    text = str(number).upper()
    for index, char in enumerate(text):
        nxt = text[index + 1] if index + 1 < len(text) else ""
        total += -value[char] if nxt and value[char] < value[nxt] else value[char]
    return total


def roman(number):
    """An integer as the roman numeral a treaty anchors its articles under."""
    out, value = "", number
    for amount, sign in ((10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while value >= amount:
            out += sign
            value -= amount
    return out


def article_fragment(number, prefix="A", numerals="arabic"):
    """The anchor an article carries in a treaty artifact -- the grammar `coe`,
    `icrc` and `untc` all mint (``A8``, ``AII``).

    `prefix` is the target's own, because an instrument reproduced as an annex
    anchors its articles under it: the Hague Regulations are the annex to
    Convention (IV), so their article 42 is ``Annex42`` and ``A42`` points at
    nothing.

    `numerals` is the form the target numbers in, because the citation need not
    use it: the Genocide Convention runs Article I to XIX and a judgment that
    writes "article 1" still means ``#AI``."""
    value = arabic(number)
    written = roman(value) if numerals == "roman" else str(value)
    return "%s%s" % (prefix, written)
