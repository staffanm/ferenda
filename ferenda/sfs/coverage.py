"""Cover a consolidation gap: reconstruct a superseded consolidated wording
that the corpus never captured -- an amendment folded in, then superseded
again before this corpus's downloader ever saw it as "current" (a structural
limit of "poll current, no changelog"; see the [[eurlex-reparse-pending]]
memory) -- by mechanically applying the amending act's own published PDF to
the nearest available prior consolidation.

The register's Omfattning says what the amendment did (`parse_omfattning`),
and each of those changes is written the way the government's own
consolidated text writes it: a provision replaced by its printed text, a
new provision placed after the one before it by ordinal, a repealed
provision reduced to "N § Har upphävts genom lag (YYYY:NNN).", a heading
before a provision replaced, added or removed, the act's title, and a word
substitution ("ordet X ska bytas ut mot Y") applied in every provision the
clause names. Pending variants an earlier amendment left in the base are
settled up to the amendment's own effective date first. A renumbering, a
bilaga, a whole chapter, a moment, an amendment whose effective date is
still in the future, and any disagreement between the register, the
enacting clause and the printed body refuse rather than guess. A bare,
whole-act repeal ("upph.", no provision named) is not a gap at all: the
government's own system never mints a new consolidated text for it, so
there is nothing to reconstruct, and `pending_gaps` skips those links.

The PDF is read through `pdftext.pdf_pages`, page by page, with the print's
own typography as the signal: font size tells body text from a footnote
from a superscript reference, a bold leading run marks a provision, a run's
position gives the indent a stycke opens with and the column a running
header sits in. That is what lets the splice write the replacement in the
consolidated text's own shape (one stycke per blank-line-separated block,
the trailing "Lag (YYYY:NNN)." marker), so the parsed archive version
carries the same stycke structure a government-published consolidation
would have.

Every reconstructed archive file carries a `_reconstructed` metadata key
(sorted first in the serialized JSON) naming the base, the amendment, the
PDF and the exact command that reproduces it -- this is ferenda's own
reconstruction, not the government's published wording. The mark lives only
in the raw archive JSON; nothing downstream reads it.
"""

import re
from datetime import datetime, timedelta, timezone

from ..lib import compress, layout, pdftext, util
from ..lib.errors import SkipDocument
from . import extract
from . import register as register_mod
from .download import serialize, version_id
from .extract import sniff_encoding
from .versions import archival_header

RECONSTRUCTED_KEY = "_reconstructed"

_RE_KAP_PREFIX = re.compile(r"(\d+(?: ?[a-z])?) kap\.\s*")
_RE_RANGE_WITH_MARK = re.compile(r"(\d+)\s*[-–]\s*(\d+)\s*§")
_RE_RANGE_BARE = re.compile(r"(\d+)\s*[-–]\s*(\d+)\Z")
_RE_PAR_WITH_MARK = re.compile(r"(\d+(?: ?[a-z])?)\s*§")
_RE_PAR_BARE = re.compile(r"(\d+(?: ?[a-z])?)\Z")


def _spaced(ordinal):
    """"8a" -> "8 a", the consolidated text's own spelling of a lettered
    ordinal (the PDF's kerning drops the space now and then: "2a§")."""
    return re.sub(r"(\d)([a-z])", r"\1 \2", ordinal)


def _parse_pinpoints(pinpoints_text):
    """Every `(kap_or_None, par)` pinpoint an enacting clause's own pinpoint
    list names: each of "8 a kap. 1 § och 12 kap. 9 §" (every provision
    carries its own "§"), the Swedish convention sharing one trailing
    "§"/"§§" mark across a comma/"och"-separated run of bare numbers ("1, 3,
    4, 12, 26, 32 och 33 §§", "5 kap. 3, 3 a, 3 b §§"), and an en-dash
    *range* of consecutive numbers ("11–14 §§" -> 11, 12, 13, 14) -- the
    range form only for plain integers with no letter suffix on either end
    (a lettered range, "3–3 f §§", is not expanded; the marker cross-check
    downstream refuses rather than guess at it, same as any other
    unrecognized token).

    Caught live, 2026-09-04: a plain per-pinpoint regex found only "33" in
    the shared-mark example above -- silently dropping 1, 3, 4, 12, 26 and
    32, since none of them carry their own "§" at all."""
    pinpoints = []
    kap = None
    pending = []
    for token in re.split(r",|\boch\b", pinpoints_text):
        token = token.strip()
        if not token:
            continue
        km = _RE_KAP_PREFIX.match(token)
        if km:
            kap = _spaced(km.group(1))
            token = token[km.end():].strip()
        m = _RE_RANGE_WITH_MARK.match(token) or _RE_RANGE_BARE.match(token)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if m.re is _RE_RANGE_BARE:
                pending.extend(str(i) for i in range(lo, hi + 1))
            else:
                pinpoints.extend((kap, par) for par in pending)
                pinpoints.extend((kap, str(i)) for i in range(lo, hi + 1))
                pending = []
            continue
        m = _RE_PAR_WITH_MARK.match(token)
        if m:
            pinpoints.extend((kap, par) for par in pending)
            pinpoints.append((kap, _spaced(m.group(1))))
            pending = []
            continue
        m = _RE_PAR_BARE.match(token)
        if m:
            pending.append(_spaced(m.group(1)))
    return pinpoints


# --- the register's Omfattning: what an amendment changes --------------------
#
# The register writes each amendment's changes as "; "-separated clauses, a
# kind word followed by items: "upph. 4 kap. 3 §; ändr. 1, 9 §§, rubr.
# närmast före 12 §; ny 5 a §, rubr. närmast före 5 a §". The kind can also
# switch inside a clause ("ändr. författningsrubr., 1-4, nya 12-15 §§,
# rubr. närmast före 4, 5 §§"), so the text is read as one token stream:
# a kind word sets what the pinpoints that follow are; "rubr. närmast före"
# switches to headings until the next kind word; a chapter prefix holds
# until the next one; a shared "§§" closes a comma/"och" run of bare
# numbers; an en-dash range of plain integers expands. Anything else --
# a whole chapter, a bilaga, a moment or anvisningspunkt (the old tax
# laws), a renumbering, an omtryck, "rubr. närmast efter" -- is
# unsupported and refuses the whole amendment.
_RE_OMFATTNING_TOKEN = re.compile(
    r"(?P<kind>ändr\.?|upph\.|upp\.|nya|nytt|ny|utgår|nuvarande|omtryck|ikrafttr\.|forts\.)(?=\s|$)"
    r"|(?P<rubr>rubr\. närmast (?P<pos>före|efter))"
    r"|(?P<skip>tidigare upphävda|genom \d{4}:\d+)"
    r"|(?P<title>författningsrubr\.)"
    r"|(?P<bil>bil\.(?: \d+)?)"
    r"|(?P<kap>\d+(?: ?[a-z])?) kap\.?"
    r"|(?P<range>\d+)\s*[-–]\s*(?P<range_hi>\d+)\s*(?P<range_mark>§§?)?"
    r"|(?P<num>\d+(?: ?[a-z])?)\s*(?P<mark>§§?)?"
    r"|(?P<sep>,|\boch\b|\bsamt\b)"
    r"|(?P<clause>;)"
    r"|(?P<word>\S+)")


class Changes:
    """What an amendment does to its act, from the register's Omfattning:
    provisions replaced, added and repealed, headings (the one "närmast
    före" a provision) changed, added and removed, the act's own title
    changed, and every item this module does not handle."""

    def __init__(self):
        self.replaced, self.added, self.repealed = [], [], []
        self.headings_changed, self.headings_added, self.headings_removed = [], [], []
        self.title = False
        self.unsupported = []

    def provisions(self):
        return self.replaced + self.added + self.repealed


def parse_omfattning(text):
    """`Changes` for one Omfattning string. Never raises: an item it cannot
    place lands in `unsupported`, and the caller refuses on that."""
    changes = Changes()
    lists = {"ändr": (changes.replaced, changes.headings_changed),
             "ny": (changes.added, changes.headings_added),
             "upph": (changes.repealed, changes.headings_removed),
             "utgår": (None, changes.headings_removed)}
    kind, heading, kap, pending = None, False, None, []

    def flush():
        target = lists.get(kind, (None, None))[1 if heading else 0]
        for par in pending:
            if target is None:
                changes.unsupported.append("%s %s%s" % (kind or "?", ("%s kap. " % kap) if kap else "", par))
            else:
                target.append((kap, par))
        pending.clear()

    # "rubr. närmast före 5 § utgår" names the kind last; put it first
    text = re.sub(r"(rubr\. närmast före [^;]*?) utgår", r"utgår \1", util.normalize_space(text))
    for m in _RE_OMFATTNING_TOKEN.finditer(text):
        if m.group("clause"):
            flush()
            kind, heading, kap = None, False, None
        elif m.group("kind"):
            flush()
            word = m.group("kind").rstrip(".")
            kind = {"ändr": "ändr", "upph": "upph", "upp": "upph", "ny": "ny", "nya": "ny",
                    "nytt": "ny", "utgår": "utgår"}.get(word)
            if kind is None:
                changes.unsupported.append(word)
            heading = False
        elif m.group("rubr"):
            flush()
            heading = True
            if m.group("pos") != "före":
                changes.unsupported.append(m.group("rubr"))
        elif m.group("title"):
            if kind == "ändr":
                changes.title = True
            else:
                changes.unsupported.append("%s %s" % (kind, m.group("title")))
        elif m.group("bil"):
            changes.unsupported.append(m.group("bil"))
        elif m.group("kap"):
            flush()
            kap = _spaced(m.group("kap"))
            rest = text[m.end():].lstrip()
            after = _RE_OMFATTNING_TOKEN.match(rest) if rest else None
            if after is None or after.group("kind") or after.group("rubr") or after.group("clause") or after.group("sep"):
                changes.unsupported.append("%s %s kap." % (kind or "?", kap))
        elif m.group("range"):
            lo, hi = int(m.group("range")), int(m.group("range_hi"))
            pending.extend(str(i) for i in range(lo, hi + 1))
            if m.group("range_mark"):
                flush()
        elif m.group("num"):
            pending.append(_spaced(m.group("num")))
            if m.group("mark"):
                flush()
        elif m.group("sep") or m.group("skip"):
            continue
        else:
            changes.unsupported.append(m.group("word"))
    flush()
    return changes


