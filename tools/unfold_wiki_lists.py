#!/usr/bin/env python3
"""Repair the lists the MediaWiki->markdown conversion folded onto one line.

MediaWiki lists are line-based (`# item` / `* item` per line), but the wikitext
paragraph splitter `mediawiki_to_markdown` reads through joined consecutive
lines into one paragraph, and `_stycke_to_md` then escaped only the *leading*
marker (`^#` -> `\\#`, "so the markdown parser keeps it as prose"). The result is
a list printed as one run-on sentence with its markers still in the text:

    \\# Båda makarnas närvaro # Deras uttryckliga samtycke # Att vigselförättaren…
    * Fritt utnyttjande, t ex 12 §. * Begränsat utnyttjande mot ersättning…
    1) Ett spridande av 2) ett hot eller missaktning 3) måste ske på allmän plats

No markdown parser renders those as a list, so this rewrites them to one item
per line. Three source forms, each requiring the *whole* line to be a list (it
must start with a marker) so a stray `*` or a paragraph mentioning "2)" is never
split:

  * `\\# a # b`      -- a MediaWiki ordered list  -> `1. a` / `2. b`
  * `* a * b`       -- a bullet list             -> `* a` / `* b`
  * `1) a 2) b`     -- an already-numbered list  -> `1. a` / `2. b`

The numbered forms additionally require the markers to run consecutively from
the line's own first number, which is what keeps "22 §" and "1962:700" out.

Usage:  unfold_wiki_lists.py <content-repo>  [--apply]
"""
import re
import sys
from pathlib import Path

# A line that is entirely a folded list, by form. Each pattern anchors on the
# line's first marker; the separators are found by SPLIT_* below. Every pattern
# ends in a lookahead, because the match end is where the item's *text* starts
# and consuming that first character would drop it ("1) Ett" -> "1. tt").
# The space after a MediaWiki `#` is optional and some pages omit it
# ("\\#utsläpp av avloppsvatten, #användning av mark", Miljöfarlig verksamhet).
RE_HASH_LINE = re.compile(r"^\\#\s*(?=\S)")
RE_BULLET_LINE = re.compile(r"^([*+])\s+(?=\S)")
RE_NUM_LINE = re.compile(r"^(\d+)([).])\s+(?=\S)")

# a separator *inside* the line: whitespace, the marker, whitespace
SPLIT_HASH = re.compile(r"\s+#\s*(?=\S)")
SPLIT_NUM = re.compile(r"\s+(\d+)([).])\s+")


def _split_bullet(line, marker):
    return [p.strip() for p in
            re.split(r"\s+\%s\s+" % marker, line[1:].strip()) if p.strip()]


def _split_numbered(line):
    """Items of a `1) a 2) b` line, or None when the numbers are not consecutive
    (so a paragraph that merely contains "… 2) …" is left alone)."""
    first = RE_NUM_LINE.match(line)
    start = int(first.group(1))
    parts, nums, last, expect = [], [], first.end(), start + 1
    for m in SPLIT_NUM.finditer(line, first.end()):
        if int(m.group(1)) != expect:
            continue                       # not this list's next number
        parts.append(line[last:m.start()].strip())
        nums.append(expect)
        last, expect = m.end(), expect + 1
    if not parts:
        return None
    parts.append(line[last:].strip())
    return [p for p in parts if p]


# a list folded onto the end of its own lead-in sentence ("Två rekvisit ska vara
# uppfyllda: # vilseledande … # och …"). The colon ends the sentence, so the
# split point is unambiguous -- but only at the *first* colon that a marker
# follows, since the items themselves may contain one ("Försök till: mord, …").
RE_LEADIN = re.compile(r":\s+(?=(?:#|[*+]|\d+[).])\s)")


