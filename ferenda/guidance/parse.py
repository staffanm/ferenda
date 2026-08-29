"""A harvested edpb record + its PDF -> :class:`Vagledning` -> JSON artifact.

Both routes deliver the same kind of document: an EU institutional report set
in one column, structure marked by **size alone** -- the EDPB sets no bold
anywhere in its guidelines (body 17, sections 24, title 27), which is the
`by_size` reading `lib.pdftext.classify_letterhead` already offers and
Finansinspektionens ställningstaganden already need. So the block layer is the
shared one, configured by the two patterns it takes: the cover/front-matter
lines to drop, and the footer that has to be removed in place.

What is EDPB-specific, and lives here, is the **numbered punkt**. The EDPB
numbers every substantive paragraph and sets the number in a column of its own,
which is exactly the case the paragraph-gap heuristic cannot see: paragraph 17
of Riktlinjer 05/2020 sits half a line below paragraph 16 and arrives glued to
the end of it, losing both an anchor and the boundary a citation scan needs. The
numbers are therefore read first, as a *running sequence* -- a line opening
"N. " counts only when N is the number the document is due next, so a year, a
list item or an article number opening a line cannot start a paragraph -- and
handed to `page_paragraphs` as forced breaks, the same mechanism DV's bitmap
paragraph numbers use. Each numbered paragraph then anchors on its own number,
so a decision citing "punkt 27 i riktlinjer 05/2020" lands on the paragraph.

Two of the documents print the number with **no period at all** -- riktlinjer
02/2025 and 04/2020 hang a bare "1" in the margin at x=66 and set the prose
beside it at the body's 108 -- and there the text says nothing: "1 The concept
commonly …" reads like any sentence that opens with a quantity. So the number is
recognised by its *column*, and by the column the document itself demonstrates
(`punkt_margin`), never by a bare number wherever one turns up. Read as text it
was 0 punkter in 02/2025 and its punkter 1-3 arrived as one block.

A paragraph continued across a page break arrives as a block of its own with no
number of its own, and is joined back onto the numbered paragraph it continues
-- otherwise the sentence is split mid-clause, which loses both the reference
that straddles the break and the fragment's own place in the numbering.

The one field read out of a document rather than off its index is the **WP29
title and adoption date**, and only because the index is demonstrably wrong
about them: the EDPB page that endorses WP250 is titled "Dataskyddsombud",
which is WP243's subject (see `edpb_data.WP29`). Their Swedish covers state both,
in a layout fixed across all seven, and a document naming itself beats an index
naming it wrongly -- the same departure `rs` makes for Försäkringskassans
serienummer.
"""

import functools
import re
from collections import Counter

from bs4 import BeautifulSoup

from ..lib import compress, util
from ..lib.datasets import NAMEDACTS
from ..lib.formex import (
    TABLE,
    _text,
    append_annex,
    collect_notes,
    formex_roots,
    parse_act,
    walk_content,
)
from ..lib.formex import Block as FormexBlock
from ..lib.lagrum import (
    EMDRATTSFALL,
    EULAGSTIFTNING,
    EURATTSFALL,
    VAGLEDNING,
    LagrumParser,
    load_namedacts,
)
from ..lib.pdftext import (
    classify_letterhead,
    letterhead_footnotes,
    page_paragraphs,
    pdf_pages,
    strip_page_furniture,
)
from ..lib.util import (
    TITLE_ECHO_MIN,
    drop_leading_title_echo,
    match_fold,
    normalize_space,
    record_path,
    shouted,
)
from . import (
    acer_download,
    berec_download,
    edps_download,
    eiopa_download,
    euipo_download,
    eurlex_download,
)
from .edpb_data import WP29_BY_SLUG
from .edpb_download import pdf_path
from .issuers import (
    ACER,
    BEREC,
    BY_KOD,
    EASA,
    EBA,
    EDPB,
    EDPS,
    EIOPA,
    ENISA,
    ESMA,
    EUIPO,
)
from .model import Block, Fotnot, Vagledning

# what an EDPB guideline actually cites: EU legislation (the förordning it
# interprets, by article), the EU courts, and the EDPB's and artikel
# 29-gruppens own guidance. It cites no Swedish statute at all, so the SFS
# machinery is not requested -- a smaller grammar and no false "3 §" matches.
GUIDANCE_PARSE_TYPES = [EULAGSTIFTNING, EURATTSFALL, VAGLEDNING, EMDRATTSFALL]

# a numbered punkt opening a line: "1. ", "27. ". Matched against the line, not
# the paragraph, because the number is set in its own column and the sequence
# check below is what makes a bare number safe to trust.
RE_PUNKT = re.compile(r"^(\d{1,4})\.\s+\S")
# the same punkt with the period left off: "1 The concept commonly ...". Two
# documents number this way (riktlinjer 02/2025 and 04/2020), and nothing in the
# text says the leading number is a paragraph number rather than a quantity, so
# this surface is trusted only where the *geometry* puts the number in the
# document's own number column (`punkt_margin`). Three digits at most and never
# followed by another -- a punkt runs to 137 here, while "12 000 kronor" opens a
# paragraph with a number that is neither.
RE_BARE_PUNKT = re.compile(r"^(\d{1,3})\s+(?!\d)\S")
# a line fragment that is nothing but such a number: the shape `punkt_margin`
# reads the document's number column off
RE_NUMBER_ONLY = re.compile(r"^\d{1,3}$")

# how much of a document its numbering must cover before the numbers are read
# as punkter at all -- see `join_continuations` for what the two populations
# look like and what relying on the premise where it does not hold costs.
PUNKT_COVERAGE_MIN = 0.2

# the cover and front matter, which the record already carries as fields: the
# document's own version line and version history, its adoption dates, and the
# EDPB's translation disclaimer. Dropped rather than published, so the body
# starts at the body.
RE_FRONT_MATTER = re.compile(
    r"^(?:Version(?:shistorik)?\b.*|(?:vo\.[\d.]+\s*)+.*"
    # the adoption line, in every inflection the translations use (antagen /
    # antaget / antagna), and the running footer that repeats the same word
    # with the page number -- sometimes in a paragraph of its own, sometimes
    # with the EDPB's "efter offentligt samråd" qualifier
    r"|(?:Senast\s+(?:reviderade|granskade)\s+och\s+)?[Aa]ntag(?:en|et|na)"
    r"(?:\s+den\s+.*|\s*[-–]\s*efter\s+offentligt\s+samråd\s*\d*|\s*\d*)"
    r"|Adopted(?:\s+on\s+.*|\s*\d*)|Version\s+history\b.*|Utkast\s*\d*"
    r"|Translations?\s+proofread\s+by.*"
    # "has not been" in some, "has not yet been" in others
    r"|This\s+(?:language\s+version|translation)\s+has\s+not\s+(?:yet\s+)?"
    r"been\s+proofread.*"
    # the WP29 language mark ("17/SV") and the WP number, on one line or two
    r"|\d{1,2}/[A-Z]{2}"
    r"|(?:\d{1,2}/[A-Z]{2}\s+)?WP\s*\d{2,3}\s*(?:rev\.?\s*\d+)?"
    # EUIPO sets a list bullet as a paragraph of its own, above the item it
    # belongs to. The item's text follows as the next paragraph, so dropping
    # the glyph loses the marker and no words.
    r"|[\u2022\u00b7\u25e6\u2013\u2014-]"
    # the two lines EUIPO sets across the top of every volume's cover, in the
    # two languages this source takes. Whole paragraphs of their own, which is
    # what makes them safe to drop: the volume names itself in running prose
    # too, and there the sentence continues past the name.
    r"|(?:GUIDELINES FOR EXAMINATION|RIKTLINJER FÖR (?:PRÖVNING|GRANSKNING))"
    r"\b.*"
    r"|(?:EUROPEAN UNION INTELLECTUAL PROPERTY OFFICE"
    r"|EUROPEISKA UNIONENS IMMATERIALRÄTTSMYNDIGHET)\s*\(EUIPO\)"
    # the classification mark the ECB sets across the top of every page of a
    # yttrande, as a paragraph of its own. The first page carries the language
    # code with it and the rest do not, so `strip_page_furniture` -- which
    # drops what *recurs* in the margin -- leaves the first page's line
    # standing in each of the 1 168 PDF yttranden.
    r"|(?:EN|SV)\s+ECB[-\s]PUBLIC|ECB[-\s]PUBLIC"
    r")$", re.I)