def is_simple_omfattning(text):
    """Whether this module handles every change `text` (an amendment's own
    Omfattning field) names."""
    return not parse_omfattning(text).unsupported


def _chain(source):
    """Every amendment already folded into `source`'s own text, oldest
    first -- the same (year, running-number) order `layout.sfs_version_key`
    already imposes on archived versions, so a chain position and an
    archived link always agree on order."""
    entries = source.get("andringsforfattningar") or []
    return sorted(entries, key=lambda a: layout.sfs_version_key(a["beteckning"]))


# a whole-act repeal -- the entire act ceasing, not any provision inside
# it being amended -- never gets its own consolidated text at all: the
# government's own system doesn't mint one for it (confirmed against a
# repealed act's own current record, 1902:71 s.1: andringInford stays at
# its last real textual amendment, never bumped to the repeal SFS), and
# corpus-wide, 2026-09-04, zero of 5,716 real repeal chain entries have
# ever been archived under their own key -- not because the corpus missed
# capturing them, but because there was never anything there to capture.
# `pending_gaps` skips these outright: neither "covered" nor a gap to
# fill, the walk continues past one exactly as if it weren't in the chain
# at all. Caught live, 2026-09-04, only after mechanically "reconstructing"
# ~2,000 of them as if they were missing archive files -- wrong on the
# concept, not just the implementation; see the module docstring.
_WHOLE_ACT_REPEAL_MARKERS = ("upph.", "upp.")


def pending_gaps(basefile):
    """The consolidation gaps in `basefile`'s history that are safe to
    attempt right now: for each run of consecutive uncovered chain links,
    only the first -- the one whose immediate predecessor is an
    already-covered file (archived, or the live document itself). Applying
    an amendment PDF to anything but its own immediate prior consolidation
    is exactly the kind of guess this module refuses to make; a later link
    in the same run only becomes attemptable once the one before it is
    filled (a later call, after this run's own gap is covered) -- unless a
    later link happens to be independently covered on disk already, which
    resets tracking there instead. A whole-act repeal link is skipped
    entirely (`_WHOLE_ACT_REPEAL_MARKERS`) -- never a gap.

    Returns `(base_beteckning, base_path, gap_beteckning)` tuples, oldest
    gap first. `base_path` is the *logical* download path (compress-aware);
    read it with `compress.read_json`."""
    gaps = []
    last_beteckning, last_path = None, None
    for beteckning, path in covered_links(basefile):
        if path is not None:
            last_beteckning, last_path = beteckning, path
            continue
        if last_beteckning is not None:
            gaps.append((last_beteckning, last_path, beteckning))
        last_beteckning, last_path = None, None
    return gaps


def covered_links(basefile):
    """`basefile`'s amendment chain, oldest first, each link with the path of
    the consolidation that covers it -- an archived file, or the live
    document for the chain's own cutoff -- or None where the corpus holds
    none. A whole-act repeal link is left out: never a consolidation of its
    own. Returns `(beteckning, path_or_None)` pairs."""
    current_path = layout.sfs_source(basefile)
    current = compress.read_json(current_path)
    covered = dict(layout.sfs_version_downloads(basefile))
    cutoff = version_id(current)
    return [(e["beteckning"], current_path if e["beteckning"] == cutoff else covered.get(e["beteckning"]))
            for e in _chain(current)
            if (e.get("anteckningar") or "").strip() not in _WHOLE_ACT_REPEAL_MARKERS]


class NotSimple(Exception):
    """The amendment, its PDF or the base text is not a case this module
    writes mechanically. The message is human-readable, meant for the run
    report -- never guessed past."""


# the amendment's own closing "Denna lag/förordning träder i kraft ..."
# sentence -- the second gate a change must pass. Caught live, 2026-09-04:
# skattebrottslagen (1971:69) 17 §, SFS 2026:1531, "ändr. 17 §" only, its
# own base provision carrying no pending tag either -- passed every other
# check, applied cleanly, and was still wrong. The act takes effect
# 2026-09-10, a date still in the future when this ran; the government's
# own consolidated text for it keeps *both* wordings, the old one tagged
# "/Upphör att gälla U:2026-09-10/" and the new one "/Träder i kraft
# I:2026-09-10/", not a swap -- confirmed directly against legacy's own
# archived copy of that exact consolidation. A plain replacement is correct
# once that date has passed (nothing left to stack against), so this reads
# the real calendar date, not a fixed cutoff.
#
# ".{1,300}?(?<!kap)\.", not "[^.]+\.": a sentence naming a split
# commencement ("... i fråga om 3 kap. 7-11 §§ ... samt i övrigt den 1
# juli 2019.") embeds an abbreviation period of its own ("kap.") well
# before the sentence's real end -- a plain lazy ".{1,300}?\." still stops
# at that *first* period, so the lookbehind rules out "kap." specifically
# as a sentence end, letting the match run on to the real one.
_RE_IKRAFT_SENTENCE = re.compile(
    r"[Dd]enna (?:lag|förordning) (?:träder|ska träda) i ?kraft (.{1,300}?)(?<!kap)\.", re.DOTALL)
# a repeal-only act names no commencement at all; the provision ceases "vid
# utgången av april 2009" / "vid utgången av den 7 november 2024" / "vid
# utgången av 2011", and the change is in force the day after
_RE_END_OF = re.compile(r"upphöra att gälla vid utgången av (?:den )?(\d+ \w+ \d{4}|\w+ \d{4}|\d{4})\.")


def _day_after_end_of(period):
    """The ISO date following the end of `period` ("7 november 2024",
    "april 2009", "2011"), or None for a period this does not read."""
    parts = period.split()
    if len(parts) == 3:
        date = util.swedish_date(period)
        return date and (datetime.fromisoformat(date) + timedelta(days=1)).date().isoformat()
    if len(parts) == 2:
        date = util.swedish_date("1 " + period)
        if not date:
            return None
        year, month = int(date[:4]), int(date[5:7])
        return "%04d-%02d-01" % ((year + 1, 1) if month == 12 else (year, month + 1))
    return "%s-01-01" % (int(parts[0]) + 1)


def effective_date(tail, clause, current_beteckning):
    """The ISO date the amendment takes effect: its commencement sentence's
    (in `tail`), or the day after the "vid utgången av" period a repeal-only
    act names in `clause`; None when neither says anything (ordinary
    immediate effect). Raises `NotSimple` when a sentence is there but names
    no readable date, leaves the date to the Government ("den dag
    regeringen bestämmer"), or names a date still in the future -- the
    government's own text then stacks the old and new wording, not swaps."""
    m = _RE_IKRAFT_SENTENCE.search(" ".join(tail))
    if m:
        if "regeringen bestämmer" in m.group(1):
            raise NotSimple("%s: takes effect on a date the Government sets -- the "
                            "government's own consolidated text would stack the old "
                            "and new wording, not swap them" % current_beteckning)
        date = util.swedish_date(m.group(1))
    else:
        end = _RE_END_OF.search(clause)
        if not end:
            return None
        date = _day_after_end_of(end.group(1))
    if date is None:
        raise NotSimple("%s: commencement sentence names no date this module reads: %r"
                        % (current_beteckning, (m or end).group()[:80]))
    if date > datetime.now(timezone.utc).date().isoformat():
        raise NotSimple("%s: takes effect on %s, still in the future -- the "
                        "government's own consolidated text would stack the old "
                        "and new wording, not swap them" % (current_beteckning, date))
    return date


