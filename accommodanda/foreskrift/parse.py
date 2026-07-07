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

import functools
import re
from pathlib import Path

from .. import config
from ..lib.datasets import NAMEDLAWS as SFS_NAMEDLAWS
from ..lib.lagrum import (
    EULAGSTIFTNING,
    LAGRUM,
    LagrumParser,
    interleave,
    load_abbreviations,
    load_namedlaws,
)
from ..lib.pdftext import RE_KAP_MARK, RE_PARA_MARK, page_paragraphs, pdf_pages
from ..lib.util import MONTHS
from .model import Amendment, Consolidation, Regulation, regulation_uri
from .structure import nest

# a föreskrift cites SFS (the empowering law) and EU directives (what it
# implements); it does not cite case law or förarbeten in its operative text.
PARSE_TYPES = [LAGRUM, EULAGSTIFTNING]

RE_RUBRIK_NUM = re.compile(r"^(\d+(?:\.\d+)*)\s+\S")     # "2.1 Heading"
RE_LIST_ITEM = re.compile(r"^(?:\d+[.)]|[-–—•])\s")       # "1." / "– " list rows

# masthead facts (best-effort; the layout that carries them is often mangled)
RE_DATE = re.compile(r"den\s+(\d{1,2})\s+(%s)(?:\s+(\d{4}))?" % "|".join(MONTHS),
                     re.IGNORECASE)
RE_BESLUTAD = re.compile(r"beslutad[e]?\s+den\s+(\d{1,2})\s+(\w+)\s+(\d{4})", re.I)
RE_UTKOM = re.compile(r"Utkom\s+från\s+trycket.*?den\s+(\d{1,2})\s+(\w+)\s+(\d{4})",
                      re.IGNORECASE | re.DOTALL)
RE_IKRAFT = re.compile(r"träder\s+i\s+kraft\s+den\s+(\d{1,2})\s+(\w+)\s+(\d{4})", re.I)
RE_STODAV = re.compile(r"[Mm]ed\s+stöd\s+av\b(.*?)(?:föreskriver|kungör|beslutar|"
                       r"meddelar|följande|\.)", re.DOTALL)
