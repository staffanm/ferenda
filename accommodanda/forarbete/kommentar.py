"""Extract EU-implementation relations from a proposition's författningskommentar.

The författningskommentar (the section-by-section commentary at the end of a
proposition) routinely states, for a paragraf, which EU directive article it
implements -- "Paragrafen genomför artikel 21.1-21.3 i NIS 2-direktivet". That
is a *genomför* (implements) relation, stronger than a bare reference: it ties a
Swedish provision to the exact EU-law article it transposes, which is the
machine-readable mapping that otherwise only exists as ad-hoc tables.

Implementation relations are authoritative when the commentary accompanies the
*final* text of the enactment. That holds for a *proposition* (the bill text is
the version closest to the enacted law) and for a *förordningsmotiv* (`fm`) --
the regeringen publishes förordningsmotiv alongside the förordning it has just
enacted, so an fm's "Förordningen genomför … direktivet" statement is as
authoritative as a prop's. It does NOT hold for a lagrådsremiss/SOU/Ds, whose
proposed structure is still renumbered and revised (on Lagrådet's and the remiss
bodies' feedback) before enactment. So `extract` extracts from props and
förordningsmotiv (`{"prop", "fm"}`); the commentary of other förarbete types is
ignored. A förordningsmotiv writes its författningskommentar at heading level 3
(prop props it at level 1) and names its förordning in the leading title rubriks
rather than a "Förslaget till lag om ändring i…" level-2 heading, so the fm
branch locates the section at any level and reads the law context from the
title.

This module extracts those statements:

  - the directive is resolved to a CELEX uri -- and only ever to a *directive*
    (a sector-3 act whose CELEX type letter is 'L'). "Paragrafen genomför …
    artikel … i direktivet" transposes a directive into national law; a
    regulation ('R') applies directly and is never "genomförd", so a regulation
    result is necessarily a misparse and is discarded. A named directive ("NIS
    2-direktivet") is resolved from its defining sentence -- the alias in
    parentheses binds to the *subject* directive (the first directive citation),
    not the trailing acts the title mentions amending/repealing. A bare
    "direktivet" falls back to the proposition's dominant directive.
  - the article reference is parsed into pinpoints (dotted, ranged, listed and
    sub-lettered: "21.1-21.3", "34.4, 34.5 och 34.7", "23.4 a") plus the bare
    article numbers, which are the citation-target fragments on the EU act.

What this module does NOT yet do: pin each statement to the exact paragraf (the
flattened PDF text doesn't carry reliable paragraf boundaries) or to the eventual
SFS (a new law has no SFS number in its proposition -- the link comes backwards
from the SFSR register). It records the best-effort paragraf context it can see
and leaves those joins to the caller. See REWRITE.md.
"""

import argparse
import json
import re
from pathlib import Path

from ..lib.catalog import norm_title
from ..lib.lagrum import EULAGSTIFTNING, LagrumParser
from ..lib.util import normalize_fold
from .parse import parse_record, to_artifact
from .structure import flatten

# "Paragrafen genomför [delvis] artikel(n/na) <refs> i <directive>"; the article
# block is captured loosely (digits, dots, letters, ranges, "och"/"samt"/commas)
# up to " i <name>direktivet" -- note article numbers contain dots, so we do not
# split on '.'. The subject vocabulary is *per document type*, like SUBJECT_RES:
# "Förordningen" only in the fm pattern. A prop commentary sentence
# "Förordningen genomför artikel …" is about some *other* instrument, but the
# emitted row would still carry the law/chapter/paragraf context of the lag
# section under comment -- a false implements edge pinned to the wrong
# instrument's paragraf, later resolved to an SFS anchor by genomforande.py.
_IMPLEMENTS = (
    r"(?P<subject>%s)\w*\s+(?:[a-zåäö ]*?\s)?"
    r"genomför(?:s|t)?\s+(?P<partial>delvis\s+)?"
    r"(?:artikel|artiklarna)\s+(?P<refs>[0-9][0-9.,‐-―\- a-zåäö]*?)"
    r"\s+i\s+(?P<dir>(?:[A-Za-zÅÄÖåäö0-9./()‐-―\- ]*?)?direktivet)")
_SUBJECTS = "Paragrafen|Paragraferna|Bestämmelsen|Lagförslaget|Ändringen|Punkten"
IMPLEMENTS_RES = {
    "prop": re.compile(_IMPLEMENTS % _SUBJECTS, re.IGNORECASE),
    "fm": re.compile(_IMPLEMENTS % (_SUBJECTS + "|Förordningen"),
                     re.IGNORECASE),
}

