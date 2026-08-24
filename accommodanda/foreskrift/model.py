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

from .agencies import REGISTRY

BASE = "https://lagen.nu"


def regulation_uri(fs, arsutgava, lopnummer):
    """The citation-target URI for a regulation, language-neutral and stable."""
    return "%s/%s/%s:%s" % (BASE, fs, arsutgava, lopnummer)


def printed_designation(uri):
    """The designation a regulation is cited by ("RPSFS 2011:16"), read back out
    of its URI -- `regulation_uri`'s inverse, spelled the same way the harvest
    spells an identifier (`agency.designation or fs.upper()`).

    For the regulations a page references but the corpus does not hold: a
    repealed predecessor series nobody harvests still has to be *named* in the
    Upphäver row, and naming it "rpsfs/2011:16" tells the reader the slug rather
    than the citation. `None` for a URI that is not a regulation's."""
    rest = uri.removeprefix(BASE + "/")
    fs, _, number = rest.partition("/")
    if not number or "/" in number:
        return None
    agency = REGISTRY.get(fs)
    return "%s %s" % ((agency.designation if agency and agency.designation
                       else fs.upper()), number)


@dataclass
class Block:
    """One typed block of a föreskrift's printed body, in document order, before
    :func:`structure.nest` folds the run into the kapitel/paragraf tree.

    Flat except for ``children``, which carries the one nesting the page layout
    states rather than the statute does: an **allmänt råd**, the advisory text a
    föreskrift sets in smaller type under the paragraf it explains. A råd is not
    binding (the documents say so themselves), so it must not read as another
    stycke of the § above it."""
    kind: str                   # kapitel | paragraf | rubrik | stycke | lista
                                # | punkt | tabell | allmanna_rad | ingress
    text: str = ""
    page: int | None = None
    num: str | None = None      # kapitel/paragraf number ("3", "3 a")
    size: int = 0               # the opening line's font size (0 = unknown)
    level: int | None = None    # rubrik depth, ranked over the document's sizes
    rows: list[tuple[str, ...]] | None = None   # tabell: its cell rows
    th: bool = False            # tabell: row 0 is the column header
    children: list["Block"] = field(default_factory=list)


@dataclass
class Amendment:
    """An ändringsförfattning: a later regulation that changes the base one.
    Captured as a reference (identity + its own PDF); its body, when we parse
    it, is just another Regulation in its own right. `identifier`/`uri` are
    None when the agency's link carried no readable designation (some PMFS
    entries) -- the `url` still pins the reference to its source."""
    identifier: str | None       # "FFFS 2026:27"
    uri: str | None              # https://lagen.nu/fffs/2026:27, minted from identifier
    url: str | None = None       # the agency's own link for the amendment
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
    url: str | None = None       # the agency's own link for the konsoliderad PDF
    structure: list[dict] = field(default_factory=list)
    # its own page-foot notes, beside its own body: the consolidated text is a
    # different document from the base, so the base's notes do not describe it
    footnotes: list[dict] = field(default_factory=list)


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

    # metadata that only the PDF text carries (filled at parse, not harvest)
    beslutsdatum: str | None = None
    ikrafttradandedatum: str | None = None
    utkomFranTryck: str | None = None
    bemyndigande: list[str] = field(default_factory=list)   # SFS paragraf uris
    upphaver: list[str] = field(default_factory=list)       # föreskrift uris
    andrar: list[str] = field(default_factory=list)         # föreskrift uris
    genomfor: list[str] = field(default_factory=list)       # EU directive uris

    structure: list[dict] = field(default_factory=list)  # förarbete-style §§ tree
    # the notes the printed pages set below a rule at their foot, already
    # citation-scanned into runs like `structure`: a föreskrift grounds the EU
    # directive it transposes in exactly such a note ("Jfr Europaparlamentets
    # och rådets direktiv …")
    footnotes: list[dict] = field(default_factory=list)
    consolidations: list["Consolidation"] = field(default_factory=list)
    amendments: list["Amendment"] = field(default_factory=list)
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
                "bemyndigande": self.bemyndigande,
                "upphaver": self.upphaver,
                "andrar": self.andrar,
                "genomfor": self.genomfor,
                # the amendment register's minted uris, projected as the typed
                # inverse relation (X ändrar this regulation) so the catalog's
                # field-driven producer sees relations only under metadata
                "andradAv": [a.uri for a in self.amendments if a.uri],
            },
            "structure": self.structure,
            "consolidations": [
                {"of": c.of, "konsolideradTom": c.konsolideradTom,
                 "url": c.url, "structure": c.structure,
                 "footnotes": c.footnotes}
                for c in self.consolidations
            ],
            "amendments": [
                {"identifier": a.identifier, "uri": a.uri, "url": a.url,
                 "beslutsdatum": a.beslutsdatum}
                for a in self.amendments
            ],
        }
        if self.footnotes:
            art["footnotes"] = self.footnotes
        if self.source_url:
            art["source_url"] = self.source_url
        return art
