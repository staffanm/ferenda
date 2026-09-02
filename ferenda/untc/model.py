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
lives.  The URI grammar (``untc/<unts>``) is kept here -- ``untc`` is its
only producer. The identity is the **UNTS registration number** in the UN's own
form (``I-14668``, as in ``volume-999-I-14668-English.pdf``): it is what the
UNTS cites itself by, and it survives for an instrument whose depositary is not
the UN, where an MTDSG chapter number does not exist at all.
"""

import functools
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..lib.artifact import Provision as ArtifactProvision
from ..lib.artifact import provision_nodes, prune
from ..lib.catalog import BASE

PUBLISHER = "United Nations"
DEPOSITARY = "UN Secretary-General"
SITE = "https://treaties.un.org"
# the MTDSG status-page URL (also the harvest target); one home for both the
# downloader and the artifact's source_url so the scheme can't drift between them
DETAIL = (SITE + "/pages/ViewDetailsIII.aspx"
          "?src=TREATY&mtdsg_no=%s&chapter=%s&clang=_en")
TREATIES = Path(__file__).resolve().parent / "data" / "treaties.json"
# An article fragment's number, for the artifact's `ordinal`: "A5" -> 5, "AII"
# -> II, "A12BIS" -> 12BIS. An annex that carries text of its own is a
# provision named "AnnexI", and it numbers nothing -- this does not match it.
RE_ORDINAL = re.compile(r"^A(\d+(?:BIS|TER|QUATER)?|[IVXLC]+)$")


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
    return "%suntc/%s" % (BASE, unts)


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
        """The treaty's articles as the shared provision projection mints them,
        which is the node shape `icrc` mints too, so both treaty corpora anchor
        a citation the same way: an ``artikel`` node keyed on its fragment
        (``A5``, ``AII``) over one ``stycke`` per paragraph.

        This source's own `Provision` states no ordinal -- the article number is
        read back out of the fragment here, since only an article has one: an
        annex that carries text of its own ("AnnexI", UNCLOS's list of highly
        migratory species) is a provision under its own name and numbers
        nothing."""
        return provision_nodes(
            ArtifactProvision(
                heading=provision.heading, fragment=provision.fragment,
                ordinal=(match.group(1) if (match := RE_ORDINAL.match(
                    (provision.fragment or "").rsplit("_", 1)[-1])) else None),
                paragraphs=provision.paragraphs)
            for provision in self.provisions)

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
        return prune({
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
        })
