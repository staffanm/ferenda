"""Typed ICJ decision model and its artifact projection.

The Court files every decision under one filename grammar --
``<case>-<yyyymmdd>-<KIND>-<nn>-<nn>-<LANG>.pdf``, e.g.
``070-19860627-JUD-01-00-EN.pdf`` for the Nicaragua merits judgment. That stem
without its language segment is the document's identity here, because it is the
only id the Court itself assigns a *decision* (the case number names the case,
which holds many decisions). ``icj`` is its only producer, so the grammar lives
here.

Scope is the Court's own word on the law: judgments, advisory opinions, and the
orders that indicate provisional measures. The ~600 docket orders that fix and
extend time-limits are out -- see `download.in_scope`.
"""

import re
from dataclasses import dataclass, field

from ..lib.artifact import Block, numbered_nodes
from ..lib.catalog import BASE

COURT_EN = "International Court of Justice"
SITE = "https://www.icj-cij.org"

# the decision filename stem: case number, date, kind, and the two-part
# sequence the Court numbers a decision's parts with. The separators are
# inconsistent at the source -- 875 files use "-", one uses "_" throughout
# (171_20201218_JUD_01-00-EN.pdf) -- so both are accepted and normalised.
RE_STEM = re.compile(r"^(\d{3})[-_](\d{8})[-_]([A-Za-z]{3})[-_](\d{2})[-_](\d{2})$")
# the language segment a decision filename closes with: EN, FR, or BI
# (bilingual). One 2022 order is published as "…-enc.pdf", a typing slip at the
# Court for "en" -- read as English rather than skipped, since it is the only
# copy of that order.
RE_LANGUAGE = re.compile(r"^(EN|FR|BI)C?$", re.I)

KINDS = {"JUD": "judgment", "ADV": "advisory opinion", "ORD": "order"}
# the catalog `kind` each decision files under. What those kinds are *called* to
# a reader is `facets.ICJ_KIND_LABELS`, which both the facet axis and the
# folkrätt landing read -- a source may not be imported from `lib`, so the
# labels live there and this stays the identity half (rule:lib-never-imports-vertical)
KIND_SV = {"judgment": "dom", "advisory opinion": "rådgivande yttrande",
           "order": "beslut"}


def parse_stem(stem):
    """A decision filename stem (language segment already removed) as its
    parts, or None when it does not match the Court's grammar."""
    match = RE_STEM.match(stem)
    if not match:
        return None
    case, date, kind, part, sub = match.groups()
    return {"case": case, "date": "%s-%s-%s" % (date[:4], date[4:6], date[6:]),
            "kind": kind.upper(), "part": part, "sub": sub}


def doc_basefile(stem):
    """The decision's identity: its filename stem with the separators and the
    kind normalised (``171_20201218_JUD_01-00`` -> ``171-20201218-JUD-01-00``).

    Raises rather than passing an unrecognised stem through, because a stem
    that does not parse would mint a URI no `parse_stem` consumer can read back
    (rule:errors-drive-retry-use-raise)."""
    parts = parse_stem(stem)
    if parts is None:
        raise ValueError("icj: %r is not a decision filename stem" % stem)
    return "%s-%s-%s-%s-%s" % (parts["case"], parts["date"].replace("-", ""),
                               parts["kind"], parts["part"], parts["sub"])


def decision_uri(basefile):
    return "%sicj/%s" % (BASE, basefile)


def case_uri(case):
    """The Court's own page for a case ("070" -> .../case/70). The case number
    is zero-padded in filenames and bare in URLs."""
    return "%s/case/%d" % (SITE, int(case))


@dataclass
class Decision:
    basefile: str                # 070-19860627-JUD-01-00
    case: str                    # "070" -- the Court's General List number, padded
    case_name: str               # Military and Paramilitary Activities … (Nicaragua v. USA)
    kind: str                    # judgment | advisory opinion | order
    title: str                   # "Judgment of 27 June 1986"
    date: str                    # ISO
    procedure: str | None = None # "Merits", "Preliminary Objections", …
    # tokens `ocr.repair` rewrote. Nonzero means the text was read off the
    # printed Reports by OCR, which is what the page tells the reader -- a
    # count of real repairs, not a guess at the PDF's provenance.
    ocr_repairs: int = 0
    pdf_url: str | None = None   # the Court's own PDF, the official text
    # the decision's own official citation off its cover sheet ("I.C.J.
    # Reports 1990, p. 92") -- the citable form, and the key other decisions
    # cite it by (`icj.reports`). None until a printed volume exists.
    reports_citation: str | None = None
    # the curated treaties this decision applies (`icj.treaties`), as
    # document-level relations rather than literal body spans
    references: list[dict] = field(default_factory=list)
    body: list[Block] = field(default_factory=list)

    @property
    def uri(self):
        return decision_uri(self.basefile)

    @property
    def source_url(self):
        return case_uri(self.case)

    @property
    def identifier(self):
        """The compact citing form: the case's General List number and what the
        decision is. "ICJ 70 (Judgment, 27 June 1986)"."""
        return "ICJ %d (%s, %s)" % (int(self.case), self.kind.capitalize(),
                                    self.date)

    def to_artifact(self, refs_for=None):
        metadata = {
            "title": self.title,
            "publisher": COURT_EN,
            "caseName": self.case_name,
            "caseNumber": str(int(self.case)),
            "decisionType": self.kind,
            "procedure": self.procedure,
            "ocrRepairs": self.ocr_repairs,
            "pdfUrl": self.pdf_url,
            "reportsCitation": self.reports_citation,
        }
        return {
            "uri": self.uri,
            "type": "avgorande",
            "court": "icj",
            "doctype": KIND_SV[self.kind],
            "docnumber": self.basefile,
            "identifier": self.identifier,
            "title": self.case_name,
            "avgorandedatum": self.date,
            "metadata": metadata,
            "references": self.references,
            "structure": numbered_nodes(self.body, refs_for),
            "source_url": self.source_url,
        }