# the running footer. The EDPB sets "Antagna <n>" ("Adopted <n>") at the foot of
# every page; the working party set its own name and a page number. Removed in
# place, since a body line sharing the footer's baseline arrives glued to it.
RE_MASTHEAD = re.compile(
    r"\s*(?:[Aa]ntagna|Adopted)\s+\d+\s*$"
    r"|\s*ARTIKEL\s+29-ARBETSGRUPPEN[^\n]*"
    r"|\s*ARTICLE\s+29\s+DATA\s+PROTECTION\s+WORKING\s+PARTY[^\n]*"
    # the same masthead in title case, which one document sets ("ARTICLE 29
    # Data Protection Working Party"). Case-insensitive but anchored to a line
    # of its own, and deliberately not extended to the Swedish name: the group
    # names *itself* in running prose hundreds of times across this corpus
    # ("... anser artikel 29-arbetsgruppen att ..."), and this pattern removes
    # to the end of the line, so a case-insensitive unanchored match on that
    # name would delete body text wholesale.
    r"|^\s*(?i:ARTICLE\s+29\s+DATA\s+PROTECTION\s+WORKING\s+PARTY)\s*$"
    # BEREC sets its own document number as the running head of every page, and
    # a body line sharing that baseline arrives glued to it -- "BoR (26) 70 1.
    # Introduction" for a heading, and a paragraph split across a page break
    # resuming as "BoR (26) 70 communications networks. Likewise ...". Anchored
    # to the **start** of the paragraph, which is the whole safety of it: BEREC
    # cites its other documents by the same number in running prose, and a
    # loose match would delete those citations. Measured over the 43-document
    # corpus, this removes only the running head and no prose reference.
    r"|^\s*BoR\s*\(\s*\d{2}\s*\)\s*\d{1,4}\b"
    # EUIPO sets a two-line footer on every page of every volume: the volume's
    # own name with the page number embedded in it, and the approval/version/
    # date line below. Both are anchored to a line of their own -- the volume
    # names itself in running prose ("Guidelines for Examination in the
    # Office") and an unanchored match would delete those sentences.
    r"|^\s*(?:Guidelines for Examination in the Office"
    r"|Riktlinjer för (?:prövning|granskning) vid myndigheten)[^\n]*"
    r"|^\s*FINAL\s+VERSION\s+[\d.]+\s+\d{2}/\d{2}/\d{4}\s*$")

# the WP29 cover. Its *order* is fixed across all seven documents -- the working
# party's name, a "17/SV" language mark, the WP number, the title, then the
# adoption dates, the last of which is the revision the EDPB endorsed -- but its
# line breaking is not: three of the seven set the language mark and the number
# on one line ("16/SV WP 242 rev.01") and two run the adoption dates together,
# so every part is *searched* for rather than matched against a whole line.
RE_WP_NUMBER = re.compile(r"\bWP\s*(\d{2,3})\s*(?:rev\.?\s*\d+)?\s*$", re.I)
# the adoption line, in every inflection the translations use. A riktlinje is
# "antagna" (plural, the guidelines) and a working document "antaget"/"antagen"
# (singular, the document), and the revision line agrees with it -- "Senast
# granskade och antagna" beside "Senast reviderat och antaget".
RE_WP_ADOPTED = re.compile(
    r"(?:Senast\s+(?:reviderad|granskad)(?:e|t)?\s+och\s+)?"
    r"[Aa]ntag(?:na|en|et)\s+den\s+(\d{1,2})\s+([a-zåäö]+)\s+(\d{4})", re.I)
RE_WP_ADOPTED_EN = re.compile(
    r"(?:Last\s+[Rr]evised\s+and\s+)?Adopted\s+on\s+"
    r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})")
# the working party naming *itself* between the number and the title. Stripped
# off the front of the cover region rather than matched as a whole line: most
# set it on a line of its own ("Artikel 29-arbetsgruppen"), and WP259 runs it
# straight into the title ("Artikel 29-gruppen Riktlinjer om samtycke …"). No
# qualifier follows it there -- the "FÖR UPPGIFTSSKYDD" kind belongs to the
# masthead above the region -- so none is allowed, which is what keeps the
# strip from eating the title behind it.
RE_WP29_NAME = re.compile(r"^Artikel\s+29[-‑](?:arbets)?gruppen\s*", re.I)
# Swedish and English month names, lower-cased. Four spellings coincide
# (april, september, november, december) and are written once.
MONTHS = util.MONTHS | util.MONTHS_EN


@functools.cache
def _refparser(lang):
    """The citation parser for a document in `lang`.

    Two of them, because the corpus is two-language: the EDPB publishes 48 of
    these in Swedish and three in English only, and the same act citation reads
    "artikel 6.1 f i förordning (EU) 2016/679" in one and "Article 6(1)(f) of
    Regulation (EU) 2016/679" in the other -- which is the language switch the
    engine already carries for pre-accession EU case law. Parsing an English
    guideline with the Swedish surface found one reference in the whole
    document.

    `named_acts` is not optional here the way it is for most verticals: a
    riktlinje is *about* one act and names it in prose on almost every line
    ("artikel 6.1 a i allmänna dataskyddsförordningen"). Without the named-act
    surface those bare articles bind to whatever act was last named by number --
    which in these documents is the repealed direktiv 95/46/EG, cited in every
    historical aside -- and the whole document links to the wrong act. The named
    forms are Swedish, so the English parser is built without them."""
    return LagrumParser({}, basefile="guidance", parse_types=GUIDANCE_PARSE_TYPES,
                        named_acts=load_namedacts(NAMEDACTS) if lang == "swe"
                        else None, lang=lang)


def _fresh_parser(sprak):
    """The shared parser with document-lifetime state reset (so one document's
    learned act names do not bleed into the next)."""
    parser = _refparser("swe" if sprak == "sv" else "eng")
    parser.reset()
    return parser


# --------------------------------------------------------------------------
# the numbered punkt
# --------------------------------------------------------------------------

def body_column(pages):
    """The x the document sets its body text at: the commonest line start.

    Read over the whole document rather than per page, because a page whose text
    is a cover, a version-history table or a table annex has no body column of
    its own -- and the commonest start there is the table's, which puts the
    running footer (riktlinjer 02/2025 sets "5 | Adopted" at the body margin)
    out in the margin where the punkt numbers are. `None` where the source
    carries no run geometry at all (the OCR/legacy routes)."""
    starts = Counter(line.runs[0].left for _pageno, lines in pages
                     for line in lines if line.runs)
    return starts.most_common(1)[0][0] if starts else None


