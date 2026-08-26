"""ICJ stored records (index row + Reports PDF) to artifacts.

The Court publishes every decision as its page range from the printed *I.C.J.
Reports*, which means the PDF opens with the Reports' own front matter -- a
bilingual cover, the official-citation page, and (since 2012) a table of
contents -- before the decision itself begins. The decision starts where the
Court sets its own letterhead: ``INTERNATIONAL COURT OF JUSTICE`` over a
``YEAR <yyyy>`` line. Everything above that is the publisher's furniture, and
half of it is French.

From there the shape is one the citation engine can use: a headnote, the bench,
the parties, then the numbered paragraphs the Court itself cites by ("… as the
Court held in paragraph 87"), and finally the separate and dissenting opinions
the Reports bind in with the judgment.

Decisions before ~2002 are scans with an OCR text layer; `ocr.repair` fixes its
systematic character confusions and the count it returns is what marks the
artifact as OCR-derived.
"""

import re

from ..lib import compress
from ..lib.lagrum import yield_overlaps
from ..lib.pdftext import (
    join_across_pages,
    pages_with_ocr,
    paragraph_texts,
    strip_page_furniture,
)
from ..lib.util import normalize_space
from . import ocr, reports, treaties
from .download import body_path, record_path
from .model import Block, Decision

# Where the decision begins and the Reports' front matter ends. All three
# patterns are matched against the page with *every* space removed, because
# they are read off a scan and the OCR breaks words at arbitrary points: the
# 1986 judgment gives "1986 Y EAR 1986", the 1948 one "COURT O F JUSTICE".
#
# The Court's *dateline* is the seam, not its letterhead. The letterhead words
# themselves do not survive OCR -- the 1949 Corfu Channel judgment prints
# "INTERNATIONAL COUI2T OF JUSTICE" and the 1951 Fisheries one
# "INTEIINATIONAL COURT OF JUSTICIC" -- while "YEAR 1949" comes through intact.
# Measured over the corpus the dateline finds the seam in 252 of 255 documents
# and never later than page 12.
RE_DATELINE = re.compile(r"YEAR\d{4}")
# The fallback for the three whose dateline the OCR lost, and the reason it
# needs a second half: the Reports' *cover* prints the letterhead too, in the
# English half of its bilingual title page, so keying on the letterhead alone
# starts the body at page 1 and keeps the French. The cover is the page that
# also carries the series masthead, so a letterhead without one is the
# decision's own first page.
LETTERHEAD = "INTERNATIONALCOURTOFJUSTICE"
MASTHEAD = ("REPORTSOFJUDGMENTS", "RECUEILDES")
# a paragraph the Court numbers -- the unit it cites itself by
RE_NUMBERED = re.compile(r"^(\d{1,3})\.\s+(.*)$", re.DOTALL)
# a section head the Court enumerates: "I. GEOGRAPHY", "A. Uti possidetis
# juris", "1. The 1928 Treaty". Anchored and followed by a capital, so a
# sentence opening with a date ("2012. The Court then …") does not match.
RE_ENUMERATED_HEAD = re.compile(
    r"^(?:(?=[IVXL])X{0,3}(?:IX|IV|V?I{0,3})|[A-Z]|\d{1,2})\.\s+[A-Z]")
# the headings the Reports set without an enumerator. An opinion heading is
# always in caps ("SEPARATE OPINION OF JUDGE SIMMA"), and matching the word
# alone would head every paragraph that mentions "the opinion of the Court".
HEADING_EXACT = {"JUDGMENT", "ORDER", "ADVISORY OPINION", "OPERATIVE CLAUSE",
                 "TABLE OF CONTENTS", "ANNEX", "APPENDIX"}
RE_OPINION_HEAD = re.compile(
    r"^(?:JOINT\s+)?(?:PARTLY\s+)?(?:SEPARATE|DISSENTING|DECLARATION)\b.*\bJUDGE",
    re.I)
# A heading is a title, not a paragraph. The longest real ICJ heading measured
# over the corpus is a joint opinion's list of judges; 200 characters clears it
# and rejects the run of prose an unsplit page leaves behind.
HEADING_MAX = 200
# Debris the Court's own typesetting leaves in the published text, cut out of
# the block rather than used to reject it. Both land *inside* a sentence, so a
# block-level guard throws away real reasoning: dropping the one block carrying
# the imposition stamp cost the 2015 Croatia v. Serbia judgment paragraphs 75
# to 524, because the paragraph sequence never resumed after the hole.
#   * the imposition stamp the printer sets inside the text block
#     ("6 CIJ1034.indb 3 7/01/14 12:43")
#   * an unfilled placeholder from the running-head template, published as-is
#     ("… with effect from 6 February 2024, running head content of Judge Dire
#     Tladi …")
# The stamp is matched to its exact shape (page, file, page, date, time) rather
# than as "CIJ…indb and the digits after it": a trailing run of digits and
# punctuation swallows the next paragraph number too, which loses the very
# anchor this cleaning exists to protect.
RE_TYPESETTING_DEBRIS = re.compile(
    r"\s*(?:\d*\s*\bCIJ\d+\.indb\s+\d+\s+\d+/\d+/\d+\s+\d+:\d+"
    r"|\brunning head content\b)\s*", re.I)
