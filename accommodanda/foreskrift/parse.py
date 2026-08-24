"""Parse a myndighetsföreskrift PDF into the :class:`Regulation` model and
project it to a JSON artifact.

The shape is shared across all ~100 författningssamlingar (they follow the same
Swedish authoring conventions -- the *Myndigheternas skrivregler* masthead, an
``N kap.``/``N §`` body), so one parser serves every agency and a new fs stays
download-config only. The corpus is, however, deeply heterogeneous -- scanned
1990s PDFs with no font signal, 600-page förteckningar with no §§ at all,
two-column mastheads that text extraction mangles -- so every step is
best-effort: a missing date or an unparsed bemyndigande is ``None``/empty, never
an error, and a body with not one § still yields a document (its stycken).

Two layers over the shared font-aware extraction (``lib.pdftext``):

  * **body** -- :func:`classify` turns the page paragraphs into ``kapitel`` /
    ``paragraf`` / ``rubrik`` / ``stycke`` blocks. The structural markers are
    read *textually* (a block that begins ``N §`` or ``N kap.``), not from font:
    bold is reliable on a modern FFFS PDF but absent on a scanned one, while the
    text convention holds across the corpus. ``structure.nest`` then builds the
    kapitel/paragraf tree, minting the SFS ``#K2P3`` anchors that make each
    paragraf a citation target.
  * **metadata** -- :func:`extract_metadata` lifts the masthead facts the model
    carries: beslutsdatum, ikraftträdande, "Utkom från trycket", the
    ``bemyndigande`` (the empowering SFS paragrafer, via the citation engine --
    the edge that lets a statute list the regulations issued under it), the EU
    directives a footnote says it ``genomför``, and the regulations it replaces.
"""

import re
from pathlib import Path

from bs4 import BeautifulSoup

from ..lib import tabell
from ..lib.artifact import footnote_nodes
from ..lib.lagrum import (
    EULAGSTIFTNING,
    FORESKRIFT,
    KORTLAGRUM,
    LAGRUM,
    interleave,
    sfs_parser,
)
from ..lib.pdftext import (
    RE_KAP_MARK,
    RE_PARA_MARK,
    Para,
    page_paragraphs,
    pdf_pages,
    ruled_footnotes,
)
from ..lib.util import MONTHS, approximate_date, confine, fold_swedish
from .agencies import AAFS_SERIES, REGISTRY
from .model import Amendment, Block, Consolidation, Regulation, regulation_uri
from .structure import nest

# a föreskrift cites SFS (the empowering law), EU directives (what it
# implements) and its siblings -- an agency's regulations cross-refer constantly
# ("Utöver denna föreskrift gäller MSBFS 2020:7"), and the metadata relations
# (upphäver/ändrar, RE_FS_REF below) only ever capture the masthead's, never one
# in the operative text. It does not cite case law or förarbeten. KORTLAGRUM
# links pinpointed abbreviation refs ("32 § LVU", the corpus's densest
# pinpointed-abbreviation seam); its trigger requires the pinpoint, so a bare
# "enligt LVU" -- which names no provision -- stays plain text by design.
PARSE_TYPES = [LAGRUM, EULAGSTIFTNING, FORESKRIFT, KORTLAGRUM]

RE_RUBRIK_NUM = re.compile(r"^(\d+(?:\.\d+)*)\s+\S")     # "2.1 Heading"
RE_LIST_ITEM = re.compile(r"^(?:\d+[.)]|[-–—•])\s")       # "1." / "– " list rows
# a heading has to say something: MCFFS 2026:11 sets a stray bold 8-point "."
# on page 12, which is a typesetting artifact and listed in the table of
# contents as a heading named "."
RE_HAS_LETTER = re.compile(r"[^\W\d_]")
# the årsutgåva inside a föreskrift designation ("PMFS 2023:12" -> 2023), which
# is what dates a consolidation: its newest amendment is its own cutoff
RE_FS_YEAR = re.compile(r"\b(\d{4}):\d")

# masthead facts (best-effort; the layout that carries them is often mangled)
RE_DATE = re.compile(r"den\s+(\d{1,2})\s+(%s)(?:\s+(\d{4}))?" % "|".join(MONTHS),
                     re.IGNORECASE)
RE_BESLUTAD = re.compile(r"beslutad[e]?\s+den\s+(\d{1,2})\s+(\w+)\s+(\d{4})", re.I)
RE_UTKOM = re.compile(r"Utkom\s+från\s+trycket.*?den\s+(\d{1,2})\s+(\w+)\s+(\d{4})",
                      re.IGNORECASE | re.DOTALL)
RE_IKRAFT = re.compile(r"träder\s+i\s+kraft\s+den\s+(\d{1,2})\s+(\w+)\s+(\d{4})", re.I)
# Whose entry into force a "träder i kraft" sentence states, read from the noun
# immediately before the verb -- the only position the corpus ever puts the
# subject in (10 830 occurrences, 199 of them with none of these nouns there). A
# föreskrift quotes dates that are not its own: 99 sentences say "Förordningen
# träder i kraft …" of an EU regulation named in a "Jfr" footnote, 45 read "…
# som träder i kraft …" about the act being amended. The determiner is not
# required, because the noun alone already disambiguates -- except for
# `ändring`, which admits 4 real provisions ("Ändringarna träder i kraft …",
# AFS 1993:10) at the price of also admitting a quoted "Ändringsförordningen".
RE_IKRAFT_SUBJECT = re.compile(
    r"(?:författ|föreskrift|allmänna\s+råd|kungörelse"
    r"|arbetsordning|beslut|ändring)\w*\s*$", re.I)
# What overrides an ändring declaration (RE_ANDRING / RE_AMENDING_FORMULA below):
# a masthead saying the document is the base regulation *with its amendments
# folded in*. Such a text names the amendments it incorporates
# ("Grundförfattningen i dess lydelse med införda ändringar omtryckt CSNFS
# 2009:3 ändrad CSNFS 2023:8"), so the amendment wording matches although the
# document is a grundförfattning and dated as one. 48 föreskrifter print such a
# note; without this veto 11 of them took the date of the newest amendment bound
# into them -- CSNFS 1998:7, decided in 1998, came into force in 2026.
RE_KONSOLIDERAD_MASTHEAD = re.compile(
    r"ändringar\s+införda|grundförfattningen\s+i\s+dess\s+lydelse"
    r"|i\s+dess\s+lydelse\s+(?:enligt|med)|konsoliderad", re.I)
