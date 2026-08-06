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

from ..lib.lagrum import EULAGSTIFTNING, FORESKRIFT, LAGRUM, interleave, sfs_parser
from ..lib.pdftext import RE_KAP_MARK, RE_PARA_MARK, Para, page_paragraphs, pdf_pages
from ..lib.util import MONTHS, approximate_date
from .agencies import AAFS_SERIES, REGISTRY
from .model import Amendment, Consolidation, Regulation, regulation_uri
from .structure import nest

# a föreskrift cites SFS (the empowering law), EU directives (what it
# implements) and its siblings -- an agency's regulations cross-refer constantly
# ("Utöver denna föreskrift gäller MSBFS 2020:7"), and the metadata relations
# (upphäver/ändrar, RE_FS_REF below) only ever capture the masthead's, never one
# in the operative text. It does not cite case law or förarbeten.
PARSE_TYPES = [LAGRUM, EULAGSTIFTNING, FORESKRIFT]

RE_RUBRIK_NUM = re.compile(r"^(\d+(?:\.\d+)*)\s+\S")     # "2.1 Heading"
RE_LIST_ITEM = re.compile(r"^(?:\d+[.)]|[-–—•])\s")       # "1." / "– " list rows
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

def classify(paras):
    """A page's paragraphs -> föreskrift blocks. Structural markers are read from
    the text (``N §`` / ``N kap.`` at the block start), so the classification
    survives a scanned PDF with no font; bold and short length back up an
    *unnumbered* heading (``Definitioner``). Returns ``[(kind, text, num)]``."""
    out = []
    for p in paras:
        text = p.text
        mk, mp = RE_KAP_MARK.match(text), RE_PARA_MARK.match(text)
        if mk:
            out.append(("kapitel", text, mk.group(1)))
        elif mp:
            out.append(("paragraf", text, re.sub(r"\s+", "", mp.group(1))))
        elif (p.bold or RE_RUBRIK_NUM.match(text)) and len(text) < 120 \
                and not RE_LIST_ITEM.match(text):
            m = RE_RUBRIK_NUM.match(text)
            num = m.group(1) if m else None
            out.append(("rubrik", text, num))
        else:
            out.append(("stycke", text, None))
    return out


def _body_start(blocks):
    """The index where the operative body begins, i.e. past the masthead
    (författningssamling name, utgivare, ISSN, the Utkom/beslutade/med-stöd-av
    lines). The first ``kapitel``/``paragraf`` marker is the reliable boundary;
    a föreskrift with no §§ at all (a short declarative, a förteckning) has none,
    so we fall back to the block just after the closing preamble verb ('…
    föreskriver följande'), and failing even that keep everything."""
    for i, (kind, *_rest) in enumerate(blocks):
        if kind in ("kapitel", "paragraf"):
            return i
    for i, (_kind, text, *_rest) in enumerate(blocks):
        if RE_PREAMBLE_END.search(text):
            return i + 1
    return 0


def parse_body(pages, identifier):
    """All blocks of a föreskrift, page by page, masthead included (the caller
    reads metadata from the masthead, then drops it via :func:`_body_start`). The
    running header is the identifier (``FFFS 2013:10``), which the printed pages
    repeat, so ``page_paragraphs`` strips it. Returns ``[(kind, text, page, num)]``."""
    blocks = []
    for pageno, lines in pages:
        for kind, text, num in classify(page_paragraphs(lines, identifier, pageno)):
            blocks.append((kind, text, pageno, num))
    return blocks


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


def extract_metadata(text, parser):
    """Best-effort masthead facts from the regulation's plain text. ``text`` is
    the whole document (ikraftträdande sits at the end, the rest up front)."""
    meta = {
        "beslutsdatum": _first_date(RE_BESLUTAD, text),
        "utkomFranTryck": _first_date(RE_UTKOM, text),
        "ikrafttradandedatum": _first_date(RE_IKRAFT, text),
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

def _structure(blocks, parser):
    """Flat ``(kind, text, page, num)`` blocks -> the nested ``structure`` list,
    each block's text scanned for SFS/EU citations and spliced into inline runs."""
    dicts = []
    for kind, text, page, num in blocks:
        block = {"type": kind, "page": page,
                 "text": interleave(text, parser.parse_text(text, context={}))}
        if num:
            block["num"] = num
        dicts.append(block)
    return nest(dicts)


def _full_text(blocks):
    return "\n".join(text for _, text, _, _ in blocks)


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
            return " ".join(title.split())
    return None


def parse_pdf(path, identifier, parser, patch_key=None):
    """One föreskrift PDF -> (structure tree, its metadata dict). Metadata is read
    from the whole text (the masthead up front, ikraftträdande at the end); the
    structure is built from the operative body only, the masthead dropped.
    `patch_key=(source, basefile)` patches the pdftohtml XML before extraction."""
    blocks = parse_body(pdf_pages(path, patch_key), identifier)
    start = _body_start(blocks)
    meta = extract_metadata(_full_text(blocks), parser)
    # the publisher is a masthead fact only (a body citation to another agency's
    # föreskrifter must not be mistaken for it), so read it from the masthead blocks
    meta["publisher"] = extract_publisher(_full_text(blocks[:start]) or _full_text(blocks))
    # the body's own rubric, for records whose harvest title is link chrome (F7)
    meta["title"] = title_from_masthead(blocks, start)
    return _structure(blocks[start:], parser), meta


_FOLD_SWEDISH = str.maketrans("åäö", "aao")
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
    return _DESIGNATION_SLUGS.get(key, key.translate(_FOLD_SWEDISH))


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
    """A konsoliderad PDF -> (structure tree, konsolideradTom uri, masthead
    amendment triples). The amendment list sits in the masthead (the blocks
    before the body), so it is read there."""
    blocks = parse_body(pdf_pages(path), identifier)
    start = _body_start(blocks)
    masthead = _full_text(blocks[:start]) or _full_text(blocks)
    return (_structure(blocks[start:], parser),
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
    on-page rather than as a PDF) -> the same (structure, konsolideradTom,
    masthead refs) contract as :func:`parse_consolidation`. The page is
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
    blocks = [(kind, text, None, num) for kind, text, num in classify(paras)]
    refs.sort(key=lambda r: (int(r[1]), int(r[2])))
    tom = (regulation_uri(_fs_key(refs[-1][0]), refs[-1][1], refs[-1][2])
           if refs else None)
    return _structure(blocks[_body_start(blocks):], parser), tom, refs


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
    under ``root/fs/<name>``."""
    return Path(root) / fs / entry["name"]


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
    structure, meta = [], {}
    if reg_file:
        structure, meta = parse_pdf(
            body_path(root, fs, reg_file), record["identifier"], parser,
            ("foreskrift", basefile))

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
        structure=structure, **meta)
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
            cstruct, tom, refs = (
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
                structure=cstruct))
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
