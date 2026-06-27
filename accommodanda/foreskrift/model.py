"""First-class primitives for the föreskrift vertical: a base **Regulation**
that embeds its **Consolidation**(s) and **Amendment**(s).

Unlike the SFS source we built first, these are **as-published, immutable
documents**: a grundförfattning and each ändringsförfattning is a fixed
historical artifact (an amendment changes the base by being a separate later
document, not by mutating it), so a base/amendment regulation carries no
currency metadata at all. Only a konsoliderad version -- an inofficial
compilation a minority of regulations have -- needs the one fact of *which*
amendments it folds in. The structure layer is grounded in förarbete:

  * förarbete -- the nested ``structure`` tree (``nest``/``flatten``) with §§
    as anchor-bearing leaves, so the body renders with real headings and each
    paragraf is a citation target / inbound-annotation site.
  * förarbete -- the nested ``structure`` tree (``nest``/``flatten``) with §§
    as anchor-bearing leaves, so the body renders with real headings and each
    paragraf is a citation target / inbound-annotation site.

These are kept in the vertical for now; once a second författningssamling is
built the genuinely shared shape can be promoted to ``lib`` (the rewrite's
"extract after the second instance" rule). The artifact on disk (``to_artifact``)
is the source of truth; the dataclasses are the in-memory model.

URI scheme: ``https://lagen.nu/{fs}/{year}:{lopnummer}`` (e.g.
``https://lagen.nu/fffs/2013:10``) -- the historical lagen.nu identifier, the
SFS pattern with the författningssamling prefix that disambiguates one agency's
2013:10 from another's. ``bemyndigande`` points into SFS at the empowering
paragraf (``https://lagen.nu/1977:1166#P18``), the edge that makes a statute's
page list the regulations issued under it.
"""

from dataclasses import dataclass, field

BASE = "https://lagen.nu"


def regulation_uri(fs, arsutgava, lopnummer):
    """The citation-target URI for a regulation, language-neutral and stable."""
    return "%s/%s/%s:%s" % (BASE, fs, arsutgava, lopnummer)


@dataclass
class Amendment:
    """An ändringsförfattning: a later regulation that changes the base one.
    Captured as a reference (identity + its own PDF); its body, when we parse
    it, is just another Regulation in its own right."""
    identifier: str              # "FFFS 2026:27"
    uri: str                     # https://lagen.nu/fffs/2026:27
    file: str | None = None      # stored amendment PDF, if downloaded
    beslutsdatum: str | None = None


@dataclass
class Consolidation:
    """A konsoliderad version: the base regulation with a run of
    ändringsförfattningar folded into one full-text version. It is an *inofficial*
    compilation -- the printed författning stays the officially valid text (an
    officially consolidated reprint is instead an *Omtryck*) -- and only a small
    minority of regulations have one. The one fact that pins it is *which*
    amendments it incorporates: ``konsolideradTom`` is the most recent one folded
    in ('konsoliderad t.o.m. FFFS 2026:6'), a föreskrift uri. NOT a date -- a
    'senast uppdaterad' date is just when the file was regenerated, and an
    amendment's enactment date conflates which-amendment with when-enacted."""
    of: str                      # base regulation uri
    konsolideradTom: str | None = None   # uri of the most recent amendment folded in
    file: str | None = None      # stored konsoliderad PDF
    structure: list = field(default_factory=list)


@dataclass
class Regulation:
    """A grundförfattning (or, standalone, an ändringsförfattning) -- the
    first-class unit of the vertical. One harvested record per base regulation,
    embedding the consolidation(s) and amendment(s) its landing page lists."""
    uri: str
    identifier: str              # "FFFS 2013:10"
    fs: str                      # författningssamling code, "fffs"
    arsutgava: str               # "2013"
    lopnummer: str               # "10"
    title: str | None = None
    publisher: str | None = None         # the issuing agency (org)
    is_amendment: bool = False           # grund vs ändringsförfattning
    amends: str | None = None            # base reg uri, iff is_amendment

    # metadata that only the PDF text carries (filled at parse, not harvest)
    beslutsdatum: str | None = None
    ikrafttradandedatum: str | None = None
    utkomFranTryck: str | None = None
    bemyndigande: list = field(default_factory=list)   # SFS paragraf uris
    upphaver: list = field(default_factory=list)       # föreskrift uris
    andrar: list = field(default_factory=list)         # föreskrift uris
    genomfor: list = field(default_factory=list)       # EU directive uris

    structure: list = field(default_factory=list)      # förarbete-style §§ tree
    consolidations: list = field(default_factory=list) # Consolidation
    amendments: list = field(default_factory=list)     # Amendment
    file: str | None = None              # the original grundförfattning PDF
    source_url: str | None = None        # the agency landing page ("Källa")

    def to_artifact(self):
        """The on-disk artifact: a plain dict, the source of truth, shaped like
        the other verticals' artifacts (a typed envelope the catalog walks)."""
        art = {
            "type": "foreskrift",
            "uri": self.uri,
            "identifier": self.identifier,
            "fs": self.fs,
            "metadata": {
                "arsutgava": self.arsutgava,
                "lopnummer": self.lopnummer,
                "title": self.title,
                "publisher": self.publisher,
                "beslutsdatum": self.beslutsdatum,
                "ikrafttradandedatum": self.ikrafttradandedatum,
                "utkomFranTryck": self.utkomFranTryck,
                "is_amendment": self.is_amendment,
                "amends": self.amends,
                "bemyndigande": self.bemyndigande,
                "upphaver": self.upphaver,
                "andrar": self.andrar,
                "genomfor": self.genomfor,
            },
            "structure": self.structure,
            "consolidations": [
                {"of": c.of, "konsolideradTom": c.konsolideradTom,
                 "structure": c.structure}
                for c in self.consolidations
            ],
            "amendments": [
                {"identifier": a.identifier, "uri": a.uri,
                 "beslutsdatum": a.beslutsdatum}
                for a in self.amendments
            ],
        }
        if self.source_url:
            art["source_url"] = self.source_url
        return art
