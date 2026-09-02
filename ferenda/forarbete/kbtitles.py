"""The reader-facing title of a KB two-chamber proposition (propkb, 1867-1970).

The 19 066 propkb records take their `title` verbatim from the old ferenda
entry JSONs -- as they should, a record is what the upstream said -- and three
defects in those entries would otherwise reach the artifact, and from there the
catalog listing (`/forarbete/prop/1952/`):

  * **1 603 titles are a Python 1-tuple repr** --
    ``"('med förslag till lag angående ändring i lagen ...',)"`` -- an
    old-ferenda write bug (the frozen entry file itself holds the tuple), with
    every non-ASCII character escaped inside it (``för\\xad slag``). Our import
    copied it faithfully, so no re-import can undo it.
  * **8 117 titles keep the OCR line-break hyphen** U+00AC (or U+00AD) and the
    space after it ("angående fortsatt dispo¬ sition av vissa äldre anslag").
    The body parse already joins those lines
    (`legacy_formats._join_ocr_lines`); the entry title never did. Removing a
    line-break *hint* from a harvested title is `lib.util.normalize_hints`'s
    job at download time; joining an OCR line break in a title we already hold
    is this one, and it needs the rule the hint remover does not have -- U+00AC,
    and a real hyphen kept before an uppercase continuation.
  * **1 570 titles are the placeholder "Doc 1952:64"** -- the old downloader
    found no title for the record and wrote its basefile instead.

The first two are string repairs. The third is read back off the document's own
first page, where the printed title follows the "Kungl. Maj:ts proposition till
riksdagen" head and ends at the dateline ("; given Stockholms slott den 15
februari 1952") -- see `title_from_paras`. Over the 1 570 placeholders it
recovers 1 551 and leaves 19 at `MISSING`. As a control it was pointed at the
records whose entry title is intact, where it never runs in production: on 600
sampled off the raw OCR pages it reproduced the entry's own title on 599 (the
600th is a bound volume whose entry title took the *previous* document's, and
the OCR reading is the right one), and on 200 sampled through the parse body
path on 199 (the 200th's entry title is body prose, and so is the reading --
that front page prints no title paragraph).

`parse.parse_record` calls `reader_title` for every förarbete document, and the
record on disk keeps the broken title: the record is the harvested copy of what
the upstream held, and every extracted semantic -- this title included -- is
the parse's output. That also keeps the reading revisable, since the artifact
is rebuilt from the same bytes whenever this module changes (`FA_CODE`).
"""

import ast
import re

from ..lib import util

# the same line-join the body parse uses, for the paragraph boundary a title
# runs across: the OCR breaks a word there as readily as inside a paragraph
from ..lib.pdftext import dehyphenate as join_line
from . import legacy_formats

# our own marker for a proposition whose printed title the OCR does not carry,
# in the shape sfs/extract.py already uses for missing statute text
MISSING = "(rubrik saknas)"

# "Doc 1952:64" -- the old downloader's placeholder, its basefile verbatim
RE_PLACEHOLDER = re.compile(r"^Doc \d")

# the OCR renders the line-break hyphen as U+00AC (NOT SIGN) or U+00AD (SOFT
# HYPHEN) and the entry title kept the space that followed it. Joining is the
# body's rule (lib.pdftext.dehyphenate): a lowercase continuation is one word
# ("Malmö¬ hus" -> "Malmöhus"), anything else keeps a real hyphen
RE_LINE_HYPHEN = re.compile(r"[%s%s]\s*(.)" % (legacy_formats.OCR_HYPHEN,
                                               legacy_formats.SOFT_HYPHEN))

# the front page names the document kind, then (usually) its addressee. The
# OCR garbles both freely -- "Kungl. i\laj:ts proposition", "till .Riksdagen",
# "Ull Biksdagen" -- so the addressee pattern allows up to three junk
# characters before "iksdagen" and the kind pattern spells out the era's two
# spellings of skrivelse
RE_KIND = re.compile(r"(proposition|skri[fv]{1,2}else)", re.IGNORECASE)
RE_ADDRESSEE = re.compile(r"till\s+\S{0,3}[a-zA-ZÅÄÖåäö]iksdagen", re.IGNORECASE)

# a title opens with one of these words in 99.6% of the 15 893 intact propkb
# titles ("angående" 70%, "med förslag till" 27%, then "om", "i", "rörande")
RE_OPENER = re.compile(
    r"\b(angående|angaende|angånde|med|om|rörande|innefattande"
    r"|i\s+(?:fråga|anledning|öfverensstämmelse|överensstämmelse))\b",
    re.IGNORECASE)

