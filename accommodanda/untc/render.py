"""FN-fördragssidan: the instrument and its parties.

Registered as this source's page renderer in `build.SOURCE_RENDERERS`;
`render` is the `(art, site) -> str` the generate driver calls.
"""

from ..lib import labels, tpl
from ..lib.page import Rail, doc_meta, page_context

ENV = tpl.environment("accommodanda.untc")


# the consent-to-be-bound forms an MTDSG participation records, in Swedish
UNTC_ACTIONS_SV = {
    "ratification": "ratificering", "accession": "anslutning",
    "succession": "succession", "formal confirmation": "formellt bekräftande",
    "acceptance": "godtagande", "approval": "godkännande",
}


def _untc_parties(parties):
    """The participation-table rows -- each state's signature and its binding
    consent (form + date), display-ready for the untc template. The MTDSG
    carries no treaty text, so this table is the page's body."""
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
        ("Ikraftträdande", md.get("entryIntoForce")),
        ("Registrering (UNTS)", md.get("registration")),
        ("Depositarie", md.get("depositary")),
        ("Antal parter", str(md["statesParties"]) if md.get("statesParties") else None),
    ]
    rail = Rail(site, art["uri"])
    rail.add_document()
    return ENV.get_template("untc.html").render(page_context(
        lb.short_title or lb.official_title, "FN-fördrag",
        doc_meta(meta, art.get("source_url")), eyebrow=lb.short_id,
        island=rail.island(), parties=_untc_parties(art.get("parties") or [])))
