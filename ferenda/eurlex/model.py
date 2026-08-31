"""Typed model for an EU legal document parsed from Formex.

Formex marks up two document families we care about -- legislation/treaties
(`ACT`) and case law (`JUDGMENT`) -- with deep, faithful structure (parts,
titles, chapters, articles, paragraphs, points; recitals; judgment paragraphs
and the ruling). We flatten that into an ordered list of typed `Block`s rather
than a nesting tree (like the DV and forarbete models): each block carries its
structural number and a citation `anchor`, which is enough to render the
document and to resolve pinpoint citations ("Article 5 of Directive ...") to a
fragment, without the bookkeeping of a tree.

The document URI is the language-neutral CELEX URI the citation engine mints
for EU references (`https://lagen.nu/celex/{CELEX}`), so a citation to an
act and the act itself agree by construction.
"""

import re
from dataclasses import dataclass, field

from ..lib.formex import Block

# the language-neutral CELEX URI the citation engine mints for EU references, so
# a citation to an act and the act itself agree by construction
BASE = "https://lagen.nu/celex/%s"


# --------------------------------------------------------------------------
# short label -- a distinctive human handle derived from the official title
# --------------------------------------------------------------------------

# a trailing parenthetical that is *not* a naming short title (EEA-relevance /
# recast / codification boilerplate), stripped before anything else is read
_LABEL_BOILERPLATE = re.compile(
    r"\s*\((?:Text av betydelse för EES|Text with EEA relevance|EES-text|"
    r"omarbetning|recast|kodifierad version|kodifiering|codification|"
    r"konsolidering|konsoliderad version|consolidation|consolidated version)\)",
    re.IGNORECASE)
# the act's number designation: the parenthesised regulation form
# "(EU) 2022/2523" / "(EG) nr 593/2008" / "(EC) No 593/2008", or the suffixed
# directive form "2003/49/EG" / "74/637/EEC". Everything before it (issuing body
# + act type) is dropped. Both the Swedish (EG/EEG) and English (EC/EEC) treaty
# abbreviations are matched, so an English-only manifestation is handled too.
_ACT_ABBR = r"EU|EG|EEG|EC|EEC|Euratom"
_LABEL_DESIGNATION = re.compile(
    r"\((?:%s)\)(?:\s+(?:nr|No))?\s+\d[\d/]*"
    r"|\b\d{1,4}/\d+/(?:%s)\b" % (_ACT_ABBR, _ACT_ABBR))
# the date phrase between the designation and the subject ("av den 14 december 2022")
_LABEL_DATE = re.compile(r"\b(?:av den|of)\s+\d{1,2}\s+\w+\s+\d{4}\b", re.IGNORECASE)
# the low-value tail: amendment / repeal / cross-reference clauses, dropped
_LABEL_TAIL = re.compile(
    r"\s+(?:och om (?:ändring|upphävande)|samt om|enligt|and (?:amending|repealing)|"
    r"amending|repealing|pursuant to)\b.*$", re.IGNORECASE | re.DOTALL)
# the connector that introduces the subject ("om …"/"on …"), with any stray
# leading punctuation (a comma sometimes follows the date)
_LABEL_LEAD = re.compile(r"^[\s,;:.]*(?:om|on)\b\s*", re.IGNORECASE)
# a corrigendum names the act it corrects: it opens with "Rättelse till …" and
# closes with that act's own OJ coordinate in parentheses. The coordinate is not
# a short title -- read as one it labelled 1 644 corrigenda "(EU) 2022/2555
# Europeiska unionens officiella tidning L 333 av den 27 december 2022" -- and
# the opening words are the only thing that tells a corrigendum apart from the
# act, so they stay in front of the name it derives.
_LABEL_CORRIGENDUM = re.compile(
    r"^\s*(rättelser? (?:till|av)|rättelseprotokoll till|erratum till|"
    r"corrigend(?:um|a) to)\s+", re.IGNORECASE)
_LABEL_OJREF = re.compile(
    r"\s*\(\s*(?:Europeiska (?:unionens|gemenskapernas) officiella tidning|"
    r"Official Journal|EUT|EGT|OJ)\b[^()]*\)"
    # a C-series corrigendum prints its own OJ number after the coordinate
    # ("… (EUT C 202 av den 7 juni 2016) 2017/C 059/01")
    r"(?:\s*\(?\d{4}/C\s?\d+/\d+\)?)?\s*$", re.IGNORECASE)


def _corrigendum(title):
    """(opening words, title) of a corrigendum: the "Rättelse till" it opens
    with, which the label keeps, and the corrected act's title with the trailing
    OJ coordinate removed. Any other title comes back as ('', title)."""
    m = _LABEL_CORRIGENDUM.match(title)
    if not m:
        return "", title
    return m.group(0).strip() + " ", _LABEL_OJREF.sub("", title[m.end():])