# The bemyndigande clause every agency föreskrift must carry -- 18 b §
# författningssamlingsförordningen (1976:725): "I ingressen till författningen
# skall uppgift lämnas om det bemyndigande på vilket myndighetens beslutanderätt
# grundar sig". A föreskrift without one is this parser failing, not the
# document being silent, so the clause is found in two explicit steps rather
# than by one regex that can quietly match the wrong occurrence.
RE_STODAV = re.compile(r"[Mm]ed\s+stöd\s+av\b")
STODAV_WINDOW = 600     # longest real clause seen is FFFS 2014:12's ~400 chars
# Where the clause stops: the preamble verb it runs into, "att"/"i fråga om"
# (past which an ändringsförfattning names the föreskrift it *amends*, which is
# not its bemyndigande), or the end of the sentence.
#
# "End of sentence" cannot be a bare `\.`. A delegation almost always runs
# through a chapter, so "7 kap. 7 § fastighetstaxeringslagen (1979:1152)"
# truncated at the abbreviation dot to " 7 kap" -- no §, no act, nothing to
# resolve. That single character was most of the missing corpus, a delegation
# into an unchaptered act being the exception. Nor can it be "a period followed
# by a capital": the sentence a clause most often runs into is the first
# provision, ". 1 § Dessa föreskrifter …", which opens with a digit. So the test
# looks *behind* instead, at the abbreviations that occur inside a lagrum.
#
# The verbs are \b-anchored: unanchored, "kungör" matched inside "kungörelsen"
# and cut "13 § kungörelsen (1958:272) om tjänstekort" to "13 §", losing the act.
RE_STODAV_END = re.compile(
    r"\b(?:föreskriver|kungör|beslutar|meddelar|följande|att)\b"
    r"|\bi\s+fråga\s+om\b"
    # the abbreviations that occur *inside* a lagrum, whose dot is not a
    # sentence: kapitel, moment, bihang, stycke, nummer, punkt, m.fl., f/ff
    r"|(?<!\bkap)(?<!\bmom)(?<!\bbih)(?<!\bst)(?<!\bnr)(?<!\bpkt)(?<!\bp)"
    r"(?<!\bm)(?<!\bfl)(?<!\bff)(?<!\bf)\.")


def stodav_clause(text):
    """The "med stöd av …" bemyndigande clause of a föreskrift's ingress, or
    None. The window is bounded so a clause whose terminator two-column
    extraction has mangled yields its opening -- partial but right -- instead of
    running on into the body and collecting unrelated citations; and the *first*
    occurrence always wins, so a document with a long clause cannot silently
    fall through to a later, unrelated "med stöd av" further down."""
    start = RE_STODAV.search(text)
    if not start:
        return None
    window = text[start.end():start.end() + STODAV_WINDOW]
    end = RE_STODAV_END.search(window)
    return window[:end.start()] if end else window
# active masthead form ("ersätter/upphäver …") and the transitional-provision
# passive ("Genom föreskrifterna upphävs … (PMFS 2019:2)")
RE_ERSATTER = re.compile(r"\b(?:ersätter|upphäv(?:er|s))\b(.*?)(?:\.|$)",
                         re.DOTALL | re.I)
RE_FS_REF = re.compile(r"\b([A-ZÅÄÖ]+-?FS)\s*(\d{4}):(\d+)")   # NFS/TFS … ELSÄK-FS
# an ändringsförfattning's own title names its target: "… föreskrifter om
# ändring i <agency>s föreskrifter (ÅFS 2005:5) om …". Some agencies drop
# their own series designation in the parenthesis ("föreskrifter (2007:12)");
# the possessive title implies the record's own fs, so a bare ref is accepted
# only right after a "föreskrifter…"/"allmänna råd…" word (an SFS parenthesis
# like "förordningen (2001:512)" must never mint a föreskrift target).
RE_ANDRING = re.compile(r"ändring(?:ar)?\s+(?:i|av)\b", re.IGNORECASE)
# The other way a document declares it amends another: the amending enacting
# formula, "föreskriver … i fråga om <författning>" against a grundförfattning's
# "föreskriver följande". It is the only declaration an Omtryck carries whose
# title restates the base regulation's (SKSFS 2014:3). The span between the two
# halves has to admit periods -- the bemyndigande sitting in it abbreviates
# ("med stöd av 2 kap. 1 § förordningen (2010:1879), i fråga om …"), and 269
# mastheads phrase it that way.
RE_AMENDING_FORMULA = re.compile(
    r"föreskriver\b.{0,160}?\bi\s+fråga\s+om\b", re.IGNORECASE | re.DOTALL)
RE_BARE_OWN_REF = re.compile(
    r"(?:föreskrifter(?:na)?|allmänna\s+råd(?:en)?)[^()]*\((\d{4}):(\d+)\)")
# the issuing agency, read from the masthead (searched over a whitespace-collapsed
# copy, since two-column extraction breaks the lines apart). Three signals, tried
# in order:
#   1. the "Utgivare:" line as "<person>, <agency>" -- keep the agency (the segment
#      after the first comma), up to the ISSN / Utkom / FS-number the masthead runs
#      on into. A line with no comma is just a name (extraction often drops the
#      agency), so it yields nothing and the name signals take over.
# (case is significant throughout -- the captured agency must begin at a real
# uppercase letter, so these patterns carry no IGNORECASE; the anchor words spell
# both cases where a masthead varies them.)
RE_UTGIVARE = re.compile(
    r"Utgivare:[^,]{1,60},\s*([A-ZÅÄÖ][a-zåäö0-9 .-]{2,55}?)"
    r"\s*(?:ISSN|[A-ZÅÄÖ]|\d{4}:\d+|$)")
#   2. the publication title "<agency>s författningssamling" -- the agency is the
#      possessive prefix (the genitive -s optional: an older masthead prints
#      "Krisberedskapsmyndigheten Författningssamling" without it). Prose-safe --
#      "författningssamling" never occurs in the operative text.
# An agency name is one Capitalised word followed by lowercase continuation words
# ("Myndigheten för samhällsskydd och beredskap"); the continuation class excludes
# uppercase, so the capture cannot bleed left into a preceding heading word
# ("Skyltning Överlåtelse Transport Sprängämnesinspektionen"), and the optional
# trailing -s absorbs the genitive.
RE_FS_SERIES = re.compile(
    r"([A-ZÅÄÖ][a-zåäö0-9 .-]{2,55}?)s?\s+[Ff]örfattningssamling\b")
#   3. failing that, the föreskrift's own name "<agency>s föreskrifter/allmänna råd"
#      -- the genitive -s is mandatory here so a prose "följande allmänna råd" can
#      never be mistaken for a possessive agency prefix.
RE_FS_TITLE = re.compile(
    r"([A-ZÅÄÖ][a-zåäö0-9 .-]{2,55}?)s\s+(?:[Ff]öreskrift(?:er)?|[Aa]llmänna\s+råd)\b")
RE_DIREKTIV_CELEX = re.compile(r"/ext/celex/\d+L\d")    # a directive (…L…), not a reg (…R…)
# the "Jfr … direktiv …" implementation footnote; the directive right after "Jfr"
# is the one the föreskrift genomför (any further directives in the clause are ones
# *it* amends, not ones this föreskrift implements).
RE_JFR = re.compile(r"\bJfr\b(.*?)(?:\.\s|\n\n|\Z)", re.DOTALL)
# the verb that closes a föreskrift preamble ("… föreskriver följande")
RE_PREAMBLE_END = re.compile(r"föreskriver|kungör|beslutar|meddelar", re.I)


def _dedupe_bemyndigande(uris):
    """Drop a bare-law URI when a paragraf of that same law is also cited -- the
    paragraf is the precise empowering edge ('förordningen (2013:587)' plus '4 §'
    -> keep …/2013:587#P4, not the looser …/2013:587)."""
    laws_with_para = {u.split("#", 1)[0] for u in uris if "#" in u}
    return sorted(u for u in uris if "#" in u or u not in laws_with_para)


def _iso(day, month_word, year):
    """Swedish 'den 25 juni 2013' parts -> ISO '2013-06-25', or None if the month
    word is not a month or the year is missing."""
    month = MONTHS.get(month_word.lower())
    if month and year:
        return "%s-%02d-%02d" % (year, month, int(day))
    return None


