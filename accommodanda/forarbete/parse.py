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
import sys
from pathlib import Path

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
    interleave,
    load_abbreviations,
    load_namedlaws,
)

# font-aware extraction + paragraph reflow are shared across the PDF verticals
# (re-exported here so this module's existing import sites keep working)
from ..lib.pdftext import (
    RE_KAP_MARK,
    RE_PARA_MARK,
    page_paragraphs,
    pdf_pages,
)
from .download import basefile_slug
from .model import Block, Forarbete
from .structure import flatten, nest

# förarbeten cite across the whole spectrum, like court decisions
PARSE_TYPES = [LAGRUM, KORTLAGRUM, EULAGSTIFTNING, RATTSFALL, FORARBETEN,
               EURATTSFALL, MYNDIGHETSBESLUT]

RE_HEADING_NUM = re.compile(r"^\d+(?:\.\d+)*$")       # "4" / "4.3.2" (own line)
RE_HEADING_INLINE = re.compile(r"^(\d+(?:\.\d+)+)\s+\S")   # "4.3.2 Title"
RE_NUM_TITLE = re.compile(r"^(\d+(?:\.\d+)*)\s+\S")        # "15 Title" / "4.3 T"


def mint_uri(typ, basefile):
    """https://lagen.nu/<type>/<basefile> -- the citation-target form (prop,
    sou, ds, dir, …), identical to what the FORARBETEN grammar mints."""
    return "https://lagen.nu/%s/%s" % (typ, basefile)


def classify(paras, page):
    """Paragraphs -> Blocks. Bold chapter/§ markers (recovered from font) become
    `kapitel`/`paragraf` blocks -- the structure that lets commentary be tied to
    a paragraf; other bold or numbered paragraphs are headings; the rest stycken."""
    blocks = []
    i = 0
    while i < len(paras):
        p = paras[i]
        mk, mp, mt = (RE_KAP_MARK.match(p.text), RE_PARA_MARK.match(p.text),
                      RE_NUM_TITLE.match(p.text))
        if p.lead_bold and mk:
            blocks.append(Block("kapitel", p.text, page, num=mk.group(1)))
        elif p.lead_bold and mp:
            blocks.append(Block("paragraf", p.text, page,
                                num=re.sub(r"\s+", "", mp.group(1))))
        elif (p.bold or mt) and mt and len(p.text) < 120:
            blocks.append(Block("rubrik", p.text, page,
                                mt.group(1).count(".") + 1))
        elif p.bold and len(p.text) < 120:
            blocks.append(Block("rubrik", p.text, page, 3))   # unnumbered subhead
        elif RE_HEADING_NUM.match(p.text):
            nxt = paras[i + 1].text if i + 1 < len(paras) else ""
            if nxt[:1].isupper() and not RE_HEADING_NUM.match(nxt):
                blocks.append(Block("rubrik", "%s %s" % (p.text, nxt), page,
                                    p.text.count(".") + 1))
                i += 2
                continue
        else:
            blocks.append(Block("stycke", p.text, page))
        i += 1
    return blocks


def parse_pdf(pdf_path, identifier):
    """All body blocks of a förarbete PDF, page by page (page = pdf index)."""
    blocks = []
    for pageno, lines in pdf_pages(pdf_path):
        blocks += classify(page_paragraphs(lines, identifier, pageno), pageno)
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
    return LagrumParser(load_namedlaws(SFS_NAMEDLAWS), basefile="forarbete",
                        abbreviations=load_abbreviations(SFS_NAMEDLAWS),
                        parse_types=PARSE_TYPES)


def to_artifact(fa):
    """Project to JSON. Each block becomes an inline-run list (plain runs +
    {predicate,uri,text} link dicts), scanned with one parser threaded across the
    document so 'a. prop.'/'samma lag' state carries; the flat block run is then
    grouped into the nested `structure` tree by heading level (see structure.py)."""
    parser = _refparser()
    parser.state = type(parser.state)()      # fresh per-document state
    blocks = [{"type": b.kind, "page": b.page,
               "text": interleave(b.text, parser.parse_text(b.text, context={}))}
              | ({"level": b.level} if b.level else {})
              | ({"num": b.num} if b.num else {})
              for b in fa.body]
    return {"uri": fa.uri, "type": fa.type, "identifier": fa.identifier,
            "basefile": fa.basefile, "title": fa.title, "date": fa.date,
            "structure": nest(blocks)}


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
        flat = flatten(art["structure"])
        blocks += len(flat)
        links += sum(1 for b in flat for r in b.get("text", [])
                     if isinstance(r, dict))
        empty += not art["structure"]
        art_path(root, record).write_text(
            json.dumps(art, ensure_ascii=False, indent=2))
    print("%d records: %d blocks, %d links, %d empty-body, %d failed"
          % (len(records), blocks, links, empty, fail))


def art_path(root, record):
    out = Path(root) / record["type"] / "artifact"
    out.mkdir(parents=True, exist_ok=True)
    return out / (basefile_slug(record["basefile"]) + ".json")


def main():
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
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
