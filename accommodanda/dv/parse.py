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

  python -m accommodanda.dv_parse --uuid UUID         # one record -> artifact
  python -m accommodanda.dv_parse --index INDEX [--limit N]  # batch + report
"""

import argparse
import functools
import html as htmllib
import json
import re
import sys
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup

from .model import Avgorande, Lagrum, Rubrik, Stycke
from .structure import nest
from ..lib.lagrum import (EULAGSTIFTNING, EURATTSFALL, FORARBETEN, KORTLAGRUM,
                     LAGRUM, MYNDIGHETSBESLUT, RATTSFALL, LagrumParser,
                     interleave, load_abbreviations, load_namedlaws)

# Court decisions cite across the whole spectrum of legal sources, so the
# DV citation scanner enables every ported grammar.
DV_PARSE_TYPES = [LAGRUM, KORTLAGRUM, EULAGSTIFTNING, RATTSFALL, FORARBETEN,
                  EURATTSFALL, MYNDIGHETSBESLUT]

DOMSTOL_DEFAULT = "site/data/domstol/downloaded"
INDEX_DEFAULT = "site/data/dv/identity-index.json"
SFS_NAMEDLAWS = "lagen/nu/res/extra/sfs_namedlaws.json"

# SFS id at the head of a lagen.nu lagrum URI (https://lagen.nu/1995:450#P4)
RE_SFS_URI = re.compile(r"https://lagen\.nu/(\d{4}:\d+)")

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


def collapse(text):
    text = text.replace("\xa0", " ")
    # collapse runs of spaces/tabs but keep explicit newlines (from <br>)
    text = re.sub(r"[ \t]+", " ", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def is_heading(text):
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


def parse_innehall(html):
    """Flat list of Rubrik / Stycke blocks from the decision HTML."""
    soup = BeautifulSoup(html or "", "html.parser")
    blocks = []
    paragraphs = soup.find_all("p") or [soup]
    for p in paragraphs:
        for br in p.find_all("br"):
            br.replace_with("\n")
        text = collapse(htmllib.unescape(p.get_text()))
        if not text or RE_SEPARATOR.match(text):
            continue
        m = RE_NUMPARA.match(text)
        if m:
            blocks.append(Stycke(text=collapse(m.group(2)), ordinal=m.group(1)))
        elif is_heading(text):
            blocks.append(Rubrik(text=text))
        else:
            blocks.append(Stycke(text=text))
    return blocks


def parse_api_record(d):
    """API record dict -> Avgorande."""
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
        body=parse_innehall(d.get("innehall")),
        sources=["domstol"],
    )


@functools.cache
def legal_vocab():
    """Named-law and abbreviation tables for the citation scanner, loaded
    once. KORTLAGRUM is enabled (court decisions cite both full law names
    and abbreviations -- "12 kap. 57 § JB", "10 kap. 10 § RB")."""
    return load_namedlaws(SFS_NAMEDLAWS), load_abbreviations(SFS_NAMEDLAWS)


def scan_body(body):
    """Each body block's text as an inline-run list (plain `str` runs
    interleaved with `{"predicate", "uri", "text"}` link dicts at their
    exact positions) -- the same shape SFS emits for its text nodes, so the
    discovered citations live inline rather than in a flat list. A court
    decision has no base law, so the scanner runs with an empty context
    (relative refs without a named law stay unlinked); one parser threads
    the whole body in document order so "samma lag" and in-document
    law-name learning carry across blocks."""
    parser = _scanner()
    parser.state = type(parser.state)()   # fresh per-document state
    return [interleave(b.text, parser.parse_text(b.text, context={}))
            for b in body]


@functools.cache
def _scanner():
    """The body citation scanner, built once (grammar compilation is the
    expensive part); scan_body resets its per-document state each call."""
    namedlaws, abbreviations = legal_vocab()
    return LagrumParser(namedlaws, basefile="dom", abbreviations=abbreviations,
                        parse_types=DV_PARSE_TYPES)


def body_links(runs_per_block):
    """The link dicts across every block's runs (for the recall stat)."""
    return [run for runs in runs_per_block for run in runs
            if isinstance(run, dict)]


def slug(case_id):
    return re.sub(r"[^\w]+", "_", case_id).strip("_")


@functools.cache
def _rattsfall_parser():
    return LagrumParser({}, basefile="dom", parse_types=[RATTSFALL])


