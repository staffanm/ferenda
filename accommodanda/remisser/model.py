"""Typed model for remiss (public referral) cases from regeringen.se.

A remiss sends a SOU/Ds out for consultation; over the referral period answers
("remissvar") accumulate from courts, agencies and organisations. This corpus
is never published as its own pages -- it only feeds a later LLM analysis that
surfaces sentiment/quotes on the referred SOU/Ds page -- so the model captures
just the harvest facts: the case, its cross-reference to the referred förarbete
(`remitterat`), and the per-organisation answers (`svar`).

Every field is a plain JSON scalar -- ISO date *strings*, not `datetime.date`,
matching the forarbete records -- so `dataclasses.asdict` round-trips straight
to the on-disk record (the source of truth), and `from_dict` reads it back.
"""

from dataclasses import asdict, dataclass, field


def org_slug(source_url):
    """The PDF basename (no extension) an answer is filed under -- the shared
    derivation `download.py` (fetch), `parse.py` (read back) and `build.py`
    (enumerate parse targets) all key on, so the three agree on identity."""
    name = source_url.rstrip("/").rsplit("/", 1)[-1]
    return name[:-4] if name.lower().endswith(".pdf") else name


@dataclass
class Remissinstans:
    """One organisation that has answered the remiss, with its answer PDF."""
    organisation: str
    source_url: str
    downloaded: bool = False


@dataclass
class Remissvar:
    """One organisation's answer, parsed from its PDF -- the parse-stage
    artifact. Carries a copy of the case's own `titel`/`remitterat` (rather
    than just `case_basefile`) so the downstream LLM analysis reads a single
    self-contained artifact per answer, without re-joining the case record."""
    basefile: str                    # "<case-slug>/<org-slug>"
    case_basefile: str
    organisation: str
    case_titel: str
    remitterat: list[dict[str, str]]   # copied from the case's Remiss.remitterat
    source_url: str
    full_text: list[str]             # ordered paragraph texts, dehyphenated

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**data)


@dataclass
class Remiss:
    basefile: str                       # the regeringen.se case slug
    titel: str
    url: str                            # the full case-page URL
    dnr: str | None = None
    departement: str | None = None
    publicerad: str | None = None       # ISO date
    uppdaterad: str | None = None       # ISO date
    sista_svarsdag: str | None = None    # ISO date -- the referral deadline
    # forarbete cross-refs from the "Genvägar" link(s): {"typ", "basefile"}
    remitterat: list[dict[str, str]] = field(default_factory=list)
    remissinstanser_pdf: str | None = None   # the single "who was asked" PDF
    svar: list[Remissinstans] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        data["svar"] = [Remissinstans(**s) for s in data.get("svar", [])]
        return cls(**data)