# one article pinpoint inside the refs span: article number, optional dotted
# subsection(s), optional trailing letter ("23.4 a", "21.1", "28")
ARTICLE_RE = re.compile(r"(\d+(?:\.\d+)*)\s*([a-z])?", re.IGNORECASE)

# a directive defined with an alias: "... direktiv (EU) 2022/2555 ... (NIS 2-
# direktivet)". The alias binds to the subject (first) directive of the sentence.
ALIAS_RE = re.compile(r"\(([^()]*?direktivet)\)")

# a document-level statement of what the enactment transposes: "(Genom) lag(en)/
# lagförslaget genomför(s) [delvis] … direktiv (EU) 2015/2302 …" in a prop,
# "(Genom) förordningen genomförs …" in a förordningsmotiv. The first directive
# cited after it is the document's subject directive -- the authoritative
# fallback for a bare "direktivet" when (as is normal for a single-directive
# document) the directive is never given a parenthetical alias. Beats a raw
# citation count, which a repealed predecessor directive can dominate. The
# subject vocabulary is *per document type*: a prop speaks of its lag(förslag),
# an fm of its förordning -- "förordningen" stays out of the prop pattern
# because a prop sentence about some other förordning ("… förordningen …
# genomförs …") would otherwise cast a vote on the prop's document-wide default.
_GENOMFOR = r"\b[^.]*?genomför(?:s|t)?\b"
SUBJECT_RES = {
    "prop": re.compile(r"\b(?:lag|lagen|lagförslaget)" + _GENOMFOR,
                       re.IGNORECASE),
    "fm": re.compile(r"\b(?:förordning|förordningen)" + _GENOMFOR,
                     re.IGNORECASE),
}


def plain(runs):
    return "".join(r if isinstance(r, str) else r.get("text", "") for r in runs)


def is_directive(uri):
    """True when `uri` is a CELEX *directive* -- a sector-3 act whose type letter
    is 'L' (as opposed to a regulation 'R' or decision 'D'). Only a directive can
    be the target of a "genomför … i direktivet" statement, so a non-directive
    resolution is rejected rather than recorded."""
    celex = uri.rsplit("/", 1)[-1].split("#")[0]
    return len(celex) > 5 and celex[5] == "L"


def _refparser():
    return LagrumParser({}, basefile="prop", parse_types=[EULAGSTIFTNING])


def _first_directive(parser, text):
    """The first CELEX *directive* the parser finds in `text` (regulations and
    other acts skipped), or None."""
    return next((r.uri for r in parser.parse_text(text, context={})
                 if is_directive(r.uri)), None)


# a sentence boundary: '.'/'!'/'?' followed by whitespace and an uppercase
# sentence start. Legal prose is dense with dotted abbreviations and neither
# signal suffices alone: the uppercase requirement filters the open-ended
# citation abbreviations ("jfr prop. 2014/15:128 s. 12", "art. 5"), which are
# followed by digits -- no lookbehind set could enumerate them all -- while the
# closed set of prose abbreviations ("bl.a.", "t.ex.", "s.k.", "dvs.", "m.m.",
# "m.fl.", "resp.", "o.s.v.") routinely precedes proper nouns ("bl.a.
# Europaparlamentets …") and so needs the explicit negative lookbehinds.
# Directive numbers ("2009/147/EG") and CELEX cites ("C:2018:255") carry no
# terminator-space-uppercase run, so they never split.
SENTENCE_SEP_RE = re.compile(
    r"(?<!\bbl\.a)(?<!\bt\.ex)(?<!\bs\.k)(?<!\bdvs)(?<!\bm\.m)(?<!\bm\.fl)"
    r"(?<!\bresp)(?<!o\.s\.v)"
    r"[.!?]\s+(?=[A-ZÅÄÖ])")


def _sentence_start(text, pos):
    """Index of the start of the sentence that contains `pos` -- the char after
    the last sentence terminator before `pos`, or 0. Scoping the alias lookback
    to its defining sentence (rather than a fixed char window) keeps a long
    "senast ändrat genom <amendment list>" clause from pushing the window past
    the subject directive it should bind to."""
    ends = [m.end() for m in SENTENCE_SEP_RE.finditer(text, 0, pos)]
    return ends[-1] if ends else 0


