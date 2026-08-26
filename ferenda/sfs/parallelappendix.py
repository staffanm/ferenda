"""Parser for parallel-text convention appendices — a statute that incorporates
a treaty as the same text printed in two or three languages side by side (the
European Convention on Human Rights, tax and social-security conventions, tax
information-exchange agreements, and so on). It parses this one appendix shape
with **no per-law knowledge**; it does not handle appendices in general.

It is wired into the SFS pipeline (:func:`sfs._assemble`): a statute whose sole
Bilaga is a parallel corpus is parsed here into a :class:`Konventionsbilaga`;
anything else — or an appendix that looks parallel but doesn't line up — falls
back to the ordinary flat statute parser. The goal is *a* valid aligned parse,
not a byte-for-byte match to an earlier representation. See
``parallelappendix.md`` for the corpus status and remaining boundaries.

Approach in one line: **structure finds the blocks, language detection labels
them.** Repeated article sequences locate the large language copies; langdetect
then labels each copy as a whole. Within a block, ordinary structural rules (a
fresh "Article 1" or a "Protocol" heading starts a new instrument) do the rest.
Harmless layout joins are normalised; duplicated, missing or reordered legal
text is never guessed away.

Treaty *identity* stays out of this structural parsing: instruments carry their
protocol number (the base convention none), which the SFS projection turns into
a stable ``#B1``/``#B1P4`` anchor and resolves to a treaty URI through the
curated ``sfs/data/incorporates.json`` map.
"""
import re
from dataclasses import dataclass, field

from langdetect import DetectorFactory, LangDetectException, detect

from ..lib.util import normalize_space
from .model import (
    Konventionsartikel,
    Konventionsavdelning,
    Konventionsbilaga,
    Konventionsinstrument,
    Konventionsstycke,
)

# langdetect is randomised; pin it so a build is reproducible.
DetectorFactory.seed = 0


class AppendixMisaligned(Exception):
    """A detected parallel appendix does not line up across languages.

    Detection is heuristic; when the language blocks don't share the same
    instruments/articles the caller falls back to the ordinary flat parser
    rather than emit a partial parse.
    """

RE_APPENDIX = re.compile(r"\n[ \t]*\nBilaga[ \t]*\n[ \t]*\n")
DIRECTIVE = r"(?:/[^/\n]*/\s*)*"
# An article heading: "Article 5", "Article 5 - Liberty", "Artikel I",
# "Article premier" -- but NOT a mid-sentence "Article 5 shall apply" (a word,
# with no separating dash/colon, follows the number).
RE_ARTICLE = re.compile(
    rf"^{DIRECTIVE}(?:Article|Artikel|Art\.|Madde)\s+"
    r"(\d+|premier|première|[ivxlcdm]+)"
    r"(?:\s*[–\-:.]\s*\S.*)?$", re.I)
RE_ARTICLE_TOKEN = re.compile(
    rf"{DIRECTIVE}(?:Article|Artikel|Art\.|Madde)\s+"
    r"(\d+|premier|première|[ivxlcdm]+)", re.I)
RE_DIVISION = re.compile(
    rf"^{DIRECTIVE}(?:PART|PARTIE|AVDELNING|CHAPTER|CHAPITRE|KAPITEL|SECTION|TITRE|DEL|TITEL|AVSNITT|BÖLÜM)"
    r"\s+([IVXLCDM0-9]+)\b.*$", re.I)
RE_PROTOCOL = re.compile(
    rf"^{DIRECTIVE}(?:Protocol|Protocole|Protokoll|Tilläggsprotokoll|Additional Protocol"
    r"|Protocole additionnel)\b", re.I)
# a sub-paragraph marker: "1.", "1)", "a)", "(a)", "(iv)"
RE_MARKER = re.compile(r"^(?:\d+[.)]|\(?[a-z]{1,4}\)|\(?[ivx]{1,4}\))\s", re.I)
RE_SENTENCE_END = re.compile(r"[.!?:;…。！？][\"'’”»)]*$")
ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50,
                "c": 100, "d": 500, "m": 1000}


