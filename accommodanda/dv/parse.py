"""Parse a court decision (DV) into the Avgorande model and project it to
a JSON artifact.

Driven by the identity index (accommodanda.dv_identity): each canonical
case may have several source records, and metadata is merged field by
field rather than picking one source whole. The API path is implemented
(body from the record's `innehall` HTML, metadata from its curated
fields), and the body is scanned for citations across every ported
grammar (DV_PARSE_TYPES) to populate `references`. The legacy Word/OOXML
path (for the ~1,600 legacy-only cases) remains the next increment; its
seam is marked below.

The body HTML is flat: each <p> is either a section heading (a short,
all-caps or known-label paragraph) or a body paragraph (optionally
numbered, as in HFD/HD prejudikat). We classify generously -- a
misclassified heading still keeps its text, so nothing is lost.
"""

import functools
import html as htmllib
import re

from bs4 import BeautifulSoup

from ..lib import patch
from ..lib.casenaming import case_uri
from ..lib.datasets import NAMEDACTS
from ..lib.datasets import NAMEDLAWS as SFS_NAMEDLAWS
from ..lib.lagrum import (
    EULAGSTIFTNING,
    EURATTSFALL,
    FORARBETEN,
    KORTLAGRUM,
    LAGRUM,
    MYNDIGHETSBESLUT,
    RATTSFALL,
    LagrumParser,
    Ref,
    interleave,
    load_abbreviations,
    load_namedacts,
    load_namedlaws,
)
from .model import Avgorande, Fotnot, Lagrum, Rubrik, Stycke
from .structure import nest

# Court decisions cite across the whole spectrum of legal sources, so the
# DV citation scanner enables every ported grammar.
DV_PARSE_TYPES = [LAGRUM, KORTLAGRUM, EULAGSTIFTNING, RATTSFALL, FORARBETEN,
                  EURATTSFALL, MYNDIGHETSBESLUT]

# section labels that are headings even when not all-caps
KNOWN_HEADINGS = {
    "bakgrund", "yrkanden", "yrkanden m.m.", "skälen för avgörandet",
    "domskäl", "domslut", "slut", "avgörande", "saken", "klagande",
    "motpart", "motparter", "sökande", "parter", "rättslig reglering",
    "rättslig reglering m.m.", "frågan i målet", "bakgrund och frågor",
    "överklagat avgörande", "förhandsbesked", "beslut", "dom",
}

# leading numbered-paragraph marker, e.g. "1.    text" (HD/HFD prejudikat)
RE_NUMPARA = re.compile(r"^(\d+)\.\s+(.*)", re.S)
RE_SEPARATOR = re.compile(r"^[\W_]+$")

# an end-of-document footnote definition (HD's 2023+ format): "[N] text", the
# marker often a stray <sup>[N]</sup>N pair so the digit leaks in doubled
RE_FOOTDEF = re.compile(r"^\[(\d{1,2})\]\s*(.*)", re.S)

# an inline footnote reference in body text: "[N]", optionally preceded by the
# same OOXML artifact -- a duplicated single digit glued onto the word before it
# (with at most one separating punctuation): "C-268/213,[3]" is "C-268/21" + ref
# 3, "C-520/184[4]" is "C-520/18" + ref 4. The duplicate must equal N to count
# as the artifact; an unrelated trailing digit is kept as real text.
RE_FOOTREF = re.compile(r"(\d)?([.,]?)\[(\d{1,2})\]")


