"""Extract EU-implementation relations from a proposition's författningskommentar.

The författningskommentar (the section-by-section commentary at the end of a
proposition) routinely states, for a paragraf, which EU directive article it
implements -- "Paragrafen genomför artikel 21.1-21.3 i NIS 2-direktivet". That
is a *genomför* (implements) relation, stronger than a bare reference: it ties a
Swedish provision to the exact EU-law article it transposes, which is the
machine-readable mapping that otherwise only exists as ad-hoc tables.

This module extracts those statements:

  - the directive is resolved to a CELEX uri. A named directive ("NIS 2-
    direktivet") is resolved from its defining sentence -- the alias in
    parentheses binds to the *subject* directive (the first "(EU) yyyy/n"),
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

from .parse import parse_record, to_artifact
from ..lib.lagrum import EULAGSTIFTNING, LagrumParser

# "Paragrafen genomför [delvis] artikel(n/na) <refs> i <directive>"; the article
# block is captured loosely (digits, dots, letters, ranges, "och"/"samt"/commas)
# up to " i <name>direktivet" -- note article numbers contain dots, so we do not
# split on '.'
IMPLEMENTS_RE = re.compile(
    r"(?P<subject>Paragrafen|Paragraferna|Bestämmelsen|Lagförslaget|"
    r"Ändringen|Punkten)\w*\s+(?:[a-zåäö ]*?\s)?"
    r"genomför(?:s|t)?\s+(?P<partial>delvis\s+)?"
    r"(?:artikel|artiklarna)\s+(?P<refs>[0-9][0-9.,‐-―\- a-zåäö]*?)"
    r"\s+i\s+(?P<dir>(?:[A-Za-zÅÄÖåäö0-9./()‐-―\- ]*?)?direktivet)",
    re.IGNORECASE)

# one article pinpoint inside the refs span: article number, optional dotted
# subsection(s), optional trailing letter ("23.4 a", "21.1", "28")
ARTICLE_RE = re.compile(r"(\d+(?:\.\d+)*)\s*([a-z])?", re.IGNORECASE)

# a directive defined with an alias: "... direktiv (EU) 2022/2555 ... (NIS 2-
# direktivet)". The alias binds to the subject (first) directive of the sentence.
ALIAS_RE = re.compile(r"\(([^()]*?direktivet)\)")


def plain(runs):
    return "".join(r if isinstance(r, str) else r.get("text", "") for r in runs)


def _refparser():
    return LagrumParser({}, basefile="prop", parse_types=[EULAGSTIFTNING])


def resolve_directives(blocks, parser):
    """Map each directive alias used in the proposition to a CELEX uri, plus a
    'default' for a bare "direktivet". A defining sentence is one ending in
    "(<alias>direktivet)"; the alias binds to its first EU-act citation (the
    subject), not the acts it amends/repeals."""
    aliases = {}
    for b in blocks:
        text = plain(b["text"])
        for m in ALIAS_RE.finditer(text):
            sentence = text[max(0, m.start() - 400):m.end()]
            refs = parser.parse_text(sentence, context={})
            if refs:
                aliases[m.group(1).lower()] = refs[0].uri   # subject directive
    # the dominant directive (most-cited CELEX) backs a bare "direktivet"
    if aliases:
        aliases["default"] = max(set(aliases.values()), key=list(
            aliases.values()).count)
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


def directive_uri(name, aliases):
    """CELEX uri for a directive named in an implements statement, or None."""
    key = name.strip().lower()
    if key in aliases:
        return aliases[key]
    return aliases.get("default")    # bare "direktivet" / unrecognised alias


def find_kommentar(blocks):
    """The block index range [start, end) of the författningskommentar section
    (its level-1 heading to the next level-1 heading), or None."""
    start = next((i for i, b in enumerate(blocks)
                  if b["type"] == "rubrik" and (b.get("level") or 1) == 1
                  and "författningskommentar" in plain(b["text"]).lower()), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(blocks))
                if blocks[i]["type"] == "rubrik"
                and (blocks[i].get("level") or 1) == 1), len(blocks))
    return start, end


def extract(art):
    """Implements relations stated in a proposition artifact's
    författningskommentar, each tied to the paragraf it comments on. The
    paragraf is tracked from the font-derived `kapitel`/`paragraf` structure
    blocks the parser emits. Records: {predicate, directive, articles,
    pinpoints, uris, partial, law, chapter, paragraf, sentence, page}."""
    blocks = art["body"]
    parser = _refparser()
    aliases = resolve_directives(blocks, parser)
    span = find_kommentar(blocks)
    if span is None:
        return []
    out = []
    law = chapter = paragraf = None
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
        for m in IMPLEMENTS_RE.finditer(text):
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


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("record", help="a förarbete record JSON (or its artifact)")
    ap.add_argument("--root", default="site/data/forarbete")
    args = ap.parse_args()
    data = json.loads(Path(args.record).read_text())
    art = data if "body" in data else to_artifact(parse_record(data, args.root))
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