def official_short_title(title):
    """The established short title an act carries in a trailing parenthesis --
    "… (allmän dataskyddsförordning)" -> "Allmän dataskyddsförordning",
    "… (cyberresiliensförordningen) (Text av betydelse för EES)" ->
    "Cyberresiliensförordningen" -- capitalised, or None when the title carries no
    such naming parenthetical.

    The EEA-relevance / recast boilerplate parenthetical (and the trailing OJ note
    after it) is stripped first; what then sits in a trailing "(…)" is a real short
    title when it contains a lowercase letter -- so an all-caps marker like "(SUB)"
    is rejected -- and is not itself an act-number designation. A single-word short
    title ("cyberresiliensförordningen") is accepted: Swedish compounds the whole
    name into one word, so a space must not be required.

    A corrigendum keeps its opening words and reads the short title of the act it
    corrects: "Rättelse till … (NIS 2-direktivet) (Europeiska unionens officiella
    tidning L 333 …)" -> "Rättelse till NIS 2-direktivet"."""
    prefix, title = _corrigendum(re.sub(r"\s+", " ", title or "").strip())
    title = _LABEL_BOILERPLATE.sub("", title).strip()
    m = re.search(r"\(([^()]{3,})\)\s*$", title)
    if (m and re.search(r"[a-zåäöéèüæø]", m.group(1))
            and not _LABEL_DESIGNATION.search(m.group(0))):
        name = m.group(1).strip()
        # after "Rättelse till" the name reads on in the same sentence, so it
        # keeps the case the act writes it in
        return prefix + name if prefix else name[:1].upper() + name[1:]
    return None


def short_label(title):
    """A short, distinctive label for an EU act, derived from its official title --
    what a browse index or a search result shows instead of the bare CELEX.

    Prefers the established short title the act carries in a trailing parenthesis
    ("… (allmän dataskyddsförordning)" -> "(EU) 2016/679 Allmän dataskyddsförordning").
    For an act with no such short title, trims the title to its number designation
    plus the substantive subject, dropping the issuing body, act type, date and the
    low-value amendment/repeal/cross-reference tail
    ("Rådets direktiv (EU) 2022/2523 av den 14 december 2022 om säkerställande av en
    global minimiskattenivå …" -> "(EU) 2022/2523 Säkerställande av en global
    minimiskattenivå …"). Returns None for an empty title. Tuned for the Swedish
    manifestation (the catalogued one); English connectors are covered so an
    English-only act still trims sensibly."""
    title = re.sub(r"\s+", " ", title or "").strip()
    if not title:
        return None
    prefix, title = _corrigendum(title)
    short = official_short_title(title)
    title = _LABEL_BOILERPLATE.sub("", title).strip()
    d = _LABEL_DESIGNATION.search(title)
    designation = d.group(0) if d else None
    if short:
        name = short
    elif d:
        name = _LABEL_LEAD.sub("", _LABEL_TAIL.sub(
            "", _LABEL_DATE.sub("", title[d.end():]))).strip().rstrip(".")
    else:
        name = _LABEL_TAIL.sub("", title).strip().rstrip(".")
    name = re.sub(r"\s+", " ", name)
    name = prefix + name if prefix else (name[:1].upper() + name[1:] if name else name)
    return "%s %s" % (designation, name) if designation else name


# a date phrase that opens the title's body ("av den 30 oktober 1990" / "of 1
# December 1974") -- distinguishes the title line from a bare heading
_TITLE_DATE = re.compile(r"\b(?:av den|of)\s+\d", re.IGNORECASE)


def looks_like_act_title(text):
    """Whether a paragraph looks like an act's official title: it carries the
    number designation *and* an "av den …"/"of …" date phrase. Used to recover
    the title from the legacy 'Avis juridique important' HTML, which has no
    semantic title class -- the title is the class-less header line that has this
    shape (the issuing body + act type + "(EEG) nr …" + date), ahead of the
    recitals that, while they cite other acts by number, are filtered out by the
    surrounding scan stopping at the preamble."""
    return bool(text and _LABEL_DESIGNATION.search(text)
                and _TITLE_DATE.search(text))


# block kinds, in rough document order of where they occur:
#   title       the document title (ACT) / case title (JUDGMENT)
#   keyword     a subject keyword (JUDGMENT index)
#   citation    a preamble "having regard to ..." visa
#   recital     a numbered preamble recital (CONSID)
#   preamble    preamble framing text (PREAMBLE.INIT / .FINAL)
#   heading     a division/section/chapter title in the body
#   article     an article title (TI.ART)
#   paragraph   a body paragraph (numbered PARAG, ALINEA, or judgment NP) --
#               its text is the paragraph's *first* stycke
#   stycke      a second or later sub-paragraph of that paragraph (ALINEA),
#               cited as "artikel 9.2 andra stycket" and anchored 9.2.S2
#   point       a list item (LIST/ITEM)
#   ruling      the operative part of a judgment (JURISDICTION/DISPOSITIF)
#   signature   place/date/signatories


@dataclass
class EurlexDoc:
    celex: str
    uri: str                   # https://lagen.nu/celex/{CELEX}
    doctype: str               # regulation | directive | decision | judgment
                               # | treaty | act
    lang: str                  # 3-letter code (swe, eng)
    title: str = ""
    date: str | None = None    # ISO date of the document
    ecli: str | None = None    # case law
    oj: str | None = None      # Official Journal reference (e.g. "L 333")
    body: list[Block] = field(default_factory=list)   # document order
    # a consolidated wording (CONSLEG): which one, what it folds in, and what
    # changed each article. `version` is set only on a *historical* lydelse --
    # the latest consolidation serves at the act's own uri and is marked by
    # `consolidation` alone.
    version: str | None = None        # this lydelse's date ("2024-10-18")
    consolidation: dict | None = None # the FAM.COMP register + this text's date
    provenance: dict | None = None    # article number -> {action, by}
