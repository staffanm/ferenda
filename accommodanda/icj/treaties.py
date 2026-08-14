"""Which treaties in the corpus an ICJ decision applies.

This is why the source is here. The corpus already held the instruments the
Court interprets -- the Genocide Convention, the VCLT, UNCLOS, the ICCPR, CAT,
the Refugee Convention -- and every one of those pages had no inbound link at
all: nothing in the corpus cited them. An ICJ judgment is the document that
does, and saying so is what turns a status page into cited law.

The match is on the instrument's *authoritative English title* and the short
form the Court itself uses for it ("the Genocide Convention"), both taken from
the curated UNTC list through `lib.datasets` -- the same indirection
`lib/render.py` uses to read that file, so no source imports a sibling
(rule:lib-never-imports-vertical).

Scope is deliberately the treaty, not the article. The MTDSG carries status
only -- dates, registration, per-state participation -- and no treaty text, so
an untc artifact has no article to anchor to. Article-level references onto the
`icrc` humanitarian-law treaties, which do carry their articles, are the
follow-up this leaves room for.
"""

import functools
import json
import re

from ..lib import datasets
from ..lib.catalog import BASE

PREDICATE = "dcterms:references"
# A short form is only usable if it is *distinctive*. "the Convention" names
# whichever instrument the decision is about and would cite every treaty in
# every decision, so a short form has to carry the instrument's own subject
# word. These are the forms the Court uses, checked against the curated titles.
SHORT_FORMS = {
    "XXIII-1": ("Vienna Convention on the Law of Treaties",),
    "XXI-6": ("United Nations Convention on the Law of the Sea",
              "Law of the Sea Convention"),
    "IV-1": ("Genocide Convention",),
    "IV-2": ("Convention on the Elimination of All Forms of Racial "
             "Discrimination", "CERD"),
    "IV-3": ("International Covenant on Economic, Social and Cultural Rights",),
    "IV-4": ("International Covenant on Civil and Political Rights",),
    "IV-8": ("Convention on the Elimination of Discrimination against Women",),
    "IV-9": ("Convention against Torture",),
    "IV-11": ("Convention on the Rights of the Child",),
    "IV-15": ("Convention on the Rights of Persons with Disabilities",),
    "IV-16": ("Convention for the Protection of All Persons from Enforced "
              "Disappearance",),
    "V-2": ("Convention relating to the Status of Refugees",
            "1951 Refugee Convention"),
    "V-5": ("Protocol relating to the Status of Refugees",),
}
# An acronym is matched case-sensitively and as a whole word: "CAT" and "CRC"
# are ordinary words away from being noise, and the Court sets them in capitals.
ACRONYMS = {"UNCLOS": "XXI-6", "ICCPR": "IV-4", "ICESCR": "IV-3",
            "CERD": "IV-2", "CEDAW": "IV-8", "CRPD": "IV-15"}


def treaty_uri(mtdsg_no):
    """The instrument's page. Keyed on its UNTS registration number, which is
    `untc`'s identity -- the curated list maps the MTDSG id this module keys its
    patterns by onto it."""
    return "%sext/untc/%s" % (BASE, _unts()[mtdsg_no])


@functools.lru_cache(maxsize=1)
def _unts():
    """{mtdsg_no: UNTS number}, read through `lib.datasets` rather than by
    importing the sibling source (rule:lib-never-imports-vertical)."""
    return {t["mtdsg_no"]: t["unts"]
            for t in json.loads(datasets.UNTC_TREATIES.read_text("utf-8"))["treaties"]}


@functools.lru_cache(maxsize=1)
def _patterns():
    """(compiled pattern, mtdsg_no, the name it matches) for every curated
    instrument, longest name first so the full title wins over a short form
    that is a prefix of it."""
    curated = json.loads(datasets.UNTC_TREATIES.read_text("utf-8"))["treaties"]
    named = []
    for treaty in curated:
        number = treaty["mtdsg_no"]
        for name in (treaty["title"], *SHORT_FORMS.get(number, ())):
            named.append((re.compile(r"\b%s\b" % re.escape(name), re.I),
                          number, name))
    for acronym, number in ACRONYMS.items():
        named.append((re.compile(r"\b%s\b" % acronym), number, acronym))
    return sorted(named, key=lambda entry: -len(entry[2]))


def references(text):
    """The curated treaties this decision names, as artifact `references`.

    One reference per instrument however often the decision names it: these are
    document-level relations ("this judgment applies that treaty"), not the
    literal spans a body walk collects, and the Court names the instrument it is
    deciding under on nearly every page.
    """
    seen = {}
    for pattern, number, name in _patterns():
        if number in seen:
            continue
        match = pattern.search(text)
        if match:
            seen[number] = {"uri": treaty_uri(number), "predicate": PREDICATE,
                            "text": match.group(0) if name.isupper() else name}
    # ordered by the URI the consumer sees, not by the MTDSG id this module
    # keys its patterns on -- that key is internal, and sorting on it put the
    # references in an order nothing downstream could predict
    return sorted(seen.values(), key=lambda reference: reference["uri"])
