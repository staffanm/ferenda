"""Build the vocabulary that guides the ICJ scans' OCR repair.

The pre-2002 *I.C.J. Reports* are published as scans, and their OCR layer
carries a systematic error class (``al1`` for all, ``Judgrnent`` for Judgment).
`icj.ocr` repairs a token only when a known confusion turns it into a word the
Court actually uses -- so it needs a list of those words, and the list must not
come from the scans it is meant to fix.

It comes from the decisions the Court published *born-digital*, which this tool
identifies by measurement rather than by year: a page range reproduced from a
scan carries one embedded raster image per page, a typeset one carries none.
Every document whose pages are image-free contributes its words.

    .venv/bin/python tools/icj_vocabulary.py

Writes ``accommodanda/icj/data/vocabulary.txt``, one lower-cased word per line.
Rerun it after a harvest that adds a year of decisions; the file is committed,
so the build never depends on this tool having been run.
"""

import collections
import re
import subprocess
import sys
from pathlib import Path

from accommodanda.icj import parse
from accommodanda.icj.download import body_path, list_basefiles
from accommodanda.lib import compress, layout
from accommodanda.lib.pdftext import pdf_info, pdftotext_text
from accommodanda.lib.util import status

OUT = (Path(__file__).resolve().parent.parent / "accommodanda" / "icj"
       / "data" / "vocabulary.txt")
# a word for repair purposes: letters and the apostrophe the Court sets in
# "State's". Digits are never part of one, so a token carrying any is skipped.
RE_WORD = re.compile(r"[a-z][a-z']+")
# A word seen once in a hundred decisions is as likely to be a typo or a proper
# noun from a citation as a word. Requiring two sightings is what stops the
# repair inventing a rare "word" to justify a rewrite.
#
# It does *not* keep French out, and an earlier version of this comment claimed
# it did. Cutting the Reports' bilingual cover (`body_text` below) removes 210
# words and leaves `cour`, `recueil`, `greffier`, `les`, `des` and `que` in
# place, because the Court's own English text quotes French constantly -- every
# I.C.J. citation carries "Recueil", and some separate opinions are written in
# it. French in the list is harmless for a measured reason instead: a repair
# fires only where a *confusion* turns an English token into one of these, and
# over six scanned decisions and 500 repairs not one did.
MIN_COUNT = 2
# Below this the corpus is not harvested (or the scan test stopped working), and
# a thin vocabulary would silently disarm the repair.
MIN_TYPESET = 10
# A page range reproduced from a scan carries a raster image on every page; a
# typeset one carries images only where the Court prints a sketch-map or its
# seal. Measured over 18 decisions the two classes are 0.95-1.00 against
# 0.00-0.03, so the split is nowhere near either. A date rule would have been
# wrong: the July 2004 Wall opinion is a scan and the December 2004 judgment in
# the same volume is typeset.
SCAN_SHARE = 0.5


def image_pages(path):
    """How many of a PDF's pages carry an embedded raster image.

    ``pdfimages -list`` only reads the image table; `pdftext.pdf_images` would
    answer the same question by running `pdftohtml` *without* ``-i``, which
    extracts every page bitmap to disk first -- minutes per scanned volume."""
    out = subprocess.run(["pdfimages", "-list", str(path)],
                         capture_output=True, check=True, text=True).stdout
    return len({line.split()[0] for line in out.splitlines()[2:] if line.split()})


def is_typeset(path):
    """Whether this PDF is typeset rather than reproduced from a scan."""
    return (image_pages(path)
            < SCAN_SHARE * int(pdf_info(path)["Pages"]))


def body_text(path):
    """The decision's own text, without the Reports' bilingual front matter.

    `pdftotext` reads the whole PDF, cover included, which is how the French
    got into the vocabulary. The seam is the same one `icj.parse` cuts on, so
    the two agree about where a decision starts -- but where the parser raises,
    this keeps the whole text. A vocabulary is a bag of words: a few pages of
    front matter add noise the `MIN_COUNT` floor absorbs, where refusing the
    document would drop every word it holds."""
    text = pdftotext_text(path)
    pages = text.split("\f")
    for index, page in enumerate(pages):
        flat = "".join(page.split()).upper()
        if parse.RE_DATELINE.search(flat) or (
                parse.LETTERHEAD in flat
                and not any(mark in flat for mark in parse.MASTHEAD)):
            return "\f".join(pages[index:])
    return text


def main():
    root = layout.ICJ_DOWNLOADED
    basefiles = sorted(list_basefiles(root))
    counts = collections.Counter()
    typeset = 0
    for done, basefile in enumerate(basefiles, 1):
        status(done, len(basefiles), "icj  reading born-digital decisions")
        body = body_path(root, basefile)
        if not compress.exists(body) or not is_typeset(body):
            continue
        typeset += 1
        counts.update(RE_WORD.findall(body_text(body).lower()))
    sys.stderr.write("\n")
    if typeset < MIN_TYPESET:
        raise ValueError("icj: only %d typeset decisions of %d -- the corpus is "
                         "not harvested, or `pdf_images` no longer sees a scan"
                         % (typeset, len(basefiles)))
    words = sorted(word for word, count in counts.items() if count >= MIN_COUNT)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(words) + "\n", encoding="utf-8")
    print("icj vocabulary: %d words from %d typeset decisions (of %d) -> %s"
          % (len(words), typeset, len(basefiles), OUT))


if __name__ == "__main__":
    main()