# --- the PDF's own pages, as `pdftext.pdf_pages` reads them --------------------
#
# The typography says what each line is, and `pdf_pages` reports it per line:
# `size` tells body text (17 in the 2018- print, 14 in the older one) from a
# footnote (14 / 13) from a superscript reference digit (9-11 / 8-9);
# `lead_bold` marks a provision's own "1 §" line, which a cross-reference
# wrapped to a line start never carries; a run's `left` gives the indent a
# stycke opens with and the column a running header sits in. A flow of text
# (`pdftotext`) keeps none of that, and every rule that read it back out of
# spacing broke on the next print variant (footnotes set with no blank line
# above them, a page with a wide margin, a header without "SFS", a bottom-
# left page number -- four in one afternoon, 2026-09-04).
#
# Per page:
# - a run that is only the running header ("SFS 2004:764", or the bare
#   "2009:7" of one print) is dropped wherever it sits -- right of the first
#   line in the 2018- print, left of it in the older one;
# - the footer is every line below the page's last line of prose (three or
#   more words at body size) that is smaller than the body or is digits only:
#   the footnotes, their labels, the page number. Its runs
#   give the footnote map, a label being a small bare-digit run;
# - a small bare-digit line on a body line's baseline is a superscript
#   reference: the footnote digit of a provision marker (kept, for the
#   "Senaste lydelse" cross-check), or an inline reference ("(Eric-
#   konsortium)2", "beslut1") -- dropped either way;
# - a body-size line of digits only, standing alone, is a page number;
# - the "1 2 3 4 5 6 7 8 9 0 : ;" ruler the 2018- print sets in the margin
#   is dropped wherever it lands.
_RE_RUNNING_HEADER = re.compile(r"SFS|(?:SFS )?\d{4}:\d+")
_RE_SENASTE_LYDELSE = re.compile(r"Senaste lydelse (\d{4}:\d+)\.")
_RE_DIGITS = re.compile(r"\d+(?: \d+)*")
_RE_RULER = re.compile(r"(?:\d ?){10}")   # the 2018- print's "1 2 3 4 5 6 7 8 9 0 : ;" margin ruler
BASELINE_TOL = 6       # a superscript and its line: poppler's tops differ by this
SHORT_LINE = 0.35      # a line ending this share of the measure early ends its stycke
                       # (a line set without hyphenation can end a long word
                       # short, ~20% at most; 2014:1549 "... anländer till")


PAGE_NUMBER_GAP = 4    # a page number sits this many em right of the line's text


def _without_furniture(line, body):
    """`(line, digit)`: `line` with its furniture runs removed -- the running
    header, a superscript reference digit set on the line's own baseline
    ("(Eric-konsortium)2", or a marker's "5 §2"), a page number set far
    right of the text -- or None when nothing else was on it; `digit` the
    superscript's text, "" for none."""
    runs = sorted(line.runs, key=lambda r: r.left)
    kept, digit, prev_right = [], "", None
    for run in runs:
        text = run.text.strip()
        small = run.size and run.size <= body - pdftext.FOOTNOTE_DROP
        if _RE_RUNNING_HEADER.fullmatch(text):
            continue
        if _RE_DIGITS.fullmatch(text) and small:
            digit = text
            continue
        if (_RE_DIGITS.fullmatch(text) and prev_right is not None
                and run.left - prev_right > PAGE_NUMBER_GAP * body):
            continue
        kept.append(run)
        prev_right = run.right
    if len(kept) == len(runs):
        return line, digit
    return (pdftext.line_from_runs(kept, line.top, line.bottom) if kept else None), digit


_RE_WORD = re.compile(r"[A-Za-zÅÄÖåäö]{2,}")


def _is_prose(line, body):
    """A body-size line of three or more tokens, at least one of them a
    word -- not the "0 2 1" a ruler fragment, a footnote label and a page
    number make when poppler groups them on one baseline."""
    return line.size == body and len(line.text.split()) >= 3 and _RE_WORD.search(line.text) is not None


def _page_parts(lines, body):
    """`(body lines, footer lines, marker digits)` for one page: `body lines`
    the lines the stycke assembly reads, their furniture runs removed;
    `footer lines` the footnote zone, untouched (its labels are the small
    digit runs `_without_furniture` would drop); `marker digits` `{top:
    digit}` for every superscript found on a body line (a provision
    marker's footnote reference)."""
    lines = [ln for ln in lines if not _RE_RULER.match(ln.text.strip())]
    last_prose = max((ln.top for ln in lines if _is_prose(ln, body)), default=-1)
    footer, kept, digits = [], [], {}
    for line in lines:
        text = line.text.strip()
        if line.top > last_prose and (line.size < body or _RE_DIGITS.fullmatch(text)):
            footer.append(line)
            continue
        cleaned, digit = _without_furniture(line, body)
        if digit:
            digits[line.top] = digit
        if cleaned is None:
            continue
        if _RE_DIGITS.fullmatch(cleaned.text.strip()) and not any(
                abs(o.top - line.top) <= BASELINE_TOL for o in lines if o is not line):
            continue                       # a page number standing alone
        kept.append(cleaned)
    return kept, footer, digits


def _footnotes(footer_lines, body):
    """`{digit: text}` from every page's footnote zone (`footer_lines` one
    list per page), read run by run: a small bare-digit run labels a note, a
    body-size bare-digit run is the page number (glued to the last note in
    the older print, sharing the label's baseline in the 2018- print),
    anything else is the note's text."""
    footnotes, label = {}, None
    for page in footer_lines:
        label = None                   # a page's notes are its own
        for run in (run for line in page for run in sorted(line.runs, key=lambda r: r.left)):
            text = run.text.strip()
            if _RE_DIGITS.fullmatch(text):
                if run.size <= body - pdftext.FOOTNOTE_DROP:
                    label = text
                    footnotes[label] = ""
                continue
            if label is not None:
                footnotes[label] = (footnotes[label] + " " + text).strip()
    return footnotes


_RE_KAP_HEADING = re.compile(r"(\d+(?: ?[a-z])?) kap\.")
# a provision marker opening a stycke: "7 § När ...", "2a§" alone on its
# line (the older print sets the marker on a line of its own, the text
# starting on the next). The line is `lead_bold`; a cross-reference that
# wraps to a line start ("27 kap.\n33 § andra stycket rättegångsbalken") is
# not, so the text after the marker needs no test of its own.
_RE_MARKER = re.compile(r"(?P<par>\d+(?: ?[a-z])?) ?§(?P<foot>\d{0,2})(?: (?P<rest>.*))?")
_RE_TABLE_SHAPED = re.compile(r"\S {3,}\S")
# where the printed replacement text ends and the PDF's tail matter begins:
# the commencement sentence (alone, or as the first of a numbered list of
# transitional provisions), a transitional-provisions heading, or the
# signature block.
_RE_TAIL = re.compile(
    r"(?:\d+\. )?Denna (?:lag|förordning) (?:träder|ska träda) i ?kraft|"
    r"\d+\. Denna (?:lag|förordning) tillämpas|"
    r"Övergångsbestämmelser|På regeringens vägnar")


def _tight(text):
    """`text` stripped, without the space a dropped superscript run leaves
    before the punctuation that followed it ("(Eric-konsortium) ."), and
    with the minus sign one print sets its strecksatser in (U+2212) as the
    en dash the consolidated text uses."""
    return re.sub(r" (?=[.,;:])", "", text.strip()).replace("\u2212", "–")


def _table_shaped(line):
    """Whether `line` is a table row: two runs with a column gap between
    them (wider than two em), or a run holding three or more spaces."""
    runs = sorted(line.runs, key=lambda r: r.left)
    return (any(b.left - a.right > 2 * line.size for a, b in zip(runs, runs[1:], strict=False))
            or bool(_RE_TABLE_SHAPED.search(line.text)))


class _Stycke:
    """One stycke of the PDF's body: its lines joined into one string,
    line-end hyphenation resolved; `marker` when the stycke opens with a
    bold provision marker (its `foot` the superscript digit on that line,
    "" for none); `heading` for a whole-bold line; `table` when any of its
    lines was a table row."""

    def __init__(self, line, foot=""):
        self.text = _tight(line.text)
        m = _RE_MARKER.fullmatch(self.text) if line.lead_bold else None
        self.marker = m is not None
        self.rest = m.group("rest") if m else None
        # a whole-bold line, or a whole-italic one: the older print sets a
        # lower-level heading in italics ("Preskription av rätt till
        # försäkringsskydd" before 7 kap. 4 § försäkringsavtalslagen)
        self.heading = line.bold or (line.italic and not self.marker)
        self.foot = foot
        self.table = _table_shaped(line)

    def add(self, line):
        self.table = self.table or _table_shaped(line)
        text = _tight(line.text)
        # "säker-" + "hets- eller" -> "säkerhets- eller"; "EU-" +
        # "förordning" -> "EU-förordning" (an abbreviation keeps its
        # hyphen); "hälso-" + "och sjukvård" -> "hälso- och sjukvård" (a
        # suspended hyphen is not a line break's); "till-" + "lämplig" ->
        # "tillämplig" (a compound's third identical consonant, restored
        # only at the hyphenation point); "24–" + "28 kap." -> "24–28 kap."
        # (a range's en dash stays). `pdftext.dehyphenate` knows only the
        # first of these.
        if self.text.endswith("–"):
            self.text += text
        elif self.text.endswith("-") and len(self.text) > 1 and self.text[-2].isalnum():
            if text.startswith(("och ", "eller ")):
                self.text += " " + text
            elif not self.text[-2].islower():
                self.text += text
            elif self.text[-3:-1] == self.text[-2] * 2 and text[:1] == self.text[-2]:
                self.text = self.text[:-1] + text[1:]
            else:
                self.text = self.text[:-1] + text
        else:
            self.text += " " + text


