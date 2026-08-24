"""The lawpub parse phase: one downloaded article's harvest record plus its
PDF -> a mined-text artifact, the way `lawreview.parse` does.

An article's only durable content is its running text, which the platform
serves as a PDF: the parse reads the stored PDF's pages back into paragraphs
(`lib.pdftext.pdf_paragraph_texts`) and hands them, citation-scanned, to the
catalog. Nothing of structure is read off the text -- the article is published
as a PDF, and the text only has to survive the citation scan that puts the
references it makes on the context rails of the documents it names.
"""

from ..lib import compress
from ..lib.lagrum import ALL_PARSE_TYPES, sfs_parser
from ..lib.pdftext import pdf_paragraph_texts
from .download import pdf_path, record_path
from .model import Artikel, Block

LAWPUB_PARSE_TYPES = ALL_PARSE_TYPES


def parse(basefile, root):
    """One basefile ("880", "10.53292-c42237cc.fe896fd9") ->
    artifact dict, body citation-scanned."""
    record = compress.read_json(record_path(root, basefile))
    pdf = pdf_path(root, basefile)
    body = [Block("stycke", text)
            for text in pdf_paragraph_texts(pdf, ("lawpub", basefile))]
    return Artikel(
        basefile=basefile,
        titel=record["titel"],
        utgivare=record["utgivare"],
        utgivare_namn=record.get("utgivare_namn"),
        utgava=record.get("utgava"),
        fattare=record.get("fattare"),
        date=record.get("date"),
        sida=record.get("sida"),
        sammanfattning=record.get("sammanfattning"),
        body=body,
        source_url=record.get("source_url"),
        document_url=record.get("document_url"),
    ).to_artifact(sfs_parser(basefile, LAWPUB_PARSE_TYPES,
                             # an undated article scans under today's law
                             # (sfs_parser's own default for no date)
                             written=record.get("date")))