def punkt_margin(pages):
    """The x a document hangs its bare punkt numbers at, or None where it prints
    none.

    Learned from the lines that set the number *apart* -- a fragment of digits
    alone, ending left of the body column, with the prose beginning at that
    column ("1" at x=66, "The concept commonly …" at 108) -- because those are
    the only ones whose geometry says so unambiguously. poppler emits a wider
    two-digit number and the prose beside it as one fragment ("10  Finally, the
    use of …", starting at 66), and such a line cannot be told from a table row
    whose first column holds a number: riktlinjer 02/2022 sets four of those in
    its annex ("2" at 59, "Artikel 60.2 – Den" at 91, "Vem" at 291). Trusting
    only the column the document itself demonstrates keeps them out -- measured
    over the corpus, a column is learned for exactly the two documents that
    number this way, and the other 58 read the same punkter and the same blocks
    as they did before."""
    column = body_column(pages)
    if column is None:
        return None
    lefts = Counter(
        line.runs[0].left for _pageno, lines in pages for line in lines
        if len(line.runs) > 1 and line.runs[0].right < column
        and line.runs[1].left == column
        and RE_NUMBER_ONLY.match(line.runs[0].text.strip()))
    return lefts.most_common(1)[0][0] if lefts else None


def line_punkt(line, margin):
    """The punkt number `line` opens with, or None -- printed with its period, or
    set bare in the number column `margin`."""
    in_column = margin is not None and line.runs and line.runs[0].left == margin
    match = RE_PUNKT.match(line.text) or (RE_BARE_PUNKT.match(line.text)
                                          if in_column else None)
    return match.group(1) if match else None


def numbered_breaks(pages, margin):
    """``{pageno: {top, …}}`` -- the lines that open a numbered punkt.

    The number is trusted only where it is the one the document is *due*: the
    sequence starts at 1 and advances by one, so "2016." opening a line, an
    article number, or a numbered list inside a paragraph cannot pass for a
    paragraph number. A document that numbers nothing yields no breaks at all
    and reads as the plain prose it is (the WP29 vägledningar).

    `margin` is the document's own number column (`punkt_margin`) and `None`
    where it prints its numbers with the period, which is all but two of them."""
    breaks, expected = {}, 1
    for pageno, lines in pages:
        tops = set()
        for line in lines:
            number = line_punkt(line, margin)
            if number and int(number) == expected:
                tops.add(line.top)
                expected += 1
        breaks[pageno] = tops
    return breaks


def punkt_of(text, bare):
    """The punkt number a block opens with, or None. `bare` where the document
    prints its numbers without the period."""
    match = RE_PUNKT.match(text) or (RE_BARE_PUNKT.match(text) if bare else None)
    return match.group(1) if match else None


def block_punkter(blocks, bare):
    """The punkt every block opens with, aligned with `blocks`.

    A bare number is read here without the geometry that made it safe at the line
    level -- `classify_letterhead` hands on text, not lines -- so the numbers have
    to *climb*: a bilaga's own numbered list is otherwise a second run of
    punkter, and riktlinjer 04/2020 closes with a nine-item list that read as
    punkt 1-9 all over again, taking 90 paragraphs' worth of joins onto the wrong
    punkt with it.

    Climbing, and not the line level's "the number the document is due": measured
    against the block stream that rule loses whole documents, because the blocks
    do not number 1..N. Front matter takes punkt 1 with it in riktlinjer 09/2020
    and punkt 9 goes missing in 03/2019, and the strict rule stops dead at the
    first gap -- 8 of the 60 documents lose every punkt they have, 09/2020 all
    47 of them."""
    out, last = [], 0
    for kind, text, _level in blocks:
        punkt = punkt_of(text, bare) if kind == "stycke" else None
        if bare and punkt and int(punkt) <= last:
            punkt = None
        if punkt:
            last = int(punkt)
        out.append(punkt)
    return out


def join_continuations(blocks, bare):
    """Join a block that continues the previous numbered punkt back onto it.

    Where a document numbers its punkter, every substantive paragraph carries a
    number, so an unnumbered paragraph directly after a numbered one is the tail
    of it, split by a page break or by the indented run of a list. A heading ends
    the join -- what follows a heading starts something, whatever its numbering.

    That premise is *tested* rather than assumed, because a minority of these
    documents number their **sections** "1." and "2." and set plain prose under
    them. Relied on there it is catastrophic: the section number swallows every
    paragraph until the next section, and WP 250 arrived as a single
    46,000-character block, WP 248 as a 33,000-character one. Measured over the
    corpus the two populations do not overlap -- a section-numbered document
    numbers at most 9 % of its paragraphs, a punkt-numbered one at least 29 % --
    so a document below `PUNKT_COVERAGE_MIN` is read as the plain prose it is:
    nothing is joined, and its numbers anchor nothing, since a number that is
    not a punkt is not what a citation to a punkt means."""
    punkter = block_punkter(blocks, bare)
    styck = [punkt for (kind, _text, _level), punkt
             in zip(blocks, punkter, strict=True) if kind == "stycke"]
    if not styck or sum(1 for p in styck if p) / len(styck) < PUNKT_COVERAGE_MIN:
        return [(kind, text, level, None) for kind, text, level in blocks]
    out = []
    for (kind, text, level), punkt in zip(blocks, punkter, strict=True):
        if (kind == "stycke" and out and out[-1][0] == "stycke"
                and out[-1][3] and not punkt):
            out[-1] = (kind, "%s %s" % (out[-1][1], text), level, out[-1][3])
            continue
        out.append((kind, text, level, punkt))
    return out


def drop_repeated_title(blocks, titel):
    """Drop the cover's copy of the title where the PDF opens with it -- the
    page already carries it as the h1, so the body would open by repeating
    itself. The echo matching (including the letterhead-before-the-title shape,
    and the step-over of cover punctuation that folds away entirely) is the
    shared `drop_leading_title_echo`."""
    return drop_leading_title_echo(blocks, titel, text_of=lambda b: b[1])


# --------------------------------------------------------------------------
# the cover title, where the page's is in the wrong language
# --------------------------------------------------------------------------

# how a Swedish document names itself, and how an English one does. The EDPB
# leaves the English title standing on the Swedish page of four documents
# (Riktlinjer 4/2019, 05/2021, 10/2020 and Rekommendationer 1/2025 all carry
# "Guidelines …" / "Recommendations …" as the Swedish page's heading) even
# though the Swedish PDF beside it is a full translation with its own Swedish
# title on the cover. Everywhere else the page title is the better text -- it is
# clean HTML rather than PDF extraction, which glues hyphenated line breaks and
# occasionally truncates -- so the cover is consulted *only* to correct the
# language, never as a general second opinion.
RE_SWEDISH_LEAD = re.compile(r"^(?:Riktlinjer?|Rekommendationer?)\b", re.I)
RE_ENGLISH_LEAD = re.compile(r"^(?:Guidelines?|Recommendations?)\b", re.I)
# a cover title runs to at most this many blocks before the version/adoption
# line that closes it ("Riktlinjer 4/2019 om artikel 25" / "Inbyggt dataskydd
# och dataskydd som standard" / "Version 2.0")
COVER_TITLE_BLOCKS = 3


def cover_title(paras):
    """The Swedish title off the document's own cover, or None when the cover
    does not open the way these documents' covers do."""
    texts = [t for p in paras if (t := normalize_space(p.text))]
    start = next((i for i, t in enumerate(texts[:COVER_TITLE_BLOCKS])
                  if RE_SWEDISH_LEAD.match(t)), None)
    if start is None:
        return None
    title = [texts[start]]
    for text in texts[start + 1:start + 1 + COVER_TITLE_BLOCKS]:
        if RE_FRONT_MATTER.match(text):
            return " ".join(title)
        title.append(text)
    return None


