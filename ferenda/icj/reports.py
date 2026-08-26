"""The Court's own citation grammar: "I.C.J. Reports 1990, p. 92".

An ICJ decision cites its precedent by the annual Reports volume and the page
the cited decision *starts* on -- 4,012 such citations across 250 of the 255
held decisions, every one plain text. The key is in the corpus already: each
decision's cover sheet prints its own official citation ("Official citation:
…, I.C.J. Reports 1990, p. 92", with a French twin), so the held PDFs
themselves say which (year, page) each decision is.

Resolution is exact-start-page only. A pinpoint cite ("I.C.J. Reports 1950,
p. 71" reaching into an opinion that starts at p. 65) would need every
decision's page *range*, and with 255 of the Court's 877 decisions held, the
gap between two held start pages proves nothing about whose pages lie in it
-- attributing a pinpoint to the nearest held start would mislink citations
of the unheld decisions in between (rule:fail-fast). The canonical citation
form leads with the start page, so exact matching carries the bulk.

A decision too recent for a printed volume has no official citation yet, and
correctly indexes nothing.
"""

import functools
import re
import subprocess
from pathlib import Path

from ..lib.lagrum import Ref
from ..lib.pdftext import pages_with_ocr
from .model import decision_uri

PREDICATE = "dcterms:references"

# The cover block, as the scans actually spell it: OCR renders "Official" as
# "Officia1", "I.C.J." as "C.Z.J." and the French "C.I.J. Recueil" ranges
# further. Year first, then the volume half some years split into ("1996 (I)"
# -- the two halves paginate independently, so the half is part of the key),
# then the page. The *first* match on the cover is the English citation; the
# French twin repeats the same numbers.
RE_OFFICIAL = re.compile(
    r"(?:I\s*\.?\s*C\s*\.?\s*[JZ]\s*\.?\s*(?:Reports|Recueil)"
    r"|C\s*\.?\s*[IZ1l]\s*\.?\s*J\s*\.?\s*Recueil)\s*"
    r"(?P<year>\d{4})\s*(?:\((?P<volume>[IVX]+)\))?\s*,?\s*"
    r"pp?\s*\.?\s*(?P<page>\d+)")
# how the running text cites: tighter than the cover form (body text is
# reflowed, not raw scan debris), volume half included where the year needs it
RE_CITE = re.compile(
    r"I\.?\s?C\.?\s?J\.?\s+Reports\s+(?P<year>\d{4})"
    r"\s*(?:\((?P<volume>[IVX]+)\))?,?\s*(?:at\s+)?pp?\.\s*(?P<page>\d+)")
COVER_PAGES = 3


def _cover_citation(pdf_path, basefile):
    """(year, volume, start page) from a decision's own cover sheet, or None
    where no printed volume exists yet (or the scan lost the block).

    `pdftotext` on the cover pages first: it reads the scans' invisible OCR
    layer too, and costs milliseconds where the full-document conversion
    `pages_with_ocr` runs takes seconds per decision. Only a PDF whose cover
    yields nothing that way -- a scan with no text layer at all -- pays for
    the OCR route."""
    text = subprocess.run(
        ["pdftotext", "-l", str(COVER_PAGES), str(pdf_path), "-"],
        capture_output=True, text=True, check=True).stdout
    m = RE_OFFICIAL.search(" ".join(text.split()))
    if not m:
        pages = pages_with_ocr(str(pdf_path), ("icj", basefile), lang="eng")
        text = " ".join(line.text for _no, lines in pages[:COVER_PAGES]
                        for line in lines)
        m = RE_OFFICIAL.search(text)
    if not m:
        return None
    return (m.group("year"), m.group("volume") or "", m.group("page"))


def own_citation(pdf_path, basefile):
    """The decision's own citable form, for its metadata: "I.C.J. Reports
    1996 (I), p. 226". None until a printed volume exists."""
    key = _cover_citation(pdf_path, basefile)
    if not key:
        return None
    year, volume, page = key
    return "I.C.J. Reports %s%s, p. %s" % (
        year, " (%s)" % volume if volume else "", page)


@functools.lru_cache(maxsize=1)
def index(root):
    """{(year, volume, start page) -> basefile} over every held decision,
    read off the covers of the PDFs already on disk (the conversion cache
    makes the sweep cheap after one parse run).

    Two decisions cannot legitimately start on the same Reports page, so a
    key claimed twice is an OCR-misread cover -- and linking every citation
    of it to whichever claimant sorts first would be a guess. The colliding
    key is dropped whole: unlinked over mislinked, like the pinpoint rule."""
    out, collided = {}, set()
    for pdf in sorted(Path(root).glob("*.pdf")):
        key = _cover_citation(pdf, pdf.stem)
        if key in out:
            collided.add(key)
        elif key:
            out[key] = pdf.stem
    return {key: basefile for key, basefile in out.items()
            if key not in collided}


def refs(text, own, root):
    """Every "I.C.J. Reports YYYY, p. N" citation in one block of text whose
    (year, page) is a held decision's own start page, as `lagrum.Ref` spans.
    The Court cites itself this way constantly; a self-citation (a decision's
    cover form quoted in its own text) stays unlinked."""
    out = []
    for m in RE_CITE.finditer(text):
        key = (m.group("year"), m.group("volume") or "", m.group("page"))
        basefile = index(root).get(key)
        if basefile and basefile != own:
            out.append(Ref(m.start(), m.end(), m.group(0), PREDICATE,
                           decision_uri(basefile)))
    return out