def _stycken(pages):
    """`(stycken, footnotes)`: the PDF's body as `_Stycke`s in print order
    (page furniture removed, see above), and its footnotes by digit. A
    stycke starts at a vertical gap wider than a line and a half, at a line
    indented past the page's own margin, at a bold line (a marker, a chapter
    heading), at a strecksats ("– medverka till ..."), at the line after a
    chapter heading or a marker set alone on its line, and at the top of a
    page when the line opens the tail ("På regeringens vägnar" opens
    2026:1248's last page); every other line continues the stycke before
    it, across a page break included."""
    pages = list(pages)
    body = pdftext.line_body_size([ln for _pageno, lines in pages for ln in lines])
    if not body:
        raise NotSimple("the PDF carries no font sizes (a scan?)")
    stycken, footer = [], []
    prev_right = None
    for _pageno, lines in pages:
        kept, page_footer, digits = _page_parts(lines, body)
        footer.append(page_footer)
        # the page's own column: the leftmost prose line -- not the commonest
        # left, which on a page of list items is the items' indent -- to the
        # rightmost prose line's end
        prose = [ln for ln in kept if ln.runs and _is_prose(ln, body)]
        margin = min((min(r.left for r in ln.runs) for ln in prose), default=0)
        right_edge = max((max(r.right for r in ln.runs) for ln in prose), default=margin)
        prev_top = None
        for line in kept:
            left = min(r.left for r in line.runs) if line.runs else margin
            text = line.text.strip()
            # a line that ends well short of the column's right edge ended
            # its stycke; the next line at the margin opens a new one even
            # with no indent and no gap (2011:1196 5 § sets "Ersättning
            # enligt första och andra styckena betalas med" flush)
            short_before = (prev_right is not None
                            and prev_right < right_edge - SHORT_LINE * (right_edge - margin)
                            and text[:1].isupper())
            after_heading = bool(stycken) and (
                _RE_KAP_HEADING.fullmatch(stycken[-1].text) is not None
                or (stycken[-1].marker and stycken[-1].rest is None))
            starts = (line.bold or line.lead_bold or after_heading or short_before
                      or left - margin >= pdftext.INDENT_MIN
                      or text.startswith(("– ", "- ", "\u2212 "))
                      or (prev_top is not None and line.top - prev_top > 1.5 * body)
                      or (prev_top is None and _RE_TAIL.match(text) is not None))
            foot = next((d for top, d in digits.items() if abs(top - line.top) <= BASELINE_TOL), "")
            if starts or not stycken:
                stycken.append(_Stycke(line, foot))
            else:
                stycken[-1].add(line)
            prev_top = line.top
            prev_right = max(r.right for r in line.runs) if line.runs else None
    return stycken, _footnotes(footer, body)


# a footnote or a running header that the per-page cleaning missed, found
# inside a provision's own text -- the net under `_page_lines`
_RE_FURNITURE_LEAK = re.compile(
    r"(?:^|\s)\d{1,2} (?:Prop\.|Bet\.|Senaste lydelse|Tidigare|Ändringen|\w+ omtryckt)\b|"
    r"\bSFS \d{4}:\d+\b")


# a footnote reference set inside running text -- "(Eric-konsortium)2." for
# an EU act's own footnote -- glued to the word before it; the consolidated
# text carries none. Only a digit the PDF's own footnote list knows is one.
_RE_INLINE_FOOTNOTE = re.compile(r"(?<=[a-zåäö)])(\d{1,2})(?=[.,;:)]|\s|$)")


def _without_footnote_digits(text, footnotes):
    return _RE_INLINE_FOOTNOTE.sub(lambda m: "" if m.group(1) in footnotes else m.group(), text)


def _title_stem(rubrik):
    """The word a PDF's title names the act by, from the base's own rubrik:
    "Föräldrabalk (1949:381)" -> "föräldrabalk" (the PDF says "om ändring i
    föräldrabalken", and prints no SFS number for a balk). Empty for an act
    the rubrik names only by form ("Lag (1998:204) om ..."): the PDF then
    names it by number, and "lag" would match any lag at all."""
    stem = rubrik.split("(")[0].strip().lower()
    return "" if stem in ("", "lag", "förordning", "kungörelse") else stem


# the enacting clause's own verbs, one per drafting shape this module reads:
# a replacement or an insertion prints text ("... ska ha följande lydelse",
# "... en ny paragraf, 5 a §, av följande lydelse"), a repeal or a heading
# removal prints none ("... ska upphöra att gälla", "... ska utgå"), a word
# substitution names the words ("... ordet ”X” ska bytas ut mot ”Y”")
_RE_CLAUSE_VERB = re.compile(
    r"följande lydelse|bytas(?: ut)? mot|upphöra att gälla|ska(?:ll)? utgå")
_RE_QUOTED_HEADING = re.compile(
    r"rubriken närmast före (?P<pinpoint>(?:\d+(?: ?[a-z])? kap\. )?\d+(?: ?[a-z])? §) "
    r"ska(?:ll)? lyda [”\"“](?P<text>[^”\"“]+)[”\"“]")
_RE_SUBSTITUTION = re.compile(
    r"\bi (?P<pinpoints>.+?) ord(?:et|en) [”\"“](?P<old>[^”\"“]+)[”\"“] "
    r"(?P<inflected>i olika böjningsformer )?ska(?:ll)? bytas(?: ut)? mot "
    r"[”\"“](?P<new>[^”\"“]+)[”\"“](?: i motsvarande form)?")
_RE_CLAUSE_ITEM = r"\d+(?: ?[a-z])?(?:\s*[-–]\s*(?:\d+)?(?: ?[a-z])?)?"
_RE_CLAUSE_PINPOINTS = re.compile(
    r"(?:(?P<kap>\d+(?: ?[a-z])?) kap\. )?"
    r"(?P<list>%s(?:(?:, |,? och |,? samt )%s)*) ?§§?" % (_RE_CLAUSE_ITEM, _RE_CLAUSE_ITEM))
_RE_LETTER_RANGE = re.compile(r"(\d+) ?([a-z])?\s*[-–]\s*(\d+)? ?([a-z])")


def _expanded(item):
    """A clause list item as the ordinals it names: "3" -> ["3"]; "3–5" ->
    ["3", "4", "5"]; the lettered ranges "11 a–11 c", "45 a–c" -> the
    letters from the first to the last on the same number; "1–5 a" -> 1
    to 5 and then 5 a."""
    m = _RE_LETTER_RANGE.fullmatch(item)
    if not m:
        return [item]
    number, lo, hi_number, hi = m.groups()
    if lo and hi_number not in (None, number):
        # "13 d–14 a" runs across two numbers; the ends are named, and a
        # printed provision between them refuses as never named
        return ["%s %s" % (number, lo), "%s %s" % (hi_number, hi)]
    if lo:
        return ["%s %s" % (number, chr(c)) for c in range(ord(lo), ord(hi) + 1)]
    return [str(i) for i in range(int(number), int(hi_number) + 1)] + \
           ["%s %s" % (hi_number, chr(c)) for c in range(ord("a"), ord(hi) + 1)]


def _clause_pinpoints(clause):
    """Every provision the enacting clause names, in any of its lists --
    "1 kap. 1, 4 och 8 §§", "2 kap. 1, 3–9 och 10 §§", "24 och 29 §§",
    "11 a–11 c §§" -- as `(kap, par)` pairs. A safety net over the
    register: a printed provision the clause never names is not this
    amendment's."""
    return {(m.group("kap") and _spaced(m.group("kap")), _spaced(par))
            for m in _RE_CLAUSE_PINPOINTS.finditer(clause)
            for item in re.split(r", |,? och |,? samt ", m.group("list"))
            for _kap, par in _parse_pinpoints(" och ".join(_expanded(item.strip())) + " §")}


class Amendment:
    """One amendment PDF, read: `form` ("Lag"/"Förordning", the word the
    consolidated text's trailing marker uses); `effective` (the ISO date its
    commencement sentence names, None for an unstated or undetermined one);
    `provisions` (`{(kap, par): (foot_digit, [stycke, ...])}`, every
    provision it prints, marker line excluded); `items` (the print order:
    `("heading", text)` for a bold line group, `("provision", key)` for a
    marker -- a heading is the act's new title or the heading before the
    provision that follows it, which the register decides); `substitutions`
    (`[(pinpoints, old, new, inflected)]` from a word-substitution clause);
    `quoted_headings` (`{(kap, par): text}`, a heading the clause itself
    states: "rubriken närmast före 2 § ska lyda ”...”"); `named` (every
    provision the enacting clause names); `tail` (the
    commencement and transitional provisions); `footnotes` (digit ->
    text)."""

    def __init__(self):
        self.form = "Lag"
        self.effective = None
        self.provisions, self.items = {}, []
        self.substitutions = []
        self.quoted_headings = {}
        self.named = set()
        self.tail, self.footnotes = [], {}