def titled(record, paras):
    """The document's title: the EDPB page's, except where a Swedish document's
    page kept the English heading and the Swedish cover states the real one."""
    if record["sprak"] == "sv" and RE_ENGLISH_LEAD.match(record["titel"]):
        return cover_title(paras) or record["titel"]
    return record["titel"]


# --------------------------------------------------------------------------
# the WP29 cover
# --------------------------------------------------------------------------

def wp_cover(paras, wp):
    """``{titel, antagen}`` off an endorsed WP29 document's own cover.

    The title is what stands between the WP number and the adoption dates; the
    date is the *last* of those dates, which is the revision the EDPB endorsed
    ("Antagna den 3 oktober 2017 / Senast granskade och antagna den 6 februari
    2018").

    One of the endorsed documents sets no such cover: the ställningstagande on
    artikel 30.5 opens with its title in the running text, states no adoption
    date anywhere and carries no WP number to anchor either of them to. The
    registry writes both down off the EDPB's own page for it. That document is
    recognised by having *no number* rather than by having a registry title, so
    that writing a title into any other entry cannot quietly turn off the
    identity check below -- which is the whole reason a conversion published by
    someone other than the issuer can be trusted here (`edpb_data.HBDI`). It gets
    an identity check of its own instead: the opening prose has to state the
    title the registry claims for it."""
    texts = [normalize_space(p.text) for p in paras]
    if wp.number is None:
        # `raise`, not `assert` (rule:errors-drive-retry-use-raise): this is the
        # one check in this function whose absence would let the parse *succeed*
        # with the wrong text -- the two below leave `start`/`end` None and die
        # on the slice either way, so they degrade loudly. Stripped under -O,
        # this one would file whatever the source served under this URI wearing
        # the registry's title and date, which is the outcome it exists to stop.
        if match_fold(wp.titel) not in match_fold(
                " ".join(texts[:COVER_TITLE_BLOCKS + 1])):
            raise ValueError(
                "the document filed as %s does not open with the title the "
                "registry records for it -- the source it is fetched from now "
                "serves something else" % wp.slug)
        return {"titel": wp.titel, "antagen": wp.antagen}
    number = wp.number
    start = next((i for i, t in enumerate(texts)
                  if (m := RE_WP_NUMBER.search(t)) and m.group(1) == number), None)
    assert start is not None, (
        "the cover of the WP%s document names no matching WP number -- the "
        "newsroom item recorded for it in edpb_data.WP29 serves another document"
        % number)
    end = next((i for i, t in enumerate(texts[start + 1:], start + 1)
                if RE_WP_ADOPTED.search(t) or RE_WP_ADOPTED_EN.search(t)), None)
    assert end is not None, (
        "the cover of the WP%s document states no adoption date" % number)
    # the *last* date in the adoption block: these documents were adopted, then
    # revised, and it is the revision the EDPB endorsed
    dates = RE_WP_ADOPTED.findall(" ".join(texts[end:end + 2])) \
        or RE_WP_ADOPTED_EN.findall(" ".join(texts[end:end + 2]))
    day, month, year = dates[-1]
    return {"titel": RE_WP29_NAME.sub("", " ".join(texts[start + 1:end])).strip(),
            "antagen": "%s-%02d-%02d" % (year, MONTHS[month.lower()], int(day))}


# --------------------------------------------------------------------------
# body
# --------------------------------------------------------------------------

def _paragraphs(path, patch_key, upprepat_sidhuvud=False):
    """The PDF's paragraph stream with the numbered punkter forced apart, and
    whether the document prints those numbers bare -- the block layer reads the
    number off the text and has no geometry left to decide that for itself.

    `upprepat_sidhuvud` is the issuing body's own registry flag: whether its
    template reprints a running head at the top of every page. The shared
    masthead pattern cannot name that head, because it is the document's own
    avsnittsnamn and differs per document; what identifies it is that it recurs
    in the page margin, which is `pdftext.strip_page_furniture`'s test."""
    pages = list(pdf_pages(path, patch_key))
    if upprepat_sidhuvud:
        pages = strip_page_furniture(pages)
    margin = punkt_margin(pages)
    breaks = numbered_breaks(pages, margin)
    paras = [p for pageno, lines in pages
             for p in page_paragraphs(lines, None, pageno,
                                      force_break_tops=breaks[pageno])]
    return paras, margin is not None


def body(paras, titel, bare, feta_rubriker=False):
    """The document's text as typed blocks.

    `feta_rubriker` is the issuing body's own registry flag: whether its
    template marks a heading bold. Most of these do not, so a size above the
    running text is the only signal there is; EUIPO:s does, and reading its
    documents by size instead calls every paragraph of prose a heading -- its
    running head and footer are set smaller than its body text and are the
    commonest size in a short avsnitt, so "larger than the commonest size" is
    the body itself."""
    return [Block("stycke", text, punkt=punkt) if kind == "stycke"
            else Block("rubrik", text, level)
            for kind, text, level, punkt in join_continuations(
                drop_repeated_title(
                    classify_letterhead(paras, RE_FRONT_MATTER, RE_MASTHEAD,
                                        by_size=not feta_rubriker), titel),
                bare)]


def footnotes(paras):
    """The notes the block classifier drops -- see
    `lib.pdftext.letterhead_footnotes`. A riktlinje's apparatus lives here: the
    artikel 29-gruppens yttranden it builds on and the EU-domstolens judgments
    it reads are cited in the notes far more often than in the running text."""
    return [Fotnot(mark, text)
            for mark, text in letterhead_footnotes(paras, RE_FRONT_MATTER,
                                                   RE_MASTHEAD)]


def _edpb_fields(serie, record, paras):
    """What an EDPB document's cover and registry add to its record.

    The WP29 documents are the ones that need it: the endorsed text states its
    own WP number and revision on its cover, and `wp_cover` checks the file
    against the registry entry so a mirror that ever serves another document
    fails the parse rather than filing the wrong text."""
    if serie != "wp":
        return {"titel": titled(record, paras), "antagen": record["antagen"],
                "revision": None, "citation": None}
    wp = WP29_BY_SLUG[record["nummer"]]
    return {**wp_cover(paras, wp), "revision": wp.revision,
            # the one endorsed document the working party numbered not at all is
            # cited by the name the registry files it under. The model cannot
            # look that up -- it knows no EDPB data -- so the caller that does
            # passes it, and passes None for every numbered one
            "citation": wp.citation}


RE_EBA_COVER = re.compile(r"EBA/(?:GL|REC)/(\d{4})/(\d+)")


# --------------------------------------------------------------------------
# the EBA's Swedish title, which lives only on the document's own cover
# --------------------------------------------------------------------------
# 72 of the EBA's 80 documents *are* Swedish text, but everything the harvest
# can read names them in English: the leaf page's <h1>, the link, and the file
# name (which marks the language with an "_SV" suffix and nothing else). The
# Swedish name is printed on the cover of the same PDF the body is read from.
# That matters beyond tidiness -- the corpus cites this material by title far
# more often than by number (285 title citations against 73 number ones for the
# EBA), so an English title is what stops those citations resolving.
#
# The cover sets, in some order: a shouted running head, the number and date,
# the EBA's distribution mark, and the title in sentence case. The title is the
# first sentence-case paragraph carrying a vägledningsord, with the furniture
# taken off its ends -- never its middle, since an amending riktlinje names the
# riktlinje it amends by number inside its own title.
RE_VAGLEDNINGSORD = re.compile(
    r"riktlinjer(?:na)?|riktlinje|rekommendation(?:er)?(?:na)?", re.I)
