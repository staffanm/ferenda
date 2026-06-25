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
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from .download import basefile_slug
from .model import Block, Forarbete
from .structure import flatten, nest
from ..lib.lagrum import (EULAGSTIFTNING, EURATTSFALL, FORARBETEN, KORTLAGRUM,
                          LAGRUM, MYNDIGHETSBESLUT, RATTSFALL, LagrumParser,
                          interleave, load_abbreviations, load_namedlaws)
from ..lib.util import normalize_space

SFS_NAMEDLAWS = "lagen/nu/res/extra/sfs_namedlaws.json"

# förarbeten cite across the whole spectrum, like court decisions
PARSE_TYPES = [LAGRUM, KORTLAGRUM, EULAGSTIFTNING, RATTSFALL, FORARBETEN,
               EURATTSFALL, MYNDIGHETSBESLUT]

RE_DOTS = re.compile(r"\.{4,}")                       # TOC dotted leaders
RE_HEADING_NUM = re.compile(r"^\d+(?:\.\d+)*$")       # "4" / "4.3.2" (own line)
RE_HEADING_INLINE = re.compile(r"^(\d+(?:\.\d+)+)\s+\S")   # "4.3.2 Title"
RE_NUM_TITLE = re.compile(r"^(\d+(?:\.\d+)*)\s+\S")        # "15 Title" / "4.3 T"
RE_KAP_MARK = re.compile(r"^(\d+)\s*kap\.\s")             # bold "2 kap. ..."
RE_PARA_MARK = re.compile(r"^(\d+\s*[a-z]?)\s*§(?:\s|$)")  # bold "3 §" / "3 a §"

LINE_TOL = 4          # spans within this many y-units are the same visual line
PARA_GAP = 1.5        # a vertical gap > PARA_GAP x line-height starts a paragraph


def mint_uri(typ, basefile):
    """https://lagen.nu/<type>/<basefile> -- the citation-target form (prop,
    sou, ds, dir, …), identical to what the FORARBETEN grammar mints."""
    return "https://lagen.nu/%s/%s" % (typ, basefile)


# --------------------------------------------------------------------------
# font-aware extraction (pdftohtml -xml: poppler speed + bold/italic markup)
# --------------------------------------------------------------------------

@dataclass
class Line:
    text: str
    top: int
    bold: bool          # the whole visual line is bold (an unnumbered heading)
    lead_bold: bool     # the leftmost run is bold (a bold §/chapter marker that
                        # leads regular statutory text on the same line)
    italic: bool


@dataclass
class Para:
    text: str
    bold: bool = False
    lead_bold: bool = False
    italic: bool = False


def pdf_pages(pdf_path):
    """(pageno, [Line]) per page via `pdftohtml -xml`. Each <text> fragment is
    one font run carrying <b>/<i>; fragments on the same `top` are one visual
    line, bold/italic when all their runs are."""
    xml = subprocess.run(
        ["pdftohtml", "-xml", "-i", "-nodrm", "-stdout", str(pdf_path)],
        capture_output=True, check=True).stdout
    # pdftohtml emits occasionally malformed XML (overlapping <b>/<i>, stray &),
    # so parse leniently rather than abort the document
    root = etree.fromstring(xml, etree.XMLParser(recover=True, load_dtd=False,
                                                 no_network=True))
    for page in root.findall("page"):
        spans = []
        for t in page.findall("text"):
            text = normalize_space("".join(t.itertext()))
            if text:
                top, height = int(t.get("top")), int(t.get("height") or 0)
                spans.append((top, int(t.get("left")), top + height, text,
                              t.find(".//b") is not None,
                              t.find(".//i") is not None))
        yield int(page.get("number")), _lines(spans)


def _lines(spans):
    """Group spans sharing a text baseline (top + height) into visual lines, left
    to right. We group on the baseline, not the top, because one line may mix font
    sizes -- a large heading number beside its title ('9' + 'Författnings-
    kommentar'), a bold §-marker leading body text -- and such spans share a
    baseline while sitting at different tops; a top-only grouping would split them
    (and reflow e.g. '9 Författningskommentar' to 'Författningskommentar 9', which
    then fails heading detection). The line's `top` is the topmost of its spans."""
    lines = []
    for top, left, base, text, bold, italic in sorted(spans):
        if lines and abs(base - lines[-1][0]) <= LINE_TOL:
            lines[-1][1].append((left, text, bold, italic))
            lines[-1][2] = min(lines[-1][2], top)
        else:
            lines.append([base, [(left, text, bold, italic)], top])
    out = []
    for base, runs, top in lines:
        runs.sort()
        out.append(Line(normalize_space(" ".join(r[1] for r in runs)), top,
                        all(r[2] for r in runs), runs[0][2],
                        all(r[3] for r in runs)))
    return out


def _dehyphenate(acc, line):
    if acc.endswith("-") and line[:1].islower():
        return acc[:-1] + line          # soft hyphen: "för-\nfogar" -> "förfogar"
    return (acc + " " + line) if acc else line


def page_paragraphs(lines, identifier, pageno):
    """Reflow a page's lines into paragraphs, dropping the running header (the
    identifier), the page-number line and TOC dotted-leader lines. A bold line
    (heading or a §/chapter marker) always begins its own paragraph; otherwise
    a vertical gap larger than the body line-height does. A page dominated by
    dotted leaders is the table of contents -- skipped whole."""
    if sum(RE_DOTS.search(l.text) is not None for l in lines) >= 5:
        return []
    header_re = re.compile(r"\s*".join(re.escape(t) for t in identifier.split()))
    kept = []
    for l in lines:
        text = normalize_space(header_re.sub(" ", l.text))
        if text and text != str(pageno) and not RE_DOTS.search(text):
            kept.append(Line(text, l.top, l.bold, l.lead_bold, l.italic))
    gaps = sorted(b.top - a.top for a, b in zip(kept, kept[1:]) if b.top > a.top)
    body_gap = gaps[len(gaps) // 2] if gaps else 0      # median line-height
    paras, cur, prev = [], None, None
    for l in kept:
        marker = l.lead_bold and (RE_KAP_MARK.match(l.text)
                                  or RE_PARA_MARK.match(l.text))
        starts = (cur is None or l.bold or marker or (prev and prev.bold)
                  or (body_gap and l.top - prev.top > PARA_GAP * body_gap))
        if starts and cur is not None:
            paras.append(cur)
            cur = None
        if cur is None:
            cur = Para(l.text, l.bold, bool(marker), l.italic)
        else:
            cur.text = _dehyphenate(cur.text, l.text)
            cur.italic = cur.italic and l.italic
        prev = l
    if cur is not None:
        paras.append(cur)
    return paras


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
