"""Typed model for the kammarrätternas avgöranden in public procurement cases
that Konkurrensverket collects in its domstolsdatabas.

A case is a court decision without a referat: the kammarrätt decided it, and
nobody reported it. lagen.nu has always addressed such a decision by the court,
its målnummer and its date -- the ``dom/{court}/{målnummer}/{date}`` grammar
`lib.casenaming.verdict_uri` mints, and the one the dv source uses for the
kammarrätt decisions it holds (``dom/kst/1137-00/2000-10-05``). This source mints
the same shape, so the two sources name a kammarrätt decision the same way.
The dv corpus holds kammarrätt decisions up to 2007 and this database starts in
2016, so the two do not overlap.

The database states the case's metadata; the PDF is the decision itself. So
what the listing and the case page say (the parties, the ärendemening, the
outcome, Konkurrensverkets kortreferat) is kept as metadata, and the decision's
text is the body. The body is citation-scanned: a procurement decision is an
argument about a handful of LOU paragraphs, and the scan puts it on the rail of
each.
"""

from dataclasses import dataclass, field

from ..lib.artifact import prune, scanned_nodes
from ..lib.casenaming import verdict_uri

KAMMARRATT = "Kammarrätten i "


@dataclass
class Block:
    kind: str            # "rubrik" | "stycke"
    text: str
    level: int = 1
    page: int | None = None   # the PDF page it starts on (the facsimile tabs)


@dataclass
class Avgorande:
    court: str                          # the court code, "KST"
    domstol: str                        # "Kammarrätten i Stockholm"
    malnummer: str                      # the first målnummer: "3404-22"
    malnummer_lista: str                # every målnummer, as the database
                                        # lists them: "3404-3409-22"
    avgorandedatum: str                 # ISO date
    instans: str                        # "Kammarrätt"
    doktyp: str | None = None           # "dom" | "beslut", from the PDF's page 1
    arendemening: str | None = None     # "Offentlig upphandling; fråga om …"
    arendetyp: str | None = None        # "Överprövning av upphandling"
    utgang: str | None = None           # the database's "Avgörande": "Avskrivning"
    kortreferat: str | None = None      # Konkurrensverkets own summary
    sokande: str | None = None          # "Leverantör/Sökande"
    motpart: str | None = None          # "UM/UE", the contracting authority
    body: list[Block] = field(default_factory=list)
    domslut: str | None = None          # the text under "…S AVGÖRANDE"
    facsimile_pdf: str | None = None    # the PDF, data_root-relative
    source_url: str | None = None       # the database's case page
    document_url: str | None = None     # the decision PDF

    @property
    def uri(self):
        return verdict_uri(self.court, self.malnummer, self.avgorandedatum)

    @property
    def identifier(self):
        """"KamR Stockholm mål 6426-25" -- the court's seat and the first
        målnummer, the short form a listing or a sidebar row has room for (the
        page's metadata still names the court in full)."""
        assert self.domstol.startswith(KAMMARRATT), (
            "%r is not a kammarrätt" % self.domstol)
        more = " m.fl." if self.malnummer_lista != self.malnummer else ""
        return "KamR %s mål %s%s" % (self.domstol.removeprefix(KAMMARRATT),
                                     self.malnummer, more)

    def _paged(self, nodes):
        """`scanned_nodes`' one node per block, each given its block's PDF page
        -- what `lib.page.document_body` sets the page tabs by."""
        return [{**node, "page": block.page} if block.page else node
                for node, block in zip(nodes, self.body, strict=True)]

    def to_artifact(self, scanner):
        return prune({
            "uri": self.uri, "type": "avgorande",
            "court": self.court, "court_namn": self.domstol,
            "malnummer": [self.malnummer],
            "avgorandedatum": self.avgorandedatum,
            "doctype": self.doktyp,
            "identifier": self.identifier,
            "title": self.arendemening,
            "metadata": prune({"domstol": self.domstol,
                               "malnummer": self.malnummer_lista,
                               "instans": self.instans,
                               "arendemening": self.arendemening,
                               "arendetyp": self.arendetyp,
                               "utgang": self.utgang,
                               "sokande": self.sokande,
                               "motpart": self.motpart}),
            "kortreferat": self.kortreferat,
            "domslut": self.domslut,
            "structure": self._paged(scanned_nodes(self.body, scanner)),
            # the decision's own PDF, data_root-relative: the /api/v1/facsimile
            # resolver for a dom/ uri renders its pages for the page tabs
            "facsimile_pdf": self.facsimile_pdf,
            "source_url": self.source_url,
            "document_url": self.document_url})