# what the cover prints beside the title and the title is not: the number, the
# date in either spelling, the EBA's own distribution mark, and a version note
RE_EBA_FURNITURE = re.compile(
    r"EBA/(?:GL|REC)/\d{4}/\d+|ESMA\d[\w-]*"
    r"|\b\d{1,2}\s+(?:januari|februari|mars|april|maj|juni|juli|augusti"
    r"|september|oktober|november|december)\s+(?:19|20)\d{2}"
    r"|\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b|\b(?:19|20)\d{2}-\d{2}-\d{2}\b"
    r"|EBA (?:Public|Regular Use)|\(konsoliderad version\)", re.I)
# "Slutrapport om riktlinjer för X" -- the EBA publishes a riktlinje inside a
# final report and the report's wrapper is not part of the riktlinje's name
RE_EBA_RAPPORT = re.compile(
    r"^(?:Slutlig\s+rapport|Slutrapport)\s*[–-]?\s*(?:om\s+)?", re.I)
# what a consolidated cover prints before the title: EUR-Lex's own change
# markers, set in a symbol font (a private-use glyph) and followed by the
# marker's letter code -- "\uf0daO Riktlinjer", "\uf0d8A1 EBA/GL/2023/02".
# Dropping the glyph alone left the bare "O" opening the title.
RE_EBA_COVER_LEAD = re.compile(r"^(?:[\uE000-\uF8FF][A-Z]\d?|[^\w(])+\s*")
RE_EBA_BODY = re.compile(
    r"^(?:[A-D]\.\s*|\d+\.?\s+)?(?:Efterlevnad|Riktlinjernas status"
    r"|Rekommendationernas status|Innehåll|Contents)", re.I)
COVER_LINES = 12
# a title set across two paragraphs continues in one opening with a preposition
RE_EBA_FORTSATTNING = re.compile(
    r"(?:för|om|enligt|avseende|gällande|till|på|i)\s", re.I)
# an unfilled template the EBA left in the document
RE_EBA_MALL = re.compile(r"20XX|ÅÅÅÅ|XX/XX|DD\s+månad", re.I)
# the riktlinje a cover title says it amends. A document cannot amend itself,
# so this naming the record's *own* number proves the PDF behind that leaf is
# the amending riktlinje rather than the document filed there.
RE_EBA_ANDRING = re.compile(
    r"om\s+ändring\s+av\s+(?:riktlinjerna|rekommendationerna)\s+"
    r"EBA/(?:GL|REC)/(\d{4})/(\d+)", re.I)
# shorter than this the cover printed a lead word, not a title
TITEL_MIN = 25


def _clean(para):
    """One cover paragraph with the furniture beside the title removed.

    Only at the ends: an amending riktlinje names the riktlinje it amends by
    number in the middle of its own title ("Riktlinjer om ändring av
    riktlinjerna EBA/GL/2016/07 för tillämpningen av..."), and that number is
    part of what the document is called."""
    text = RE_EBA_COVER_LEAD.sub("", normalize_space(para or ""))
    while True:
        head = RE_EBA_FURNITURE.match(text)
        if head:
            text = text[head.end():].strip()
            continue
        tail = None
        for m in RE_EBA_FURNITURE.finditer(text):
            if m.end() == len(text.rstrip()):
                tail = m
        if not tail:
            return normalize_space(text)
        text = text[:tail.start()].strip()


def eba_cover_title(paras):
    raw = [RE_EBA_COVER_LEAD.sub("", normalize_space(p.text or "")) for p in paras[:COVER_LINES]]
    clean = [_clean(text) for text in raw]
    for i, text in enumerate(clean):
        if RE_EBA_BODY.match(text):
            break
        ord_ = RE_VAGLEDNINGSORD.search(text)
        if not text or shouted(text) or ord_ is None:
            continue
        title = raw[i]
        # the EBA sets a long title as its lead word alone over the rest
        oavslutad = ord_.end() == len(text)
        for j in range(i + 1, len(raw)):
            if not clean[j] or shouted(clean[j]) or RE_EBA_BODY.match(clean[j]):
                break
            if oavslutad and RE_VAGLEDNINGSORD.match(clean[j]):
                title = raw[j]          # the lead word repeated, not continued
            elif oavslutad or RE_EBA_FORTSATTNING.match(clean[j]):
                title = "%s %s" % (title, raw[j])
            else:
                break
            oavslutad = False
        title = RE_EBA_RAPPORT.sub("", _clean(title))
        if len(title) < TITEL_MIN or RE_EBA_MALL.search(title):
            continue
        return title[:1].upper() + title[1:]
    return None


def eba_titel(record, paras):
    """The document's title: its Swedish name off the cover where the document
    is Swedish, and the record's own English name otherwise.

    Two documents' worth of care. An English document's record title is already
    in its own language, so the cover adds nothing. And a Swedish cover whose
    title says it amends *this document's own number* is not this document's
    title at all: nothing amends itself, so the PDF behind that leaf is the
    amending riktlinje, which the EBA files behind the amended riktlinje's page.
    Five documents are in that state (eba/gl/2015-12, 2018-01, 2018-05, 2018-10
    and 2020-14). Taking the cover title there would leave the artifact saying
    it is the amendment while its own identifier says it is the amended act --
    so the record's title stands, and `KNOWN-GAPS.md` records the harvest
    defect underneath it."""
    if record["sprak"] != "sv":
        return record["titel"]
    titel = eba_cover_title(paras)
    # 72 of the 72 Swedish documents state a title here. None means the EBA
    # changed its cover template, which is a parser change and not something to
    # ship an English title over in silence (rule:fail-fast).
    assert titel, ("the cover of %s prints no Swedish title"
                   % record["basefile"])
    ar, lopnummer = record["nummer"].split("/")
    amends = RE_EBA_ANDRING.search(titel)
    if amends and (amends.group(1), int(amends.group(2))) == (ar, int(lopnummer)):
        return record["titel"]
    return titel


def _eba_fields(serie, record, paras):
    """What an EBA document's cover adds to its record: the check that it is the
    right document.

    The cover states the number as its first line ("EBA/GL/2026/01", above the
    date and the title), and that is the only place four fifths of this corpus
    states it at all -- the leaf pages of the newer guidelines print no number
    anywhere. `eba_download` therefore *names* the document from this same
    cover, and re-reading it here closes the loop the way `wp_cover` does for a
    WP29 document: a file that ever changes behind its URL fails the parse
    rather than being filed under an identity that is not its.

    The Swedish title comes from that same cover (`eba_cover_title`): the
    record's is the English one, which is what the EBA's leaf page, link and
    file name all carry even for a document that is Swedish throughout."""
    del serie
    cover = " ".join(p.text for p in paras[:8] if p.text)
    printed = {"%s/%s" % (year, int(serial))
               for year, serial in RE_EBA_COVER.findall(cover)}
    assert printed, ("the cover of %s prints no EBA number -- either the file "
                     "behind its URL changed or this is not the document the "
                     "harvest named" % record["basefile"])
    # anywhere on the cover, not the first one: an amending riktlinje prints the
    # number it amends beside its own ("RIKTLINJER OM ÄNDRING AV RIKTLINJERNA
    # EBA/GL/2018/10 — EBA/GL/2022/13"), and the amended one comes first
    year, serial = record["nummer"].split("/")
    assert "%s/%s" % (year, int(serial)) in printed, (
        "%s is filed as %s but its cover prints %s"
        % (record["basefile"], record["nummer"], ", ".join(sorted(printed))))
    return {"titel": eba_titel(record, paras), "antagen": record["antagen"],
            "revision": None, "citation": None}


