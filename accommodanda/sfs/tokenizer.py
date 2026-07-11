"""Tokenizer: classify SFS plaintext into a flat stream of events.

The recognition heuristics (what makes a line a chapter heading rather
than a TOC entry, what distinguishes a table from short paragraphs, ...)
are ported from the old ferenda sfs_parser; the cited SFS numbers in
comments mark the documents that motivated each special case. Assembly of
the event stream into a tree happens in assembler.py.
"""

import difflib
import logging
import re
from dataclasses import dataclass
from datetime import datetime

from ..lib import util
from .model import Tabell, Tabellrad

log = logging.getLogger(__name__)

re_SimpleSfsId = re.compile(r"(\d{4}:\d+)\s*$")
re_ChangeNote = re.compile(r"(Lag|Förordning) \(\d{4}:\d+\)\.?$")
re_ChapterId = re.compile(r"^(\d+( \w|))\s[Kk][Aa][Pp]\.").match
re_SectionId = re.compile(r"^(\d+ ?\w?) \xa7[ \.]")
re_SectionIdOld = re.compile(r"^\xa7 (\d+ ?\w?).")  # as used in eg 1810:0926
re_NumberRightPara = re.compile(r"^(\d+)\) ").match
re_DottedNumber = re.compile(r"^(\d+ ?\w?)\. ")
re_Bokstavslista = re.compile(r"^(\w)\) ")
re_Strecksatslista = re.compile(r"^(- |\x96 |– |--)")
re_ElementId = re.compile(r"^(\d+) mom\.")  # historical "moment" subsections
# this many consecutive chapter-shaped, content-less paragraphs are an
# (unannounced) in-body TOC, not real chapters (see Tokenizer._toc_run_ahead)
TOC_RUN = 3

re_ChapterRevoked = re.compile(
    r"^(\d+( \w|)) [Kk]ap. (upphävd|[Hh]ar upphävts) genom "
    r"(förordning|lag) \([\d\:\. s]+\)\.?$").match
re_SectionRevoked = re.compile(
    r"^(\d+ ?\w?) \xa7[ \.]([Hh]ar upphävts|[Nn]y beteckning (\d+ ?\w?) \xa7) "
    r"genom ([Ff]örordning|[Ll]ag) \([\d\:\. s]+\)\.$").match
re_RevokeDate = re.compile(
    r"/(?:Rubriken u|Kapitlet u|U)pphör att gälla U:(\d+)-(\d+)-(\d+)"
    r"(?: genom lag \(\d{4}:\d+\).|)/")
re_RevokeAuthorization = re.compile(
    r"/(?:Kapitlet u|U)pphör att gälla U:(den dag (?:som |)regeringen bestämmer)"
    r"(?: genom lag \(\d{4}:\d+\).|)/")
re_EntryIntoForceDate = re.compile(
    r"/(?:Rubriken t||Kapitlet t|T)räder i kraft I:(\d+)-(\d+)-(\d+)"
    r"(?: genom lag \(\d{4}:\d+\).|)/")
re_EntryIntoForceAuthorization = re.compile(
    r"/(?:Kapitlet t|T)räder i kraft I:(den dag (?:som |)regeringen bestämmer)"
    r"(?: genom lag \(\d{4}:\d+\).|)/")
re_dehyphenate = re.compile(r"\b- (?!(och|eller))").sub

OB_SEPARATORS = ["Övergångsbestämmelser",
                 "Ikraftträdande- och övergångsbestämmelser",
                 "Övergångs- och ikraftträdandebestämmelser"]

BILAGA_LINES = ("Bilaga", "Bilaga*", "Bilaga *", "Bilaga 1", "Bilaga 2",
                "Bilaga 2 a", "Bilaga 3", "Bilaga 4", "Bilaga 5", "Bilaga 6")


