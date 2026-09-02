"""Typed model for a tidskriftsartikel -- one article in one issue of one of
the nine journals in `journals.py`.

An article is a fixed historical publication: it is issued in one issue, it
revises in place nowhere, and it is withdrawn nowhere. Its model is therefore
the thinnest in the corpus -- identity, the issue's coordinates, the author,
and the text (the abstract where the listing states one). There is no currency
axis, no version axis and no relation axis, because the publishers state none.

The text is the mining text, not a republished edition: every paragraph in
order, no structure read off it (see `parse`), because the journals publish
the article as a pointer to their own page or PDF. The only thing the text
must survive is the citation scan that reads it.

The coordinates differ with the journal, and that is data in `journals`, not
branches here. The identifier takes the minimal article-citation form each
journal's own references use -- see `_IDENTIFIER`, one small rule per journal
-- and the basefile (the `slug`) is the same coordinates joined in the order
`journals.Journal.slug_parts` states: ``svjt/2026-104`` (the issue is the
page), ``jp/2026-01-03`` (issue, then place in the issue), ``urt/2026-1-147``
(issue, then page -- UrT numbers no sequence at all).
"""

from dataclasses import dataclass, field

from ..lib.artifact import prune, scanned_nodes
from ..lib.catalog import BASE
from ..lib.util import approximate_date
from .journals import BY_KOD, SCOPES

__all__ = ["Block", "Artikel", "lawreview_uri"]


def lawreview_uri(scope, slug):
    """The published document URI, minted from the scope's own coordinates,
    so the address reproduces the citation by construction -- the rule
    `rs.model.rs_uri` follows for an agency's number. The scope is a journal's
    kod or the platform scope `lawpub`, whose handle mints the same way."""
    if scope not in SCOPES:
        raise ValueError("no such lawreview scope: %r" % scope)
    return "%slawreview/%s/%s" % (BASE, scope, slug)


@dataclass
class Block:
    kind: str            # "stycke" -- no structure is read off the text
    text: str


@dataclass
class Artikel:
    journal: str                            # journals.JOURNALS' kods
    year: str                               # the issue's year, "2026"
    issue: str                              # the page (svjt) or the issue's own number
    titel: str
    seq: str | None = None                  # the place in the issue, "03"
    kind: str | None = None                 # jp's "inledning"; lod's theme ("Leder")
    fattare: str | None = None              # the author, as the listing states
    sammanfattning: str | None = None       # the abstract, as the listing states
    body: list[Block] = field(default_factory=list)   # the mining text
    sida: str | None = None                 # the issue page the article opens on
    date: str | None = None                 # the day the publisher states (euar, lod)
    source_url: str | None = None           # the journal's own page for it
    document_url: str | None = None         # the PDF the journal published it as

    @property
    def slug(self):
        """The basefile with its journal segment off: the journal's own
        coordinates joined in `journals.Journal.slug_parts` order."""
        parts = BY_KOD[self.journal].slug_parts
        out = []
        for part in parts:
            value = getattr(self, part)
            if value is None:
                raise ValueError(
                    "%s %s-%s has no %s for its basefile"
                    % (self.journal, self.year, self.issue, part))
            out.append(str(value))
        return "-".join(out)

    @property
    def uri(self):
        return lawreview_uri(self.journal, self.slug)

    @property
    def identifier(self):
        """The citation form: the minimal article citation the journal's own
        references use. One small rule per journal, keyed off its kod -- see
        `_IDENTIFIER`."""
        return _IDENTIFIER[self.journal](BY_KOD[self.journal], self)

    @property
    def publisher(self):
        return BY_KOD[self.journal].namn

    def to_artifact(self, scanner):
        """The JSON artifact: the shared node convention (`structure` of
        stycke nodes with inline-run text), every text scanned for
        citations -- which is what puts an article's references on the
        context rails of the documents it names. The catalog's date
        projection expects a 10-char ISO date, and every dated document in
        the corpus stores one: the day the publisher states (`date`, the
        euar items' "Publicerad" line) stands as it is, and a bare year is
        widened to a representative day (`lib.util.approximate_date` fills
        its middle). The issue's year stays a separate field."""
        return prune({
            "uri": self.uri, "type": "juridisk_artikel",
            "journal": self.journal,
            "date": self.date or approximate_date(self.year),
            "identifier": self.identifier,
            "metadata": prune({"title": self.titel, "publisher": self.publisher,
                               "year": self.year,
                               "typ": self.kind,
                               "fattare": self.fattare,
                               "sida": self.sida}),
            "structure": scanned_nodes(self.body, scanner),
            "sammanfattning": self.sammanfattning,
            "source_url": self.source_url,
            "document_url": self.document_url})


# --------------------------------------------------------------------------
# one identifier rule per journal
# --------------------------------------------------------------------------

