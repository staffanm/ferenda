"""Typed HUDOC case model and its artifact projection."""

import re
from dataclasses import dataclass, field
from datetime import datetime

from ..lib.artifact import numbered_nodes
from ..lib.coe import hudoc_articles
from ..lib.lagrum import ECHR_BASE

COURT = "European Court of Human Rights"
# HUDOC's own address for one item -- the case, its Swedish translation and the
# Court's summary of it are all items, so all three are named this way
ITEM_URL = "https://hudoc.echr.coe.int/eng?i=%s"


def case_uri(itemid):
    return ECHR_BASE + itemid


def record_date(record):
    """The decision date of a raw HUDOC record, ISO-formatted. HUDOC spells it
    three ways across its collections; kpdate is the fallback that always
    exists. Shared by `parse` (the artifact's avgorandedatum) and `citations`
    (the corpus index that dates apart a case's chamber and Grand Chamber
    judgments)."""
    for key in ("judgementdate", "decisiondate", "kpdate"):
        value = record.get(key) or ""
        if re.match(r"\d{4}-\d{2}-\d{2}", value):
            return value[:10]
        if value:
            return datetime.strptime(value[:10], "%d/%m/%Y").date().isoformat()
    return None


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
    # the Court's own Case-Law Information Note on this case, as the
    # `{"itemid", "docname"}` sidecar `summaries.store` writes -- a link, not a
    # document of ours
    summary: dict[str, str] | None = None

    @property
    def uri(self):
        return case_uri(self.itemid)

    @property
    def kind(self):
        return document_kind(self.collection)

    def to_artifact(self, refs_for=None):
        structure = numbered_nodes(self.body, refs_for)
        # HUDOC sets a judgment's footnotes as ordinary paragraphs inside an
        # `_ftn` container, so they arrive as blocks of their own; the tag rides
        # along on the node so the page can tell them from the running text. One
        # node per block, in order, is `numbered_nodes`' contract (and a rubrik
        # is never a note -- `parse.parse_body` classifies the two apart)
        for block, node in zip(self.body, structure, strict=True):
            if block.kind == "note":
                node["class"] = "note"

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
            "avgorandedatum": self.date,
            "metadata": metadata,
            "references": references,
            "structure": structure,
            "source_url": ITEM_URL % self.itemid,
        }
        if self.ecli:
            art["ecli"] = self.ecli
        if self.summary:
            # both keys are an invariant of the sidecar `summaries.store` writes;
            # reading them straight makes a sidecar-shape change fail loudly
            art["summary"] = {"itemid": self.summary["itemid"],
                              "title": self.summary["docname"],
                              "url": ITEM_URL % self.summary["itemid"]}
        return art
