"""Typed model for Europeiska dataskyddsstyrelsens riktlinjer och
rekommendationer -- the interpretive layer over a regulation the site already
carries, and the first *soft law* it publishes from outside Sweden.

A riktlinje is neither a rättsakt nor an avgörande. It binds nobody: the EDPB
states, in advance and in general, how the tillsynsmyndigheterna are to read
the allmänna dataskyddsförordningen, and the myndigheter and domstolar that
apply it are free to read it otherwise. What makes it worth carrying is that it
is the reading a Swedish reader of the förordning will actually meet -- IMY
cites these by number in its beslut, and the citation scan puts each of them on
the rail of the artikel it interprets, beside the förordning itself.

The document model follows from that shape:

  * **Identity is the number the EDPB gave it**, not a diarienummer and not a
    CELEX -- these documents have no CELEX at all, which is what keeps them out
    of the eurlex vertical. A riktlinje is published *as* a numbered item in a
    running series ("Riktlinjer 05/2020"), which is how the EDPB and everyone
    citing it names it. The artikel 29-gruppens endorsed vägledningar carry the
    working party's own WP number instead ("WP 248"), for the same reason.

  * **Language is a first-class fact.** These are EU documents, published in
    all 24 languages, and the site carries the Swedish version wherever the
    EDPB has issued one -- the whole point being that a Swedish reader meets
    the guidance in Swedish beside the Swedish statute. Three of them exist only
    in English, and a page that shows English text must say so rather than let
    the reader take it for an untranslated original.

  * **The version is not decoration.** A riktlinje is adopted, put out for
    public consultation, and re-adopted in a revised version, and the EDPB
    publishes both. Two things follow, and they pull in opposite directions. A
    reader arriving with no citation must land on the current version -- and
    since the site republishes these under the EDPB's own reuse terms ("the
    original meaning or message of the documents is not distorted"), stating
    which version that is is a condition of publishing them at all. But a
    *citation* names the version that existed when it was made, and the EDPB
    renumbers between versions, so punkt 42 of the adopted text is generally
    not punkt 42 of the draft: resolving such a citation onto the current
    version silently lands the reader on a different paragraph. Only the
    current version is carried today, and `edpb/KNOWN-GAPS.md` records what
    carrying the superseded ones would take -- the address has to be able to
    name a version, which it cannot yet.

  * **The numbered punkt is the citable unit.** The EDPB numbers every
    substantive paragraph, and that is what a decision citing one names ("punkt
    27 i riktlinjer 05/2020"). Each numbered paragraph therefore keeps its
    number as its anchor, so such a citation can land on the paragraph rather
    than the document.

URI scheme: ``https://lagen.nu/edpb/{serie}/{nummer}`` --
``https://lagen.nu/edpb/riktlinjer/05-2020``,
``https://lagen.nu/edpb/rekommendationer/01-2020``,
``https://lagen.nu/edpb/wp/248`` -- the avg/rs grammar with the series in place
of the myndighet, since it is the same kind of address: the issuing body's own
division of its output, then the identity it gave the document.
"""

from dataclasses import dataclass, field

from ..lib.catalog import BASE
from ..lib.lagrum import interleave
from .series import BY_KOD, KODER, number_slug

__all__ = ["KODER", "Block", "Fotnot", "Vagledning", "vagledning_identifier",
           "vagledning_uri"]


def vagledning_uri(serie, nummer):
    """The published document URI. Minted from the EDPB's own number, so a
    citation that names the number ("Riktlinjer 05/2020", "WP 248") and this
    page agree by construction -- the rule `avg.model.beslut_uri` follows for a
    dnr and `rs.model.rs_uri` for an agency's serienummer."""
    return "%sedpb/%s/%s" % (BASE, serie,
                             nummer if serie == "wp" else number_slug(nummer))


def vagledning_identifier(serie, nummer):
    """The citation form: "Riktlinjer 05/2020", "Rekommendation 01/2019",
    "WP 248". The number is kept exactly as the document writes it (the EDPB
    pads it in some years and not others) -- only the URI normalises."""
    return BY_KOD[serie].identifier % nummer


@dataclass
class Block:
    """One block of the document body. `punkt` is the EDPB's own paragraph
    number where the paragraph carries one, which becomes its anchor."""
    kind: str            # "rubrik" | "stycke"
    text: str
    level: int = 1       # rubrik nesting (1 title, 2 section, 3 subsection …)
    punkt: str | None = None


@dataclass
class Fotnot:
    """A note set below the running text. `mark` is the marker digit the
    document printed; `text` is the note body, citation-linked like any other.
    These carry the guidance's own apparatus -- the artikel 29-gruppens
    yttranden it builds on, the EU-domstolens judgments it reads -- which is
    most of what a riktlinje cites outside the förordning itself."""
    mark: str
    text: str


@dataclass
class Vagledning:
    serie: str                          # series.KODER
    nummer: str                         # the EDPB's own number, verbatim
    titel: str
    sprak: str = "sv"                   # "sv" | "en" -- which version this is
    antagen: str | None = None          # ISO date of adoption
    version: str | None = None          # "Version 2.0", "Final version", …
    revision: str | None = None         # a WP29 document's endorsed revision
    konsultation_url: str | None = None     # the pre-consultation first version
    amnesord: list[str] = field(default_factory=list)   # the EDPB's own topics
    body: list[Block] = field(default_factory=list)
    fotnoter: list[Fotnot] = field(default_factory=list)
    source_url: str | None = None       # the EDPB's own page for it
    document_url: str | None = None     # the PDF it was published as

    @property
    def uri(self):
        return vagledning_uri(self.serie, self.nummer)

    @property
    def identifier(self):
        return vagledning_identifier(self.serie, self.nummer)

    @property
    def publisher(self):
        return BY_KOD[self.serie].publisher

    def to_artifact(self, scanner):
        """The JSON artifact: the shared node convention (`structure` of
        rubrik/stycke nodes with inline-run text), every text scanned for
        citations -- which is what puts a riktlinje on the rail of the artikel
        it interprets. A numbered paragraph anchors on its own number
        (``#punkt27``), an unnumbered one on its position."""
        structure = []
        n = 0
        for b in self.body:
            runs = interleave(b.text, scanner.parse_text(b.text, context={}))
            if b.kind == "rubrik":
                structure.append({"type": "rubrik", "level": b.level,
                                  "text": runs})
                continue
            n += 1
            node = {"type": "stycke",
                    "id": "punkt%s" % b.punkt if b.punkt else "S%d" % n,
                    "text": runs}
            if b.punkt:
                node["punkt"] = b.punkt
            structure.append(node)
        footnotes = [{"mark": f.mark,
                      "text": interleave(f.text,
                                         scanner.parse_text(f.text, context={}))}
                     for f in self.fotnoter]
        metadata = {"title": self.titel, "publisher": self.publisher,
                    "nummer": self.nummer, "sprak": self.sprak}
        for key, value in (("antagen", self.antagen),
                           ("version", self.version),
                           ("revision", self.revision),
                           ("konsultation", self.konsultation_url)):
            if value:
                metadata[key] = value
        if self.amnesord:
            metadata["amnesord"] = self.amnesord
        art = {"uri": self.uri, "type": "vagledning", "serie": self.serie,
               "identifier": self.identifier, "metadata": metadata,
               "structure": structure}
        if footnotes:
            art["footnotes"] = footnotes
        if self.source_url:
            art["source_url"] = self.source_url
        if self.document_url:
            art["document_url"] = self.document_url
        return art
