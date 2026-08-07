"""Scan the published case-law artifacts for personal-data-adjacent identifiers
a patch may need to redact: personnummer, samordningsnummer, organisationsnummer,
fastighetsbeteckningar, registreringsnummer, e-post, telefon, bank-/plusgiro,
enskilda firmor and personal names in the clear.

The courts anonymise parties to initials as a matter of course, so what survives
into a published referat is what that anonymisation *missed* -- a number in an
exhibit list, a fastighet in a bodelning, a firm carrying its owner's name.
Those are the ones worth a curated patch (`accommodanda/patches/`), and each is
an editorial call: this only finds and ranks candidates, it changes nothing.

  python tools/pii_scan.py [--source dv] [--out report.md] [--since 30d]

A review tool, not pipeline code: it reads artifacts and prints.
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from accommodanda.lib import compress, layout

# --------------------------------------------------------------------------
# patterns
# --------------------------------------------------------------------------

# A personnummer and an organisationsnummer share one shape, NNNNNN-NNNN. They
# are told apart by the month field: a real month (01-12) in a personnummer,
# >= 20 in an organisationsnummer -- which is how the two number spaces are kept
# disjoint. A samordningsnummer adds 60 to the day, so days run 01-31 or 61-91.
RE_PNR_SHAPE = re.compile(r"(?<![\d-])(\d{2})(\d{2})(\d{2})[-+](\d{4})(?![\d-])")

# an SFS number is "1962:700", a fastighetsbeteckning "Trästa 1:10": a
# capitalised trakt name followed by block:enhet, both small numbers. The
# leading name is what separates it from a lagrum or a case reference, and
# NEAR_FASTIGHET then demands the vocabulary of real property.
RE_FASTIGHET = re.compile(
    r"\b([A-ZÅÄÖ][a-zåäöéü]{2,}(?: [A-ZÅÄÖ][a-zåäöéü]{2,})?) (\d{1,3}:\d{1,4})\b")
NEAR_FASTIGHET = re.compile(
    r"fastighet|lagfart|inteckn|taxeringsvärde|tomt|arrende|servitut|"
    r"bouppteckn|bodelning|köpekontrakt|lantmäteri|avstyckning|"
    r"stamfastighet|samfällighet", re.IGNORECASE)

RE_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b")
# a residential address: a street with a house number, or a postnummer with its
# ort. Marknadsdomstolen prints a sole trader's home address in the party block
# in the clear, next to the personnummer.
RE_ADDRESS = re.compile(
    r"\b([A-ZÅÄÖ][\wåäöé-]*(?:gatan|vägen|gränden|gränd|torget|stigen|backen|"
    r"gången|plan|allén|kajen)\s+\d{1,3}\s*[A-Z]?(?:\s*LGH\s*\d+)?)\b")
RE_POSTORT = re.compile(r"\b(\d{3}\s?\d{2}\s+[A-ZÅÄÖ][A-ZÅÄÖa-zåäöé-]{2,})\b")
# a Swedish number written for a reader: 08-123 45 67, 070-123 45 67, +46 ...
# `\w` on both sides, not just `\d`: a UUID fragment ("af00-938803ffded7") fits
# the digit shape but is not a telephone number
RE_PHONE = re.compile(
    r"(?<![\w-])(?:\+46[ -]?|0)\d{1,3}[ -]\d{2,3}[ ]?\d{2}[ ]?\d{2}(?![\w-])")
RE_GIRO = re.compile(r"\b(?:bankgiro|plusgiro|postgiro|bg|pg)[\s.:nr]{0,6}"
                     r"(\d{3,4}-\d{4})\b", re.IGNORECASE)
# a vehicle plate, with a word nearby that makes it a plate and not a citation
RE_PLATE = re.compile(r"\b([A-ZÅÄÖ]{3}[ ]?\d{2}[0-9A-HJ-PR-UW-Z])\b")
NEAR_PLATE = re.compile(r"registreringsnummer|reg\.?\s?nr|personbil|fordon|bilen|"
                        r"lastbil|släpvagn|motorcykel|husvagn", re.IGNORECASE)

# a *named* firm run by a natural person -- an enskild firma has no legal
# personality, so its name identifies its owner as surely as a personnummer.
# The name is captured, not the surrounding discussion: "den enskilda firman
# Båstad Gräv & Schakt", not every sentence containing the word "firman".
# every word of the captured name must be capitalised (or "&"), so "firman
# Båstad Gräv & Schakt" is a name and "firman skulle åta sig" is a verb phrase
# NB the keyword alone is case-insensitive, via an inline group: a whole-pattern
# re.IGNORECASE would make the capture's [A-ZÅÄÖ] match lowercase too, and
# "firman utgjort en inledande del" would read as a firm name
RE_ENSKILD_FIRMA = re.compile(
    r"\b(?i:enskild[ae]? firma[n]?|firman|enskild näringsidkare)\s+"
    r"((?:[A-ZÅÄÖ][\w.\-]*|&)(?:\s+(?:[A-ZÅÄÖ][\w.\-]*|&)){0,4})")

# a full personal name (given + a surname with a Swedish surname ending) in a
# corpus whose parties are initials
# only the endings that are surnames and not also placenames: -berg, -lund,
# -holm and -borg name a landsting or a company as often as a person
RE_FULLNAME = re.compile(
    r"\b([A-ZÅÄÖ][a-zåäöé]{2,}(?:[ -][A-ZÅÄÖ][a-zåäöé]{2,})?)"
    r" ([A-ZÅÄÖ][a-zåäöé]{2,}(?:sson|son|ström|qvist|kvist|gren|"
    r"stedt|dahl|én))\b")
# a company or an authority carrying a person-shaped name ("AB Hällde Maskiner",
# "Salong Alexander AB") is not a natural person
RE_ORGWORD = re.compile(
    r"\b(?:AB|HB|KB|Aktiebolag|Handelsbolag|Landsting|Kommun|Salong|"
    r"Televerket|Bolaget|Förbund|Stiftelse|Förening|Myndighet|Institut)")
# ...but a judge, a lay assessor, a lawyer, an official or an author is named in
# the open on purpose, and that is the overwhelming majority of full names in a
# referat -- the panel roster alone accounts for most of them
NEAR_OFFICIAL = re.compile(
    r"justitieråd|hovrättsråd|hovrättslagman|rådman|lagman|tingsfiskal|"
    r"assessor|nämndeman|revisionssekreterare|föredragande|referent|"
    r"ordförande|sekreterare|advokat|jur\.?\s?kand|ombud|åklagar|kronofogde|"
    r"professor|docent|chefsjurist|justitiekansler|justitieombudsman|domare|"
    r"expert|utredare|departementsråd|kansliråd|generaldirektör|"
    r"verkställande direktör|miljöråd|tekniskt råd|skiljeman|bitr\.|"
    r"rättens|dömande|deltagit|enhällig|skiljaktig|RevSekr|JustR|"
    r"ledamot|ledamöter|ersättare|sakkunnig|protokollför|närvarande|"
    r"beslutande|justitier|förvaltningsrättsråd|kammarrättsråd|"
    r"kammarrättslagman|rättssekreterare|notarie|överåklagare|vice ordf",
    re.IGNORECASE)
# ...and conversely, a name standing where a *party* stands is the interesting
# case: the court anonymises those to initials, so a full name there is a miss
NEAR_PARTY = re.compile(
    r"\bkärande|\bsvarande|tilltalad|målsägand|gäldenär|borgenär|"
    r"klagande|motpart|sökande|arbetstagare|arbetsgivare|"
    r"vårdnadshavare|dödsbo|makarna|maken|makan|sambo|"
    r"parter[:na]|\bparts\b|anställd|uppsagd|avskedad", re.IGNORECASE)


def _pnr_kind(groups):
    """'personnummer' / 'samordningsnummer' / 'organisationsnummer', or None for
    a NNNNNN-NNNN that is neither (a målnummer, a page range, a date span)."""
    month, day = int(groups[1]), int(groups[2])
    if month >= 20:
        return "organisationsnummer"
    if 1 <= month <= 12 and 1 <= day <= 31:
        return "personnummer"
    if 1 <= month <= 12 and 61 <= day <= 91:
        return "samordningsnummer"
    return None


def _luhn_ok(digits):
    """Luhn over the 10 digits -- the cheap way to drop the målnummer and page
    ranges that happen to fit the shape. Both number spaces carry the check."""
    total = 0
    for i, ch in enumerate(digits):
        n = int(ch) * (2 if i % 2 == 0 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


# --------------------------------------------------------------------------
# scan
# --------------------------------------------------------------------------

def texts(art):
    """Every string leaf of an artifact, so a hit is found wherever it sits --
    body stycken, sammanfattning, sökord, målnummer, label."""
    out = []

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str):
            out.append(node)
    walk(art)
    return out


def _context(text, start, end, width=70):
    return re.sub(r"\s+", " ", text[max(0, start - width):end + width]).strip()


def scan_text(text):
    """(kind, hit, context) for every candidate in one string."""
    found = []
    for m in RE_PNR_SHAPE.finditer(text):
        kind = _pnr_kind(m.groups())
        if kind and _luhn_ok("".join(m.groups())):
            found.append((kind, m.group(0), _context(text, m.start(), m.end())))
    for kind, pattern in (("e-post", RE_EMAIL), ("giro", RE_GIRO),
                          ("telefon", RE_PHONE), ("adress", RE_ADDRESS),
                          ("postort", RE_POSTORT)):
        for m in pattern.finditer(text):
            found.append((kind, m.group(0), _context(text, m.start(), m.end())))
    for m in RE_FASTIGHET.finditer(text):
        ctx = _context(text, m.start(), m.end(), 120)
        if NEAR_FASTIGHET.search(ctx):
            found.append(("fastighet", m.group(0), ctx))
    for m in RE_PLATE.finditer(text):
        ctx = _context(text, m.start(), m.end(), 100)
        if NEAR_PLATE.search(ctx):
            found.append(("fordon", m.group(1), ctx))
    for m in RE_ENSKILD_FIRMA.finditer(text):
        found.append(("firma", m.group(1).strip(),
                      _context(text, m.start(), m.end(), 40)))
    for m in RE_FULLNAME.finditer(text):
        ctx = _context(text, m.start(), m.end(), 90)
        near = text[max(0, m.start() - 25):m.end() + 25]
        if (NEAR_PARTY.search(ctx) and not NEAR_OFFICIAL.search(ctx)
                and not RE_ORGWORD.search(near)):
            found.append(("namn", m.group(0), ctx))
    return found


def since_cutoff(since):
    """A `--since` value -> a POSIX timestamp. Accepts a date ("2026-08-01") or
    a number of days back ("30", "30d") -- the second is what a recurring sweep
    wants: "everything parsed since I last looked"."""
    if since is None:
        return None
    if re.fullmatch(r"\d+d?", since):
        return (datetime.now() - timedelta(days=int(since.rstrip("d")))).timestamp()
    return datetime.strptime(since, "%Y-%m-%d").timestamp()


def scan_source(source, limit=None, since=None):
    """({kind: [(label, hit, context)]}, artifacts scanned, unreadable paths).

    `since` narrows the sweep to artifacts written on or after that point, by
    file mtime -- a document that was re-parsed is a document whose text may
    have changed, which is exactly the set a recurring sweep should re-read.

    `layout.artifacts` already filters the index sidecars out, so anything it
    yields that will not parse is a broken artifact -- reported rather than
    skipped, since a truncated artifact is a corpus defect in its own right."""
    hits, unreadable = defaultdict(list), []
    paths = sorted(layout.artifacts(source))
    if (cutoff := since_cutoff(since)) is not None:
        # `layout.artifacts` yields *logical* paths; the file on disk is the
        # brotli variant, so stat through compress rather than the Path
        paths = [p for p in paths if compress.stat(p).st_mtime >= cutoff]
    if limit:
        paths = paths[:limit]
    for path in paths:
        raw = compress.read_text(path)
        if not raw.strip():
            unreadable.append(path)
            continue
        art = json.loads(raw)
        label = art.get("label") or art.get("uri")
        seen = set()
        for text in texts(art):
            for kind, hit, ctx in scan_text(text):
                if (kind, hit) not in seen:
                    seen.add((kind, hit))
                    hits[kind].append((label, hit, ctx))
    return hits, len(paths), unreadable


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="dv")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--since", metavar="DATE|DAYS",
                    help="only artifacts (re)written since then: 2026-08-01, or 30d")
    ap.add_argument("--per-kind", type=int, default=400)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    hits, total, unreadable = scan_source(args.source, args.limit, args.since)
    out = ["# PII-adjacent identifiers in `%s` (%d artifacts scanned)\n"
           % (args.source, total)]
    for kind in sorted(hits, key=lambda k: -len(hits[k])):
        rows = hits[kind]
        docs = Counter(label for label, _, _ in rows)
        out.append("\n## %s — %d hits in %d documents\n" % (kind, len(rows), len(docs)))
        for label, hit, ctx in rows[:args.per_kind]:
            out.append("- **%s** `%s`\n  > …%s…" % (label, hit, ctx))
        if len(rows) > args.per_kind:
            out.append("\n_(%d further hits not listed)_" % (len(rows) - args.per_kind))
    if unreadable:
        out.append("\n## unreadable artifacts — %d\n" % len(unreadable))
        out += ["- `%s` (empty)" % p for p in unreadable]
    report = "\n".join(out)
    if args.out:
        args.out.write_text(report, encoding="utf-8")
        print("%s: %d kinds, %d hits over %d artifacts -> %s" % (
            args.source, len(hits), sum(len(v) for v in hits.values()),
            total, args.out))
    else:
        print(report)


if __name__ == "__main__":
    main()
