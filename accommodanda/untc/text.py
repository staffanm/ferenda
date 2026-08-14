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
from .model import RE_ORDINAL

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
#
# The arabic number admits a lowercase L among its digits. The UNCLOS PDF sets
# two of its numerals with an L for the digit 1 -- "Article 3l" and "Article
# 4l", Annex VI articles 31 and 41 -- and each of the two had no anchor of its
# own and printed its text under the article above it. The run must still carry
# a real digit, so a pure number is never touched; `fragment` repairs it.
RE_ARTICLE = re.compile(
    r"^Article\s+([0-9l]*[0-9][0-9l]*(?:\s*(?:bis|ter|quater))?|[IVXLC]+)"
    r"\s*(?:[-–—.]\s*\S.*|\.?\s+[A-Z“\"(].*|\.?)$")
# A contents entry's dotted leader, which runs from the entry's title to its
# page number. Five dots at least: three is the ellipsis that two OHCHR pages
# set in running prose, and reading that as a contents entry dropped the whole
# of the Refugee Protocol's article 1(2).
RE_LEADER = re.compile(r"\.(?:\s*\.){4,}")
# How far apart two leader lines sit and still belong to one contents block.
# UNCLOS's own contents breaks its longest run over 23 lines of wrapped titles,
# and 8 027 lines of treaty separate that block from the contents of the Final
# Act printed after it, so every threshold between the two reads alike.
LEADER_GAP = 100
# How many leader lines make a contents block. A contents block lists a
# document: UNCLOS's own runs 500 leader lines and the Final Act's, the
# smallest real block in the corpus, runs 7. One leader line is a line of the
# treaty -- a schedule row, a tariff table, a signature page -- and reading it
# as a block cuts every article above it and publishes the remainder green.
MIN_LEADER_LINES = 5
# a part/chapter heading, which sits on its own line above an article
RE_HEADING = re.compile(
    r"^(PART|CHAPTER|SECTION|ANNEX|APPENDIX)\b[^.]{0,80}$", re.I)
# An annex opens its own article numbering. UNCLOS has nine of them, so "A1"
# named ten different provisions and 159 of its 601 anchors were ambiguous --
# a link to /untc/I-31363#A1 could not say which Article 1 it meant.
#
# The label carries its title on the same line, so the line is matched to its
# end on anything *but a lowercase letter*: "ANNEX I. HIGHLY MIGRATORY SPECIES"
# is the heading, and "Annex III, article 11." is the prose that cites one.
# Reading the second as a heading would restart the numbering mid-body.
#
# A trailing period does not tell the two apart. UNCLOS lists "ANNEX I." to
# "ANNEX IX." in its contents *and* prints "ANNEX I. HIGHLY MIGRATORY SPECIES"
# where the annex begins; the three bare "ANNEX I"/"ANNEX II"/"ANNEX VI" lines
# further down open the Final Act of the conference, not the Convention. The
# contents are cut before this runs (`treaty_body`), so a label reaching here
# is the body's own.
RE_ANNEX = re.compile(r"^(?:ANNEX|APPENDIX)\s+([IVXLC]+|\d+)\b[^a-z]*$")
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
    number = re.sub(r"\s+", "", number).rstrip(".")
    # The UNCLOS PDF sets "Article 3l" and "Article 4l" with a lowercase L for
    # the digit 1, which left Annex VI articles 31 and 41 with no anchor and
    # their text under articles 30 and 40. Repaired only where the run already
    # carries a digit, so a number made of digits alone is never rewritten.
    if any(character.isdigit() for character in number):
        number = number.replace("l", "1")
    return "A" + number.upper()


def treaty_body(lines):
    """The treaty's own lines, clear of the contents block a published PDF opens
    with -- and of the next publication bound into the same file.

    A contents block is a run of dotted leaders. Dropping those lines one by one
    is not enough, because the block sets each entry's "Article N." and "ANNEX
    I." on lines of their own with no leader: honouring them counted 885
    articles against the Convention's 320 and filed 444 of its provisions under
    Annex IX. Cutting the whole run leaves the body, which carries no leader of
    its own in either PDF.

    A second block opens a second publication, so the treaty ends there: the
    UNCLOS PDF prints the Final Act of the Third UN Conference after the
    Convention, whose Annexes I, II and VI would otherwise claim the anchors of
    the Convention's own.

    A run of fewer than `MIN_LEADER_LINES` is not a contents block. It is one
    line of the treaty that happens to carry dots, and cutting at it would drop
    every article above it.
    """
    blocks = []
    for index, line in enumerate(lines):
        if RE_LEADER.search(line):
            if blocks and index - blocks[-1][-1] <= LEADER_GAP:
                blocks[-1].append(index)
            else:
                blocks.append([index])
    blocks = [block for block in blocks if len(block) >= MIN_LEADER_LINES]
    if not blocks:
        return lines
    return lines[blocks[0][-1] + 1:blocks[1][0] if len(blocks) > 1 else len(lines)]


def provisions(lines):
    """A treaty's line stream -> [(fragment, heading, [paragraph])].

    The preamble comes first with no fragment, then one entry per article. A
    part or chapter heading is dropped rather than kept as a provision: it names
    a group of articles and nothing cites it, where every article is a citation
    target.

    An annex heading both scopes the articles under it and opens a provision of
    its own. It has to: an annex may carry text and no article at all --
    UNCLOS's Annex I is a list of 17 species, which otherwise printed under
    article 320, "Authentic texts" -- and it is the only line that tells a
    reader where one annex ends and the next begins.
    """
    out, current, scope = [], None, ""
    preamble, annexes = [], set()
    for line in treaty_body(lines):
        match = RE_ARTICLE.match(line)
        if match:
            number = normalize_space(match.group(1))
            current = (scope + fragment(number), line, [])
            out.append(current)
            continue
        match = RE_ANNEX.match(line)
        if match:
            # an annex restarts the numbering, so its articles are scoped by it
            scope = "Annex%s_" % match.group(1).upper()
            current = (scope.rstrip("_"), line, [])
            annexes.add(current[0])
            out.append(current)
            continue
        if RE_HEADING.match(line):
            # a part, chapter or section does not restart it, so the scope holds
            continue
        (current[2] if current else preamble).append(line)
    # An article heading with no text under it is a contents entry that reached
    # here, not an article: it would take the plain "#A5" anchor and leave the
    # real article 5 with a `unique_id` suffix. An annex heading is empty on
    # purpose -- three of UNCLOS's nine print their title and go straight to
    # their first article -- so it is kept.
    out = [entry for entry in out if entry[2] or entry[0] in annexes]
    # a treaty with no article at all means the reader found the wrong block
    if not out:
        raise ValueError("no article heading in %d lines" % len(lines))
    if len(" ".join(preamble)) >= PREAMBLE_MIN:
        out.insert(0, (None, "Preamble", preamble))
    return out


def article_count(provisions):
    """How many of `provisions` are articles. The preamble and an annex heading
    are provisions too, so `len(provisions)` counts something else -- and the
    curated count this is checked against is the treaty's own article count.

    An article is told from an annex heading ("AnnexIV") by `model.RE_ORDINAL`,
    the same grammar the artifact reads a provision's `ordinal` with -- a second
    copy here would disagree the first time a treaty numbers an "Article 5 bis".
    """
    return sum(1 for fragment, _, _ in provisions
               if fragment and RE_ORDINAL.match(fragment.rsplit("_", 1)[-1]))