# a block that is only a page number, left behind where the header stripper's
# margin test could not reach it
RE_BARE_NUMBER = re.compile(r"^\d{1,4}$")
# The running head the Reports stamp on every page: a page number, the case
# name, and the decision kind in brackets -- "63 MILITARY AND PARAMILITARY
# ACTIVITIES (JUDGMENT)", "625 territorial and maritime dispute (judgment)",
# "5 application of the genocide convention (order 26 I 24)".
#
# Dropped by its shape, the way `icc/parse.py` drops its court-record stamp,
# because `strip_page_furniture` cannot see it: the Reports alternate the head
# between recto and verso and the OCR perturbs each copy, so the repeated-line
# test never fires. 66 documents kept 742 of these, and they land *between* the
# two halves of a sentence a page break split -- in the Nicaragua judgment the
# head sits as a rubrik between paragraphs 110 and the rest of 110's sentence.
RE_RUNNING_HEAD = re.compile(
    r"^\d{0,4}\s*[^\s(].{2,90}?\((?:judgment|order|advisory\s+opinion|arr[eê]t"
    r"|ordonnance|avis\s+consultatif)\b[^)]*\)\s*\d{0,4}$", re.I)
# Anything that *could* open a numbered paragraph: a one-to-three-digit number,
# a full stop, and a capital. Deliberately generous -- it also matches "Article
# 5. The Parties" and an ICTY paragraph the Court quotes. Which of these are the
# Court's own numbering is settled by `_paragraph_chain`, not here. The
# lookbehind only rejects a decimal inside a longer number ("15.3.").
RE_PARA_START = re.compile(r"(?<![\d.])(\d{1,3})\.\s+(?=[A-Z“\"(])")
# How far the Court's numbering may jump and still be one chain. Four, so the
# chain steps over a short run the OCR lost: the Nicaragua judgment renders
# paragraphs 111 and 112 as "1 1 1." and "1 12.", a step of three from 110, and
# at two the chain broke there and shipped paragraphs 1-112 unanchored. A wide
# gap is safe only because `paragraph_chain` also requires the chain to open at
# the Court's own first paragraph -- see MAX_FIRST_NUMBER.
MAX_NUMBER_GAP = 4
# The Court numbers from 1, so a chain that opens at 1 (or within an OCR slip
# of it) is its numbering. A chain that opens later has to earn it by *reaching
# back at least as far as it opens*: a run that starts at 210 and is 30 long has
# not accounted for the 209 numbers before it, so it is page numbers, not
# paragraphs. Measured over the corpus this separates cleanly -- it admits the
# Qatar v. Bahrain merits chain (159 long, opens at 14), LaGrand (121 at 7) and
# Avena (73 at 71), and rejects the Gulf of Maine page-number run (30 at 210)
# and two footnote fragments (7 at 162, 5 at 151).
#
# Both guards exist because "longest chain wins" had no floor at all: a single
# stray number is a chain of length one, so the pre-1960 Reports, which number
# no paragraphs, minted anchors out of page numbers -- the 1950 Asylum judgment
# shipped #P812, #P814 and #P816 as permanent citation targets. An absolute
# opening veto is not the answer either: it cost LaGrand all 121 of its anchors
# because paragraphs 1-6 are swallowed by the appearance list.
MAX_FIRST_NUMBER = 3
MIN_CHAIN = 3


def _is_heading(text):
    """A section heading in an ICJ decision."""
    if len(text) > HEADING_MAX:
        return False
    if text.upper() in HEADING_EXACT or RE_OPINION_HEAD.match(text):
        return True
    if RE_NUMBERED.match(text):
        return False
    return bool(RE_ENUMERATED_HEAD.match(text)
                or (text == text.upper() and len(text) >= 8
                    and any(char.isalpha() for char in text)))


def _heading_level(text):
    """The depth the Court's own enumerator sets: roman numerals head a part,
    letters a section under it, digits a subsection under that."""
    marker = text.split(".", 1)[0]
    if marker.isdigit():
        return 3
    if len(marker) == 1 and marker.isalpha() and marker not in ("I", "V", "X"):
        return 2
    return 1


