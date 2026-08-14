"""The authentic treaty text, as its depositary publishes it, to provisions.

The MTDSG carries status and no text at all, which is why an untc artifact used
to publish six metadata rows and an empty structure -- nothing for a citation to
land on. The text comes from the depositary instead (see `download`), in two
shapes that reduce to the same line stream:

  * **OHCHR HTML** -- the UN human-rights core, twelve of the fourteen. The
    treaty sits in one ``.field--name-body`` block whose lines are already the
    document's own: a bare "Article II" line, then its paragraphs, then the next.
  * **a born-digital PDF** -- the VCLT from legal.un.org and UNCLOS from
    un.org/depts/los, read with ``pdftotext``. Deliberately not the UNTS's own
    volumes, which are scans (volume 999 carries the ICCPR over 92 pages with an
    image on all 92).

Both give a flat run of lines where an article opens with its own line, so one
splitter serves both and the reader named in ``data/treaties.json`` only decides
how the lines are produced.
"""

import re

from bs4 import BeautifulSoup

from ..lib.pdftext import pdftotext_text
from ..lib.util import normalize_space

# where OHCHR puts the instrument itself, clear of the site's chrome
BODY_SELECTOR = ".field--name-body"
# An article's own line: "Article 5", "Article II", "Article 12 bis", and the
# form three OHCHR pages use, which sets the rubric on the same line --
# 'Article 1 - Definition of the term "refugee"'. Anchored, because the phrase
# runs through the prose of every treaty ("in accordance with article XIII")
# and only the heading opens a line.
# The rubric may follow on the same line after a dash ('Article 1 - Definition
# of the term "refugee"'), after a full stop ("Article 11. Deposit in the
# archives") or after nothing at all ("Article 20 Personal mobility") -- all
# three occur across the fourteen. A bare space is only allowed before a
# *capitalised* word, which is what keeps the running prose of every treaty
# ("Article 5 shall apply mutatis mutandis") from reading as a heading.
RE_ARTICLE = re.compile(
    r"^Article\s+([0-9]+(?:\s*(?:bis|ter|quater))?|[IVXLC]+)"
    r"\s*(?:[-–—.]\s*\S.*|\.?\s+[A-Z“\"(].*|\.?)$")
# A table-of-contents line: the dotted leader that runs to its page number.
# UNCLOS's PDF opens with 33 pages of them, and each entry is an "Article N."
# line of its own -- 885 matches against the Convention's 320 real articles.
RE_DOTTED = re.compile(r"\.\s*\.\s*\.")
# a part/chapter heading, which sits on its own line above an article
RE_HEADING = re.compile(
    r"^(PART|CHAPTER|SECTION|ANNEX|APPENDIX)\b[^.]{0,80}$", re.I)
# An annex opens its own article numbering. UNCLOS has nine of them, so "A1"
# named ten different provisions and 159 of its 601 anchors were ambiguous --
# a link to /untc/I-31363#A1 could not say which Article 1 it meant.
#
# Matched case-sensitively and whole-line, because the heading is "ANNEX I." and
# the prose that cites it is "Annex III, article 11." -- treating the second as
# a heading would reset the numbering in the middle of the Convention's body.
RE_ANNEX = re.compile(r"^(?:ANNEX|APPENDIX)\s+([IVXLC]+|\d+)\s*\.?$")
# The preamble runs from the start to the first article. Below this it is a
# stray line -- an OHCHR "Entry into force:" note, a PDF's running header --
# rather than the treaty's recitals.
PREAMBLE_MIN = 200


def html_lines(html):
    """The instrument's own lines from an OHCHR page."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    body = soup.select_one(BODY_SELECTOR)
    # the page is fetched with an "Article" marker, so a page without the body
    # block is a shape this reader has not seen rather than an empty treaty
    if body is None:
        raise ValueError("OHCHR page carries no %s block" % BODY_SELECTOR)
    return [normalize_space(line)
            for line in body.get_text("\n", strip=True).split("\n")
            if line.strip()]


def pdf_lines(path):
    """The instrument's own lines from a born-digital PDF."""
    return [normalize_space(line) for line in pdftotext_text(path).split("\n")
            if line.strip()]


def fragment(number):
    """An article's stable anchor, in the grammar `icrc` and `coe` already mint:
    Article 5 -> "A5", Article II -> "AII"."""
    return "A" + re.sub(r"\s+", "", number).upper().rstrip(".")


def provisions(lines):
    """A treaty's line stream -> [(fragment, heading, [paragraph])].

    The preamble comes first with no fragment, then one entry per article. A
    part or chapter heading is dropped rather than kept as a provision: it names
    a group of articles and nothing cites it, where every article is a citation
    target.
    """
    out, current, scope = [], None, ""
    preamble = []
    for line in lines:
        if RE_DOTTED.search(line):
            continue
        match = RE_ARTICLE.match(line)
        if match:
            number = normalize_space(match.group(1))
            current = (scope + fragment(number), line, [])
            out.append(current)
            continue
        annex = RE_ANNEX.match(line)
        if annex:
            # an annex restarts the numbering, so its articles are scoped by it
            scope = "Annex%s_" % annex.group(1).upper()
            continue
        if RE_HEADING.match(line):
            # a part, chapter or section does not restart it, so the scope holds
            continue
        (current[2] if current else preamble).append(line)
    # A table-of-contents entry is an article heading with nothing under it,
    # its title having gone with the dotted leader above. Dropping the empty
    # ones is what leaves UNCLOS its 320 articles instead of 885.
    out = [provision for provision in out if provision[2]]
    # a treaty with no article at all means the reader found the wrong block
    if not out:
        raise ValueError("no article heading in %d lines" % len(lines))
    if len(" ".join(preamble)) >= PREAMBLE_MIN:
        out.insert(0, (None, "Preamble", preamble))
    return out
