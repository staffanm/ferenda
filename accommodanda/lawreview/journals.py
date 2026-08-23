"""The two journals this source collects, as data.

The shape of a harvested document does not vary with the journal, and what
does vary is data: where the journal publishes, where its documents are pages
or PDFs, and how it names its own issues. That lives here, and nothing in the
vertical branches on the journal code except to read one of these two entries.

The one real difference is the document itself:

  * **svjt** (Svensk Juristtidning, since 1916) publishes every article as a
    web page, the whole archive 1916-2026, and a PDF of newer issues beside
    it. The page is the document: it is complete for every year, and the PDF
    is the same text with page breaks.

  * **jp** (Juridisk Publikation, since 2009) publishes its articles only as
    PDFs in an issue; the issue's web page carries the metadata (title,
    author, abstract) and nothing of the text. There the PDF is the document
    and the issue page is its listing entry.
"""

from dataclasses import dataclass

__all__ = ["Journal", "SVJT", "JP", "JOURNALS", "BY_KOD"]


@dataclass(frozen=True)
class Journal:
    kod: str            # the basefile's first segment ("svjt", "jp")
    namn: str           # full name, the artifact's publisher
    abbrev: str         # the short form the identifier carries ("SvJT", "JP")
    base: str           # the host's root
    listing: str        # the page that enumerates the whole archive
    html_document: bool  # the document is a web page (svjt), not a PDF (jp)


SVJT = Journal(kod="svjt", namn="Svensk Juristtidning", abbrev="SvJT",
               base="https://svjt.se", listing="https://svjt.se/arkiv",
               html_document=True)

JP = Journal(kod="jp", namn="Juridisk Publikation", abbrev="JP",
             base="https://juridiskpublikation.se",
             listing="https://juridiskpublikation.se/tidskriften/",
             html_document=False)

JOURNALS = (SVJT, JP)
BY_KOD = {j.kod: j for j in JOURNALS}