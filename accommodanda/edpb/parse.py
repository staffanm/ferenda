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
which is WP243's subject (see `series.WP29`). Their Swedish covers state both,
in a layout fixed across all seven, and a document naming itself beats an index
naming it wrongly -- the same departure `rs` makes for Försäkringskassans
serienummer.
"""

import functools
import re
from collections import Counter

from ..lib import compress, util
from ..lib.datasets import NAMEDACTS
from ..lib.lagrum import (
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
)
from ..lib.util import (
    drop_leading_title_echo,
    match_fold,
    normalize_space,
    record_path,
)
from .download import pdf_path
from .model import Block, Fotnot, Vagledning
from .series import BY_KOD, WP29_BY_SLUG

# what an EDPB guideline actually cites: EU legislation (the förordning it
# interprets, by article), the EU courts, and the EDPB's and artikel
# 29-gruppens own guidance. It cites no Swedish statute at all, so the SFS
# machinery is not requested -- a smaller grammar and no false "3 §" matches.
EDPB_PARSE_TYPES = [EULAGSTIFTNING, EURATTSFALL, VAGLEDNING]

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
    r"|^\s*(?i:ARTICLE\s+29\s+DATA\s+PROTECTION\s+WORKING\s+PARTY)\s*$")

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
    return LagrumParser({}, basefile="edpb", parse_types=EDPB_PARSE_TYPES,
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
    someone other than the issuer can be trusted here (`series.HBDI`). It gets
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
        "newsroom item recorded for it in series.WP29 serves another document"
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

def _paragraphs(path, patch_key):
    """The PDF's paragraph stream with the numbered punkter forced apart, and
    whether the document prints those numbers bare -- the block layer reads the
    number off the text and has no geometry left to decide that for itself."""
    pages = list(pdf_pages(path, patch_key))
    margin = punkt_margin(pages)
    breaks = numbered_breaks(pages, margin)
    paras = [p for pageno, lines in pages
             for p in page_paragraphs(lines, None, pageno,
                                      force_break_tops=breaks[pageno])]
    return paras, margin is not None


def body(paras, titel, bare):
    """The document's text as typed blocks."""
    return [Block("stycke", text, punkt=punkt) if kind == "stycke"
            else Block("rubrik", text, level)
            for kind, text, level, punkt in join_continuations(
                drop_repeated_title(
                    classify_letterhead(paras, RE_FRONT_MATTER, RE_MASTHEAD,
                                        by_size=True), titel), bare)]


def footnotes(paras):
    """The notes the block classifier drops -- see
    `lib.pdftext.letterhead_footnotes`. A riktlinje's apparatus lives here: the
    artikel 29-gruppens yttranden it builds on and the EU-domstolens judgments
    it reads are cited in the notes far more often than in the running text."""
    return [Fotnot(mark, text)
            for mark, text in letterhead_footnotes(paras, RE_FRONT_MATTER,
                                                   RE_MASTHEAD)]


def parse(basefile, root):
    """One basefile ("riktlinjer/05-2020", "wp/248") -> artifact dict, body
    citation-scanned."""
    serie = basefile.split("/", 1)[0]
    assert serie in BY_KOD, "no EDPB series %r" % serie
    record = compress.read_json(record_path(root, serie, basefile))
    paras, bare = _paragraphs(pdf_path(root, basefile), ("edpb", basefile))
    fields = (wp_cover(paras, WP29_BY_SLUG[record["nummer"]]) if serie == "wp"
              else {"titel": titled(record, paras),
                    "antagen": record["antagen"]})
    return Vagledning(
        serie=serie, nummer=record["nummer"], titel=fields["titel"],
        sprak=record["sprak"], antagen=fields["antagen"],
        version=record.get("version"),
        revision=WP29_BY_SLUG[record["nummer"]].revision
        if serie == "wp" else None,
        konsultation_url=record.get("konsultation_url"),
        amnesord=list(record.get("amnesord") or []),
        body=body(paras, fields["titel"], bare), fotnoter=footnotes(paras),
        source_url=record.get("source_url"),
        document_url=record.get("dokument_url"),
    ).to_artifact(_fresh_parser(record["sprak"]))