RE_ERSATTER = re.compile(r"\b(?:ersätter|upphäver)\b(.*?)(?:\.|$)", re.DOTALL | re.I)
RE_FS_REF = re.compile(r"\b([A-ZÅÄÖ]+-?FS)\s*(\d{4}):(\d+)")   # NFS/TFS … ELSÄK-FS
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
    stod = RE_STODAV.search(text)
    if stod:
        meta["bemyndigande"] = _dedupe_bemyndigande(
            {r.uri for r in parser.parse_text(stod.group(1), context={})
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
    # upphäver: regulations an "ersätter/upphäver …" clause replaces
    repl = RE_ERSATTER.search(text)
    if repl:
        meta["upphaver"] = sorted({regulation_uri(fs.lower(), y, str(int(n)))
                                   for fs, y, n in RE_FS_REF.findall(repl.group(1))})
    return meta


# --------------------------------------------------------------------------
# record -> Regulation -> artifact
# --------------------------------------------------------------------------

@functools.cache
def _refparser():
    return LagrumParser(load_namedlaws(SFS_NAMEDLAWS), basefile="foreskrift",
                        abbreviations=load_abbreviations(SFS_NAMEDLAWS),
                        parse_types=PARSE_TYPES)


def _fresh_parser():
    """The shared parser with document-lifetime state reset (so one document's
    'samma lag' / learned law names do not bleed into the next)."""
    parser = _refparser()
    parser.reset()
    return parser


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


def parse_pdf(path, identifier, parser):
    """One föreskrift PDF -> (structure tree, its metadata dict). Metadata is read
    from the whole text (the masthead up front, ikraftträdande at the end); the
    structure is built from the operative body only, the masthead dropped."""
    blocks = parse_body(pdf_pages(path), identifier)
    meta = extract_metadata(_full_text(blocks), parser)
    return _structure(blocks[_body_start(blocks):], parser), meta


def _fs_key(designation):
    """Fold an FS designation to its slug form for matching -- lowercase, drop the
    hyphen/spaces, ASCII-fold the Swedish vowels -- so the printed 'ELSÄK-FS'
    matches the agency's ``elsakfs`` slug (and 'NFS' matches 'nfs')."""
    return designation.lower().replace("-", "").replace(" ", "").translate(
        str.maketrans("åäö", "aao"))


def konsoliderad_tom(masthead, fs, base_ars, base_lop):
    """The most recent ändringsförfattning a konsoliderad version folds in -> its
    föreskrift uri, or None. The consolidated PDF's masthead lists the amendments
    incorporated ('Ändringar: FFFS 2014:29, … FFFS 2026:6'); the data point is the
    last of them, so we take the highest-numbered reference to this fs in the
    masthead, excluding the base regulation's own number. This is the one fact
    that pins a consolidation -- not the (irrelevant) 'senast uppdaterad' date."""
    base = (base_ars, str(int(base_lop)))
    refs = [(int(y), int(n)) for f, y, n in RE_FS_REF.findall(masthead)
            if _fs_key(f) == _fs_key(fs) and (y, str(int(n))) != base]
    if not refs:
        return None
    y, n = max(refs)
    return regulation_uri(fs, str(y), str(n))


def parse_consolidation(path, identifier, fs, base_ars, base_lop, parser):
    """A konsoliderad PDF -> (structure tree, konsolideradTom uri). The amendment
    list sits in the masthead (the blocks before the body), so it is read there."""
    blocks = parse_body(pdf_pages(path), identifier)
    start = _body_start(blocks)
    tom = konsoliderad_tom(_full_text(blocks[:start]) or _full_text(blocks),
                           fs, base_ars, base_lop)
    return _structure(blocks[start:], parser), tom


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
    """Absolute path of a body PDF a record's ``files`` entry references. A
    live-harvest file is stored under ``root/fs/<name>``; a frozen-import file
    (the §7g point-at-the-bytes rule) carries a ``legacy`` relpath resolved under
    ``config.LEGACY_ROOT`` in place, never copied into the corpus tree."""
    if "legacy" in entry:
        return config.LEGACY_ROOT / entry["legacy"]
    return Path(root) / fs / entry["name"]


def parse_record(record, root):
    """A harvested record (``<slug>.json``) -> a parsed :class:`Regulation`.
    The regulation body comes from the downloaded ``regulation`` PDF -- when a
    record has none (`files["regulation"]` is `None`; every classifier still
    hangs a landing page's PDFs onto a `regulation`/`consolidation`/`amendment`
    role, but not every entry has to fill each role), the base `Regulation`
    keeps an empty `structure` and only its `consolidations` carry a parsed
    body. Each downloaded consolidation PDF is parsed into its own
    ``structure``. A frozen-import record (§7g) points the ``regulation``
    entry at the frozen PDF via ``body_path``."""
    fs, basefile = record["fs"], record["basefile"]
    arsutgava, lopnummer = basefile.split("/", 1)[1].split(":", 1)
    files = record.get("files", {})
    parser = _fresh_parser()

    reg_file = files.get("regulation") or None
    structure, meta = [], {}
    if reg_file:
        structure, meta = parse_pdf(
            body_path(root, fs, reg_file), record["identifier"], parser)

    reg = Regulation(
        uri=regulation_uri(fs, arsutgava, lopnummer),
        identifier=record["identifier"], fs=fs,
        arsutgava=arsutgava, lopnummer=lopnummer,
        title=record.get("title"), publisher=record.get("publisher"),
        source_url=record.get("url"),
        structure=structure, **meta)

    for cons in files.get("consolidation", []):
        if cons.get("name"):
            cstruct, tom = parse_consolidation(
                Path(root) / fs / cons["name"], record["identifier"],
                fs, arsutgava, lopnummer, _fresh_parser())
            reg.consolidations.append(Consolidation(
                of=reg.uri, konsolideradTom=tom, structure=cstruct))
    for am in files.get("amendment", []):
        # the harvest record always carries both keys (harvest.py normalizes);
        # identifier may be None (unreadable link text) -- the url still pins it
        reg.amendments.append(Amendment(
            identifier=am["identifier"], uri=amendment_uri(am["identifier"]),
            url=am["url"], beslutsdatum=None))
    return reg