def collapse(text):
    text = text.replace("\xa0", " ")
    # collapse runs of spaces/tabs but keep explicit newlines (from <br>)
    text = re.sub(r"[ \t]+", " ", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def is_heading(text):
    # a trailing footnote marker must not make a short sentence look like a
    # heading ("Brödtext.[1]" is body, not a rubrik)
    text = re.sub(r"\[\d+\]", "", text).strip()
    if "\n" in text or len(text) > 80:
        return False
    if text.lower().rstrip(".") in KNOWN_HEADINGS:
        return True
    letters = [c for c in text if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.8:
        return True
    # short, capitalized, no terminal sentence punctuation -> heading
    return (text[:1].isupper() and text[-1:] not in ".!?:,"
            and len(text.split()) <= 7)


def _footnote(m):
    """A Fotnot from an `RE_FOOTDEF` match, stripping the leading digit the
    OOXML `<sup>[N]</sup>N` artifact duplicates into the footnote body."""
    num, text = m.group(1), m.group(2)
    text = re.sub(r"^%s\b[\s.,]*" % re.escape(num), "", text)
    return Fotnot(num=num, text=collapse(text))


def parse_body(html):
    """`(blocks, footnotes)`: the Rubrik/Stycke body in document order plus the
    end-of-document footnote definitions lifted out of the block stream.

    Headings come from the source's own `<h1>`–`<h4>` tags as well as the `<p>`
    heuristic (legacy records carry no heading tags); an `<h1>` is an instance
    name ("Svea hovrätt"), `<h2>/<h3>` a section, and a `<p>` heading gets level
    0. `<li>` content is still skipped, as it always was."""
    soup = BeautifulSoup(html or "", "html.parser")
    blocks, footnotes = [], []
    for el in soup.find_all(["h1", "h2", "h3", "h4", "p"]) or [soup]:
        for br in el.find_all("br"):
            br.replace_with("\n")
        text = collapse(htmllib.unescape(el.get_text()))
        if not text or RE_SEPARATOR.match(text):
            continue
        if el.name in ("h1", "h2", "h3", "h4"):
            blocks.append(Rubrik(text=text, level=int(el.name[1])))
            continue
        fd = RE_FOOTDEF.match(text)
        if fd:
            footnotes.append(_footnote(fd))
            continue
        m = RE_NUMPARA.match(text)
        if m:
            blocks.append(Stycke(text=collapse(m.group(2)), ordinal=m.group(1)))
        elif is_heading(text):
            blocks.append(Rubrik(text=text))
        else:
            blocks.append(Stycke(text=text))
    return blocks, footnotes


def parse_innehall(html):
    """The body blocks alone (footnotes dropped) -- the contract the structural
    tests and any prose-only caller rely on."""
    return parse_body(html)[0]


def parse_api_record(d, basefile=None):
    """API record dict -> Avgorande. The innehåll HTML is DV's intermediate
    format: when `basefile` is given, apply any curated patch to it (a
    correction, or a rot13 redaction anonymising a party) before it is parsed."""
    innehall = d.get("innehall")
    if basefile is not None and innehall is not None:
        innehall = patch.apply("dv", basefile, innehall)
    body, footnotes = parse_body(innehall)
    return Avgorande(
        court=d["domstol"]["domstolKod"],
        court_namn=d["domstol"]["domstolNamn"],
        malnummer=[m.strip() for m in d.get("malNummerLista", [])],
        referat=[r.strip() for r in d.get("referatNummerLista", [])],
        avgorandedatum=d.get("avgorandedatum"),
        publiceringsform=d.get("publiceringsform"),
        typ=d.get("typ"),
        rattsomrade=[r.strip() for r in d.get("rattsomradeLista", [])],
        nyckelord=[n.strip() for n in d.get("nyckelordLista", []) if n.strip()],
        lagrum=[Lagrum(referens=l.get("referens", "").strip(),
                       sfsnummer=l.get("sfsNummer"))
                for l in d.get("lagrumLista", [])],
        forarbeten=[f.strip() for f in d.get("forarbeteLista", [])],
        sammanfattning=(d.get("sammanfattning") or "").strip() or None,
        related=[p for p in d.get("hanvisadePubliceringarLista", [])]
                + [e for e in d.get("europarattsligaAvgorandenLista", [])],
        body=body,
        footnotes=footnotes,
        sources=["domstol"],
    )


@functools.cache
def legal_vocab():
    """Named-law, abbreviation and EU-act tables for the citation scanner, loaded
    once. KORTLAGRUM is enabled (court decisions cite both full law names and
    abbreviations -- "12 kap. 57 § JB", "10 kap. 10 § RB"); the EU-act table lets
    "artikel 6 i dataskyddsförordningen" pinpoint the regulation's article."""
    return (load_namedlaws(SFS_NAMEDLAWS), load_abbreviations(SFS_NAMEDLAWS),
            load_namedacts(NAMEDACTS))


def extract_footrefs(text):
    """`(clean, marks)`: `text` with its inline footnote markers removed (and
    the OOXML duplicated-digit artifact undone, so the citation scanner sees the
    real case number), plus `[(position, num), …]` for each marker -- the
    position being where the superscript sits in `clean`."""
    parts, marks, last, clean_len = [], [], 0, 0
    for m in RE_FOOTREF.finditer(text):
        gap = text[last:m.start()]
        parts.append(gap)
        clean_len += len(gap)
        stray, punct, num = m.group(1), m.group(2), m.group(3)
        if not (stray is not None and stray == num):  # drop only the doubled digit
            kept = (stray or "") + punct              # stray/punct here is real text
            parts.append(kept)
            clean_len += len(kept)
        marks.append((clean_len, num))
        last = m.end()
    parts.append(text[last:])
    return "".join(parts), marks


def scan_body(body):
    """Each body block's text as an inline-run list (plain `str` runs
    interleaved with `{"predicate", "uri", "text"}` link dicts at their
    exact positions) -- the same shape SFS emits for its text nodes, so the
    discovered citations live inline rather than in a flat list. A court
    decision has no base law, so the scanner runs with an empty context
    (relative refs without a named law stay unlinked); one parser threads
    the whole body in document order so "samma lag" and in-document
    law-name learning carry across blocks. Inline footnote markers are lifted
    out as zero-width `kind="footnote"` runs."""
    parser = _scanner()
    parser.state = type(parser.state)()   # fresh per-document state
    runs = []
    for b in body:
        clean, marks = extract_footrefs(b.text)
        refs = parser.parse_text(clean, context={})
        refs += [Ref(p, p, num, "dcterms:references", "#fn-%s" % num,
                     kind="footnote") for p, num in marks]
        runs.append(interleave(clean, refs))
    return runs


def scan_footnotes(footnotes):
    """Footnote-body texts as inline-run lists, citation-scanned like the body
    (HD's footnotes cite CJEU case law and EU regulations)."""
    parser = _scanner()
    parser.state = type(parser.state)()
    return [interleave(fn.text, parser.parse_text(fn.text, context={}))
            for fn in footnotes]


@functools.cache
def _scanner():
    """The body citation scanner, built once (grammar compilation is the
    expensive part); scan_body resets its per-document state each call."""
    namedlaws, abbreviations, named_acts = legal_vocab()
    return LagrumParser(namedlaws, basefile="dom", abbreviations=abbreviations,
                        parse_types=DV_PARSE_TYPES, named_acts=named_acts)


def to_artifact(av, canonical_id=None):
    runs = scan_body(av.body)
    def block(b, text):
        if isinstance(b, Rubrik):
            return {"type": "rubrik", "level": b.level, "text": text}
        return {"type": "stycke", "ordinal": b.ordinal, "text": text}
    cid = canonical_id or (av.referat[0] if av.referat
                           else "%s %s" % (av.court, av.malnummer[0])
                           if av.malnummer else av.court)
    return {
        "uri": case_uri(cid),
        "court": av.court,
        "court_namn": av.court_namn,
        "malnummer": av.malnummer,
        "referat": av.referat,
        "avgorandedatum": av.avgorandedatum,
        "metadata": {
            "publiceringsform": av.publiceringsform,
            "typ": av.typ,
            "rattsomrade": av.rattsomrade,
            "nyckelord": av.nyckelord,
            "lagrum": [{"referens": l.referens, "sfsnummer": l.sfsnummer}
                       for l in av.lagrum],
            "forarbeten": av.forarbeten,
            "sammanfattning": av.sammanfattning,
            "related": av.related,
        },
        # the content-bearing instance/ruling tree (delmål → instans →
        # betänkande/dom → domskäl/domslut → …) with the prose attached as leaves
        # (the DV structural golden's reducer drops the prose, comparing only the
        # skeleton); the renderer walks it to show the instance structure
        "structure": nest([block(b, text)
                           for b, text in zip(av.body, runs, strict=True)]),
        "footnotes": [{"num": fn.num, "text": runs}
                      for fn, runs in zip(av.footnotes,
                                          scan_footnotes(av.footnotes),
                                          strict=True)],
        "sources": av.sources,
    }


def api_member(case):
    for member in case["members"]:
        if member["store"] == "domstol":
            return member
    return None