def parse_amendment_pdf(pdf_path, current_beteckning, basefile, rubrik):
    """The `Amendment` one PDF prints, or raises `NotSimple` when its own
    effective date is still in the future (see `_effective_date_ok`), it
    amends some other act than `basefile` (named by number or, a balk, by
    its `rubrik`), its enacting clause has none of the verbs this module
    reads, or its printed body holds anything other than chapter headings,
    provision markers with their text, and headings directly before a
    provision."""
    stycken, footnotes = _stycken(pdftext.pdf_pages(pdf_path))
    first = next((i for i, st in enumerate(stycken) if re.search(r"före?skriv(?:s|er)\d{0,2}\b", st.text)), None)
    if first is None:
        raise NotSimple("%s: no enacting clause (\"föreskrivs ...\") found" % current_beteckning)
    body_start = next((i for i, st in enumerate(stycken) if i > first
                       and (st.marker or st.heading or _RE_TAIL.match(st.text))), len(stycken))
    clause = " ".join(st.text for st in stycken[first:body_start])
    if not _RE_CLAUSE_VERB.search(clause):
        raise NotSimple("%s: enacting clause names no change this module reads: %r"
                        % (current_beteckning, clause[:120]))
    head = "\n".join(st.text for st in stycken[:body_start])
    stem = _title_stem(rubrik)
    if not (basefile in head or (stem and stem in head.lower())):
        raise NotSimple("%s: names neither %s nor \"%s\" -- not this act's "
                        "amendment" % (current_beteckning, basefile, rubrik))
    amendment = Amendment()
    amendment.footnotes = footnotes
    amendment.form = "Förordning" if re.search(r"^Förordning\b", head, re.MULTILINE) else "Lag"
    amendment.named = _clause_pinpoints(clause)
    for m in _RE_QUOTED_HEADING.finditer(clause):
        for key in _clause_pinpoints(m.group("pinpoint")):
            amendment.quoted_headings[key] = m.group("text")
    for m in _RE_SUBSTITUTION.finditer(clause):
        amendment.substitutions.append((
            sorted(_clause_pinpoints(m.group("pinpoints")), key=str),
            m.group("old"), m.group("new"), bool(m.group("inflected"))))
    kap, key, heading = None, None, []
    for i, st in enumerate(stycken[body_start:], body_start):
        if _RE_TAIL.match(st.text):
            tail = [t.text for t in stycken[i:] if t.text != "Övergångsbestämmelser"]
            signature = [k for k, t in enumerate(tail) if t.startswith("På regeringens vägnar")]
            if not signature:
                raise NotSimple("%s: no signature block after the commencement "
                                "sentence" % current_beteckning)
            tail = tail[:signature[0]]
            end = _RE_END_OF.search(clause)
            if not tail and not end:
                raise NotSimple("%s: no commencement sentence before the "
                                "signature block" % current_beteckning)
            leak = next((m for m in map(_RE_FURNITURE_LEAK.search, tail) if m), None)
            if leak:
                raise NotSimple("%s: the transitional provisions carry page "
                                "furniture: %r" % (current_beteckning, leak.group()))
            amendment.tail = tail
            amendment.effective = effective_date(tail, clause, current_beteckning)
            break
        km = _RE_KAP_HEADING.fullmatch(st.text)
        if km:
            kap = _spaced(km.group(1))
            key = None
            continue
        mm = _RE_MARKER.fullmatch(st.text) if st.marker else None
        if mm:
            key = (kap, _spaced(mm.group("par")))
            if key in amendment.provisions:
                raise NotSimple("%s: %s printed twice" % (current_beteckning, _describe(*key)))
            amendment.provisions[key] = (st.foot or mm.group("foot"),
                                         [mm.group("rest")] if mm.group("rest") else [])
            if heading:
                amendment.items.append(("heading", " ".join(heading)))
                heading = []
            amendment.items.append(("provision", key))
            continue
        if st.heading:
            heading.append(st.text)
            key = None
            continue
        if key is None:
            raise NotSimple("%s: text printed outside any provision: %r"
                            % (current_beteckning, st.text[:80]))
        if st.table:
            raise NotSimple("%s: %s is table-shaped (a column gap inside its "
                            "printed text)" % (current_beteckning, _describe(*key)))
        leak = _RE_FURNITURE_LEAK.search(st.text)
        if leak:
            raise NotSimple("%s: %s carries page furniture in its text: %r"
                            % (current_beteckning, _describe(*key), leak.group()))
        amendment.provisions[key][1].append(_without_footnote_digits(st.text, footnotes))
    else:
        raise NotSimple("%s: no commencement sentence or signature block "
                        "bounds the printed text" % current_beteckning)
    if heading:
        raise NotSimple("%s: a heading printed with no provision after it: %r"
                        % (current_beteckning, " ".join(heading)[:80]))
    empty = [key for key, (_foot, texts) in amendment.provisions.items() if not texts]
    if empty:
        raise NotSimple("%s: %s printed without any text"
                        % (current_beteckning, _describe(*empty[0])))
    return amendment


# --- the base consolidation's text --------------------------------------------

def _line_start(m):
    """`m`'s own start, normalized past any leading "\\r\\n" its `(?:^|\\r\\n)`
    alternative consumed -- MULTILINE's bare `^` only zero-width-matches
    right after a *single* preceding "\\n", so a genuine blank line (two
    "\\r\\n" pairs) instead matches through the "\\r\\n" alternative and
    swallows one into the match. Both spacings occur in real archived text
    (confirmed directly: 1992:1300's own "Inledande bestämmelser" opens its
    first paragraf with a single "\\r\\n", most chapter transitions with a
    blank line) -- the span this returns must start at the marker itself
    either way, or a spliced-in replacement loses the blank line that
    separated it from what precedes, silently reformatting text this module
    never touched."""
    start = m.start()
    return start + 2 if m.group().startswith("\r\n") else start


def _chapter_span(text, kap):
    """The byte span of chapter `kap`'s own text (its heading line to the
    next chapter heading or end of document), or the whole text when `kap`
    is None -- a single-chapter act's provisions are cited without "kap." at
    all. See `_CHAPTER_HEADING` for what counts as a heading."""
    if kap is None:
        return 0, len(text)
    m = _chapter_heading(text, re.escape(kap))
    if not m:
        raise NotSimple("base text has no %s kap. heading" % kap)
    nxt = _chapter_heading(text, r"\d+(?: [a-z])?", m.end())
    end = nxt.start() if nxt else len(text)
    return _line_start(m), len(text[:end].rstrip("\r\n"))


# a chapter heading is a stycke of its own -- "14 kap. Ledning och styrning"
# ("1 Kap. Om allmän underrätt" in rättegångsbalken's legacy text) between
# blank lines, its title wrapping over lines when long -- and the
# title is heading-shaped: it never opens with a digit, which tells it from
# a list item citing a provision ("14 kap. 13 § om rätt för
# Finansinspektionen att ...", 2010:2043), and it never ends in a sentence
# period, which tells it from a wrapped line of prose that happens to open
# with a chapter citation ("8 kap. patientsäkerhetslagen (2010:659) om
# yrkesutövaren hade varit legitimerad. Förordning (2016:161).", 2010:1369)
_CHAPTER_HEADING = r"(?:^|\r\n)%s [Kk]ap\.(?P<title>(?: [^\d\r\n](?:[^\r\n]|\r\n(?!\r\n))*)?)(?=\r\n\r\n|\Z)"


def _chapter_heading(text, kap_pattern, pos=0):
    """The first chapter heading for `kap_pattern` at or after `pos`, or
    None."""
    for m in re.compile(_CHAPTER_HEADING % kap_pattern).finditer(text, pos):
        if _RE_CHAPTER_TITLE.fullmatch(util.normalize_space(m.group("title"))):
            return m
    return None


# a chapter title has no length cap (2015:62's 6 kap. runs to three lines)
_RE_CHAPTER_TITLE = re.compile(r"|(?![\d/(–-])[^a-zåäö](?:.*(?<![.,:;])|.*m\. ?(?:m|fl)\.)")


def _describe(kap, par):
    """A pinpoint as a reader would name it, for error messages: "8 a kap.
    1 §", "17 §"."""
    return "%s%s §" % (("%s kap. " % kap) if kap else "", par)


# a stycke that opens something other than the provision before it: the
# next provision's marker, a chapter heading, or a rubrik -- the shape
# `tokenizer.is_rubrik` reads a heading from: opens uppercase, short, and
# ends without sentence punctuation (a period, a comma, a colon) unless the
# period is an abbreviation's ("m.m."). Caught live, 2026-09-04: a span that
# ran to the next *marker* swallowed the heading between the replaced
# provision and the one after it ("Jäv" before 4 a kap. 5 § sparbankslagen,
# 1987:619), and the splice deleted it.
_RE_TRAILING_MARKER = re.compile(
    r"(?:Lag|Förordning) \((?P<sfs>\d{4}:\d+)\)\.?\s*\Z")