# The title ends at the dateline, in one of two readings. The first is the
# given/gifven clause, spaced or not ("gif ven"); it deliberately carries no
# left word boundary, because the OCR glues the verb to the title as readily as
# it spaces it -- prop 1963:137 reads "..., m. mgiven Stockholms slott den 29
# mars 1963", and there the glued match is the only thing that ends the title.
# The price is that the same pattern cuts a title *containing* the letters
# ("utgifven", "medgiven"): it would truncate 3 of the 15 893 intact propkb
# titles, none of which this module ever reads. Do not "fix" the boundary back.
# The second reading is the same clause with its verb and its "den" garbled
# ("givep Stockholms slott deri 24 fefrruapi lff33"), anchored on the word
# "slott" and eating the two tokens before it.
DATELINES = (re.compile(r"[\s;:,\-]*g[ie]f?\s?ven\b.*$", re.IGNORECASE | re.DOTALL),
             re.compile(r"[\s;:,\-]*(?:\S+\s+){0,2}\S*slott(?:et)?\s+d\w{0,3}\b.*$",
                        re.IGNORECASE | re.DOTALL))

# three more things a title runs into, cut but never counted as its end (see
# `_has_tail`): the statsverksproposition's opening sentence, which is all that
# closes its display-line title; the running head of the bound volume ("Bihang
# till riksdagens protokoll 1945"), which the OCR sometimes glues onto the title
# paragraph; and the stub of a dateline the paragraph break cut mid-word
# ("... fattade beslut; gi\xad")
TAILS = DATELINES + (
    re.compile(r"[\s;:,\-]*J[äe]mlikt grundlagens bud.*$", re.DOTALL),
    re.compile(r"[\s;:,\-]*Bih(?:ang|\.)\s+till\s+\S*[Rr]iks?d.*$", re.DOTALL),
    re.compile(r"[\s;:,\-]*\S{0,4}[%s%s]\s*$" % (legacy_formats.OCR_HYPHEN,
                                                 legacy_formats.SOFT_HYPHEN)))

# how many following paragraphs may be pulled in to complete a title the OCR
# broke across paragraphs, and how many paragraphs of the front page to read
JOIN_AHEAD = 3
FRONT_PARAS = 16

# the display-line pass reads only the head of the page, and only lines short
# enough to be a title: the longest intact propkb title runs to 604 characters,
# but every title that long carries a dateline and the first pass finds it
FRONT_LINES = 12
LOOSE_MAX = 300

# a display-line title continues on another display line ("... under
# budgetåret" / "1923-1924."), never in the prose that follows it
CONTINUATION_MAX = 60


def untuple(title):
    """The string inside a 1-tuple repr, or `title` unchanged. `literal_eval`
    decodes the escapes the repr introduced (``för\\xad slag``), which a slice
    of the raw text would leave as four literal characters."""
    if not title.startswith(("('", '("')):
        return title
    value = ast.literal_eval(title)
    assert isinstance(value, tuple) and len(value) == 1, title
    return value[0]


def dehyphenate(title):
    """`title` with the OCR line-break hyphens joined, the body's rule. Not
    `lib.util.normalize_hints`, which *removes* U+00AD (and the other
    invisible break hints: the zero-width, joiner and direction marks) from a
    title a CMS set with break hints -- here the character is a
    hyphenation the OCR read off the printed page, and joining the two halves
    is the repair."""
    return RE_LINE_HYPHEN.sub(
        lambda m: m.group(1) if m.group(1).islower() else "- " + m.group(1),
        title)


def _cut_tail(text):
    for tail in TAILS:
        text = tail.sub("", text)
    # the trailing period stays: a title ends "m. m." as often as not
    return re.sub(r"[\s;:,\-]+$", "", text).strip()


def _has_tail(text):
    """Whether the text reaches the dateline that closes a title. Only the
    `DATELINES` count: the boilerplate, running-head and stub rules cut a title
    short, they do not prove the title is complete."""
    return any(dateline.search(text) for dateline in DATELINES)


def _title_after_addressee(text, start):
    """The text after the "till riksdagen" that introduces the title, or None.

    A front page can name the addressee twice: the protocol paragraph above the
    title says "skulle i enlighet med samma förslag nådiga propositioner till
    Riksdagen aflåtas" (prop 1874:31), and taking the first match there puts
    "aflåtas. Ex protocollo ..." in front of the real title. So the match whose
    continuation opens like a title wins, and the first match is only the
    fallback -- for the pages whose opener the OCR garbled ("till riksdagen
    nied förslag till kungörelse ...", prop 1931:199), where no match qualifies
    and the first one is still the right one."""
    first = None
    for addressee in RE_ADDRESSEE.finditer(text, start):
        title = re.sub(r"^[\s,;:.\-]+", "", text[addressee.end():])
        if RE_OPENER.match(title):
            return title
        first = first if first is not None else title
    return first


def _from_text(text, need_tail):
    """The title inside one front-page paragraph, or None. With `need_tail` the
    paragraph must carry the dateline that closes the title -- the opening pass
    over the page, so a paragraph the OCR cut short cannot win over the whole
    one. Without it, the reading takes what it can get and demands instead that
    the result open like a title."""
    kind = RE_KIND.search(text)
    if not kind or (need_tail and not _has_tail(text)):
        return None
    title = _title_after_addressee(text, kind.end())
    if title is None:
        if not (_has_tail(text) and (opener := RE_OPENER.search(text, kind.end()))):
            return None
        title = text[opener.start():]
    title = _cut_tail(title)
    if len(title) < 10 or (not need_tail and not RE_OPENER.match(title)):
        return None
    return title


