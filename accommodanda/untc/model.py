"""Typed UN Treaty Collection model and its artifact projection.

The Multilateral Treaties Deposited with the Secretary-General (MTDSG) is a
status register: a treaty's page carries its date/place of conclusion, entry
into force, UNTS registration, and the full participation list (each state's
signature and ratification/accession/succession), but NOT the treaty text. The
text comes from the depositary instead (`download`, `text`), so a treaty is the
two halves joined: the articles a citation lands on, and the states bound by
them.

The curated instrument list (``data/treaties.json``) drives one harvest engine
over every treaty (rule:configured-by-data): it supplies the authoritative
English title (the page headline is generic), the Swedish name/acronym and the
subject group shown in the folkrätt listing, and where the authentic text
lives.  The URI grammar (``ext/untc/<unts>``) is kept here -- ``untc`` is its
only producer. The identity is the **UNTS registration number** in the UN's own
form (``I-14668``, as in ``volume-999-I-14668-English.pdf``): it is what the
UNTS cites itself by, and it survives for an instrument whose depositary is not
the UN, where an MTDSG chapter number does not exist at all.
"""

import functools
import json
from dataclasses import dataclass, field
from pathlib import Path

from ..lib.artifact import unique_id
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
    """The curated instrument list as {UNTS number: entry}.

    Keyed on the UNTS registration, not the MTDSG id, because that is the
    identity: it is what the UNTS cites itself by, and an instrument whose
    depositary is not the UN has no MTDSG chapter at all."""
    return {t["unts"]: t
            for t in json.loads(TREATIES.read_text("utf-8"))["treaties"]}


@functools.lru_cache(maxsize=1)
def by_mtdsg():
    """{mtdsg_no: UNTS number} -- for a consumer that still holds the old key."""
    return {entry["mtdsg_no"]: unts for unts, entry in load_treaties().items()}


def treaty_uri(unts):
    return "%sext/untc/%s" % (BASE, unts)


@dataclass
class Provision:
    fragment: str | None          # A5, AII -- None for the preamble
    heading: str                  # "Article 5", "Preamble"
    paragraphs: list[str] = field(default_factory=list)


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
    unts: str                                 # the identity: I-14668
    chapter: str
    title: str                                # from the curated list (page headline is generic)
    conclusion_place: str | None = None
    conclusion_date: str | None = None
    entry_into_force: str | None = None       # the full "27 January 1980, …" text
    registration: str | None = None
    parties: list[Party] = field(default_factory=list)
    provisions: list[Provision] = field(default_factory=list)

    @property
    def uri(self):
        return treaty_uri(self.unts)

    @property
    def kind(self):
        return "protocol" if "protocol" in self.title.lower() else "treaty"

    @property
    def source_url(self):
        return DETAIL % (self.mtdsg_no, self.chapter)

    def _structure(self):
        """The treaty's articles as the node shape `icrc` already mints, so both
        treaty corpora anchor a citation the same way: an ``artikel`` node keyed
        on its fragment (``A5``, ``AII``) over one ``stycke`` per paragraph.

        Anchors run through `unique_id` because a treaty numbers Article 1 more
        than once: UNCLOS restarts at 1 in each of its nine annexes and again in
        the 1994 Part XI Agreement bound with it. `text.provisions` scopes the
        annexes it can name, and this closes what is left -- 39 of UNCLOS's 593
        anchors were still ambiguous, which is one unreachable article each."""
        structure, ids = [], {}
        for provision in self.provisions:
            base = provision.fragment or "Preamble"
            anchor = unique_id(base, ids)
            children = [{"type": "stycke", "id": "%sS%d" % (anchor, index),
                         "text": [paragraph]}
                        for index, paragraph in enumerate(provision.paragraphs, 1)]
            node = {"type": "artikel", "id": anchor,
                    "text": [provision.heading], "children": children}
            if provision.fragment:
                node["ordinal"] = provision.fragment.rsplit("_", 1)[-1][1:]
            structure.append(node)
        return structure

    def to_artifact(self):
        metadata = {
            "title": self.title,
            "publisher": PUBLISHER,
            "depositary": DEPOSITARY,
            "reference": "UNTS %s" % self.unts,
            "mtdsg": self.mtdsg_no,
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
            "number": self.unts,
            "identifier": self.title,
            "title": self.title,
            "date": self.conclusion_date,
            "metadata": metadata,
            "references": [],
            "structure": self._structure(),
            "parties": [party.to_dict() for party in self.parties],
            "source_url": self.source_url,
        }
        return art
