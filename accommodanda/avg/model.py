"""Typed model for vägledande myndighetsavgöranden (JO + JK decisions).

The document URI is minted by the same rule the MYNDIGHETSBESLUT citation
grammar uses (`lagrum.fmt_jo_refs`/`fmt_jk_refs`: ``base + 'avg/{org}/' + dnr``)
so a decision and any citation to it agree by construction -- the DV-URI lesson,
fourth application. The canonical diarienummer therefore *is* the identity: the
first dnr names the document, any further dnr (JO decides joined complaints in
one beslut) stay in the metadata.
"""

from dataclasses import dataclass, field

from ..lib.lagrum import interleave

BASE = "https://lagen.nu/"

ORGS = ("jo", "jk", "arn")
ORG_NAME = {"jo": "Justitieombudsmannen", "jk": "Justitiekanslern",
            "arn": "Allmänna reklamationsnämnden"}


def beslut_uri(org, dnr):
    """The published document URI -- byte-identical to what a MYNDIGHETSBESLUT
    citation to this decision mints."""
    return "%savg/%s/%s" % (BASE, org, dnr)


def beslut_identifier(org, dnr):
    """The old pipeline's dcterms:identifier forms, kept: "JO dnr 6356-2012"
    (jo.py infer_identifier), "JK 3497-06-40" (jk.py), "ARN 1992-3657"
    (arn.py infer_identifier)."""
    return {"jo": "JO dnr %s", "jk": "JK %s", "arn": "ARN %s"}[org] % dnr


@dataclass
class Block:
    kind: str            # "rubrik" | "stycke"
    text: str
    level: int = 1       # rubrik nesting (1 section, 2 subsection)


@dataclass
class Beslut:
    org: str                            # "jo" | "jk" | "arn"
    diarienummer: list[str]             # first = canonical (names the document)
    titel: str
    beslutsdatum: str | None = None     # ISO date
    sammanfattning: str | None = None   # JO's "Beslutet i korthet" / summary
    avgjord_av: str | None = None       # JO: the deciding ombudsman
    nyckelord: list[str] = field(default_factory=list)  # JO: sakområden
    body: list[Block] = field(default_factory=list)
    source_url: str | None = None       # the decision's own page at jo.se/jk.se

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
        structure = []
        n = 0
        for b in self.body:
            runs = interleave(b.text, scanner.parse_text(b.text, context={}))
            if b.kind == "rubrik":
                structure.append({"type": "rubrik", "level": b.level,
                                  "text": runs})
            else:
                n += 1
                structure.append({"type": "stycke", "id": "S%d" % n,
                                  "text": runs})
        metadata = {"title": self.titel,
                    "publisher": ORG_NAME[self.org],
                    "diarienummer": self.diarienummer}
        if self.beslutsdatum:
            metadata["beslutsdatum"] = self.beslutsdatum
        if self.avgjord_av:
            metadata["avgjordAv"] = self.avgjord_av
        if self.nyckelord:
            metadata["nyckelord"] = self.nyckelord
        art = {"uri": self.uri, "type": "avgorande", "org": self.org,
               "identifier": self.identifier, "metadata": metadata,
               "structure": structure}
        if self.sammanfattning:
            art["sammanfattning"] = self.sammanfattning
        if self.source_url:
            art["source_url"] = self.source_url
        return art