def andrings_datum(line, match=False):
    """Find and strip /Upphör att gälla U:.../ and /Träder i kraft I:.../
    directives. Returns (stripped line, upphor, ikrafttrader)."""
    dates = {"ikrafttrader": None, "upphor": None}
    for regex, key in ((re_RevokeDate, "upphor"),
                       (re_RevokeAuthorization, "upphor"),
                       (re_EntryIntoForceDate, "ikrafttrader"),
                       (re_EntryIntoForceAuthorization, "ikrafttrader")):
        m = regex.match(line) if match else regex.search(line)
        if m:
            try:
                if len(m.groups()) == 3:
                    dates[key] = datetime(int(m.group(1)), int(m.group(2)),
                                          int(m.group(3)))
                else:
                    dates[key] = m.group(1)
                line = regex.sub("", line)
            except ValueError:
                pass  # invalid datestring like "2014-081-01": leave line as-is
    return line.strip(), dates["upphor"], dates["ikrafttrader"]


# --- events --------------------------------------------------------------

@dataclass
class OpenAvdelning:
    ordinal: str
    rubrik: str
    underrubrik: str | None = None


@dataclass
class OpenUnderavdelning:
    ordinal: str
    rubrik: str


@dataclass
class OpenKapitel:
    ordinal: str
    rubrik: str
    # datetime for a dated /Upphör att gälla U:.../ or /Träder i kraft
    # I:.../ directive, str for the "den dag regeringen bestämmer" form
    upphor: datetime | str | None = None
    ikrafttrader: datetime | str | None = None


@dataclass
class OpenParagraf:
    ordinal: str
    first_stycke: str
    moment: str | None = None
    # datetime for a dated /Upphör att gälla U:.../ or /Träder i kraft
    # I:.../ directive, str for the "den dag regeringen bestämmer" form
    upphor: datetime | str | None = None
    ikrafttrader: datetime | str | None = None


@dataclass
class OpenOBSection:
    rubrik: str


@dataclass
class OpenOB:
    sfsnr: str


@dataclass
class OpenBilaga:
    rubrik: str
    # datetime for a dated /Upphör att gälla U:.../ or /Träder i kraft
    # I:.../ directive, str for the "den dag regeringen bestämmer" form
    upphor: datetime | str | None = None
    ikrafttrader: datetime | str | None = None


@dataclass
class UpphavtKapitelEv:
    ordinal: str
    text: str


@dataclass
class UpphavdParagrafEv:
    ordinal: str
    text: str


@dataclass
class RubrikEv:
    text: str
    underrubrik: bool = False
    # datetime for a dated /Upphör att gälla U:.../ or /Träder i kraft
    # I:.../ directive, str for the "den dag regeringen bestämmer" form
    upphor: datetime | str | None = None
    ikrafttrader: datetime | str | None = None


@dataclass
class StyckeEv:
    text: str


@dataclass
class ListItemEv:
    kind: str  # "numrerad" | "bokstav" | "strecksats"
    text: str
    ordinal: str | None = None


@dataclass
class TabellEv:
    tabell: Tabell


# --- tokenizer -----------------------------------------------------------