# a provision marker opens with an uppercase letter (or a pending tag); a
# list item citing a provision ("12 § om styrelsens ansvar", 1997:652 10 §)
# does not, and is the provision's own text
_RE_NEXT_MARKER = re.compile(r"\d+(?: [a-z])? §[ .](?=[A-ZÅÄÖ]|/)|\d+(?: [a-z])? [Kk]ap\.")
_RE_HEADING_SHAPE = re.compile(
    r"(?![\d/(–-])[^a-zåäö](?:.{0,134}(?<![.,:;])|.{0,134}m\. ?(?:m|fl)\.)\Z", re.DOTALL)


def _opens_next(rest):
    """Whether the stycke `rest` opens with belongs to what follows the
    provision rather than to it: a marker, or a heading-shaped stycke that
    is itself followed by a marker or another heading -- the lookahead
    `tokenizer.is_rubrik` applies, and what keeps a stycke that merely
    introduces a list ("Underrättelsen skall innehålla upplysning om") with
    its provision."""
    first, _, after = rest.partition("\r\n\r\n")
    second = after.split("\r\n\r\n", 1)[0].strip()
    first = first.strip()
    return bool(_RE_NEXT_MARKER.match(first)
                or (_RE_HEADING_SHAPE.fullmatch(first)
                    and (_RE_NEXT_MARKER.match(second) or _RE_HEADING_SHAPE.fullmatch(second))))


_RE_PENDING_TAG = re.compile(
    r"/(?:Rubriken |Kapitlet )?(?:[Uu]pphör att gälla U|[Tt]räder i kraft I):|/Ny beteckning ")


def _find_provision(chapter_text, par):
    """`(start, end)` of provision `par`'s own text inside `chapter_text`:
    its marker line through the last of its own stycken -- the one carrying
    its trailing "Lag (YYYY:NNN)." marker, or, for a provision never
    amended, the one before the next provision, chapter or heading
    (`_opens_next`). None when the chapter has no such marker."""
    m = re.search(r"(?:^|\r\n)%s §[ .](?=[A-ZÅÄÖ]|/)" % re.escape(par),
                  chapter_text, re.MULTILINE)
    if not m:
        return None
    start = _line_start(m)
    end = len(chapter_text)
    for gap in re.finditer(r"\r\n\r\n+", chapter_text[m.end():]):
        stycke = chapter_text[start:m.end() + gap.start()]
        if _RE_TRAILING_MARKER.search(stycke):
            end = m.end() + gap.start()
            break
        if _opens_next(chapter_text[m.end() + gap.end():]):
            end = m.end() + gap.start()
            break
    # the legacy text now and then glues the next heading to the
    # provision's last line with a single line break ("... inte
    # offentliggöras.\r\nStart- och stopperioder\r\n\r\n24 §", 2018:471):
    # that line is the heading's, not the provision's
    lines = chapter_text[start:end].split("\r\n")
    if (len(lines) > 1 and _RE_HEADING_SHAPE.fullmatch(lines[-1].strip())
            and not _RE_NEXT_MARKER.match(lines[-1].strip())
            and not _RE_TRAILING_MARKER.search(lines[-1])):
        end -= len(lines[-1]) + 2
    return start, end


def locate_provision(text, kap, par):
    """The byte span of provision `par` (within chapter `kap`, or the whole
    text for a single-chapter act) in `text`, see `_find_provision`. Raises
    `NotSimple` when the provision isn't found, or -- the one condition
    this scan exists to catch -- when it still carries a pending variant
    (`/Upphör att gälla .../`, `/Träder i kraft .../`, `/Ny beteckning
    .../`) that `resolve_pending` did not clear: two wordings in flight is
    a stacked amendment, and splicing into it would silently discard
    whichever variant the splice didn't touch."""
    lo, hi = _chapter_span(text, kap)
    span = _find_provision(text[lo:hi], par)
    if span is None:
        raise NotSimple("base text has no %s marker" % _describe(kap, par))
    start, end = span
    if _RE_PENDING_TAG.search(text[lo + start:lo + end]):
        raise NotSimple("%s still carries a pending future/ceasing wording "
                        "in the base text -- a stacked amendment, not a "
                        "plain replacement" % _describe(kap, par))
    return lo + start, lo + end


def _heading_before(text, start):
    """`(hstart, hend)` of the heading set directly before the provision
    opening at `start` -- the stycke before it, when heading-shaped -- or
    None. `hend` runs through the blank line that separates them, so
    removing the span removes the heading whole."""
    before = text[:start]
    if not before.endswith("\r\n\r\n"):
        return None
    hstart = before.rstrip().rfind("\r\n\r\n")
    hstart = 0 if hstart < 0 else hstart + 4
    stycke = before[hstart:].strip()
    if _RE_NEXT_MARKER.match(stycke) or not _RE_HEADING_SHAPE.fullmatch(stycke) \
            or stycke == "Övergångsbestämmelser":
        return None
    return hstart, start


def _insertion_point(text, kap, par):
    """Where a new provision `par` goes in chapter `kap`: right after the
    nearest provision before it by ordinal ("5 a §" after "5 §", "5 b §"
    after "5 a §"). Raises `NotSimple` when the provision already exists or
    nothing precedes it."""
    lo, hi = _chapter_span(text, kap)
    chapter_text = text[lo:hi]
    ordinals = [_spaced(m.group(1)) for m in re.finditer(
        r"(?:^|\r\n)(\d+(?: ?[a-z])?) §[ .](?=[A-ZÅÄÖ]|/)", chapter_text, re.MULTILINE)]
    if par in ordinals:
        raise NotSimple("base text already has %s -- not a new provision" % _describe(kap, par))
    before = [o for o in ordinals if util.numcmp(o, par) < 0]
    if not before:
        raise NotSimple("base text has no provision before %s to place it after"
                        % _describe(kap, par))
    prev = max(before, key=util.split_numalpha)
    _start, end = _find_provision(chapter_text, prev)
    return lo + end


# the two wordings stand next to each other; the old one runs up to the
# new one's marker and never across another provision or chapter (the
# same paragraf number recurs in every chapter)
_RE_PENDING_PAIR = re.compile(
    r"(?:^|(?<=\r\n\r\n))(?P<key>(?:\d+(?: [a-z])? § )?)/(?P<who>Rubriken u|U)pphör att gälla U:(?P<date>\d{4}-\d\d-\d\d)/"
    r"(?:(?!\r\n\r\n\d+(?: [a-z])? (?:§|kap\.))[\s\S])*?"
    r"\r\n\r\n(?P=key)/(?:Rubriken t|T)räder i kraft I:(?P=date)/ ?(?:\r\n(?!\r\n))?")
_RE_PENDING_CEASE = re.compile(r"/(?:Rubriken u|U)pphör att gälla U:(?P<date>\d{4}-\d\d-\d\d)/")
_RE_PENDING_NEW = re.compile(r"/(?:Rubriken t|T)räder i kraft I:(?P<date>\d{4}-\d\d-\d\d)/ ?(?:\r\n(?!\r\n))?")
_RE_PENDING_RENUMBER = re.compile(
    r"(?P<old>\d+(?: [a-z])?) § /Ny beteckning (?P<kap>\d+(?: [a-z])? kap\. )?(?P<new>\d+(?: [a-z])?) § U:(?P<date>\d{4}-\d\d-\d\d)/ ?")
_RE_PENDING_REPEAL = re.compile(
    r"(?:^|(?<=\r\n))(?P<key>\d+(?: [a-z])? §) /Upphör att gälla U:(?P<date>\d{4}-\d\d-\d\d) genom "
    r"(?P<form>lag|förordning|Lag) \((?P<sfs>\d{4}:\d+)\)\./")


