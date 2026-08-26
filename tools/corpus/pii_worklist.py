"""Rank `pii_scan`'s candidates into a review worklist: one row per document,
scored by which identifiers it carries and how they combine.

A lone postort ("121 56 Johanneshov") is not personal data; a personnummer next
to a home address next to a name is. So the ranking is about *co-occurrence* --
what makes a document identify a natural person rather than merely mention a
place.

An organisationsnummer is not automatically company data either: a sole
trader's *is* their personnummer, and a one-person AB's points at exactly one
natural person. Being in a public register does not make an identifier
non-personal. So an orgnr scores when its company is one person's (ONE_PERSON,
EPONYMOUS_FIRM) and not otherwise. A fleet's registreringsnummer and a
switchboard number stay at zero.

  python tools/pii_worklist.py [--source dv] [--out worklist.md] [--since 30d]

`--since` narrows the sweep to artifacts (re)parsed since then, which is how
this is meant to be run on an ongoing basis: sweep the new cases, not the
whole corpus, every time the nightly build lands a batch.

Writes PII. Send the output somewhere untracked (`wip/` is gitignored), never
into the repo proper.
"""

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.corpus.pii_scan import scan_source

# what each kind is worth on its own. An identifier that pins a *natural person*
# scores; one that names a company or a place does not.
WEIGHT = {
    "personnummer": 100,        # direct identifier, no context needed
    "samordningsnummer": 100,
    "e-post": 12,               # weighted up below when the domain is private
    "telefon": 8,               # ...and when the number is a mobile
    "adress": 10,               # ...and only a *residential* one, see RESIDENCE
    "namn": 10,
    "fastighet": 4,             # a private-law fastighet; see PRIVATE_LAW
    "firma": 4,
    "postort": 1,               # alone, a place; in company, an address
    "organisationsnummer": 40,  # when it is a one-person company's; see ONE_PERSON
    "fordon": 0,                # overwhelmingly company fleets
    "giro": 0,                  # every hit so far was a beslagsnummer
}

# One kind must not swamp the score. A hyresmål comparing twenty apartments, or
# an estate agent's commission list, piles up street addresses that say nothing
# about a person, and un-capped they outranked a case naming a child and her
# personnummer. Beyond the third hit a kind adds nothing: what matters is
# *which* identifiers a document carries, not how many times.
MAX_PER_KIND = 3

# a consumer mailbox is the individual's own; @employer.se is their work role
PRIVATE_MAIL = re.compile(
    r"@(?:hotmail|gmail|yahoo|live|outlook|telia|comhem|bredband2?|spray|"
    r"passagen|swipnet|glocalnet|tele2|bahnhof|icloud|me|msn|aol)\.",
    re.IGNORECASE)
MOBILE = re.compile(r"^07\d")
# a fastighet in a bodelning or an utmätning ties to a person; one in a
# detaljplan case is the subject of a public administrative decision
PRIVATE_LAW = re.compile(
    r"bodelning|arvskifte|bouppteckning|utmätning|konkurs|skuldsanering|"
    r"äktenskapsförord|testamente|dödsbo|gåva|laglott|sambo", re.IGNORECASE)
# an address is personal data when it is where somebody *lives*. A hyresmål's
# jämförelselägenheter, an estate agent's objects and a detaljplan's properties
# are addresses of buildings, not of people.
RESIDENCE = re.compile(
    r"bosatt|folkbokför|hemadress|bostadsadress|hemvist|"
    r"\bLGH\b|\bLgh\b|\blgh\b|adress[:e]?n? (?:är|var)|"
    r"i sin bostad|till bostaden|bodde (?:på|i)", re.IGNORECASE)
# ...and the addresses printed in a party block are the professionals': the
# prosecutor's box, the defence firm's street, the rights-holder's registered
# office. Those are business contact details, published on purpose.
PROFESSIONAL = re.compile(
    r"advokat|advokatfirma|advokatbyrå|jur\.?\s?kand|ombud|åklagar|"
    r"riksenheten|myndighet|\bBox\b|\bAB\b|\bKB\b|\bHB\b|förvaltare|"
    r"domstol|nämnd|kammare|Rättighetsalliansen|"
    # an incorporated party has a registered office, not a home. Deliberately
    # no "firma": an enskild firma's address *is* its owner's, which is how
    # Marknadsdomstolen prints a sole trader's home in the party block.
    r"aktiebolag|handelsbolag|kommanditbolag|ekonomisk förening|"
    r"\bförening\b|stiftelse|samfällighet|kommun\b|landsting|region\b",
    re.IGNORECASE)
