"""The three series this vertical carries, as *data*: what each is called, how
a document in it is numbered, and how that number is cited.

Europeiska dataskyddsstyrelsen (EDPB) publishes its guidance in two open,
running series -- ``Riktlinjer NN/ÅÅÅÅ`` and ``Rekommendation(er) NN/ÅÅÅÅ`` --
which is the identity a citation names and the identity these documents get
here. The third series is **closed**: the artikel 29-gruppens vägledningar that
the EDPB endorsed whole at its first plenary on 25 May 2018 (Endorsement
1/2018). Those carry no NN/ÅÅÅÅ number at all; they are cited by the WP number
the working party gave them ("WP 248", "wp248rev.01"), and since the group
ceased to exist with the GDPR, no more can appear. So they are listed here
individually rather than harvested from an index -- a closed corpus of seven
documents is *data*, and writing it down is what lets each entry be verified.

Why they need listing at all: the EDPB publishes a page per endorsed WP29
document, but those pages are **stubs**. Five carry no file whatsoever (only a
link out to the Commission newsroom archive where the working party actually
published), one links an unrelated Danish decision, and one -- the breach
notification guideline, WP250 -- is titled "Dataskyddsombud", which is a
different document entirely. Two separate pages exist for WP242 and two for
WP260. None of that is harvestable as a series, and none of it can be trusted
for identity, so what the stub page supplies is exactly one thing: the URL a
reader should be sent to as the source. Everything else comes from the newsroom
item (the WP number and revision, in the item's own title) and from the Swedish
PDF (its title, off the cover).

The newsroom item id is the stable identity on that side; it is recorded here
rather than resolved through the EDPB stub, because the stub's link is the part
that has already been observed wrong.
"""

import re
from dataclasses import dataclass

EDPB_NAME = "Europeiska dataskyddsstyrelsen"
WP29_NAME = "Artikel 29-gruppen"
BASE = "https://www.edpb.europa.eu"
# where the working party's own documents live: the Commission newsroom archive
# the EDPB stubs point into. An item page names the WP number in its title and
# links the English PDF beside a ZIP of every language version.
NEWSROOM = "https://ec.europa.eu/newsroom/article29/items/%s"


@dataclass(frozen=True)
class Series:
    """One guidance series."""
    kod: str                 # our short code, the URI segment ("riktlinjer")
    doctype: str | None      # the EDPB site's own document-type path segment,
                             # None for the closed WP29 series, which the EDPB
                             # site does not publish (see WP29 below)
    publisher: str           # who issues it (dcterms:publisher)
    identifier: str          # citation form, %-formatted with the number
    label: str               # what the collection is called in Swedish
    note: str = ""           # what is peculiar about this series


REGISTRY = (
    Series(kod="riktlinjer", doctype="guideline", publisher=EDPB_NAME,
           identifier="Riktlinjer %s", label="Riktlinjer",
           note="the main series; the EDPB numbers it inconsistently "
                "(01/2020 beside 1/2018), so the number is normalised for the "
                "URI and kept verbatim for the citation"),
    Series(kod="rekommendationer", doctype="recommendation", publisher=EDPB_NAME,
           identifier="Rekommendation %s", label="Rekommendationer",
           note="the same numbering; the EDPB's own Swedish titles alternate "
                "between 'Rekommendation 01/2019' and 'Rekommendationer "
                "02/2020', and the title is left as the document wrote it"),
    Series(kod="wp", doctype=None, publisher=WP29_NAME,
           identifier="WP %s", label="Artikel 29-gruppens vägledningar",
           note="closed: the seven GDPR-related WP29 documents the EDPB "
                "endorsed on 25 May 2018, enumerated in WP29 below"),
)

BY_KOD = {series.kod: series for series in REGISTRY}
KODER = tuple(series.kod for series in REGISTRY)
# the two open series, which are the ones harvested from the EDPB site's index
HARVESTED = tuple(series.kod for series in REGISTRY if series.doctype)


@dataclass(frozen=True)
class Wp29:
    """One endorsed artikel 29-gruppen document."""
    number: str              # the WP number, bare ("248")
    revision: str            # the revision the endorsed text carries ("rev.01")
    item: str                # its Commission newsroom item id
    page: str                # the EDPB page that endorses it (the source url)
    subject: str             # what it is about, for the harvest report only


WP29 = (
    Wp29("242", "rev.01", "611233", BASE + "/documents/guideline/"
         "right-to-data-portability", "dataportabilitet"),
    Wp29("243", "rev.01", "612048", BASE + "/documents/guideline/"
         "data-protection-officer", "dataskyddsombud"),
    Wp29("244", "rev.01", "611235", BASE + "/documents/guideline/"
         "guidelines-for-identifying-a-controller-or-processors-lead-supervisory",
         "ansvarig tillsynsmyndighet"),
    Wp29("248", "rev.01", "611236", BASE + "/documents/guideline/"
         "data-protection-impact-assessments-high-risk-processing",
         "konsekvensbedömning"),
    Wp29("250", "rev.01", "612052", BASE + "/documents/guideline/"
         "guidelines-on-personal-data-breach-notification-under-regulation-2016679-wp250",
         "anmälan av personuppgiftsincidenter"),
    Wp29("251", "rev.01", "612053", BASE + "/documents/guideline/"
         "automated-decision-making-and-profiling",
         "automatiserat beslutsfattande och profilering"),
    Wp29("260", "rev.01", "622227", BASE + "/documents/guideline/"
         "article-29-working-party-guidelines-on-transparency-under-regulation-2016679",
         "öppenhet"),
)

WP29_BY_NUMBER = {wp.number: wp for wp in WP29}

# the EDPB pages that duplicate an entry above -- a second page for the same
# WP29 document, kept out of the harvest so one document does not arrive twice
# under two identities
WP29_DUPLICATE_PAGES = frozenset((
    BASE + "/documents/guideline/transparency",                    # WP260 again
    BASE + "/documents/guideline/"
    "guidelines-on-the-right-to-data-portability-under-regulation-2016679-wp242",
))

# "05/2020", and the unpadded "1/2018" the EDPB writes just as often
RE_NUMBER = re.compile(r"\b(\d{1,2})/(\d{4})\b")


def number_slug(number):
    """The URI/file form of a series number. The EDPB pads the löpnummer to two
    digits in some years and not others -- "Riktlinjer 05/2020" beside
    "Riktlinjer 1/2018" -- and a citation copies whichever it saw, so the slug
    normalises to the padded form and one document has one address however it
    was written ("5/2020" and "05/2020" -> ``05-2020``)."""
    match = RE_NUMBER.fullmatch(number.strip())
    assert match, "not an EDPB series number: %r" % number
    return "%02d-%s" % (int(match.group(1)), match.group(2))
