"""Typed Council of Europe treaty model and artifact projection."""

from dataclasses import dataclass, field

from ..lib.coe import treaty_number, treaty_uri

TREATY_OFFICE = "Council of Europe Treaty Office"
SFS_ECHR = "https://lagen.nu/1994:1219"

# Instruments reproduced in the appendices to SFS 1994:1219. Protocol 12 is
# deliberately absent: Sweden has not incorporated it there.
SFS_1994_1219 = frozenset({"005", "009", "046", "114", "117", "187", "214"})


@dataclass
class Treaty:
    number: str
    title: str
    opening_date: str | None = None
    opening_place: str | None = None
    entry_into_force: str | None = None
    reference: str | None = None
    summary: str | None = None
    source_url: str | None = None
    # the artifact-shaped article tree from parse.build_structure: nested
    # rubrik/artikel/stycke/punkt nodes with stable fragment ids
    structure: list[dict] = field(default_factory=list)

    def __post_init__(self):
        self.number = treaty_number(self.number)

    @property
    def uri(self):
        return treaty_uri(self.number)

    @property
    def kind(self):
        return "protocol" if "protocol" in self.title.lower() else "treaty"

    def to_artifact(self):
        metadata = {
            "title": self.title,
            "publisher": TREATY_OFFICE,
            "reference": self.reference or ("CETS No. %s" % self.number),
            "openingDate": self.opening_date,
            "openingPlace": self.opening_place,
            "entryIntoForce": self.entry_into_force,
        }
        references = []
        if self.number in SFS_1994_1219:
            metadata["swedishImplementation"] = SFS_ECHR
            references.append({"uri": SFS_ECHR, "predicate": "rdfs:seeAlso",
                               "text": "SFS 1994:1219"})
        art = {
            "uri": self.uri,
            "type": "internationell-overenskommelse",
            "doctype": self.kind,
            "number": self.number,
            "identifier": metadata["reference"],
            "title": self.title,
            "date": self.opening_date,
            "metadata": metadata,
            "references": references,
            "structure": self.structure,
        }
        if self.summary:
            art["summary"] = self.summary
        if self.source_url:
            art["source_url"] = self.source_url
        return art