def resolve_pending(text, date):
    """`text` with every pending variant whose date is on or before `date`
    settled the way the government's own text settles it once the date has
    passed: a provision's (or heading's) "Upphör att gälla" wording dropped
    and its "Träder i kraft" wording kept untagged, a lone "Träder i
    kraft" untagged, a pending renumbering within the chapter given its
    new number, a pending repeal turned into the "Har upphävts genom"
    note, a provision (or heading) under a lone "Upphör att gälla" naming
    no repealing act removed. Variants dated after `date` stay, and a provision the amendment
    touches while still carrying one refuses in `locate_provision`. A
    renumbering into another chapter raises `NotSimple`: the provision
    would have to move."""
    # the tag sits on the marker's own line, the text starting on the next:
    # "4 § /Träder i kraft I:2026-01-01/\r\nText" settles to "4 § Text"
    def settle(m):
        return m.group("key") if m.group("date") <= date else m.group()
    text = _RE_PENDING_PAIR.sub(settle, text)
    text = _RE_PENDING_NEW.sub(lambda m: "" if m.group("date") <= date else m.group(), text)

    def renumber(m):
        if m.group("date") > date:
            return m.group()
        if m.group("kap"):
            raise NotSimple("a provision moves to another chapter (%s) on %s -- not settled here"
                            % (m.group().strip(), m.group("date")))
        return "%s § " % m.group("new")
    text = _RE_PENDING_RENUMBER.sub(renumber, text)
    left = next((m for m in re.finditer(r"/Ny beteckning [^/]*U:(\d{4}-\d\d-\d\d)[^/]*/", text)
                 if m.group(1) <= date), None)
    if left:
        raise NotSimple("a renumbering this module does not settle (%s) fell due on %s"
                        % (left.group().strip(), left.group(1)))
    out, pos = [], 0
    for m in _RE_PENDING_REPEAL.finditer(text):
        if m.group("date") > date:
            continue
        span = _find_provision(text[m.start():], m.group("key")[:-2])
        assert span, "a pending repeal without its own provision span"
        out.append(text[pos:m.start()])
        out.append("%s Har upphävts genom %s (%s)." % (
            m.group("key"), m.group("form").lower(), m.group("sfs")))
        pos = m.start() + span[1]
    out.append(text[pos:])
    text = "".join(out)
    # a lone "Upphör att gälla" with no new wording and no repealing act
    # named: the provision (or the heading) is simply gone from the
    # government's own text once the date has passed (1996:380 at
    # 2008:424: the verksförordning heading and 9 § both vanish)
    out, pos = [], 0
    for m in _RE_PENDING_CEASE.finditer(text):
        if m.group("date") > date:
            continue
        head = text[:m.start()]
        block_start = head.rfind("\r\n\r\n") + 4 if "\r\n\r\n" in head else 0
        if block_start < pos:
            continue
        key = text[block_start:m.start()].strip()
        if key and not re.fullmatch(r"\d+(?: [a-z])? §", key):
            continue
        if key:
            span = _find_provision(text[block_start:], key[:-2])
            assert span, "a ceasing provision without its own span"
            block_end = block_start + span[1]
        else:
            gap = text.find("\r\n\r\n", m.end())
            block_end = gap if gap >= 0 else len(text)
        out.append(text[pos:block_start])
        pos = block_end + 4 if text.startswith("\r\n\r\n", block_end) else block_end
    out.append(text[pos:])
    return "".join(out)


_RE_WORD_CHAR = r"[\wåäöÅÄÖ]"


def _substituted(block, old, new, inflected, describe):
    """`block` with every `old` replaced by `new`: as a whole word, or --
    "i olika böjningsformer" -- as the stem of any word that opens with it
    ("länsrätt" -> "förvaltningsrätt" in "länsrättens"), a capital kept
    where the text set one. Raises `NotSimple` when `block` holds no
    occurrence: the clause named this provision for a reason."""
    stem = "[%s%s]%s" % (old[:1].upper(), old[:1].lower(), re.escape(old[1:]))
    pattern = re.compile(r"(?<!%s)%s(%s*)" % (_RE_WORD_CHAR, stem, _RE_WORD_CHAR) if inflected
                         else r"(?<!%s)%s()(?!%s)" % (_RE_WORD_CHAR, stem, _RE_WORD_CHAR))

    def swap(m):
        word = new
        if m.group()[:1].isupper() and not new[:1].isupper():
            word = new[:1].upper() + new[1:]
        return word + m.group(1)
    replaced, n = pattern.subn(swap, block)
    if not n:
        raise NotSimple("%s: no \"%s\" to replace with \"%s\"" % (describe, old, new))
    return replaced


def _rewritten(par, stycken, form, target_beteckning):
    """A provision in the consolidated text's own shape: the marker and
    first stycke on one line, every further stycke after a blank line, the
    amendment's own marker on a line of its own at the end -- the form the
    government's text uses (1992:1300 3 §, for one), and the one the
    parser reads a stycke boundary from."""
    return "%s § %s\r\n%s (%s)." % (par, "\r\n\r\n".join(stycken), form, target_beteckning)


def _with_marker(block, form, target_beteckning):
    """`block` (a provision's own text) with its trailing marker replaced
    by, or given, the amendment's own."""
    m = _RE_TRAILING_MARKER.search(block.rstrip())
    core = (block[:m.start()] if m else block).rstrip()
    return "%s\r\n%s (%s)." % (core, form, target_beteckning)


def apply_amendment(base_source, pdf_path, target_beteckning, changes):
    """The reconstructed download-shaped document for `target_beteckning`,
    built from `base_source` (a decoded JSON dict, the nearest prior
    consolidation), `pdf_path` (the amendment's own published PDF) and
    `changes` (what the register says the amendment does). Raises
    `NotSimple` -- never guesses -- when the PDF and the register disagree
    on what changed, or any change is not one this module writes:

    - a replaced provision: its printed text, spliced in the consolidated
      shape (`_rewritten`), the PDF's "Senaste lydelse" footnote checked
      against the base's own trailing marker;
    - a new provision: the printed text placed after the provision before
      it by ordinal, its printed heading (if any) before it;
    - a repealed provision: the government's own note, "5 § Har upphävts
      genom lag (YYYY:NNN).";
    - a heading before a provision: replaced, added or removed;
    - the act's title: the printed one, into `rubrik`;
    - a word substitution: applied in every provision the clause names,
      the amendment's marker set on each.

    Before any of that, pending variants the base carries from earlier
    amendments are settled up to the amendment's own effective date
    (`resolve_pending`)."""
    amendment = parse_amendment_pdf(pdf_path, target_beteckning,
                                    base_source["beteckning"], base_source["rubrik"])
    text = base_source["fulltext"]["forfattningstext"]
    if amendment.effective:
        text = resolve_pending(text, amendment.effective)
    substituted = {key for pinpoints, *_rest in amendment.substitutions for key in pinpoints}
    if not substituted <= set(changes.replaced):
        raise NotSimple("%s: substitutes words in %s, which the register does not list as changed"
                        % (target_beteckning, ", ".join(_describe(*k) for k in sorted(substituted - set(changes.replaced)))))
    expected = (set(changes.replaced) - substituted) | set(changes.added)
    printed = set(amendment.provisions)
    if printed != expected:
        raise NotSimple("%s: register says %s changed, PDF prints %s"
                        % (target_beteckning,
                           ", ".join(_describe(*k) for k in sorted(expected, key=str)) or "nothing",
                           ", ".join(_describe(*k) for k in amendment.provisions) or "nothing"))
    unnamed = [k for k in printed if k not in amendment.named and (None, k[1]) not in amendment.named]
    if unnamed:
        raise NotSimple("%s: prints %s, which its enacting clause never names"
                        % (target_beteckning, _describe(*unnamed[0])))
    # the printed headings: the title, or the heading before a provision
    title, headings, pending_heading = None, {}, None
    heading_keys = set(changes.headings_changed) | set(changes.headings_added)
    for kind, value in amendment.items:
        if kind == "heading":
            pending_heading = value
        elif pending_heading is not None:
            if changes.title and title is None and value not in heading_keys and not headings:
                title = pending_heading
            else:
                headings[value] = pending_heading
            pending_heading = None
    for key, quoted in amendment.quoted_headings.items():
        headings.setdefault(key, quoted)
    if set(headings) != heading_keys:
        raise NotSimple("%s: register names headings before %s, PDF prints headings before %s"
                        % (target_beteckning,
                           ", ".join(_describe(*k) for k in sorted(heading_keys, key=str)) or "nothing",
                           ", ".join(_describe(*k) for k in headings) or "nothing"))
    if changes.title and title is None:
        raise NotSimple("%s: register says the title changed, PDF prints no title" % target_beteckning)
    edits = []
    for key in changes.replaced:
        kap, par = key
        start, end = locate_provision(text, kap, par)
        if key in substituted:
            block = text[start:end]
            for pinpoints, old, new, inflected in amendment.substitutions:
                if key in pinpoints:
                    block = _substituted(block, old, new, inflected, _describe(kap, par))
            edits.append((start, end, _with_marker(block, amendment.form, target_beteckning)))
            continue
        foot_digit, stycken = amendment.provisions[key]
        _check_footnote(kap, par, foot_digit, amendment.footnotes, _trailing_marker_sfs(text[start:end]))
        edits.append((start, end, _rewritten(par, stycken, amendment.form, target_beteckning)))
    for key in changes.repealed:
        kap, par = key
        start, end = locate_provision(text, kap, par)
        edits.append((start, end, "%s § Har upphävts genom %s (%s)."
                      % (par, amendment.form.lower(), target_beteckning)))
    # several new provisions after one existing one ("nya 37 a, 37 b, 37 c
    # §§" after 37 §) share an insertion point and go in as one block, in
    # ordinal order -- as separate edits at one offset they came out
    # reversed (2011:318 at 2022:1124)
    inserted = {}
    for key in sorted(changes.added, key=lambda k: (k[0] or "", util.split_numalpha(k[1]))):
        kap, par = key
        at = _insertion_point(text, kap, par)
        block = _rewritten(par, amendment.provisions[key][1], amendment.form, target_beteckning)
        if key in headings:
            block = headings.pop(key) + "\r\n\r\n" + block
        inserted.setdefault(at, []).append(block)
    for at, blocks in inserted.items():
        edits.append((at, at, "\r\n\r\n" + "\r\n\r\n".join(blocks)))
    for key, heading in headings.items():
        kap, par = key
        start, _end = locate_provision(text, kap, par)
        existing = _heading_before(text, start)
        if existing:
            edits.append((existing[0], existing[1], heading + "\r\n\r\n"))
        else:
            edits.append((start, start, heading + "\r\n\r\n"))
    for key in changes.headings_removed:
        kap, par = key
        start, _end = locate_provision(text, kap, par)
        existing = _heading_before(text, start)
        if not existing:
            raise NotSimple("%s: no heading before %s to remove" % (target_beteckning, _describe(kap, par)))
        edits.append((existing[0], existing[1], ""))
    # splice back-to-front so earlier offsets stay valid; an insertion at a
    # replaced span's start goes in after the replacement, i.e. before it
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    if amendment.tail and not (len(amendment.tail) == 1
                               and _RE_BARE_COMMENCEMENT.fullmatch(amendment.tail[0])):
        text = _with_transitional_entry(text, target_beteckning, amendment.tail)
    new_source = dict(base_source)
    if title:
        new_source["rubrik"] = title
    new_source["fulltext"] = dict(base_source["fulltext"],
                                  forfattningstext=text,
                                  andringInford="t.o.m. SFS %s" % target_beteckning)
    return new_source


