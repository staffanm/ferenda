"""Typed model for a tidskriftsartikel -- one article in one issue of one of
the two journals in `journals.py`.

An article is a fixed historical publication: it is issued in one issue, it
revises in place nowhere, and it is withdrawn nowhere. Its model is therefore
the thinnest in the corpus -- identity, the issue's coordinates, the author,
the abstract, and the text. There is no currency axis, no version axis and no
relation axis, because the publisher states none.

The text is the mining text, not a republished edition: every paragraph in
order, no structure read off it (see `parse`), because the site shows the
article as a pointer to the journal's own page and PDF. The only thing the
text must survive is the citation scan that reads it.

The coordinates differ with the journal, and that is data in `journals`, not
branches here:

  * **svjt** numbers each article by the page of its issue it opens on
    ("SvJT 2026 s. 104"). `issue` is that page number and there is no
    sequence.
  * **jp** numbers no sequence at all; the article's place in its issue is
    its order in the issue's table of contents, carried as a two-digit `seq`
    ("JP 2025 nr 1-03"), and its citation page is the `sida` the Särtryck
    prints as its page footer ("JP 2009 s. 37"). An issue is "01" or "02",
    or "J" for the jubileumsnummer.

The article's citation takes the minimal form articles get -- abbreviated
journal name, year, opening page ("JP 2009 s. 37") -- and only when no page
is on record does the article's place in the issue stand in for it ("JP 2014
jubileumsnummer-02").

URI scheme: ``https://lagen.nu/lawreview/{journal}/{year}-{issue}[-{seq}]`` --
the svjt grammar with the jp sequence appended, and the basefile is the same
address with the host off, which is why `slug` is the one field both derive
from.
"""

from dataclasses import dataclass, field

from ..lib.artifact import scanned_nodes
from ..lib.catalog import BASE
from ..lib.util import approximate_date
from .journals import BY_KOD

__all__ = ["Block", "Artikel", "lawreview_uri"]


def lawreview_uri(journal, slug):
    """The published document URI, minted from the journal's own coordinates,
    so the address reproduces the citation by construction -- the rule
    `rs.model.rs_uri` follows for an agency's number."""
    if journal not in BY_KOD:
        raise ValueError("no such journal: %r" % journal)
    return "%slawreview/%s/%s" % (BASE, journal, slug)


@dataclass
class Block:
    kind: str            # "stycke" -- no structure is read off the text
    text: str


@dataclass
class Artikel:
    journal: str                            # journals.JOURNALS' kods
    year: str                               # the issue's year, "2026"
    issue: str                              # the page (svjt) or 01/02/J (jp)
    titel: str
    seq: str | None = None                  # the jp place in its issue, "03"
    kind: str | None = None                 # "inledning" (the jp editors' words)
    fattare: str | None = None              # the author, as the listing states
    sammanfattning: str | None = None       # the abstract, as the listing states
    body: list[Block] = field(default_factory=list)   # the mining text
    sida: str | None = None                # the issue page the article opens on (jp)
    source_url: str | None = None           # the journal's own page for it
    document_url: str | None = None         # the PDF the journal published it as

    @property
    def slug(self):
        if self.seq is None:
            return "%s-%s" % (self.year, self.issue)
        return "%s-%s-%s" % (self.year, self.issue, self.seq)

    @property
    def uri(self):
        return lawreview_uri(self.journal, self.slug)

    @property
    def identifier(self):
        """The citation form: the minimal article citation, abbreviated name,
        year, opening page ("SvJT 2026 s. 104", "JP 2009 s. 37"). The opening
        page is the article's `issue` (svjt) or its Särtryck footer page
        `sida` (jp); only when a jp article has no page on record does its
        place in the issue stand in ("JP 2014 jubileumsnummer-02")."""
        journal = BY_KOD[self.journal]
        page = self.sida if self.sida is not None else \
            (self.issue if self.seq is None else None)
        if page is not None:
            return "%s %s s. %s" % (journal.abbrev, self.year, page)
        issue = {"01": "nr 1", "02": "nr 2", "J": "jubileumsnummer"}
        if self.issue not in issue:
            raise ValueError("no jp issue label for %r" % self.issue)
        return "%s %s %s-%s" % (journal.abbrev, self.year, issue[self.issue],
                                self.seq)

    @property
    def publisher(self):
        return BY_KOD[self.journal].namn

    def to_artifact(self, scanner):
        """The JSON artifact: the shared node convention (`structure` of
        stycke nodes with inline-run text), every text scanned for
        citations -- which is what puts an article's references on the
        context rails of the documents it names. The year is the one date
        the publisher states and it is widened to a representative day
        (`lib.util.approximate_date` fills a bare year's middle), because
        the catalog's date projection expects a 10-char ISO date, and every
        dated document in the corpus stores one. The issue's year stays a
        separate field."""
        structure = scanned_nodes(self.body, scanner)
        metadata = {"title": self.titel, "publisher": self.publisher,
                    "year": self.year}
        if self.kind:
            metadata["typ"] = self.kind
        if self.fattare:
            metadata["fattare"] = self.fattare
        if self.sida:
            metadata["sida"] = self.sida
        art = {"uri": self.uri, "type": "juridisk_artikel",
               "journal": self.journal,
               "date": approximate_date(self.year),
               "identifier": self.identifier,
               "metadata": metadata, "structure": structure}
        if self.sammanfattning:
            art["sammanfattning"] = self.sammanfattning
        if self.source_url:
            art["source_url"] = self.source_url
        if self.document_url:
            art["document_url"] = self.document_url
        return art