def resolve_directives(blocks, parser, typ):
    """Map each directive alias used in the document (a proposition or
    förordningsmotiv) to a CELEX uri, plus a 'default' for a bare "direktivet".
    A defining sentence is one ending in
    "(<alias>direktivet)"; the alias binds to its first *directive* citation (the
    subject), not the acts it amends/repeals and not a co-cited regulation
    (which can never be the subject of a "genomför" statement).

    The 'default' is the directive the enactment transposes, named in a
    document-level "lag(en)/förordningen genomför(s) … direktiv X" statement
    (SUBJECT_RES[typ], the subject vocabulary of the document type) -- the
    reliable subject signal when, as for a single-directive document, the
    directive is never aliased. Failing that it falls back to the dominant
    aliased directive."""
    subject_re = SUBJECT_RES[typ]
    aliases = {}
    subjects = []
    for b in blocks:
        text = plain(b["text"])
        for m in ALIAS_RE.finditer(text):
            uri = _first_directive(parser,
                                   text[_sentence_start(text, m.start()):m.end()])
            if uri:
                aliases[m.group(1).lower()] = uri           # subject directive
        for m in subject_re.finditer(text):
            uri = _first_directive(parser, text[m.start():m.end() + 200])
            if uri:
                subjects.append(uri)
    default = (max(set(subjects), key=subjects.count) if subjects else
               max(set(aliases.values()), key=list(aliases.values()).count)
               if aliases else None)
    if default:
        aliases["default"] = default
    return aliases


def parse_articles(refs):
    """The refs span -> (pinpoints, articles): pinpoints keep the dotted form
    and expand ranges ("21.1-21.3" -> 21.1, 21.2, 21.3); articles are the bare
    numbers (the EU-act fragment ids), de-duplicated in order."""
    pinpoints, articles = [], []
    for part in re.split(r"\s*(?:,|\boch\b|\bsamt\b)\s*", refs):
        part = part.strip()
        ends = ARTICLE_RE.findall(part)
        if re.search(r"[‐-―-]", part) and len(ends) == 2:
            (lo, _), (hi, _) = ends      # a range "21.1-21.3" over the subsection
            base, a, b = lo.rsplit(".", 1)[0], lo.rsplit(".", 1)[-1], hi.rsplit(".", 1)[-1]
            if a.isdigit() and b.isdigit():
                pinpoints += ["%s.%d" % (base, n) for n in range(int(a), int(b) + 1)]
                articles.append(base.split(".")[0])
                continue
        for num, letter in ends:
            pinpoints.append(num + (" " + letter if letter else ""))
            articles.append(num.split(".")[0])
    seen = set()
    return pinpoints, [a for a in articles if not (a in seen or seen.add(a))]


def article_of(pinpoint):
    """The bare article number a pinpoint belongs to ('23.4 a' -> '23',
    '28' -> '28') -- the key tying a pinpoint to its directive-article fragment."""
    return pinpoint.split(".")[0].split()[0]


def pinpoints_by_article(pinpoints):
    """Group pinpoints under their article number, order preserved -- so a
    statement spanning several articles shows each article only its own
    pinpoints ('2.1, 2.2 f' under article 2, '26.1 c' under article 26)."""
    out = {}
    for pp in pinpoints:
        out.setdefault(article_of(pp), []).append(pp)
    return out


def directive_uri(name, aliases):
    """CELEX uri for a directive named in an implements statement, or None."""
    key = name.strip().lower()
    if key in aliases:
        return aliases[key]
    return aliases.get("default")    # bare "direktivet" / unrecognised alias


def find_kommentar(blocks, any_level=False):
    """The block index range [start, end) of the författningskommentar section,
    or None. In a proposition the section is a level-1 chapter, bounded by the
    next level-1 heading. A förordningsmotiv (`any_level`) writes it at heading
    level 3 (unnumbered), and its own kapitel/paragraf subheadings share that
    level, so the section runs from the heading to the next heading of a
    *shallower* level -- in practice to the end of the document, förordningsmotiv
    put the commentary last."""
    if any_level:
        start = next((i for i, b in enumerate(blocks)
                      if b["type"] == "rubrik"
                      and "författningskommentar" in plain(b["text"]).lower()),
                     None)
        if start is None:
            return None
        level = blocks[start].get("level") or 1
        end = next((i for i in range(start + 1, len(blocks))
                    if blocks[i]["type"] == "rubrik"
                    and (blocks[i].get("level") or 1) < level), len(blocks))
        return start, end
    start = next((i for i, b in enumerate(blocks)
                  if b["type"] == "rubrik" and (b.get("level") or 1) == 1
                  and "författningskommentar" in plain(b["text"]).lower()), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(blocks))
                if blocks[i]["type"] == "rubrik"
                and (blocks[i].get("level") or 1) == 1), len(blocks))
    return start, end