def _check_footnote(kap, par, foot_digit, footnotes, prev_marker_sfs):
    """Raise `NotSimple` unless the PDF's own "Senaste lydelse" footnote for
    this provision names the exact SFS number the base text's own trailing
    marker already carries for it -- the cross-check the drafters print for
    free: it catches both a wrong base version and a wrong provision id
    without needing any second source of truth. A footnote saying something
    else ("Tidigare 5 § upphävd genom ...", "Ändringen innebär ...") is no
    cross-check either way."""
    m = _RE_SENASTE_LYDELSE.search(footnotes.get(foot_digit, ""))
    if not m:
        return   # no footnote at all: an unamended-since-enactment provision
    if prev_marker_sfs is not None and m.group(1) != prev_marker_sfs:
        raise NotSimple(
            "%s: PDF states \"Senaste lydelse %s\", base text's own "
            "trailing marker says %s -- refusing rather than apply to the "
            "wrong prior wording"
            % (_describe(kap, par), m.group(1), prev_marker_sfs))


def _trailing_marker_sfs(block):
    """The SFS number a provision's own trailing "Lag (YYYY:NNNN)." marker
    names, or None when the block carries none (an unamended-since-enactment
    provision, or a shape this scan doesn't recognize -- treated as "nothing
    to cross-check against" rather than a hard failure, since the footnote
    check is a bonus safety net, not the only gate)."""
    m = _RE_TRAILING_MARKER.search(block.rstrip())
    return m.group("sfs") if m else None


# the consolidated text's closing "Övergångsbestämmelser" section lists,
# under each amendment's own SFS number, that amendment's commencement and
# transitional provisions -- but only when there is more to them than the
# bare "Denna lag träder i kraft den 1 juni 2026." (or "... den dag som
# regeringen bestämmer.") sentence: 6 of krigsmateriellagen's 21 amendments
# have an entry, and the 15 without are exactly the bare-sentence ones
# (1992:1300, checked 2026-09-04 against the government's own text). A
# statute prints the section after its last provision and before any
# bilaga.
_RE_BARE_COMMENCEMENT = re.compile(
    r"Denna (?:lag|förordning) (?:träder|ska träda) i ?kraft (?:den )?"
    r"(?:\d+ \w+ \d{4}|dag(?: som)? regeringen bestämmer)\.")
_RE_TRANSITIONAL_SECTION = re.compile(r"(?:^|\r\n)Övergångsbestämmelser[ \t]*\r\n")
_RE_BILAGA_HEADING = re.compile(r"\r\n\r\nBilag(?:a|an|or)\b")


def _with_transitional_entry(text, target_beteckning, tail):
    """`text` with `tail` added under `target_beteckning` at the end of the
    Övergångsbestämmelser section (the section created after the last
    provision when the base carries none yet), before any bilaga."""
    entry = "%s\r\n\r\n%s" % (target_beteckning, "\r\n\r\n".join(tail))
    section = _RE_TRANSITIONAL_SECTION.search(text)
    if not section:
        entry = "Övergångsbestämmelser\r\n\r\n" + entry
    bilaga = _RE_BILAGA_HEADING.search(text, section.end() if section else 0)
    if not bilaga:
        return text.rstrip() + "\r\n\r\n" + entry + "\r\n"
    return text[:bilaga.start()].rstrip() + "\r\n\r\n" + entry + text[bilaga.start():]


def read_base(base_path, basefile):
    """`base_source`, a document dict shaped like a beta-API `_source` --
    read directly from a JSON archive (every field carried forward, so a
    reconstruction from a JSON base keeps the prior consolidation's own
    richer register/andringsforfattningar), or, for either legacy HTML
    generation, built fresh from `extract.extract_body` -- the same
    plain-text reader `sfs.versions.parse_version` already trusts for that
    shape -- plus the header `versions.archival_header`/
    `register.parse_sfst_header` already extract from it, normalized to the
    "\\r\\n" line convention every regex in this module assumes throughout
    (`extract_body` strips "\\r" entirely; confirmed byte-for-byte identical
    against a JSON-shaped consolidation of the same statute, 2026-09-04).

    Only `beteckning`, `rubrik` and `fulltext` are populated for an HTML
    base -- `versions.parse_version`'s JSON path needs no more than that to
    parse it (`register.sfst_header_from_source`/`register_from_source`
    both fall back on `.get()` for everything past those two), and an HTML
    archive never carried the richer register `versions.py` itself only
    ever reads from a JSON source to begin with, reconstructed or not.
    Raises `SkipDocument` (never caught here -- the caller decides what an
    unreadable base means) when the HTML page itself doesn't parse."""
    if base_path.suffix == ".json":
        return compress.read_json(base_path)
    raw = compress.read_bytes(base_path)
    header = (archival_header(base_path) if sniff_encoding(raw) == "latin-1"
              else register_mod.parse_sfst_header(base_path))
    text = extract.extract_body(base_path).replace("\n", "\r\n")
    return {
        "beteckning": basefile,
        "rubrik": header.get("Rubrik", ""),
        "fulltext": {"forfattningstext": text,
                    "andringInford": header.get("Ändring införd", "")},
        "organisation": {},
        "register": {},
        "andringsforfattningar": [],
    }


def cover_gap(basefile, base_beteckning, base_path, target_beteckning,
             *, dry_run=False):
    """Attempt to cover one gap: `(status, detail)`, `status` one of "wrote",
    "skipped" (no published PDF to apply, or the nearest prior
    consolidation's own HTML page didn't parse -- not a failure, just
    nothing to do yet) or "not_simple" (`detail` is the `NotSimple`
    reason). Never raises for an ordinary triage refusal -- only for a
    genuinely broken environment (a missing base file, a malformed
    archive).

    The reconstruction carries the live document's own amendment chain cut
    at the target -- the register a government-published consolidation of
    that cutoff would carry, and what `versions.parse_version` reads the
    version's register from."""
    pdf_path = layout.sfs_pdf(target_beteckning)
    if not compress.exists(pdf_path):
        return "skipped", "no published PDF mirrored for %s yet (see " \
            "`lagen sfs mirror-pdf %s`)" % (target_beteckning, target_beteckning)
    try:
        base_source = read_base(base_path, basefile)
    except SkipDocument as exc:
        return "skipped", "%s's nearest prior consolidation (%s) doesn't " \
            "parse: %s" % (base_beteckning, base_path, exc)
    chain = _chain(compress.read_json(layout.sfs_source(basefile)))
    target_key = layout.sfs_version_key(target_beteckning)
    entry = [e for e in chain if e["beteckning"] == target_beteckning]
    assert entry, "%s: %s is not in the amendment chain" % (basefile, target_beteckning)
    omfattning = entry[0].get("anteckningar") or ""
    changes = parse_omfattning(omfattning)
    if changes.unsupported:
        return "not_simple", ("%s: Omfattning \"%s\" names %s"
                              % (target_beteckning, omfattning, ", ".join(changes.unsupported)))
    if not (changes.provisions() or changes.headings_changed or changes.headings_added
            or changes.headings_removed or changes.title):
        return "not_simple", ("%s: Omfattning \"%s\" names no change to write"
                              % (target_beteckning, omfattning))
    try:
        new_source = apply_amendment(base_source, pdf_path, target_beteckning, changes)
    except NotSimple as exc:
        return "not_simple", str(exc)
    command = ("lagen sfs cover-consolidation-gap %s  # covering %s -> %s"
              % (basefile, base_beteckning, target_beteckning))
    new_source["andringsforfattningar"] = [
        e for e in chain if layout.sfs_version_key(e["beteckning"]) <= target_key]
    new_source[RECONSTRUCTED_KEY] = {
        "base": base_beteckning, "amendment": target_beteckning,
        "source_pdf": str(pdf_path), "command": command,
        "reconstructed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if dry_run:
        return "wrote", "dry run: would write %s consolidated through %s" \
            % (basefile, target_beteckning)
    dest = layout.sfs_archive_version_download(
        layout.SFS_DOWNLOADED, basefile, target_beteckning)
    compress.write_download(dest, serialize(new_source))
    return "wrote", str(dest)
