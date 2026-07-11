"""Typed HUDOC case model and its artifact projection."""

from dataclasses import dataclass, field

from ..lib.catalog import BASE
from ..lib.coe import hudoc_articles

COURT = "European Court of Human Rights"


def case_uri(itemid):
    return "%sdom/echr/%s" % (BASE, itemid)


def document_kind(collection):
    values = set((collection or "").upper().split(";"))
    for token, kind in (
        ("JUDGMENTS", "judgment"),
        ("DECISIONS", "decision"),
        ("COMMUNICATEDCASES", "communicated-case"),
        ("ADVISORYOPINIONS", "advisory-opinion"),
        ("LEGALSUMMARIES", "legal-summary"),
        ("RESOLUTIONS", "resolution"),
    ):
        if token in values:
            return kind
    return "case-law"


@dataclass
class Block:
    kind: str                    # rubrik | stycke | note
    text: str
    level: int = 1
    number: str | None = None


@dataclass
class HudocCase:
    itemid: str
    title: str
    collection: str
    language: str
    date: str | None = None
    application_numbers: list[str] = field(default_factory=list)
    ecli: str | None = None
    respondent: str | None = None
    originating_body: str | None = None
    importance: str | None = None
    article_codes: list[str] = field(default_factory=list)
    conclusions: list[str] = field(default_factory=list)
    body: list[Block] = field(default_factory=list)

    @property
    def uri(self):
        return case_uri(self.itemid)

    @property
    def kind(self):
        return document_kind(self.collection)

    def to_artifact(self):
        structure = []
        serial = 0
        for block in self.body:
            if block.kind == "rubrik":
                structure.append({"type": "rubrik", "level": block.level,
                                  "text": [block.text]})
                continue
            serial += 1
            node = {"type": "stycke", "text": [block.text],
                    "id": "P%s" % block.number if block.number else "S%d" % serial}
            if block.number:
                node["ordinal"] = block.number
            if block.kind == "note":
                node["class"] = "note"
            structure.append(node)

        articles = []
        for code in self.article_codes:
            uri = hudoc_articles(code)
            articles.extend(u for u in uri if u not in articles)
        references = [
            {"uri": uri, "predicate": "dcterms:references",
             "text": next((code for code in self.article_codes
                           if uri in hudoc_articles(code)), uri)}
            for uri in articles
        ]
        metadata = {
            "title": self.title,
            "publisher": COURT,
            "applicationNumber": self.application_numbers,
            "language": self.language,
            "documentCollection": self.collection.split(";") if self.collection else [],
            "respondent": self.respondent,
            "originatingBody": self.originating_body,
            "importance": self.importance,
            "articles": self.article_codes,
            "conclusions": self.conclusions,
        }
        art = {
            "uri": self.uri,
            "type": "avgorande",
            "court": "echr",
            "itemid": self.itemid,
            "doctype": self.kind,
            "title": self.title,
            "date": self.date,
            "metadata": metadata,
            "references": references,
            "structure": structure,
            "source_url": "https://hudoc.echr.coe.int/eng?i=%s" % self.itemid,
        }
        if self.ecli:
            art["ecli"] = self.ecli
        return art