# the förordning a förordningsmotiv concerns, named in its leading title rubriks
# (there is no prop-style "Förslaget till lag om ändring i…" level-2 heading):
# the title wraps over as many rubriks as the layout needs -- "Förordning" +
# "om ändring i X (NNNN:NN)" (Fm 2022:5), a single "X-förordning" (Fm 2025:1),
# or a long name split at the line break (Fm 2026:1 "Förordning om omfattning av
# tiden för lärares och" + "förskollärares undervisningsuppdrag").
FM_TITLE_RE = re.compile(r"förordning", re.IGNORECASE)

# the document-type label heading the title rubriks: a bare "Förordningsmotiv",
# or the label fused into the title itself ("Förordningsmotiv om förordning om
# ändring i X (NNNN:NN)"). Stripped as a *prefix*, not skipped as a whole rubrik:
# in the fused form the post-label remainder is exactly the förordning name, and
# dropping the whole rubrik would leave no law at all.
FM_LABEL_RE = re.compile(r"^förordningsmotiv\b(?:\s+(?:om|för)\b)?\s*",
                         re.IGNORECASE)


def fm_law(blocks):
    """The förordning a förordningsmotiv comments on, assembled from its leading
    title rubriks -- only the rubriks *before the first non-rubrik block*, so an
    in-body heading that mentions "förordning" can never win -- and fed to
    `sfs_number`/`proposed_name` like a prop's level-2 rubrik. Each leading
    rubrik has the doc-type label stripped by prefix (a bare "Förordningsmotiv"
    strips to nothing and drops out); the remainders are joined, rejoining a
    title the layout wrapped over several rubriks. Returns the joined title, or
    None when it does not name a förordning."""
    parts = []
    for b in blocks:
        if b["type"] != "rubrik":
            break
        t = FM_LABEL_RE.sub("", plain(b["text"]).strip())
        if t:
            parts.append(t)
    law = " ".join(parts)
    return law if FM_TITLE_RE.search(law) else None


def extract(art):
    """Implements relations stated in a proposition's or förordningsmotiv's
    författningskommentar, each tied to the paragraf it comments on. The
    paragraf is tracked from the font-derived `kapitel`/`paragraf` structure
    blocks the parser emits. Records: {predicate, directive, articles,
    pinpoints, uris, partial, law, chapter, paragraf, sentence, page}.

    Only the commentary that accompanies the final enactment is authoritative --
    a proposition (bill text closest to the enacted law) or a förordningsmotiv
    (published alongside the enacted förordning) -- while a lagrådsremiss/SOU/Ds
    still has its provisions renumbered and revised before enactment, so the
    commentary of any other förarbete type yields nothing."""
    typ = art.get("type")
    if typ not in {"prop", "fm"}:
        return []
    blocks = flatten(art["structure"])      # document-order flat view of the tree
    span = find_kommentar(blocks, any_level=typ == "fm")
    if span is None:                      # no författningskommentar (most types)
        return []
    parser = _refparser()
    aliases = resolve_directives(blocks, parser, typ)
    implements_re = IMPLEMENTS_RES[typ]
    out = []
    chapter = paragraf = None
    law = fm_law(blocks) if typ == "fm" else None   # fm names its förordning up top
    for i in range(*span):
        b = blocks[i]
        text = plain(b["text"])
        kind = b["type"]
        if kind == "rubrik" and (b.get("level") or 1) == 2:
            law, chapter, paragraf = text, None, None   # "15.1 Förslaget …"
        elif kind == "kapitel":
            chapter, paragraf = b.get("num"), None
        elif kind == "paragraf":
            paragraf = b.get("num")
        for m in implements_re.finditer(text):
            uri = directive_uri(m.group("dir"), aliases)
            if uri is None:
                continue
            pinpoints, articles = parse_articles(m.group("refs"))
            out.append({
                "predicate": "rpubl:genomforDirektiv",
                "directive": uri,
                "articles": articles,
                "pinpoints": pinpoints,
                "uris": [uri + "#" + a for a in articles],
                "partial": bool(m.group("partial")),
                "law": law,
                "chapter": chapter,
                "paragraf": paragraf,
                "sentence": m.group(0).strip(),
                "page": b.get("page"),
            })
    return out