def _id_svjt(journal, artikel):
    """The issue's number *is* the article's opening page ("SvJT 2026 s. 104")."""
    return "%s %s s. %s" % (journal.abbrev, artikel.year, artikel.issue)


def _id_jp(journal, artikel):
    """The opening page the Särtryck's footer prints ("JP 2009 s. 37"); only
    when no page is on record does the place in the issue stand in
    ("JP 2014 jubileumsnummer-02")."""
    if artikel.sida is not None:
        return "%s %s s. %s" % (journal.abbrev, artikel.year, artikel.sida)
    if artikel.seq is None:
        raise ValueError("jp %s %s has neither page nor place"
                         % (artikel.year, artikel.issue))
    label = journal.issue_labels[artikel.issue]
    return "%s %s %s-%s" % (journal.abbrev, artikel.year, label, artikel.seq)


def _id_ft(journal, artikel):
    """The page the PDF's first leaf prints as the issue's running table of
    contents ("FT 2025 s. 23"); when that line states no page, the place in
    the issue stands in ("FT 2026 nr 1-02")."""
    if artikel.sida is not None:
        return "%s %s s. %s" % (journal.abbrev, artikel.year, artikel.sida)
    if artikel.seq is None:
        raise ValueError("ft %s %s has neither page nor place"
                         % (artikel.year, artikel.issue))
    return "%s %s nr %s-%s" % (journal.abbrev, artikel.year, artikel.issue,
                               artikel.seq)


def _id_nmt(journal, artikel):
    """The issue's own number and the page the issue's table of contents
    states ("NMT 2025:2 s. 5"; the special issues are ":s"). The journal's
    oldest hands set no page on some of the lines of a table of contents,
    and where the line states none, the article's place in the issue takes
    the page's turn ("NMT 2017:1 nr 1")."""
    if artikel.sida is None:
        return "%s %s:%s nr %s" % (journal.abbrev, artikel.year,
                                   artikel.issue, artikel.seq)
    return "%s %s:%s s. %s" % (journal.abbrev, artikel.year, artikel.issue,
                               artikel.sida)


def _id_njel(journal, artikel):
    """Issue in parentheses, then the first page of the issue range the
    issue's table of contents gives ("NJEL 2024(1) s. 213"). The journal's
    editorial notes set no page range in the listing (two on record, the
    2019(2) and 2021(1) notes), and where the line states none, the
    article's place in the issue takes the page's turn
    ("NJEL 2019(2) nr 01")."""
    if artikel.sida is None:
        if artikel.seq is None:
            raise ValueError("njel %s(%s) has neither page nor place"
                             % (artikel.year, artikel.issue))
        return "%s %s(%s) nr %s" % (journal.abbrev, artikel.year,
                                    artikel.issue, artikel.seq)
    return "%s %s(%s) s. %s" % (journal.abbrev, artikel.year, artikel.issue,
                                artikel.sida)


def _id_siplr(journal, artikel):
    """The opening page the article's PDF footer prints ("SIPLR 2025 s. 5");
    only where the journal's one scanned article prints no footer does the
    place in the issue stand in ("SIPLR 2020 #1-04")."""
    if artikel.sida is not None:
        return "%s %s s. %s" % (journal.abbrev, artikel.year, artikel.sida)
    if artikel.seq is None:
        raise ValueError("siplr %s %s has neither page nor place"
                         % (artikel.year, artikel.issue))
    return "%s %s #%s-%s" % (journal.abbrev, artikel.year, artikel.issue,
                             artikel.seq)


def _id_urt(journal, artikel):
    """The journal's own form, issue and page both from its listing's
    citation ("UrT 2026 no 1 p. 147")."""
    if artikel.sida is None:
        raise ValueError("urt %s %s states no page"
                         % (artikel.year, artikel.issue))
    return "%s %s no %s p. %s" % (journal.abbrev, artikel.year,
                                  artikel.issue, artikel.sida)


def _id_lod(journal, artikel):
    """The journal's own masthead form, number over year ("Lov & Data
    3/2022"). The web edition prints no page numbers, so the citation stops
    at the issue, and the basefile's sequence number alone keeps the
    issue's articles apart."""
    return "%s %s/%s" % (journal.abbrev, artikel.issue, artikel.year)


def _id_euar(journal, artikel):
    """The newsletter's own number, then the item's place in it
    ("EU & arbetsrätt 2026 nr 2-01")."""
    if artikel.seq is None:
        raise ValueError("euar %s %s has no place in the issue"
                         % (artikel.year, artikel.issue))
    return "%s %s nr %s-%s" % (journal.abbrev, artikel.year, artikel.issue,
                               artikel.seq)


_IDENTIFIER = {"svjt": _id_svjt, "jp": _id_jp, "ft": _id_ft, "nmt": _id_nmt,
               "njel": _id_njel, "siplr": _id_siplr, "urt": _id_urt,
               "euar": _id_euar, "lod": _id_lod}