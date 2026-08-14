"""FN-fördragssidan: the instrument, its text and its parties.

Registered as this source's page renderer in `build.SOURCE_RENDERERS`;
`render` is the `(art, site) -> str` the generate driver calls.

The text comes from the depositary and the participation from the MTDSG, so
the page is the two halves in that order: the articles a citation lands on,
then the states bound by them.
"""

import re

from markupsafe import Markup

from ..lib import labels, tpl
from ..lib.page import (
    Rail,
    Toc,
    article_label,
    doc_meta,
    page_context,
    provision_section,
    render_toc,
)

ENV = tpl.environment("accommodanda.untc")

# the MTDSG's entry-into-force field is English prose ("16 November 1994, in
# accordance with article 308(1)."); the meta row is a date row, so the date
# is what it shows -- ISO, like the conclusionDate beside it
_MONTHS = {m: i + 1 for i, m in enumerate(
    ("January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"))}
_ENTRY_DATE = re.compile(r"(\d{1,2}) (%s) (\d{4})" % "|".join(_MONTHS))


def _entry_into_force(text):
    if not text:
        return None
    m = _ENTRY_DATE.match(text)
    return ("%s-%02d-%02d" % (m.group(3), _MONTHS[m.group(2)], int(m.group(1)))
            if m else text)


# the consent-to-be-bound forms an MTDSG participation records, in Swedish
UNTC_ACTIONS_SV = {
    "ratification": "ratificering", "accession": "anslutning",
    "succession": "succession", "formal confirmation": "formellt bekräftande",
    "acceptance": "godtagande", "approval": "godkännande",
}


def _untc_parties(parties):
    """The participation-table rows -- each state's signature and its binding
    consent (form + date), display-ready for the untc template."""
    return [{"country": party["country"],
             "signature": party.get("signature") or "",
             "consent": ("%s (%s)" % (party.get("actionDate") or "",
                                      UNTC_ACTIONS_SV.get(party["action"],
                                                          party["action"]))
                         if party.get("action") else "")}
            for party in parties]


def render(art, site):
    md = art.get("metadata", {})
    place, date = md.get("conclusionPlace"), md.get("conclusionDate")
    lb = labels.document_labels("untc", art)
    meta = [
        ("Titel", lb.official_title if lb.official_title != lb.short_title else None),
        ("Referens", md.get("reference")),
        ("Antagen", "%s, %s" % (place, date) if place and date else date),
        ("Ikraftträdande", _entry_into_force(md.get("entryIntoForce"))),
        ("Registrering (UNTS)", md.get("registration")),
        ("Depositarie", md.get("depositary")),
        ("Antal parter", str(md["statesParties"]) if md.get("statesParties") else None),
    ]
    toc = Toc()
    rail = Rail(site, art["uri"])
    # every node the model emits is an `artikel` -- an article, an annex
    # heading, or the preamble -- so one walk serves the whole treaty
    parts = [provision_section(node, site, art["uri"], toc, rail,
                               article_label(node))
             for node in art.get("structure", [])]
    toc.add("parter", "Parter", 1)
    rail.add_document()
    return ENV.get_template("untc.html").render(page_context(
        lb.short_title or lb.official_title, "FN-fördrag",
        doc_meta(meta, art.get("source_url")), toc=render_toc(toc, lb.short_id),
        eyebrow=lb.short_id, island=rail.island(),
        structure=Markup("".join(parts)),
        parties=_untc_parties(art.get("parties") or [])))
