"""Vägledningssidan: an EU-level body's guidance, its body and its sections.

Registered as this source's page renderer (the `render=` field of its
`build.py` registration);
`render` is the `(art, site) -> str` the generate driver calls.
"""

from markupsafe import Markup

from ..lib import catalog, layout, tpl
from ..lib.page import (
    BANNERS,
    doc_meta,
    document_body,
    footnote_items,
    page_context,
    render_toc,
)

ENV = tpl.environment("ferenda.guidance")


# what one document of a series is called in the singular, for the section
# line. The registry holds the plural (the collection's name), which is not
# derivable from it in Swedish ("Riktlinjer" -> "Riktlinje", but "Artikel
# 29-gruppens vägledningar" -> "Artikel 29-gruppens vägledning"), so it is
# written down per series here rather than guessed.
SECTION = {("edpb", "riktlinjer"): "Riktlinje",
           ("edpb", "rekommendationer"): "Rekommendation",
           ("edpb", "wp"): "Artikel 29-gruppens vägledning",
           ("eba", "gl"): "Riktlinje",
           ("eba", "rec"): "Rekommendation",
           # Esma runs one series holding both, and its covers alternate
           # between the two words, so the singular names both as well
           ("esma", "riktlinjer"): "Riktlinje eller rekommendation",
           # EASA issues its AMC/GM in English and its readers name them in
           # English; a Swedish rendering of "AMC" would be an invention, not a
           # translation
           ("easa", "amc-gm"): "AMC & GM",
           ("easa", "amc"): "AMC",
           ("easa", "gm"): "GM",
           ("enisa", "rapporter"): "Rapport",
           ("berec", "riktlinjer"): "Riktlinje",
           # EUIPO's three volumes are riktlinjer whichever IP right they are
           # about, and one document is one del or avsnitt of one of them. The
           # section line says what kind of document the reader is on, which is
           # the same word for all three; which volume it belongs to is the
           # eyebrow's job, since the identifier names it ("Trade mark
           # guidelines, Part C Opposition, Section 0 Introduction")
           ("euipo", "varumarke"): "Riktlinje",
           ("euipo", "formgivning"): "Riktlinje",
           ("euipo", "gi"): "Riktlinje"}


def _ersatt_av(md, site):
    """The successor as the banner needs it: ``{label, url}``.

    The label is the number the issuing body printed, and the link goes to the
    successor's page here where the corpus holds it. Where it does not -- a
    harvest can know a wording was replaced without being able to name what
    replaced it -- the link goes to the body's own page for it instead, and the
    banner says only that a later wording exists. A url is never used as a
    label: "Ersatt av https://www.eba.europa.eu/activities/…" reads as a
    defect, which is what printing an address in place of a name is."""
    uri = md.get("ersattAv")
    if uri and site.has(uri):
        return {"label": md.get("ersattAvIdentifier") or catalog.local(uri),
                "url": layout.page_url(uri)}
    return {"label": md.get("ersattAvIdentifier"),
            "url": md.get("ersattAvKalla")}


def render(art, site):
    """An EDPB riktlinje/rekommendation, or an endorsed artikel 29-gruppens
    vägledning.

    Two things separate it from every other document on the site, and both are
    said in a banner rather than left to the metadata list. Which **version**
    this is: the EDPB adopts, consults on and re-adopts these, publishes both,
    and republishing one without saying which would misstate what the board
    says today -- which the EDPB's own reuse terms ("the original meaning or
    message of the documents is not distorted") make a condition of publishing
    it at all. And which **language**: three of them exist in no Swedish version,
    and a reader meeting English text on a Swedish site should be told why
    before they start reading rather than after."""
    md = art.get("metadata", {})
    ident = art.get("identifier") or catalog.local(art["uri"])
    meta = [
        ("Utgivare", md.get("publisher")),
        ("Antagen", md.get("antagen")),
        ("Version", md.get("version")),
        # the instrument a body's guidance was issued as an annex to, where it
        # issues it that way -- one ED Decision issues four EASA-annexes, so the
        # decision is what a reader follows back to the explanatory note
        ("Utfärdat genom", md.get("beslut")),
        ("Revision", md.get("revision")),
        ("Språk", {"sv": "svenska", "en": "engelska"}.get(md.get("sprak"))),
        ("Ämnesord", ", ".join(md.get("amnesord", [])) or None),
    ]
    structure, toc, rail = document_body(art, site)
    utgivare = md.get("publisher")
    banner = Markup("").join(part for part in (
        BANNERS.vagledning_language_banner(utgivare, art.get("source_url"))
        if md.get("sprak") == "en" else "",
        BANNERS.vagledning_version_banner(utgivare, md["version"],
                                          md.get("konsultation"))
        if md.get("version") else "",
        BANNERS.vagledning_ersatt_banner(utgivare, _ersatt_av(md, site))
        if md.get("status") == "upphävt" else "") if part)
    return ENV.get_template("vagledning.html").render(page_context(
        md.get("title") or ident,
        SECTION.get((art.get("utgivare"), art.get("serie")), "Vägledning"),
        doc_meta(meta, art.get("source_url")),
        toc=render_toc(toc, ident), eyebrow=ident,
        banner=banner,
        footnotes=footnote_items(art.get("footnotes", []), site,
                                  backref=False),
        island=rail.island(), structure=structure))