@dataclass
class Division:
    """A grouping heading (part/chapter/section) inside one language block."""
    ordinal: str
    heading: str


@dataclass
class Article:
    ordinal: str
    heading: str
    paragraphs: list[str] = field(default_factory=list)


@dataclass
class Instrument:
    """One treaty or protocol within one language block, children in order."""
    heading: str | None  # the Protocol heading, or None
    ingress: list[str] = field(default_factory=list)  # title + preamble, in order
    children: list[Division | Article] = field(default_factory=list)

    def articles(self) -> list[Article]:
        return [c for c in self.children if isinstance(c, Article)]


@dataclass
class LanguageRun:
    language: str | None                       # langdetect code, e.g. "en"/"fr"/"sv"
    instruments: list[Instrument] = field(default_factory=list)


@dataclass
class LanguageBlock:
    language: str | None
    start: int
    end: int


def ordinal(raw: str) -> str:
    """Normalise an article number: roman and 'premier' both fold to arabic."""
    folded = raw.casefold()
    if folded in ("premier", "première"):
        return "1"
    if folded.isdecimal():
        return str(int(folded))
    total = 0
    previous = 0
    for digit in reversed(folded):
        value = ROMAN_VALUES[digit]
        total += -value if value < previous else value
        previous = value
    return str(total)


def _article_number(paragraph: str) -> int | None:
    match = RE_ARTICLE.match(paragraph)
    return int(ordinal(match.group(1))) if match else None


def paragraphs(text: str) -> list[str]:
    """Split paragraphs and recover structurally unambiguous article headings.

    The source sometimes joins a heading to its title/body, to a preceding
    division, or to the preceding sentence. Recovery requires the next ordinal
    plus a structural boundary; prose such as ``as set out in\nArticle 5 of the
    Convention`` remains prose.
    """
    out: list[str] = []
    previous_article: int | None = None
    for block in re.split(r"\n\s*\n", text):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        first_number = _article_number(lines[0])
        if first_number is not None and len(lines) > 1:
            tail = normalize_space(" ".join(lines[1:]))
            if len(tail.split()) <= 16 and not RE_SENTENCE_END.search(tail):
                out.append(normalize_space(lines[0] + " – " + tail))
            else:
                out.extend((normalize_space(lines[0]), tail))
            previous_article = first_number
            continue
        start = 0
        for index, line in enumerate(lines):
            number = _article_number(line)
            if (index > start and number is not None
                    and previous_article is not None
                    and number == previous_article + 1
                    and RE_SENTENCE_END.search(lines[index - 1])):
                out.append(normalize_space(" ".join(lines[start:index])))
                out.append(normalize_space(line))
                previous_article = number
                start = index + 1
        if start < len(lines):
            paragraph = normalize_space(" ".join(lines[start:]))
            number = _article_number(paragraph)
            if number is not None:
                out.append(paragraph)
                previous_article = number
                continue
            for match in RE_ARTICLE_TOKEN.finditer(paragraph):
                candidate = int(ordinal(match.group(1)))
                before = paragraph[:match.start()].rstrip()
                glued_body = (not before and len(lines) > 1
                               and len(paragraph.split()) >= 10)
                if (previous_article is not None
                        and candidate == previous_article + 1
                        and (glued_body
                             or RE_SENTENCE_END.search(before)
                             or RE_DIVISION.match(before))):
                    if before:
                        out.append(before)
                    out.append(paragraph[match.start():match.end()])
                    if paragraph[match.end():].strip():
                        out.append(paragraph[match.end():].strip())
                    previous_article = candidate
                    break
            else:
                out.append(paragraph)
    return out


def _language(text: str) -> str | None:
    try:
        return detect(text)
    except LangDetectException:
        return None


