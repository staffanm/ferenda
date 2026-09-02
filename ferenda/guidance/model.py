"""Typed model for EU-level vägledning -- the interpretive layer over rättsakter
the site already carries.

One model serves every issuing body in `issuers.REGISTRY`, because the document
shape does not vary with the body: a numbered document in a running series,
issued in a stated version and a stated language, whose numbered punkt is what
a citation names. What varies is *data* -- the body, its series, its numbering
-- and that lives in `issuers`, not in a subclass here (rule:no-source-base-class).

Three fields are first-class rather than decoration, and each is load-bearing
for a different reason:

  * **utgivare** is now part of the identity. The source carries several
    bodies, and the same number means different documents under different ones:
    "Riktlinjer 05/2020" is the EDPB's, "EBA/GL/2020/05" the EBA:s.

  * **sprak**. These are EU documents, and the site carries the Swedish version
    wherever the body has issued one -- the whole point being that a Swedish
    reader meets the guidance in Swedish beside the Swedish rättsakt. Some
    exist only in English, and a page showing English text must say so rather
    than let the reader take it for an untranslated original.

  * **version**. Guidance is adopted, put out for public consultation, and
    re-adopted in a revised version, and the bodies publish both. A reader
    arriving with no citation must land on the current version; a *citation*
    names the version that existed when it was made, and the punkt numbering
    generally differs between them, so resolving such a citation onto the
    current version would silently land the reader on a different paragraph.

URI scheme: ``https://lagen.nu/guidance/{utgivare}/{serie}/{nummer}`` --
``guidance/edpb/riktlinjer/05-2020``, ``guidance/edpb/wp/248``,
``guidance/eba/gl/2021-05``. A body that numbers in one sequence across its
document types takes no serie segment (``guidance/esrb/2014-1``): the URI
reproduces the citation, and where the citation carries no series neither does
the address.
"""

from dataclasses import dataclass, field

from ..lib.artifact import Fotnot, footnote_nodes, prune, scanned_nodes
from ..lib.catalog import BASE
from .issuers import BY_KOD, BY_SERIE, publisher_of

__all__ = ["Block", "Fotnot", "Vagledning", "vagledning_identifier",
           "vagledning_uri"]


def vagledning_uri(utgivare, serie, nummer):
    """The published document URI, minted from the issuing body's own number,
    so a citation that names the number ("Riktlinjer 05/2020", "EBA/GL/2021/05")
    and this page agree by construction -- the rule `avg.model.beslut_uri`
    follows for a dnr and `rs.model.rs_uri` for an agency's serienummer.

    `serie` is None for a body that numbers in one sequence across its document
    types, and then the address carries no series segment."""
    if utgivare not in BY_KOD:
        raise ValueError("no such issuing body: %r" % utgivare)
    slug = BY_SERIE[(utgivare, serie)].slug(nummer)
    if serie is None:
        return "%sguidance/%s/%s" % (BASE, utgivare, slug)
    return "%sguidance/%s/%s/%s" % (BASE, utgivare, serie, slug)


def vagledning_identifier(utgivare, serie, nummer, citation=None):
    """The citation form: "Riktlinjer 05/2020", "EBA/GL/2021/05", "WP 248".

    The number is kept exactly as the document writes it -- a body pads its
    löpnummer in some years and not others -- and only the URI normalises.

    `citation` is the name a document is filed under where it has no number to
    be cited by; one endorsed WP29 document has none. It is passed in by the
    caller that knows (the WP29 registry entry), never inferred here, so that a
    citation written beside a present number cannot quietly displace it."""
    if citation is not None:
        return citation
    return BY_SERIE[(utgivare, serie)].identifier % nummer


@dataclass
class Block:
    """One block of the document body. `punkt` is the body's own paragraph
    number where the paragraph carries one, which becomes its anchor."""
    kind: str            # "rubrik" | "stycke"
    text: str
    level: int = 1       # rubrik nesting (1 title, 2 section, 3 subsection …)
    punkt: str | None = None


