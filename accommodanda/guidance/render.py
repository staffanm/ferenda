"""Vägledningssidan: an EU-level body's guidance, its body and its sections.

Registered as this source's page renderer in `build.SOURCE_RENDERERS`;
`render` is the `(art, site) -> str` the generate driver calls.
"""

from markupsafe import Markup

from ..lib import catalog, tpl
from ..lib.page import (
    BANNERS,
    doc_meta,
    document_body,
    footnote_items,
    page_context,
    render_toc,
)

ENV = tpl.environment("accommodanda.guidance")


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
        if md.get("version") else "") if part)
    return ENV.get_template("vagledning.html").render(page_context(
        md.get("title") or ident,
        SECTION.get((art.get("utgivare"), art.get("serie")), "Vägledning"),
        doc_meta(meta, art.get("source_url")),
        toc=render_toc(toc, ident), eyebrow=ident,
        banner=banner,
        footnotes=footnote_items(art.get("footnotes", []), site,
                                  backref=False),
        island=rail.island(), structure=structure))
