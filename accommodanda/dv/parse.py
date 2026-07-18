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
from datetime import date

from bs4 import BeautifulSoup

from ..lib import patch
from ..lib.casenaming import case_uri
from ..lib.datasets import NAMEDACTS
from ..lib.datasets import NAMEDLAWS as SFS_NAMEDLAWS
from ..lib.lagrum import (
    ALL_PARSE_TYPES,
    LagrumParser,
    Ref,
    interleave,
    lagrum_uri,
    load_abbreviations,
    load_namedacts,
    load_namedlaws,
)
from .model import Avgorande, Fotnot, Hanvisning, Lagrum, Rubrik, Stycke
from .structure import nest

# Court decisions cite across the whole spectrum of legal sources, so the
# DV citation scanner enables every ported grammar.
DV_PARSE_TYPES = ALL_PARSE_TYPES

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

MONTHS = {
    "januari": 1, "februari": 2, "mars": 3, "april": 4,
    "maj": 5, "juni": 6, "juli": 7, "augusti": 8,
    "september": 9, "oktober": 10, "november": 11, "december": 12,
}
COURT_DATE_ALIASES = {
    "HDO": ("HD", "Högsta domstolen"),
    "HFD": ("HFD", "Högsta förvaltningsdomstolen", "Regeringsrätten"),
    "ADO": ("Arbetsdomstolen",),
    "MIOD": ("Migrationsöverdomstolen",),
    "MOD": ("Mark- och miljööverdomstolen", "Miljööverdomstolen"),
    "PMOD": ("Patent- och marknadsöverdomstolen",),
}


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


def decision_dates_from_text(body, court, court_namn, referat, metadata_date=None,
                             today=None):
    """Return sane dates explicitly stated for the publishing court.

    Publisher metadata occasionally contains a typo. Text wins only when the
    publishing court itself is named in the formal final-ruling formula
    ``meddelade den [date] följande [dom/beslut]``; lower-instance, cited-case
    and earlier procedural dates are therefore not candidates. A malformed or
    future date, one incompatible with the referat year, or multiple distinct
    candidates are all retained: one referat can publish several decisions.
    """
    aliases = set(COURT_DATE_ALIASES.get(court, ()))
    aliases.add(court_namn)
    if "hovrätt" in court_namn.lower():
        aliases.add("Hovrätten")
    court_pattern = "|".join(re.escape(alias) for alias in
                             sorted(aliases, key=len, reverse=True) if alias)
    assert court_pattern, "publishing court name required for textual date"
    prefix = (r"(?:^|[.!?]\s)(?:%s)(?:\s*\([^)]{0,500}\))?\s+"
              % court_pattern)
    date_pattern = r"(\d{1,2})\s+(%s)\s+((?:19|20)\d{2})" % "|".join(MONTHS)
    patterns = [
        re.compile(prefix + r"meddelade\s+den\s+" + date_pattern
                   + r"\s+följande\s+(?:slutliga\s+)?(?:dom|beslut)\b",
                   re.I),
        re.compile(prefix + r"anförde\s+i\s+(?:slutligt\s+)?(?:dom|beslut)\s+"
                   r"den\s+" + date_pattern
                   + r"(?:\s+i\s+huvudsak)?\s+följande\b", re.I),
    ]
    iso_pattern = re.compile(
        r"(?:^|[.!?]\s)(?:%s)\s*\(((?:19|20)\d{2})-(\d{2})-(\d{2})"
        r"(?:,|\))" % court_pattern, re.I)
    iso_group_pattern = re.compile(
        r"(?:^|[.!?]\s)(?:%s)\s*\(([^)]{0,500})\)" % court_pattern,
        re.I)
    candidates = set()
    limit = today or date.today()
    referat_years = {int(year) for value in referat
                     for year in re.findall(r"\b((?:19|20)\d{2})\b", value)}
    try:
        metadata_year = date.fromisoformat(metadata_date).year if metadata_date else None
    except ValueError:
        metadata_year = None

    def accept(candidate):
        if candidate > limit:
            return
        # A referat may be published the calendar year after the decision.
        if referat_years and not any(candidate.year in (refyear - 1, refyear)
                                     for refyear in referat_years):
            return
        if not referat_years and (metadata_year is None
                                  or abs(candidate.year - metadata_year) > 2):
            return
        candidates.add(candidate.isoformat())

    for block in body:
        found = [match for pattern in patterns for match in pattern.findall(block.text)]
        if re.match(r"^HD:s\s+(?:dom|domar|beslut)\s+meddelades\b",
                    block.text, re.I):
            found += re.findall(r"(?:den|d\.?)\s*" + date_pattern,
                                block.text, re.I)
        if ("hovrätt" in court_namn.lower()
                and re.match(r"^Hovrättens\s+(?:domar|beslut)\s+meddelade?:",
                             block.text, re.I)):
            found += re.findall(r"(?:den|d\.?)\s*" + date_pattern,
                                block.text, re.I)
        for day, month, year in found:
            try:
                candidate = date(int(year), MONTHS[month.lower()], int(day))
            except ValueError:
                continue
            accept(candidate)
        for year, month, day in iso_pattern.findall(block.text):
            try:
                candidate = date(int(year), int(month), int(day))
            except ValueError:
                continue
            accept(candidate)
        for group in iso_group_pattern.findall(block.text):
            for year, month, day in re.findall(
                    r"((?:19|20)\d{2})-(\d{2})-(\d{2})", group):
                try:
                    candidate = date(int(year), int(month), int(day))
                except ValueError:
                    continue
                accept(candidate)
        if court == "PMOD":
            for year, month, day in re.findall(
                    r"^(?:DOM|BESLUT)\s*\(att meddelas\s+"
                    r"((?:19|20)\d{2})-(\d{2})-(\d{2})\)", block.text, re.I):
                try:
                    candidate = date(int(year), int(month), int(day))
                except ValueError:
                    continue
                accept(candidate)
    return sorted(candidates)