def _article_language(paras: list[str], start: int, end: int) -> str | None:
    """Detect one Article 1 from its first substantial body text."""
    sample = []
    for paragraph in paras[start + 1:end]:
        sample.append(paragraph)
        if len(re.findall(r"[a-zåäöéèüæøàçœ]+", " ".join(sample).lower())) >= 8:
            break
    return _language(" ".join(sample))


def language_blocks(paras: list[str]) -> list[tuple[str | None, int, int]]:
    """Return [(language, start, end)] for the major language blocks.

    Structure supplies stable boundary candidates: every base convention or
    protocol starts at Article 1. Each complete article sequence is large enough
    to detect reliably, unlike an isolated title or paragraph. Adjacent sequences
    with the same label are instruments in one language copy; a label change
    starts the next copy. The resulting whole blocks are detected once more so
    their public labels do not depend on one instrument's wording.
    """
    starts = [index for index, paragraph in enumerate(paras)
              if _article_number(paragraph) == 1]
    if not starts:
        return []
    sequences = [
        (_article_language(paras, start, end), start, end)
        for start, end in zip(starts, starts[1:] + [len(paras)], strict=True)
    ]
    blocks: list[LanguageBlock] = []
    for language, start, end in sequences:
        if blocks and blocks[-1].language == language:
            blocks[-1].end = end
        else:
            blocks.append(LanguageBlock(language, start, end))
    blocks[0].start = 0
    for index in range(1, len(blocks)):
        language = blocks[index].language
        boundary = blocks[index].start
        floor = blocks[index - 1].start
        while boundary > floor:
            paragraph = paras[boundary - 1]
            if (_language(paragraph) == language
                    or RE_DIVISION.match(paragraph)
                    or RE_PROTOCOL.match(paragraph)
                    or _division_subtitle(paragraph)):
                boundary -= 1
            else:
                break
        blocks[index - 1].end = boundary
        blocks[index].start = boundary
    return [(_language(" ".join(paras[block.start:block.end])),
             block.start, block.end)
            for block in blocks]


def lenient(text: list[str]) -> list[str]:
    """Absorb harmless layout noise: de-hyphenate and rejoin split lines.

    This is normalisation, not error-repair: a word broken across a blank line
    or a continuation line with no sub-paragraph marker is rejoined. Genuine
    content mistakes are never touched here.
    """
    out = []
    for paragraph in text:
        if out and out[-1].rstrip().endswith("-") and paragraph[:1].islower():
            out[-1] = out[-1].rstrip()[:-1] + paragraph
        elif out and not RE_MARKER.match(paragraph) and RE_MARKER.match(out[-1]):
            out[-1] += " " + paragraph
        else:
            out.append(paragraph)
    return out


def _division_subtitle(paragraph: str) -> bool:
    """Whether a paragraph can be the short title after ``CHAPTER II``."""
    words = paragraph.split()
    return bool(words) and len(words) <= 16 and (
        paragraph.isupper() or not RE_SENTENCE_END.search(paragraph))


