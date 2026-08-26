"""Which Convention provisions an ECHR text applies, as inline links.

The Court writes its own instrument by short form on nearly every page --
"Article 8 of the Convention", "Article 1 of Protocol No. 1" -- and the
corpus holds every one of those targets: coe/005 with its 59 article anchors,
and the full protocol series. The matching is `lib.treatyref`'s; what is
local here is the Court's own shorthand, safe only inside an ECHR text:

  * **"the Convention"** is the ECHR and nothing else. The pattern stands
    down where a longer title continues it ("the Convention on the Rights of
    the Child" must keep naming the CRC), and the capital C is load-bearing:
    a lower-cased "the convention" in running prose is the sentence's word,
    not the Court's. The continuation word carries the Court's own
    capitalisation -- it writes "the Convention Against Torture" as often as
    "against" -- and a Geneva convention continues in a roman numeral instead
    ("the Convention (IV) relative to the Protection of Civilian Persons").
    A curated title that starts one word later now wins the span either way
    (`treatyref._named`), so this guard is what protects the titles the table
    does *not* hold.
  * **"Protocol No. N"** names the ECHR protocol series. Outside an ECHR
    text the same words number a different family (the CoE corpus holds four
    treaty families with numbered protocols), which is why these are caller
    extras rather than curated names.

The curated full titles (the ECHR long form, the UN conventions an ECHR
judgment compares against) match through the same call with no local help.
"""

import re

from ..lib import treatyref
from ..lib.coe import ECHR_PROTOCOLS

CONVENTION = "coe/005"
# the protocols HUDOC's article facet does not reach: amending/procedural
# instruments, cited by number in the Court's procedural history sections
AMENDING_PROTOCOLS = {
    "2": "044",
    "3": "045",
    "5": "055",
    "8": "118",
    "9": "140",
    "10": "146",
    "11": "155",
    "14bis": "204",
    "15": "213",
}
SHORT_FORMS = (
    (re.compile(r"\b[Tt]he Convention\b"
                r"(?!\s+(?i:on|for|against|of|relating|concerning|to)\b)"
                r"(?!\s+\((?=[IVX]+\)))"),
     CONVENTION),
) + tuple(
    (re.compile(r"\bProtocol No\.\s*%s\b" % number), "coe/%s" % ets)
    for number, ets in sorted((ECHR_PROTOCOLS | AMENDING_PROTOCOLS).items()))


def refs(text):
    """Every treaty citation in one block of ECHR text, as `lagrum.Ref`
    spans: the Convention and protocol short forms above, article-anchored
    where the instrument is curated, plus every curated full title."""
    return treatyref.refs(text, extra=SHORT_FORMS)