# the law a författningskommentar section comments on, named in its level-2
# rubrik: "9.2 Förslaget till lag om ändring i marknadsföringslagen (2008:486)"
# (amends a known SFS) or "9.1 Förslaget till lag om alternativ tvistlösning ..."
# (a new law, resolved by title). Strip the leading section number + "Förslag(et)
# till ".
RUBRIK_SFS_RE = re.compile(r"\((\d{4}:\d+)\)")
RUBRIK_PREFIX_RE = re.compile(r"^\s*\d+(?:\.\d+)*\s*förslag(?:et|en)?\s*"
                              r"(?:till\s+)?", re.IGNORECASE)


def sfs_number(law):
    """The SFS number a `lag om ändring i …` rubrik amends ('… (2008:486)' ->
    '2008:486'), or None for a new law (named by title, no number yet)."""
    m = RUBRIK_SFS_RE.search(law or "")
    return m.group(1) if m else None


def proposed_name(law):
    """The bare proposed-law name from a level-2 rubrik, prefix stripped:
    '9.1 Förslaget till lag om alternativ tvistlösning …' -> 'lag om alternativ
    tvistlösning …'. The caller matches it against the SFS title index."""
    return RUBRIK_PREFIX_RE.sub("", law or "").strip().rstrip(".")


def fk_section(prop_art, new_title):
    """The författningskommentar prose for the proposed law titled `new_title`, as
    one text block -- the förarbete half of the SFS correspondence derivation
    (sfs.correspond consumes this text; the reading of a proposition artifact lives
    here, in the vertical that owns propositioner). Sliced from the level-2 rubrik
    whose "Förslaget till X" names this law (under the FK's level-1 heading) up to
    the next level-2 rubrik -- the next proposed law -- or the document end. Chapter
    and § markers are kept inline so a consumer can place each statement under its
    new paragraf. Robust to the chapter-heading-as-level-1 misdetection that
    truncates `find_kommentar`: it bounds on level-2 (proposed-law) rubriks only."""
    blocks = flatten(prop_art["structure"])
    fk = next((i for i, b in enumerate(blocks)
               if b["type"] == "rubrik" and (b.get("level") or 1) == 1
               and "författningskommentar" in normalize_fold(plain(b["text"]))),
              None)
    if fk is None:
        return ""
    want = norm_title(new_title)
    start = next((i for i in range(fk + 1, len(blocks))
                  if blocks[i]["type"] == "rubrik" and blocks[i].get("level") == 2
                  and norm_title(proposed_name(plain(blocks[i]["text"]))) == want),
                 None)
    if start is None:
        return ""
    end = next((i for i in range(start + 1, len(blocks))
                if blocks[i]["type"] == "rubrik" and blocks[i].get("level") == 2),
               len(blocks))
    return "\n".join(plain(blocks[i]["text"]) for i in range(start, end))


def paragraf_fragment(chapter, paragraf):
    """The SFS fragment id for a commented paragraf, matching the SFS vertical's
    minting: 'K{kap}P{par}' in a chaptered law, 'P{par}' in a flat one
    (sub-letters kept, spaces dropped: '7 a' -> 'P7a'). None without a paragraf."""
    if not paragraf:
        return None
    par = re.sub(r"\s+", "", str(paragraf))
    if chapter:
        return "K%sP%s" % (re.sub(r"\s+", "", str(chapter)), par)
    return "P%s" % par


def main():
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("record", help="a förarbete record JSON (or its artifact)")
    ap.add_argument("--root", default="site/data/forarbete")
    args = ap.parse_args()
    data = json.loads(Path(args.record).read_text())
    art = data if "structure" in data else to_artifact(parse_record(data, args.root))
    records = extract(art)
    print("%d implements-statements in författningskommentar" % len(records))
    for r in records:
        ref = ", ".join(r["pinpoints"]) or ", ".join(r["articles"])
        where = ("%s kap. %s §" % (r["chapter"], r["paragraf"])
                 if r["chapter"] and r["paragraf"]
                 else (r["paragraf"] and r["paragraf"] + " §") or "?")
        print("  %-12s %sgenomför art %s -> %s" % (
            where, "(delvis) " if r["partial"] else "", ref,
            r["directive"].rsplit("/", 1)[-1]))


if __name__ == "__main__":
    main()
