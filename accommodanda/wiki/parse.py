"""Project MediaWiki dump pages into kommentar / begrepp artifacts.

A **kommentar** page ("SFS/2009:400") is per-paragraph commentary: each
`== 21 kap 1 § ==` heading becomes a section anchored to the statute fragment
it annotates (`2009:400#K21P1`), and the heading itself is a link to that
paragraph -- so `relate` records a kommentar→paragraph edge and the statute
paragraph shows the commentary in its margin (the old side-by-side). The prose
is citation-scanned with the commented law as the relative-reference base.

A **begrepp** page ("Ne bis in idem") is a concept definition published at
`begrepp/Ne_bis_in_idem`; its `[[wikilinks]]` resolve to other concepts and its
prose citations to laws/cases, so the concept becomes a hub the rest of the
corpus links into.

    python -m accommodanda.wiki.parse kommentar FILE.xml   # one page -> stdout
    python -m accommodanda.wiki.parse begrepp   FILE.xml
"""

import argparse
import functools
import glob
import json
import re
import sys
from pathlib import Path

from ..lib import wikitext
from ..lib.lagrum import (EULAGSTIFTNING, EURATTSFALL, FORARBETEN, KORTLAGRUM,
                          LAGRUM, MYNDIGHETSBESLUT, RATTSFALL, LagrumParser,
                          load_abbreviations, load_namedlaws)

SFS_NAMEDLAWS = "lagen/nu/res/extra/sfs_namedlaws.json"
PARSE_TYPES = [LAGRUM, KORTLAGRUM, EULAGSTIFTNING, RATTSFALL, FORARBETEN,
               EURATTSFALL, MYNDIGHETSBESLUT]

# a commentary heading -> the statute fragment it annotates
RE_PARA = re.compile(r"(\d+)\s*kap\.?\s+(\d+)\s*([a-z])?\s*§(?:\s+(\d+)\s*st)?")
RE_KAP = re.compile(r"^\s*(\d+)\s*kap")
# pages that are navigation/meta, not concepts
SKIP_TITLE = ("Lagar inom", "Kategori:", "Mall:", "Användare:", "MediaWiki:",
              "Lagen.nu:", "Fil:", "Hjälp:")


@functools.cache
def _vocab():
    return load_namedlaws(SFS_NAMEDLAWS), load_abbreviations(SFS_NAMEDLAWS)


def _parser(basefile):
    namedlaws, abbreviations = _vocab()
    return LagrumParser(namedlaws, basefile=basefile,
                        abbreviations=abbreviations, parse_types=PARSE_TYPES)


def heading_fragment(heading):
    """"21 kap 1 §" -> "K21P1"; "1 kap. 1 c §" -> "K1P1c"; "25 kap" -> "K25"."""
    m = RE_PARA.search(heading)
    if m:
        frag = "K%sP%s" % (m.group(1), m.group(2))
        if m.group(3):
            frag += m.group(3)
        if m.group(4):
            frag += "S%s" % m.group(4)
        return frag
    m = RE_KAP.match(heading)
    return "K%s" % m.group(1) if m else None


def kommentar_artifact(path):
    title, _, text = wikitext.load_page(path)
    if wikitext.is_redirect(text):
        return None
    sfsnr = title.split("/", 1)[1] if "/" in title else title
    law_uri = "https://lagen.nu/" + sfsnr
    parser = _parser(sfsnr)
    body, section, frag = [], None, None
    for block in wikitext.blocks(text):
        if block[0] == "rubrik":
            _, level, heading = block
            f = heading_fragment(heading)
            if f:
                frag, section = f, []
                body.append({"type": "sektion", "id": frag, "heading": heading,
                             "text": [{"predicate": "dcterms:references",
                                       "uri": "%s#%s" % (law_uri, frag),
                                       "text": heading}],
                             "children": section})
            else:
                (section if section is not None else body).append(
                    {"type": "rubrik", "level": level, "text": [heading]})
        else:
            runs = wikitext.to_runs(block[1], parser, fragment=frag)
            (section if section is not None else body).append(
                {"type": "stycke", "text": runs})
    return {"uri": "https://lagen.nu/kommentar/" + sfsnr, "type": "kommentar",
            "sfs": sfsnr, "annotates": law_uri, "author": wikitext.author(text),
            "categories": wikitext.categories(text), "body": body}


def begrepp_artifact(path):
    title, ns, text = wikitext.load_page(path)
    if ns != "0" or wikitext.is_redirect(text) or title.startswith(SKIP_TITLE):
        return None
    parser = _parser("begrepp")
    body = []
    for block in wikitext.blocks(text):
        if block[0] == "rubrik":
            body.append({"type": "rubrik", "level": block[1], "text": [block[2]]})
        else:
            body.append({"type": "stycke",
                         "text": wikitext.to_runs(block[1], parser, context={})})
    return {"uri": wikitext.begrepp_uri(title), "type": "begrepp",
            "title": title, "categories": wikitext.categories(text),
            "body": body}


# --------------------------------------------------------------------------
# basefile <-> file indexes (titles are authoritative; filenames are encoded)
# --------------------------------------------------------------------------

@functools.cache
def kommentar_index(root):
    """sfsnr -> path, over the SFS/ commentary tree."""
    out = {}
    for path in glob.glob(str(Path(root) / "SFS" / "*" / "*.xml")):
        title, _, text = wikitext.load_page(path)
        if "/" in title and not wikitext.is_redirect(text):
            out[title.split("/", 1)[1]] = path
    return out


@functools.cache
def begrepp_index(root):
    """concept title -> path, over the top-level concept pages."""
    out = {}
    for path in glob.glob(str(Path(root) / "*.xml")):
        if Path(path).name == "dump.xml":   # the full export the pages were split from
            continue
        title, ns, text = wikitext.load_page(path)
        if ns == "0" and not wikitext.is_redirect(text) \
                and not title.startswith(SKIP_TITLE):
            out[title] = path
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("kind", choices=("kommentar", "begrepp"))
    ap.add_argument("file")
    args = ap.parse_args()
    build = kommentar_artifact if args.kind == "kommentar" else begrepp_artifact
    art = build(args.file)
    if art is None:
        sys.exit("not a %s page (redirect/namespace)" % args.kind)
    json.dump(art, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    main()