def case_uri(cid):
    """The published document URI for a court decision.

    A referat case (the vast majority, and the only kind a citation can name)
    is minted by running its referat through the *same* RATTSFALL citation
    parser, so the document URI is byte-identical to what any reference to it
    produces -- the old pipeline's `dom/{serie}/{year}:{nr}` /
    `dom/nja/{year}s{page}` / `.../not/{n}` scheme. This is the published
    identifier the old site used; it must not change.

    A non-referat case (~7%, never a citation target) has no such canonical
    form; it keeps a stable slug URI for now -- restoring the old verdict
    scheme `dom/{court}/{malnummer}/{date}` for these is tracked separately."""
    refs = _rattsfall_parser().parse_text(cid, context={})
    return refs[0].uri if refs else "https://lagen.nu/dom/" + slug(cid)


def to_artifact(av, canonical_id=None):
    runs = scan_body(av.body)
    def block(b, text):
        if isinstance(b, Rubrik):
            return {"type": "rubrik", "text": text}
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
        # skeleton); the renderer flattens it back to the linear body
        "structure": nest([block(b, text) for b, text in zip(av.body, runs)]),
        "sources": av.sources,
    }


def find_api_record(domstoldir, uuid):
    hits = list(Path(domstoldir).rglob(uuid + ".json"))
    if not hits:
        sys.exit("no API record with uuid %s under %s" % (uuid, domstoldir))
    return json.loads(hits[0].read_text())


def api_member(case):
    for member in case["members"]:
        if member["store"] == "domstol":
            return member
    return None


def cmd_uuid(args):
    record = find_api_record(args.domstoldir, args.uuid)
    av = parse_api_record(record)
    json.dump(to_artifact(av), sys.stdout, ensure_ascii=False, indent=2)
    print()


def curated_sfs(record):
    """SFS numbers the editors tagged in lagrumLista (the recall oracle)."""
    return {l["sfsNummer"] for l in record.get("lagrumLista", [])
            if l.get("sfsNummer")}


def found_sfs(refs):
    return {m.group(1) for r in refs
            if (m := RE_SFS_URI.match(r["uri"]))}


def cmd_index(args):
    cases = json.loads(Path(args.index).read_text())
    cases = [c for c in cases if api_member(c)]
    if args.limit:
        cases = cases[:args.limit]
    counts = Counter()
    blockstats = Counter()
    refstats = Counter()
    failures = []
    for case in cases:
        member = api_member(case)
        try:
            record = json.loads(Path(member["path"]).read_text())
            av = parse_api_record(record)
        except Exception as e:
            failures.append((case["canonical_id"], "%s: %s"
                             % (type(e).__name__, e)))
            continue
        counts["parsed"] += 1
        blockstats["rubrik"] += sum(isinstance(b, Rubrik) for b in av.body)
        blockstats["stycke"] += sum(isinstance(b, Stycke) for b in av.body)
        if not av.body:
            counts["empty_body"] += 1
        if args.references:
            refs = body_links(scan_body(av.body))
            curated, found = curated_sfs(record), found_sfs(refs)
            refstats["refs"] += len(refs)
            refstats["curated"] += len(curated)
            refstats["curated_found"] += len(curated & found)
            if curated and not (curated & found):
                refstats["cases_missing_all"] += 1
    print("%d API-backed cases: %d parsed, %d empty body, %d failed"
          % (len(cases), counts["parsed"], counts["empty_body"],
             len(failures)))
    print("blocks: %d rubrik, %d stycke" % (blockstats["rubrik"],
                                            blockstats["stycke"]))
    if args.references:
        cur = refstats["curated"] or 1
        # Recall against lagrumLista: a curated-but-unfound SFS is usually
        # the editors deriving a lagrum from reasoning rather than citing
        # it verbatim, not a scanner miss -- a signal, not a pass/fail.
        print("references: %d found; lagrumLista recall %d/%d (%.1f%%), "
              "%d cases with no curated SFS found"
              % (refstats["refs"], refstats["curated_found"],
                 refstats["curated"], 100 * refstats["curated_found"] / cur,
                 refstats["cases_missing_all"]))
    for cid, err in failures[:20]:
        print("  FAIL %s: %s" % (cid, err))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--domstoldir", default=DOMSTOL_DEFAULT)
    parser.add_argument("--uuid")
    parser.add_argument("--index", default=INDEX_DEFAULT)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--references", action="store_true",
                        help="also scan bodies for citations and report "
                             "lagrumLista recall (slower)")
    args = parser.parse_args()
    (cmd_uuid if args.uuid else cmd_index)(args)


if __name__ == "__main__":
    main()