def _eiopa_fields(serie, record, paras):
    """What an Eiopa document's cover adds to its record: the check that it is
    the right document.

    The check is conditional, unlike the EBA's, and the Swedish text is why.
    `eiopa_download` names a document from the cover of its **English**
    manifestation, because that is the one the Board of Supervisors printed the
    number on, and several of the Swedish translations print no number at all.
    So a stored cover that names one number must name this document's; a cover
    that names none says nothing either way and is left alone. `cover_number`
    reports two numbers the same way it reports none, which is the same
    silence for the same reason: a consolidated edition prints the number it
    consolidates beside the number that amended it, and nothing on the file
    says which of the two it is filed under.

    Eiopa writes that number in almost every spelling a text can take, down to
    poppler rendering an old cover's hyphen as an opening parenthesis
    ("EIOPA(BoS(14(026"), so the reading is `eiopa_download.cover_number`
    rather than a second regex kept in step with it by hand."""
    del serie
    printed = eiopa_download.cover_number(
        " ".join(p.text for p in paras[:8] if p.text))
    assert printed is None or printed == record["nummer"], (
        "%s is filed as %s but its cover prints %s"
        % (record["basefile"], record["nummer"], printed))
    return {"titel": record["titel"], "antagen": record["antagen"],
            "revision": None, "citation": None}


# the ED Decision a document's own cover names, in the two spellings EASA has
# used for it: "Annex III to ED Decision 2026/006/R" on the annexes issued since
# about 2016, and the bare "Decision 2015/010/R" on the older ones.
RE_EASA_COVER = re.compile(r"\b(?:ED\s+)?Decision\s+(\d{4})[/-](\d{3})[/-]R\b",
                           re.I)


def _easa_fields(serie, record, paras):
    """What an EASA annex's cover adds to its record: the check that it is the
    right annex.

    The cover names the instrument -- "Annex IV to ED Decision 2022/005/R 'AMC
    and GM to Annex IV (Part-CAT) to Commission Regulation (EU) No 965/2012 --
    Issue 2, Amendment 20'" -- and the page named the same instrument in its
    Related ED Decision field. Reading both closes the loop the way `_eba_fields`
    does: a file that ever changes behind its URL fails the parse rather than
    being filed under an identity that is not its.

    **Either witness will do**, because EASA prints one or the other and not
    always both. An amending annex names the decision it amends and not its own
    ("The Annex to ED Decision 2012/020/R is amended as follows"), and one
    cover in the corpus misspells its own ("Annex IV to ED Decision 201/022/R",
    three digits where EASA means 2019). What every cover does carry is the
    annex's own name, set as its first line, so a cover that names the annex
    identifies it just as well as a cover that names the decision. A cover
    doing neither still fails.

    The number is accepted **anywhere** on the cover, never as the first match:
    an amending annex prints the decision it amends beside its own ("Annex IV to
    ED Decision 2022/005/R ... The Annex to Decision 2014/015/R of 24 April 2014
    ... is amended"). And the check is conditional on the cover naming one at
    all, because the oldest annexes name none: the cover of AMC & GM to Part-MED
    reads "Initial issue / 15 December 2011" and nothing more.

    The citation is the annex's own name, which is also its title. EASA gives it
    no separate number -- see `issuers.EASA` -- so `Series.identifier` is never
    reached for this body."""
    del serie
    cover = " ".join(p.text for p in paras[:8] if p.text)
    printed = {"%s/%s/R" % pair for pair in RE_EASA_COVER.findall(cover)}
    beslut = (record["beslut"] or "").removeprefix("ED Decision ")
    # the annex's own name, which every cover prints as its first line whether
    # or not it prints a decision. "&" is spelled out before folding because
    # EASA's library writes "AMC & GM" and its covers write "AMC and GM". The
    # length floor is `drop_leading_title_echo`'s, for its reason: a folded name
    # of a few characters is inside almost any cover, and would witness for a
    # document it has nothing to do with.
    namn = match_fold(record["titel"].replace("&", " and "))
    heter = len(namn) >= TITLE_ECHO_MIN \
        and namn in match_fold(cover.replace("&", " and "))
    assert not printed or not beslut or beslut in printed or heter, (
        "%s is filed under %s, its cover names %s, and its cover does not "
        "carry its own name either"
        % (record["basefile"], record["beslut"], ", ".join(sorted(printed))))
    return {"titel": record["titel"], "antagen": record["antagen"],
            "revision": None, "citation": record["titel"]}


def _acer_fields(serie, record, paras):
    """What an ACER document's cover adds to its record: the check that it is
    the right document.

    ACER's yttranden and rekommendationer print their number on their own cover
    ("OPINION No 13/2026"), and `acer_download` already required the listing's
    number to be among the numbers the cover prints. Reading it again here
    closes the loop the way `_eba_fields` does, and it is read the same way, for
    the same two reasons: **anywhere** on the cover, because a document may name
    another beside itself, and only **where the cover prints one at all**,
    because the scanned pre-2014 covers print none that OCR can read.

    Twelve paragraphs rather than eight: every ACER PDF published before 2017
    opens with a feedback wrapper page, and the cover is behind it.

    A ramriktlinje has no number and is cited by its name, which the record
    carries as `citation` -- see `issuers.ACER`, and `_easa_fields` for the same
    shape."""
    printed = acer_download.cover_numbers(
        " ".join(p.text for p in paras[:12] if p.text))
    assert serie == "ramriktlinjer" or not printed \
        or record["nummer"] in printed, (
            "%s is filed as %s but its cover prints %s"
            % (record["basefile"], record["nummer"], ", ".join(sorted(printed))))
    return {"titel": record["titel"], "antagen": record["antagen"],
            "revision": None, "citation": record["citation"]}


def _enisa_fields(serie, record, paras):
    """What an ENISA report's record already carries -- which is everything.

    This is the one issuer here whose documents state **no identity of their
    own**: an ENISA cover prints a title, a month and the agency's logo, and
    nothing that names the report in a series. There is therefore no
    cover-against-record check to make, the way `_eba_fields` and
    `_acer_fields` make one; the reading is what the leaf page said, and the
    citation is the title, which is what a citation to an ENISA report actually
    names (see `issuers.ENISA`)."""
    del serie, paras
    return {"titel": record["titel"], "antagen": record["antagen"],
            "revision": None, "citation": record["titel"]}


def _esma_fields(serie, record, paras):
    """What an Esma document's record already carries -- which is everything.

    Esma is the one issuer here whose **index is a register**: the library's
    Reference column states the number for every row, and the document
    corroborates it rather than supplying it. So there is no cover-against-record
    check to make here the way `_eba_fields` makes one, and making one anyway
    would cost documents: three of the 126 covers read disagree with the column,
    and in one of them the *cover* is wrong -- ESMA70-151-435 ('Samarbete mellan
    myndigheter enligt artiklarna 17 och 23') prints ESMA70-151-294 in its
    footer, which is a different riktlinje entirely. `esma_download` counts the
    agreement on every run instead, so a drift in either shows in its output.

    The title is the library's, and it is English for every document Esma
    published after 2016: those rows title only in English and hang the
    translations in a panel, so a Swedish riktlinje from 2023 arrives under its
    English name. `KNOWN-GAPS.md` says what that costs."""
    del serie, paras
    return {"titel": record["titel"], "antagen": record["antagen"],
            "revision": None, "citation": None}


