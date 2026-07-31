"""Typed model for rättsliga ställningstaganden -- the third kind of document a
Swedish förvaltningsmyndighet publishes, beside its föreskrifter and its
beslut, and the one lagen.nu has never carried.

A ställningstagande is neither of the other two. A föreskrift is binding law
issued under a bemyndigande; a beslut decides one ärende. A ställningstagande
binds nobody outside the agency and decides no case: it states, in advance and
in general, how the agency reads a rule it administers, in a question where the
courts have not yet answered. Every one of the six agencies says so in almost
the same words -- "styrande för vår verksamhet", "inte bindande för till exempel
domstolar" -- and that is exactly what makes them worth carrying: they are the
published interpretation a reader of the statute will actually meet, and the
citation scan puts each of them on the rail of the paragraf it interprets.

The document model follows from that shape:

  * **Identity is the agency's own number**, not a diarienummer. A
    ställningstagande is published *as* a numbered item in the agency's series
    ("IMYRS 2024:1", "FKRS 2025:01", "RS/028/2021"), which is how the agency and
    everyone citing it names it. The diarienummer is metadata, kept where the
    document prints one. This is the one deliberate departure from `avg/model.py`,
    whose organs number nothing and where the dnr is all the identity there is.

  * **Currency is a first-class fact.** Unlike a beslut, which is a fixed
    historical artifact, a ställningstagande is *in force until the agency
    withdraws it* -- and three of the six say so in the listing itself (FI's
    Status column, Konkurrensverkets "(upphävt 20 oktober 2025)",
    Migrationsverkets version numbering). So `status` and `ersatt_av` are
    modelled rather than dropped: a repealed statement still has to be readable
    (it governed what the agency did while it stood) but must not read as
    current law.

  * **The body is the document**, whatever the agency published it as -- a PDF
    for five of them, a web page for IMY. Both arrive here as the same
    rubrik/stycke block stream and leave as the shared artifact node shape, so
    catalog/render/search reuse their generic walkers.

URI scheme: ``https://lagen.nu/rs/{org}/{nummer}`` (e.g.
``https://lagen.nu/rs/imy/2024:1``, ``https://lagen.nu/rs/kfm/1-23-VER``) -- the
avg grammar with `rs` in place of `avg`, since the two are the same kind of
address: a myndighet's short code, then the identity that myndighet gave the
document.
"""

from dataclasses import dataclass, field

from ..lib.catalog import BASE
from ..lib.lagrum import interleave
from .agencies import BY_ORG, ORGS, number_slug

__all__ = ["ORGS", "Block", "Stallningstagande", "rs_identifier", "rs_uri"]


def rs_uri(org, nummer):
    """The published document URI. Minted from the agency's own number, so a
    citation that names the number ("FKRS 2020:2") and this page agree by
    construction -- the same rule `avg.model.beslut_uri` follows for a dnr."""
    return "%srs/%s/%s" % (BASE, org, number_slug(nummer))


def rs_identifier(org, nummer):
    """The citation form: the agency's own designation where it has published
    one ("IMYRS 2024:1", "FKRS 2025:01"), else the way its own page names the
    document ("Konkurrensverkets ställningstagande 2025:1"). See
    `agencies.REGISTRY` -- no acronym is invented for an agency that has not
    coined one."""
    return BY_ORG[org].identifier % nummer


@dataclass
class Block:
    kind: str            # "rubrik" | "stycke"
    text: str
    level: int = 1       # rubrik nesting (1 section, 2 subsection)


@dataclass
class Stallningstagande:
    org: str                            # agencies.ORGS
    nummer: str                         # the agency's own number, verbatim
    titel: str
    beslutsdatum: str | None = None     # ISO date
    diarienummer: str | None = None     # where the document prints one
    sammanfattning: str | None = None   # the agency's own ingress/summary
    status: str = "gällande"            # "gällande" | "upphävt"
    upphavd: str | None = None          # when it was withdrawn, as stated
    ersatt_av: str | None = None        # the ställningstagande that replaced it
    ersatter: str | None = None         # the one this replaced
    version: str | None = None          # Migrationsverket revises in place ...
    foregaende_version: str | None = None   # ... over a version fastställd then
    doktyp: str = "stallningstagande"   # Migrationsverket's series also holds
                                        # rättsliga kommentarer ("kommentar"):
                                        # its reading of one named avgörande,
                                        # published in the same numbered series
    nyckelord: list[str] = field(default_factory=list)
    body: list[Block] = field(default_factory=list)
    source_url: str | None = None       # the agency's own page for it
    document_url: str | None = None     # the PDF the agency published it as

    @property
    def uri(self):
        return rs_uri(self.org, self.nummer)

    @property
    def identifier(self):
        return rs_identifier(self.org, self.nummer)

    @property
    def publisher(self):
        return BY_ORG[self.org].name

    def to_artifact(self, scanner):
        """The JSON artifact: the shared node convention (`structure` of
        rubrik/stycke nodes with inline-run text), every text scanned for
        citations -- which is what puts a ställningstagande on the rail of the
        paragraf it interprets."""
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
        metadata = {"title": self.titel, "publisher": self.publisher,
                    "nummer": self.nummer, "status": self.status}
        for key, value in (("beslutsdatum", self.beslutsdatum),
                           ("diarienummer", self.diarienummer),
                           ("upphavd", self.upphavd),
                           ("ersattAv", self.ersatt_av),
                           ("ersatter", self.ersatter),
                           ("version", self.version),
                           ("foregaendeVersion", self.foregaende_version)):
            if value:
                metadata[key] = value
        if self.nyckelord:
            metadata["nyckelord"] = self.nyckelord
        art = {"uri": self.uri, "type": "stallningstagande", "org": self.org,
               "doktyp": self.doktyp, "identifier": self.identifier,
               "metadata": metadata, "structure": structure}
        if self.sammanfattning:
            art["sammanfattning"] = self.sammanfattning
        if self.source_url:
            art["source_url"] = self.source_url
        if self.document_url:
            art["document_url"] = self.document_url
        return art
