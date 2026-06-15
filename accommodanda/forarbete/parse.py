"""Parse a preparatory work (förarbete) PDF into the Forarbete model and project
it to a JSON artifact.

Text is extracted with poppler's `pdftotext` (plain, reading-order mode -- it
isolates the running header and page number on their own lines, unlike
`-layout` which mashes them into the body in the alternating outer margin).
Each PDF page maps to one printed page (modern regeringen.se PDFs number from
the title page), so the PDF page index is the `#sid{N}` anchor förarbete
citations resolve to ("prop. 1997/98:45 s. 39" -> `prop/1997/98:45#sid39`).

The document URI is minted to the same form the FORARBETEN citation grammar
produces (`prop/{riksmöte}:{no}`, `sou/{year}:{no}`, …), so a citation to this
document and the document itself agree by construction -- the lesson from the DV
case-URI work. Body blocks are scanned for citations (SFS / other förarbeten /
case law) and carry inline links, like SFS and DV.

    python -m accommodanda.forarbete.parse RECORD.json   # one record -> artifact
    python -m accommodanda.forarbete.parse --root DIR [--limit N] [--type prop]
"""

import argparse
import functools
import glob
import json
import re
import subprocess
import sys
from pathlib import Path

from .model import Block, Forarbete
from ..lib.lagrum import (EULAGSTIFTNING, EURATTSFALL, FORARBETEN, KORTLAGRUM,
                          LAGRUM, MYNDIGHETSBESLUT, RATTSFALL, LagrumParser,
                          interleave, load_abbreviations, load_namedlaws)

SFS_TTL = "lagen/nu/res/extra/sfs.ttl"

# förarbeten cite across the whole spectrum, like court decisions
PARSE_TYPES = [LAGRUM, KORTLAGRUM, EULAGSTIFTNING, RATTSFALL, FORARBETEN,
               EURATTSFALL, MYNDIGHETSBESLUT]

RE_DOTS = re.compile(r"\.{4,}")                       # TOC dotted leaders
RE_HEADING_NUM = re.compile(r"^\d+(?:\.\d+)*$")       # "4" / "4.3.2" (own line)
RE_HEADING_INLINE = re.compile(r"^(\d+(?:\.\d+)+)\s+\S")   # "4.3.2 Title"


def mint_uri(typ, basefile):
    """https://lagen.nu/<type>/<basefile> -- the citation-target form (prop,
    sou, ds, dir, …), identical to what the FORARBETEN grammar mints."""
    return "https://lagen.nu/%s/%s" % (typ, basefile)


def pdf_to_pages(pdf_path):
    out = subprocess.run(["pdftotext", "-enc", "UTF-8", str(pdf_path), "-"],
                         capture_output=True, check=True)
    return out.stdout.decode("utf-8", "replace").split("\f")


def _dehyphenate(acc, line):
    if acc.endswith("-") and line[:1].islower():
        return acc[:-1] + line          # soft hyphen: "för-\nfogar" -> "förfogar"
    return (acc + " " + line) if acc else line


def page_paragraphs(text, identifier, pageno):
    """Reflow one page into paragraphs, dropping the running header (the
    identifier), the page-number line (== pageno) and TOC dotted-leader lines.
    A page dominated by dotted leaders is the table of contents -- skipped
    whole (its residual bare section numbers would otherwise become junk)."""
    if len(RE_DOTS.findall(text)) >= 5:
        return []
    # the running header is the identifier; plain pdftotext sometimes merges it
    # into an adjacent body line, so strip it as a substring anywhere (tolerant
    # of its internal spacing), not only as a whole line
    header_re = re.compile(r"\s*".join(re.escape(t) for t in identifier.split()))
    paras, cur = [], ""
    for raw in text.split("\n"):
        line = re.sub(r"\s+", " ", header_re.sub(" ", raw)).strip()
        if not line:
            if cur:
                paras.append(cur)
                cur = ""
            continue
        if line == str(pageno):
            continue                      # printed page number (== pdf index)
        if RE_DOTS.search(line):
            continue                      # table-of-contents leader
        cur = _dehyphenate(cur, line)
    if cur:
        paras.append(cur)
    return paras