def body_pages(pages, basefile=""):
    """The decision's own pages: everything from the Court's letterhead on.

    The Court opens each decision with its dateline ("YEAR 1986"), which is
    what this looks for; the letterhead is the fallback for the three documents
    whose dateline the OCR lost.

    Raises when neither is found, rather than keeping the whole document. That
    old behaviour was not the visible defect its comment claimed to be: it
    published the Reports' French cover, the official-citation page and the
    table of contents as body text, silently, on 53 of 255 documents -- the
    Wall opinion among them (rule:fail-fast).
    """
    flat = ["".join("".join(line.text.split()) for line in lines).upper()
            for _pageno, lines in pages]
    for index, text in enumerate(flat):
        if RE_DATELINE.search(text):
            return pages[index:]
    for index, text in enumerate(flat):
        if LETTERHEAD in text and not any(mark in text for mark in MASTHEAD):
            return pages[index:]
    raise ValueError("%s: no page carries the Court's dateline or its "
                     "letterhead, so the Reports' front matter cannot be cut"
                     % (basefile or "icj"))


def paragraph_chain(texts):
    """Which candidate numbers in `texts` are the Court's own paragraph
    numbering: ``{(block index, offset)}``.

    The Reports set a numbered paragraph flush with the one above it, so
    `page_paragraphs` sees no vertical gap and hands back a whole run of
    reasoning as a single block -- 4,900 characters holding paragraphs 1 to 5 of
    the 2024 Gaza order. Every one of those numbers is a citation anchor ("as
    the Court held in paragraph 87"), so the runs have to be cut, and the cut
    has to tell the Court's "5." from the 5 in "Article 5. The Parties" and from
    an ICTY paragraph the Court block-quotes.

    What tells them apart is that the Court's numbering is *consecutive over the
    whole decision and opens at its first paragraph*. This takes every candidate
    in reading order, builds every chain that counts up in steps of at most
    `MAX_NUMBER_GAP`, and keeps the longest one that opens at or below
    `MAX_FIRST_NUMBER`. Both halves of that rule are load-bearing:

      * without the length and reach-back guards, a lone stray number is a
        chain of one and wins by default. The pre-1960 Reports number no
        paragraphs at all, and the 1950 Asylum judgment shipped
        #P812/#P814/#P816 minted from an annex's page numbers.
      * without a chain that reaches back towards paragraph 1, a hole splits
        the decision and the longer half wins. The Nicaragua judgment's OCR
        mangles 111 and 112, so its chain started at 113 and its first 112
        paragraphs -- 1,930 across the corpus -- shipped with no anchor at all.
        "Reaches back towards" and not "opens at 1": requiring the latter cost
        LaGrand every one of its 121 anchors, because the appearance list
        swallows its paragraphs 1-6 and its chain opens at 7.

    Returning nothing is a real answer: a decision the Court did not number, or
    one whose scan lost the numbering, has no anchors to give, and inventing
    three is worse than publishing none.

    A residue survives both guards and is left in on purpose. 24 documents --
    22 of them pre-1965 Reports that number nothing -- mint 97 anchors from
    runs that do open at 1: quoted articles of the ILO Administrative Tribunal
    Statute, a party's numbered submissions, a footnote's paragraph reference.
    A density test would remove them (4 anchors over 358 blocks is not
    numbering) and would also remove the partial recovery of a real judgment
    whose scan lost most of its numbers, which is the more valuable of the two.
    These anchors are wrong about what they name but land on text the document
    itself numbers, where #P812 landed on a page number. Bounded and known
    beats a third guard that overreaches.

    A separate or dissenting opinion restarts at 1 and so forms its own chain.
    It loses on length to the Court's own reasoning -- the text a citation to
    "paragraph 87" means.
    """
    candidates = [(index, match.start(), int(match.group(1)))
                  for index, text in enumerate(texts)
                  for match in RE_PARA_START.finditer(text)]
    # best[n] = (length of the chain ending at value n, index of its last
    # candidate); previous[i] = the candidate before i in that chain
    # best[n] = (chain length ending at value n, that chain's last candidate,
    # the value it opened at); previous[i] = the candidate before i
    best, previous = {}, {}
    for position, (_index, _offset, number) in enumerate(candidates):
        length, back, first = 1, None, number
        for step in range(1, MAX_NUMBER_GAP + 1):
            prior = best.get(number - step)
            if prior and prior[0] + 1 > length:
                length, back, first = prior[0] + 1, prior[1], prior[2]
        previous[position] = back
        if number not in best or length > best[number][0]:
            best[number] = (length, position, first)
    usable = [entry for entry in best.values()
              if entry[0] >= MIN_CHAIN
              and entry[2] <= max(MAX_FIRST_NUMBER, entry[0])]
    if not usable:
        return set()
    # ties on length go to the chain that ends earlier in the document
    chain, position = set(), max(usable, key=lambda v: (v[0], -v[1]))[1]
    while position is not None:
        index, offset, _number = candidates[position]
        chain.add((index, offset))
        position = previous[position]
    return chain


