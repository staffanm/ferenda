"""Small text utilities (ported from ferenda.util)."""

import re

ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}

# matches only well-formed roman numerals
re_roman = re.compile(
    r"^M?M?M?(CM|CD|D?C?C?C?)(XC|XL|L?X?X?X?)(IX|IV|V?I?I?I?)$").match


def normalize_space(s):
    return " ".join(s.split())


def split_numalpha(s):
    """'10 a §' -> ['', 10, ' a §'], so strings with mixed numbers and
    letters sort naturally."""
    res = []
    seg = ""
    if not s:
        return res
    if s[0].isdecimal():
        res.append("")  # every list starts with a string, so elements at
        # the same index always have comparable types
    digit = s[0].isdecimal()
    for c in s:
        if c.isdecimal() == digit:
            seg += c
        else:
            res.append(int(seg) if digit else seg)
            seg = c
            digit = not digit
    res.append(int(seg) if digit else seg)
    return res


def numcmp(x, y):
    nx, ny = split_numalpha(x), split_numalpha(y)
    return (nx > ny) - (nx < ny)


def from_roman(s):
    s = s.upper()
    total = 0
    prev = 0
    for c in reversed(s):
        val = ROMAN_VALUES[c]
        total += val if val >= prev else -val
        prev = max(prev, val)
    return total


SWEDISH_ORDINALS = ("första", "andra", "tredje", "fjärde", "femte", "sjätte",
                    "sjunde", "åttonde", "nionde", "tionde", "elfte", "tolfte")
SWEDISH_ORDINAL_MAP = {word: i + 1 for i, word in enumerate(SWEDISH_ORDINALS)}


def swedish_ordinal(s):
    """'första' -> 1, or None"""
    return SWEDISH_ORDINAL_MAP.get(s.lower())
