"""Typed UN Treaty Collection model and its artifact projection.

The Multilateral Treaties Deposited with the Secretary-General (MTDSG) is a
status register: a treaty's page carries its date/place of conclusion, entry
into force, UNTS registration, and the full participation list (each state's
signature and ratification/accession/succession), but NOT the treaty text --
that lives in per-treaty UNTS volumes outside this uniform scrape. The model is
therefore metadata + participation; `structure` is empty and the page links out
to the UN authentic text.

The curated instrument list (``data/treaties.json``) drives one harvest engine
over every treaty (rule:configured-by-data): it supplies the authoritative
English title (the page headline is generic), the Swedish name/acronym and the
subject group shown in the folkrätt listing.  The URI grammar
(``ext/untc/<mtdsg_no>``) is kept here -- ``untc`` is its only producer.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..lib.catalog import BASE

PUBLISHER = "United Nations"
DEPOSITARY = "UN Secretary-General"
SITE = "https://treaties.un.org"
# the MTDSG status-page URL (also the harvest target); one home for both the
# downloader and the artifact's source_url so the scheme can't drift between them
DETAIL = (SITE + "/pages/ViewDetailsIII.aspx"
          "?src=TREATY&mtdsg_no=%s&chapter=%s&clang=_en")
TREATIES = Path(__file__).resolve().parent / "data" / "treaties.json"


def load_treaties():
    """The curated instrument list as {mtdsg_no: entry}."""
    return {t["mtdsg_no"]: t
            for t in json.loads(TREATIES.read_text("utf-8"))["treaties"]}


def treaty_uri(mtdsg_no):
    return "%sext/untc/%s" % (BASE, mtdsg_no)


@dataclass
class Party:
    country: str
    signature: str | None = None        # ISO date
    action: str | None = None           # ratification / accession / succession
    action_date: str | None = None      # ISO date

    def to_dict(self):
        out = {"country": self.country}
        if self.signature:
            out["signature"] = self.signature
        if self.action:
            out["action"] = self.action
            out["actionDate"] = self.action_date
        return out


@dataclass
class Treaty:
    mtdsg_no: str
    chapter: str
    title: str                                # from the curated list (page headline is generic)
    conclusion_place: str | None = None
    conclusion_date: str | None = None
    entry_into_force: str | None = None       # the full "27 January 1980, …" text
    registration: str | None = None
    parties: list[Party] = field(default_factory=list)

    @property
    def uri(self):
        return treaty_uri(self.mtdsg_no)

    @property
    def kind(self):
        return "protocol" if "protocol" in self.title.lower() else "treaty"

    @property
    def source_url(self):
        return DETAIL % (self.mtdsg_no, self.chapter)

    def to_artifact(self):
        metadata = {
            "title": self.title,
            "publisher": PUBLISHER,
            "depositary": DEPOSITARY,
            "reference": "MTDSG %s" % self.mtdsg_no,
            "conclusionPlace": self.conclusion_place,
            "conclusionDate": self.conclusion_date,
            "entryIntoForce": self.entry_into_force,
            "registration": self.registration,
            "statesParties": sum(1 for p in self.parties if p.action),
            "signatories": sum(1 for p in self.parties if p.signature),
        }
        art = {
            "uri": self.uri,
            "type": "internationell-overenskommelse",
            "doctype": self.kind,
            "number": self.mtdsg_no,
            "identifier": self.title,
            "title": self.title,
            "date": self.conclusion_date,
            "metadata": metadata,
            "references": [],
            "structure": [],                  # MTDSG carries status, not treaty text
            "parties": [party.to_dict() for party in self.parties],
            "source_url": self.source_url,
        }
        return art