# --------------------------------------------------------------------------
# body: page paragraphs -> typed blocks
# --------------------------------------------------------------------------

def _rank_rubriker(blocks, start):
    """Give every rubrik of the operative body its depth, in place.

    The depth is the rank of the heading's font size among the sizes the body's
    *other* headings use, largest first, which is how a template's own nesting
    survives without anyone naming its point sizes. Level 1 is the kapitel
    heading, so a chaptered föreskrift's rubriker start at 2 -- without that
    every heading of a chaptered document was level 1 and its whole table of
    contents read flat, chapter and subheading side by side (all 1 370 chaptered
    regulations in the corpus).

    Ranked over `blocks[start:]`, not the whole PDF: the masthead sets the
    document title in the same size as a kapitel heading, and counting it pushed
    every real rubrik one level deeper than it is. A size the scheme does not
    know (a heading found by its number rather than its font, or a scanned PDF
    with no font at all) takes the deepest level in use."""
    body = blocks[start:]
    has_kapitel = any(b.kind == "kapitel" for b in body)
    sizes = sorted({b.size for b in body if b.kind == "rubrik" and b.size},
                   reverse=True)
    for b in body:
        if b.kind == "rubrik":
            rank = sizes.index(b.size) if b.size in sizes \
                else max(len(sizes) - 1, 0)
            b.level = (2 if has_kapitel else 1) + rank


def classify(paras, pageno):
    """A page's paragraphs -> föreskrift blocks. Structural markers are read from
    the text (``N §`` / ``N kap.`` at the block start), so the classification
    survives a scanned PDF with no font; bold and short length back up an
    *unnumbered* heading (``Definitioner``)."""
    out = []
    for p in paras:
        text = p.text
        mk, mp = RE_KAP_MARK.match(text), RE_PARA_MARK.match(text)
        if mk:
            out.append(Block("kapitel", text, pageno, num=mk.group(1), size=p.size))
        elif mp:
            out.append(Block("paragraf", text, pageno,
                             num=re.sub(r"\s+", "", mp.group(1)), size=p.size))
        elif (p.bold or RE_RUBRIK_NUM.match(text)) and len(text) < 120 \
                and RE_HAS_LETTER.search(text) and not RE_LIST_ITEM.match(text):
            m = RE_RUBRIK_NUM.match(text)
            out.append(Block("rubrik", text, pageno, size=p.size,
                             num=m.group(1) if m else None))
        else:
            out.append(Block("stycke", text, pageno, size=p.size))
    return out


# a bullet list the reflow glued into one paragraph. Poppler sets the bullet as
# its own run, so the character survives extraction and the item boundaries with
# it -- 7 688 blocks of the corpus carry at least one. Only the bullet is split
# on: an en dash is Swedish punctuation as often as it is a list marker, and a
# leading "1." is already the shape `RE_LIST_ITEM` guards headings against.
# Both characters are real: an agency that sets its bullets in Symbol prints
# U+F0B7, and SKSFS 2014:7 uses it 90 times with not one U+2022 -- tested for
# with the pattern itself, since a guard naming only U+2022 left that document
# (and DVFS 2014:19's six Symbol bullets, mixed in with 141 ordinary ones) with
# every item glued into running text.
RE_BULLET = re.compile(r"\s*[•\uf0b7]\s*")


def _split_bullets(block):
    """`block` as itself, or as the intro stycke plus the ``lista`` its text runs
    into. The lead-in keeps its own block ("Ledningens utbildning bör omfatta"),
    since it is the sentence the items complete."""
    if block.kind != "stycke" or not RE_BULLET.search(block.text):
        return [block]
    lead, *items = RE_BULLET.split(block.text)
    items = [i.strip() for i in items if i.strip()]
    if not items:
        return [block]
    out = [Block("stycke", lead.strip(), block.page, size=block.size)] \
        if lead.strip() else []
    return out + [Block("lista", "", block.page, size=block.size,
                        children=[Block("punkt", i, block.page, size=block.size)
                                  for i in items])]


# the heading a föreskrift sets over the advisory text under a paragraf. It is
# the whole line, and the documents themselves state the status it marks:
# "Allmänna råd har en annan juridisk status än föreskrifter. De är inte
# tvingande." The optional "till …" names the provision the råd explains
# ("Allmänna råd till 2 kap. 1 § andra stycket häkteslagen (2010:611)"), which
# is the heading's own words and is kept as the section's label.
RE_ALLMANNA_RAD = re.compile(r"^Allmänn[at]\s+råd(?:\s+till\s+.+)?$", re.IGNORECASE)
# the rule of underscores a föreskrift draws above its closing block, and the
# sentence boundary the closing clause starts after
RE_SLUTRULE = re.compile(r"_{5,}")
RE_SENTENCE_END = re.compile(r"[.!?]\s+")


def _closing_split(text):
    """`text` cut into (what still belongs to the råd, the closing matter), the
    second empty where the block carries none.

    The **closing matter** is the ikraftträdande/övergångsbestämmelser and the
    signature that follow the operative body. A råd cannot reach past the body
    it explains: without this cut it did, and the page then set binding text
    inside the advisory box under a label saying it is not binding -- the exact
    inverse of what the section is for (TFS 2009:2, KVFS 2021:2, RPSFS 2011:12;
    ~180 regulations print a råd in that position).

    Cutting inside the block, not moving the whole block, because the reflow
    glues the råd's last paragraph to the clause that follows it: KVFS 2021:2
    prints "… Ett sådant behov föreligger normalt för en intagen som är dömd
    för sexualbrott … ___________ Dessa föreskrifter … träder i kraft den 1 maj
    2021." as one paragraph, and moving it out ended the råd mid-sentence while
    IAFFS 2025:5 -- whose råd is that one paragraph -- lost its råd entirely.

    Two signals: the rule of underscores drawn above the closing block, and a
    "träder i kraft" sentence whose subject is *this* document -- the same pair
    `ikrafttradande_date` uses to tell the document's own date from one it
    quotes, so a råd that discusses another act's entry into force stays a råd.
    """
    cut = None
    if rule := RE_SLUTRULE.search(text):
        cut = rule.start()
    if (m := RE_IKRAFT.search(text)) and RE_IKRAFT_SUBJECT.search(text[:m.start()]):
        # the clause starts at its own sentence, not at the verb
        ends = [e.end() for e in RE_SENTENCE_END.finditer(text, 0, m.start())]
        start = ends[-1] if ends else 0
        cut = start if cut is None else min(cut, start)
    if cut is None:
        return text, ""
    return text[:cut].rstrip(), text[cut:].lstrip()


def _group_allmanna_rad(blocks):
    """The run with each allmänt råd folded into its own ``allmanna_rad`` block.

    A råd opens on the heading line and runs to the next structural marker --
    the next kapitel, paragraf or rubrik, a second råd, or the document's
    closing matter (:func:`_closes_rad`). That is the printed convention: a råd
    explains the § above it and the next § ends it. Left flat, its text read as
    further stycken of a binding paragraf (6 419 blocks across 895
    regulations)."""
    out = []
    for b in blocks:
        if RE_ALLMANNA_RAD.match(b.text.strip()):
            out.append(Block("allmanna_rad", b.text.strip(), b.page, size=b.size))
        elif out and out[-1].kind == "allmanna_rad" \
                and b.kind not in ("kapitel", "paragraf", "rubrik"):
            head, tail = _closing_split(b.text)
            if not tail:
                # the common case, and the only one a container block can take:
                # a lista's or tabell's own text is empty and its content hangs
                # in `children`, so it must pass through whole
                out[-1].children.append(b)
            else:
                if head:
                    out[-1].children.append(
                        Block(b.kind, head, b.page, size=b.size))
                out.append(Block("stycke", tail, b.page, size=b.size))
        else:
            out.append(b)
    # a heading whose råd turned out to be empty (the page broke under it) is
    # not a section -- keep the text rather than dropping it (rule:fail-fast)
    return [Block("rubrik", b.text, b.page, size=b.size)
            if b.kind == "allmanna_rad" and not b.children else b
            for b in out]


