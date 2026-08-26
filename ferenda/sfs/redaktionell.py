"""Detect a stycke that is the publisher's *editorial note* rather than statute
text.

Both forms below are ordinary prose in the SFST text database -- one `<p>` like
any other -- so nothing downstream can tell them from the statute's own wording.
That is not a cosmetic problem: any measurement or search over "the text of the
law" reads them as the law. It showed up in the corpus statistics, where all
twelve rows of "De kortaste lagarna" were editorial notes rather than short laws
(1840:46's entire body is the 49-character `/Författningens text finns bara i
tryckt version/`).

Two sorts, deliberately kept apart because they say opposite things about the
corpus:

* ``endast-tryckt`` -- *we* are missing the text; the published SFS has it, the
  text database does not. A gap in the corpus, and the same kind of loss
  `graphics.py` detects for omitted figures and tables. 23 base acts.
* ``upphavd`` -- *nothing* is missing: the act is repealed, and this notice is
  the whole of what the publisher still carries. 3 base acts.

Distinct from `graphics.py`, whose markers sit *inside* an otherwise intact
document and name one omitted figure. These replace the body outright. Also
distinct from `extract.py`'s ``(Författningstext saknas)``, which is our own
placeholder for an empty fetch, not something the publisher wrote.

The detection is a projection-time overlay (nf.py), exactly like the graphics
markers and the reference links: the typed model keeps the raw stycke text, and
the normal form replaces it with a typed ``redaktionell`` node. Nothing is
hidden from the reader -- the note still renders, subdued -- but it is no longer
indistinguishable from statute text.
"""

import re

# `/Författningens text finns bara i tryckt version/`. The slash delimiters are
# the publisher's own editorial convention (the same one graphics.py's markers
# wear); the wording varies across the 23 occurrences ("Författningen",
# "Bilagan", "Texten"), so the noun is not pinned. The marker must be the whole
# stycke: this must not fire on a sentence that merely mentions print.
RE_ENDAST_TRYCKT = re.compile(
    r"^/[^/]{0,80}?finns\s+(?:bara|endast)\s+i\s+tryckt\s+version\.?/$", re.I)

# `Har upphävts genom förordning (2024:1330).`, and the variant naming the
# paragraf it replaced -- `4 § har upphävts genom lag (1982:1101)`. Anchored and
# length-bounded so it cannot swallow a real provision that happens to *discuss*
# a repeal ("Bestämmelserna i 4 § har upphävts genom …" is prose about the law,
# and runs well past this).
#
# The act type and *each* parenthesis are independently optional on purpose, not
# by oversight: the publisher's own text is inconsistent, and requiring the tidy
# form drops 36 of the 306 real notices in the corpus -- `Har upphävts genom
# förordning 2006:1412).` (no opening paren), `har upphävts genom förordning
# (1996:1302.` (no closing one) and `Har upphävts genom lag 2025:729` (neither).
# Those are the publisher's typos; refusing to read them would leave exactly the
# provisions this module exists to type sitting in the statistics as text.
RE_UPPHAVD = re.compile(
    r"^(?:\d+\s*[a-z]?\s*§\s*)?har\s+upphävts\s+genom\s+"
    r"(?:lag|förordning|föreskrifter|kungörelse|beslut)?\s*"
    r"\(?(?P<sfs>\d{4}:\w+)\)?\s*\.?$", re.I)

MAX_LEN = 120           # an editorial note is one short sentence, never a body


def editorial(text):
    """``(sort, satt_av)`` when `text` is *only* an editorial note, else None.
    `satt_av` is the SFS the note names (``upphavd`` only), so the repeal stays
    linkable; ``endast-tryckt`` names nothing and carries None."""
    text = text.strip()
    if len(text) > MAX_LEN:
        return None
    if RE_ENDAST_TRYCKT.match(text):
        return ("endast-tryckt", None)
    m = RE_UPPHAVD.match(text)
    return ("upphavd", m.group("sfs")) if m else None