class Tokenizer:
    def __init__(self, reader, basefile):
        self.reader = reader
        self.basefile = basefile
        self.current_avdelning = "0"
        self.current_chapter = "0"
        self.current_section = "0"
        self.fake_chapter = "0"
        self.saw_dash_toc = False  # a "N kap. - Title" TOC entry has been seen
        self.in_toc = False  # the last § announced a chapter listing
        self.headline_level = 0  # 0 = unknown, 1 = normal seen, 2 = in subs

    def preamble(self):
        """Consume leading blank lines and a document-level entry-into-force
        directive, if present. Returns ikrafttrader or None."""
        r = self.reader
        while not r.eof() and r.peekline() == "":
            r.readline()
        if r.eof():
            return None
        line, upphor, ikrafttrader = andrings_datum(r.peekline())
        if ikrafttrader:
            r.readline()
            return ikrafttrader
        return None

    def __iter__(self):
        return self

    def __next__(self):
        event = self.next_event()
        if event is None:
            raise StopIteration
        return event

    def next_event(self, in_numrerad=False):
        r = self.reader
        while True:
            if r.eof():
                return None
            try:
                if r.peekline() == "":
                    r.readline()
                    continue
            except IOError:
                return None
            break

        # Each is_*/tok_* pair below that looks further ahead than the
        # current line/paragraph (is_rubrik, is_tabell,
        # is_overgangsbestammelser, tok_avdelning) catches its own IOError
        # from peeking past the end of data internally, so a lookahead
        # failure there falls back to "no more context" rather than
        # aborting the whole event. Nothing here is expected to raise
        # IOError; if it does, that's a bug to fail fast on, not to paper
        # over (rule:narrow-what-you-catch, rule:fail-fast).
        #
        # inside a numbered list, list continuation takes priority over
        # everything (some items would otherwise classify as tables)
        if in_numrerad and self.is_numrerad_lista():
            return self.tok_listitem("numrerad")
        if self.is_avdelning():
            return self.tok_avdelning()
        if self.is_underavdelning():
            return self.tok_underavdelning()
        if self.is_upphavt_kapitel():
            return self.tok_upphavt_kapitel()
        if self.is_upphavd_paragraf():
            return self.tok_upphavd_paragraf()
        if self.is_kapitel():
            return self.tok_kapitel()
        if self.is_paragraf():
            return self.tok_paragraf()
        if self.is_tabell():
            return self.tok_tabell()
        if self.is_overgangsbestammelser():
            return OpenOBSection(rubrik=self.reader.readparagraph())
        if self.is_overgangsbestammelse():
            return OpenOB(sfsnr=self.reader.readline())
        if self.is_bilaga():
            return self.tok_bilaga()
        if self.is_numrerad_lista():
            return self.tok_listitem("numrerad")
        if self.is_strecksatslista():
            return self.tok_listitem("strecksats")
        if self.is_bokstavslista():
            return self.tok_listitem("bokstav")
        if self.is_rubrik():
            return self.tok_rubrik()
        return StyckeEv(text=util.normalize_space(r.readparagraph()))

    # --- recognizers ----------------------------------------------------

    def is_avdelning(self):
        # max three lines (AVD VII in 2009:400 has 3)
        p = self.reader.peekparagraph()
        if p.count("\n") > 2:
            return False
        ordinal = self.id_of_avdelning()
        # ordinal must advance, and chapter '1' suggests we are inside a
        # TOC excerpt rather than at a real avdelning (2011:1244)
        return bool(ordinal and
                    util.numcmp(ordinal, self.current_avdelning) > 0 and
                    self.current_chapter != "1")

    def id_of_avdelning(self):
        # The styles in use: "FÖRSTA AVDELNINGEN" + label line (1998:808 et
        # al), "Avd. 1. Bestämmelser..." (1979:1152), "Avdelning I Fartyg"
        # (1994:1009), "AVD. I INNEHÅLL..." (1999:1229), "AVDELNING I. ..."
        # (2009:400), "AVD. A ..." (2010:110), "1 avd." (1959:287)
        p = self.reader.peekline()
        if p.lower().endswith("avdelningen") and len(p.split()) == 2:
            ordinal = util.swedish_ordinal(p.split()[0])
            return str(ordinal) if ordinal else None
        elif p.startswith("AVD. ") or p.startswith("AVDELNING "):
            roman = re.split(r"\s+", p)[1]
            if roman.endswith("."):
                roman = roman[:-1]
            # 2010:110 uses single letters; "AVD. C" must not parse as roman
            if util.re_roman(roman) and self.basefile != "2010:110":
                return str(util.from_roman(roman))
            elif roman in ("A", "B", "C", "D", "E", "F", "G", "H"):
                return roman
        elif p.startswith("Avdelning "):
            roman = re.split(r"\s+", p)[1]
            if util.re_roman(roman):
                return str(util.from_roman(roman))
        elif p[2:6] == "avd.":
            if p[0].isdigit():
                return p[0]
        elif p.startswith("Avd. "):
            idstr = re.split(r"\s+", p)[1]
            if idstr.isdigit():
                return idstr
        return None

    def tok_avdelning(self):
        ordinal = self.id_of_avdelning()
        self.current_avdelning = ordinal
        rubrik = self.reader.readline()
        underrubrik = None
        try:
            has_underrubrik = (
                self.reader.peekline(1) == "" and
                self.reader.peekline(3) == "" and
                not (self.is_kapitel(self.reader.peekline(2)) or
                     self.is_underavdelning(self.reader.peekline(2))))
        except IOError:
            # a trailing avdelning heading with nothing after it: there is
            # no room for an underrubrik, so the heading itself is the last
            # event -- don't let the lookahead failure drop it (rule:
            # narrow-what-you-catch)
            has_underrubrik = False
        if has_underrubrik:
            self.reader.readline()
            underrubrik = self.reader.readline()
        return OpenAvdelning(ordinal=ordinal, rubrik=rubrik,
                             underrubrik=underrubrik)

    def is_underavdelning(self, p=None):
        # only two statutes use this structural element
        if self.basefile not in ("1942:740", "2010:110"):
            return False
        if p is None:
            p = self.reader.peekparagraph()
        return bool(p.count("\n") < 2 and
                    re.match(r"^[IVX]+\.? +[A-ZÅÄÖ]", p) and
                    (not p.endswith(".") or p.endswith("m.m.")))

    def tok_underavdelning(self):
        para = self.reader.readparagraph()
        ordinal, rubrik = para.split(" ", 1)
        if ordinal.strip().endswith("."):
            ordinal = ordinal.strip()[:-1]
        return OpenUnderavdelning(ordinal=ordinal, rubrik=rubrik)

    def is_upphavt_kapitel(self):
        return re_ChapterRevoked(self.reader.peekline()) is not None

    def tok_upphavt_kapitel(self):
        ordinal = self.id_of_kapitel()
        return UpphavtKapitelEv(ordinal=ordinal, text=self.reader.readline())

    def is_kapitel(self, p=None):
        ordinal = self.id_of_kapitel(p)
        if not ordinal:
            return False
        # equal ordinal can be a title change for the same chapter
        if util.numcmp(ordinal, self.current_chapter) >= 0:
            # the § at hand contains a TOC whose lines look like chapter
            # headings: either announced outright ("… är uppdelat enligt
            # följande." -- in_toc, set by tok_paragraf; 2023:200 carries its
            # TOC in 1 kap. 2 §), or inferred from seeing only a single § so
            # far in chapter 1 (2011:1244). An announced listing must open at
            # "1 kap." before it fakes anything: after a *dash-form* listing
            # (whose lines never look like chapters) the first chapter-shaped
            # line is the real next chapter (2026:667). The high-water
            # heuristic only covers TOCs without the "N kap. - Title" dash
            # form (those are rejected by shape in id_of_kapitel, which
            # leaves fake_chapter at 0 -- so once such a TOC has been seen we
            # must not let this branch mis-fake the real body chapters).
            if ((self.current_chapter == "1" and self.current_section == "1"
                    and not self.saw_dash_toc)
                    or (self.in_toc
                        and (self.fake_chapter != "0" or ordinal == "1"))):
                if util.numcmp(ordinal, self.fake_chapter) < 0:
                    return True
                else:
                    self.fake_chapter = ordinal
                    return False
            if p is None and self._toc_run_ahead(ordinal):
                # an *unannounced* TOC: this chapter-shaped paragraph opens a
                # run of TOC_RUN+ consecutive ones with nothing between them.
                # Real chapters always carry at least a rubrik or a § before
                # the next chapter (an empty chapter exists only as an
                # upphävt-notice, excluded in the lookahead), so a bare run
                # this long is a listing. Arm in_toc so the rest of the run
                # takes the high-water branch above.
                self.in_toc = True
                self.fake_chapter = ordinal
                return False
            return True
        return False

    def _toc_run_ahead(self, first_ordinal):
        """Whether the chapter-shaped paragraph at hand opens a run of at
        least TOC_RUN consecutive chapter-shaped paragraphs with ascending
        ordinals -- none an upphävt-kapitel notice ("4 kap. har upphävts
        genom lag (2005:20).", which legitimately leaves a title-only
        chapter)."""
        prev = first_ordinal
        for i in range(2, TOC_RUN + 1):
            try:
                p = self.reader.peekparagraph(i).replace("\n", " ")
            except IOError:
                return False
            if re_ChapterRevoked(p):
                return False
            ordinal = self.id_of_kapitel(p)
            if not ordinal or util.numcmp(ordinal, prev) <= 0:
                return False
            prev = ordinal
        return True

    def id_of_kapitel(self, p=None):
        if not p:
            p = self.reader.peekparagraph().replace("\n", " ")
        p, upphor, ikrafttrader = andrings_datum(p)
        m = re_ChapterId(p)
        if not m:
            return None
        # "N kap. - Title" (a dash separating number and title) is a table-of-
        # contents entry, not a heading -- the body repeats it as "N kap. Title"
        # without the dash. The TOC may sit outside 1 kap. 1 § (eg the 2026
        # "Balkens innehåll" § in 1981:774), so reject it here by shape rather
        # than relying on the §1 high-water heuristic below.
        if p[m.span()[1]:].lstrip().startswith(("-", "–", "—")):
            self.saw_dash_toc = True
            return None
        # paragraphs *referring* to chapters typically end in these ways
        # (a real chapter heading does not), eg a TOC line ending in ","
        if (p.endswith(",") or
                p.endswith(";") or
                p.endswith(" och") or   # 1998:808 kap. 3 spans two lines
                p.endswith(" om") or
                p.endswith(" samt") or
                (p.endswith(".") and not
                 (m.span()[1] == len(p) or  # entire p is eg "6 kap." (1962:700)
                  p.endswith(" m.m.") or
                  p.endswith(" m. m.") or
                  p.endswith(" m.fl.") or
                  p.endswith(" m. fl.") or
                  re_ChapterRevoked(p)))):
            return None
        # "1 kap. 5 §" is a headline referencing a section (2005:1207)
        if (p.endswith(" \xa7") or
                p.endswith(" \xa7\xa7") or
                (p.endswith(" stycket") and " \xa7 " in p)):
            return None
        return m.group(1)

    def tok_kapitel(self):
        ordinal = self.id_of_kapitel()
        para = self.reader.readparagraph()
        line, upphor, ikrafttrader = andrings_datum(para)
        self.headline_level = 0
        self.current_section = "0"
        self.fake_chapter = "0"
        self.in_toc = False
        self.current_chapter = ordinal
        return OpenKapitel(ordinal=ordinal, rubrik=util.normalize_space(line),
                           upphor=upphor, ikrafttrader=ikrafttrader)

    def is_upphavd_paragraf(self):
        return re_SectionRevoked(self.reader.peekline()) is not None

    def tok_upphavd_paragraf(self):
        ordinal = self.id_of_paragraf(self.reader.peekline())
        self.current_section = ordinal
        return UpphavdParagrafEv(ordinal=ordinal, text=self.reader.readline())

    def is_paragraf(self, p=None):
        if not p:
            p = self.reader.peekparagraph()
        ordinal = self.id_of_paragraf(p)
        if ordinal is None:
            return False
        if ordinal == "1":
            return True
        # a smaller § number than the current one is probably a reference,
        # not a new section (1991:1469 1 kap. 7 §)
        if util.numcmp(ordinal, self.current_section) < 0:
            return False
        # a reference can also have a larger number (1994:260, 2007:972);
        # real sections start with an upper-case letter
        firstcharidx = len(ordinal) + len(" \xa7 ")
        if len(p) > firstcharidx and p[firstcharidx].islower():
            return False
        return True

    def id_of_paragraf(self, p):
        match = re_SectionId.match(p)
        if match:
            return match.group(1)
        match = re_SectionIdOld.match(p)
        if match:
            return match.group(1)
        return None

    def tok_paragraf(self):
        r = self.reader
        firstline = r.peekline()
        ordinal = self.id_of_paragraf(r.peekparagraph())
        self.current_section = ordinal
        r.read(len(ordinal) + len(" \xa7 "))

        # really old laws split sections into "moment": "1 § 2 mom."
        match = re_ElementId.match(firstline)
        moment = None
        if match:
            moment = match.group(1)
            r.read(len(moment) + len(" mom. "))

        fixedline, upphor, ikrafttrader = andrings_datum(firstline)
        # skip past the /Upphör.../ and /Ikraftträder.../ directives, which
        # sit directly after the section number
        r.read(len(firstline) - len(fixedline))
        if ikrafttrader and ordinal == "1" and self.current_chapter == "1":
            # the expired and the enacted version of 1 kap. 1 § can both
            # contain a TOC; reset so the second copy is also detected
            self.fake_chapter = "0"
        first_stycke = util.normalize_space(r.readparagraph())
        # a § announcing the law's chapter listing ("Lagens innehåll är
        # uppdelat enligt följande.", 2023:200 1 kap. 2 §) opens a TOC whose
        # lines is_kapitel must read as fake chapter headings; the flag holds
        # until the next § (the listing always ends before one)
        self.in_toc = first_stycke.endswith("uppdelat enligt följande.")
        return OpenParagraf(
            ordinal=ordinal, moment=moment, upphor=upphor,
            ikrafttrader=ikrafttrader, first_stycke=first_stycke)

    def is_rubrik(self, p=None):
        if p is None:
            p = self.reader.peekparagraph()
            indirect = False
        else:
            indirect = True

        if len(p) > 0 and p[0].lower() == p[0] and not p.startswith("/Rubriken"):
            return False
        # the longest known legitimate headline is just under this (2:15 IL)
        if len(p) > 135:
            return False
        if self.is_paragraf(p):
            return False
        if self.is_numrerad_lista(p):
            return False
        if self.is_strecksatslista(p):
            return False
        if (p.endswith(".") and
                not (p.endswith("m.m.") or p.endswith("m. m.") or
                     p.endswith("m.fl.") or p.endswith("m. fl."))):
            return False
        if (p.endswith(",") or p.endswith(":") or
                p.endswith("samt") or p.endswith("eller")):
            return False
        # TOC lines for appendices in 2016:1145 look like headlines
        if re.match(r"Bilaga \d(| \w) – ", p):
            return False
        if re_ChangeNote.search(p):  # eg 1994:1512 8 §
            return False
        if p.startswith("/") and p.endswith("./"):
            return False

        try:
            nextp = self.reader.peekparagraph(2)
        except IOError:
            nextp = ""

        # a headline is followed by a section or another headline (only
        # checked one level deep to avoid infinite recursion)
        if not indirect:
            if not self.is_paragraf(nextp) and not self.is_rubrik(nextp):
                return False
        # headline followed by another headline: subsequent ones are
        # sub-headlines, unless this one carries a date directive (then it
        # is the same heading reworded from a certain date)
        if not indirect and self.is_rubrik(nextp) and andrings_datum(p)[0] == p:
            self.headline_level = 1
        return True

    def tok_rubrik(self):
        para = self.reader.readparagraph()
        line, upphor, ikrafttrader = andrings_datum(para)
        underrubrik = False
        if self.headline_level == 2:
            underrubrik = True
        elif self.headline_level == 1:
            self.headline_level = 2
        return RubrikEv(text=util.normalize_space(line), underrubrik=underrubrik,
                        upphor=upphor, ikrafttrader=ikrafttrader)

    # --- lists ------------------------------------------------------------

    def is_numrerad_lista(self, p=None):
        return self.id_of_numrerad_lista(p) is not None

    def id_of_numrerad_lista(self, p=None):
        if not p:
            p = self.reader.peekline()
        match = re_DottedNumber.match(p) or re_NumberRightPara(p)
        return match.group(1).replace(" ", "") if match else None

    def is_strecksatslista(self, p=None):
        if not p:
            p = self.reader.peekline()
        return re_Strecksatslista.match(p) is not None

    def is_bokstavslista(self):
        return re_Bokstavslista.match(self.reader.peekline()) is not None

    def tok_listitem(self, kind):
        if kind == "numrerad":
            ordinal = self.id_of_numrerad_lista()
            # NB: only the "1. " form is stripped from the text; the "1) "
            # form is kept (faithful to the old parser, whose output the
            # golden corpus reflects)
            text = re_DottedNumber.sub("", self.reader.readparagraph())
            return ListItemEv(kind=kind, ordinal=ordinal, text=text)
        elif kind == "bokstav":
            m = re_Bokstavslista.match(self.reader.peekline())
            assert m
            ordinal = m.group(1)
            text = re_Bokstavslista.sub("", self.reader.readparagraph())
            return ListItemEv(kind=kind, ordinal=ordinal.replace(" ", ""),
                              text=text)
        else:
            text = re_Strecksatslista.sub("", self.reader.readparagraph())
            return ListItemEv(kind=kind, ordinal=None, text=text)

    # --- tables -----------------------------------------------------------

    def is_tabell(self, p=None, assume_table=False, require_columns=False):
        shortline = 55
        shorterline = 52
        if not p:
            p = self.reader.peekparagraph()
        # a sloppily formatted table can have a right cell that runs one
        # line further than the next row's empty right cell; look only at
        # the first such part
        lines = []
        emptyleft = False
        lastline = ""
        for l in p.split(self.reader.linesep):
            lastline = l
            if l.startswith(" "):
                emptyleft = True
                lines.append(l)
            else:
                if emptyleft:
                    break
                lines.append(l)

        numlines = len(lines)
        # heuristic 1: every line is short (a table row with only a left cell)
        if (assume_table or numlines > 1) and not require_columns:
            matches = [l for l in lines if len(l) < shortline]
            if numlines == 1 and "  " in lines[0]:
                return True
            if len(matches) == numlines:
                # exception: no column division and looks like the start of
                # a section -> not a table
                if "  " not in lines[0] and self.is_paragraf(p):
                    return False
                # exception: a table whose first row has only a left column
                # MUST be followed by a column-divided row
                try:
                    p2 = self.reader.peekparagraph(2)
                except IOError:
                    p2 = ""
                try:
                    p3 = self.reader.peekparagraph(3)
                except IOError:
                    p3 = ""
                if not assume_table and not self.is_tabell(
                        p2, assume_table=True, require_columns=True):
                    return False
                elif numlines == 1:
                    # a single short line could be a short headline; if a
                    # section or headline+section follows, the table is over
                    if self.is_paragraf(p2):
                        return False
                    if self.is_rubrik(p2) and self.is_paragraf(p3):
                        return False
                    # the transition from table (eg at the end of an
                    # appendix, as in SekrL) to transitional provisions
                    if self.is_overgangsbestammelser():
                        return False
                    if self.is_bilaga():
                        return False
                return True

        # heuristic 2: every line has runs of multiple spaces (columns)
        matches = [l for l in lines if "  " in l]
        if numlines > 1 and len(matches) == numlines:
            return True

        # heuristic 3: every line is short OR column-divided
        if (assume_table or numlines > 1) and not require_columns:
            matches = [l for l in lines if "  " in l or len(l) < shorterline]
            if len(matches) == numlines:
                return True

        # heuristic 4: single line with unmistakable column division
        # (NB: examines the last raw line, faithful to the old parser)
        if numlines == 1 and "   " in lastline:
            return True
        return False

    def tok_tabell(self):
        r = self.reader
        pcnt = 0
        t = Tabell()
        autostrip = r.autostrip
        r.autostrip = False
        try:
            p = r.readparagraph()
            trs, tabstops = self.make_tabellrad(p)
            t.rows.extend(trs)
            current_upphor = None
            current_ikrafttrader = None
            while not r.eof():
                line, upphor, ikrafttrader = andrings_datum(
                    r.peekline(), match=True)
                if upphor:
                    current_upphor = upphor
                    r.readline()
                    pcnt = 1
                elif ikrafttrader:
                    current_ikrafttrader = ikrafttrader
                    current_upphor = None
                    r.readline()
                    pcnt = -pcnt + 1
                elif self.is_tabell(assume_table=True):
                    kwargs = {}
                    if pcnt > 0:
                        kwargs["upphor"] = current_upphor
                        pcnt += 1
                    elif pcnt < 0:
                        kwargs["ikrafttrader"] = current_ikrafttrader
                        pcnt += 1
                    elif pcnt == 0:
                        current_ikrafttrader = None
                    p = r.readparagraph()
                    if p:
                        trs, tabstops = self.make_tabellrad(
                            p, tabstops, kwargs=kwargs)
                        t.rows.extend(trs)
                else:
                    break
        finally:
            r.autostrip = autostrip
        return TabellEv(tabell=t)

    def make_tabellrad(self, p, tabstops=None, kwargs=None):
        """Split a text paragraph into table rows/cells by analyzing runs of
        spaces, reusing tab stop positions from earlier rows. Tolerates the
        ragged column alignment of the source data."""
        kwargs = kwargs or {}

        def make_cell(text):
            if len(text) > 1:
                text = re_dehyphenate("", text)
            return util.normalize_space(text)

        cols = ["", "", "", "", "", "", "", ""]  # no table has more than 8
        statictabstops = tabstops is not None
        if not statictabstops:
            tabstops = [0, 0, 0, 0, 0, 0, 0, 0]
        lines = p.split(self.reader.linesep)
        numlines = len([x for x in lines if x])
        potentialrows = len([x for x in lines
                             if x and (x[0].isupper() or x[0].isdigit())])
        linecount = 0
        # all lines starting with upper-case/digit: one table row per line
        singlelinemode = numlines > 1 and numlines == potentialrows

        rows = []
        emptyleft = False
        for l in lines:
            if l == "":
                continue
            linecount += 1
            charcount = 0
            spacecount = 0
            lasttab = 0
            colcount = 0
            if singlelinemode:
                cols = ["", "", "", "", "", "", "", ""]
            if l[0] == " ":
                emptyleft = True
            else:
                if emptyleft:
                    # a row with an empty left cell ends here; start new row
                    rows.append(cols)
                    cols = ["", "", "", "", "", "", "", ""]
                    emptyleft = False

            for c in l:
                charcount += 1
                if c == " ":
                    spacecount += 1
                else:
                    if spacecount > 1:  # cell boundary found; fill the cell
                        cols[colcount] += "\n" + l[
                            lasttab:charcount - (spacecount + 1)]
                        lasttab = charcount - 1
                        # handle empty left cells
                        if linecount > 1 or statictabstops:
                            # tolerate up to seven chars of misalignment
                            if tabstops[colcount + 1] + 7 < charcount:
                                if len(tabstops) <= colcount + 2:
                                    tabstops.append(0)
                                    cols.append("")
                                if tabstops[colcount + 2] != 0:
                                    colcount += 1
                        colcount += 1
                        if len(tabstops) <= charcount:
                            tabstops.append(0)
                            cols.append("")
                        tabstops[colcount] = charcount
                    spacecount = 0
            cols[colcount] += "\n" + l[lasttab:charcount]
            if singlelinemode:
                rows.append(cols)

        if not singlelinemode:
            rows.append(cols)

        res = []
        for r in rows:
            tr = Tabellrad(**kwargs)
            emptyok = True
            for c in r:
                if c or emptyok:
                    tr.cells.append(make_cell(c.replace("\n", " ")))
                    if c.strip() != "":
                        emptyok = False
            res.append(tr)
        return res, tabstops

    # --- document parts ----------------------------------------------------

    def is_overgangsbestammelser(self):
        l = self.reader.peekline()
        if l not in OB_SEPARATORS:
            fuzz = difflib.get_close_matches(l, OB_SEPARATORS, 1, 0.9)
            if fuzz:
                log.debug("%s: assuming '%s' means '%s'",
                          self.basefile, l, fuzz[0])
            else:
                return False
        try:
            # if followed by a regular section it is an ordinary headline,
            # not the separator before the transitional provisions
            if self.is_paragraf(self.reader.peekparagraph(2)):
                return False
        except IOError:
            pass
        return True

    def is_overgangsbestammelse(self):
        return re_SimpleSfsId.match(self.reader.peekline()) is not None

    def is_bilaga(self):
        line, upphor, ikrafttrader = andrings_datum(self.reader.peekline())
        if line.endswith(" /Bilagan är inte med här/"):
            line = line.replace(" /Bilagan är inte med här/", "")
        return line in BILAGA_LINES

    def tok_bilaga(self):
        rubrik = self.reader.readparagraph()
        rubrik, upphor, ikrafttrader = andrings_datum(rubrik)
        return OpenBilaga(rubrik=rubrik, upphor=upphor,
                          ikrafttrader=ikrafttrader)