# An organisationsnummer is *not* automatically company data. A sole trader's
# is literally their personnummer, and a one-person AB's points at exactly one
# natural person — the public register says who. Being public does not make an
# identifier non-personal, so what matters is whether the company behind the
# number is one person.
ONE_PERSON = re.compile(
    r"enmansbolag|enmansaktiebolag|fåmansbolag|fåmansföretag|fåmansaktiebolag|"
    r"enskild[ae]? firma|enskild näringsidkare|"
    r"ensam ägare|ensam aktieägare|ensam(?:t)? äg[dts]|helägt av|"
    r"ende styrelseledamot|ensam styrelseledamot|enda styrelseledamot|"
    r"eget bolag|egna bolag|sitt bolag|sitt eget bolag|hans bolag|hennes bolag|"
    r"eget aktiebolag|egen firma|ägs av [A-ZÅÄÖ]|ägdes av [A-ZÅÄÖ]",
    re.IGNORECASE)
# NB there is deliberately no "the firm carries a surname" signal. It was tried
# and had *zero* precision: every one of the 22 hits it produced was an ordinary
# limited company whose trade name happens to contain a founder's surname
# ("Söderhamn Eriksson AB", "Nilssons Grus & Transport", "Otterdahls
# Bilservice") -- which is unremarkable in Swedish business and says nothing
# about ownership. What makes an orgnr personal data is that *one person* is
# behind it, and only the text can say so.


@dataclass
class Doc:
    """One document's accumulated score and the hits behind it."""
    points: int = 0
    rows: list[tuple[str, str, str]] = field(default_factory=list)   # kind, hit, context
    notes: set[str] = field(default_factory=set)
    per_kind: defaultdict[str, int] = field(default_factory=lambda: defaultdict(int))


def score(kind, hit, context):
    """(points, note) for one hit. `kind` is indexed, not `.get`-defaulted:
    a pattern added to `pii_scan` and not weighted here would otherwise score
    zero and vanish -- a new detector that silently finds nothing."""
    base = WEIGHT[kind]
    if kind == "e-post" and PRIVATE_MAIL.search(hit):
        return base * 3, "privat mejladress"
    if kind == "telefon" and MOBILE.match(hit.replace(" ", "").replace("-", "")):
        return base * 2, "mobilnummer"
    if kind == "fastighet":
        return (base * 4, "fastighet i civilrättsligt mål") if PRIVATE_LAW.search(context) \
            else (0, None)
    if kind == "adress":
        # a residence marker wins over the party-block form words: "med enskild
        # firma …, Södra Klöverstigen 81 LGH 1201" is a home whatever else the
        # line names
        if RESIDENCE.search(context):
            return base * 3, "bostadsadress"
        if PROFESSIONAL.search(context):
            return 0, None
        return 1, None              # a building, until something says otherwise
    if kind == "organisationsnummer":
        return ((base, "enmansbolagets orgnr") if ONE_PERSON.search(context)
                else (0, None))
    return base, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="dv")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--since", metavar="DATE|DAYS",
                    help="only artifacts (re)written since then: 2026-08-01, or 30d")
    ap.add_argument("--top", type=int, default=120)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    hits, total, unreadable = scan_source(args.source, args.limit, args.since)
    docs = defaultdict(Doc)
    for kind, rows in hits.items():
        for label, hit, ctx in rows:
            points, note = score(kind, hit, ctx)
            if not points:
                continue
            doc = docs[label]
            doc.rows.append((kind, hit, ctx))
            # cap each kind's contribution -- see MAX_PER_KIND
            doc.per_kind[kind] += 1
            if doc.per_kind[kind] <= MAX_PER_KIND:
                doc.points += points
            if note:
                doc.notes.add(note)

    ranked = sorted(docs.items(), key=lambda kv: -kv[1].points)
    out = ["# PII review worklist — `%s` (%d artifacts, %d documents scored)\n"
           % (args.source, total, len(docs)),
           "Ranked by how strongly the document identifies a natural person. "
           "An organisationsnummer counts when the company behind it is one "
           "person (enskild firma, enmansbolag, a firm named after its owner) "
           "— a public register entry is still personal data. Fleet plates and "
           "switchboard numbers do not.\n"]
    for label, doc in ranked[:args.top]:
        out.append("\n## %s — %d p%s\n" % (
            label, doc.points,
            " (%s)" % ", ".join(sorted(doc.notes)) if doc.notes else ""))
        for kind, hit, ctx in sorted(doc.rows):
            out.append("- `%s` **%s**\n  > …%s…" % (kind, hit, ctx))
    if len(ranked) > args.top:
        out.append("\n_(%d further documents scored lower)_" % (len(ranked) - args.top))
    if unreadable:
        out.append("\n## unreadable artifacts — %d\n" % len(unreadable))
        out += ["- `%s` (empty)" % p for p in unreadable]

    report = "\n".join(out)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print("%s: %d documents scored, top %d -> %s"
              % (args.source, len(docs), min(args.top, len(ranked)), args.out))
    else:
        print(report)


if __name__ == "__main__":
    main()