def _body_start(blocks):
    """The index where the operative body begins, i.e. past the masthead and the
    ingress (författningssamling name, utgivare, ISSN, the Utkom/beslutade/
    med-stöd-av lines). The first ``kapitel``/``paragraf`` marker is the reliable
    boundary; a föreskrift with no §§ at all (a short declarative, a förteckning)
    has none, so we fall back to the block just after the closing preamble verb
    ('… föreskriver följande'), and failing even that keep everything."""
    for i, b in enumerate(blocks):
        if b.kind in ("kapitel", "paragraf"):
            return i
    for i, b in enumerate(blocks):
        if RE_PREAMBLE_END.search(b.text):
            return i + 1
    return 0


# the masthead lines that are the *samling's* furniture rather than this
# document's own words: the samling title, its publisher and ISSN, the "Utkom
# från trycket" stamp and the FS number. The title sentence ends the masthead --
# a föreskrift's title is printed as the opening of a sentence the preamble
# completes ("… för väsentliga och viktiga verksamhetsutövare; beslutade den 15
# juni 2026.") -- so the ingress opens at the first block past it.
RE_MASTHEAD_LINE = re.compile(
    r"författningssamling|Utgivare|ISSN|Utkom\s+från\s+trycket"
    r"|^[A-ZÅÄÖ][A-ZÅÄÖ\-]*\s*\d{4}:\d+\s*$", re.IGNORECASE)


def _ingress_start(blocks, body_start):
    """The index where the föreskrift's **ingress** begins -- the preamble that
    states the day it was decided and the bemyndigande it rests on ("Myndigheten
    för civilt försvar föreskriver … med stöd av 38 § p. 5 …").

    That text is required by 18 b § författningssamlingsförordningen (1976:725)
    and it is the document's own opening words, but the parser used to drop it
    with the masthead: `_body_start` skips to the first ``N §``, so 11 538 of the
    corpus's 11 899 regulations published no preamble at all. It is found by
    walking *back* from the body to the last thing that cannot be ingress. Three
    signals stop the walk, measured over 250 random regulations:

      * a **rubrik** -- 150 of 229. The commonest by far: a heading before the
        first § ends the masthead, and everything after it is the document
        speaking.
      * a **masthead furniture line** -- 47. The samling name, the utgivare,
        the ISSN, the "Utkom från trycket" stamp, the FS number
        (:data:`RE_MASTHEAD_LINE`).
      * the **title sentence's semicolon** -- 30. A föreskrift's title is
        printed as the opening of a sentence the preamble completes ("… för
        väsentliga och viktiga verksamhetsutövare; beslutade den 15 juni 2026.").

    The remaining 2 reach the top without meeting any of them -- the thin-
    masthead population `role_declaration` documents, whose first ``N §`` sits
    inside a running head. Those blocks may well *be* an ingress, but nothing
    here separates them from the title, so they return `body_start` (no ingress)
    rather than publishing the title as a preamble stycke.
    """
    for i in range(body_start - 1, -1, -1):
        if RE_MASTHEAD_LINE.search(blocks[i].text) or blocks[i].kind == "rubrik" \
                or blocks[i].text.rstrip().endswith(";"):
            return i + 1
    return body_start


def parse_body(pages, identifier):
    """All blocks of a föreskrift, page by page, masthead included (the caller
    reads metadata from the masthead, then drops it via :func:`_body_start`),
    plus the page-foot footnotes split off the body. The running header is the
    identifier (``FFFS 2013:10``), which the printed pages repeat, so
    ``page_paragraphs`` strips it.

    Three page-level shapes are read before the prose reflow, because each is
    lost once the lines are folded into paragraphs: the notes under a page's
    footnote rule, the two-column ordförklaringar table, and (after the reflow)
    the bullet lists and allmänna råd the reflow glues into one stycke."""
    blocks, notes = [], []
    for pageno, lines in pages:
        body_lines, page_notes = ruled_footnotes(lines)
        notes += page_notes
        for kind, th_or_lines, rows in tabell.split_two_column(body_lines):
            if kind == "tabell":
                blocks.append(Block("tabell", "", pageno, rows=list(rows),
                                    th=bool(th_or_lines)))
                continue
            for block in classify(page_paragraphs(th_or_lines, identifier, pageno),
                                  pageno):
                blocks += _split_bullets(block)
    blocks = _group_allmanna_rad(tabell.merge_continued(blocks))
    _rank_rubriker(blocks, _body_start(blocks))
    return blocks, notes


# --------------------------------------------------------------------------
# metadata: the masthead facts the model carries
# --------------------------------------------------------------------------

def _first_date(rx, text):
    m = rx.search(text)
    return _iso(*m.groups()) if m else None


def extract_publisher(masthead):
    """The issuing agency, read from the PDF masthead -- the one place the *real*
    issuer is knowable (the harvest label is only the current custodian, so an
    older MSBFS number may in truth name Statens räddningsverk, not MSB, and an
    inherited SÄIFS/SRVFS number its own defunct agency).

    Tries, in order: the ``Utgivare:`` line's agency, the "<agency>s
    författningssamling" masthead title, then the föreskrift's own
    "<agency>s föreskrifter" name (see the ``RE_*`` patterns above). ``None`` when
    the masthead yields none of them, so the caller keeps the harvest-time label."""
    flat = re.sub(r"\s+", " ", masthead)       # two-column extraction breaks lines
    for rx in (RE_UTGIVARE, RE_FS_SERIES, RE_FS_TITLE):
        m = rx.search(flat)
        if m:
            return m.group(1).strip(" .,-")
    return None


def ikrafttradande_date(text, declaration):
    """The date *this* föreskrift entered into force, chosen among every
    "träder i kraft den …" the document prints. ``declaration`` is where the
    document says what it is (see :func:`role_declaration`).

    Three such sentences can appear in one document and only one is the
    document's own. Sentences about somebody else's regulation are dropped first
    (:data:`RE_IKRAFT_SUBJECT`). Of what remains, an ändringsförfattning's own
    provision is the *last* -- the text it reprints from the base regulation
    comes before its own transitional block -- and a grundförfattning's is the
    *first*, since a printed grundförfattning carries the transitional blocks of
    the amendments made to it after its own. Reading the first one always (which
    this did until 2026-08-08) gave 328 föreskrifter an entry into force before
    the day they were decided, SJÖFS 2006:39 among them.

    Falls back to the unfiltered sentences when the document phrases its
    provision with a subject the census did not see (199 of 10 830), so a
    recognised date is never lost to the filter."""
    hits = list(RE_IKRAFT.finditer(text))
    own = [m for m in hits if RE_IKRAFT_SUBJECT.search(text[:m.start()])] or hits
    if not own:
        return None
    amends = ((RE_ANDRING.search(declaration) or RE_AMENDING_FORMULA.search(declaration))
              and not RE_KONSOLIDERAD_MASTHEAD.search(declaration))
    return _iso(*(own[-1] if amends else own[0]).groups())