def classify(paras, page):
    """Paragraphs -> Blocks. A lone section number merges with the following
    paragraph as its heading; a dotted section number with inline title is a
    heading; everything else is a stycke. All carry the page."""
    blocks = []
    i = 0
    while i < len(paras):
        p = paras[i]
        nxt = paras[i + 1] if i + 1 < len(paras) else ""
        # a lone section number is a heading only if a title (uppercase-led,
        # not itself a number) follows -- else it is stray noise, dropped
        if RE_HEADING_NUM.match(p):
            if nxt[:1].isupper() and not RE_HEADING_NUM.match(nxt):
                blocks.append(Block("rubrik", "%s %s" % (p, nxt),
                                    page, p.count(".") + 1))
                i += 2
            else:
                i += 1
        elif RE_HEADING_INLINE.match(p) and len(p) < 120:
            blocks.append(Block("rubrik", p, page, p.split()[0].count(".") + 1))
            i += 1
        else:
            blocks.append(Block("stycke", p, page))
            i += 1
    return blocks


def parse_pdf(pdf_path, identifier):
    """All body blocks of a förarbete PDF, page by page (page = pdf index)."""
    blocks = []
    for i, text in enumerate(pdf_to_pages(pdf_path), start=1):
        blocks += classify(page_paragraphs(text, identifier, i), i)
    return blocks


def parse_record(record, root):
    """A downloaded record (the `<slug>.json`) -> a Forarbete. Uses the first
    PDF the downloader stored; a record without one yields metadata + no body
    (still a real catalog document at its URI)."""
    typ, basefile = record["type"], record["basefile"]
    pdfs = [f for f in record.get("files", []) if f.lower().endswith(".pdf")]
    body = (parse_pdf(Path(root) / typ / pdfs[0], record["identifier"])
            if pdfs else [])
    return Forarbete(type=typ, basefile=basefile,
                     identifier=record["identifier"], uri=mint_uri(typ, basefile),
                     title=record.get("title", ""), date=record.get("date"),
                     body=body)


@functools.cache
def _refparser():
    return LagrumParser(load_namedlaws(SFS_TTL), basefile="forarbete",
                        abbreviations=load_abbreviations(SFS_TTL),
                        parse_types=PARSE_TYPES)


def to_artifact(fa):
    """Project to JSON. Body blocks become inline-run lists (plain runs +
    {predicate,uri,text} link dicts), scanned with one parser threaded across
    the document so 'a. prop.'/'samma lag' state carries."""
    parser = _refparser()
    parser.state = type(parser.state)()      # fresh per-document state
    body = [{"type": b.kind, "page": b.page,
             "text": interleave(b.text, parser.parse_text(b.text, context={}))}
            | ({"level": b.level} if b.level else {})
            for b in fa.body]
    return {"uri": fa.uri, "type": fa.type, "identifier": fa.identifier,
            "basefile": fa.basefile, "title": fa.title, "date": fa.date,
            "body": body}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def cmd_one(path):
    record = json.loads(Path(path).read_text())
    json.dump(to_artifact(parse_record(record, Path(path).parents[1])),
              sys.stdout, ensure_ascii=False, indent=2)
    print()


def cmd_batch(root, typ, limit):
    pattern = "%s/%s/*.json" % (root, typ or "*")
    records = sorted(glob.glob(pattern))
    if limit:
        records = records[:limit]
    blocks = links = empty = fail = 0
    for path in records:
        record = json.loads(Path(path).read_text())
        if record.get("files") is None:
            continue
        try:
            art = to_artifact(parse_record(record, root))
        except Exception as exc:
            fail += 1
            print("  FAIL %s: %s: %s" % (record["basefile"],
                                         type(exc).__name__, exc))
            continue
        blocks += len(art["body"])
        links += sum(1 for b in art["body"] for r in b["text"]
                     if isinstance(r, dict))
        empty += not art["body"]
        json.dump(art, open(art_path(root, record), "w"),
                  ensure_ascii=False, indent=2)
    print("%d records: %d blocks, %d links, %d empty-body, %d failed"
          % (len(records), blocks, links, empty, fail))


def art_path(root, record):
    from .download import basefile_slug
    out = Path(root) / record["type"] / "artifact"
    out.mkdir(parents=True, exist_ok=True)
    return out / (basefile_slug(record["basefile"]) + ".json")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("record", nargs="?", help="a single record JSON -> stdout")
    ap.add_argument("--root", default="site/data/forarbete")
    ap.add_argument("--type", help="restrict batch to one type")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    if args.record:
        cmd_one(args.record)
    else:
        cmd_batch(args.root, args.type, args.limit)


if __name__ == "__main__":
    main()