def _split_block(text, offsets):
    """One block cut at `offsets`, as (piece, opens-a-numbered-paragraph)."""
    pieces, start = [], 0
    for offset in sorted(offsets):
        if offset > start:
            pieces.append((text[start:offset].strip(), start in offsets))
        start = offset
    pieces.append((text[start:].strip(), start in offsets))
    return [piece for piece in pieces if piece[0]]


def clean(text):
    """One reflowed block with the publisher's debris removed, or '' for a block
    that is nothing but a page number or the Reports' running head."""
    text = normalize_space(RE_TYPESETTING_DEBRIS.sub(" ", text))
    if RE_BARE_NUMBER.match(text):
        return ""
    # A block the Court numbered is never a running head, whatever its shape.
    # Without this the head pattern also matches a short numbered paragraph that
    # *ends* in a case citation -- "5. The Court recalls its Order of 3 March
    # 2014 (Order of 3 March 2014)" -- which would delete a citation anchor and
    # break the paragraph chain at that point. No block in the corpus is in that
    # shape today; the guard costs one line and closes the exposure.
    return "" if (RE_RUNNING_HEAD.match(text)
                  and not RE_NUMBERED.match(text)) else text


def _classify(raw_texts):
    """Reflowed paragraph texts -> classified blocks.

    The cleaning runs before `paragraph_chain`, not after: the chain records
    offsets into these strings, and removing a character afterwards would move
    every cut that follows it."""
    texts = [text for text in map(clean, raw_texts) if text]
    chain = paragraph_chain(texts)
    blocks = []
    for index, text in enumerate(texts):
        for piece, numbers in _split_block(
                text, {offset for block, offset in chain if block == index}):
            numbered = RE_NUMBERED.match(piece) if numbers else None
            if numbered:
                blocks.append(Block("stycke", numbered.group(2),
                                    number=numbered.group(1)))
            elif _is_heading(piece):
                blocks.append(Block("rubrik", piece, level=_heading_level(piece)))
            else:
                blocks.append(Block("stycke", piece))
    return blocks


def _blocks(path, basefile):
    """The decision PDF's paragraphs, classified, with the scan's OCR
    confusions repaired.

    Through `pages_with_ocr`, not `pdf_pages`: the pre-2002 Reports scans carry
    an *invisible* OCR text layer, which poppler omits unless asked for it --
    the same shape the ICC's court records have.

    The running header goes before the page join, not after: the Reports stamp
    "625 territorial and maritime dispute (judgment)" at the top of every page,
    so leaving it in place puts the case name between the two halves of every
    sentence a page break splits, and nothing ever rejoins.
    """
    pages = strip_page_furniture(
        list(pages_with_ocr(str(path), ("icj", basefile), lang="eng")))
    texts = join_across_pages(paragraph_texts(body_pages(pages, basefile)))
    known = ocr.vocabulary()
    repaired, repairs = [], 0
    for text in texts:
        fixed, count = ocr.repair(text, known)
        repaired.append(fixed)
        repairs += count
    return _classify(repaired), repairs


def parse(basefile, root):
    record = compress.read_json(record_path(root, basefile))
    body = body_path(root, basefile)
    # `download.resolve` writes the PDF *before* the record, so a record without
    # a body means the store was edited by hand or is corrupt -- not a shape the
    # Court can serve. Unlike `icc`, where an unresolved Legal Tools record is a
    # real metadata-only state with its own banner, `icj` has nothing to show
    # for it: the old branch published an empty page, no error, and a fresh
    # manifest entry (rule:fail-fast).
    if not compress.exists(body):
        raise ValueError("%s: the record is stored without its PDF -- "
                         "re-download it with `lagen icj download --only %s`"
                         % (basefile, basefile))
    blocks, repairs = _blocks(body, basefile)
    return Decision(
        basefile=basefile,
        case=record["case"],
        case_name=record["case_name"],
        kind=record["kind"],
        title=record["title"],
        date=record["date"],
        procedure=record["procedure"],
        ocr_repairs=repairs,
        pdf_url=record["url"],
        references=treaties.references(
            " ".join(block.text for block in blocks)),
        body=blocks,
        reports_citation=reports.own_citation(body, basefile),
    ).to_artifact(refs_for=lambda text: _refs(text, basefile, root))


def _refs(text, basefile, root):
    """One block's inline citation spans: the Reports self-citations, plus
    every treaty span not overlapping one. The two grammars cannot overlap
    today, but interleave requires disjoint spans and the treaty side rests
    on curated name data -- so the merge filters like every other two-list
    caller, with the Reports form (the Court's own identity) winning."""
    rep = reports.refs(text, basefile, root)
    return sorted(rep + yield_overlaps(treaties.refs(text), rep),
                  key=lambda ref: ref.start)
