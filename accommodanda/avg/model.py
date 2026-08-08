"""Typed model for vägledande myndighetsavgöranden (JO, JK, ARN, IMY, KKV).

The document URI is minted by the same rule the MYNDIGHETSBESLUT citation
grammar uses (`lagrum.fmt_jo_refs`/`fmt_jk_refs`: ``base + 'avg/{org}/' + dnr``)
so a decision and any citation to it agree by construction -- the DV-URI lesson,
fourth application. The canonical diarienummer therefore *is* the identity: the
first dnr names the document, any further dnr (JO decides joined complaints in
one beslut) stay in the metadata.

IMY is the organ where that identity has to be *read out of the decision*: its
site publishes tillsyn pages, not documents, and the diarienummer is printed
only inside the decision PDFs a page attaches (see `avg/download.py`). A
decision is therefore assembled from one or more documents -- a beslut, the
tillsynsskrivelse that opened the same ärende, an English translation -- which
`delar` records, together with the tillsyn pages it was published on.

KKV's decisions are joined from two of its own sources, so they carry both the
diarium's case fields -- ärendetyp, motpart, the handling avdelning -- and the
curated ärendelista's: the branch, the kinds of beslut the case produced, and
Konkurrensverkets own account of what the case was about and what the courts
later did with the decision, which becomes the head of the document body.
"""

from dataclasses import dataclass, field

from ..lib.artifact import footnote_nodes, scanned_nodes
from ..lib.catalog import BASE

ORGS = ("jo", "jk", "arn", "imy", "kkv")
ORG_NAME = {"jo": "Justitieombudsmannen", "jk": "Justitiekanslern",
            "arn": "Allmänna reklamationsnämnden",
            "imy": "Integritetsskyddsmyndigheten",
            "kkv": "Konkurrensverket"}


def beslut_uri(org, dnr):
    """The published document URI -- byte-identical to what a MYNDIGHETSBESLUT
    citation to this decision mints."""
    return "%savg/%s/%s" % (BASE, org, dnr)


def beslut_identifier(org, dnr):
    """The old pipeline's dcterms:identifier forms, kept: "JO dnr 6356-2012"
    (jo.py infer_identifier), "JK 3497-06-40" (jk.py), "ARN 1992-3657"
    (arn.py infer_identifier). IMY's own form names the myndighet and the
    number, which is how its decisions are cited: "IMY dnr IMY-2024-2904"
    (and "IMY dnr DI-2019-3375" for a Datainspektionen-era number)."""
    return {"jo": "JO dnr %s", "jk": "JK %s", "arn": "ARN %s",
            "imy": "IMY dnr %s", "kkv": "KKV dnr %s"}[org] % dnr


@dataclass
class Block:
    kind: str            # "rubrik" | "stycke"
    text: str
    level: int = 1       # rubrik nesting (1 section, 2 subsection)


@dataclass
class Fotnot:
    """A note set below the running text. `mark` is the marker digit the
    document printed (``""`` where it printed none); `text` is the note body,
    citation-linked downstream like any other text.

    Worth carrying because of what these notes hold: IMY names a vägledning in
    prose ("Europeiska dataskyddsstyrelsens riktlinjer om samtycke") and grounds
    it with the number in the note below ("Riktlinjer 05/2020"). Discard the
    notes and the decision cites nothing a citation scan can resolve -- which is
    exactly what happened: 43 of the 83 IMY-beslut that name this guidance carry
    its number, and not one of those numbers reached the artifact."""
    mark: str
    text: str


@dataclass
class Beslut:
    org: str                            # "jo" | "jk" | "arn" | "imy" | "kkv"
    diarienummer: list[str]             # first = canonical (names the document)
    titel: str
    beslutsdatum: str | None = None     # ISO date
    sammanfattning: str | None = None   # JO's "Beslutet i korthet" / summary
    avgjord_av: str | None = None       # JO: the deciding ombudsman
    official_report: str | None = None  # JO: the ämbetsberättelse citation
                                        # ("JO 1990/91 s. 70"), frozen-corpus only
    nyckelord: list[str] = field(default_factory=list)  # JO: sakområden
    body: list[Block] = field(default_factory=list)
    fotnoter: list[Fotnot] = field(default_factory=list)  # the notes below the
                                        # running text, where the template sets
                                        # them smaller (imy, kkv)
    source_url: str | None = None       # the decision's own page at jo.se/jk.se
    delar: list[dict] = field(default_factory=list)     # IMY: the documents the
                                        # decision was published as
                                        # ({titel, url, sprak})
    tillsyner: list[dict] = field(default_factory=list)  # IMY: the tillsyn pages
                                        # that publish it ({slug, url})
    praxis: dict | None = None          # IMY: the praxisbeslut page's curated
                                        # fields (lagrum, överklagan, laga kraft)
    sanktionsavgift: str | None = None  # IMY: the fine, as the sanktionsavgift
                                        # listing states it ("6 miljoner kronor")
    arendetyp: str | None = None        # KKV: the diarium's process class
                                        # ("3.2.3.2 Prövning av företagskoncentration")
    motpart: str | None = None          # KKV: the case's counterparty
    bransch: list[str] = field(default_factory=list)    # KKV: curated branch
    beslutstyp: list[str] = field(default_factory=list)  # KKV: curated kinds of
                                        # beslut the case produced (Åtagande,
                                        # Konkurrensskadeavgift, Dom, …)
    referat_url: str | None = None      # KKV: the curated case page
    artal: str | None = None            # KKV: the span the curated account
                                        # dates the case by ("2025-2026",
                                        # "2022-"), which is not a beslutsdatum

    @property
    def uri(self):
        return beslut_uri(self.org, self.diarienummer[0])

    @property
    def identifier(self):
        return beslut_identifier(self.org, self.diarienummer[0])

    def to_artifact(self, scanner):
        """The JSON artifact: shared node convention (`structure` of
        rubrik/stycke nodes with inline-run text) so catalog/render/search reuse
        their generic walkers; every text scanned for citations."""
        structure = scanned_nodes(self.body, scanner)
        footnotes = footnote_nodes(self.fotnoter, scanner)
        metadata = {"title": self.titel,
                    "publisher": ORG_NAME[self.org],
                    "diarienummer": self.diarienummer}
        for key, value in (("beslutsdatum", self.beslutsdatum),
                           ("avgjordAv", self.avgjord_av),
                           ("officialReport", self.official_report),
                           ("nyckelord", self.nyckelord),
                           ("dokument", self.delar),
                           ("tillsyner", self.tillsyner),
                           ("arendetyp", self.arendetyp),
                           ("motpart", self.motpart),
                           ("bransch", self.bransch),
                           ("beslutstyp", self.beslutstyp),
                           ("referatUrl", self.referat_url),
                           ("artal", self.artal),
                           ("praxis", self.praxis),
                           ("sanktionsavgift", self.sanktionsavgift)):
            if value:
                metadata[key] = value
        art = {"uri": self.uri, "type": "avgorande", "org": self.org,
               "identifier": self.identifier, "metadata": metadata,
               "structure": structure}
        if footnotes:
            art["footnotes"] = footnotes
        if self.sammanfattning:
            art["sammanfattning"] = self.sammanfattning
        if self.source_url:
            art["source_url"] = self.source_url
        return art