def unfold(line):
    """A folded list line -> its items, or None when the line is not one.
    A lead-in sentence, when there is one, comes back as the first element."""
    line = line.rstrip()
    m = RE_LEADIN.search(line)
    if m and not (RE_HASH_LINE.match(line) or RE_BULLET_LINE.match(line)
                  or RE_NUM_LINE.match(line)):
        tail = line[m.end():]
        # only a *leading* `#` was escaped by the converter, so a list that
        # starts mid-line still carries the bare MediaWiki marker
        rest = unfold("\\" + tail if tail.startswith("#") else tail)
        # a lead-in whose tail holds only one item is a sentence with a stray
        # marker in it, not a list -- leave it alone
        return [line[:m.end()].rstrip()] + rest if rest and len(rest) > 1 else None
    if RE_HASH_LINE.match(line):
        items = [p.strip() for p in SPLIT_HASH.split(RE_HASH_LINE.sub("", line)) if p.strip()]
        return ["%d. %s" % (i, t) for i, t in enumerate(items, 1)] if items else None
    m = RE_BULLET_LINE.match(line)
    if m:
        items = _split_bullet(line, m.group(1))
        return ["%s %s" % (m.group(1), t) for t in items] if items else None
    if RE_NUM_LINE.match(line):
        items = _split_numbered(line)
        if items:
            start = int(RE_NUM_LINE.match(line).group(1))
            return ["%d. %s" % (i, t) for i, t in enumerate(items, start)]
    return None


RE_MARKER = re.compile(r"^(?:\\#|[*+]|\d+[).])\s*")


def check_lossless(line, items):
    """Unfolding may only move markers and line breaks -- never text. Compares
    the words of the folded line with the words of the items, both stripped of
    list markers, and raises when they differ (a mis-split would show up here as
    a lost or duplicated word)."""
    def words(s):
        # the same normalisation on both sides: drop the leading marker, then
        # every inner one (`… mark, #användning …`, `… av 2) ett hot …`). It may
        # also strip a page reference like "s. 702." -- harmless, because both
        # sides get the identical treatment.
        s = re.sub(r"(?<=\s)(?:#|[*+]|\d+[).])\s*", "", RE_MARKER.sub("", s.strip()))
        return s.split()
    before = words(line)
    after = [w for item in items for w in words(item)]
    if before != after:
        # strict=False on purpose: a length difference is one of the failures
        # being reported, and the index falls back to the shorter length
        at = next((i for i, (a, b) in enumerate(zip(before, after, strict=False))
                   if a != b), min(len(before), len(after)))
        raise ValueError(
            "unfolding changed the text of %r at word %d:\n  before %r\n  after  %r"
            % (line[:80], at, before[at:at + 6], after[at:at + 6]))


def is_rewritten(line, items):
    """Whether `unfold`'s result is a change worth writing: several items, or a
    lone MediaWiki `\\#` item whose marker still has to become a markdown one."""
    return bool(items) and (len(items) > 1 or line.startswith("\\#"))


def rewrite(text):
    """The file with every folded list line expanded; unchanged text otherwise."""
    out, changed = [], 0
    for line in text.split("\n"):
        items = unfold(line)
        if is_rewritten(line, items):
            check_lossless(line, items)
            out.extend(items)
            changed += 1
        else:
            out.append(line)
    return "\n".join(out), changed


def main(root, apply_it):
    files = [p for t in ("commentary", "concept")
             for p in sorted((Path(root) / t).rglob("*.md"))]
    touched = items = 0
    for p in files:
        text = p.read_text()
        new, changed = rewrite(text)
        if not changed:
            continue
        touched += 1
        items += changed
        print(f"\n=== {p.relative_to(root)}  ({changed} folded list(s))")
        for line in text.split("\n"):
            got = unfold(line)
            if is_rewritten(line, got):
                print(f"  -  {line[:150]}")
                for g in got:
                    print(f"  +  {g[:150]}")
        if apply_it:
            p.write_text(new)
    print(f"\n{touched} files, {items} folded lists "
          f"{'rewritten' if apply_it else '(dry run -- pass --apply to write)'}")


if __name__ == "__main__":
    main(sys.argv[1], "--apply" in sys.argv)
