"""Typed ICRC treaty model and its artifact projection.

The ICRC "Treaties, States Parties and Commentaries" database serves the whole
of international humanitarian law -- the four 1949 Geneva Conventions and their
Additional Protocols, the Hague law, and the weapons/cultural-property regimes
-- as a Drupal JSON:API.  One `node--treaty` carries the metadata; its
`field_treaty_content` paragraphs are the authentic article text (no PDF parse)
and its `field_treaty_state_parties` paragraphs the per-state participation.

The URI grammar (``ext/icrc/<number>``) is kept here rather than in ``lib``:
the ICRC vertical is its only producer today, and nothing in ``lib`` mints an
ICRC target (the folkrätt renderer reads the stored number back off the uri).
If the citation engine later links "Genèvekonventionen artikel 3" it becomes a
second consumer and the grammar moves to ``lib`` then (rule:second-use-goes-to-lib).
"""

import re
from dataclasses import dataclass, field

from ..lib.catalog import BASE

PUBLISHER = "International Committee of the Red Cross"
SITE = "https://ihl-databases.icrc.org"

# the ICRC treaty-content sections that carry operative text vs. those that are
# only structural headings; everything else a content list holds (Table of
# Contents, Foreword, Introduction -- the commentary front matter) is dropped.
TEXT_SECTIONS = frozenset({"Article", "Annex", "Preamble", "Testimonium"})
HEADING_SECTIONS = frozenset({"Chapter", "Title", "Part", "Section"})

RE_ARTICLE_ORDINAL = re.compile(r"Article\s+(\S+)", re.I)


def treaty_uri(number):
    return "%sext/icrc/%s" % (BASE, str(number))


@dataclass
class Provision:
    """One node of a treaty's content tree.  `kind` is the artifact node type
    (``artikel`` for operative provisions, ``rubrik`` for structural headings);
    `paragraphs` is the article body split into its stycken (empty for a heading)."""
    kind: str
    section: str                 # raw ICRC section: Article / Annex / Chapter …
    heading: str                 # "Article 1 - Respect for the Convention"
    fragment: str | None = None  # stable id: A1, Annex1, Preamble, Testimonium
    ordinal: str | None = None
    paragraphs: list[str] = field(default_factory=list)


@dataclass
class Party:
    country: str
    action: str                  # accession / ratification / signature / succession / …
    date: str | None = None
    reservation: str | None = None

    def to_dict(self):
        out = {"country": self.country, "action": self.action}
        if self.date:
            out["date"] = self.date
        if self.reservation:
            out["reservation"] = self.reservation
        return out


@dataclass
class Treaty:
    number: str
    title: str
    short_title: str | None = None
    unid: str | None = None
    adoption_date: str | None = None
    entry_into_force: str | None = None
    in_force: bool | None = None
    treaty_type: str | None = None      # geneva_conventions / additional_protocols / None
    historical: bool = False
    slug: str | None = None             # field_path, e.g. "/ihl-treaties/gci-1949"
    depositary: str | None = None
    topics: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    summary: str | None = None
    provisions: list[Provision] = field(default_factory=list)
    parties: list[Party] = field(default_factory=list)

    def __post_init__(self):
        self.number = str(self.number)

    @property
    def uri(self):
        return treaty_uri(self.number)

    @property
    def kind(self):
        if self.treaty_type == "additional_protocols" or "protocol" in self.title.lower():
            return "protocol"
        if "declaration" in self.title.lower():
            return "declaration"
        return "treaty"

    @property
    def identifier(self):
        """The short, reader-facing citation label -- the ICRC short title where
        one exists (unique and descriptive), else the full title."""
        return self.short_title or self.title

    @property
    def source_url(self):
        return SITE + "/en" + self.slug if self.slug else None

    def _structure(self):
        structure = []
        for prov in self.provisions:
            if prov.kind == "rubrik":
                structure.append({"type": "rubrik", "level": 1,
                                  "text": [prov.heading]})
                continue
            children = [{"type": "stycke", "id": "%sS%d" % (prov.fragment, i),
                         "text": [para]}
                        for i, para in enumerate(prov.paragraphs, 1)]
            node = {"type": "artikel", "id": prov.fragment,
                    "text": [prov.heading], "children": children}
            if prov.ordinal:
                node["ordinal"] = prov.ordinal
            structure.append(node)
        return structure

    def to_artifact(self):
        metadata = {
            "title": self.title,
            "publisher": PUBLISHER,
            "reference": self.identifier,
            "adoptionDate": self.adoption_date,
            "entryIntoForce": self.entry_into_force,
            "inForce": self.in_force,
            "depositary": self.depositary,
            "topics": self.topics,
            "languages": self.languages,
            "statesParties": len(self.parties),
            "historical": self.historical,
        }
        art = {
            "uri": self.uri,
            "type": "internationell-overenskommelse",
            "doctype": self.kind,
            "number": self.number,
            "identifier": self.identifier,
            "title": self.title,
            "date": self.adoption_date,
            "metadata": metadata,
            "references": [],
            "structure": self._structure(),
            "parties": [party.to_dict() for party in self.parties],
        }
        if self.summary:
            art["summary"] = self.summary
        if self.source_url:
            art["source_url"] = self.source_url
        return art