def role_declaration(masthead, harvest_title):
    """Where to look for the document's declaration that it amends another one.

    Its own masthead is the authority, but 259 föreskrifter have next to none:
    `_body_start` finds its first ``N §`` inside a running head, leaving
    `masthead` empty or a fragment like "GRUNDLÄGGANDE BESTÄMMELSER" (SJVFS
    prints one on page 1). For 38 of those the harvest title is the only
    surviving copy of the "om ändring i …" phrase, so both are searched. The
    harvest title cannot be trusted *alone* -- it is link chrome often enough
    that `clean_title` exists to throw it away -- but as a second place to find
    a declaration the masthead lost, it costs nothing."""
    return f"{masthead} {harvest_title or ''}"


def extract_metadata(text, declaration, parser):
    """Best-effort masthead facts from the regulation's plain text. ``text`` is
    the whole document (ikraftträdande sits at the end, the rest up front);
    ``declaration`` is what the document says it *is*, per
    :func:`role_declaration`, which decides which of its ikraftträdande
    sentences is its own."""
    meta = {
        "beslutsdatum": _first_date(RE_BESLUTAD, text),
        "utkomFranTryck": _first_date(RE_UTKOM, text),
        "ikrafttradandedatum": ikrafttradande_date(text, declaration),
        "bemyndigande": [], "genomfor": [], "upphaver": [], "andrar": [],
    }
    # bemyndigande: the SFS paragrafer named in the "med stöd av …" clause
    stod = stodav_clause(text)
    if stod:
        meta["bemyndigande"] = _dedupe_bemyndigande(
            {r.uri for r in parser.parse_text(stod, context={})
             if r.predicate.endswith("references")})
    # genomför: the directive each "Jfr … direktiv …" footnote points to (its
    # first directive ref; later ones in the clause are amended, not implemented)
    genomfor = set()
    for jfr in RE_JFR.findall(text):
        dirs = [r for r in parser.parse_text(jfr, context={})
                if RE_DIREKTIV_CELEX.search(r.uri)]
        if dirs:
            genomfor.add(min(dirs, key=lambda r: r.start).uri)
    meta["genomfor"] = sorted(genomfor)
    # upphäver: regulations an "ersätter/upphäver(s) …" clause replaces --
    # every clause, since the first "upphävs" in a document is often a bare
    # provision repeal ("5 § upphävs") that names no regulation at all.
    # _fs_key, not lower(): 'ÅFS' must mint aafs/…, never a dangling åfs/…
    meta["upphaver"] = sorted({regulation_uri(_fs_key(fs), y, str(int(n)))
                               for m in RE_ERSATTER.finditer(text)
                               for fs, y, n in RE_FS_REF.findall(m.group(1))})
    return meta


# --------------------------------------------------------------------------
# record -> Regulation -> artifact
# --------------------------------------------------------------------------

def _node(block, parser):
    """One :class:`Block` -> its artifact node dict, its text scanned for SFS/EU
    citations and spliced into inline runs. A ``tabell`` carries cells instead of
    text; a container (``lista``, ``allmanna_rad``) carries its children."""
    def scan(text):
        return interleave(text, parser.parse_text(text, context={}))

    if block.kind == "tabell":
        # `th` sits on the header *row*, the way a förarbete lydelse table
        # carries it, so the shared row renderer needs no table-level state
        rows = [dict({"type": "rad", "cells": [scan(c) for c in row]},
                     **({"th": True} if i == 0 and block.th else {}))
                for i, row in enumerate(block.rows or [])]
        return {"type": "tabell", "page": block.page, "children": rows}
    node = {"type": block.kind, "page": block.page}
    if block.children:
        node["children"] = [_node(c, parser) for c in block.children]
    # a råd carries both: its children and its own printed heading. The heading
    # goes under `text` like any other node's words, not a key of its own --
    # `lib.text` collects `text`, and the whole document's plain text is what
    # feeds the search index; and the heading names a provision ("Allmänna råd
    # till 2 kap. 1 § häkteslagen (2010:611)"), so it is scanned for citations.
    if block.kind == "allmanna_rad" or not block.children:
        node["text"] = scan(block.text)
    if block.num:
        node["num"] = block.num
    if block.level:
        node["level"] = block.level
    return node


def _structure(blocks, parser):
    """A :class:`Block` run -> the nested ``structure`` list."""
    return nest([_node(b, parser) for b in blocks])


def _full_text(blocks):
    """Every block's printed text in document order, containers walked and table
    cells included -- the metadata scan reads dates and the bemyndigande clause
    off this, and an ikraftträdande sentence must not go missing because the page
    set it inside an allmänt råd or a table cell."""
    out = []
    for b in blocks:
        out.append(b.text)
        out += [c for row in b.rows or [] for c in row]
        out.append(_full_text(b.children))
    return "\n".join(t for t in out if t)


# The title a föreskrift prints in its masthead: '<Agency>s föreskrifter om …;
# beslutade den …' (or 'Föreskrifter om ändring i …'). It is read in three
# explicit steps rather than by one regex over the whole masthead, because the
# masthead is the part of the page text extraction mangles worst -- it is set in
# two columns, so the "Utkom från trycket / den 4 februari 2022" block lands
# *inside* the title sentence, between its semicolon and the "beslutade" clause.
#
# The type word is what anchors the read. Everything before it back to the last
# the standing masthead text (the ISSN, the publisher line, the samling's name,
# the FS number, a date) is the issuing agency's possessive; everything after it up
# to the semicolon or the beslutade/utfärdad clause is the subject.
RE_TITLE_TYPE = re.compile(
    r"\b(?:föreskrifter|föreskrift|allmänna\s+råd|allmänt\s+råd|kungörelse"
    r"|tillkännagivande)\b", re.IGNORECASE)
# What the masthead prints on every föreskrift, whatever it says: the
# samling's name, the ISSN, the utgivare, "Utkom från trycket", the FS number,
# the dates. Only the title varies, so everything here is removed to leave it.
# It is *removed* rather than treated as a boundary,
# because the masthead's second column lands in the middle of the title
# sentence, not beside it: "Skolverkets föreskrifter Utkom från trycket den 21
# mars 2012 om betygskatalog för vuxenutbildning" is one block of extracted
# text. Cutting at that text would keep "Skolverkets föreskrifter" and lose
# the subject; deleting it rejoins the sentence that was printed.
RE_MASTHEAD_BOILERPLATE = re.compile(
    # the samling's own name, possessive included: dropping only the head word
    # leaves it orphaned in front of the title ("Statens skolverks" +
    # "Skolverkets föreskrifter om …")
    r"(?:[A-ZÅÄÖ][\wåäöÅÄÖ-]*(?:\s+[\wåäöÅÄÖ-]+){0,3}\s+)?författningssamling\w*"
    r"|ISSN\s*[\d\s-]{4,}|Utgivare:\s*|Utkom\s+från\s+trycket"
    # the agency's own contact block, which several samlingar print in the
    # masthead's second column ("Box 7821, 103 97 Stockholm, Sverige, www.fi.se")
    r"|\bwww\.[\w.-]+|\bBox\s+\d+|\b\d{3}\s?\d{2}\s+[A-ZÅÄÖ][a-zåäö]+,?"
    r"|\bTfn\b[\s\d-]*|\bSverige\b,?"
    r"|Publicerings?datum|Publicerade?\s+den|\b[A-ZÅÄÖ]{2,}-?FS\b|\b\d{4}:\d+\b"
    r"|\b(?:den\s+)?\d{1,2}\s+(?:%s)(?:\s+\d{4})?|\bnr\s+\d+"
    % "|".join(MONTHS), re.IGNORECASE)