def parse_block(paras: list[str], start: int, end: int) -> list[Instrument]:
    """Parse one language block into its instruments (treaty + any protocols).

    Each instrument opens with a title/preamble before its first article: the
    base convention with its bare title, a protocol with its ``Protocol …``
    heading. That leading matter is kept as the instrument's ``ingress`` rather
    than discarded — the formal treaty title and "have agreed as follows" recital
    are part of the incorporated text.
    """
    instruments: list[Instrument] = []
    current: Instrument | None = None
    article: Article | None = None
    pending_division: Division | None = None
    for i in range(start, end):
        paragraph = paras[i]
        match = RE_ARTICLE.match(paragraph)
        division = RE_DIVISION.match(paragraph)
        is_protocol = bool(RE_PROTOCOL.match(paragraph))
        starts_instrument = is_protocol or (
            match and ordinal(match.group(1)) == "1"
            and (current is None or any(a.ordinal == "1" for a in current.articles())))
        if starts_instrument:
            current = Instrument(heading=paragraph if is_protocol else None)
            instruments.append(current)
            article = None
            pending_division = None
            if is_protocol:
                continue  # the heading is recorded; nothing else on this line
        elif current is None:
            # the base convention opens with its title before Article 1; create
            # it now so the title and preamble are kept as ingress.
            current = Instrument(heading=None)
            instruments.append(current)
        if match:
            article = Article(ordinal(match.group(1)), paragraph)
            current.children.append(article)
            pending_division = None
        elif division:
            pending_division = Division(division.group(1).upper(), paragraph)
            current.children.append(pending_division)
            article = None
        elif pending_division is not None and _division_subtitle(paragraph):
            pending_division.heading += " " + paragraph
            pending_division = None
        elif article is not None:
            article.paragraphs.append(paragraph)
        elif not current.children:
            current.ingress.append(paragraph)  # title/preamble before article 1
            pending_division = None
        else:
            pending_division = None
    for instrument in instruments:
        instrument.ingress = lenient(instrument.ingress)
        for child in instrument.children:
            if isinstance(child, Article):
                child.paragraphs = lenient(child.paragraphs)
    return [instrument for instrument in instruments if instrument.articles()]


def parse_appendix(text: str) -> list[LanguageRun]:
    """Return the [LanguageRun] for a parallel-corpus appendix.

    A structural run with no articles is boundary material, not a language copy
    of the treaty, so it is dropped.
    """
    paras = paragraphs(text)
    runs = [LanguageRun(lang, parse_block(paras, start, end))
            for lang, start, end in language_blocks(paras)]
    return [run for run in runs
            if any(inst.articles() for inst in run.instruments)]


# ---------------------------------------------------------------------------
# Mapping onto the document model
# ---------------------------------------------------------------------------

RE_PROTOCOL_NUMBER = re.compile(r"\b(\d+)\b")


def _protocol_number(heading: str | None) -> str | None:
    """The protocol number for one instrument: the number printed in its heading
    (``Protocol No. 4`` → ``"4"``), ``"1"`` for an unnumbered additional/first
    protocol, or ``None`` for the base convention (which carries no heading)."""
    if heading is None:
        return None
    match = RE_PROTOCOL_NUMBER.search(heading)
    return match.group(1) if match else "1"


def _aligned_paragraphs(
        per_language: list[list[str]],
        languages: tuple[str, ...]) -> list[Konventionsstycke]:
    """One Konventionsstycke per row; short languages pad with empty cells."""
    width = max((len(runs) for runs in per_language), default=0)
    padded = [list(runs) + [""] * (width - len(runs)) for runs in per_language]
    return [Konventionsstycke(dict(zip(languages, row, strict=True)))
            for row in zip(*padded, strict=True)]


def _division_groups(instrument: Instrument) -> list[list[Division]]:
    """Return divisions before article 0, article 1, … and after the last."""
    groups: list[list[Division]] = [[]]
    for child in instrument.children:
        if isinstance(child, Article):
            groups.append([])
        else:
            groups[-1].append(child)
    return groups


def _is_subsequence(short: list[str], long: list[str]) -> bool:
    positions = iter(long)
    return all(any(candidate == item for candidate in positions)
               for item in short)


