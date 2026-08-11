"""The agencies whose rättsliga ställningstaganden this vertical carries, as
*data*: one entry per myndighet, naming its listing, how it designates a
ställningstagande and how that designation is cited.

This is the `foreskrift/agencies.py` idea at a smaller scale -- one harvest
engine configured per agency rather than one pipeline per agency -- but the
listings differ too much for the extraction itself to be table-driven: FI writes
a hand-authored HTML table, Kronofogden a year-grouped document list,
Försäkringskassan a per-year file widget, IMY a CMS page of info blocks,
Konkurrensverket a table of links to per-document pages, Migrationsverket
publishes through the Lifos database's search UI, and Skatteverket ships its
whole register as one JSON payload inside a JavaScript-protected page. So each
agency keeps a small listing reader (in `download.py`, or in `skv.py` for the
one whose listing is big enough to own a module), and what lives here is
everything that is genuinely *data*: identity, naming and provenance.

Numbering. Six of the seven agencies number their ställningstaganden in a series
of their own, and four do it in the familiar ``år:löpnummer`` form. Only two have
published a short designation for the series -- Försäkringskassan writes "FKRS
2020:2" in its own prose and IMY prints "IMYRS 2024:1" on the document -- so
those are used verbatim and the rest are cited the way their own page names
them ("Konkurrensverkets ställningstagande 2025:1"). Nothing is invented: an
agency that has coined no acronym does not get one here.

The identity that names the document is the agency's own number, not a
diarienummer: unlike a beslut (see `avg/model.py`) a ställningstagande is
published *as* a numbered item in a series, which is how the agency itself and
everyone citing it refers to it. **Skatteverket is the one exception**, and it
is the agency's own doing: it numbers no series at all and cites its own
ställningstaganden by date and diarienummer ("Skatteverkets ställningstagande
2026-07-06, dnr 8-207888-2026"), so there the dnr *is* the published
designation and this rule points at it rather than away from it.

Transport. Six agencies serve ordinary HTTP. Skatteverket sits behind the same
F5/Shape JavaScript challenge the SKVFS föreskrift harvest meets, so it needs
the detached headful-Chrome transport (`lib.browser`) -- slow, serial and
process-global in its display, which is why `browser` splits it out of the
default `lagen rs download` sweep and onto `lagen rs browser-download`.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Agency:
    """One myndighet's ställningstagande series."""
    org: str                     # our short code, the URI segment ("fk")
    name: str                    # the myndighet, spelled out (dcterms:publisher)
    listing: str                 # the page the harvest walks
    identifier: str              # citation form, %-formatted with the number
    note: str = ""               # what is peculiar about this agency's listing
    browser: bool = False        # detached headful Chrome instead of HTTP (F5: skv)
    page_body: bool = False      # the document is a web page, not a PDF (skv)


REGISTRY = (
    Agency(org="imy",
           name="Integritetsskyddsmyndigheten",
           listing="https://www.imy.se/om-oss/beslut-publikationer-och-remisser"
                   "/rattsliga-stallningstaganden/",
           identifier="IMYRS %s",
           note="the only agency that publishes the full text as a web page; "
                "the PDF beside it is a rendering of the same text"),
    Agency(org="fi",
           name="Finansinspektionen",
           listing="https://www.fi.se/sv/publicerat/rattsliga-stallningstaganden/",
           identifier="FI:s rättsliga ställningstagande %s",
           note="a hand-authored table that keeps repealed ställningstaganden "
                "listed with a Status column, so the repeal is harvestable"),
    Agency(org="fk",
           name="Försäkringskassan",
           listing="https://www.forsakringskassan.se/om-forsakringskassan"
                   "/vagledningar-och-rattsliga-stallningstaganden"
                   "/rattsliga-stallningstaganden",
           identifier="FKRS %s",
           note="the largest series (2005-); the PDF's own Serienummer field is "
                "authoritative for the number, the listing having at least one "
                "typo (a 2026:01 listed as 2026:03)"),
    Agency(org="kfm",
           name="Kronofogdemyndigheten",
           listing="https://kronofogden.se/om-kronofogden"
                   "/dina-rattigheter-lagar-och-regler/stallningstaganden",
           identifier="Kronofogdens ställningstagande %s",
           note="numbers run löpnummer/år with a verksamhets suffix "
                "(1/23/VER, 2/21/RKF); only gällande ställningstaganden are listed"),
    Agency(org="migr",
           name="Migrationsverket",
           listing="https://lifos.migrationsverket.se/sokning/detaljerad-sokning.html",
           identifier="%s",
           note="published through the Lifos database, whose numbers "
                "(RS/028/2021) are already unambiguous; revised in place, so a "
                "record carries the version the database currently serves"),
    Agency(org="kkv",
           name="Konkurrensverket",
           listing="https://www.konkurrensverket.se/om-oss/stallningstaganden/",
           identifier="Konkurrensverkets ställningstagande %s",
           note="the förteckning keeps repealed and superseded entries, naming "
                "what replaced them; a repealed one usually keeps no document"),
    Agency(org="skv",
           name="Skatteverket",
           listing="https://www4.skatteverket.se/rattsligvagledning/121.html",
           identifier="Skatteverkets ställningstagande dnr %s",
           browser=True,
           page_body=True,
           note="by far the largest series (2,614 documents, 2004-) and the "
                "only one with no series number: Skatteverket cites its own "
                "positions by date and diarienummer, so the dnr is the "
                "identity. Behind the F5/Shape challenge, and published as web "
                "pages rather than PDFs"),
)

BY_ORG = {agency.org: agency for agency in REGISTRY}
ORGS = tuple(agency.org for agency in REGISTRY)
# The transport split. A browser agency drives headful Chrome on the
# process-global DISPLAY, one navigation at a time, so it cannot share a run
# with the HTTP agencies and is kept off the default sweep -- the föreskrift
# `browser_scopes`/`default_scopes` rule, at rs scale.
BROWSER_ORGS = tuple(agency.org for agency in REGISTRY if agency.browser)
DEFAULT_ORGS = tuple(agency.org for agency in REGISTRY if not agency.browser)


# A ställningstagande's number as it appears in a URI and a file name: the
# agency's own designation with its separators reduced to what a path segment
# takes. Three shapes occur -- "2025:01" (four agencies, the SFS-like
# år:löpnummer these series follow), the slash-separated "1/23/VER" /
# "RS/028/2021", and Skatteverkets diarienummer ("8-193984-2026", and before
# 2020 "131 297826-13/111", whose space goes the same way as a slash) -- and
# only the slash and the space have to go, the colon being what the whole site
# already spells an author's number with (`2018:585`, `fffs/2013:10`).
RE_UNSAFE = re.compile(r"[/\s]+")


def number_slug(number):
    """The URI/file form of an agency's own number ("1/23/VER" -> "1-23-VER")."""
    return RE_UNSAFE.sub("-", number.strip()).strip("-")