# a word the removal left doubled ("Kriminalvårdens
# [författningssamling] Kriminalvårdens föreskrifter …")
RE_TITLE_DOUBLED = re.compile(r"\b(\w{3,})(\s+\1)+\b", re.IGNORECASE)
# a parenthesis emptied by the removal above: an ändringsförfattning's title
# names the regulation it amends by number ("Läkemedelsverkets föreskrifter
# (LVFS 1997:13) om …"), which is the one place the designation belongs in a
# title, so those spans are held back from the removal and restored after
RE_TITLE_PARENS = re.compile(r"\([^()]{0,80}\)")
# the second column's own headers, which land wherever the first column's line
# broke -- including inside a parenthesis
RE_MASTHEAD_COLUMN = re.compile(
    r"Utkom\s+från\s+trycket|Publicerings?datum|Publicerade?\s+den"
    r"|\b(?:den\s+)?\d{1,2}\s+(?:%s)(?:\s+\d{4})?" % "|".join(MONTHS),
    re.IGNORECASE)
# where the title stops: its own semicolon, or the clause that follows it
RE_TITLE_END = re.compile(r";|\bbeslutad|\butfärdad|\bbeslutat\b", re.IGNORECASE)
# link chrome a harvest title trails off into ('(pdf, 63 kB)', 'Pdf, 278.1 kB,
# öppnas i nytt fönster.', a bare '.pdf') -- from the pdf token to the end
RE_TITLE_CHROME = re.compile(r"\s*[,(]?\s*(?:pdf|\.pdf)\b.*$",
                             re.IGNORECASE | re.DOTALL)
# words that make a harvest "title" a role label, not a title
RE_TITLE_BOILERPLATE = re.compile(
    r"\b(?:grundförfattning|ändringsförfattning|konsoliderad(?:\s+version)?|"
    r"öppnas|nytt\s+fönster)\b", re.IGNORECASE)


def clean_title(raw, identifier):
    """A harvest title stripped of link chrome and its own-designation prefix,
    or None when nothing title-like remains ('.pdf', 'KKVFS 2025:1',
    'Grundförfattning (MDFFS 2019:1)') -- many harvests hand us the PDF link's
    text, which is file chrome rather than a title (F7). None sends the
    caller to the PDF's own rubric (title_from_body)."""
    t = RE_TITLE_CHROME.sub("", raw or "").strip()
    if identifier:
        t = re.sub(r"^%s\s*[-–—:]*\s*" % re.escape(identifier), "", t).strip()
    # what remains once designations, numbers and role words go: a title has
    # prose left, chrome does not
    probe = re.sub(r"[\d\s:/().,–—-]+", "",
                   RE_TITLE_BOILERPLATE.sub("", RE_FS_REF.sub("", t)))
    return t if len(probe) >= 8 else None


TITLE_MAX = 300      # a subject longer than this is extraction running on
# lower-case words that join an agency's name ("Myndigheten *för* civilt
# försvars", "Post- *och* telestyrelsens")
_NAME_JOINERS = {"för", "och", "av", "i", "med", "samt", "vid", "om"}


def _strip_boilerplate(masthead):
    """The masthead with its standing text deleted and the sentence rejoined,
    parenthesised references left intact."""
    # a held parenthesis keeps its FS number but not the column header the
    # second column dropped into it ("(LVFS Utkom från trycket 2006:16)")
    held = [" ".join(RE_MASTHEAD_COLUMN.sub(" ", p).split())
            for p in RE_TITLE_PARENS.findall(masthead)]
    masked = RE_TITLE_PARENS.sub("\x00", masthead)
    cleaned = RE_TITLE_DOUBLED.sub(
        r"\1", " ".join(RE_MASTHEAD_BOILERPLATE.sub(" ", masked).split()))
    for paren in held:
        cleaned = cleaned.replace("\x00", paren, 1)
    return " ".join(cleaned.split())


def _agency_possessive(before):
    """The issuing agency's possessive immediately preceding the type word, as a
    start offset into `before` (its length when there is none -- 'Föreskrifter om
    ändring i …' names no agency).

    Walked backwards over tokens rather than matched, because the token that
    ends the name is the only reliable landmark: it is the possessive '-s'
    ('Säkerhetspolisens', 'Myndigheten för civilt försvars'). From there the name
    runs back through its lower-case joiners to the capitalised word that begins
    it. Two adjacent capitalised words are a boundary, not a name -- that is what
    keeps the utgivare off the front of the title, the masthead's two columns
    having landed 'Utgivare: Gunilla Hedwall' directly before
    'Säkerhetspolisens föreskrifter om säkerhetsskydd'."""
    tokens = list(re.finditer(r"\S+", before))
    if not tokens or not tokens[-1].group().endswith(("s", "s:")):
        return len(before)
    start = len(tokens) - 1
    while start > 0:
        prev, cur = tokens[start - 1].group(), tokens[start].group()
        if not prev[:1].isalpha() or prev.endswith((":", ".")):
            break                       # standing masthead text, not a name
        if prev.lower() in _NAME_JOINERS or prev[:1].islower():
            start -= 1
            continue
        # a capitalised word: part of the name only where the word it precedes
        # is not itself capitalised ("Statens jordbruksverks", not
        # "Hedwall Säkerhetspolisens")
        if cur[:1].isupper():
            break
        start -= 1
        break
    return tokens[start].start()


# The shortest repeated head worth believing in: below this, a title that
# genuinely opens the way it continues ("Föreskrifter om föreskrifter…") would
# be truncated. Every real case runs to 40+ characters.
UNDOUBLE_MIN = 20


def undouble(title):
    """A title printed twice, reduced to the copy that is whole.

    Five föreskrifter carry their masthead title twice, for two reasons, and
    both land here as the same shape: a truncated head glued to the front of the
    complete title. SJÖFS 2005:25 prints its title once as a page header and
    again in the ingress, and the scan above runs from the first straight into
    the second; the RNFS 2013:1 PDF has two text layers, so *every* line of its
    first page is doubled ("ISSN 1401-7288ISSN 1401-7288").

    The split is found from the longest candidate down, so a title that repeats
    a short phrase inside itself keeps it -- only a head that begins what
    follows it is a second printing."""
    for i in range(len(title) - UNDOUBLE_MIN, UNDOUBLE_MIN - 1, -1):
        head, rest = title[:i].strip(), title[i:].strip()
        if len(head) >= UNDOUBLE_MIN and rest.startswith(head):
            return rest
    return title