def _aligned_divisions(
        groups: list[list[Division]],
        languages: tuple[str, ...]) -> list[Konventionsavdelning]:
    """Align division headings, permitting a language to omit the heading.

    Articles are the legal structure and remain strictly aligned. Printed
    part/chapter headings are optional navigation: when one language omits one,
    use the longest compatible ordinal sequence and leave that language's title
    empty. Conflicting division order is still a hard misalignment.
    """
    skeleton = max(groups, key=len)
    ordinals = [division.ordinal for division in skeleton]
    if any(not _is_subsequence([division.ordinal for division in group], ordinals)
           for group in groups):
        raise AppendixMisaligned("division order differs across languages")
    indexes = [0] * len(groups)
    out = []
    for division in skeleton:
        rubriker = {}
        for language, group_index, group in zip(
                languages, range(len(groups)), groups, strict=True):
            if (indexes[group_index] < len(group)
                    and group[indexes[group_index]].ordinal == division.ordinal):
                rubriker[language] = group[indexes[group_index]].heading
                indexes[group_index] += 1
            else:
                rubriker[language] = ""
        out.append(Konventionsavdelning(division.ordinal, rubriker))
    return out


def to_model(runs: list[LanguageRun]) -> Konventionsbilaga:
    """Merge the per-language runs into a :class:`Konventionsbilaga`.

    Requires the runs to be structurally parallel (same instruments, same
    ordered children); otherwise raises :class:`AppendixMisaligned` so the
    caller falls back to the flat parser.
    """
    languages = tuple(run.language for run in runs)
    if None in languages or len(set(languages)) != len(languages):
        raise AppendixMisaligned("language blocks are not uniquely labelled")
    languages = tuple(language for language in languages if language is not None)
    reference = runs[0]
    if len({len(run.instruments) for run in runs}) != 1:
        raise AppendixMisaligned("instrument count differs across languages")
    instruments = []
    for index, ref_instrument in enumerate(reference.instruments):
        peers = [run.instruments[index] for run in runs]
        peer_articles = [peer.articles() for peer in peers]
        sequences = [tuple(article.ordinal for article in articles)
                     for articles in peer_articles]
        if any(sequence != sequences[0] for sequence in sequences[1:]):
            raise AppendixMisaligned(
                "instrument %d article sequence differs" % index)
        protokoll = _protocol_number(ref_instrument.heading)
        # The instrument's title is its heading (a protocol) or the first ingress
        # paragraph (the base convention's formal title); the remaining ingress
        # is preamble. Both are kept, aligned across languages.
        if ref_instrument.heading is None:
            rubriker = {lang: (peer.ingress[0] if peer.ingress else "")
                        for lang, peer in zip(languages, peers, strict=True)}
            ingress_lists = [peer.ingress[1:] for peer in peers]
        else:
            rubriker = {lang: (peer.heading or "")
                        for lang, peer in zip(languages, peers, strict=True)}
            ingress_lists = [peer.ingress for peer in peers]
        children: list[Konventionsavdelning | Konventionsartikel] = []
        division_groups = [_division_groups(peer) for peer in peers]
        for position in range(len(peer_articles[0]) + 1):
            children.extend(_aligned_divisions(
                [groups[position] for groups in division_groups], languages))
            if position == len(peer_articles[0]):
                continue
            aligned = [articles[position] for articles in peer_articles]
            children.append(Konventionsartikel(
                aligned[0].ordinal,
                {language: article.heading for language, article in zip(
                    languages, aligned, strict=True)},
                _aligned_paragraphs(
                    [article.paragraphs for article in aligned], languages)))
        instruments.append(Konventionsinstrument(
            protokoll=protokoll, rubriker=rubriker,
            ingresser=_aligned_paragraphs(ingress_lists, languages),
            children=children))
    return Konventionsbilaga(instruments, languages)


def parse(text: str) -> tuple[str, Konventionsbilaga] | None:
    """Return ``(statute_text, Konventionsbilaga)`` for a statute whose sole
    Bilaga is a parallel-text convention appendix, else ``None``.

    Raises :class:`AppendixMisaligned` when it looks parallel but doesn't line
    up (the caller flat-parses instead).
    """
    parts = RE_APPENDIX.split(text.replace("\r", ""))
    if len(parts) != 2:
        return None
    runs = parse_appendix(parts[1])
    if len(runs) < 2:
        return None
    return parts[0].rstrip() + "\n", to_model(runs)
