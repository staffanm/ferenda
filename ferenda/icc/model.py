"""Typed ICC decision model and its artifact projection.

The ICC vertical joins two sources: the icc-cpi.int ``/decisions`` facets scope
the curated substantive set (by Rome-Statute article) and give each record's
document number; the ICC Legal Tools API (legal-tools.org) resolves that number
to the decision's metadata and its PDF, whose text becomes the article tree.

The document-number grammar (``ext/icc/<doc-number>``, slashes flattened) is
kept here -- ``icc`` is its only producer.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..lib.artifact import numbered_nodes
from ..lib.catalog import BASE

COURT = "International Criminal Court"
SITE = "https://www.icc-cpi.int"
DECISION_TYPES = Path(__file__).resolve().parent / "data" / "decision_types.json"

# a full ICC document number: situation/case/document, e.g. ICC-01/04-02/06-2359.
# variant suffixes (-Red, -Corr, -tFRA, -Anx…) trail the trailing -<docnum>.
RE_DOC_BASE = re.compile(r"ICC-\d+/\d+-\d+/\d+-\d+", re.I)
RE_CASE = re.compile(r"ICC-\d+/\d+-\d+/\d+", re.I)


def load_types():
    """The curated decision types as {facet_id: entry}."""
    return {t["facet"]: t
            for t in json.loads(DECISION_TYPES.read_text("utf-8"))["types"]}


def doc_basefile(doc_number):
    """The filesystem-safe / URI-local form of a document number: slashes to
    underscores (ICC-01/04-02/06-2359 -> ICC-01_04-02_06-2359)."""
    return doc_number.replace("/", "_")


def decision_uri(doc_number):
    return "%sext/icc/%s" % (BASE, doc_basefile(doc_number))


@dataclass
class Block:
    kind: str                    # rubrik | stycke
    text: str
    level: int = 1
    number: str | None = None


@dataclass
class Decision:
    doc_number: str              # the Legal Tools externalId, e.g. ICC-02/04-01/15-1762-Red
    title: str                   # "Judgment", "Trial Judgment", "Decision on the confirmation…"
    case_name: str               # The Prosecutor v. Bosco Ntaganda
    case_number: str             # ICC-01/04-02/06
    decision_type: str           # curated kind (judgment/sentence/…)
    date: str | None = None
    chamber: str | None = None   # "Trial Chamber VI", "Appeals Chamber"
    slug: str | None = None      # Legal Tools slug (source of the PDF)
    body: list[Block] = field(default_factory=list)
    # the instruments this decision applies (`icc.treaties`), article-level
    # where it names one -- document relations, not literal body spans
    references: list[dict] = field(default_factory=list)

    @property
    def uri(self):
        return decision_uri(self.doc_number)

    @property
    def source_url(self):
        # the authoritative ICC court record (reachable in a browser; only bots
        # meet Cloudflare there), keyed by the lower-cased document number
        return "%s/court-record/%s" % (SITE, self.doc_number.lower())

    def to_artifact(self, refs_for=None):
        structure = numbered_nodes(self.body, refs_for)
        metadata = {
            "title": self.title,
            "publisher": COURT,
            "caseName": self.case_name,
            "caseNumber": self.case_number,
            "documentNumber": self.doc_number,
            "chamber": self.chamber,
            "decisionType": self.decision_type,
        }
        art = {
            "uri": self.uri,
            "type": "avgorande",
            "court": "icc",
            "doctype": self.decision_type,
            "docnumber": self.doc_number,
            "identifier": "%s (%s)" % (self.case_number, self.title),
            "title": self.case_name,
            "avgorandedatum": self.date,
            "metadata": metadata,
            "references": self.references,
            "structure": structure,
            "source_url": self.source_url,
        }
        return art