def title_from_masthead(blocks, start):
    """The document's own title, read from the printed masthead -- '<Agency>s
    föreskrifter om …' up to the semicolon or the beslutade clause -- or None
    where the masthead carries no such phrase.

    The masthead is `blocks[:start]`, not the operative body: this used to search
    the first blocks *past* `_body_start`, where the title has already been left
    behind, so it found one only for the föreskrifter whose body happens to
    repeat it. The blocks are joined, and the standing masthead text deleted,
    before matching: two-column extraction interleaves the columns, so neither
    block holds the whole sentence and the second column lands inside it."""
    masthead = _strip_boilerplate(" ".join(_full_text(blocks[:start]).split()))
    for word in RE_TITLE_TYPE.finditer(masthead):
        head = _agency_possessive(masthead[:word.start()].rstrip())
        rest = masthead[word.end():word.end() + TITLE_MAX]
        stop = RE_TITLE_END.search(rest)
        title = (masthead[head:word.end()]
                 + (rest[:stop.start()] if stop else rest)).strip(" ;,.-")
        # a bare type word with no subject is the samling's own name or a
        # running header, not this document's title
        if stop and len(title) > len(word.group()) + 4:
            return undouble(" ".join(title.split()))
    return None


def parse_pdf(path, identifier, parser, patch_key=None, harvest_title=None):
    """One föreskrift PDF -> (structure tree, its metadata dict, its footnotes).
    Metadata is read
    from the whole text (the masthead up front, ikraftträdande at the end); the
    structure is built from the operative body only, the masthead dropped.
    `patch_key=(source, basefile)` patches the pdftohtml XML before extraction."""
    blocks, notes = parse_body(pdf_pages(path, patch_key), identifier)
    start = _body_start(blocks)
    masthead = _full_text(blocks[:start])
    # the notes are read for metadata with the body: the "Jfr … direktiv" clause
    # that names what a föreskrift genomför is *printed as* a page-foot note, so
    # a scan of the blocks alone would lose the very relation it exists to find
    meta = extract_metadata("\n".join([_full_text(blocks)]
                                      + [text for _mark, text in notes]),
                            role_declaration(masthead, harvest_title), parser)
    # the publisher is a masthead fact only (a body citation to another agency's
    # föreskrifter must not be mistaken for it), so read it from the masthead blocks
    meta["publisher"] = extract_publisher(masthead or _full_text(blocks))
    # the body's own rubric, for records whose harvest title is link chrome (F7)
    meta["title"] = title_from_masthead(blocks, start)
    ingress = _ingress_start(blocks, start)
    body = ([Block("ingress", "", blocks[ingress].page,
                   children=blocks[ingress:start])] if ingress < start else []) \
        + blocks[start:]
    return _structure(body, parser), meta, footnote_nodes(notes, parser)


# printed designation (lowercased, hyphens/spaces dropped, Swedish vowels
# kept) -> registered samling slug, for series whose slug is not the naive
# transliteration: 'ÅFS' -> aafs (afs is Arbetsmiljöverkets samling) and its
# predecessor 'RÅFS' -> raafs (rafs is Riksarkivets RA-FS)
_DESIGNATION_SLUGS = {
    **{a.designation.lower().replace("-", "").replace(" ", ""): fs
       for fs, a in REGISTRY.items() if a.designation},
    **{d.lower(): fs for d, (fs, _) in AAFS_SERIES.items()},
}


def _fs_key(designation):
    """Fold an FS designation to its slug form for matching -- lowercase, drop
    the hyphen/spaces, then let the registry's own designation->slug rows
    override the åäö transliteration ('ÅFS' folds to ``aafs``, never ``afs``;
    'ELSÄK-FS' matches the agency's ``elsakfs`` slug either way)."""
    key = designation.lower().replace("-", "").replace(" ", "")
    return _DESIGNATION_SLUGS.get(key, fold_swedish(key))


def masthead_amendments(masthead, fs, base_ars, base_lop):
    """Every ändringsförfattning of this fs a konsoliderad PDF's masthead lists
    ('Ändringar: FFFS 2014:29, … FFFS 2026:6') -> chronologically sorted
    (printed designation, year, nr) triples, the base regulation's own number
    excluded. This is amendment evidence in its own right: agencies' landing
    pages list amendments incompletely, the consolidation masthead names
    exactly the ones folded in."""
    base = (base_ars, str(int(base_lop)))
    seen = {}
    for f, y, n in RE_FS_REF.findall(masthead):
        if _fs_key(f) == _fs_key(fs) and (y, str(int(n))) != base:
            seen.setdefault((int(y), int(n)), f)
    return [(f, str(y), str(n)) for (y, n), f in sorted(seen.items())]


def konsoliderad_tom(masthead, fs, base_ars, base_lop):
    """The most recent ändringsförfattning a konsoliderad version folds in -> its
    föreskrift uri, or None: the highest-numbered masthead reference to this fs.
    This is the one fact that pins a consolidation -- not the (irrelevant)
    'senast uppdaterad' date."""
    refs = masthead_amendments(masthead, fs, base_ars, base_lop)
    if not refs:
        return None
    _, y, n = refs[-1]
    return regulation_uri(fs, y, n)


def parse_consolidation(path, identifier, fs, base_ars, base_lop, parser):
    """A konsoliderad PDF -> (structure tree, its footnotes, konsolideradTom uri,
    masthead amendment triples). The amendment list sits in the masthead (the
    blocks before the body), so it is read there.

    The notes are carried rather than dropped: `parse_body` *cuts* the below-rule
    lines out of the body, so discarding them here would put that text in no
    artifact key at all. No konsoliderad PDF in the corpus prints one today
    (0 of 150 sampled), which is why the discard went unnoticed."""
    blocks, notes = parse_body(pdf_pages(path), identifier)
    start = _body_start(blocks)
    masthead = _full_text(blocks[:start]) or _full_text(blocks)
    return (_structure(blocks[start:], parser), footnote_nodes(notes, parser),
            konsoliderad_tom(masthead, fs, base_ars, base_lop),
            masthead_amendments(masthead, fs, base_ars, base_lop))


# a Socialstyrelsen "Senaste version av …" page lists its incorporated
# amendments on one preamble line -- "Ändrad: t.o.m. HSLF-FS 2017:27" or just
# "Ändrad: SOSFS 2014:9" -- each ref under its own samling designation (a
# SOSFS base consolidated t.o.m. an HSLF-FS amendment is the 2015 series
# transition, not an error)
RE_ANDRAD = re.compile(r"Ändrad:")
# the boilerplate lines every such page opens with (page metadata, not body)
RE_HTML_PREAMBLE = re.compile(r"(?:Observera att|Senaste lydelse:)")


def parse_consolidation_html(path, parser):
    """A Socialstyrelsen konsoliderad HTML page (the frozen SOSFS/HSLF-FS
    ``konsolidering`` corpus; the old site rendered the consolidated fulltext
    on-page rather than as a PDF) -> the same (structure, footnotes,
    konsolideradTom, masthead refs) contract as :func:`parse_consolidation`. The page is
    regular: ``<main>`` holds an h1 page title, three preamble lines, then
    h2/h3 headings over ``p``/``li`` body text -- headings classify as bold
    paragraphs, everything else by its textual ``N §``/``N kap.`` markers."""
    soup = BeautifulSoup(path.read_text("utf-8"), "html.parser")
    main = soup.select_one("main")
    if main is None:
        raise ValueError("no <main> content in konsoliderad page %s" % path)
    paras, refs = [], []
    for el in main.find_all(["h2", "h3", "h4", "p", "li"]):
        if el.find_parent(["p", "li"]):
            continue                       # a p/li nested in a li: parent has it
        text = " ".join(el.get_text(" ", strip=True).split())
        if not text:
            continue
        if RE_ANDRAD.match(text):
            refs += [(f, y, str(int(n))) for f, y, n in RE_FS_REF.findall(text)]
            continue
        if RE_HTML_PREAMBLE.match(text):
            continue
        paras.append(Para(text, bold=el.name not in ("p", "li")))
    blocks = classify(paras, None)
    _rank_rubriker(blocks, _body_start(blocks))
    refs.sort(key=lambda r: (int(r[1]), int(r[2])))
    tom = (regulation_uri(_fs_key(refs[-1][0]), refs[-1][1], refs[-1][2])
           if refs else None)
    # an HTML consolidation has no page-foot rule, so it carries no notes
    return _structure(blocks[_body_start(blocks):], parser), [], tom, refs