def _berec_fields(serie, record, paras):
    """What a BEREC document's cover adds to its record: the check that it is
    the right document.

    BEREC prints its number as the **first line** of the cover, above the title
    and the date ("BoR (22) 81"), and that is where this reads it -- not
    anywhere in the front matter, the way `_eba_fields` and `_acer_fields` read
    theirs. The narrower window is what BEREC's own layout allows and what its
    corpus requires, and it is the stronger check of the two: 41 of the 43
    documents open with their number alone, a 42nd sets it inside a cover line
    ("BEREC BoR (19) 189 Final V 1.0"), and a number the document merely
    *cites* can no longer satisfy the check.

    Which is not hypothetical here. The Handbook of the geographical-survey
    riktlinjer, BoR (21) 104, consolidates three riktlinjer and reprints each
    with its own running head, so its front matter prints BoR (20) 42, BoR (21)
    32 and BoR (21) 82 and its own number nowhere at all. Read over eight
    paragraphs, that document fails a check it should pass and passes a check
    it should fail. Read over the first, it states no number and is not
    checked -- the same "only where the cover prints one" rule ACER's scanned
    covers need, arrived at from the opposite direction.

    The qualifier is left out of the comparison (`base_number`): the register
    writes "BoR (10) 44  Rev 1" where that document's cover writes
    "BoR (10) 44 Rev1", while the year and the serial are written one way in
    both."""
    del serie
    printed = berec_download.cover_numbers(paras[0].text if paras else "")
    assert not printed \
        or berec_download.base_number(record["nummer"]) in printed, (
            "%s is filed as %s but its cover opens with %s"
            % (record["basefile"], record["nummer"], ", ".join(sorted(printed))))
    return {"titel": record["titel"], "antagen": record["antagen"],
            "revision": None, "citation": None}


def _edps_fields(serie, record, paras):
    """What an EDPS document's cover adds to its record: the check that a
    numbered yttrande is the yttrande it is filed as.

    Only a numbered one can be checked, and only 111 of the EDPS's 442 documents
    have a number at all -- it numbers no riktlinje ever and numbered no
    yttrande before 2020. Where there is one the cover is where it came from
    (`edps_download` reads it there, because two listing titles in three drop
    it), so re-reading it closes the loop the way `_eba_fields` does: a file
    that changes behind its URL fails the parse rather than being filed under an
    identity that is not its.

    The check is conditional on the cover printing a number *now*, for the same
    reason `_berec_fields`' is: a scanned yttrande sets its number as an image
    and `pdftotext` reads none, and `edps_download` took that document's number
    off its listing title instead and counted it apart.

    The citation is always supplied, because `Series.identifier` is never
    reached for this body -- see `issuers.EDPS`."""
    printed = edps_download.printed_nummer(
        " ".join(p.text for p in paras[:8] if p.text))
    assert printed is None \
        or edps_download.nummer_slug(printed) == record["nummer"], (
            "%s is filed as %s but its cover prints %s"
            % (record["basefile"], record["nummer"], printed))
    return {"titel": record["titel"], "antagen": record["antagen"],
            "revision": None,
            "citation": edps_download.citation(serie, record["nummer"],
                                               record["antagen"])}


def _euipo_fields(serie, record, paras):
    """What an EUIPO volume's cover adds to its record: the check that it is
    the right del of the right volume.

    EUIPO gives its riktlinjer no number, so the check is on the coordinate
    instead. A cover prints it in a fixed layout -- two masthead lines, "Part
    C", "Opposition", "Section 3", "Unauthorised filing …" -- and
    `cover_scope` reads both halves off the opening paragraphs. A file that
    ever changes behind its ``/binary/`` reference therefore fails the parse
    rather than being filed under a coordinate that is not its.

    The two halves are checked differently, because a del-level PDF carries the
    covers of every avsnitt inside it: the del must be the one printed first,
    while the avsnitt need only be *among* the numbers the opening prints. That
    is the same reading `_easa_fields` and `_acer_fields` make of a number
    printed anywhere on a cover, for the same reason.

    The check is only made for the series carried del by del. The two families
    carried as one volume print a list of delar on the cover instead, and their
    `nummer` is not a coordinate at all.

    The citation is always supplied, because `Series.identifier` is never
    reached for this body -- see `issuers.EUIPO`."""
    if serie == EUIPO.series[0].kod:
        printed_del, printed_avsnitt = euipo_download.cover_scope(
            " ".join(p.text for p
                     in paras[:euipo_download.COVER_PARAGRAPHS] if p.text))
        part, _, avsnitt = record["nummer"].partition("-section-")
        assert printed_del is None or euipo_download.unit_nummer(
            "PART%s" % printed_del, "") == part, (
                "%s is filed under %s but its cover prints del %s"
                % (record["basefile"], part, printed_del))
        assert not avsnitt or not printed_avsnitt \
            or avsnitt in printed_avsnitt, (
                "%s is filed as avsnitt %s but its cover prints %s"
                % (record["basefile"], avsnitt,
                   ", ".join(sorted(printed_avsnitt))))
    return {"titel": record["titel"], "antagen": record["antagen"],
            "revision": None, "citation": record["citation"]}


# what each issuing body's documents need beyond their harvest record. A body
# is a program, not a subclass: this is the one place the source branches on
# which one, and it branches on data in `issuers`, never on a hardcoded list.
# --------------------------------------------------------------------------
# route A: the ECB and the ESRB, who publish in EUT rather than on their own
# sites, so their documents come out of CELLAR (`lib.cellar`) as the
# Publications Office serves them
# --------------------------------------------------------------------------

# which utgivare take this path -- the same map that harvests them
EURLEX_KODER = frozenset(eurlex_download.SYNC)

# the word a heading puts before an article's number, per language. Formex sets
# the designation and the title as separate elements; the guidance block carries
# one string, so the heading reads "Artikel 1 Ändringar".
ARTIKEL = {"sv": "Artikel", "en": "Article"}

# Formex block kinds that are apparatus rather than running text: the title is
# already the record's `titel`, and a keyword index belongs to case law
FORMEX_SKIP = {"title", "keyword"}

# the language codes a route A PDF prints alone above its classification mark
LANGUAGE_MARKS = {"EN", "SV"}


def _manifestation(root, basefile, sprak):
    """The stored manifestation for the language the record says it holds.

    `lib.cellar` stores one file per language it got, named for that language
    (``swe.fmx4.br``, ``eng.pdf``), so the record's own `sprak` picks the file
    and the parse never reads a language the page will not claim."""
    code = "swe" if sprak == "sv" else "eng"
    found = [p for p in compress.glob(
        eurlex_download.content_dir(root, basefile), code + ".*")]
    assert len(found) == 1, \
        "%s: %d %s manifestations, expected 1" % (basefile, len(found), code)
    return found[0]