def _join_ahead(paras, i, complete, longest=None):
    """Paragraph `i` completed from the ones after it while `complete` says the
    text is still cut short -- the OCR breaks a title across paragraphs often
    enough that a paragraph on its own is not a candidate. `longest` bounds what
    counts as a continuation, for the display-line pass whose next paragraph is
    prose as often as the rest of the title."""
    text = paras[i]
    for following in paras[i + 1:i + 1 + JOIN_AHEAD]:
        if complete(text) or (longest and len(following) > longest):
            break
        # a word the OCR broke at the paragraph boundary is joined by both
        # rules: "stads-" + "planelag" by the body's, "för\xad" + "mynderskap"
        # by this module's -- the pdftotext route joins them inside the
        # paragraph, the hidden-text-layer route leaves them at its end
        text = dehyphenate(join_line(text, following))
    return text


def title_from_paras(paras):
    """The printed title of a proposition, read off the OCR of its first page,
    or None. `paras` is the page's paragraph texts in order.

    The front page prints one title paragraph -- "Kungl. Maj:ts proposition
    till riksdagen angående anslag till Svenska skifferoljeaktiebolaget m. m.;
    given Stockholms slott den 7 mars 1952." -- and the title is what stands
    between the addressee and the dateline. Two shapes make that more than one
    regex over one paragraph, and each gets a pass:

      * the OCR breaks the paragraph in the middle of the title often enough to
        matter (prop 1922:223 ends one paragraph at "med förslag till lag om."
        and opens the next with "ändrad lydelse av 2 kap. 11 § strafflagen;
        given ..."), so the first pass completes each paragraph up to its
        dateline and takes only a candidate that reaches one;
      * the budget proposition (prop N:1 of each year) sets its head as display
        lines ("PROPOSITION" / "TILL" / "Riksdagen" / "angående statsverkets
        tillstånd och behov under budgetåret" / "1943/44."), where no paragraph
        carries head and title together, so a second pass reads an opener-led
        line off a page that names the document kind above it.

    A third pass closes: the first one again, without demanding the dateline,
    for the pages whose OCR garbled it past recognition ("; ginen Stockholms
    slott den It"). It runs last because it reads a paragraph the OCR may have
    cut short, and `_from_text` guards it by demanding a title-shaped
    opener."""
    paras = [util.normalize_space(dehyphenate(p)) for p in paras]
    for i in range(len(paras)):
        if title := _from_text(_join_ahead(paras, i, _has_tail), need_tail=True):
            return title
    if any(RE_KIND.search(para) for para in paras[:FRONT_LINES]):
        for i in range(min(len(paras), FRONT_LINES)):
            if not RE_OPENER.match(paras[i]):
                continue
            title = _cut_tail(_join_ahead(
                paras, i, lambda t: t.endswith(".") or len(t) > LOOSE_MAX,
                CONTINUATION_MAX))
            if 10 <= len(title) <= LOOSE_MAX:
                return title
    for i in range(len(paras)):
        title = _from_text(_join_ahead(paras, i, _has_tail), need_tail=False)
        if title and len(title) <= LOOSE_MAX:
            return title
    return None


def reader_title(title, body):
    """The reader-facing title for one förarbete record: `title` with the tuple
    wrapper and the OCR line-break hyphens removed, or -- where the old
    downloader stored the "Doc 1952:64" placeholder -- the title read off the
    document's own first page, falling back to `MISSING`. `body` is the parsed
    block list, whose leading blocks are that page.

    Called for every förarbete document and gated on the defect, not on the
    corpus: a title carrying none of the three is returned character-for-
    character as the record holds it, whitespace included -- 4 006 non-propkb
    titles would otherwise have had theirs collapsed (rskr's trailing space, a
    newline inside a Ds title), which is a separate decision about a different
    corpus. The tuple repr and the placeholder are KB-only, but the line-break
    hyphen is repaired wherever it stands: 47 titles outside propkb carry one
    (45 U+00AD from a CMS heading, 2 U+00AC), and on 45 of them this gives what
    `lib.util.normalize_hints` gives at harvest time. The 2 differ because
    `normalize_hints` removes U+00AD and the invisible break hints and knows nothing
    about U+00AC -- that character class, not the capitalization branch, is why
    it cannot stand in for `dehyphenate` here.

    An absent title is the empty string, not None: 345 records (286 prop, 59
    dir, no propkb) carry ``"title": null`` because the upstream published
    none, and `Forarbete.title` is typed `str`."""
    if not title:
        return ""
    if RE_PLACEHOLDER.match(title):
        return title_from_paras([b.text for b in body[:FRONT_PARAS]]) or MISSING
    if title.startswith(("('", '("')) or RE_LINE_HYPHEN.search(title):
        return util.normalize_space(dehyphenate(untuple(title)))
    return title