def andrar_target(title, fs, self_uri):
    """The base regulation an ändringsförfattning's harvest title names, or None
    when the title declares no ändring. The direct object is the *first* ref
    after the ändring phrase (a chained "… (ÅFS 2006:3) om ändring i … (ÅFS
    2005:5)" amends 2006:3, which in turn amends 2005:5); the record's own
    designation, when the title restates it, is never the target."""
    m = RE_ANDRING.search(title or "")
    if not m:
        return None
    rest = title[m.end():]
    for f, y, n in RE_FS_REF.findall(rest):
        uri = regulation_uri(_fs_key(f), y, str(int(n)))
        if uri != self_uri:
            return uri
    for y, n in RE_BARE_OWN_REF.findall(rest):
        uri = regulation_uri(fs, y, str(int(n)))
        if uri != self_uri:
            return uri
    return None


def amendment_uri(identifier):
    """Mint an ändringsförfattning's uri from its printed designation
    ("ELSÄK-FS 2026:27" -> https://lagen.nu/elsakfs/2026:27), or None when the
    harvest couldn't read one. Minted from the identifier's *own* FS code --
    an RPSFS base amended by PMFS acts is a normal mixed-prefix graph."""
    m = RE_FS_REF.search(identifier or "")
    if not m:
        return None
    return regulation_uri(_fs_key(m.group(1)), m.group(2), str(int(m.group(3))))


def body_path(root, fs, entry):
    """Absolute path of a body PDF a record's ``files`` entry references, stored
    under ``root/fs/<name>``. `fs` comes off the basefile and `name` off the
    harvested record, so neither is trusted to stay under `root`."""
    return Path(root) / confine(Path(fs) / entry["name"],
                                "%s/%s" % (fs, entry["name"]), str(root))


def parse_record(record, root):
    """A harvested record (``<slug>.json``) -> a parsed :class:`Regulation`.
    The regulation body comes from the downloaded ``regulation`` PDF -- when a
    record has none (`files["regulation"]` is `None`; every classifier still
    hangs a landing page's PDFs onto a `regulation`/`consolidation`/`amendment`
    role, but not every entry has to fill each role), the base `Regulation`
    keeps an empty `structure` and only its `consolidations` carry a parsed
    body. Each downloaded consolidation PDF is parsed into its own
    ``structure``."""
    fs, basefile = record["fs"], record["basefile"]
    arsutgava, lopnummer = basefile.split("/", 1)[1].split(":", 1)
    files = record.get("files", {})
    # Dated by årsutgåva, which is all that is known before the body is read: a
    # föreskrift's beslutsdatum is extracted from page 1 of the PDF (`_meta`),
    # which happens below with this parser already in hand. The årsutgåva is the
    # right granularity anyway -- `approximate_date` places a bare year at its
    # middle, and no act a föreskrift cites changes name twice within one year.
    parser = sfs_parser("foreskrift", PARSE_TYPES,
                        written=approximate_date(arsutgava))

    reg_file = files.get("regulation") or None
    structure, meta, notes = [], {}, []
    if reg_file:
        structure, meta, notes = parse_pdf(
            body_path(root, fs, reg_file), record["identifier"], parser,
            ("foreskrift", basefile), record.get("title"))

    # the PDF masthead is the authoritative issuer; the harvest label (the current
    # custodian agency) is only the fallback when the PDF names none
    publisher = meta.pop("publisher", None) or record.get("publisher")
    # a real harvest title wins (cleaned of link chrome); a chrome-only or
    # missing one falls back to the PDF body's own rubric (F7)
    body_title = meta.pop("title", None)
    title = clean_title(record.get("title"), record["identifier"]) or body_title
    reg = Regulation(
        uri=regulation_uri(fs, arsutgava, lopnummer),
        identifier=record["identifier"], fs=fs,
        arsutgava=arsutgava, lopnummer=lopnummer,
        title=title, publisher=publisher,
        source_url=record.get("url"),
        structure=structure, footnotes=notes, **meta)
    # the resolved title, not the raw harvest one: for a chrome-titled record
    # the ändring declaration lives in the body rubric just adopted above
    if target := andrar_target(title or "", fs, reg.uri):
        reg.andrar = [target]
    # an "ersätter/upphäver …" clause restating the document's own designation
    # must not claim the regulation replaces itself (LIVSFS 2022:4 does this)
    reg.upphaver = [u for u in reg.upphaver if u != reg.uri]

    for am in files.get("amendment", []):
        # the harvest record always carries both keys (harvest.py normalizes);
        # identifier may be None (unreadable link text) -- the url still pins it
        reg.amendments.append(Amendment(
            identifier=am["identifier"], uri=amendment_uri(am["identifier"]),
            url=am["url"], beslutsdatum=None))
    # A consolidation is a snapshot at its *own* cutoff, not at the base act's
    # year, and the two are a median 8 years apart (max 33). Dating one by the
    # base årsutgåva moved 156 links in consolidation bodies onto acts already
    # repealed by the time the consolidated text was written -- into the very
    # text `presented_consolidation` shows as the reading text. The cutoff it
    # states (`konsolideradTom`) is produced by the parse below, so it cannot
    # date the parser that produces it; the newest amendment on the record is
    # in hand here and agrees with it in 1,043 of 1,127 consolidations.
    cons_written = approximate_date(max(
        (m.group(1) for a in files.get("amendment", [])
         if (m := RE_FS_YEAR.search(a.get("identifier") or ""))), default=""))
    known = {a.uri for a in reg.amendments if a.uri}
    for cons in files.get("consolidation", []):
        if cons.get("name"):
            path = Path(root) / fs / cons["name"]
            cstruct, cnotes, tom, refs = (
                parse_consolidation_html(
                    path, sfs_parser("foreskrift", PARSE_TYPES, written=cons_written))
                if path.suffix == ".html"
                else parse_consolidation(path, record["identifier"],
                                         fs, arsutgava, lopnummer,
                                         sfs_parser("foreskrift", PARSE_TYPES,
                                                    written=cons_written)))
            if any(c.konsolideradTom == tom and c.structure == cstruct
                   for c in reg.consolidations):
                continue          # the landing page listed the same PDF twice
            reg.consolidations.append(Consolidation(
                of=reg.uri, konsolideradTom=tom, url=cons.get("url"),
                structure=cstruct, footnotes=cnotes))
            # the masthead's amendment list is register evidence the landing
            # page often lacks -- fold the unlisted ones into the register
            # (each ref minted under its own printed samling: a SOSFS base
            # consolidated t.o.m. an HSLF-FS amendment crosses the series)
            for f, y, n in refs:
                uri = regulation_uri(_fs_key(f), y, n)
                if uri not in known:
                    known.add(uri)
                    reg.amendments.append(Amendment(
                        identifier="%s %s:%s" % (f, y, n), uri=uri))
    return reg