def _formex_main(root, raw):
    """The main Formex part of a route A document.

    Three roots occur: `ACT` (the ESRB's beslut and rekommendationer), `GENERAL`
    (an ECB-yttrande as printed in the C series) and `CORR` (a rättelse). Only
    ACT has enacting terms. The other two carry their text in CONTENTS, which
    `parse_act` walks straight past -- it returns zero blocks for every one of
    them, which is what a bare `else: parse_act(...)` would have published.
    """
    if root.tag == "ACT":
        parse_act(root, raw)
        return
    if root.tag == "CORR":
        corr = root.find("CONTENTS.CORR")
        assert corr is not None, "CORR root carries no CONTENTS.CORR"
        for correction in corr.findall("CORRECTION"):
            # the description names the passage corrected ("Sidan 2, skäl 4"),
            # which walk_content does not reach and the correction is unreadable
            # without
            description = _text(correction, "DESCRIPTION")
            if description:
                raw.append(FormexBlock("heading", description, level=2))
            walk_content(correction, raw)
        return
    contents = root.find("CONTENTS")
    assert contents is not None, "%s root carries no CONTENTS" % root.tag
    walk_content(contents, raw)


def _from_formex_blocks(raw, sprak):
    """`lib.formex`'s blocks -> this source's ``(blocks, fotnoter)``.

    The projection is this source's own: a vägledning's page shows rubriker and
    stycken and has no article rail, so an article becomes a heading that names
    itself and every other running-text kind becomes a stycke."""
    blocks, noter = [], []
    for block in raw:
        if block.kind == "note":
            noter.append(Fotnot(block.num, block.text))
        elif block.kind == TABLE:
            # a vägledning's page has no table markup, so a table reads as its
            # caption (where the source sets one) plus one stycke per row with
            # the row's cells joined
            if block.text:
                blocks.append(Block("stycke", block.text))
            blocks.extend(Block("stycke",
                                " | ".join(cell.text.replace("\n", " ")
                                           for cell in row.cells))
                          for row in block.rows)
        elif block.kind in FORMEX_SKIP or not block.text:
            continue
        elif block.kind == "heading":
            blocks.append(Block("rubrik", block.text, block.level or 2))
        elif block.kind == "article":
            blocks.append(Block("rubrik", " ".join(
                x for x in (ARTIKEL[sprak], block.num, block.text) if x), 2))
        else:
            blocks.append(Block("stycke", block.text,
                                punkt=block.num or punkt_of(block.text, False)))
    return blocks, noter


def _formex_body(path, basefile, sprak):
    """A Formex manifestation -> ``(blocks, fotnoter)``. `lib.formex` reads the
    XML -- the same reader the eurlex source uses on the same documents."""
    raw = []
    for i, root in enumerate(formex_roots(path, "guidance", basefile)):
        if root.tag == "ANNEX":
            append_annex(raw, root)
        elif i == 0:
            _formex_main(root, raw)
        else:
            walk_content(root, raw, level=1)
        collect_notes(root, raw)
    return _from_formex_blocks(raw, sprak)


def _html_paragraph_blocks(text):
    """The ``<p>`` run of an HTML manifestation -> stycken.

    EUR-Lex serves the oldest ECB-yttranden as a flat run of ``<p>`` with no
    heading markup at all, so every paragraph is a stycke and the numbering is
    the only structure there is. The title block above the first numbered
    paragraph is the record's own metadata reprinted -- the title, the date, the
    CON number, the OJ coordinate -- and is dropped rather than published."""
    paragraphs = [normalize_space(p.get_text(" ", strip=True))
                  for p in BeautifulSoup(text, "html.parser").find_all("p")]
    first = next((i for i, para in enumerate(paragraphs)
                  if punkt_of(para, False)), 0)
    return [Block("stycke", para, punkt=punkt_of(para, False))
            for para in paragraphs[first:] if para]


def _html_body(path):
    """An HTML manifestation -> ``(blocks, [])``: this route carries no
    footnote apparatus of its own."""
    return _html_paragraph_blocks(compress.read_text(path)), []


def _eurlex_document(root, basefile, record):
    """A route A document's body and notes, from whichever manifestation CELLAR
    served it as. Each document has exactly one: 750 PDF (every ECB-yttrande
    before 2004), 176 Formex, 21 HTML."""
    path = _manifestation(root, basefile, record["sprak"])
    if ".fmx4" in path.name:
        return _formex_body(path, basefile, record["sprak"])
    if ".html" in path.name:
        return _html_body(path)
    issuer = BY_KOD[basefile.split("/")[0]]
    paras, bare = _paragraphs(path, ("guidance", basefile),
                              issuer.upprepat_sidhuvud)
    blocks = body(paras, record["titel"], bare, issuer.feta_rubriker)
    # the language code the ECB prints above that mark, alone on the first
    # page only. Dropped here rather than in RE_FRONT_MATTER because "en" is
    # also a Swedish word, and the pattern is shared with every other issuer.
    if blocks and blocks[0].text in LANGUAGE_MARKS:
        blocks = blocks[1:]
    return blocks, footnotes(paras)


FIELDS = {EDPB.kod: _edpb_fields, EBA.kod: _eba_fields, EASA.kod: _easa_fields,
          ACER.kod: _acer_fields, ENISA.kod: _enisa_fields,
          ESMA.kod: _esma_fields, BEREC.kod: _berec_fields,
          EDPS.kod: _edps_fields, EIOPA.kod: _eiopa_fields,
          EUIPO.kod: _euipo_fields}


def _eurlex_parse(basefile, root, record):
    """One route A basefile ("ecb/con/2013-82", "esrb/2016-14") -> artifact.

    The metadata is CELLAR's, not a cover's: the body states its own number,
    title and adoption date in the notice, so none of the cover reading the
    route B issuers need applies here. Only the body has to be read."""
    utgivare = basefile.split("/")[0]
    blocks, noter = _eurlex_document(root, basefile, record)
    return Vagledning(
        utgivare=utgivare, serie=record["serie"], nummer=record["nummer"],
        titel=record["titel"], sprak=record["sprak"],
        antagen=record.get("antagen"), celex=record.get("celex"),
        amnesord=list(record.get("amnesord") or []),
        body=blocks, fotnoter=noter,
        source_url=record.get("source_url"),
        document_url=record.get("dokument_url"),
    ).to_artifact(_fresh_parser(record["sprak"]))


def parse(basefile, root):
    """One basefile ("edpb/riktlinjer/05-2020", "eba/gl/2021-05") -> artifact
    dict, body citation-scanned."""
    utgivare, serie = basefile.split("/")[:2]
    if utgivare in EURLEX_KODER:
        return _eurlex_parse(basefile, root, compress.read_json(
            record_path(root, utgivare, basefile)))
    assert utgivare in FIELDS, "no guidance issuer %r" % utgivare
    assert serie in BY_KOD[utgivare].koder, \
        "no %s series %r" % (utgivare, serie)
    record = compress.read_json(record_path(root, utgivare, basefile))
    paras, bare = _paragraphs(pdf_path(root, basefile),
                              ("guidance", basefile),
                              BY_KOD[utgivare].upprepat_sidhuvud)
    fields = FIELDS[utgivare](serie, record, paras)
    return Vagledning(
        utgivare=utgivare, serie=serie, nummer=record["nummer"],
        titel=fields["titel"], sprak=record["sprak"],
        antagen=fields["antagen"], version=record.get("version"),
        revision=fields["revision"], citation=fields["citation"],
        beslut=record.get("beslut"),
        ersatt_av=record.get("ersatt_av"),
        ersatt_av_identifier=record.get("ersatt_av_identifier"),
        ersatt_av_url=record.get("ersatt_av_url"),
        konsultation_url=record.get("konsultation_url"),
        amnesord=list(record.get("amnesord") or []),
        body=body(paras, fields["titel"], bare,
                  BY_KOD[utgivare].feta_rubriker),
        fotnoter=footnotes(paras),
        source_url=record.get("source_url"),
        document_url=record.get("dokument_url"),

    ).to_artifact(_fresh_parser(record["sprak"]))