def decision_date_from_text(body, court, court_namn, referat, metadata_date=None,
                            today=None):
    """A single text-confirmed date, or None for zero/multiple decisions."""
    candidates = decision_dates_from_text(
        body, court, court_namn, referat, metadata_date, today)
    return candidates[0] if len(candidates) == 1 else None


def parse_api_record(d, basefile=None):
    """API record dict -> Avgorande. The innehåll HTML is DV's intermediate
    format: when `basefile` is given, apply any curated patch to it (a
    correction, or a rot13 redaction anonymising a party) before it is parsed."""
    innehall = d.get("innehall")
    if basefile is not None and innehall is not None:
        innehall = patch.apply("dv", basefile, innehall)
    body, footnotes = parse_body(innehall)
    text_dates = decision_dates_from_text(
        body, d["domstol"]["domstolKod"], d["domstol"]["domstolNamn"],
        d.get("referatNummerLista", []), d.get("avgorandedatum"))
    return Avgorande(
        court=d["domstol"]["domstolKod"],
        court_namn=d["domstol"]["domstolNamn"],
        malnummer=[m.strip() for m in d.get("malNummerLista", [])],
        referat=[r.strip() for r in d.get("referatNummerLista", [])],
        avgorandedatum=max(text_dates) if text_dates else d.get("avgorandedatum"),
        avgorandedatum_lista=text_dates if len(text_dates) > 1 else [],
        publiceringsform=d.get("publiceringsform"),
        typ=d.get("typ"),
        rattsomrade=[r.strip() for r in d.get("rattsomradeLista", [])],
        nyckelord=[n.strip() for n in d.get("nyckelordLista", []) if n.strip()],
        lagrum=[Lagrum(referens=l.get("referens", "").strip(),
                       sfsnummer=l.get("sfsNummer"))
                for l in d.get("lagrumLista", [])],
        forarbeten=[f.strip() for f in d.get("forarbeteLista", [])
                    if f.strip() and not RE_SEPARATOR.match(f.strip())],
        sammanfattning=(d.get("sammanfattning") or "").strip() or None,
        # europarattsligaAvgorandenLista never holds citations, only coarse
        # topic labels (3 distinct values corpus-wide, 2026-07-18) -- kept as
        # labels beside rattsomrade, never projected as relation edges
        europarattslig=[e.strip() for e in
                        d.get("europarattsligaAvgorandenLista", [])
                        if e.strip()],
        related=[Hanvisning(fritext=p["fritext"].strip(),
                            grupp=p.get("gruppKorrelationsnummer"))
                 for p in d.get("hanvisadePubliceringarLista", [])],
        litteratur=[", ".join(part for part in
                              (l.get("forfattare", "").strip(),
                               l.get("titel", "").strip()) if part)
                    for l in d.get("litteraturLista", [])],
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
    parser.reset()                        # fresh per-document state
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
    parser.reset()
    return [interleave(fn.text, parser.parse_text(fn.text, context={}))
            for fn in footnotes]


@functools.cache
def _scanner():
    """The body citation scanner, built once (grammar compilation is the
    expensive part); scan_body resets its per-document state each call."""
    namedlaws, abbreviations, named_acts = legal_vocab()
    return LagrumParser(namedlaws, basefile="dom", abbreviations=abbreviations,
                        parse_types=DV_PARSE_TYPES, named_acts=named_acts)


def curated_runs(text, predicate, fallback_uri=None):
    """One curated metadata string normalized to an inline-run list through the
    same citation grammar the body uses, every resolved reference carrying the
    field's typed `predicate`. Unresolved text survives as plain runs -- a
    failed normalization retains the editor's string, it never erases it. When
    the grammar finds nothing and the source supplies an authoritative identity
    beside the string (lagrumLista's sfsNummer, a hanvisning's grupp join), the
    whole string links to that `fallback_uri` instead."""
    parser = _scanner()
    parser.reset()
    refs = parser.parse_text(text, context={}, predicate=predicate)
    if not refs and fallback_uri:
        return [{"predicate": predicate, "uri": fallback_uri, "text": text}]
    return interleave(text, refs)


def _related_entry(h, grupp_uris):
    """A hanvisad publicering as a curated artifact entry. The fritext grammar
    resolves the published citation form; a fritext the grammar cannot read
    falls back to the grupp join (the cited case's publication group), which is
    authoritative but only present on newer records. When both resolve and
    *disagree* -- a grammar mis-read, or an editor's string citing a different
    case than the group names -- the grammar's link stands but the conflict is
    recorded as `grupp_konflikt`, so the acceptance pass can list exactly the
    edges that may be wrong instead of the disagreement being undetectable."""
    grupp_id = grupp_uris.get(h.grupp) if h.grupp else None
    grupp_uri = case_uri(grupp_id) if grupp_id else None
    runs = curated_runs(h.fritext, "rpubl:rattsfallshanvisning", grupp_uri)
    entry = {"text": h.fritext, "runs": runs}
    if h.grupp:
        entry["grupp"] = h.grupp
    if grupp_uri and grupp_uri not in [r["uri"] for r in runs
                                       if isinstance(r, dict)]:
        entry["grupp_konflikt"] = grupp_uri
    return entry


def to_artifact(av, canonical_id=None, grupp_uris=None):
    grupp_uris = grupp_uris or {}
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
        "avgorandedatum_lista": av.avgorandedatum_lista,
        "metadata": {
            "publiceringsform": av.publiceringsform,
            "typ": av.typ,
            "rattsomrade": av.rattsomrade,
            "europarattslig": av.europarattslig,
            "nyckelord": av.nyckelord,
            # the curated fields, normalized through the citation grammar into
            # the same inline-run shape body text uses ({"text": raw string,
            # "runs": [...]}) -- the typed relation edges the catalog projects
            # (rpubl:lagrum / rpubl:forarbete / rpubl:rattsfallshanvisning /
            # dcterms:relation), with unresolved strings retained as plain runs
            "lagrum": [{"text": l.referens, "sfsnummer": l.sfsnummer,
                        "runs": curated_runs(
                            l.referens, "rpubl:lagrum",
                            lagrum_uri({"law": l.sfsnummer})
                            if l.sfsnummer else None)}
                       for l in av.lagrum if l.referens],
            "forarbeten": [{"text": f,
                            "runs": curated_runs(f, "rpubl:forarbete")}
                           for f in av.forarbeten],
            "sammanfattning": av.sammanfattning,
            "related": [_related_entry(h, grupp_uris) for h in av.related],
            "litteratur": [{"text": t,
                            "runs": curated_runs(t, "dcterms:relation")}
                           for t in av.litteratur if t],
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
