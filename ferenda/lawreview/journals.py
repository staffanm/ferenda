"""The nine journals this source collects, as data. (The source's tenth
scope, the lawpub platform, is not a journal and holds no entry here: its
coordinates are per-article -- publisher, edition, month-year -- and its
record shape, model and parse live in `lawpub.py`.)

The shape of a harvested document does not vary with the journal, and what
does vary is data: where the journal publishes, where its documents are pages
or PDFs, what its document reader is, and where an article's opening page
comes from. That lives here, and nothing in the vertical branches on the
journal code except to read one of these entries.

The documents are of two kinds:

  * **pages** (`html_document`) -- svjt sets every article as a web page
    (1916 and all), EU och arbetsrätt sets every newsletter item as one, and
    Lov & Data sets every article since its 2022 volume as one (its earlier
    volumes are full-issue PDFs only, and stay out until they are pages)
    (`_svjt_body` / `_euar_body` / `_lod_body` read the stored page, keyed
    off `page_reader`).
  * **PDFs** -- jp, ft, nmt, njel, siplr and urt publish the article as a
    per-article PDF, and the issue page (or the issue's table of contents) is
    the listing entry that names it.

And an article's opening page (`sida`, the coordinate its identifier carries)
comes from four places, keyed off `sida_kalla`:

  * ``"footer"`` -- the PDF prints it as a page footer (jp's Särtryck; the
    siplr article's own `-- N --` hand, which the journal has dropped from
    its one scanned article, so that one takes its place in the issue);
  * ``"head"``   -- the PDF's first leaf prints the issue's running table of
    contents, and the article's line there ends in its page (ft);
  * ``"record"`` -- the listing line states it (nmt, njel, urt);
  * ``None``     -- the journal states no page: svjt's `issue` *is* the page,
    and euar and lod number the item's place in the issue instead.

`issue_labels` is jp's alone: its issues are "01"/"02" or the jubileumsnummer
"J", and only the identifier writes them out in words. `slug_parts` is the
order the journal's own coordinates enter its basefile -- svjt's is
``year-issue`` (the issue is a page), urt's ``year-issue-sida`` (no sequence
at all), and the rest ``year-issue-seq``.
"""

from dataclasses import dataclass

__all__ = ["Journal", "SVJT", "JP", "FT", "NMT", "NJEL", "SIPLR", "URT",
           "EUAR", "LOD", "JOURNALS", "BY_KOD", "SCOPES"]


@dataclass(frozen=True)
class Journal:
    kod: str            # the basefile's first segment ("svjt", "jp", ...)
    namn: str           # full name, the artifact's publisher
    abbrev: str         # the short form the identifier carries
    base: str           # the host's root
    listings: tuple[str, ...]   # the page(s) that enumerate the whole archive
    html_document: bool  # the document is a web page, not a PDF
    page_reader: str | None  # the stored page's body reader ("svjt", "euar")
    sida_kalla: str | None   # "footer", "head", "record" or None
    issue_labels: dict[str, str] | None
    slug_parts: tuple[str, ...]


SVJT = Journal(kod="svjt", namn="Svensk Juristtidning", abbrev="SvJT",
               base="https://svjt.se",
               listings=("https://svjt.se/arkiv",),
               html_document=True, page_reader="svjt", sida_kalla=None,
               issue_labels=None,
               slug_parts=("year", "issue"))

JP = Journal(kod="jp", namn="Juridisk Publikation", abbrev="JP",
             base="https://juridiskpublikation.se",
             listings=("https://juridiskpublikation.se/tidskriften/",),
             html_document=False, page_reader=None, sida_kalla="footer",
             issue_labels={"01": "nr 1", "02": "nr 2",
                           "J": "jubileumsnummer"},
             slug_parts=("year", "issue", "seq"))

FT = Journal(kod="ft", namn="Förvaltningsrättslig tidskrift", abbrev="FT",
             base="https://www.forvaltningsrattslig.org",
             listings=("https://www.forvaltningsrattslig.org/Journals",),
             html_document=False, page_reader=None, sida_kalla="head",
             issue_labels=None,
             slug_parts=("year", "issue", "seq"))

NMT = Journal(kod="nmt", namn="Nordisk miljörätt", abbrev="NMT",
              base="https://nordiskmiljoratt.se",
              listings=("https://nordiskmiljoratt.se/earlier-issues.html",
                        "https://nordiskmiljoratt.se/latest-issue.html"),
              html_document=False, page_reader=None, sida_kalla="record",
              issue_labels=None,
              slug_parts=("year", "issue", "seq"))

NJEL = Journal(kod="njel", namn="Nordic Journal of European Law",
               abbrev="NJEL",
               base="https://journals.lub.lu.se/njel",
               listings=("https://journals.lub.lu.se/njel/issue/archive",),
               html_document=False, page_reader=None, sida_kalla="record",
               issue_labels=None,
               slug_parts=("year", "issue", "seq"))

SIPLR = Journal(kod="siplr", namn="Stockholm Intellectual Property Law Review",
                abbrev="SIPLR",
                base="https://stockholmiplawreview.com",
                listings=("https://stockholmiplawreview.com/issues/",),
                html_document=False, page_reader=None, sida_kalla="footer",
                issue_labels=None,
                slug_parts=("year", "issue", "seq"))

URT = Journal(kod="urt", namn="Upphandlingsrättslig Tidskrift", abbrev="UrT",
              base="https://urt.cc",
              listings=("https://urt.cc/open-access/",),
              html_document=False, page_reader=None, sida_kalla="record",
              issue_labels=None,
              slug_parts=("year", "issue", "sida"))

EUAR = Journal(kod="euar", namn="EU och arbetsrätt",
               abbrev="EU & arbetsrätt",
               base="https://euocharbetsratt.se",
               listings=("https://euocharbetsratt.se/nyhetsbrev/",),
               html_document=True, page_reader="euar", sida_kalla=None,
               issue_labels=None,
               slug_parts=("year", "issue", "seq"))

LOD = Journal(kod="lod", namn="Lov & Data",
              abbrev="Lov & Data",
              base="https://lod.lovdata.no",
              listings=("https://lod.lovdata.no/journal",),
              html_document=True, page_reader="lod", sida_kalla=None,
              issue_labels=None,
              slug_parts=("year", "issue", "seq"))

JOURNALS = (SVJT, JP, FT, NMT, NJEL, SIPLR, URT, EUAR, LOD)
BY_KOD = {j.kod: j for j in JOURNALS}

# every scope the source harvests: the nine journals above plus lawpub, the
# platform scope, which holds no Journal entry because its coordinates are
# per-article (`lawpub.py`). This is what a scope name is checked against --
# the download's SYNC table names the same ten.
SCOPES = tuple(BY_KOD) + ("lawpub",)