@dataclass
class Vagledning:
    utgivare: str                       # issuers.KODER
    serie: str | None                   # the issuer's series kod, or None
    nummer: str                         # the body's own number, verbatim
    titel: str
    sprak: str = "sv"                   # "sv" | "en" -- which version this is
    antagen: str | None = None          # ISO date of adoption
    version: str | None = None          # "Version 2.0", "Final version", …
    revision: str | None = None         # a WP29 document's endorsed revision
    citation: str | None = None         # the filed name, where there is no
                                        # number to be cited by
    celex: str | None = None            # for the eurlex-routed bodies: the
                                        # CELEX the document also has, kept so
                                        # a CELEX address still resolves even
                                        # though nobody cites it that way
    beslut: str | None = None           # the instrument that issued it, where
                                        # the body issues its guidance as an
                                        # annex to a separately numbered
                                        # decision: EASA's AMC/GM come as
                                        # "Annex III to ED Decision 2026/006/R",
                                        # and one decision issues four of them,
                                        # so the decision is provenance and the
                                        # annex is the identity
    konsultation_url: str | None = None     # the pre-consultation first version
    amnesord: list[str] = field(default_factory=list)   # the body's own topics
    body: list[Block] = field(default_factory=list)
    fotnoter: list[Fotnot] = field(default_factory=list)
    ersatt_av: str | None = None        # the uri of the vägledning that
                                        # replaced this one, where the body has
                                        # issued a later wording of the same
                                        # document. A superseded vägledning is
                                        # still citable and still readable --
                                        # it governed conduct while it stood --
                                        # but it no longer states current
                                        # practice, so it drops out of every
                                        # listing (`catalog_rows._expired_date`)
    ersatt_av_url: str | None = None    # the *body's* own page for that later
                                        # wording. Kept apart from `ersatt_av`
                                        # because a harvest can know that a
                                        # document was replaced without being
                                        # able to name what replaced it: the
                                        # successor's page may state no number
                                        # at all. The document is superseded
                                        # either way, and the page says so with
                                        # a link off-site rather than a dead one
    ersatt_av_identifier: str | None = None  # and its printed name
                                        # ("EBA/GL/2026/05"). Carried rather
                                        # than derived: an address minted by
                                        # `own_number_slug` folds every
                                        # character class to a hyphen and
                                        # cannot be read back as a number
    source_url: str | None = None       # the body's own page for it
    document_url: str | None = None     # the file it was published as

    @property
    def uri(self):
        return vagledning_uri(self.utgivare, self.serie, self.nummer)

    @property
    def identifier(self):
        return vagledning_identifier(self.utgivare, self.serie, self.nummer,
                                     self.citation)

    @property
    def publisher(self):
        return publisher_of(self.utgivare, self.serie)

    def to_artifact(self, scanner):
        """The JSON artifact: the shared node convention (`structure` of
        rubrik/stycke nodes with inline-run text), every text scanned for
        citations -- which is what puts a riktlinje on the rail of the artikel
        it interprets. A numbered paragraph anchors on its own number
        (``#punkt27``), an unnumbered one on its position."""
        structure = scanned_nodes(
            self.body, scanner,
            anchor=lambda b, n: "punkt%s" % b.punkt if b.punkt else "S%d" % n)
        # the body's own paragraph number, kept beside the anchor minted from it
        # -- one node per block, in order, is `scanned_nodes`' contract (a rubrik
        # never carries a punkt: the parse numbers only the styckena)
        for b, node in zip(self.body, structure, strict=True):
            if b.punkt:
                node["punkt"] = b.punkt
        # the shared repeal vocabulary (`catalog_rows._expired_date`), which is what
        # drops a superseded document from the browse trees, the feeds, the
        # search results and other documents' citation rails while leaving its
        # page reachable by direct link. The EBA states no date for it -- its
        # version pages carry no repeal marker at all -- so `status` stands
        # alone and the column takes its date-free form. The identifier of the
        # successor is provenance for the link, so it goes only where the link
        # itself does.
        repeal = ({"status": "upphävt", "ersattAv": self.ersatt_av,
                   "ersattAvKalla": self.ersatt_av_url,
                   "ersattAvIdentifier": self.ersatt_av_identifier}
                  if self.ersatt_av or self.ersatt_av_url else {})
        return prune({
            "uri": self.uri, "type": "vagledning",
            "utgivare": self.utgivare, "serie": self.serie,
            "identifier": self.identifier,
            "metadata": prune({"title": self.titel, "publisher": self.publisher,
                               "nummer": self.nummer, "sprak": self.sprak,
                               **repeal,
                               "antagen": self.antagen,
                               "version": self.version,
                               "revision": self.revision,
                               "celex": self.celex,
                               "beslut": self.beslut,
                               "konsultation": self.konsultation_url,
                               "amnesord": self.amnesord}),
            "structure": structure,
            "footnotes": footnote_nodes(self.fotnoter, scanner),
            "source_url": self.source_url,
            "document_url": self.document_url})
